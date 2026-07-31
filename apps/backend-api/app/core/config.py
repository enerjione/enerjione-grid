import re

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Imajla birlikte paketlenen surum. Kok dizindeki VERSION dosyasi ve
# apps/frontend-web/package.json ile AYNI olmali; release CI ucunu de
# birbirine karsi dogrular.
_FALLBACK_APP_VERSION = "2.28.0"


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

    # ----- JetStream DISK TAVANI (stream basina) ----------------------------
    # `max_age` bir ZAMAN siniridir, disk sinirini GARANTI ETMEZ: disk
    # kullanimi hiz x zamandir ve hiz kontrol edilmedigi surece yas tabanli
    # retention hicbir bayt tavani vermez. Onceden her uc stream de
    # max_bytes=0 (SINIRSIZ) ile olusturuluyordu; tek gercek fren
    # nats-server.conf'taki hesap seviyesi `max_file_store` idi ve o da
    # BUDAMA yapmaz — tavana carpinca publish REDDEDILIR, yani telemetri
    # akisi tamamen DURUR.
    #
    # Artik her stream kendi bayt tavanini tasiyor ve discard=OLD ile tavana
    # carpinca EN ESKI mesajlar dusuruluyor; akis DEVAM EDER. Bu bilincli bir
    # takas: uzun bir kesintide (raw icin ~19 saat) en eski mesajlar sessizce
    # kaybolur, ama sistem asla durmaz.
    #
    # Toplam: 8 + 3 + 1 = 12 GiB. nats-server.conf `max_file_store` bunun
    # UZERINDE kalmali (aksi halde hesap tavani once dolar ve sert red geri
    # gelir).
    # Degerler BAYT cinsinden ve LITERAL yazilir (ifade degil): hem
    # tests/test_config_consistency.py bunlari kaynaktan okuyup .env.example /
    # docker-compose.yml ile karsilastirabilsin, hem de operator neye baktigini
    # tereddutsuz gorsun.
    nats_stream_raw_max_bytes: int = 8_589_934_592         # 8 GiB (~19 saat tampon)
    nats_stream_normalized_max_bytes: int = 3_221_225_472  # 3 GiB
    nats_stream_dlq_max_bytes: int = 1_073_741_824         # 1 GiB

    # ----- Gateway saglik heartbeat'i --------------------------------------
    # Saha gateway'i NAT arkasinda; backend onun /health ucuna ULASAMAZ.
    # Saglik ozeti, gateway'in zaten 1 Hz attigi komut-poll istegine
    # `X-E1-Gateway-Health` basligiyla biniyor (ek istek maliyeti yok).
    #
    # Bu deger gateway'e "kac saniyede bir gonder" der. 1 Hz'de gondermek
    # gateway basina gunde 86.400 gereksiz DB yazimi demekti.
    # 0 = saglik toplama kapali.
    gateway_heartbeat_interval_sec: int = 30

    # ----- WebSocket fan-out (coklu surecin ON KOSULU) ----------------------
    # Canli deger yayini bugun SAF BELLEK-ICI: telemetry_consumer dogrudan
    # ayni surecteki WS abonelerine yaziyor. Bu, backend TEK surec oldugu
    # surece calisir.
    #
    # Coklu surece gecince (API worker'lari + ayri tuketici container'i)
    # bellek-ici yayin KIRILIR: tuketici artik baska bir surecte, dolayisiyla
    # API surecindeki WS abonelerine hicbir sey ulasmaz. Bu ariza SESSIZDIR —
    # ekran "bagli" gorunur, sadece deger akmaz.
    #
    # Cozum: yayin NATS uzerinden yapilir, HER surec abone olur ve kendi yerel
    # WS istemcilerine dagitir.
    #
    # CORE NATS (JetStream DEGIL) — bilincli: canli deger EFEMERDIR. Kalicilik,
    # ack ve disk maliyeti odemenin anlami yok; kacan bir mesaj zaten bir
    # sonraki okumayla veya `/signals/live` anlik goruntusuyle telafi ediliyor.
    #
    # DIKKAT — QUEUE GROUP KULLANILMAZ: queue group mesajlari abone surecler
    # ARASINDA PAYLASTIRIR; her surec 1/N gorurdu ve bu tam da kacinmaya
    # calistigimiz hata. Fan-out icin duz subscribe sart.
    ws_fanout_nats_enabled: bool = True
    ws_fanout_subject: str = "e1.ws.telemetry"

    # ----- Telemetri boru hatti gorunurlugu ---------------------------------
    # Stream `discard=old` ile calisiyor: tampon dolarsa EN ESKI mesajlar
    # SESSIZCE dusurulur (sistem durmasin diye bilincli tercih). Bu sessizligi
    # tehlikeli olmaktan cikaran tek sey, tasmaya YAKLASILDIGINI haber veren
    # bir sinyaldir.
    #
    # Esik stream tavaninin cok altinda: raw stream 8 GiB ~ milyonlarca mesaj
    # alir; 50.000'de uyarmak operatore mudahale icin bol zaman birakir.
    telemetry_backlog_warn_threshold: int = 50_000
    # Olay kaydi rate-limit'i — backlog saatlerce yuksek kalsa bile
    # system_events tablosu dolmasin.
    telemetry_backlog_warn_interval_sec: int = 300

    # ----- Retention / TTL: "disk asla dolmamali" ---------------------------
    # Bu degerler ONCEDEN telemetry_retention.py icinde dogrudan os.getenv ile
    # okunuyordu. config.py'de tanimli olmadiklari icin ne `.env.example`'da ne
    # docker-compose.yml'de goruniyorlardi; operator varliklarindan habersizdi.
    # Artik tek kaynak burasi ve tests/test_config_consistency.py uc dosyayi
    # birbirine kilitliyor.
    #
    # `telemetry` — CANLI DEGER tablosu, kisa kayan pencere. Her (cihaz, sinyal)
    # ikilisinin SON degeri retention'dan muaftir (bkz. _purge_telemetry), yani
    # sure ne olursa olsun canli ekran bosalmaz.
    telemetry_retention_minutes: int = 30
    telemetry_retention_interval_sec: int = 300
    # `processed_messages` — idempotency defteri (ayni mesaj iki kez islenmesin).
    # GERCEK redelivery penceresi ack_wait(60s) x max_deliver(10) = 10 DAKIKA.
    # Ustelik ikinci bir dedup katmani daha var: telemetry_history dogal
    # anahtarinda (device_id, signal_key, source_timestamp) ON CONFLICT DO
    # NOTHING. Onceki 7 gunluk deger gercek ihtiyacin ~1000 katiydi ve tabloyu
    # ~180M satira cikariyordu. 24 saat hala 144 kat marj birakir.
    # NOT: birim GUN'den SAAT'e cevrildi; eski PROCESSED_MESSAGES_RETENTION_DAYS
    # artik okunmuyor — telemetry_retention.py set edilmisse uyari loglar.
    processed_messages_retention_hours: int = 24
    processed_messages_interval_sec: int = 600
    # `system_events` — denetim/olay kaydi. Onceden HIC retention yoktu.
    # 2 yil: operator karari (yasal/operasyonel geriye donuk inceleme ufku).
    system_events_retention_days: int = 730
    system_events_interval_sec: int = 21_600  # 6 saat
    # FIFO tavani: beklenmedik bir olay firtinasinda 2 yil DOLMADAN da sinir
    # devreye girsin, en eski kayitlar dusulsun. 0 = kapali (yalnizca sure).
    system_events_max_rows: int = 5_000_000
    # Retention DELETE'leri LIMIT'li TURLAR halinde kosar. Tek transaction'da
    # milyonlarca satir silmek WAL'i sisirir, tabloyu uzun sure kilitler ve
    # autovacuum'u geride birakir (tablo sismesi). Her tur ayri commit.
    retention_delete_batch: int = 20_000
    # Bir tetikte en fazla kac tur. Tavan, tek bir purge'un saatlerce surmesini
    # onler; artan satirlar bir sonraki tetikte silinir (birikme olmaz cunku
    # tavan x batch >> periyottaki uretim).
    retention_max_batches_per_run: int = 50

    # ----- DISK GUARD: son emniyet subabi ----------------------------------
    # Yukaridaki TTL'ler ve NATS/yedek/harita tavanlari "normalde dolmaz"
    # garantisi verir. Disk guard ise "tavanlardan biri YANLIS hesaplanmis
    # olsa bile disk DOLMASIN" garantisidir. Hicbir tavana guvenmez, gercek
    # bos alani olcer.
    #
    # Rezerv YUZDE tabanlidir cunku disk boyutu kurulumdan kuruluma degisir
    # (saha standardi 500 GB, ama 128 GB'lik eski kutular da var). Sabit bir
    # GB degeri kucuk diskte sistemi bogar, buyuk diskte alani bosa yatirir.
    #
    # Cok kucuk disklerde yuzde tek basina yetersiz kalir: PostgreSQL'in
    # VACUUM / index yeniden kurma / pg_dump icin calisma alani ister. Bu
    # yuzden taban = max(toplam x yuzde, mutlak_taban).
    disk_guard_enabled: bool = True
    disk_guard_reserve_percent: int = 10
    disk_guard_reserve_min_gb: int = 5
    disk_guard_interval_sec: int = 300
    # Olculecek yol. Bos ise BACKUP_DIR kullanilir — o GERCEK bir mount'tur
    # (docker volume), container'in `/` overlay'inden daha dogru bir
    # gostergedir. O da yoksa `/` (Windows'ta C:\).
    disk_guard_path: str = ""
    # ACIL seviyede kac yedek korunur. En yeni BASARILI yedek her kosulda
    # korunur; bu deger onun altina inemez.
    disk_guard_emergency_backup_keep: int = 2

    # Manuel / yuklenen yedekler icin YAS bazli temizlik (gun).
    # 0 = KAPALI (varsayilan) — bilincli tercih: operator "buyuk degisiklik
    # oncesi" diye elle aldigi bir yedegi habersiz kaybetmemeli. Zamanlanmis
    # yedekler zaten `retention_count` ile sinirli.
    # Disk ACIL seviyeye gelirse disk_guard bu ayardan BAGIMSIZ olarak fazla
    # yedekleri temizler; yani "disk dolmasin" garantisi bu 0 degeriyle de
    # korunur. Operator duzenli temizlik isterse 90 gibi bir deger verir.
    backup_manual_retention_days: int = 0
    # Basarisiz yedek kayitlari ve yarim kalmis dosyalar KOSULSUZ temizlenir
    # (bunlar tanim geregi cop; yarim .dump dosyasi geri yuklenemez).
    backup_failed_retention_days: int = 7

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
    # Surec rolu: all | api | worker. Bkz. app/core/service_role.py.
    #
    #   all    — HTTP + arka plan isleri (VARSAYILAN, bugunku davranis)
    #   api    — yalnizca HTTP/WS; arka plan isi YOK (coklu worker guvenli)
    #   worker — yalnizca arka plan isleri
    #
    # VARSAYILAN NEDEN `all`: bu alan eskiden "api" idi ama HICBIR YERDE
    # okunmuyordu (olu alan) ve hicbir yerde set edilmiyordu. Artik gercekten
    # davranisi belirledigi icin varsayilanin bugunku tek-container davranisini
    # KORUMASI sart. "api" birakilsaydi, guncelleme alan her saha kurulumunda
    # telemetri tuketicisi, retention, alarm mutabakati ve yedekleme SESSIZCE
    # dururdu — ve hicbir hata gorunmezdi.
    service_role: str = "all"
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

    # Gateway kurulum ajani (e1-gwd) ile paylasilan dizin. Backend buraya
    # SADECE request.json yazar (compose govdesi dahil); state.json/status.json
    # okur. Docker soketine erisimi YOKTUR — container'a docker.sock vermek
    # host'ta root vermekle esdegerdir ve compose'daki cap_drop/read_only
    # sertlestirmesini anlamsizlastirirdi. Dizin yoksa "bu cihaza kur" secenegi
    # kapali gorunur; "baska cihaza kur" akisi etkilenmez.
    gateway_state_dir: str = "/var/lib/e1-grid/gw"

    # Uzaktan bakim izni ajani (e1-rad) ile paylasilan dizin. Backend buraya
    # SADECE request.json yazar; state.json/status.json okur. Tailscale'i
    # CALISTIRMAZ.
    #
    # Uzaktan erisim VARSAYILAN KAPALIDIR: musterinin yetkili kullanicisi
    # (engineer rolu) arayuzden sureli izin verir, sure dolunca erisim
    # kendiliginden kapanir. Sureyi HOST ajani sayar — backend/DB kapali olsa
    # bile izin kapanmali, bu yuzden son tarih DB'de DEGIL ajanin lease
    # dosyasinda (root:root 0700, /var/lib/e1-grid/remote-priv) tutulur.
    remote_access_state_dir: str = "/var/lib/e1-grid/remote"

    # Site basina ust sinir (dakika). Semadaki ve ajandaki mutlak tavan 1440
    # (24 saat); bu ayar yalnizca DAHA DUSUGE cekebilir, yukari cikaramaz —
    # sinirsiz sure "her zaman acik"i geri getirir ve ozelligi iptal ederdi.
    # docker-compose.yml'e BILEREK konmadi: ayni sayiyi dort dosyada tutmak
    # tests/test_config_consistency.py'nin kilitledigi hatanin ta kendisi.
    remote_access_max_minutes: int = 1440

    # Cevrimdisi harita karolari. Tum karolar backend uzerinden gecer:
    # once disk, yoksa yukari akis (ve diske yaz). Saha cihazinda internet
    # kesilse bile indirilmis alan calismaya devam eder.
    #
    # Sinirlar KASITLI dusuk: ucretsiz karo servisleri toplu indirmeye izin
    # vermez, zoom basina karo sayisi 4 katina cikar ve saha diski kucuktur.
    # Kendi karo sunucunuz varsa bu degerleri yukseltebilirsiniz.
    map_tile_dir: str = "/var/lib/e1-map-tiles"
    map_tile_online_fallback: bool = True     # onbellekte yoksa internetten cek
    # INTERNET ONCELIKLI: baglanti varken indirilmis kopya KULLANILMAZ, karo
    # internetten gelir (guncel kalir). Baglanti yoksa indirilen alana duser.
    # False yaparsaniz once disk okunur (bant genisligi tasarrufu, ama karolar
    # indirildikleri gunde kalir).
    map_tile_prefer_online: bool = True
    # Yukari akis bir kez patlayinca bu sure boyunca "internet yok" sayilir ve
    # dogrudan diskten servis edilir. Aksi halde baglanti kopunca HER karo ayri
    # ayri zaman asimi bekler ve harita hic acilmaz.
    map_tile_offline_cooldown_sec: int = 20
    map_tile_max_download_zoom: int = 17      # z18+ bir ilcede bile milyonlarca karo
    map_tile_max_pack_tiles: int = 60_000     # tek alan indirmesinde ust sinir
    map_tile_max_cache_bytes: int = 4 * 1024**3
    map_tile_avg_bytes: int = 18 * 1024       # boyut tahmini icin ortalama karo
    map_tile_concurrency: int = 2             # OSM politikasi: en fazla 2
    map_tile_request_delay_sec: float = 0.05
    map_tile_timeout_sec: int = 20
    map_tile_user_agent: str = "EnerjiOne-Grid/1.0 (+https://enerjione.com; saha cihazi)"

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
