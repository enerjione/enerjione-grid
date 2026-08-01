""" Historian (arsiv) politikasi — her okuma arsive yazilmaz.

GERCEK SCADA PRATIGI
--------------------
Anlik deger (RTDB) her zaman guncel tutulur; arsive yalnizca isaretlenen
tag'ler, olu bant suzgecinden gecerek yazilir.

Bu sistemde iki on kosul da SAGLANIYOR — bu yuzden arsivi kismak alarm
dogrulugunu ETKILEMIYOR:
  * alarm-service JetStream akisini dinliyor, gecmis sorgusu YAPMIYOR,
  * canli deger `telemetry_latest` tablosunda.

OLCUM (saha test cihazi, 15 cihaz): 375 okuma/sn ve hepsi arsive giriyordu.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from app.services import historian_policy as hp


class _SahteDB:
    """`select(...)` sonucunu sabitleyen minimal session."""

    def __init__(self, satirlar, patlat: Exception | None = None):
        self.satirlar = satirlar
        self.patlat = patlat
        self.cagri = 0

    def execute(self, _stmt):
        self.cagri += 1
        if self.patlat is not None:
            raise self.patlat
        return self

    def all(self):
        return self.satirlar


@pytest.fixture(autouse=True)
def _temiz():
    hp.reset_caches()
    yield
    hp.reset_caches()


def _db(*satirlar):
    return _SahteDB(list(satirlar))


# ---------------------------------------------------------------------------
# historize bayragi
# ---------------------------------------------------------------------------

def test_isaretli_sinyal_ARSIVLENIYOR():
    db = _db(("master.actual_current", True, 0.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.actual_current", value=12.0)


def test_isaretsiz_sinyal_ARSIVLENMIYOR():
    """Seri no / firmware gibi statik metadata ve komut noktalari."""
    db = _db(("master.info_serial_number", False, 0.0))
    assert not hp.should_archive(
        db, device_id=1, signal_key="master.info_serial_number", value=12345.0
    )


def test_BILINMEYEN_sinyal_arsivleniyor():
    """Katalogda olmayan anahtari sessizce atmak, yeni eklenen bir sinyalin
    arsivinin HIC olusmamasina yol acardi."""
    db = _db(("master.x", True, 0.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.YENI", value=1.0)


def test_katalog_OKUNAMAZSA_her_sey_arsivleniyor():
    """Migration henuz uygulanmamis olabilir. Sessizce arsivlemeyi KESMEK
    veri kaybi olurdu; guvenli yon fazla yazmaktir."""
    db = _SahteDB([], patlat=RuntimeError("kolon yok"))
    assert hp.should_archive(db, device_id=1, signal_key="master.x", value=1.0)


# ---------------------------------------------------------------------------
# Olu bant
# ---------------------------------------------------------------------------

def test_olu_bant_KAPALIYKEN_her_okuma_arsivleniyor():
    db = _db(("master.v", True, 0.0))
    for deger in (10.0, 10.0, 10.001, 10.002):
        assert hp.should_archive(db, device_id=1, signal_key="master.v", value=deger)


def test_olu_bant_KUCUK_degisimi_eliyor():
    db = _db(("master.v", True, 1.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=230.0)
    assert not hp.should_archive(db, device_id=1, signal_key="master.v", value=230.5)
    assert not hp.should_archive(db, device_id=1, signal_key="master.v", value=229.6)


def test_olu_bant_BUYUK_degisimi_geciriyor():
    db = _db(("master.v", True, 1.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=230.0)
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=232.0)


def test_olu_bant_SON_ARSIVLENEN_degere_gore_calisiyor():
    """Son OKUNAN'a gore olsaydi, esigin altinda surekli tirmanan bir deger
    HIC arsivlenmezdi (sinsi veri kaybi)."""
    db = _db(("master.v", True, 1.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=100.0)
    for deger in (100.4, 100.8, 101.2):
        pass  # her adim 0.4 artiyor
    assert not hp.should_archive(db, device_id=1, signal_key="master.v", value=100.4)
    assert not hp.should_archive(db, device_id=1, signal_key="master.v", value=100.8)
    # 101.2, son ARSIVLENEN 100.0'dan 1.2 uzakta -> gecmeli
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=101.2)


def test_olu_bant_CIHAZ_BAZINDA_ayri():
    """Ortak tutulsaydi bir cihazin degeri digerinin arsivini bastirirdi."""
    db = _db(("master.v", True, 5.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=100.0)
    assert hp.should_archive(db, device_id=2, signal_key="master.v", value=100.0)


def test_SAYISAL_OLMAYAN_deger_olu_banta_takilmiyor():
    """String/binary sinyalde olu bant anlamsiz."""
    db = _db(("master.s", True, 5.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.s", value=None)
    assert hp.should_archive(db, device_id=1, signal_key="master.s", value=None)


# ---------------------------------------------------------------------------
# Onbellek
# ---------------------------------------------------------------------------

def test_katalog_ONBELLEKLENIYOR():
    """Okuma basina katalog sorgusu, cozmeye calistigimiz sorunu buyuturdu."""
    db = _db(("master.v", True, 0.0))
    for _ in range(50):
        hp.should_archive(db, device_id=1, signal_key="master.v", value=1.0)
    assert db.cagri == 1, f"katalog {db.cagri} kez sorgulandi"


def test_onbellek_SINIRLI():
    """Sinirsiz buyume, 600 cihazda bellek sizintisi olurdu."""
    assert hp._LAST_CACHE_MAX >= 600 * 193, "sinir gercek olcegin altinda"
    assert hp._LAST_CACHE_MAX <= 2_000_000, "sinir pratikte sinirsiz"


# ---------------------------------------------------------------------------
# Tuketici baglantisi
#
# Politikanin dogru olmasi yetmez, TUKETICIDE UYGULANMASI gerekir. Ilk
# yazimda bu testler yoktu ve `if True or should_archive(...)` yapan bir
# mutasyon KACTI — politika calisir gorunurken arsive her sey yaziliyordu.
# ---------------------------------------------------------------------------

def _tuketici_kodu() -> str:
    import inspect
    import re

    from app.services import telemetry_consumer

    kaynak = inspect.getsource(telemetry_consumer._persist_batch)
    kaynak = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


def test_tuketici_politikayi_UYGULUYOR():
    kod = _tuketici_kodu()
    assert "historian_policy.should_archive(" in kod, (
        "arsiv politikasi tuketicide cagrilmiyor — her okuma arsive gider"
    )


def test_arsiv_satiri_politikanin_ICINDE_ekleniyor():
    """Kosul disinda kalirsa politika hicbir sey yapmaz."""
    kod = _tuketici_kodu()
    i_cagri = kod.find("historian_policy.should_archive(")
    i_ekle = kod.find("historian_rows.append(")
    assert i_cagri != -1 and i_ekle != -1
    assert i_cagri < i_ekle, "arsiv satiri kosuldan ONCE ekleniyor"

    # Kontrol `if`in BASINDAN baslamali, cagridan degil.
    #
    # Ilk yazimda cagridan itibaren bakiyordum ve `if True or should_archive(...)`
    # yapan mutasyon KACTI: eklenen `or` cagrinin ONUNDE kaldigi icin
    # dilimin disinda kaliyordu. Mutasyon testi yakaladi.
    i_if = kod.rfind("if ", 0, i_cagri)
    assert i_if != -1, "kosulun basi bulunamadi"
    kosul = kod[i_if:i_ekle]
    for kacamak in (" or ", " and ", "True", "not "):
        assert kacamak not in kosul.replace("should_archive", ""), (
            f"kosul baska bir ifadeyle zayiflatilmis ({kacamak!r}): {kosul!r}"
        )


def test_CANLI_deger_politikadan_ETKILENMIYOR():
    """RTDB her zaman guncel kalmali — arsiv kisilsa bile ekran ve alarm
    son degeri gormeye devam etmeli. Bu, SCADA modelinin temeli."""
    kod = _tuketici_kodu()
    i_kosul = kod.find("historian_policy.should_archive(")
    i_latest = kod.find("latest_rows[_latest_key]")
    assert i_latest != -1, "telemetry_latest yazimi bulunamadi"
    assert i_latest > i_kosul, "beklenmeyen sira"
    # `latest_rows` atamasi arsiv kosulunun govdesinde OLMAMALI: govde
    # `historian_rows.append(` ile bitiyor.
    i_ekle_son = kod.find("})", kod.find("historian_rows.append("))
    assert i_latest > i_ekle_son, (
        "canli deger yazimi arsiv kosulunun ICINDE — arsiv kisilinca ekran "
        "ve alarm da son degeri kaybeder"
    )


# ---------------------------------------------------------------------------
# Olu bant varsayilanlari (migration 0033)
#
# Operator onayi: A -> 0.5, V -> 1.0, °C -> 0.5.
# mA BILEREK 0 kaldi: onay "0.5 A" idi, bu birime ceviri 500 mA eder ve o,
# ariza analizinin merkezindeki akim sinyalleri icin cok buyuk olabilir.
# Yanlis olu bant ariza oncesindeki egriyi KALICI olarak siler.
# ---------------------------------------------------------------------------

_MIGRATION_0033 = (
    __import__("pathlib").Path(__file__).resolve().parents[1]
    / "alembic_migrations" / "versions"
    / "2026_08_01_0007-0033_historian_deadbands.py"
)


def _m0033():
    import importlib.util

    spec = importlib.util.spec_from_file_location("m0033", _MIGRATION_0033)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0033_zinciri():
    m = _m0033()
    assert m.revision == "0033"
    assert m.down_revision == "0032"


def test_onaylanan_birimler_ve_degerler():
    """Onay disinda bir birim eklenirse ya da deger degisirse burada durur."""
    m = _m0033()
    assert m._DEADBANDS == {"A": 0.5, "V": 1.0, "°C": 0.5}


def test_mA_BILEREK_disarida():
    """0.5 A = 500 mA; bu grup icin cok buyuk bir esik olabilir ve ariza
    analizinin merkezinde."""
    m = _m0033()
    assert "mA" not in m._DEADBANDS, (
        "mA'ya olu bant verilmis — onay 'A' icindi, 500 mA ariza oncesi akim "
        "egrisini silebilir"
    )


def test_statik_analoglar_ARSIVDEN_cikariliyor():
    """`serial_number` / `firmware_version` / `hardware_revision` katalogda
    `analog` tiplenmis; 0032 yalnizca string ve binary_output'u kapatmisti."""
    m = _m0033()
    assert set(m._STATIK_ANALOG_SONEKLERI) == {
        "serial_number", "firmware_version", "hardware_revision",
    }


def test_olu_bant_yalnizca_HISTORIZE_edilenlere():
    """Arsivlenmeyen sinyale olu bant vermek anlamsiz ve kafa karistirici."""
    import re

    kaynak = _MIGRATION_0033.read_text(encoding="utf-8")
    kod = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    assert "historize IS TRUE" in kod, (
        "olu bant guncellemesi arsivlenmeyen sinyalleri de kapsiyor"
    )


def test_migration_GERI_ALINABILIR():
    import re

    kaynak = _MIGRATION_0033.read_text(encoding="utf-8")
    kod = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    govde = kod[kod.index("def downgrade"):]
    assert "historize_deadband = 0" in govde
    assert "historize = true" in govde


# ---------------------------------------------------------------------------
# Kalan olu bantlar (migration 0035) — BIRIM BAZINDA YAPILAMAZ
#
# 0033 akim/gerilim/sicakligi birim bazinda ayarladi. Kalanlarda ayni yol
# YANLIS olurdu: `°` altinda hem GPS KONUMU (sabit) hem ACI OLCUMU (dinamik)
# var. Tek bir esik ya konumu gereksiz sik kaydeder ya acidaki anlamli
# degisimi kacirirdi.
# ---------------------------------------------------------------------------

_M0035 = (
    Path(__file__).resolve().parents[1] / "alembic_migrations" / "versions"
    / "2026_08_01_0009-0035_remaining_deadbands.py"
)


def _m0035():
    import importlib.util

    spec = importlib.util.spec_from_file_location("m0035", _M0035)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_m0035_zinciri():
    m = _m0035()
    assert m.revision == "0035" and m.down_revision == "0034"


def test_AYNI_birimde_FARKLI_esik_veriliyor():
    """`°` altindaki konum ile aci ayni esigi ALMAMALI — yoksa biri
    gereksiz kaydedilir, digerinde anlamli degisim kacirilir."""
    d = _m0035()._DEADBANDS
    assert d["latitude_degrees"] != d["pitch_angle"], (
        "GPS konumu ile aci olcumu ayni esikte — birim bazinda yapilmis"
    )
    assert d["latitude_degrees"] < d["pitch_angle"]


def test_GPS_arsivden_CIKARILMIYOR():
    """Cihaz yerinden oynarsa (hirsizlik, direk hasari) "ne zaman oynadi"
    sorusu degerli. Esik hareket buyuklugunde; sabit durdugu surece tek
    satir yazilir."""
    d = _m0035()._DEADBANDS
    for ad in ("latitude_degrees", "longitude_degrees"):
        assert d[ad] > 0, f"{ad} icin esik yok"
        assert d[ad] < 0.001, (
            f"{ad} esigi cok buyuk — gercek yer degistirme kacirilir"
        )


def test_fault_duration_SIFIR_kaliyor():
    """OLAY VERISI: her deger ayri bir arizanin suresi. Olu bant koymak
    ardisik benzer sureli arizalardan birini SILMEK olurdu."""
    assert "fault_duration" not in _m0035()._DEADBANDS, (
        "fault_duration'a olu bant verilmis — benzer sureli ariza kaybolur"
    )


def test_ANLAMI_belirsiz_sinyale_tahmin_yapilmiyor():
    assert "test_point_level" not in _m0035()._DEADBANDS


def test_statik_metadataya_olu_bant_VERILMIYOR():
    """0034 onlari zaten `historize=false` yapti; olu bant anlamsiz olurdu."""
    d = _m0035()._DEADBANDS
    for ad in ("serial_number", "firmware_version", "hardware_revision"):
        assert ad not in d


def test_m0035_ZATEN_AYARLI_olani_ezmiyor():
    """Operator elle bir esik verdiyse migration onu geri almamali.

    Kontrol UPGRADE govdesine daraltildi: ilk yazimda tum dosyada
    `historize_deadband = 0` ariyordum ve kosulu WHERE'den kaldiran mutasyon
    GECTI, cunku ayni metin DOWNGRADE'de (`SET historize_deadband = 0`)
    duruyordu. Mutasyon testi yakaladi.
    """
    import ast
    import re

    ham = _M0035.read_text(encoding="utf-8")
    agac = ast.parse(ham)
    fn = next(
        d for d in agac.body
        if isinstance(d, ast.FunctionDef) and d.name == "upgrade"
    )
    govde = ast.get_source_segment(ham, fn) or ""
    govde = re.sub(r"^\s*#.*$", "", govde, flags=re.MULTILINE)
    assert "AND historize_deadband = 0" in govde, (
        "migration mevcut esikleri ezebilir — WHERE kosulunda "
        "`historize_deadband = 0` yok, yani elle verilmis esikler de "
        "ustune yazilir"
    )


def test_m0035_yalnizca_ARSIVLENENLERE():
    import re

    kod = re.sub(r'""".*?"""', "", _M0035.read_text(encoding="utf-8"), flags=re.DOTALL)
    assert "historize IS TRUE" in kod


def test_TUM_analoglar_artik_ele_alindi():
    """Yeni bir analog sinyal eklenirse burada gorulur — sessizce esiksiz
    kalmasin."""
    import json

    katalog = (
        Path(__file__).resolve().parents[1] / "app" / "data" / "horstmann_sn2_signals.json"
    )
    sig = json.loads(katalog.read_text(encoding="utf-8"))
    sig = sig if isinstance(sig, list) else sig["signals"]

    m33_birimler = {"A", "V", "\u00b0C"}
    m35 = set(_m0035()._DEADBANDS)
    # Bilerek esiksiz birakilanlar.
    bilinen_sifir = {"fault_duration", "test_point_level"}
    # 0034 arsivden cikardi.
    arsivsiz = {"serial_number", "firmware_version", "hardware_revision"}

    ele_alinmayan = set()
    for s in sig:
        if s.get("data_type") != "analog":
            continue
        sonek = s["key"].split(".", 1)[1]
        if s.get("unit") in m33_birimler:
            continue
        if sonek in m35 or sonek in bilinen_sifir or sonek in arsivsiz:
            continue
        ele_alinmayan.add(sonek)

    assert not ele_alinmayan, (
        f"su analog sinyaller hicbir karara baglanmadi: {sorted(ele_alinmayan)}"
    )
