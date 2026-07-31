"""B1 — DNP3 kalite bayraklari: KAPSAM ayrimi.

SORUNUN OZU
-----------
`invalid` ve `restart` token'lari IKI FARKLI KAPSAMDA uretiliyor:

  * ESKI gateway (0.4.x / legacy dnp3_master): CIHAZ seviyesi.
    "Tum cihaz okunamadi" demek. Cihazi OFFLINE saymak DOGRU.
  * YENI gateway (0.5.0+, map_dnp3_quality): NOKTA seviyesi.
    "Bu tek olcum gecersiz" demek. Cihaz pekala ayakta ve diger 192 sinyali
    saglikli olabilir.

Ayni kelime, iki farkli anlam. Ayrim yapilmadan yeni gateway'in bayragi
acilsaydi, TEK bir noktanin referans hatasi (orn. CT referansi kaybi) TUM
CIHAZI offline gosterirdi: harita kirmizi, "son veri" sayaci donar, batarya
senkronu durur. Ustelik olcum kaybolmaz — SESSIZCE YANLIS YORUMLANIRDI.

AYRIMI NE YAPIYOR
-----------------
`dnp3_flags` alaninin VARLIGI. Gateway bayragi okumadan yeni kaliteyi zaten
uretemiyor, dolayisiyla ayri bir surum alanina gerek yok:

    dnp3_flags is not None -> NOKTA seviyesi
    dnp3_flags is None     -> CIHAZ seviyesi (bugunku davranis aynen korunur)
"""

from __future__ import annotations

import pytest

from app.models.enums import CommunicationStatus
from app.services.tag_engine_service import (
    ALARM_BLOCKING_QUALITIES,
    map_quality_to_status,
    map_quality_to_status_scoped,
    quality_blocks_alarm,
)

ONLINE = CommunicationStatus.ONLINE
OFFLINE = CommunicationStatus.OFFLINE


# ---------------------------------------------- ESKI gateway (dnp3_flags YOK)


@pytest.mark.parametrize(
    "quality,beklenen",
    [
        ("good", ONLINE),
        ("comm_lost", OFFLINE),
        ("invalid", OFFLINE),   # cihaz seviyesi: tum cihaz okunamadi
        ("restart", OFFLINE),
        ("bad", OFFLINE),
        ("offline", OFFLINE),
    ],
)
def test_eski_gateway_davranisi_AYNEN_korunur(quality, beklenen):
    """dnp3_flags yoksa bugunku davranis bit bit ayni kalmali.

    Sahada 0.4.x gateway'ler var; bu satirlar degisirse onlarin cihazlari
    yanlis durumda gorunur.
    """
    assert map_quality_to_status_scoped(quality, dnp3_flags=None) is beklenen
    # Kapsamsiz eski fonksiyonla da ayni sonucu vermeli
    assert map_quality_to_status(quality) is beklenen


# ---------------------------------------- YENI gateway (dnp3_flags MEVCUT)


@pytest.mark.parametrize("quality", ["invalid", "restart", "forced"])
def test_nokta_bazli_sorun_CIHAZI_offline_YAPMAZ(quality):
    """Asil kazanim: tek noktanin sorunu tum cihazi karartmamali.

    Somut senaryo: outstation CT referansini kaybediyor ve analog noktayi
    `value=0.0, flags=ONLINE|REFERENCE_ERR` olarak raporluyor. Cihazin geri
    kalan 192 sinyali saglikli. Cihazi OFFLINE gostermek yanlis olurdu.
    """
    assert (
        map_quality_to_status_scoped(quality, dnp3_flags=0x20) is ONLINE
    ), f"'{quality}' nokta seviyesinde oldugu halde cihaz OFFLINE yapildi"


def test_comm_lost_bayrak_olsa_bile_CIHAZ_seviyesidir():
    """`comm_lost` = DNP3/TCP baglantisi YOK. Bu her zaman cihaz seviyesidir.

    Nokta bazli bayrak tasisa bile cihaz OFFLINE kalmali — baglanti yoksa
    hicbir nokta okunamiyor demektir.
    """
    assert map_quality_to_status_scoped("comm_lost", dnp3_flags=0) is OFFLINE


def test_good_her_iki_kapsamda_da_online():
    assert map_quality_to_status_scoped("good", dnp3_flags=None) is ONLINE
    assert map_quality_to_status_scoped("good", dnp3_flags=0x01) is ONLINE


def test_dnp3_flags_sifir_da_VARLIK_sayilir():
    """0 gecerli bir bayrak degeridir; `is not None` ile ayrilmali.

    `if dnp3_flags:` yazilsaydi 0 degeri "yok" sayilir ve yeni gateway'in
    tum-bayraklar-temiz noktalari eski kapsamda degerlendirilirdi.
    """
    assert map_quality_to_status_scoped("invalid", dnp3_flags=0) is ONLINE


# ------------------------------------------------------- alarm ayrimi


def test_forced_cihazi_online_birakir_ama_ALARMI_engeller():
    """`forced` iki karari AYIRAN somut ornek.

    Operator noktayi elle zorlamis: cihaz AYAKTA (haberlesme var) ama deger
    YAPAY. O degerden alarm uretmek ya da acik bir alarmi o degerle kapatmak
    yanlis olur.
    """
    assert map_quality_to_status_scoped("forced", dnp3_flags=0x10) is ONLINE
    assert quality_blocks_alarm("forced") is True


def test_alarm_kumesi_haberlesme_kumesinden_GENIS():
    """Iki kume ayni DEGIL — ayni olsalardi `forced` ayrimi kaybolurdu."""
    from app.services.tag_engine_service import _OFFLINE_QUALITIES

    assert "forced" in ALARM_BLOCKING_QUALITIES
    assert "forced" not in _OFFLINE_QUALITIES
    assert set(_OFFLINE_QUALITIES).issubset(set(ALARM_BLOCKING_QUALITIES))


# ------------------------------------------------------- sozlesme


def test_telemetri_semasi_dnp3_flags_kabul_eder():
    from datetime import datetime, timezone

    from app.schemas.telemetry import TelemetryIn

    t = TelemetryIn(
        device_code="DEV1",
        signal_key="master.current",
        value=0.0,
        quality="invalid",
        dnp3_flags=0x22,
        source_timestamp=datetime.now(timezone.utc),
    )
    assert t.dnp3_flags == 0x22


def test_eski_gateway_payloadi_hala_gecerli():
    """0.4.x `dnp3_flags` GONDERMEZ — sema bunu kabul etmeye devam etmeli.

    Alan zorunlu yapilsaydi sahadaki tum gateway'lerin telemetrisi
    dogrulamadan duserdi.
    """
    from datetime import datetime, timezone

    from app.schemas.telemetry import TelemetryIn

    t = TelemetryIn(
        device_code="DEV1",
        signal_key="master.current",
        value=1.0,
        quality="good",
        source_timestamp=datetime.now(timezone.utc),
    )
    assert t.dnp3_flags is None
