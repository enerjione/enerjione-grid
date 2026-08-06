import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import select as _select, text

from app.core.license_gate import LicenseGateMiddleware
from app.core.rate_limit import limiter

from app.api import alarm_rules, alarms, api_keys, auth, backups, bulk_notifications, device_configs, device_models, devices, events, faults, ftp_settings as ftp_settings_api, gateways, grid_topology, health, internal, licensing, map_tiles, network, notification_settings, notifications as notifications_api, outbound_targets, project_settings as project_settings_api, public, remote_access, responsibility_areas, sessions as sessions_api, signals, system_admin, system_status, telemetry, user_notification_preferences, users, ws_live
from app.core.config import settings
from app.core import service_role
from app.core.service_role import leader
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import alarm, alarm_rule, api_key as api_key_model, backup as backup_model, device, fault as fault_model, gateway, gateway_ingest_batch, notification as notification_model, notification_settings as notification_settings_model, outbound_target, outbox_event, processed_message, project_settings as project_settings_model, responsibility_area as responsibility_area_model, signal_catalog, system_event, telemetry as telemetry_model, user, user_notification_preference as user_notif_pref_model  # noqa: F401
from app.services.iec104.bootstrap import deploy_all_active_targets, undeploy_all as iec104_undeploy_all
from app.services.signal_catalog_seed import seed_default_signals
from app.services import alarm_reconciliation, backup_scheduler, gateway_staleness_watchdog, gateway_update_notifier, outbox_flush_worker, telemetry_consumer, telemetry_retention

app = FastAPI(
    title=settings.app_name,
    description=(
        "EnerjiOne Grid backend. Web/mobile için JWT, dış sistemler için "
        "**API Key (Personal Access Token)** desteği var.\n\n"
        "**Public API:** `/api/v1/public/*` altında, `Authorization: Bearer hsl_pat_<token>` "
        "ile çağrılır. Token yönetimi için `/api/v1/api-keys` endpoint'lerine bakın."
    ),
    openapi_tags=[
        {"name": "auth", "description": "Kullanıcı oturumu (JWT)."},
        {"name": "api-keys", "description": "Kullanıcının Personal Access Token (PAT) yönetimi."},
        {
            "name": "public-api",
            "description": (
                "Dış sistemler için read-only REST endpoint'leri. API Key ile korunur. "
                "Path: `/api/v1/public/*`."
            ),
        },
        {"name": "devices", "description": "Cihaz yönetimi (web UI)."},
        {"name": "signals", "description": "Sinyal kataloğu yönetimi (web UI)."},
        {"name": "alarms", "description": "Alarm event ve yorumlar."},
        {"name": "alarm-rules", "description": "Alarm kuralları."},
        {"name": "outbound-targets", "description": "Outbound hedef (REST/MQTT/IEC104)."},
        {"name": "gateways", "description": "Gateway yönetimi."},
        {"name": "internal", "description": "Servis-token korumalı internal endpoint'ler."},
        {
            "name": "remote-access",
            "description": (
                "Uzaktan bakim izni (sureli). Erisim VARSAYILAN KAPALI; izni "
                "yalnizca `engineer` rolu verir ve sure dolunca host ajani "
                "otomatik kapatir."
            ),
        },
    ],
)

# Rate limiter — login ve diger endpoint'lerde brute-force koruma.
# Decorator (`@limiter.limit("5/minute")`) ile endpoint-specific limit;
# default limit yok (sadece explicit isaretlenen route'lar kontrolde).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Lisans kilidi — lisanssiz kurulumda api_prefix altindaki her sey 403.
# CORS'tan ONCE eklenir ki CORS middleware'i DISTA kalsin: aksi halde 403
# yanitina CORS basliklari eklenmez ve tarayici hatayi okuyamaz (opak hata
# "Failed to fetch" olarak gorunur). Beyaz liste icin bkz. license_gate.py.
app.add_middleware(LicenseGateMiddleware)

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
app.include_router(device_configs.router, prefix=settings.api_prefix)
app.include_router(ftp_settings_api.router, prefix=settings.api_prefix)
app.include_router(licensing.router, prefix=settings.api_prefix)
app.include_router(responsibility_areas.router, prefix=settings.api_prefix)
app.include_router(gateways.router, prefix=settings.api_prefix)
app.include_router(telemetry.router, prefix=settings.api_prefix)
app.include_router(alarms.router, prefix=settings.api_prefix)
app.include_router(faults.router, prefix=settings.api_prefix)
app.include_router(user_notification_preferences.router, prefix=settings.api_prefix)
app.include_router(user_notification_preferences.admin_router, prefix=settings.api_prefix)
app.include_router(events.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(notification_settings.router, prefix=settings.api_prefix)
app.include_router(bulk_notifications.router, prefix=settings.api_prefix)
app.include_router(outbound_targets.router, prefix=settings.api_prefix)
app.include_router(signals.router, prefix=settings.api_prefix)
app.include_router(alarm_rules.router, prefix=settings.api_prefix)
app.include_router(internal.router, prefix=settings.api_prefix)
app.include_router(project_settings_api.router, prefix=settings.api_prefix)
app.include_router(grid_topology.router, prefix=settings.api_prefix)
app.include_router(system_status.router, prefix=settings.api_prefix)
app.include_router(network.router, prefix=settings.api_prefix)
app.include_router(remote_access.router, prefix=settings.api_prefix)
app.include_router(map_tiles.router, prefix=settings.api_prefix)
app.include_router(notifications_api.router, prefix=settings.api_prefix)
app.include_router(sessions_api.router, prefix=settings.api_prefix)
app.include_router(backups.router, prefix=settings.api_prefix)
app.include_router(system_admin.router, prefix=settings.api_prefix)
# API Key yonetimi (kullanici kendi PAT'larini olusturup revoke eder).
app.include_router(api_keys.router, prefix=settings.api_prefix)
# Public REST API (dis sistemlerin tukettigi, API Key korumali, read-only).
# Path: /api/v1/public/* — versiyonlama icin ileride /api/v2/public... acilabilir.
app.include_router(public.router, prefix=settings.api_prefix)
# WebSocket endpoint: api_prefix altinda /ws/live-values
app.include_router(ws_live.router, prefix=settings.api_prefix)


@app.on_event("startup")
def reconcile_remote_access_audit():
    """Backend kapaliyken kapanan uzaktan bakim oturumlarini denetime yaz.

    Sure dolunca kapatmayi host ajani (e1-rad) yapar; backend bunu ancak bir
    durum okumasinda ogrenir. Sayfa hic acilmasa bile guncelleme/restart
    sonrasi yakalansin diye burada bir kez daha uzlastiriyoruz.
    """
    try:
        from app.services import remote_access_service

        db = SessionLocal()
        try:
            remote_access_service.reconcile_audit(db)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        # Denetim uzlastirmasi backend'i ayaga kalkmaktan ALIKOYMAMALI.
        print(f"[startup] uzaktan bakim denetim uzlastirmasi atlandi: {exc}")


@app.on_event("startup")
def create_tables():
    """Legacy bootstrap DDL'ini EN-IYI-CABA olarak calistirir.

    NEDEN try/except: asagidaki `_legacy_bootstrap_ddl` ~160 DDL ifadesini
    TEK transaction'da kosturuyor ve startup event'i icinde cagriliyor.
    Buradan bir exception cikarsa uvicorn "Application startup failed."
    diyip process'i sonlandirir; Dockerfile CMD `migrate_db && exec uvicorn`
    zinciri oldugu icin container cikar ve `restart: unless-stopped` ile
    SONSUZ CRASH-LOOP'a girer. Yani tek bir kilit cakismasi veya beklenmedik
    bir satir, appliance'i tamamen KARARTIR — operator arayuze ulasip
    teshis bile yapamaz.

    Bu blok zaten LEGACY: gercek sema otoritesi Alembic (`scripts/migrate_db`
    uvicorn'dan ONCE kosuyor ve gercek bir sema sorununda zaten gurultulu
    sekilde patliyor). Buradaki ALTER'lar idempotent no-op'lardir. Dolayisiyla
    basarisizligini yutup acilmaya devam etmek, karanlik bir kutudan iyidir.

    Tum blok TEK transaction oldugu icin kismi uygulama riski YOKTUR:
    ya hepsi uygulanir ya hicbiri.
    """
    import logging as _logging

    try:
        _legacy_bootstrap_ddl()
    except Exception:  # noqa: BLE001
        _logging.getLogger(__name__).exception(
            "legacy_bootstrap_ddl_failed — backend YINE DE aciliyor. "
            "Sema otoritesi Alembic'tir; bu blok idempotent legacy ALTER'lardir. "
            "Kalici olarak tekrarliyorsa DB kilitlerini ve pg_stat_activity'yi "
            "inceleyin."
        )


def _legacy_bootstrap_ddl():
    """LEGACY BOOTSTRAP — production'da Alembic baseline tamamlandi (2026-05-12).
    Bu fonksiyon mevcut sahalardaki eski sema'lari Alembic'e kavusturmadan
    once eksik kolonlari ekleyen idempotent ALTER TABLE listesini calistirir.

    YENI SCHEMA DEGISIKLIKLERI ICIN BUNU KULLANMAYIN. Onun yerine:
        alembic revision -m "kolon_eklendi"
        # alembic_migrations/versions/ icinde upgrade()/downgrade() yazin
        # alembic upgrade head ile yeni satira gec

    Bu blok 2026-05-13 itibariyle FREEZED — yeni satir ekleme yapmayin.
    Burada listelenen ALTER'lar mevcut sahalarda calismaya devam etmesi
    icin kalir; backend her boot'ta bunlari calistirir (PostgreSQL
    `IF NOT EXISTS` ile no-op).
    """
    Base.metadata.create_all(bind=engine)
    # ALTER TYPE ADD VALUE transaction icinde calistirilamaz; autocommit kullan.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as ac_conn:
        # Kilit bekleme tavani — bkz. asagidaki engine.begin() blogundaki
        # ayrintili gerekce. Autocommit'te transaction yok, `SET LOCAL` islemez;
        # oturum bazli `SET` kullaniyoruz.
        ac_conn.execute(text("SET lock_timeout = '5s'"))
        ac_conn.execute(text("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'INSTALLER'"))
        # ops_manager rolu — alembic 0002'de de var, mevcut deploylarda da
        # idempotent calisabilsin diye burada da garantili eklenir.
        ac_conn.execute(text("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'OPS_MANAGER'"))
    with engine.begin() as connection:
        # KILIT BEKLEME TAVANI — tek satirlik ama en yuksek getirili koruma.
        #
        # Asagidaki ~100 `ALTER TABLE ... IF NOT EXISTS` ifadesi NO-OP olsa
        # bile ACCESS EXCLUSIVE kilidi alir ve bu kilit transaction sonuna
        # kadar tutulur (16 tablo, boot boyunca). Cakisan bir kilit varsa
        # — zamanlanmis bir pg_dump, restore, operator psql'i ya da onceki
        # container'dan kalmis idle-in-transaction bir baglanti — ALTER
        # varsayilan olarak SONSUZA KADAR bekler.
        #
        # Bu, restart ile COZULMEYEN bir kilitlenmedir: her yeniden deneme
        # ayni kilide girer. `SET LOCAL` transaction kapsamindadir, yani
        # buradaki tum DDL'i kapsar. Kilit alinamazsa ifade 5sn'de hata
        # verir, ustteki try/except yutar ve backend acilmaya devam eder —
        # sinirsiz bekleme yerine sinirli hasar.
        #
        # statement_timeout: tek bir ifadenin (orn. beklenmedik sekilde
        # buyumus bir tabloda tablo yeniden yazimi) boot'u kilitlemesini
        # onler.
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SET LOCAL statement_timeout = '60s'"))
        # FREEZED 2026-05-13. Yeni kolon: alembic_migrations/versions/'a yaz.
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number VARCHAR(32)"))
        # Brute-force koruma kolonlari (account lockout)
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0"))
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ"))
        # Sifre yonetimi: default-pwd zorla degistir + davet token akisi
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token_hash VARCHAR(128)"))
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token_expires_at TIMESTAMPTZ"))
        # Davet edilmis ama sifre belirlememis user'lar icin hashed_password
        # NULL olabilmeli (eski kayitlarda NOT NULL idi).
        connection.execute(text("ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(120)"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS acknowledged BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS reset BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS reset_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS signal_key VARCHAR(120)"))
        # produces_fault — "bu alarm gercek hat arizasi uretir mi?" bayragi.
        # FREEZE politikasina ragmen burada tutuluyor cunku mevcut sahalar
        # alembic_version'i zaten head'e stamp'lemis durumda; yeni alembic
        # revision (0003) bu deploylarda OTOMATIK kosmaz. Idempotent ALTER her
        # boot'ta no-op olarak calisip yeni kolonun varligini garanti eder.
        # Esdeger alembic revision 0003'te de tanimli (yeni kurulumlar icin).
        connection.execute(
            text("ALTER TABLE alarm_rules ADD COLUMN IF NOT EXISTS produces_fault BOOLEAN NOT NULL DEFAULT TRUE")
        )
        connection.execute(
            text("ALTER TABLE alarm_events ADD COLUMN IF NOT EXISTS produces_fault BOOLEAN NOT NULL DEFAULT TRUE")
        )
        # Alarm kurali bileşik (AND/OR) destegi — Faz 1.
        connection.execute(
            text("ALTER TABLE alarm_rules ADD COLUMN IF NOT EXISTS rule_kind VARCHAR(20) NOT NULL DEFAULT 'simple'")
        )
        connection.execute(
            text("ALTER TABLE alarm_rules ADD COLUMN IF NOT EXISTS expression_json TEXT")
        )
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
        # Cihaz komut (DNP3 CROB) proxy'si icin gateway'e ozel Bearer token.
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS command_token VARCHAR(255)")
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
        # Acil sema repair: eski startup `stamp head` nedeniyle 0008 gercekte
        # calismadan isaretlenmis olabilir. Gateway ORM her SELECT'te bu kolonu
        # bekledigi icin eksikligi `/gateways` endpoint'ini 500'e dusurur.
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS config_nonce INTEGER NOT NULL DEFAULT 0")
        )
        # Gateway token hash kolonu — yeni gateway create'lerinde SHA-256 hash
        # token'la birlikte yazilir. validate_gateway_token() once hash'a bakar,
        # yoksa eski plaintext yoluna duser (geriye uyumluluk).
        connection.execute(
            text("ALTER TABLE gateways ADD COLUMN IF NOT EXISTS token_hash VARCHAR(128)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS idx_gateways_token_hash ON gateways (token_hash)")
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
        # Twilio destegi — Account SID + From Number.
        connection.execute(
            text("ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS sms_account_sid VARCHAR(120) NOT NULL DEFAULT ''")
        )
        connection.execute(
            text("ALTER TABLE notification_settings ADD COLUMN IF NOT EXISTS sms_from_number VARCHAR(40) NOT NULL DEFAULT ''")
        )
        # Kullanici bildirim tercihlerine Telegram kanali — varsayilan FALSE
        # (opt-in: kullanici bot'a /start atmadan Telegram bildirimi
        # tetiklenmesin).
        connection.execute(
            text("ALTER TABLE user_notification_preferences ADD COLUMN IF NOT EXISTS telegram_enabled BOOLEAN NOT NULL DEFAULT FALSE")
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
        # MQTT auth/TLS/topic-template/periyot kolonlari (yeni)
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_port INTEGER"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_username VARCHAR(255)"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_password VARCHAR(500)"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_client_id VARCHAR(255)"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_tls_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_tls_insecure BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_tls_ca_path VARCHAR(500)"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_tls_cert_path VARCHAR(500)"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_tls_key_path VARCHAR(500)"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_keepalive_sec INTEGER NOT NULL DEFAULT 60"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_connect_timeout_sec INTEGER NOT NULL DEFAULT 10"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_publish_interval_sec INTEGER NOT NULL DEFAULT 10"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_topic_template VARCHAR(500)"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_topic_prefix VARCHAR(60) NOT NULL DEFAULT 'e1'"))
        connection.execute(text("ALTER TABLE outbound_targets ADD COLUMN IF NOT EXISTS mqtt_customer_id VARCHAR(120)"))
        # MQTT custom topic mapping tablosu (ORM Base.metadata.create_all
        # yapmis olabilir; idempotent CREATE IF NOT EXISTS yine de garanti).
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS outbound_topic_mappings ("
                "id SERIAL PRIMARY KEY, "
                "target_id INTEGER NOT NULL REFERENCES outbound_targets(id) ON DELETE CASCADE, "
                "topic VARCHAR(500) NOT NULL, "
                "device_codes TEXT NOT NULL DEFAULT '', "
                "signal_keys TEXT NOT NULL DEFAULT '', "
                "qos INTEGER, "
                "retain BOOLEAN, "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE"
                ")"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_outbound_topic_mappings_target_id ON outbound_topic_mappings(target_id)")
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
        # API Key (PAT) tablosu: kullanici-bazli, scope'lu, revoke edilebilir
        # token'lar. Token'in plain hali sadece olusturulurken gosterilir;
        # DB'de sha256 hash saklanir.
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS api_keys ("
                "id SERIAL PRIMARY KEY, "
                "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                "name VARCHAR(120) NOT NULL, "
                "token_hash VARCHAR(64) UNIQUE NOT NULL, "
                "token_prefix VARCHAR(20) NOT NULL, "
                "scopes VARCHAR(500) NOT NULL DEFAULT '', "
                "created_at TIMESTAMPTZ NOT NULL, "
                "expires_at TIMESTAMPTZ, "
                "last_used_at TIMESTAMPTZ, "
                "revoked_at TIMESTAMPTZ, "
                "allowed_ips VARCHAR(500), "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE)"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)")
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
        # Cihaz FCI yon oryantasyonu (green_forward / red_forward / NULL).
        # Kurulum verisi; fault yonu algoritmasi ileride buna baglanabilir.
        connection.execute(
            text("ALTER TABLE line_segments ADD COLUMN IF NOT EXISTS device_orientation VARCHAR(20)")
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

        # Hat (lines) silindiginde bagli tablolar CASCADE/SET NULL olmali. Eski
        # deployda FK'lar NO ACTION ile olusturulmus olabilir; modeldeki ondelete
        # parametresi yeni tablolara uygulanir, ama mevcut DB'de constraint
        # zaten varsa SQLAlchemy silip yeniden olusturmaz. Hat silme hatasinin
        # kok nedeni budur: poles / line_segments / responsibility_area_lines
        # / branched_from_pole_id constraint'lerini idempotent rebuild edelim.
        line_dep_fk_specs = [
            ("poles", "poles_line_id_fkey", "line_id", "lines", "CASCADE"),
            ("line_segments", "line_segments_line_id_fkey", "line_id", "lines", "CASCADE"),
            ("line_segments", "line_segments_from_pole_id_fkey", "from_pole_id", "poles", "CASCADE"),
            ("line_segments", "line_segments_to_pole_id_fkey", "to_pole_id", "poles", "CASCADE"),
            ("lines", "lines_region_id_fkey", "region_id", "regions", "CASCADE"),
            ("lines", "lines_branched_from_pole_id_fkey", "branched_from_pole_id", "poles", "SET NULL"),
            ("responsibility_area_lines", "responsibility_area_lines_line_id_fkey", "line_id", "lines", "CASCADE"),
        ]
        for tbl, fk_name, col, ref_table, action in line_dep_fk_specs:
            tbl_exists = connection.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name=:t"),
                {"t": tbl},
            ).first()
            if tbl_exists is None:
                continue
            connection.execute(
                text(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {fk_name}")
            )
            connection.execute(
                text(
                    f"ALTER TABLE {tbl} ADD CONSTRAINT {fk_name} "
                    f"FOREIGN KEY ({col}) REFERENCES {ref_table}(id) "
                    f"ON DELETE {action}"
                )
            )
    db = SessionLocal()
    try:
        # strict=False: ACILISTA SILME YOK.
        #
        # Eskiden `strict=True` idi ve JSON listesinde olmayan HER sinyali
        # siliyordu — ama `POST /signals` kurulumcuya sinyal YARATMA izni
        # veriyor. Yani operatorun ekledigi sinyaller ilk yeniden baslatmada
        # sessizce yok oluyordu. Fabrikaya donmek isteyen icin zaten ayri ve
        # BILINCLI bir uc var: `POST /signals/reset-to-defaults`.
        #
        # `respect_user_overrides` (varsayilan True) ise elle degistirilmis
        # ALANLARI korur: `_MUTABLE_FIELDS` tam da arayuzden duzenlenen
        # alanlari (label, scale, offset, iec104_ioa, dnp3_index...) icerdigi
        # icin her acilis, kaydedilmis ve denetim kaydi tutulmus degisiklikleri
        # sessizce geri aliyordu. Fabrika duzeltmeleri DOKUNULMAMIS alanlara
        # gelmeye devam eder.
        result = seed_default_signals(db, strict=False)
        if not result.get("skipped"):
            import logging

            logging.getLogger(__name__).info(
                "signal_catalog seed sync -> inserted=%d updated=%d removed=%d "
                "korunan_alan=%d",
                result.get("inserted", 0),
                result.get("updated", 0),
                result.get("removed", 0),
                result.get("kept", 0),
            )
        # NOT: Burada bir `flush_outbox(db)` cagrisi vardi ve KALDIRILDI
        # (2026-08-03). `leader`/`service_role` korumasi YOKTU: bu yol her
        # surecte kosuyor, yani `--workers N`e gecildiginde N surec acilista
        # ayni satirlari yayinlamaya kalkiyordu. Yayin artik TEK yerden,
        # `leader.register("outbox_flush", ...)` arkasindaki worker'dan yapilir
        # ve o en gec OUTBOX_FLUSH_INTERVAL_SEC (0.3 sn) icinde ayni isi yapar.

        # Fabrika config sablonu: SN2 icin HIC sablon yoksa depoyla gelen
        # dogrulanmis dosya varsayilan yapilir. Sablonsuz kurulumda "yeni
        # cihaza otomatik config" kancasi hic tetiklenemiyordu; bos durumdaki
        # "Sablondan olustur" dugmesi de buna dayanir. Coklu surec guvenli:
        # varlik kontrolu var, yaris halinde en kotu iki sablon olusur ve
        # tek varsayilan kurali zaten uygulanir.
        try:
            from app.services import device_config_service as _cfg_svc

            if _cfg_svc.seed_factory_template(db) is not None:
                db.commit()
                import logging

                logging.getLogger(__name__).info("fabrika config sablonu yuklendi")
        except Exception:  # noqa: BLE001 - seed eksikligi acilisi engellemez
            import logging

            logging.getLogger(__name__).warning(
                "fabrika config sablonu yuklenemedi", exc_info=True
            )
    finally:
        db.close()



@app.on_event("startup")
async def reapply_gateway_rabbitmq_permissions():
    """Eski sistem icin gateway RabbitMQ user permission yenileme.

    Gateway artik telemetriyi JetStream'e basiyor, RabbitMQ kullanmiyor;
    bu task'in islevi yok. Geriye uyumluluk icin no-op olarak birakildi
    (eski .env'lerde RABBITMQ_ADMIN_* set edilmis olsa bile baska bir
    sebebe sebep olmasin). Eski olusturulan RabbitMQ user'lar pasif kalir;
    operator istiyorsa rabbitmqctl ile elle silebilir.
    """
    pass


#: `start_iec104_servers`ta yakalanan FastAPI event loop'u. IEC 104
#: sunuculari bu loop uzerinde yasar; liderlik SONRADAN devralinirsa deploy
#: bu loop'a gonderilir (bkz. `_start_iec104_on_leadership`).
_iec104_loop: "asyncio.AbstractEventLoop | None" = None

#: Sunucular bu surecte acildi mi. Iki giris yolu var (acilis hizli yolu ve
#: liderligi devralma yolu); bayrak olmasa ikisi ust uste gelip
#: `manager.deploy` once undeploy yaptigi icin bagli SCADA oturumunu bosuna
#: koparirdi.
_iec104_deployed = False


async def _deploy_iec104_once() -> None:
    """Aktif IEC 104 hedeflerini bu surecte BIR KEZ ayaga kaldirir."""
    global _iec104_deployed
    import logging

    if _iec104_deployed:
        return
    _iec104_deployed = True
    db = SessionLocal()
    try:
        deployed = await deploy_all_active_targets(db, loop=asyncio.get_running_loop())
        if deployed:
            logging.getLogger(__name__).info("iec104_servers_deployed count=%d", deployed)
    except Exception:  # noqa: BLE001
        # Bayragi geri al: sonraki bir liderlik denemesi tekrar deneyebilsin.
        _iec104_deployed = False
        logging.getLogger(__name__).exception("iec104_startup_failed")
    finally:
        db.close()


def _start_iec104_on_leadership() -> None:
    """LIDERLIK ALINDIGINDA IEC 104 sunucularini ac — DEVRALMA yolu.

    NEDEN KAYITLI BIR IS
    --------------------
    `start_iec104_servers` kilidi acilista bir kez dener. Kilit o an baska bir
    oturumdaysa eskiden sessizce vazgeciliyordu ve BIR DAHA denenmiyordu —
    oysa `try_start` 15 sn'de bir yeniden dener ve bu surec pekala LIDER
    olabilir:

      * `update.sh` sonrasi eski container SIGKILL edilmisse Postgres onun
        advisory lock oturumunu TCP keepalive suresi boyunca (varsayilan
        saatler) acik tutar; yeni surec acilirken kilidi ALAMAZ, saniyeler
        sonra alir.
      * `--workers N` ile IEC 104'u acan worker olur ve baska bir worker
        liderligi devralir.

    Her iki durumda da surec LIDER olur, telemetri/outbox/retention calisir,
    `/health` "is_leader: true" der — ama 2404 HIC baglanmaz. SCADA'ya giden
    butun ariza-gecis ve kalite bildirimleri sessizce kaybolurdu.

    SONUC BEKLENMEZ: Starlette senkron startup handler'ini (bkz.
    `start_background_jobs`) dogrudan event loop THREAD'INDE kosturur;
    `future.result()` orada kilitlenirdi. Hata `_deploy_iec104_once` icinde
    zaten loglaniyor.
    """
    import logging

    loop = _iec104_loop
    if loop is None or _iec104_deployed:
        return
    try:
        asyncio.run_coroutine_threadsafe(_deploy_iec104_once(), loop)
    except RuntimeError:  # loop kapanmis
        logging.getLogger(__name__).exception("iec104_devralma_planlanamadi")


# Kayit SIRASI baslatma sirasidir; IEC 104 en basa gelsin ki telemetri
# tuketicisi deger basmaya basladiginda sunucular ayakta olsun. Durdurma
# `stop_iec104_servers` shutdown event'inde yapiliyor, burada no-op.
leader.register("iec104_servers", _start_iec104_on_leadership, lambda: None)


@app.on_event("startup")
async def start_iec104_servers():
    """Aktif IEC 104 outbound target'lari icin TCP server'lari baslat.

    `create_tables` tamamlandiktan sonra calisir (FastAPI startup event'lari
    tanımlanma sirasina göre kosturulur). IEC 104 sunucularinin yaşam
    dongusu FastAPI loop icindedir; thread dongusundeki
    `outbound_dispatch_service._dispatch_iec104` `call_soon_threadsafe` ile
    degerleri guvenli iletir.
    """
    global _iec104_loop

    # Loop LIDERLIK KONTROLUNDEN ONCE yakalanir: kilit su an baskasinda olsa
    # bile bu surec sonradan lider olabilir ve o zaman deploy'un gonderilecegi
    # bir loop gerekir. Yalnizca async startup event'inde elde edilebilir.
    _iec104_loop = asyncio.get_running_loop()

    # TEK SURECTE: IEC104 sunuculari TCP portu baglar.
    #
    # Burada eskiden `service_role.wants_background()` vardi ve YETMIYORDU:
    # o bayrak rol `all` iken HER uvicorn worker'inda True'dur. `--workers 4`
    # ile dort surec de `deploy_all_active_targets` cagirip ayni netns icinde
    # 2404'e bind denerdi; ucu EADDRINUSE alip sessizce (try/except) duserdi.
    # Kazanan surec rastgeledir ve o surec LIDER DEGILSE bagli SCADA istemcisi
    # acik ama SESSIZ bir sunucuya baglanirdi — tam bir sessiz ariza.
    #
    # `leader.claim()` advisory lock'u ALIR, yani kumede tek surecte True
    # doner. Isleri baslatmaz; onu asagidaki `start_background_jobs` yapar ve
    # ayni kilidi tekrar almaya calismaz.
    if not leader.claim():
        # Kilit su an BASKASINDA. Vazgecmek YETMEZ: liderlik 15 sn'de bir
        # yeniden denenir ve bu surec devralabilir. O yol `_start_jobs`ten
        # gecer; IEC 104 kayitli bir is oldugu icin orada acilir.
        return

    await _deploy_iec104_once()


@app.on_event("shutdown")
async def stop_iec104_servers():
    import logging

    try:
        await iec104_undeploy_all()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("iec104_shutdown_failed")


@app.on_event("startup")
def start_jetstream_bus():
    """NATS JetStream — dual-publish/dual-consume aktifse baslatir.

    NATS_DUAL_PUBLISH_ENABLED ve NATS_CONSUME_ENABLED ikisi de kapali ise
    bu fonksiyon hicbir sey yapmaz (log: skipped). nats-py paketi yoksa
    veya NATS server'a baglanilamiyorsa warning log + skip — backend
    calismaya devam eder, RabbitMQ akisi etkilenmez.

    Telemetry consumer'dan ONCE baslatilir ki consume tarafi acildiginda
    bus hazir olsun.
    """
    import logging

    try:
        from app.services.jetstream_bus import start_bus_if_enabled

        start_bus_if_enabled()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("jetstream_bus_startup_unexpected_error")

    # RabbitMQ vhost izolasyonu — production'da `/` reddedilir, `e1` (veya
    # operator'in tanimladigi) vhost dedicated. Backend startup'inda admin
    # API uzerinden vhost'u idempotent olarak ensure edip kendi
    # kullanicisina yetki verir. Yetersizse warning + devam (broker offline
    # senaryosunda backend hala healthcheck ile yukselebilsin); ileride
    # publish/consume cagrisi 403/404 atinca operator goruler.
    try:
        from app.core.config import settings as _s
        from app.services.rabbitmq_admin import RabbitMqAdminClient

        if _s.rabbitmq_vhost and _s.rabbitmq_vhost != "/":
            admin = RabbitMqAdminClient(
                management_url=_s.rabbitmq_management_url,
                admin_username=_s.rabbitmq_admin_username,
                admin_password=_s.rabbitmq_admin_password,
            )
            admin.ensure_vhost(_s.rabbitmq_vhost)
            try:
                admin.grant_admin_on_vhost(
                    vhost=_s.rabbitmq_vhost,
                    admin_username=_s.rabbitmq_admin_username,
                )
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).debug(
                    "rabbitmq_admin_grant_failed (idempotent ok if pre-existing)",
                    exc_info=True,
                )
            logging.getLogger(__name__).info(
                "rabbitmq_vhost_ensured vhost=%s", _s.rabbitmq_vhost
            )
    except Exception as exc:  # noqa: BLE001
        # Vhost ensure starting'i bloklamasin — broker olmasa bile backend
        # ayagi kalsin (health endpoint zaten broker durumunu raporlar).
        logging.getLogger(__name__).warning(
            "rabbitmq_vhost_ensure_failed error=%s "
            "(broker reachable degil olabilir; vhost manual yaratilirsa devam eder)",
            exc,
        )


@app.on_event("shutdown")
def stop_jetstream_bus():
    import logging

    try:
        from app.services.jetstream_bus import stop_bus

        stop_bus()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).debug("jetstream_bus_shutdown_error", exc_info=True)


@app.on_event("startup")
def start_ws_fanout_bridge():
    """Canli deger yayinini surecler arasi tasiyan NATS koprusu.

    TUKETICIDEN ONCE baslatilir: tuketici ilk mesaji yayinladiginda kopru
    hazir olsun, yoksa o mesajlar bellek-ici yola duser (zararsiz ama
    gereksiz).

    Kopru kurulamazsa (NATS kopuk / nats-py yok / ayar kapali) yayin
    bellek-ici calismaya devam eder — TEK surecte davranis aynidir. Coklu
    surece gecildiginde ise kopru ZORUNLUDUR: olmadan tuketici baska bir
    surecte oldugu icin WS istemcilerine hicbir sey ulasmaz ve bu ariza
    SESSIZ olur (soket bagli gorunur, deger akmaz). Bu yuzden
    `/health` ve ws_broadcaster.stats() icinde `bridge_ready` raporlanir.
    """
    from app.services.ws_broadcaster import bridge, broadcaster

    bridge.start(on_message=broadcaster._deliver_local)


@app.on_event("shutdown")
def stop_ws_fanout_bridge():
    from app.services.ws_broadcaster import bridge

    bridge.stop()


# --------------------------------------------------------------------------
# ARKA PLAN ISLERI — TEK SURECTE
#
# Asagidaki isler daha once her biri ayri bir startup event'i olarak
# aciliyordu. Tek uvicorn worker varken bu dogruydu; olcek buyudukce (600
# cihaz) API'yi coklu worker ile calistirmak ve arka plani ayri bir
# container'a almak gerekiyor. Ikisi de ayni tehlikeyi doguruyor: is birden
# fazla surecte TEKRAR calisir. Sonuclar sessiz ve pahali — yedek iki kez
# alinir, toplu bildirim iki kez gider, retention ayni satirlari iki kez
# silmeye calisir.
#
# Artik isler `leader`a kaydediliyor ve yalnizca Postgres advisory lock'u
# ALAN surecte aciliyor. Rol yapilandirmasi (all/api/worker) niyeti soyler,
# kilit gercekten tekil OLDUGUNU garanti eder. Bkz. core/service_role.py.
#
# Kayit SIRASI onemli: baslatma bu sirada, durdurma TERS sirada yapilir.
# --------------------------------------------------------------------------
# TELEMETRI TUKETICISI BILEREK LEADER'DA DEGIL — surec basina calisir.
#
# Leader kilidi "kumede TEK KOPYA calismali" isler icindir (retention,
# zamanlanmis isler...). Tuketici tam tersi: JetStream durable'lari paralel
# uyeler arasinda mesaj PAYLASIR ve persist kodu paralel tuketiciye
# dayaniklidir (dedup IN on-kontrolu, ON CONFLICT, source_timestamp
# korumasi, savepoint karantinasi). Kilit altindayken UVICORN_WORKERS=4
# acmak 3 sureci bosta biraktiriyordu; 400 cihazda tek surec ~2.4k msj/sn
# tavanindaydi. Artik arka plan rolu tasiyan HER surec tuketir —
# UVICORN_WORKERS dogrudan persist kapasitesini carpar.
# Baslatma/durdurma startup/shutdown kancalarinda (bkz. asagida
# _telemetri_tuketici_baslat/_durdur).

# Outbox flush — telemetri yayinini ingest request yolundan ayirir. Ingest
# sadece DB'ye yazar; bu worker arka planda RabbitMQ'ya yayinlar. 200 cihaz
# yukunde gateway 'Read timed out' onlenir.
leader.register("outbox_flush", outbox_flush_worker.start, outbox_flush_worker.stop)


def _start_outbound_batcher() -> None:
    """REST webhook batch dispatcher — 5sn penceresinde biriktirip tek POST."""
    from app.services import outbound_telemetry_batcher

    outbound_telemetry_batcher.start()


def _stop_outbound_batcher() -> None:
    from app.services import outbound_telemetry_batcher

    outbound_telemetry_batcher.stop()


leader.register("outbound_telemetry_batcher", _start_outbound_batcher, _stop_outbound_batcher)


def _start_mqtt() -> None:
    """Her aktif MQTT target icin kalici client + periyodik flush."""
    from app.services import mqtt_publisher_service

    mqtt_publisher_service.start()


def _stop_mqtt() -> None:
    from app.services import mqtt_publisher_service

    mqtt_publisher_service.stop()


leader.register("mqtt_publisher", _start_mqtt, _stop_mqtt)


def _start_bulk_notifications() -> None:
    """Zamanlanmis toplu bildirim — her 30sn'de bir due job'lari isler."""
    from app.services import bulk_notification_scheduler

    bulk_notification_scheduler.start()


def _stop_bulk_notifications() -> None:
    from app.services import bulk_notification_scheduler

    bulk_notification_scheduler.stop()


leader.register("bulk_notification_scheduler", _start_bulk_notifications, _stop_bulk_notifications)

# Telemetri kayan penceresi — eskileri otomatik temizler (varsayilan: son 30
# dakika, 5 dakikada bir DELETE).
leader.register("telemetry_retention", telemetry_retention.start, telemetry_retention.stop)

# Acik alarmlarin periyodik mutabakati — kosul artik karsilanmiyorsa onaylanmis
# ise siler, onaylanmamis ise reset=True yapar. alarm-service'in bellek-ici
# durumundan bagimsiz self-healing saglar.
leader.register("alarm_reconciliation", alarm_reconciliation.start, alarm_reconciliation.stop)

# Gateway susarsa cihazlari yesil birakma. `communication_status` yalnizca
# telemetri okumasi geldiginde guncelleniyor; gateway tamamen sustugunda hic
# okuma gelmez ve tum cihazlar son durumlarinda (genellikle ONLINE) donar.
# Bkz. gateway_staleness_watchdog — CIHAZ bazli veri-yasi kontrolu BILEREK
# yapilmiyor, o yanlis alarm uretiyordu.
leader.register(
    "gateway_staleness",
    gateway_staleness_watchdog.start,
    gateway_staleness_watchdog.stop,
)

# Gateway icin yeni imaj yayinlandiginda TUM kullanicilara bir kez bildirim.
# "Bir kez" onemli: kontrol periyodik, her turda bildirim gondermek bildirim
# merkezini kullanilamaz hale getirirdi (bkz. gateway_update_notifier).
leader.register(
    "gateway_update_notifier",
    gateway_update_notifier.start,
    gateway_update_notifier.stop,
)

# Periyodik yedekleme. IKI KEZ CALISMASI ozellikle pahali: her tetikte pg_dump
# kosar, diski ve CPU'yu iki katina cikarir ve retention sayimini bozar.
leader.register("backup_scheduler", backup_scheduler.start, backup_scheduler.stop)

# Harici FTP yoklayicisi — FTP modu "harici" iken musterinin sunucusundaki
# `<seri>_Configuration.csv` dosyalarini tarayip surume cevirir. Gomulu modda
# hicbir sey yapmaz (orada olaylar ftp-server callback'lerinden aninda gelir).
# Tek surecte kosmali: iki yoklayici ayni dosyayi iki kez indirir ve ayni
# anda iki surum yaratmaya kalkabilirdi (unique kisitta patlar).
from app.services import ftp_poll_worker  # noqa: E402

leader.register("ftp_config_poll", ftp_poll_worker.start, ftp_poll_worker.stop)


@app.on_event("startup")
def start_background_jobs():
    """Kilidi almayi dener; alirsa kayitli tum arka plan islerini baslatir.

    Kilit baska bir surecte ise burada HICBIR SEY acilmaz ve periyodik olarak
    yeniden denenir — worker container'i yeniden baslatildiginda devralmayi
    saglayan sey budur.
    """
    leader.try_start()


@app.on_event("startup")
def _telemetri_tuketici_baslat():
    """Telemetri tuketicisi LEADER'SIZ, surec basina calisir.

    JetStream durable'i mesajlari paralel uyeler arasinda paylasir; persist
    kodu paralel tuketiciye dayanikli (dedup, ON CONFLICT, damga korumasi).
    UVICORN_WORKERS=N ile worker container'i N tuketici surec calistirir —
    persist kapasitesi dogrudan carpilir. API rolunde ACILMAZ.
    """
    if service_role.wants_background():
        telemetry_consumer.start()


@app.on_event("shutdown")
def stop_background_jobs():
    if service_role.wants_background():
        telemetry_consumer.stop()
    leader.stop()
