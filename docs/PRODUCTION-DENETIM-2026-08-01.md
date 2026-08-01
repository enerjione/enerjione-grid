# Production Denetim Raporu — 600+ Cihaz Ölçeği · 2026-08-01

> Sürüm: **2.29.0** (`VERSION`, tag `v2.29.0`) · Dal: `main`
> Kapsam: **güvenlik + performans + kullanılabilirlik**, hedef ölçek **600+ cihaz**
> Yöntem: 12 eksende bağımsız kod taraması → her ciddi iddia için ayrı bir "çürütmeye
> çalışan" doğrulayıcı ajan → bütünlük eleştirmeni + ölçek hakemi. 80 ajan, 2364 araç çağrısı.
>
> **126 iddia üretildi → 63 doğrulandı, 3 çürütüldü, 48 ikincil (doğrulanmadı), 15 eleştirmen eki.**
> Doğrulanmışların **32'si sahaya 600 cihazla çıkmayı engelliyor.**

Bu rapor `docs/PRODUCTION-DENETIM-2026-07-31.md`'nin devamıdır, yerine geçmez. Önceki
raporun A1–A13 maddeleri düzeltilmiş; bu rapor **o düzeltmelerin eksik kalan yerlerini**
ve **200 → 600 cihaz geçişinde ilk kırılan noktaları** takip eder.

---

## 0. Karar

**600 cihazla bugün sahaya çıkılamaz.** Sistem ~200 cihazda çalışıyor ve 2.29.0 ile
belirgin biçimde sağlamlaştırılmış; ancak 600 cihaz, mimarinin üç yerinde doğrusal
olmayan bir bedel çıkarıyor:

1. **Anasayfa her açılışta 115.800 satırlık bir kartezyen çarpım çekiyor.** Bu tek başına
   backend-api'yi 2 GB tavanında OOM'a götürüyor ve WS koptuğunda kendini 6 kat hızlandırıyor.
2. **Alarm ve arıza görünürlüğü sabit 500 satırlık bir tavana takılı.** 600 cihazda bu tavan
   aşılıyor ve *açık arızalar arayüzden ve haritadan kayboluyor* — üründen beklenen tek şeyin
   tam tersi.
3. **SCADA çıkışı 600 cihazda yapısal olarak yanlış.** IEC 104 genel sorgusu 12. nesnede
   kesiliyor ve 600 cihazın tamamı aynı (Common Address, IOA) çiftine biniyor.

Bunların hiçbiri "yük altında yavaşlama" değil; üçü de **sessiz yanlış veri** üretiyor.
Arıza-geçiş göstergesi izleyen bir üründe en tehlikeli hata sınıfı budur.

### Zemin gerçeği (çalıştırılarak ölçüldü)

| Kontrol | Sonuç |
|---|---|
| `pytest` (apps/backend-api) | **548 geçti**, 4,8 sn (önceki denetimde 265 idi) |
| `tsc --noEmit` (apps/frontend-web) | temiz |
| `vite build` | başarılı — `index` 814 kB (249 kB gz), `DeviceDetailPage` **868 kB** (292 kB gz) |
| Test boşluğu | 38 test dosyası, ama yalnızca **3'ü** `TestClient` kullanıyor. 35 router'ın çoğunda endpoint testi yok. Frontend **0 test**. tag-engine / notification-worker / ftp-server **0 test**. |
| Sürüm tutarlılığı | `VERSION`=2.29.0 ama `CLAUDE.md` **2.24.6** diyor (güncellenmemiş) |
| Bağımlılık | `chart.js` **ve** `echarts` ikisi de yüklü — iki grafik kütüphanesi, `DeviceDetailPage`'in 868 kB'ının kaynağı |

> Test sayısı iki katına çıkmış, bu iyi. Ama aşağıdaki 63 bulgunun **hiçbiri** mevcut
> testlerle yakalanamazdı: kritik yolların (router'lar, worker'lar, frontend) uçtan uca
> testi hâlâ yok.

---

## 1. Ölçek matematiği — 200'den 600'e çıkarken ilk ne kırılır?

Hedef yük: 600 cihaz × 20 aktif sinyal / 10 sn = **~1.200 değer/sn**, **103,68M satır/gün**.

| Sıra | Kırılan | Ne zaman | Neden |
|---|---|---|---|
| 1 | **backend-api belleği** | 3–5 eşzamanlı anasayfa | `/signals/live` istek başına ~311 MB tepe, container tavanı 2 GB |
| 2 | **Alarm/arıza görünürlüğü** | `alarm_events` > 500 satır (600 cihazda haberleşme alarmı tek başına yeter) | Global `LIMIT 500`, kapsam sonradan |
| 3 | **SCADA çıkışı** | ilk genel sorgu | GI 12. nesnede kesiliyor; CA/IOA çakışması |
| 4 | **Postgres önbelleği** | ~300–400 cihaz | Sıcak index çalışma kümesi 15+ GB, container 3 GB |
| 5 | **Disk** | 4–6 ay | Historian + özet katmanları 350–500 GB, 500 GB diskte |
| 6 | **tag-engine / telemetri tüketicisi** | ~1.200 msg/sn | Tek iş parçacığı, senkron publish, tur başına 416 ms bütçe |
| 7 | **Modbus adres planı** | **327. cihaz** | float32 modunda register alanı doluyor, 273 cihaz sessizce düşüyor |
| 8 | **DB bağlantı havuzu** | `E1_API_WORKERS>1` ile | 250 bağlantı isteniyor, Postgres `max_connections` varsayılan 100 |

**Kapasite sabitleri 4 kat yanlış boyutlandırılmış.** `app/core/config.py:211` ve
`historian_service.py:9` çevresindeki tüm tavanlar **25,9M satır/gün** varsayımıyla
hesaplanmış; hedef yük **103,68M satır/gün**. Kodun kendi yorumlarındaki "~19 saat NATS
tamponu" gerçekte **~3,5 saat**, normalized stream **~1,3 saat**.

---

## 2. ENGELLEYİCİ bulgular (doğrulanmış, 32 adet)

### T1 — Kartezyen `/signals/live`: tek kök neden, sekiz semptom

**`apps/backend-api/app/api/signals.py:158` — [KRİTİK, performans]**

`list_live_values`, görünür cihazlar × **aktif sinyal kataloğunun tamamı** kartezyen
çarpımını üretiyor. Katalog (`app/data/horstmann_sn2_signals.json`) **193 kayıt** ve
hepsi `is_active=True`, hepsi tek model. Telemetrisi hiç gelmemiş çiftler için de
`value=None` satırı üretiliyor. `limit` parametresi, varsayılan tavan ve önbellek **yok**.

- 600 × 193 = **115.800 satır**, **~42 MB** ham gövde, istek başına **~311 MB** bellek tepesi.
- Verinin **%90'ı çöp**: 12.000 anlamlı satır, 103.800 boş dolgu.
- `App.tsx:1089` bunu **anasayfada** çağırıyor ve `device_codes` **geçmiyor** (`App.tsx:1048`).
- Periyot: WS açıkken 30 sn, **WS koptuğunda 5 sn**. Yani bağlantı bozulduğunda yük 6 katlanıyor.
- Anasayfanın gerçekten okuduğu sinyal sayısı: **dört** (üç batarya voltajı + `master.modem_rssi`).

Endpoint'in kendi docstring'i sorunu itiraf ediyor ("600 cihazda ~115.800 satır eder,
`device_codes` ile daraltmak MÜMKÜN") — ama sunucu tarafında hiçbir zorlayıcı koruma yok,
tamamen istemcinin nezaketine bırakılmış. İstemci de daraltmıyor.

> **Doğrulayıcı düzeltmesi:** nginx `gzip on` olduğu için tel üzerindeki gövde ~1,85 MB
> (43 MB değil). CPU/bellek tarafı değişmiyor; ayrıca nginx her yanıt için ek gzip CPU'su
> harcıyor ve `proxy_buffering` açık olduğundan ~42 MB'lık proxy temp dosyası yazıyor.

**Bağlı bulgular:**
- `signals.py:256` [YÜKSEK] — Sorgunun `ORDER BY device_id, signal_key, id DESC`'i mevcut
  index `(device_id, signal_key, source_timestamp DESC)` ile uyuşmuyor. `DISTINCT ON`
  PostgreSQL'de skip-scan değil: 30 dakikalık penceredeki **2,16M satırın tamamı** okunup
  12.000'e indirgeniyor. Yorumdaki "<50 ms / index-only scan" iddiası 600 cihazda geçersiz.
- `App.tsx:467` [YÜKSEK] — Anasayfa WebSocket'i **tüm** telemetriye abone. Her 250 ms flush'ta
  kök `App` (2971 satır) yeniden render oluyor; kod tabanında **hiç `React.memo` yok** (grep: 0).
  1800 Leaflet marker × `setLatLng` + ~1200 SVG path × `setStyle` = saniyede ~24.000 SVG
  öznitelik yazımı. Kiosk tam bu ekranda duruyor.
- `useLiveValuesSocket.ts:92` [ORTA] — Her flush'ta 115.800 satırın tamamı geziliyor,
  satır başına şablon-string üretiliyor: **saniyede ~463.000 string tahsisi**.
- `DeviceMapTab.tsx:369` [ORTA] — Seçili cihazın batarya/RSSI değerleri 115.800 satırlık
  dizide `Array.find` ile aranıyor; saniyede 4 kez, 4 ayrı tam tarama.
- `App.tsx:2250` [ORTA] — Hiçbir yerde kullanılmayan `filteredDashboardLiveValues` memo'su
  her güncellemede 115.800 satırı boşuna süzüyor.

**Bu bir tasarım kararının bedeli, ayrı ayrı bug değil.** Doğru çözüm: kalıcı bir
`telemetry_latest` tablosu (PK `(device_id, signal_key)`, consumer'da upsert) + anasayfa
için dar bir özet ucu. Tek başına bu değişiklik yukarıdaki altı bulgunun tamamını kapatır.

---

### T2 — Alarm ve arıza görünürlüğü: 500 satırlık tavan

**`apps/backend-api/app/services/alarm_engine_service.py:28` + `app/api/alarms.py:44` — [YÜKSEK, veri bütünlüğü]**

Doğruladım — kod aynen şu:

```
# alarm_engine_service.py:28
stmt = stmt.order_by(AlarmEvent.created_at.desc()).limit(500)

# alarms.py:44
rows = list_alarm_events_service(db)          # <- kapsam GEÇİLMİYOR
return _scope_filter_alarms(db, current_user, rows)   # <- kapsam Python'da, LIMIT'ten SONRA
```

Servis `visible_device_ids` parametresini **destekliyor**; `ack-all`/`reset-all` uçları
(A3 düzeltmesi) bunu **doğru** geçiriyor. Düzeltme yalnızca mutasyon yollarına uygulanmış,
**liste yoluna uygulanmamış.**

İki bağımsız sonuç:

1. **Operatör kendi alarmlarını göremiyor.** LIMIT tüm sahaya, kapsam sonra. 20 cihazdan
   sorumlu bir operatör, 600 cihazın en yeni 500 kaydı içinden kendine denk gelenleri görüyor.
2. **Eski açık alarmlar tamamen kayboluyor.** `alarm_events` tablosunun retention'ı yok
   ve 600 cihazda haberleşme alarmı tek başına 600 satır üretebiliyor. 500'lük pencerenin
   dışına düşen bir alarm API'den **hiç dönmüyor** — ve `DeviceMapTab.tsx:427` marker rengini
   bu listeden hesapladığı için **haritadaki marker yeşile dönüyor.**

> Cuma akşamı bir cihazda kalıcı hat arızası oluşur. Pazartesi sabahı operatör haritaya
> bakar: marker **yeşil**. Alarmlar sayfasının "Aktif Alarmlar" sekmesinde de yok.
> Arıza 3 gündür açıktır. Yanıt gövdesinde `truncated`/`total` bilgisi de yok, yani
> arayüzün kırpıldığını anlaması imkânsız.

---

### T3 — "Yeşil yalan": veri yokken normal göstermek

Bu sınıf, bir arıza izleme ürününde en ağır kusur. Üç ayrı yerde var ve üçü de doğrulandı.

**`App.tsx:2765` — [YÜKSEK, kullanım]** — Hat Arızaları sayfası `loading={false}` sabitiyle
çağrılıyor. `FaultListPage.tsx:224` içindeki **doğru yazılmış** yükleniyor dalı bu yüzden
ölü kod. Akış her zaman bir sonraki dala düşüyor: yeşil `CheckCircle2` + **"Aktif arıza yok
— Sistem temiz."** `pollFaults` hatayı `catch { // ignore }` ile yutuyor, hata state'i yok.

> Nöbetçi operatör telefonla "X hattında arıza var mı?" sorusunu alır, sekmeyi açar,
> yeşil tik görür, "yok" der. Gerçekte istemci veriyi getirememiştir.

**`DeviceDetailPage.tsx:641` + `DeviceAllSignalsTab.tsx:164` — [YÜKSEK, kullanım]** —
Binary arıza göstergeleri `value === 1` ile değerlendiriliyor; `value === null` (veri yok)
ile `value === 0` (gerçekten normal) **aynı yeşil "Normal" rozetini** üretiyor. Bu durumu
ele almak için gereken veri **zaten hesaplanıyor**: `DeviceDetailPage.tsx:208-220` `gwOnline`
ve `effQuality` üretiyor — ve `effQuality` dosyanın başka hiçbir yerinde geçmiyor (grep ile
doğrulandı).

> Doğrulayıcı bunu daha da kötüleştirdi: asıl tetikleyici "bayat veri" değil, **kalite**.
> Haberleşmesi kopan cihaz için gateway `comm_lost` kalitesiyle **0.0** basıyor. Alarm motoru
> bu okumayı özellikle bloke ediyor (`_ALARM_BLOCKING_QUALITIES`, `test_alarm_quality_gate.py`)
> — ama UI aynı yükü alıp **taze ama zehirli 0.0'ı yeşil "Normal"** olarak gösteriyor.
> Yani backend doğru davranıyor, frontend onu geçersiz kılıyor.

**`WsStatusBadge.tsx:29` — [YÜKSEK, kullanım — doğrulanmadı]** — Üstteki yeşil "Canlı" rozeti
veri tazeliğini değil sadece soket durumunu gösteriyor. Sunucu 30 sn'de bir ping attığı için
**telemetri tamamen dursa bile rozet yeşil kalıyor.**

---

### T4 — Telemetri hattı: tek bozuk mesaj hattı durduruyor

**`apps/backend-api/app/schemas/telemetry.py:14` — [KRİTİK, dayanıklılık]**

`TelemetryIn.signal_key` ve `quality` alanlarında `max_length` **yok**; zincirin hiçbir
yerinde kırpma da yok. Hedef kolonlar `String(120)` ve `String(50)`. Uzun değer pydantic'i
geçiyor, batch'e giriyor ve ancak toplu INSERT sırasında `DataError` olarak patlıyor.

Kritik nokta: `telemetry_consumer.py:425`'teki tek yakalayıcı `except IntegrityError` —
`DataError`'ı **yakalamıyor** (kardeş sınıflar). İstisna dışarı çıkıyor ve `:644`'teki genel
`except Exception` bunu bir **bağlantı hatası** sanıp `telemetry_consumer_reconnect` logluyor.

> Bir gateway firmware'i 130 karakterlik bir `signal_key` üretir. Batch commit'i patlar,
> hiçbir mesaj ack edilmez. `ack_wait=60s` × `max_deliver=10` ile aynı zehirli mesaj 10 kez
> yeniden dağıtılır; her turda aynı batch'teki **sağlam ölçümler de** birlikte düşer.
> Operatör ekranda "NATS bağlantısı koptu" görür ve sebebi ağda arar. Gerçek sebep tek bir
> uzun string'tir.

**Bağlı bulgular:**
- `telemetry_consumer.py:314` [YÜKSEK] — Postgres kesintisinde mesajlar ne ack ne DLQ
  ediliyor; `max_deliver=10` aşılınca NATS onları **sessizce düşürüyor** → kalıcı veri kaybı.
- `schemas/telemetry.py:60` [YÜKSEK] — `source_timestamp` hiçbir yerde doğrulanmıyor.
  İleri tarihli tek bir damga `telemetry` retention'ından **muaf** oluyor, historian chunk'ı
  hiç düşmüyor ve alarm mutabakatı sonsuza dek o donmuş değeri "son değer" sayıyor.
- `tag_engine/main.py:181` [KRİTİK — doğrulanmadı] — tag-engine tek iş parçacıklı ve mesaj
  başına senkron JetStream publish yapıyor; 1.200 msg/sn tek çekirdeklik tavana dayanıyor
  ve gecikme hiçbir yerde ölçülmüyor.
- `telemetry_consumer.py:601` [YÜKSEK — doğrulanmadı] — Tüketici tur başına **416 ms** bütçeye
  sahip tek serili boru hattı; fetch/persist/WS/outbound/ack adımları hiç üst üste binmiyor,
  500 ack tek tek gönderiliyor.

---

### T5 — Depolama: 500 GB disk 600 cihazı kaldırmıyor

**`alembic .../0023_tier_historian_aggregates.py:190` — [YÜKSEK, dayanıklılık]**

1dk/1saat özet tablolarının retention'ı `_try` ile sarılmış: hata SAVEPOINT'e geri alınıp
**yutuluyor**, yalnızca alembic log'una bir warning düşüyor. Alembic revision yine
damgalanıyor ve migration bir daha koşmuyor — bu **tam olarak 0019'un onarmak için var
olduğu arızanın aynısı, bir kat yukarıda tekrarlanmış.**

Görünürlük katmanı da kapsamıyor: `historian_service._collect` yalnızca
`hypertable_name = 'telemetry_history'` için iş arıyor; CAGG'ler için yalnızca **isim**
döndürüyor. Yani Sistem Durumu'ndaki Historian kartı bu arıza sırasında **"ok"** gösteriyor.

> Doğrulayıcı daha deterministik bir yol buldu: `update.sh:780-812`'deki "idempotent historian
> ensure" bloğu iki CAGG için yalnızca `CREATE MATERIALIZED VIEW` + `add_continuous_aggregate_policy`
> koşturuyor; `add_retention_policy`/`add_compression_policy` **yok**. Yani arıza için
> istisna bile gerekmiyor — her update'te tekrarlanıyor.

1dk özeti: 12.000 seri × 1440 dakika = **17,28M satır/gün ≈ 2,3 GB/gün**. Politikasız
kalırsa ~4 ayda 280 GB. `disk_guard` historian'a **kasten dokunmuyor**, acil durumda en
fazla ~4 GB kurtarabiliyor.

**Bağlı bulgular:**
- `0007_add_telemetry_history_hypertable.py:86` [YÜKSEK] — `chunk_time_interval = 1 gün`:
  600 cihazda günlük chunk **~17 GB** (shared_buffers'ın 22 katı) ve `compress_after=7 gün`
  sürekli ~119 GB sıkıştırılmamış veri bırakıyor.
- `docker-compose.yml:69` [YÜKSEK — doğrulanmadı] — Postgres 3 GB'a kısıtlı ama 600 cihazda
  sıcak index çalışma kümesi **15+ GB**. 200 cihazda önbelleğe sığan probe'lar 600'de diske düşer.
- `0023:69` [YÜKSEK — doğrulanmadı] — Historian + özet katmanları tek başına **350–500 GB**;
  sonuç hiçbir yerde ölçülmeyen bir sıkıştırma oranına bağlı.
- `device_repository.py:72` [YÜKSEK] — Cihaz/gateway silme, 90 günlük hypertable'ı **tek
  transaction'da ve zaman filtresi olmadan** siliyor. 600 cihazda tek cihaz 15,5M satır;
  gateway silme yüz milyonlarca satır.
- `processed_messages` [YÜKSEK — doğrulanmadı] — Telemetri için **gereksiz** ama en pahalı
  tablo: günde 103,68M satır, ~25 GB, batch başına 500 rastgele probe, retention bütçesinin **%72'si**.

---

### T6 — SCADA çıkışı 600 cihazda yapısal olarak bozuk

**`apps/iec104-outbound/iec104_outbound/server.py:393` — [KRİTİK, dayanıklılık]**

`_handle_interrogation` okuma döngüsünün **içinden** çağrılıyor. GI süresince `reader.read()`
hiç çalışmıyor, dolayısıyla master'ın S-frame'leri işlenmiyor. `session.unacked` yalnızca
S-frame gelince sıfırlandığı ve `_send_i` `unacked >= 12` olunca frame'i sessizce düşürdüğü
için: **ACT_CON + 11 nesne gider, geri kalanı ve ACT_TERM hiç gitmez.**

Bu A7'den (yarı-açık TCP) **farklı ve bağımsız** bir yol: bağlantı tamamen sağlıklıyken de
her GI'da tetikleniyor.

**`apps/iec104-outbound/iec104_outbound/registry.py:152` — [YÜKSEK, veri bütünlüğü]**

Doğruladım. IOA **sinyalden** geliyor (tüm cihazlarda aynı), CA ise `_resolve_device_ca`'dan:

```
# registry.py:97
raw = device.get("iec104_common_address")
if raw is None:
    return default          # <- tüm cihazlar aynı CA
```

`Device.iec104_common_address` modelde `nullable=True`, **varsayılan yok**
(`models/device.py:45`). Yani her cihaz elle ayrı CA atanmadıkça **600 cihazın tamamı aynı
(CA, IOA) çiftlerine biniyor** — SCADA'da 600 cihaz tek sanal cihaza çöküyor. Çakışma
uyarısı da hiç basılmıyor.

**Bağlı bulgular:**
- `server.py:237` [YÜKSEK] — **A7 ölçeklenme sertleştirmesi yanlış kopyaya uygulanmış.**
  SCADA'nın gerçekten bağlandığı servis hâlâ korumasız. (Regresyon.)
- `server.py:155` [YÜKSEK, güvenlik] — `_peer_allowed`: `if not self.allowed_peers: return True`.
  IP allowlist **fail-open**. Model varsayılanı NULL, UI'da zorunlu alan değil. 2404/502
  portları koşulsuz 0.0.0.0'a yayınlanmış, bind adresi için env knob'u yok.
- `modbus_plan_service.py:387` [YÜKSEK] — float32 modunda adres planı **327. cihazda doluyor**;
  273 cihaz sessizce düşüyor ve arayüz kapasiteyi "tam" gösteriyor.
- `registry.py:131` [YÜKSEK — doğrulanmadı] — Worker `iec104_enabled` bayrağını **hiç okumuyor**;
  SCADA'ya verilen CSV/XLSX nokta listesi sahada yayınlananla örtüşmüyor.
- `catalog.py:190` [ORTA — doğrulanmadı] — Herhangi bir cihaz eklendiğinde/pasifleştiğinde
  IEC 104 hedefi baştan kuruluyor ve **tüm SCADA oturumları kopuyor.**

---

### T7 — Senkron dış çağrılar istek yolunun içinde

**`notification_test_service.py:82` — [YÜKSEK, dayanıklılık]**

`smtplib.SMTP(host, port)` ve `SMTP_SSL(...)` çağrılarında **`timeout` parametresi yok** →
`_GLOBAL_DEFAULT_TIMEOUT` (None) → sonsuz blok. Aynı dosyadaki diğer tüm ağ çağrıları
timeout'lu (Telegram 12 sn, FCM 8 sn); eksik olan tek yol SMTP. `notification_hook_service.py:72,77`
de aynı durumda.

Kritik nokta: alarm bildirimleri `notification_inline_dispatch_enabled` bayrağıyla korunuyor
(varsayılan False) ama **arıza bildirimleri bu bayrağın dışında, koşulsuz çalışıyor.** Yani
"dispatch sorumluluğu notification-worker'a alındı" koruması arıza yolunda geçersiz.

> Doğrulayıcı şiddeti KRİTİK'ten YÜKSEK'e indirdi: threadpool tükenmesi olmuyor, ama arıza
> motoru kilidin içinde kalıcı olarak duruyor ve **arıza commit edilmemiş kalıyor.**

**`outbound_telemetry_batcher.py:212` + `:120` — [YÜKSEK ×2]**

Modül docstring'i "5 sn penceresinde biriken değişiklikleri **TEK batch payload** ile yollar"
diyor. Gerçekte cihaz başına **ayrı, seri, bloklayan** POST atıyor (`urlopen(req, timeout=15)`),
hepsi tek arka plan thread'inde. Ayak uydurmak için POST gidiş-dönüşünün **< 16,7 ms** olması
gerekir; 30 ms'de periyot 18 sn'ye, 100 ms'de 60 sn'ye çıkar. Ölü hedefte 600 × 15 sn = **2,5 saat**
tek flush turu.

Üstüne: dedup haritası `_last_sent` gönderimden **önce** güncelleniyor. POST patlasa bile
değer "gönderildi" işaretleniyor ve cihaz aynı değeri tekrarladığı sürece bir daha **asla**
buffer'a girmiyor.

> 4G 3 dakika kopar. `master.permanent_fault` 0 → 1 olur. Değer drain edilir, `_last_sent=1`
> yazılır, POST timeout'a düşer. 4G geri gelir. Cihaz hâlâ 1 yayınlar; `submit()`
> `last_val == new_val` görüp buffer'a **koymaz**. Müşterinin webhook'u arızayı **hiç görmez**
> — ta ki arıza kalkıp değer 0'a dönene kadar. Tam da bildirilmesi gereken olay sessizce yutulur.

**Bağlı bulgular:**
- `notification_dispatch_service.py:214` [YÜKSEK] — Her alarm e-postası için **her alıcıya
  ayrı ayrı** internetten OSM karosu indirilip PNG harita render ediliyor. 4G saha cihazında
  bildirim hattını kilitliyor.
- `notification-worker/main.py:123` [YÜKSEK] — 10 sn HTTP zaman aşımı senkron dispatch bitmeden
  dolduğu için her alarm bildirimi **N kez** gönderiliyor ve kuyruk temizlenmiyor.
- `outbound_dispatch_service.py:90` [ORTA — doğrulanmadı] — Alarm kayıt isteği içinde REST/MQTT
  denemeleri senkron: bir alarm **26 saniyeye kadar** API thread'i tutuyor.
- `bulk_notifications.py:70` [ORTA — doğrulanmadı] — Toplu bildirim istek içinde senkron,
  alıcı başına ayrı SMTP bağlantısı, tarayıcı zaman aşımı → mükerrer gönderim.

---

### T8 — Güvenlik: kimlik, kapsam ve kimlik doğrulama

**`ingest_service.py:135` — [YÜKSEK, güvenlik]** — **Gateway kimliği cihaz kapsamına bağlı değil.**
`validate_gateway_token` yalnızca "bu token bu gateway_code'a mı ait" sorusunu yanıtlıyor;
`_persist_readings` okumaların `device_code` alanını **hiç doğrulamıyor.** NATS tarafında da
tüm filo için tek `gateway` kullanıcısı var ve `e1.telemetry.raw.>` altındaki her subject'e
publish yetkisi tanımlı.

> Tek bir gateway ele geçirilirse (veya bir compose dosyası sızarsa) saldırgan 580 başka
> cihazın kodlarıyla okuma basabilir: sahte arıza üretebilir veya **gerçek arızayı maskeleyebilir**
> (sahte akış her zaman daha yeni `source_timestamp` taşır). Denetim kaydında görünen tek şey
> `gateway_batch_ingested` olur.

**`public_deps.py:78` + `core/client_ip.py:32` — [YÜKSEK, güvenlik]** — **X-Forwarded-For
tamamen istemci kontrolünde.** Host nginx'te `set_real_ip_from`/`real_ip_header` yok; sadece
`$proxy_add_x_forwarded_for` var — bu istemcinin gönderdiği header'ın **sağına** ekler, silmez.
Backend `xff.split(",")[0]` alıyor. Bu IP üç yerde **güvenlik kararı**: API key IP allowlist'i,
slowapi rate limit anahtarı, denetim kaydı IP'si.

> API anahtarı `allowed_ips` ile kilitlenmiş olsa bile tek bir `X-Forwarded-For` başlığı
> ile atlanıyor. Denetim kayıtlarındaki IP de saldırganın yazdığı değer — olay sonrası
> inceleme yanlış IP'yi kovalar.
>
> **Doğrulayıcı daralttı:** nginx katmanındaki limitler sağlam (`login_zone 5r/m`,
> `api_zone 60r/s`) çünkü iç nginx `real_ip_recursive` kullanıyor. Yani sınırsız değil,
> ~720 kat zayıflama.

**`auth.py:85` — [YÜKSEK, dayanıklılık]** — **Kilitlenen hesabın API üzerinden açılma yolu yok.**
Kilit parola doğrulamasından önce kontrol ediliyor ve `failed_login_count` kilit süresi dolunca
sıfırlanmıyor. `POST /users/{id}/reset-password` `locked_until`'a **dokunmuyor**. INSTALLER
hesabına yalnızca INSTALLER müdahale edebiliyor.

> **15 dakikada tek bir hatalı istek** (saatte 4, tüm limitlerin çok altında) tek installer
> hesabını süresiz kilitli tutmaya yeter. Sonrasında gateway ekleme, ağ ayarı, yedek/geri
> yükleme, uzaktan bakım izni — hepsi kilitli. Sahaya fiziksel gitmeden çözülemiyor.

**`ws_live.py:305` — [YÜKSEK, güvenlik]** — **A2'nin zorunlu şifre değişimi kapısı WebSocket'te
uygulanmıyor.** Varsayılan parolayla tüm sahanın canlı telemetrisi okunabiliyor. (Regresyon.)

**`gateways.py:324` — [YÜKSEK, güvenlik]** — Gateway güncelleme `setattr(row, key, value)` ile
tüm alanları yazıyor ama `token` değişince **`token_hash` güncellenmiyor** (create yolunda
`gateways.py:293` `hash_gateway_token` çağrılıyor, update yolunda yok). Sonuç: **yeni token
401 alıyor, ESKİ token çalışmaya devam ediyor.** Token rotasyonu hem bozuk hem de iptal etmiyor.
(Regresyon — doğruladım.)

**`gateway_compose.py:313` — [YÜKSEK, güvenlik]** — Tüm gateway'ler tek NATS parolası paylaşıyor,
compose dosyasına düz metin gömülüyor, TLS varsayılan kapalı ve kök `.env.example`'da
`NATS_TLS_ENABLED` satırı **hiç yok** — operatör özelliğin varlığından haberdar değil.

> **Doğrulayıcı düzeltmesi:** Parola sabit gömülü değil, kurulum başına rastgele üretiliyor
> (`install.sh:401`). Yani patlama yarıçapı bir appliance ile sınırlı — ki hedef ölçekte
> bu zaten 600 cihaz demek.

---

### T9 — Güvenlik: appliance ve host sınırı

**`infra/appliance/setup-appliance.sh:554` — [KRİTİK, güvenlik]**

Appliance kurulumu WiFi erişim noktasını kurarken kablosuz güvenlik ayarını **bilerek siliyor**
(`-802-11-wireless-security.key-mgmt ""`). **WPA/WPA2 yok, ağ tamamen açık.** Profil
`autoconnect yes` + `priority 100` ile işaretli ve e1-netd 30 sn'lik timer ile AP'yi
kalıcı olarak geri getiriyor. `ipv4.method shared` 10.42.0.0/24 DHCP+NAT veriyor ve
docker-compose'daki **tüm** `ports:` girişleri 0.0.0.0'a bind olduğu için AP istemcisine açık:
80, 21, 502, 2404-2406, 4222, 5672, 5020, 5021, 30000-30009.

> Sokaktan telefonla `E1GRID-<müşteri>` ağına **parolasız** bağlanan biri: `http://10.42.0.1/`
> ile giriş ekranına düz HTTP üzerinden ulaşır; `10.42.0.1:2404` ve `:502`'ye bağlanıp
> **kimlik doğrulamasız** tüm sahanın arıza/konum/ölçüm durumunu okur; `:4222` NATS ve
> `:5672` RabbitMQ'yu brute-force için bulur. 600 cihaz = 600 bağımsız, sürekli yayında,
> şifresiz giriş noktası.

**`e1-netd.py:1600` ve `e1-gwd.py:421` — [YÜKSEK ×2, güvenlik]** — **A11/A13 düzeltmesi eksik.**
Commit `63d444d` yalnızca `_write_json`'un **son bileşenini** `O_NOFOLLOW` ile korudu. Yol
üzerindeki **dizin bileşenleri** ve path tabanlı `os.chmod` korumasız kaldı. `ARCHIVE_DIR`
container'ın yazabildiği paylaşılan dizinin içinde ve hiçbir setup betiği onu önceden oluşturmuyor.

> Container'da RCE alan saldırgan: `mv .../archive .../.a && ln -s /usr/bin .../archive`.
> `e1-netd.path` birimi saniyeler içinde root olarak `apply` tetikler, `os.chmod(ARCHIVE_DIR, 0o750)`
> symlink'i **takip eder** ve host'ta `chmod 0750 /usr/bin` uygulanır. Root olmayan hiçbir süreç
> binary çalıştıramaz: kiosk, SSH yönetim hesabı, NetworkManager yardımcıları. **Kendi kendine
> düzelmez, fiziksel müdahale gerekir.** Aynı imaj 600 cihazda olduğu için tek bir backend
> açığı tüm filoyu tek seferde tuğlalayabilir.

**`install.sh:450` [ORTA]** — 600 cihazın hepsi aynı GitHub token'ını ve aynı yeniden
kullanılabilir Tailscale anahtarını düz metin taşıyor; disk şifreleme, cihaza özel kimlik
ve rotasyon yolu yok.

**`infra/host-nginx/enerjione-grid.conf:20` [YÜKSEK — doğrulanmadı]** — Varsayılan dağıtımın
hiçbir yerinde **TLS yok**: giriş parolası ve JWT düz HTTP üzerinden gidiyor, gönderilen
HSTS başlığı etkisiz.

**`docs/DEPLOYMENT.md:58` [ORTA]** — Dokümandaki `ufw` talimatı Docker'ın yayınladığı portları
**kapatmaz** (Docker `DOCKER-USER` zincirini atlar); operatör kapalı sandığı portları açık bırakır.

---

### T10 — Kurtarma yolları kırık

**`app/main.py:820` — [KRİTİK, veri bütünlüğü]**

Doğruladım — kod ve yorumu aynen şu:

```
# strict=True: JSON listesi disindaki tum sinyalleri siler.
result = seed_default_signals(db, strict=True)
```

**Her backend açılışında** sinyal kataloğu fabrika JSON'una geri dönüyor. `_MUTABLE_FIELDS`
listesi tam da operatörün UI'dan değiştirdiği alanları içeriyor: `label, unit, scale, offset,
dnp3_index, iec104_type_id, iec104_ioa, iec104_ioa_offset`. `strict=True` dalı ise seed
dosyasında bulunmayan her satırı **siliyor**. Oysa `POST /signals` installer'a sinyal yaratma,
`PATCH /signals/{key}` ise bu alanları değiştirme izni veriyor ve olay kaydına `signal_updated`
yazılıyor.

> Sistem operatöre "kaydedildi" diyor, denetim kaydı tutuyor, sonra ilk yeniden başlatmada
> sessizce geri alıyor. Devreye alma mühendisi SCADA için 20 sinyalin IOA'sını düzenler ve
> akım trafosu için `scale=0.1` yapar. Gece elektrik kesintisi olur. Sabah SCADA **yanlış
> IOA'dan** okur ve akım değerleri **10 kat yanlış** görünür. Hiçbir hata logu, hiçbir alarm yok.
>
> **Doğrulayıcı ölçtü:** 145 sinyalde IOA fabrika değerine döner, **18 sinyalde NULL'a çekilerek
> IEC104 yayınından tamamen düşer.**

**`apps/frontend-web/nginx.conf:69` — [YÜKSEK ×2, kullanım]**

Doğruladım: iç nginx `client_max_body_size 10m`, backend `_BACKUP_UPLOAD_MAX_BYTES = 2 GiB`,
host nginx `100M`. **Zincirdeki en düşük değer kazanır: 10 MB.** Backend'in streaming/413
mantığı hiç çalışmıyor; istek uvicorn'a ulaşmadan nginx'in HTML 413'üyle reddediliyor ve
frontend JSON beklediği için kullanıcıya anlamsız genel bir hata gösteriliyor.

> Felaket kurtarmanın tek UI adımı çalışmıyor. Ayrıca indirme yönünde `proxy_buffering` açık
> ve container `read_only: true` + `tmpfs /tmp size=64m` altında — nginx'in proxy temp dosyası
> 64 MiB'lik tmpfs'e yazılıyor.

**`app/api/health.py:138` — [YÜKSEK, dayanıklılık]** — NATS'a bağlanılamadığı sürece `/health`
kalıcı 503 dönüyor ve `depends_on: service_healthy` zinciri **arayüz dahil tüm stack'in**
açılmasını engelliyor. Cihaz tamamen kararıyor.

**`backups.py:442` [ORTA]** — Geri yükleme sonrası ne `alembic upgrade` ne süreç yeniden
başlatma var: eski şemalı bir yedek geri yüklendiğinde çalışan backend head şemasını beklemeye
devam eder ve **API topluca 500'e düşer.**

**`backup_service.py:337` [ORTA]** — Geri yükleme **zamanlanmış yedeği sessizce kapatıyor**:
`backup_schedule` tablosu yedeğe dahil değil, restore sonrası `enabled=False` olarak yeniden
yaratılıyor. Yani kurtarma işlemi bir sonraki kurtarmayı devre dışı bırakıyor.

**`packaging/bin/enerjione-grid:126` [YÜKSEK — doğrulanmadı]** — Müşteriye verilen tek elle-yedek
komutu **tüm historian'ı** dump ediyor: hariç tutma, rotasyon veya disk kontrolü yok. (A12'nin
`update.sh` için düzeltilen hâlinin bu yolda tekrarı.)

---

### T11 — Konfigürasyon çelişkileri

| Yer | Çelişki |
|---|---|
| `docker-compose.yml:118` | Belgelenen ölçekleme reçetesi (`E1_API_WORKERS>1` + scale profili) **250 bağlantı** istiyor; Postgres `max_connections` hiçbir yerde yükseltilmemiş, varsayılan **100**. |
| `models/device.py:28` | Varsayılan cihaz poll aralığı **2 saniye** — tüm kapasite sabitleriyle **20 kat** çelişiyor ve sunucu tarafında alt sınır doğrulaması yok. |
| `core/config.py:211` | NATS ham stream tamponu kodun iddiasına göre "~19 saat", gerçekte **~3,5 saat**; normalized stream **~1,3 saat**. |
| `auth_service.py:118` | 600 cihaz için önerilen çoklu-worker kurulumu WebSocket kimlik doğrulamasını **kırıyor** — bilet deposu süreç içi. |
| `DeviceManagementPanel.tsx:524` | Sihirbazdan oluşturulan gateway `initiating_port_count: 0` alıyor → üretilen compose hiç initiating port yayınlamıyor, o cihazlar **sessizce hiç bağlanamıyor**. (doğrulanmadı) |
| `gateways.py:858` | Initiating modundaki cihazların Master IP Port'u her `/config` çağrısında **sıra numarasından** yeniden hesaplanıyor — bir cihaz silinince sonraki tüm cihazların portu kayıyor. (doğrulanmadı) |
| `telemetry-contract.json:19` | Sözleşme gerçek payload'dan **5 alan geride** ve hiçbir yerde doğrulanmıyor. (doğrulanmadı) |

---

## 3. Önceki denetimden hâlâ açık olanlar

Regresyon ekseninin bulguları — A1–A13 düzeltmeleri büyük ölçüde **tuttu**, ancak:

| Madde | Durum |
|---|---|
| **A2** (zorunlu şifre değişimi) | HTTP'de kapalı, **WebSocket'te açık** (`ws_live.py:305`). Ayrıca admin şifre sıfırlaması `must_change_password` **set etmiyor** (`users.py:351`). |
| **A3** (toplu işlem kapsamı) | Mutasyon yollarına uygulandı, **liste yoluna uygulanmadı** (`alarms.py:44`). `/telemetry/latest` (B5) ve `/public/*` (B3) hâlâ kapsamsız. |
| **A7** (IEC104 sertleştirme) | **Yanlış kopyaya uygulandı** — SCADA'nın bağlandığı servis hâlâ korumasız (`server.py:237`). |
| **A8** (update.sh geri alma) | Yalnızca `.env`'i kapsıyor; **git checkout geri alınmıyor** → cihaz yine açılmayan compose ile boot ediyor (`update.sh:122`). |
| **A11/A13** (symlink) | Yalnızca son yol bileşeni korundu; **dizin bileşenleri ve `os.chmod` korumasız** (`e1-netd.py:1600`, `e1-gwd.py:421`). |
| **A12** (yedek rotasyonu) | `update.sh` düzeltildi, aynı hata `packaging/bin/enerjione-grid:126`'da tekrarlanıyor. |
| **B16/B20** (disk %95 koruması) | Hâlâ etkisiz (`backup_scheduler.py:87`). |
| **B17** (ağ ayarı denetim kaydı) | Hâlâ commit edilmiyor (`network.py:137`). |
| **B19** (notification DLQ) | DLX'e bağlı kuyruk hâlâ **yok** (`notification-worker/main.py:189`). |

### Çürütülenler (tekrar açmayın)

1. ~~`uninstall.sh`'in uzaktan bakım kapatma adımı appliance dışı kurulumlarda hiç çalışmıyor~~ —
   Zincir doğru okundu ama `e1_appliance_installed()` varsayımı yanlış; A9 düzeltmesi geçerli.
2. ~~A9 temizliği WiFi'siz kurulumlarda çalışmıyor~~ — Aynı sebeple çürütüldü.
3. ~~A3 kapsam düzeltmesi tamamen yetersiz~~ — Kısmen çürütüldü: mutasyon yolları doğru,
   yalnızca liste yolu açık (yukarıda ayrı bulgu olarak duruyor).

---

## 4. Önerilen sıra

Bağımlılıklara ve "tek değişiklikle kaç bulgu kapanır" oranına göre:

**Faz 1 — sahaya çıkışı açan minimum (sırayla):**

1. **`telemetry_latest` tablosu + dar anasayfa ucu.** T1'in tamamını (6 bulgu) kapatır.
   En büyük tek kazanç.
2. **`alarms.py:44`'e kapsamı geçir + açık alarmları LIMIT'ten muaf tut.** T2 (2 bulgu) —
   tek satırlık değişiklik, en yüksek risk/çaba oranı.
3. **`main.py:820` → `strict=False` + kullanıcı düzenlemelerini koru.** Sessiz veri kaybını durdurur.
4. **`schemas/telemetry.py`'ye `max_length` + `except (IntegrityError, DataError)`.** Hattın
   tek mesajla durmasını engeller.
5. **IEC 104: GI'yi okuma döngüsünden ayır + cihaz başına CA ata.** SCADA çıkışını çalışır hale getirir.
6. **`setup-appliance.sh`'e WPA2 + AP arayüzünde port filtresi.** Tek KRİTİK güvenlik açığı.
7. **`nginx.conf:69` → `client_max_body_size` backend ile hizala.** Kurtarma yolunu açar.
8. **SMTP çağrılarına `timeout` + arıza dispatch'ini bayrağa bağla.**

**Faz 2 — 600 cihazda ayakta kalmak için:**

9. Historian/CAGG retention'ını `historian_service`'e denetlet; `update.sh`'in ensure bloğunu
   politikalarla tamamla.
10. `chunk_time_interval`'ı yeniden boyutlandır; Postgres bellek tavanını gözden geçir.
11. `outbound_telemetry_batcher`'ı gerçek batch + devre kesici hâline getir; `_last_sent`'i
    gönderim sonrasına al.
12. Symlink sertleştirmesini dizin bileşenlerine ve `os.chmod`'a genişlet.
13. XFF zincirini düzelt (`proxy_set_header X-Forwarded-For $remote_addr`).
14. `gateways.py` token rotasyonunda `token_hash`'i güncelle.
15. Hesap kilidi için `unlock` ucu / `reset-password` içinde kilit temizliği.
16. Kapasite sabitlerini 103,68M satır/gün varsayımına göre yeniden hesapla;
    `max_connections`'ı ölçekleme reçetesiyle hizala.

**Faz 3 — kalıcı:**

17. "Yeşil yalan" sınıfını sistematik kapat: `loading`/`error` state'lerini gerçek değerlere
    bağla, `value == null` ve `quality` durumlarını nötr rozete çevir, `WsStatusBadge`'i veri
    tazeliğine bağla.
18. Test kapsamı: en az 35 router için `TestClient` duman testi + worker'lar için tüketici
    testi. Bu raporun bulgularının hiçbiri mevcut testlerle yakalanmıyordu.
19. `CLAUDE.md` sürümünü güncelle; `chart.js`/`echarts` ikiliğini tekleştir (868 kB chunk).

---

## Ek — bulgu envanteri

- **Doğrulanmış (adversaryal doğrulayıcıdan geçmiş): 63** — 5 KRİTİK, 35 YÜKSEK, 22 ORTA, 1 DÜŞÜK.
  Bunların **32'si** `blocks_production=true`.
- **Eleştirmen eki (doğrulanmadı): 15** — 2 KRİTİK, 10 YÜKSEK, 3 ORTA.
- **İkincil (doğrulanmadı): 48** — finder'ların ürettiği, doğrulama eşiğinin altında kalanlar.
- **Çürütülen: 3.**

Doğrulanmamış maddeler **kabul edilmiş bulgu değildir**; doğru da olabilir, yanlış da.
Faz 1/2'ye alınmadan önce tek tek okunmalı.

Ham çıktı (her ajanın tam dönüş değeri):
`.claude/projects/.../subagents/workflows/wf_ccd94fb3-aff/journal.jsonl`
