"""YENI KURULUM DEGISMEZ IMAJA SABITLENIR — VEYA HIC YAPILMAZ.

NEDEN AYRI DOSYA
----------------
Onceki surumde kurulum, kayit defterine ulasilamadiginda etiketli
`:1.15.1`e duser ve UYARI loglardi. Gerekce "yeni kurulumda korunacak
calisan bir gateway yok" idi. Yanlis olan sey su: korunmasi gereken
CONTAINER degil, HANGI KODUN KURULDUGU BILGISI.

Etiket kayit defterinde tasinabilir bir isaretcidir. Ayni etiketle iki
sahaya iki farkli gateway kurulmus olabilir ve ne compose dosyasinda ne
loglarda bunu ayirt edecek bir sey kalir. Bir kurulumun BASARISIZ olmasi,
sessizce YANLIS olmasindan iyidir.

Buradaki testler o davranisin geri gelmesini engeller.
"""

from __future__ import annotations

import inspect

import pytest

from app.api import gateways
from app.services import gateway_release_policy as pol
from app.services import gateway_release_service


class _Yanit:
    """`gateway_release_service.fetch` donusunun testlik ikizi."""

    def __init__(self, digest: str | None = None, error: str | None = None) -> None:
        self.digest = digest
        self.error = error


@pytest.fixture()
def kayit_defteri_yok(monkeypatch):
    """Kayit defteri ERISILEMEZ — kurulum aninda GHCR down senaryosu."""

    def _patla(*_a, **_k):
        raise OSError("ghcr.io erisilemiyor")

    monkeypatch.setattr(gateway_release_service, "fetch", _patla)


# ===========================================================================
# A — SABIT PIN: AG OLMADAN DEGISMEZ REFERANS
# ===========================================================================


def test_A_kayit_defteri_ERISILEMEZKEN_de_digest_uretilir(kayit_defteri_yok):
    """Sabit pin agdan BAGIMSIZ: GHCR down iken bile kurulum yapilabilir."""
    ref, digest = pol.production_image_ref()
    assert "@sha256:" in ref, "degismez referans uretilmedi"
    assert digest == pol.APPROVED_GATEWAY_DIGESTS[pol.APPROVED_GATEWAY_VERSION]


def test_A_onayli_surumun_pini_KAYITLI():
    """Surum yukseltilip digest yazilmazsa kurulum fail-closed olur; bu
    testin amaci o durumu SESSIZ birakmamak."""
    assert pol.APPROVED_GATEWAY_VERSION in pol.APPROVED_GATEWAY_DIGESTS, (
        f"{pol.APPROVED_GATEWAY_VERSION} icin APPROVED_GATEWAY_DIGESTS girdisi "
        "yok — kurulum kayit defterine bagimli hale gelir"
    )


def test_A_pin_bicimi_MANIFEST_digesti():
    for surum, digest in pol.APPROVED_GATEWAY_DIGESTS.items():
        assert digest.startswith("sha256:"), f"{surum}: digest bicimi yanlis"
        # sha256 = 64 onaltilik karakter; kisasi kisaltilmis/bozuk demektir.
        assert len(digest) == 71, f"{surum}: digest uzunlugu {len(digest)}"
        int(digest.split(":", 1)[1], 16)  # onaltilik degilse ValueError


def test_A_referans_HEM_etiket_HEM_digest_tasir():
    """Etiket okunabilirlik icin kalir; CALISTIRILAN seyi digest belirler."""
    ref, digest = pol.production_image_ref()
    taban, _, sabitleme = ref.partition("@")
    assert taban == pol.approved_image_tag()
    assert sabitleme == digest


# ===========================================================================
# B — FAIL-CLOSED: PIN YOK + KAYIT DEFTERI YOK
# ===========================================================================


def test_B_pin_yok_ve_kayit_defteri_yok_ISE_HATA(monkeypatch, kayit_defteri_yok):
    monkeypatch.setattr(pol, "APPROVED_GATEWAY_DIGESTS", {})
    with pytest.raises(pol.DigestCozulemedi):
        pol.production_image_ref()


def test_B_kayit_defteri_digest_DONMEZSE_HATA(monkeypatch):
    monkeypatch.setattr(pol, "APPROVED_GATEWAY_DIGESTS", {})
    monkeypatch.setattr(
        gateway_release_service,
        "fetch",
        lambda *_a, **_k: _Yanit(digest=None, error="manifest okunamadi"),
    )
    with pytest.raises(pol.DigestCozulemedi):
        pol.production_image_ref()


def test_B_hata_mesaji_NE_YAPILACAGINI_soyler(monkeypatch, kayit_defteri_yok):
    """Operator "digest cozulemedi" ile bas basa kalmamali."""
    monkeypatch.setattr(pol, "APPROVED_GATEWAY_DIGESTS", {})
    with pytest.raises(pol.DigestCozulemedi) as hata:
        pol.production_image_ref()
    mesaj = str(hata.value)
    assert "APPROVED_GATEWAY_DIGESTS" in mesaj
    assert pol.approved_image_tag() in mesaj


def test_B_ASLA_etiketli_referansa_dusmez(monkeypatch, kayit_defteri_yok):
    """ANA KABUL OLCUTU: sessiz fallback yok."""
    monkeypatch.setattr(pol, "APPROVED_GATEWAY_DIGESTS", {})
    try:
        ref, _ = pol.production_image_ref()
    except pol.DigestCozulemedi:
        return  # dogru davranis
    pytest.fail(f"degisebilir etikete dusuldu: {ref}")


def test_B_digest_cozumunu_KAPATAN_kacis_kalmadi():
    """Donus tipi `str | None` degil `str`: digest artik opsiyonel degil."""
    imza = inspect.signature(pol.production_image_ref)
    assert str(imza.return_annotation).replace(" ", "") == "tuple[str,str]"
    assert "resolve_digest" not in inspect.getsource(pol.production_image_ref)


# ===========================================================================
# C — ETIKET TASINIRSA ONAYLI DIGEST KAZANIR
# ===========================================================================


def test_C_kayit_defteri_BASKA_digest_derse_ONAYLI_kullanilir(monkeypatch):
    """Etiketin release sonrasi tasinmasi kurulumu baska bir artefakta
    kaydirmamali — engellemek istedigimiz sey tam olarak bu."""
    monkeypatch.setattr(
        gateway_release_service,
        "fetch",
        lambda *_a, **_k: _Yanit(digest="sha256:" + "0" * 64),
    )
    ref, digest = pol.production_image_ref()
    assert digest == pol.APPROVED_GATEWAY_DIGESTS[pol.APPROVED_GATEWAY_VERSION]
    assert "0" * 64 not in ref


def test_C_tasima_LOGLANIR(monkeypatch, caplog):
    monkeypatch.setattr(
        gateway_release_service,
        "fetch",
        lambda *_a, **_k: _Yanit(digest="sha256:" + "0" * 64),
    )
    with caplog.at_level("WARNING"):
        pol.production_image_ref()
    assert any("TASINMIS" in k.message for k in caplog.records), (
        "etiket tasinmis ama sessiz kalindi"
    )


def test_C_kayit_defteri_dogrulamasi_KURULUMU_ENGELLEMEZ(kayit_defteri_yok):
    """Dogrulama iyimserdir: sabit pin elimizdeyken ag hatasi kurulumu
    dusurmemeli."""
    ref, _ = pol.production_image_ref()
    assert "@sha256:" in ref


# ===========================================================================
# D — GELISTIRME KACISI URETIMI ETKILEMEZ
# ===========================================================================


def test_D_acik_imaj_verilirse_dokunulmaz():
    """Ozel kayit defteri / gelistirme kacisi: sorumluluk cagiranda."""
    kaynak = inspect.getsource(gateways.install_gateway_locally)
    assert "acik_imaj" in kaynak
    # Acik imaj verildiginde digest cozumu CALISMAZ.
    bas = kaynak.index("if acik_imaj:")
    dal = kaynak[bas : kaynak.index("else:", bas)]
    assert "production_image_ref" not in dal


def test_D_kacis_uretim_varsayilanini_DEGISTIRMEZ():
    """`DEV_TAGS`/`latest` uretim referansina sizmamali."""
    ref, _ = pol.production_image_ref()
    etiket = ref.partition("@")[0].rsplit(":", 1)[1]
    assert etiket == pol.APPROVED_GATEWAY_VERSION
    assert etiket not in pol.NON_PRODUCTION_TARGET_TAGS


def test_D_uretim_referansi_uretim_sayilir():
    ref, _ = pol.production_image_ref()
    assert pol.is_production_ref(ref)


# ===========================================================================
# UCLAR — SESSIZ KURULUM YOK
# ===========================================================================


def test_kurulum_UCU_digest_yoksa_503_doner():
    """Kurulum reddedilir ve sebebi soylenir."""
    kaynak = inspect.getsource(gateways.install_gateway_locally)
    assert "DigestCozulemedi" in kaynak, "fail-closed yakalanmiyor"
    assert "HTTP_503_SERVICE_UNAVAILABLE" in kaynak


def test_compose_INDIRME_ucu_de_digest_sabitler():
    """Indirilen compose da bir kurulumdur: dosyanin uretildigi an ile
    calistirildigi an arasinda etiket tasinabilir."""
    kaynak = inspect.getsource(gateways.download_gateway_compose)
    assert "production_image_ref" in kaynak
    assert "DigestCozulemedi" in kaynak
    assert "HTTP_503_SERVICE_UNAVAILABLE" in kaynak
