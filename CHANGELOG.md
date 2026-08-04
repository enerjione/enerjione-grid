# Değişiklik Günlüğü

Bu dosya **yayınlanan sürümleri** özetler. Biçim [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/)
esaslıdır; sürümleme [SemVer](https://semver.org/lang/tr/).

Kayıt tutma kuralı: her `v*` tag'inden önce `[Yayınlanmamış]` başlığı altındaki
maddeler yeni sürüm başlığına taşınır. GitHub Release notları commit listesini
zaten otomatik üretir — buraya **kullanıcıyı etkileyen** değişiklikler yazılır,
her commit değil.

Türler: `Eklendi`, `Değişti`, `Düzeltildi`, `Kaldırıldı`, `Güvenlik`.

## [Yayınlanmamış]

---

## [2.45.4] — 2026-08-04

### Düzeltildi — alarm değerlendirme hattı artık backend'i beklemiyor

- **Alarm prio kuyruğu birikiyordu** (401 cihaz testinde ~1.166 mesaj/sn):
  backend'e giden senkron HTTP çağrıları (alarm kaldırma + temizleme)
  mesaj işleme döngüsünün içinde bloklayarak koşuyordu. Gönderim ayrı bir
  thread'e ve sınırlı kuyruğa taşındı; kural değerlendirmesi artık yalnızca
  bellek içi çalışıyor. ("Önce backend POST → alarm_id → RabbitMQ" sırası
  korunur; sağlık ucunda `notify_bekleyen` alanıyla izlenir.)
- **Drift temizlik seli**: alarm hiç aktif olmamış (kural × cihaz)
  anahtarları için 60 sn'de bir atılan idempotent clear POST'ları Postgres'i
  no-op sorgularla boğuyordu. Aralık 600 sn'ye çıkarıldı ve
  `ALARM_DRIFT_CLEAR_INTERVAL_SEC` ile ayarlanabilir; gerçek alarm geçişleri
  bu aralıktan bağımsız anında gönderilir.

---

## [2.45.3] — 2026-08-04

### Düzeltildi — haberleşme durumu telemetri kuyruğundan bağımsızlaştı

- **Sağlık kanalından sayı-bazlı güvenli çıkarım**: gateway "tüm cihazlar
  koptu" diyorsa (devices_online=0) cihazlar en geç bir tarama periyodunda
  OFFLINE'a çekilir — telemetri kuyruğu tıkalı/purge edilmiş olsa bile.
  (Sahada iki kez yaşandı: comm_lost olayları kuyrukla birlikte kaybolunca
  cihazlar ONLINE takılı kalıyordu.)
- **Filo alarmı hiç çalışmamıştı**: var olmayan User.is_active kolonuna
  bakıp her turda hata fırlatıyordu; düzeltildi.

---

## [2.45.2] — 2026-08-04

### Düzeltildi

- **Boru hattı panelinde ham kuyruk "—" gösteriyordu**: aşama görünümü
  eski durable adını arıyordu; queue-group'lu yeni ad (…-q1) da okunur,
  geçiş anında ikisi toplanır.

### Eklendi

- tag-engine ayar düğmeleri env'den: TAG_PUBLISH_PARALLEL ve
  TAG_MAX_ACK_PENDING (büyük replay'lerde hız ayarı).

---

## [2.45.1] — 2026-08-04

### Düzeltildi

- **2.45.0'da tag-engine replikaları hiç başlayamıyordu** (nats-py kuralı:
  queue aboneliğinde queue adı durable adıyla aynı olmalı; farklı
  verildiğinde kütüphane consumer'ı yaratmadan hata fırlatıyor ve iki
  replika da döngüye giriyordu — normalize akışı durdu). Ayrıca yeni
  durable artık stream'de birikmiş ne varsa işler (DeliverPolicy.ALL):
  kesinti sırasında biriken ham ölçümler atlanmaz, güncellemeden sonra
  otomatik yeniden işlenir.

---

## [2.45.0] — 2026-08-04

### Değişti — 400-500 cihaz ölçek paketi

- **Kalıcılaştırma artık çok süreçli**: telemetri tüketicisi leader
  kilidinden ayrıldı, worker container'ı varsayılan 4 süreçle çalışır
  (E1_WORKER_PROCESSES) — persist kapasitesi süreç sayısıyla çarpılır.
- **tag-engine yatay ölçeklenir**: queue-group'lu durable'a kayıpsız geçiş
  + ikinci replika (tag-engine-b). İki kopya mesajları bölüşür.
- **Kaynak bütçesi yeniden dağıtıldı** (ölçüme göre): NATS 4 CPU/3G,
  backend 4 CPU, alarm 2 CPU, Postgres 6G→4G (ayarları birlikte indirildi).
- **Arşivde birebir tekrar bastırma**: ikili/sayaç sinyallerde değer VE
  kalite aynı olan tekrarlar yazılmaz; her değişim ve her kalite geçişi
  aynen arşivlenir (kurallar korunur).

### Kaldırıldı

- alarm-service'in artık işlevsiz legacy hattı (4-token eski konu); durable
  startup'ta silinir — 7,9M'lık hayalet birikim stream diskini baskılıyordu.

---

## [2.44.2] — 2026-08-04

### Düzeltildi

- **Ağ sayfası görsel düzeltmeleri**: WiFi ayarları penceresindeki çift
  kapatma düğmesi kaldırıldı (tek desen: sağ üstte X); tüm modallarda
  başlık/aksiyon hizası sabitlendi (aksiyonlar sağda). Kablolu ağ kartına
  profesyonel alt bölüm (ayırıcı + uyarı notu + sağda buton; kart yandaki
  WiFi kartıyla aynı yükseklikte biter). WiFi listesinde bağlı ağ her
  zaman en üstte, kalanlar sinyal gücüne göre.

---

## [2.44.1] — 2026-08-04

### Düzeltildi

- **`gateway_health` tablosunun migration'ı hiç üretilmemişti**: gateway
  sağlık başlığı gönderince `/pending` (SCADA komut kanalı) 500 veriyor,
  gateway başlığı 10 dakika bırakıyordu — gateway sağlığı ve cihaz-link
  durumu panele hiç ulaşmıyordu. Migration eklendi (0039).
- **Sağlık yazımı artık kendi transaction'ında**: yazım hatası komut
  kanalının transaction'ını zehirleyemez — asıl kusur buydu; tablo olsa
  bile herhangi bir DB hatası aynı şekilde 500 üretirdi.

---

## [2.44.0] — 2026-08-04

### Eklendi

- **Sistem Durumu'nda aşama-aşama boru hattı görünümü**: ham kuyruk
  (normalize bekleyen) → işlenmiş kuyruk (öncelikli/toplu ayrı) → arşiv;
  oklarda tag-engine ve kalıcılaştırma hızları. Tek "bekleyen" sayısı,
  üst kuyruk alt kuyruğa boşalırken "kuyruk kendi kendine artıyor"
  yanılgısı yaratıyordu. Veri NATS monitor'den (NATS_MONITOR_URL,
  fail-soft: ulaşılamazsa panel eski görünüme düşer).

### Düzeltildi

- **tag-engine artık sinyal kataloğunu sınırlı süre bekleyip öyle başlıyor**
  (KATALOG_BEKLE_SEC, varsayılan 20 sn): katalog yüklü değilken büyük bir
  birikim boşaltılırsa tüm analog sel "bilinmeyen → öncelikli" kuralıyla
  öncelikli hatta yığılıyordu (sahada 3M boşaltmanın 1,58M'i).

---

## [2.43.2] — 2026-08-04

### Düzeltildi

- **tag-engine ~1.000 msj/sn'de tıkanıyordu** (300 cihaz testinde görüldü):
  her mesajda yayın onayı sırayla bekleniyordu. Yayın artık sınırlı
  eşzamanlılıkla paralel (TAG_PUBLISH_PARALLEL, varsayılan 512); teslim
  güvencesi (at-least-once) ve DLQ davranışı değişmedi.

---

## [2.43.1] — 2026-08-04

### Değişti

- Arşiv yönetimi penceresi yeniden tasarlandı: büyük arşiv sayısı +
  yazma yükü ölçüm çubuğu, etiketli bölümler (görünüm filtresi / toplu
  işlem), birleşik ölü bant girdi grubu ve açıklayıcı alt başlık.

---

## [2.43.0] — 2026-08-04

### Değişti

- **Gateway güç işlemleri popup menüye taşındı**: başlat/durdur/yeniden
  başlat düğmeleri satırda değil, tek "güç" düğmesinin açtığı menüde
  (kazara tıklamaya uzak; yalnızca installer görür).
- **Sinyaller sayfasında arşiv yönetimi popup'a taşındı**: özet, filtreler
  ve toplu işlemler "Arşiv yönetimi" düğmesinin açtığı pencerede; sayfada
  kompakt özet kalır.

---

## [2.42.1] — 2026-08-04

### Düzeltildi

- **2.42.0'da kalıcılaştırma işçisi ilk dolu partide çöküyordu** (yarım
  kalmış eski kod bloğu tanımsız isim kullanıyordu; canlı ekran akmaya devam
  ettiği için sorun panelde "Akış yok" uyarısıyla görünüyordu, veri NATS'ta
  birikip bekliyordu — kayıp yok). COPY toplu yazım entegrasyonu tamamlandı;
  arşiv/canlı/dedup satırları artık gerçekten tek geçişte yazılıyor.

---

## [2.42.0] — 2026-08-04

### Değişti

- **Arşiv yazımı toplulaştı (COPY)**: kalıcılaştırma ölçüm başına ayrı
  gidiş-dönüş yerine partiyi tek geçişte dört tabloya yazar (COPY +
  tek-ifade upsert). Bozuk satır partiyi düşürmez; ikiye bölünerek yalnızca
  gerçekten bozuk satır karantinaya alınır.
- **Dijital/analog hat ayrımı**: arıza/durum sinyalleri ve kalite geçişleri
  öncelikli hattan işlenir — analog ölçüm seli durum değişimlerini
  geciktirmez. Arıza/ikili sinyaller her değişimde arşivlenir; ölü bant
  yalnızca analog tipte uygulanır.

### Eklendi

- **Sinyaller sayfasında arşiv/ölü bant yönetimi**: hangi sinyalin
  arşivleneceği ve ölü bant eşiği panelden yönetilir.
- **Gateway başlat/durdur/yeniden başlat** (panelden, onaylı): durdurma
  onayı sonucu açıkça söyler (veri akışı duracak) ve olay kaydına yazılır.

### Düzeltildi

- Arka plan lider kilidinin bağlantısı süresiz "idle in transaction"
  bekliyordu; bu, tüm veritabanının VACUUM ufkunu sabitleyip yüksek devirli
  tabloları (canlı değerler, dedup defteri) şişiriyordu. Kilit artık
  transaction açık bırakmadan tutulur.

---

## [2.41.0] — 2026-08-04

### Değişti

- **Kalıcılaştırma backend API'den ayrıldı**: backend-api artık telemetri
  tüketmiyor; kalıcılaştırma ayrı worker sürecinde ve tag-engine çıkışından
  (NORMALIZED) besleniyor. Arşivdeki değer ile alarm/IEC104/Modbus'un gördüğü
  değer aynı normalizasyondan geçer; API süreci telemetri yükünden etkilenmez.
- **Gateway şablonları tek gateway'de 500 cihaza göre güncellendi**
  (`MAX_PARALLEL_DEVICES=500`; gateway imajı 1.2.0 ile birlikte). Panel
  "Güncelle" akışı mevcut kurulumların compose'unu yeniden üretince yeni
  değer sahaya iner.

---

## [2.40.0] — 2026-08-04

### Değişti

- **Gateway telemetrisi için NATS-direkt rota artık standart.** Paneldeki
  "Güncelle" düğmesi gateway compose'unu güncel NATS adresiyle yeniden üretir;
  NATS öncesi kurulan (veya anonim NATS adresli) gateway'ler HTTP yedek
  yolundan çıkıp telemetriyi doğrudan JetStream'e basar. Kurulumda seçilen
  imaj/port/adres değerleri korunur; imaj çekilemezse çalışan kuruluma
  dokunulmaz.

### Eklendi

- Telemetriyi HTTP yedek yolundan basmaya devam eden gateway için 10 dakikada
  bir uyarı loglanır — standart dışı çalışma (ve backend'e binen gereksiz yük)
  görünmez kalmaz.

---

## [2.39.0] — 2026-08-04

### Eklendi

- **Toast bildirimleri artık ayarlanabilir** (Proje Ayarları, kurulum geneli):
  konum seçimi ve kendiliğinden gelen bildirimleri susturma. Kullanıcının kendi
  işleminin sonucu (kaydedildi, hata, yetki, oturum) susturma açıkken de
  görünür — o mesajların başka kanalı yok.
- **Outbox dead-letter**: tekrar tekrar başarısız olan kayıt işaretlenip
  sıradan çıkıyor. Önceden tek bir "zehirli" kayıt tüm kuyruğu kilitliyordu.
- Arka plan işleri (telemetri tüketicisi, outbox yayıncısı) API sürecinden
  ayrıldı; API artık güvenle çoğaltılabilir.

### Düzeltildi

- **Veri kaybı: ölü bant, kalite ve arıza bayrağı geçişlerini yutuyordu.**
  Değer ölü bandın içinde kalırken `good → invalid / comm_lost / forced`
  geçişleri arşive hiç girmiyordu; ham kopyanın penceresi 30 dakika olduğu
  için kayıp kalıcı hale geliyordu. Ayrıca ölü bant ikili sinyallere de
  uygulanabiliyordu. Ölü bant artık yalnızca analog ölçümlerde ve karşılaştırma
  değer + kalite ikilisi üzerinden. Normal akışta ek yazım maliyeti sıfır.
- **Outbox temizliği üretimin gerisinde kalıyordu** (silme 1.000/sn, üretim
  1.074/sn) — tablo hiçbir zaman kararlı duruma gelmiyordu. Yeni kapasite
  üretimin ~15 katı.
- Sayfa kaydırma çubuğu: konumlanmamış ata yüzünden ekran okuyucu etiketleri
  sütun kırpmasından kaçıp belge yüksekliğini büyütüyordu.
- Hat segment kartı: "Cihaz Ekle" listenin üstüne alındı, liste kendi içinde
  kayıyor, kart açıldığı noktaya göre sınırlanıyor.

### Bilinen durum

- Telemetri boru hattındaki birikmenin kök nedeni **henüz bulunamadı**. Backend
  saniyede ~2.145 mesaj işliyor; sorun kendisine gelen mesaj sayısının beklenenin
  çok üzerinde olması. İnceleme sürüyor.

## [2.38.13] — 2026-08-03

### Düzeltildi

- **Arayüzün gövde yazı tipi kuralı hiç uygulanmıyordu — asıl sebep bulundu.**
  `styles.css` bir BOM (görünmez U+FEFF karakteri) ile başlıyordu ve `:root`
  dosyanın ilk kuralıydı; BOM dosya başındayken zararsızdır. Sonradan dosyanın
  üstüne CSS eklenince bu karakter dosyanın ortasına, `:root`un hemen önüne
  düştü. Satır ortasındaki U+FEFF artık BOM sayılmaz; selektöre yapışıp kuralı
  hiçbir elemana uymayan bir tip selektörüne çevirir. Sonuç: gövde yazı tipi,
  metin rengi ve arka plan rengi birlikte düşüyor ve tarayıcı varsayılanına
  (Chrome/Windows'ta Times New Roman — serif) geçiliyordu. Üst sekme çubuğu
  dahil tüm metinlerin yazı tipi bu yüzden değişmişti.
- Bu hata sınıfı için davranış testi eklendi: `styles.css` projenin kendi
  paketleyicisiyle derlenip `:root` kuralının çıktıda gerçekten canlı kaldığı
  doğrulanıyor. Kaynakta desen aramıyor — bu arıza tam olarak "kaynak doğru
  görünüyor ama tarayıcıda ölü" biçiminde ortaya çıktı.

## [2.38.12] — 2026-08-03

### Değişti

- **Ana sayfadaki cihaz listesi artık sayfa başına 20 cihaz gösteriyor** (önceki
  varsayılan 50). Sayfa boyutu seçenekleri: 20 / 50 / 100 / 200.
- **Sayfalama kontrolü yenilendi.** Düğmeler ayrı kutular yerine bitişik tek bir
  grup halinde; aktif sayfa marka rengiyle (turuncu) işaretleniyor — önceki mor
  vurgu arayüzün geri kalanıyla uyumsuzdu. Sayfa boyutu seçici de özel ok
  simgesiyle sadeleştirildi. Sayılar tablo rakamlarıyla dizildiği için sayfa
  değiştikçe genişlik oynamıyor.

## [2.38.11] — 2026-08-03

### Değişti

- **Sistem Durumu KPI kartlarının tipografisi v2.25.0 değerlerine döndürüldü**
  (etiket 11px/0.06em, değer 1.6rem/-0.02em, kesir 1.05rem). Arayüzün tamamı
  artık v2.25.0 ile aynı yazı tipi ölçülerine sahip.

## [2.38.10] — 2026-08-03

### Değişti

- **Arayüz tipografisi v2.30.0 ile birebir aynı hale getirildi.** Eski sürüm
  incelendi: o sürümde gövde için tek bir tanım vardı (`Arial, sans-serif`),
  gömülü bir yazı tipi ya da dış font bağlantısı yoktu. `:root` bloğu artık
  v2.30.0 ile karakter karakter aynı; Manrope paketten çıkarıldı.

## [2.38.9] — 2026-08-03

### Eklendi

- **Kiosk açılış ekranı artık dinamik.** Müşteri logosu (varsa) ve müşteri adı
  ortada, EnerjiOne Grid kimliği sol altta (giriş ekranıyla aynı dil), müşteri
  ve sürüm bilgisi sağ altta gösteriliyor. Değerler her açılışta çözülür;
  dosyaya gömülmediği için sürüm/port değişince bayatlamaz.
- Müşteri logosu uygulama ayağa kalktıktan sonra arka planda diske
  önbelleklenir; ilk açılışta henüz yoktur, sonraki her açılışta görünür.
  (Logo veritabanında tutulduğu için açılış ekranı anında erişemez.)

### Değişti

- "İlk kurulum uzun sürebilir" ara mesajı kaldırıldı.

## [2.38.8] — 2026-08-03

### Değişti

- **Arayüz yazı tipi 2.30'daki haline döndürüldü.** Gövde fontu yeniden Arial
  (Ubuntu'da Liberation Sans) tabanlı.
- **Kiosk açılış ekranı** artık açık renkli E1 logosunu kullanıyor ve metinler
  düzgün Türkçe karakterlerle yazılıyor ("Sistem başlatılıyor…").

### Düzeltildi

- **Aynı sayfada karışık yazı tipi.** Yedek zincirinde `Helvetica Neue` vardı;
  bu ad Ubuntu'da karşılıksızdır ve URW/Nimbus paketleri kurulu değilse
  fontconfig onu serif bir yüze eşleştirebiliyor. Belirtisi, bazı başlıkların
  serif, gövdenin sans görünmesiydi. Zincirde artık yalnızca hedef sistemde
  karşılığı olan adlar var.

## [2.38.7] — 2026-08-03

### Düzeltildi

- **Temiz kurulumda arşiv tablosu hypertable'a çevrilmiyor, saklama süresi
  politikası kurulmuyordu.** 2.38.4'te temiz kurulum şemayı modellerden tek
  adımda kuracak şekilde değiştirilmişti; bu, kurulumu çökerten sorunu çözdü
  ancak `create_all` yalnızca düz tabloları oluşturur. Hypertable'a çevirme,
  90 günlük saklama, sıkıştırma ve özet katmanları yalnızca migration
  gövdesinde yaşadığı için sessizce atlanıyordu — Sistem Durumu sayfasındaki
  "tablo sınırsız büyüyor" uyarısı bunun belirtisiydi. Depolama kurulumu artık
  şemadan ayrı, idempotent bir adım olarak **her açılışta** çalışıyor; eksik
  olanı tamamlıyor, kurulu olana dokunmuyor. Mevcut kurulumlarda da kendini
  onarır; elle müdahale gerekmez.

## [2.38.6] — 2026-08-03

### Düzeltildi

- **Kiosk açılış ekranından uygulamaya geçilmiyordu.** Açılış ekranı uygulamayı
  yoklayıp hazır olunca kendiliğinden yönleniyor; ancak yoklanan adres
  `http://localhost/` olarak sabitti. Arayüzün yayınlandığı port `.env`
  içindeki `FRONTEND_HTTP_PORT` ile değişebiliyor (host'un 80 portu
  host-nginx'teyse kurulum bunu 8080 yapar) ve o durumda yoklama hiçbir zaman
  başarılı olmuyor, operatör açılış ekranında süresiz bekliyordu. Adres artık
  her oturumda yapılandırmadan okunuyor; kurulumda adres açıkça verildiyse ona
  dokunulmuyor.

## [2.38.5] — 2026-08-03

### Düzeltildi

- **Arayüz yazı tipi sistem fontuna düşüyordu.** Manrope pakete gömülüydü ve
  dosyalar doğru yayınlanıyordu, ancak `@font-face` kuralında standart dışı bir
  format değeri (`woff2-variations`) kullanılmıştı. Tarayıcı tanımadığı formatta
  kaynağı atlar, font hiç indirilmez ve sessizce yedek yazı tipine düşülür —
  konsolda hata, ağ sekmesinde başarısız istek görünmez. Arayüz artık her
  sayfada Manrope ile açılıyor.
- **Kiosk açılış ekranı "File not found" veriyordu.** Geçiş ekranı
  `/usr/local/share` altında tutuluyordu; Ubuntu'da Firefox bir snap paketi
  olduğu için sandbox bu dizini göremiyor. Dosya diskte duruyor olmasına rağmen
  operatör ekranında tarayıcı hata sayfası çıkıyordu. Açılış ekranı artık
  oturum başında kullanıcının ev dizinine kopyalanıp oradan açılıyor; snap
  olmayan tarayıcılarda eski konum yedek olarak korunuyor.

## [2.38.4] — 2026-08-03

### Düzeltildi
- **Temiz kurulum tamamlanamıyordu — asıl sebep bulundu.** Backend, boş bir
  veritabanında şemayı güncel hâliyle bir kerede kuruyor; ancak ardından
  geçmiş şema adımlarını da baştan uygulamaya çalışıyordu. Şema zaten
  eksiksiz olduğu için ilk alan ekleyen adım çakışıp hata veriyor, backend
  açılamıyor ve kurulum *"backend-api is unhealthy"* diyerek duruyordu.

  2.38.3'te bu adımlardan biri düzeltilmişti; ancak aynı riski taşıyan
  sekiz adım daha vardı, yani sorun bir sonraki adımda tekrarlayacaktı.
  Bu sürümde kaynak düzeltildi: boş veritabanında geçmiş adımlar artık
  hiç tekrarlanmıyor.

  Mevcut kurulumlar etkilenmez; onlarda şema adımları eskisi gibi
  sırayla uygulanmaya devam eder.

---

## [2.38.3] — 2026-08-03

### Düzeltildi
- **Önceki bir kurulum denemesinden veri kalmış cihazlarda kurulum
  tamamlanamıyordu.** Backend açılışta veritabanı şemasını güncelliyor,
  ardından geçmiş şema adımlarını sırayla uyguluyor. Bir adım, zaten var
  olan bir alanı yeniden eklemeye çalışıp hata veriyor; backend açılamıyor
  ve kurulum *"backend-api is unhealthy"* diyerek duruyordu.

  Cihaz kalıcı olarak kilitleniyordu: her yeniden deneme aynı noktada
  patlıyordu. Temiz veritabanında görülmediği için "bir sunucuda oluyor,
  diğerinde olmuyor" şeklinde ortaya çıkıyordu.

  İlgili adım artık alanın zaten var olduğunu görünce atlıyor.

---

## [2.38.2] — 2026-08-03

### Düzeltildi
- **Sürüm yayınlama akışı tamamlandı.** 2.38.1'deki düzeltme yetersizdi:
  kesme işlemi başka bir komuta taşınmıştı ama liste yine erken
  kapatılıyordu, bu kez paketi okuyan araç hata veriyordu. Artık liste
  sonuna kadar okunuyor, yalnızca gösterim sınırlanıyor.

  Servis imajları 2.38.0'dan beri zaten doğru yayınlanıyordu; eksik olan
  kurulum paketi ve sürüm kaydıydı.

---

## [2.38.1] — 2026-08-03

### Düzeltildi
- **Sürüm yayınlama akışı tamamlanamıyordu.** 2.38.0'da servis imajlarının
  tümü başarıyla yayınlandı, ancak kurulum paketini üreten adım hata verdiği
  için sürüm kaydı oluşmadı.

  Sebep, paket içeriğini özetleyen bir satırdı: liste ilk 25 kalemden sonra
  kesiliyor, kesilen tarafta kalan komut yazamayıp hata döndürüyor ve bu
  tüm adımı düşürüyordu. Hata **zamanlamaya bağlı** olduğu için bazen
  görünüyor bazen görünmüyordu; bu yüzden geliştirme makinesinde tekrar
  edilemiyordu.

  Aynı tuzağın bulunduğu iki yer daha düzeltildi: kaldırma betiğinin yardım
  ekranı ve kurulum sırasında geçersiz sürüm adı girildiğinde gösterilen
  sürüm listesi (ikincisi, hata anında ikinci bir hata üretiyordu).

---

## [2.38.0] — 2026-08-03

### Eklendi
- **Kurulum yarıda kalırsa yaptıklarını geri alıyor.** Önceden bir adımda
  düşünce cihazda yarım bir kurulum kalıyordu: container'lar ayakta, ayar
  dosyası üretilmiş, ama sistem çalışmıyor. Tekrar denendiğinde hangi
  parçanın eski hangisinin yeni olduğu belli olmuyordu.

  > **Mevcut veriler korunur.** Var olan bir kurulumun üzerine yapılan
  > denemede telemetri, olay kayıtları ve yedekler **silinmez**; yalnızca o
  > koşumun oluşturdukları geri alınır.

- **Kurulum hata verdiğinde sebebi ekranda görünüyor.** Önceden yalnızca
  "logları inceleyin" deniyor ve komut veriliyordu; artık sorunlu servisin
  son satırları doğrudan basılıyor.

### Değişti
- **Arayüz yazı tipi (Manrope) uygulamaya gömüldü.** Önceden internetten
  indiriliyor sanılıyordu; aslında hiç yüklenmiyor ve sistem yazı tipine
  düşülüyordu. Artık cihazın internet erişimi olmasa da — erişim noktası
  modunda doğrudan cihaza bağlanıldığında da — arayüz doğru görünür.

- **Ağ Ayarları ve Uzaktan Bakım sayfalarında bildirimler artık ekranın
  köşesinde beliriyor.** Önceden sayfanın ortasına satır olarak ekleniyor,
  her göründüğünde alttaki kartlar aşağı kayıyordu.

  Kalıcı durumlar (örneğin cihaza ulaşılamaması) kaybolmuyor: üst şeritte
  görünmeye devam ediyor, çünkü geçici bir bildirim kaybolduktan sonra
  sayfa "her şey yolunda" gibi görünürdü.

- **Uzaktan Bakım sayfası sadeleştirildi.** İşlem sürerken aynı bilgiyi iki
  yerde birden yazan mükerrer satır kaldırıldı; süre seçimi ve durum
  gösterimi yenilendi.

### Düzeltildi
- **WiFi kartı, sistemde ne varsa ona göre algılanıyor.** Bazı cihazlarda
  kart takılı olduğu hâlde "WiFi kartı yok" deniyor ve erişim noktası /
  ağa bağlanma seçimi kilitli kalıyordu. Özellikle USB WiFi adaptörlerinde
  görülüyordu.

  Kart bulunup da ağ yöneticisi tarafından tanınmadığı durum artık ayrıca
  belirtiliyor ve ne yapılacağı yazıyor — "kart yok" demek yerine.

- Gateway ayarlarında DNP3 kalite bayrağı seçeneğinin kutusu başlığın
  üstünde tek başına duruyordu; artık başlığın solunda.

---

## [2.37.0] — 2026-08-03

### Güvenlik
- **Uzaktan erişim izni yokken cihaz artık uzaktan erişim ağına bağlı
  kalmıyor.** Önceden cihaz ağda duruyor, yalnızca gelen bağlantıları
  reddediyordu. Teknik olarak güvenliydi ama erişimi engelleyen tek şey
  yazılımın kendi kararıydı; müşteri "girilmiyor" sözüne güvenmek zorundaydı.

  Artık izin verilmediği sürece cihaz **ağdan çıkıyor**. Müşteri bunu kendi
  güvenlik duvarında "hiç trafik yok" diye doğrulayabilir.

  İzin verildiğinde cihaz ağa yeniden bağlanır, ağdaki diğer cihazlardan
  erişilebilir olur ve (seçilmişse) SSH açılır. Süre dolduğunda bağlantı
  düşer ve açık oturumlar kopar. Cihazın ağ kaydı hiçbir zaman silinmez;
  izin verilince sahaya gitmeden geri gelir.

  > Bunun bir bedeli var: erişim kapalıyken cihaz konsolda çevrimdışı görünür
  > ve "elektrik yok", "internet yok", "cihaz arızalı", "izin verilmemiş"
  > birbirinden ayırt edilemez. Canlılık bilgisinin şart olduğu kurulumlar
  > eski davranışa dönebilir.

- **İzin verildiği hâlde bağlanılamadığında ekran artık bunu söylüyor.**
  Cihaz izni alıp da tünele bağlanamazsa (en sık sebebi internet erişiminin
  olmaması) sayfa nedeni açıklıyor. Önceden bu durum sessizce geçiliyordu:
  "İzin ver"e basılıyor, hiçbir şey olmuyordu.

### Değişti
- **Ağ Ayarları sayfasındaki WiFi bölümü sadeleşti.** Kart aç/kapat, kartın
  görevi ve ölçülen durum ayrı bir **WiFi ayarları** penceresine taşındı.
  Panelde tek satırlık bir özet kaldı: WiFi kartı, kartın görevi ve **bağlı
  ağ** (ad + sinyal). Ağ listesi artık ekranın dışına düşmüyor.

  Bağlı ağ ölçüme dayanır: kayıtlı ama bağlı olmayan bir profil "bağlı"
  gösterilmez, *"(bağlı değil)"* yazar.

  Geçici uyarılar (ağ değişimi sırasında bağlantı kopma bildirimi, cihazın
  kendi ağını geri açtığı durum, hata satırı) pencere kapalıyken de görünsün
  diye panelde bırakıldı.

### Düzeltildi
- **"Cihazdaki her şeyi sil" başarıyla bitiyor ama "işlem başarısız"
  diyordu.** Kaldırma sonuna kadar tamamlanıyor, yalnızca son bilgi satırı
  hatalı biçimlendiği için betik hata koduyla çıkıyordu. Operatör bir
  şeylerin silinmeden kaldığını sanıyordu.

- **Kaldırma sonrası sistemde bilerek ne bırakıldığı artık yazılıyor**
  (Docker Engine, yönetim hesabı, ağ ayarı yedekleri...). Önceden neyin
  kasıtlı neyin arıza olduğu anlaşılmıyordu.

- **Kurulum aracında cihazın kendi WiFi ağı listelenmiyor.** Erişim noktası
  modunda başka ağ görünmediğinde tek seçenek cihazın kendi ağı oluyordu;
  seçildiğinde cihaz kendine bağlanmaya çalışıp erişim noktasını düşürüyor
  ve kurulum kilitleniyordu.

- Adım sayacı `--purge-all` ile fazladan adım eklendiğinde "[8/7]" gibi
  tutarsız görünüyordu.

---

## [2.36.0] — 2026-08-02

### Eklendi
- **DNP3 kalite bayrakları artık gateway ayarlarından açılabiliyor.** Gateway
  bugüne kadar her ölçümü "iyi" olarak yayınlıyordu. Bir gösterge akım
  ölçümünü *geçersiz* diye raporladığında (örneğin CT referansını kaybettiğinde
  0 A bildirdiğinde) bu bilgi kayboluyor, SCADA değeri geçerli sanıyordu —
  "hat enerjisiz" yorumu ve buna dayalı yanlış manevra kararı mümkündü.

  Açıldığında geçersiz ölçümler **alarm değerlendirmesine girmez**: alarm
  durumu donar, o ölçümle ne yeni alarm açılır ne açık alarm kapanır.

  > Anahtar **gateway başına**. Açmak saha davranışını değiştirdiği için önce
  > tek bir gateway'de denenip yaygınlaştırılabilsin diye filo geneli tek
  > anahtar yapılmadı. Varsayılan kapalı; mevcut kurulumların davranışı
  > değişmiyor. Kaydedince gateway kurulumu tazelenir ve kısa süre telemetri
  > gelmez.

- **Yeni cihaz modeli sürüm çıkarmadan eklenebiliyor.** Model listesi artık
  sinyal kataloğundan da besleniyor: yeni bir modelin sinyallerini tanımlamak
  onu cihaz formunda seçilebilir kılmaya yetiyor. Önceden sinyalleri
  girebiliyor ama modeli hiçbir cihaza atayamıyordunuz.

- **Gateway güncellemesinde ilerleme görünüyor.** Butona basınca "İstek
  gönderiliyor → Yeni imaj indiriliyor → Güncel imajla başlatılıyor →
  Tamamlandı" akışı ekranda takip ediliyor. Önceden ekran sessiz kalıyor,
  işin başlayıp başlamadığı anlaşılmıyordu. Hata olursa nedeni gösteriliyor.

- **Hangi sürümün geldiği yazıyor.** Çalışan sürüm her durumda görünüyor;
  güncelleme varsa hedef sürüm adıyla belirtiliyor.

### Değişti
- **Sistem Durumu sayfasının üst kısmı yenilendi.** Sayfa başlığı kaldırıldı
  (sekme zaten söylüyor) ve dağınık duran üç grup tek bir gösterge şeridinde
  toplandı: canlı veri durumu → makine/çalışma süresi/son örnek → sürüm →
  yenile. Sayaç kartları sadeleştirildi.

### Düzeltildi
- **Gateway kurulu saha cihazlarında güncelleme durmuyor.** (2.35.1'de
  düzeltilmişti; bu sürümde de geçerli.)
- Gateway ajanı hataları artık ham kod yerine anlaşılır mesaj döndürüyor
  ("request_pending" yerine "Önceki istek hâlâ uygulanıyor").

---

## [2.35.1] — 2026-08-02

### Düzeltildi
- **Gateway kurulu saha cihazlarında güncelleme başlamıyordu.** Gateway
  ajanı kurulumu, repo dizininin içine `gateways/` adında bir çalışma-zamanı
  dizini açıyor. `update.sh` ise güncellemeden önce çalışma ağacının temiz
  olmasını şart koşuyor ve bu dizin `.gitignore` kapsamında olmadığı için
  güncelleme *"Repo'da commit edilmemiş lokal değişiklik var: `?? gateways/`"*
  diyerek duruyordu.

  Tek bir cihazda elle temizlenip geçilecek bir sorun değildi: dizin her
  kurulumda yeniden oluşuyor, dolayısıyla **her güncellemede** tekrarlıyordu.

  Aynı dizindeki dosyalar gateway erişim anahtarı taşıdığı için yok
  sayılması ayrıca güvenlik gereği.

---

## [2.35.0] — 2026-08-02

### Eklendi
- **Cihaz haberleşme durumu artık gateway'in bildirdiği link durumundan da
  belirleniyor.** Önceden bir cihazın "canlı" sayılması yalnızca telemetri
  gelmesine bağlıydı. Arıza bekleyen bir gösterge saatlerce hiçbir şey
  yayınlamayabilir — değer değişmiyorsa gateway veri göndermez. Bu süre
  boyunca cihazın canlı mı kopuk mu olduğu **bilinmiyordu**.

  "Veri gelmiyor" ile "haberleşme koptu" aynı şey değil. Bu ayrımı yapabilen
  tek yer gateway; DNP3 link durumu orada tutuluyor. Gateway bu bilgiyi
  saniyede bir zaten attığı istekle gönderiyor, ek yük yok.

  > Çalışması için gateway'in de güncel olması gerekir (gateway ≥ bu sürümle
  > birlikte yayınlanan imaj). Eski gateway'de davranış aynen eskisi gibi.

- **Arşiv ölü bantları GPS, sinyal seviyesi ve açı ölçümlerine genişletildi.**
  Konum bileşenlerinde eşik hareket büyüklüğünde (~11-18 m): cihaz direkte
  sabit durduğu sürece tek satır yazılır, gerçekten oynarsa kaydedilir —
  "ne zaman oynadı" sorusu (hırsızlık, direk hasarı, yanlış montaj) cevapsız
  kalmasın diye arşivden çıkarılmadı. Telsiz sinyal seviyesinde 2 dBm, açı
  ölçümlerinde 1° gürültü bandı.

  `fault_duration` bilerek eşiksiz bırakıldı: her değer ayrı bir arızanın
  süresidir, ölü bant ardışık benzer süreli arızalardan birini silerdi.

### Düzeltildi
- **TLS'siz saha cihazında harita hiç açılmıyordu.** Oturum çerezi `Secure`
  işaretleniyordu; cihaz `http://enerjione.local` üzerinden kullanıldığı için
  tarayıcı o çerezi göndermiyordu. Normal API çağrıları kurtuluyordu (ayrıca
  Bearer başlığı gidiyor), ama harita karoları `<img>` ile isteniyor ve `<img>`
  başlık gönderemez — her karo 401 alıyordu, indirilmiş çevrimdışı önbellek
  dahil. Artık bayrak isteğin şemasına bağlı.

- **Askıda kalan servisler kendini toparlıyor.** `restart: unless-stopped`
  yalnızca çıkış yapan süreci geri kaldırır; ana döngüsü kilitlenen bir worker
  "çalışıyor" görünür — container ayakta, süreç ayakta, ama telemetri sessizce
  akmayı bırakmıştır. Başında kimse olmayan bir saha cihazında fark edilmesi
  en zor arıza buydu.

- **Disk dolması.** Yeniden teslim defteri (`processed_messages`) 24 saat
  yerine 2 saat tutuluyor — gerçek yeniden teslim penceresi 10 dakika, 24 saat
  onun 144 katıydı. Alt sınır artık kodda kilitli: defteri mesaj hâlâ yeniden
  teslim edilebilirken silmek yinelenen telemetri yazdırırdı.

- **NATS akış yaş sınırları artık ayarlanabiliyor.** Üç ayar kodda tanımlıydı
  ama compose'dan geçirilmiyordu; operatör değiştiremiyordu.

### Güvenlik
- Vite 6 ve pytest 9 yükseltmeleri.

---

## [2.34.0] — 2026-08-01

### Eklendi
- **Gateway sürüm kontrolü ve güncelleme butonu.** Mühendislik > Gateway'ler
  ekranında her gateway için sürüm durumu görünüyor: *Yeni sürüm var*,
  *Güncel* ya da *Sürüm bilinmiyor*. Güncelleme tek tıkla yapılıyor; yeni
  imaj indirilemezse çalışan sürüme dokunulmuyor.

  Buton onay soruyor: gateway yeniden başlarken ona bağlı cihazlardan kısa
  süre telemetri gelmez.

  **"Sürüm bilinmiyor" ayrı bir durumdur.** Kayıt defterine ulaşılamadığında
  "güncel" göstermiyoruz — bu, sormadan verilmiş bir iddia olur ve operatör
  eski sürümde kaldığını fark etmezdi.

- **Yeni gateway sürümü çıktığında tüm kullanıcılara bildirim.** Bildirim
  sürüm başına **bir kez** gönderiliyor; aksi halde operatör güncelleyene
  kadar sürekli tekrarlar ve gerçek uyarılar bu yığının içinde kaybolurdu.

---

## [2.33.0] — 2026-08-01

Sahada görülen bir "ağ kararsız" şikâyetinin kökü bulundu ve kaynağı kapatıldı.

### Düzeltildi
- **Aynı gateway'de iki cihaza aynı IP:port verilebiliyordu.** Horstmann
  cihazı yeni bir bağlantı geldiğinde mevcut olanı kapatır; aynı adrese iki
  cihaz bağlanınca sırayla birbirlerini atarlar. Gateway günlüğünde **2.172
  bağlantı kapanması** birikmişti ve belirti "ağ kararsız, cihazlar kopuyor"
  gibi görünüyordu — oysa tek bir yanlış port alanıydı. Adres düzeltildikten
  sonra 15 dakikada sıfır kopma oldu. Artık hem cihaz eklerken hem
  **düzenlerken** engelleniyor ve hata mesajı sonucu açıklıyor.

- **Akım sinyalleri iki farklı birimde tutuluyordu.** `actual_current`
  ampere çevriliyor, diğer altı akım sinyali (trip level, min/maks/ortalama/
  arıza/son bilinen akım) miliamper olarak bırakılıyordu. Aynı cihazda aynı
  büyüklük 1000 kat farklı görünüyor, bu sinyallere kurulan alarm eşikleri
  diğerleriyle kıyaslanamıyor ve IEC 104 / Modbus çıkışlarına tutarsız
  ölçekle gidiyordu. Hepsi ampere çevrildi.

  **Dikkat:** eski arşiv kayıtları eski ölçekte kalıyor; bu altı sinyalin
  grafiğinde güncelleme anına denk gelen bir basamak görünür.

### Eklendi
- **Gateway susarsa cihazlar artık yeşil kalmıyor.** Cihaz durumu yalnızca
  telemetri geldiğinde güncelleniyordu; gateway tamamen sustuğunda tüm
  cihazlar son durumlarında donuyor ve harita sağlıklı görünmeye devam
  ediyordu. Gateway üç dakikadır görülmediyse cihazların durumu artık
  **"bilinmiyor"** olarak işaretleniyor — "çevrimdışı" değil, çünkü cihazlar
  çalışıyor olabilir ve yalnızca haber ulaşamıyordur.

  Cihaz bazlı "veri gelmiyor" kontrolü **bilerek yapılmıyor**: gateway
  yalnızca değişen değerleri yayınladığı için durağan bir fiderde yanlış
  alarm üretirdi.

---

## [2.32.0] — 2026-08-01

Saha test cihazında **15 cihazla canlı yük altında** yapılan ölçümlerden doğdu.
Okuma başına ~6,4 satır işlemi yapılıyordu; bu sürüm bunun büyük kısmını
kaldırıyor.

### Değişti
- **Historian artık seçici.** Gerçek SCADA pratiğinde her tag arşive
  yazılmaz: anlık değer her zaman güncel tutulur, arşive yalnızca
  işaretlenen tag'ler ölü bant süzgecinden geçerek yazılır. Bu sistemde iki
  ön koşul da zaten sağlanıyordu — alarm motoru akış tabanlı çalışıyor
  (geçmiş sorgusu yapmıyor) ve canlı değer ayrı bir tabloda — dolayısıyla
  **alarm doğruluğu etkilenmiyor**.

  Arşivden çıkarılanlar: seri numarası, firmware sürümü, donanım revizyonu,
  SIM CCID, GPS gibi ömür boyu sabit metadata (30 sinyal) ve
  `config_update` / `firmware_update` / `trigger_*` gibi komut noktaları
  (18 sinyal). Bunların zaman serisi hiçbir soruya cevap vermiyordu.

  Ölü bant varsayılanları: akım 0.5 A, gerilim 1 V, sıcaklık 0.5 °C.
  Miliamper birimli akım sinyalleri bilerek kapsam dışı bırakıldı (ayrıca
  belirlenecek). Her sinyal için ayrı ayrı ya da toplu kapatılabilir.

- **Alarm kendiliğinden temizlendiğinde ayrıca olay kaydı düşülmüyor**
  (varsayılan). Dalgalanan bir sinyal dakikalar içinde binlerce
  tetiklen/temizlen çifti üretiyor ve gerçek operatör olayları — yetki
  kullanımı, komut gönderimi, ayar değişikliği — bu yığının içinde
  kayboluyordu. Bilgi kaybı yok: temizlenme zaten alarm kaydının kendisinde
  duruyor ve alarm geçmişi oradan okunuyor. **Onaylanmış** bir alarm
  temizlendiğinde kayıt her zaman yazılmaya devam ediyor, çünkü orada alarm
  satırı siliniyor ve olay kaydı geriye kalan tek iz.

### Düzeltildi
- **IEC 104 açıkken her telemetri okuması için boşa iş yapılıyordu.** Nokta
  güncellemesi başına bir denetim kaydı oluşturuluyor ama hiçbir zaman
  kaydedilmiyordu (çağıran taraf oturumu kaydetmeden kapatıyor). Saniyede
  yüzlerce nesne kurulup atılıyordu. Kaydedilseydi daha kötü olurdu: denetim
  kaydı 2 yıl saklanıyor ve 15 cihazlık test kurulumunda bile günde 32
  milyon satır demekti.

- **SCADA istemcisi bağlantıyı kapattığında hata günlüğüne "çöktü" yazılıyordu.**
  Normal bağlantı sonlanması yakalanan kopma tiplerinden biri değildi;
  her oturum sonunda tam bir hata izi basılıyor, gerçek arızalar bu
  gürültünün içinde kayboluyordu.

- **Cihaz "son veri" zamanı her okumada yazılıyordu.** 15 cihazlık kurulumda
  saniyede ~55 güncelleme demekti; alanın tek tüketicisi arayüzdeki
  "Son veri: X önce" göstergesi. Birkaç saniyelik eşikle yazma yükü ~%95
  düştü, ekranda hiçbir fark yok. Cihazın çevrimiçi olup olmadığı bilgisi
  **kısılmadı** — o anında yazılmaya devam ediyor.

- **Yayınlanmış outbox kayıtları 15 dakika saklanıyor** (önceden 1 saat,
  ondan önce 24 saat). Süre tahminle değil ölçülen yeniden teslim
  penceresinden türetildi ve kod artık o eşiğin altına inilmesine izin
  vermiyor.

---

## [2.31.0] — 2026-08-01

Saha test cihazında yapılan **ölçümlerden** doğan sürüm. Önceki sürümde
kapatılamayan "yeşil yalan" sınıfı bitirildi, IEC 104 reset komutu eklendi ve
diski dolduran bir tablo bulundu.

### Düzeltildi
- **Kuyruk arızası cihazı tamamen karartıyordu.** `/health` NATS'ı kritik
  sayıp 503 dönüyor, `frontend-web` compose'da `service_healthy` beklediği
  için **arayüz hiç başlamıyordu**. Yani NATS'taki tek bir yanlış
  yapılandırma (ör. yarım uygulanmış TLS) 80 portunda hiçbir şey
  bırakmıyordu. Oysa kuyruk çökse bile giriş, yetkilendirme, ayarlar, geçmiş
  veri, arıza listesi, yedekleme ve uzaktan bakım çalışmaya devam eder.
  Artık kritiklik sınırı yalnızca Postgres; NATS/RabbitMQ düşüşü
  `status="degraded"` + `degraded_reasons` ile **açıkça** raporlanır.

- **`outbox_events` tablosu diski dolduruyordu.** Ölçüm: saatte 326.027 satır
  / 272 MB — veritabanının en büyük tablosu, telemetrinin kendisinden (65 MB)
  dört kat büyük. 24 saatlik saklama süresi ölçülen oranda **7,8 milyon satır
  / ~6,5 GB** demekti; cihazda 8,9 GB boş disk vardı, yani sürekli yük
  altında ~1,5 günde doluyordu. Saklama 1 saate çekildi, hiç taranmayan iki
  indeks kaldırıldı ve `published` indeksi kısmi indekse çevrildi (aynı sorgu
  93 buffer → 1 buffer).

- **Bilinmeyen durum yeşil "Normal" görünüyordu.** Cihaz detay ekranlarındaki
  arıza rozetleri "veri yok", "gerçekten normal" ve "haberleşmesi kopuk
  cihazdan gelen 0.0" için **aynı yeşil rozeti** üretiyordu. Üçüncüsü en
  ağırıydı: sunucu o okumayı alarm değerlendirmesine zaten sokmuyor, arayüz
  onun kararını geçersiz kılıyordu. Artık güvenilmeyen ölçüm nötr "Veri yok"
  / "Güvenilmez" rozeti alıyor.

- **Canlı veri rozeti hiç görünmüyordu.** Soket durumu iki sayfaya
  geçiriliyor ama ikisinde de okunmuyordu; soket ölse bile ekranda hiçbir
  işaret çıkmıyor, bayat değerler sessizce duruyordu. Rozet artık görünür ve
  soket durumunu değil **veri akışını** gösteriyor — sunucu 30 sn'de bir ping
  attığı için soket, gateway tamamen sussa bile açık kalıyordu.

- **Proxy bozulunca harita tamamen kararıyordu.** Karolar çevrimdışı önbellek
  için backend üzerinden geçiyor; nginx yönlendirmesi ya da backend
  bozulduğunda tarayıcıda internet olsa bile harita boş kalıyordu. Artık
  proxy'den karo gelmeyince doğrudan yukarı akışa düşülür.

- **NATS TLS yarım kalabiliyordu.** TLS'in çalışması üç şeyin aynı anda doğru
  olmasına bağlı; biri eksik kalırsa arıza **sessiz** oluyordu, çünkü NATS'ın
  kendi healthcheck'i TLS'siz izleme portunu prob ediyor ve container
  "healthy" görünüyordu. Ayrıca sertifika dizini yalnızca sunucuya mount
  ediliyordu; istemciler CA dosyasını bulamıyordu.

- **`OUTBOX_*` ayarları hiç uygulanmıyordu.** Beş ayar belgeliydi ama
  compose'da listelenmediği için container'a ulaşmıyordu; operatör `.env`'e
  yazsa da hiçbir şey değişmiyordu.

### Eklendi
- **IEC 104 üzerinden arıza göstergesi reset komutu** (`C_SC_NA_1`). Kapsam
  bilinçli olarak dar: kabul edilen tek kontrol komutu budur. Komut, arayüzden
  gelenle aynı yetki/allowlist/denetim yolundan geçer ve "kabul edildi"
  (ACT_CON) ile "cihazda gerçekleşti" (ACT_TERM) ayrı raporlanır — komut NAT
  arkasındaki gateway'e config-poll ile gittiği için arada dakikalar olabilir.

- **Frontend'in ilk otomatik testleri.** Yeni bağımlılık eklenmeden
  (esbuild + Node'un yerleşik test koşucusu) ve CI'da çalışıyor.

### Güvenlik
- IEC 104 komut kapsamı **iki katmanlı** doğrulanıyor. Tip filtresi tek başına
  yetmezdi: sinyal kataloğu düzenlenebilir olduğu için `firmware_update` gibi
  bir noktaya komut tipi verilmesi, tek katmanlı bir tasarımda uzaktan
  firmware tetiklemeye dönüşürdü.

---

## [2.30.0] — 2026-08-01

600 cihaz ölçeği için yapılan ikinci denetimin **Faz 1 ve Faz 2'sinin tamamı**
(16 madde) ve önceki denetimden kalan engelleyiciler kapatıldı.

### Güvenlik
- **WiFi erişim noktasından SCADA ve mesajlaşma portları açıktı.** Appliance'ın
  şifresiz WiFi ağına bağlanan biri, kimlik doğrulaması olmadan Modbus (502) ve
  IEC 104 (2404-2406) üzerinden tüm sahanın arıza/konum/ölçüm durumunu
  okuyabiliyor; NATS ve RabbitMQ'yu da brute-force için bulabiliyordu. Artık
  AP arayüzünde yalnızca web arayüzü (80/443) erişilebilir.
- **İstemci IP'si uydurulabiliyordu.** `X-Forwarded-For` başlığı zincire
  olduğu gibi giriyor ve backend en soldaki — yani istemcinin yazdığı —
  değeri okuyordu. Bu IP üç yerde güvenlik kararıydı: API anahtarı IP
  kısıtlaması **tek bir başlıkla atlanıyordu**, hız sınırı aşılabiliyordu ve
  denetim kayıtlarına yanlış IP yazılıyordu.
- **Gateway token'ı değiştirildiğinde eski token çalışmaya devam ediyordu.**
  Yeni token 401 alıyor, eskisi geçerli kalıyordu; yani "sızdı, değiştirelim"
  amacıyla yapılan işlem tam tersini yapıyordu.
- **Zorunlu şifre değişimi WebSocket'te uygulanmıyordu.** Varsayılan kurulum
  parolasıyla giren biri arayüzden engelleniyor ama canlı telemetri akışına
  erişebiliyordu.
- **Kilitlenen hesabın açılma yolu yoktu.** Şifre sıfırlama kilide
  dokunmuyordu; tek installer hesabı kilitlenince gateway ekleme, ağ ayarı,
  yedek ve uzaktan bakım birlikte kilitleniyor, çözüm saha ziyareti oluyordu.
- **Root ajanlar symlink takip ediyordu.** Koruma yalnızca dosya adını
  kapsıyordu; dizin bileşenleri açıktı ve bu yolla cihaz kalıcı olarak
  açılamaz hale getirilebiliyordu.

### Düzeltildi
- **Açık arızalar listeden ve haritadan kaybolabiliyordu.** Alarm listesi 500
  kayıtla sınırlıydı ve sınır, sorumluluk alanı süzgecinden önce
  uygulanıyordu. Eski ama hâlâ açık bir arıza pencerenin dışına düşünce
  haritadaki işaret yeşile dönüyordu. Artık açık alarmlar hiç kırpılmıyor ve
  süzgeç sorguya iniyor.
- **Sinyal ayarları her yeniden başlatmada fabrika değerine dönüyordu.**
  Arayüzden yapılan IOA/ölçek/etiket düzenlemeleri kaydediliyor, denetim
  kaydı tutuluyor, sonra ilk açılışta sessizce geri alınıyordu. Kullanıcının
  değiştirdiği alanlar artık korunuyor; fabrikaya dönüş ayrı ve bilinçli bir
  işlem olarak duruyor.
- **Tek bozuk mesaj tüm telemetri akışını durduruyordu.** Beklenenden uzun bir
  sinyal adı toplu yazmayı patlatıyor, hiçbir ölçüm onaylanmıyor ve aynı
  paketteki sağlam ölçümler de tekrar tekrar düşüyordu. Ekranda "bağlantı
  koptu" görünüyordu; sebep tek bir metindi.
- **SCADA genel sorgusu 12. nesneden sonra kesiliyordu.** Sorgu bitiş bildirimi
  hiç gitmiyordu.
- **Tüm cihazlar SCADA'da tek cihaz gibi görünüyordu.** Cihazlara ayrı adres
  atanmadıkça hepsi aynı adrese biniyor, hangi fiderin arızalandığı
  anlaşılamıyordu. Adresler artık otomatik atanıyor (elle verilmişlere
  dokunulmuyor).
- **Arıza bildirimi webhook'a hiç gitmeyebiliyordu.** Gönderim başarısız olsa
  bile değer "gönderildi" sayıldığı için, bağlantı döndüğünde arıza bir daha
  yollanmıyordu — arıza kalkana kadar.
- **E-posta gönderimi sonsuza kadar bekleyebiliyordu** ve bu bekleme arıza
  kaydının yazılmasını da askıya alıyordu.
- **Yedek yükleme arayüzden çalışmıyordu.** Zincirdeki en düşük boyut sınırı
  (10 MB) yüzünden felaket kurtarmanın tek arayüz adımı kullanılamıyordu.
- **Yedek dosyaları diski dolduruyordu.** Güncelleme öncesi alınan yedekler
  hiç silinmiyor, geçmiş telemetri arşivinin tamamını içeriyor ve arayüzden
  geri de yüklenemiyordu. Aynı hata müşteriye verilen elle yedek komutunda da
  vardı.
- **Off-site yedekleme hiç çalışmıyordu.** Ayar girilse bile kopya
  alınmıyordu ve bu ancak felaket anında anlaşılırdı.
- **Özet tabloları sınırsız büyüyordu** ve Sistem Durumu bu sırada "sorun yok"
  gösteriyordu.
- **Yarıda kalan güncelleme cihazı açılamaz bırakabiliyordu**; dosyalar da
  artık eski sürüme geri alınıyor.
- **Belgelenen ölçekleme ayarı veritabanı bağlantı sınırını aşıyordu** — yani
  performans için yapılan değişiklik hataya yol açıyordu.

### Değişti
- **Canlı değerler artık ayrı bir tablodan okunuyor.** Anasayfa her açılışta
  geçmiş telemetrinin tamamını tarıyordu; bu, eşzamanlı birkaç kullanıcıda
  arka ucu belleğe boğuyordu.
- **IEC 104 kapsamı sabitlendi:** yalnızca izleme sinyalleri yayınlanır;
  metin sinyalleri ve analog çıkış kapsam dışıdır. Desteklenmeyen bir komut
  artık sessizce yutulmak yerine açıkça reddedilir.
- **Geçmiş verisi daha küçük parçalara bölünüyor** (600 cihaz ölçeğinde yazma
  ve sorgu başarımı için). Mevcut veri etkilenmez.
- Kurulumda FTP parolası otomatik üretiliyor; eskiden boş kaldığı için dosya
  transferi sunucusu sürekli yeniden başlıyor ve gerçek arızaları
  maskeliyordu.

### Test ve doğrulama
- Backend testleri 265 → **884**; IEC 104 servisi 15 → **40**.
- CI 7 → **12 iş**: Modbus ve IEC 104 servisleri, nginx yapılandırması,
  güvenlik duvarı kuralları ve appliance ajanları artık gerçekten koşuluyor.
- 201 API ucunun yetki sınırı otomatik doğrulanıyor.

---

## [2.29.0] — 2026-07-31

### Düzeltildi
- **Yedekten geri yükleme veritabanını yarım bırakıyordu (kritik).** Geri
  yükleme sırasında eski bağlantıları temizleyen döngü, `pg_restore`'un kendi
  bağlantılarını da kesiyordu. Sonuç: geri yükleme her denemede aynı yerde
  duruyor, üstelik silinmiş tablolar geri gelmediği için mevcut veri de
  kaybediliyordu — tam da yedeğe en çok ihtiyaç duyulan anda.
- **SCADA çıkışı kendini boğuyordu (kritik).** IEC 104 sunucusu her değer
  değişiminde sınırsız iş kuyruğa alıyordu; SCADA tarafı yavaşladığında bellek
  dolana kadar büyüyordu. Ayrıca sıra numarası yarışı yüzünden SCADA bağlantısı
  kendiliğinden kopabiliyordu. Artık bağlantı başına sınırlı kuyruk var ve
  yetişemeyen istemcide en eski bildirimler düşürülüp kayıt altına alınıyor.
- **Alarm servisi birkaç saatte bir yeniden başlıyordu (kritik).** Hiçbir
  kuralın kullanmadığı ölçüm geçmişi biriktiriliyor ve bellek sınırı
  aşılıyordu. Her yeniden başlangıçta açık alarmlar "yeni alarm" sayılıp
  bildirimler tekrar gönderiliyordu.
- **NATS erişilemezken açılan sistem bir daha telemetri yayınlamıyordu
  (kritik).** Bağlantı yalnızca bir kez deneniyordu; NATS saniyeler sonra
  düzelse bile veri akmıyor, cihazlar arayüzde "Kesik" görünüyordu. Artık
  bağlantı kurulana kadar yeniden denenir.
- **Operatör yetkisi dışına taşabiliyordu.** "Tümünü onayla/resetle"
  işlemleri sorumluluk alanı dışındaki alarmlara da uygulanıyor, ekranda ise
  hiçbir şey olmamış gibi görünüyordu. Ayrıca gateway listesi telemetri
  şifresini düz metin döndürüyordu; bu şifreyle sahte arıza üretmek veya
  gerçek arızayı gizlemek mümkündü.
- **Zorunlu şifre değişimi atlanabiliyordu.** Uyarı yalnızca arayüzdeydi;
  doğrudan istek atan biri tam yetkiyle işlem yapabiliyordu. Artık şifre
  değiştirilene kadar diğer işlemler sunucu tarafında reddedilir.

### Eklendi
- **NATS için TLS desteği (isteğe bağlı).** Açıldığında gateway şifresi ve
  telemetri şifreli kanaldan gider. Kurulum: `nats-tls-setup.sh` ile sertifika
  üretilir, ardından `.env` içinde etkinleştirilir. Varsayılan kapalıdır.
- **Ayrı arka plan servisi (isteğe bağlı).** Yoğun kurulumlar için arayüz ve
  arka plan işleri ayrı süreçlere alınabilir; arayüz çok çekirdekli
  çalıştırılabilir. Arka plan işlerinin tek yerde çalışması garanti altına
  alındı — yedekleme veya bildirim iki kez tetiklenmez.

---

## [2.28.0] — 2026-07-31

### Düzeltildi
- **Haritalar boş kalıyordu (kritik).** Harita karo istekleri nginx'te statik
  dosya kuralına takılıyordu: yol `.png` ile bittiği için regex bloğu düz
  prefix kuralını eziyor ve istek backend'e **hiç ulaşmıyordu**. Tarayıcıda
  internet olsa bile tüm haritalar boştu. `npm run dev` bu kuralı
  çalıştırmadığı için sorun yalnızca Docker/nginx kurulumunda görünüyordu.
- **Yeniden başlatmadan sonraki ilk backlog uyarısı bastırılıyordu (kritik).**
  Uyarı sınırlayıcısı `time.monotonic()` değerini mutlak olarak
  karşılaştırıyordu; Linux'ta bu değer makine açılışından beri geçen süre
  olduğu için açılıştan sonraki ilk 5 dakika boyunca uyarı üretilmiyordu. Oysa
  telemetri birikimi tam da o pencerede zirvede olur. Telemetri akışı
  `discard=old` ile çalıştığından tampon taşarsa mesajlar sessizce düşer —
  operatör hem veri kaybını hem uyarıyı kaçırıyordu.
- **Bağlantısı kopan cihaz için "arıza geçti" denmesi.** Alarm kapatma yolları
  ölçüm kalitesine bakmıyordu; `comm_lost` ile gelen 0.0 değeri eşiğin altına
  düştüğü için açık arıza alarmı kapanıyor ve harita yeşile dönüyordu.
- **Ayar değişikliği sahaya ulaşmıyordu.** `config_version` gönderilen
  ayarların tamamını temsil etmiyordu; cihazın TCP portu gibi alanlar
  değiştiğinde sürüm aynı kalıyor, gateway "değişmedi" yanıtı alıp eski ayarla
  çalışmaya devam ediyordu.

### Eklendi
- **Cihaz türüne göre sinyal profili.** Aynı DNP3 adresi farklı cihaz
  modellerinde farklı büyüklüğü gösterir. Backend artık her cihazın türünü
  bildiriyor ve gateway o türün sinyal setini kullanıyor; adres haritası
  gateway'de yerleşik olarak da bulunuyor. İkinci bir cihaz modeli
  eklendiğinde ölçümlerin yanlış sinyal adıyla kaydedilmesi engellendi.
- **Cihaz saati göstergesi.** Cihazın kendi olay zamanı ve o zamanın
  güvenilirliği kaydediliyor; saati kaymış cihaz canlı değerler ekranında
  işaretleniyor. Alarm saatleri her zaman sunucu saatine göre belirlenir.
- **Gateway sağlık bildirimi.** NAT arkasındaki gateway'in durumu düzenli
  olarak raporlanıyor.

### Değişti
- Gateway, ayarları yalnızca **değiştiğinde** indiriyor. Önceden her
  yoklamada tüm sinyal listesi tekrar iniyordu.

---

## [2.27.0] — 2026-07-31

### Düzeltildi
- **Kurulum, istenen sürümü sessizce yok sayabiliyordu (kritik).** Kurulum
  aracında bir sürüm seçilse bile cihaz eski sürümde kalıp "başarılı"
  bitiyordu. İki nedenin çarpımıydı: (1) kurulum betikleri deponun içindeki
  ajan dosyalarına `chmod` uygulayıp çalışma ağacını **kalıcı olarak** kirli
  bırakıyordu — yani ilk kurulumdan sonra her cihazda varsayılan durum buydu;
  (2) bu kirlilik yüzünden atlanan komut `git fetch` idi, oysa fetch çalışma
  ağacına dokunmaz — korunması gereken `git checkout` idi ve orada hiçbir
  kontrol yoktu. Artık istenen sürüme geçilemiyorsa kurulum **durur** ve
  sonunda "istenen sürüm gerçekten kuruldu mu" doğrulaması yapılır.
- **Canlı değer ekranı NATS koptuğunda kararıyordu.** Yeni fan-out köprüsü
  bağlantı koptuğunda hâlâ "hazırım" dediği için bellek-içi yedek yol devreye
  girmiyordu. Ayrıca köprü ilk bağlantı başarısız olursa bir daha hiç
  denemiyordu. İkisi de giderildi.
- Uzaktan bakımda "kapat/aç" düğmesi cihazı kalıcı çevrimdışı bırakabiliyordu.

### Eklendi
- **Telemetri Boru Hattı göstergesi (Sistem Durumu).** Tüketicinin gelen
  veriye yetişip yetişmediği artık görünür: bekleyen mesaj sayısı, işlem hızı,
  hatalı mesaj, NATS bağlantı durumu. Eşik aşılırsa uyarı ve olay kaydı
  üretilir. Bu gösterge önemli çünkü tampon taşarsa en eski ölçümler
  **sessizce** düşürülüyor — ekranda başka hiçbir belirti çıkmıyor.
- **Canlı değer yayını NATS üzerinden dağıtılıyor.** Tek başına davranış
  değiştirmez; sistemin ileride birden fazla sürece bölünebilmesinin ön
  koşuludur.
- Cihaz hesaplarının profil resmi EnerjiOne logosu yapılıyor; giriş ekranında
  gri siluet yerine ürün logosu görünür.
- WiFi kartı için kalıcı görev tercihi: cihaz kendi ağını mı yayınlasın (AP)
  yoksa kayıtlı bir ağa mı katılsın (client). Tek radyo ikisini aynı anda
  yapamaz; tercih artık kalıcı. Mevcut cihazlarda davranış değişmez.

### Değişti
- Sürekli entegrasyon artık NATS servisiyle çalışıyor: fan-out köprüsünün
  koruyucu testi eskiden her koşuda sessizce atlanıyordu.

---

## [2.26.0] — 2026-07-31

### Düzeltildi
- **Telemetri alımı ~83 gün sonra tamamen duruyordu (kritik).** `telemetry` ve
  `processed_messages` tablolarının birincil anahtarı `int4` idi. 600 cihaz
  ölçeğinde günde ~26M satır girdiği için sayaç 2,1 milyar tavanına yaklaşık
  83 günde dayanıyor; o an `nextval()` hata veriyor, toplu yazım commit'i
  patlıyor ve **hiçbir NATS mesajı onaylanmadığı için** aynı grup sonsuza
  kadar yeniden deneniyordu. Sonuç kademeli yavaşlama değil, ani ve tam
  duruştu. Kolonlar ve arkalarındaki sequence'ler `bigint`e çevrildi
  (migration 0021). **Not:** retention bu sorunu çözmez — satır silmek
  sayacı geri almaz.
- **Boot sırasında sonsuza kadar bekleme.** Açılışta çalışan eski şema
  bloğunda hiçbir kilit zaman aşımı yoktu; çakışan bir kilit (zamanlanmış
  yedek, restore, açık bir psql oturumu) varsa açılış süresiz bekliyordu ve
  yeniden başlatmak bunu çözmüyordu. Artık 5 sn kilit tavanı var ve blok
  hata verse bile backend açılmaya devam ediyor (önceden sonsuz crash-loop
  ve tamamen karanlık bir cihaz demekti).

### Eklendi
- **Disk guard — "disk asla dolmasın" güvencesi.** Toplam kapasitenin %10'u
  boş kalacak şekilde gerçek boş alanı ölçer (yüzde tabanlı, farklı disk
  boyutlarına uyarlanır). Kademeli davranır: önce yalnızca uyarır, sonra
  saklama sürelerini geçici kısaltır, en son yeniden üretilebilir veriyi
  (harita önbelleği, fazla yedekler) siler. **Denetim kaydına, lisansa,
  ayarlara, alarm/arıza geçmişine ve telemetri arşivine asla dokunmaz.**
  Ayarlar: `DISK_GUARD_*`.
- **Olay kayıtları (denetim) için 2 yıllık saklama.** Önceden hiç
  temizlenmiyordu. Beklenmedik bir olay fırtınasına karşı adet tavanı da var;
  bu tavan yalnızca telemetri/outbound gürültüsünü düşürür — güvenlik,
  lisans ve kimlik kayıtlarına dokunmaz.
- **Telemetri özet arşivi artık sınırlı ve sıkıştırılıyor.** Dakikalık özet
  1 yıl, saatlik özet 2 yıl saklanır (migration 0023). Önceden bu iki tablo
  sınırsız büyüyordu ve dakikalık özet pratikte ham verinin kopyası
  boyutundaydı — diski asıl dolduran kalem buydu.

### Değişti
- **NATS tamponu dolduğunda sistem artık DURMUYOR.** Önceden akış tavanına
  çarpınca yayın reddediliyor ve telemetri tamamen kesiliyordu; artık en eski
  mesajlar düşürülüp akış sürdürülüyor. Uzun bir kesintide (ham akış için
  ~19 saat) o dönemin en eski mesajları sessizce kaybolur — bilinçli takas.
  Ayrıca akış başına disk tavanı eklendi (toplam 12 GiB).
- **Yedekler artık telemetri arşivini içermiyor.** Yedek dosyası birkaç yüz
  MB'a düştü (önceden her yedek 90 günlük arşivi taşıyordu). Ayar, alarm,
  arıza, denetim ve bildirim geçmişi korunur; felaket kurtarma sonrası
  telemetri geçmişi boş gelir ve yeniden toplanmaya başlar.
- **İdempotency defteri 7 gün yerine 24 saat tutuluyor.** Gerçek ihtiyaç
  10 dakika; eski değer tabloyu gereksiz yere ~180M satıra çıkarıyordu.
- Başarısız yedek kayıtları ve yarım kalmış dosyalar otomatik temizleniyor.
  Elle alınan yedekler **varsayılan olarak silinmez**
  (`BACKUP_MANUAL_RETENTION_DAYS=0`); her koşulda en yeni başarılı yedek
  korunur.
- Saha cihazı disk standardı **128 GB → 500 GB** olarak güncellendi
  (`docs/APPLIANCE.md`), kalem kalem disk bütçesi eklendi.

### Güvenlik
- **Uzaktan bakım artık varsayılan KAPALI (davranış değişikliği).** Saha cihazı
  tailnet'e kayıtlı kalır ama gelen tüm bağlantılar reddedilir
  (`tailscale set --shields-up=true --ssh=false`). Müşterinin yetkili
  kullanıcısı — **yalnızca `engineer` rolü** — arayüzden süreli izin verir
  (Mühendislik > Sistem > Uzaktan Bakım; 15 dk – 24 saat), süre dolunca erişim
  kendiliğinden kapanır. `installer` rolü izin **veremez**: installer üretici
  tarafıdır, kendi kendine açabilseydi "müşteri izin verir" mekanizması
  anlamsızlaşırdı.
  - Süreyi host'ta root ile çalışan yeni `e1-rad` ajanı sayar; son tarih mutlak
    zaman olarak `lease.json`da durur ve 30 sn'lik systemd timer'ı uygular.
    **Backend, veritabanı ve container tamamen kapalı olsa bile izin kapanır.**
    Yeniden başlatma izni silmez ama uzatmaz da.
  - İzin verme/geri alma ve otomatik kapanma `system_events`e
    (`category=security`) yazılır: kim, hangi rolle, hangi IP'den, ne kadar
    süreyle. Otomatik kapanma olayları **gerçekleştiği zamanla** kaydedilir.
  - `setup-tailscale.sh` artık erişimi AÇMAZ. Önceki sürümde her `update.sh`
    çalışmasında `_ensure_ssh()` SSH'i geri açıyordu ve idempotent erken çıkış
    yalnızca `BackendState == "Running"` iken devreye giriyordu — bu ikisi
    birlikte müşterinin kapattığı kapıyı sessizce geri açardı.
  - **Sahaya çıkışta dikkat:** güncelleme tailnet üzerinden yapılıyorsa
    `setup-remote-access.sh` kendi SSH oturumunuzu kesmemek için 60 dakikalık
    kurulum mahsubu yazar (`E1_RAD_GRACE_MIN`). Bu tespit ilk olarak TEK bir
    test cihazında, yerel/fiziksel erişim elde tutularak denenmelidir.

---

## [2.25.0] — 2026-07-31

### Güvenlik
- **Canlı telemetri WebSocket'inde operatör kapsamı uygulanmıyordu** — cihaz
  filtresi tamamen istemciden geliyordu; filtre göndermeyen bir operatör
  sistemdeki **tüm** cihazların telemetrisini dinleyebiliyordu. Kapsam artık
  sunucuda hesaplanıyor, istemci filtresi yalnızca daraltabiliyor.
- **Oturum iptali WebSocket'te işlemiyordu** — "oturumu at" dendikten sonra
  açık soket akmaya devam ediyordu, logout edilmiş token ile yeni bağlantı
  açılabiliyordu. Artık bağlantı kurulurken ve her 30 saniyede doğrulanıyor.
- **Gateway kurulum ajanı** artık container'dan compose dosyası kabul etmiyor;
  yalnızca doğrulanmış parametrelerden kendi şablonunu üretiyor. Önceki regex
  kara listesi uzun-form bind, named-volume `driver_opts`, `security_opt`
  unconfined gibi yollarla aşılabiliyordu (host'ta root'a çıkış).
- **nginx rate-limit'i gerçek istemci IP'si üzerinden** çalışıyor. Ters vekil
  arkasında tüm istekler aynı IP görünüyordu; bu, dakikada 5 denemeyle
  **herkesin girişini** kilitlemeye izin veriyordu.
- **Güvenlik başlıkları statik dosyalarda kayboluyordu** — nginx'te
  `add_header` miras alınmadığı için tüm `.js`/`.css` dosyaları CSP, nosniff
  ve X-Frame-Options olmadan servis ediliyordu.
- **Oturum ömrü** dört dosyada dört farklı değerdeydi ve compose'daki değer
  "beni hatırla" süresine eşitti — yani kutucuk işlevsizdi, işaretlemeyen
  kullanıcı da 30 günlük token alıyordu. Hepsi 24 saate hizalandı.

### Eklendi
- **Telemetri Arşivi sağlık kartı** (Sistem Durumu) — arşiv tablosunun saklama
  süresi politikası gerçekten kurulu mu, hypertable mı, ne kadar disk
  kullanıyor. Politika kurulmadığında tek belirti diskin dolmasıydı; artık
  önceden görülüyor. Eksik politikaları onaran migration da eklendi.
- **Arıza tel mesafesi** — arıza bölgesinin hat başından kaç metre uzakta
  olduğu hesaplanıyor ve gösteriliyor. Kuş uçuşu değil: direk koordinatları
  üzerinden hat boyunca, cihazların direkler arasındaki konumu da hesaba
  katılarak. Branşman hatlarda mesafe ana hattaki dallanma direğinden itibaren
  toplanır.
- **Çevrimdışı harita** artık modal yerine Mühendislik altında ayrı bir sayfa;
  alan seçimi harita üzerinde sürükleyerek yapılıyor.
- **Klavye erişilebilirliği** — modallar ESC ile kapanıyor ve odak modal içinde
  kalıyor. Önce yedi modalın yalnızca biri ESC ile kapanıyordu; Tab'a basan
  kullanıcı modalın arkasındaki forma düşüyordu.
- Render hatasında beyaz ekran yerine "yeniden yükle" ekranı gösteriliyor.

### Düzeltildi
- **Bozuk bir üçüncü taraf apt deposu kurulumu tamamen durduruyordu.** Sahada
  makinede duran ve imza anahtarı eksik bir Google Chrome deposu yüzünden
  `apt-get update` hata döndü ve kurulum orada öldü — oysa ihtiyaç duyulan
  Ubuntu depoları sağlamdı. Artık ilgisiz bir deponun bozuk olması kurulumu
  durdurmuyor; depo adıyla bildiriliyor ve karar paket kurulumunda veriliyor.
- **Canlı değerler ekranı çok cihazda donuyordu.** Gelen her telemetri mesajı
  tüm satır listesini baştan sona geziyordu; 600 cihazda tarayıcı sekmesi
  kilitleniyordu. Mesajlar artık toplu işleniyor (ölçüldü: 699 ms → 24 ms).
- **Arka planda gereksiz sorgu yükü** — alarm, arıza, olay, topoloji ve cihaz
  listesi hangi sayfada olduğunuza bakılmaksızın 5 saniyede bir çekiliyordu.
  Artık yalnızca o veriyi gösteren sayfalarda çekiliyor ve sekme arka plandayken
  tamamen duruyor.
- **Harita çok cihazda takılıyordu** — topoloji hesabı cihaz durumu her
  güncellendiğinde (5 saniyede bir) baştan yapılıyordu; artık yalnızca topoloji
  veya konum gerçekten değişince yapılıyor.
- **Hat Arızaları sayfası** her satır için ayrı sorgular atıyordu (200 arızada
  5 saniyede ~1.200 sorgu). Sorgu sayısı artık arıza sayısından bağımsız.

---

## [2.24.6] — 2026-07-30

### Düzeltildi
- **Lisans kilidi ağ ayarlarını da kilitliyordu** — lisanssız cihazda ağ
  yapılandırmasına erişilemiyor, dolayısıyla lisans da alınamıyordu.
- **Tailscale SSH** zaten tailnet'e katılmış cihazlarda açılmıyordu.
- **Postgres varsayılan ayarlarla koşuyordu** (`shared_buffers=128MB`,
  `work_mem=4MB`) — sıralamalar diske taşıyordu.
- Telemetri temizleme sorgusu tüm tabloyu tarıyordu; filtre pencere
  fonksiyonunun içine indirildi.
- Alarm reconcile'da N+1 — açık alarm başına bir sorgu atılıyordu.

### Değişti
- **İlk yükleme 2.1 MB → 739 KB** (%66): 21 sayfa tembel yüklemeye alındı.
- Kurulum artık **sessiz "hiçbir şey olmadı" durumlarını görünür kılıyor**:
  appliance atlandığında sonucu, güncellemede yayınlanmamış değişiklik
  olduğunu açıkça söylüyor.

### Eklendi
- Kurulum aracına **WiFi ayarı ve internet kontrolü** — cihazda internet
  yoksa kurulum GitHub'dan indirme yapamıyordu.
- Kurulum aracı sekmeli arayüz, ağ listesi, GitHub anahtarı alma yardımcısı.


---

## [2.24.5] — 2026-07-28

### Eklendi
- **Saha Kurulum Aracı (GUI)** — cihaza SSH ile bağlanıp kurulum/güncelleme/
  kaldırma işlemlerini tek ekrandan yapar, çıktıyı canlı gösterir.
  (`tools/installer-gui`)
- **Debian paketi** — müşteriye giden dağıtım biçimi; uygulama kaynak kodu
  içermez. Her PR'da üretilip temiz bir konteynerde kurularak doğrulanır.
- **Kurulum dosyası üreticisi** — anahtarlar depoda durmadan tek dosyalık
  kurulum scripti üretir (`packaging/make-provisioner.sh`).
- Uzaktan bakım VPN'i (Tailscale) ve saha kimliği (müşteri/saha) desteği.
- Sağlıklı olmayan altyapı servisi için otomatik onarım ve tek dosyalık
  teşhis raporu.

### Düzeltildi
- **Oturum kaydı hiç oluşmuyordu** (`_timedelta` yazım hatası): her girişte
  `NameError` yutuluyor, "Aktif Oturumlar" boş kalıyor ve oturum sonlandırma
  çalışacak kayıt bulamıyordu.
- **Postgres parola şifreleme uyumsuzluğu** (MD5 ↔ SCRAM): kurulum
  "parola hizalandı" deyip TCP girişinde reddediliyordu. Artık otomatik
  onarılıyor.
- `.env` şablonu CRLF ile geliyordu; Linux'ta değerlere satır sonu karakteri sızıyordu.
- Paket kurulumunda `install.sh`/`update.sh` git yokluğunda ölüyordu.

### Değişti
- **Kurulum tamamen sessiz** — hiçbir soru sorulmuyor; tüm girdiler kurulum
  aracından geliyor. systemd kaydı artık koşulsuz (atlanırsa cihaz yeniden
  başlatıldığında ayağa kalkmıyordu).
- Anahtarı depoda tutan yol tamamen kaldırıldı; depoda canlı sır yok.
- Saha cihazları bir dalın ucunu değil, yayınlanmış bir **tag**'i takip eder.
  Kurulum imajları **indirir**, cihazda derlemez.
- Sürümün tek kaynağı kök dizindeki `VERSION` dosyası.

### Altyapı
- GitHub Actions ile CI: frontend build, backend ruff+pytest, alembic tek-head
  kontrolü, shellcheck, compose doğrulama, sürüm tutarlılığı, Debian paketi.
- Tag ile tetiklenen release hattı: imajlar CI'da derlenip GHCR'a basılır.
- `update.sh --version X.Y.Z` ile belirli bir sürüme geçiş ve **geri alma**.

---

## [2.24.4] — 2026-07-28

Bu sürüm ve öncesi için ayrıntı: `git log`. Değişiklik günlüğü bu sürümden
itibaren tutulmaya başlandı.
