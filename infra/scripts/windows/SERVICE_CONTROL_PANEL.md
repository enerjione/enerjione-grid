# Service Control Panel (Windows)

Bu panel; altyapı servisleri + Python microservice'leri + gateway instance'larını
**tek pencereden, CMD kullanmadan** yönetmek için tasarlanmıştır.

## Yönetilen Servisler

- PostgreSQL (Windows service)
- RabbitMQ (Windows service)
- Backend API (FastAPI process)
- Tag Engine Service (process)
- Alarm Service (process)
- Notification Service (process)
- Frontend Web (Vite dev server)

Gateway'ler ayrı **Gateway Yönetimi** sekmesinde tutulur ve **backend'deki
kayıtlardan** otomatik listelenir. Gateway'in lokalde ya da farklı sunucuda
olmasının bir önemi yoktur; kontrol paneli uzak gateway'leri `control_host` +
`control_port` alanlarını kullanarak izler ve `is_active` bayrağı üzerinden
uzaktan başlatır/durdurur.

## Dosyalar

- `infra/scripts/windows/service_control_panel.py`
- `infra/scripts/windows/service_control_panel.config.json`

## Çalıştırma

1. `service_control_panel.config.json` içindeki yolları kendi ortamına göre
   kontrol et (özellikle `working_dir` alanları).
2. `windows_service_name` alanlarının makinedeki servis isimleri ile **bire bir
   aynı** olduğundan emin ol (örn. `postgresql-x64-16`, `RabbitMQ`).
3. Paneli başlat:

```powershell
py -3.10 "infra/scripts/windows/service_control_panel.py"
```

İlk kullanımda şu sırayı öneririz:

1. **Kurulum** sekmesi → *Tüm Bağımlılıkları Kur* (pip + npm install hepsi)
2. **Kurulum** sekmesi → *Kurulumcu (Installer) Hesabı Oluştur / Sıfırla*
3. **Kurulum** sekmesi → *Varsayılan Sinyalleri Seed Et*
4. **Hızlı Aksiyonlar** → *Akıllı Başlat (sıralı)*

## Sekmeler

### Temel Servisler
Core microservice'ler. Her satırda `Başlat`, `Durdur` ve `Yeniden Başlat`
butonları var.

### Gateway Yönetimi
Web uygulamasındaki gateway listesinin aynısı backend üzerinden çekilir; yeni
kayıt web arayüzünde eklenir. Tabloda sade sütunlar vardır:

- **Gateway** — ad ve kod birlikte
- **Uzak adres** — izleme/kontrol için IP:port (yoksa “uzaktan izleme kapalı”)
- **Veri toplama** — açık / duraklatıldı
- **Uzak erişim** — kontrol portuna erişim var mı (evet/hayır; port yoksa —)
- **Son görülme** — son görülen zaman (veya —)

**Başlat / Durdur / Yeniden Başlat** ile seçili kayıt uzak tarafta (birkaç
saniye içinde) uygulanır. Panel, çalışan collector proseslerini açıp
kapamaz; sadece web’le aynı “veri toplama açık mı” bilgisini değiştirir.

> Durmuş bir collector’u makinede ayağa kaldırmak için ayrıca supervisor
> veya servis yönetimi gerekir.

Liste yaklaşık 15 sn’de bir yenilenir; **Yenile** ile de anlık
yenilenebilir. Sunucuya ulaşılamazsa kısa bir hata metni üstte görünür.

### Kurulum
CMD'e gerek kalmadan:
- Her servis için `pip install -r requirements.txt`
- Frontend için `npm install`
- Kurulumcu hesabı oluştur / şifresini sıfırla (`scripts/seed_installer.py`)
- Varsayılan Horstmann SN2 sinyallerini seed et

Her görev arka planda çalışır; çıktıyı `Çıktıyı Göster` ile canlı takip edebilirsin.

### Olay Günlüğü
Tüm başlatma, durdurma, yeniden başlatma, sağlık değişimi ve hata olayları
merkezi bir zaman çizelgesinde görünür. Sütunlar: `Zaman`, `Seviye`
(INFO/OK/WARN/ERROR), `Kaynak` (ilgili servis adı ya da "Panel"), `Mesaj`.
- `Otomatik aşağı kaydır` açıkken en son olay her zaman ekranda kalır.
- `Temizle` tüm satırları siler.
- `Dışa Aktar` günlüğü `olay_gunlugu_YYYYMMDD_HHMMSS.txt` olarak kaydeder —
  sistemi başkasıyla paylaşırken veya destek talebi açarken kullanışlıdır.

Durum satırında görünen her bildirim otomatik olarak Olay Günlüğü'ne de
düşer; ayrıca arka plan poll döngüsü her servisin durum/sağlık değişimini
de bu sekmeye kaydeder.

## Performans Notları (v2.18.0+)

- **Tüm aksiyonlar arka plan thread'inde** çalışır — UI asla donmaz.
- **Akıllı Başlat**: Windows servisleri (PostgreSQL, RabbitMQ) yalnızca
  `STOPPED` ise başlatılmaya çalışılır; zaten çalışıyorlarsa dokunulmaz.
  Admin yetki yoksa uyarı gösterilir ama akış durmaz — backend ve diğer
  servisler yine sırayla ayağa kaldırılır.
- **Toplu `Uygulamaları Durdur` / `Uygulamaları Yeniden Başlat` butonları
  PostgreSQL ve RabbitMQ'ya dokunmaz.** Bunlar altyapı kabul edilir ve
  sürekli açık kalması beklenir. İhtiyaç hâlinde satır bazlı `Durdur`
  butonu ile tek tek durdurulabilir (bu durumda PowerShell'i Admin olarak
  çalıştırmak gerekir).
- **`ÇALIŞIYOR (dış)` durumundaki servisler de durdurulabilir.** Panel
  kendi başlatmadığı (veya panel yeniden açılınca PID'i kaybolmuş)
  prosesleri `Get-NetTCPConnection` ile health port üzerinden bulup
  `taskkill /T /F` uygular. Bu sayede Tag Engine, Frontend vb. panel
  dışında başlatılmış servisler de tek tıkla durdurulur.
- **Child process tree** Windows'ta `taskkill /T /F /PID` ile kapatılır;
  `npm run dev` gibi alt proses spawn eden servisler bırakılmaz.
- **Ring-buffer log** her servis için son 500 satırı tutar; kurulum
  adımlarının `Çıktıyı Göster` penceresi canlı akar.
- **PowerShell çağrıları** hiçbir zaman console penceresi açmaz
  (`CREATE_NO_WINDOW`).

## Harici Olarak Kurulmuş Servisler (RabbitMQ / PostgreSQL)

Panel **RabbitMQ veya PostgreSQL'i kurmaz.** Bunları sen kurdun; panel
sadece Windows Service Manager'daki mevcut kayda `Start-Service` /
`Stop-Service` komutu gönderir. `service_control_panel.config.json`
içindeki `windows_service_name` alanı bu kaydın adıdır (örn. `RabbitMQ`,
`postgresql-x64-16`).

`Cannot open RabbitMQ service on computer '.'` gibi bir hata görürsen:

1. Paneli yönetici (Admin) olarak çalıştırdığından emin ol, **veya**
2. Bu servisleri Windows Service Manager'dan yönet ve paneldeki toplu
   durdur/yeniden başlat butonlarına güven — bunlar zaten RabbitMQ ve
   PostgreSQL'e dokunmuyor.

## Durum ve Sağlık Göstergeleri

- **Durum** alanı:
  - `ÇALIŞIYOR`: panel tarafından başlatılan process hâlâ açık.
  - `ÇALIŞIYOR (dış)`: servis ayakta ama bu process başka bir ortamdan (örn.
    el ile açılmış terminal, Windows Service Manager) başlatılmış.
  - `BAŞLATILIYOR…`: başlatma işlemi sürüyor (arka planda).
  - `DURDURULUYOR…`: durdurma işlemi sürüyor — toplu durdur veya **Durdur**
    sonrası; **başlatma ile karışmaz** (pending işlem tipi ayrı tutulur).
  - `DURDU` / `SERVİS BULUNAMADI`: port kapalı / Windows'ta servis kayıtlı değil.
- **Sağlık** alanı: belirtilen host/port için TCP connect testi
  (`UP` / `ERİŞİLEMİYOR`).

Health port'ları (temel servisler):

| Servis | Port |
|---|---|
| Backend API | 8000 |
| Tag Engine | 8011 |
| Alarm Service | 8012 |
| Notification Service | 8013 |
| Frontend Web | 5173 |

Gateway'lerin kontrol portu (WORKER_HEALTH_PORT) her gateway için uzak sunucuda
serbestçe seçilir ve frontend gateway ekleme formundaki `Kontrol Port` alanına
girilir.

## Yeni Gateway Eklemek (v2.20.0+)

Lokal `config.json`'a artık gateway yazmıyorsun. Akış şu:

1. **Frontend** (Mühendislik → Gateway Yönetimi) → *Gateway Ekle* ile yeni
   kayıt oluştur. Doldurulacak kritik alanlar:
   - `Kod`, `Gateway Adı`, `DNP3 Host/Port`, `Gateway Token`
   - `Kontrol Host` → uzaktaki collector makinasının IP'si (örn. `10.10.10.30`)
   - `Kontrol Port` → o makinadaki `WORKER_HEALTH_PORT` (örn. `8020`)

2. **Uzak sunucuya** ayrı repo olan `Horstmann Smart Logger DNP3 Gateway` dağıt:
   ```
   GATEWAY_CODE=GW-003
   GATEWAY_TOKEN=<frontend'de verdiğin token>
   BACKEND_API_URL=http://<merkez-sunucu>:8000/api/v1
   RABBITMQ_URL=amqp://guest:guest@<merkez-sunucu>:5672/
   GATEWAY_MODE=dnp3
   WORKER_HEALTH_HOST=0.0.0.0
   WORKER_HEALTH_PORT=8020
   ```
   Sonra `py -3.10 -m dnp3_gateway` (veya `run_gateway.cmd`) ile ayağa kaldır
   (ya da Windows servisi olarak kaydet ki reboot sonrası otomatik başlasın).

3. **Kontrol Paneli** → *Gateway Yönetimi* sekmesini yenile. Yeni gateway
   listede görünür; Başlat/Durdur/Yeniden Başlat butonları backend'in
   `is_active` bayrağıyla uzaktan yönetilir.

### Backend bağlantı ayarları (v2.20.1+)

Panel artık kişisel bir kullanıcı hesabıyla login yapmaz; backend'in
`INTERNAL_SERVICE_TOKEN` değeri ile eşleşen servis token'ı üzerinden
`/internal/gateways` endpoint'lerine gider. Bu sayede:

- Kurulumcu şifresi değişse bile panel etkilenmez.
- Token backend `.env` dosyasında yönetildiği için production'da tek merkezden
  değiştirilir.

`service_control_panel.config.json` içindeki `backend` bloğu:

```json
"backend": {
  "base_url": "http://127.0.0.1:8000/api/v1",
  "service_token": "change-me-internal-token"
}
```

> Backend tarafında `.env` dosyasına `INTERNAL_SERVICE_TOKEN=...` satırı ekle
> ve bu değeri paneldeki `service_token` ile birebir aynı yap. Panel geçersiz
> token durumunda "Servis token'ı reddedildi" uyarısı gösterir.
