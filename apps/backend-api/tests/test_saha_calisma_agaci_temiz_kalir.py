"""Saha cihazinda `update.sh` calisma agacini temiz bulmali.

YASANAN ARIZA
-------------
`update.sh`, guncellemeden once repo calisma agacinin TEMIZ olmasini sart
kosuyor. Kurulum script'leri ise repo dizininin ICINE calisma-zamani
dizinleri aciyor. Bu dizinler `.gitignore`da degilse git onlari takipsiz
dosya olarak gorur ve update DURUR:

    X Repo'da commit edilmemis lokal degisiklik var:
        ?? gateways/
    KURULUM DURDU

Somut vaka: `setup-gateway-agent.sh`, `/opt/enerjione-grid/gateways`
dizinini aciyordu (root:root 0750, icinde per-gateway compose + TOKEN).
Ignore edilmedigi icin gateway kurulu HER saha cihazinda guncelleme
basarisiz oluyordu — ve bu her guncellemede tekrarliyordu.

Tek tek arizalanip elle duzeltilecek bir sey degil: kurulum script'lerine
yeni bir dizin eklendiginde ayni arıza sessizce geri gelir. Test bunu
kaynaktan yakalar.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_KOK = Path(__file__).resolve().parents[3]

#: Kurulum/ajan script'lerinin `INSTALL_DIR` altinda dizin actigi yerler.
_SCRIPT_DIZINI = _KOK / "infra" / "appliance"


def _script_lerin_actigi_dizinler() -> set[str]:
    """`mkdir -p "${INSTALL_DIR}/<ad>"` kaliplarini toplar."""
    desen = re.compile(
        r'mkdir\s+-p\s+"?\$\{?(?:INSTALL_DIR|E1_ROOT|APP_DIR)\}?/([A-Za-z0-9_.-]+)'
    )
    bulunan: set[str] = set()
    if not _SCRIPT_DIZINI.is_dir():
        return bulunan
    for yol in _SCRIPT_DIZINI.rglob("*.sh"):
        bulunan |= set(desen.findall(yol.read_text(encoding="utf-8", errors="ignore")))
    return bulunan


def test_kurulum_scriptlerinin_actigi_dizinler_ignore_edilmis() -> None:
    dizinler = _script_lerin_actigi_dizinler()
    assert dizinler, (
        "infra/appliance altinda `mkdir -p ${INSTALL_DIR}/...` bulunamadi — "
        "desen ya da dizin yapisi degismis olabilir"
    )

    ignore_edilmeyen = []
    for ad in sorted(dizinler):
        sonuc = subprocess.run(
            ["git", "check-ignore", "-q", f"{ad}/"],
            cwd=_KOK,
            capture_output=True,
        )
        if sonuc.returncode != 0:
            ignore_edilmeyen.append(ad)

    assert not ignore_edilmeyen, (
        "Bu dizinleri kurulum script'leri repo icinde aciyor ama .gitignore "
        "kapsamiyor. Saha cihazinda `update.sh` calisma agacini kirli bulup "
        "DURACAK:\n  "
        + "\n  ".join(f"{a}/" for a in ignore_edilmeyen)
        + "\n\n.gitignore'a ekleyin."
    )


def test_gateways_dizini_ignore_edilmis() -> None:
    """Somut vaka ayrica kilitleniyor.

    Yukaridaki test desen tabanli; script yeniden yazilirsa (orn. mkdir
    satiri degisirse) sessizce hicbir sey kontrol etmez hale gelebilir.
    Bu test o durumda bile `gateways/` korumasini ayakta tutar.

    Ayrica guvenlik: bu dizindeki compose dosyalari GATEWAY_TOKEN tasiyor,
    yanlislikla commit'lenmemeli.
    """
    sonuc = subprocess.run(
        ["git", "check-ignore", "-q", "gateways/"], cwd=_KOK, capture_output=True
    )
    assert sonuc.returncode == 0, (
        "`gateways/` .gitignore kapsaminda degil — gateway ajani kurulu her "
        "saha cihazinda update.sh duracak, ustelik dizin GATEWAY_TOKEN iceriyor"
    )
