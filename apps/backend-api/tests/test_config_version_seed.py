"""`config_version` payload'i TAM olarak temsil etmeli.

YASANAN HATA (bu test yazilmadan once CANLIYDI)
------------------------------------------------
`config_version` elle tutulan bir "seed" string'inden hesaplaniyordu:

    device_seed  = code : ip_address : dnp3_address : poll_interval_sec
    signal_seed  = source : key : data_type : group : index : scale

Ama payload BUNLARDAN FAZLASINI tasiyor: `dnp3_tcp_port`, `master_address`,
`ip_endpoint_type`, `master_ip_port`, `timeout_ms`, `retry_count`,
`signal_profile`, sinyal `offset`i, `label`, `unit`, `supports_alarm`...

Sonuc: bir cihazin TCP PORTUNU degistirdiginizde
    payload degisir  ->  config_version AYNI kalir
    ->  ETag esleşir  ->  gateway 304 alir
    ->  DEGISIKLIGI HIC OGRENMEZ
Ustelik gateway disk cache'ini de tazelemedigi icin, backend erisilemezken
yeniden baslayan bir gateway ESKI ayarla aciliyordu.

COZUM: hash artik gonderilecek payload'in KENDISINDEN turetiliyor. Elle
liste tutulmadigi icin bir daha sapamaz. Bu testler o ozelligi kilitler.
"""

from __future__ import annotations

from app.api.gateways import compute_config_version
from app.schemas.gateway import GatewayConfigDevice, GatewayConfigSignal


def _surum(
    devices,
    signals,
    *,
    gateway_name="GW",
    batch=5,
    max_dev=200,
    aktif=True,
    profiller=None,
) -> str:
    """Endpoint'in KULLANDIGI fonksiyonun ta kendisi.

    Burada bir zamanlar hesabin ELLE KOPYASI vardi. Iki kopya sessizce
    ayrisabilirdi — ki bu dosyanin belgeledigi hatanin ozu tam olarak "hash
    gonderilen veriyi temsil etmiyor" idi. Kopyayi kaldirdik: endpoint neyi
    hash'liyorsa test de onu hash'ler.
    """
    return compute_config_version(
        gateway_name=gateway_name,
        batch_interval_sec=batch,
        max_devices=max_dev,
        is_active=aktif,
        devices=devices,
        signals=signals,
        signals_by_profile=profiller,
    )


def _cihaz(**over) -> GatewayConfigDevice:
    alanlar = dict(
        code="DEV-1",
        name="Cihaz 1",
        ip_address="10.0.0.5",
        dnp3_address=10,
        dnp3_tcp_port=20000,
        master_address=1,
        ip_endpoint_type="listening",
        master_ip_port=None,
        poll_interval_sec=5,
        timeout_ms=3000,
        retry_count=2,
        signal_profile="horstmann_sn2_fixed",
    )
    alanlar.update(over)
    return GatewayConfigDevice(**alanlar)


def _sinyal(**over) -> GatewayConfigSignal:
    alanlar = dict(
        key="master.current",
        label="Akim",
        unit="A",
        source="master",
        dnp3_class="Class 1",
        data_type="analog",
        dnp3_object_group=30,
        dnp3_index=12,
        scale=1.0,
        offset=0.0,
        supports_alarm=True,
    )
    alanlar.update(over)
    return GatewayConfigSignal(**alanlar)


def test_ayni_payload_ayni_surum():
    a = _surum([_cihaz()], [_sinyal()])
    b = _surum([_cihaz()], [_sinyal()])
    assert a == b, "ayni payload farkli surum uretti — 304 hic calismaz"


# --------------------------------------------------------- ESKI TOHUMUN
# --------------------------------------------------------- KACIRDIKLARI


def test_TCP_PORTU_degisince_surum_DEGISIR():
    """Yasanan hatanin ta kendisi.

    Eski tohumda `dnp3_tcp_port` YOKTU; port degisikligi sahaya HIC
    ulasmiyordu.
    """
    onceki = _surum([_cihaz(dnp3_tcp_port=20000)], [_sinyal()])
    sonraki = _surum([_cihaz(dnp3_tcp_port=20001)], [_sinyal()])
    assert onceki != sonraki, "TCP port degisti ama config_version ayni kaldi"


def test_master_address_degisince_surum_DEGISIR():
    """DNP3 link layer adresi — saha cihazi bu adresi bekler, yanlissa hic
    konusamaz."""
    assert _surum([_cihaz(master_address=1)], [_sinyal()]) != _surum(
        [_cihaz(master_address=7)], [_sinyal()]
    )


def test_timeout_ve_retry_degisince_surum_DEGISIR():
    assert _surum([_cihaz(timeout_ms=3000)], [_sinyal()]) != _surum(
        [_cihaz(timeout_ms=5000)], [_sinyal()]
    )
    assert _surum([_cihaz(retry_count=2)], [_sinyal()]) != _surum(
        [_cihaz(retry_count=5)], [_sinyal()]
    )


def test_signal_profile_degisince_surum_DEGISIR():
    """B3 icin kritik: profil degisikligi gateway'e ULASMALI."""
    assert _surum([_cihaz(signal_profile="a")], [_sinyal()]) != _surum(
        [_cihaz(signal_profile="b")], [_sinyal()]
    )


def test_endpoint_tipi_degisince_surum_DEGISIR():
    """listening <-> initiating: gateway'in TCP rolu tamamen degisir."""
    assert _surum([_cihaz(ip_endpoint_type="listening")], [_sinyal()]) != _surum(
        [_cihaz(ip_endpoint_type="initiating", master_ip_port=20100)], [_sinyal()]
    )


def test_sinyal_OFFSETI_degisince_surum_DEGISIR():
    """Eski tohumda `scale` vardi ama `offset` YOKTU — olcum kalibrasyonu
    sessizce eski degerle devam ederdi."""
    assert _surum([_cihaz()], [_sinyal(offset=0.0)]) != _surum(
        [_cihaz()], [_sinyal(offset=-40.0)]
    )


def test_sinyal_etiketi_degisince_surum_DEGISIR():
    assert _surum([_cihaz()], [_sinyal(label="Akim")]) != _surum(
        [_cihaz()], [_sinyal(label="Faz Akimi")]
    )


def test_supports_alarm_degisince_surum_DEGISIR():
    assert _surum([_cihaz()], [_sinyal(supports_alarm=True)]) != _surum(
        [_cihaz()], [_sinyal(supports_alarm=False)]
    )


# ------------------------------------------------------------- yapisal


def test_HER_cihaz_alani_surumu_etkiler():
    """Yapisal koruma: ileride payload'a alan eklenirse bu test onu yakalar.

    Elle tutulan liste yerine payload'dan turetildigi icin YENI eklenen her
    alan otomatik olarak hash'e girer. Bu test o ozelligin korundugunu
    dogrular — biri hash'i tekrar elle listeye baglarsa kirmizi olur.
    """
    taban = _surum([_cihaz()], [_sinyal()])
    degistirilebilir = {
        "code": "DEV-2",
        "name": "Baska",
        "ip_address": "10.0.0.9",
        "dnp3_address": 99,
        "dnp3_tcp_port": 20009,
        "master_address": 9,
        "poll_interval_sec": 9,
        "timeout_ms": 9000,
        "retry_count": 9,
        "signal_profile": "baska_profil",
    }
    for alan, deger in degistirilebilir.items():
        assert _surum([_cihaz(**{alan: deger})], [_sinyal()]) != taban, (
            f"'{alan}' degisti ama config_version AYNI kaldi — bu alan sahaya ulasmaz"
        )


def test_gateway_seviyesi_alanlar_da_surumu_etkiler():
    taban = _surum([_cihaz()], [_sinyal()])
    assert _surum([_cihaz()], [_sinyal()], batch=9) != taban
    assert _surum([_cihaz()], [_sinyal()], max_dev=50) != taban
    assert _surum([_cihaz()], [_sinyal()], aktif=False) != taban
    assert _surum([_cihaz()], [_sinyal()], gateway_name="Yeni Ad") != taban
