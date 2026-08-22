"""GATEWAY DAGITIMI DETERMINISTIK — `:latest` uretim kararindan cikti.

YASANAN RISK
------------
Uretim dagitimi `ghcr.io/.../enerjione-grid-dnp3-gateway:latest` uzerine
kuruluydu ve compose sablonu `pull_policy: always` tasiyor. Ikisi birlikte
sunu uretiyordu:

    Grid AYNI SURUMDE KALSA BILE, kayit defterinde `latest` baska bir
    release'e tasindiginda container'in yeniden olusturuldugu HER an
    (yeniden kurulum, `docker compose up`, cihaz degisimi) operator ONAYI
    OLMADAN farkli bir gateway kodu calisiyordu.

Bu kuram degil: 2026-08-11'de `:latest` 1.6.2'ye tasinmisken ekran "Surum
bilinmiyor" diyordu (bkz. `gateway_release_service` modul basligi).

NE DEGISMEDI
------------
`:latest`i kaldirmak gateway'i kilitlemek DEGILDIR. Yeni surumler yine
gorulur ve operator guncelleyebilir; kalkan sey OTOMATIK surum kaymasi.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services import gateway_release_policy as pol
from app.services.gateway_compose import (
    DEFAULT_GATEWAY_IMAGE,
    ComposeRenderInput,
    render_compose,
    render_env,
)

KOK = pathlib.Path(__file__).resolve().parents[3]

TEMEL = dict(
    code="GW-1",
    token="t" * 24,
    name="Saha 1",
    backend_url="https://grid.example.com/api/v1",
    nats_url="nats://nats:4222",
)


# ===========================================================================
# A) URETIM KURULUMUNDA `:latest` YOK
# ===========================================================================


def test_A_uretim_compose_LATEST_ICERMEZ():
    metin = render_compose(ComposeRenderInput(**TEMEL))
    assert ":latest" not in metin, "uretim compose'unda `:latest` duruyor"


def test_A2_uretim_env_LATEST_ICERMEZ():
    assert ":latest" not in render_env(ComposeRenderInput(**TEMEL))


def test_A3_varsayilan_imaj_uretim_referansi():
    assert pol.is_production_ref(DEFAULT_GATEWAY_IMAGE)
    assert not DEFAULT_GATEWAY_IMAGE.endswith(":latest")


# ===========================================================================
# B) TEK KAYNAK — ayni deger uc sabite kopyalanmaz
# ===========================================================================


def test_B_surum_TEK_KAYNAKTAN_gelir():
    """API katmani kendi imaj sabitini TUTMAZ.

    Eskiden bir takma ad (`_DEFAULT_GATEWAY_IMAGE`) vardi ve bu test onun
    tek kaynakla ayni kaldigini dogruluyordu. Takma ad kaldirildi: API
    artik varsayilan imaji ISTEK ANINDA `production_image_ref` ile cozuyor
    (etiket degil, digest). Guvence yer degistirdi, KALKMADI — kontrol
    edilen sey artik "kopya ayni mi" degil, "kopya hic yok mu".
    """
    metin = (KOK / "apps/backend-api/app/api/gateways.py").read_text(encoding="utf-8")
    assert pol.GATEWAY_IMAGE_REPO not in metin, (
        "API katmani kendi literal imaj kopyasina donmus"
    )
    assert "production_image_ref" in metin, (
        "API katmani varsayilan imaji tek kaynaktan cozmuyor"
    )
    assert DEFAULT_GATEWAY_IMAGE == pol.approved_image_tag()


def test_B2_literal_latest_kopyasi_KALMADI():
    """Uretim kod yollarinda `dnp3-gateway:latest` literali olmamali."""
    hedefler = [
        "app/services/gateway_compose.py",
        "app/api/gateways.py",
        "app/services/gateway_agent_service.py",
        "app/services/gateway_update_service.py",
    ]
    for yol in hedefler:
        metin = (KOK / "apps/backend-api" / yol).read_text(encoding="utf-8")
        # Yorum satirlari HARIC: gerekce metinlerinde etiket adi gecebilir.
        kod = "\n".join(
            s for s in metin.splitlines() if not s.strip().startswith("#")
        )
        assert "dnp3-gateway:latest" not in kod, f"{yol}: literal `:latest` duruyor"


# ===========================================================================
# C/D) UYUMLULUK — fail-closed
# ===========================================================================


def test_C_uyumlu_hedef_GECER():
    uygun, _ = pol.uyumlu_mu("1.15.1", "2.109.1")
    assert uygun is True


def test_D_grid_ESKIYSE_bloke():
    uygun, gerekce = pol.uyumlu_mu("1.15.1", "2.108.0")
    assert uygun is False
    assert "2.109.0" in gerekce and "Grid" in gerekce


def test_D2_BILINMEYEN_surum_bloke_FAIL_CLOSED():
    """Gateway imaji kendi Grid gereksinimini ILAN ETMIYOR.

    Tabloda olmayan bir surumu "muhtemelen uyumludur" saymak FAIL-OPEN
    olurdu: 1.17.0 Grid 2.112 isteseydi ve biz 2.109'da olsaydik, sessizce
    kurup sahayi bozardik.
    """
    # BILINEN EN YUKSEKTEN YENI olanlar bloke.
    for surum in ("1.16.0", "1.17.0", "9.9.9"):
        uygun, gerekce = pol.uyumlu_mu(surum, "2.109.1")
        assert uygun is False, f"{surum} fail-open gecti"
        assert gerekce, "gerekce bos"
    # Okunamayan surum de bloke.
    for surum in (None, "", "bozuk"):
        assert pol.uyumlu_mu(surum, "2.109.1")[0] is False


def test_D4_ESKI_gateway_surumleri_GECER():
    """Eski bir gateway YENI Grid gerektirmez; Grid onlari bilerek
    destekliyor. Bloke etmek geri alma ve eski kurulum yollarini kapatirdi —
    koruma degil ENGEL olurdu."""
    for surum in ("1.14.0", "1.12.0", "1.6.2"):
        uygun, gerekce = pol.uyumlu_mu(surum, "2.109.1")
        assert uygun is True, f"{surum} bosuna bloke edildi: {gerekce}"


def test_D3_grid_surumu_okunamazsa_bloke():
    uygun, gerekce = pol.uyumlu_mu("1.15.1", None)
    assert uygun is False
    assert "Grid surumu" in gerekce


# ===========================================================================
# E) DIGEST COZULEMEZSE CALISAN GATEWAY'E DOKUNULMAZ
# ===========================================================================


def test_E_prepare_digest_cozulemezse_FAIL_CLOSED(monkeypatch):
    """Kayit defterine ulasilamiyorsa guncelleme BASLAMAZ.

    Alternatif ("etiketle gonderelim") onaylanan ile kurulanin ayrismasina
    kapi acardi — bu isin butun konusu o.
    """
    from app.services import gateway_release_service as rel
    from app.services import gateway_update_service as upd

    monkeypatch.setattr(
        upd, "_local",
        lambda kod: type("L", (), {
            "tracked_image": pol.approved_image_tag(),
            "image_digest": "sha256:" + "a" * 64,
            "local_version": "1.15.1",
        })(),
    )
    monkeypatch.setattr(
        rel, "fetch",
        lambda ref: rel.RegistryImage(error="registry unreachable"),
    )
    # DB'ye ihtiyac yok: kayit satiri sahte, cunku sinanan sey kayit
    # defteri cagrisinin FAIL-CLOSED olmasi.
    satir = type("R", (), {"status": "idle"})()
    monkeypatch.setattr(upd, "_row_or_new", lambda db, kod: satir)
    with pytest.raises(upd.GatewayUpdateError) as exc:
        upd.prepare(None, "GW-1", "tester")  # type: ignore[arg-type]
    assert exc.value.code == "registry_unreachable"


def test_E2_LATEST_uretim_HEDEFI_olamaz():
    """Hareketli etiket hedef olamaz: onaylanan ile kurulan ayrisir."""
    from app.services import gateway_update_service as upd

    for etiket in ("latest", "main", "dev", "nightly", "edge", "sha-abc123"):
        assert upd.is_valid_update_target(etiket) is False, (
            f"{etiket} uretim hedefi sayildi"
        )
    assert upd.is_valid_update_target("1.15.1") is True


def test_E3_KANAL_ile_HEDEF_ayri_kavramlar():
    """Sahadaki HER kurulum `:latest` izliyor.

    Onlari "gelistirme kanali" saymak arayuzde surum bilgisini ve Guncelle
    butonunu kapatirdi — 2026-08-11'de yasanan "Surum bilinmiyor" ekraninin
    aynisi. Kurulumun `:latest` IZLEMESI sorun degil; onu HEDEF SECMEK sorun.
    """
    from app.services import gateway_update_service as upd

    assert upd.is_development_tag("latest") is False, (
        "`latest` gelistirme kanali sayildi — saha kurulumlari Guncelle "
        "butonunu kaybeder"
    )
    assert upd.is_valid_update_target("latest") is False
    # Gercek gelistirme etiketleri her iki testte de dusmeli.
    for etiket in ("main", "sha-abc123"):
        assert upd.is_development_tag(etiket) is True
        assert upd.is_valid_update_target(etiket) is False


# ===========================================================================
# F/G) DEGISMEZ REFERANS — dagitim ve geri alma
# ===========================================================================


def test_F_pin_digeste_sabitler():
    d = "sha256:" + "b" * 64
    assert pol.pin("repo:1.2.3", d) == f"repo:1.2.3@{d}"
    # Zaten sabitlenmis referans IKI KEZ sabitlenmez.
    assert pol.pin(f"repo:1.2.3@{d}", d) == f"repo:1.2.3@{d}"


def test_G_uretim_referansi_TANIMI():
    d = "sha256:" + "c" * 64
    assert pol.is_production_ref(f"repo/x@{d}") is True
    assert pol.is_production_ref("repo/x:1.15.1") is True
    for kotu in ("repo/x:latest", "repo/x:main", "repo/x:dev", "repo/x", "", None):
        assert pol.is_production_ref(kotu) is False, f"{kotu} uretim sayildi"


# ===========================================================================
# H) `latest` TASINSA BILE calisan referans DEGISMEZ
# ===========================================================================


def test_H_latest_tasinsa_bile_varsayilan_KAYMAZ():
    """Varsayilan referans bir SURUME baglidir; kayit defterindeki `latest`
    etiketi nereye giderse gitsin bu deger degismez."""
    once = pol.approved_image_tag()
    # Kayit defteri degisimini taklit etmek gerekmiyor: deger hicbir ag
    # cagrisina bagli DEGIL.
    assert pol.approved_image_tag() == once
    assert pol.APPROVED_GATEWAY_VERSION in once


# ===========================================================================
# I/J) DURUM VE ILGISIZ AYARLAR KORUNUR
# ===========================================================================


def test_I_durum_volume_KORUNUR():
    """Imaj referansi degisti; kalici veri baglantisi degismedi."""
    metin = render_compose(ComposeRenderInput(**TEMEL))
    assert "volumes:" in metin


def test_J_ilgisiz_env_KAYBOLMADI():
    """Imaj degisikligi DNP3/komut ayarlarini dusurmemeli."""
    metin = render_compose(ComposeRenderInput(**TEMEL))
    for anahtar in (
        "DNP3_TIME_SYNC",
        "DEVICE_HEALTH_PUBLISH_ENABLED",
        "DNP3_TCP_PORT",
        "GATEWAY_CODE",
    ):
        assert anahtar in metin, f"{anahtar} compose'dan dusmus"


# ===========================================================================
# K) YEREL VE UZAK KURULUM AYNI POLITIKAYI IZLER
# ===========================================================================


def test_K_ajan_ve_backend_AYNI_varsayilani_kullanir():
    from app.services import gateway_agent_service as ajan

    kaynak = pathlib.Path(ajan.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_GATEWAY_IMAGE" in kaynak, (
        "ajan kendi imaj varsayilanina donmus"
    )
    assert "dnp3-gateway:latest" not in kaynak


def test_K2_appliance_sablonunda_LATEST_YOK():
    metin = (KOK / "infra/appliance/e1-gwd.py").read_text(encoding="utf-8")
    kod = "\n".join(s for s in metin.splitlines() if not s.strip().startswith("#"))
    assert "dnp3-gateway:latest" not in kod, "appliance sablonunda `:latest` duruyor"


# ===========================================================================
# L) SURUM TABLOSU TUTARLI
# ===========================================================================


def test_L_onayli_surum_tabloda_VAR():
    """Onayli surumun Grid gereksinimi bilinmiyorsa kendi kurulumumuz da
    uyumluluk kapisindan gecemezdi."""
    assert pol.min_grid_for(pol.APPROVED_GATEWAY_VERSION) is not None
    uygun, _ = pol.uyumlu_mu(pol.APPROVED_GATEWAY_VERSION, _grid_surumu())


def _grid_surumu() -> str:
    return (KOK / "VERSION").read_text(encoding="utf-8").strip()


def test_L2_onayli_surum_BU_GRID_ile_uyumlu():
    uygun, gerekce = pol.uyumlu_mu(pol.APPROVED_GATEWAY_VERSION, _grid_surumu())
    assert uygun is True, (
        f"onayli gateway surumu bu Grid ile uyumsuz: {gerekce}"
    )


def test_L3_latest_uretim_hedefi_kumesinde():
    assert "latest" in pol.NON_PRODUCTION_TARGET_TAGS
    # Ama GELISTIRME kanali degil (bkz. test_E3).
    assert "latest" not in pol.DEV_TAGS
