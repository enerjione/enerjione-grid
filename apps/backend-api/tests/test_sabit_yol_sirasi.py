"""Sabit yollar, parametreli desenden ONCE tanimlanmali.

YASANAN ARIZA
-------------
Ariza Analizi sayfasi tamamen bos aciliyor ve ustte su hata cikiyordu:

    Dogrulama hatasi (fault_id): Input should be a valid integer

Sebep: `/faults/analytics` ucu `/faults/{fault_id}` deseninden SONRA
tanimlanmisti. FastAPI yollari SIRAYLA eslestirir; parametreli desen once
geldigi icin istek ona dusuyor ve "analytics" tam sayiya cevrilmeye
calisiliyordu — 422. Uc hicbir zaman calismadi.

Sessiz bir siniftir: kod dogru, test yesil, uc var; yalnizca SIRA yanlis.
Bu yuzden davranis degil YAPI kilitleniyor — yeni bir sabit yol yanlis yere
eklenirse bu test kirilir.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_API = Path(__file__).resolve().parents[1] / "app" / "api"

#: (dosya, router degiskeni) — parametreli desen iceren router'lar.
_DOSYALAR = ["faults.py", "map_tiles.py", "alarms.py", "devices.py", "grid_topology.py"]

_ROUTE = re.compile(r'@router\.(get|post|patch|put|delete)\(\s*[\'"]([^\'"]*)[\'"]')


def _yollar(dosya: str) -> list[tuple[int, str]]:
    p = _API / dosya
    if not p.exists():
        return []
    out: list[tuple[int, str]] = []
    for i, satir in enumerate(p.read_text(encoding="utf-8").split("\n"), start=1):
        m = _ROUTE.search(satir)
        if m:
            out.append((i, m.group(2)))
    return out


def _ilk_segment(yol: str) -> str:
    return yol.strip("/").split("/", 1)[0] if yol.strip("/") else ""


@pytest.mark.parametrize("dosya", _DOSYALAR)
def test_sabit_yol_parametreli_desenden_ONCE(dosya: str):
    yollar = _yollar(dosya)
    if not yollar:
        pytest.skip(f"{dosya} yok ya da router tanimi bulunamadi")

    # Ilk segmenti `{...}` olan ilk yol: bundan SONRA gelen her sabit ilk
    # segment yakalanamaz hale gelir.
    ilk_parametreli: tuple[int, str] | None = None
    for satir, yol in yollar:
        if _ilk_segment(yol).startswith("{"):
            ilk_parametreli = (satir, yol)
            break
    if ilk_parametreli is None:
        return

    p_satir, p_yol = ilk_parametreli
    golgede = [
        (satir, yol)
        for satir, yol in yollar
        if satir > p_satir and _ilk_segment(yol) and not _ilk_segment(yol).startswith("{")
    ]
    assert not golgede, (
        f"{dosya}: su sabit yollar `{p_yol}` (satir {p_satir}) tarafindan GOLGELENIYOR "
        f"— istek parametreli desene duser ve 422 doner:\n  "
        + "\n  ".join(f"satir {s}: {y}" for s, y in golgede)
        + "\n\nSabit yollari parametreli desenin USTUNE tasiyin."
    )
