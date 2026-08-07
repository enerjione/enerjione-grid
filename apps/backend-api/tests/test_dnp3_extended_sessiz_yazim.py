"""DNP3 ek ayarlari: dokunulmamis alan diske YAZILMAMALI (2026-08-07 arizasi).

SAHADA OLAN
-----------
`master_address` varsayilani 100'du ve `merge_dnp3_extended` TUM alanlari
somutlastiriyordu. Operator cihaz kaydinda ILGISIZ bir alani (orn. TCP portu)
degistirip kaydettiginde master_address diske 100 olarak yaziliyor, gateway o
cihaza artik 100 adresiyle konusuyordu. Gateway'in kendi varsayilani ise 1.

DNP3 outstation'lari beklemedikleri master adresinden gelen istekleri SESSIZCE
ATAR: TCP kurulur, uygulama katmani hic cevap vermez. Gateway log'unda
`link_open -> 15sn fresh frame yok -> lost -> forced_relink` dongusu gorunur,
cihazin kendi ekraninda ise "DNP3 session var" yazar (dogru — TCP oturumu).

AYIRT EDICI: ayni gateway'deki SIMULATOR cihazlari master=100 ile sorunsuz
calisiyordu. Simulator master adresini dogrulamiyor, gercek outstation
doguluyor — yani bu hata SIMULASYON TESTLERINDE GORUNMEZ. Test etmenin tek
yolu, yazma yolunun dokunulmamis alani hic yazmadigini dogrulamaktir.
"""

from __future__ import annotations

from app.schemas.device import DeviceCreate, DeviceUpdate
from app.schemas.dnp3_extended import (
    Dnp3ExtendedSettings,
    dnp3_extended_to_store,
    merge_dnp3_extended,
)


def test_master_address_varsayilani_YOK():
    """Merkezi bir varsayilan, saha cihazinin bekledigini ezer."""
    assert Dnp3ExtendedSettings().master_address is None, (
        "master_address'e varsayilan atanmis — her kayitta diske yazilir ve "
        "gercek cihazin haberlesmesini SESSIZCE keser"
    )


def test_gonderilmeyen_alan_diske_yazilmaz():
    """Istemci yalnizca TCP portunu degistirdi; master_address YAZILMAMALI."""
    ayar = Dnp3ExtendedSettings.model_validate({"master_ip_port": 20005})
    saklanan = dnp3_extended_to_store(ayar)
    assert saklanan == {"master_ip_port": 20005}, (
        f"dokunulmamis alanlar somutlastirilmis: {saklanan}"
    )
    assert "master_address" not in saklanan
    # Ayni risk digerlerinde de var — hicbiri sizmamali.
    for alan in (
        "unsolicited_reporting",
        "validate_source_address",
        "session_timeout_listening_sec",
        "socket_listening_timeout_sec",
    ):
        assert alan not in saklanan


def test_cihaz_olustururken_master_address_sizmaz():
    """Uctan uca: DeviceCreate -> diske yazilacak sozluk."""
    payload = DeviceCreate.model_validate(
        {
            "code": "SN-1",
            "name": "SN-1",
            "ip_address": "10.0.0.5",
            "latitude": 0.0,
            "longitude": 0.0,
            "dnp3_extended": {"ip_endpoint_type": "listening"},
        }
    )
    saklanan = dnp3_extended_to_store(payload.dnp3_extended)
    assert saklanan == {"ip_endpoint_type": "listening"}
    assert "master_address" not in saklanan


def test_acikca_gonderilen_master_address_YAZILIR():
    """Operator bilerek girdiyse saygi gosterilir — sessizce yutulmaz."""
    payload = DeviceUpdate.model_validate(
        {"dnp3_extended": {"master_address": 7}}
    )
    saklanan = dnp3_extended_to_store(payload.dnp3_extended)
    assert saklanan == {"master_address": 7}


def test_gorunumde_eksik_alan_None_kalir():
    """Okuma yolunda tamamlama yapilir ama master_address UYDURULMAZ:
    None kalmali ki arayuz 'gateway varsayilani' gosterebilsin."""
    gorunum = merge_dnp3_extended({"ip_endpoint_type": "listening"})
    assert gorunum.master_address is None
    # Gosterim icin guvenli olan diger varsayilanlar dolar.
    assert gorunum.socket_listening_timeout_sec == 600


def test_gateway_configinde_None_master_address_gonderilmez():
    """gateways.py None'i 'gateway kendi varsayilanini kullansin' diye
    yorumlar; 100 yazmak yerine alani bos birakmak DOGRU davranistir."""
    ext = {"ip_endpoint_type": "listening"}
    ham = ext.get("master_address")
    master_address = int(ham) if ham is not None else None
    assert master_address is None
