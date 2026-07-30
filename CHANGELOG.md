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
