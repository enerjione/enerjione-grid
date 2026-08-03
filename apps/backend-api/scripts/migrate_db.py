"""DB semasini legacy kurulumlardan Alembic head'e guvenle tasir."""

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


if __name__ == "__main__":
    migrate()
