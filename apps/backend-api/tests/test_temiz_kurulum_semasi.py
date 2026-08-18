"""Temiz kurulum sozlesmesi — sema ALEMBIC'ten gelir, modelden DEGIL.

ONCEKI ARIZA (tarihce)
----------------------
`migrate_db.py` temiz veritabaninda `Base.metadata.create_all()` cagirip
semayi GUNCEL modellerden kuruyor, sonra "0006" damgaliyordu; ardindan
`upgrade head` 0007..head arasini bastan oynatiyor ve var olan kolona
carpiyordu:

    psycopg2.errors.DuplicateColumn: column "device_event_at" ...

Ara cozum "damgayi head yap" oldu. O da calisti ama SEMA OTORITESINI
modelde biraktu: migration gecmisi semayi tarif etmiyordu ve `stamp head`
yiyen sahalarda tablo kuran migration'lar BIR DAHA ASLA kosmuyordu.

BUGUNKU SOZLESME
----------------
    bos DB -> `alembic stamp <taban>` -> `alembic upgrade head`

`create_all` YOK. 0072 guncel semanin tamamini explicit operasyonlarla
kurar. Taban 0071'dir cunku 0001-0071 zinciri bos bir veritabaninda
GECILEMEZ (olculdu: 71 revizyonun 52'si "UndefinedTable" ile kirilir).

Bu dosya sozlesmenin iki ucunu da kilitler: taban ne head olabilir (yoksa
0072 atlanir ve sema HIC kurulmaz), ne de gerçekten oynatilamayacak kadar
geri gidebilir.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/migrate_db.py"
_VERSIONS = Path(__file__).resolve().parents[1] / "alembic_migrations" / "versions"


def _kaynak() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _taban_sabiti() -> str:
    """`migrate_db._TEMIZ_KURULUM_TABANI` degerini kaynaktan okur."""
    for dugum in ast.walk(ast.parse(_kaynak())):
        if isinstance(dugum, ast.Assign):
            for hedef in dugum.targets:
                if getattr(hedef, "id", None) == "_TEMIZ_KURULUM_TABANI":
                    return dugum.value.value
    raise AssertionError("_TEMIZ_KURULUM_TABANI bulunamadi — bootstrap degismis")


def _semayi_kuran_revizyon() -> str:
    """Guncel semayi materyalize eden migration (0072)."""
    dosyalar = sorted(_VERSIONS.glob("*0072*.py"))
    assert dosyalar, "0072 (sema materyalizasyonu) bulunamadi"
    return "0072"


def test_temiz_kurulum_create_all_KULLANMAZ() -> None:
    """Sema otoritesi Alembic; bootstrap modelden sema kurmamali."""
    agac = ast.parse(_kaynak())
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Attribute) and dugum.attr == "create_all":
            raise AssertionError(
                "temiz kurulum hala `create_all` cagiriyor — sema modelden "
                "uretiliyor demektir"
            )


def test_temiz_kurulum_tabani_HEAD_DEGIL() -> None:
    """Taban `head` olursa 0072 ATLANIR ve sema HIC kurulmaz.

    Bu, eski `stamp head` davranisinin tam olarak yeniden dogmasi olurdu:
    bos bir veritabani "guncelim" der, icinde tek tablo yoktur.
    """
    taban = _taban_sabiti()
    assert taban != "head", (
        "temiz kurulum head damgaliyor — 0072 kosmaz, sema bos kalir"
    )
    assert taban < _semayi_kuran_revizyon(), (
        f"taban ({taban}) sema migration'indan geride OLMALI, yoksa atlanir"
    )


def test_taban_semayi_kuran_revizyonun_HEMEN_ONCESI() -> None:
    """Taban ile sema migration'i arasinda baska revizyon KALMAMALI.

    Arada bir revizyon birakmak, o migration'in temiz kurulumda sessizce
    atlanmasi demektir — `stamp head` doneminde tam olarak bu oldu ve dort
    tablo (gateway_health, device_purge_jobs, ftp_settings,
    device_model_settings) sifirdan kurulan sahalarda HIC olusmadi.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    kok = Path(__file__).resolve().parents[1]
    cfg = Config(str(kok / "alembic.ini"))
    cfg.set_main_option("script_location", str(kok / "alembic_migrations"))
    script = ScriptDirectory.from_config(cfg)

    sema_rev = script.get_revision(_semayi_kuran_revizyon())
    assert sema_rev.down_revision == _taban_sabiti(), (
        f"{_semayi_kuran_revizyon()} taban {_taban_sabiti()!r} uzerine "
        f"gelmiyor (down_revision={sema_rev.down_revision!r}) — aradaki "
        "revizyonlar temiz kurulumda ATLANIR"
    )


def test_mevcut_kurulumda_upgrade_hala_kosuyor() -> None:
    """Mevcut cihazlar gercek gecmisi oynatmaya DEVAM etmeli.

    `upgrade` cagrisi kaldirilirsa saha cihazlari yeni migration'lari hic
    almaz ve sema sessizce geride kalir — ilk arizadan daha sinsi.
    """
    assert 'command.upgrade(config, "head")' in _kaynak(), (
        "upgrade cagrisi kaybolmus — mevcut kurulumlar migration almaz"
    )
