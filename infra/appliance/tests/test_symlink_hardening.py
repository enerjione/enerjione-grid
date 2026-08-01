"""Root ajanlar symlink TAKIP ETMEMELI (denetim A11 + A13).

YASANAN ACIK
------------
Uc ajan da (`e1-rad`, `e1-netd`, `e1-gwd`) ROOT olarak calisir ve durum
dosyalarini backend container'inin (uid 10001) YAZABILDIGI paylasilan bir
dizine yazar. `.tmp` dosyasi duz `open(tmp, "w")` (ya da O_TRUNC) ile
aciliyordu — yani container oraya onceden bir symlink birakip root'a istedigi
host dosyasini truncate + uzerine yazdirabiliyordu.

SOMUT SALDIRI
-------------
Container icinde:
    ln -s /etc/systemd/system/e1-rad-report.service <dizin>/state.json.tmp
30 saniye icinde timer ROOT olarak kosar, symlink'i TAKIP eder ve unit
dosyasini JSON ile ezer. Sonraki boot'ta sure-dolunca-kapatma zorlayicisi OLU
olur — uzaktan bakim ozelliginin TEK guvenlik garantisi sessizce devre disi
kalir. Ayni primitif /etc/shadow, /etc/sudoers.d/*, /etc/cron.d icin de
kullanilabilir; container'daki cap_drop / no-new-privileges / read_only
sertlestirmesinin TAMAMI bu tek satirdan asiliyordu.

`os.replace` sonrasi symlink kayboldugu icin IZ DE BIRAKMIYORDU.

NOT: O_NOFOLLOW POSIX'e ozgudur; bu testler Windows'ta atlanir. Ajanlar zaten
yalnizca Linux appliance uzerinde calisir.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

AJANLAR = ("e1-rad", "e1-netd", "e1-gwd")
APPLIANCE = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW yalnizca POSIX"
)


# Ajanlarin state dizini icin okudugu GERCEK env degiskenleri.
# DIKKAT: bunlar bir kez yanlis yazilmisti (`E1_NETD_...` / `E1_GWD_...`) ve
# testler izolasyonsuz kosuyordu — modul yine de yukleniyor, `_write_json`
# acik yol aldigi icin testler geciyordu. Yani "izole" gorunen ama olmayan bir
# kurulumdu. Arsiv dizini STATE_DIR'den TURETILDIGI icin asagidaki dizin
# testleri gercek izolasyon olmadan calismaz.
_STATE_ENV = {
    "e1-rad": "E1_RAD_STATE_DIR",
    "e1-netd": "E1_NET_STATE_DIR",
    "e1-gwd": "E1_GW_STATE_DIR",
}


def _yukle(ad: str, state_dir: Path):
    """Ajani izole bir state dizini ile yukle."""
    os.environ[_STATE_ENV[ad]] = str(state_dir)
    if ad == "e1-rad":
        os.environ["E1_RAD_PRIV_DIR"] = str(state_dir / "priv")
    yol = APPLIANCE / f"{ad}.py"
    spec = importlib.util.spec_from_file_location(ad.replace("-", "_"), yol)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("ajan", AJANLAR)
def test_symlink_tuzagi_HEDEFE_yazmiyor(ajan, tmp_path):
    """Testin ozu: tuzak kurulmusken yazma hedefi BOZMAMALI."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "priv").mkdir()
    mod = _yukle(ajan, state_dir)

    kurban = tmp_path / "kurban.service"
    kurban.write_text("[Unit]\nDescription=dokunulmamali\n", encoding="utf-8")
    orijinal = kurban.read_text(encoding="utf-8")

    hedef = state_dir / "state.json"
    os.symlink(kurban, f"{hedef}.tmp")   # container'in kurdugu tuzak

    # Yazma ya reddedilir ya da tuzagi temizleyip guvenle yazar; HER IKI
    # durumda da kurban dosyaya DOKUNULMAMALI.
    try:
        mod._write_json(str(hedef), {"x": 1})
    except OSError:
        pass

    assert kurban.read_text(encoding="utf-8") == orijinal, (
        f"{ajan}: root ajan symlink'i takip edip kurban dosyayi EZDI"
    )


@pytest.mark.parametrize("ajan", AJANLAR)
def test_normal_yazma_CALISIYOR(ajan, tmp_path):
    """Sertlestirme normal akisi bozmamali."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "priv").mkdir()
    mod = _yukle(ajan, state_dir)

    hedef = state_dir / "state.json"
    mod._write_json(str(hedef), {"schema": 1, "ok": True})
    assert json.loads(hedef.read_text(encoding="utf-8"))["ok"] is True


@pytest.mark.parametrize("ajan", AJANLAR)
def test_bayat_tmp_yazmayi_ENGELLEMIYOR(ajan, tmp_path):
    """O_EXCL tek basina kullanilsaydi cokme sonrasi kalan .tmp kalici kilitlerdi."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "priv").mkdir()
    mod = _yukle(ajan, state_dir)

    hedef = state_dir / "state.json"
    Path(f"{hedef}.tmp").write_text("bayat", encoding="utf-8")

    mod._write_json(str(hedef), {"schema": 1, "ok": True})
    assert json.loads(hedef.read_text(encoding="utf-8"))["ok"] is True


@pytest.mark.parametrize("ajan", AJANLAR)
def test_okuma_da_symlink_TAKIP_ETMIYOR(ajan, tmp_path):
    """Okuma tarafi da onemli: symlink ile dosya SIZDIRMA primitifi olurdu.

    Container paylasilan dizine /etc/shadow'a isaret eden bir symlink birakirsa,
    root ajan onu okuyup icerigini container'in OKUYABILDIGI durum dosyasina
    yansitabilirdi.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "priv").mkdir()
    mod = _yukle(ajan, state_dir)

    sir = tmp_path / "sir.json"
    sir.write_text(json.dumps({"parola": "gizli"}), encoding="utf-8")
    link = state_dir / "request.json"
    os.symlink(sir, link)

    assert mod._read_json(str(link)) is None, (
        f"{ajan}: root ajan symlink'i takip edip disaridaki dosyayi OKUDU"
    )


# ---------------------------------------------------------------------------
# DIZIN seviyesi — ilk duzeltmenin ATLADIGI kisim
#
# `_write_json`'in O_NOFOLLOW'u yalnizca yolun SON bilesenini korur. Ama
# `_archive_request` once ARCHIVE_DIR'i olusturuyor ve (e1-netd'de) chmod
# ediyordu. ARCHIVE_DIR = <STATE_DIR>/archive, yani container'in yazabildigi
# paylasilan dizinin ICINDE — container onu symlink ile degistirebilir:
#
#     mv .../archive .../.a && ln -s /usr/bin .../archive
#
# `makedirs(exist_ok=True)` symlink'i "zaten dizin" sayip gecer. Sonrasi:
#   e1-netd: os.chmod(ARCHIVE_DIR, 0o750) -> ROOT olarak /usr/bin 0750 olur.
#            Root olmayan hicbir surec binary calistiramaz; kiosk, SSH yonetim
#            hesabi, NetworkManager yardimcilari duser. KENDI KENDINE DUZELMEZ.
#   e1-gwd : root arsiv dosyasini saldirganin sectigi dizine yazar.
#
# Ayni imaj tum filoda oldugu icin tek bir backend acigi 600 cihazi ayni anda
# tuglalayabilirdi.
# ---------------------------------------------------------------------------

DIZIN_AJANLARI = ("e1-netd", "e1-gwd")


def _arsivle(mod) -> None:
    """Ajanin arsivleme yolunu tetikler (ikisinde de ayni imza)."""
    mod._archive_request({"id": "test-1", "action": "noop"}, {"ok": True})


@pytest.mark.parametrize("ajan", DIZIN_AJANLARI)
def test_arsiv_dizini_symlink_ise_HEDEFE_yazmiyor(ajan, tmp_path):
    """Tuzak dizin kurulmusken root, hedef dizine dosya BIRAKMAMALI."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    mod = _yukle(ajan, state_dir)

    kurban_dizin = tmp_path / "kurban_dizin"
    kurban_dizin.mkdir()
    # Container'in kurdugu tuzak: archive -> kurban_dizin
    os.symlink(kurban_dizin, Path(mod.ARCHIVE_DIR))

    try:
        _arsivle(mod)
    except OSError:
        pass  # reddedilmesi de kabul; onemli olan hedefin bozulmamasi

    assert list(kurban_dizin.iterdir()) == [], (
        f"{ajan}: root ajan symlink'lenmis dizini takip edip HEDEFE yazdi "
        f"({[p.name for p in kurban_dizin.iterdir()]})"
    )


def test_netd_arsiv_dizini_symlink_ise_IZIN_DEGISTIRMIYOR(tmp_path):
    """En agiri: `os.chmod(ARCHIVE_DIR, 0o750)` symlink'i takip ediyordu.

    Gercek saldiri hedefi /usr/bin idi; burada 0o777'lik bir kurban dizin
    kullaniyoruz. Izin degisirse cihaz sahada tuglalanmis demektir.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    mod = _yukle("e1-netd", state_dir)

    kurban_dizin = tmp_path / "usr_bin_taklidi"
    kurban_dizin.mkdir()
    os.chmod(kurban_dizin, 0o777)
    onceki = os.stat(kurban_dizin).st_mode & 0o777

    os.symlink(kurban_dizin, Path(mod.ARCHIVE_DIR))
    try:
        _arsivle(mod)
    except OSError:
        pass

    sonraki = os.stat(kurban_dizin).st_mode & 0o777
    assert sonraki == onceki, (
        "e1-netd: root ajan symlink'i takip edip hedef dizinin iznini "
        f"{oct(onceki)} -> {oct(sonraki)} olarak degistirdi (saha ziyareti gerektirir)"
    )


@pytest.mark.parametrize("ajan", DIZIN_AJANLARI)
def test_arsivleme_normal_akista_CALISIYOR(ajan, tmp_path):
    """Sertlestirme arsivlemeyi bozmamali — yoksa denetim izi kaybolur."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    mod = _yukle(ajan, state_dir)

    _arsivle(mod)

    arsiv = Path(mod.ARCHIVE_DIR)
    assert arsiv.is_dir(), f"{ajan}: arsiv dizini olusmadi"
    dosyalar = list(arsiv.glob("*.json"))
    assert len(dosyalar) == 1, f"{ajan}: arsiv dosyasi yazilmadi ({dosyalar})"
    icerik = json.loads(dosyalar[0].read_text(encoding="utf-8"))
    assert icerik["request"]["id"] == "test-1"


@pytest.mark.parametrize("ajan", DIZIN_AJANLARI)
def test_arsiv_dizini_dosya_ise_SESSIZCE_gecmiyor(ajan, tmp_path):
    """`archive` yerine DOSYA konursa yazma reddedilmeli, sessizce gecmemeli."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    mod = _yukle(ajan, state_dir)

    Path(mod.ARCHIVE_DIR).write_text("dizin degil", encoding="utf-8")

    try:
        _arsivle(mod)
    except OSError:
        pass

    # Dosya oldugu gibi kalmali; icine bir sey yazilmis olmamali.
    assert Path(mod.ARCHIVE_DIR).read_text(encoding="utf-8") == "dizin degil"
