import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select as _select, text

from app.api import alarm_rules, alarms, auth, backups, device_models, devices, events, faults, gateways, grid_topology, health, internal, notification_settings, notifications as notifications_api, outbound_targets, project_settings as project_settings_api, responsibility_areas, signals, system_status, telemetry, user_notification_preferences, users, ws_live
from app.core.config import settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import alarm, alarm_rule, backup as backup_model, device, fault as fault_model, gateway, gateway_ingest_batch, notification as notification_model, notification_settings as notification_settings_model, outbound_target, outbox_event, processed_message, project_settings as project_settings_model, responsibility_area as responsibility_area_model, signal_catalog, system_event, telemetry as telemetry_model, user, user_notification_preference as user_notif_pref_model  # noqa: F401
from app.services.iec104.bootstrap import deploy_all_active_targets, undeploy_all as iec104_undeploy_all
from app.services.outbox_service import flush_outbox
from app.services.signal_catalog_seed import seed_default_signals
from app.services import alarm_reconciliation, backup_scheduler, telemetry_consumer, telemetry_retention

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
app.include_router(faults.router, prefix=settings.api_prefix)
app.include_router(user_notification_preferences.router, prefix=settings.api_prefix)
app.include_router(events.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(notification_settings.router, prefix=settings.api_prefix)
app.include_router(outbound_targets.router, prefix=settings.api_prefix)
app.include_router(signals.router, prefix=settings.api_prefix)
app.include_router(alarm_rules.router, prefix=settings.api_prefix)
app.include_router(internal.router, prefix=settings.api_prefix)
app.include_router(project_settings_api.router, prefix=settings.api_prefix)
app.include_router(grid_topology.router, prefix=settings.api_prefix)
app.include_router(system_status.router, prefix=settings.api_prefix)
app.include_router(notifications_api.router, prefix=settings.api_prefix)
app.include_router(backups.router, prefix=settings.api_prefix)
# WebSocket endpoint: api_prefix altinda /ws/live-values
app.include_router(ws_live.router, prefix=settings.api_prefix)


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
        # Initiating mode TCP server port araligi: gateway basina ayri 1000'lik
        # blok. Default 20100 (geriye uyumluluk; tek gateway senaryolari icin).
        # Frontend yeni gateway eklerken otomatik benzersiz blok atar.
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS initiating_port_base INTEGER NOT NULL DEFAULT 20100")
        )
        # Bu gateway icin acilacak initiating port sayisi (max initiating cihaz).
        # Default 0: sadece listening cihazlar; gateway cihazlara outbound TCP
        # client olarak baglanir, port acmaz. Initiating cihaz eklenecekse
        # kullanici frontend formundan deger artirir.
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS initiating_port_count INTEGER NOT NULL DEFAULT 0")
        )
        # Operator "tum cihazlara sorgu at" sayaci. Gateway config refresh
        # akisinda kendi en son gordugu degerle kiyaslayip integrity poll
        # tetikler.
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS refresh_nonce INTEGER NOT NULL DEFAULT 0")
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
        # Telemetry retention DELETE'i (telemetry_retention.py) source_timestamp
        # uzerinde range query yapar; index olmadan tablo buyudukce full table
        # scan + lock contention. 600 cihaz olceginde tablo dakikada milyon
        # satir buyur — bu index zorunlu.
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS idx_telemetry_source_timestamp ON telemetry(source_timestamp)")
        )
        # Frontend "son N dakikalik degerler" + alarm engine "device+signal'in
        # son durumu" sorgulari icin composite index. device_id + signal_key
        # ayri ayri zaten index'li ama PostgreSQL bu kombinasyonda multi-index
        # bitmap scan yaparak suboptimal. Composite + DESC timestamp ile latest-
        # value sorgusunda dogrudan index seek olur.
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_device_signal_ts "
                "ON telemetry(device_id, signal_key, source_timestamp DESC)"
            )
        )
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
        # Telegram bot ayarlari (mevcut deploylar icin migration).
        connection.execute(
            text("ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS telegram_enabled BOOLEAN NOT NULL DEFAULT FALSE")
        )
        connection.execute(
            text("ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS telegram_bot_token VARCHAR(255) NOT NULL DEFAULT ''")
        )
        connection.execute(
            text("ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS telegram_chat_ids VARCHAR(2000) NOT NULL DEFAULT ''")
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
        # Tarayici sekme basligi + favicon + login dekoratif gorseli (data URL)
        connection.execute(
            text("ALTER TABLE project_settings ADD COLUMN IF NOT EXISTS site_title VARCHAR(200)")
        )
        connection.execute(
            text("ALTER TABLE project_settings ADD COLUMN IF NOT EXISTS favicon TEXT")
        )
        connection.execute(
            text("ALTER TABLE project_settings ADD COLUMN IF NOT EXISTS login_image TEXT")
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
        # IEC 104 zaman etiketi (CP56Time2a). Default false; mevcut binary
        # (data_type='binary') sinyalleri tek seferlik backfill ile true yap
        # (kullanici cogu zaman dijital event'lere zaman bekler). Analog/sayac
        # default false kalir.
        connection.execute(
            text(
                "ALTER TABLE signal_catalog ADD COLUMN IF NOT EXISTS iec104_with_timestamp BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        # Tek seferlik backfill: binary sinyalleri true yap. UPDATE her startup'ta
        # idempotent calisir (zaten true olanlari yeniden true yapmak no-op);
        # kullanici sonradan elle false yaparsa yine true'ya cevirmeyiz cunku
        # sadece NULL'dan baslayanlari guncelliyoruz... ama default NOT NULL
        # FALSE oldugu icin bu kosul islemez. Bunun yerine: sadece DEFAULT
        # uygulanmis FALSE'lari binary'lerde true yap, ama bunu tespit etmek
        # icin ek bir 'iec104_with_timestamp_user_set' flag gerek. SCADA pratigi
        # standart: ilk migration anindaki backfill yeterli — sonradan kullanici
        # tercihini ezmesin diye sadece tek seferlik calistir.
        # Cozum: bir migration_marker tablosu kullan.
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS migration_markers ("
                "key VARCHAR(120) PRIMARY KEY, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
        )
        marker_row = connection.execute(
            text("SELECT 1 FROM migration_markers WHERE key='iec104_with_timestamp_binary_default'")
        ).first()
        if marker_row is None:
            connection.execute(
                text(
                    "UPDATE signal_catalog SET iec104_with_timestamp = TRUE "
                    "WHERE data_type = 'binary' AND iec104_type_id IS NOT NULL"
                )
            )
            connection.execute(
                text("INSERT INTO migration_markers(key) VALUES ('iec104_with_timestamp_binary_default')")
            )
        # Direk tipi: normal direk, trafo, vb. Hat baslangic/bitis goruntusu kullanici secimine
        # gore degistirilebilir. Default 'pole'.
        connection.execute(
            text(
                "ALTER TABLE poles ADD COLUMN IF NOT EXISTS pole_type VARCHAR(20) NOT NULL DEFAULT 'pole'"
            )
        )
        # Iki direk arasinda birden fazla cihaz olabilmesi icin (from, to) UNIQUE
        # kisitini dusur. Cihaz UNIQUE constraint'i (uq) ayrica kalir; ayni cihaz
        # iki yerde olamaz.
        connection.execute(
            text("ALTER TABLE line_segments DROP CONSTRAINT IF EXISTS uq_segment_endpoints")
        )
        # Cihazin slot icindeki fiziksel konumu (0..1 arasinda t parametresi).
        # NULL = auto orta-nokta dagilimi.
        connection.execute(
            text("ALTER TABLE line_segments ADD COLUMN IF NOT EXISTS device_position_t DOUBLE PRECISION")
        )
        # Bransman: hattin baska bir hattin diregine bagli oldugunu isaretler.
        # NULL = bagimsiz hat (default). Set edilirse hattin baslangic noktasi
        # bu pole'dan kabul edilir; ariza algoritmasi ana hat -> dal akisini
        # birlikte kontrol eder.
        connection.execute(
            text("ALTER TABLE lines ADD COLUMN IF NOT EXISTS branched_from_pole_id INTEGER REFERENCES poles(id) ON DELETE SET NULL")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS idx_lines_branched_from_pole_id ON lines (branched_from_pole_id)")
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
        # Kanal-bazli bildirim flag'leri (kural duzeyinde acilip kapatilir).
        # Default false: kullanici kasitla acmadigi surece email/sms/telegram
        # gitmez (spam koruma).
        connection.execute(
            text("ALTER TABLE alarm_rules ADD COLUMN IF NOT EXISTS notify_email BOOLEAN NOT NULL DEFAULT FALSE")
        )
        connection.execute(
            text("ALTER TABLE alarm_rules ADD COLUMN IF NOT EXISTS notify_sms BOOLEAN NOT NULL DEFAULT FALSE")
        )
        connection.execute(
            text("ALTER TABLE alarm_rules ADD COLUMN IF NOT EXISTS notify_telegram BOOLEAN NOT NULL DEFAULT FALSE")
        )

        # i18n: kullanici basina arayuz dili. NULL = sistem default (tr).
        connection.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS language VARCHAR(8)")
        )

        # FaultEvent FK kaskat migration: hat/region/direk/cihaz silinince
        # bu hat/region/direkle iliskili ariza kaydi otomatik temizlenmeli.
        # Eski deploylar `ON DELETE NO ACTION` ile birakilmisti — `delete_line`
        # bu yuzden FK constraint hatasi veriyordu. Constraint'leri DROP edip
        # CASCADE ile yeniden olusturuyoruz. fault_events tablosu yoksa atla.
        existing_fault_table = connection.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name='fault_events'")
        ).first()
        if existing_fault_table is not None:
            fault_fk_specs = [
                ("fault_events_line_id_fkey", "line_id", "lines", "CASCADE"),
                ("fault_events_region_id_fkey", "region_id", "regions", "CASCADE"),
                ("fault_events_last_red_device_id_fkey", "last_red_device_id", "devices", "CASCADE"),
                ("fault_events_first_green_device_id_fkey", "first_green_device_id", "devices", "SET NULL"),
                ("fault_events_from_pole_id_fkey", "from_pole_id", "poles", "CASCADE"),
                ("fault_events_to_pole_id_fkey", "to_pole_id", "poles", "CASCADE"),
            ]
            for fk_name, col, ref_table, action in fault_fk_specs:
                connection.execute(
                    text(f"ALTER TABLE fault_events DROP CONSTRAINT IF EXISTS {fk_name}")
                )
                connection.execute(
                    text(
                        f"ALTER TABLE fault_events ADD CONSTRAINT {fk_name} "
                        f"FOREIGN KEY ({col}) REFERENCES {ref_table}(id) "
                        f"ON DELETE {action}"
                    )
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


@app.on_event("startup")
def start_alarm_reconciliation():
    """Acik alarmlari periyodik olarak kontrol et — kosul artik karsilanmiyorsa
    onaylanmis ise sil, onaylanmamis ise reset=True yap. alarm-service in-memory
    state drift'inden bagimsiz self-healing saglar."""
    alarm_reconciliation.start()


@app.on_event("shutdown")
def stop_alarm_reconciliation():
    alarm_reconciliation.stop()


@app.on_event("startup")
def start_backup_scheduler():
    """Periyodik DB yedek alma worker'i. BackupSchedule tablosuna bakar;
    enabled=True ise interval_hours kadar surede bir pg_dump yapar ve
    retention_count'a gore eski yedekleri siler."""
    backup_scheduler.start()


@app.on_event("shutdown")
def stop_backup_scheduler():
    backup_scheduler.stop()
