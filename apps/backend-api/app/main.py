import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select as _select, text

from app.api import alarm_rules, alarms, auth, device_models, devices, events, gateways, health, internal, notification_settings, outbound_targets, project_settings as project_settings_api, responsibility_areas, signals, telemetry, users
from app.core.config import settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import alarm, alarm_rule, device, gateway, gateway_ingest_batch, notification_settings as notification_settings_model, outbound_target, outbox_event, processed_message, project_settings as project_settings_model, responsibility_area as responsibility_area_model, signal_catalog, system_event, telemetry as telemetry_model, user  # noqa: F401
from app.services.iec104.bootstrap import deploy_all_active_targets, undeploy_all as iec104_undeploy_all
from app.services.outbox_service import flush_outbox
from app.services.signal_catalog_seed import seed_default_signals
from app.services import telemetry_consumer, telemetry_retention

app = FastAPI(title=settings.app_name)

_cors_origins = settings.cors_origin_list
if "*" in _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(devices.router, prefix=settings.api_prefix)
app.include_router(device_models.router, prefix=settings.api_prefix)
app.include_router(responsibility_areas.router, prefix=settings.api_prefix)
app.include_router(gateways.router, prefix=settings.api_prefix)
app.include_router(telemetry.router, prefix=settings.api_prefix)
app.include_router(alarms.router, prefix=settings.api_prefix)
app.include_router(events.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(notification_settings.router, prefix=settings.api_prefix)
app.include_router(outbound_targets.router, prefix=settings.api_prefix)
app.include_router(signals.router, prefix=settings.api_prefix)
app.include_router(alarm_rules.router, prefix=settings.api_prefix)
app.include_router(internal.router, prefix=settings.api_prefix)
app.include_router(project_settings_api.router, prefix=settings.api_prefix)


@app.on_event("startup")
def create_tables():
    Base.metadata.create_all(bind=engine)
    # ALTER TYPE ADD VALUE transaction icinde calistirilamaz; autocommit kullan.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as ac_conn:
        ac_conn.execute(text("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'INSTALLER'"))
    with engine.begin() as connection:
        # Keep Windows-first setup easy by ensuring newly added columns exist.
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number VARCHAR(32)"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(120)"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS acknowledged BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS reset BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS reset_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS signal_key VARCHAR(120)"))
        connection.execute(
            text(
                "ALTER TABLE gateways ADD COLUMN IF NOT EXISTS upstream_url VARCHAR(500) "
                "DEFAULT 'https://central.example.com/api/v1/telemetry/gateway'"
            )
        )
        connection.execute(text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS batch_interval_sec INTEGER DEFAULT 5"))
        connection.execute(text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS max_devices INTEGER DEFAULT 200"))
        connection.execute(text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS device_code_prefix VARCHAR(80)"))
        # Uzaktan yonetim icin control_host / control_port kolonlari.
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS control_host VARCHAR(255) NOT NULL DEFAULT '127.0.0.1'")
        )
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS control_port INTEGER NOT NULL DEFAULT 0")
        )
        # Per-gateway RabbitMQ cred (otomatik provisionlanir; manual rabbitmqctl gerek yok)
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS rabbitmq_username VARCHAR(120)")
        )
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS rabbitmq_password VARCHAR(255)")
        )
        connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS description VARCHAR(500)"))
        connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS gateway_code VARCHAR(50)"))
        connection.execute(
            text(
                "ALTER TABLE devices ADD COLUMN IF NOT EXISTS model VARCHAR(80) "
                "NOT NULL DEFAULT 'horstmann_sn_2_0'"
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_devices_model ON devices (model)"))
        connection.execute(
            text(
                "ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS model VARCHAR(80) "
                "NOT NULL DEFAULT 'horstmann_sn_2_0'"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS idx_signal_catalog_model ON signal_catalog (model)")
        )
        connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS dnp3_address INTEGER DEFAULT 1"))
        connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS poll_interval_sec INTEGER DEFAULT 5"))
        connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS timeout_ms INTEGER DEFAULT 3000"))
        connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 2"))
        connection.execute(
            text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS signal_profile VARCHAR(80) DEFAULT 'horstmann_sn2_fixed'")
        )
        connection.execute(
            text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS dnp3_outstation_port INTEGER NOT NULL DEFAULT 20001")
        )
        connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS dnp3_extended JSONB"))
        # DNP3 Group 110 (Octet String) sinyalleri icin: numeric value NULL'a
        # dusebilmeli (cunku string sinyalde sayi gelmez), ek olarak metin
        # icerigi value_string kolonunda saklanir. Daha onceki versiyonlarda
        # value NOT NULL Float idi; mevcut tabloyu nullable'a alir, yeni
        # kolonu ekleriz.
        connection.execute(text("ALTER TABLE telemetry ALTER COLUMN value DROP NOT NULL"))
        connection.execute(text("ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS value_string TEXT"))
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS gateway_ingest_batches ("
                "id SERIAL PRIMARY KEY, "
                "gateway_code VARCHAR(50) NOT NULL, "
                "sequence_no INTEGER NOT NULL, "
                "sent_at TIMESTAMPTZ NOT NULL, "
                "created_at TIMESTAMPTZ NOT NULL, "
                "CONSTRAINT uq_gateway_sequence UNIQUE (gateway_code, sequence_no))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS notification_settings ("
                "id INTEGER PRIMARY KEY, "
                "smtp_enabled BOOLEAN NOT NULL DEFAULT FALSE, "
                "smtp_host VARCHAR(255) NOT NULL DEFAULT '', "
                "smtp_port INTEGER NOT NULL DEFAULT 25, "
                "smtp_username VARCHAR(255) NOT NULL DEFAULT '', "
                "smtp_password VARCHAR(255) NOT NULL DEFAULT '', "
                "smtp_from_email VARCHAR(255) NOT NULL DEFAULT '', "
                "sms_enabled BOOLEAN NOT NULL DEFAULT FALSE, "
                "sms_provider VARCHAR(80) NOT NULL DEFAULT 'mock', "
                "sms_api_url VARCHAR(500) NOT NULL DEFAULT '', "
                "sms_api_key VARCHAR(255) NOT NULL DEFAULT '')"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS outbound_targets ("
                "id SERIAL PRIMARY KEY, "
                "name VARCHAR(120) UNIQUE NOT NULL, "
                "protocol VARCHAR(20) NOT NULL, "
                "endpoint VARCHAR(500) NOT NULL DEFAULT '', "
                "topic VARCHAR(255), "
                "event_filter VARCHAR(40) NOT NULL DEFAULT 'all', "
                "auth_header VARCHAR(255), "
                "auth_token VARCHAR(255), "
                "qos INTEGER NOT NULL DEFAULT 0, "
                "retain BOOLEAN NOT NULL DEFAULT FALSE, "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE)"
            )
        )
        # IEC 60870-5-104 sunucu alanlari (rest/mqtt icin NULL kalir).
        connection.execute(text("ALTER TABLE outbound_targets ALTER COLUMN endpoint DROP NOT NULL"))
        connection.execute(text("ALTER TABLE outbound_targets ALTER COLUMN endpoint SET DEFAULT ''"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS listen_host VARCHAR(255)"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS listen_port INTEGER"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS iec104_common_address INTEGER"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS iec104_ioa_device_stride INTEGER"))
        # Whitelist (NULL/'' = serbest)
        connection.execute(
            text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS iec104_allowed_peers VARCHAR(2000)")
        )
        # Proje ayarlari (singleton; logo + isimler + batarya esikleri)
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS project_settings ("
                "id INTEGER PRIMARY KEY DEFAULT 1, "
                "project_name VARCHAR(200), "
                "customer_name VARCHAR(200), "
                "customer_logo TEXT, "
                "customer_logo_light TEXT"
                ")"
            )
        )
        connection.execute(
            text("ALTER TABLE project_settings ADD COLUMN IF NOT EXISTS battery_voltage_low DOUBLE PRECISION")
        )
        connection.execute(
            text("ALTER TABLE project_settings ADD COLUMN IF NOT EXISTS battery_voltage_full DOUBLE PRECISION")
        )
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS outbox_events ("
                "id SERIAL PRIMARY KEY, "
                "topic VARCHAR(120) NOT NULL, "
                "dedup_key VARCHAR(120) UNIQUE NOT NULL, "
                "payload_json TEXT NOT NULL, "
                "published BOOLEAN NOT NULL DEFAULT FALSE, "
                "created_at TIMESTAMPTZ NOT NULL, "
                "published_at TIMESTAMPTZ)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS processed_messages ("
                "id SERIAL PRIMARY KEY, "
                "consumer_name VARCHAR(80) NOT NULL, "
                "message_id VARCHAR(120) NOT NULL, "
                "processed_at TIMESTAMPTZ NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_processed_message_consumer_msg "
                "ON processed_messages (consumer_name, message_id)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS signal_catalog ("
                "id SERIAL PRIMARY KEY, "
                "key VARCHAR(120) UNIQUE NOT NULL, "
                "label VARCHAR(200) NOT NULL, "
                "unit VARCHAR(40), "
                "description VARCHAR(500), "
                "source VARCHAR(20) NOT NULL DEFAULT 'master', "
                "dnp3_class VARCHAR(20) NOT NULL DEFAULT 'Class 1', "
                "data_type VARCHAR(20) NOT NULL DEFAULT 'analog', "
                "dnp3_object_group INTEGER NOT NULL DEFAULT 30, "
                "dnp3_index INTEGER NOT NULL DEFAULT 0, "
                "scale DOUBLE PRECISION NOT NULL DEFAULT 1.0, "
                "\"offset\" DOUBLE PRECISION NOT NULL DEFAULT 0.0, "
                "supports_alarm BOOLEAN NOT NULL DEFAULT FALSE, "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
                "display_order INTEGER NOT NULL DEFAULT 0)"
            )
        )
        # Horstmann SN2 sinyal setine gecis icin gerekli kolon/uzunluk guncellemeleri.
        connection.execute(text("ALTER TABLE signal_catalog ALTER COLUMN key TYPE VARCHAR(120)"))
        connection.execute(text("ALTER TABLE signal_catalog ALTER COLUMN label TYPE VARCHAR(200)"))
        connection.execute(
            text("ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'master'")
        )
        connection.execute(
            text("ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS dnp3_class VARCHAR(20) NOT NULL DEFAULT 'Class 1'")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS idx_signal_catalog_source ON signal_catalog (source)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS idx_signal_catalog_data_type ON signal_catalog (data_type)")
        )
        # IEC 60870-5-104 adresleme sutunlari (TypeID + IOA offset). NULL = tip
        # IEC 104 icin haritalanmaz (ornegin `string` sinyaller).
        connection.execute(
            text("ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS iec104_type_id INTEGER")
        )
        connection.execute(
            text("ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS iec104_ioa_offset INTEGER")
        )
        # Mutlak IOA modeli: cihaz bazli ayrim ASDU CA ile yapilir, IOA sinyale
        # ait sabit bir adres olur. Eski deploylar `iec104_ioa_offset` ile
        # birlikte yasar; yeni alan NULL ise kod offset'i fallback olarak okur.
        connection.execute(
            text("ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS iec104_ioa INTEGER")
        )
        # Cihaza ASDU Common Address atamasi. NULL kalirsa outbound target'in
        # default CA'si kullanilir (eski tek-CA davranisiyla uyumlu).
        connection.execute(
            text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS iec104_common_address INTEGER")
        )
        # Modbus + MQTT outbound template adresleme.
        connection.execute(
            text("ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS modbus_function INTEGER")
        )
        connection.execute(
            text("ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS modbus_address INTEGER")
        )
        connection.execute(
            text("ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS mqtt_topic VARCHAR(200)")
        )
        # Sinyal bazinda IEC 104 yayini gecici kapatma — default true (yayinla).
        connection.execute(
            text(
                "ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS iec104_enabled BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alarm_rules ("
                "id SERIAL PRIMARY KEY, "
                "signal_key VARCHAR(80) NOT NULL, "
                "name VARCHAR(160) NOT NULL, "
                "description VARCHAR(500), "
                "level VARCHAR(20) NOT NULL DEFAULT 'warning', "
                "comparator VARCHAR(20) NOT NULL DEFAULT 'gt', "
                "threshold DOUBLE PRECISION NOT NULL DEFAULT 0.0, "
                "threshold_high DOUBLE PRECISION, "
                "hysteresis DOUBLE PRECISION NOT NULL DEFAULT 0.0, "
                "debounce_sec INTEGER NOT NULL DEFAULT 0, "
                "device_code_filter VARCHAR(500), "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE)"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS idx_alarm_rules_signal_key ON alarm_rules (signal_key)")
        )
    db = SessionLocal()
    try:
        # strict=True: JSON listesi disindaki tum sinyalleri siler.
        # Bu sayede baslangicta sadece Horstmann SN2 fabrika katalogu kalir.
        result = seed_default_signals(db, strict=True)
        if not result.get("skipped"):
            import logging

            logging.getLogger(__name__).info(
                "signal_catalog seed sync -> inserted=%d updated=%d removed=%d",
                result.get("inserted", 0),
                result.get("updated", 0),
                result.get("removed", 0),
            )
        flush_outbox(db)
    finally:
        db.close()


@app.on_event("startup")
async def reapply_gateway_rabbitmq_permissions():
    """Onceden olusturulmus gateway'lerin RabbitMQ izinlerini yeniden uygular.

    Permission semasi degistirildiyse (orn. "configure" alani genisletildi)
    DB'de saklanan eski parolayi koruyarak izinleri guncelleriz. Yeni bir
    deploy/upgrade'de manuel rabbitmqctl cagrisi yapmaya gerek kalmaz.
    Best-effort: RabbitMQ Management API ulasilamiyorsa sadece warn loglar.
    """
    import logging

    from app.api.gateways import _rmq_admin
    from app.models.gateway import Gateway as _Gateway
    from app.services.rabbitmq_admin import RabbitMqAdminError

    log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        rows = list(db.scalars(_select(_Gateway)).all())
        if not rows:
            return
        client = _rmq_admin()
        if not client.ping():
            log.warning("rabbitmq_management_unreachable_at_startup gateways=%d", len(rows))
            return
        for gw in rows:
            if not gw.rabbitmq_username or not gw.rabbitmq_password:
                continue
            try:
                # Mevcut parolayi koru, sadece kullanici/permission'i tazele
                client.create_gateway_user(
                    gateway_code=gw.code,
                    existing_password=gw.rabbitmq_password,
                )
            except RabbitMqAdminError as exc:
                log.warning("rabbitmq_reapply_failed gateway=%s error=%s", gw.code, exc)
        log.info("rabbitmq_permissions_reapplied gateways=%d", len(rows))
    except Exception:  # noqa: BLE001
        log.exception("rabbitmq_reapply_startup_failed")
    finally:
        db.close()


@app.on_event("startup")
async def start_iec104_servers():
    """Aktif IEC 104 outbound target'lari icin TCP server'lari baslat.

    `create_tables` tamamlandiktan sonra calisir (FastAPI startup event'lari
    tanımlanma sirasina göre kosturulur). IEC 104 sunucularinin yaşam
    dongusu FastAPI loop icindedir; thread dongusundeki
    `outbound_dispatch_service._dispatch_iec104` `call_soon_threadsafe` ile
    degerleri guvenli iletir.
    """
    import logging

    loop = asyncio.get_running_loop()
    db = SessionLocal()
    try:
        deployed = await deploy_all_active_targets(db, loop=loop)
        if deployed:
            logging.getLogger(__name__).info("iec104_servers_deployed count=%d", deployed)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("iec104_startup_failed")
    finally:
        db.close()


@app.on_event("shutdown")
async def stop_iec104_servers():
    import logging

    try:
        await iec104_undeploy_all()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("iec104_shutdown_failed")


@app.on_event("startup")
def start_telemetry_consumer():
    telemetry_consumer.start()


@app.on_event("shutdown")
def stop_telemetry_consumer():
    telemetry_consumer.stop()


@app.on_event("startup")
def start_telemetry_retention():
    """Telemetri tablosu kayan pencere — eskileri otomatik temizle.

    Default: son 30 dakikalik kayitlar tutulur, her 5 dakikada bir DELETE.
    TELEMETRY_RETENTION_MINUTES / TELEMETRY_RETENTION_INTERVAL_SEC ile override.
    """
    telemetry_retention.start()


@app.on_event("shutdown")
def stop_telemetry_retention():
    telemetry_retention.stop()
