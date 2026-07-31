# Değişiklik Günlüğü

Bu dosya **yayınlanan sürümleri** özetler. Biçim [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/)
esaslıdır; sürümleme [SemVer](https://semver.org/lang/tr/).

Kayıt tutma kuralı: her `v*` tag'inden önce `[Yayınlanmamış]` başlığı altındaki
maddeler yeni sürüm başlığına taşınır. GitHub Release notları commit listesini
zaten otomatik üretir — buraya **kullanıcıyı etkileyen** değişiklikler yazılır,
her commit değil.

Türler: `Eklendi`, `Değişti`, `Düzeltildi`, `Kaldırıldı`, `Güvenlik`.

## [Yayınlanmamış]

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
