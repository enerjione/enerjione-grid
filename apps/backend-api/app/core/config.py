import re

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Imajla birlikte paketlenen surum. Kok dizindeki VERSION dosyasi ve
# apps/frontend-web/package.json ile AYNI olmali; release CI ucunu de
# birbirine karsi dogrular.
_FALLBACK_APP_VERSION = "2.53.0"


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
    # NATS TLS — kendinden imzali CA sertifikasinin yolu.
    #
    # BOS = TLS KAPALI (varsayilan, bugunku davranis). Doluysa baglanti
    # YALNIZCA bu CA'ya guvenir; isletim sisteminin guven deposu kullanilmaz.
    # Sertifikalar `infra/scripts/linux/nats-tls-setup.sh` ile uretilir ve
    # `NATS_URL` `tls://` semasina cevrilir.
    #
    # Neden gerekli: 4222 tum arayuzlere acik ve gateway'ler parolayla
    # baglaniyor; TLS'siz hem parola hem telemetri duz metin gider.
    nats_ca_file: str = ""
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
    # Stream retention (gun) — JetStream WAL'in disk'te kalma suresi.
    #
    # HANGI SINIR NE ZAMAN ISLER (ikisi de gerekli, biri digerinin yerine
    # gecmez):
    #   * YUKLU sahada `max_bytes` baskindir. Olcum (100 cihaz, 1.135 msg/sn,
    #     ~600 bayt/mesaj): ~2,45 GB/saat. Yani raw tavani saatler icinde
    #     dolar, `max_age` gunlere HIC ULASILMAZ.
    #   * KUCUK sahada (5-10 cihaz) tam tersi: hiz o kadar dusuk ki bayt
    #     tavani asla carpmaz ve stream, ACK'lenmis mesajlari da tutmaya
    #     devam eder — cunku retention=LIMITS (bkz. jetstream_bus.py).
    #     Yani raw stream bir ingest TAMPONU degil ARSIV gibi davranir.
    #     Kalicilik zaten telemetry_history'de; JetStream'de ikinci bir kopya
    #     tutmanin degeri yok, maliyeti disk.
    #
    # 7 -> 2 gun (raw) ve 30 -> 7 gun (normalized) BU IKINCI DURUM icindir ve
    # yuklu sahada TEK BIR MESAJ dusurmez (bayt tavani saatler icinde zaten
    # carpiyor). Kaybedilen sey yalnizca sudur: tuketicisi 2 gunden uzun sure
    # ayakta olmayan KUCUK bir sahada replay penceresi kisalir.
    nats_stream_raw_max_age_days: int = 2
    nats_stream_normalized_max_age_days: int = 7
    # DLQ (dead-letter queue): worker max_deliver'a takilan "poison" mesajlari
    # buraya tasinir. Sessizce kaybolmaz; operator JetStream UI'dan veya `nats
    # stream view TELEMETRY_DLQ` ile inceler, root-cause sonra replay eder.
    # Subject: `e1.dlq.<service>.<original-subject>` — service adi DLQ'ya
    # publish eden worker'in hangisi oldugunu gosterir.
    nats_stream_telemetry_dlq: str = "TELEMETRY_DLQ"
    nats_subject_telemetry_dlq: str = "e1.dlq.>"
    # DLQ yas siniri BILEREK 30 gunde birakildi. Bu stream bir veri akisi
    # degil KANIT deposudur ve normal sartlarda neredeyse bostur; kisaltmanin
    # disk kazanci sifira yakin, maliyeti ise "SCADA'ya su olay neden gitmedi"
    # sorusunun cevabini operator sormadan silmek olurdu.
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
    # ----- Kalicilastirma kaynagi: RAW -> NORMALIZED gecisi ------------------
    # HEDEF MIMARI: backend yalnizca HTTP alir ve akisa YAYINLAR; ham akisi
    # tag-engine isler, kalicilastirma tag-engine CIKISINDAN beslenir.
    # Boylece arsivdeki deger ile alarm/IEC104/Modbus'un gordugu deger AYNI
    # normalizasyondan gecer (eskiden persist RAW'i, digerleri NORMALIZED'i
    # okuyordu — iki farkli karar zinciri).
    #
    # Yeni durable AYRI bir stream'de (TELEMETRY_NORMALIZED) yasar, bu yuzden
    # ayri bir isim gerekir. Eski RAW durable'i (yukaridaki v2) SILINMEZ;
    # once BOSALTILIR (bkz. telemetry_consumer._cutover_*), cunku icinde
    # HENUZ YAZILMAMIS olcumler durur.
    nats_consumer_telemetry_persist_normalized: str = "telemetry-persist-normalized-v3"

    # ----- IKI HAT: oncelikli (dijital/durum) ve toplu (analog) --------------
    # SORUN: tek durable ile yuz binlerce ANALOG olcumu ile bir ARIZA BAYRAGI
    # AYNI SIRADA bekliyordu. Ariza gostergesinde gecikme konfor degil
    # DOGRULUK meselesi: 10 dk gec gorulen ariza KACIRILMISTIR.
    #
    # COZUM: tag-engine konuya bir SINIF token'i ekler
    # (`e1.telemetry.normalized.<gw>.<prio|bulk>`, bkz. tag_engine/katalog.py)
    # ve kalicilastirma her sinifi KENDI durable'i ile ceker. JetStream
    # durable'lari birbirinden bagimsiz teslim/ack muhasebesi tutar; toplu
    # hattaki birikim oncelikli hattin ONUNE GECEMEZ.
    #
    # ONCELIKLI HAT KAPSAM DISI BIRAKMAZ: filtresi IKI konu tasir — sinifli
    # `*.prio` VE SINIFSIZ 4 token'li eski konu. Guncelleme sirasinda henuz
    # yenilenmemis bir tag-engine hala `...normalized.<gw>` basiyor olabilir;
    # o mesajlar hicbir sinifli filtreye uymaz ve SESSIZCE KAYBOLURDU.
    # Bilinmeyen -> oncelikli hat kurali burada da gecerli.
    nats_subject_telemetry_normalized_prio: str = "e1.telemetry.normalized.*.prio"
    nats_subject_telemetry_normalized_bulk: str = "e1.telemetry.normalized.*.bulk"
    # Sinifsiz (eski surum tag-engine) konu — oncelikli hattin ikinci filtresi.
    nats_subject_telemetry_normalized_legacy: str = "e1.telemetry.normalized.*"
    # Hat basina durable. `telemetry-persist-normalized-v3` (yukarida) TEK HAT
    # donemine ait; bu ikisi olusmadan ONCE o BOSALTILIR ve sonra silinir —
    # ayni gerekce zinciri `_cutover` ile birebir ayni (bkz.
    # telemetry_consumer._hat_ayrimina_gec).
    nats_consumer_telemetry_persist_prio: str = "telemetry-persist-prio-v1"
    nats_consumer_telemetry_persist_bulk: str = "telemetry-persist-bulk-v1"
    # Hat ayrimini kapatma kolu (geri alma). false = TEK HAT (v3 durable'i,
    # bugunku davranis). Ayrim yapilmis bir kurulumda false'a alinirsa iki hat
    # BOSALTILIP silinir ve tek hatta donulur; kaybolan olcum olmaz.
    telemetry_persist_split_enabled: bool = True
    # auto | raw | normalized
    #   auto       — VARSAYILAN. Once eski RAW durable'i bosaltir (birikmis
    #                mesajlar kaybolmasin), bosalinca NORMALIZED'e gecer.
    #                Sahadaki kurulum guncellenince kendiliginden dogru olan
    #                tek deger budur.
    #   raw        — gecisi ERTELE, gecis yapilmissa GERI AL (eski davranis;
    #                tag-engine olmadan da persist calisir). Tesis/teshis icin.
    #                GERI ALIRKEN ortada kalan NORMALIZED durable'i SILINIR:
    #                birakilsaydi kimse tuketmedigi icin birikir ve `auto`ya
    #                donuldugunde (ki `auto` compose varsayilanidir) o birikim
    #                bastan islenip ayni olcumu IKINCI KEZ yazardi. RAW
    #                durable'i da simetrik olarak geri sarilarak yaratilir ki
    #                surecin kapali oldugu pencere kaybolmasin.
    #   normalized — drenaji ATLA ve dogrudan NORMALIZED'e bagla. YALNIZCA
    #                temiz kurulumda guvenli; birikmis RAW varsa O MESAJLAR
    #                HIC YAZILMAZ.
    telemetry_persist_source: str = "auto"
    # Gecis aninda NORMALIZED akisinda ne kadar GERI SARILIR (saniye).
    #
    # NEDEN GERI SARILIYOR: RAW drenaji bittigi an ile yeni durable'in
    # olusturuldugu an arasinda tag-engine'in kendi gecikmesi kadar bir
    # pencere vardir. Geri sarmazsak o penceredeki olcumler HIC yazilmaz.
    # Geri sarilan aralik ZATEN yazilmis olcumleri tekrar getirir; onlari
    # `processed_messages` defteri eler — dolayisiyla bu deger defterin
    # OMRUNU (processed_messages_retention_hours) ASAMAZ, yoksa dedup kaydi
    # silinmis mesajlar IKINCI KEZ yazilirdi. Kod bu tavani zorlar.
    telemetry_persist_cutover_overlap_sec: int = 900
    # Toplu yazimda tek COPY / execute_values dilimindeki azami satir sayisi.
    # execute_values ifade basina 65535 parametre tavanina tabidir (9 kolonlu
    # `telemetry_latest` icin ~7280 satir); 5000 bu tavanin guvenli altinda
    # kalir ve parti boyutu ayarla buyutulse bile ifade tavani asilamaz.
    telemetry_write_chunk_size: int = 5000
    # NATS HTTP monitor adresi (jsz) — Sistem Durumu asama kuyruklari buradan
    # okunur. Compose icinden http://nats:8222; ulasilamazsa panel asamasiz
    # calisir (fail-soft), rapor dusmez.
    nats_monitor_url: str = "http://localhost:8222"
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
    # takas: tavana carpinca en eski mesajlar sessizce kaybolur, ama sistem
    # asla durmaz.
    #
    # TAMPON SURESI — HEDEF OLCEKTE HESAPLANMIS DEGER
    # -----------------------------------------------
    # Buradaki yorumlar bir zamanlar "raw icin ~19 saat" diyordu. O rakam
    # 25,9M satir/gun (200 cihaz) varsayimina dayaniyordu; HEDEF yuk ise
    # 600 cihaz x 20 aktif sinyal / 10 sn = ~1.200 deger/sn = 103,68M
    # satir/gun, yani DORT KAT fazla. Gercek tampon sureleri:
    #
    #   raw         24 GiB  -> ~9,9 saat
    #   normalized  12 GiB  -> ~5,0 saat
    #
    # 2026-08-04'te tavanlar yukseltildi (bkz. asagidaki saha olcumu):
    # 3 GiB'lik NORMALIZED tavani 500 cihazlik testte DOLDU ve akis
    # sessizce en eski mesajlari atmaya basladi. Yeni tavanlarla ~10
    # saatlik bir backend kesintisi veri kaybi olmadan tolere edilir.
    #
    # BU TAVANLARI DUSURMEK NEDEN TEHLIKELI (okumadan degistirmeyin)
    # -------------------------------------------------------------
    # discard=OLD ile tavan bir "budama esigi"dir ve budama TUKETICININ
    # NEREDE OLDUGUNA BAKMAZ: stream'in kuyrugundan siler, ack durumundan
    # bagimsiz. Yani tavan, tuketicinin GERI KALABILECEGI mesafedir.
    #
    # Olcum (100 cihaz): gateway 1.135 msg/sn basiyor, backend 480 msg/sn
    # isliyor -> saniyede 655 mesaj (~393 KB/sn, ~1,4 GB/saat) BIRIKIYOR.
    # Olcum aninda JetStream deposu 5,6 GB idi ve bunun bir kismi HENUZ
    # ISLENMEMIS telemetridir. Tavani 5,6 GB'in ALTINA cekmek, backend'in
    # ilk yeniden baslamasinda (jetstream_bus drift yolu update_stream
    # cagirir) o mesajlari SESSIZCE siler.
    #
    # 2026-08-04 SAHA OLCUMU — TAVANLAR YUKSELTILDI
    # 500 cihazlik yuk testinde (~15.000 sinyal/sn) TELEMETRY_NORMALIZED
    # 3 GiB tavanina DAYANDI: `bytes` = 3.221.224.872 / `max_bytes` =
    # 3.221.225.472 — 600 bayt kalmisti. `discard: old` oldugu icin akis
    # SESSIZCE en eski mesajlari atmaya baslamisti, yani VERI KAYBI
    # yasaniyordu.
    #
    # Daha kotusu: dolu bir akisa yazmak, bos bir akisa yazmaktan kat kat
    # pahalidir. Her yayin once yer acmak icin eski mesajlari silmeyi ve
    # dosya indekslerini yeniden yazmayi gerektirir. NATS %124 CPU'da tam
    # bunu yapiyordu (87 GB okuma / 85 GB yazma / 145M yazma syscall).
    # Zincir soyle kilitlenmisti:
    #   NORMALIZED dolu -> tag-engine yayinlayamiyor -> aldigi 10.000
    #   mesaji ONAYLAYAMIYOR -> yenisini cekemiyor -> RAW 2,1 milyona
    #   sisiyor -> persist/alarm/iec104/modbus AC bekliyor (hepsinin
    #   bekleyeni 0).
    # tag-engine %13 CPU'daydi: yavas degil, ONUNDEKI KAPI KAPALIYDI.
    #
    # Tavanlar yukseltilince (RAW 8->24, NORMALIZED 3->12, hesap 12->48 GiB)
    # birikim 90 saniyede 3,09M -> 1,56M'a dustu. Kapi acildi.
    #
    # NEDEN BU DEGERLER: tipik saha cihazinda 456 GB disk var ve olcumde
    # 351 GB bostu. 38 GiB toplam tavan, diskin ~%8'i — telemetri arsivi
    # (Postgres) ile yarismayacak kadar kucuk, ama saatler suren bir
    # kesintiyi tasiyacak kadar buyuk.
    #
    # TAVAN YUKSELTMEK COZUM DEGIL, ZAMAN KAZANDIRIR. Tuketim uretimin
    # altinda kaldigi surece daha buyuk bir tavan da dolar; sadece daha
    # gec. Asil is tuketimi hizlandirmak (toplu yazim, yatay olcek).
    #
    # Toplam: 24 + 12 + 2 = 38 GiB. nats-server.conf `max_file_store`
    # bunun UZERINDE kalmali (48 GiB) — aksi halde hesap tavani once dolar
    # ve akis basina tavan hic devreye girmeden SERT RED geri gelir.
    # Degerler BAYT cinsinden ve LITERAL yazilir (ifade degil): hem
    # tests/test_config_consistency.py bunlari kaynaktan okuyup .env.example /
    # docker-compose.yml ile karsilastirabilsin, hem de operator neye baktigini
    # tereddutsuz gorsun.
    # 24 GiB — hedef olcekte (~1.200 deger/sn) yaklasik 9,9 SAAT tampon.
    nats_stream_raw_max_bytes: int = 25_769_803_776
    # 12 GiB — hedef olcekte yaklasik 5,3 saat.
    nats_stream_normalized_max_bytes: int = 12_884_901_888
    nats_stream_dlq_max_bytes: int = 2_147_483_648         # 2 GiB

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
    #
    # GERCEK redelivery penceresi ack_wait(60s) x max_deliver(10) = 10 DAKIKA.
    # Ustelik ikinci bir dedup katmani daha var: telemetry_history dogal
    # anahtarinda (device_id, signal_key, source_timestamp) ON CONFLICT DO
    # NOTHING.
    #
    # DEGER TARIHCESI: 7 gun -> 24 saat -> 2 saat.
    #   7 gun  gercek ihtiyacin ~1000 katiydi, tabloyu ~180M satira cikariyordu.
    #   24 saat hala 144 kat marj birakiyordu ve SAHADA OLCULDU: 15 cihazlik
    #     test kurulumunda tablo 184 MB'a ciktiktan sonra hala buyuyordu
    #     (376 islem/sn). 600 cihazda ayni oran 24 saatte on milyonlarca satir.
    #   2 saat  redelivery penceresinin 12 KATI — bir mesajin yeniden teslim
    #     edilebilecegi en uzun sureden hala cok uzun, ama tablo 12 kat kucuk.
    #
    # ALT SINIR KODDA ZORLANIYOR (telemetry_retention): redelivery penceresinin
    # altina inilirse dedup kaydi mesaj HALA yeniden teslim edilebilirken
    # silinir ve DUPLICATE telemetri yazilir.
    #
    # NOT: birim GUN'den SAAT'e cevrildi; eski PROCESSED_MESSAGES_RETENTION_DAYS
    # artik okunmuyor — telemetry_retention.py set edilmisse uyari loglar.
    processed_messages_retention_hours: int = 2
    # TEMIZLIK ARALIGI — SILME KAPASITESI URETIMIN USTUNDE OLMALI
    #
    # 2026-08-05 SAHA OLCUMU: bu deger 600 sn idi ve tablo 20 GB'a / 74 MILYON
    # satira ciktiy. 2 saatlik pencerede olmasi gereken tabloda 10,5 SAATLIK
    # veri birikmisti.
    #
    # HESAP — neden kacinilmazdi:
    #   silme kapasitesi = retention_delete_batch (20.000)
    #                    x retention_max_batches_per_run (50)
    #                    / aralik (600 sn)          = 1.666 satir/sn
    #   olculen uretim   = ~2.900 satir/sn
    # Silme uretimin ALTINDA oldugu icin tablo kararli duruma HIC gelemez.
    #
    # KISIR DONGU: tablo buyur -> dedup sorgusu onbellege sigmaz -> her mesaj
    # icin DISKTEN okuma -> tuketim yavaslar -> temizlik daha da geri kalar.
    # Postgres bekleme olaylarinda birebir gorulmustu:
    #   IO | DataFileRead | SELECT processed_messages.message_id FROM ...
    # Yazma hizi 2.920 -> 1.581 satir/sn'ye dusmustu ve CPU'lar BOSTA
    # bekliyordu (tikanma degil, disk sirasi).
    #
    # 60 sn ile kapasite 16.666 satir/sn = uretimin ~5,7 KATI. Kararli
    # durumda maliyet yok: `_batch_delete` parti dolmayinca ERKEN CIKAR,
    # yani yalnizca gercekten silinecek kadar is yapilir.
    #
    # AYNI HATA outbox_events'te de yasanmisti (bkz. 2026-08-04). Kural:
    # bir temizlik isinin kapasitesi, temizledigi seyin URETIM HIZINI
    # asmali — aksi halde is "calisiyor" gorunur ama hicbir zaman yetismez.
    processed_messages_interval_sec: int = 60
    # `system_events` — denetim/olay kaydi. Onceden HIC retention yoktu.
    # 2 yil: operator karari (yasal/operasyonel geriye donuk inceleme ufku).
    # Alarm KENDILIGINDEN normale dondugunde `system_events`e ayrica kayit
    # dusulsun mu?
    #
    # VARSAYILAN KAPALI. Bilgi ZATEN alarm satirinda: `AlarmEvent.reset` ve
    # `reset_at`. Yani bu olay kaydi tekrar; alarm gecmisi arayuzu de
    # `alarm_events` tablosundan okuyor.
    #
    # Kapali olmasinin asil gerekcesi gurultu: dalgalanan bir sinyal (saha
    # kaynakli ya da yuk testi sirasinda) dakikalar icinde binlerce
    # tetiklen/temizlen cifti uretiyor ve GERCEK operator olaylari
    # (yetki kullanimi, komut gonderimi, ayar degisikligi) bu yiginin
    # icinde kayboluyor. Denetim kaydinin degeri okunabilirliginde.
    #
    # DIKKAT: bu ayar YALNIZCA onaylanmamis alarmin temizlenmesini etkiler.
    # ONAYLANMIS alarm temizlendiginde satir SILINIYOR ve olay kaydi geriye
    # kalan TEK iz — o her zaman yazilir, bu bayrak onu kapatmaz.
    alarm_auto_clear_events: bool = False

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

    # `outbox_events` — broker'a yayin kuyrugu. SAHADA OLCULDU (2026-08-03,
    # 100 cihaz / 176 sinyal): tablo 1.7 GB / 2.32M satirdi ve EN ESKI SATIR
    # 36 DAKIKALIKTI — ayarli pencere 15 dakika oldugu halde.
    #
    # SEBEP ARITMETIK: purge tavani OUTBOX_PURGE_BATCH(10.000) /
    # OUTBOX_PURGE_INTERVAL_SEC(10sn) = 1.000 satir/sn idi; uretim ise ~1.074
    # satir/sn. Silme hizi uretimin ALTINDA kalinca tablo hicbir zaman kararli
    # duruma gelmez, monoton buyur. Artik purge RetentionWorker'da kosuyor ve
    # tavani retention_delete_batch x retention_max_batches_per_run =
    # 1.000.000 satir/tur; 60sn periyotla ~16.600 satir/sn, uretimin ~15 kati.
    #
    # NEDEN 15 DAKIKA (deger olculmus, tahmin degil): `dedup_key` UNIQUE +
    # ON CONFLICT DO NOTHING oldugu icin satir durdugu surece ayni message_id
    # tekrar kuyruga girmez; yani pencere = tekrar-yayin koruma penceresi.
    # Gercek yeniden teslim penceresi ack_wait(60sn) x max_deliver = 10 dakika,
    # 15 dakika ona %50 pay birakir. Tek koruma bu da degil: tuketici tarafinda
    # bagimsiz `processed_messages` defteri var. Yani pencereyi kisaltmanin
    # bedeli CIFT KAYIT DEGIL, bosa giden bir yayin.
    #
    # ALT SINIR KODDA ZORLANIYOR (telemetry_retention.purge_outbox_events):
    # redelivery penceresinin altina inilemez.
    #
    # published=False satira ASLA dokunulmaz — teslim edilmemis olay, sure ne
    # olursa olsun kaybolmaz.
    outbox_published_retention_minutes: int = 15
    outbox_purge_interval_sec: int = 60
    # DEAD-LETTER: yayini kalici olarak basarisiz olan satirlar. Bunlar
    # yayinlanmis satirlarla AYNI KEFEYE KONMAZ — 15 dakikalik pencere onlari
    # hata ayiklama sansi dogmadan siler. 14 gun: operatorun "SCADA'ya su olay
    # neden gitmedi" sorusunu geriye donuk cevaplayabilecegi ufuk.
    outbox_dead_letter_retain_days: int = 14
    # Kac basarisiz denemeden sonra satir dead-letter'a dusurulur. Dead-letter
    # olmadan TEK zehirli satir tum kuyrugu kalici olarak tikiyordu
    # (head-of-line block) ve published=False hic silinmedigi icin tablo
    # sinirsiz buyuyordu.
    outbox_max_publish_attempts: int = 5

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
    # Gomulu FTP sunucusunun health ucu — FTP ayarlari ekranindaki "baglanti
    # durumu" paneli buradan sunucunun ayakta olup olmadigini ve AKTIF
    # kullanici adini okur (kimlik degisiminin sunucuya yansiyip yansimadigi
    # ancak boyle gorulur). Docker network icinde servis adiyla erisilir.
    ftp_server_health_url: str = "http://ftp-server:8015"
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
    # Arka plan surecinin IC ag adresi. Yalnizca `api` rolunde kullanilir.
    #
    # NEDEN GEREKLI: telemetri tuketicisinin sayaclari (islenmis mesaj/sn,
    # backlog) SUREC ICIDIR. Tuketici ayri container'a alindiginda
    # `/system-status/telemetry-pipeline` API surecinde kalici olarak
    # "critical" ve backlog=None gosterirdi — yani ayirmanin bedeli,
    # ayirmanin ise yarayip yaramadigini olcen TEK gostergeyi kaybetmek
    # olurdu. API bu adresten gercek sayaclari sorar.
    #
    # `all` rolunde (tek container) HIC KULLANILMAZ; sayaclar zaten yerelde.
    backend_worker_host: str = "backend-worker"
    backend_worker_port: int = 8000
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
