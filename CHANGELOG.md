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
