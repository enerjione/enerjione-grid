"""nats-server.conf template'indeki degisiklik SAHAYA ULASIYOR mu?

YASANAN SORUN
-------------
Canli `infra/nats/nats-server.conf` template'ten BIR KEZ uretiliyor (update.sh
render blogu) ve `.gitignore`'da — yani sifre hash'leri iceren yerel bir
dosya. update.sh yeniden render'i yalnizca DAR kosullarda tetikliyor:
dosya yoksa, ya da bilinen bir "eski surum izi" varsa.

Sonuc: template'te bir degeri degistirmek (ornegin hesap seviyesi
`max_file_store` tavani) zaten kurulu cihazlarda HICBIR SEY yapmaz. Degisiklik
merge edilir, release cikar, "duzelttik" denir — ve sahadaki her cihaz eski
degerle kosmaya devam eder.

TERS TUZAK
----------
Tetikleyici, template'in GUNCEL degerini ararsa kosul her update'te dogru
kalir: NATS her guncellemede yeniden render edilip RESTART edilir (update.sh
`docker compose up -d --force-recreate nats`). Yani tetikleyici bir kez ise
yarayip sonra susmalidir.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "infra" / "nats" / "nats-server.conf.template"
UPDATE_SH = REPO / "update.sh"


def _template_max_file_store() -> str:
    m = re.search(
        r"^\s*max_file_store:\s*(\S+)\s*$",
        TEMPLATE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert m, "template icinde max_file_store bulunamadi"
    return m.group(1)


def _render_tetikleyicileri() -> list[str]:
    """update.sh'in "eski conf" tespiti icin aradigi max_file_store degerleri."""
    return re.findall(
        r"grep -q ['\"]max_file_store:\s*(\S+?)['\"]",
        UPDATE_SH.read_text(encoding="utf-8"),
    )


def test_hesap_tavani_degisikligi_KURULU_cihazlara_yayiliyor():
    """Template'teki tavan degistiyse update.sh eski degeri tanimali.

    Aksi halde duzeltme yalnizca YENI kurulumlara gider; sahadaki cihazlar
    eski tavanla kalir ve kimse fark etmez.
    """
    tetikleyiciler = _render_tetikleyicileri()
    assert tetikleyiciler, (
        "update.sh'te max_file_store icin yeniden-render tetikleyicisi yok — "
        "template degisikligi kurulu cihazlara ULASMAZ"
    )


def test_render_tetikleyicisi_SONSUZ_DONGU_yapmiyor():
    """Tetikleyici template'in GUNCEL degerini aramamali.

    Ararsa kosul her update'te saglanir; NATS her guncellemede yeniden render
    edilip restart edilir — her seferinde kisa bir telemetri kesintisi.
    """
    guncel = _template_max_file_store()
    assert guncel not in _render_tetikleyicileri(), (
        f"update.sh guncel degeri ({guncel}) 'eski conf' isareti sayiyor — "
        "her guncellemede NATS gereksiz yere yeniden render/restart edilir"
    )
