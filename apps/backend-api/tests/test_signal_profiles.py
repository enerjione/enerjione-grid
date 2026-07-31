"""B3 — profil (cihaz turu) bazli sinyal katalogu.

SORUN
-----
`GET /gateways/{code}/config` DUZ bir `signals` listesi donuyordu ve gateway
onu TUM cihazlara ayni sekilde uyguluyordu (poller tek `state.signals()`
kullanir). Cihaz modeli tek oldugu surece bu TESADUFEN dogru calisir.

Ikinci bir model eklendigi anda bozulur: ayni (object_group, index) cifti iki
modelde FARKLI buyuklugu gosterir. Gateway hangi cihaz icin hangi seti
kullanacagini bilmedigi icin okudugu degeri YANLIS `signal_key` ile yayinlar.
Hata SESSIZDIR — telemetri akar, deger makul gorunur, ama esik alarmi baska
bir buyuklugun uzerinden calisir.

Ustelik gateway'in kendi dokumantasyonu bunu ZATEN varsayiyordu:
"signal_profile ... Backend, gateway'e dondugu signals listesini bu profile
gore filtreler." Backend bu filtrelemeyi HIC yapmiyordu.

ADRES SAHIPLIGI (hedef mimari)
------------------------------
DNP3 adres haritasi gateway'de yasar; backend cihaz basina yalnizca TURU
soyler. Adres haritasi cihaz firmware'inin ozelligidir, musteri kurulumunun
degil. Farkli protokolde bir model geldiginde DNP3 sekilli katalog satiri o
cihazi zaten ifade edemez. Buradaki `signals_by_profile` o hedefe giderken
kopru: gateway'in henuz yerlesik profili olmayan modeller icin tek kaynak.

PROFIL ANAHTARI NEDEN `model`
-----------------------------
`devices.signal_profile` kolonu OLU: frontend cihaz olustururken sabit
"horstmann_sn2_fixed" yaziyor, hicbir yer okumuyor ve katalogun model
sozlugunde boyle bir deger YOK. Anahtar olarak kullanilsaydi hicbir profile
eslesmezdi. Katalogun gercek ayiricisi `signal_catalog.model` ve backend'in
geri kalani (api/signals.py) cihazi kataloga ZATEN model uzerinden bagliyor.
"""

from __future__ import annotations

import pytest

from app.api.gateways import (
    _DEFAULT_PROFILE_KEY,
    _profile_key_of,
    _to_config_signal,
    compute_config_version,
)
from app.models.device import Device
from app.models.signal_catalog import SignalCatalog

MODEL_A = "horstmann_sn_2_0"
MODEL_B = "acme_rtu_9000"


def _sinyal(key: str, model: str, group: int, index: int) -> SignalCatalog:
    return SignalCatalog(
        key=key,
        model=model,
        label=key,
        source="master",
        dnp3_class="Class 1",
        data_type="analog",
        dnp3_object_group=group,
        dnp3_index=index,
        scale=1.0,
        offset=0.0,
        supports_alarm=True,
        is_active=True,
        display_order=0,
    )


# --------------------------------------------------------- profil anahtari


def test_profil_anahtari_MODELDEN_gelir():
    assert _profile_key_of(Device(code="D1", model=MODEL_B)) == MODEL_B


def test_OLU_signal_profile_kolonu_KULLANILMAZ():
    """Kolon anahtar olarak kullanilsaydi hicbir profile eslesmezdi.

    Sahadaki deger "horstmann_sn2_fixed"; katalogun model sozlugunde boyle bir
    deger yok. Bu test o kolonun geri sizmasini engeller.
    """
    device = Device(code="D1", model=MODEL_A, signal_profile="horstmann_sn2_fixed")
    assert _profile_key_of(device) == MODEL_A
    assert _profile_key_of(device) != device.signal_profile


@pytest.mark.parametrize("bos", [None, "", "   "])
def test_model_bos_ise_VARSAYILAN_profil(bos):
    """Model'i bos kalmis eski kayit hicbir profile eslesmeden kalmamali."""
    assert _profile_key_of(Device(code="D1", model=bos)) == _DEFAULT_PROFILE_KEY


def test_varsayilan_profil_KAYIT_DEFTERINDEN_gelir():
    """Uc yerin de ayni degeri soylemesi ZORUNLU.

    Ayrisirlarsa model'i bos kalmis eski bir cihaz kaydi var olmayan bir
    profile eslesir; profil bos liste doner ve cihaz HIC yoklanmaz. Sessiz
    bir "cihaz karanlikta" arizasi olurdu.
    """
    from app.data.device_models import DEFAULT_MODEL

    assert _DEFAULT_PROFILE_KEY == DEFAULT_MODEL
    assert _DEFAULT_PROFILE_KEY == SignalCatalog.__table__.c.model.default.arg
    assert _DEFAULT_PROFILE_KEY == Device.__table__.c.model.default.arg


def test_kayitli_MODELLER_gecerli_profil_anahtaridir():
    """`device_models.MODELS` ile profil anahtari sozlugu ayni dili konusmali."""
    from app.data.device_models import MODELS

    for kod in MODELS:
        assert _profile_key_of(Device(code="D", model=kod)) == kod


# ------------------------------------------------------ profil ayristirmasi
#
# Endpoint'in profil kurma mantiginin ta kendisi (app/api/gateways.py).


def _profilleri_kur(devices, signals_rows):
    by_profile: dict[str, list] = {}
    for profile_key in sorted({_profile_key_of(d) for d in devices}):
        rows = [s for s in signals_rows if (s.model or "") == profile_key]
        by_profile[profile_key] = [_to_config_signal(s) for s in rows]
    return by_profile


def test_her_model_KENDI_sinyallerini_alir():
    """B3'un ozu: ayni (group, index) iki modelde farkli sey demektir."""
    signals = [
        _sinyal("master.current", MODEL_A, 30, 0),
        _sinyal("master.voltage", MODEL_A, 30, 1),
        _sinyal("acme.oil_temp", MODEL_B, 30, 0),  # AYNI adres, BASKA anlam
    ]
    devices = [Device(code="D1", model=MODEL_A), Device(code="D2", model=MODEL_B)]

    profiller = _profilleri_kur(devices, signals)

    assert {s.key for s in profiller[MODEL_A]} == {"master.current", "master.voltage"}
    assert {s.key for s in profiller[MODEL_B]} == {"acme.oil_temp"}
    # Cakisan adres iki profilde de var ama FARKLI anahtarla — gateway artik
    # hangi cihaz icin hangisini kullanacagini biliyor.
    a_30_0 = [s for s in profiller[MODEL_A] if (s.dnp3_object_group, s.dnp3_index) == (30, 0)]
    b_30_0 = [s for s in profiller[MODEL_B] if (s.dnp3_object_group, s.dnp3_index) == (30, 0)]
    assert a_30_0[0].key != b_30_0[0].key


def test_cihazi_OLMAYAN_modelin_sinyalleri_GONDERILMEZ():
    """Eskiden TUM katalog gidiyordu; gateway ilgisiz adresleri de yokluyordu."""
    signals = [
        _sinyal("master.current", MODEL_A, 30, 0),
        _sinyal("acme.oil_temp", MODEL_B, 30, 0),
    ]
    profiller = _profilleri_kur([Device(code="D1", model=MODEL_A)], signals)
    assert set(profiller) == {MODEL_A}


def test_kataloglu_olmayan_model_BOS_LISTE_alir_ama_ANAHTAR_YAZILIR():
    """Kasitli tasarim karari.

    Bos profili ATLAYIP gateway'i duz listeye dusurmek, farkli modelli bir
    kurulumda YABANCI adresleri yoklamak demektir: makul gorunen ama baska bir
    buyukluge ait degerler yanlis anahtarla yayinlanir. Sessiz yanlis veri,
    gorunur eksik veriden daha kotudur. Bu yuzden anahtar bos liste ile yazilir
    ve eksiklik operator'a gorunur olur.
    """
    signals = [_sinyal("master.current", MODEL_A, 30, 0)]
    profiller = _profilleri_kur([Device(code="D9", model="bilinmeyen_model")], signals)

    assert "bilinmeyen_model" in profiller, "anahtar atlanmis — gateway yabanci adres yoklar"
    assert profiller["bilinmeyen_model"] == []


def test_HER_cihazin_profili_sozlukte_VAR():
    """Gateway her cihaz icin anahtari bulabilmeli."""
    devices = [
        Device(code="D1", model=MODEL_A),
        Device(code="D2", model=MODEL_B),
        Device(code="D3", model=None),
    ]
    profiller = _profilleri_kur(devices, [_sinyal("master.current", MODEL_A, 30, 0)])
    for d in devices:
        assert _profile_key_of(d) in profiller


# -------------------------------------------------- config_version kapsami


def _cihaz(model=MODEL_A):
    from app.schemas.gateway import GatewayConfigDevice

    return GatewayConfigDevice(
        code="D1",
        name="Cihaz",
        ip_address="10.0.0.1",
        dnp3_address=1,
        dnp3_tcp_port=20000,
        poll_interval_sec=5,
        timeout_ms=3000,
        retry_count=2,
        signal_profile=model,
    )


def test_sinyalin_MODELI_degisince_config_version_DEGISIR():
    """En ince tuzak — bu olmadan degisiklik sahaya ULASMAZ.

    Sinyal `key`leri katalogda global benzersiz. Bir sinyali A modelinden B
    modeline tasimak DUZ listeyi (birlesimi) degistirmez: ayni anahtarlar, ayni
    sira. Yalnizca profil sozlugu degisir. `signals_by_profile` hash'e
    girmeseydi gateway 304 alir ve sinyalin artik baska modele ait oldugunu HIC
    ogrenmezdi — config_version'da duzeltilen sessiz sapmanin aynisi.
    """
    sinyal = _to_config_signal(_sinyal("master.current", MODEL_A, 30, 0))
    duz = [sinyal]

    once = compute_config_version(
        gateway_name="GW", batch_interval_sec=5, max_devices=200, is_active=True,
        devices=[_cihaz()], signals=duz, signals_by_profile={MODEL_A: [sinyal]},
    )
    sonra = compute_config_version(
        gateway_name="GW", batch_interval_sec=5, max_devices=200, is_active=True,
        devices=[_cihaz()], signals=duz, signals_by_profile={MODEL_B: [sinyal]},
    )
    assert once != sonra, (
        "sinyal baska modele tasindi ama config_version AYNI — gateway 304 alir "
        "ve degisikligi hic ogrenmez"
    )


def test_profil_sozlugu_AYNIYSA_surum_de_AYNI():
    """Gereksiz surum oynamasi tum sinyal listesini tel uzerinden tekrar yollar."""
    sinyal = _to_config_signal(_sinyal("master.current", MODEL_A, 30, 0))
    kw = dict(
        gateway_name="GW", batch_interval_sec=5, max_devices=200, is_active=True,
        devices=[_cihaz()], signals=[sinyal], signals_by_profile={MODEL_A: [sinyal]},
    )
    assert compute_config_version(**kw) == compute_config_version(**kw)


def test_profilsiz_cagri_ESKI_surumu_bozmaz():
    """`signals_by_profile=None` ile cagri patlamamali (geriye uyum)."""
    sinyal = _to_config_signal(_sinyal("master.current", MODEL_A, 30, 0))
    v = compute_config_version(
        gateway_name="GW", batch_interval_sec=5, max_devices=200, is_active=True,
        devices=[_cihaz()], signals=[sinyal],
    )
    assert isinstance(v, str) and len(v) == 12
