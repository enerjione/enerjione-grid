"""`device.last_update_at` yazma kismasi.

OLCUM (saha test cihazi, 12 cihaz, 2026-08-01)
-----------------------------------------------
Alan HER online okumada yaziliyordu ve `devices` tablosuna **saniyede ~36
UPDATE** gidiyordu. Tablo 12 SATIR ve 6 indeksli; her UPDATE yeni bir satir
surumu + 6 indeks girdisi + WAL uretiyor, autovacuum surekli o tabloda
calisiyor. `devices` neredeyse her sorguda okundugu icin surekli kirlenen
sayfalar onbellegi de bozuyor.

Alanin tek tuketicisi arayuzdeki "Son veri: X once" gostergesi. Saniyede uc
kez yazmanin kullaniciya hicbir karsiligi yok.

NEYIN KISILMADIGI DA ONEMLI
---------------------------
`communication_status` KISILMIYOR. Cihazin cevrimici olup olmadigi o alanla
belirleniyor; onu geciktirmek "cihaz dustu" bilgisini geciktirmek olurdu ve
bu, bu depoda kapatilan "yesil yalan" sinifinin ta kendisi olurdu.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import CommunicationStatus
from app.services import tag_engine_service as svc


def _kod(kaynak: str) -> str:
    """Yorum satirlarini eler.

    Bu depoda metin aramasi defalarca KENDI aciklamalarina takildi; burada da
    oyle oldu. Denetlenen sey KOD olmali."""
    import re

    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


class _SahteCihaz:
    """Yalnizca kismanin dokundugu alanlar."""

    def __init__(self, last_update_at=None):
        self.id = 1
        self.code = "DEV-001"
        self.name = "Test"
        self.last_update_at = last_update_at
        self.communication_status = CommunicationStatus.OFFLINE
        self.battery_percent = None


# URETIM FONKSIYONUNUN KENDISI cagriliyor, kopyasi DEGIL.
#
# Ilk yazimda kosulu testte YENIDEN YAZMISTIM; uretimden naive-datetime
# korumasini tamamen silen bir mutasyon test tarafindan KACIRILDI, cunku
# test kendi kopyasini kontrol ediyordu. Mutasyon testi yakaladi.
_kisma_karari = svc.should_write_last_update


def test_esik_makul_aralikta():
    """Cok kucukse kisma ise yaramaz, cok buyukse arayuz gorunur sekilde
    geride kalir."""
    assert 1.0 <= svc.LAST_UPDATE_WRITE_THROTTLE_SEC <= 30.0


def test_ilk_okumada_HEMEN_yaziliyor():
    """Cihaz hic gorulmemisse gecikme kabul edilemez — arayuzde 'hic veri
    gelmedi' gorunurdu."""
    simdi = datetime.now(timezone.utc)
    assert _kisma_karari(None, simdi) is True


def test_esik_dolmadan_YAZILMIYOR():
    simdi = datetime.now(timezone.utc)
    onceki = simdi - timedelta(seconds=svc.LAST_UPDATE_WRITE_THROTTLE_SEC / 2)
    assert _kisma_karari(onceki, simdi) is False


def test_esik_dolunca_YAZILIYOR():
    simdi = datetime.now(timezone.utc)
    onceki = simdi - timedelta(seconds=svc.LAST_UPDATE_WRITE_THROTTLE_SEC + 0.1)
    assert _kisma_karari(onceki, simdi) is True


def test_TAM_esikte_yaziliyor():
    simdi = datetime.now(timezone.utc)
    onceki = simdi - timedelta(seconds=svc.LAST_UPDATE_WRITE_THROTTLE_SEC)
    assert _kisma_karari(onceki, simdi) is True


def test_NAIVE_datetime_cokertmiyor():
    """Eski kayitlarda naive deger olabilir; cikarma islemi TypeError
    atarsa telemetri alimi tamamen durur."""
    simdi = datetime.now(timezone.utc)
    naive = (simdi - timedelta(seconds=60)).replace(tzinfo=None)
    assert _kisma_karari(naive, simdi) is True


# ---------------------------------------------------------------------------
# Kaynak duzeyinde: dogru sey kisildi mi
# ---------------------------------------------------------------------------

def test_last_update_at_KISILIYOR():
    """Kontrol METIN DEGIL cagri uzerinden.

    Ilk yazimda `"LAST_UPDATE_WRITE_THROTTLE_SEC" in kaynak` diye bakiyordum
    ve bu YETERSIZDI: kismayi tamamen kaldiran bir mutasyon GECTI, cunku ayni
    ad hemen ustteki ACIKLAMA SATIRINDA geciyordu. Mutasyon testi yakaladi.
    """
    kaynak = _kod(inspect.getsource(svc.process_telemetry_reading))
    assert "should_write_last_update(" in kaynak, (
        "last_update_at her okumada yaziliyor — 12 cihazda bile saniyede "
        "~36 UPDATE demek"
    )
    assert "device.last_update_at = simdi" in kaynak
    # Atama, kararin ICINDE olmali.
    i_karar = kaynak.find("should_write_last_update(")
    i_atama = kaynak.find("device.last_update_at = simdi")
    assert i_karar < i_atama, "atama karardan ONCE yapiliyor"


def test_communication_status_KISILMIYOR():
    """Cihazin cevrimici olup olmadigi bu alanla belirleniyor; geciktirmek
    'cihaz dustu' bilgisini geciktirmek olurdu."""
    kaynak = _kod(inspect.getsource(svc.process_telemetry_reading))
    i_status = kaynak.find("device.communication_status = next_status")
    i_throttle = kaynak.find("should_write_last_update(")
    assert i_status != -1, "communication_status atamasi bulunamadi"
    assert i_throttle != -1
    assert i_status < i_throttle, (
        "communication_status kismanin ICINE alinmis — cihaz dustugu anda "
        "degil, en fazla esik kadar SONRA gorunur"
    )


def test_hata_yolu_ANA_YOL_ile_ayni():
    """Ikisi ayrisirsa hata yolu sessizce eski (kisilmamis) davranisa doner."""
    from app.services import telemetry_consumer

    kaynak = _kod(inspect.getsource(telemetry_consumer._persist_batch))
    # Ad, fonksiyon ICINDEKI import satirinda da geciyor; asil aranan
    # CAGRI. Ilk yazimda sabiti aradim ve mutasyon kacti.
    assert kaynak.count("should_write_last_update(") >= 1, (
        "hata yolundaki last_update_at yazimi kisilmamis"
    )
    i_karar = kaynak.find("should_write_last_update(device.last_update_at")
    assert i_karar != -1, "hata yolu karari cihazin mevcut degerine bakmiyor"


@pytest.mark.parametrize(
    "modul,ad",
    [("app.services.tag_engine_service", "process_telemetry_reading")],
)
def test_battery_senkronu_KISMANIN_disinda(modul: str, ad: str):
    """Batarya yuzdesi yalnizca DEGISTIGINDE yazilir (SQLAlchemy ayni degeri
    atamayi UPDATE'e cevirmez), dolayisiyla kisilmasina gerek yok — ama
    kismanin icine alinirsa batarya guncellemesi de gecikirdi."""
    import importlib

    m = importlib.import_module(modul)
    kaynak = inspect.getsource(getattr(m, ad))
    i_throttle = kaynak.find("device.last_update_at = simdi")
    i_battery = kaynak.find("device.battery_percent = derived")
    assert i_throttle != -1 and i_battery != -1
    # Batarya atamasi, kismanin `if` blogunun ICINDE OLMAMALI.
    arasi = kaynak[i_throttle:i_battery]
    assert "if derived is not None" in arasi, (
        "batarya senkronu kismanin icine alinmis"
    )
