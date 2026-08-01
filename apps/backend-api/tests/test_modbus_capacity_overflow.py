"""Modbus adres plani: kapasiteye sigmayan cihazlar SESSIZCE dusmemeli.

YASANAN SORUN
-------------
`ensure_slots` tavani asan cihazlari `continue` ile atliyordu. Hicbir sayac,
log ya da yanit alani yoktu. `remaining` de `max(0, ...)` ile 0 gorundugu icin
arayuz durumu "tam" diye okuyordu.

float32 modunda cihaz basina stride buyudugu icin tavan ~327 cihaz. Tek
hedefte 600 cihaz tanimlanirsa 273 cihaz SCADA'ya HIC yayinlanmiyor ve bunu
gosteren tek bir isaret bile yok — SCADA operatoru o cihazlarin "sakin"
oldugunu sanir.

MIMARI NOT
----------
Cozum tavani buyutmek DEGIL: her Modbus hedefi kendi adres alanina sahiptir,
yani yuk birden fazla hedefe bolunerek tasinir (600 cihaz icin iki hedef
yeterli). Ama operatorun bunu YAPMASI GEREKTIGINI once GORMESI gerekiyor —
bu testlerin korudugu sey o gorunurluk.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (Base.metadata dolsun)
from app.db.base import Base
from app.models.device import Device
from app.models.outbound_target import OutboundTarget
from app.services.modbus_plan_service import build_signal_layout
from app.services import modbus_plan_service as mps


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


def _cihazlar(db, adet: int) -> list[Device]:
    liste = []
    for i in range(adet):
        d = Device(
            code=f"DEV-{i:04d}",
            name=f"Cihaz {i}",
            ip_address=f"10.0.{i // 256}.{i % 256}",
            latitude=39.0 + i * 0.001,
            longitude=35.0 + i * 0.001,
        )
        db.add(d)
        liste.append(d)
    db.flush()
    return liste


def _layout():
    """Asgari sinyal duzeni — kapasite mantigi icin icerik onemli degil."""
    return build_signal_layout(
        [
            {
                "key": "master.actual_voltage",
                "source": "master",
                "data_type": "analog",
                "is_active": True,
                "display_order": 1,
                "label": "Gerilim",
                "unit": "V",
            }
        ]
    )


def _hedef(db, **kw) -> OutboundTarget:
    t = OutboundTarget(
        name=kw.pop("name", "SCADA-A"),
        protocol="modbus",
        is_active=True,
        **kw,
    )
    db.add(t)
    db.flush()
    return t


def test_tavan_asilinca_DUSEN_sayisi_bildiriliyor(db, monkeypatch):
    """Asil ariza: dusen cihaz sayisi hicbir yerde gorunmuyordu."""
    # Tavani testte kucultuyoruz; gercek deger stride'a bagli (float32'de ~327)
    # ve 600 cihaz uretmek testi gereksiz yavaslatirdi.
    hedef = _hedef(db)
    devices = _cihazlar(db, 12)

    orijinal = mps._capacity

    def _kucuk_tavan(mode, stride, device_count, layout):
        info = orijinal(mode, stride, device_count, layout)
        info.max_devices = 5
        return info

    monkeypatch.setattr(mps, "_capacity", _kucuk_tavan)

    plans, capacity = mps.ensure_slots(db, hedef, devices, layout=_layout(), commit=False)

    assert len(plans) == 5, f"tavan uygulanmadi: {len(plans)}"
    assert capacity.dropped_device_count == 7, (
        "kapasiteye sigmayan cihazlar SESSIZCE dusuruldu — SCADA operatoru "
        "o cihazlari 'sakin' saniyor"
    )
    assert capacity.dropped_device_codes, "dusen cihaz kodlari bildirilmiyor"
    # Dusenler plana alinmayanlar olmali
    plana_alinan = {p.device_code for p in plans}
    assert not (set(capacity.dropped_device_codes) & plana_alinan)


def test_tavan_asilmayinca_dusen_YOK(db):
    """Normal durum etkilenmemeli — uyari yalnizca gercekten tasarken cikmali."""
    hedef = _hedef(db)
    devices = _cihazlar(db, 5)

    plans, capacity = mps.ensure_slots(db, hedef, devices, layout=_layout(), commit=False)

    assert len(plans) == 5
    assert capacity.dropped_device_count == 0
    assert capacity.dropped_device_codes == ()


def test_dusen_kod_listesi_SINIRLI(db, monkeypatch):
    """273 kodluk bir yanit kimseye yardimci olmaz; sayi tam, ornek sinirli."""
    hedef = _hedef(db)
    devices = _cihazlar(db, 60)

    orijinal = mps._capacity

    def _kucuk_tavan(mode, stride, device_count, layout):
        info = orijinal(mode, stride, device_count, layout)
        info.max_devices = 2
        return info

    monkeypatch.setattr(mps, "_capacity", _kucuk_tavan)
    _, capacity = mps.ensure_slots(db, hedef, devices, layout=_layout(), commit=False)

    assert capacity.dropped_device_count == 58, "sayi TAM olmali"
    assert len(capacity.dropped_device_codes) <= 20, "ornek listesi sinirsiz"


def test_tasma_HATA_seviyesinde_loglaniyor(db, monkeypatch, caplog):
    """Sessiz kalmamali: log operatorun gorecegi ilk yer."""
    import logging

    hedef = _hedef(db, name="SCADA-B")
    devices = _cihazlar(db, 6)

    orijinal = mps._capacity

    def _kucuk_tavan(mode, stride, device_count, layout):
        info = orijinal(mode, stride, device_count, layout)
        info.max_devices = 1
        return info

    monkeypatch.setattr(mps, "_capacity", _kucuk_tavan)

    with caplog.at_level(logging.ERROR, logger=mps.__name__):
        mps.ensure_slots(db, hedef, devices, layout=_layout(), commit=False)

    hatalar = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert hatalar, "kapasite tasmasi hic loglanmadi"
    metin = hatalar[0].getMessage()
    assert "SCADA-B" in metin, "hangi hedefin tastigi belli degil"
    assert "5" in metin, "dusen cihaz sayisi log'da yok"
