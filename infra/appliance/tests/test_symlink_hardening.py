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


def _yukle(ad: str, state_dir: Path):
    """Ajani izole bir state dizini ile yukle."""
    if ad == "e1-rad":
        os.environ["E1_RAD_STATE_DIR"] = str(state_dir)
        os.environ["E1_RAD_PRIV_DIR"] = str(state_dir / "priv")
    elif ad == "e1-netd":
        os.environ["E1_NETD_STATE_DIR"] = str(state_dir)
    else:
        os.environ["E1_GWD_STATE_DIR"] = str(state_dir)
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
