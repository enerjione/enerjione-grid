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
