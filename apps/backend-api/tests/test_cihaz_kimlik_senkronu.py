"""Cihaz kimligi senkronu — kod = gercek seri no konvansiyonu.

Cihaz kurulumda yanlis kodla kaydedildiyse, baglandiginda bildirdigi
`master.serial_number` ile hem `serial_number` hem `code` otomatik
duzeltilmeli. Buradaki riskler "fonksiyon calisti mi"dan buyuk:

  - Kod baska bir cihazla CAKISIYORSA dokunulmamali (unique kimlik);
    uyari olayi da her telemetride degil BIR KEZ dusmeli (event spam'i
    system_events'te 2 yil yasar).
  - Sifir/bos seri YOK SAYILMALI (sahada cihaz bir an seri=0 gonderdi).
  - Kod degisince gateway config_nonce artmali ki gateway ~1 sn'de yeni
    kodu ceksin; artmazsa gateway eski kodla yayinlamaya devam eder ve
    telemetri eslesmez.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.device import Device
from app.models.gateway import Gateway
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


def test_yanlis_kod_gercek_seriyle_duzeltilir(db):
    gw = Gateway(
        code="GW-1", name="GW 1", token="t" * 32,
        host="127.0.0.1", listen_port=20000,
    )
    db.add(gw)
    d = _cihaz(db, "0001", 10, gateway_code="GW-1")
    eski_nonce = int(getattr(gw, "config_nonce", 0) or 0)

    _seri_ve_kod_senkronu(db, d, _okuma())

    assert d.serial_number == "50984"
    assert d.code == "50984"
    tipler = _olay_tipleri(db)
    assert "device_serial_synced" in tipler
    assert "device_code_synced" in tipler
    # Gateway yeni kodu cekebilsin diye nonce artmali.
    assert int(gw.config_nonce or 0) == eski_nonce + 1


def test_kod_zaten_seri_ise_dokunulmaz(db):
    d = _cihaz(db, "50984", 11)
    d.serial_number = "50984"
    db.flush()

    _seri_ve_kod_senkronu(db, d, _okuma())

    assert d.code == "50984"
    assert _olay_tipleri(db) == []


def test_cakisan_kod_varsa_dokunulmaz_ve_uyari_BIR_KEZ_dusulur(db):
    _cihaz(db, "50984", 12)  # gercek seriyi kod olarak tasiyan baska cihaz
    d = _cihaz(db, "0001", 13)

    _seri_ve_kod_senkronu(db, d, _okuma())
    assert d.code == "0001"  # kimlige dokunulmadi
    assert d.serial_number == "50984"  # seri yine de senkronlandi
    assert _olay_tipleri(db).count("device_code_sync_blocked") == 1

    # Ayni seri tekrar gelirse (periyodik integrity poll) spam uretilmemeli.
    _seri_ve_kod_senkronu(db, d, _okuma())
    assert _olay_tipleri(db).count("device_code_sync_blocked") == 1


def test_sifir_ve_bos_seri_yok_sayilir(db):
    d = _cihaz(db, "0001", 14)

    _seri_ve_kod_senkronu(db, d, _okuma(value=0))
    _seri_ve_kod_senkronu(db, d, _okuma(value=None, value_string="0000"))
    _seri_ve_kod_senkronu(db, d, _okuma(value=None, value_string="  "))

    assert d.code == "0001"
    assert d.serial_number is None
    assert _olay_tipleri(db) == []
