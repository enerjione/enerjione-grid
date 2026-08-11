"""Gateway UZAK SURUM kesfi — cihazdaki `docker buildx`e bagli olmadan.

SAHA BULGUSU (2026-08-11)
------------------------
Gateway 1.6.2 yayinlandi, `:latest` ona tasindi. Cihazda buildx kurulu
olmadigi icin ajan uzak digest'i sorgulayamiyor, `remote_digest` ve
`remote_version` bos donuyordu. Sonuc:

  * ekranda "Surum bilinmiyor" — HEDEF surum hic gorunmuyor,
  * `update_available` kalici olarak None, yani bildirim de gitmiyor.

Yeni surum kayit defterinde dururken operator uygulama icinden
guncelleyemiyordu. Bu testler zenginlestirmenin kurallarini kilitler.
"""

from __future__ import annotations

import pytest

from app.schemas.gateway_agent import GatewayAgentStatus, LocalGateway
from app.services import gateway_release_service as grs


@pytest.fixture(autouse=True)
def temiz_onbellek():
    grs.clear_cache()
    yield
    grs.clear_cache()


# ---------------------------------------------------------------------------
# Referans ayristirma
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ref, beklenen",
    [
        (
            "ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest",
            ("ghcr.io", "enerjione/enerjione-grid-dnp3-gateway", "latest"),
        ),
        # Etiket yoksa `latest` varsayilir (docker ile ayni kural).
        (
            "ghcr.io/enerjione/x",
            ("ghcr.io", "enerjione/x", "latest"),
        ),
        # Digest'e sabitlenmis referansta digest ATILIR: takip edilen etikettir.
        (
            "ghcr.io/enerjione/x:1.6.2@sha256:" + "a" * 64,
            ("ghcr.io", "enerjione/x", "1.6.2"),
        ),
        # Host portu etiket SANILMAMALI.
        ("localhost:5000/e1/gw:1.0", ("localhost:5000", "e1/gw", "1.0")),
        # Host yoksa Docker Hub; tek parcali ad `library/` alir.
        ("nginx:1.27", ("registry-1.docker.io", "library/nginx", "1.27")),
        ("", None),
        (None, None),
    ],
)
def test_referans_ayristirma(ref, beklenen):
    assert grs.parse_image_ref(ref) == beklenen


# ---------------------------------------------------------------------------
# Zenginlestirme kurallari
# ---------------------------------------------------------------------------
def _durum(**kw) -> GatewayAgentStatus:
    gw = LocalGateway(
        code=kw.pop("code", "GW1"),
        tracked_image=kw.pop("tracked_image", "ghcr.io/enerjione/gw:latest"),
        **kw,
    )
    return GatewayAgentStatus(available=True, docker_available=True, gateways=[gw])


def test_ajan_cevaplayabildiyse_kayit_defterine_SORULMAZ(monkeypatch):
    """Cihazin gordugu deger gerceklige en yakin olandir; ezilmemeli.

    Ozel kayit defteri / ayna / cevrimdisi kopya kullanan bir sahada backend'in
    gordugu ile cihazin cekebilecegi FARKLI olabilir.
    """
    cagrildi = False

    def sahte(_ref):
        nonlocal cagrildi
        cagrildi = True
        return grs.RegistryImage(version="9.9.9", digest="sha256:yeni")

    monkeypatch.setattr(grs, "fetch", sahte)
    durum = _durum(
        image_digest="sha256:eski",
        remote_digest="sha256:ajan",
        remote_version="1.6.1",
        update_available=True,
    )
    sonuc = grs.enrich_agent_status(durum)

    assert cagrildi is False
    assert sonuc.gateways[0].remote_version == "1.6.1"
    assert sonuc.gateways[0].remote_source == "agent"


def test_buildx_yoksa_surum_KAYIT_DEFTERINDEN_gelir(monkeypatch):
    monkeypatch.setattr(
        grs, "fetch", lambda _ref: grs.RegistryImage(version="1.6.2", digest="sha256:yeni")
    )
    durum = _durum(image_digest="sha256:eski")  # ajan uzak tarafi bilemedi
    grs.enrich_agent_status(durum)  # ilk tur: arka plan sorgusunu baslatir
    grs.enrich_agent_status(durum)  # ikinci tur: onbellekten okur

    gw = durum.gateways[0]
    assert gw.remote_version == "1.6.2"
    assert gw.remote_digest == "sha256:yeni"
    assert gw.remote_source == "registry"
    # Karar HALA digest karsilastirmasi.
    assert gw.update_available is True


def test_yerel_digest_bilinmiyorsa_GUNCEL_denmez(monkeypatch):
    """Elle kurulmus imajda RepoDigests olmaz; karsilastirilacak sey yoktur.

    Bos yerel digest ile "guncel" demek, sormadan verilmis bir iddia olurdu.
    """
    monkeypatch.setattr(
        grs, "fetch", lambda _ref: grs.RegistryImage(version="1.6.2", digest="sha256:yeni")
    )
    durum = _durum(image_digest=None)
    grs.enrich_agent_status(durum)
    grs.enrich_agent_status(durum)

    gw = durum.gateways[0]
    assert gw.update_available is None
    # Surum bilgisi yine de gosterilir: "kayit defterinde 1.6.2 var" degerli.
    assert gw.remote_version == "1.6.2"


def test_kayit_defteri_de_cevapsizsa_SEBEP_yazilir(monkeypatch):
    monkeypatch.setattr(
        grs,
        "fetch",
        lambda _ref: grs.RegistryImage(error="Kayit defteri yetki istiyor"),
    )
    durum = _durum(image_digest="sha256:eski")
    grs.enrich_agent_status(durum)
    grs.enrich_agent_status(durum)

    gw = durum.gateways[0]
    assert gw.update_available is None
    assert gw.remote_error == "Kayit defteri yetki istiyor"
    assert gw.remote_source is None


def test_sorgu_ISTEK_ICINDE_beklemez(monkeypatch):
    """Zenginlestirme aga BLOKLAMAZ: ilk tur `pending` der, deger sonra gelir.

    Arayuz guncelleme sirasinda ajan durumunu saniyede bir yokluyor; istek
    icinde uc HTTP cagrisi beklemek ekrani kilitlerdi.
    """
    import threading

    kapi = threading.Event()

    def yavas(_ref):
        kapi.wait(5.0)
        return grs.RegistryImage(version="1.6.2", digest="sha256:yeni")

    monkeypatch.setattr(grs, "fetch", yavas)
    durum = _durum(image_digest="sha256:eski")
    grs.enrich_agent_status(durum)  # donmesi ANINDA olmali

    gw = durum.gateways[0]
    assert gw.remote_pending is True
    assert gw.remote_version is None
    kapi.set()


def test_takip_edilen_imaj_yoksa_dokunulmaz():
    durum = _durum(tracked_image=None, image=None)
    grs.enrich_agent_status(durum)
    gw = durum.gateways[0]
    assert gw.remote_source is None and gw.remote_pending is False
