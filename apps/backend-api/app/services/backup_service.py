"""DB Yedekleme servisi.

Sorumluluk:
  - pg_dump ile DB yedek dosyasi uretmek (custom format = .dump)
  - pg_restore ile yedekten geri yuklemek (DROP / CREATE schema sonrasi)
  - retention'a gore eski dosyalari silmek

DB baglanti bilgilerini settings.database_url'den parse eder. URL formati:
  postgresql+psycopg2://USER:PASSWORD@HOST:PORT/DB

Env override:
  BACKUP_DIR  — yedek dosyalari icin dizin (default: ./backups)
  PG_DUMP     — pg_dump binary path (default: pg_dump, PATH'te aranir)
  PG_RESTORE  — pg_restore binary path (default: pg_restore)
  PSQL        — psql binary (geri yuklerken DROP DATABASE icin gerekli olabilir)
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.backup import BackupJob, BackupSchedule

logger = logging.getLogger(__name__)


# Yedek dosyasinin pg_dump custom-format magic header'i — operator harici
# bir dosyayi (rastgele binary, SQL plain-text vs.) yuklemesin diye restore
# oncesi dogrulariz. pg_dump custom format dosyalari "PGDMP" ile basliyor.
_PG_DUMP_CUSTOM_MAGIC = b"PGDMP"

# pg_restore icindeki tehlikeli SQL pattern'leri — RCE / privilege escalation
# vektorleri. Saldirgan engineer yetkisini ele gecirip kendi dump'ini upload
# edip restore tetiklerse, asagidaki SQL'ler postgres process'i icinde
# arbitrary command execution saglar:
#   * COPY ... FROM PROGRAM 'sh': postgres user'i ile shell command
#   * CREATE FUNCTION ... LANGUAGE plpython3u/plperlu/c: untrusted langs
#   * LOAD '/path/to/lib.so': arbitrary shared object load
#   * CREATE EXTENSION ... (file_fdw, postgres_fdw, dblink): network/fs access
# Validation HOTFIX olarak text-search; gercek izolasyon icin ayri OS user
# + restricted postgres role (no SUPERUSER, no pg_execute_server_program)
# olmali. Bu kontrol best-effort defansif derinlik.
_DUMP_DANGEROUS_PATTERNS = (
    b"COPY ",         # COPY FROM PROGRAM tek tehlikeli, ama TOC ham gormiyor
                       # — bu yerine daha ozel pattern'leri arayalim
    b"FROM PROGRAM",
    b"LANGUAGE plpython3u",
    b"LANGUAGE plpythonu",
    b"LANGUAGE plperlu",
    b"LANGUAGE c\n",
    b"LANGUAGE 'c'",
    b"CREATE EXTENSION file_fdw",
    b"CREATE EXTENSION postgres_fdw",
    b"CREATE EXTENSION dblink",
    b"LOAD '",
    # GRANT/REVOKE manuel yapilirsa security label degisebilir
    b"ALTER ROLE postgres",
    b"CREATE ROLE postgres",
)


def validate_dump_file(file_path: Path, *, max_scan_bytes: int = 50 * 1024 * 1024) -> tuple[bool, str]:
    """Restore oncesi yedek dosyasinin format + icerik dogrulamasi.

    Iki katmanli kontrol:
      1. Magic header: dosya pg_dump custom format mi (PGDMP signature)?
         Plain SQL dump'lar veya rastgele binary'ler reddedilir; sadece
         `-F c` ile alinan dump'lara izin verilir (CI'da bunu uretiyoruz).
      2. Tehlikeli SQL pattern taramasi: TOC verisi sikistirilmis olsa da
         dosyanin ilk N byte'inda (header + TOC index) bazi pattern'ler
         duz metin gorunur. COPY FROM PROGRAM gibi RCE vektorleri varsa
         reddet.

    Pattern taramasi best-effort: pg_dump custom format gzip-compressed
    TOC data icerir, oraya enjekte edilen string'i goremeyebiliriz. Bu
    sebep ile bunu TEMEL guvenlik degil DERINLIKTE SAVUNMA olarak
    konumlandiriyoruz; gercek izolasyon: pg_restore'u SUPERUSER olmayan
    bir role ile + `--no-superuser-statements` (PG 17+) ile cagirmak,
    PG_*_PROGRAM yetkisini iptal etmek vs.

    Returns: (ok, error_message). ok=False ise restore yapilmamali.
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(5)
            if head != _PG_DUMP_CUSTOM_MAGIC:
                return False, (
                    f"Yedek dosyasi pg_dump custom-format degil (magic header: "
                    f"{head!r}). Sadece `pg_dump -F c` ile alinan dosyalar kabul edilir."
                )
            f.seek(0)
            scanned = 0
            chunk_size = 1024 * 1024  # 1 MB
            buf_tail = b""
            while scanned < max_scan_bytes:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                # Pattern boundary'lerini kacirmayalim — 64 byte overlap.
                window = buf_tail + chunk
                up = window.upper()
                for needle in _DUMP_DANGEROUS_PATTERNS:
                    if needle.upper() in up:
                        return False, (
                            f"Yedek dosyasinda tehlikeli SQL pattern tespit edildi: "
                            f"{needle.decode('latin1', errors='replace')}. "
                            f"RCE riski olabilir; restore reddedildi."
                        )
                buf_tail = chunk[-64:]
                scanned += len(chunk)
        return True, ""
    except OSError as exc:
        return False, f"Yedek dosyasi okunamadi: {exc}"


def get_backup_dir() -> Path:
    """Yedek dosyalari icin diskte hedef dizin (.dump cikti yolu).

    BACKUP_DIR env ile override edilebilir; container'da volume olarak
    /var/lib/hsl-backups baglanir.
    """
    raw = os.getenv("BACKUP_DIR", "./backups")
    p = Path(raw).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# Geriye-doneuk uyumluluk: eski cagiri taraflari _backup_dir kullanmaya
# devam edebilir.
_backup_dir = get_backup_dir


def _parse_db_url(url: str) -> dict:
    """SQLAlchemy URL'den pg_dump'in cozebilecegi env doner."""
    # SQLAlchemy: postgresql+psycopg2://user:pass@host:port/db
    # urlparse postgresql+psycopg2'yi schema olarak goruyor; once + sonrasini at.
    if "://" in url and "+" in url.split("://", 1)[0]:
        proto, rest = url.split("://", 1)
        clean = proto.split("+", 1)[0] + "://" + rest
    else:
        clean = url
    u = urlparse(clean)
    return {
        "host": u.hostname or "localhost",
        "port": str(u.port or 5432),
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "dbname": (u.path or "/").lstrip("/") or "horstman",
    }


def _pg_env() -> dict[str, str]:
    db = _parse_db_url(settings.database_url)
    env = os.environ.copy()
    if db["password"]:
        env["PGPASSWORD"] = db["password"]
    return env


# Yedekten haric tutulan tablolar (sadece schema yedeklenir, veri haric).
# Telemetri/olay/queue/notification gibi operasyonel veriler hizli buyuyup
# yedek dosyasini gereksizce sisirir. Geri yuklemede bu tablolar bos
# kalir, sistem yeniden veri toplamaya devam eder. Config tablolari
# (users, gateways, devices, regions/lines/poles/segments, signal_catalog,
# alarm_rules, outbound_targets, notification_settings, project_settings,
# responsibility_areas, user_notification_preferences) tam veri ile yedeklenir.
EXCLUDED_DATA_TABLES = (
    "telemetry",
    "alarm_events",
    "alarm_comments",
    "fault_events",
    "fault_comments",
    "system_events",
    "notifications",
    "outbox_events",
    "processed_messages",
    "gateway_ingest_batches",
    # backup_jobs ve backup_schedule kendisi de geri yuklenince eski
    # gecmisi getirir; karisikligi onlemek icin de schema-only.
    "backup_jobs",
    "backup_schedule",
)


def run_pg_dump(file_path: Path) -> tuple[bool, str]:
    """pg_dump calistir, custom format (.dump) yaz. (success, error_msg).

    EXCLUDED_DATA_TABLES tablolari icin --exclude-table-data kullanilir:
    schema (CREATE TABLE) yedek dosyasinda kalir, ama satir verisi atlanir.
    Bu sayede yedek dosyasi yalnizca config/ayar verilerini icerir ve
    onemli olcude kucuk olur. Geri yuklemede bu tablolar bos haliyle
    yeniden olusturulur, sistem normal calismasina devam eder.
    """
    db = _parse_db_url(settings.database_url)
    pg_dump = os.getenv("PG_DUMP", "pg_dump")
    cmd = [
        pg_dump,
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", db["dbname"],
        "-F", "c",  # custom format (pg_restore uyumlu, sıkıştırılmış)
        "-f", str(file_path),
        "--no-owner",
        "--no-acl",
    ]
    for tbl in EXCLUDED_DATA_TABLES:
        cmd.extend(["--exclude-table-data", tbl])
    try:
        completed = subprocess.run(
            cmd,
            env=_pg_env(),
            capture_output=True,
            text=True,
            timeout=900,  # 15 dk
        )
        if completed.returncode != 0:
            return False, (completed.stderr or "pg_dump non-zero exit")[:1900]
        return True, ""
    except FileNotFoundError:
        return False, "pg_dump bulunamadı (PATH'te olmali veya PG_DUMP env ile yol verin)."
    except subprocess.TimeoutExpired:
        return False, "pg_dump zaman aşımı (15 dk)."
    except Exception as exc:  # noqa: BLE001
        return False, f"pg_dump hatasi: {exc}"


_LEGACY_ROLES = ["horstman", "hsl", "horstmann"]


def _ensure_legacy_roles_exist(db_conn_info: dict) -> None:
    """Eski rebrand oncesi yedeklerden restore yaparken pg_restore icindeki
    GRANT/OWNER referanslarinin `role does not exist` hatasi atmamasi icin
    olasi eski role isimlerini gecici olarak yaratir (NOLOGIN, sifre yok).

    Idempotent: zaten varsa dokunmaz. Yedek yeni stack'te alindiysa hicbir
    sey degismez (sadece CREATE ROLE IF NOT EXISTS tarzi SQL calistirir).

    Rebrand sonrasi geri uyumluluk icin gerekli — kullanici eski `horstman`
    DB'sinden aldigi .dump dosyasini yeni `enerjione` DB'sine restore
    edebiliyor olmali (sahadaki tarihi yedekler kaybolmasin).
    """
    psql = os.getenv("PSQL", "psql")
    for role in _LEGACY_ROLES:
        # PostgreSQL'de "CREATE ROLE IF NOT EXISTS" yok; bunun yerine
        # DO bloku ile pg_roles'a bakip yoksa yaratiyoruz.
        sql = (
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN "
            f"CREATE ROLE \"{role}\" NOLOGIN; "
            f"END IF; "
            f"END $$;"
        )
        try:
            subprocess.run(
                [
                    psql,
                    "-h", db_conn_info["host"],
                    "-p", db_conn_info["port"],
                    "-U", db_conn_info["user"],
                    "-d", db_conn_info["dbname"],
                    "-c", sql,
                ],
                env=_pg_env(),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,  # role yaratma hatasi kritik degil, restore zaten cogu sey'i atlar
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("ensure_legacy_role_failed role=%s error=%s", role, exc)


def run_pg_restore(file_path: Path) -> tuple[bool, str]:
    """pg_restore calistir — mevcut tablolarin uzerine clean+create modunda
    yazar. UYARI: Mevcut tum DB icerigi silinir.

    Eski (rebrand oncesi) yedeklerle uyumluluk: restore'dan once olasi eski
    role isimlerini (horstman/hsl) gecici olarak yaratiyoruz. Boylece dump
    icindeki GRANT/OWNER referanslari "role does not exist" hatasi atmiyor.
    """
    db = _parse_db_url(settings.database_url)
    # Legacy-aware: eski yedeklerdeki horstman/hsl role'lerini gecici olarak
    # yarat (idempotent, NOLOGIN). Yeni yedeklerde no-op.
    _ensure_legacy_roles_exist(db)
    pg_restore = os.getenv("PG_RESTORE", "pg_restore")
    cmd = [
        pg_restore,
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", db["dbname"],
        "--clean",  # mevcut objeleri DROP et
        "--if-exists",  # yoksa hata verme
        "--no-owner",
        "--no-acl",
        str(file_path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            env=_pg_env(),
            capture_output=True,
            text=True,
            timeout=1200,
        )
        # pg_restore kismi hatalar icin non-zero donebilir; kritik bir
        # error olmadigi surece basari sayalim. Ama yine de stderr loglanir.
        # `role ... does not exist` ve `permission denied` benzeri hatalar
        # --no-owner/--no-acl ile zaten atlanmasi gerekiyor, ama bazi dump
        # versiyonlarinda yine cikabilir; bunlari non-kritik sayiyoruz.
        stderr_lower = (completed.stderr or "").lower()
        fatal_error = (
            "fatal" in stderr_lower
            or "could not connect" in stderr_lower
            or "database does not exist" in stderr_lower
        )
        if completed.returncode != 0 and fatal_error:
            return False, (completed.stderr or "pg_restore connection error")[:1900]
        # Stderr varsa truncate edip basari mesaji ile birlikte don
        # (kullanici warning'leri gorsun istiyorsa).
        return True, (completed.stderr or "")[:500]
    except FileNotFoundError:
        return False, "pg_restore bulunamadı (PATH'te olmali veya PG_RESTORE env ile yol verin)."
    except subprocess.TimeoutExpired:
        return False, "pg_restore zaman aşımı."
    except Exception as exc:  # noqa: BLE001
        return False, f"pg_restore hatasi: {exc}"


def create_backup(
    db: Session, *, job_type: str = "manual", username: str | None = None
) -> BackupJob:
    """Yeni bir BackupJob kaydi olustur ve pg_dump'i calistir."""
    now = datetime.now(timezone.utc)
    fname = f"hsl-{now.strftime('%Y%m%d-%H%M%S')}-{job_type}.dump"
    target = _backup_dir() / fname
    job = BackupJob(
        job_type=job_type,
        status="running",
        file_path=str(target),
        created_by_username=username,
        created_at=now,
    )
    db.add(job)
    db.flush()
    db.commit()  # job kaydini hemen yaz, uzun sured operasyon

    ok, err = run_pg_dump(target)
    job = db.get(BackupJob, job.id) or job
    if ok:
        try:
            size = target.stat().st_size
        except OSError:
            size = None
        job.status = "success"
        job.size_bytes = size
        job.completed_at = datetime.now(timezone.utc)
    else:
        job.status = "failed"
        job.error_message = err
        job.completed_at = datetime.now(timezone.utc)
        # Eksik/yarim dosya kalmasin
        try:
            if target.exists():
                target.unlink()
        except OSError:
            pass
        job.file_path = None
    db.commit()
    return job


def restore_backup(db: Session, job: BackupJob) -> tuple[bool, str]:
    """Verilen yedek kaydini geri yukle. Backup file diskte olmali.

    UYARI: Mevcut DB icerigi tamamen degisir. Cagiran tarafin kullanici
    onayi alip, audit event yazip cagirmasi gerekir.

    pg_restore --clean DB'deki tum tablolari DROP edip yeniden olusturur;
    SQLAlchemy connection pool'undaki acik connection'lar bu yuzden stale
    kalir (cached prepared statement'lar gecersiz olur). Restore basariliysa
    pool'u tamamen dispose edip yeni connection'larin temiz acilmasini
    saglariz; aksi halde sonraki sorgular 'cached plan must not change
    result type' veya benzeri PG hatasi alabilir.
    """
    if not job.file_path:
        return False, "Yedek dosya yolu yok."
    p = Path(job.file_path)
    if not p.exists():
        return False, f"Yedek dosyasi bulunamadi: {p.name}"
    # Restore oncesi format + icerik dogrulama (RCE / privilege escalation
    # vektorlerini reddet). Yukleme sirasinda da kontrol ediyoruz; burada
    # ikinci kez kontrol etmek defansif: DB'de saklanan eski/legacy job
    # dosyalari da gecsin.
    valid, validation_err = validate_dump_file(p)
    if not valid:
        logger.error("backup_restore_validation_failed file=%s reason=%s", p.name, validation_err)
        return False, validation_err
    ok, msg = run_pg_restore(p)
    if ok:
        try:
            from app.db.session import engine as _engine
            _engine.dispose()
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("engine_dispose_after_restore_failed")
    return ok, msg


def apply_retention(db: Session, retention_count: int) -> int:
    """retention_count'tan eski yedekleri (success durumdaki) sil.

    Sadece scheduled job'lar arasinda retention uygulanir; manual job'lar
    kullanici tarafindan elle silinmedikce kalir."""
    if retention_count <= 0:
        return 0
    rows = list(
        db.scalars(
            select(BackupJob)
            .where(BackupJob.status == "success")
            .where(BackupJob.job_type == "scheduled")
            .order_by(BackupJob.created_at.desc())
        ).all()
    )
    keep = rows[:retention_count]
    drop = rows[retention_count:]
    deleted = 0
    keep_ids = {k.id for k in keep}
    for r in drop:
        try:
            if r.file_path:
                fp = Path(r.file_path)
                if fp.exists():
                    fp.unlink()
        except OSError:
            pass
        db.delete(r)
        deleted += 1
    _ = keep_ids
    if deleted > 0:
        db.commit()
    return deleted


def delete_backup_file(job: BackupJob) -> None:
    if not job.file_path:
        return
    try:
        fp = Path(job.file_path)
        if fp.exists():
            fp.unlink()
    except OSError as exc:  # noqa: BLE001
        logger.warning("backup_delete_file_failed id=%s error=%s", job.id, exc)


def get_or_create_schedule(db: Session) -> BackupSchedule:
    sch = db.get(BackupSchedule, 1)
    if sch is None:
        sch = BackupSchedule(
            id=1,
            enabled=False,
            interval_hours=24,
            retention_count=7,
        )
        db.add(sch)
        db.commit()
        db.refresh(sch)
    return sch
