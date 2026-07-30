import re

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Imajla birlikte paketlenen surum. Kok dizindeki VERSION dosyasi ve
# apps/frontend-web/package.json ile AYNI olmali; release CI ucunu de
# birbirine karsi dogrular.
_FALLBACK_APP_VERSION = "2.24.6"


# Production'da reddedilen placeholder secret prefix'leri. Settings constructor
# `app_env in ("production","prod")` durumunda bu prefix'lerden biriyle baslayan
# bir secret tespit ederse RuntimeError firlatir — boylece operator yanlislikla
# default secret'larla prod'a deploy edemez.
#
# Pattern-based: `.env.example` icindeki tum placeholder bicimleri kapsanir:
#   - "change-me-in-production"
#   - "change-me-internal-token"
#   - "change-me-strong-password"
#   - "please-change-me-32-bytes-hex"
#   - "please-change-me-internal-32-bytes-hex"
#   - "change-this-secret"
#   - "your-secret-here"
# Yeni placeholder pattern eklerken bu listeye prefix ekleyin.
_PLACEHOLDER_PREFIXES: tuple[str, ...] = (
    "change-me",
    "please-change-me",
    "change-this",
    "your-secret",
)


def _is_placeholder_secret(value: str) -> bool:
    """Bos veya bilinen placeholder prefix'lerden biriyle basliyorsa True."""
    v = (value or "").strip().lower()
    if not v:
        return True
    return v.startswith(_PLACEHOLDER_PREFIXES)


class Settings(BaseSettings):
    app_name: str = "EnerjiOne Grid Dashboard API"
    # Calisan surum. Deploy'da `E1_VERSION` ile gelir (docker-compose ayni
    # degiskeni image tag'i icin de kullaniyor); yerel dev'de asagidaki
    # varsayilan gecerli. Frontend bunu Lisans ve Sistem Durumu
    # sayfalarinda gosterir.
    # NOT: `apps/frontend-web/package.json` icindeki "version" ile ayni
    # tutulmali — surum yukseltirken ikisi birlikte guncellenir.
    app_version: str = Field(
        default=_FALLBACK_APP_VERSION,
        validation_alias=AliasChoices("E1_VERSION", "APP_VERSION"),
    )
    # Guncelleme kontrolu (SADECE BILGI AMACLI — panelden guncelleme YAPILMAZ).
    # Bos ise kontrol tamamen kapalidir ve arayuzde "kontrol kapali" gorunur.
    # Beklenen icerik: JSON. Su alanlardan ilk bulunan surum kabul edilir:
    #   "version" | "latest" | "tag_name"  (GitHub Releases API de calisir)
    update_check_url: str = ""
    # Kontrol periyodu (saniye). Backend sonucu bu sure boyunca cache'ler;
    # Sistem Durumu sayfasi 10sn'de bir sorguladigi icin cache sart.
    update_check_interval_sec: int = 21_600  # 6 saat
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    # Token omru — kullanici sahada uzun saatler calistigi icin 24 saat default
    # (eskiden 8 saat: vardiya bitiminde otomatik logout olusturuyordu, kullanici
    # tekrar tekrar login olmak zorundaydi). 24 saatten uzun cikartmak gunluk
    # rotasyonu zayiflatir, dikkatli kullanin. .env'den ACCESS_TOKEN_MINUTES
    # override edilebilir.
    #
    # "Beni hatirla" akisi icin ayri remember_me_token_minutes (default 30 gun).
    # Login response'unda remember_me=true ise uzun TTL'li token verilir.
    # Local-LAN deploy icin kabul edilebilir; internet expose'da kisa TTL +
    # refresh-token zorunlu.
    access_token_minutes: int = 1440  # 24 saat
    remember_me_token_minutes: int = 43_200  # 30 gun
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/enerjione_grid"
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
    # Event bus backend secimi:
    #   "rabbitmq" (default, production): outbox -> event_bus.publish_event ->
    #     RabbitMqEventBus -> hsl.events exchange. notification-worker,
    #     iec104-outbound vb. consumer'lar mesaji alir.
    #   "inprocess": sadece dev/test; mesajlar in-process subscriber'a gider,
    #     baska bir servis HIC alamaz. Production'da kullanilmamali — boot'ta
    #     validator uyari atar.
    event_bus_backend: str = "rabbitmq"
    # NOT: production'da `amqp://...:5672/` (kok `/` vhost) reddedilir;
    # default vhost paylasimli/eski olabilir ve baska bir tenant'a sizinti
    # riski olur. Dedicated `e1` vhost (veya operator tarafindan secilen
    # baska bir izole vhost) zorunlu — bkz. `_validate_production_safeguards`.
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/e1"
    # Backend startup'inda admin user ile bu vhost olusturulur (idempotent);
    # ayrica gateway/backend kullanicilari bu vhost altinda permission alir.
    rabbitmq_vhost: str = "e1"
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
    # DLQ (dead-letter queue): worker max_deliver'a takilan "poison" mesajlari
    # buraya tasinir. Sessizce kaybolmaz; operator JetStream UI'dan veya `nats
    # stream view TELEMETRY_DLQ` ile inceler, root-cause sonra replay eder.
    # Subject: `e1.dlq.<service>.<original-subject>` — service adi DLQ'ya
    # publish eden worker'in hangisi oldugunu gosterir.
    nats_stream_telemetry_dlq: str = "TELEMETRY_DLQ"
    nats_subject_telemetry_dlq: str = "e1.dlq.>"
    nats_stream_dlq_max_age_days: int = 30
    # Worker max_deliver — bir mesaj kac kez nack'lendikten sonra DLQ'ya
    # tasinir. 10 makul: gecici DB hatasi/lock contention 10 retry'da gecer;
    # poison payload (parse error vb.) hep nack'leyecektir, 10. nack'te DLQ.
    nats_worker_max_deliver: int = 10
    # Pull consumer fetch batch boyutu. Backend kendi hizinda batch ceker
    # (push degil) -> NATS slow-consumer disconnect'i onlenir. Batch-commit
    # ile 500 mesaj TEK transaction'da yazilir -> DB round-trip ~batch kadar
    # azalir, throughput gelis hizini gecer, backlog erir. Cok buyuk ->
    # ack_wait (60s) icinde tek commit'te islenemez riski.
    nats_pull_batch_size: int = 500
    # Pull consumer max_ack_pending — fetch inflight tavani. batch_size'in
    # kati olmali (2x guvenli marj). Backlog'u TEK BASINA cozmez; asil cozum
    # batch-commit throughput'u.
    nats_pull_max_ack_pending: int = 10000
    # Connect timeout — kisa tutulur; backend startup'i NATS yokken bloklanmasin.
    # NATS gelene kadar consumer hatasi atar ama backend ayagi kalir; NATS gelince
    # consumer kendi reconnect dongusunde devam eder.
    nats_connect_timeout_sec: int = 5
    # Durable consumer adi (backend-api telemetry persister icin). v2: eski
    # push consumer 1.4M smoke-test backlog + 10K ack_pending ile kilitliydi.
    # Yeni isim DeliverPolicy.NEW ile guncele baslar; stream verisi silinmez.
    # Batch-commit throughput sayesinde v2 tekrar geride kalmaz.
    nats_consumer_telemetry_persist: str = "backend-api-telemetry-persist-v2"
    # Gateway compose dosyasi indirilirken gateway user/password URL'e gomuluyor.
    # `infra/nats/nats-server.conf`'taki `gateway` user'inin cleartext sifresi.
    # bootstrap.sh urettiginde .env'e yazar; backend bu degeri okuyup compose
    # template'e gomer. Bos ise gateway compose URL'i anonim kalir ve NATS server
    # deny-all ile reddeder — production'da set EDILMEDIGI surece gateway calismaz.
    nats_gateway_password: str = ""

    internal_service_token: str = "change-me-internal-token"
    # DB'de saklanan secret'lar (SMTP/SMS/Telegram credentials, outbound auth
    # token) icin Fernet sifreleme anahtari. Bos ise SECRET_KEY'den HKDF ile
    # deriv edilir (geriye uyumlu). Production'da explicit ayri bir anahtar
    # set edilmesi onerilir cunku SECRET_KEY rotasyonu yapildiginda eski
    # sifrelenen veriler decrypt edilemez.
    # Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
    secrets_master_key: str = ""
    # Backend `/internal/alarms` endpoint'i alarm olusturduktan sonra
    # `dispatch_alarm_notifications` cagirir mi? notification-worker ayri
    # dispatcher servisi production'a alindiginda bu flag False olmali —
    # boylece cift dispatch (backend + worker) onlenir.
    # Default False: production'da notification-worker tek dispatcher;
    # backend inline dispatch SADECE dev/test icin. Operator backend'i
    # standalone (worker'siz) calistirsin istiyorsa env: True override.
    notification_inline_dispatch_enabled: bool = False
    smtp_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@enerjione-grid.local"
    # Frontend public URL — davet linklerinin tam URL olarak uretilmesi icin
    # gereklidir. Bos ise relative path doner; admin linki kendi domain'i
    # ile birlestirir. Ornek: `https://grid.example.com` veya `http://192.168.1.10`.
    frontend_base_url: str = ""
    sms_enabled: bool = False
    sms_provider: str = "mock"
    sms_api_url: str = ""
    sms_api_key: str = ""
    # WhatsApp Web sidecar (Baileys, QR ile giris) — internal_service_token
    # ile ayni auth. Docker network icinde servis adiyla erisilir.
    whatsapp_web_gateway_url: str = "http://whatsapp-web-gateway:8016"
    service_role: str = "api"
    service_name: str = "backend-api"
    worker_health_host: str = "127.0.0.1"
    worker_health_port: int = 0

    # Cihaz bazli offline lisans. Docker'da kalici volume; Windows native'de
    # PROGRAMDATA altinda tutulur. Makine kimligi USB/disk/MAC'ten degil,
    # sabit OS kimliginden gelir; donanim eklemek lisansi bozmaz.
    license_dir: str = "./license-data"
    host_machine_id_path: str = "/run/host-machine-id"
    license_upload_max_bytes: int = 50 * 1024

    # Appliance (mini PC) modu — host ag ajani (e1-netd) ile paylasilan dizin.
    # Backend buraya SADECE request.json yazar; state.json/status.json'i okur.
    # Host'ta root ile calisan ajan istekleri dogrulayip nmcli ile uygular.
    # Dizin yoksa/yazilamiyorsa appliance modu "kapali" kabul edilir ve Ag
    # Ayarlari sayfasi bunu kullaniciya soyler (hata degil).
    network_state_dir: str = "/var/lib/e1-grid/net"

    # Hat Arizalari sayfasi gosterim gecikmesi (saniye). Bir ariza acildiktan
    # sonra bu sure gecene kadar listede GORUNMEZ; harita aninda gosterir.
    # Amac: haberlesme gecikmesiyle gec gelen alarmlar bu pencere icinde
    # birikip arizanin dogru cihaz araligiyla TEK SEFERDE acilmasini saglamak
    # (yanlis konumda gecici ariza gostermemek). 0 = gecikme yok.
    # Env: FAULT_DISPLAY_DELAY_SEC
    fault_display_delay_sec: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @model_validator(mode="after")
    def _normalize_app_version(self) -> "Settings":
        """`E1_VERSION` bir IMAJ ETIKETI; her zaman surum degil.

        docker-compose ayni degiskeni hem imaj tag'i hem app_version icin
        kullaniyor. Tag 'latest' veya '2.25' (minor) olabilir; bunlar arayuzde
        surum olarak gosterilemez. Semver'e benzemiyorsa paketle gelen
        varsayilana doneriz.
        """
        raw = (self.app_version or "").strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+", raw):
            self.app_version = _FALLBACK_APP_VERSION
        return self

    @model_validator(mode="after")
    def _validate_production_safeguards(self) -> "Settings":
        """Production / staging ortaminda guvenlik kontrolleri.

        Operator yanlislikla default placeholder secret'larla prod'a deploy
        etmesin diye boot'ta RuntimeError firlatir. Mesaj net: operator hangi
        env degiskenini set etmesi gerektigini anlar.

        Kontroller (app_env in ('production', 'prod') iken):
          * `secret_key` placeholder olamaz (JWT forge engellenir)
          * `internal_service_token` placeholder olamaz (internal endpoint'ler
            taklit edilemez)
          * `event_bus_backend == 'inprocess'` olamaz (mikroservis-arasi
            iletisim kopar — alarm.created event'leri notification-worker'a
            ulasmaz)
          * `cors_origins == '*'` olamaz (CORS spec ihlali; credential leak)
        """
        env = (self.app_env or "development").strip().lower()
        if env not in ("production", "prod"):
            return self
        errors: list[str] = []
        if _is_placeholder_secret(self.secret_key):
            errors.append(
                "SECRET_KEY .env'de set edilmemis veya placeholder ('change-me-*', "
                "'please-change-me-*' vb.); JWT imzalama icin >=32 byte yuksek-entropy "
                "bir deger zorunlu."
            )
        if _is_placeholder_secret(self.internal_service_token):
            errors.append(
                "INTERNAL_SERVICE_TOKEN .env'de set edilmemis veya placeholder; "
                "mikroservis-arasi auth icin >=32 byte deger zorunlu."
            )
        if self.event_bus_backend.strip().lower() == "inprocess":
            errors.append(
                "EVENT_BUS_BACKEND=inprocess production'da kullanilamaz. "
                "RabbitMQ backend zorunlu (`rabbitmq`); aksi takdirde alarm "
                "event'leri notification-worker'a ulasmaz."
            )
        if "*" in self.cors_origin_list:
            errors.append(
                "CORS_ORIGINS production'da '*' olamaz. Explicit origin "
                "whitelist belirtin (orn: https://app.example.com)."
            )
        # NATS URL credentials check — anonim baglanti NATS server tarafindan
        # deny-all ile reddedilir; backend silent fail eder ve telemetri akmaz.
        # Production'da `nats://user:password@host:port` formatinda olmali.
        if "@" not in self.nats_url:
            errors.append(
                "NATS_URL production'da 'nats://user:password@host:port' "
                "formatinda olmali. Anonim baglanti NATS server tarafindan "
                "reddedilir (deny-all). bootstrap.sh `.env`'e NATS_BACKEND_PASSWORD "
                "uretir; backend NATS_URL'i `nats://backend:${NATS_BACKEND_PASSWORD}@nats:4222` "
                "olarak set edin."
            )
        # RabbitMQ vhost izolasyonu — production'da kok `/` vhost reddedilir.
        # `/` vhost paylasimli/default oldugu icin saldirgan baska bir tenant
        # uzerinden bizim queue/exchange'lere erisebilir; dedicated izolasyon
        # icin operator `e1` (veya benzeri ozel) vhost'unu URL'e koymalidir.
        from urllib.parse import urlparse as _urlparse

        try:
            _amqp = _urlparse(self.rabbitmq_url)
            _vhost = (_amqp.path or "").lstrip("/")
            if not _vhost or _vhost == "" or _vhost == "/":
                errors.append(
                    "RABBITMQ_URL production'da kok `/` vhost'u kullanamaz "
                    "(paylasimli/default vhost; saldirgan baska tenant uzerinden "
                    "queue'lara erisebilir). Dedicated bir vhost belirtin "
                    "(orn: `amqp://user:pass@host:5672/e1`). Backend startup'i "
                    "bu vhost'u idempotent olarak yaratir."
                )
        except Exception:  # noqa: BLE001
            errors.append("RABBITMQ_URL parse edilemedi; gecerli AMQP URL girin.")
        if not self.nats_gateway_password.strip():
            errors.append(
                "NATS_GATEWAY_PASSWORD production'da bos olamaz. Gateway compose "
                "template'i bu sifreyle URL uretir; bos ise gateway anonim "
                "baglanip NATS deny-all ile reddedilir, telemetri akmaz."
            )
        if errors:
            joined = "\n  - ".join(errors)
            raise RuntimeError(
                f"GUVENLIK: APP_ENV={env} ortaminda asagidaki ayarlar gecersiz:\n  - {joined}"
            )
        return self


settings = Settings()
