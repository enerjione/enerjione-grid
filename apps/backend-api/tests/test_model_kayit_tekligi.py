""""Temiz kurulumda olusmayan tablo" sinifini kapatan bekci (denetim 2026-08-13).

NEYI KORUR
----------
`scripts/migrate_db.py` semayi IKI ayri yoldan kurar:

  * TEMIZ kurulum (`alembic_version` yok): `Base.metadata.create_all()` +
    `alembic stamp head`  ->  migration'lar KOSMAZ.
  * MEVCUT kurulum: `alembic upgrade head`.

Yani temiz kurulumda bir tablonun olusmasinin TEK yolu, modelinin
`Base.metadata`'ya kayitli olmasidir. Eskiden hem `migrate_db.py` hem
`alembic_migrations/env.py` KENDI elle bakimli import listesini tutuyordu ve
ikisi de eksikti:

  * `migrate_db.py` 4 modeli kaciriyordu -> `gateway_health`,
    `device_purge_jobs`, `ftp_settings`, `device_model_settings` sifirdan
    kurulan sahada HIC OLUSMUYORDU. `gateway_health` yoklugu staleness
    watchdog'u her turda `UndefinedTable` ile dusuruyor, susmus gateway'in
    600 cihazi haritada ONLINE takili kaliyordu.
  * `env.py` 10 modeli kaciriyordu -> `alembic revision --autogenerate`
    onlar icin `op.drop_table` uretiyordu.

Ikisi de SESSIZ: gelistirici makinesi ve CI zaten yukseltme yolundan gectigi
icin her sey yesil kaliyordu. Bu yuzden bekci kaynak duzeyinde calisir.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

from app.db.base import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = BACKEND_DIR / "app" / "models"

# Model tanimlamayan yardimci moduller.
MODEL_DISI = {"__init__", "enums"}


def _tablo_adlari() -> set[str]:
    return {t.name for t in Base.metadata.tables.values()}


def test_yollar_BULUNDU():
    """Yol yanlissa asagidaki testler sessizce bos kume karsilastirir.

    Kaynak-duzeyi kontroller once dosyanin GERCEKTEN orada oldugunu
    dogrulamali; aksi halde bekci, korudugunu sandigi seyi korumaz.
    """
    assert (MODELS_DIR / "__init__.py").is_file(), MODELS_DIR
    moduller = [
        m.name for m in pkgutil.iter_modules([str(MODELS_DIR)])
        if m.name not in MODEL_DISI
    ]
    assert moduller, "app/models altinda model modulu bulunamadi"


def test_INIT_her_model_modulunu_import_ediyor():
    """Kaynak duzeyinde: `__init__.py` her model modulunu import etmeli.

    Calisma-ani kontrolu (asagidaki test) daha guclu ama bu daha ACIK bir
    hata mesaji verir: hangi modulun eklenmesi gerektigini dogrudan soyler.
    """
    kaynak = (MODELS_DIR / "__init__.py").read_text(encoding="utf-8")
    import_edilen = set(re.findall(r"^from app\.models\.(\w+) import", kaynak, re.M))
    dosyalar = {
        m.name for m in pkgutil.iter_modules([str(MODELS_DIR)])
        if m.name not in MODEL_DISI
    }
    eksik = sorted(dosyalar - import_edilen)
    assert eksik == [], (
        "Su model modulleri `app/models/__init__.py` icinde import EDILMEMIS: "
        f"{eksik}\n"
        "Sonucu SESSIZDIR: sifirdan kurulan cihazda bu tablolar HIC olusmaz "
        "(create_all onlari bilmez, `stamp head` de migration'i atlar) ve "
        "`--autogenerate` bir sonraki migration'a `op.drop_table` yazar."
    )


def test_app_models_TUM_modelleri_kaydediyor():
    """`import app.models` TEK BASINA tum tablolari Base'e baglamali.

    Bu testin korudugu sey tam olarak sudur: temiz kurulumda `create_all`
    yalnizca bu kumeyi kurar. Kume eksikse tablo sahada hic olusmaz ve
    hicbir hata mesaji cikmaz.
    """
    import app.models  # noqa: F401

    tek_kaynak = _tablo_adlari()

    # Simdi TUM model modullerini tek tek import et — yeni tablo cikarsa
    # `app/models/__init__.py` eksik demektir.
    eksik_moduller = []
    for mod in pkgutil.iter_modules([str(MODELS_DIR)]):
        if mod.name in MODEL_DISI:
            continue
        oncesi = _tablo_adlari()
        importlib.import_module(f"app.models.{mod.name}")
        yeni = _tablo_adlari() - oncesi
        if yeni:
            eksik_moduller.append((mod.name, sorted(yeni)))

    assert not eksik_moduller, (
        "Bu modeller `app/models/__init__.py` uzerinden kayitli DEGIL, yani "
        "temiz kurulumda tablolari HIC olusmaz (create_all gormez, "
        "`stamp head` de migration'i atlar):\n"
        + "\n".join(f"  app.models.{m} -> {t}" for m, t in eksik_moduller)
        + "\n\nCozum: modeli `app/models/__init__.py` icindeki import ve "
        "`__all__` listesine ekleyin."
    )

    assert len(tek_kaynak) == len(_tablo_adlari())


def test_migrate_db_KENDI_model_listesini_TUTMUYOR():
    """`migrate_db.py` yalnizca `import app.models` demeli.

    Ayri bir liste tutarsa kacinilmaz olarak eskir — dort tablonun sahada
    hic olusmamasinin sebebi tam olarak buydu.
    """
    kaynak = (BACKEND_DIR / "scripts" / "migrate_db.py").read_text(encoding="utf-8")
    kod = re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)

    assert re.search(r"^import app\.models\b", kod, flags=re.MULTILINE), (
        "migrate_db.py `import app.models` demiyor; create_all eksik bir "
        "metadata ile calisabilir."
    )
    assert not re.search(r"from app\.models import \(", kod), (
        "migrate_db.py yeniden KENDI model listesini tutuyor. Liste tek "
        "yerde (app/models/__init__.py) kalmali."
    )


def test_alembic_env_KENDI_model_listesini_TUTMUYOR():
    """`env.py` ayri liste tutarsa autogenerate `op.drop_table` uretir."""
    kaynak = (
        BACKEND_DIR / "alembic_migrations" / "env.py"
    ).read_text(encoding="utf-8")
    kod = re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)

    assert re.search(r"^import app\.models\b", kod, flags=re.MULTILINE), (
        "alembic_migrations/env.py `import app.models` demiyor."
    )
    tekil = re.findall(r"^import app\.models\.(\w+)", kod, flags=re.MULTILINE)
    assert not tekil, (
        "env.py yeniden tek tek model import ediyor "
        f"({tekil}). Liste eksildiginde autogenerate o tablolar icin "
        "`op.drop_table` uretir — tek kaynak app/models/__init__.py olmali."
    )


def test_gecmiste_KAYBOLAN_dort_tablo_kayitli():
    """Denetimde kaybolmus bulunan dort tablo icin adiyla regresyon kilidi."""
    import app.models  # noqa: F401

    adlar = _tablo_adlari()
    for tablo in (
        "gateway_health",
        "device_purge_jobs",
        "ftp_settings",
        "device_model_settings",
    ):
        assert tablo in adlar, (
            f"`{tablo}` Base.metadata'da yok — temiz kurulumda bu tablo "
            "olusmaz ve onu kullanan kod UndefinedTable ile patlar."
        )
