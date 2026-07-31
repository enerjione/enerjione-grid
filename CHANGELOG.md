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
