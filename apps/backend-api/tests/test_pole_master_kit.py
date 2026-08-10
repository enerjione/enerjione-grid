"""Horstmann Pole Master Kit — TEK fiziksel cihaz, UC sanal set.

NE KILITLENIYOR
---------------
Kit tek bir DNP3 outstation'dir ama uzerindeki 9 uydu ucerli setler halinde
sahada BIRBIRINDEN BAGIMSIZ noktalara kelepcelenir. Bu yuzden veri modelinde
bir kit = 1 fiziksel satir + N sanal set satiri olarak durur ve zincirin her
halkasi (sinyal katalogu, bolme haritasi, lisans sayimi, uc nokta kontrolu,
faz cikarimi, gateway poll listesi, komut yonlendirme) bu ikiligi DOGRU
tasimak zorunda.

BU DOSYANIN SINAVI: buradaki hatalarin neredeyse tamami SESSIZDIR.

  * bolme haritasi yanlissa uc setin telemetrisi tek cihaza duser, ariza
    araligi hep fazla genis cikar — hicbir hata olusmaz;
  * sanal setler gateway'e poll hedefi olarak sizarsa ayni outstation'a uc
    TCP oturumu acilir ve belirti "ag kararsiz" gorunur;
  * set kayitlari lisans kotasindan dusulurse tek kit alan musteri uc slot
    kaybeder;
  * faz haritasinda `sat03` yoksa setin ucuncu uydusunun gordugu ariza
    `phase=NULL` kalir ve faz dagilimi raporu o arizalari HIC saymaz;
  * komut sanal sete kuyruklanirsa gateway'e hic verilmeyen bir cihaz koduyla
    kuyrukta oturur ve hicbir yere ulasmaz.

NEDEN TestClient DEGIL: proje httpx'e bagli degil (bkz.
`test_route_auth_boundary.py` / `test_signal_historian_yonetimi.py`). Router
fonksiyonlari duz fonksiyondur ve dogrudan cagrilir — HTTP katmani atlanmis
olmuyor, yalnizca tasima katmani atlaniyor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (tum tablolari kaydeder)
from app.api import devices as devices_api
from app.api import gateways as gateways_api
from app.api import internal as internal_api
from app.core.config import settings
from app.data.device_models import (
    resolve_subunit_satellites,
    DEFAULT_MODEL,
    POLE_MASTER_KIT_MODEL,
    PMK_SET_MODEL,
    SATELLITES_PER_SET,
    SET_UNIT_SOURCES,
    satellite_source_to_set_index,
    set_satellite_numbers,
    subunit_source_map,
)
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.enums import UserRole
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.models.user import User
from app.schemas.device import DeviceCreate, DeviceUpdate
from app.services import device_command_service, device_kit_service, license_service
from app.services.fault_snapshot import resolve_source_phase
from app.services.ingest_service import hash_gateway_token
from app.services.signal_catalog_seed import seed_default_signals

DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data"
KIT_JSON = DATA_DIR / "horstmann_pole_master_kit_signals.json"
SET_JSON = DATA_DIR / "horstmann_pmk_set_signals.json"

GW = "GW-1"
GW_TOKEN = "gw-test-token"


def _yukle(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _olcum_adi(key: str) -> str:
    """`sat04.actual_current` -> `actual_current`."""
    return key.split(".", 1)[1] if "." in key else key


# ---------------------------------------------------------------------------
# Fixture'lar
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
        token=GW_TOKEN,
        token_hash=hash_gateway_token(GW_TOKEN),
    )
    db.add(gw)
    db.flush()
    return gw


@pytest.fixture(autouse=True)
def lisans_acik(monkeypatch):
    """Lisans dosyasi testlerde YOK; kota kapisi bu dosyanin konusu degil.

    Kotanin KENDISI ayri bir testte (`test_lisans_...`) gercek fonksiyonla
    olculuyor — burada yalnizca cihaz eklemenin onu aciliyor.
    """
    monkeypatch.setattr(
        license_service, "lock_and_assert_device_capacity", lambda db: None
    )


def _kit_ekle(
    db,
    kurulumcu,
    *,
    code: str = "PMK-001",
    set_count: int | None = 2,
    ip: str = "10.0.0.9",
    port: int = 20001,
) -> Device:
    return devices_api.create_device(
        payload=DeviceCreate(
            code=code,
            name=f"Kit {code}",
            model=POLE_MASTER_KIT_MODEL,
            gateway_code=GW,
            ip_address=ip,
            dnp3_outstation_port=port,
            latitude=39.9,
            longitude=32.8,
            satellite_set_count=set_count,
        ),
        current_user=kurulumcu,
        db=db,
    )


def _setler(db, kit: Device) -> list[Device]:
    return device_kit_service.list_subunits(db, kit.id)


# ---------------------------------------------------------------------------
# 1) SINYAL KATALOGU — adres haritasi VERIDIR, sessizce bozulamaz
# ---------------------------------------------------------------------------


def test_kit_katalogu_484_nokta_ve_10_kaynak() -> None:
    """Kit tek outstation ama uzerinde master + 9 uydu var.

    Sayinin kendisi degil, EKSIKSIZLIGI onemli: bir uydunun sinyalleri
    dusseydi o uyduya bagli set hicbir telemetri almaz ama arayuzde saglikli
    gorunurdu.
    """
    satirlar = _yukle(KIT_JSON)
    assert len(satirlar) == 484

    kaynaklar = {s["source"] for s in satirlar}
    beklenen = {"master"} | {f"sat{n:02d}" for n in range(1, 10)}
    assert kaynaklar == beklenen


def test_kit_katalogunda_DNP3_adresleri_TEKIL() -> None:
    """(kaynak, obje grubu, index) ucluleri cakisamaz.

    Cakisan iki satir ayni DNP3 noktasini iki farkli buyukluk olarak okur;
    hangisinin kazandigi sira bagimlidir ve hicbir yerde loglanmaz.
    """
    ucluler = [
        (s["source"], s["dnp3_object_group"], s["dnp3_index"]) for s in _yukle(KIT_JSON)
    ]
    tekrar = {u for u in ucluler if ucluler.count(u) > 1}
    assert not tekrar, f"ayni DNP3 adresi birden fazla sinyalde: {sorted(tekrar)}"


def test_kit_katalogunda_iec104_IOA_cakismiyor() -> None:
    """IOA cakisirsa SCADA'da son yazan deger digerini ezer — sessizce.

    NULL IOA "yayinlanmaz" demektir (bkz. SignalCatalog.iec104_ioa); tekillik
    yalnizca ATANMIS adresler icin aranir.
    """
    ioa = [s["iec104_ioa"] for s in _yukle(KIT_JSON) if s.get("iec104_ioa") is not None]
    tekrar = {i for i in ioa if ioa.count(i) > 1}
    assert not tekrar, f"ayni IOA birden fazla sinyalde: {sorted(tekrar)}"


def test_set_katalogu_144_nokta_ve_yalnizca_uc_uydu() -> None:
    """Sanal set UC uydudur; `master` kit seviyesinde kalir.

    Set katalogunda master sinyali gorunseydi kit seviyesindeki olcumler uc
    sanal cihaza da yazilmaya calisilir ve "bir giren = bir cikan" kurali
    kirilirdi (bkz. device_kit_service modul docstring'i).
    """
    satirlar = _yukle(SET_JSON)
    assert len(satirlar) == 144
    assert {s["source"] for s in satirlar} == set(SET_UNIT_SOURCES)


# ---------------------------------------------------------------------------
# 2) BOLME YALNIZCA KAYNAK ADINI DEGISTIRIR
# ---------------------------------------------------------------------------


def test_set_olcumleri_kitteki_HER_uydunun_olcumleriyle_AYNI() -> None:
    """Set katalogu, fiziksel uydunun ADI DEGISMIS halidir; baska bir sey degil.

    Kume ayrisirsa bolme sirasinda bir sinyal HEDEFSIZ kalir: tag-engine
    `sat07.x` -> `sat01.x` yeniden yazar, ama `sat01.x` set katalogunda yoksa
    deger hicbir yerde gorunmez. Ne hata, ne uyari.
    """
    set_satirlari = _yukle(SET_JSON)
    kit_satirlari = _yukle(KIT_JSON)

    set_adlari = {
        src: {_olcum_adi(s["key"]) for s in set_satirlari if s["source"] == src}
        for src in SET_UNIT_SOURCES
    }
    # Setin uc uydusu kendi arasinda da ayni kumeyi tasimali.
    assert set_adlari["sat01"] == set_adlari["sat02"] == set_adlari["sat03"]

    for n in range(1, 10):
        fiziksel = f"sat{n:02d}"
        kit_adlari = {
            _olcum_adi(s["key"]) for s in kit_satirlari if s["source"] == fiziksel
        }
        sanal = subunit_source_map(satellite_source_to_set_index(fiziksel))[fiziksel]
        assert kit_adlari == set_adlari[sanal], (
            f"{fiziksel} -> {sanal} eslemesinde olcum kumesi ayrisiyor: "
            f"yalniz kitte={sorted(kit_adlari - set_adlari[sanal])}, "
            f"yalniz sette={sorted(set_adlari[sanal] - kit_adlari)}"
        )


# ---------------------------------------------------------------------------
# 3) BOLME HARITASI — tag-engine'in tek dogruluk kaynagi
# ---------------------------------------------------------------------------


def test_subunit_source_map_set_bazinda_dogru() -> None:
    assert subunit_source_map(1) == {"sat01": "sat01", "sat02": "sat02", "sat03": "sat03"}
    assert subunit_source_map(2) == {"sat04": "sat01", "sat05": "sat02", "sat06": "sat03"}
    assert subunit_source_map(3) == {"sat07": "sat01", "sat08": "sat02", "sat09": "sat03"}


def test_set_1_ozel_durum_DEGIL() -> None:
    """Kimlik eslemesi de acikca uretilir; "set 1 ozel" ilk kirilan sey olurdu."""
    assert set_satellite_numbers(1) == (1, 2, 3)
    assert len(subunit_source_map(1)) == SATELLITES_PER_SET


def test_bolme_eslemesi_BIJEKTIF() -> None:
    """Uc setin fiziksel kaynaklari TEKIL, sanal kaynaklari TAM ORTUSUR.

    Bir fiziksel uydu iki sete duserse ayni olcum iki cihaza yazilir ve
    `processed_messages` tekil kisiti ikinci satiri reddeder — mesaj SONSUZA
    KADAR yeniden teslim edilir. Kacan bir uydu ise sessizce kaybolur.
    """
    fiziksel: list[str] = []
    for set_index in (1, 2, 3):
        esleme = subunit_source_map(set_index)
        fiziksel.extend(esleme.keys())
        assert sorted(esleme.values()) == sorted(SET_UNIT_SOURCES), (
            f"set {set_index} uc sanal kaynagi tam ortmuyor"
        )
    assert sorted(fiziksel) == [f"sat{n:02d}" for n in range(1, 10)]
    assert len(set(fiziksel)) == 9, "bir fiziksel uydu birden fazla sete dusuyor"


def test_kaynak_adindan_set_numarasi() -> None:
    for n, beklenen in ((1, 1), (3, 1), (4, 2), (6, 2), (7, 3), (9, 3)):
        assert satellite_source_to_set_index(f"sat{n:02d}") == beklenen
    # Uydu olmayan kaynak AYNEN gecmeli (None = "dokunma").
    assert satellite_source_to_set_index("master") is None
    assert satellite_source_to_set_index("sat") is None


# ---------------------------------------------------------------------------
# 4) SET SAYISI — varsayilan UYDURULMAZ
# ---------------------------------------------------------------------------


def test_kit_olmayan_modelde_set_sayisi_None() -> None:
    """SN2'de gonderilse bile yok sayilir."""
    assert device_kit_service.normalize_set_count(DEFAULT_MODEL, None) is None
    assert device_kit_service.normalize_set_count(DEFAULT_MODEL, 2) is None
    assert device_kit_service.normalize_set_count(None, 3) is None


def test_kit_modelinde_set_sayisi_ZORUNLU() -> None:
    """Varsayilan uydurmak (orn. hep 3) kullanilmayan iki set kaydi acar ve o
    setler hatta "veri gelmiyor" diye gorunur."""
    with pytest.raises(ValueError):
        device_kit_service.normalize_set_count(POLE_MASTER_KIT_MODEL, None)


@pytest.mark.parametrize("gecersiz", [0, 4, -1, 9])
def test_set_sayisi_araligi_disi_REDDEDILIR(gecersiz: int) -> None:
    with pytest.raises(ValueError):
        device_kit_service.normalize_set_count(POLE_MASTER_KIT_MODEL, gecersiz)


@pytest.mark.parametrize("gecerli", [1, 2, 3])
def test_set_sayisi_1_3_kabul(gecerli: int) -> None:
    assert device_kit_service.normalize_set_count(POLE_MASTER_KIT_MODEL, gecerli) == gecerli


# ---------------------------------------------------------------------------
# 5) API ILE KIT OLUSTURMA — setler AYNI transaction'da uretilir
# ---------------------------------------------------------------------------


def test_kit_eklenince_set_kayitlari_da_URETILIR(db, gateway, kurulumcu) -> None:
    """Ayri bir istege birakilsaydi yarim kalmis bir kit (setleri olmayan
    fiziksel kayit) birakma riski olurdu: telemetriyi hicbir yere yazamaz."""
    kit = _kit_ekle(db, kurulumcu, set_count=2)

    tum = db.scalars(select(Device).order_by(Device.code.asc())).all()
    assert [d.code for d in tum] == ["PMK-001", "PMK-001-S1", "PMK-001-S2"]

    setler = _setler(db, kit)
    assert [d.model for d in setler] == [PMK_SET_MODEL, PMK_SET_MODEL]
    assert [d.parent_device_id for d in setler] == [kit.id, kit.id]
    assert [d.subunit_index for d in setler] == [1, 2]


def test_her_setin_AYRI_iec104_common_address_i_var(db, gateway, kurulumcu) -> None:
    """Ortak CA verilseydi uc setin ayni IOA'lari birbirini ezerdi ve
    carpisma hicbir yerde loglanmazdi."""
    kit = _kit_ekle(db, kurulumcu, set_count=3)
    adresler = [kit.iec104_common_address] + [
        d.iec104_common_address for d in _setler(db, kit)
    ]
    assert all(a is not None for a in adresler), "CA atanmamis kayit var"
    assert len(set(adresler)) == len(adresler), f"CA cakisiyor: {adresler}"


def test_kit_satiri_set_sayisini_BILDIRIYOR(db, gateway, kurulumcu) -> None:
    """`satellite_set_count` kolon degil turetilmis alan; arayuz onu okur."""
    kit = _kit_ekle(db, kurulumcu, set_count=2)
    okunan = device_kit_service.annotate_one(db, kit)
    assert okunan.satellite_set_count == 2

    ilk_set = _setler(db, kit)[0]
    okunan_set = device_kit_service.annotate_one(db, ilk_set)
    assert okunan_set.parent_device_code == "PMK-001"
    assert okunan_set.satellite_set_count is None


def test_set_sayisi_verilmeden_kit_eklenemez(db, gateway, kurulumcu) -> None:
    with pytest.raises(HTTPException) as hata:
        _kit_ekle(db, kurulumcu, set_count=None)
    assert hata.value.status_code == 422


def test_cok_uzun_kit_kodu_kit_HIC_olusturulmadan_reddedilir(db, gateway, kurulumcu) -> None:
    """Yarim kalmis kit (fiziksel satir var, setleri yok) telemetriyi hicbir
    yere yazamaz ve arayuzde bos gorunur."""
    uzun = "P" * 49  # + "-S1" -> 52 karakter, String(50) sinirinin ustu
    with pytest.raises(HTTPException) as hata:
        _kit_ekle(db, kurulumcu, code=uzun, set_count=1)
    assert hata.value.status_code == 422
    assert db.scalar(select(Device).where(Device.code == uzun)) is None


# ---------------------------------------------------------------------------
# 6) UC NOKTA CAKISMASI — muafiyet YALNIZCA alt cihazlara
# ---------------------------------------------------------------------------


def test_ayni_uc_noktayi_paylasan_SETLER_cakisma_uretmez(db, gateway, kurulumcu) -> None:
    """Kitin setleri ayni outstation'i BILEREK paylasir ve gateway'e poll
    hedefi olarak HIC verilmez; tahliye dongusunu uretemezler.

    Kontrol kiti disladiginda geriye YALNIZCA setler kalir — muafiyet yoksa
    burasi 409 verir ve kit bir daha hic guncellenemez.
    """
    kit = _kit_ekle(db, kurulumcu, set_count=3)

    devices_api._require_unique_endpoint(
        db,
        gateway_code=GW,
        ip_address="10.0.0.9",
        port=20001,
        exclude_device_id=kit.id,
    )  # patlamamali


def test_kit_GUNCELLENEBILIYOR_setleri_kendisiyle_cakismiyor(db, gateway, kurulumcu) -> None:
    """Gercek akis: kitin adini degistirmek 409 vermemeli."""
    kit = _kit_ekle(db, kurulumcu, set_count=3)

    guncel = devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(name="Kit — yeni ad"),
        current_user=kurulumcu,
        db=db,
    )
    assert guncel.name == "Kit — yeni ad"


def test_BAGIMSIZ_ikinci_cihaz_ayni_uc_noktada_409_alir(db, gateway, kurulumcu) -> None:
    """Muafiyet kontrolun ASIL amacini bozmamali: iki BAGIMSIZ cihazin ayni
    uc noktaya ayarlanmasi karsilikli tahliye dongusu uretir (2026-08-01)."""
    _kit_ekle(db, kurulumcu, set_count=2)

    with pytest.raises(HTTPException) as hata:
        devices_api.create_device(
            payload=DeviceCreate(
                code="SN2-0001",
                name="Fider 1",
                model=DEFAULT_MODEL,
                gateway_code=GW,
                ip_address="10.0.0.9",
                dnp3_outstation_port=20001,
                latitude=39.0,
                longitude=35.0,
            ),
            current_user=kurulumcu,
            db=db,
        )
    assert hata.value.status_code == 409


# ---------------------------------------------------------------------------
# 7) LISANS — bir kit BIR donanimdir
# ---------------------------------------------------------------------------


def test_lisans_yalnizca_FIZIKSEL_kayitlari_sayar(db, gateway, kurulumcu) -> None:
    """Setler sayilsaydi tek kit alan musteri kotasindan UC slot yer ve
    "cihaz ekle" butonu satin aldigi seyle iliskisi olmayan bir sinirda
    kilitlenirdi."""
    _kit_ekle(db, kurulumcu, code="PMK-001", set_count=3)
    assert license_service.count_licensed_devices(db) == 1

    devices_api.create_device(
        payload=DeviceCreate(
            code="SN2-0001",
            name="Fider 1",
            model=DEFAULT_MODEL,
            gateway_code=GW,
            ip_address="10.0.0.10",
            dnp3_outstation_port=20001,
            latitude=39.0,
            longitude=35.0,
        ),
        current_user=kurulumcu,
        db=db,
    )
    assert license_service.count_licensed_devices(db) == 2
    # Toplam satir sayisi 5 (kit + 3 set + SN2) ama lisans 2 sayar.
    assert db.scalar(select(Device.id).where(Device.code == "PMK-001-S3")) is not None


# ---------------------------------------------------------------------------
# 8) SET SAYISI GUNCELLEME — artirma URETIR, azaltma SILER
# ---------------------------------------------------------------------------


def test_set_sayisi_artirilinca_eksik_setler_URETILIR(db, gateway, kurulumcu) -> None:
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    assert [d.subunit_index for d in _setler(db, kit)] == [1]

    devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(satellite_set_count=3),
        current_user=kurulumcu,
        db=db,
    )

    setler = _setler(db, kit)
    assert [d.code for d in setler] == ["PMK-001-S1", "PMK-001-S2", "PMK-001-S3"]
    # Var olan set YENIDEN URETILMEZ (telemetrisi ve hat yerlesimi kalmali).
    assert setler[0].subunit_index == 1


def test_set_sayisi_azaltilinca_fazla_setler_SILINIR(db, gateway, kurulumcu) -> None:
    """AZALTMA VERI SILER (telemetri, alarm, ariza gecmisi, hat yerlesimi) —
    ama fazladan acik kalan bir set telemetri almadan haritada saglikli
    gorunurdu; bu daha kotu."""
    kit = _kit_ekle(db, kurulumcu, set_count=3)
    assert len(_setler(db, kit)) == 3

    devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(satellite_set_count=1),
        current_user=kurulumcu,
        db=db,
    )

    assert [d.code for d in _setler(db, kit)] == ["PMK-001-S1"]
    assert db.scalar(select(Device.id).where(Device.code == "PMK-001-S3")) is None


# ---------------------------------------------------------------------------
# 9) FAZ ZINCIRI — modelin KENDI varsayilani kaybolamaz
# ---------------------------------------------------------------------------


def _set_cihazi(db, model: str = PMK_SET_MODEL) -> Device:
    d = Device(
        code="PMK-001-S1",
        name="Kit / Set 1",
        model=model,
        ip_address="10.0.0.9",
        latitude=39.0,
        longitude=35.0,
    )
    db.add(d)
    db.flush()
    return d


def test_set_modelinde_UC_UYDU_da_faz_uretir(db) -> None:
    """Hicbir ayar girilmemis olsa bile: `sat03` haritada olmasaydi setin
    ucuncu uydusunun gordugu ariza `phase=NULL` kalir, tek-faz/uc-faz ayrimi
    ve faz dagilimi raporu o arizalari HIC saymazdi. Hicbir hata da olusmazdi.
    """
    d = _set_cihazi(db)
    assert resolve_source_phase(db, device_id=d.id) == {
        "sat01": "a",
        "sat02": "b",
        "sat03": "c",
    }


def test_SN2_davranisi_BOZULMADI(db) -> None:
    """Ters yon: SN2'de hicbir katman konusmadiysa None donmeli — cagiran
    taraf kod varsayilanini kullanir (mevcut sozlesme)."""
    d = _set_cihazi(db, model=DEFAULT_MODEL)
    assert resolve_source_phase(db, device_id=d.id) is None
    assert resolve_source_phase(db) is None


def test_set_cihazinda_phase_sat03_ayari_KAZANIR(db) -> None:
    """Kelepceyi hangi faza takacagina sahadaki kisi karar verir."""
    d = _set_cihazi(db)
    d.phase_sat03 = "a"
    db.flush()

    esleme = resolve_source_phase(db, device_id=d.id)
    assert esleme["sat03"] == "a", "cihaz ayari model varsayilanini ezmedi"
    assert esleme["sat01"] == "a" and esleme["sat02"] == "b", (
        "dokunulmayan uniteler model varsayilaninda kalmali"
    )


def test_modele_ozel_faz_haritalari_SABITE_bagli() -> None:
    """`fault_inference` ve `fault_snapshot` model kodunu ELLE yaziyor.

    `PMK_SET_MODEL` degistiginde bu iki sozluk sessizce eslesmez olur: hicbir
    hata olusmaz, yalnizca setler `master/sat01/sat02` varsayilanina duser ve
    ucuncu uydunun arizasi faz uretmez.
    """
    from app.services.fault_inference import SOURCE_PHASE_BY_MODEL
    from app.services.fault_snapshot import _PHASE_FIELDS_BY_MODEL

    assert PMK_SET_MODEL in SOURCE_PHASE_BY_MODEL
    assert PMK_SET_MODEL in _PHASE_FIELDS_BY_MODEL


def test_set_cihazinda_phase_master_OKUNMAZ(db) -> None:
    """Kitte `master` bir olcum unitesi degil, ortak RTU'dur. Okunsaydi proje
    genelindeki "master = a fazi" varsayilani setin gercek uc fazini bozardi."""
    d = _set_cihazi(db)
    d.phase_master = "c"
    db.flush()

    esleme = resolve_source_phase(db, device_id=d.id)
    assert "master" not in esleme


# ---------------------------------------------------------------------------
# 10) SEED — anahtar (model, key); modeller birbirini EZMEZ
# ---------------------------------------------------------------------------


def _katalog(db, key: str) -> dict[str, int]:
    """model -> dnp3_index."""
    return {
        r.model: r.dnp3_index
        for r in db.scalars(select(SignalCatalog).where(SignalCatalog.key == key)).all()
    }


def test_ayni_anahtar_IKI_MODELDE_yan_yana_yasar(db) -> None:
    """`key` global tekil olsaydi ikinci model bu sinyali hic tanimlayamazdi."""
    seed_default_signals(db)

    satirlar = _katalog(db, "master.config_update")
    assert DEFAULT_MODEL in satirlar
    assert POLE_MASTER_KIT_MODEL in satirlar


def test_seed_TEKRAR_kosunca_modeller_birbirini_EZMIYOR(db) -> None:
    """Yalnizca `key` ile anahtarlamak, ayni adi paylasan iki modelden birinin
    satirini digerinin uzerine yazardi: seed her aciliste iki modeli sirayla
    "duzeltip" DNP3 indeksini yalpalatir, hicbir hata uretmezdi.

    `master.boost_mode` bu ayrimin gercek ornegi: SN2'de 26, kitte 30.
    """
    seed_default_signals(db)
    ilk = _katalog(db, "master.boost_mode")
    assert ilk == {DEFAULT_MODEL: 26, POLE_MASTER_KIT_MODEL: 30}, (
        "iki modelin indeksleri seed sonrasi ayrisik degil"
    )

    # Backend her acilista seed kosuyor — ikinci kosu ilkini bozmamali.
    seed_default_signals(db)
    assert _katalog(db, "master.boost_mode") == ilk


def test_seed_UC_MODELIN_de_katalogunu_yaziyor(db) -> None:
    """Kit ve set katalogu seed'e girmezse cihaz eklenir ama hicbir sinyali
    okunmaz — gateway o profil icin bos liste alir."""
    from sqlalchemy import func

    seed_default_signals(db)
    sayilar = {
        model: db.scalar(
            select(func.count(SignalCatalog.id)).where(SignalCatalog.model == model)
        )
        for model in (DEFAULT_MODEL, POLE_MASTER_KIT_MODEL, PMK_SET_MODEL)
    }
    assert sayilar == {
        DEFAULT_MODEL: 193,
        POLE_MASTER_KIT_MODEL: 484,
        PMK_SET_MODEL: 144,
    }


# ---------------------------------------------------------------------------
# 11) /internal/device-map — bolme kurali TEK yerde durur
# ---------------------------------------------------------------------------


def test_device_map_kit_satirinda_setleri_ve_KAYNAKLARI_veriyor(
    db, gateway, kurulumcu
) -> None:
    """Bolme worker'da yapilmazsa ariza motoru korlesir: uc setin alarmi tek
    device_id'ye duser, hatta yalnizca TEK nokta kirmizi olur ve ariza
    araligi hep fazla genis cikar."""
    _kit_ekle(db, kurulumcu, set_count=2)

    harita = internal_api.device_map_internal(
        db=db, x_service_token=settings.internal_service_token
    )
    satirlar = {d["code"]: d for d in harita["devices"]}

    kit = satirlar["PMK-001"]
    assert kit["model"] == POLE_MASTER_KIT_MODEL
    assert len(kit["subunits"]) == 2
    for alt in kit["subunits"]:
        assert alt["sources"] == subunit_source_map(alt["set_index"]), (
            "bolme haritasi subunit_source_map ile ayrisiyor"
        )
    assert [a["code"] for a in kit["subunits"]] == ["PMK-001-S1", "PMK-001-S2"]
    assert [a["set_index"] for a in kit["subunits"]] == [1, 2]

    # Set satiri kendi basina bolme tasimaz; parent'ini soyler.
    assert satirlar["PMK-001-S2"]["subunits"] == []
    assert satirlar["PMK-001-S2"]["parent_code"] == "PMK-001"


def test_device_map_SN2_cihazinda_subunits_BOS(db, gateway, kurulumcu) -> None:
    devices_api.create_device(
        payload=DeviceCreate(
            code="SN2-0001",
            name="Fider 1",
            model=DEFAULT_MODEL,
            gateway_code=GW,
            ip_address="10.0.0.20",
            latitude=39.0,
            longitude=35.0,
        ),
        current_user=kurulumcu,
        db=db,
    )

    harita = internal_api.device_map_internal(
        db=db, x_service_token=settings.internal_service_token
    )
    sn2 = next(d for d in harita["devices"] if d["code"] == "SN2-0001")
    assert sn2["subunits"] == []
    assert sn2["parent_code"] is None
    assert sn2["subunit_index"] is None


# ---------------------------------------------------------------------------
# 12) GATEWAY CONFIG — sanal setler POLL HEDEFI DEGILDIR
# ---------------------------------------------------------------------------


def test_sanal_setler_gateway_cihaz_listesine_GIRMEZ(db, gateway, kurulumcu) -> None:
    """Uc set gateway'e ayri cihaz olarak verilirse ayni uc noktaya UC TCP
    oturumu acilir. Horstmann `CloseExisting` modunda calisir: yeni baglanti
    mevcudu kapatir ve sonuc karsilikli tahliye dongusudur — belirti "ag
    kararsiz" gorunur, kok neden gorunmez."""
    _kit_ekle(db, kurulumcu, set_count=3)

    yanit = gateways_api.get_gateway_config(
        gateway_code=GW,
        response=Response(),
        db=db,
        x_gateway_token=GW_TOKEN,
        x_gateway_code=None,
        x_gateway_instance_id=None,
        x_request_id=None,
        if_none_match=None,
    )
    govde = json.loads(yanit.body)
    kodlar = [d["code"] for d in govde["devices"]]

    assert kodlar == ["PMK-001"], f"sanal setler poll listesine sizdi: {kodlar}"


# ---------------------------------------------------------------------------
# 13) KOMUT — model olmadan cozum BELIRSIZDIR, hedef FIZIKSEL kittir
# ---------------------------------------------------------------------------


def test_model_verilmezse_ayni_slug_BELIRSIZ(db) -> None:
    """Modeli vermeden cozmek komutu yanlis noktaya gondermek demektir; cihaz
    hicbir hata dondurmez, sadece istenmeyen bir sey yapar ya da hicbir sey
    yapmaz."""
    seed_default_signals(db)

    with pytest.raises(device_command_service.CommandRejected) as hata:
        device_command_service.resolve_command_index(db, "boost_mode")
    assert hata.value.reason == "ambiguous_command"


def test_model_verilince_DOGRU_index_cozulur(db) -> None:
    seed_default_signals(db)

    index_sn2, _ = device_command_service.resolve_command_index(
        db, "boost_mode", model=DEFAULT_MODEL
    )
    index_kit, _ = device_command_service.resolve_command_index(
        db, "boost_mode", model=POLE_MASTER_KIT_MODEL
    )
    assert (index_sn2, index_kit) == (26, 30)


def test_SET_cihazina_verilen_komut_FIZIKSEL_kite_kuyruklanir(
    db, gateway, kurulumcu
) -> None:
    """Sanal setin kendi DNP3 oturumu yoktur. Yonlendirme yapilmasaydi komut
    gateway'e hic verilmeyen bir cihaz koduyla kuyruga girer ve sessizce
    hicbir yere ulasmazdi."""
    seed_default_signals(db)
    kit = _kit_ekle(db, kurulumcu, set_count=2)
    ikinci_set = _setler(db, kit)[1]

    kuyruklanan = device_command_service.queue_command(
        db,
        device=ikinci_set,
        slug="reset_all_fcis",
        actor="kurulumcu",
        origin="ui",
    )
    db.flush()

    assert kuyruklanan.device_code == "PMK-001"
    satir = db.scalar(select(DeviceCommand).where(DeviceCommand.id == kuyruklanan.id))
    assert satir.device_code == "PMK-001", (
        "komut sanal set koduyla kuyruklanmis — gateway o kodu hic gormez"
    )
    assert satir.gateway_code == GW
    # Index KIT katalogundan cozulmeli, SN2'den degil.
    kit_index = next(
        s["dnp3_index"]
        for s in _yukle(KIT_JSON)
        if s["key"] == "master.reset_all_fcis"
    )
    assert satir.dnp3_index == kit_index


# ---------------------------------------------------------------------------
# !!! BULUNAN HATA — KIT TASININCA SETLERIN BAGLANTI ALANLARI ESKI KALIYOR !!!
#
# Asagidaki iki test BILEREK KIRMIZI. Urun kodunda gercek bir hata
# gosteriyorlar; testi gecirmek icin test degil KOD duzeltilmeli.
#
# NE OLUYOR: `create_subunits` gateway_code / ip_address / dnp3_outstation_port
# alanlarini fiziksel kayittan KOPYALIYOR, ama `update_device` set sayisini
# senkronlarken (`sync_subunits`) MEVCUT setlere dokunmuyor. Kit baska bir
# gateway'e ya da baska bir uc noktaya tasindiginda setler eski degerde kaliyor.
#
# NEDEN ONEMLI:
#   1. Kopyanin TEK gerekcesi arayuzun "hangi outstation'dan geliyor" sorusuna
#      cevap verebilmesiydi (bkz. `create_subunits` yorumu); tasima sonrasi o
#      cevap YANLIS ve yanlisligi hicbir yerde gorunmuyor.
#   2. Sonradan set sayisi artirilirsa YENI setler guncel degerleri alir, eski
#      setler eskisini tasir — ayni kitin setleri kendi arasinda ayrisir.
#   3. En agiri: `delete_gateway` -> `delete_all_for_gateway` cihazlari
#      `gateway_code` ile siler. Kit GW-2'ye tasinmis ama setleri GW-1'de
#      kalmissa, GW-1 silindiginde SETLER gider, KIT kalir. Setlerin hat
#      yerlesimi, ariza gecmisi ve alarmlari da gider ve geri alinamaz; kit
#      ayakta kalir ama 9 uydunun telemetrisini yazacagi hicbir kayit yoktur.
# ---------------------------------------------------------------------------


def test_kit_tasininca_setlerin_baglanti_alanlari_da_GUNCELLENIR(
    db, gateway, kurulumcu
) -> None:
    """Baglanti alanlari kitten KOPYALANIYOR ama bir daha hic tazelenmiyor.

    Kopyanin tek gerekcesi (bkz. `create_subunits` yorumu) arayuzde "hangi
    outstation'dan geliyor" sorusunun cevaplanabilmesi. Kit tasindiktan sonra
    o cevap YANLIS oluyor ve yanlisligi hicbir yerde gorunmuyor.
    """
    db.add(Gateway(code="GW-2", name="Ikinci", host="127.0.0.1", listen_port=8200,
                   token="t2", token_hash=hash_gateway_token("t2")))
    db.flush()
    kit = _kit_ekle(db, kurulumcu, set_count=2)

    devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(
            gateway_code="GW-2", ip_address="10.0.0.77", dnp3_outstation_port=20005
        ),
        current_user=kurulumcu,
        db=db,
    )

    for s in _setler(db, kit):
        assert (s.gateway_code, s.ip_address, s.dnp3_outstation_port) == (
            "GW-2",
            "10.0.0.77",
            20005,
        ), f"{s.code} kitle birlikte tasinmadi"


def test_kit_tasininca_ESKI_gateway_setleri_SILME_HEDEFI_olmuyor(
    db, gateway, kurulumcu
) -> None:
    """`delete_all_for_gateway` silinecekleri YALNIZCA `gateway_code` ile secer.

    Kit GW-2'ye tasinmis ama setleri GW-1'de kalmissa, GW-1 silindiginde
    SETLER gider ve KIT kalir. Setlerin hat yerlesimi, ariza gecmisi ve
    alarmlari da gider — geri alinamaz. Geriye telemetrisini bolecegi hicbir
    kaydi olmayan bir kit kalir.

    (Silmeyi CAGIRMIYORUZ: ayni fonksiyonda ayri bir hata var, bkz.
    `test_cihazi_olan_gateway_SILINEBILIYOR`. Burada silme HEDEFI olculuyor.)
    """
    db.add(Gateway(code="GW-2", name="Ikinci", host="127.0.0.1", listen_port=8200,
                   token="t2", token_hash=hash_gateway_token("t2")))
    db.flush()
    kit = _kit_ekle(db, kurulumcu, set_count=3)
    devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(gateway_code="GW-2"),
        current_user=kurulumcu,
        db=db,
    )

    hedef = list(db.scalars(select(Device.code).where(Device.gateway_code == GW)).all())

    assert hedef == [], (
        f"kit GW-2'ye tasindi ama GW-1 silinirse gidecekler: {hedef}"
    )


# ---------------------------------------------------------------------------
# !!! BULUNAN HATA (KIT ILE ILGISIZ) — CIHAZI OLAN GATEWAY SILINEMIYOR !!!
#
# Yukaridaki senaryoyu surerken cikti; kite ozgu DEGIL, her kurulumu etkiler.
# ---------------------------------------------------------------------------


def test_cihazi_olan_gateway_SILINEBILIYOR(db, gateway, kurulumcu) -> None:
    """HATA: `DeviceRepository.delete_all_for_gateway` TypeError ile patliyor.

    `_delete_telemetry_and_alarms_for_device` artik `"telemetry_history": None`
    donduruyor (arsiv temizligi arka plana devredildi, sayisi BILINMIYOR —
    0 yazmak "arsiv bostu" demek olurdu). Ama `delete_all_for_gateway` tum
    anahtarlari topluyor:

        total[key] = total.get(key, 0) + value      # int + None

    Sonuc: `DELETE /gateways/{code}` uzerinde EN AZ BIR cihaz varsa istek 500
    veriyor ve gateway hic silinemiyor. Tekil cihaz silme yolu (`delete`)
    toplama yapmadigi icin etkilenmiyor — bu yuzden hata yalnizca gateway
    silmede gorunuyor ve hicbir test bu yolu surmuyordu.
    """
    from app.repositories.device_repository import DeviceRepository

    devices_api.create_device(
        payload=DeviceCreate(
            code="SN2-0001",
            name="Fider 1",
            model=DEFAULT_MODEL,
            gateway_code=GW,
            ip_address="10.0.0.30",
            latitude=39.0,
            longitude=35.0,
        ),
        current_user=kurulumcu,
        db=db,
    )

    silinen, sayimlar = DeviceRepository(db).delete_all_for_gateway(GW)

    assert silinen == ["SN2-0001"]
    assert sayimlar["telemetry_history"] is None, (
        "arsiv temizligi arka planda; sayisi BILINMIYOR (0 yazmak yanlis olur)"
    )


def test_komut_hedefi_SET_ICIN_kit_DIGERLERI_ICIN_kendisi(db, gateway, kurulumcu) -> None:
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    ilk_set = _setler(db, kit)[0]

    assert device_kit_service.command_target(db, ilk_set).code == kit.code
    assert device_kit_service.command_target(db, kit).code == kit.code
    # Kit seviyesindeki telemetri de ayni satirdan okunur.
    assert device_kit_service.master_source_device(db, ilk_set).code == kit.code


# ---------------------------------------------------------------------------
# 14) MODBUS — her sanal set AYRI bir Modbus cihazi
# ---------------------------------------------------------------------------


def _modbus_hedefi(db):
    """Blok modunda bir Modbus TCP hedefi (plan uretimi icin yeterli)."""
    from app.models.outbound_target import OutboundTarget

    hedef = OutboundTarget(
        name="SCADA Modbus",
        protocol="modbus_tcp",
        is_active=True,
        modbus_mode="block",
        modbus_value_format="int16",
    )
    db.add(hedef)
    db.flush()
    return hedef


def test_her_set_AYRI_bir_modbus_cihazi_olarak_planda_yer_alir(db, kurulumcu, gateway, lisans_acik):
    """Set = ayri Device satiri oldugu icin ayri Modbus slotu almali.

    SCADA uc seti uc ayri cihaz olarak gormeli: her birinin kendi blok
    baslangici (block mode) ya da kendi unit id'si (unit mode) olur. Ortak
    slot verilseydi uc setin ayni adreslere binmesi ve son yazanin
    digerlerini ezmesi gerekirdi.
    """
    from app.services import modbus_plan_service

    seed_default_signals(db, strict=False)
    kit = _kit_ekle(db, kurulumcu, set_count=3)
    hedef = _modbus_hedefi(db)

    _layout, slotlar, _noktalar, _kapasite = modbus_plan_service.load_plan(
        db, hedef, commit=False
    )
    kodlar = {s.device_code for s in slotlar}
    setler = {c.code for c in _setler(db, kit)}
    assert setler <= kodlar, "her set planda kendi satiriyla yer almali"
    assert kit.code in kodlar, "fiziksel kit de kendi olcumleriyle planda"

    set_slotlari = [s for s in slotlar if s.device_code in setler]
    assert len({s.slot_index for s in set_slotlari}) == 3, "setler ayni slotu paylasamaz"
    assert len({s.block_start for s in set_slotlari}) == 3, "setler ayni adres blogunda olamaz"


def test_modbus_noktalari_cihazin_KENDI_modelinden_gelir(db, kurulumcu, gateway, lisans_acik):
    """Bir sete SN2 sinyali, bir SN2 cihazina kit sinyali YAPISMAMALI.

    Nokta uretimi cihaz x sinyal kartezyeni oldugu icin model filtresi
    olmasaydi her cihaza HER modelin sinyali yazilirdi: SCADA'ya hicbir zaman
    veri gelmeyecek yuzlerce adres bildirilir ve ayni blok icinde iki farkli
    sinyal ayni offsete duserdi.
    """
    from app.services import modbus_plan_service

    seed_default_signals(db, strict=False)
    # Yaninda bir de duz SN2 cihazi olsun ki karisma gorunsun.
    devices_api.create_device(
        payload=DeviceCreate(
            code="SN2-1",
            name="SN2",
            model=DEFAULT_MODEL,
            gateway_code=GW,
            ip_address="10.0.0.50",
            latitude=39.9,
            longitude=32.8,
        ),
        current_user=kurulumcu,
        db=db,
    )
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    hedef = _modbus_hedefi(db)

    _layout, _slotlar, noktalar, _kapasite = modbus_plan_service.load_plan(
        db, hedef, commit=False
    )
    set_kodu = _setler(db, kit)[0].code
    set_kaynaklari = {p.source for p in noktalar if p.device_code == set_kodu}
    assert set_kaynaklari <= {"sat01", "sat02", "sat03"}, (
        f"sette olmayan kaynaklar sizmis: {set_kaynaklari}"
    )
    # SN2'ye ozel bir sinyal (kitte YOK) sette gorunmemeli.
    set_anahtarlari = {p.signal_key for p in noktalar if p.device_code == set_kodu}
    assert "master.nominal_voltage" not in set_anahtarlari
    # Kite ozel bir sinyal (SN2'de YOK) SN2 cihazinda gorunmemeli.
    sn2_anahtarlari = {p.signal_key for p in noktalar if p.device_code == "SN2-1"}
    assert "master.solar_power" not in sn2_anahtarlari


def test_set_adresleri_cakismaz(db, kurulumcu, gateway, lisans_acik):
    """Uc setin (fonksiyon, adres) ciftleri BIRBIRINDEN AYRI olmali.

    Ayni adrese iki sinyal duserse SCADA'da biri digerini ezer ve bu
    hicbir yerde loglanmaz.
    """
    from app.services import modbus_plan_service

    seed_default_signals(db, strict=False)
    kit = _kit_ekle(db, kurulumcu, set_count=3)
    hedef = _modbus_hedefi(db)

    _layout, _slotlar, noktalar, _kapasite = modbus_plan_service.load_plan(
        db, hedef, commit=False
    )
    gorulen: dict[tuple[int, int, int], str] = {}
    for p in noktalar:
        anahtar = (p.unit_id, p.function, p.address)
        assert anahtar not in gorulen, (
            f"adres cakismasi {anahtar}: {gorulen[anahtar]} / "
            f"{p.device_code}.{p.signal_key}"
        )
        gorulen[anahtar] = f"{p.device_code}.{p.signal_key}"
    assert kit.id


def test_kit_uydu_satirlari_OUTBOUND_adres_almaz(db, kurulumcu, gateway, lisans_acik):
    """Fiziksel kitin `sat01..sat09` satirlari SCADA adres tablosuna GIRMEZ.

    Katalog cihazin DNP3 ADRES HARITASIDIR; gateway o dokuz uyduyu okur. Ama
    tag-engine hepsini SETLERE yonlendirdigi icin fiziksel kayitta yalnizca
    `master.*` saklanir. Bu satirlar icin Modbus adresi / IEC 104 IOA'si
    uretilseydi SCADA'ya HICBIR ZAMAN veri gelmeyecek yuzlerce adres
    bildirilirdi — ve bu hicbir yerde hata uretmezdi.
    """
    from app.services import modbus_plan_service

    seed_default_signals(db, strict=False)
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    hedef = _modbus_hedefi(db)

    _layout, _slotlar, noktalar, _kapasite = modbus_plan_service.load_plan(
        db, hedef, commit=False
    )
    kit_kaynaklari = {p.source for p in noktalar if p.device_code == kit.code}
    assert kit_kaynaklari == {"master"}, (
        f"kitte saklanmayan kaynaklar adres almis: {sorted(kit_kaynaklari)}"
    )


def test_kit_eklenince_MEVCUT_cihazlarin_modbus_adresleri_KAYMAZ(
    db, kurulumcu, gateway, lisans_acik
):
    """Blok boyutu en buyuk modele gore belirlenir; kit onu buyutmemeli.

    Buyutseydi `block_start = base + slot_index * stride` yeniden hesaplanir
    ve mevcut SN2 cihazlarinin SCADA'daki TUM adresleri sessizce kayardi —
    "adresler kaymaz" vaadi tam da bu senaryoda tutmazdi.
    """
    from app.services import modbus_plan_service

    seed_default_signals(db, strict=False)
    hedef = _modbus_hedefi(db)

    sadece_sn2 = modbus_plan_service.build_signal_layout(
        [s for s in db.scalars(select(SignalCatalog)).all() if s.model == DEFAULT_MODEL]
    )
    stride_once = modbus_plan_service.resolve_stride(hedef, sadece_sn2)

    _kit_ekle(db, kurulumcu, set_count=3)
    layout_sonra, _slotlar, _noktalar, kapasite = modbus_plan_service.load_plan(
        db, hedef, commit=False
    )
    assert kapasite.stride == stride_once, (
        f"kit eklenince blok boyutu degisti: {stride_once} -> {kapasite.stride}"
    )
    assert layout_sonra.summary.discrete_bits <= modbus_plan_service.BIT_STRIDE, (
        "bit blogu sabit stride'i asiyor; komsu cihazin bitlerinin uzerine yazar"
    )


# ---------------------------------------------------------------------------
# 15) UYDU ATAMASI — varsayilan var ama SABIT DEGIL
# ---------------------------------------------------------------------------


def test_yeni_setler_varsayilan_atamayi_ACIKCA_yazar(db, kurulumcu, gateway, lisans_acik):
    """1-2-3 / 4-5-6 / 7-8-9 turetilmekle kalmaz, kayda da yazilir.

    Turetmeye birakilsaydi ileride varsayilan degistiginde MEVCUT setlerin
    uydu atamasi sessizce kayardi.
    """
    kit = _kit_ekle(db, kurulumcu, set_count=3)
    beklenen = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    assert [s.subunit_satellites for s in _setler(db, kit)] == beklenen


def test_uydu_atamasi_DEGISTIRILEBILIR(db, kurulumcu, gateway, lisans_acik):
    """Uyduları kelepceyi takan kisi baglar; sira kite gore degil DIREGE gore."""
    # TEK setli kit: bitisik olmayan bir atama (2/7/9) kardes cakismasi
    # olmadan denenebilsin.
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    set1 = _setler(db, kit)[0]

    devices_api.update_device(
        device_code=set1.code,
        payload=DeviceUpdate(subunit_satellites=[2, 7, 9]),
        current_user=kurulumcu,
        db=db,
    )
    db.refresh(set1)
    assert set1.subunit_satellites == [2, 7, 9]
    set2 = set1

    # Bolme haritasi da yeni atamayi kullanmali — yoksa telemetri hala eski
    # uydulardan gelirdi ve degisiklik hicbir ise yaramazdi.
    harita = subunit_source_map(set2.subunit_index, set2.subunit_satellites)
    assert harita == {"sat02": "sat01", "sat07": "sat02", "sat09": "sat03"}


def test_ayni_uydu_IKI_SETE_atanamaz(db, kurulumcu, gateway, lisans_acik):
    """En sinsi hata: bolme bijektif olmazsa ikinci set HIC veri almaz.

    tag-engine ilk eslemeyi korur ve hata loglar, ama arayuzde set saglikli
    gorunur — yalnizca "bir faz hic olcum vermiyor" diye fark edilir.
    """
    kit = _kit_ekle(db, kurulumcu, set_count=2)
    set2 = _setler(db, kit)[1]

    with pytest.raises(HTTPException) as hata:
        devices_api.update_device(
            device_code=set2.code,
            payload=DeviceUpdate(subunit_satellites=[1, 5, 6]),  # 1 -> set 1'de
            current_user=kurulumcu,
            db=db,
        )
    assert hata.value.status_code == 422
    assert "Satellite 01" in str(hata.value.detail)


@pytest.mark.parametrize(
    "atama",
    [
        [1, 2],           # eksik
        [1, 2, 3, 4],     # fazla
        [1, 1, 2],        # set icinde tekrar
        [0, 1, 2],        # aralik disi (alt)
        [1, 2, 10],       # aralik disi (ust)
    ],
)
def test_gecersiz_atama_REDDEDILIR(db, kurulumcu, gateway, lisans_acik, atama):
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    set1 = _setler(db, kit)[0]
    with pytest.raises(HTTPException) as hata:
        devices_api.update_device(
            device_code=set1.code,
            payload=DeviceUpdate(subunit_satellites=atama),
            current_user=kurulumcu,
            db=db,
        )
    assert hata.value.status_code == 422


def test_fiziksel_cihazda_uydu_atamasi_DEGISTIRILEMEZ(db, kurulumcu, gateway, lisans_acik):
    """Atama SETIN ozelligi; kitin ya da bir SN2'nin degil."""
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    with pytest.raises(HTTPException) as hata:
        devices_api.update_device(
            device_code=kit.code,
            payload=DeviceUpdate(subunit_satellites=[1, 2, 3]),
            current_user=kurulumcu,
            db=db,
        )
    assert hata.value.status_code == 422


def test_kayitli_atama_YOKSA_varsayilana_duser(db, kurulumcu, gateway, lisans_acik):
    """Geriye uyum: 0051 oncesi acilmis setlerde kolon NULL.

    NULL'a toplu deger yazmak, kurulumcunun onaylamadigi bir atamayi
    "secilmis" gostermek olurdu; cozum okuma tarafinda yapilir.
    """
    kit = _kit_ekle(db, kurulumcu, set_count=2)
    set2 = _setler(db, kit)[1]
    set2.subunit_satellites = None      # eski kayit taklidi
    db.flush()

    assert resolve_subunit_satellites(set2.subunit_index, None) == (4, 5, 6)
    # Arayuze COZULMUS hali gider, bos degil.
    device_kit_service.annotate(db, [set2])
    assert set2.subunit_satellites == [4, 5, 6]


def test_bolme_haritasi_degisen_atamayi_yansitir(db, kurulumcu, gateway, lisans_acik):
    """/internal/device-map tag-engine'in tek kaynagi; atama oraya ulasmali."""
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    set1 = _setler(db, kit)[0]
    devices_api.update_device(
        device_code=set1.code,
        payload=DeviceUpdate(subunit_satellites=[2, 7, 9]),
        current_user=kurulumcu,
        db=db,
    )
    harita = internal_api.device_map_internal(db=db, x_service_token=settings.internal_service_token)
    kayit = next(d for d in harita["devices"] if d["code"] == kit.code)
    alt = next(s for s in kayit["subunits"] if s["set_index"] == 1)
    assert alt["sources"] == {"sat02": "sat01", "sat07": "sat02", "sat09": "sat03"}


# ---------------------------------------------------------------------------
# 16) YANIT SEMASI — tek bir modelin fazladan uydusu TUM katalogu dusurmesin
# ---------------------------------------------------------------------------


def test_katalogun_TAMAMI_yanit_semasindan_gecer(db):
    """`GET /signals` her satiri serilestirebilmeli.

    YASANAN: `SignalCatalogRead.source` alani `Literal["master","sat01","sat02"]`
    idi. Pole Master Kit'in `sat03`..`sat09` satirlari eklenince yanit
    dogrulamasi dustu ve uc TUM katalog icin 500 dondu — arayuzde Sinyaller
    sayfasi, canli deger tip sayaclari ve alarm kurali sinyal secici AYNI ANDA
    bosaldi. Belirti ("Henuz sinyal tanimli degil") sebebe hic benzemiyordu.

    Ders: kaynak kumesi VERIDIR; sema onu daraltirsa yeni bir model tum
    sistemi karartir.
    """
    from app.schemas.signal_catalog import SignalCatalogRead

    seed_default_signals(db, strict=True)
    satirlar = db.scalars(select(SignalCatalog)).all()
    assert satirlar, "katalog bos — seed calismamis"
    for satir in satirlar:
        SignalCatalogRead.model_validate(satir)


def test_her_modelin_sinyalleri_AYRI_listelenir(db):
    """Model filtresi gercekten o modelin kaynaklarini dondurmeli."""
    seed_default_signals(db, strict=True)
    beklenen = {
        DEFAULT_MODEL: {"master", "sat01", "sat02"},
        POLE_MASTER_KIT_MODEL: {"master"} | {f"sat{n:02d}" for n in range(1, 10)},
        PMK_SET_MODEL: {"sat01", "sat02", "sat03"},
    }
    for model, kaynaklar in beklenen.items():
        satirlar = db.scalars(
            select(SignalCatalog).where(SignalCatalog.model == model)
        ).all()
        assert satirlar, f"{model} icin sinyal yok"
        assert {s.source for s in satirlar} == kaynaklar, model


# ---------------------------------------------------------------------------
# 17) SET URETIMI KARDESLERLE CAKISMAZ  (saha: 48 sinyal sessizce dusuyordu)
# ---------------------------------------------------------------------------


def test_atama_degistikten_sonra_set_eklemek_CAKISMA_URETMEZ(
    db, kurulumcu, gateway, lisans_acik
):
    """Konum varsayilani korlemesine yazilinca iki set ayni uyduyu iddia ediyordu.

    YASANAN: Set 1 [7,8,9]'a alinip set sayisi 3'e cikarilinca Set 3 de
    varsayilanla [7,8,9] yaziliyordu. Bolme haritasinda ILK esleme kazaniyor,
    gec kalan setin o unitesine ait 48 sinyalin TAMAMI hic gelmiyordu — arayuzde
    set saglikli gorunuyor, tek iz tag-engine loglarindaki bir ERROR satiri.
    Ustelik cakisan uydular hicbir sete gitmedigi icin fiziksel kayitta yetim
    kaliyordu.

    Muhafiz yalnizca PATCH yolunda vardi; URETIM yolu korumasizdi.
    """
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    set1 = _setler(db, kit)[0]
    devices_api.update_device(
        device_code=set1.code,
        payload=DeviceUpdate(subunit_satellites=[7, 8, 9]),
        current_user=kurulumcu,
        db=db,
    )

    devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(satellite_set_count=3),
        current_user=kurulumcu,
        db=db,
    )

    atamalar = [tuple(s.subunit_satellites) for s in _setler(db, kit)]
    duz = [n for a in atamalar for n in a]
    assert len(duz) == len(set(duz)), f"ayni uydu birden fazla sette: {atamalar}"
    assert set(duz) == set(range(1, 10)), f"dokuz uydunun hepsi atanmali: {atamalar}"


def test_bolme_haritasinda_HER_uydu_TEK_sete_gider(db, kurulumcu, gateway, lisans_acik):
    """/internal/device-map bijektif olmali — tag-engine'in tek kaynagi bu."""
    kit = _kit_ekle(db, kurulumcu, set_count=1)
    set1 = _setler(db, kit)[0]
    devices_api.update_device(
        device_code=set1.code,
        payload=DeviceUpdate(subunit_satellites=[2, 5, 9]),
        current_user=kurulumcu,
        db=db,
    )
    devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(satellite_set_count=3),
        current_user=kurulumcu,
        db=db,
    )

    harita = internal_api.device_map_internal(
        db=db, x_service_token=settings.internal_service_token
    )
    kayit = next(d for d in harita["devices"] if d["code"] == kit.code)
    tum_kaynaklar = [k for alt in kayit["subunits"] for k in alt["sources"]]
    assert len(tum_kaynaklar) == len(set(tum_kaynaklar)), (
        f"bolme haritasi bijektif degil: {tum_kaynaklar}"
    )
    assert len(tum_kaynaklar) == 9


def test_set_azaltip_artirmak_da_cakisma_uretmez(db, kurulumcu, gateway, lisans_acik):
    """Tetiklemek icin elle atama SART DEGIL: sync_subunits silinen seti
    yeniden uretirken de ayni yoldan geciyor."""
    kit = _kit_ekle(db, kurulumcu, set_count=3)
    setler = _setler(db, kit)
    devices_api.update_device(
        device_code=setler[0].code,
        payload=DeviceUpdate(subunit_satellites=[4, 5, 6]),
        current_user=kurulumcu,
        db=db,
    ) if False else None  # kardes cakismasi zaten 422; dogrudan azalt/artir

    devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(satellite_set_count=1),
        current_user=kurulumcu,
        db=db,
    )
    devices_api.update_device(
        device_code=kit.code,
        payload=DeviceUpdate(satellite_set_count=3),
        current_user=kurulumcu,
        db=db,
    )
    duz = [n for s in _setler(db, kit) for n in s.subunit_satellites]
    assert len(duz) == len(set(duz)) == 9
