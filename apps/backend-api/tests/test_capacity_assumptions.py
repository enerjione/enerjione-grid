"""Kapasite sabitleri HEDEF olcege gore hesaplanmali (Faz 2-16).

YASANAN SORUN
-------------
Koddaki kapasite yorumlari 25,9M satir/gun (200 cihaz) varsayimiyla
yazilmisti. HEDEF yuk ise 600 cihaz x 20 aktif sinyal / 10 sn =
~1.200 deger/sn = 103,68M satir/gun — yani DORT KAT fazla.

Somut sonuc: `nats_stream_raw_max_bytes` yorumunda "~19 saat tampon"
yaziyordu; gercekte hedef olcekte ~3,5 saat. Bu, tavanin yanlis oldugu
anlamina gelmez (8 GiB disk butcesi icinde makul bir pay), ama
"19 saatlik kesintiyi tolere ederiz" varsayimiyla planlama yapmak
YANLISTIR: 4 saati asan bir backend kesintisinde en eski telemetri KAYBOLUR.

Bu testler sayilari degil VARSAYIMLARI kilitler: olcek ya da tavan
degistiginde yorumun da guncellenmesi gerektigini PR aninda gosterir.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.core import config as cfg
from app.core.config import settings

# --- Hedef olcek (denetim raporundaki tanim) --------------------------------
HEDEF_CIHAZ = 600
AKTIF_SINYAL = 20
POLL_SANIYE = 10

# Ortalama JetStream mesaj boyutu (JSON zarf + payload). Kaba ama tutarli
# bir tahmin; tampon suresi buyuklugu MERTEBE olarak dogru olsun diye.
ORT_MESAJ_BAYT = 600


def _deger_hizi() -> float:
    """Saniyede uretilen telemetri degeri."""
    return HEDEF_CIHAZ * AKTIF_SINYAL / POLL_SANIYE


def _tampon_saat(max_bytes: int) -> float:
    return max_bytes / (_deger_hizi() * ORT_MESAJ_BAYT) / 3600.0


def test_hedef_hiz_gunluk_satir_sayisiyla_TUTARLI():
    """Denetimdeki 103,68M satir/gun rakami bu parametrelerden gelir."""
    gunluk = _deger_hizi() * 86400
    assert 100_000_000 < gunluk < 110_000_000, f"gunluk satir {gunluk:,.0f}"


def test_raw_tampon_suresi_YORUMLA_uyusuyor():
    """Yorumda yazan sure gercek hesapla ayni mertebede olmali.

    Eski yorum "~19 saat" diyordu ve gercek ~3,5 saatti; operator dort kat
    yanlis bir varsayimla planlama yapiyordu.
    """
    saat = _tampon_saat(settings.nats_stream_raw_max_bytes)
    kaynak = inspect.getsource(cfg)

    m = re.search(r"raw\s+8 GiB\s+->\s+~([\d,]+)\s+saat", kaynak)
    assert m, "config.py'de raw tampon suresi belgelenmemis"
    belgelenen = float(m.group(1).replace(",", "."))

    assert abs(belgelenen - saat) < 1.5, (
        f"belgelenen tampon {belgelenen} saat, hesaplanan {saat:.1f} saat — "
        "olcek ya da tavan degismis, yorum guncellenmemis"
    )


def test_normalized_tampon_suresi_YORUMLA_uyusuyor():
    saat = _tampon_saat(settings.nats_stream_normalized_max_bytes)
    kaynak = inspect.getsource(cfg)
    m = re.search(r"normalized\s+3 GiB\s+->\s+~([\d,]+)\s+saat", kaynak)
    assert m, "config.py'de normalized tampon suresi belgelenmemis"
    belgelenen = float(m.group(1).replace(",", "."))
    assert abs(belgelenen - saat) < 1.0, (
        f"belgelenen {belgelenen} saat, hesaplanan {saat:.1f} saat"
    )


def test_sabitin_KENDI_yorumu_dogru_sureyi_soyluyor():
    """Sabitin yanindaki yorum hesapla uyusmali.

    Bu tur bir yorum sayilardan daha tehlikelidir: kod dogru calisirken
    operatoru yanlis planlamaya goturur. Eskiden burada "~19 saat" yaziyordu.

    NOT: "kaynakta hicbir yerde '~19 saat' gecmesin" seklinde bir kontrol
    YAZILMADI — modulun kendi ACIKLAMASI eski iddiayi alintiladigi icin oyle
    bir test kendi belgelendirmemize takilirdi. Denetlenen sey, SABITIN
    bulundugu yerdeki beyandir.
    """
    satirlar = inspect.getsource(cfg).splitlines()
    idx = next(
        (i for i, s in enumerate(satirlar) if s.strip().startswith("nats_stream_raw_max_bytes")),
        None,
    )
    assert idx is not None, "nats_stream_raw_max_bytes tanimi bulunamadi"

    # YALNIZCA hemen ustteki TEK satir. Blogun tamamini yukari dogru toplamak
    # modul aciklamasindaki eski "~19 saat" alintisini da yakalardi ve test
    # kendi belgelendirmemize takilirdi (bu tuzaga bir kez dusuldu).
    blok = satirlar[idx - 1].strip()
    assert blok.startswith("#"), (
        "sabitin hemen ustunde aciklama yok — tampon suresi beyan edilmeli"
    )

    m = re.search(r"~?([\d,]+)\s*SAAT", blok, re.IGNORECASE)
    assert m, f"sabitin yaninda tampon suresi beyan edilmemis: {blok!r}"
    beyan = float(m.group(1).replace(",", "."))
    gercek = _tampon_saat(settings.nats_stream_raw_max_bytes)
    assert abs(beyan - gercek) < 1.5, (
        f"sabitin yanindaki yorum {beyan} saat diyor, hesap {gercek:.1f} saat"
    )


@pytest.mark.parametrize(
    "alan",
    ["nats_stream_raw_max_bytes", "nats_stream_normalized_max_bytes",
     "nats_stream_dlq_max_bytes"],
)
def test_stream_tavanlari_SINIRLI(alan: str):
    """0 = SINIRSIZ demek; o durumda `max_age` disk garantisi vermez ve
    tavana carpinca publish REDDEDILIR, yani telemetri akisi DURUR."""
    assert getattr(settings, alan) > 0


def test_toplam_tavan_disk_butcesine_SIGIYOR():
    """Uc stream toplami 12 GiB; nats-server.conf max_file_store bunun
    UZERINDE kalmali (aksi halde hesap tavani once dolar ve sert red gelir)."""
    toplam = (
        settings.nats_stream_raw_max_bytes
        + settings.nats_stream_normalized_max_bytes
        + settings.nats_stream_dlq_max_bytes
    )
    assert toplam == 12 * 1024**3, f"toplam {toplam / 1024**3:.1f} GiB"


def test_kesinti_toleransi_GERCEKCI_belgelenmis():
    """Operatorun bilmesi gereken sey: kac saatlik kesinti veri kaybettirir."""
    saat = _tampon_saat(settings.nats_stream_raw_max_bytes)
    assert saat < 8, (
        f"hesaplanan tampon {saat:.1f} saat — bu kadar uzunsa yorum ve "
        "planlama varsayimlari gozden gecirilmeli"
    )
    kaynak = inspect.getsource(cfg)
    assert "103,68M" in kaynak or "103.68M" in kaynak, (
        "hedef yuk kaynakta belgelenmemis; bir sonraki okuyan yine eski "
        "varsayimla hesap yapar"
    )
