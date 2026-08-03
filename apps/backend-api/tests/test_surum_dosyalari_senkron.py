"""Surum numarasi TUM dosyalarda ayni olmali.

YASANAN ARIZA
-------------
Surum cikarma akisi `VERSION`, `CLAUDE.md` ve `package.json`'i guncelliyor
ama `package-lock.json`'a DOKUNMUYORDU. CI ilk adimda `npm ci` kosuyor ve
`npm ci` package.json ile package-lock.json ayrisinca CALISMAYI REDDEDER.

Sonuc: 2.35.0'dan itibaren cikarilan her tag'de CI ilk adimda dustu ve
**hicbir imaj yayinlanamadi**. Kod tarafinda hicbir sey bozuk degildi;
yalnizca iki dosya birbirini tutmuyordu.

Bu sinif sessiz: yerelde `npm run build` ve `npm test` gecer (ikisi de
mevcut node_modules ile calisir), `npm ci` ise TEMIZ kurulum yaptigi icin
yalnizca CI'da patlar. Yani gelistirici makinesinde hicbir belirti yok.

Test surum cikarmadan ONCE yakalar.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_KOK = Path(__file__).resolve().parents[3]


def _version_dosyasi() -> str:
    return (_KOK / "VERSION").read_text(encoding="utf-8").strip()


def test_package_json_version_dosyasiyla_ayni() -> None:
    pkg = json.loads((_KOK / "apps/frontend-web/package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == _version_dosyasi(), (
        "VERSION ile frontend package.json ayrismis"
    )


def test_package_lock_package_json_ile_ayni() -> None:
    """`npm ci` bu ikisi ayrisinca CALISMAYI REDDEDER — CI ilk adimda duser.

    Lock'u elle duzenlemeyin: `npm install --package-lock-only` yeter.
    """
    beklenen = _version_dosyasi()
    lock = json.loads((_KOK / "apps/frontend-web/package-lock.json").read_text(encoding="utf-8"))

    assert lock.get("version") == beklenen, (
        f"package-lock.json kok surumu {lock.get('version')!r}, beklenen {beklenen!r}. "
        "`cd apps/frontend-web && npm install --package-lock-only` calistirin."
    )
    kok_paket = lock.get("packages", {}).get("", {})
    assert kok_paket.get("version") == beklenen, (
        f"package-lock.json packages[''] surumu {kok_paket.get('version')!r}, "
        f"beklenen {beklenen!r}"
    )


def test_claude_md_surumu_ayni() -> None:
    """Proje kilavuzundaki surum satiri da guncel kalmali — yeni katilan
    gelistirici ilk oradan bakiyor."""
    metin = (_KOK / "CLAUDE.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*S[uü]r[uü]m:\*\*\s*([0-9]+\.[0-9]+\.[0-9]+)", metin)
    assert m is not None, "CLAUDE.md icinde surum satiri bulunamadi"
    assert m.group(1) == _version_dosyasi(), "CLAUDE.md surumu VERSION ile ayrismis"


def test_changelog_bu_surumu_iceriyor() -> None:
    """Tag'lenen surumun degisiklik notu OLMALI.

    Notsuz cikan bir surumde sahadaki operator neyin degistigini
    ogrenemez; guncelleme karari bilgisiz verilir.
    """
    surum = _version_dosyasi()
    metin = (_KOK / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{surum}]" in metin, (
        f"CHANGELOG.md icinde [{surum}] basligi yok"
    )
