from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "EnerjiOne Grid Dashboard API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    # Uzun operatör oturumları (fabrika) için; .env: ACCESS_TOKEN_MINUTES=...
    # Varsayılan 30 gün = 43200 dk. "Beni hatırla" tıklayan kullanıcı saha
    # ortamında hafta sonları boyunca tekrar giriş yapmak zorunda kalmasın.
    access_token_minutes: int = 43_200
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/enerjione"
    # SQLAlchemy connection pool ayarlari (600 cihaz / 10K msg/sn olcekleri icin
    # default pool_size=5, max_overflow=10 yetersiz kalir; concurrent telemetry
    # consumer + alarm-service + tag-engine + frontend istekleri = 50-80 paralel
    # query ihtimali var). pool_recycle PostgreSQL idle disconnect (default 8h)
    # oncesinde stale connection'i atip yenisini acar.
    db_pool_size: int = 30
    db_max_overflow: int = 20
    db_pool_recycle_sec: int = 3600
    db_pool_timeout_sec: int = 30
    cors_origins: str = "*"
    event_bus_backend: str = "inprocess"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    # Management API: gateway eklendiginde otomatik dedicated user yaratmak
    # icin kullanilir (manuel rabbitmqctl gerektirmez). Default Windows
    # installer'inda 15672'de aciktir. Production'da ozel admin kullanicisi
    # ile override edilebilir.
    rabbitmq_management_url: str = "http://localhost:15672"
    rabbitmq_admin_username: str = "guest"
    rabbitmq_admin_password: str = "guest"
    rabbitmq_exchange: str = "e1.events"
    rabbitmq_prefetch_count: int = 20
    rabbitmq_dlx_exchange: str = "e1.events.dlx"
    rabbitmq_queue_tag: str = "e1.tag.telemetry.raw"
    rabbitmq_queue_alarm: str = "e1.alarm.telemetry.received"
    rabbitmq_queue_outbound_alarm: str = "e1.outbound.alarm.created"
    rabbitmq_queue_outbound_telemetry: str = "e1.outbound.telemetry.received"

    # ----- NATS JetStream (telemetri akisinin primary rotasi) ----------------
    # Telemetri akisi (gateway -> tag-engine -> persister/iec104/alarm-service)
    # tamamen JetStream uzerinden gider. RabbitMQ sadece alarm.created icin
    # kullanilir. Stream'ler backend startup'inda OTOMATIK ensure edilir
    # (idempotent: varsa dokunulmaz, yoksa olusturulur).
    nats_url: str = "nats://localhost:4222"
    # Bu iki flag DEPRECATED: NATS her zaman aktif. Eski .env'lerin kirilmamasi
    # icin field'lar tutulur ama runtime davranisi etkilemezler.
    nats_dual_publish_enabled: bool = True
    nats_consume_enabled: bool = True
    # Stream isimleri: TELEMETRY_RAW ham cihaz okumalari (gateway -> stream),
    # TELEMETRY_NORMALIZED tag-engine cikisi (tag-engine -> stream).
    nats_stream_telemetry_raw: str = "TELEMETRY_RAW"
    nats_stream_telemetry_normalized: str = "TELEMETRY_NORMALIZED"
    # Subject pattern'leri — wildcard ile gateway bazinda filtreleme.
    # Konkre: e1.telemetry.raw.GW-001, e1.telemetry.normalized.GW-001
    nats_subject_telemetry_raw: str = "e1.telemetry.raw.>"
    nats_subject_telemetry_normalized: str = "e1.telemetry.normalized.>"
    # Stream retention (gun) — JetStream WAL'in disk'te kalma suresi. 7 gun raw,
    # 30 gun normalized: backfill/replay icin yeterli, disk dolusunu sinirlar.
    nats_stream_raw_max_age_days: int = 7
    nats_stream_normalized_max_age_days: int = 30
    # Connect timeout — kisa tutulur; backend startup'i NATS yokken bloklanmasin.
    # NATS gelene kadar consumer hatasi atar ama backend ayagi kalir; NATS gelince
    # consumer kendi reconnect dongusunde devam eder.
    nats_connect_timeout_sec: int = 5
    # Durable consumer adi (backend-api telemetry persister icin).
    nats_consumer_telemetry_persist: str = "backend-api-telemetry-persist"

    internal_service_token: str = "change-me-internal-token"
    smtp_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@enerjione.local"
    sms_enabled: bool = False
    sms_provider: str = "mock"
    sms_api_url: str = ""
    sms_api_key: str = ""
    service_role: str = "api"
    service_name: str = "backend-api"
    worker_health_host: str = "127.0.0.1"
    worker_health_port: int = 0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
