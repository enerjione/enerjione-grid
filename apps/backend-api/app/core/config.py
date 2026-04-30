from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Horstman Smart Logger API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    # Uzun operatör oturumları (fabrika) için; .env: ACCESS_TOKEN_MINUTES=...
    access_token_minutes: int = 720
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/horstman"
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
    rabbitmq_exchange: str = "hsl.events"
    rabbitmq_prefetch_count: int = 20
    rabbitmq_dlx_exchange: str = "hsl.events.dlx"
    rabbitmq_queue_tag: str = "hsl.tag.telemetry.raw"
    rabbitmq_queue_alarm: str = "hsl.alarm.telemetry.received"
    rabbitmq_queue_outbound_alarm: str = "hsl.outbound.alarm.created"
    rabbitmq_queue_outbound_telemetry: str = "hsl.outbound.telemetry.received"
    internal_service_token: str = "change-me-internal-token"
    smtp_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@horstman.local"
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
