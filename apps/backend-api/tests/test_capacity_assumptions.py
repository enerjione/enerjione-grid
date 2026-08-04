"""Kapasite sabitleri HEDEF olcege gore hesaplanmali (Faz 2-16).

YASANAN SORUN
-------------
Koddaki kapasite yorumlari 25,9M satir/gun (200 cihaz) varsayimiyla
yazilmisti. HEDEF yuk ise 600 cihaz x 20 aktif sinyal / 10 sn =
~1.200 deger/sn = 103,68M satir/gun — yani DORT KAT fazla.

Somut sonuc: `nats_stream_raw_max_bytes` yorumunda "~19 saat tampon"
yaziyordu; gercekte hedef olcekte ~2,5 saat. Bu, tavanin yanlis oldugu
anlamina gelmez (6 GiB disk butcesi icinde makul bir pay), ama
"19 saatlik kesintiyi tolere ederiz" varsayimiyla planlama yapmak
YANLISTIR: 3 saati asan bir backend kesintisinde en eski telemetri KAYBOLUR.

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

    Eski yorum "~19 saat" diyordu ve gercek ~2,5 saatti; operator yedi kat
    yanlis bir varsayimla planlama yapiyordu.
    """
    saat = _tampon_saat(settings.nats_stream_raw_max_bytes)
    kaynak = inspect.getsource(cfg)

    m = re.search(r"raw\s+24 GiB\s+->\s+~([\d,]+)\s+saat", kaynak)
    assert m, "config.py'de raw tampon suresi belgelenmemis"
    belgelenen = float(m.group(1).replace(",", "."))

    assert abs(belgelenen - saat) < 1.5, (
        f"belgelenen tampon {belgelenen} saat, hesaplanan {saat:.1f} saat — "
        "olcek ya da tavan degismis, yorum guncellenmemis"
    )


def test_normalized_tampon_suresi_YORUMLA_uyusuyor():
    saat = _tampon_saat(settings.nats_stream_normalized_max_bytes)
    kaynak = inspect.getsource(cfg)
    m = re.search(r"normalized\s+12 GiB\s+->\s+~([\d,]+)\s+saat", kaynak)
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
    """Uc stream toplami 38 GiB; nats-server.conf max_file_store bunun
    UZERINDE kalmali (48 GiB) — aksi halde hesap tavani once dolar ve akis
    basina tavan hic devreye girmeden SERT RED geri gelir.

    2026-08-04: 10 -> 38 GiB. 500 cihazlik testte 3 GiB'lik NORMALIZED
    tavani doldu, akis en eski mesajlari atmaya basladi (VERI KAYBI) ve
    dolu akisa yazmanin maliyeti tum zinciri kilitledi."""
    toplam = (
        settings.nats_stream_raw_max_bytes
        + settings.nats_stream_normalized_max_bytes
        + settings.nats_stream_dlq_max_bytes
    )
    assert toplam == 38 * 1024**3, f"toplam {toplam / 1024**3:.1f} GiB"


#: Saha olcumu (test sunucusu, 100 cihaz / 176 sinyal): JetStream deposu
#: 5,6 GB. Ayni anda gateway 1.135 msg/sn basiyor, backend 480 msg/sn
#: isliyordu — yani saniyede 655 mesaj BIRIKIYOR ve deponun bir kismi HENUZ
#: ISLENMEMIS telemetridir.
OLCULEN_JETSTREAM_DEPO_BAYT = 5_600_000_000


def test_stream_tavani_OLCULEN_DEPONUN_USTUNDE_kaliyor():
    """Tavani dusurmek "disk temizligi" degil VERI SILME islemidir.

    `discard=OLD` budamasi tuketicinin NEREDE OLDUGUNA BAKMAZ: stream'in
    kuyrugundan siler, ack durumundan bagimsiz. Yani `max_bytes`, tuketicinin
    geri kalabilecegi MESAFEDIR.

    Backend her boot'ta drift kontrolu yapip `update_stream` cagiriyor
    (jetstream_bus). Bu yuzden tavani olculen depo boyutunun ALTINA cekmek,
    degisiklik sahaya gittigi anda — sessizce, hicbir uyari uretmeden — o
    farki siler. Birikim varken tavan dusurmek, yavas sistemi hizli
    gostermek icin veriyi atmaktir.

    Tavanlari gercekten dusurmek icin ON KOSUL: tuketici HTTP surecinden
    ayrilip birikimin eridigi DOGRULANMALI.
    """
    assert settings.nats_stream_raw_max_bytes > OLCULEN_JETSTREAM_DEPO_BAYT, (
        f"raw tavani {settings.nats_stream_raw_max_bytes:,} bayt, olculen "
        f"JetStream deposu {OLCULEN_JETSTREAM_DEPO_BAYT:,} bayt — bu tavan "
        "sahaya gittiginde ilk yeniden baslatmada ISLENMEMIS telemetri silinir"
    )


def test_yas_sinirlari_YUKLU_sahada_hic_ISLEMEZ():
    """Yas sinirini kisaltmak yuklu sahada veri kaybettirmemeli.

    Bunun kaniti su: hedef olcekte bayt tavani SAATLER icinde carpar, yas
    siniri ise GUNLER cinsindendir. Yani budamayi her zaman bayt tavani
    yapar; yas siniri yalnizca hizin cok dusuk oldugu kucuk sahalarda —
    stream'in ack'lenmis mesajlari arsivlemesini engellemek icin — devreye
    girer.

    Bu iliski bozulursa (yas siniri tampon suresinin altina inerse) yas
    siniri yuklu sahada da budamaya baslar ve o zaman ISLENMEMIS mesajlar
    dusebilir.
    """
    tampon_saat = _tampon_saat(settings.nats_stream_raw_max_bytes)
    yas_saat = settings.nats_stream_raw_max_age_days * 24
    assert yas_saat > tampon_saat * 4, (
        f"raw yas siniri {yas_saat} saat, bayt tavani {tampon_saat:.1f} saatlik "
        "tampon veriyor — yas siniri artik yuklu sahada da budama yapiyor"
    )


def test_kesinti_toleransi_GERCEKCI_belgelenmis():
    """Operatorun bilmesi gereken sey: kac saatlik kesinti veri kaybettirir.

    UST SINIR NEDEN VAR: cok uzun bir tampon, "tuketici yavas ama sorun yok,
    kuyruk tutuyor" yanilgisini besler. Tampon bir COZUM degil, ariza aninda
    veri kaybini onleyen bir yastiktir; tuketim uretimin altinda kaldigi
    surece her tavan er ya da gec dolar.

    SINIR 8 -> 12 SAAT (2026-08-04, saha olcumuyle): 500 cihazlik testte
    3 GiB'lik NORMALIZED tavani DOLDU ve akis en eski mesajlari sessizce
    atmaya basladi — yani sinir fazla yuksek degil, fazla ALCAKTI. Ustelik
    dolu akisa yazmanin maliyeti tum zinciri kilitledi (tag-engine %13
    CPU'da bekliyordu, RAW 2,1 milyona sismisti).

    Yeni tavan bir gecelik (~10 saat) backend kesintisini veri kaybi
    olmadan tasir. Bu, sahada mudahalenin ertesi sabah gelebilecegi bir
    urun icin makul bir hedef.
    """
    saat = _tampon_saat(settings.nats_stream_raw_max_bytes)
    assert saat < 12, (
        f"hesaplanan tampon {saat:.1f} saat — bu kadar uzunsa yorum ve "
        "planlama varsayimlari gozden gecirilmeli"
    )
    kaynak = inspect.getsource(cfg)
    assert "103,68M" in kaynak or "103.68M" in kaynak, (
        "hedef yuk kaynakta belgelenmemis; bir sonraki okuyan yine eski "
        "varsayimla hesap yapar"
    )
