"""DB semasini legacy kurulumlardan Alembic head'e guvenle tasir."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.db.base import Base
from app.db.session import engine

# Tum metadata kayitlari create_all icin gerekli.
from app.models import (  # noqa: F401
    alarm,
    alarm_rule,
    api_key,
    backup,
    bulk_notification_job,
    bulk_notification_template,
    device,
    device_command,
    fault,
    gateway,
    gateway_ingest_batch,
    grid_topology,
    notification,
    notification_settings,
    outbound_target,
    outbox_event,
    processed_message,
    project_settings,
    responsibility_area,
    signal_catalog,
    system_event,
    telemetry,
    telemetry_history,
    user,
    user_fcm_token,
    user_notification_preference,
    user_session,
)

log = logging.getLogger(__name__)


def migrate() -> None:
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
