"""B5 — gateway v1.12.0 akilli oturum (smart session) sozlesmesi.

NE KILITLENIYOR
---------------
Akilli modda cihaz gunun buyuk bolumunu UYKUDA gecirir: raporunu gonderir,
baglantiyi kapatir, susar. Bu tasarim geregidir. Buradaki testlerin tamami
tek bir soruyu koruyor: "sessizlik" ile "kopma" birbirine karismasin.

Karistigi anda olusan hatalarin hepsi SESSIZDIR ve hepsi pahalidir:

  * `smart` + `listening` kombinasyonu kaydedilirse gateway kendi
    `continuous`una duser — saha calisir ama arayuzde "Akilli" yazar.
    Operatorun GORDUGU ile sahada OLAN ayrisir ve bu ayrisma hicbir yerde
    gorunmez.
  * Yeni alanlar `config_version` hash'ine girmezse gateway 304 alir ve
    politikayi HIC ogrenmez: ekranda akilli, sahada surekli.
  * Uyuyan cihaz `lost` sayilirsa gece boyunca tum saha kirmiziya boyanir ve
    GERCEK ariza o yiginin icinde kaybolur.

SOZLESME (gateway v1.12.0, main 84dc4956)
-----------------------------------------
    session_policy         : "continuous" | "smart", varsayilan continuous
    smart_max_silence_sec  : int | null, cihaz seviyesinde 60..2592000
    0                      : cihaz seviyesinde GECERSIZ (env'deki "devre
                             disi" anlamiyla cakisirdi)
    null/eksik             : "cihaz seviyesi override YOK" — gateway
                             DNP3_SMART_MAX_SILENCE_SEC env'ine duser
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (tum tablolari kaydeder)
from app.api import devices as devices_api
from app.api import gateways as gateways_api
from app.api.gateways import compute_config_version
from app.db.base import Base
from app.models.device import Device
from app.models.enums import CommunicationStatus, UserRole
from app.models.gateway import Gateway
from app.models.gateway_health import GatewayHealth
from app.models.user import User
from app.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from app.schemas.dnp3_extended import (
    SMART_MAX_SILENCE_MAX_SEC,
    SMART_MAX_SILENCE_MIN_SEC,
    Dnp3ExtendedSettings,
    dnp3_extended_to_store,
    effective_dnp3_extended,
    merge_dnp3_extended,
)
from app.schemas.gateway import GatewayConfigDevice, GatewayConfigSignal
from app.services import gateway_fleet_alarm
from app.services.gateway_health_service import (
    device_link_states,
    smart_counts,
    smart_idle_codes,
)
from app.services.gateway_staleness_watchdog import apply_link_states
from app.services.ingest_service import hash_gateway_token

GW = "GW-B5"


# ---------------------------------------------------------------------------
# Fixture'lar — proje konvansiyonu: router fonksiyonlari DOGRUDAN cagrilir
# (bkz. test_pole_master_kit.py; proje httpx'e bagli degil).
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture()
def kurulumcu() -> User:
    return User(id=1, username="kurulumcu", role=UserRole.INSTALLER)


@pytest.fixture()
def gateway(db) -> Gateway:
    gw = Gateway(
        code=GW,
        name="Saha Gateway",
        host="127.0.0.1",
        listen_port=8100,
        token="tok",
        token_hash=hash_gateway_token("tok"),
    )
    db.add(gw)
    db.flush()
    return gw


def _cihaz_ekle(db, kurulumcu, *, code: str, dnp3_extended: dict, port: int = 20001):
    return devices_api.create_device(
        payload=DeviceCreate(
            code=code,
            name=code,
            gateway_code=GW,
            ip_address="10.0.0.5",
            dnp3_outstation_port=port,
            latitude=39.0,
            longitude=35.0,
            dnp3_extended=Dnp3ExtendedSettings(**dnp3_extended),
        ),
        current_user=kurulumcu,
        db=db,
    )


def _saglik(**devices) -> str:
    return json.dumps({"devices": devices})


def _config_govdesi(db) -> dict:
    """Gateway'in GERCEKTEN aldigi govde."""
    yanit = gateways_api.get_gateway_config(
        gateway_code=GW,
        response=Response(),
        db=db,
        x_gateway_token="tok",
        x_gateway_code=None,
        x_gateway_instance_id=None,
        x_request_id=None,
        if_none_match=None,
    )
    return json.loads(yanit.body)


# ---------------------------------------------------------------------------
# B5-01 / B5-15 — geriye uyum
# ---------------------------------------------------------------------------


def test_B5_01_alani_olmayan_eski_kayit_continuous_okunur():
    """Yeni alanlari HIC bilmeyen bir kayit `continuous` + `None` olmali.

    Sahadaki her cihaz bu durumda; varsayilan yanlis secilirse yukseltmenin
    ertesi sabahi tum filo akilli moda gecmis olurdu.
    """
    eski = {
        "ip_endpoint_type": "listening",
        "master_ip_address": "10.0.0.1",
        "master_ip_port": 20002,
        "master_address": 100,
    }
    ayar = merge_dnp3_extended(eski)
    assert ayar.session_policy == "continuous"
    assert ayar.smart_max_silence_sec is None


def test_B5_15_eski_kayitlar_OKUMA_yolunda_gecerli_kalir(db, gateway, kurulumcu):
    """Yeni alanlar ZORUNLU olmamali: yukseltmeden onceki sozluk okunabilmeli.

    Aksi halde surum atlandigi anda cihaz listesi ucundan 500 donerdi ve
    arayuzde "sunucuya ulasilamadi" gorunurdu.
    """
    cihaz = _cihaz_ekle(
        db, kurulumcu, code="DEV-ESKI", dnp3_extended={"ip_endpoint_type": "initiating"}
    )
    # Yukseltme oncesi diskte duran sozlugun birebir hali.
    kayit = db.get(Device, cihaz.id)
    kayit.dnp3_extended = {"ip_endpoint_type": "initiating", "master_address": 100}
    db.flush()

    okunan = DeviceRead.model_validate(kayit)
    assert okunan.dnp3_extended.session_policy == "continuous"
    assert okunan.dnp3_extended.smart_max_silence_sec is None


def test_B5_15_gondermeyen_istemci_diske_yeni_alan_YAZDIRMAZ():
    """Sessiz-yazim korumasi yeni alanlar icin de gecerli.

    Aksi halde her kayit islemi, operatorun hic dokunmadigi bir politikayi
    diske SABITLERDI — `master_address` felaketi tam olarak boyle olmustu.
    """
    yazilacak = dnp3_extended_to_store(Dnp3ExtendedSettings(master_ip_address="10.0.0.1"))
    assert "session_policy" not in yazilacak
    assert "smart_max_silence_sec" not in yazilacak


# ---------------------------------------------------------------------------
# B5-02 / B5-03 / B5-04 — kombinasyon dogrulamasi
# ---------------------------------------------------------------------------


def test_B5_02_smart_initiating_kabul_edilir_ve_diske_yazilir(db, gateway, kurulumcu):
    cihaz = _cihaz_ekle(
        db,
        kurulumcu,
        code="DEV-SMART",
        dnp3_extended={
            "ip_endpoint_type": "initiating",
            "session_policy": "smart",
            "smart_max_silence_sec": 93600,
        },
    )
    saklanan = db.get(Device, cihaz.id).dnp3_extended
    assert saklanan["session_policy"] == "smart"
    assert saklanan["smart_max_silence_sec"] == 93600


def test_B5_02_gateway_config_payloadinda_smart_yayinlanir(db, gateway, kurulumcu):
    """Ayarin diskte durmasi yetmez — gateway'e GIDEN sozlukte olmali.

    Gercek uc nokta cagriliyor (sema elle kurulmuyor): payload'i insa eden
    kod yolu ile testin dogruladigi kod yolu AYNI olmali.
    """
    _cihaz_ekle(
        db,
        kurulumcu,
        code="DEV-SMART",
        dnp3_extended={
            "ip_endpoint_type": "initiating",
            "session_policy": "smart",
            "smart_max_silence_sec": 93600,
        },
    )
    govde = _config_govdesi(db)
    cihaz = next(d for d in govde["devices"] if d["code"] == "DEV-SMART")
    assert cihaz["session_policy"] == "smart"
    assert cihaz["smart_max_silence_sec"] == 93600
    # Mevcut davranis KORUNUYOR: initiating portu hala gateway blogundan
    # deterministik atanir, akilli mod bunu degistirmez.
    assert cihaz["ip_endpoint_type"] == "initiating"
    assert cihaz["master_ip_port"] == gateway.initiating_port_base


def test_B5_01_yeni_alanlari_olmayan_cihaz_payloadda_continuous_gider(
    db, gateway, kurulumcu
):
    """Sahadaki mevcut cihazlarin tamami bu yoldan gecer."""
    _cihaz_ekle(
        db, kurulumcu, code="DEV-ESKI", dnp3_extended={"ip_endpoint_type": "listening"}
    )
    cihaz = next(
        d for d in _config_govdesi(db)["devices"] if d["code"] == "DEV-ESKI"
    )
    assert cihaz["session_policy"] == "continuous"
    assert cihaz["smart_max_silence_sec"] is None


def test_B5_03_serializer_gecersiz_kaydi_continuous_yayinlar(db, gateway, kurulumcu):
    """Derinlemesine savunma: dogrulamadan ONCE yazilmis bir kayit.

    Gateway o kombinasyonda kendi `continuous`una duser — yani saha
    kirilmaz. Ama backend'in GONDERDIGI ile gateway'in UYGULADIGI ayrisir ve
    ayrisma hicbir yerde gorunmez. Yayinda kapatiliyor.
    """
    cihaz = _cihaz_ekle(
        db, kurulumcu, code="DEV-BOZUK", dnp3_extended={"ip_endpoint_type": "listening"}
    )
    db.get(Device, cihaz.id).dnp3_extended = {
        "ip_endpoint_type": "listening",
        "session_policy": "smart",
    }
    db.flush()

    yayin = next(
        d for d in _config_govdesi(db)["devices"] if d["code"] == "DEV-BOZUK"
    )
    assert yayin["session_policy"] == "continuous", (
        "gecersiz kombinasyon gateway'e oldugu gibi gitti"
    )


def test_B5_03_smart_listening_reddedilir(db, gateway, kurulumcu):
    """Uykudaki cihaza gateway BAGLANAMAZ; kombinasyon uretildigi yerde durur."""
    with pytest.raises(HTTPException) as exc:
        _cihaz_ekle(
            db,
            kurulumcu,
            code="DEV-KOTU",
            dnp3_extended={
                "ip_endpoint_type": "listening",
                "session_policy": "smart",
            },
        )
    assert exc.value.status_code == 422
    assert "initiating" in str(exc.value.detail)


def test_B5_04_kismi_PATCH_initiating_listeninge_cevrilemez(db, gateway, kurulumcu):
    """ASIL TUZAK: govde yalnizca `ip_endpoint_type` tasir.

    Model uzerinde dogrulama yapan bir tasarim bunu KACIRIRDI — gelen
    sozlukte `session_policy` yok, model varsayilani `continuous` gorunur ve
    yasak kombinasyon sessizce diske yazilirdi.
    """
    _cihaz_ekle(
        db,
        kurulumcu,
        code="DEV-SMART",
        dnp3_extended={"ip_endpoint_type": "initiating", "session_policy": "smart"},
    )
    with pytest.raises(HTTPException) as exc:
        devices_api.update_device(
            device_code="DEV-SMART",
            payload=DeviceUpdate(
                dnp3_extended=Dnp3ExtendedSettings(ip_endpoint_type="listening")
            ),
            current_user=kurulumcu,
            db=db,
        )
    assert exc.value.status_code == 422


def test_B5_04_ayni_istekte_continuousa_donen_PATCH_KABUL_edilir(db, gateway, kurulumcu):
    """Arayuzun tercih ettigi davranis: politikayi ayni govdede sifirla.

    Form uc nokta tipini `listening` yaptiginda politikayi `continuous`a
    cekiyor; bu iki alanli govdenin GECMESI gerekir, aksi halde arayuz kendi
    duzelttigi seyden 422 alirdi.
    """
    _cihaz_ekle(
        db,
        kurulumcu,
        code="DEV-SMART",
        dnp3_extended={"ip_endpoint_type": "initiating", "session_policy": "smart"},
    )
    guncel = devices_api.update_device(
        device_code="DEV-SMART",
        payload=DeviceUpdate(
            dnp3_extended=Dnp3ExtendedSettings(
                ip_endpoint_type="listening", session_policy="continuous"
            )
        ),
        current_user=kurulumcu,
        db=db,
    )
    saklanan = db.get(Device, guncel.id).dnp3_extended
    assert saklanan["ip_endpoint_type"] == "listening"
    assert saklanan["session_policy"] == "continuous"


def test_B5_04_yalnizca_politika_gonderen_PATCH_diskteki_endpointi_kullanir():
    """Ters yon: diskte `initiating` varken yalnizca `session_policy=smart`
    gonderen GECERLI istek reddedilmemeli."""
    efektif = effective_dnp3_extended(
        {"ip_endpoint_type": "initiating"},
        Dnp3ExtendedSettings(session_policy="smart"),
    )
    assert efektif.ip_endpoint_type == "initiating"
    assert efektif.session_policy == "smart"


def test_B5_03_alakasiz_PATCH_eski_kaydi_dogrulamaya_sokmaz(db, gateway, kurulumcu):
    """Yalnizca `name` degistiren bir istek DNP3 dogrulamasina takilmamali."""
    cihaz = _cihaz_ekle(
        db, kurulumcu, code="DEV-1", dnp3_extended={"ip_endpoint_type": "listening"}
    )
    # Dogrulamadan ONCE yazilmis gecersiz bir kaydi taklit et.
    db.get(Device, cihaz.id).dnp3_extended = {
        "ip_endpoint_type": "listening",
        "session_policy": "smart",
    }
    db.flush()
    guncel = devices_api.update_device(
        device_code="DEV-1",
        payload=DeviceUpdate(name="Yeni ad"),
        current_user=kurulumcu,
        db=db,
    )
    assert guncel.name == "Yeni ad"


# ---------------------------------------------------------------------------
# B5-05 .. B5-09 — sessizlik esigi araligi
# ---------------------------------------------------------------------------


def test_B5_05_null_esik_gecerli_ve_payloada_null_gider():
    ayar = Dnp3ExtendedSettings(ip_endpoint_type="initiating", session_policy="smart")
    assert ayar.smart_max_silence_sec is None
    cihaz = GatewayConfigDevice(
        code="D",
        name="D",
        ip_address="10.0.0.5",
        dnp3_address=1,
        dnp3_tcp_port=20000,
        poll_interval_sec=5,
        timeout_ms=3000,
        retry_count=2,
        signal_profile="p",
        session_policy="smart",
        smart_max_silence_sec=ayar.smart_max_silence_sec,
    )
    assert cihaz.model_dump()["smart_max_silence_sec"] is None


@pytest.mark.parametrize("deger", [SMART_MAX_SILENCE_MIN_SEC, SMART_MAX_SILENCE_MAX_SEC])
def test_B5_06_B5_07_sinir_degerler_kabul(deger):
    assert Dnp3ExtendedSettings(smart_max_silence_sec=deger).smart_max_silence_sec == deger


@pytest.mark.parametrize("deger", [59, SMART_MAX_SILENCE_MAX_SEC + 1])
def test_B5_08_B5_09_aralik_disi_reddedilir(deger):
    with pytest.raises(ValidationError):
        Dnp3ExtendedSettings(smart_max_silence_sec=deger)


def test_B5_05_sifir_cihaz_seviyesinde_GECERSIZ():
    """0, ENV tarafindaki "devre disi" anlamidir.

    Cihaz alaninda da kabul etmek iki farkli anlami tek alana yuklerdi:
    "esigi kapat" ile "ozel esigim yok" ayirt edilemez olurdu. Cihazda
    ikincisinin yolu None'dir.
    """
    with pytest.raises(ValidationError):
        Dnp3ExtendedSettings(smart_max_silence_sec=0)


def test_bool_tamsayi_olarak_kabul_EDILMEZ():
    """`True` sessizce 1'e donerse esik "1 saniye" olur ve akilli moddaki
    cihaz her raporundan sonra kayip sayilirdi."""
    with pytest.raises(ValidationError):
        Dnp3ExtendedSettings(smart_max_silence_sec=True)


def test_gecersiz_politika_degeri_reddedilir():
    """`auto` gibi bir ucuncu deger sozlesmede YOK."""
    with pytest.raises(ValidationError):
        Dnp3ExtendedSettings(session_policy="auto")


# ---------------------------------------------------------------------------
# B5-10 / B5-11 — config_version
# ---------------------------------------------------------------------------


def _sinyal() -> GatewayConfigSignal:
    return GatewayConfigSignal(
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


def _cihaz(**over) -> GatewayConfigDevice:
    alanlar = dict(
        code="DEV-1",
        name="Cihaz 1",
        ip_address="10.0.0.5",
        dnp3_address=10,
        dnp3_tcp_port=20000,
        master_address=100,
        ip_endpoint_type="initiating",
        master_ip_port=20100,
        poll_interval_sec=5,
        timeout_ms=3000,
        retry_count=2,
        signal_profile="horstmann_sn2_fixed",
    )
    alanlar.update(over)
    return GatewayConfigDevice(**alanlar)


def _surum(cihazlar) -> str:
    return compute_config_version(
        gateway_name="GW",
        batch_interval_sec=5,
        max_devices=200,
        is_active=True,
        devices=cihazlar,
        signals=[_sinyal()],
    )


def test_B5_10_politika_degisince_config_version_DEGISIR():
    """Degismezse gateway 304 alir ve politikayi HIC ogrenmez: ekranda
    akilli, sahada surekli."""
    taban = _surum([_cihaz(session_policy="continuous")])
    assert _surum([_cihaz(session_policy="smart")]) != taban


def test_B5_11_esik_degisince_config_version_DEGISIR():
    taban = _surum([_cihaz(session_policy="smart", smart_max_silence_sec=None)])
    a = _surum([_cihaz(session_policy="smart", smart_max_silence_sec=93600)])
    b = _surum([_cihaz(session_policy="smart", smart_max_silence_sec=3600)])
    assert a != taban
    assert a != b


def test_hash_ozel_kod_ile_DEGIL_payloaddan_turuyor():
    """Yeni alanlar hash'e elle EKLENMEDI; `model_dump()` uzerinden dogal
    olarak giriyor. Biri hash'i tekrar elle listeye baglarsa bu test kirilir."""
    kaynak = inspect.getsource(compute_config_version)
    assert "session_policy" not in kaynak, (
        "hash hesabinda alan adi elle geciyor — payload'dan turetme ozelligi "
        "bozulmus demektir"
    )


# ---------------------------------------------------------------------------
# B5-12 / B5-13 / B5-14 — saglik: uyku != kopma
# ---------------------------------------------------------------------------


def test_B5_12_smart_idle_offline_SAYILMAZ():
    """Uyku saglikli durumdur; `offline` esleseydi filo gece boyunca kirmizi
    olurdu ve gercek ariza o yiginda kaybolurdu."""
    ham = _saglik(states={"d1": "smart_idle", "d2": "lost", "d3": "connected"})
    durumlar = device_link_states(ham)
    assert "d1" not in durumlar, "smart_idle bir haberlesme durumuna eslendi"
    assert durumlar["d2"] == "offline"
    assert durumlar["d3"] == "online"


def test_B5_12_smart_idle_cihaz_kodlari_ayrica_okunabilir():
    ham = _saglik(states={"d1": "smart_idle", "d2": "connected"})
    assert smart_idle_codes(ham) == {"d1"}


def test_B5_14_smart_lost_ve_smart_idle_sayaclari_okunur():
    ham = _saglik(total=10, online=0, lost=0, smart_idle=9, smart_lost=1)
    sayac = smart_counts(ham)
    assert sayac["smart_idle"] == 9
    assert sayac["smart_lost"] == 1


def test_B5_14_alan_gondermeyen_eski_gateway_sifir_doner():
    """v1.11.x bu alanlari gondermez; "akilli cihaz yok" dogru cevaptir."""
    assert smart_counts(_saglik(total=3, online=3, lost=0)) == {
        "smart_idle": 0,
        "smart_lost": 0,
    }
    assert smart_counts(None) == {"smart_idle": 0, "smart_lost": 0}
    assert smart_counts("{bozuk json") == {"smart_idle": 0, "smart_lost": 0}


def test_B5_13_filo_alarmi_uyuyan_cihazlar_icin_tetiklenmez():
    """online=0, smart_idle>0, lost=0 -> filo SAGLIKLI.

    Bu, akilli modun normal gece gorunumu. Uyari uretirse bildirim merkezi
    her gece dolar ve gercek filo kopmasi o yiginda okunmaz hale gelir.
    """
    assert gateway_fleet_alarm.degraded(total=10, lost=0, esik=0.5) is False


def test_B5_13_toplu_offline_dusurmesi_uyku_varken_CALISMAZ(db, gateway):
    """Sayi bazli guvenli cikarim akilli modda GECERSIZDIR.

    `online=0` artik "hicbir cihaz ayakta degil" demek degil; cihazlar
    raporunu gonderip baglantiyi kapatmis olabilir. Hangisinin uyudugunu
    hangisinin koptugunu SAYIDAN ayirt edemeyiz — emin olmadan dokunmuyoruz.
    """
    d = Device(
        code="DEV-1",
        name="DEV-1",
        gateway_code=GW,
        ip_address="10.0.0.5",
        latitude=39.0,
        longitude=35.0,
        communication_status=CommunicationStatus.ONLINE,
    )
    db.add(d)
    db.add(
        GatewayHealth(
            gateway_code=GW,
            status="ok",
            devices_total=5,
            devices_online=0,
            devices_lost=2,
            raw_json=_saglik(total=5, online=0, lost=2, smart_idle=3),
            reported_at=datetime.now(timezone.utc),
        )
    )
    db.flush()

    apply_link_states(db)
    assert db.get(Device, d.id).communication_status == CommunicationStatus.ONLINE, (
        "uyuyan cihaz bulunan bir gateway'de toplu OFFLINE dusurmesi calisti"
    )


def test_uyku_YOKKEN_toplu_offline_dusurmesi_CALISIR(db, gateway):
    """Karsi kontrol: koruma, gercek toplu kopmayi KACIRMAMALI.

    Bu davranis sahada telemetri kuyrugu bosaldiginda cihazlarin ONLINE
    takili kalmasini duzeltmisti; akilli mod korumasi onu iptal etmemeli.
    """
    d = Device(
        code="DEV-2",
        name="DEV-2",
        gateway_code=GW,
        ip_address="10.0.0.6",
        latitude=39.0,
        longitude=35.0,
        communication_status=CommunicationStatus.ONLINE,
    )
    db.add(d)
    db.add(
        GatewayHealth(
            gateway_code=GW,
            status="degraded",
            devices_total=5,
            devices_online=0,
            devices_lost=5,
            raw_json=_saglik(total=5, online=0, lost=5),
            reported_at=datetime.now(timezone.utc),
        )
    )
    db.flush()

    apply_link_states(db)
    assert db.get(Device, d.id).communication_status == CommunicationStatus.OFFLINE


# ---------------------------------------------------------------------------
# Denetim
# ---------------------------------------------------------------------------


def test_denetim_kaydi_eski_yeni_gosterir(db, gateway, kurulumcu):
    """"dnp3_extended degisti" cevap degildi: cihaz ne zaman akilli moda
    alindi, esigi kim degistirdi?"""
    from app.models.system_event import SystemEvent

    _cihaz_ekle(
        db,
        kurulumcu,
        code="DEV-SMART",
        dnp3_extended={"ip_endpoint_type": "initiating"},
    )
    devices_api.update_device(
        device_code="DEV-SMART",
        payload=DeviceUpdate(
            dnp3_extended=Dnp3ExtendedSettings(
                session_policy="smart", smart_max_silence_sec=93600
            )
        ),
        current_user=kurulumcu,
        db=db,
    )
    olay = (
        db.query(SystemEvent)
        .filter(SystemEvent.event_type == "device_updated")
        .order_by(SystemEvent.id.desc())
        .first()
    )
    fark = (json.loads(olay.metadata_json or "{}")).get("dnp3_changes") or {}
    assert fark["session_policy"] == {"old": "continuous", "new": "smart"}
    assert fark["smart_max_silence_sec"] == {"old": None, "new": 93600}
