"""Komut TTL — GERCEK PostgreSQL uzerinde es zamanlilik.

NEDEN GERCEK VERITABANI GEREKIYOR
---------------------------------
`SELECT ... FOR UPDATE` satir kilidi SQLite'ta YOKTUR; SQLAlchemy onu o
lehcede sessizce yok sayar. Dolayisiyla "ayni komut hem `sent` hem
`expired` olamaz" iddiasi birim testlerinde KANITLANAMAZ — yalnizca gercek
bir Postgres'te gozlemlenebilir.

Gateway komut ucunu 1 Hz poll ediyor; ag tekrari ya da iki paralel istek
gercekci. Kilit olmasaydi iki istek ayni `pending` satiri okuyup biri
`sent`, digeri `expired` yazabilirdi.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import gateways as gw_api
from app.core.config import settings
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway

pytestmark = pytest.mark.integration

PG_URL = os.getenv("E1_TEST_PG_URL", "")
if not PG_URL:
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)

TTL = 120
DB_ADI = f"f3b_conc_{os.getpid()}"


def _url(ad: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{ad}\\1", PG_URL, count=1)


@pytest.fixture()
def pg():
    yonetim = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with yonetim.connect() as c:
        c.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{DB_ADI}' AND pid <> pg_backend_pid()"
        ))
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_ADI}"'))
        c.execute(text(f'CREATE DATABASE "{DB_ADI}" TEMPLATE template0'))
    yonetim.dispose()

    eng = create_engine(_url(DB_ADI))
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)

    kur = Session()
    # Gateway ONCE commit edilmeli: gercek Postgres'te
    # `devices_gateway_code_fkey` zorlanir (SQLite varsayilan olarak
    # zorlamaz — birim testlerinde bu sira onemsizdi).
    kur.add(Gateway(
        code="GW-1", name="S", host="10.0.0.1", listen_port=20000,
        token="t", is_active=True,
    ))
    kur.commit()
    kur.add(Device(
        code="CIHAZ-A", name="A", gateway_code="GW-1",
        ip_address="10.0.0.50", latitude=39.0, longitude=35.0,
    ))
    kur.commit()
    kur.close()

    yield Session

    eng.dispose()
    yonetim = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with yonetim.connect() as c:
        c.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{DB_ADI}' AND pid <> pg_backend_pid()"
        ))
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_ADI}"'))
    yonetim.dispose()


@pytest.fixture(autouse=True)
def baypaslar(monkeypatch):
    monkeypatch.setattr(settings, "command_max_age_sec", TTL, raising=False)
    monkeypatch.setattr(gw_api, "_signed_json_response", lambda g, m, extra_headers=None: m)

    def _sahte(db_, kod, token):
        return db_.scalars(select(Gateway).where(Gateway.code == kod)).first()

    monkeypatch.setattr("app.services.ingest_service.validate_gateway_token", _sahte)


def _komut(Session, yas_sn: float) -> int:
    s = Session()
    try:
        cmd = DeviceCommand(
            gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
            dnp3_index=3, status="pending",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=yas_sn),
        )
        s.add(cmd)
        s.commit()
        return cmd.id
    finally:
        s.close()


def _paralel_poll(Session, adet: int = 2) -> list:
    """`adet` kadar es zamanli poll — her biri KENDI oturumunda."""
    sonuclar: list = [None] * adet
    hatalar: list = [None] * adet
    engel = threading.Barrier(adet)

    def _calis(i: int) -> None:
        s = Session()
        try:
            engel.wait(timeout=30)  # ayni ANDA baslasinlar
            sonuclar[i] = gw_api.get_gateway_pending(
                "GW-1", db=s, x_gateway_token="t", x_gateway_health=None
            )
        except Exception as exc:  # noqa: BLE001
            hatalar[i] = exc
        finally:
            s.close()

    ipler = [threading.Thread(target=_calis, args=(i,)) for i in range(adet)]
    for t in ipler:
        t.start()
    for t in ipler:
        t.join(timeout=60)
    assert not any(hatalar), f"paralel poll hata verdi: {hatalar}"
    return sonuclar


def test_taze_komut_IKI_KEZ_teslim_edilmez(pg):
    """Es zamanli iki poll ayni taze komutu iki kez DONDURMEMELI."""
    Session = pg
    cid = _komut(Session, yas_sn=1)

    sonuclar = _paralel_poll(Session, 2)
    teslim = [c.id for r in sonuclar for c in r.commands]

    assert teslim.count(cid) == 1, (
        f"ayni komut {teslim.count(cid)} kez teslim edildi — mukerrer gonderim"
    )

    s = Session()
    try:
        cmd = s.get(DeviceCommand, cid)
        assert cmd.status == "sent"
        assert cmd.result_status is None
    finally:
        s.close()


def test_bayat_komut_hem_sent_hem_expired_OLAMAZ(pg):
    """Es zamanli iki poll'da bayat komut TEK bir terminal duruma gitmeli."""
    Session = pg
    cid = _komut(Session, yas_sn=TTL + 60)

    sonuclar = _paralel_poll(Session, 2)
    teslim = [c.id for r in sonuclar for c in r.commands]

    assert cid not in teslim, "bayat komut hicbir poll'da teslim edilmemeli"

    s = Session()
    try:
        cmd = s.get(DeviceCommand, cid)
        assert cmd.status == "failed"
        assert cmd.result_status == "expired"
        assert cmd.sent_at is None, (
            "komut hem expired hem sent isaretlenmis — kilit calismiyor"
        )
    finally:
        s.close()


def test_karisik_parti_es_zamanli_tutarli(pg):
    """Taze + bayat karisik parti, iki paralel poll altinda tutarli kalmali."""
    Session = pg
    taze = _komut(Session, yas_sn=2)
    bayat = _komut(Session, yas_sn=TTL + 300)

    sonuclar = _paralel_poll(Session, 3)
    teslim = [c.id for r in sonuclar for c in r.commands]

    assert teslim.count(taze) == 1, "taze komut tam bir kez teslim edilmeli"
    assert bayat not in teslim

    s = Session()
    try:
        t = s.get(DeviceCommand, taze)
        b = s.get(DeviceCommand, bayat)
        assert (t.status, t.result_status) == ("sent", None)
        assert (b.status, b.result_status, b.sent_at) == ("failed", "expired", None)
    finally:
        s.close()
