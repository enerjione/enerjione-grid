"""Temiz kurulumda alembic gecmisi BASTAN OYNATILMAMALI.

YASANAN ARIZA
-------------
`migrate_db.py` temiz veritabaninda `Base.metadata.create_all()` cagirip
semayi GUNCEL modellerden kuruyor — yani sonuc dogrudan head semasi. Ama
damga olarak "0006" yaziliyordu ve ardindan `upgrade head` 0007..head
arasini bastan oynatiyordu.

Sema zaten eksiksiz oldugu icin ilk kolon ekleyen migration carpiyordu:

    psycopg2.errors.DuplicateColumn: column "device_event_at"
    of relation "telemetry_history" already exists

Backend acilamiyor -> healthcheck dusuyor -> kurulum "backend-api is
unhealthy" ile duruyor. HER TEMIZ KURULUMDA oluyordu (cihaz log'unda
"PostgreSQL init process complete" satiri veritabaninin yeni oldugunu
gosteriyor).

0025 yalnizca sirada ILK carpandi; 0007/0020/0024/0026/0027/0028/0032/0036
da DDL yapiyor ve ayni riski tasiyordu.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/migrate_db.py"


def _agac() -> ast.AST:
    return ast.parse(_SCRIPT.read_text(encoding="utf-8"))


def _stamp_argumanlari() -> list[str]:
    return [
        d.args[1].value
        for d in ast.walk(_agac())
        if isinstance(d, ast.Call)
        and getattr(d.func, "attr", None) == "stamp"
        and len(d.args) > 1
        and isinstance(d.args[1], ast.Constant)
    ]


def test_temiz_kurulum_head_damgalar() -> None:
    """`create_all` head semasini kurar; damga da head olmali.

    "0006" damgalamak, var olan semaya 30 migration'i tekrar uygulamak
    demekti ve kurulum ilk carpismada oluyordu.
    """
    damgalar = _stamp_argumanlari()
    assert damgalar, "stamp cagrisi bulunamadi — bootstrap degismis"
    assert "head" in damgalar, (
        f"temiz kurulumda head yerine {damgalar} damgalaniyor — "
        "alembic gecmisi bastan oynatilir ve kurulum coker"
    )
    assert "0006" not in damgalar, "eski (cokerten) damga geri gelmis"


def test_mevcut_kurulumda_upgrade_hala_kosuyor() -> None:
    """Mevcut cihazlar gercek gecmisi oynatmaya DEVAM etmeli.

    `upgrade` cagrisi kaldirilirsa saha cihazlari yeni migration'lari hic
    almaz ve sema sessizce geride kalir — ilk arizadan daha sinsi.
    """
    kaynak = _SCRIPT.read_text(encoding="utf-8")
    assert "command.upgrade(config, \"head\")" in kaynak, (
        "upgrade cagrisi kaybolmus — mevcut kurulumlar migration almaz"
    )
