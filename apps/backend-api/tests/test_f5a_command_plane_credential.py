"""Kuyruklanmis komut duzlemi AYRI credential ile korunur (F5A).

KAPATILAN ACIK
--------------
`/config` ile `/pending` ayni `GATEWAY_TOKEN` ile korunuyordu. O token
sizarsa yalnizca konfigurasyon degil FIZIKSEL KOMUT duzlemi de ele gecerdi:
saldirgan `/pending` cagirip komut kuyrugunu okuyabilir, dahasi ayni anahtarla
SAHTE BIR `/pending` YANITI IMZALAYABILIRDI.

Bu yuzden ayrim iki katmanlidir ve ikisi de gereklidir:

  1. ISTEK kimligi : `X-Gateway-Command-Token` (normal kimligin YERINE GECMEZ)
  2. YANIT imzasi  : `/pending` HMAC anahtari = command_delivery_token

Yalnizca (1) yapilsaydi ayrim kagit uzerinde kalirdi.

GECIS
-----
`gateways.command_delivery_token` NULL = provision EDILMEMIS gateway; komut
uclari eski gibi calisir (saha rollout'u backend rollout'undan bagimsiz
olsun diye). DOLU = strict: eksik/yanlis baslik REDDEDILIR ve normal
token'a GERI DUSULMEZ.

`command_token` ile karistirilmamali: o legacy dogrudan `/operate` yoluna
aittir ve F5A onu ne kullanir ne de canlandirir.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import gateways as gw_api
from app.core.config import settings
from app.db.base import Base
from app.models.device import Device
from app.models.gateway import Gateway
from app.services import command_delivery_service as teslim
from app.services.gateway_compose import generate_command_delivery_token
from app.services.ingest_service import validate_gateway_command_delivery_token

NORMAL = "normal-gateway-token-" + "n" * 28
KOMUT = "komut-duzlemi-sirri-" + "k" * 28
OPERATE = "legacy-operate-token-" + "o" * 28


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=True)()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture(autouse=True)
def ayarlar(monkeypatch):
    monkeypatch.setattr(settings, "command_max_age_sec", 120, raising=False)
    monkeypatch.setattr(settings, "command_delivery_lease_sec", 30, raising=False)
    monkeypatch.setattr(settings, "command_delivery_ack_required", False, raising=False)
    teslim._legacy_last_warn.clear()
    from app.services import ingest_service

    ingest_service._f5_legacy_uyarildi.clear()


def _gateway(db, kod: str, *, komut_sirri: str | None) -> Gateway:
    g = Gateway(
        code=kod,
        name=kod,
        host="10.0.0.1",
        listen_port=20000,
        token=NORMAL if kod == "GW-1" else NORMAL + "-" + kod,
        command_token=OPERATE,
        command_delivery_token=komut_sirri,
        is_active=True,
    )
    db.add(g)
    db.add(
        Device(
            code=f"CIHAZ-{kod}",
            name="d",
            gateway_code=kod,
            ip_address="10.0.0.50",
            latitude=39.0,
            longitude=35.0,
        )
    )
    db.commit()
    return g


def _pending(db, g: Gateway, *, token: str | None, komut: str | None):
    return gw_api.get_gateway_pending(
        gateway_code=g.code,
        db=db,
        x_gateway_token=token,
        x_gateway_command_token=komut,
        x_gateway_health=None,
        x_e1_delivery=None,
    )


# ---------------------------------------------------------------------------
# A. /config DEGISMEDI
# ---------------------------------------------------------------------------


def test_A1_A2_config_komut_tokeni_ISTEMEZ(db):
    """`/config` imzasi ve auth'u F5A'dan etkilenmemeli."""
    import inspect

    imza = inspect.signature(gw_api.get_gateway_config)
    assert "x_gateway_command_token" not in imza.parameters, (
        "/config komut credential'i istiyor — config duzlemi F5A'dan "
        "etkilenmemeliydi"
    )


def test_A3_config_normal_token_ile_imzalanir(db):
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)

    class _M:
        def model_dump_json(self):
            return '{"a":1}'

    resp = gw_api._signed_json_response(g, _M(), context="config")
    beklenen = hmac.new(g.token.encode(), resp.body, hashlib.sha256).hexdigest()
    assert resp.headers["X-Config-Signature"] == beklenen


# ---------------------------------------------------------------------------
# B. GECIS (command_delivery_token NULL)
# ---------------------------------------------------------------------------


def test_B4_legacy_gateway_yalnizca_normal_token_ile_calisir(db):
    g = _gateway(db, "GW-1", komut_sirri=None)
    resp = _pending(db, g, token=NORMAL, komut=None)
    assert resp is not None


def test_B5_legacy_pending_normal_token_ile_imzalanir(db):
    g = _gateway(db, "GW-1", komut_sirri=None)

    class _M:
        def model_dump_json(self):
            return '{"a":1}'

    resp = gw_api._signed_json_response(g, _M(), context="pending")
    beklenen = hmac.new(g.token.encode(), resp.body, hashlib.sha256).hexdigest()
    assert resp.headers["X-Config-Signature"] == beklenen


# ---------------------------------------------------------------------------
# C. STRICT (command_delivery_token DOLU)
# ---------------------------------------------------------------------------


def test_C6_iki_credential_de_dogruysa_KABUL(db):
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)
    assert _pending(db, g, token=NORMAL, komut=KOMUT) is not None


def test_C7_komut_tokeni_EKSIK_ise_RED(db):
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)
    with pytest.raises(HTTPException) as h:
        _pending(db, g, token=NORMAL, komut=None)
    assert h.value.status_code == 401


def test_C8_komut_tokeni_YANLIS_ise_RED(db):
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)
    with pytest.raises(HTTPException) as h:
        _pending(db, g, token=NORMAL, komut="yanlis-sir")
    assert h.value.status_code == 401


def test_C9_normal_token_yanlissa_komut_dogru_olsa_bile_RED(db):
    """Komut credential'i gateway kimligi YERINE GECMEZ."""
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)
    with pytest.raises(HTTPException) as h:
        _pending(db, g, token="yanlis-normal-token", komut=KOMUT)
    assert h.value.status_code == 401


def test_C10_baska_gatewayin_komut_tokeni_CALISMAZ(db):
    a = _gateway(db, "GW-1", komut_sirri=KOMUT)
    b = _gateway(db, "GW-2", komut_sirri="bambaska-sir-" + "b" * 30)
    with pytest.raises(HTTPException) as h:
        _pending(db, b, token=b.token, komut=a.command_delivery_token)
    assert h.value.status_code == 401


def test_C11_C12_pending_KOMUT_sirriyla_imzalanir(db):
    """Ayrimin asil kanidi: imza anahtari degisiyor."""
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)

    class _M:
        def model_dump_json(self):
            return '{"komut":"fault_reset"}'

    resp = gw_api._signed_json_response(g, _M(), context="pending")
    komutla = hmac.new(KOMUT.encode(), resp.body, hashlib.sha256).hexdigest()
    normalle = hmac.new(g.token.encode(), resp.body, hashlib.sha256).hexdigest()

    assert resp.headers["X-Config-Signature"] == komutla
    assert resp.headers["X-Config-Signature"] != normalle, (
        "ayni govde normal token ile de dogrulanabiliyor — komut duzlemi "
        "gercekte AYRILMAMIS"
    )


def test_C13_komut_sirri_varken_normal_anahtara_FALLBACK_YOK(db):
    """Kaynak duzeyinde tripwire: `/pending` dalinda geri dusme olmamali."""
    import inspect

    kaynak = inspect.getsource(gw_api._signed_json_response)
    assert 'context == "pending"' in kaynak
    # Imza anahtari secildikten SONRA gateway.token'a donen bir yol olmamali.
    sonrasi = kaynak.split('context == "pending"', 1)[1]
    assert "imza_anahtari = gateway.token" not in sonrasi, (
        "komut sirri secildikten sonra normal token'a geri dusen bir yol var"
    )


# ---------------------------------------------------------------------------
# D/E. ACK ve RESULT ayni guvenlik alani
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uc", ["report_command_delivery_acks", "report_command_results"])
def test_D14_E17_ack_ve_result_komut_credentiali_ister(uc):
    import inspect

    imza = inspect.signature(getattr(gw_api, uc))
    assert "x_gateway_command_token" in imza.parameters, (
        f"{uc} komut credential'i istemiyor — ayni guvenlik alaninda olmali"
    )
    kaynak = inspect.getsource(getattr(gw_api, uc))
    assert "validate_gateway_command_delivery_token" in kaynak


@pytest.mark.parametrize("verilen", [None, "yanlis"])
def test_D16_E19_validator_eksik_ve_yanlis_baslikta_REDDEDER(db, verilen):
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)
    with pytest.raises(HTTPException) as h:
        validate_gateway_command_delivery_token(g, verilen)
    assert h.value.status_code == 401


def test_D15_E18_legacy_gatewayde_validator_gecirir(db):
    g = _gateway(db, "GW-1", komut_sirri=None)
    validate_gateway_command_delivery_token(g, None)  # yukselmemeli


# ---------------------------------------------------------------------------
# F. SIR YONETIMI
# ---------------------------------------------------------------------------


def test_F20_sir_API_yanitinda_GORUNMEZ():
    """Yeni alan gateway okuma semasina SIZMAMALI."""
    from app.schemas import gateway as gw_schema

    for ad in dir(gw_schema):
        nesne = getattr(gw_schema, ad)
        alanlar = getattr(nesne, "model_fields", None)
        if isinstance(alanlar, dict):
            assert "command_delivery_token" not in alanlar, (
                f"{ad} semasi komut sirrini disariya aciyor"
            )


def test_F21_sir_LOGLANMAZ(db, caplog):
    caplog.set_level("WARNING")
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)
    with pytest.raises(HTTPException):
        validate_gateway_command_delivery_token(g, "yanlis")
    assert KOMUT not in caplog.text
    assert "yanlis" not in caplog.text.replace("mismatch", "")
    assert "GW-1" in caplog.text


def test_F23_F24_uretilen_sir_diger_iki_tokendan_FARKLI(db):
    g = _gateway(db, "GW-1", komut_sirri=None)
    sir = generate_command_delivery_token()
    assert sir != g.token
    assert sir != g.command_token
    assert len(sir) >= 32


def test_F22_her_gateway_icin_FARKLI_sir():
    sirlar = {generate_command_delivery_token() for _ in range(50)}
    assert len(sirlar) == 50


# ---------------------------------------------------------------------------
# Kapsam siniri: /operate credential'i yeniden KULLANILMADI
# ---------------------------------------------------------------------------


def test_operate_command_tokeni_YENIDEN_KULLANILMADI(db):
    """Iddia METIN degil DAVRANIS uzerinden kurulur.

    Ilk hali kaynak metninde "command_token" ariyordu ve validator'in kendi
    ACIKLAMA satirina takiliyordu — yani gerekceyi yazmak testi kirmiziya
    dusuruyordu. Dogru soru "bu kelime geciyor mu" degil, "/operate sirri
    komut duzlemini acabiliyor mu".
    """
    g = _gateway(db, "GW-1", komut_sirri=KOMUT)

    # /operate credential'i komut duzleminde GECMEMELI.
    with pytest.raises(HTTPException) as h:
        validate_gateway_command_delivery_token(g, OPERATE)
    assert h.value.status_code == 401

    # Ve komut sirri /operate sirriyla ayni uretilmemeli.
    assert g.command_delivery_token != g.command_token
