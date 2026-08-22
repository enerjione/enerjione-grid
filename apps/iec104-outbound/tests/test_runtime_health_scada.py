"""SMART IDLE SCADA'DA HABERLESME KAYBI DEGILDIR.

YASANAN RISK
------------
Horstmann Smart modda modemini BILEREK kapatir ve saatlerce sessiz kalir.
Backend ve Grid arayuzu bunu dogru biliyor (`smart_idle` = SAGLIKLI), ama
IEC 104 cikisi yalnizca `telemetry.normalized.*` tuketiyordu: SCADA icin
"veri gelmiyor" ile "cihaz uyuyor" ayni seye benziyordu.

Sonuc: saglikli, uyuyan bir filo dis SCADA'da HABERLESME KAYBI gibi
gorunuyordu. Grid ekraninda dogru, SCADA'da yanlis.

BU DOSYANIN ASIL KABUL OLCUTU
-----------------------------
`test_SMART_IDLE_asla_COMM_LOST_olmaz` — digerlerinin hepsi onu destekler.
"""

from __future__ import annotations

import pytest

from iec104_outbound import runtime_health as rh
from iec104_outbound.registry import build_point_registry

CA = 7


#: Cihaz kimligi ZORUNLU: sistem IOA'si `device_id`den turer. Kimliksiz
#: cihaz icin deterministik adres uretilemez (bkz. `runtime_health.system_ioa`).
_KIMLIKLER: dict[str, int] = {}


def _cihaz(kod: str = "SN2-001", model: str = "horstmann_sn_2_0",
           *, kimlik: int | None = None, ca: int | None = CA) -> dict:
    if kimlik is None:
        kimlik = _KIMLIKLER.setdefault(kod, len(_KIMLIKLER) + 1)
    return {"id": kimlik, "code": kod, "model": model,
            "iec104_common_address": ca, "is_active": True}


def _sistem_noktalari(reg) -> dict[str, object]:
    return {
        p.signal_key: p for p in reg.points if p.signal_key.startswith("system.")
    }


# ===========================================================================
# 2A/2B — SEMANTIK
# ===========================================================================


def test_SMART_IDLE_asla_COMM_LOST_olmaz():
    """BU ISIN ANA KABUL OLCUTU.

    Modem bilincli kapanmis, DNP3 trafigi yok, runtime health `smart_idle`.
    SCADA tarafinda COMM_LOST GORUNMEMELI.
    """
    kod = rh.state_code("smart_idle")
    assert kod == rh.STATE_SMART_IDLE
    assert kod != rh.STATE_COMM_LOST, (
        "uyuyan saglikli cihaz SCADA'da haberlesme kaybi olarak gorunuyor"
    )


@pytest.mark.parametrize(
    "durum,beklenen",
    [
        ("online", rh.STATE_ONLINE),
        ("smart_idle", rh.STATE_SMART_IDLE),
        ("recovering", rh.STATE_RECOVERING),
        ("lost", rh.STATE_COMM_LOST),
        ("listener_error", rh.STATE_LISTENER_ERROR),
        ("unknown", rh.STATE_UNKNOWN),
    ],
)
def test_her_durum_AYRI_kod(durum: str, beklenen: int):
    assert rh.state_code(durum) == beklenen


def test_kodlar_BIRBIRINDEN_ayri():
    """Iki durumun ayni koda dusmesi, SCADA'da ayirt edilemez olmasi demek."""
    kodlar = list(rh.STATE_CODES.values())
    assert len(kodlar) == len(set(kodlar))


def test_saglik_kovalari_DOGRU():
    assert "smart_idle" in rh.HEALTHY, "uyuyan cihaz saglikli sayilmali"
    assert "online" in rh.HEALTHY
    assert "recovering" in rh.DEGRADED
    assert "lost" in rh.UNHEALTHY
    assert "listener_error" in rh.UNHEALTHY
    # Ortusme olmamali.
    assert not (rh.HEALTHY & rh.UNHEALTHY)


def test_TANIMADIGIMIZ_durum_COMM_LOST_DEGIL():
    """Gateway ileride yeni bir durum eklerse, onu "haberlesme kaybi" diye
    yayinlamak saglikli bir cihazi arizali gosterirdi."""
    for bilinmeyen in ("gelecekteki_durum", "", None, "LOST_", "sleep"):
        kod = rh.state_code(bilinmeyen)
        assert kod == rh.STATE_UNKNOWN, f"{bilinmeyen!r} -> {kod}"
        assert kod != rh.STATE_COMM_LOST


def test_buyuk_harf_ve_bosluk_TOLERE_edilir():
    assert rh.state_code("  SMART_IDLE  ") == rh.STATE_SMART_IDLE


# ===========================================================================
# report_late AYRI BAYRAK
# ===========================================================================


def test_report_late_AYRI_NOKTA():
    """`report_late` kanonik durumu EZMEZ.

    Bir cihaz ayni anda `smart_idle` (saglikli, uyuyor) VE `report_late`
    (bekledigimiz rapor gelmedi) olabilir. Ikisini tek enum'a sikistirmak
    ya uyuyan cihazi arizali gostermek ya da gecikmeyi kaybetmek olurdu.
    """
    assert rh.KEY_REPORT_LATE != rh.KEY_RUNTIME_STATE
    assert rh.system_ioa(7, rh.SLOT_REPORT_LATE) != rh.system_ioa(
        7, rh.SLOT_RUNTIME_STATE
    )
    # Gecikme bir ENUM DEGERI DEGIL: durum kodlari arasinda yok.
    assert "report_late" not in rh.STATE_CODES
    assert "late" not in rh.STATE_CODES


def test_report_late_DURUM_KODUNU_degistirmez():
    """smart_idle + report_late -> durum HALA smart_idle."""
    assert rh.state_code("smart_idle") == rh.STATE_SMART_IDLE
    # Gecikme ayri noktada tasindigi icin durum kodunu hesaplayan fonksiyon
    # onu HIC gormez — yapisal guvence.
    import inspect

    kaynak = inspect.getsource(rh.state_code)
    assert "report_late" not in kaynak


# ===========================================================================
# 2F — NOKTA MODELI / IOA CAKISMASI
# ===========================================================================


def test_sistem_noktalari_HER_CIHAZDA_uretilir():
    reg = build_point_registry(
        target_id=1, default_common_address=1,
        devices=[_cihaz("A"), _cihaz("B")],
        signals=[{"key": "master.v", "model": "horstmann_sn_2_0",
                  "iec104_type_id": 13, "iec104_ioa": 5, "is_active": True}],
    )
    for kod in ("A", "B"):
        anahtarlar = {
            p.signal_key for p in reg.points if p.device_code == kod
        }
        assert rh.KEY_RUNTIME_STATE in anahtarlar, f"{kod}: durum noktasi yok"
        assert rh.KEY_REPORT_LATE in anahtarlar, f"{kod}: gecikme noktasi yok"


def test_KATALOGSUZ_cihaz_da_sistem_noktasi_alir():
    """SCADA "bu cihaz nasil" sorusunu HER cihaz icin sorabilmeli."""
    reg = build_point_registry(
        target_id=1, default_common_address=1,
        devices=[_cihaz("X", model="bilinmeyen_model")],
        signals=[],
    )
    assert set(_sistem_noktalari(reg)) == {rh.KEY_RUNTIME_STATE, rh.KEY_REPORT_LATE}


def test_IOA_saha_sinyalleriyle_CAKISMAZ():
    """Katalogdaki gercek IOA'lar 1..2091; sistem bandi 9.000.000+."""
    assert rh.SYSTEM_IOA_BASE > 100_000
    for yuva in (rh.SLOT_RUNTIME_STATE, rh.SLOT_REPORT_LATE):
        assert rh.system_ioa(0, yuva) <= 0xFFFFFF, "IEC 104 IOA tavanini asiyor"

    reg = build_point_registry(
        target_id=1, default_common_address=1,
        devices=[_cihaz()],
        signals=[
            {"key": f"master.s{i}", "model": "horstmann_sn_2_0",
             "iec104_type_id": 13, "iec104_ioa": i, "is_active": True}
            for i in range(1, 3000)
        ],
    )
    adresler = [(p.common_address, p.ioa) for p in reg.points]
    assert len(adresler) == len(set(adresler)), "(CA, IOA) cakismasi"


def test_sistem_noktalari_DESTEKLENEN_tiplerde():
    """Kapsam disi bir tip, noktayi sessizce yayinlanmaz yapardi."""
    from iec104_outbound.registry import SUPPORTED_MONITORING_TYPES

    assert rh.TYPE_RUNTIME_STATE in SUPPORTED_MONITORING_TYPES
    assert rh.TYPE_REPORT_LATE in SUPPORTED_MONITORING_TYPES


def test_durum_ANALOG_gecikme_BINARY():
    """Durum 0..5 arasi bir ENUM tasiyor; tek nokta (binary) yalnizca 0/1
    tasir ve alti durumu anlatamazdi."""
    assert rh.TYPE_RUNTIME_STATE == 13  # M_ME_NC_1
    assert rh.TYPE_REPORT_LATE == 1     # M_SP_NA_1
    assert max(rh.STATE_CODES.values()) > 1


def test_anahtarlar_KATALOG_anahtarlariyla_karisamaz():
    assert rh.KEY_RUNTIME_STATE.startswith("system.")
    assert rh.KEY_REPORT_LATE.startswith("system.")


# ===========================================================================
# 2H — YASAM DONGUSU
# ===========================================================================


def test_lifecycle_online_smartidle_recovering_online():
    """SCADA: 1 -> 2 -> 3 -> 1. Hicbir adimda COMM_LOST YOK."""
    dizi = ["online", "smart_idle", "recovering", "online"]
    kodlar = [rh.state_code(d) for d in dizi]
    assert kodlar == [
        rh.STATE_ONLINE, rh.STATE_SMART_IDLE, rh.STATE_RECOVERING, rh.STATE_ONLINE
    ]
    assert rh.STATE_COMM_LOST not in kodlar


def test_lifecycle_gercek_sessizlik_COMM_LOST_uretir():
    """Beklenen sessizlik esigi GERCEKTEN asilinca durum `lost` olur."""
    assert rh.state_code("lost") == rh.STATE_COMM_LOST


def test_lifecycle_listener_error_AYRI():
    assert rh.state_code("listener_error") == rh.STATE_LISTENER_ERROR
    assert rh.state_code("listener_error") != rh.STATE_COMM_LOST


def test_lifecycle_veri_yoksa_UNKNOWN():
    assert rh.state_code(None) == rh.STATE_UNKNOWN


# ===========================================================================
# 2J — GERIYE UYUMLULUK (gateway < 1.15)
# ===========================================================================


def test_gateway_eski_ise_UNKNOWN_sahte_SMART_IDLE_degil():
    """Gateway <1.15 runtime health tasimiyor.

    Sistem cokmemeli, SAHTE `smart_idle` uretmemeli. Backend o cihaz icin
    satir yazmaz; SCADA UNKNOWN gorur.
    """
    assert rh.state_code(None) == rh.STATE_UNKNOWN
    assert rh.state_code("") == rh.STATE_UNKNOWN
    # `smart_idle` ASLA varsayilan olmamali.
    assert rh.STATE_CODES.get("") is None


def test_durum_kodlari_SOZLESME_sabit():
    """Sayilar SCADA nokta listesine yazilir; kaydirmak sessiz veri
    bozulmasi olurdu."""
    assert rh.STATE_UNKNOWN == 0
    assert rh.STATE_ONLINE == 1
    assert rh.STATE_SMART_IDLE == 2
    assert rh.STATE_RECOVERING == 3
    assert rh.STATE_COMM_LOST == 4
    assert rh.STATE_LISTENER_ERROR == 5


# ===========================================================================
# 2C/2D — TASIMA VE BASLANGIC SNAPSHOT'I
# ===========================================================================


class _SahteKatalog:
    def __init__(self, satirlar):
        self.satirlar = satirlar
        self.cagri = 0

    def fetch_runtime_health(self):
        self.cagri += 1
        return self.satirlar


class _SahteManager:
    def __init__(self):
        self.yazilan = []

    def update_point_threadsafe(self, **kw):
        self.yazilan.append(kw)


def _syncer(satirlar):
    from iec104_outbound.catalog import CatalogSyncer

    s = CatalogSyncer.__new__(CatalogSyncer)  # __init__ agi/lock ister
    s.catalog = _SahteKatalog(satirlar)
    s.manager = _SahteManager()
    # Gercek `__init__` ag/lock ister; sure dolumu icin gereken durum
    # alanlari elle kurulur.
    s._saglik_bilinen = set()
    s._saglik_hata_sayaci = 0
    return s


def test_saglik_degerleri_SUNUCUYA_yazilir():
    s = _syncer([
        {"device_code": "A", "state": "smart_idle", "report_late": False,
         "stale": False, "updated_at": "2026-08-21T12:00:00+00:00"},
    ])
    assert s._push_runtime_health() == 2
    yazilan = {w["signal_key"]: w for w in s.manager.yazilan}
    assert yazilan[rh.KEY_RUNTIME_STATE]["value"] == float(rh.STATE_SMART_IDLE)
    assert yazilan[rh.KEY_REPORT_LATE]["value"] is False
    # Zaman damgasi tasinir.
    assert yazilan[rh.KEY_RUNTIME_STATE]["timestamp"] is not None


def test_SMART_IDLE_sunucuya_COMM_LOST_olarak_gitmez():
    """Ucdan uca kabul olcutu: uyuyan cihaz SCADA'da haberlesme kaybi degil."""
    s = _syncer([
        {"device_code": "A", "state": "smart_idle", "report_late": False,
         "stale": False, "updated_at": None},
    ])
    s._push_runtime_health()
    durum = next(w for w in s.manager.yazilan
                 if w["signal_key"] == rh.KEY_RUNTIME_STATE)
    assert durum["value"] == float(rh.STATE_SMART_IDLE)
    assert durum["value"] != float(rh.STATE_COMM_LOST)
    assert durum["good"] is True, "saglikli uyuyan cihaz kotu kalitede gitti"


def test_BAYAT_gozlem_kalitesi_KOTU_ve_degeri_UNKNOWN():
    """Iki isaret birbirini destekler, celismez."""
    s = _syncer([
        {"device_code": "A", "state": "unknown", "report_late": None,
         "stale": True, "updated_at": None},
    ])
    s._push_runtime_health()
    yazilan = {w["signal_key"]: w for w in s.manager.yazilan}
    assert yazilan[rh.KEY_RUNTIME_STATE]["value"] == float(rh.STATE_UNKNOWN)
    assert yazilan[rh.KEY_RUNTIME_STATE]["good"] is False
    # `report_late` BILINMIYOR: `0` (gecikme yok) diye yayinlamak iyi haber
    # uydurmak olurdu.
    assert yazilan[rh.KEY_REPORT_LATE]["good"] is False


def test_report_late_DURUMU_ezmez_ucdan_uca():
    s = _syncer([
        {"device_code": "A", "state": "smart_idle", "report_late": True,
         "stale": False, "updated_at": None},
    ])
    s._push_runtime_health()
    yazilan = {w["signal_key"]: w for w in s.manager.yazilan}
    assert yazilan[rh.KEY_RUNTIME_STATE]["value"] == float(rh.STATE_SMART_IDLE)
    assert yazilan[rh.KEY_REPORT_LATE]["value"] is True


def test_saglik_alinamazsa_KISA_kesintide_deger_korunur():
    """Gecici ag sorunu SCADA'da toplu durum degisikligine cevrilmemeli."""
    from iec104_outbound.catalog import SAGLIK_HATA_BUTCESI

    s = _syncer(None)
    s._saglik_bilinen = {"A"}
    for _ in range(SAGLIK_HATA_BUTCESI - 1):
        assert s._push_runtime_health() == 0
    assert s.manager.yazilan == []


def test_kodsuz_satir_ATLANIR():
    s = _syncer([{"state": "online"}, {"device_code": "", "state": "online"}])
    assert s._push_runtime_health() == 0


def test_BASLANGIC_snapshot_run_forever_icinde():
    """Servis yeniden basladiginda bir sonraki GECISI beklememeli.

    Cihaz haftalarca `smart_idle`da kalirsa, gecis beklemek SCADA'yi
    sonsuza kadar karanlikta birakirdi.
    """
    import inspect

    from iec104_outbound.catalog import CatalogSyncer

    kaynak = inspect.getsource(CatalogSyncer.run_forever)
    ilk_tick = kaynak.index("await self.tick()")
    ilk_push = kaynak.index("_push_runtime_health")
    assert ilk_push > ilk_tick, "baslangic snapshot'i ilk turda yayinlanmiyor"
    # Periyodik turda da cagrilmali.
    assert kaynak.count("_push_runtime_health") >= 2


# ===========================================================================
# IOA CAKISMASI — CA BENZERSIZLIGI YOK
# ===========================================================================


def test_CA_benzersizligi_GARANTI_DEGIL():
    """Ilk tasarimin dayandigi varsayim YANLIS — kaynaktan dogrulanir.

    Sistem noktalari HER cihazda kosulsuz bulundugu icin, ayirt edici
    olarak yalnizca Common Address'e guvenmek cakisma GARANTISI olurdu.
    Bu test o varsayimin geri gelmesini engeller: kolon nullable ve
    benzersizlik kisiti/dogrulamasi yok.
    """
    import pathlib

    kok = pathlib.Path(__file__).resolve().parents[3]
    model = (kok / "apps/backend-api/app/models/device.py").read_text(encoding="utf-8")
    # Yorum satirlari degil, KOLON TANIMI aranir.
    satir = next(s for s in model.splitlines()
                 if "iec104_common_address" in s and "Mapped[" in s)
    assert "int | None" in satir, "kolon artik nullable degil mi?"
    assert "unique=True" not in satir, (
        "CA benzersiz oldu — sabit IOA tasarimi yeniden degerlendirilebilir"
    )

    # Semada CA icin benzersizlik dogrulamasi yok. ("unique" kelimesi
    # dosyada gecer ama IP/port ucu icin — `_require_unique_endpoint`.)
    sema = (kok / "apps/backend-api/app/schemas/device.py").read_text(encoding="utf-8")
    ca_satirlari = [s for s in sema.splitlines() if "iec104_common_address" in s]
    assert ca_satirlari, "sema alani kaybolmus"
    for satir in ca_satirlari:
        dusuk = satir.lower()
        assert "unique" not in dusuk and "benzersiz" not in dusuk, (
            f"CA benzersizlik dogrulamasi eklenmis: {satir.strip()}"
        )


def test_AYNI_CA_paylasan_cihazlar_CAKISMAZ():
    """CA'sиz iki cihaz hedefin varsayilan CA'sina duser — yine de ayrisir."""
    reg = build_point_registry(
        target_id=1, default_common_address=1,
        devices=[_cihaz("A", kimlik=11, ca=None), _cihaz("B", kimlik=12, ca=None)],
        signals=[],
    )
    adresler = [(p.common_address, p.ioa) for p in reg.points]
    assert len(adresler) == len(set(adresler)), "ayni (CA, IOA) ciftine binen nokta var"
    # Ayni CA'da olduklarini da dogrula — testin kendisi anlamsizlasmasin.
    assert len({ca for ca, _ in adresler}) == 1


def test_ACIKCA_ayni_CA_verilen_cihazlar_CAKISMAZ():
    reg = build_point_registry(
        target_id=1, default_common_address=99,
        devices=[_cihaz("A", kimlik=21, ca=7), _cihaz("B", kimlik=22, ca=7)],
        signals=[],
    )
    adresler = [(p.common_address, p.ioa) for p in reg.points]
    assert len(adresler) == len(set(adresler))


def test_IOA_cihaz_EKLENINCE_kaymaz():
    """Sirali indeks kullansaydik araya cihaz girmesi SCADA nokta listesini
    sessizce gecersiz kilardi. `device_id` sabit kalir."""
    def ioalar(cihazlar):
        reg = build_point_registry(target_id=1, default_common_address=1,
                                   devices=cihazlar, signals=[])
        return {(p.device_code, p.signal_key): p.ioa for p in reg.points}

    once = ioalar([_cihaz("B", kimlik=30), _cihaz("C", kimlik=31)])
    sonra = ioalar([_cihaz("A", kimlik=29), _cihaz("B", kimlik=30),
                    _cihaz("C", kimlik=31)])
    for anahtar, ioa in once.items():
        assert sonra[anahtar] == ioa, f"{anahtar} adresi kaydi: {ioa} -> {sonra[anahtar]}"


def test_KIMLIKSIZ_cihaza_nokta_URETILMEZ():
    """Sabit bir IOA'ya dusurmek cakismaya doner; nokta uretmemek durust."""
    reg = build_point_registry(
        target_id=1, default_common_address=1,
        devices=[{"code": "X", "model": "m", "is_active": True}], signals=[],
    )
    assert _sistem_noktalari(reg) == {}


def test_IOA_bandi_TASARSA_hata():
    """Sessizce saha sinyallerinin uzerine yazmaktansa yuksek sesle patla."""
    import pytest

    with pytest.raises(ValueError):
        rh.system_ioa(rh.SYSTEM_IOA_MAX, rh.SLOT_RUNTIME_STATE)
    with pytest.raises(ValueError):
        rh.system_ioa(-1, rh.SLOT_RUNTIME_STATE)


# ===========================================================================
# SESSIZ ESKIME — ONLINE'DA TAKILI KALMA
# ===========================================================================


def _durum(manager, kod: str):
    """Bir cihaza yazilan SON durum kaydi."""
    kayitlar = [w for w in manager.yazilan
                if w["device_code"] == kod
                and w["signal_key"] == rh.KEY_RUNTIME_STATE]
    return kayitlar[-1] if kayitlar else None


def test_KAYIT_KAYBOLURSA_UNKNOWN_olur():
    """ANA KABUL OLCUTU: onceden ONLINE bilinen cihaz listeden duserse
    SCADA'da ONLINE'da TAKILI KALMAMALI."""
    s = _syncer([{"device_code": "A", "state": "online", "report_late": False,
                  "stale": False, "updated_at": None}])
    s._push_runtime_health()
    assert _durum(s.manager, "A")["value"] == float(rh.STATE_ONLINE)

    # Cihaz artik saglik listesinde yok.
    s.catalog.satirlar = []
    s.manager.yazilan.clear()
    assert s._push_runtime_health() == 2
    son = _durum(s.manager, "A")
    assert son["value"] == float(rh.STATE_UNKNOWN), "ONLINE degerinde takili kaldi"
    assert son["good"] is False
    # `lost` DEGIL: dogrulanmamis bir ariza iddiasi olurdu.
    assert son["value"] != float(rh.STATE_COMM_LOST)


def test_KAYIT_kayboldugunda_gecikme_de_BILINMIYOR():
    s = _syncer([{"device_code": "A", "state": "online", "report_late": True,
                  "stale": False, "updated_at": None}])
    s._push_runtime_health()
    s.catalog.satirlar = []
    s.manager.yazilan.clear()
    s._push_runtime_health()
    gecikme = next(w for w in s.manager.yazilan
                   if w["signal_key"] == rh.KEY_REPORT_LATE)
    assert gecikme["good"] is False, "bilinmeyen gecikme iyi kalitede gitti"


def test_UC_ERISILEMEZ_kalirsa_UNKNOWN_olur():
    """Butce dolunca elimizdeki bilgi artik bir DURUM IDDIASI degildir."""
    from iec104_outbound.catalog import SAGLIK_HATA_BUTCESI

    s = _syncer([{"device_code": "A", "state": "online", "report_late": False,
                  "stale": False, "updated_at": None}])
    s._push_runtime_health()
    s.catalog.satirlar = None
    s.manager.yazilan.clear()
    for _ in range(SAGLIK_HATA_BUTCESI - 1):
        s._push_runtime_health()
    assert s.manager.yazilan == [], "tek hicup'ta filo karartildi"

    assert s._push_runtime_health() == 2
    assert _durum(s.manager, "A")["value"] == float(rh.STATE_UNKNOWN)


def test_UC_geri_gelince_TEKRAR_yayinlanir():
    """Sure dolumu KALICI OLMAMALI: uc dondugunde gercek durum geri gelir."""
    s = _syncer(None)
    s._saglik_bilinen = {"A"}
    s._saglik_hata_sayaci = 99
    s._push_runtime_health()
    s.catalog.satirlar = [{"device_code": "A", "state": "smart_idle",
                           "report_late": False, "stale": False,
                           "updated_at": None}]
    s.manager.yazilan.clear()
    s._push_runtime_health()
    assert _durum(s.manager, "A")["value"] == float(rh.STATE_SMART_IDLE)
    assert s._saglik_hata_sayaci == 0


def test_ayni_cihaz_IKI_KEZ_dusurulmez():
    """Sure dolumu bir kez yayilir; her turda tekrar yazmak SCADA'ya
    gereksiz trafik uretirdi."""
    s = _syncer([{"device_code": "A", "state": "online", "report_late": False,
                  "stale": False, "updated_at": None}])
    s._push_runtime_health()
    s.catalog.satirlar = []
    assert s._push_runtime_health() == 2
    s.manager.yazilan.clear()
    assert s._push_runtime_health() == 0
    assert s.manager.yazilan == []
