"""ALARM SIKLIGI TAKVIMI GECMISI BOS GOSTERIYORDU.

SORUN
-----
Takvim `alarm_daily_counts` sayacini okuyor. Sayac ileriye dogru dogru
calisiyordu ama GECMISI yoktu: tablo 0061 ile geldi ve tek seferlik
`alarm_events` uzerinden dolduruldu — oysa o tablo o gune kadar bir DURUM
tablosuydu ve tekrar tetikleyen/kapanan alarmlarin satirlarini SILIYORDU.
Geri doldurma elinde zaten silinmis bir gecmis buldu; ekranda 365 gunun
tamami "hic alarm gelmemis" gibi cizildi.

Gercek gecmis `system_events` olay kaydinda duruyor: alarm ureten her iki
yol da satir eklerken bir olay yaziyor ve olay kaydinin satirlari hicbir
zaman silinmedi.

Bu dosya `alarm_counter_service.arsivden_onar`in o gecmisi sayaca
isledigini ve DOGRU sayilari bozmadigini korur.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.alarm import AlarmDailyCount
from app.models.device import Device
from app.models.system_event import SystemEvent
from app.services.alarm_counter_service import arsivden_onar


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
    d = Device(code="SN2-1", name="SN2-1", ip_address="10.0.0.1",
               latitude=39.0, longitude=35.0)
    db.add(d)
    db.commit()
    return d


def _gun_ortasi(gun_once: int) -> datetime:
    """`gun_once` gun oncesinin UTC OGLE VAKTI.

    NEDEN SAAT SABITLENIYOR: bu dosyadaki birkac test, ayni gune ait
    OLMASI GEREKEN birden fazla olay kuruyor (+5 dk, +3 saat gibi) ve
    sonucun TEK bir gunluk sayaca yazilmasini bekliyor. Baslangic ani
    `datetime.now(UTC)`den alinirsa test, kosuldugu SAATE bagli olur:
    UTC 21:00'den sonra kosuldugunda "+3 saat" ertesi gune tasar, sayac
    ikiye bolunur ve test kirilir. Nitekim CI'da 22:38 UTC'de tam boyle
    kirildi; ayni test gunun erken saatinde YESIL geciyordu.

    Ogle vakti her iki yonde de 12 saat pay birakir; testin kurdugu en
    genis aralik (+3 saat) bunun cok altinda. Gun yine GECMISTE kalir —
    onarimin retention penceresi davranisi degismez.
    """
    return datetime.combine(
        (datetime.now(timezone.utc) - timedelta(days=gun_once)).date(),
        time(hour=12),
        tzinfo=timezone.utc,
    )


def _olay(db, cihaz_kodu: str, ne_zaman: datetime, tip: str = "alarm_triggered") -> None:
    db.add(
        SystemEvent(
            category="alarm",
            event_type=tip,
            severity="warning",
            message="Alarm rule triggered: test",
            device_code=cihaz_kodu,
            created_at=ne_zaman,
        )
    )
    db.commit()


def _sayac(db) -> dict:
    return {
        (r.day.isoformat(), r.device_id): r.count
        for r in db.scalars(select(AlarmDailyCount)).all()
    }


def test_GECMIS_olay_kaydindan_sayaca_islenir(db, cihaz):
    """Asil regresyon: sayacin hic gormedigi gun artik dolu."""
    gecmis = _gun_ortasi(200)
    _olay(db, cihaz.code, gecmis)
    _olay(db, cihaz.code, gecmis + timedelta(minutes=5))
    _olay(db, cihaz.code, gecmis + timedelta(hours=3))

    assert _sayac(db) == {}, "on kosul: sayac bos"

    arsivden_onar(db, gun_sayisi=730)

    assert _sayac(db) == {(gecmis.date().isoformat(), cihaz.id): 3}


def test_HABERLESME_alarmi_da_sayilir(db, cihaz):
    """Iki alarm yolu iki ayri olay tipi yaziyor; biri atlanirsa o yolun
    gecmisi sessizce eksik kalir."""
    gun = _gun_ortasi(10)
    _olay(db, cihaz.code, gun, tip="alarm_triggered")
    _olay(db, cihaz.code, gun + timedelta(minutes=1), tip="alarm_created")

    arsivden_onar(db, gun_sayisi=730)

    assert _sayac(db) == {(gun.date().isoformat(), cihaz.id): 2}


def test_SAYAC_daha_BUYUKSE_bozulmaz(db, cihaz):
    """Sayac ileri yonde daha dogrudur: alarm satiri silinse de eksilmez,
    olay kaydi budanmis olabilir. Onarim onu ASLA asagi cekmemeli."""
    gun = (datetime.now(timezone.utc) - timedelta(days=5))
    db.add(AlarmDailyCount(day=gun.date(), device_id=cihaz.id, count=9))
    db.commit()
    _olay(db, cihaz.code, gun)

    arsivden_onar(db, gun_sayisi=730)

    assert _sayac(db) == {(gun.date().isoformat(), cihaz.id): 9}


def test_TEKRAR_calistirmak_sayiyi_DEGISTIRMEZ(db, cihaz):
    """Onarim hem migration'da hem bakim dongusunde kosuyor; idempotent
    olmasaydi her tur sayilari sisirirdi."""
    gun = _gun_ortasi(3)
    _olay(db, cihaz.code, gun)
    _olay(db, cihaz.code, gun + timedelta(minutes=2))

    arsivden_onar(db, gun_sayisi=730)
    ilk = _sayac(db)
    arsivden_onar(db, gun_sayisi=730)

    assert _sayac(db) == ilk == {(gun.date().isoformat(), cihaz.id): 2}


def test_PENCERE_disindaki_olay_alinmaz(db, cihaz):
    """Onarim retention penceresiyle sinirli — sinirsiz tarama bakim
    dongusunu her turda butun tabloya bindirirdi."""
    cok_eski = datetime.now(timezone.utc) - timedelta(days=400)
    yakin = datetime.now(timezone.utc) - timedelta(days=2)
    _olay(db, cihaz.code, cok_eski)
    _olay(db, cihaz.code, yakin)

    arsivden_onar(db, gun_sayisi=30)

    assert _sayac(db) == {(yakin.date().isoformat(), cihaz.id): 1}


def test_ALARM_DISI_olay_sayilmaz(db, cihaz):
    """Olay kaydinda yetki/komut/ayar olaylari da var; hepsini saymak
    takvimi alarm grafigi olmaktan cikarirdi."""
    gun = datetime.now(timezone.utc) - timedelta(days=1)
    db.add(
        SystemEvent(
            category="device",
            event_type="command_sent",
            severity="info",
            message="komut",
            device_code=cihaz.code,
            created_at=gun,
        )
    )
    db.commit()

    arsivden_onar(db, gun_sayisi=730)

    assert _sayac(db) == {}


def test_BILINMEYEN_cihaz_kodu_sessizce_dusulur(db, cihaz):
    """Silinmis cihazin olayi bir cihaza yazilamaz; sahipsiz satir cihaz x
    zaman matrisinde bos bir satir acardi."""
    gun = datetime.now(timezone.utc) - timedelta(days=1)
    _olay(db, "YOK-BOYLE-CIHAZ", gun)

    arsivden_onar(db, gun_sayisi=730)

    assert _sayac(db) == {}
