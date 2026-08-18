"""Migration hedef veritabani secimi — ACIK precedence.

NEDEN AYRI DOSYA
----------------
Mantik `env.py` icinde yasasaydi test edilemezdi: `env.py` import edilir
edilmez migration KOSTURUR. Buradaki fonksiyonlar SAF (yan etkisiz), yani
`pytest` onlari gercek bir veritabani olmadan dogrulayabilir. Regresyon
testinin var olabilmesi icin gereken en kucuk ayrim budur.

YASANMIS OLAY
-------------
`env.py` eskiden hedefi KOSULSUZ olarak `settings.database_url` ile eziyordu:

    config.set_main_option("sqlalchemy.url", settings.database_url)

Cagiran hedefi ACIKCA verse bile sessizce yok sayiliyordu. Bir migration
testi bu yuzden `alembic upgrade head`i GELISTIRICININ veritabanina kosturdu
ve onu 0063'ten 0071'e tasidi.

PRECEDENCE
----------
    1. ACIKCA verilen Alembic URL   (Config.sqlalchemy.url, ozel .ini)
    2. E1_MIGRATION_DATABASE_URL    (migration'a ozel ortam degiskeni)
    3. settings.database_url        (uygulamanin yapilandirilmis DB'si)

1 veya 2 varsa 3'e DUSULMEZ.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Eski `alembic.ini` bu placeholder'i tasiyordu. Gercek bir hedef DEGIL;
#: "acikca verilmis URL" sayilmamali (yoksa her kurulum ona kosardi).
ESKI_PLACEHOLDER = "postgresql+psycopg2://placeholder@localhost/placeholder"

#: Ortam degiskeni adi — migration'a OZEL hedef. `DATABASE_URL` degil:
#: uygulamanin DB'si ile migration hedefi ayri kavramlar.
ORTAM_ADI = "E1_MIGRATION_DATABASE_URL"


def hedef_url(
    acik_url: str | None,
    ortam_url: str | None,
    ayar_url: str,
) -> tuple[str, str]:
    """Precedence'i uygular. `(url, kaynak)` dondurur.

    `kaynak` yalnizca log/teshis icindir; karar verici degildir.
    """
    acik = (acik_url or "").strip()
    if acik and acik != ESKI_PLACEHOLDER:
        return acik, "explicit alembic url"

    ortam = (ortam_url or "").strip()
    if ortam:
        return ortam, ORTAM_ADI

    return ayar_url, "settings.database_url"


def parolasiz(url: str) -> str:
    """DSN'i PAROLA SIZDIRMADAN ozetler: `host:port/dbname`.

    Log'a ham DSN yazmak parolayi diske dusurur; teshis icin gereken tek sey
    hangi sunucudaki hangi veritabanina kosuldugudur.
    """
    try:
        ayrik = urlsplit(url)
        konak = ayrik.hostname or "?"
        kapi = f":{ayrik.port}" if ayrik.port else ""
        ad = (ayrik.path or "").lstrip("/") or "?"
        return f"{konak}{kapi}/{ad}"
    except Exception:  # noqa: BLE001
        return "?"
