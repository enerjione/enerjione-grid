"""DNP3 kalite bayragi anahtari — gateway ayarindan compose'a kadar.

NEDEN VAR
---------
Gateway kapaliyken her okumayi `quality: "good"` yayinliyor. Outstation bir
noktayi `REFERENCE_ERR` ile raporladiginda (CT referansi kaybolmus,
`value=0.0`) bu bilgi SCADA'ya GECERLI OLCUM olarak gidiyor: hat akimi 0 A
sanilir. Backend bu kaliteleri v2.28.0'dan beri taniyor; eksik olan tek sey
gateway'de bayragi acabilmekti.

NEDEN GATEWAY BAZINDA
---------------------
Acmak saha davranisini degistirir: kotu kaliteli okumalar alarm
degerlendirmesinden bloke olur (alarm durumu DONAR). Dogru davranis, ama tum
filoda birden acmak kontrolsuz olurdu.

BU TESTLERIN KORUDUGU SEY
-------------------------
Ayar arayuzde "acik" gorunup compose'a "false" gitmemeli. Aradaki zincir
uzun (model -> ComposeRenderInput -> sablon / ajan parametresi) ve sessizce
kirilabilir; kirildiginda kullanici ayari actigini SANIR.
"""

from __future__ import annotations

import pytest

from app.services.gateway_compose import ComposeRenderInput, render_compose, render_env


def _girdi(**kw) -> ComposeRenderInput:
    temel = dict(
        code="GW-1",
        token="a" * 24,
        name="Saha 1",
        backend_url="https://grid.example.com/api/v1",
        nats_url="nats://backend:pass@nats:4222",
    )
    temel.update(kw)
    return ComposeRenderInput(**temel)


def test_varsayilan_kapali() -> None:
    """Mevcut kurulumlarin davranisi degismemeli."""
    assert _girdi().publish_dnp3_quality is False
    assert 'GATEWAY_PUBLISH_DNP3_QUALITY: "false"' in render_compose(_girdi())


def test_acikken_composeya_true_gidiyor() -> None:
    govde = render_compose(_girdi(publish_dnp3_quality=True))
    assert 'GATEWAY_PUBLISH_DNP3_QUALITY: "true"' in govde


def test_env_dosyasi_da_ayni_degeri_tasiyor() -> None:
    """Indirilen `.env` ile compose ayrisirsa iki kurulum yolu farkli davranir."""
    assert "GATEWAY_PUBLISH_DNP3_QUALITY=true" in render_env(_girdi(publish_dnp3_quality=True))
    assert "GATEWAY_PUBLISH_DNP3_QUALITY=false" in render_env(_girdi())


#: Iki sablonun BILINCLI farklari (INSTALL_MODE, MAX_PARALLEL_DEVICES)
#: test_gateway_agent_compose ile AYNI kuraldan gelir — tek kaynak.
from tests.test_gateway_agent_compose import _parity_normalize  # noqa: E402


@pytest.mark.parametrize("acik", [True, False])
def test_ajan_ve_backend_ayni_composeyu_uretiyor(acik: bool) -> None:
    """Ajan sablonu backend'inkinden AYRISMAMIS olmali.

    Iki taraf ayrisirsa "bu cihaza kur" ile "dosyayi indir" akislari ayni
    gateway'i FARKLI davranisla kurar.
    """
    import importlib.util
    from pathlib import Path

    yol = Path(__file__).resolve().parents[3] / "infra" / "appliance" / "e1-gwd.py"
    spec = importlib.util.spec_from_file_location("e1gwd_kalite", yol)
    ajan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ajan)

    params = ajan._validate_params(
        {
            "image": "ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest",
            "token": "a" * 24,
            "backend_url": "https://grid.example.com/api/v1",
            "nats_url": "nats://backend:pass@nats:4222",
            "host_port": 8020,
            "app_environment": "production",
            "initiating_port_base": 20100,
            "initiating_port_count": 0,
            "publish_dnp3_quality": acik,
        }
    )
    ajandan = _parity_normalize(ajan.render_compose("GW-1", "Saha 1", params))
    backendden = _parity_normalize(render_compose(_girdi(publish_dnp3_quality=acik)))
    assert ajandan == backendden, "ajan ve backend compose sablonlari ayrismis"


def test_ajan_sacma_degeri_ACIK_saymaz() -> None:
    """Yanlis okunan bir ayar SESSIZCE ozellik ACMAMALI.

    Deger cifte tirnakli bir YAML skalerine giriyor; serbest metni oldugu gibi
    gecirmek YAML enjeksiyonu riskiydi. Yalnizca acikca "true" aciyor.
    """
    import importlib.util
    from pathlib import Path

    yol = Path(__file__).resolve().parents[3] / "infra" / "appliance" / "e1-gwd.py"
    spec = importlib.util.spec_from_file_location("e1gwd_kalite2", yol)
    ajan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ajan)

    temel = {
        "image": "ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest",
        "token": "a" * 24,
        "backend_url": "https://grid.example.com/api/v1",
        "nats_url": "nats://backend:pass@nats:4222",
        "host_port": 8020,
        "app_environment": "production",
        "initiating_port_base": 20100,
        "initiating_port_count": 0,
    }
    for sacma in ("evet", "1", "yes", 'true"\nprivileged: yes', "", None):
        p = ajan._validate_params({**temel, "publish_dnp3_quality": sacma})
        assert p["publish_dnp3_quality"] is False, f"{sacma!r} acik sayildi"

    # Acikca "true" (metin ya da bool) acar.
    assert ajan._validate_params({**temel, "publish_dnp3_quality": "true"})["publish_dnp3_quality"] is True
    assert ajan._validate_params({**temel, "publish_dnp3_quality": True})["publish_dnp3_quality"] is True
