"""Calisma zamani sema uyumluluk kontrolu — SALT OKURDUR.

NE YAPAR
--------
Veritabanindaki `alembic_version` degerini, BU IMAJIN bildigi head
revizyonuyla karsilastirir ve sonucu bir bayrakta tutar. `/health/ready`
bu bayragi okur.

NE YAPMAZ
---------
Hicbir sema DEGISIKLIGI yapmaz: `upgrade`, `downgrade`, `stamp`, `create_all`
ve "onarim" YOKTUR. Sema tasima ACIK bir deploy adimidir
(`python -m scripts.migrate_db`, uvicorn'dan ONCE kosar).

NEDEN VAR — YASANMIS DAVRANIS
-----------------------------
Eskiden backend acilista `Base.metadata.create_all()` cagirip ~124 idempotent
DDL ifadesi kosuyordu. Yani eksik sema SESSIZCE "onariliyor", uygulama yanlis
ya da yarim bir semayla ayaga kalkabiliyordu. Sema otoritesi fiilen calisma
zamanindaydi. Artik otorite Alembic; burasi yalnizca DOGRULAR.

NEDEN CRASH DEGIL, NOT READY
----------------------------
Startup'ta exception firlatmak uvicorn'u sonlandirir; `restart: unless-stopped`
ile appliance sonsuz crash-loop'a girer ve operator arayuze ulasip teshis bile
yapamaz. Bunun yerine surec ayakta kalir, `/health/ready` 503 doner (yani LB
trafigi kesmis olur) ve sebep log'a ACIK yazilir. Gercekten tehlikeli olan
durum — sema kodun ILERISINDE — zaten `migrate_db` tarafindan uvicorn
BASLAMADAN once yakalanir ve orada bilincli olarak sert biter.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text

log = logging.getLogger(__name__)

#: Son dogrulamanin sonucu. `/health/ready` bunu okur.
#: `uyumlu is None` -> kontrol henuz kosmadi (uygulama aciliyor).
DURUM: dict[str, object] = {
    "uyumlu": None,
    "beklenen": None,
    "gercek": None,
    "sebep": None,
}


def beklenen_revizyon() -> str | None:
    """Bu imajin `alembic_migrations/` dizinindeki head revizyonu."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        kok = Path(__file__).resolve().parents[2]  # apps/backend-api
        cfg = Config(str(kok / "alembic.ini"))
        cfg.set_main_option("script_location", str(kok / "alembic_migrations"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001
        log.debug("beklenen revizyon okunamadi", exc_info=True)
        return None


def _bilinen_revizyonlar() -> set[str]:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        kok = Path(__file__).resolve().parents[2]
        cfg = Config(str(kok / "alembic.ini"))
        cfg.set_main_option("script_location", str(kok / "alembic_migrations"))
        return {r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()}
    except Exception:  # noqa: BLE001
        return set()


def gercek_revizyon(bind) -> str | None:  # noqa: ANN001
    """DB'deki `alembic_version`. Tablo yoksa None. SADECE OKUR."""
    if not inspect(bind).has_table("alembic_version"):
        return None
    satir = bind.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    return satir[0] if satir else None


def dogrula(bind) -> tuple[bool, str]:  # noqa: ANN001
    """`(uyumlu_mu, sebep)`. Hicbir yan etkisi YOKTUR."""
    beklenen = beklenen_revizyon()
    if beklenen is None:
        # Migration dizini okunamadi — bunu "uyumsuz" saymak, calisan bir
        # sahayi teshis edilemez bicimde NOT READY yapardi.
        return True, "beklenen revizyon okunamadi (kontrol atlandi)"

    gercek = gercek_revizyon(bind)
    DURUM["beklenen"] = beklenen
    DURUM["gercek"] = gercek

    if gercek is None:
        return False, (
            "sema KURULMAMIS: `alembic_version` tablosu yok. "
            "`python -m scripts.migrate_db` calistirilmali."
        )
    if gercek == beklenen:
        return True, f"sema guncel ({gercek})"
    if gercek not in _bilinen_revizyonlar():
        return False, (
            f"sema bu surumden ILERIDE: DB {gercek!r} tasiyor, bu imaj "
            f"{beklenen!r} biliyor ve o migration dosyasi burada YOK. "
            "Daha yeni bir surume donun ya da yedekten geri yukleyin."
        )
    return False, (
        f"sema ESKI: DB {gercek!r}, beklenen {beklenen!r}. "
        "`python -m scripts.migrate_db` calistirilmali."
    )


def dogrula_ve_isaretle(engine) -> None:  # noqa: ANN001
    """Startup kancasi. Sonucu `DURUM`a yazar ve loglar. ASLA sema degistirmez."""
    try:
        with engine.connect() as bind:
            uyumlu, sebep = dogrula(bind)
    except Exception as exc:  # noqa: BLE001
        # DB'ye hic ulasilamiyor: bu bir SEMA karari degil, baglanti sorunu.
        # `/health/ready` zaten baglanti kontrolu yapiyor; burada uygulamayi
        # NOT READY'ye cakmak teshisi zorlastirirdi.
        log.warning("sema uyumluluk kontrolu yapilamadi: %s", exc)
        DURUM.update(uyumlu=None, sebep=f"kontrol yapilamadi: {exc}")
        return

    DURUM.update(uyumlu=uyumlu, sebep=sebep)
    if uyumlu:
        log.info("sema uyumlulugu: TAMAM (%s)", DURUM.get("gercek"))
    else:
        # Parola/DSN LOGLANMAZ; yalnizca revizyonlar ve yapilacak is.
        log.error(
            "SEMA UYUMSUZ — uygulama NOT READY. beklenen=%s gercek=%s sebep=%s",
            DURUM.get("beklenen"),
            DURUM.get("gercek"),
            sebep,
        )


def hazir_mi() -> tuple[bool, str]:
    """`/health/ready` icin. Kontrol kosmadiysa engellemez."""
    if DURUM["uyumlu"] is None:
        return True, str(DURUM.get("sebep") or "sema kontrolu kosmadi")
    return bool(DURUM["uyumlu"]), str(DURUM.get("sebep") or "")
