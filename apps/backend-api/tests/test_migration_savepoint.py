""""En iyi caba" migration adimlari transaction'i BOZMAMALI (denetim A10).

YASANAN SORUN
-------------
0019 ve 0023 migration'lari, ortama gore basarisiz olabilecek adimlari
`_try(...)` ile sariyordu: hata yakalanip yutuluyor, "atlandi" deniyordu.

Ama hatayi YUTMAK TEK BASINA YETMEZ. PostgreSQL'de bir ifade patlayinca
transaction "aborted" duruma gecer ve o transaction'daki SONRAKI HER ifade
`InFailedSqlTransaction` ile duser — alembic'in kendi `UPDATE alembic_version`
ifadesi DAHIL. `env.py` tum migration'lari TEK transaction'da kosturuyor.

ZINCIR
------
    postgres:16'dan devralinmis dolu volume'de shared_preload_libraries yok
    -> `CREATE EXTENSION timescaledb` "could not access file" ile patlar
    -> `_try` hatayi yutar ve "atlandi" der
    -> ama transaction ARTIK BOZUK
    -> 0020-0023 ve `UPDATE alembic_version` de patlar
    -> `scripts.migrate_db` exception firlatir
    -> backend container CMD basarisiz -> SONSUZ CRASH-LOOP

Yani "en iyi caba" politikasi tam da onarmak icin yazildigi sahalari
tugluyordu.

0023'te durum daha da net: kod, "tam form" ALTER'in bazi TimescaleDB
surumlerinde reddedilecegini BEKLEYIP `if not ok:` ile "sade form"a dusuyor.
Savepoint olmadan ilk hata transaction'i abort ettigi icin PLANLANAN GERI
DUSME YOLU DA calisamiyordu.

COZUM: `bind.begin_nested()` (SAVEPOINT). Adim patlarsa yalnizca o savepoint'e
donulur; dis transaction saglam kalir.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "alembic_migrations" / "versions"


def _try_iceren_dosyalar() -> list[Path]:
    return [p for p in VERSIONS.glob("*.py") if "def _try(" in p.read_text(encoding="utf-8")]


def test_en_iyi_caba_dosyalari_BULUNDU():
    """Yol yanlissa testler sessizce yesil kalirdi."""
    assert _try_iceren_dosyalar(), "`_try` iceren migration bulunamadi"


@pytest.mark.parametrize("yol", _try_iceren_dosyalar(), ids=lambda p: p.name[:24])
def test_try_SAVEPOINT_kullanir(yol: Path):
    """En kritik koruma.

    `_try` savepoint'siz hale donerse, ortama bagli tek bir hata TUM
    migration zincirini ve alembic_version guncellemesini goturur; backend
    crash-loop'a girer ve saha cihazi acilmaz.
    """
    kaynak = yol.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    fn = next(
        (d for d in ast.walk(agac) if isinstance(d, ast.FunctionDef) and d.name == "_try"),
        None,
    )
    assert fn is not None
    govde = ast.dump(fn)
    assert "begin_nested" in govde, (
        f"{yol.name}: `_try` SAVEPOINT kullanmiyor — yutulan bir hata "
        "transaction'i abort eder ve sonraki TUM adimlar (alembic_version "
        "dahil) patlar"
    )


@pytest.mark.parametrize("yol", _try_iceren_dosyalar(), ids=lambda p: p.name[:24])
def test_try_hatayi_YINE_yutar(yol: Path):
    """Savepoint eklemek "en iyi caba" politikasini bozmamali.

    Adim patlayinca migration DURMAMALI; yalnizca o adim atlanmali.
    """
    kaynak = yol.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    fn = next(d for d in ast.walk(agac) if isinstance(d, ast.FunctionDef) and d.name == "_try")
    handlers = [d for d in ast.walk(fn) if isinstance(d, ast.ExceptHandler)]
    assert handlers, f"{yol.name}: hata artik yutulmuyor — tek adim tum boot'u durdurur"
    # `raise` ile yeniden firlatilmamali
    for h in handlers:
        for alt in ast.walk(h):
            assert not isinstance(alt, ast.Raise), (
                f"{yol.name}: `_try` hatayi yeniden firlatiyor"
            )


def test_savepoint_semantigi_DOGRU():
    """Davranis: SQLAlchemy `begin_nested` gercekten SAVEPOINT uretir mi.

    sqlite uzerinde de SAVEPOINT desteklenir; burada dogrulanan sey, ic
    blogun patlamasinin DIS transaction'i kullanilabilir birakmasidir.
    """
    from sqlalchemy import create_engine, text

    eng = create_engine("sqlite:///:memory:")
    with eng.connect() as conn:
        trans = conn.begin()
        conn.execute(text("CREATE TABLE t(x int)"))
        try:
            with conn.begin_nested():
                conn.execute(text("SELECT * FROM olmayan_tablo"))
        except Exception:
            pass  # hata yutuldu (mevcut politika)
        # Dis transaction SAGLAM olmali:
        conn.execute(text("INSERT INTO t VALUES (1)"))
        trans.commit()

    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM t")).scalar() == 1
