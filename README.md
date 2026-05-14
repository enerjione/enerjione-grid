# EnerjiOne Grid

**Endüstriyel Akıllı Şebeke İzleme Platformu** — Horstmann Smart Navigator 2.0 arıza-geçiş göstergesi cihazları için açık kaynak izleme/yönetim platformu.

> 🌐 **Web:** `https://enerjione-grid.fikretsafak.com.tr`
> 📦 **Repo:** [github.com/fikretsafak/EnerjiOneGrid](https://github.com/fikretsafak/EnerjiOneGrid)
> 📅 **Sürüm:** 2.24.4

---

## 🚀 Hızlı Kurulum (Linux VPS)

**Tek komutla sıfırdan ayağa kalkar.** Test edildi: Ubuntu 22.04/24.04, Debian 12.

```bash
curl -fsSL https://raw.githubusercontent.com/fikretsafak/EnerjiOneGrid/docker-linux-deploy/install.sh | sudo bash
```

Veya manuel:

```bash
sudo git clone --branch docker-linux-deploy \
  https://github.com/fikretsafak/EnerjiOneGrid.git /opt/enerjione-grid
cd /opt/enerjione-grid
sudo bash install.sh
```

Kurulum sonrası:
- 🌐 Web: `http://<VPS-IP>/`
- 👤 İlk giriş: `installer` / `ChangeMe123!` _(mutlaka değiştir)_

---

## 🎛️ Yönetim Komutları

### systemd ile (önerilen)

Install script kurulum sonrası "systemd kaydı yapayım mı?" diye sorar. Onaylarsan:

```bash
sudo systemctl start enerjione-grid      # başlat
sudo systemctl stop enerjione-grid       # durdur
sudo systemctl restart enerjione-grid    # yeniden başlat
sudo systemctl status enerjione-grid     # durum
sudo systemctl enable enerjione-grid     # boot'ta otomatik başlat
sudo journalctl -u enerjione-grid -f     # canlı log
```

Sonradan eklemek:
```bash
sudo bash /opt/enerjione-grid/infra/systemd/setup-systemd.sh
```

### Docker Compose ile (alternatif)

```bash
cd /opt/enerjione-grid

# Stack yönetimi
sudo docker compose up -d              # başlat
sudo docker compose down               # durdur (container'lar silinir, volume korunur)
sudo docker compose restart            # yeniden başlat
sudo docker compose ps                 # container durumu
sudo docker compose logs -f            # canlı log (tüm servisler)
sudo docker compose logs -f backend-api  # tek servis

# Servis güncelleme
sudo bash update.sh                    # tüm servisler
sudo bash update.sh backend            # sadece backend-api
sudo bash update.sh frontend           # sadece frontend-web
sudo bash update.sh alarm              # alarm-service
# diğerleri: tag / notification / iec

# Kaldırma
sudo bash uninstall.sh                 # interaktif onay
sudo bash uninstall.sh --yes           # onay atla
sudo bash uninstall.sh --keep-images   # image'lar korunur
sudo bash uninstall.sh --purge-dir     # /opt/enerjione-grid'i de sil
```

---

## 🌍 Multi-Domain (Birden Fazla Uygulama)

Aynı VPS'te EnerjiOne Grid + EnerjiOne Solar gibi birden fazla uygulama yan yana çalıştırılabilir.

### 1. DNS ayarları
Her uygulama için subdomain A kaydı:
```
enerjione-grid     A  <VPS-IP>
enerjione-solar    A  <VPS-IP>
```

### 2. Grid'i localhost'a bind et
```bash
cd /opt/enerjione-grid
sed -i 's|^FRONTEND_HTTP_PORT=.*|FRONTEND_HTTP_PORT=127.0.0.1:8080|' .env
sudo systemctl restart enerjione-grid
```

### 3. Host nginx kur
```bash
sudo bash /opt/enerjione-grid/infra/host-nginx/setup-host-nginx.sh
```

Bu script:
- Sistem nginx'i kurar
- `enerjione-grid.fikretsafak.com.tr` → `127.0.0.1:8080` proxy
- `enerjione-solar.fikretsafak.com.tr` → `127.0.0.1:8081` proxy
- WebSocket upgrade + uzun timeout'lar yapılandırılır

### 4. SSL (Let's Encrypt)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx \
  -d enerjione-grid.fikretsafak.com.tr \
  -d enerjione-solar.fikretsafak.com.tr
```

Otomatik yenileme cron'ı certbot kendisi kurar (her 12 saatte bir dener).

---

## 🏗️ Mimari

```
┌──────────────────────────────────────────────────────┐
│  Frontend (Vite + React + TypeScript)                │
│  nginx:80 → ports/8080                               │
└──────────────────────────────┬───────────────────────┘
                               │ /api/v1/...
┌──────────────────────────────▼───────────────────────┐
│  Backend API (FastAPI + SQLAlchemy)                  │
│  uvicorn:8000                                        │
└──┬──────────┬─────────────┬────────────┬─────────────┘
   │          │             │            │
   ▼          ▼             ▼            ▼
┌─────┐  ┌────────┐    ┌────────┐   ┌──────────┐
│ PG  │  │RabbitMQ│    │ NATS   │   │ Workers  │
│ 16  │  │management│  │JetStream│   │ (4 adet) │
└─────┘  └────────┘    └────────┘   └──────────┘
                                     ├─ tag-engine
                                     ├─ alarm-service
                                     ├─ notification-worker
                                     └─ iec104-outbound
```

### Container namespace
Tüm container'lar `e1-grid-` prefix ile gelir (örn: `e1-grid-backend-api`).
Solar yan-yana çalıştırılırsa `e1s-*` namespace kullanır — çakışma yok.

| Element | Değer |
|---|---|
| Compose project | `enerjione-grid` |
| Image prefix | `e1-grid/<service>:latest` |
| Container prefix | `e1-grid-<service>` |
| Volume prefix | `enerjione-grid_<name>` |
| Network | `enerjione-grid_e1-net` |
| DB adı | `enerjione_grid` |
| Default dizin | `/opt/enerjione-grid` |

---

## 💻 Geliştirici Modu (Windows / Mac / Linux native)

Backend ve frontend ayrı olarak local'de çalıştırılır — IDE ile hızlı iterasyon.

### Backend (Python 3.11+)
```bash
cd apps/backend-api
python -m venv .venv
.venv\Scripts\activate         # Windows
source .venv/bin/activate      # Linux/Mac

pip install -e .
cp .env.example .env
# .env'i düzenle (DATABASE_URL local postgres'e işaret etmeli)

python -m uvicorn app.main:app --reload --port 8000
```

### Frontend (Node 20+)
```bash
cd apps/frontend-web
npm install
npm run dev
# Vite dev server :5173, API'yi http://localhost:8000/api/v1'e proxy eder
```

### Test
```bash
# Backend testleri
cd apps/backend-api && pytest

# Frontend type check
cd apps/frontend-web && npx tsc --noEmit
```

---

## 📋 Özellikler

### Mühendislik Modülleri
- **Cihaz Yönetimi**: DNP3 protokolü, gateway tabanlı topoloji
- **Sinyal Yönetimi**: Analog/digital sinyal mapping, ölçeklendirme
- **Alarm Kuralları**: Eşik, dV/dt, AND/OR bileşik mantık
- **Hat Yönetimi**: Bölge → Hat → Direk → Cihaz hiyerarşisi, harita üzerinde
- **Webhook & Outbound**: REST/MQTT/IEC104 dış sistem entegrasyonu
- **API Erişimi**: PAT (Personal Access Token) yönetimi
- **Bildirim Ayarları**: SMTP, SMS (Twilio/Netgsm), Telegram
- **Proje Ayarları**: Logo, dil, batarya eşikleri
- **Yedekler**: Manuel + zamanlanmış DB yedeği, restore

### İzleme & Operasyon
- **Anasayfa**: Cihaz haritası + sol listede tüm cihazlar (hat atanmamış olanlar ayrı pill ile)
- **Alarmlar**: Kategori/seviye filtresi, atama, yorum, sıfırlama
- **Hat Arızaları**: Son kırmızı → ilk yeşil aralığı haritada vurgulama
- **Olaylar**: Audit log, kategori/öncelik filtresi
- **Sistem Durumu**: CPU/RAM/disk/uptime + servis sağlık probe'ları
- **Toplu Bildirim**: Wizard ile çoklu kullanıcı/ekibe duyuru (web push + email + SMS)

### Roller
| Rol | Yetkiler |
|---|---|
| `installer` | Süper admin — her şey |
| `engineer` | Mühendis — installer dışı her şey, yedek geri yükleme YOK |
| `ops_manager` | Operasyon Yöneticisi — kullanıcı (sadece operator) + ekip yönetimi + toplu bildirim |
| `operator` | Saha personeli — alarm/arıza görüntüleme, yorum/atama kabul |

---

## 🔧 Yapılandırma

### .env

Önemli alanlar (kurulum scripti rastgele üretir):

```bash
# Auth
SECRET_KEY=<random>                    # JWT secret
INTERNAL_SERVICE_TOKEN=<random>        # backend ↔ worker token

# Veritabanı
POSTGRES_DB=enerjione_grid
POSTGRES_USER=enerjione_grid
POSTGRES_PASSWORD=<random>

# Mesaj kuyrukları
RABBITMQ_PASSWORD=<random>
NATS_BACKEND_PASSWORD=<random>
NATS_WORKER_PASSWORD=<random>
NATS_GATEWAY_PASSWORD=<random>

# Frontend bind
FRONTEND_HTTP_PORT=80                  # multi-app: 127.0.0.1:8080

# CORS
CORS_ORIGINS=http://localhost,http://127.0.0.1,https://enerjione-grid.fikretsafak.com.tr

# SMTP (opsiyonel)
SMTP_ENABLED=false
SMTP_HOST=
SMTP_FROM_EMAIL=noreply@enerjione-grid.local

# Backup retention
BACKUP_RETENTION_COUNT=30
```

### FCM (Mobil Push)
Firebase Console > Project Settings > Service Accounts > Generate new private key
İndirdiğin JSON'u `/opt/enerjione-grid/fcm-service-account.json` olarak kaydet.
```bash
sudo bash update.sh backend
```

---

## 🆘 Sorun Giderme

### Container ayağa kalkmıyor
```bash
sudo docker compose logs <service>
sudo systemctl status enerjione-grid
sudo journalctl -u enerjione-grid -n 100
```

### DB bağlantı sorunu
```bash
docker exec -it e1-grid-postgres psql -U enerjione_grid -d enerjione_grid -c '\l'
```

### Disk dolu
```bash
docker system df
docker system prune -af --volumes   # eski/dangling temizliği
```

### Cihazlar çevrimdışı görünüyor
1. Gateway'lerin çalıştığını kontrol et (saha cihazları)
2. Backend log: `sudo docker compose logs -f backend-api`
3. Alarm/Tag service worker'ları: `sudo docker compose logs -f alarm-service tag-engine`

---

## 📞 İletişim

- **Geliştirici:** Fikret Şafak
- **Şirket:** [Form Elektrik](https://www.formelektrik.com.tr)
- **Issue tracker:** [GitHub Issues](https://github.com/fikretsafak/EnerjiOneGrid/issues)

---

## 📜 Lisans

Form Elektrik İnş.Müh.A.Ş. mülkiyeti. Tüm hakları saklıdır.
