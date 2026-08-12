"""ARIZANIN KIMLIGI = IKI CIHAZ ARASI ARALIK (hat DEGIL).

SAHADA GORULEN
--------------
Bir hatta ariza acildiktan sonra BASKA bir cihaz da ariza gordugunde ekranda
yeni bir ariza cikmiyordu: mevcut kayit sessizce guncelleniyor, aralik yer
degistiriyordu. Operator icin bu "ariza suruyor" demek; oysa gercekte ikinci
bir olay olmustu ve ilk aralik artik temizdi.

Sebep eslestirmedeydi: bolge, mevcut kayitla DIREK ARALIGI KESISIMINE gore
eslesiyordu ve komsu araliklar bir direk PAYLASIR — yani neredeyse her zaman
kesisirdi.

KURAL
-----
Kayit bir aralikin (son goren cihaz -> ilk gormeyen cihaz) olayidir ve
kimligini `zone_code` tasir. Aralik degistiyse bu BASKA bir arizadir: yeni
kayit acilir, karsiliksiz kalan eski kayit `resolved` olur.

TEK ISTISNA yerlesme penceresidir (`fault_display_delay_sec`): ariza olusurken
alarmlar hep birlikte gelmez, haberlesme geciken cihazin "ben de gordum"u
saniyeler sonra duser. Kayit o sure boyunca listede zaten GORUNMEZ; aralik
netlesirken kayit yerinde guncellenir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.config import settings
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.services.fault_recompute_service import recompute_faults, zone_code


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
def saha(db):
    """Tek hat, 4 direk, aralarinda uc cihaz: D1 | D2 | D3."""
    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()
    hat = Line(name="ANA HAT", code="ANA", region_id=r.id)
    db.add(hat)
    db.flush()

    direkler = []
    for i in range(1, 5):
        p = Pole(line_id=hat.id, sequence_no=i, latitude=39.0 + i * 0.01, longitude=35.0)
        db.add(p)
        direkler.append(p)
    db.flush()

    cihazlar = []
    for i in range(3):
        d = Device(code=f"D{i + 1}", name=f"D{i + 1}", ip_address=f"10.0.0.{i + 1}",
                   latitude=39.0, longitude=35.0)
        db.add(d)
        db.flush()
        db.add(
            LineSegment(
                line_id=hat.id,
                from_pole_id=direkler[i].id,
                to_pole_id=direkler[i + 1].id,
                device_id=d.id,
                device_position_t=0.5,
            )
        )
        cihazlar.append(d)
    db.flush()
    return {"hat": hat, "direkler": direkler, "cihazlar": cihazlar}


def _alarm(db, dev: Device) -> AlarmEvent:
    a = AlarmEvent(
        device_id=dev.id, title=f"{dev.code} asiri akim", description="esik asildi",
        level="critical", signal_key="sat02.overcurrent_tripped", kind="rule",
        produces_fault=True, reset=False, created_at=datetime.now(timezone.utc),
    )
    db.add(a)
    db.flush()
    return a


def _arizalar(db) -> list[FaultEvent]:
    return list(db.scalars(select(FaultEvent).order_by(FaultEvent.id)).all())


def _eskit(db, fault: FaultEvent, saniye: int) -> None:
    """Kaydi yerlesme penceresinin DISINA tasi (test saat beklemesin)."""
    fault.opened_at = datetime.now(timezone.utc) - timedelta(seconds=saniye)
    db.flush()


def test_bolge_kodu_iki_cihazdan_uretilir(db, saha):
    d1, d2, _ = saha["cihazlar"]
    _alarm(db, d1)
    recompute_faults(db)

    (ariza,) = _arizalar(db)
    assert ariza.zone_code == zone_code(saha["hat"].id, d1.id, d2.id)
    assert ariza.last_red_device_id == d1.id
    assert ariza.first_green_device_id == d2.id


def test_hat_ucundeki_ariza_END_ile_kodlanir(db, saha):
    *_, d3 = saha["cihazlar"]
    _alarm(db, d3)
    recompute_faults(db)

    (ariza,) = _arizalar(db)
    assert ariza.zone_code.endswith(">END"), "ilerisinde cihaz yok"


def test_YENI_bir_aralikta_ariza_YENI_kayit_acar(db, saha):
    """Yerlesme penceresi kapandiktan sonra ikinci cihaz da gorurse: yeni olay."""
    d1, d2, d3 = saha["cihazlar"]
    _alarm(db, d1)
    recompute_faults(db)
    (ilk,) = _arizalar(db)
    ilk_kod = ilk.zone_code
    _eskit(db, ilk, settings.fault_display_delay_sec + 60)

    _alarm(db, d2)
    recompute_faults(db)

    hepsi = _arizalar(db)
    assert len(hepsi) == 2, "aralik degisti — bu ikinci bir arizadir"
    eski = next(f for f in hepsi if f.id == ilk.id)
    yeni = next(f for f in hepsi if f.id != ilk.id)
    assert eski.status == "resolved", "eski aralik artik arizali degil"
    assert eski.zone_code == ilk_kod, "gecmis kaydin kimligi DEGISMEZ"
    assert yeni.status == "open"
    assert yeni.zone_code == zone_code(saha["hat"].id, d2.id, d3.id)


def test_yerlesme_penceresinde_gec_gelen_alarm_AYNI_kaydi_netlestirir(db, saha):
    """Haberlesme gecikmesi yeni ariza uretmemeli — pencere bunun icin var."""
    d1, d2, d3 = saha["cihazlar"]
    _alarm(db, d1)
    recompute_faults(db)
    (ilk,) = _arizalar(db)

    # Kayit HENUZ listede gorunmuyor (opened_at = simdi).
    _alarm(db, d2)
    recompute_faults(db)

    hepsi = _arizalar(db)
    assert len(hepsi) == 1, "pencere icinde ikinci kayit ACILMAZ"
    assert hepsi[0].id == ilk.id
    assert hepsi[0].last_red_device_id == d2.id, "aralik netlesti"
    assert hepsi[0].zone_code == zone_code(saha["hat"].id, d2.id, d3.id)


def test_araya_cihaz_eklenirse_KOD_degisir_ve_yeni_kayit_acilir(db, saha):
    """Kullanici kurali: araya yeni bir cihaz girerse o araligin kodu degismeli."""
    d1, d2, _ = saha["cihazlar"]
    _alarm(db, d1)
    recompute_faults(db)
    (ilk,) = _arizalar(db)
    assert ilk.zone_code == zone_code(saha["hat"].id, d1.id, d2.id)
    _eskit(db, ilk, settings.fault_display_delay_sec + 60)

    # D2'nin oturdugu aciklika, ONUNDEN once gelecek sekilde yeni cihaz.
    yeni_cihaz = Device(code="D1B", name="D1B", ip_address="10.0.0.9",
                        latitude=39.0, longitude=35.0)
    db.add(yeni_cihaz)
    db.flush()
    db.add(
        LineSegment(
            line_id=saha["hat"].id,
            from_pole_id=saha["direkler"][1].id,
            to_pole_id=saha["direkler"][2].id,
            device_id=yeni_cihaz.id,
            device_position_t=0.2,
        )
    )
    db.flush()

    recompute_faults(db)

    hepsi = _arizalar(db)
    assert len(hepsi) == 2
    eski = next(f for f in hepsi if f.id == ilk.id)
    yeni = next(f for f in hepsi if f.id != ilk.id)
    assert eski.status == "resolved"
    assert yeni.zone_code == zone_code(saha["hat"].id, d1.id, yeni_cihaz.id), (
        "aralik daraldi — kod da degismeli"
    )


def test_ayni_alarm_surekli_ise_TEKRAR_kayit_acilmaz(db, saha):
    """En kritik guvence: her recompute turu yeni satir uretmemeli."""
    d1, *_ = saha["cihazlar"]
    _alarm(db, d1)
    recompute_faults(db)
    (ilk,) = _arizalar(db)
    _eskit(db, ilk, settings.fault_display_delay_sec + 600)

    for _ in range(3):
        recompute_faults(db)

    hepsi = _arizalar(db)
    assert len(hepsi) == 1
    assert hepsi[0].status == "open"
