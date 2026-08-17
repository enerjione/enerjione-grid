"""Backend->gateway yanit imzasi FAIL-CLOSED (F4A).

KAPATILAN ACIK
--------------
`_signed_json_response` imza uretimindeki hatayi YAKALIYOR, logluyor ve
govdeyi BASLIKSIZ 200 olarak donuyordu. Gateway tarafinda dogrulama
"baslik varsa dogrula" seklinde oldugu icin iki uc birlikte sessizce
authenticity'siz calisabiliyordu.

Bu iki ucun tasidigi sey onemsiz degil:

  GET /config   -> cihaz listesi, IP/adres ve BINARY OUTPUT KATALOGU
                   (gateway'deki F1/F2 yetkilendirmesinin GIRDISI)
  GET /pending  -> FIZIKSEL KOMUT niyeti: command, dnp3_index, created_at,
                   delivery_token

Saha gateway'leri backend'e duz HTTP ile baglaniyor; yani bu iki uc icin
imza TEK authenticity kontrolu. Imzasiz bir 200, kataloğu degistirip
F1/F2'yi etkisiz kilmaya ya da dogrudan komut enjekte etmeye acik kapi
birakirdi.

SOZLESME: gecerli imzali 200 YA DA 5xx. Ucuncu secenek yok.

TEL SOZLESMESI DEGISMEDI — gateway v1.9.0 zaten bu bicimi dogruluyor:
  govde  : model.model_dump_json().encode("utf-8")
  alg    : HMAC-SHA256
  anahtar: gateway.token
  baslik : X-Config-Signature
  bicim  : kucuk harf hex (64 karakter)
"""

from __future__ import annotations

import hashlib
import hmac
import re

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import gateways as gw_api
from app.core.config import settings
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway
from app.services import command_delivery_service as teslim

BASLIK = "X-Config-Signature"


# ---------------------------------------------------------------------------
# Kurulum
# ---------------------------------------------------------------------------


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
    monkeypatch.setattr(settings, "command_delivery_lease_sec", 30, raising=False)
    monkeypatch.setattr(settings, "command_delivery_max_attempts", 5, raising=False)
    monkeypatch.setattr(settings, "command_delivery_ack_required", True, raising=False)
    teslim._legacy_last_warn.clear()


@pytest.fixture()
def gateway(db):
    g = Gateway(
        code="GW-1",
        name="Saha 1",
        host="10.0.0.1",
        listen_port=20000,
        token="cok-gizli-token",
        is_active=True,
    )
    db.add(g)
    db.add(
        Device(
            code="CIHAZ-A",
            name="A",
            gateway_code="GW-1",
            ip_address="10.0.0.50",
            latitude=39.0,
            longitude=35.0,
        )
    )
    db.commit()
    return g


@pytest.fixture(autouse=True)
def token_dogrulamasi_baypas(monkeypatch, request):
    if "gateway" not in request.fixturenames:
        return

    def _sahte(db_, kod, token):  # noqa: ANN001
        return db_.scalars(select(Gateway).where(Gateway.code == kod)).first()

    monkeypatch.setattr("app.services.ingest_service.validate_gateway_token", _sahte)


class _SahteModel:
    """`model_dump_json()` sozlesmesini karsilayan en kucuk nesne."""

    def __init__(self, govde: str = '{"a":1}') -> None:
        self._govde = govde

    def model_dump_json(self) -> str:
        return self._govde


def _imzala(token: str, govde: bytes) -> str:
    return hmac.new(token.encode("utf-8"), govde, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# 1-4. Mutlu yol: imza var ve TAM govde byte'lari uzerinden dogrulaniyor
# ---------------------------------------------------------------------------


def test_yanit_imzali_doner(gateway):
    resp = gw_api._signed_json_response(gateway, _SahteModel(), context="test")
    assert BASLIK in resp.headers


def test_imza_tam_govde_byte_lari_uzerinden(gateway):
    """Gateway ayni byte'lardan dogruluyor; serialize farki imzayi bozar."""
    model = _SahteModel('{"gateway_code":"GW-1","x":42}')
    resp = gw_api._signed_json_response(gateway, model, context="test")

    beklenen = _imzala(gateway.token, model.model_dump_json().encode("utf-8"))
    assert resp.headers[BASLIK] == beklenen
    # Govde AYNEN yaziliyor — FastAPI'nin kendi renderer'i devrede degil.
    assert resp.body == model.model_dump_json().encode("utf-8")


def test_imza_kucuk_harf_64_karakter_hex(gateway):
    resp = gw_api._signed_json_response(gateway, _SahteModel(), context="test")
    assert re.fullmatch(r"[0-9a-f]{64}", resp.headers[BASLIK])


def test_extra_headers_korunur(gateway):
    """Config ucu ETag'i bu yolla tasiyor; imza onu ezmemeli."""
    resp = gw_api._signed_json_response(
        gateway, _SahteModel(), extra_headers={"etag": '"v1"'}, context="config"
    )
    assert resp.headers["etag"] == '"v1"'
    assert BASLIK in resp.headers


# ---------------------------------------------------------------------------
# 5-6. Kurcalama
# ---------------------------------------------------------------------------


def test_govde_tek_byte_degisince_imza_tutmaz(gateway):
    resp = gw_api._signed_json_response(gateway, _SahteModel('{"a":1}'), context="test")
    kurcalanmis = resp.body.replace(b'"a":1', b'"a":2')
    assert kurcalanmis != resp.body
    assert resp.headers[BASLIK] != _imzala(gateway.token, kurcalanmis)


def test_baska_token_ile_imza_dogrulanmaz(gateway):
    """12: token rotasyonundan sonra eski anahtarla dogrulama BASARISIZ."""
    resp = gw_api._signed_json_response(gateway, _SahteModel(), context="test")
    assert resp.headers[BASLIK] != _imzala("eski-token", resp.body)


# ---------------------------------------------------------------------------
# 7. FAIL-CLOSED — asil iddia
# ---------------------------------------------------------------------------


class _TokensuzGateway:
    """Imza uretimini KONTROLLU sekilde patlatir (`token` None -> AttributeError)."""

    code = "GW-1"
    token = None


def test_imza_uretilemezse_imzasiz_200_DONMEZ(caplog):
    with pytest.raises(HTTPException) as hata:
        gw_api._signed_json_response(_TokensuzGateway(), _SahteModel(), context="pending")

    assert hata.value.status_code == 500
    assert hata.value.detail == "Gateway response signing failed"


def test_hata_govdesi_sir_sizdirmaz():
    with pytest.raises(HTTPException) as hata:
        gw_api._signed_json_response(_TokensuzGateway(), _SahteModel('{"gizli":"x"}'))

    metin = str(hata.value.detail)
    assert "gizli" not in metin
    assert BASLIK not in metin
    assert "token" not in metin.lower()


def test_ic_log_gateway_ve_baglam_tasir_token_tasimaz(caplog):
    caplog.set_level("ERROR")
    with pytest.raises(HTTPException):
        gw_api._signed_json_response(_TokensuzGateway(), _SahteModel(), context="pending")

    kayit = caplog.text
    assert "gateway_body_signature_failed" in kayit
    assert "GW-1" in kayit
    assert "pending" in kayit
    assert "cok-gizli-token" not in kayit


# ---------------------------------------------------------------------------
# 8-9. /pending imza hatasi: komut yasam dongusu GUVENLI kalmali
# ---------------------------------------------------------------------------


def _komut_ekle(db) -> DeviceCommand:
    cmd = DeviceCommand(
        gateway_code="GW-1",
        device_code="CIHAZ-A",
        command="fault_reset",
        dnp3_index=3,
        status="pending",
    )
    db.add(cmd)
    db.commit()
    return cmd


def test_pending_imza_hatasi_komutu_sent_yapmaz(db, gateway, monkeypatch):
    """8: imza patlarsa komut TESLIM EDILMIS sayilmaz.

    Kira ZATEN commit edilmis olabilir (imza commit'ten SONRA hesaplaniyor);
    bu guvenlidir ve P1 sozlesmesinin ta kendisidir: `sent` yalnizca gateway
    dayanikli ACK gonderince olur. Kira suresi dolunca komut normal
    mekanizmayla YENIDEN teklif edilir; fiziksel komut uretilmez.
    """
    cmd = _komut_ekle(db)

    def _patlat(gateway_, model, extra_headers=None, **_):  # noqa: ANN001
        raise HTTPException(status_code=500, detail="Gateway response signing failed")

    monkeypatch.setattr(gw_api, "_signed_json_response", _patlat)

    with pytest.raises(HTTPException) as hata:
        gw_api.get_gateway_pending(
            gateway_code="GW-1",
            db=db,
            x_gateway_token="t",
            x_gateway_health=None,
            x_e1_delivery=_baslik(),
        )
    assert hata.value.status_code == 500

    db.expire_all()
    taze = db.get(DeviceCommand, cmd.id)
    assert taze.status == "pending", "imza hatasinda komut `sent` yapilmis"
    assert taze.sent_at is None, "teslim dogrulanmadan sent_at yazilmis"


def test_pending_imza_hatasi_mutlak_ttl_yi_otelemez(db, gateway, monkeypatch):
    """9: yasam dongusu tutarli — `created_at` degismez, TTL kaymaz."""
    cmd = _komut_ekle(db)
    olusturma = cmd.created_at

    def _patlat(gateway_, model, extra_headers=None, **_):  # noqa: ANN001
        raise HTTPException(status_code=500, detail="Gateway response signing failed")

    monkeypatch.setattr(gw_api, "_signed_json_response", _patlat)
    with pytest.raises(HTTPException):
        gw_api.get_gateway_pending(
            gateway_code="GW-1",
            db=db,
            x_gateway_token="t",
            x_gateway_health=None,
            x_e1_delivery=_baslik(),
        )

    db.expire_all()
    taze = db.get(DeviceCommand, cmd.id)
    assert taze.created_at == olusturma
    assert taze.status not in ("ok", "failed"), "imza hatasi komutu terminal yapmis"


# ---------------------------------------------------------------------------
# 10. Normal yol degismedi
# ---------------------------------------------------------------------------


def test_imza_saglikliyken_pending_normal_calisir(db, gateway):
    """Kira olusur, komut yanitta gider, `sent` OLMAZ (ACK bekler)."""
    cmd = _komut_ekle(db)

    resp = gw_api.get_gateway_pending(
        gateway_code="GW-1",
        db=db,
        x_gateway_token="t",
        x_gateway_health=None,
        x_e1_delivery=_baslik(),
    )

    assert BASLIK in resp.headers
    db.expire_all()
    taze = db.get(DeviceCommand, cmd.id)
    assert taze.status == "pending", "ACK gelmeden `sent` yapilmis"
    assert taze.delivery_token, "kira jetonu uretilmemis"
    assert taze.delivery_attempt == 1


def _baslik(epoch: str = "epoch-f4a-1", surum: int = 1) -> str:
    import base64
    import json

    ham = json.dumps({"v": surum, "epoch": epoch}, separators=(",", ":"))
    return base64.urlsafe_b64encode(ham.encode()).decode()
