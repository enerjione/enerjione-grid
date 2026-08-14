"""Alembic migration ortami — env.py.

`Settings.database_url`'tan `sqlalchemy.url`'i override eder; production'da
secret .env'den gelir, alembic.ini'ye yazilmaz.

Autogenerate icin `app.db.base.Base.metadata` referans alinir — tum SQLAlchemy
model'lerini import eden `app.models.__init__` paketi (veya main.py'deki
yan etkili imports) Base'e baglar.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# `app/` paketini PYTHONPATH'a ekle — alembic CLI baska dizinden cagrilsa bile
# `from app.core.config import settings` calismali.
_HERE = Path(__file__).resolve().parent
_BACKEND_DIR = _HERE.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402

# Model'lerin Base'e register olmasi icin TEK import — liste
# `app/models/__init__.py` icinde tutulur.
#
# ESKIDEN BURADA AYRI BIR LISTE VARDI ve 10 model eksikti (bulk_notification_*,
# device_command, device_config, device_model_settings, device_purge_job,
# ftp_settings, gateway_health, telemetry_latest, user_session). Autogenerate
# bu tablolari "DB'de var ama modelde yok" sanip onlar icin `op.drop_table`
# uretiyordu — gozden kacan tek bir migration `telemetry_latest`i ya da
# `user_sessions`i her saha cihazindan dusurebilirdi.
import app.models  # noqa: F401,E402

config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DB URL — Settings'tan oku; alembic.ini placeholder'i override.
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Offline mode: SQL'i URL kullanmadan dosyaya yaz."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Postgres-spesifik: hangi alanlar production'da gercek server-side
        # default'a sahip; autogenerate karsilastirirken kullanir.
        compare_server_default=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: DB connection ile migration'lari uygula."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_server_default=True,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
