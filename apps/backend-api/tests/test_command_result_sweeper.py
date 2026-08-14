"""Sonucu gelmeyen komut + gec gelen sonucun mutabakati (F3C-C).

KAPATILAN ARIZA
---------------
`sent` durumundaki komutu HICBIR SEY sonlandirmiyordu. Gateway sonucu teslim
edemezse (defter dead-letter'a aldi, gateway kalici olarak kayboldu) komut
SONSUZA KADAR `sent` kaliyordu: arayuzde "gonderildi" gorunuyor, operator
kesicinin surulup surulmedigini hicbir zaman ogrenemiyordu.

BU DOSYANIN KILITLEDIGI EN ONEMLI SEY
-------------------------------------
Sonlandirma bir TAHMINDIR, gozlem degil — ve FIZIKSEL YENIDEN DENEME URETMEZ.
`sent -> pending` ASLA olmaz. Gec gelen gercek sonuc bu tahmini duzeltebilir,
ama bu istisna BILEREK DARDIR: yalnizca `result_unknown` -> gercek sonuc.
`ok -> failed`, `expired -> ok`, `cancelled -> *` gecisleri ACILMADI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import gateways as gw_api
from app.core.config import settings
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway
from app.schemas.gateway import CommandResultItem
from app.services import command_delivery_service as teslim
from app.services.command_result_sweeper import sonucu_gelmeyenleri_sonlandir

ZAMAN_ASIMI = 300


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


@pytest.fixture(autouse=True)
def ayarlar(monkeypatch):
    monkeypatch.setattr(settings, "command_max_age_sec", 120, raising=False)
    monkeypatch.setattr(settings, "command_result_timeout_sec", ZAMAN_ASIMI, raising=False)


@pytest.fixture()
def gateway(db):
    g = Gateway(
        code="GW-1", name="Saha 1", host="10.0.0.1", listen_port=20000,
        token="t1", is_active=True,
    )
    db.add(g)
    db.commit()
    db.add(Device(
        code="CIHAZ-A", name="A", gateway_code="GW-1",
        ip_address="10.0.0.50", latitude=39.0, longitude=35.0,
    ))
    db.commit()
    return g


@pytest.fixture(autouse=True)
def token_baypas(monkeypatch, request):
    if "gateway" not in request.fixturenames:
        return

    def _sahte(db_, kod, token):
        return db_.scalars(select(Gateway).where(Gateway.code == kod)).first()

    monkeypatch.setattr("app.services.ingest_service.validate_gateway_token", _sahte)


def gonderilmis_komut(db, *, gonderim_yasi_sn: float) -> DeviceCommand:
    simdi = datetime.now(timezone.utc)
    cmd = DeviceCommand(
        gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
        dnp3_index=3, status="sent",
        created_at=simdi - timedelta(seconds=gonderim_yasi_sn + 5),
        sent_at=simdi - timedelta(seconds=gonderim_yasi_sn),
        delivery_token="jeton", delivery_attempt=1,
    )
    db.add(cmd)
    db.commit()
    return cmd


def sonuc_bildir(db, cmd_id: int, *, ok: bool, status: str = "ok"):
    return gw_api.report_command_results(
        "GW-1",
        results=[CommandResultItem(id=cmd_id, ok=ok, status=status)],
        db=db,
        x_gateway_token="t",
    )


# ---------------------------------------------------------------------------
# T22 — sonucu gelmeyen komut
# ---------------------------------------------------------------------------


def test_T22_zaman_asimi_result_unknown_uretir(db, gateway):
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI + 60)
    assert sonucu_gelmeyenleri_sonlandir(db) == 1
    db.commit()

    db.refresh(cmd)
    assert cmd.status == "failed"
    assert cmd.result_status == teslim.RESULT_UNKNOWN
    assert "uygulanmis olabilir" in (cmd.result_error or ""), (
        "belirsizlik operatore acikca soylenmeli"
    )
    assert cmd.completed_at is not None


def test_T22b_FIZIKSEL_YENIDEN_DENEME_YOK(db, gateway):
    """`sent -> pending` ASLA olmaz — kesici ikinci kez surulmez."""
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI + 60)
    sonucu_gelmeyenleri_sonlandir(db)
    db.commit()
    db.refresh(cmd)
    assert cmd.status != "pending", "komut yeniden kuyruga alindi — FIZIKSEL TEKRAR RISKI"


def test_T22_eski_protokol_komutunda_KABUL_IDDIA_EDILMEZ(db, gateway):
    """Eski yolda `sent` kabul kaniti DEGIL — mesaj bunu iddia etmemeli."""
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI + 60)
    cmd.delivery_token = None  # eski protokol: jeton hic uretilmedi
    cmd.delivery_attempt = 0
    db.commit()

    sonucu_gelmeyenleri_sonlandir(db)
    db.commit()
    db.refresh(cmd)

    assert cmd.result_status == teslim.RESULT_UNKNOWN
    assert "kalici olarak kabul etti" not in (cmd.result_error or ""), (
        "eski protokolde kanitlanmamis bir kabul iddia ediliyor"
    )
    assert "eski teslim protokolu" in (cmd.result_error or "")


def test_T22c_suresi_dolmayan_komuta_dokunulmaz(db, gateway):
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI - 30)
    assert sonucu_gelmeyenleri_sonlandir(db) == 0
    db.refresh(cmd)
    assert cmd.status == "sent"


def test_T22d_sinir_tam_zaman_asimi_henuz_dolmamis(db, gateway):
    """`age == timeout` henuz ASILMIS degildir (kesin `<` karsilastirmasi)."""
    simdi = datetime.now(timezone.utc)
    cmd = DeviceCommand(
        gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
        dnp3_index=3, status="sent", created_at=simdi,
        sent_at=simdi - timedelta(seconds=ZAMAN_ASIMI),
    )
    db.add(cmd)
    db.commit()
    assert sonucu_gelmeyenleri_sonlandir(db, now=simdi) == 0


def test_T22e_pending_komut_supurulmez(db, gateway):
    """Olcut `sent_at`; teslim edilmemis komut bu supurucunun isi degil."""
    cmd = DeviceCommand(
        gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
        dnp3_index=3, status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(cmd)
    db.commit()
    assert sonucu_gelmeyenleri_sonlandir(db) == 0


def test_T22f_terminal_komut_supurulmez(db, gateway):
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI + 60)
    cmd.status = "ok"
    db.commit()
    assert sonucu_gelmeyenleri_sonlandir(db) == 0


def test_T22g_denetim_kaydi_yazilir(db, gateway):
    from app.models.system_event import SystemEvent

    gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI + 60)
    sonucu_gelmeyenleri_sonlandir(db)
    db.commit()

    olaylar = db.scalars(
        select(SystemEvent).where(SystemEvent.event_type == "device_command_result_unknown")
    ).all()
    assert len(olaylar) == 1


# ---------------------------------------------------------------------------
# T23 — gec gelen gercek sonucun mutabakati
# ---------------------------------------------------------------------------


def test_T23_gec_gelen_gercek_sonuc_result_unknown_u_duzeltir(db, gateway):
    """Gateway sonucu gec teslim ederse tahmin DUZELTILIR."""
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI + 60)
    sonucu_gelmeyenleri_sonlandir(db)
    db.commit()
    db.refresh(cmd)
    assert cmd.result_status == teslim.RESULT_UNKNOWN

    sonuc_bildir(db, cmd.id, ok=True, status="ok")
    db.refresh(cmd)
    assert cmd.status == "ok", "gec gelen gercek sonuc YUTULDU"
    assert cmd.result_status == "ok"


def test_T23b_gec_gelen_basarisiz_sonuc_da_kabul_edilir(db, gateway):
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI + 60)
    sonucu_gelmeyenleri_sonlandir(db)
    db.commit()

    sonuc_bildir(db, cmd.id, ok=False, status="NO_SELECT")
    db.refresh(cmd)
    assert cmd.status == "failed"
    assert cmd.result_status == "NO_SELECT"


def test_T23c_ISTISNA_DAR_ok_sonucu_degistirilemez(db, gateway):
    """Mukerrer sonuc bildirimi hala idempotent — `ok` KILITLI."""
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=1)
    sonuc_bildir(db, cmd.id, ok=True, status="ok")
    db.refresh(cmd)
    assert cmd.status == "ok"

    sonuc_bildir(db, cmd.id, ok=False, status="NO_SELECT")
    db.refresh(cmd)
    assert cmd.status == "ok", "terminal `ok` degistirildi — idempotency kirildi"
    assert cmd.result_status == "ok"


def test_T23d_ISTISNA_DAR_normal_failed_degistirilemez(db, gateway):
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=1)
    sonuc_bildir(db, cmd.id, ok=False, status="NO_SELECT")
    db.refresh(cmd)
    assert (cmd.status, cmd.result_status) == ("failed", "NO_SELECT")

    sonuc_bildir(db, cmd.id, ok=True, status="ok")
    db.refresh(cmd)
    assert cmd.status == "failed", "normal `failed` -> `ok` gecisi acilmis"
    assert cmd.result_status == "NO_SELECT"


def test_T23e_ISTISNA_DAR_expired_degistirilemez(db, gateway):
    """`expired` = komut cihaza HIC gitmedi. `ok` olmasi imkansiz."""
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=1)
    cmd.status = "failed"
    cmd.result_status = teslim.RESULT_EXPIRED
    db.commit()

    sonuc_bildir(db, cmd.id, ok=True, status="ok")
    db.refresh(cmd)
    assert cmd.result_status == teslim.RESULT_EXPIRED, "expired -> ok gecisi acilmis"


def test_T23f_delivery_state_lost_degistirilemez(db, gateway):
    """Operator incelemesine birakilan komut sessizce kapanmamali."""
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=1)
    cmd.status = "failed"
    cmd.result_status = teslim.RESULT_DELIVERY_STATE_LOST
    db.commit()

    sonuc_bildir(db, cmd.id, ok=True, status="ok")
    db.refresh(cmd)
    assert cmd.result_status == teslim.RESULT_DELIVERY_STATE_LOST


def test_REGRESYON_cancelled_komut_gec_sonucla_EZILMEZ(db, gateway):
    """BULGU: guard yalnizca ('ok','failed') bakiyordu; `cancelled` eleniyordu.

    Cihaz silindiginde komutlar `cancelled` + "Cihaz silindi" yapiliyor
    (device_repository). Gateway'in gec gelen sonucu o kaydi `ok` yapip iptal
    gerekcesini siliyor, ustelik silinmis bir cihaz kodu icin yeni bir olay
    uretiyordu.
    """
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=1)
    cmd.status = "cancelled"
    cmd.result_error = "Cihaz silindi"
    db.commit()

    sonuc_bildir(db, cmd.id, ok=True, status="ok")
    db.refresh(cmd)

    assert cmd.status == "cancelled", "iptal edilmis komut gec sonucla dirildi"
    assert cmd.result_error == "Cihaz silindi", "iptal gerekcesi silindi"


def test_T23g_mutabakat_sonrasi_tekrar_bildirim_idempotent(db, gateway):
    """Duzeltilen komut artik terminal; ikinci bildirim onu degistirmez."""
    cmd = gonderilmis_komut(db, gonderim_yasi_sn=ZAMAN_ASIMI + 60)
    sonucu_gelmeyenleri_sonlandir(db)
    db.commit()

    sonuc_bildir(db, cmd.id, ok=True, status="ok")
    sonuc_bildir(db, cmd.id, ok=False, status="NO_SELECT")
    db.refresh(cmd)
    assert cmd.status == "ok"


# ---------------------------------------------------------------------------
# Yapilandirma
# ---------------------------------------------------------------------------


def test_zaman_asimi_TTL_den_kucuk_olamaz():
    """`COMMAND_RESULT_TIMEOUT_SEC <= COMMAND_MAX_AGE_SEC` acilista REDDEDILIR."""
    from app.core.config import Settings

    with pytest.raises(RuntimeError, match="COMMAND_RESULT_TIMEOUT_SEC"):
        Settings(command_max_age_sec=300, command_result_timeout_sec=120)


@pytest.mark.parametrize(
    "alan",
    [
        "command_delivery_lease_sec",
        "command_delivery_max_attempts",
        "command_result_timeout_sec",
        "command_result_sweep_interval_sec",
    ],
)
def test_teslim_ayarlari_kapatilamaz(alan):
    """FAIL-OPEN YOK: hicbir teslim ayari 0 ile devre disi birakilamaz."""
    from app.core.config import Settings

    with pytest.raises(RuntimeError):
        Settings(**{alan: 0})
