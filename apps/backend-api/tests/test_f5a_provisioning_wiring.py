"""F5 komut sirri DB'den DEPLOYMENT ARTEFAKTINA kadar tasinir.

KAPATILAN ACIK
--------------
F5A guvenlik mantigi (dual auth, `/pending` HMAC ayrimi) v2.100.1'de vardi ve
dogruydu; ama sir DB'den uretilen artefakta HIC GECMIYORDU:
`_build_render_input()` `gateway.command_delivery_token` degerini
`ComposeRenderInput`e tasimiyordu.

Sonuc production blocker'di:

    DB'ye sir yazilir  -> backend o gateway icin STRICT moda gecer
    artefakt sirsiz    -> gateway `X-Gateway-Command-Token` GONDEREMEZ
                       -> /pending 401  ->  KOMUT KANALI KESILIR

Yani guvenlik mantigi dogru, tasima yolu kopuktu. Bu dosya tasima yolunu
davranis duzeyinde kilitler.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.gateways import _build_render_input
from app.db.base import Base
from app.models.gateway import Gateway
from app.services.gateway_compose import (
    ComposeRenderInput,
    generate_command_delivery_token,
    render_compose,
    render_env,
)

SIR = "komut-duzlemi-sirri-" + "s" * 24
ENV_ADI = "GATEWAY_COMMAND_DELIVERY_TOKEN"


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


def _gw(db, kod="GW-1", *, sir=None):
    g = Gateway(
        code=kod,
        name=kod,
        host="10.0.0.1",
        listen_port=20000,
        token="normal-" + "t" * 40,
        command_token="operate-" + "o" * 40,
        command_delivery_token=sir,
        is_active=True,
    )
    db.add(g)
    db.commit()
    return g


def _girdi(db, g):
    return _build_render_input(
        db,
        g,
        backend_url="http://10.0.0.5/api/v1",
        nats_url="nats://gateway:pw@10.0.0.5:4222",
        host_port=8020,
        image="ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest",
        app_environment="production",
    )


# ---------------------------------------------------------------------------
# A. render girdisi -- ASIL KOPUK HALKA
# ---------------------------------------------------------------------------


def test_A1_db_NULL_ise_render_girdisi_None(db):
    assert _girdi(db, _gw(db, sir=None)).command_delivery_token is None


def test_A2_db_DOLU_ise_render_girdisine_TASINIR(db):
    """Kopuk halka tam olarak buydu."""
    assert _girdi(db, _gw(db, sir=SIR)).command_delivery_token == SIR


@pytest.mark.parametrize("render", [render_compose, render_env])
def test_A3_A5_sir_yokken_env_URETILMEZ(db, render):
    assert ENV_ADI not in render(_girdi(db, _gw(db, sir=None)))


@pytest.mark.parametrize("render", [render_compose, render_env])
def test_A4_A6_sir_varken_env_DOGRU_DEGERLE_uretilir(db, render):
    govde = render(_girdi(db, _gw(db, sir=SIR)))
    assert ENV_ADI in govde
    assert SIR in govde


def test_B7_B9_indirilen_artefakt_uctan_uca(db):
    """`/gateways/{kod}/docker-compose` ayni `_build_render_input`ten besleniyor."""
    yok = _girdi(db, _gw(db, "GW-A", sir=None))
    var = _girdi(db, _gw(db, "GW-B", sir=SIR))
    for render in (render_compose, render_env):
        assert ENV_ADI not in render(yok)
        assert ENV_ADI in render(var)


# ---------------------------------------------------------------------------
# C. yerel kurulum -> host ajani
# ---------------------------------------------------------------------------


def test_C10_C12_ajan_istegi_sirri_yalnizca_VARSA_tasir(monkeypatch):
    from app.services import gateway_agent_service as ajan

    yakalanan = {}
    monkeypatch.setattr(ajan, "_write_request", lambda body: yakalanan.update(body) or "req-1")

    ortak = dict(
        image="img", token="t" * 40, backend_url="http://x/api/v1",
        nats_url="nats://a:b@x:4222", host_port=8020, app_environment="production",
        initiating_port_base=20100, initiating_port_count=0,
    )
    ajan.request_install("GW-1", "S", "kullanici", **ortak)
    assert "command_delivery_token" not in yakalanan["params"], (
        "provision edilmemis gateway icin sir gonderilmemeli"
    )

    yakalanan.clear()
    ajan.request_install("GW-1", "S", "kullanici", command_delivery_token=SIR, **ortak)
    assert yakalanan["params"]["command_delivery_token"] == SIR


def test_C11_ajan_sablonu_sirri_compose_a_yaziyor():
    import importlib.util
    from pathlib import Path

    kok = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("e1gwd", kok / "infra/appliance/e1-gwd.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert "command_delivery_token" in mod.ALLOWED_PARAM_KEYS, (
        "ajan allowlist'i sirri reddeder -> kurulum 400 doner"
    )

    temel = dict(
        token="t" * 40, backend_url="http://host.docker.internal/api/v1",
        nats_url="nats://a:b@h:4222", host_port=8020,
        image="ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest",
        app_environment="production", initiating_port_base=20100,
        initiating_port_count=0, publish_dnp3_quality=False,
    )
    assert ENV_ADI not in mod.render_compose("GW-1", "S", dict(temel))
    govde = mod.render_compose("GW-1", "S", dict(temel, command_delivery_token=SIR))
    assert ENV_ADI in govde and SIR in govde

    import yaml

    yaml.safe_load(govde)  # gecerli YAML kalmali


# ---------------------------------------------------------------------------
# D. provisioning
# ---------------------------------------------------------------------------


def test_D13_D16_sir_CSPRNG_ve_gateway_basina_benzersiz(db):
    g = _gw(db, sir=None)
    sirlar = {generate_command_delivery_token() for _ in range(50)}
    assert len(sirlar) == 50
    assert len(next(iter(sirlar))) >= 32
    for s in list(sirlar)[:5]:
        assert s != g.token
        assert s != g.command_token


def test_D17_mevcut_sir_SESSIZCE_degistirilmez(db):
    """Ustune yazmak sahadaki gateway'in kanalini sessizce keserdi."""
    from app.api.gateways import provision_command_credential

    g = _gw(db, sir=SIR)

    class _K:
        username = "kurulumcu"

    sonuc = provision_command_credential(gateway_code=g.code, db=db, current_user=_K())
    assert sonuc["created"] is False
    db.expire_all()
    assert db.get(Gateway, g.id).command_delivery_token == SIR


def test_D18_uretim_audit_kaydi_birakir_ama_SIRRI_yazmaz(db):
    from app.api.gateways import provision_command_credential
    from app.models.system_event import SystemEvent

    g = _gw(db, sir=None)

    class _K:
        username = "kurulumcu"

    sonuc = provision_command_credential(gateway_code=g.code, db=db, current_user=_K())
    assert sonuc["created"] is True

    db.expire_all()
    uretilen = db.get(Gateway, g.id).command_delivery_token
    assert uretilen and uretilen != g.token

    olaylar = db.query(SystemEvent).all()
    assert any(o.event_type == "gateway_command_credential_provisioned" for o in olaylar)
    for o in olaylar:
        assert uretilen not in (o.message or "")
        assert uretilen not in str(o.event_metadata or o.metadata_json if hasattr(o, "event_metadata") else "")


def test_D19_sir_siradan_gateway_semasinda_YOK():
    from app.schemas import gateway as gw_schema

    for ad in dir(gw_schema):
        alanlar = getattr(getattr(gw_schema, ad), "model_fields", None)
        if isinstance(alanlar, dict):
            assert "command_delivery_token" not in alanlar, f"{ad} sirri disariya aciyor"
