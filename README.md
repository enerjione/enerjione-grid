# Horstman Smart Logger Platform

**Version:** 2.24.4
Industrial monitoring platform for Horstmann Smart Navigator 2.0 devices.
Iki dağıtım modu:

- **Production / Linux + Docker** — VDS, sunucu kurulumları (asağıdaki bölüm).
- **Geliştirici / Windows native** — masaüstünde IDE ile hızlı iterasyon.

---

## Production: Linux + Docker (VDS / sunucu)

Tek komutla ayağa kalkar. Ubuntu 22.04 / 24.04 ve Debian 12 üzerinde test edilmiştir.

### 1. VDS'e Docker kur (yeni sunucu ise)

```bash
sudo bash infra/scripts/linux/install-docker.sh
# Eger sudo kullanicisini docker grubuna eklediyse, tekrar SSH ile gir.
```

### 2. Repo'yu çek ve bootstrap

```bash
git clone https://github.com/<KULLANICI>/horstman-smart-logger.git
cd horstman-smart-logger
sudo bash infra/scripts/linux/bootstrap.sh
```

`bootstrap.sh` şunları yapar:
- `.env.example`'dan `.env` üretir; `SECRET_KEY`, `INTERNAL_SERVICE_TOKEN`,
  `POSTGRES_PASSWORD`, `RABBITMQ_PASSWORD` değerlerini rastgele üretir.
- Tüm imajları build eder (`docker compose build`).
- Servisleri ayağa kaldırır (`docker compose up -d`).
- backend-api hazır olana kadar bekler.

### 3. Default installer hesabini oluştur

```bash
docker compose exec backend-api python -m scripts.seed_installer
```

Çıktı:
```
Installer user created (username=installer, password=ChangeMe123!).
```

### 4. Browser'dan aç

```
http://<vds-ip>/
```

Kullanıcı: `installer` / Şifre: `ChangeMe123!` — **ilk girişte mutlaka değiştir.**

### Servisler ve portlar

| Servis | Public port | Açıklama |
|---|---|---|
| frontend-web (nginx) | **80** | SPA + `/api/*` reverse proxy |
| backend-api | — | Sadece compose network'unde |
| postgres | — | Sadece compose network'unde |
| rabbitmq AMQP | — | Sadece compose network'unde |
| rabbitmq Management UI | 127.0.0.1:15672 | SSH tüneliyle erişim |
| iec104-outbound | 2404, 2405, 2406 | Dış SCADA master bağlantısı |

### Yaygın komutlar

```bash
docker compose ps                              # durum
docker compose logs -f backend-api             # log akışı
docker compose restart backend-api             # servis restart
docker compose down                            # tümünü durdur (volume'ler kalir)
docker compose down -v                         # volume'leri de sil (DB silinir!)
docker compose pull && docker compose up -d    # imajlari guncelle
```

### HTTPS (opsiyonel, domain varsa)

VDS önüne Caddy/Traefik/Cloudflare koyup `:80`'e proxy edin. Caddy örneği:

```caddy
hsl.example.com {
    reverse_proxy localhost:80
}
```

---

## Geliştirici: Windows native (IDE ile hızlı iterasyon)

## Tek Tıkla Başlatma

Servis Kontrol Paneli artık tamamen GUI odaklı:

```powershell
py -3.10 "infra/scripts/windows/service_control_panel.py"
```

Önerilen ilk çalıştırma sırası (hepsi panelden, CMD kullanmadan):

1. **Kurulum** sekmesi → *Tüm Bağımlılıkları Kur* (pip + npm install)
2. **Kurulum** sekmesi → *Kurulumcu (Installer) Hesabı Oluştur / Sıfırla*
3. **Kurulum** sekmesi → *Varsayılan Sinyalleri Seed Et*
4. **Hızlı Aksiyonlar** → *Akıllı Başlat (sıralı)*

Panel özellikleri: arka plan thread'lerde non-blocking aksiyonlar, child
process tree'yi `taskkill /T /F` ile düzgün kapatma, servis başına 500 satırlık
canlı log penceresi ve `CREATE_NO_WINDOW` ile görünmez PowerShell çağrıları.
Detay için bkz. `infra/scripts/windows/SERVICE_CONTROL_PANEL.md`.

## Roller (RBAC)

| Yetki | operator | engineer | installer |
|---|:---:|:---:|:---:|
| Canlı izleme (harita + tablo) | ✓ | ✓ | ✓ |
| Alarm / event görüntüleme + onay / reset | ✓ | ✓ | ✓ |
| Cihaz ekle / çıkar / güncelle | — | ✓ | ✓ |
| Gateway ekle / düzenle / sil | — | — | ✓ |
| Sinyal kataloğu (DNP3 adresleri, scale, supports_alarm) | — | — | ✓ |
| Alarm kuralları (eşik / hysteresis / debounce) | — | — | ✓ |
| Kullanıcı yönetimi (operatör / mühendis) | — | ✓ | ✓ |
| Kullanıcı yönetimi (kurulumcu atama) | — | — | ✓ |
| Outbound hedefleri (REST / MQTT) | — | — | ✓ |
| Bildirim ayarları (SMTP / SMS) | — | — | ✓ |

- **operator**: yalnızca canlı izleme ve alarm ack/reset yapar.
- **engineer**: sistemi basitçe genişletip daraltır; cihaz ekler/kaldırır. **Operatör ve mühendis** kullanıcılarını yönetebilir; kurulumcu hesaplarını göremez/müdahale edemez ve kimseye kurulumcu rolü atayamaz. Gateway/sinyal kataloğu/alarm kuralları/bildirim ve outbound ayarlarını değiştiremez.
- **installer** (süper admin): tüm altyapı, şablon ve parametre kurgusunu yönetir. Tüm rollerde (operator / engineer / installer) kullanıcı oluşturup silebilir. Backend güvenlik gereği kullanıcı kendi hesabını silemez.

## Structure

- `apps/frontend-web`: React + TypeScript operator UI (Anasayfa, Alarmlar, Olaylar, **Sistem durumu** özeti, mühendislik)
- `apps/backend-api`: FastAPI central backend (auth + signal catalog + alarm rules + IEC 104 / outbound)
- `apps/tag-engine`: Tag processing microservice (raw telemetri → normalize)
- `apps/alarm-service`: Alarm evaluation microservice (kural bazlı eşik/debounce)
- `apps/notification-worker`: Notification microservice (SMTP / Telegram / SMS)
- `packages/shared-contracts`: shared payload contracts
- `infra/scripts`: Windows/Linux service scripts
- **DNP3 Gateway** ayrı repodadır: `Horstmann Smart Logger DNP3 Gateway/` — uzak sunucuda (şube/saha)
  çalıştırılan standalone Python servisi. Backend'den `/gateways/{code}/config` ile cihaz + sinyal
  listesini çeker, RabbitMQ `telemetry.raw_received` routing key'i ile yayın yapar.

## Veri akışı (özet)

```
[Uzak] dnp3-gateway  --(telemetry.raw_received)-->  tag-engine  --(telemetry.received)-->  alarm-service
       |                                                                                         |
       +--- GET /gateways/{code}/config (signal list + device list) --- backend-api              |
                                                                            ^                    |
                                                                            +-- GET /internal/alarm-rules
                                                                            |
                                         IEC 104 / MQTT / REST / Modbus / OPC UA  <-- outbound dispatch
```

- Sinyal kataloğu tüm cihazlar için ortaktır; cihaz eklendiğinde otomatik uygulanır.
- Alarm kuralları `signal_key` bazlı template'dir; `supports_alarm=True` olan sinyalde değerlendirilir.
- `device_code_filter` alanı virgülle ayrılmış cihaz kodları ile kuralın kapsamını daraltır (boş = tüm cihazlar).

## Uzak Gateway Yönetimi

Gateway'ler farklı sunucularda (şubelerde/sahalarda) çalıştırılmak üzere tasarlandı.
Kontrol paneli artık lokal `config.json` yerine **backend'deki gateway kayıtlarını**
kaynak alır:

1. Frontend → *Mühendislik → Gateway Yönetimi* ekranından gateway eklenir. Yeni
   alanlar: `Kontrol Host` ve `Kontrol Port` (uzak makinanın IP'si ve gateway'in
   health portu).
2. Uzak sunucuya **ayrı DNP3 Gateway repo'su** (`Horstmann Smart Logger DNP3 Gateway`)
   kurulur; `.env`'de `GATEWAY_CODE`, `GATEWAY_TOKEN`, `BACKEND_API_URL`,
   `WORKER_HEALTH_HOST`, `WORKER_HEALTH_PORT`, `RABBITMQ_URL`, `GATEWAY_MODE=dnp3`
   ayarlanıp Windows Service veya systemd ile çalıştırılır.
3. Kontrol Paneli → *Gateway Yönetimi* sekmesi backend'den listeyi çeker ve
   her gateway için kontrol adresi, TCP sağlık durumu ve son görülme zamanını
   gösterir. **Başlat / Durdur / Yeniden Başlat** butonları backend'in
   `is_active` bayrağını değiştirir; uzak gateway bir sonraki konfig
   refresh'te bu bilgiyi görüp polling'i askıya alır ya da devam ettirir
   (proses ayakta kalır, komut kaybolmaz).

Panel backend'e **kişisel kullanıcı login'i yapmadan** bağlanır; backend'in
`INTERNAL_SERVICE_TOKEN` değeri ile eşleşen servis token'ını kullanır. Bu
token `service_control_panel.config.json` içindeki `backend.service_token`
alanında tutulur ve backend `.env` dosyasında da aynı değerle tanımlanmalıdır.

> Not: Durmuş bir gateway prosesini **sıfırdan başlatmak** sunucu tarafında
> kurulu bir supervisor (Windows Service / systemd) ister. Panel bu durumda
> yalnızca `is_active` flag'ini `true` yapar; supervisor ayağa kalkınca
> gateway zaten flag'i görüp yayına devam eder.

## Sinyal Yönetimi ve Canlı Değerler (v2.21.0+)

Mühendislik menüsü, sinyal ve telemetri verilerini net biçimde ayırır:

- **Sinyaller** (yalnızca installer): Sinyal kataloğu sadece tanım/parametre
  yönetimi içindir (etiket, DNP3 adres, scale, alarm desteği vb.). Canlı
  değerler bu sayfada gösterilmez.
  - Sayfa üst sekmeler ile veri tipine göre bölünmüştür:
    `Analog Input`, `Analog Output`, `Binary Input`, `Binary Output`,
    `Counter`, `String`. Her sekmede **tablo şeklinde liste** (etiket, kaynak,
    tip, adres, birim, özellik sütunları) ve sağ tarafta **geniş düzenleme
    paneli** bulunur. DNP3 adres ve ölçeklendirme alanları ayrı fieldset'lerde
    gruplandı.
  - Backend her başlangıçta **strict seed** yapar: JSON'da olmayan kayıtlar
    (eski mock/test sinyalleri) otomatik temizlenir; listedeki tanımlarla
    DB birebir eşitlenir. Bu nedenle kurulumcu UI'da ayrıca *Sinyal Ekle*
    düğmesi yer almaz — tüm katalog `horstmann_sn2_signals.json` tarafından
    yönetilir.
- **Canlı Değerler** (engineer + installer): Her cihaz için **aktif sinyal
  kataloğundaki** tüm sinyallere bir satır açılır; telemetri geldikçe değer
  ve kalite dolar, gelmeden önce `—` gösterilir. Veri tipine göre sekmeler,
  arama ve araç çubuğundaki "Yenile" vardır. Ana dashboard'un "Tablo" sekmesi
  aynı sayfayı kullanır.

## First Run (Development)

Önerilen yol Servis Kontrol Paneli (yukarıda). Yine de manuel çalıştırmak isteyenler için:

### Backend

1. Install Python 3.10
2. `cd apps/backend-api`
3. `pip install -r requirements.txt`
4. `uvicorn app.main:app --reload --port 8000`

### Frontend

1. Install Node.js LTS
2. `cd apps/frontend-web`
3. `npm install`
4. `npm run dev`

### DNP3 Gateway (ayrı repo)

1. `cd ../Horstmann\ Smart\ Logger\ DNP3\ Gateway`
2. `py -3.10 -m venv .venv && .venv\Scripts\activate`
3. `pip install -r requirements.txt` (+ `pip install nfm-dnp3` için gerçek cihaz modu)
4. `.env` düzenle (`GATEWAY_CODE`, `GATEWAY_TOKEN`, `BACKEND_API_URL`, `RABBITMQ_URL`)
5. `run_gateway.cmd` veya `python -m dnp3_gateway`
