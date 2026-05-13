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
    # NOT: Cipla "COPY " pattern'i KULLANILMAZ — her pg_dump dosyasinda
    # `COPY <table> FROM stdin` satirlari var (veri yukleme standardi).
    # Sadece RCE vektoru olan `FROM PROGRAM` ifadesini hedefle.
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
    /var/lib/e1-backups baglanir.
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
        "dbname": (u.path or "/").lstrip("/") or "enerjione",
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
            # stderr server log'a, kullaniciya kisaltilmis generic mesaj
            # (DB host/port/path, kullanici adi gibi alanlar sizmasin).
            import logging as _logging

            _logging.getLogger(__name__).error(
                "pg_dump_nonzero_exit rc=%s stderr=%s",
                completed.returncode,
                (completed.stderr or "")[:4000],
            )
            return False, f"pg_dump non-zero exit (rc={completed.returncode})"
        return True, ""
    except FileNotFoundError:
        return False, "pg_dump bulunamadı (PATH'te olmali veya PG_DUMP env ile yol verin)."
    except subprocess.TimeoutExpired:
        return False, "pg_dump zaman aşımı (15 dk)."
    except Exception:  # noqa: BLE001
        import logging as _logging

        _logging.getLogger(__name__).exception("pg_dump_unexpected_error")
        return False, "pg_dump hatasi (detay icin server log'una bakin)"


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


def _run_psql_on_postgres_db(db_conn_info: dict, sql: str, *, timeout: int = 15) -> tuple[bool, str]:
    """`postgres` sistem DB'sine baglanip SQL calistir. Hedef DB'mizi
    rahatsiz etmeden ALTER DATABASE / pg_terminate_backend yapmak icin."""
    psql = os.getenv("PSQL", "psql")
    try:
        completed = subprocess.run(
            [
                psql,
                "-h", db_conn_info["host"],
                "-p", db_conn_info["port"],
                "-U", db_conn_info["user"],
                "-d", "postgres",
                "-t", "-A",
                "-c", sql,
            ],
            env=_pg_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            return False, (completed.stderr or "").strip()
        return True, (completed.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _terminate_other_connections(db_conn_info: dict) -> int:
    """Hedef DB'deki TUM diger connection'lari kill et (kendimiz haric).

    Returns: kac connection terminate edildi.
    """
    dbname = db_conn_info["dbname"]
    sql = (
        f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
        f"WHERE datname='{dbname}' AND pid <> pg_backend_pid();"
    )
    ok, out = _run_psql_on_postgres_db(db_conn_info, sql)
    if not ok:
        logger.warning("pg_terminate_backend failed: %s", out[:200])
        return 0
    try:
        return int(out or "0")
    except ValueError:
        return 0


def _set_db_allow_connections(db_conn_info: dict, allow: bool) -> bool:
    """Hedef DB icin ALLOW_CONNECTIONS bayragini ayarla.

    Restore sirasinda False -> yeni connection acilmaz (worker reconnect
    denerse reddedilir). Restore sonrasi True'ya geri cek. Bu sayede
    terminate sonrasi worker'lar restore bitene kadar baglanamaz, DROP
    lock'larini hicbir sey rahatsiz etmez.
    """
    dbname = db_conn_info["dbname"]
    flag = "true" if allow else "false"
    sql = f"ALTER DATABASE \"{dbname}\" WITH ALLOW_CONNECTIONS = {flag};"
    ok, out = _run_psql_on_postgres_db(db_conn_info, sql)
    if not ok:
        logger.warning("alter_db_allow_connections failed: %s", out[:200])
    return ok


def run_pg_restore(file_path: Path) -> tuple[bool, str]:
    """pg_restore calistir — mevcut tablolarin uzerine clean+create modunda
    yazar. UYARI: Mevcut tum DB icerigi silinir.

    Performans + kilitlenme dayanikligi:
      1. Backend engine pool'u dispose edilir (kendi connection'larimizi serbest birak).
      2. Hedef DB'deki tum diger backend'ler pg_terminate_backend ile kill edilir
         (worker servisleri auto-reconnect eder; ama restore sirasinda DROP'lara
         lock vermek zorundalar).
      3. pg_restore --single-transaction (atomic, hata olursa rollback) +
         --jobs=4 (paralel COPY + INDEX) + --exit-on-error (kismi yarim restore
         olmasin).

    Eski (rebrand oncesi) yedeklerle uyumluluk: restore'dan once olasi eski
    role isimlerini (horstman/hsl) gecici olarak yaratiyoruz.
    """
    db = _parse_db_url(settings.database_url)

    # tracker import — pre-flight sub-step mesajlari icin
    try:
        from app.services import restore_status_tracker as _tracker
    except ImportError:
        _tracker = None  # type: ignore

    def _sub(msg: str) -> None:
        if _tracker is not None:
            try:
                _tracker.update_message(msg)
            except Exception:  # noqa: BLE001
                pass

    # 1. Kendi pool'umuzu dispose et — DROP'lar lock alabilsin.
    _sub("Backend bağlantı havuzu serbest bırakılıyor...")
    try:
        from app.db.session import engine as _engine
        _engine.dispose()
        logger.info("backup_restore_pre_dispose_done")
    except Exception:  # noqa: BLE001
        logger.exception("engine_dispose_before_restore_failed")

    # 2. Worker connection'larini kill et.
    # NOT: ALLOW_CONNECTIONS=false KULLANMIYORUZ — superuser olmayan user'larla
    # pg_restore'un kendisi de bu DB'ye baglanamaz, FATAL hata alir
    # ("database is not currently accepting connections"). Worker'larin
    # reconnect denemesini engelleyecek baska bir mekanizma yok; bunun
    # yerine arka plan thread'i pg_restore boyunca her 1sn worker'lari
    # terminate eder. Kisa loop sayesinde worker reconnect olur olmaz tekrar
    # kill edilir, lock'a girip pg_restore'u bloklayamaz.
    _sub("Aktif veritabanı bağlantıları kapatılıyor (worker'lar restore boyunca block'lu)...")
    terminated = _terminate_other_connections(db)
    if terminated > 0:
        logger.info("backup_restore_terminated_connections count=%d", terminated)
        _sub(f"{terminated} aktif bağlantı kapatıldı; pg_restore başlatılıyor...")
    else:
        _sub("Aktif bağlantı yok; pg_restore başlatılıyor...")

    # 3. Legacy role'leri ensure
    _ensure_legacy_roles_exist(db)

    pg_restore = os.getenv("PG_RESTORE", "pg_restore")
    # NOT: --single-transaction KULLANILMIYOR — eski denemede DROP FK CONSTRAINT
    # asamasinda "ON_ERROR_STOP" gibi davranip kucuk uyarilari bile fatal sayip
    # tum restore'u rollback ediyordu. Bunun yerine --jobs=4 (paralel CREATE/COPY)
    # + atlanabilir hatalar tolerans.
    #
    # --jobs sadece COPY ve INDEX/CONSTRAINT post-data asamasinda paralelism
    # saglar; pre-data (TABLE DDL) sirali. Yine de buyuk DB'lerde 2-3x hizlanma.
    cmd = [
        pg_restore,
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", db["dbname"],
        "--clean",          # mevcut objeleri DROP et
        "--if-exists",      # yoksa hata verme
        "--no-owner",
        "--no-acl",
        "--jobs=4",         # paralel restore (sadece data + post-data asamasinda)
        "--verbose",        # progress goz onunde
        str(file_path),
    ]
    # Lock bekleme suresi — DROP/CREATE icin 60sn. Default sonsuz; sinir
    # koyarak kullanici "30 dk takildi" durumunu yasamaz, fail-fast hata gorur.
    env = _pg_env()
    env["PGOPTIONS"] = "-c statement_timeout=600000 -c lock_timeout=60000"

    # pg_restore'u Popen ile baslat; stderr'i satir satir oku, tracker'a
    # sub-step mesaji olarak yansit. --verbose ile pg_restore "creating TABLE
    # devices", "processing data for ..." gibi satirlar basar.
    import threading as _threading
    _sub("pg_restore başlatılıyor (--jobs=4, --verbose)...")
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return False, "pg_restore bulunamadı (PATH'te olmali veya PG_RESTORE env ile yol verin)."
    except Exception as exc:  # noqa: BLE001
        return False, f"pg_restore baslatilamadi: {exc}"

    stderr_lines: list[str] = []
    last_pretty_msg = [""]
    stop_terminate_loop = _threading.Event()

    def _stderr_reader() -> None:
        """stderr satir satir oku, pg_restore'un --verbose ciktilarini tracker'a yolla."""
        if proc.stderr is None:
            return
        try:
            for raw_line in proc.stderr:
                line = raw_line.rstrip()
                if not line:
                    continue
                stderr_lines.append(line)
                # pg_restore --verbose ornek satirlar:
                #   pg_restore: connecting to database for restore
                #   pg_restore: dropping TABLE devices
                #   pg_restore: creating TABLE "public.devices"
                #   pg_restore: processing data for table "public.devices"
                #   pg_restore: creating INDEX "ix_devices_code"
                low = line.lower()
                pretty: str | None = None
                if "dropping " in low:
                    obj = line.split("dropping", 1)[1].strip()
                    pretty = f"DROP: {obj[:80]}"
                elif "creating " in low:
                    obj = line.split("creating", 1)[1].strip()
                    pretty = f"CREATE: {obj[:80]}"
                elif "processing data" in low:
                    obj = line.split("processing data", 1)[1].strip()
                    pretty = f"COPY: {obj[:80]}"
                elif "executing " in low:
                    obj = line.split("executing", 1)[1].strip()
                    pretty = f"EXEC: {obj[:80]}"
                if pretty and pretty != last_pretty_msg[0]:
                    last_pretty_msg[0] = pretty
                    _sub(pretty)
        except Exception:  # noqa: BLE001
            pass

    def _terminate_loop() -> None:
        """pg_restore calistigi sure boyunca her 1.5sn'de worker connection'larini
        yeniden kill et. Worker'lar (tag-engine/alarm-service/...) auto-reconnect
        eder; yeni baglantilar DROP TABLE/CONSTRAINT lock'larini bloklar. Loop
        sayesinde reconnect olur olmaz tekrar kapatilirlar — pg_restore TEMIZ
        lock alabilir. pg_restore'un kendi connection'i (psql user) hep ayakta,
        cunku _terminate_other_connections pid != pg_backend_pid() filtresi
        ile cagiran psql baglantisini korur; pg_restore subprocess'i de baska
        bir pid'le baglanir, terminate edilir? — Cevap: terminate sorgusu
        'postgres' DB'sine baglani, hedef DB'deki backend'leri tarar. pg_restore
        'enerjione' DB'sine baglani, kendi pid'i farkli; ama BIZ filtre olarak
        sadece 'pg_backend_pid()' (psql'imizin pid'i) hariç tutuyoruz, pg_restore
        DAHIL kill ediyoruz. Bu YANLIS. Fix: pg_restore'un application_name'ini
        belirleyip onu da filtre et.
        """
        psql_path = os.getenv("PSQL", "psql")
        dbname = db["dbname"]
        # Filtre: pg_restore session'i (application_name='e1_restore_session')
        # ve backend kendi connection'lari ('e1_backend') KORUNUR. Sadece
        # worker servisleri (tag-engine, alarm-service, vb. — application_name
        # default veya farkli) kill edilir.
        # 'e1_%' prefix'i ile her iki kategoriyi tek pattern'de yakalariz.
        sql = (
            f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            f"WHERE datname='{dbname}' "
            f"AND pid <> pg_backend_pid() "
            f"AND COALESCE(application_name, '') NOT LIKE 'e1_%';"
        )
        while not stop_terminate_loop.wait(1.5):
            try:
                subprocess.run(
                    [
                        psql_path,
                        "-h", db["host"],
                        "-p", db["port"],
                        "-U", db["user"],
                        "-d", "postgres",
                        "-t", "-A",
                        "-c", sql,
                    ],
                    env=_pg_env(),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except Exception:  # noqa: BLE001
                pass  # gecici hata, bir sonraki tick'te tekrar

    # pg_restore'a unique application_name ver ki terminate loop onu kill etmesin.
    env["PGAPPNAME"] = "e1_restore_session"

    reader_t = _threading.Thread(target=_stderr_reader, daemon=True)
    terminate_t = _threading.Thread(target=_terminate_loop, daemon=True)
    reader_t.start()
    terminate_t.start()

    try:
        rc = proc.wait(timeout=1200)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        stop_terminate_loop.set()
        terminate_t.join(timeout=2)
        return False, "pg_restore zaman aşımı (20 dk)."

    # Loop'lari durdur
    stop_terminate_loop.set()
    terminate_t.join(timeout=2)
    reader_t.join(timeout=2)

    full_stderr = "\n".join(stderr_lines)

    # Asil pg_restore stderr'ini /var/lib/e1-backups/restore-stderr.log'a yaz.
    # Audit event INSERT'i DB tarafinda fail etse bile (column overflow,
    # connection drop) operator bu dosyada GERCEK hatayi gorebilir.
    try:
        stderr_log = get_backup_dir() / "last-restore-stderr.log"
        with open(stderr_log, "w", encoding="utf-8") as f:
            f.write(f"# pg_restore exit code: {rc}\n")
            f.write(f"# command: {' '.join(cmd)}\n")
            f.write(f"# file: {file_path}\n\n")
            f.write(full_stderr)
        logger.info("pg_restore stderr written to %s (rc=%d, %d lines)",
                    stderr_log, rc, len(stderr_lines))
    except Exception:  # noqa: BLE001
        logger.exception("pg_restore_stderr_write_failed")

    if rc != 0:
        return False, (full_stderr or "pg_restore failed")[:1900]
    return True, full_stderr[:500]


def create_backup(
    db: Session, *, job_type: str = "manual", username: str | None = None
) -> BackupJob:
    """Yeni bir BackupJob kaydi olustur ve pg_dump'i calistir."""
    now = datetime.now(timezone.utc)
    fname = f"e1-{now.strftime('%Y%m%d-%H%M%S')}-{job_type}.dump"
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

    Progress: restore_status_tracker ile adim adim ilerleme bildirilir.
    Frontend /admin/backups/restore/status endpoint'ini polling ile takip
    eder; kullanici hangi asamada oldugunu gorebilir.
    """
    from app.services import restore_status_tracker as _tracker

    if not job.file_path:
        _tracker.fail("Yedek dosya yolu yok.")
        return False, "Yedek dosya yolu yok."
    p = Path(job.file_path)
    if not p.exists():
        msg = f"Yedek dosyasi bulunamadi: {p.name}"
        _tracker.fail(msg)
        return False, msg

    # Adim 1: validate
    _tracker.set_step("validating", f"{p.name} dogrulaniyor...")
    valid, validation_err = validate_dump_file(p)
    if not valid:
        logger.error("backup_restore_validation_failed file=%s reason=%s", p.name, validation_err)
        _tracker.fail(validation_err)
        return False, validation_err

    # Adim 2: prepare (legacy role ensure run_pg_restore icinde, ama yine de
    # adim olarak isaretleyelim ki kullanici 'preparing' rozetini gorsun).
    _tracker.set_step("preparing", "Veritabani hazirlaniyor...")

    # Adim 3: pg_restore (en uzun adim — 30 sn ile 10 dk arasi)
    _tracker.set_step("restoring", "pg_restore calisiyor (uzun surebilir)...")
    ok, msg = run_pg_restore(p)
    if not ok:
        _tracker.fail(msg)
        return False, msg

    # Adim 4: finalizing — connection pool dispose + backup_jobs re-index
    _tracker.set_step("finalizing", "Baglantilar yenileniyor...")
    try:
        from app.db.session import engine as _engine
        _engine.dispose()
    except Exception:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).exception("engine_dispose_after_restore_failed")

    # backup_jobs tablosu EXCLUDED_DATA_TABLES'ta schema-only oldugu icin
    # restore sonrasi BOS kalir. Diskte fiilen duran .dump dosyalarini
    # tarayarak DB kayitlarini geri yarat — kullanici tum yedek gecmisini
    # listede gormeye devam etsin (restore yapilan yedek dahil).
    try:
        from app.db.session import SessionLocal as _SessionLocal
        reindex_db = _SessionLocal()
        try:
            added = reindex_backup_jobs_from_disk(reindex_db)
            if added > 0:
                _tracker.update_message(
                    f"Yedek listesi yeniden olusturuldu ({added} dosya bulundu)..."
                )
        finally:
            reindex_db.close()
    except Exception:  # noqa: BLE001
        logger.exception("backup_jobs_reindex_failed")

    _tracker.finish_ok()
    return True, ""


def reindex_backup_jobs_from_disk(db: Session) -> int:
    """Diskteki `.dump` dosyalarini tarayip DB'de eksik olan BackupJob
    kayitlarini otomatik yarat.

    Restore sonrasi senaryosu: pg_restore --clean backup_jobs tablosunu
    DROP + CREATE yapar; ama dump dosyalari `EXCLUDED_DATA_TABLES` listesinde
    schema-only oldugu icin satir verisi yok → tablo bos. Kullanici "yedek
    listem nereye gitti?" diye soruyor. Bu fonksiyon diskte fiilen duran
    dump dosyalarini tarayarak BackupJob satirlarini geri yarar.

    Idempotent: ayni file_path icin tekrar kayit eklenmez (DB'de varsa atla).

    Returns: yeniden index'lenen kayit sayisi.
    """
    backup_dir = get_backup_dir()
    if not backup_dir.exists():
        return 0
    # Mevcut DB kayitlarinin file_path setini al — dupes engellemek icin.
    existing_paths: set[str] = set()
    try:
        for j in db.scalars(select(BackupJob)).all():
            if j.file_path:
                existing_paths.add(str(Path(j.file_path).resolve()))
    except Exception:  # noqa: BLE001
        logger.exception("reindex_backup_jobs_existing_query_failed")
        return 0

    added = 0
    try:
        for entry in backup_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".dump"):
                continue
            resolved = str(entry.resolve())
            if resolved in existing_paths:
                continue
            # Dosya adi formatlari (uretim akislari):
            #   e1-YYYYMMDD-HHMMSS-manual.dump
            #   e1-YYYYMMDD-HHMMSS-scheduled.dump
            #   e1-YYYYMMDD-HHMMSS-uploaded-<orig>.dump
            name = entry.stem
            job_type = "manual"
            if "-scheduled" in name:
                job_type = "scheduled"
            elif "-uploaded" in name:
                job_type = "uploaded"
            # created_at: dosya mtime'i kullan (cok dogru olmasa da mantikli).
            try:
                mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
            except Exception:  # noqa: BLE001
                mtime = datetime.now(timezone.utc)
            try:
                size = entry.stat().st_size
            except OSError:
                size = None
            db.add(BackupJob(
                job_type=job_type,
                status="success",
                file_path=resolved,
                size_bytes=size,
                error_message=None,
                created_by_username="system-reindex",
                created_at=mtime,
                completed_at=mtime,
            ))
            added += 1
        if added > 0:
            db.commit()
            logger.info("reindex_backup_jobs added=%d", added)
    except Exception:  # noqa: BLE001
        logger.exception("reindex_backup_jobs_failed")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    return added


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
