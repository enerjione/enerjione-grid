"""Guvenli (staging + cutover) veritabani geri yukleme.

NEDEN VAR — ESKI AKISIN KUSURU
------------------------------
Onceki restore, yedegi DOGRUDAN uretim veritabanina uyguluyordu:

    pg_restore --clean --if-exists --jobs=4 -d enerjione_grid

Uc sey ayni anda dogruydu:
  * `--single-transaction` YOKTU (`--jobs` ile birlikte kullanilamaz),
  * `--exit-on-error` docstring'de yaziyordu ama KOMUTTA YOKTU,
  * restore oncesi/sonrasi hicbir dogrulama yoktu.

Yani `--clean` uretimdeki tablolari DROP ettikten sonra herhangi bir hata
(bozuk arsiv, disk dolmasi, guc kesintisi) veritabanini YARIM RESTORE
EDILMIS halde birakiyordu ve geri donus yoktu. Felaket kurtarma icin
kullanilan aracin kendisi felaket uretebiliyordu.

YENI AKIS
---------
Uretim veritabanina, yedegin GERCEKTEN geri yuklenebildigi KANITLANANA
kadar tek bir yikici ifade uygulanmaz:

    0 preflight   kilit + arsiv + `pg_restore --list` + disk
    1 staging     CREATE DATABASE ... TEMPLATE template0   (BOS)
    2 restore     pg_restore --single-transaction --exit-on-error -> staging
    3 migrate     DATABASE_URL=<staging> python -m scripts.migrate_db (ALT SUREC)
    4 validate    sema/extension/hypertable/alembic dogrulamasi
    ------------------------------------------------------------------
    5 cutover     iki ALTER DATABASE ... RENAME   <- URETIME DOKUNAN ILK ADIM
    6 post        yeni uretimde dogrulama

0-4 arasinda herhangi bir hata: staging dusurulur, URETIM DEGISMEMISTIR.

TIMESCALEDB — OLCULMUS KARAR
----------------------------
Canli TimescaleDB 2.17.2 uzerinde iki aday olculdu (bkz. IT-14):

  Aday 1: CREATE EXTENSION + timescaledb_pre_restore() + pg_restore +
          timescaledb_post_restore()
  Aday 2: bos DB'ye dogrudan pg_restore; extension DUMP'TAN gelir

Ikisi de AYNI sonucu verdi (exit 0, extension 2.17.2, `telemetry_history`
hypertable, iki continuous aggregate, 11 politika isi). Aday 2 secildi:
iki adim daha az ve extension SURUMUNU dump'in kendisi belirliyor. Aday 1
extension'i onceden kurmayi, yani surumu ELLE eslestirmeyi gerektiriyor;
dump farkli bir surumden gelirse bu sessiz bir uyumsuzluk uretirdi.

Bu yuzden `timescaledb_pre_restore()` CAGRILMIYOR — bir eksiklik degil,
olculmus bir karar. Hypertable/politika onarimi zaten migration adimindaki
`ensure_historian_storage` tarafindan yapiliyor.

`--clean` KULLANILMIYOR: staging `template0`'dan yaratildigi icin
dusurulecek obje yok. Ayrica `--clean` extension'i da DROP etmeye
calisirdi.

TEK SINIR, TEK KAPI
-------------------
Uretim veritabanina gidebilecek HER admin ifadesi `_admin_sql()`, her
harici arac cagrisi `_run_pg_tool()` uzerinden gecer. Testler "uretime
yikici hicbir sey gitmedi" iddiasini bu iki kapinin cagri kaydindan
dogrular; assert'ler dolayli degil DOGRUDANDIR.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, unquote

from app.core.config import settings
from app.services import restore_state_store as durum_deposu
from app.services.restore_state_store import (
    RestoreState,
    rollback_db_name,
    rollback_pattern,
    staging_db_name,
    staging_pattern,
)

logger = logging.getLogger(__name__)


# ==========================================================================
# Yapilandirma
# ==========================================================================

#: Advisory lock anahtari. Sabit ve keyfi; yalnizca bu uygulamanin restore
#: akisi kullanir. `postgres` BAKIM VERITABANINDA alinir — bkz. RestoreLock.
_RESTORE_LOCK_KEY = 0x5245_5354  # "REST"

#: Cutover'da rename denemesi sayisi. Superuser'lar CONNECTION LIMIT'i
#: bypass ettigi icin yeniden baglanma yarisi olabilir.
_CUTOVER_RENAME_DENEME = 5
_CUTOVER_RENAME_BEKLE_SN = 1.0


# ==========================================================================
# Sonuc tipleri
# ==========================================================================


@dataclass
class Kontrol:
    ad: str
    gecti: bool
    detay: str = ""


@dataclass
class DogrulamaRaporu:
    """Staging dogrulamasinin ACIK sonucu — log satiri degil, nesne.

    API'den okunabilir olmasi sart: operator cutover'in neden reddedildigini
    ekranda gormeli.
    """

    ok: bool = False
    kontroller: list[Kontrol] = field(default_factory=list)
    alembic_version: str | None = None
    eksik_tablolar: list[str] = field(default_factory=list)
    timescale: dict[str, Any] = field(default_factory=dict)

    def ekle(self, ad: str, gecti: bool, detay: str = "") -> None:
        self.kontroller.append(Kontrol(ad=ad, gecti=gecti, detay=detay))

    def sonucla(self) -> "DogrulamaRaporu":
        self.ok = all(k.gecti for k in self.kontroller)
        return self

    def ozet(self) -> str:
        dusen = [k for k in self.kontroller if not k.gecti]
        if not dusen:
            return "tum kontroller gecti"
        return "; ".join(f"{k.ad}: {k.detay or 'basarisiz'}" for k in dusen)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "alembic_version": self.alembic_version,
            "eksik_tablolar": self.eksik_tablolar,
            "timescale": self.timescale,
            "kontroller": [
                {"ad": k.ad, "gecti": k.gecti, "detay": k.detay} for k in self.kontroller
            ],
        }


class RestoreHatasi(RuntimeError):
    """Restore akisinin bilincli olarak durdurdugu durum."""


class KilitAlinamadi(RestoreHatasi):
    """Baska bir restore devam ediyor."""


# ==========================================================================
# TEK KAPI 1 — admin SQL (postgres bakim veritabaninda)
# ==========================================================================


def _admin_url() -> str:
    """`postgres` bakim veritabanina baglanti URL'i.

    NEDEN BAKIM VERITABANI: `CREATE/DROP/ALTER DATABASE` komutlari hedef
    veritabanina BAGLI OLMAYAN bir oturumdan calistirilmak zorundadir.
    Ayrica cutover uretim veritabanini YENIDEN ADLANDIRDIGI icin, oraya
    bagli bir oturum tam da is bitmeden koparilirdi.
    """
    ham = settings.database_url
    return re.sub(r"/[^/?]+(\?|$)", r"/postgres\1", ham, count=1)


def _admin_sql(sql: str, *, params: dict | None = None) -> list[tuple]:
    """Bakim veritabaninda TEK admin ifadesi calistirir (AUTOCOMMIT).

    BU FONKSIYON TEK KAPIDIR. Uretim veritabanini etkileyebilecek her
    ifade buradan gecer; testler cagri kaydini buradan izler.

    AUTOCOMMIT sart: `CREATE DATABASE` / `DROP DATABASE` / `ALTER DATABASE
    ... RENAME` transaction blogu icinde calistirilamaz.
    """
    from sqlalchemy import create_engine, text

    eng = create_engine(
        _admin_url(),
        isolation_level="AUTOCOMMIT",
        connect_args={"application_name": "e1_restore_admin"},
        pool_pre_ping=True,
    )
    try:
        with eng.connect() as conn:
            sonuc = conn.execute(text(sql), params or {})
            try:
                return list(sonuc.fetchall())
            except Exception:  # noqa: BLE001 - DDL sonuc dondurmez
                return []
    finally:
        eng.dispose()


def _db_sql(dbname: str, sql: str) -> list[tuple]:
    """Verilen veritabaninda salt-okunur sorgu (dogrulama icin)."""
    from sqlalchemy import create_engine, text

    eng = create_engine(
        _url_for_db(dbname),
        connect_args={"application_name": "e1_restore_check"},
        pool_pre_ping=True,
    )
    try:
        with eng.connect() as conn:
            return list(conn.execute(text(sql)).fetchall())
    finally:
        eng.dispose()


def _url_for_db(dbname: str) -> str:
    """Ayni sunucuda BASKA bir veritabani icin URL uretir.

    `dbname` her zaman kod tarafindan uretilir (sabit onek + tamsayi /
    zaman damgasi); kullanici girdisi girmez.
    """
    return re.sub(r"/[^/?]+(\?|$)", f"/{dbname}\\1", settings.database_url, count=1)


def _production_db_name() -> str:
    yol = urlparse(settings.database_url).path or "/"
    return unquote(yol.lstrip("/")) or "postgres"


# ==========================================================================
# TEK KAPI 2 — harici PostgreSQL araclari
# ==========================================================================


def _run_pg_tool(
    arac: str,
    args: list[str],
    *,
    dbname: str,
    timeout: int = 3600,
    stdin_kapali: bool = True,
) -> tuple[int, str]:
    """`pg_restore` / `pg_dump` gibi araclari calistirir.

    BU FONKSIYON TEK KAPIDIR — harici arac cagrilarinin tamami buradan
    gecer. Iki guvenlik ozelligi:

      * Komut ARGUMAN LISTESI olarak kurulur, kabuk YOK: enjeksiyon yuzeyi
        bulunmaz.
      * `dbname` acikca parametre; cagiran yanlislikla uretim adini
        gecirse bile bu tek noktada gorulur ve test edilebilir.

    Parola ORTAM DEGISKENIYLE gecer (`PGPASSWORD`), komut satirinda ASLA
    gorunmez — `ps` ciktisinda ve loglarda sizmasin.
    """
    from app.services.backup_service import _parse_db_url, resolve_pg_binary

    db = _parse_db_url(settings.database_url)
    ikili = resolve_pg_binary(arac)
    komut = [
        ikili,
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", dbname,
        *args,
    ]
    ortam = os.environ.copy()
    if db.get("password"):
        ortam["PGPASSWORD"] = db["password"]
    # Restore oturumunun adi: mevcut terminate donguleri `e1_%` ile baslayan
    # baglantilari KORUR. Bu ad verilmezse kendi restore'umuzu oldururuz.
    ortam["PGAPPNAME"] = "e1_restore_session"
    try:
        p = subprocess.run(
            komut,
            env=ortam,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL if stdin_kapali else None,
            check=False,
        )
    except FileNotFoundError:
        return 127, f"{arac} bulunamadi (PATH / PostgreSQL istemcisi eksik)"
    except subprocess.TimeoutExpired:
        return 124, f"{arac} zaman asimi ({timeout} sn)"
    cikti = (p.stderr or "") + (p.stdout or "")
    return p.returncode, cikti[-4000:]


# ==========================================================================
# FAZ A — kilit
# ==========================================================================


class RestoreLock:
    """Kume genelinde tek restore garantisi.

    NEDEN SUREC-ICI BIR LOCK YETMEZ
    -------------------------------
    Onceki koruma `restore_status_tracker` icindeki modul global'iydi.
    `backend-api` birden fazla uvicorn sureciyle kosabiliyor
    (`E1_API_WORKERS`), dolayisiyla iki restore AYNI ANDA baslayabilirdi.
    Ayrica surec yeniden baslarsa durum "idle"a donuyordu.

    NEDEN `postgres` VERITABANINDA
    ------------------------------
    PostgreSQL advisory kilitlerinin locktag'i `MyDatabaseId` icerir, yani
    kilitler VERITABANI KAPSAMLIDIR. Kilidi uretim veritabaninda almak iki
    kez yanlis olurdu: cutover o veritabanini yeniden adlandirdiginda
    baglanti kopar ve kilit dusar; ustelik yeniden adlandirilmis veritabani
    farkli bir kilit uzayina dusrdu.

    Kilit OTURUM seviyesindedir: surec olurse baglanti kapanir ve kilit
    kendiliginden serbest kalir. TTL ya da heartbeat gerekmez.
    """

    def __init__(self) -> None:
        self._eng = None
        self._conn = None

    def acquire(self) -> None:
        from sqlalchemy import create_engine, text

        self._eng = create_engine(
            _admin_url(),
            isolation_level="AUTOCOMMIT",
            connect_args={"application_name": "e1_restore_lock"},
        )
        self._conn = self._eng.connect()
        alindi = self._conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _RESTORE_LOCK_KEY}
        ).scalar()
        if not alindi:
            self.release()
            raise KilitAlinamadi(
                "Su an baska bir restore calisiyor. Tamamlanmasini bekleyin."
            )

    def release(self) -> None:
        from sqlalchemy import text

        if self._conn is not None:
            try:
                self._conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": _RESTORE_LOCK_KEY}
                )
            except Exception:  # noqa: BLE001
                logger.debug("restore_lock_unlock_failed", exc_info=True)
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None
        if self._eng is not None:
            try:
                self._eng.dispose()
            except Exception:  # noqa: BLE001
                pass
            self._eng = None

    def __enter__(self) -> "RestoreLock":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


# ==========================================================================
# FAZ A — disk on-kontrolu
# ==========================================================================


def disk_preflight(dump_path: Path) -> tuple[bool, str, dict[str, int]]:
    """Staging + mevcut rollback DB'si icin yer var mi?

    DUMP BOYUTU TEK BASINA KULLANILMAZ — sahada olculdu:
        veritabani 64 MB, dump 1,5 MB  ->  ~42x genisleme
    En buyuk tablolarin verisi dump'a hic girmiyor (EXCLUDED_DATA_TABLES),
    dosya sikistirilmis ve index'ler restore sirasinda YENIDEN uretiliyor.
    Bu yuzden birincil olcut CANLI VERITABANI BOYUTUDUR; dump boyutu
    yalnizca bir alt sinir kontrolu olarak carpanla kullanilir.

    ESKI ROLLBACK VERITABANI SILINMEZ: yer acmak icin mevcut geri donus
    noktasini yok etmek, guvenligi tam da en cok ihtiyac duyuldugu anda
    dusururdu. Yer yoksa restore HIC BASLAMAZ (fail-closed).
    """
    olculer: dict[str, int] = {}
    try:
        uretim = _production_db_name()
        satir = _admin_sql(
            "SELECT pg_database_size(:d)", params={"d": uretim}
        )
        uretim_boyut = int(satir[0][0]) if satir else 0
    except Exception as exc:  # noqa: BLE001
        return False, f"Veritabani boyutu olculemedi: {exc}", olculer

    try:
        dump_boyut = dump_path.stat().st_size
    except OSError as exc:
        return False, f"Yedek dosyasi okunamadi: {exc}", olculer

    # Mevcut rollback veritabanlari yerinde kalacak; onlarin boyutu da
    # "kullanilamaz alan" degil ama silinmeyecegi icin hesaba KATILMAZ
    # (zaten diskte, yeniden yer istemiyorlar). Yine de raporlanir.
    rollback_boyut = 0
    _rb_kalip = rollback_pattern(_production_db_name())
    for (ad,) in _mevcut_veritabanlari():
        if _rb_kalip.match(ad):
            try:
                r = _admin_sql("SELECT pg_database_size(:d)", params={"d": ad})
                rollback_boyut += int(r[0][0]) if r else 0
            except Exception:  # noqa: BLE001
                pass

    tahmini_staging = max(
        uretim_boyut,
        int(dump_boyut * settings.restore_expansion_factor),
    )
    gerekli = int(tahmini_staging * settings.restore_disk_safety)

    # Olcum PostgreSQL veri dizininin bulundugu dosya sistemi uzerinden
    # yapilmali; yedek dizini AYNI birimde olmayabilir.
    try:
        veri_dizini = _admin_sql("SHOW data_directory")[0][0]
    except Exception:  # noqa: BLE001
        veri_dizini = None
    olcum_yolu = veri_dizini if veri_dizini and Path(veri_dizini).exists() else None
    if olcum_yolu is None:
        # Container disindan `data_directory` gorunmeyebilir; yedek dizini
        # sahada ayni birimde ve makul bir vekil.
        from app.services.backup_service import get_backup_dir

        olcum_yolu = str(get_backup_dir())
    try:
        kullanim = shutil.disk_usage(olcum_yolu)
    except OSError as exc:
        return False, f"Disk olculemedi ({olcum_yolu}): {exc}", olculer

    # Mevcut disk guard rezervi KORUNUR: restore, sistemin son emniyet
    # subabini yiyerek yer acmaz.
    rezerv = max(
        int(kullanim.total * max(0, settings.disk_guard_reserve_percent) / 100),
        max(0, settings.disk_guard_reserve_min_gb) * 1024**3,
    )
    kullanilabilir = kullanim.free - rezerv

    olculer.update(
        {
            "uretim_bayt": uretim_boyut,
            "dump_bayt": dump_boyut,
            "mevcut_rollback_bayt": rollback_boyut,
            "tahmini_staging_bayt": tahmini_staging,
            "gerekli_bayt": gerekli,
            "bos_bayt": kullanim.free,
            "rezerv_bayt": rezerv,
            "kullanilabilir_bayt": kullanilabilir,
        }
    )

    if kullanilabilir < gerekli:
        gb = 1024**3
        return (
            False,
            (
                f"Yetersiz disk: staging icin ~{gerekli / gb:.1f} GB gerekli, "
                f"rezerv sonrasi {max(0, kullanilabilir) / gb:.1f} GB var. "
                f"Restore BASLATILMADI ve mevcut geri donus noktasi korundu."
            ),
            olculer,
        )
    return True, "", olculer


def _mevcut_veritabanlari() -> list[tuple]:
    try:
        return _admin_sql(
            "SELECT datname FROM pg_database WHERE datistemplate = false"
        )
    except Exception:  # noqa: BLE001
        return []


# ==========================================================================
# FAZ A — arsiv on-dogrulamasi
# ==========================================================================


def istemci_sunucu_surum_uyumu() -> tuple[bool, str]:
    """`pg_restore` istemcisi sunucudan YENI olmamali.

    OLCULEN ARIZA (PG16 + TimescaleDB 2.17.2 uzerinde):

        pg16 dump -> pg16 restore -> PG16 sunucu :  rc=0   (uretim yolu)
        pg16 dump -> pg18 restore -> PG16 sunucu :  rc=1
            ERROR: unrecognized configuration parameter "transaction_timeout"

    `pg_restore` 18, PG17 ile gelen `transaction_timeout` GUC'unu SET
    etmeye calisiyor; PG16 sunucu bunu tanimadigi icin restore duser.

    NEDEN GERCEK BIR RISK: `resolve_pg_binary` bulunan EN YUKSEK surumu
    secer. Docker kurulumunda istemci imaja gomulu (16) oldugu icin sorun
    yok; ama native/Windows kurulumda ya da cihaza sonradan daha yeni bir
    PostgreSQL kurulursa yedekler SESSIZCE geri yuklenemez hale gelir.
    Staging tasarimi bu durumda uretimi korur (hata staging'de olusur), ama
    operatorun sebebi anlamasi icin ERKEN ve ACIK bir mesaj gerekir.

    Ters yon (istemci sunucudan ESKI) burada engellenmez: PostgreSQL bunu
    genel olarak destekler ve gercek bir arizaya yol actigi olculmedi.
    """
    from app.services.backup_service import resolve_pg_binary

    try:
        satir = _admin_sql("SHOW server_version_num")
        sunucu_major = int(satir[0][0]) // 10000
    except Exception as exc:  # noqa: BLE001
        return True, f"sunucu surumu okunamadi ({exc}) — kontrol atlandi"

    try:
        p = subprocess.run(
            [resolve_pg_binary("pg_restore"), "--version"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        m = re.search(r"(\d+)\.", (p.stdout or "") + (p.stderr or ""))
        if not m:
            return True, "istemci surumu okunamadi — kontrol atlandi"
        istemci_major = int(m.group(1))
    except Exception as exc:  # noqa: BLE001
        return True, f"istemci surumu okunamadi ({exc}) — kontrol atlandi"

    if istemci_major > sunucu_major:
        return False, (
            f"pg_restore istemcisi (surum {istemci_major}) veritabani "
            f"sunucusundan (surum {sunucu_major}) YENI. Bu birlesim restore'u "
            f"sunucunun tanimadigi ayarlar yuzunden basarisiz kilar "
            f"(olculdu: 'unrecognized configuration parameter "
            f"transaction_timeout'). Sunucuyla AYNI ana surumden bir "
            f"PostgreSQL istemcisi kurun ya da PG_RESTORE/PG_DUMP ortam "
            f"degiskenleriyle dogru ikiliyi gosterin."
        )
    return True, ""


def arsiv_on_dogrula(dump_path: Path) -> tuple[bool, str]:
    """Mevcut guvenlik dogrulamasi + `pg_restore --list`.

    Mevcut `validate_dump_file` (PGDMP magic + tehlikeli SQL pattern
    taramasi) AYNEN korunur — guvenlik davranisi degismez.

    `pg_restore --list` ARSIVIN ICINDEKILER TABLOSUNU okur; yarim inmis,
    kesilmis ya da bozuk bir dosyayi restore BASLAMADAN yakalar. Bu bir
    "restore basarili olacak" garantisi DEGILDIR (sikistirilmis veri
    bloklari acilmaz) — gercek dogrulama staging restore'un kendisidir.
    """
    from app.services.backup_service import validate_dump_file

    gecerli, hata = validate_dump_file(dump_path)
    if not gecerli:
        return False, hata

    rc, cikti = _run_pg_tool(
        "pg_restore", ["--list", str(dump_path)], dbname="postgres", timeout=120
    )
    if rc != 0:
        return False, (
            f"Yedek arsivi okunamadi (pg_restore --list rc={rc}). "
            f"Dosya bozuk veya eksik olabilir. {cikti[:300]}"
        )
    return True, ""


# ==========================================================================
# FAZ B — staging yasam dongusu
# ==========================================================================


def staging_yarat(staging: str) -> None:
    """Bos staging veritabani.

    `TEMPLATE template0`: tamamen bos. Bu secim tek basina bir hata
    sinifini ortadan kaldiriyor — eski bir yedek yeni bir kuruluma
    yuklendiginde, arsivde OLMAYAN yeni tablolar (or. yeni surumun
    tablolari) staging'de HIC olusmaz. Eski akista bunlar uretimde kalir,
    `alembic_version` geriye doner ve bir sonraki acilista migration
    "zaten var" hatasiyla duserdi (konteyner crash-loop'u).
    """
    _admin_sql(f'CREATE DATABASE "{staging}" WITH TEMPLATE template0 ENCODING \'UTF8\'')


def staging_dusur(staging: str) -> None:
    """Staging'i dusur — hatalar YUTULUR (temizlik asla akisi bozmaz)."""
    try:
        _admin_sql(
            f'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
            f"WHERE datname = '{staging}' AND pid <> pg_backend_pid()"
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        _admin_sql(f'DROP DATABASE IF EXISTS "{staging}"')
    except Exception:  # noqa: BLE001
        logger.warning("staging_drop_failed db=%s", staging, exc_info=True)


def staginge_restore_et(staging: str, dump_path: Path) -> tuple[bool, str]:
    """Yedegi staging'e yukle — ATOMIK.

    `--single-transaction`: hata halinde staging TAMAMEN geri sarilir,
    kismi durum olusmaz.
    `--exit-on-error`: ilk hatada dur; sessizce yarim restore yok.
    `--jobs` YOK: `--single-transaction` ile birlikte kullanilamaz ve
    staging'de hiz onemli degil — uretim zaten dokunulmamis calisiyor.
    `--clean` YOK: veritabani zaten bos; ustelik `--clean` dump'in
    kurdugu extension'i dusurmeye calisirdi.
    """
    rc, cikti = _run_pg_tool(
        "pg_restore",
        [
            "--single-transaction",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            str(dump_path),
        ],
        dbname=staging,
        timeout=settings.restore_pg_timeout_sec,
    )
    if rc != 0:
        return False, f"pg_restore basarisiz (rc={rc}): {cikti[:800]}"
    return True, ""


# ==========================================================================
# FAZ C — staging uzerinde migration
# ==========================================================================


def staging_migrate(staging: str) -> tuple[bool, str]:
    """`scripts.migrate_db`'yi STAGING uzerinde, ALT SUREC olarak kostur.

    NEDEN ALT SUREC — IN-PROCESS YAPILAMAZ
    --------------------------------------
    Iki bagimsiz baglanti kaynagi var ve ikisi de MODUL YUKLENIRKEN
    sabitlenir:

      * `alembic_migrations/env.py` calistiginda `sqlalchemy.url`u
        KOSULSUZ `settings.database_url` ile ezer (`-x` destegi yok).
      * `app/db/session.py` engine'i modul govdesinde kurar ve
        `scripts/migrate_db.py` onu YEDI ayri noktada kullanir
        (advisory lock, `inspect`, `create_all`, sema kontrolu, historian).

    Yalnizca birini yonlendirmek, migration'i staging'de ama `create_all`
    ve kontrolleri URETIMDE calistiran bir karisim uretirdi. Alt surecte
    `Settings()` yeniden kurulur ve IKISI DE staging'e bakar; `migrate_db`
    kodunda tek satir degisiklik gerekmez.

    `.env` DOSYASI TUZAGI: `Settings` `env_file=".env"` okur. Konteyner
    imajinda `.env` YOKTUR (Dockerfile onu kopyalamaz) ama gelistirici
    makinesinde VARDIR ve oradaki DATABASE_URL ortam degiskenini EZMEZ —
    pydantic-settings'te ortam degiskeni dotenv'den once gelir. Yine de
    calisma dizinini repo koku disina almiyoruz cunku `scripts.migrate_db`
    paket olarak cozulmeli.
    """
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = _url_for_db(staging)
    # Alt surecin YANLISLIKLA uretime baglanmasini imkansiz kilacak ikinci
    # kemer: uretim adini tasiyan degiskenleri ortamdan cikariyoruz.
    for anahtar in ("POSTGRES_DB",):
        ortam.pop(anahtar, None)

    kok = Path(__file__).resolve().parents[2]  # apps/backend-api
    try:
        p = subprocess.run(
            [sys.executable, "-m", "scripts.migrate_db"],
            cwd=str(kok),
            env=ortam,
            capture_output=True,
            text=True,
            timeout=settings.restore_migrate_timeout_sec,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "Migration zaman asimina ugradi"
    except Exception as exc:  # noqa: BLE001
        return False, f"Migration baslatilamadi: {exc}"

    cikti = ((p.stderr or "") + (p.stdout or ""))[-4000:]
    if p.returncode != 0:
        return False, f"Migration basarisiz (rc={p.returncode}): {cikti[-800:]}"
    return True, ""


# ==========================================================================
# FAZ C — staging dogrulamasi
# ==========================================================================

#: Var olmasi ZORUNLU tablolar. IS VARSAYIMI YOK — "devices > 0" gibi bir
#: kosul kullanilmiyor: bos ama gecerli bir kurulum mumkundur ve onu
#: reddetmek dogru olmazdi. Yalnizca SEMA varligi sorgulaniyor.
_ZORUNLU_TABLOLAR = (
    "alembic_version",
    "users",
    "devices",
    "telemetry",
    "telemetry_latest",
    "telemetry_history",
    "alarm_events",
    "system_events",
    "project_settings",
)

#: HYPERTABLE / CONTINUOUS AGGREGATE listeleri SABIT KODLANMAZ.
#:
#: Olcut: "uretimde ne varsa staging'de de olmali". Sabit bir liste iki
#: yonde birden yanlis olurdu — historian yapisi surumle degisirse liste
#: bayatlar; farkli yapilandirilmis bir saha ise hicbir zaman restore
#: edemezdi. Ayni ilke extension kontrolunde de uygulaniyor.
_ZORUNLU_HYPERTABLE = "telemetry_history"  # yalnizca gunluk/teshis metni icin


def staging_dogrula(staging: str) -> DogrulamaRaporu:
    """Cutover'dan ONCE staging'in gercekten kullanilabilir oldugunu kanitla."""
    rapor = DogrulamaRaporu()

    # 1) Baglanti
    try:
        _db_sql(staging, "SELECT 1")
        rapor.ekle("baglanti", True)
    except Exception as exc:  # noqa: BLE001
        rapor.ekle("baglanti", False, str(exc)[:200])
        return rapor.sonucla()

    # 2) Kritik tablolar
    try:
        satirlar = _db_sql(
            staging,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'",
        )
        mevcut = {r[0] for r in satirlar}
        eksik = [t for t in _ZORUNLU_TABLOLAR if t not in mevcut]
        rapor.eksik_tablolar = eksik
        rapor.ekle(
            "kritik_tablolar",
            not eksik,
            f"eksik: {', '.join(eksik)}" if eksik else f"{len(mevcut)} tablo",
        )
    except Exception as exc:  # noqa: BLE001
        rapor.ekle("kritik_tablolar", False, str(exc)[:200])

    # 3) Alembic surumu kodun head'i ile AYNI olmali.
    #    Migration adimi staging uzerinde kostugu icin burada esitlik
    #    bekleriz; esit degilse migration sessizce bir sey yapmamis demektir.
    try:
        satir = _db_sql(staging, "SELECT version_num FROM alembic_version")
        surum = satir[0][0] if satir else None
        rapor.alembic_version = surum
        head = _kod_head()
        rapor.ekle(
            "alembic_head",
            bool(surum) and (head is None or surum == head),
            f"staging={surum} kod_head={head}",
        )
    except Exception as exc:  # noqa: BLE001
        rapor.ekle("alembic_head", False, str(exc)[:200])

    # 4) Extension'lar
    try:
        satirlar = _db_sql(staging, "SELECT extname FROM pg_extension")
        extler = {r[0] for r in satirlar}
        rapor.timescale["extensions"] = sorted(extler)
        timescale_var = "timescaledb" in extler
        # KOSULLU KONTROL: TimescaleDB'yi kosulsuz zorunlu tutmak,
        # Timescale'siz kurulan bir sahanin HIC restore yapamamasi demekti
        # (gercek PostgreSQL uzerindeki uctan uca test bunu yakaladi).
        # Olcut: "uretimde varsa staging'de de olmali".
        beklenir = _uretimde_timescale_var()
        rapor.ekle(
            "timescaledb_extension",
            (timescale_var or not beklenir),
            ("kurulu" if timescale_var else
             ("YOK — uretimde var, staging'de olmali" if beklenir
              else "yok (uretimde de yok, beklenmiyor)")),
        )
    except Exception as exc:  # noqa: BLE001
        timescale_var = False
        rapor.ekle("timescaledb_extension", False, str(exc)[:200])

    # 5) TimescaleDB yapilari — yalnizca extension varsa anlamli.
    if timescale_var:
        try:
            hts = _timescale_kume(
                staging,
                "SELECT hypertable_name FROM timescaledb_information.hypertables",
            )
            uretim_hts = _timescale_kume(
                _production_db_name(),
                "SELECT hypertable_name FROM timescaledb_information.hypertables",
            )
            rapor.timescale["hypertables"] = sorted(hts)
            eksik_ht = sorted(uretim_hts - hts)
            rapor.ekle(
                "hypertable",
                not eksik_ht,
                f"eksik: {', '.join(eksik_ht)}" if eksik_ht
                else f"{len(hts)} hypertable",
            )

            caggs = _timescale_kume(
                staging,
                "SELECT view_name FROM timescaledb_information.continuous_aggregates",
            )
            uretim_caggs = _timescale_kume(
                _production_db_name(),
                "SELECT view_name FROM timescaledb_information.continuous_aggregates",
            )
            rapor.timescale["continuous_aggregates"] = sorted(caggs)
            eksik_cagg = sorted(uretim_caggs - caggs)
            rapor.ekle(
                "continuous_aggregates",
                not eksik_cagg,
                f"eksik: {', '.join(eksik_cagg)}" if eksik_cagg else "tamam",
            )

            isler = _db_sql(
                staging, "SELECT count(*) FROM timescaledb_information.jobs"
            )
            rapor.timescale["job_sayisi"] = int(isler[0][0]) if isler else 0
            # Politika isleri `ensure_historian_storage` tarafindan kurulur;
            # sayinin SIFIR olmasi historian onariminin kosmadigini gosterir.
            uretim_is = 0
            try:
                r = _db_sql(
                    _production_db_name(),
                    "SELECT count(*) FROM timescaledb_information.jobs",
                )
                uretim_is = int(r[0][0]) if r else 0
            except Exception:  # noqa: BLE001
                uretim_is = 0
            rapor.ekle(
                "timescale_politikalari",
                (rapor.timescale["job_sayisi"] > 0 or uretim_is == 0),
                f"staging={rapor.timescale['job_sayisi']} uretim={uretim_is}",
            )
        except Exception as exc:  # noqa: BLE001
            rapor.ekle("timescale_yapilari", False, str(exc)[:200])

    # 6) "Bos ama gecerli" ile "bozuk" ayrimi: en az bir kullanici tablosu
    #    ve alembic kaydi varsa sema kurulmus demektir. Satir SAYISI
    #    KONTROL EDILMEZ (is varsayimi yok).
    try:
        satir = _db_sql(
            staging,
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public'",
        )
        adet = int(satir[0][0]) if satir else 0
        # Sabit bir esik (or. ">=10 tablo") keyfi olurdu; "bozuk mu"
        # sorusunu ZORUNLU TABLOLAR kontrolu zaten cevapliyor. Burada
        # yalnizca "tamamen bos degil" araniyor.
        rapor.ekle("sema_bos_degil", adet > 0, f"{adet} tablo")
    except Exception as exc:  # noqa: BLE001
        rapor.ekle("sema_bos_degil", False, str(exc)[:200])

    return rapor.sonucla()


def _kod_head() -> str | None:
    """Bu imajin alembic head revizyonu."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        kok = Path(__file__).resolve().parents[2]
        cfg = Config(str(kok / "alembic.ini"))
        cfg.set_main_option("script_location", str(kok / "alembic_migrations"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001
        logger.debug("alembic_head_okunamadi", exc_info=True)
        return None


# ==========================================================================
# FAZ D — cutover
#
# CUTOVER ATOMIK DEGILDIR. Iki ayri `ALTER DATABASE ... RENAME` icerir ve
# aralarinda surec olebilir. Bu yuzden "atomik varsayip en iyisini ummak"
# yerine her gecis KALICI DURUM DOSYASINA yazilir; kurtarma kodu guc
# kesintisinden sonra hangi adimda kalindigini oradan ve veritabani
# ADLARINDAN okur (bkz. cutover_durumunu_coz).
# ==========================================================================


def _baglantilari_kes(dbname: str) -> int:
    """Hedef veritabanindaki TUM baglantilari kes.

    Kendi admin/kilit baglantilarimiz `postgres` veritabaninda oldugu icin
    bu cagri onlari ETKILEMEZ — yani restore kendi ayagini kesmez. Eski
    akista bu ayrim yoktu ve `e1_%` application_name filtresine bagliydi;
    filtre yanlis kurulursa restore kendini olduruyordu (yasanmis hata).
    """
    try:
        satir = _admin_sql(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()",
            params={"d": dbname},
        )
        return int(satir[0][0]) if satir else 0
    except Exception:  # noqa: BLE001
        logger.warning("cutover_terminate_failed db=%s", dbname, exc_info=True)
        return 0


def _yeni_baglantilari_engelle(dbname: str, engelle: bool) -> None:
    """`CONNECTION LIMIT` ile yeni baglantilari kis.

    DURUST SINIR: PostgreSQL'de `CONNECTION LIMIT` SUPERUSER'LARI BAGLAMAZ
    ve bu kurulumda uygulama rolu (`enerjione_grid`) superuser'dir. Yani bu
    cagri bir GARANTI degil, yalnizca yarisi daraltan bir onlemdir. Gercek
    korunma, kesme + rename'in ARDISIK ve kisa olmasi ve basarisizlikta
    yeniden denenmesidir.
    """
    try:
        limit = 0 if engelle else -1
        _admin_sql('ALTER DATABASE "' + dbname + '" CONNECTION LIMIT ' + str(limit))
    except Exception:  # noqa: BLE001
        logger.debug("connection_limit_ayarlanamadi db=%s", dbname, exc_info=True)


def _rename_db(eski: str, yeni: str) -> tuple[bool, str]:
    """`ALTER DATABASE ... RENAME` — kisa yeniden denemeli.

    Rename hedef veritabaninda SIFIR baglanti ister. Superuser'lar
    `CONNECTION LIMIT`i bypass ettigi icin bir worker tam o anda yeniden
    baglanabilir; bu durumda `ObjectInUse` alinir. Kisa araliklarla birkac
    kez denenir, her denemeden once baglantilar tekrar kesilir.
    """
    import time as _time

    son_hata = ""
    for deneme in range(1, _CUTOVER_RENAME_DENEME + 1):
        _baglantilari_kes(eski)
        try:
            _admin_sql('ALTER DATABASE "' + eski + '" RENAME TO "' + yeni + '"')
            return True, ""
        except Exception as exc:  # noqa: BLE001
            son_hata = str(exc)[:300]
            logger.warning(
                "cutover_rename_retry deneme=%d/%d %s->%s hata=%s",
                deneme, _CUTOVER_RENAME_DENEME, eski, yeni, son_hata,
            )
            _time.sleep(_CUTOVER_RENAME_BEKLE_SN)
    return False, son_hata


def cutover(state: RestoreState, staging: str) -> tuple[bool, str]:
    """Staging'i uretim yap. URETIME DOKUNAN ILK VE TEK ADIM BURASIDIR.

    Sira:
        1. kendi havuzumuzu birak (uretime bagli kalmayalim)
        2. yeni baglantilari kis + mevcutlari kes
        3. uretim -> `enerjione_grid_pre_<ts>`      [PRODUCTION_RENAMED]
        4. staging -> uretim adi                     [STAGING_PROMOTED]
        5. havuzu tazele

    3 basarisiz olursa hicbir sey degismemistir. 4 basarisiz olursa eski
    veritabani `_pre_<ts>` adiyla SAGLAM durur ve otomatik geri alinir —
    bu geri alma KANITLANABILIR sekilde guvenlidir cunku hedef ad o an
    bostur ve eski veritabaninin icerigine dokunulmamistir.
    """
    uretim = _production_db_name()
    rollback = rollback_db_name(uretim)
    state.rollback_db = rollback

    # 1) Kendi havuzumuz uretime bagli kalmasin.
    try:
        from app.db.session import engine as _engine

        _engine.dispose()
    except Exception:  # noqa: BLE001
        logger.warning("cutover_engine_dispose_failed", exc_info=True)

    # 2) Baglantilari daralt ve kes.
    _yeni_baglantilari_engelle(uretim, True)
    kesilen = _baglantilari_kes(uretim)
    durum_deposu.advance(
        state,
        durum_deposu.STAGE_CONNECTIONS_DRAINED,
        note=str(kesilen) + " baglanti kesildi",
    )

    # 3) URETIM -> ROLLBACK.  Bu satirdan onceki her sey geri alinabilirdi.
    ok, hata = _rename_db(uretim, rollback)
    if not ok:
        # Hicbir sey degismedi: uretim hala kendi adinda.
        _yeni_baglantilari_engelle(uretim, False)
        return False, (
            "Cutover yapilamadi, URETIM DEGISMEDI. Veritabani yeniden "
            "adlandirilamadi (aktif baglanti kapanmiyor olabilir): " + hata
        )
    durum_deposu.advance(
        state,
        durum_deposu.STAGE_PRODUCTION_RENAMED,
        note=uretim + " -> " + rollback,
    )

    # 4) STAGING -> URETIM.
    ok, hata = _rename_db(staging, uretim)
    if not ok:
        # Eski veritabani SAGLAM, adi degismis durumda. Geri alma guvenli:
        # hedef ad bos ve icerige dokunulmadi.
        logger.error("cutover_promote_failed — geri aliniyor: %s", hata)
        geri_ok, geri_hata = _rename_db(rollback, uretim)
        if geri_ok:
            _yeni_baglantilari_engelle(uretim, False)
            durum_deposu.advance(
                state,
                durum_deposu.STAGE_FAILED_SAFE,
                note="staging yukseltilemedi, eski veritabani geri alindi",
            )
            return False, (
                "Cutover yapilamadi; ESKI VERITABANI GERI ALINDI ve sistem "
                "restore oncesi haline dondu. Sebep: " + hata
            )
        durum_deposu.advance(
            state,
            durum_deposu.STAGE_FAILED_MANUAL,
            note="geri alma da basarisiz: " + geri_hata,
        )
        return False, (
            "KRITIK: cutover yarida kaldi ve otomatik geri alma da basarisiz "
            "oldu. Veriler KAYIP DEGIL — eski veritabani '" + rollback +
            "' adiyla duruyor. Elle kurtarma gerekli. Sebep: " + hata +
            " / geri alma: " + geri_hata
        )

    durum_deposu.advance(
        state,
        durum_deposu.STAGE_STAGING_PROMOTED,
        note=staging + " -> " + uretim,
    )

    # 5) Yeni uretime baglanabilelim.
    _yeni_baglantilari_engelle(uretim, False)
    try:
        from app.db.session import engine as _engine

        _engine.dispose()
    except Exception:  # noqa: BLE001
        pass
    return True, ""


def cutover_sonrasi_dogrula() -> DogrulamaRaporu:
    """Yeni uretim veritabani gercekten calisiyor mu?

    `pg_restore` sifir dondu diye restore'u BASARILI saymiyoruz; cutover
    sonrasi da ayni ilke gecerli. Burada uretim ADIYLA baglanip ayni
    kontroller tekrarlanir.
    """
    return staging_dogrula(_production_db_name())


# ==========================================================================
# FAZ E — oksuz staging / yarim cutover kurtarma
# ==========================================================================


def cutover_durumunu_coz() -> dict[str, Any]:
    """Acilista: yarim kalmis bir cutover var mi?

    IKI KAYNAK BIRLIKTE okunur — yalnizca birine guvenilmez:
      * kalici durum dosyasi (hangi asamada kalindigi)
      * veritabani ADLARI (gercekte ne oldugu)

    Durum dosyasi kayipsa ya da bozuksa OTOMATIK HICBIR SEY YAPILMAZ;
    tahmin etmek yanlis veritabanini dusurmeye giden yoldur.
    """
    uretim = _production_db_name()
    adlar = {r[0] for r in _mevcut_veritabanlari()}
    state = durum_deposu.read_state()

    sonuc: dict[str, Any] = {
        "uretim_var": uretim in adlar,
        "durum_dosyasi": None if state is None else state.stage,
        "staging_adaylari": sorted(a for a in adlar if staging_pattern(uretim).match(a)),
        "rollback_adaylari": sorted(a for a in adlar if rollback_pattern(uretim).match(a)),
        "mudahale_gerekli": False,
        "mesaj": "",
    }

    if uretim in adlar:
        # En yaygin ve iyi durum: uretim yerinde. Yarim kalmis bir restore
        # olabilir ama sistem CALISIR haldedir.
        if state is not None and not state.is_terminal() and state.is_stale():
            sonuc["mesaj"] = (
                "Yarim kalmis bir restore kaydi var ama uretim veritabani "
                "yerinde ve calisiyor. Artik staging veritabani(lari) "
                "incelenip elle temizlenebilir."
            )
        return sonuc

    # URETIM ADINDA VERITABANI YOK — backend acilamaz.
    sonuc["mudahale_gerekli"] = True
    if state is not None and state.cutover_in_flight() and state.rollback_db:
        sonuc["mesaj"] = (
            "Cutover yarida kalmis. Veriler KAYIP DEGIL: onceki uretim "
            "veritabani '" + state.rollback_db + "' adiyla duruyor. Geri "
            'almak icin: ALTER DATABASE "' + state.rollback_db +
            '" RENAME TO "' + uretim + '";'
        )
    elif sonuc["rollback_adaylari"]:
        sonuc["mesaj"] = (
            "Uretim veritabani bulunamadi. Durum dosyasi okunamadi, ama geri "
            "alinabilecek aday(lar) var: " +
            ", ".join(sonuc["rollback_adaylari"]) +
            ". Hangisinin dogru oldugunu DOGRULAMADAN yeniden adlandirmayin."
        )
    else:
        sonuc["mesaj"] = (
            "Uretim veritabani bulunamadi ve geri alinabilecek bir aday da "
            "yok. Elle kurtarma gerekli."
        )
    return sonuc


def oksuz_staging_listele() -> list[dict[str, Any]]:
    """Silinebilir gorunen staging veritabanlari — SILME YAPMAZ.

    IKI KANIT KURALI. Bir veritabani ancak SU IKISI DE saglanirsa
    "silinebilir" isaretlenir:

      1. Adi TAM OLARAK `enerjione_grid_stg_<tamsayi>` kalibinda
         (onek eslesmesi DEGIL — `..._stg_deneme` gibi elle yaratilmis bir
         veritabanini kapsam disinda tutar),
      2. Kalici durum dosyasindaki is kimligi ile ESLESIYOR ve o kayit
         artik ilerlemiyor (terminal ya da sahipsiz).

    Kanitlardan biri eksikse veritabani "belirsiz" olarak isaretlenir ve
    SILINMEZ; operatore gosterilir. Yanlis bir `DROP DATABASE`in bedeli,
    birkac yuz MB'lik artik alandan kat kat buyuktur.
    """
    state = durum_deposu.read_state()
    kalip = staging_pattern(_production_db_name())
    adlar = [r[0] for r in _mevcut_veritabanlari()]
    sonuc: list[dict[str, Any]] = []
    for ad in sorted(adlar):
        m = kalip.match(ad)
        if not m:
            continue
        job_id = int(m.group(1))
        kanit_durum = (
            state is not None
            and state.job_id == job_id
            and (state.is_terminal() or state.is_stale())
        )
        sonuc.append(
            {
                "db": ad,
                "job_id": job_id,
                "kanit_isim": True,
                "kanit_durum": bool(kanit_durum),
                "silinebilir": bool(kanit_durum),
                "not": (
                    "durum dosyasiyla eslesti, guvenle silinebilir"
                    if kanit_durum
                    else "durum kaydi yok/eslesmiyor — ELLE dogrulanmali"
                ),
            }
        )
    return sonuc


def oksuz_staging_dusur(dbname: str) -> tuple[bool, str]:
    """Operatorun ACIKCA istedigi staging veritabanini dusur.

    Iki kanit kurali BURADA DA uygulanir: liste "silinebilir" demiyorsa
    istek reddedilir. Operatorun tek tikla yanlis veritabanini dusurmesi
    mumkun olmamali.
    """
    for kayit in oksuz_staging_listele():
        if kayit["db"] == dbname:
            if not kayit["silinebilir"]:
                return False, (
                    "Bu veritabani guvenle silinebilir olarak isaretlenmedi: "
                    + str(kayit["not"])
                )
            staging_dusur(dbname)
            logger.warning("oksuz_staging_dusuruldu db=%s", dbname)
            return True, ""
    return False, "Boyle bir staging veritabani yok (ad kalibi eslesmiyor)."


def eski_rollback_temizle(korunacak: str | None) -> int:
    """Bir onceki restore'dan kalan rollback veritabanlarini dusur.

    YALNIZCA basarili bir cutover + cutover sonrasi dogrulamadan SONRA
    cagrilir. Gerekcesi: o noktada elde DAHA YENI bir geri donus noktasi
    vardir, dolayisiyla eskisini birakmak guvenlik katmiyor, yalnizca disk
    yiyor. Preflight'ta silmek ise tam tersi olurdu — yeni restore
    dogrulanmadan mevcut geri donus noktasini yok etmek.
    """
    dusurulen = 0
    kalip = rollback_pattern(_production_db_name())
    for (ad,) in _mevcut_veritabanlari():
        if not kalip.match(ad):
            continue
        if korunacak and ad == korunacak:
            continue
        try:
            _admin_sql('DROP DATABASE IF EXISTS "' + ad + '"')
            dusurulen += 1
            logger.info("eski_rollback_dusuruldu db=%s", ad)
        except Exception:  # noqa: BLE001
            logger.warning("eski_rollback_dusurulemedi db=%s", ad, exc_info=True)
    return dusurulen


# ==========================================================================
# ORKESTRATOR
# ==========================================================================


def run(job_id: int, dump_path: Path, *, started_by: str = "(system)") -> tuple[bool, str]:
    """Guvenli restore akisinin tamami.

    Donus: (basarili, hata_mesaji). Istisna FIRLATMAZ — cagiran (API arka
    plan thread'i) her kosulda temiz bir sonuc gorur.

    URETIM VERITABANI, `cutover()` cagrilana kadar HICBIR SEKILDE
    degistirilmez. O noktaya kadar olusan her hata staging'i dusurup
    doner; sistem restore oncesi haliyle calismaya devam eder.
    """
    from app.services import restore_status_tracker as izleme

    uretim = _production_db_name()
    staging = staging_db_name(job_id, uretim)
    state = durum_deposu.new_state(
        job_id=job_id,
        backup_file=str(dump_path),
        started_by=started_by,
        production_db=uretim,
    )

    kilit = RestoreLock()
    try:
        # --- FAZ A: kilit -------------------------------------------------
        izleme.set_step("preflight", "On kontroller yapiliyor...")
        try:
            kilit.acquire()
        except KilitAlinamadi as exc:
            return False, str(exc)

        try:
            durum_deposu.write_state(state)
        except Exception as exc:  # noqa: BLE001
            # Durum yazilamiyorsa kurtarilamaz bir restore'a GIRMEYIZ.
            return False, (
                "Restore durum dosyasi yazilamadi, islem baslatilmadi: "
                + str(exc)[:200]
            )

        # --- FAZ A: arsiv -------------------------------------------------
        izleme.set_step("validating_archive", "Yedek arsivi dogrulaniyor...")
        durum_deposu.advance(state, durum_deposu.STAGE_VALIDATING_ARCHIVE)
        ok, hata = arsiv_on_dogrula(dump_path)
        if not ok:
            return _basarisiz(state, izleme, hata, staging=None)

        # Istemci/sunucu ana surum uyumu — erken ve acik hata.
        ok, hata = istemci_sunucu_surum_uyumu()
        if not ok:
            return _basarisiz(state, izleme, hata, staging=None)

        # --- FAZ A: disk --------------------------------------------------
        ok, hata, olculer = disk_preflight(dump_path)
        logger.info("restore_disk_preflight %s", olculer)
        if not ok:
            return _basarisiz(state, izleme, hata, staging=None)

        # --- FAZ B: staging ----------------------------------------------
        izleme.set_step("creating_staging", "Gecici veritabani olusturuluyor...")
        durum_deposu.advance(state, durum_deposu.STAGE_CREATING_STAGING)
        state.staging_db = staging
        try:
            staging_dusur(staging)  # onceki denemeden kalmis olabilir
            staging_yarat(staging)
        except Exception as exc:  # noqa: BLE001
            return _basarisiz(
                state, izleme,
                "Gecici veritabani olusturulamadi: " + str(exc)[:300],
                staging=None,
            )

        izleme.set_step("restoring", "Yedek gecici veritabanina yukleniyor...")
        durum_deposu.advance(state, durum_deposu.STAGE_RESTORING)
        ok, hata = staginge_restore_et(staging, dump_path)
        if not ok:
            return _basarisiz(state, izleme, hata, staging=staging)

        # --- FAZ C: migration --------------------------------------------
        izleme.set_step("migrating", "Sema guncelleniyor...")
        durum_deposu.advance(state, durum_deposu.STAGE_MIGRATING)
        ok, hata = staging_migrate(staging)
        if not ok:
            return _basarisiz(state, izleme, hata, staging=staging)

        # --- FAZ C: dogrulama --------------------------------------------
        izleme.set_step("validating_staging", "Geri yuklenen veri dogrulaniyor...")
        durum_deposu.advance(state, durum_deposu.STAGE_VALIDATING_STAGING)
        rapor = staging_dogrula(staging)
        if not rapor.ok:
            # Staging BILEREK dusurulmez: operator neyin yanlis oldugunu
            # inceleyebilsin. Uretim zaten dokunulmamis calisiyor.
            return _basarisiz(
                state, izleme,
                "Geri yuklenen veri dogrulamayi gecemedi: " + rapor.ozet(),
                staging=None,
            )

        # --- FAZ D: cutover ----------------------------------------------
        izleme.set_step("preparing_cutover", "Gecise hazirlaniliyor...")
        durum_deposu.advance(state, durum_deposu.STAGE_PREPARING_CUTOVER)
        izleme.set_step("cutover", "Veritabani degistiriliyor (kisa kesinti)...")
        ok, hata = cutover(state, staging)
        if not ok:
            izleme.fail(hata)
            return False, hata

        # --- FAZ D: cutover sonrasi --------------------------------------
        izleme.set_step("post_validation", "Yeni veritabani dogrulaniyor...")
        durum_deposu.advance(state, durum_deposu.STAGE_POST_VALIDATING)
        son_rapor = cutover_sonrasi_dogrula()
        if not son_rapor.ok:
            # OTOMATIK GERI ALMA YAPILMAZ. Cutover ile bu an arasinda
            # worker'lar yeni veritabanina baglanip yazmis olabilir; ikinci
            # bir yikici takas "korlemesine" olurdu. Eski veritabani
            # duruyor ve operatore net talimat veriliyor.
            mesaj = (
                "Gecis tamamlandi ancak dogrulama basarisiz: "
                + son_rapor.ozet()
                + ". Onceki veritabani '" + str(state.rollback_db)
                + "' adiyla KORUNUYOR; geri donmek icin operator onayi gerekli."
            )
            durum_deposu.advance(
                state, durum_deposu.STAGE_FAILED_MANUAL, note=mesaj
            )
            izleme.fail(mesaj)
            return False, mesaj

        # --- Basarili: ARTIK eski rollback'leri temizleyebiliriz ----------
        durum_deposu.advance(state, durum_deposu.STAGE_COMPLETED)
        try:
            eski_rollback_temizle(korunacak=state.rollback_db)
        except Exception:  # noqa: BLE001
            logger.warning("eski_rollback_temizligi_basarisiz", exc_info=True)

        izleme.finish_ok()
        return True, ""

    except Exception as exc:  # noqa: BLE001
        logger.exception("safe_restore_beklenmedik_hata")
        mesaj = "Beklenmedik hata: " + str(exc)[:300]
        try:
            durum_deposu.advance(state, durum_deposu.STAGE_FAILED_SAFE, note=mesaj)
        except Exception:  # noqa: BLE001
            pass
        izleme.fail(mesaj)
        return False, mesaj
    finally:
        kilit.release()


def _basarisiz(
    state: RestoreState,
    izleme: Any,
    hata: str,
    *,
    staging: str | None,
) -> tuple[bool, str]:
    """Cutover ONCESI basarisizlik: staging temizle, uretim DEGISMEDI."""
    if staging:
        staging_dusur(staging)
    mesaj = hata + " (Uretim veritabani DEGISTIRILMEDI.)"
    durum_deposu.advance(state, durum_deposu.STAGE_FAILED_SAFE, note=hata)
    izleme.fail(mesaj)
    return False, mesaj


def _uretimde_timescale_var() -> bool:
    """Mevcut uretim veritabaninda TimescaleDB kurulu mu?

    Staging dogrulamasinda TimescaleDB'yi KOSULLU yapar: uretimde varsa
    staging'de de olmasi ZORUNLUDUR (aksi halde restore sonrasi historian
    calismaz); uretimde yoksa aranmaz. Kosulsuz zorunlu tutmak,
    TimescaleDB'siz kurulan bir sahayi restore edemez hale getirirdi.
    """
    try:
        satir = _db_sql(
            _production_db_name(),
            "SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'",
        )
        return bool(satir and int(satir[0][0]) > 0)
    except Exception:  # noqa: BLE001
        # Uretim okunamiyorsa BEKLEME (fail-open degil, fail-safe): eksik
        # bir extension yuzunden gecerli bir restore reddedilmesin; asil
        # koruma zorunlu tablo ve alembic kontrolleri.
        return False


def _timescale_kume(dbname: str, sql: str) -> set[str]:
    """TimescaleDB katalog sorgusu — okunamazsa BOS kume.

    Bos donmek guvenlidir: karsilastirma "uretimde ne varsa staging'de de
    olmali" seklinde yapiliyor, dolayisiyla uretim okunamadiginda kontrol
    kendiliginden gecer ve gecerli bir restore yanlislikla reddedilmez.
    """
    try:
        return {r[0] for r in _db_sql(dbname, sql)}
    except Exception:  # noqa: BLE001
        return set()


# ==========================================================================
# ACILISTA KURTARMA TESPITI
#
# YALNIZCA TESPIT VE RAPORLAMA. Hicbir veritabani otomatik DROP EDILMEZ.
# Yanlis bir `DROP DATABASE`in bedeli, birkac yuz MB'lik artik alandan kat
# kat buyuktur; belirsiz her durum operatore birakilir.
# ==========================================================================


def acilista_kurtarma_kontrolu() -> dict[str, Any]:
    """Acilista yarim kalmis restore / artik staging var mi?

    NEREDE KOSAR: `leader.register` ile TEK surecte. Bu onemli — birden
    fazla uvicorn sureci ayni tespiti yapsaydi her acilista N kopya olay
    kaydi olusur, olay tablosu ve bildirimler gurultuye bogulurdu.

    NE YAPAR: durumu okur, olay kaydi yazar, `restore_status_tracker`i
    "kurtarma gerekli" durumuna alir.

    NE YAPMAZ: silme. Artik staging veritabanlari yerinde birakilir.
    """
    from app.db.session import SessionLocal
    from app.services import restore_status_tracker as izleme
    from app.services.event_service import record_event

    try:
        cutover = cutover_durumunu_coz()
        artiklar = oksuz_staging_listele()
    except Exception:  # noqa: BLE001
        # Veritabani henuz hazir olmayabilir; acilisi BLOKLAMA.
        logger.debug("acilista_kurtarma_kontrolu_atlandi", exc_info=True)
        return {"kontrol_edildi": False}

    sonuc = {
        "kontrol_edildi": True,
        "mudahale_gerekli": bool(cutover.get("mudahale_gerekli")),
        "artik_staging": [a["db"] for a in artiklar],
        "mesaj": cutover.get("mesaj", ""),
    }

    if not sonuc["mudahale_gerekli"] and not artiklar:
        return sonuc  # temiz acilis — sessiz kal

    # Operatore gorunur kil.
    if sonuc["mudahale_gerekli"]:
        logger.error("restore_kurtarma_gerekli %s", cutover.get("mesaj"))
        izleme.fail(cutover.get("mesaj") or "Yarim kalmis restore tespit edildi")
    else:
        logger.warning(
            "restore_artik_staging db=%s — otomatik SILINMEDI",
            ", ".join(sonuc["artik_staging"]),
        )

    db = SessionLocal()
    try:
        record_event(
            db,
            category="backup",
            event_type=(
                "restore_recovery_required"
                if sonuc["mudahale_gerekli"]
                else "restore_orphan_staging_detected"
            ),
            severity="critical" if sonuc["mudahale_gerekli"] else "warning",
            actor_username="(system)",
            message=(
                cutover.get("mesaj")
                or (
                    "Onceki restore'dan kalan gecici veritabani(lari) bulundu: "
                    + ", ".join(sonuc["artik_staging"])
                    + " (otomatik silinmedi)"
                )
            ),
            metadata={
                "mudahale_gerekli": sonuc["mudahale_gerekli"],
                "artik_staging": sonuc["artik_staging"],
                "rollback_adaylari": cutover.get("rollback_adaylari", []),
                "durum_dosyasi": cutover.get("durum_dosyasi"),
            },
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("restore_kurtarma_olayi_yazilamadi")
    finally:
        db.close()

    return sonuc


def _acilista_kurtarma_baslat() -> None:
    """`leader.register` icin baslatici — acilisi bloklamaz."""
    import threading as _t

    def _calis() -> None:
        try:
            acilista_kurtarma_kontrolu()
        except Exception:  # noqa: BLE001
            logger.exception("acilista_kurtarma_kontrolu_basarisiz")

    _t.Thread(target=_calis, name="restore-recovery-check", daemon=True).start()
