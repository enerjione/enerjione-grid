"""Restore durumunun VERITABANI DISINDA, kalici tutuldugu yer.

NEDEN DB'DE DEGIL
-----------------
Restore'un kendisi veritabanini degistiriyor. Durumu DB'ye yazmak iki ayri
sekilde ise yaramaz:

  1. `backup_jobs` ve `backup_schedule` `EXCLUDED_DATA_TABLES` icinde, yani
     her restore sonrasi BOS kalir ve `reindex_backup_jobs_from_disk` ile
     dosya adlarindan yeniden uretilir. Oraya yazilan bir restore kaydi ilk
     cutover'da kaybolur.
  2. Cutover sirasinda uretim veritabani YENIDEN ADLANDIRILIYOR. Tam o
     pencerede surec olurse, durumu okuyacagimiz DB bir sure "yok"tur.
     Kurtarma kodunun tam olarak o ani anlamasi gerekiyor.

Bu yuzden durum, yedek biriminde (BACKUP_DIR) duz bir JSON dosyasinda
tutulur. O birim restore'dan etkilenmez, konteyner yeniden olusturulsa da
kalir ve veritabani hic acilamiyorken bile okunabilir.

YOL SABIT — DIZIN GECISI YUZEYI YOK
-----------------------------------
Dosya adi sabittir ve kullanici girdisinden turetilmez. `BACKUP_DIR` zaten
compose'dan gelen guvenilir bir yol; buraya disaridan bir bilesen eklenmez.

ATOMIK YAZIM
------------
Gecici dosyaya yaz + `os.replace`. Yarim yazilmis bir durum dosyasi, tam da
guc kesintisi senaryosunda okunacagi icin kabul edilemez; `os.replace` ayni
dosya sisteminde atomiktir.

BAYAT KAYIT TESPITI
-------------------
Kayit `pid` ve `boot_id` tasir. Surec artik yasamiyorsa ya da makine o
kayittan sonra yeniden baslatildiysa kayit "sahipsiz" sayilir. SAHIPSIZ
KAYIT OTOMATIK SILME YETKISI VERMEZ — yalnizca operatore gosterilecek
"incelenmeli" isaretidir (bkz. safe_restore.recover_orphans).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Durum dosyasinin adi. SABIT — kullanici girdisinden turetilmez.
STATE_FILENAME = ".restore-state.json"

#: Dosya semasi surumu. Alan eklendiginde artirilir; okuyucu tanimadigi bir
#: surumu "anlasilmaz" sayar ve OTOMATIK HICBIR SEY YAPMAZ (fail-closed).
STATE_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Asamalar. `restore_status_tracker` UI icin bunlarin bir alt kumesini
# gosterir; burasi KURTARMA icin gereken kesin durumu tutar.
# ---------------------------------------------------------------------------
STAGE_PREFLIGHT = "preflight"
STAGE_VALIDATING_ARCHIVE = "validating_archive"
STAGE_CREATING_STAGING = "creating_staging"
STAGE_RESTORING = "restoring"
STAGE_MIGRATING = "migrating"
STAGE_VALIDATING_STAGING = "validating_staging"
STAGE_PREPARING_CUTOVER = "preparing_cutover"
#: --- cutover ic durumlari: kurtarmanin dayandigi asil bilgi ---
STAGE_CONNECTIONS_DRAINED = "connections_drained"
STAGE_PRODUCTION_RENAMED = "production_renamed"
STAGE_STAGING_PROMOTED = "staging_promoted"
STAGE_POST_VALIDATING = "post_validating"
#: --- terminal ---
STAGE_COMPLETED = "completed"
STAGE_FAILED_SAFE = "failed_safe"
STAGE_FAILED_MANUAL = "manual_recovery_required"

#: Terminal asamalar: bunlarda restore artik "calisiyor" sayilmaz.
TERMINAL_STAGES = frozenset(
    {STAGE_COMPLETED, STAGE_FAILED_SAFE, STAGE_FAILED_MANUAL}
)

#: Uretim veritabaninin ADININ ARTIK YERINDE OLMADIGI asamalar. Kurtarma
#: kodu icin kritik: bu iki asamada `enerjione_grid` diye bir DB OLMAYABILIR.
CUTOVER_IN_FLIGHT_STAGES = frozenset(
    {STAGE_PRODUCTION_RENAMED, STAGE_STAGING_PROMOTED, STAGE_POST_VALIDATING}
)

#: PostgreSQL identifier ust siniri (NAMEDATALEN-1). Uretim adi cok uzunsa
#: turetilen staging/rollback adi sessizce KIRPILIRDI ve iki farkli isim
#: ayni sonuca duserek yanlis veritabanini hedefleyebilirdi.
_PG_IDENT_MAX = 63


def staging_db_name(job_id: int, production_db: str) -> str:
    """Staging DB adi — URETIM ADINDAN turetilir.

    NEDEN SABIT LITERAL DEGIL: veritabani adi yapilandirilabilir
    (`POSTGRES_DB`). Adi sabit `enerjione_grid_stg_*` yazmak, farkli adla
    kurulmus bir sahada staging'i uretimle iliskisiz bir isim uzayina
    koyardi; oksuz tespiti (isim kalibi eslesmesi) o sahada HIC calismazdi.
    Bunu gercek PostgreSQL uzerindeki uctan uca test ortaya cikardi.

    Kullanici girdisi girmez: `production_db` yapilandirmadan, `job_id`
    `int()` ile zorlanir. SQL identifier enjeksiyonu icin yuzey yoktur.
    """
    ad = f"{production_db}_stg_{int(job_id)}"
    if len(ad) > _PG_IDENT_MAX:
        raise ValueError(
            f"Staging veritabani adi PostgreSQL sinirini asiyor ({len(ad)}>63): {ad}"
        )
    return ad


def rollback_db_name(production_db: str, now: datetime | None = None) -> str:
    """Geri alma DB adi — uretim adi + zaman damgasi."""
    an = now or datetime.now(timezone.utc)
    ad = f"{production_db}_pre_{an.strftime('%Y%m%d_%H%M%S')}"
    if len(ad) > _PG_IDENT_MAX:
        raise ValueError(
            f"Geri alma veritabani adi PostgreSQL sinirini asiyor ({len(ad)}>63): {ad}"
        )
    return ad


def staging_pattern(production_db: str) -> re.Pattern[str]:
    """Staging adi kalibi — TAM ESLESME.

    "Iki kanit" kuralinin BIRINCI kaniti. `startswith` DEGIL tam eslesme:
    aksi halde elle yaratilmis `<uretim>_stg_deneme` gibi bir veritabani da
    silinebilir kapsama girerdi.
    """
    return re.compile(rf"^{re.escape(production_db)}_stg_(\d+)$")


def rollback_pattern(production_db: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(production_db)}_pre_(\d{{8}}_\d{{6}})$")


def _boot_id() -> str:
    """Makine acilis kimligi — bayat kayit tespiti icin.

    Linux'ta `/proc/sys/kernel/random/boot_id` her acilista degisir. Baska
    platformlarda (gelistirici makinesi) okunamaz; bos donmek GUVENLIDIR
    cunku bos boot_id "karsilastirilamaz" sayilir ve kayit otomatik olarak
    bayat ILAN EDILMEZ — yani yanlislikla silme yonunde bir karar uretmez.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


@dataclass
class RestoreState:
    """Bir restore denemesinin kalici kaydi."""

    schema_version: int = STATE_SCHEMA_VERSION
    job_id: int | None = None
    backup_file: str | None = None
    stage: str = STAGE_PREFLIGHT
    staging_db: str | None = None
    rollback_db: str | None = None
    production_db: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    started_by: str | None = None
    pid: int | None = None
    boot_id: str | None = None
    error: str | None = None
    #: Serbest bicimli teshis notlari (asama gecislerinde eklenir).
    notes: list[str] = field(default_factory=list)

    # -- turetilmis --------------------------------------------------------

    def is_terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES

    def cutover_in_flight(self) -> bool:
        """Uretim DB adi su an yerinde OLMAYABILIR mi?"""
        return self.stage in CUTOVER_IN_FLIGHT_STAGES

    def is_stale(self) -> bool:
        """Kaydi yazan surec artik yasamiyor mu?

        DIKKAT: "bayat" TEK BASINA silme yetkisi vermez. Yalnizca
        "bu kaydi kimse ilerletmiyor, operatore goster" demektir.
        """
        if self.is_terminal():
            return False
        # Makine yeniden baslamissa kaydi yazan surec kesinlikle yok.
        simdiki = _boot_id()
        if self.boot_id and simdiki and self.boot_id != simdiki:
            return True
        if self.pid is None:
            return True
        try:
            os.kill(self.pid, 0)  # sinyal gondermez, yalnizca varligi sorar
        except ProcessLookupError:
            return True
        except PermissionError:
            return False  # surec var ama baska kullaniciya ait
        except OSError:
            # Windows/kisitli ortam: karar veremiyoruz -> BAYAT DEME.
            return False
        return False


def _state_path(backup_dir: Path | None = None) -> Path:
    from app.services.backup_service import get_backup_dir

    kok = backup_dir or get_backup_dir()
    return Path(kok) / STATE_FILENAME


def write_state(state: RestoreState, *, backup_dir: Path | None = None) -> None:
    """Durumu ATOMIK olarak yaz.

    Hata YUTULMAZ ama YUKARI DA FIRLAMAZ: durum yazilamiyorsa restore'u
    baslatmamak dogru karardir (kurtarilamaz bir restore'a girmeyelim), o
    yuzden cagiran bunu preflight'ta kontrol eder. Burada yalnizca loglanir
    ve `False` yerine istisna ile bildirilir.
    """
    state.updated_at = datetime.now(timezone.utc).isoformat()
    hedef = _state_path(backup_dir)
    hedef.parent.mkdir(parents=True, exist_ok=True)
    # Ayni dizinde gecici dosya: `os.replace` ancak ayni dosya sisteminde
    # atomiktir. /tmp kullanmak bu garantiyi kaybettirirdi.
    fd, gecici = tempfile.mkstemp(dir=str(hedef.parent), prefix=".restore-state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # guc kesintisinde yarim JSON kalmasin
        os.replace(gecici, hedef)
    except Exception:
        try:
            os.unlink(gecici)
        except OSError:
            pass
        raise


def read_state(*, backup_dir: Path | None = None) -> RestoreState | None:
    """Kalici durumu oku. Yoksa None; BOZUKSA da None + uyari.

    Bozuk dosyada None donmek bilincli: kurtarma kodu "durum bilinmiyor"
    haline duser ve OTOMATIK HICBIR SEY YAPMAZ. Tahmin etmek, yanlis
    veritabanini dusurmeye giden yoldur.
    """
    yol = _state_path(backup_dir)
    try:
        ham = yol.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("restore_state_read_failed path=%s error=%s", yol, exc)
        return None
    try:
        veri = json.loads(ham)
    except ValueError:
        logger.error(
            "restore_state_corrupt path=%s — durum BILINMIYOR sayiliyor; "
            "otomatik kurtarma yapilmayacak",
            yol,
        )
        return None
    if not isinstance(veri, dict):
        return None
    surum = veri.get("schema_version")
    if surum != STATE_SCHEMA_VERSION:
        # Ileri surumlu bir dosyayi YORUMLAMAYA CALISMA.
        logger.error(
            "restore_state_schema_mismatch dosya=%s beklenen=%s — durum "
            "BILINMIYOR sayiliyor",
            surum,
            STATE_SCHEMA_VERSION,
        )
        return None
    bilinen = {f for f in RestoreState().__dataclass_fields__}  # type: ignore[attr-defined]
    return RestoreState(**{k: v for k, v in veri.items() if k in bilinen})


def clear_state(*, backup_dir: Path | None = None) -> None:
    """Durum dosyasini sil (yalnizca terminal asamada cagrilmali)."""
    try:
        _state_path(backup_dir).unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("restore_state_clear_failed error=%s", exc)


def new_state(
    *, job_id: int, backup_file: str, started_by: str, production_db: str
) -> RestoreState:
    simdi = datetime.now(timezone.utc).isoformat()
    return RestoreState(
        job_id=job_id,
        backup_file=backup_file,
        stage=STAGE_PREFLIGHT,
        production_db=production_db,
        started_at=simdi,
        updated_at=simdi,
        started_by=started_by,
        pid=os.getpid(),
        boot_id=_boot_id(),
    )


def advance(
    state: RestoreState,
    stage: str,
    *,
    note: str | None = None,
    backup_dir: Path | None = None,
) -> RestoreState:
    """Asamayi ilerlet ve HEMEN diske yaz.

    Her gecisin diske yazilmasi sart: kurtarmanin dayandigi tek bilgi bu.
    Ozellikle cutover'in iki `RENAME` arasindaki gecisi, guc kesintisinde
    hangi veritabaninin hangi isimde oldugunu soyleyen YEGANE kayittir.
    """
    state.stage = stage
    if note:
        state.notes.append(f"{datetime.now(timezone.utc).isoformat()} {note}")
    write_state(state, backup_dir=backup_dir)
    return state
