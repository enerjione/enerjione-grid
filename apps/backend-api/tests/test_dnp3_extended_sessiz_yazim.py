"""DNP3 ek ayarlari: master_address'in IKI AYRI kurali (2026-08-07 arizasi).

1) YAZMA — istemcinin GONDERMEDIGI alan diske yazilmamali. Yoksa operator
   ILGISIZ bir alani (orn. TCP portu) degistirip kaydettiginde dokunmadigi
   ayarlar merkezi varsayilanlarla SABITLENIR.
2) OKUMA — eksik ya da `null` kayit VARSAYILANA (100) tamamlanmali.

Bu ikisi birbirinden BAGIMSIZDIR: `exclude_unset` alanin varsayilan degerine
degil, istemcinin gonderip gondermedigine bakar. v2.54.1 bunlari karistirdi,
(1)'i korumak icin varsayilani None yapti ve (2)'yi kirdi; v2.54.3 yalnizca
frontend sabitini geri aldigi icin yetmedi — backend'in acik `null`'i
frontend'deki `{...DEFAULT, ...raw}` spread'inde 100'u ezmeye devam etti.

SAHADA OLAN: master_address bosalinca gateway kendi DNP3_LOCAL_ADDRESS=1
varsayilanini kullandi. DNP3 outstation'lari beklemedikleri master adresinden
gelen istekleri SESSIZCE ATAR: TCP kurulur, uygulama katmani hic cevap vermez.
Gateway log'unda `link_open -> 15sn fresh frame yok -> lost -> forced_relink`
dongusu gorunur, cihazin kendi ekraninda "DNP3 session var" yazar (dogru — TCP
oturumu). Horstmann SN2 fabrika degeri 100'dur.

AYIRT EDICI: ayni gateway'deki SIMULATOR cihazlari sorunsuz calisir —
simulator master adresini dogrulamaz, gercek outstation dogrular. Yani bu hata
SIMULASYON TESTLERINDE GORUNMEZ; ancak bu birim testleriyle yakalanabilir.
"""

from __future__ import annotations

from app.schemas.device import DeviceCreate, DeviceUpdate
from app.schemas.dnp3_extended import (
    DEFAULT_MASTER_ADDRESS,
    Dnp3ExtendedSettings,
    dnp3_extended_to_store,
    merge_dnp3_extended,
)


def test_master_address_varsayilani_100():
    """Varsayilan 100 — frontend DEFAULT_DNP3_EXTENDED ile AYNI olmali.

    v2.54.1 bunu None'a cekti; okuma yolu `null` donunce frontend'deki
    `{...DEFAULT, ...raw}` spread'i 100'u EZDI ve kayitli cihazlarin
    Master Adres alani bosaldi. v2.54.3 yalnizca form sabitini geri aldigi
    icin yetmedi: backend'in acik `null`'i kazanmaya devam etti.
    """
    assert Dnp3ExtendedSettings().master_address == DEFAULT_MASTER_ADDRESS == 100


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


def test_gorunumde_eksik_alan_varsayilana_tamamlanir():
    """Okuma yolunda eksik master_address 100'e tamamlanir — arayuzdeki
    form da 100 gosterir, ikisi ayrisamaz."""
    gorunum = merge_dnp3_extended({"ip_endpoint_type": "listening"})
    assert gorunum.master_address == 100
    # Gosterim icin guvenli olan diger varsayilanlar dolar.
    assert gorunum.socket_listening_timeout_sec == 600


def test_diske_yazilmis_null_varsayilana_iyilesir():
    """REGRESYON — sahayi susturan durum.

    v2.54.1 penceresinde kaydedilen cihazlarin diskinde acikca
    `master_address: null` var. Bu deger okuma yolunda "yok" sayilmali,
    yoksa gateway alani bos alir, DNP3_LOCAL_ADDRESS=1 kullanir ve 100
    bekleyen Horstmann SN2 istegi sessizce atar.
    """
    gorunum = merge_dnp3_extended(
        {"ip_endpoint_type": "listening", "master_address": None}
    )
    assert gorunum.master_address == 100


def test_acikca_girilen_deger_null_ile_ezilmez():
    """Operator 7 girdiyse iyilestirme onu 100'e CEKMEZ."""
    assert merge_dnp3_extended({"master_address": 7}).master_address == 7
