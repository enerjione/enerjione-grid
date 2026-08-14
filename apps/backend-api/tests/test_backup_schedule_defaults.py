"""Zamanli yedek programinin VARSAYILANI ve geriye uyumlulugu.

YASANAN SORUN
-------------
`backup_schedule.enabled` varsayilani `False` idi ve hicbir kurulum adimi
onu acmiyordu. Sonuc: saha IPC'sinde kimse Yedekleme ekranina girmezse
HICBIR zamanli yedek alinmiyordu. Tek yedek kaynagi `update.sh`in aldigi
pre-update dump'i oluyordu — yani yedek sikligi "operator ne zaman
guncelleme yapti" sorusuna bagliydi. Disk arizasinda 2 yillik denetim
kaydi ve tum konfigurasyon giderdi.

BU TESTLERIN KILITLEDIGI IKI SEY
--------------------------------
1. TEMIZ KURULUM ACIK GELIR. Varsayilan domain katmaninda duruyor
   (`get_or_create_schedule`), migration'da DEGIL — cunku temiz kurulum
   semayi `create_all` + `stamp head` ile kuruyor ve migration'lar
   KOSMUYOR (bkz. scripts/migrate_db.py). Bir migration tam da hedeflenen
   senaryoda calismazdi.

2. MEVCUT TERCIH KORUNUR. Operator bilincli olarak kapattiysa
   (`enabled=False`) hicbir kod yolu onu geri acmaz. Bu, testlerin en
   onemlisi: "varsayilani degistirdik" diye mevcut kurulumlari koru
   koru UPDATE etmek, musterinin bilincli kararini ezmek olurdu.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (tum tablolar metadata'ya kayitli olsun)
from app.db.base import Base
from app.models.backup import BackupJob, BackupSchedule
from app.services import backup_scheduler
from app.services.backup_service import (
    DEFAULT_SCHEDULE_ENABLED,
    DEFAULT_SCHEDULE_INTERVAL_HOURS,
    DEFAULT_SCHEDULE_RETENTION_COUNT,
    get_or_create_schedule,
)


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


# --------------------------------------------------------------------------
# Test 1 — temiz kurulum
# --------------------------------------------------------------------------


def test_temiz_kurulumda_program_acik_gelir(db):
    """Satir yokken yaratilan program ACIK ve 24 saatlik olmali."""
    sch = get_or_create_schedule(db)

    assert sch.enabled is True
    assert sch.interval_hours == 24
    # Retention mevcut `apply_retention` ile uyumlu: en yeni 7 basarili yedek.
    assert sch.retention_count == 7
    # Ilk yedek 24 saat beklemesin diye bilerek bos.
    assert sch.last_run_at is None


def test_varsayilan_sabitleri_modelle_uyumlu(db):
    """`DEFAULT_SCHEDULE_*` sabitleri gercekten yazilan degerler olmali.

    Sabitler ile INSERT ayrisirsa varsayilan "belgelenmis ama uygulanmiyor"
    haline gelir — bu proje bunu daha once BACKUP_OFFSITE_DIR'da yasadi.
    """
    sch = get_or_create_schedule(db)

    assert sch.enabled is DEFAULT_SCHEDULE_ENABLED
    assert sch.interval_hours == DEFAULT_SCHEDULE_INTERVAL_HOURS
    assert sch.retention_count == DEFAULT_SCHEDULE_RETENTION_COUNT


# --------------------------------------------------------------------------
# Test 2 — mevcut ACIK program
# --------------------------------------------------------------------------


def test_mevcut_acik_program_degismeden_kalir(db):
    """Kullanici ayarlari (interval/retention dahil) ezilmemeli."""
    db.add(
        BackupSchedule(
            id=1, enabled=True, interval_hours=6, retention_count=30
        )
    )
    db.commit()

    sch = get_or_create_schedule(db)

    assert sch.enabled is True
    assert sch.interval_hours == 6      # varsayilan 24'e CEKILMEDI
    assert sch.retention_count == 30    # varsayilan 7'ye CEKILMEDI


# --------------------------------------------------------------------------
# Test 3 — mevcut BILINCLI OLARAK KAPATILMIS program  (en kritik test)
# --------------------------------------------------------------------------


def test_bilincli_kapatilmis_program_acilmaz(db):
    """`enabled=False` her kod yolunda KAPALI kalmali.

    Bu, varsayilan degisikliginin geriye uyumluluk sozudur. Kirilirsa
    musterinin bilincli karari sessizce geri alinmis olur.
    """
    db.add(
        BackupSchedule(
            id=1, enabled=False, interval_hours=24, retention_count=7
        )
    )
    db.commit()

    # Tek cagri degil: acilis, API GET'i ve scheduler turu ayni satira
    # tekrar tekrar dokunur. Hicbiri onu acmamali.
    for _ in range(3):
        sch = get_or_create_schedule(db)
        assert sch.enabled is False

    db.expire_all()
    assert db.get(BackupSchedule, 1).enabled is False


def test_kapali_programda_scheduler_yedek_almaz(db, monkeypatch):
    """Kapali program scheduler turunda da yedek URETMEMELI."""
    db.add(BackupSchedule(id=1, enabled=False, interval_hours=24, retention_count=7))
    db.commit()

    cagrildi: list[str] = []
    _scheduler_baglantisini_sabitle(monkeypatch, db)
    monkeypatch.setattr(
        backup_scheduler,
        "create_backup",
        lambda *a, **k: cagrildi.append("create") or _sahte_job(db),
    )

    backup_scheduler.BackupSchedulerWorker()._maybe_run()

    assert cagrildi == []
    assert db.scalar(select(func.count()).select_from(BackupJob)) == 0


# --------------------------------------------------------------------------
# Test 4 — scheduler varsayilan programla yedek uretebiliyor
# --------------------------------------------------------------------------


def test_scheduler_varsayilan_programla_yedek_alir(db, monkeypatch):
    """Temiz kurulumda ilk tur yedek URETMELI (24 saat beklemeden)."""
    _scheduler_baglantisini_sabitle(monkeypatch, db)
    uretilen: list[dict] = []

    def _sahte_create(_db, *, job_type, username):
        uretilen.append({"job_type": job_type, "username": username})
        return _sahte_job(_db, status="success", job_type=job_type)

    monkeypatch.setattr(backup_scheduler, "create_backup", _sahte_create)

    backup_scheduler.BackupSchedulerWorker()._maybe_run()

    assert len(uretilen) == 1
    assert uretilen[0]["job_type"] == "scheduled"
    # Tur damgalandi: bir sonraki tur interval dolmadan tekrar almayacak.
    assert db.get(BackupSchedule, 1).last_run_at is not None


def test_interval_dolmadan_ikinci_yedek_alinmaz(db, monkeypatch):
    """Ard arda gelen turlar interval'i BEKLEMELI."""
    _scheduler_baglantisini_sabitle(monkeypatch, db)
    sayac: list[int] = []
    monkeypatch.setattr(
        backup_scheduler,
        "create_backup",
        lambda _db, **k: (sayac.append(1), _sahte_job(_db, status="success"))[1],
    )

    worker = backup_scheduler.BackupSchedulerWorker()
    worker._maybe_run()   # ilk tur -> alir
    worker._maybe_run()   # hemen ardindan -> almamali
    worker._maybe_run()

    assert len(sayac) == 1

    # Interval dolunca yeniden alir.
    sch = db.get(BackupSchedule, 1)
    sch.last_run_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()
    worker._maybe_run()

    assert len(sayac) == 2


# --------------------------------------------------------------------------
# Test 5 — yeniden baslatma: tek satir, tekrar yedek yok
# --------------------------------------------------------------------------


def test_yeniden_baslatmada_program_cogalmaz(db, monkeypatch):
    """Restart dongusu ne ikinci satir ne de ikinci yedek uretmeli."""
    _scheduler_baglantisini_sabitle(monkeypatch, db)
    sayac: list[int] = []
    monkeypatch.setattr(
        backup_scheduler,
        "create_backup",
        lambda _db, **k: (sayac.append(1), _sahte_job(_db, status="success"))[1],
    )

    # 1. acilis: program yaratilir + ilk yedek alinir.
    backup_scheduler.BackupSchedulerWorker()._maybe_run()
    ilk_damga = db.get(BackupSchedule, 1).last_run_at

    # 2. ve 3. acilis (yeni worker nesnesi = surec yeniden basladi).
    backup_scheduler.BackupSchedulerWorker()._maybe_run()
    backup_scheduler.BackupSchedulerWorker()._maybe_run()

    assert db.scalar(select(func.count()).select_from(BackupSchedule)) == 1
    assert len(sayac) == 1
    assert db.get(BackupSchedule, 1).last_run_at == ilk_damga


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------


def test_basarisiz_yedek_scheduler_i_oldurmez_ve_kayit_birakir(db, monkeypatch):
    """pg_dump patlarsa: istisna disari sizmaz, denetim kaydi olusur."""
    from app.models.system_event import SystemEvent

    _scheduler_baglantisini_sabitle(monkeypatch, db)
    monkeypatch.setattr(
        backup_scheduler,
        "create_backup",
        lambda _db, **k: _sahte_job(_db, status="failed", hata="pg_dump non-zero exit"),
    )

    # Istisna FIRLATMAMALI — dis dongu bunu yakalasa bile tur temiz bitmeli.
    backup_scheduler.BackupSchedulerWorker()._maybe_run()

    olaylar = db.scalars(
        select(SystemEvent).where(SystemEvent.event_type == "backup_scheduled_failed")
    ).all()
    assert len(olaylar) == 1
    assert olaylar[0].severity == "error"
    assert "pg_dump" in (olaylar[0].message or "")

    # Damga vuruldu: kalici ariza 5 dakikada bir yeniden denenmez.
    assert db.get(BackupSchedule, 1).last_run_at is not None


def test_create_backup_istisnasi_yutulur(db, monkeypatch):
    """`create_backup` beklenmedik sekilde firlatirsa tur cokmemeli."""
    from app.models.system_event import SystemEvent

    _scheduler_baglantisini_sabitle(monkeypatch, db)

    def _patla(_db, **_k):
        raise RuntimeError("pg_dump ikilisi bulunamadi")

    monkeypatch.setattr(backup_scheduler, "create_backup", _patla)

    backup_scheduler.BackupSchedulerWorker()._maybe_run()

    olaylar = db.scalars(
        select(SystemEvent).where(SystemEvent.event_type == "backup_scheduled_failed")
    ).all()
    assert len(olaylar) == 1
    assert "pg_dump ikilisi" in (olaylar[0].message or "")


def test_disk_dolu_ise_yedek_atlanir_ve_damga_vurulmaz(db, monkeypatch):
    """Hard-stop esiginde yedek ALINMAZ; yer acilinca bir sonraki tur alir.

    Eskiden `_check_backup_disk_usage`in firlattigi RuntimeError genel
    `except` tarafindan yutuluyor ve YEDEK YINE ALINIYORDU — yani
    belgelenen "hard stop" islemiyordu.
    """
    _scheduler_baglantisini_sabitle(monkeypatch, db)
    cagrildi: list[int] = []
    monkeypatch.setattr(
        backup_scheduler,
        "create_backup",
        lambda _db, **k: (cagrildi.append(1), _sahte_job(_db))[1],
    )

    def _disk_dolu(_db):
        raise backup_scheduler.BackupDiskFull("Backup dizini %97.0 dolu")

    monkeypatch.setattr(backup_scheduler, "_check_backup_disk_usage", _disk_dolu)

    backup_scheduler.BackupSchedulerWorker()._maybe_run()

    assert cagrildi == []
    # Damga VURULMAZ: operator yer acar acmaz bir sonraki turda alinsin.
    assert db.get(BackupSchedule, 1).last_run_at is None


def test_disk_olcumu_yapilamazsa_yedek_yine_alinir(db, monkeypatch):
    """Olcemedik != yer yok. Karar verecek veri yoksa yedek alinmaya devam."""
    _scheduler_baglantisini_sabitle(monkeypatch, db)
    cagrildi: list[int] = []
    monkeypatch.setattr(
        backup_scheduler,
        "create_backup",
        lambda _db, **k: (cagrildi.append(1), _sahte_job(_db, status="success"))[1],
    )

    def _olculemedi(_db):
        raise OSError("mount kayboldu")

    monkeypatch.setattr(backup_scheduler, "_check_backup_disk_usage", _olculemedi)

    backup_scheduler.BackupSchedulerWorker()._maybe_run()

    assert len(cagrildi) == 1


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------


def _scheduler_baglantisini_sabitle(monkeypatch, db) -> None:
    """Scheduler'i test session'ina bagla ve dis dunyayi kes.

    `_maybe_run` kendi `SessionLocal()`ini acar ve `finally` icinde
    `close()` eder; testte ayni in-memory session'i kullanmamiz gerekiyor,
    o yuzden `close` da etkisiz hale getiriliyor (aksi halde ikinci tur
    kapali bir session'la kosardi).
    """
    monkeypatch.setattr(backup_scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    # Gercek disk olcumu ve dosya silme testte istenmiyor.
    monkeypatch.setattr(backup_scheduler, "_check_backup_disk_usage", lambda _db: None)
    monkeypatch.setattr(backup_scheduler, "apply_retention", lambda _db, _n: 0)


def _sahte_job(
    db, *, status: str = "success", job_type: str = "scheduled", hata: str | None = None
) -> BackupJob:
    """`create_backup`in dondurdugu gibi kalici bir is kaydi uretir."""
    job = BackupJob(
        job_type=job_type,
        status=status,
        file_path="/var/lib/e1-backups/test.dump" if status == "success" else None,
        size_bytes=1234 if status == "success" else None,
        error_message=hata,
        created_by_username="(system)",
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    return job
