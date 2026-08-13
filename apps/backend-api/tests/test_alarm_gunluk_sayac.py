"""Gunluk alarm tetiklenme sayaci ve grafigin zaman serisi.

ISTENEN
-------
"Alarm tetiklendiginde bugun kac kez tetiklendi, bunun sayisini tut; sonra
bu grafikte zaman serisi olarak goster."

NEDEN SATIR SAYMAK YETMIYOR
---------------------------
Alarm satiri bir DURUM kaydidir: acilir, onaylanir, normale doner,
arsivlenir, gunu gelince retention'a takilir. Tetiklenme ise degismez bir
OLAY. Grafik "gecmiste ne oldu" diye soruyor; cevabi durum tablosundan
saymak, cevabi o tablonun bugunku sekline bagimli kilar — takvim de bu
yuzden bos gorunuyordu.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
import app.models.responsibility_area  # noqa: F401  (FaultEvent FK'si icin)
from app.api import internal
from app.core.config import settings
from app.db.base import Base
from app.models.alarm import AlarmDailyCount, AlarmEvent
from app.models.device import Device
from app.schemas.internal import InternalAlarmIngest
from app.services.fault_analytics_service import (
    alarm_isi_haritasi,
    alarm_ozeti,
    alarm_takvimi,
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


@pytest.fixture()
def cihaz(db):
    d = Device(code="DEMO-1", name="DEMO-1", ip_address="10.0.0.1",
               latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    return d


def _tetikle(db, cihaz: Device, *, baslik: str = "Asiri akim", sinyal: str = "oc"):
    """Gercek uctan tetikle — sayacin dogru yerde artmasini olcuyoruz."""
    return internal.ingest_alarm(
        InternalAlarmIngest(
            device_code=cihaz.code, title=baslik, description="esik asildi",
            level="critical", signal_key=sinyal,
        ),
        db=db,
        x_service_token=settings.internal_service_token,
    )


def _sayac(db, cihaz: Device, gun: date) -> int:
    satir = db.scalar(
        select(AlarmDailyCount)
        .where(AlarmDailyCount.day == gun)
        .where(AlarmDailyCount.device_id == cihaz.id)
    )
    return satir.count if satir else 0


BUGUN = datetime.now(timezone.utc).date()


# --- SAYAC ---------------------------------------------------------------


def test_alarm_tetiklenince_gunun_sayaci_ARTAR(db, cihaz):
    _tetikle(db, cihaz)
    assert _sayac(db, cihaz, BUGUN) == 1


def test_ayni_gun_ikinci_tetiklenme_sayaci_IKIYE_cikarir(db, cihaz):
    """Farkli sinyal/kural -> ayri alarm, ayni gun. Sayac toplar."""
    _tetikle(db, cihaz, baslik="Asiri akim", sinyal="oc")
    _tetikle(db, cihaz, baslik="Dusuk gerilim", sinyal="uv")

    assert _sayac(db, cihaz, BUGUN) == 2


def test_ZATEN_ACIK_alarmin_tekrari_sayaci_ARTIRMAZ(db, cihaz):
    """Dedup: ayni alarm zaten acikken gelen mesaj yeni bir tetiklenme
    degildir. Saymak, saniyede bir mesaj basan sessiz bir cihazi grafikte
    firtina gibi gosterirdi."""
    _tetikle(db, cihaz)
    sonuc = _tetikle(db, cihaz)

    assert sonuc["status"] == "deduplicated"
    assert _sayac(db, cihaz, BUGUN) == 1


def test_sayac_ALARM_SATIRINDAN_bagimsiz(db, cihaz):
    """Satir silinse bile o gun tetiklendigi gercegi durmali."""
    _tetikle(db, cihaz)
    for a in db.scalars(select(AlarmEvent)).all():
        db.delete(a)
    db.flush()

    assert _sayac(db, cihaz, BUGUN) == 1
    assert alarm_takvimi(db, days=30, visible_device_ids=None)["total"] == 1


# --- GRAFIK: ZAMAN SERISI ------------------------------------------------


def _sayac_yaz(db, cihaz: Device, gun_once: int, adet: int) -> None:
    db.add(AlarmDailyCount(
        day=BUGUN - timedelta(days=gun_once), device_id=cihaz.id, count=adet
    ))
    db.flush()


def test_takvim_GECMIS_gunlerin_sayilarini_gosterir(db, cihaz):
    """Kullanicinin sordugu sey: gecmis tarihli alarm sayilari."""
    _sayac_yaz(db, cihaz, 10, 4)
    _sayac_yaz(db, cihaz, 3, 1)

    takvim = alarm_takvimi(db, days=30, visible_device_ids=None)

    sayim = {g["date"]: g["count"] for g in takvim["days"]}
    assert sayim[(BUGUN - timedelta(days=10)).isoformat()] == 4
    assert sayim[(BUGUN - timedelta(days=3)).isoformat()] == 1
    assert takvim["total"] == 5
    assert takvim["max"] == 4
    # Sessiz gunler de kare acar — takvim veriden degil takvimden uretilir.
    assert len(takvim["days"]) == 30
    assert sayim[(BUGUN - timedelta(days=1)).isoformat()] == 0


def test_matris_CIHAZ_x_ZAMAN_serisini_gosterir(db, cihaz):
    ikinci = Device(code="DEMO-2", name="DEMO-2", ip_address="10.0.0.2",
                    latitude=39.0, longitude=35.0)
    db.add(ikinci)
    db.flush()
    _sayac_yaz(db, cihaz, 5, 3)
    _sayac_yaz(db, cihaz, 2, 2)
    db.add(AlarmDailyCount(day=BUGUN - timedelta(days=5), device_id=ikinci.id, count=1))
    db.flush()

    isi = alarm_isi_haritasi(db, days=30, visible_device_ids=None, surekli=True)

    assert isi["bucket"] == "day"
    assert len(isi["buckets"]) == 30, "sessiz gunler sutun acmali"
    kodlar = {d["code"]: d["total"] for d in isi["devices"]}
    assert kodlar == {"DEMO-1": 5, "DEMO-2": 1}
    assert isi["max"] == 3
    # Hucre: [sutun, satir, adet] — bes gun onceki sutunda iki cihaz da var.
    sutun = isi["buckets"].index((BUGUN - timedelta(days=5)).isoformat())
    adetler = sorted(h[2] for h in isi["cells"] if h[0] == sutun)
    assert adetler == [1, 3]


def test_pencere_disindaki_gun_takvime_GIRMEZ(db, cihaz):
    _sayac_yaz(db, cihaz, 40, 9)
    _sayac_yaz(db, cihaz, 2, 1)

    takvim = alarm_takvimi(db, days=30, visible_device_ids=None)

    assert takvim["total"] == 1, "pencere disindaki gun sayilmis"


def test_kapsam_suzgeci_sayacta_da_gecerli(db, cihaz):
    """Operator yalnizca kendi cihazlarinin sayisini gormeli."""
    baskasi = Device(code="DEMO-9", name="DEMO-9", ip_address="10.0.0.9",
                     latitude=39.0, longitude=35.0)
    db.add(baskasi)
    db.flush()
    _sayac_yaz(db, cihaz, 1, 2)
    db.add(AlarmDailyCount(day=BUGUN - timedelta(days=1), device_id=baskasi.id, count=7))
    db.flush()

    takvim = alarm_takvimi(db, days=30, visible_device_ids={cihaz.id})

    assert takvim["total"] == 2
    assert alarm_takvimi(db, days=30, visible_device_ids=set())["total"] == 0


def test_ust_serit_TOPLAMI_takvimle_ayni(db, cihaz):
    """Baslikta "6 alarm" yazip takvimin baska sey gostermesi kafa
    karistiriyordu; ikisi ayni kaynaktan okumali."""
    _sayac_yaz(db, cihaz, 4, 3)
    _tetikle(db, cihaz)  # bugun 1 tane daha

    ozet = alarm_ozeti(db, days=30, visible_device_ids=None)
    takvim = alarm_takvimi(db, days=30, visible_device_ids=None)

    assert ozet["total"] == takvim["total"] == 4
