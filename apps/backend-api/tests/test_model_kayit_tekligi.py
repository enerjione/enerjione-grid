"""HER MODEL `app/models/__init__.py` UZERINDEN KAYITLI OLMALI.

NEDEN BU TEST VAR
-----------------
`scripts/migrate_db.py` semayi IKI ayri yoldan kurar:

  * TEMIZ kurulum (`alembic_version` yok): `Base.metadata.create_all()` +
    `alembic stamp head`. Migration'lar KOSMAZ.
  * MEVCUT kurulum: `alembic upgrade head`.

Yani sifirdan kurulan bir cihazda bir tablonun olusmasinin TEK yolu,
modelinin `Base.metadata`ya kayitli olmasidir. Kayit `import app.models`
ile yapiliyor; oradaki liste eksik kalirsa:

  * `create_all` o tabloyu kurmaz VE onu kuran migration da `stamp head`
    yuzunden kosmaz  ->  tablo sifirdan kurulan cihazda HIC OLUSMAZ,
  * `alembic revision --autogenerate` onu "DB'de var, modelde yok" sanip
    yeni migration'a `op.drop_table` yazar.

Ikisi de SESSIZDIR: gelistirici makinesi ve CI zaten yukseltme yolundan
gectigi icin yesil kalir. 2026-08-13 denetiminde `gateway_health`,
`device_purge_jobs`, `ftp_settings` ve `device_model_settings` tam boyle
kaybolmustu; `gateway_health`in yoklugu staleness watchdog'u her turda
dusurup susmus gateway'in cihazlarini haritada ONLINE takili birakiyordu.

NEDEN STATIK KONTROL
--------------------
Calisma zamaninda `Base.metadata`ya bakmak yaniltici olurdu: baska bir test
ya da conftest o modulu ZATEN import etmis olabilir ve tablo metadata'da
gorunur — yani test, `__init__.py` eksik olsa bile YESIL kalir. Bu yuzden
kontrol kaynak metni uzerinden yapiliyor: `app/models/` altindaki her model
dosyasi `__init__.py` tarafindan import ediliyor mu?
"""

from __future__ import annotations

import re
from pathlib import Path

MODELS = Path(__file__).resolve().parents[1] / "app" / "models"
INIT = MODELS / "__init__.py"


def _model_modulleri() -> list[str]:
    """`app/models/` altinda TABLO tanimlayan modullerin adlari.

    Olcut `__tablename__`: tablo tanimlamayan yardimci modul (ornegin
    `enums.py`) `__init__.py`de import edilmek ZORUNDA degildir.
    """
    adlar = []
    for yol in sorted(MODELS.glob("*.py")):
        if yol.name == "__init__.py":
            continue
        if "__tablename__" in yol.read_text(encoding="utf-8"):
            adlar.append(yol.stem)
    return adlar


def _init_import_edilenler() -> set[str]:
    kaynak = INIT.read_text(encoding="utf-8")
    return set(re.findall(r"^from app\.models\.(\w+) import", kaynak, re.M))


def test_model_dosyasi_BULUNDU():
    """Yol yanlissa test sessizce bos kume karsilastirir; once onu dogrula."""
    assert INIT.exists(), f"bulunamadi: {INIT}"
    assert _model_modulleri(), "app/models altinda tablo tanimlayan modul bulunamadi"


def test_her_model_INIT_uzerinden_kayitli():
    eksik = sorted(set(_model_modulleri()) - _init_import_edilenler())
    assert eksik == [], (
        "Su model modulleri `app/models/__init__.py` icinde import EDILMEMIS: "
        f"{eksik}\n\n"
        "Sonucu sessizdir: sifirdan kurulan cihazda bu tablolar HIC olusmaz "
        "(create_all onlari bilmez, `alembic stamp head` de migration'i "
        "atlar) ve `--autogenerate` bir sonraki migration'a `op.drop_table` "
        "yazar. Modul eklendiyse `__init__.py`ye de eklenmeli."
    )


def test_migrate_db_KENDI_listesini_tutmuyor():
    """Liste iki yerde elle tutuldugu surece yine ayrisir.

    Bu yuzden `migrate_db.py` ve `alembic_migrations/env.py` artik model
    model import etmiyor, yalnizca `import app.models` diyor.
    """
    for goreli in ("scripts/migrate_db.py", "alembic_migrations/env.py"):
        yol = Path(__file__).resolve().parents[1] / goreli
        if not yol.exists():
            continue
        kaynak = yol.read_text(encoding="utf-8")
        # `from app.models import (alarm, device, ...)` gibi modul modul
        # listeleme geri gelmemeli.
        assert not re.search(r"from app\.models import \(", kaynak), (
            f"{goreli} yeniden KENDI model listesini tutuyor. Liste tek yerde "
            "kalmali (app/models/__init__.py); aksi halde biri eksik kalir ve "
            "sifirdan kurulan sahada tablo olusmaz."
        )
