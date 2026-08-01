""""Yesil yalan" — veri yokken "sorun yok" gostermek (Faz 3-17).

DENETIMIN TANIMI
----------------
Bir ariza izleme urununde EN AGIR hata sinifi, sistemin BILMEDIGINI "sorun
yok" diye gostermesidir. Ariza sayfasinda tam olarak bu vardi:

  * `pollFaults` hatayi `catch { // ignore }` ile yutuyordu — hata durumu YOK.
  * Sayfaya `loading={false}` SABIT geciliyordu; `FaultListPage` icindeki
    DOGRU yazilmis "yukleniyor" dali bu yuzden OLU KODDU.
  * Dolayisiyla akis her zaman bir sonraki dala dusuyordu: yesil tik +
    "Aktif ariza yok — Sistem temiz".

  > Nobetci operator telefonla "X hattinda ariza var mi?" sorusunu alir,
  > sekmeyi acar, yesil tik gorur ve "yok" der. Gercekte istemci veriyi
  > getirememistir.

BU TESTLER NEYI KORUR
---------------------
Frontend'de test kosucusu yok (denetim: "Frontend: sifir test"). Bir test
altyapisi kurmak ayri bir is; ama bu desen KAYNAK DUZEYINDE yakalanabilir ve
geri gelmesi kolay oldugu icin ucuz bir bekci degerli.

NOT: metin araması bu depoda birkac kez KENDI aciklamalarina takildi. Bu
yuzden kontroller yorum satirlarini ELEYEREK yapiliyor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
FE = REPO / "apps" / "frontend-web" / "src"
APP_TSX = FE / "app" / "App.tsx"
FAULT_PAGE = FE / "features" / "faults" / "FaultListPage.tsx"


def _kod(yol: Path) -> str:
    """Dosyayi yorumlari ATILMIS halde dondurur.

    Aciklama metinleri bu depoda birkac kez testleri yanlis yere goturdu;
    denetlenen sey KOD olmali."""
    ham = yol.read_text(encoding="utf-8")
    ham = re.sub(r"/\*.*?\*/", "", ham, flags=re.DOTALL)   # blok yorum
    ham = re.sub(r"^\s*//.*$", "", ham, flags=re.MULTILINE)  # satir yorumu
    return ham


def test_dosyalar_BULUNDU():
    assert APP_TSX.is_file(), APP_TSX
    assert FAULT_PAGE.is_file(), FAULT_PAGE


def test_loading_SABIT_false_gecilmiyor():
    """`loading={false}` bir bilesenin yukleniyor dalini OLU KOD yapar.

    Bu, "yesil yalan"in en dogrudan bicimi: bilesen dogru yazilmis olsa bile
    cagiran taraf onu devre disi birakir.
    """
    kod = _kod(APP_TSX)
    kalanlar = re.findall(r"loading=\{false\}", kod)
    assert not kalanlar, (
        f"{len(kalanlar)} yerde `loading={{false}}` var — ilgili bilesenin "
        "yukleniyor dali olu kod olur ve veri yokken 'sorun yok' gorunur"
    )


def test_ariza_cekimi_hatayi_YUTMUYOR():
    """`catch { }` hata durumunu yok eder; ekran "temiz" gorunur."""
    kod = _kod(APP_TSX)
    m = re.search(r"const pollFaults = useCallback\(async \(\) => \{(.*?)\n  \}, \[", kod, re.DOTALL)
    assert m, "pollFaults bulunamadi"
    govde = m.group(1)

    # YALNIZCA CATCH BLOGU denetlenir.
    #
    # Ilk yazimda "govdede setFaultsError geciyor mu" diye bakiyordum ve bu
    # YETERSIZDI: basari yolunda da `setFaultsError("")` var, dolayisiyla
    # catch'i tamamen bosaltan bir mutasyon testi GECTI. Mutasyon testi
    # yakaladi.
    i = govde.find("catch")
    assert i != -1, "pollFaults'ta catch yok"
    catch_blogu = govde[i:]

    assert "setFaultsError" in catch_blogu, (
        "pollFaults HATA durumunda `setFaultsError` cagirmiyor — istemci "
        "veriyi alamasa bile sayfa 'Sistem temiz' gosterir"
    )
    assert not re.search(r"catch\s*\{\s*\}", govde), (
        "pollFaults hatayi hala sessizce yutuyor"
    )


def test_ariza_sayfasina_GERCEK_durum_geciliyor():
    kod = _kod(APP_TSX)
    assert "loading={faultsLoading}" in kod, "gercek yukleniyor durumu gecilmiyor"
    assert "error={faultsError}" in kod, "hata durumu sayfaya gecilmiyor"


def test_ilk_cekim_ONCESINDE_yukleniyor_sayiliyor():
    """Baslangic `false` olsaydi sayfa acilir acilmaz (veri yokken) yesil
    "Sistem temiz" gorunurdu — arizanin ta kendisi."""
    kod = _kod(APP_TSX)
    assert re.search(r"useState\(true\)\s*;?\s*$", kod, re.MULTILINE) or \
        "const [faultsLoading, setFaultsLoading] = useState(true)" in kod, (
        "faultsLoading `true` baslamiyor"
    )


def test_hata_dali_TEMIZ_dalindan_ONCE_geliyor():
    """Sira onemli: hata dali sonra gelirse akis yine yesil ekrana duser."""
    kod = _kod(FAULT_PAGE)
    i_hata = kod.find("error && activeFaults.length === 0")
    i_temiz = kod.find("faults.empty.systemClean")
    assert i_hata != -1, "FaultListPage'de hata dali YOK"
    assert i_temiz != -1, "'sistem temiz' dali bulunamadi"
    assert i_hata < i_temiz, (
        "hata dali 'Sistem temiz' dalindan SONRA — veri alinamadiginda yine "
        "yesil ekran gosterilir"
    )


@pytest.mark.parametrize("dil", ["tr", "en"])
def test_hata_basligi_CEVIRIDE_var(dil: str):
    """Eksik ceviri anahtari ekranda ham anahtar metni gosterir."""
    yol = FE / "shared" / "i18n" / "resources" / f"{dil}.json"
    d = json.loads(yol.read_text(encoding="utf-8"))
    assert d["faults"]["empty"].get("errorTitle"), f"{dil}: errorTitle yok"


def test_gecici_hatada_mevcut_liste_KORUNUYOR():
    """Hata aninda listeyi bosaltmak "ariza kayboldu" izlenimi verir —
    ayni yaniltmanin ters yonu."""
    kod = _kod(APP_TSX)
    m = re.search(r"const pollFaults = useCallback\(async \(\) => \{(.*?)\n  \}, \[", kod, re.DOTALL)
    assert m
    govde = m.group(1)
    hata_blogu = govde[govde.find("catch"):]
    assert "setFaults([])" not in hata_blogu, (
        "hata durumunda ariza listesi bosaltiliyor — operator arizanin "
        "cozuldugunu saniyor"
    )


# ---------------------------------------------------------------------------
# Binary rozetler: "veri yok" ve "guvenilmez" YESIL gosterilmemeli
#
# Eskiden yalnizca `value === 1` bakiliyordu ve UC AYRI durum ayni yesil
# "Normal" rozetini uretiyordu:
#
#   value === null -> HIC VERI YOK                     -> "Normal" (yanlis)
#   value === 0    -> gercekten normal                 -> "Normal" (dogru)
#   value === 0 ama kalite `comm_lost`                 -> "Normal" (TEHLIKELI)
#
# Ucuncusu en agiri: haberlesmesi kopan cihaz icin gateway `comm_lost`
# kalitesiyle 0.0 basiyor. Backend bu okumayi alarm degerlendirmesine
# SOKMUYOR (`ALARM_BLOCKING_QUALITIES`) — yani sunucu dogru davraniyor.
# Arayuz ise ayni degeri "taze" sanip yesil gosterip sunucunun kararini
# GECERSIZ KILIYORDU.
# ---------------------------------------------------------------------------

QUALITY_TS = FE / "shared" / "signalQuality.ts"
ALL_SIGNALS_TAB = FE / "features" / "device-detail" / "DeviceAllSignalsTab.tsx"
DETAIL_PAGE = FE / "features" / "device-detail" / "DeviceDetailPage.tsx"


def test_kalite_yardimcisi_VAR():
    assert QUALITY_TS.is_file(), "shared/signalQuality.ts yok"


def test_engelleyen_kaliteler_BACKEND_ILE_ayni():
    """Ayrisirsa arayuz ile alarm motoru ayni okuma icin FARKLI sey soyler."""
    from app.services.tag_engine_service import ALARM_BLOCKING_QUALITIES

    kod = _kod(QUALITY_TS)
    m = re.search(r"BLOCKING_QUALITIES[^=]*=\s*new Set\(\[(.*?)\]\)", kod, re.DOTALL)
    assert m, "signalQuality.ts icinde BLOCKING_QUALITIES bulunamadi"
    frontend = {s.strip().strip('"').strip("'") for s in m.group(1).split(",") if s.strip()}

    assert frontend == set(ALARM_BLOCKING_QUALITIES), (
        f"frontend {sorted(frontend)} != backend {sorted(ALARM_BLOCKING_QUALITIES)} — "
        "arayuz, backend'in guvenilmez saydigi bir olcumu 'Normal' gosterebilir"
    )


@pytest.mark.parametrize("yol", [DETAIL_PAGE, ALL_SIGNALS_TAB], ids=lambda p: p.name)
def test_binary_rozet_GUVENILIRLIGI_kontrol_ediyor(yol: Path):
    kod = _kod(yol)
    assert "signalTrust(" in kod, (
        f"{yol.name}: binary rozet yalnizca degere bakiyor — 'veri yok' ve "
        "'comm_lost kaliteli 0.0' yesil 'Normal' gorunur"
    )
    assert "is-unknown" in kod, f"{yol.name}: notr rozet sinifi kullanilmiyor"


@pytest.mark.parametrize("yol", [DETAIL_PAGE, ALL_SIGNALS_TAB], ids=lambda p: p.name)
def test_guvenilirlik_kontrolu_ROZETTEN_once(yol: Path):
    """Sira onemli: kontrol sonra gelirse akis yine yesil rozete duser."""
    kod = _kod(yol)
    i_trust = kod.find("signalTrust(")
    i_normal = kod.find('t("deviceDetail.status.normal")')
    assert i_trust != -1 and i_normal != -1
    assert i_trust < i_normal, (
        f"{yol.name}: guvenilirlik kontrolu 'Normal' rozetinden SONRA geliyor"
    )


@pytest.mark.parametrize("dil", ["tr", "en"])
def test_notr_rozet_cevirileri_VAR(dil: str):
    yol = FE / "shared" / "i18n" / "resources" / f"{dil}.json"
    d = json.loads(yol.read_text(encoding="utf-8"))
    for anahtar in ("noData", "untrusted"):
        assert d["deviceDetail"]["status"].get(anahtar), f"{dil}: {anahtar} yok"


# ---------------------------------------------------------------------------
# Canli veri rozeti: OLU KOD olmasin + soket durumunu "veri akiyor" sanmasin
#
# Denetim bu rozeti "yesil 'Canli' veri tazeligini degil soket durumunu
# gosteriyor" diye isaretlemisti. Kaynaga bakinca daha kotusu cikti: rozet
# HICBIR YERDE render EDILMIYORDU. `wsState` prop'u hem `Header`a hem
# `SystemStatusPage`e geciliyor ama IKISINDE DE okunmuyordu — yani soket
# olse bile ekranda hicbir isaret cikmiyor, bayat degerler sessizce duruyordu.
#
# Sunucu 30 sn'de bir `ping` attigi icin soket, gateway tamamen sussa bile
# "open" kalir; rozet yalnizca soket durumuna baksaydi yine yaniltirdi.
# Karar mantigi bu yuzden `shared/wsDataStatus.ts` icinde ve GERCEK testleri
# `apps/frontend-web/tests/index.test.ts` altinda calisiyor (node:test).
# Buradaki kontroller yalnizca "tekrar olu koda donmesin" bekcisi.
# ---------------------------------------------------------------------------

BADGE = FE / "components" / "WsStatusBadge.tsx"
WS_STATUS_TS = FE / "shared" / "wsDataStatus.ts"
HEADER = FE / "components" / "Header.tsx"
SYS_STATUS = FE / "features" / "system-status" / "SystemStatusPage.tsx"
FE_TESTS = FE.parent / "tests" / "index.test.ts"


@pytest.mark.parametrize("yol", [HEADER, SYS_STATUS], ids=lambda p: p.name)
def test_rozet_RENDER_ediliyor(yol: Path):
    """Prop'u alip kullanmamak, gecirmemekten daha kotudur: cagiran taraf
    durumu gosterdigini SANIR. Ikisi de `wsState` aliyor, ikisi de gostermeli.

    Aranan sey JSX ACILIS ETIKETI (`<WsStatusBadge`), adin kendisi
    DEGIL: ilk yazimda `"WsStatusBadge" in kod` diye bakiyordum ve bu
    YETERSIZDI — JSX'i tamamen silsem bile `import` satiri adi icerdigi icin
    test geciyordu. Mutasyon testi yakaladi.
    """
    kod = _kod(yol)
    assert "wsState?: WsConnectionState" in kod, f"{yol.name}: prop tanimi yok"
    assert "<WsStatusBadge" in kod, (
        f"{yol.name}: WsStatusBadge render EDILMIYOR — soket kopsa bile "
        "operator bunu ekranda goremez"
    )
    assert re.search(r"<WsStatusBadge[^>]*state=\{wsState\}", kod, re.DOTALL), (
        f"{yol.name}: rozete gercek soket durumu gecilmiyor"
    )
    assert re.search(r"<WsStatusBadge[^>]*lastDataAt=", kod, re.DOTALL), (
        f"{yol.name}: rozete son veri zamani gecilmiyor — rozet yine yalnizca "
        "soket durumunu gosterir"
    )


def test_tazelik_karari_SOKET_durumundan_ayri():
    kod = _kod(WS_STATUS_TS)
    assert "lastDataAt" in kod, "tazelik karari son veri zamanina bakmiyor"
    # Soket "open" iken hemen "live" donen bir kisayol olmamali.
    assert 'return "live"' in kod
    i_open = kod.find('state !== "open"')
    i_live = kod.find('return "live"')
    assert i_open != -1 and i_open < i_live, (
        "soket durumu kontrolu veri kontrolunden sonra geliyor"
    )


def test_ping_TAZELIK_sayilmiyor():
    """Sunucu 30sn'de bir ping atiyor; ping'i veri saymak rozeti kalici
    yesil yapardi — kapatmaya calistigimiz yanilmanin ta kendisi."""
    kod = _kod(FE / "shared" / "useLiveValuesSocket.ts")
    m = re.search(r"const flush = \(\) => \{(.*?)\n    \};", kod, re.DOTALL)
    assert m, "flush fonksiyonu bulunamadi"
    assert "setLastDataAt" in m.group(1), (
        "son veri zamani flush icinde isaretlenmiyor"
    )
    # `onmessage` icinde telemetry disindaki mesajlar zamani guncellememeli.
    m2 = re.search(r"ws\.onmessage = \(event\) => \{(.*?)\n      \};", kod, re.DOTALL)
    assert m2, "onmessage bulunamadi"
    assert "setLastDataAt" not in m2.group(1), (
        "son veri zamani onmessage icinde guncelleniyor — ping/hello de "
        "tazelik sayilir ve rozet gateway sussa bile yesil kalir"
    )


def test_frontend_testleri_CI_da_kosuyor():
    """Kosulmayan test, test degildir."""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "npm test" in ci, "frontend testleri CI'da kosulmuyor"
    assert FE_TESTS.is_file(), "frontend test dosyasi yok"
