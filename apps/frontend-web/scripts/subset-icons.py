#!/usr/bin/env python3
"""
Material Symbols subset uretici.

NEDEN: material-symbols npm paketi ~3600 ikonluk TEK bir variable font
gonderiyor (material-symbols-outlined.woff2 = 3.83 MB). Biz ~250 ikon
kullaniyoruz. Font `font-display: block` ile yuklendigi icin, font inene
kadar TUM ikonlar gorunmez kutu olarak duruyor -- 4G sahada ilk acilis
onlarca saniye ikonsuz geciyordu.

NE YAPAR: kaynak kodda gecen ikon adlarini toplar, sadece o glyph'leri
iceren bir woff2 uretir (src/assets/fonts/). Variable eksenler (FILL, wght,
GRAD, opsz) KORUNUR -- styles.css bunlarin dordunu de kullaniyor, instance
alinirsa dolu/ince ikon varyantlari bozulur.

KULLANIM (ikon ekleyip/cikardiktan sonra tekrar calistir):
    cd apps/frontend-web
    pip install "fonttools[woff]" brotli
    python scripts/subset-icons.py

Cikti dogrulanir: uretilen fontta her ikon adi icin ligature var mi diye
kontrol edilir, eksik varsa hata verip cikar (sessizce bozuk font uretmez).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PKG = ROOT / "node_modules" / "material-symbols"
SOURCE_FONT = PKG / "material-symbols-outlined.woff2"
DECL = PKG / "index.d.ts"
OUT_DIR = SRC / "assets" / "fonts"
OUT_FONT = OUT_DIR / "material-symbols-outlined-subset.woff2"

# Ligature girdisi olan karakterler -- ikon adlari bu alfabeden olusur.
# Bunlar olmadan liga kurallari calismaz.
LIGATURE_CHARS = "abcdefghijklmnopqrstuvwxyz_0123456789"

# Kodda ikon adiyla ayni yazilan ama ikon OLMAYAN string'ler cikabilir
# (ornegin "map", "search"). Bunlari da almak zararsiz: birkac fazla glyph
# 3.8 MB'lik farkin yaninda olculemez. Eksik birakmak ise ikonu bozar --
# bu yuzden kasitli olarak GENIS taraniyor.
SCAN_SUFFIXES = {".ts", ".tsx", ".css"}


def ligature_map(font_path: Path = SOURCE_FONT) -> dict[str, str]:
    """
    Fontun GSUB tablosundan {ikon adi -> glyph adi} eslemesi.

    index.d.ts'deki ada guvenmiyoruz: bazi ikonlar (ornegin "location_on")
    fontta baska bir glyph adi altinda duruyor ve ada gore subset denemesi
    "MissingGlyphs" ile patliyor. Tek dogru kaynak fontun kendi ligature
    tablosu -- ikon adini olusturan harf dizisi hangi glyph'e cikiyorsa o.
    """
    from fontTools.ttLib import TTFont

    font = TTFont(font_path)
    # glyph -> karakter (ligature girdilerini metne cevirmek icin).
    char_of = {glyph: chr(code) for code, glyph in font.getBestCmap().items()}

    mapping: dict[str, str] = {}
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for sub in lookup.SubTable:
            # Lookup'lar Extension (type 7) sarmali icinde geliyor.
            sub = getattr(sub, "ExtSubTable", sub)
            for first, ligs in getattr(sub, "ligatures", {}).items():
                head = char_of.get(first)
                if head is None:
                    continue
                for lig in ligs:
                    tail = [char_of.get(comp) for comp in lig.Component]
                    if any(ch is None for ch in tail):
                        continue
                    mapping[head + "".join(tail)] = lig.LigGlyph
    return mapping


def used_names(valid: set[str]) -> set[str]:
    """Kaynak kodda gecen ve gecerli ikon adi olan tum token'lar."""
    found: set[str] = set()
    for path in SRC.rglob("*"):
        if path.suffix not in SCAN_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # String literal'ler ("x" / 'x' / `x`) VE JSX text child'lari (>x<).
        for token in re.findall(r"['\"`>]\s*([a-z0-9_]{2,})\s*['\"`<]", text):
            if token in valid:
                found.add(token)
    return found


def main() -> None:
    if not SOURCE_FONT.exists():
        sys.exit(f"HATA: {SOURCE_FONT} yok -- once 'npm install' calistir.")

    ligatures = ligature_map()
    icons = sorted(used_names(set(ligatures)))
    if not icons:
        sys.exit("HATA: hic ikon bulunamadi -- tarama regex'i bozulmus olabilir.")
    glyphs = sorted({ligatures[name] for name in icons})

    print(f"Fontta ikon (ligature)   : {len(ligatures)}")
    print(f"Kodda kullanilan ikon    : {len(icons)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --no-layout-closure KRITIK: bu bayrak olmadan pyftsubset, verilen
    # harflerden uretilebilen TUM ligature'lari (yani tum 3600 ikonu)
    # korur ve font hic kuculmez.
    cmd = [
        sys.executable, "-m", "fontTools.subset",
        str(SOURCE_FONT),
        f"--text={LIGATURE_CHARS}",
        f"--glyphs={','.join(glyphs)}",
        # DIKKAT: bu font ligature'lari 'liga' DEGIL 'rlig'/'rclt' altinda
        # tutuyor. 'liga' yazilirsa kurallar dusen feature'la birlikte silinir
        # ve ikonlar yerine ikon ADI duz metin olarak gorunur.
        "--layout-features=rlig,rclt",
        "--no-layout-closure",
        "--flavor=woff2",
        f"--output-file={OUT_FONT}",
    ]
    subprocess.run(cmd, check=True)

    verify(icons)

    before = SOURCE_FONT.stat().st_size
    after = OUT_FONT.stat().st_size
    print(
        f"\n{SOURCE_FONT.name}: {before / 1024:,.0f} kB"
        f"  ->  {OUT_FONT.name}: {after / 1024:,.0f} kB"
        f"  ({100 * (1 - after / before):.1f}% kucuk)"
    )


def verify(icons: list[str]) -> None:
    """
    Uretilen fontta her ikonun ligature'i gercekten calisiyor mu?

    Glyph korunmus ama ligature kurali dusmusse ikon yerine duz metin
    ("notifications") gorunur -- sessiz ve cirkin bir bozulma. O yuzden
    cikti fontu kaynak gibi yeniden cozulup ad-ad karsilastiriliyor.
    """
    from fontTools.ttLib import TTFont

    produced = ligature_map(OUT_FONT)
    missing = [name for name in icons if name not in produced]
    if missing:
        sys.exit(f"HATA: {len(missing)} ikon subset'e girmedi: {missing[:20]}")

    # Variable eksenler duruyor mu (styles.css FILL/wght/GRAD/opsz kullaniyor).
    axes = {axis.axisTag for axis in TTFont(OUT_FONT)["fvar"].axes}
    for required in ("FILL", "wght", "GRAD", "opsz"):
        if required not in axes:
            sys.exit(f"HATA: '{required}' ekseni kayboldu -- instance alinmis olabilir.")

    print(f"Dogrulama OK: {len(icons)} ikon + ligature, eksenler {sorted(axes)}")


if __name__ == "__main__":
    main()
