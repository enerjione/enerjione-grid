"""Bransman noktasina CIHAZ BAGLANABILMELI.

YASANAN SORUN
-------------
Bir bransman kolu ayri bir hattir (`Line.branched_from_pole_id` ana hattaki
dallanma diregini gosterir). Kolun ILK segmenti dogasi geregi iki FARKLI
hattin direklerini birlestirir:

    ana hattaki dallanma diregi  ->  kolun ilk diregi

Segment dogrulamasi "iki direk de ayni hatta olmali" diyordu; bu segment
reddediliyor ve dallanma noktasina cihaz baglanamiyordu. Oysa saha acisindan
en kritik olcum noktalarindan biri orasi: dala giden akimi goren cihaz,
arizanin ANA HATTA mi KOLDA mi oldugunu ayirt eder.
"""

from __future__ import annotations

import inspect

from app.api import grid_topology


def test_dogrulama_BRANSMAN_istisnasini_biliyor():
    kaynak = inspect.getsource(grid_topology._validate_segment_endpoints)
    assert "branched_from_pole_id" in kaynak, (
        "segment dogrulamasi bransman kolunu tanimiyor — dallanma noktasina "
        "cihaz baglanamaz"
    )


def test_istisna_DAR_tutulmus():
    """Keyfi iki hat birbirine baglanamamali; yalnizca kolun kendi
    dallanma diregi `from_pole` olabilir."""
    kaynak = inspect.getsource(grid_topology._validate_segment_endpoints)
    # `to_pole` hala bu hatta ait olmak ZORUNDA.
    assert "to_pole.line_id != line_id" in kaynak
    # `from_pole` istisnasi kimlik esitligiyle sinirli.
    assert "from_pole.id == branch_parent_id" in kaynak


def test_arayuz_bransman_SLOTUNU_uretiyor():
    """Backend izin verse de arayuz slot uretmezse cihaz yine eklenemez."""
    from pathlib import Path

    panel = (
        Path(__file__).resolve().parents[2]
        / "frontend-web" / "src" / "features" / "grid" / "GridManagementPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "branchParent" in panel, (
        "GridManagementPanel bransman baglanti slotunu uretmiyor — slot listesi "
        "yalnizca ardisik direk ciftlerinden olusuyor"
    )
