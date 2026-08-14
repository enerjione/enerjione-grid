"""DB semasini legacy kurulumlardan Alembic head'e guvenle tasir."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.session import engine

# Tum metadata kayitlari create_all icin gerekli.
# Modellerin `Base.metadata`'ya kaydi icin TEK import — liste `app/models/__init__.py`
# icinde tutulur.
#
# ESKIDEN BURADA AYRI BIR LISTE VARDI ve eksikti: `gateway_health`,
# `device_purge_job`, `ftp_settings`, `device_model_settings` yoktu. Temiz
# kurulumda `create_all` bu tablolari kurmuyor, `stamp head` de onlari kuran
# migration'lari atliyordu — yani sifirdan kurulan sahada bu dort tablo HIC
# olusmuyordu. `gateway_health` yoklugu staleness watchdog'u her turda
# UndefinedTable ile dusurup susmus gateway'in cihazlarini haritada ONLINE
# takili birakiyordu. Liste iki yerde (burada ve alembic/env.py) elle
# tutuldugu surece tekrar kacar; bu yuzden tek kaynaga indirildi.
import app.models  # noqa: F401

log = logging.getLogger(__name__)

# Migration icin AYRI bir advisory lock anahtari.
# `service_role._BACKGROUND_LOCK_KEY` ile ayni OLMAMALI: o kilit surec omru
# boyunca tutulur, bu ise yalnizca migration suresince. Ayni anahtari
# paylassalardi migration biter bitmez liderlik de dusmus olurdu.
_MIGRATION_LOCK_KEY = 0x0E1_6D_1167

# Kilidi beklerken ust sinir. Sinirsiz beklemek container'i "starting"
# durumunda SESSIZCE asardi: healthcheck henuz baslamadigi icin dusmez,
# hicbir hata gorunmez, kimse sebebini bilmez.
_MIGRATION_LOCK_WAIT_SEC = 600


def migrate() -> None:
    """Semayi head'e tasir — kumede TEK SUREC, digerleri bekler.

    NEDEN KILIT: `backend-api` ve `backend-worker` AYNI imaji ve AYNI
    komutu (`python -m scripts.migrate_db && exec uvicorn ...`) kullanir ve
    ikisi de `postgres: service_healthy` kosuluyla ES ZAMANLI acilir. Iki
    surec ayni anda `upgrade head` kosarsa ayni revizyon iki kez oynatilir:

        psycopg2.errors.DuplicateColumn: column ... already exists

    Container cikar, `restart: unless-stopped` ile donguye girer. Genelde
    kendini toparlar ama uzun bir migration'da gorunur kesinti ve teshisi
    cok zor bir hata olur.

    `pg_try_advisory_lock` DEGIL `pg_advisory_lock`: ikinci surec ATLAMAMALI,
    BEKLEMELI. Atlarsa uvicorn'u eski semayla acar ve ilk sorguda patlar.

    KILIT ALINAMAZSA PATLAR (yutulmaz): tek sebebi onceki migration'in
    asili kalmasidir ve o durumda kilitsiz devam etmek tam da onlemek
    istedigimiz es zamanli upgrade'i yapardi. Container cikar, sebep log'da
    goruntur — sessizce yanlis semayla acilmaktan iyidir.
    """
    if engine.dialect.name != "postgresql":
        # SQLite (testler/gelistirme): advisory lock yok, es zamanli ikinci
        # surec de yok. Kilitsiz kosmak dogru davranis.
        log.debug("advisory lock desteklenmeyen backend — migration kilitsiz kosuyor")
        _migrate_locked()
        return

    # AUTOCOMMIT: kilidi tutan baglanti migration boyunca "idle in
    # transaction" kalmamali — o durumda VACUUM ilerleyemez ve uzun bir
    # migration sirasinda tablo sismesi hizlanir.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_conn:
        # lock_timeout SADECE bu baglantiyi baglar; alembic kendi
        # baglantilarinda kosar ve bundan etkilenmez.
        lock_conn.execute(text(f"SET lock_timeout = '{_MIGRATION_LOCK_WAIT_SEC}s'"))
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY})
        try:
            _migrate_locked()
        finally:
            try:
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY}
                )
            except Exception:  # noqa: BLE001
                # Baglanti nasil olsa kapaniyor; session-level kilit onunla duser.
                log.debug("migration advisory unlock basarisiz", exc_info=True)


def _migrate_locked() -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic_migrations"))
    if not inspect(engine).has_table("alembic_version"):
        # TEMIZ KURULUM: `create_all` semayi GUNCEL MODELLERDEN kurar, yani
        # sonuc dogrudan HEAD semasidir. Damga da head olmali.
        #
        # ESKIDEN "0006" DAMGALANIYORDU ve kurulum bu yuzden COKUYORDU:
        # sema zaten eksiksizken alembic 0007..head arasini bastan
        # oynatiyor, ilk kolon ekleyen migration var olan kolona carpip
        # patliyordu:
        #
        #     psycopg2.errors.DuplicateColumn: column "device_event_at"
        #     of relation "telemetry_history" already exists
        #
        # Backend acilamiyor -> healthcheck dusuyor -> kurulum
        # "backend-api is unhealthy" ile duruyor. HER TEMIZ KURULUMDA olur;
        # 0025 yalnizca sirada ILK carpandi (0007, 0020, 0024, 0026, 0027,
        # 0028, 0032, 0036 da ayni riski tasiyordu). Tek tek yamalamak
        # kostebek oyunu olurdu — kaynak burasi.
        Base.metadata.create_all(bind=engine)
        command.stamp(config, "head")
    else:
        # MEVCUT kurulum: gercek gecmisi oynat.
        command.upgrade(config, "head")

    # ---- Historian depolamasi: hypertable + saklama politikalari ----------
    #
    # SEMADAN AYRI BIR ADIM, cunku bunlar SQLAlchemy modelinde tarif
    # EDILEMEZ. Yukaridaki temiz-kurulum dali semayi `create_all` ile
    # modellerden kuruyor; `create_all` ise YALNIZCA DUZ TABLOLARI yaratir.
    # Hypertable'a cevirme ve 90 gunluk saklama politikasi migration
    # govdesinde yasadigi icin, hicbir migration kosmayan temiz kurulumda
    # sessizce ATLANIYORDU:
    #
    #     "Arsiv tablosu hypertable'a cevrilmemis — saklama suresi politikasi
    #      calismiyor, tablo sinirsiz buyuyor."
    #
    # Belirti disk dolana kadar ortaya cikmaz (600 cihazda gunde ~26M satir).
    #
    # HER ACILISTA kosuyor, yalnizca temiz kurulumda degil: extension ilk
    # boot'ta hazir olmayabilir ve sonradan dogru hale gelebilir. Adim
    # idempotent; zaten kuruluysa birkac katalog sorgusu maliyeti var.
    #
    # Hata YUTULUR: burada patlamak backend'in hic ayaga kalkmamasi demek.
    # Tespit garantisi ayri bir yerde — Sistem Durumu sayfasi eksikligi
    # raporluyor (`app/services/historian_service.py`).
    try:
        from app.db.timescale_setup import ensure_historian_storage

        with engine.begin() as bind:
            rapor = ensure_historian_storage(bind)
        if rapor.get("actions"):
            log.info("historian depolama kurulumu: %s", ", ".join(rapor["actions"]))
        elif rapor.get("skipped"):
            log.info("historian depolama kurulumu atlandi: %s", rapor["skipped"])
    except Exception as exc:  # noqa: BLE001
        log.warning("historian depolama kurulumu basarisiz (atlaniyor): %s", exc)


if __name__ == "__main__":
    migrate()
