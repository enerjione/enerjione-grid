"""Indirme ucu kurulum modunu ACIKCA tasiyor mu?

YASANAN SORUN
-------------
`GET /gateways/{kod}/docker-compose` "baska cihaza kur" icin tasarlanmisti ve
INSTALL_MODE'u SABIT `remote` uretiyordu. Ama uc, YEREL kurulumlara da
hizmet ediyor: "bu cihaza kur" host ajaniyla basarisiz olunca sihirbaz
kullaniciyi elle kuruluma dusuruyor (GatewayCreateModal `fallbackToManual`)
ve o kullanici AYNI MAKINEYE kuracagi dosyayi bu uctan indiriyor.

Sabit `remote` ile o kurulum, gateway sozlesmesinin yerel mod icin
YASAKLADIGI sessiz HTTP yedegini kazaniyordu: ayni makinede NATS'a
erisilememek bir YAPILANDIRMA HATASIDIR, gizlenmemeli.

Gateway tarafinda karsiligi v1.11.3'te yapildi: `render_compose.py
--install-mode` artik ZORUNLU, varsayilan YOK.

BU DOSYA NEYI KILITLER
----------------------
  * Mod render zamani secilebiliyor ve iki sablona da EXPLICIT giriyor.
  * Parametre verilmezse davranis DEGISMIYOR (remote) -- mevcut "baska
    cihaza kur" akisi ve elle yazilmis script'ler bozulmaz.
  * Ucun imzasi parametreyi gercekten BEYAN ediyor (sessizce dusmesin).
  * "Bu cihaza kur" yolu ajanin parametre sinirini genisletmiyor: compose'u
    ajan kendi sablonundan uretir, mod disardan gelmez.
"""

from __future__ import annotations

import inspect
import typing

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.gateways import _build_render_input, download_gateway_compose
from app.db.base import Base
from app.models.gateway import Gateway
from app.services.gateway_compose import render_compose, render_env


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


def _gw(db, kod="GW-1"):
    g = Gateway(
        code=kod,
        name=kod,
        host="10.0.0.1",
        listen_port=20000,
        token="normal-" + "t" * 40,
        command_token="operate-" + "o" * 40,
        is_active=True,
    )
    db.add(g)
    db.commit()
    return g


def _girdi(db, g, **kw):
    return _build_render_input(
        db,
        g,
        backend_url="http://10.0.0.5/api/v1",
        nats_url="nats://gateway:pw@10.0.0.5:4222",
        host_port=8020,
        image="ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest",
        app_environment="production",
        **kw,
    )


def _compose_env(govde: str) -> dict[str, str]:
    svc = yaml.safe_load(govde)["services"]["gateway"]
    return {k: str(v) for k, v in svc["environment"].items()}


def _env_dosyasi(govde: str) -> dict[str, str]:
    out = {}
    for satir in govde.splitlines():
        satir = satir.strip()
        if satir and not satir.startswith("#") and "=" in satir:
            k, v = satir.split("=", 1)
            out[k] = v
    return out


@pytest.mark.parametrize("mod", ["local", "remote"])
def test_secilen_mod_compose_ve_env_ciktisina_gecer(db, mod):
    girdi = _girdi(db, _gw(db), install_mode=mod)
    assert _compose_env(render_compose(girdi))["INSTALL_MODE"] == mod
    assert _env_dosyasi(render_env(girdi))["INSTALL_MODE"] == mod


def test_parametresiz_cagri_remote_uretir_davranis_degismedi(db):
    """Geri uyum: mevcut "baska cihaza kur" akisi ve script'ler bozulmasin."""
    girdi = _girdi(db, _gw(db))
    assert _compose_env(render_compose(girdi))["INSTALL_MODE"] == "remote"
    assert _env_dosyasi(render_env(girdi))["INSTALL_MODE"] == "remote"


def test_uc_install_mode_parametresini_BEYAN_ediyor():
    """Imzadan dusesse frontend'in gonderdigi deger sessizce yok sayilirdi.

    FastAPI bilinmeyen query parametresini yok sayar: 422 gelmez, dosya
    uretilir, INSTALL_MODE yine `remote` olur. Sessiz basarisizlik.
    """
    imza = inspect.signature(download_gateway_compose)
    assert "install_mode" in imza.parameters, (
        "indirme ucu install_mode parametresini beyan etmiyor"
    )
    p = imza.parameters["install_mode"]
    assert set(typing.get_args(p.annotation)) == {"local", "remote"}, (
        f"install_mode yalnizca local|remote kabul etmeli: {p.annotation}"
    )
    assert getattr(p.default, "default", None) == "remote", (
        "varsayilan `remote` olmali (mevcut uzak kurulum akisi degismesin)"
    )


def test_yerel_kurulum_yolu_ajan_sinirini_genisletmedi():
    """Ajan compose'u KENDI sablonundan uretir; mod disardan gelmez.

    `install_mode`in ajan parametrelerine sizmasi, "ajan disardan compose
    kabul etmez" guvenlik sinirinin ilk catlagi olurdu.
    """
    import importlib.util
    from pathlib import Path

    yol = Path(__file__).resolve().parents[3] / "infra/appliance/e1-gwd.py"
    spec = importlib.util.spec_from_file_location("e1gwd_mod", yol)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "install_mode" not in mod.ALLOWED_PARAM_KEYS
    assert "INSTALL_MODE" not in mod.UPDATE_PARAM_KEYS
    # Ajan sablonu modu SABIT `local` yazar -- guncelleme turunda da korunur.
    assert 'INSTALL_MODE: "local"' in mod.COMPOSE_TEMPLATE
