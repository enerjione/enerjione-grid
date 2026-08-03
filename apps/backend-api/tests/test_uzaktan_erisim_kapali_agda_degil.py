"""Uzaktan erisim: izin yokken cihaz TAILNET'TE DEGIL.

NEDEN DEGISTI
-------------
Onceki varsayilan `shields` idi: cihaz agda KALIR, tum gelen baglantilari
reddederdi. Teknik olarak guvenli, ama musteri acisindan cihaz her an bizim
agimizda duruyor ve "girmiyoruz" sozune guvenmek zorunda kaliyordu.

`down` modunda cihaz agda DEGILDIR. Musteri bunu kendi guvenlik duvarinda
"hic trafik yok" diye DOGRULAYABILIR. Guven soze degil olcume dayanir.

Kayitlilik korunur: `logout` HICBIR ZAMAN calistirilmaz, authkey ajanda
yoktur — izin verilince ajan tuneli kendisi geri kaldirir.

BEDELI (bilerek): kapaliyken dugum OFFLINE gorunur; "elektrik yok",
"internet yok", "cihaz bozuk" ve "izin verilmemis" ayirt edilemez.

BU TESTLERIN KORUDUGU SEY
-------------------------
Varsayilanin sessizce `shields`e donmesi. Geri donerse hicbir sey KIRILMAZ —
sistem calismaya devam eder, testler yesil kalir, cihaz da sessizce surekli
agda durur. Yani bu, kendini belli etmeyen bir gerileme; test olmadan
farkedilmesi icin birinin cihazin agda olup olmadigini elle kontrol etmesi
gerekirdi.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_KOK = Path(__file__).resolve().parents[3]
_AJAN = _KOK / "infra" / "appliance" / "e1-rad.py"
_KURULUM = _KOK / "infra" / "appliance" / "setup-remote-access.sh"


def _ajani_yukle(monkeypatch, env: dict[str, str] | None = None):
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("E1_RAD_LOCK_MODE", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("e1rad_test", _AJAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_varsayilan_mod_down(monkeypatch) -> None:
    """Ayar verilmediginde cihaz izin yokken agdan CIKMALI."""
    ajan = _ajani_yukle(monkeypatch)
    assert ajan.LOCK_MODE == "down", (
        "varsayilan `shields`e donmus — cihaz izin yokken de tailnet'te kalir "
        "ve musteri 'agda degiliz' garantisini dogrulayamaz"
    )


def test_shields_hala_secilebilir(monkeypatch) -> None:
    """Canlilik sinyalinin sart oldugu kurulumlar eski davranisa donebilmeli."""
    ajan = _ajani_yukle(monkeypatch, {"E1_RAD_LOCK_MODE": "shields"})
    assert ajan.LOCK_MODE == "shields"


def test_gecersiz_mod_GUVENLI_tarafa_duser(monkeypatch) -> None:
    """Yazim hatasi sessizce agda kalmaya yol acmamali."""
    ajan = _ajani_yukle(monkeypatch, {"E1_RAD_LOCK_MODE": "sheilds"})
    assert ajan.LOCK_MODE == "down"


def test_kurulum_scripti_de_down_varsayiyor() -> None:
    """Ajan ve kurulum scripti ayni varsayilani kullanmali.

    Ayrisirlarsa kurulum `shields` yazar, ajan `down` bekler ve cihaz
    beklenenden farkli davranir — kimse fark etmez.
    """
    metin = _KURULUM.read_text(encoding="utf-8")
    m = re.search(r'RA_LOCK="\$\{RA_LOCK:-([a-z]+)\}"', metin)
    assert m is not None, "setup-remote-access.sh icinde RA_LOCK varsayilani bulunamadi"
    assert m.group(1) == "down"


def test_logout_hicbir_yerde_calistirilmiyor() -> None:
    """Kayitlilik KORUNMALI.

    `tailscale logout` cihazi tailnet KAYDINDAN duserirdi; geri almak icin
    sahaya yeni bir authkey goturmek gerekirdi. `down` yalnizca tuneli
    indirir, kaydi degil. Bu ayrim ozelligin calisabilir kalmasinin sarti.
    """
    kaynak = _AJAN.read_text(encoding="utf-8")
    # Yorumlarda gecebilir; CAGRI olarak gecmemeli.
    cagrilar = re.findall(r'_run\(\s*"logout"', kaynak)
    assert not cagrilar, "ajan `tailscale logout` calistiriyor — kayitlilik kaybolur"
