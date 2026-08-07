"""Cihaz seri numarasi senkronu — KOD'a BILEREK dokunmaz (2026-08-07 olayi).

v2.53.31'de `master.serial_number` telemetrisi geldiginde `device.code` da
otomatik olarak gercek seriye cekiliyordu. Sahada canli bir cihaz bunun
yuzunden HABERLESMEYI KESTI: ingest her telemetri batch'inde cihazi
`device_code` ile bulan TEK sorguyu batch basinda calistirir; kod
degistiginde gateway'in kendi yayin dongusu ESKI kodu kullanmaya devam
eder (config'i ne zaman yeniden cekecegi garanti degil, dis repo) ve o
aradaki paketler `telemetry-consumer-device-not-found` ile SESSIZCE
DUSER — cihaz fiziksel olarak konusuyor ama sistem "bilinmeyen cihaz"
sayip atiyor.

Bu testler DUZELTILMIS davranisi kilitler: seri senkronu KALIR (config
dosyasi icin gerekli), kod mutasyonu KALICI OLARAK KALDIRILDI, yerine
tek seferlik (spam yapmayan) bir uyari olayi var.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.device import Device
from app.models.system_event import SystemEvent
from app.services.telemetry_consumer import _seri_ve_kod_senkronu


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _cihaz(db, code: str, ip_son: int, gateway_code: str | None = None) -> Device:
    d = Device(
        code=code, name=f"cihaz-{code}", model="horstmann_sn_2_0",
        ip_address=f"192.168.1.{ip_son}", latitude=0.0, longitude=0.0,
        gateway_code=gateway_code,
    )
    db.add(d)
    db.flush()
    return d


def _okuma(value=50984, value_string=None):
    return SimpleNamespace(
        signal_key="master.serial_number", value=value, value_string=value_string
    )


def _olay_tipleri(db) -> list[str]:
    return list(db.scalars(select(SystemEvent.event_type)).all())


def test_seri_senkronlanir_kod_ASLA_degismez(db):
    """Regresyon kilidi: bu test kirilirsa 2026-08-07 olayi tekrar ediyordur."""
    d = _cihaz(db, "0001", 10, gateway_code="GW-1")

    _seri_ve_kod_senkronu(db, d, _okuma())

    assert d.serial_number == "50984"
    assert d.code == "0001", (
        "device.code otomatik degisti — bu tam olarak sahada canli cihazi "
        "'haberlesmiyor' gosteren regresyon (batch basi device_code lookup, "
        "gateway eski kodla yayina devam eder)."
    )
    tipler = _olay_tipleri(db)
    assert "device_serial_synced" in tipler
    assert "device_code_synced" not in tipler
    assert "device_code_mismatch" in tipler


def test_kod_zaten_seri_ise_uyusmazlik_olayi_dusmez(db):
    d = _cihaz(db, "50984", 11)
    d.serial_number = "50984"
    db.flush()

    _seri_ve_kod_senkronu(db, d, _okuma())

    assert d.code == "50984"
    assert _olay_tipleri(db) == []


def test_uyusmazlik_uyarisi_seri_DEGISTIGINDE_bir_kez_duser(db):
    d = _cihaz(db, "0001", 13)

    _seri_ve_kod_senkronu(db, d, _okuma())
    assert d.code == "0001"  # kimlige HICBIR ZAMAN dokunulmaz
    assert d.serial_number == "50984"
    assert _olay_tipleri(db).count("device_code_mismatch") == 1

    # Ayni seri tekrar gelirse (periyodik telemetri) spam uretilmemeli —
    # serial_number zaten esit oldugu icin senkron bloguna hic girilmez.
    _seri_ve_kod_senkronu(db, d, _okuma())
    assert _olay_tipleri(db).count("device_code_mismatch") == 1


def test_sifir_ve_bos_seri_yok_sayilir(db):
    d = _cihaz(db, "0001", 14)

    _seri_ve_kod_senkronu(db, d, _okuma(value=0))
    _seri_ve_kod_senkronu(db, d, _okuma(value=None, value_string="0000"))
    _seri_ve_kod_senkronu(db, d, _okuma(value=None, value_string="  "))

    assert d.code == "0001"
    assert d.serial_number is None
    assert _olay_tipleri(db) == []
