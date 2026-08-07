"""`update` aksiyonu compose'u guncel NATS URL'i ile yeniden uretebilmeli.

NEDEN
-----
Telemetrinin STANDART rotasi gateway -> NATS JetStream. NATS oncesi kurulan
(veya anonim NATS URL'li) gateway'ler HTTP fallback'ine duser: her olcum
backend HTTP ingest -> Postgres outbox -> NATS zincirinden gecer ve yuk
backend'e biner (sahada olculdu: 100 cihazlik yuk testinde outbox drain
3.250 msj/sn basarken persist 1.100 msj/sn isleyebiliyordu — backlog
kendiliginden erimez). `update` istegi opsiyonel `params` tasir; ajan mevcut
compose'daki degerleri KORUYUP yalnizca NATS_URL'i degistirir. Boylece
paneldeki "Guncelle" butonu eski kurulumlari NATS'a gecirir.

Kullanicinin kurulumda sectigi imaj/port/URL degerleri sessizce varsayilana
DONMEMELI — bu yuzden gidis-donus (render -> parse -> render) kayipsizligi
ayrica test ediliyor.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_AJAN_YOL = Path(__file__).resolve().parents[1] / "e1-gwd.py"
_spec = importlib.util.spec_from_file_location("e1_gwd_ut", _AJAN_YOL)
assert _spec is not None and _spec.loader is not None
gwd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gwd)

ORNEK_PARAMS = {
    "image": "ghcr.io/enerjione/dnp3-gateway:latest",
    "token": "abcdefghijklmnop1234",
    "backend_url": "http://host.docker.internal/api/v1",
    "nats_url": "nats://gateway:eski-parola@host.docker.internal:4222",
    "host_port": 8021,
    "app_environment": "production",
    "initiating_port_base": 21100,
    "initiating_port_count": 8,
    "publish_dnp3_quality": True,
}

YENI_NATS_URL = "nats://gateway:yeni-parola@host.docker.internal:4222"


def _compose_yaz(tmp_path: Path, params: dict, ad: str = "Saha A") -> Path:
    body = gwd.render_compose("GW-7", ad, gwd._validate_params(dict(params)))
    yol = tmp_path / "docker-compose.yml"
    yol.write_text(body, encoding="utf-8")
    return yol


# ---------------------------------------------------------------------------
# _validate_update_params: yalnizca izinli anahtarlar
# ---------------------------------------------------------------------------

def test_update_params_nats_url_kabul_ediyor():
    out = gwd._validate_update_params({"nats_url": YENI_NATS_URL})
    assert out == {"nats_url": YENI_NATS_URL}


def test_update_params_image_KABUL_EDER():
    """`image` 2026-08-07'de BILINCLI olarak kabul listesine alindi.

    Oncesinde ajan guncellemede compose'daki mevcut etiketi geri kazanip
    aynen yaziyordu: compose'a bir kez sabit etiket (`:1.5.0`) yazildiysa
    "Guncelle" butonu onu BIR DAHA degistiremiyordu. Sahada GW-001 boyle
    kilitlendi, ekran kalici "Guncel" dedi ve 1.6.x hic gorunmedi.
    """
    temiz = gwd._validate_update_params({"image": "ghcr.io/x/y:latest"})
    assert temiz["image"] == "ghcr.io/x/y:latest"


def test_update_params_diger_anahtarlari_reddediyor():
    """update, install degildir: token gibi alanlar bu yoldan degismemeli."""
    with pytest.raises(ValueError):
        gwd._validate_update_params({"nats_url": YENI_NATS_URL, "token": "x" * 20})


def test_update_params_sozluk_olmali():
    with pytest.raises(ValueError):
        gwd._validate_update_params("nats://h:4222")


def test_update_params_gecersiz_url_reddediyor():
    """Deger cifte tirnakli YAML skalerine giriyor; tirnak/bosluk sizarsa
    compose'a alan enjekte edilebilirdi — install ile AYNI regex'ten gecmeli."""
    for kotu in ('nats://h:4222"\n  privileged: true', "http://h:4222", "nats://h :4222"):
        with pytest.raises(ValueError):
            gwd._validate_update_params({"nats_url": kotu})


# ---------------------------------------------------------------------------
# _params_from_compose: gidis-donus kayipsiz
# ---------------------------------------------------------------------------

def test_compose_gidis_donus_kayipsiz(tmp_path):
    yol = _compose_yaz(tmp_path, ORNEK_PARAMS)
    params, ad = gwd._params_from_compose(str(yol))
    assert ad == "Saha A"
    # Ayni dogrulamadan gecirilince birebir ayni degerler cikmali — aksi
    # halde update, kullanicinin kurulum secimlerini sessizce degistirir.
    assert gwd._validate_params(params) == gwd._validate_params(dict(ORNEK_PARAMS))


def test_initiating_port_yokken_de_okunuyor(tmp_path):
    sade = dict(ORNEK_PARAMS, initiating_port_count=0)
    yol = _compose_yaz(tmp_path, sade)
    params, _ad = gwd._params_from_compose(str(yol))
    assert params["initiating_port_count"] == 0


def test_elle_bozulmus_compose_net_hata_veriyor(tmp_path):
    yol = tmp_path / "docker-compose.yml"
    yol.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        gwd._params_from_compose(str(yol))


# ---------------------------------------------------------------------------
# Overlay: eski kurulum NATS'a geciyor
# ---------------------------------------------------------------------------

def test_nats_url_overlay_eski_kurulumu_nats_a_geciriyor(tmp_path):
    """NATS oncesi sablonda NATS_URL satiri hic yok. Parse bos dondurur,
    overlay doldurur ve yeniden uretilen compose yeni URL'i tasir."""
    yol = _compose_yaz(tmp_path, ORNEK_PARAMS)
    govde = yol.read_text(encoding="utf-8")
    govde = re.sub(r'^\s+NATS_URL: "[^"]*"\n', "", govde, flags=re.MULTILINE)
    assert 'NATS_URL:' not in govde
    yol.write_text(govde, encoding="utf-8")

    params, ad = gwd._params_from_compose(str(yol))
    assert params["nats_url"] == ""

    params.update(gwd._validate_update_params({"nats_url": YENI_NATS_URL}))
    yeni_govde = gwd.render_compose("GW-7", ad, gwd._validate_params(params))
    assert f'NATS_URL: "{YENI_NATS_URL}"' in yeni_govde
    # Kalan degerler korunmus olmali.
    assert 'GATEWAY_TOKEN: "abcdefghijklmnop1234"' in yeni_govde
    assert '127.0.0.1:8021:8020' in yeni_govde


# ---------------------------------------------------------------------------
# _validate: update istegi params'i tasiyor (opsiyonel)
# ---------------------------------------------------------------------------

def test_update_istegi_params_ile_dogrulaniyor():
    istek = {
        "action": "update",
        "code": "GW-7",
        "name": "Saha A",
        "params": {"nats_url": YENI_NATS_URL},
    }
    clean = gwd._validate(istek)
    assert clean["params"] == {"nats_url": YENI_NATS_URL}


def test_update_istegi_params_siz_eski_davranis():
    """Eski backend params gondermez; update yalnizca imaj ceker."""
    clean = gwd._validate({"action": "update", "code": "GW-7", "name": ""})
    assert "params" not in clean
