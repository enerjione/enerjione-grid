"""Akim sinyalleri TEK bir birim/olcek konvansiyonunda olmali.

YASANAN SORUN (2026-08-01, sahada fark edildi)
-----------------------------------------------
Katalogda AYNI fiziksel buyukluk iki farkli sekilde tanimliydi:

    actual_current            unit=A   scale=0.001
    trip_level                unit=mA  scale=1.0
    minimum/maximum/average/fault/last_good_known_current
                              unit=mA  scale=1.0

Cihaz ham veriyi mA gonderiyor; `actual_current` bunu ampere ceviriyordu,
diger altisinda donusum unutulmustu. Sonuc: ayni cihazda `actual_current`
0.37 A, `average_current` 608 goruntuluyordu — ayni buyukluk, 1000 kat
farkli sunum. Bu sinyallere kurulan alarm esikleri digerleriyle
KIYASLANAMAZ durumdaydi ve IEC 104 / Modbus cikislarina da tutarsiz olcekle
gidiyordu.

Bu testler kataloga yeni bir akim sinyali eklendiginde ayni tuzagin
tekrarlanmasini engelliyor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

KATALOG = (
    Path(__file__).resolve().parents[1] / "app" / "data" / "horstmann_sn2_signals.json"
)


def _sinyaller() -> list[dict]:
    d = json.loads(KATALOG.read_text(encoding="utf-8"))
    return d if isinstance(d, list) else d["signals"]


def _akim_sinyalleri() -> list[dict]:
    return [
        s for s in _sinyaller()
        if s.get("data_type") == "analog"
        and ("current" in s["key"] or s["key"].endswith(".trip_level"))
    ]


def test_akim_sinyalleri_BULUNDU():
    """Test kendi hedefini kaybetmesin: anahtar deseni degisirse burada durur."""
    assert len(_akim_sinyalleri()) >= 21, "akim sinyalleri bulunamadi (desen degismis?)"


def test_HIC_mA_kalmadi():
    kalan = [s["key"] for s in _sinyaller() if s.get("unit") == "mA"]
    assert not kalan, (
        f"mA birimli sinyal kalmis: {kalan[:5]} — cihaz ham veriyi mA "
        "gonderiyor, katalog ampere cevirmeli"
    )


def test_tum_akim_sinyalleri_AYNI_birim():
    birimler = {s.get("unit") for s in _akim_sinyalleri()}
    assert birimler == {"A"}, (
        f"akim sinyalleri farkli birimlerde: {birimler} — alarm esikleri "
        "kiyaslanamaz hale gelir"
    )


def test_tum_akim_sinyalleri_AYNI_olcek():
    olcekler = {s.get("scale") for s in _akim_sinyalleri()}
    assert olcekler == {0.001}, (
        f"akim sinyalleri farkli olceklerde: {olcekler} — ayni buyukluk "
        "1000 kat farkli sunulur"
    )


@pytest.mark.parametrize("kaynak", ["master", "sat01", "sat02"])
def test_UC_KAYNAK_da_ayni(kaynak: str):
    """Uydular master'dan farkli olursa set ozeti anlamsizlasir."""
    bunlar = [s for s in _akim_sinyalleri() if s["key"].startswith(f"{kaynak}.")]
    assert bunlar, f"{kaynak} icin akim sinyali yok"
    assert {s.get("unit") for s in bunlar} == {"A"}
    assert {s.get("scale") for s in bunlar} == {0.001}


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

_M0034 = (
    Path(__file__).resolve().parents[1] / "alembic_migrations" / "versions"
    / "2026_08_01_0008-0034_current_unit_fix.py"
)


def _m0034():
    import importlib.util

    spec = importlib.util.spec_from_file_location("m0034", _M0034)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_zinciri():
    m = _m0034()
    assert m.revision == "0034"
    assert m.down_revision == "0033"


def test_migration_KATALOGDAKI_sinyallerle_ayni_kumeyi_hedefliyor():
    """Migration mevcut kurulumlari, katalog yeni kurulumlari duzeltir.
    Ikisi ayrisirsa bir grup duzelmeden kalir."""
    m = _m0034()
    katalog_sonekleri = {
        s["key"].split(".", 1)[1] for s in _akim_sinyalleri()
        if s["key"].split(".", 1)[1] != "actual_current"
    }
    assert set(m._AKIM_SONEKLERI) == katalog_sonekleri, (
        f"migration {set(m._AKIM_SONEKLERI)} hedefliyor ama katalogda "
        f"{katalog_sonekleri} var"
    )


def test_migration_GERI_ALINABILIR():
    """AST ile bakiyoruz, metinle DEGIL.

    Ilk yazimda govdede `unit = 'mA'` metnini ariyordum; govdeye erken bir
    `return` koyup gerisini YORUM SATIRINA ceviren mutasyon GECTI, cunku
    metin hala oradaydi. Mutasyon testi yakaladi."""
    import ast

    agac = ast.parse(_M0034.read_text(encoding="utf-8"))
    fn = next(
        d for d in agac.body
        if isinstance(d, ast.FunctionDef) and d.name == "downgrade"
    )
    # Erken cikis olmamali.
    assert not any(isinstance(n, ast.Return) for n in fn.body), (
        "downgrade govdesinde erken `return` var — geri alma etkisiz"
    )
    # Govdede gercekten calisan bir op.execute olmali.
    cagrilar = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    assert cagrilar, "downgrade hicbir sey calistirmiyor"
    metin = ast.get_source_segment(_M0034.read_text(encoding="utf-8"), fn) or ""
    import re
    metin = re.sub(r"^\s*#.*$", "", metin, flags=re.MULTILINE)
    assert "unit = 'mA'" in metin and "scale = 1.0" in metin


def test_migration_ESKI_ARSIVI_yeniden_olceklendirMIYOR():
    """Bilincli karar: hangi satirin hangi olcekle yazildigini ayirt eden bir
    isaret yok. Migration iki kez calisirsa veriyi ikinci kez bolerdi ve bu
    GERI DONULEMEZ olurdu."""
    import re

    kod = re.sub(r'""".*?"""', "", _M0034.read_text(encoding="utf-8"), flags=re.DOTALL)
    for tehlikeli in ("telemetry_history", "UPDATE telemetry"):
        assert tehlikeli not in kod, (
            f"migration arsiv verisine dokunuyor ({tehlikeli}) — tekrar "
            "calistirilmasi veriyi ikinci kez olceklendirir"
        )
