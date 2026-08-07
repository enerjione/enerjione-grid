"""Cihaz seri numarasi senkronu — kod ve seri BAGIMSIZ iki alandir.

device.code operatorun serbestce sectigi, sistemdeki YONLENDIRME
anahtaridir (ingest/gateway/outbound hep bununla calisir). serial_number
ise cihazin fabrika etiketindeki gercek numaradir; yalnizca telemetriden
otomatik yazilir, kullanici cihaz eklerken elle girmez.

Bu testler REGRESYON KILIDI: kod ASLA otomatik degismemeli. 2026-08-07'de
`device.code` da otomatik gercek seriye cekiliyordu; sahada canli bir
cihaz bunun yuzunden HABERLESMEYI KESTI (ingest batch basi `device_code`
ile cihaz bulur, kod DB'de degisince gateway eski kodla yayina devam eder
ve paketler "bilinmeyen cihaz" sayilip sessizce duser).
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


def _cihaz(db, code: str, ip_son: int) -> Device:
    d = Device(
        code=code, name=f"cihaz-{code}", model="horstmann_sn_2_0",
        ip_address=f"192.168.1.{ip_son}", latitude=0.0, longitude=0.0,
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
    d = _cihaz(db, "0001", 10)

    _seri_ve_kod_senkronu(db, d, _okuma())

    assert d.serial_number == "50984"
    assert d.code == "0001", (
        "device.code otomatik degisti — bu tam olarak sahada canli cihazi "
        "'haberlesmiyor' gosteren regresyon (batch basi device_code lookup, "
        "gateway eski kodla yayina devam eder)."
    )
    assert _olay_tipleri(db) == ["device_serial_synced"]


def test_kod_ve_seri_farkli_olmasi_NORMAL_uyari_uretmez(db):
    """Kod ve seri BASTAN BERI farkli iki kavram — 'uyusmazlik' diye bir
    durum yok, bu yuzden ayni seri tekrar gelince ikinci bir olay olmamali."""
    d = _cihaz(db, "F1-DEV-001", 11)

    _seri_ve_kod_senkronu(db, d, _okuma())
    assert d.code == "F1-DEV-001"
    assert d.serial_number == "50984"
    assert _olay_tipleri(db) == ["device_serial_synced"]

    # Ayni seri tekrar gelirse (periyodik telemetri) ikinci olay dusmemeli.
    _seri_ve_kod_senkronu(db, d, _okuma())
    assert _olay_tipleri(db) == ["device_serial_synced"]


def test_sifir_ve_bos_seri_yok_sayilir(db):
    d = _cihaz(db, "0001", 14)

    _seri_ve_kod_senkronu(db, d, _okuma(value=0))
    _seri_ve_kod_senkronu(db, d, _okuma(value=None, value_string="0000"))
    _seri_ve_kod_senkronu(db, d, _okuma(value=None, value_string="  "))

    assert d.code == "0001"
    assert d.serial_number is None
    assert _olay_tipleri(db) == []
