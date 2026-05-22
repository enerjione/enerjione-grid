# Host-Level Nginx Reverse Proxy

Aynı VPS üzerinde birden fazla web uygulamasını (örn. EnerjiOne Grid +
EnerjiOne Solar) subdomain bazlı çalıştırmak için host'a kurulan nginx
reverse proxy konfigürasyonu.

## Mimari

```
DNS:
  grid.enerjione.com  → VPS IP
  solar.enerjione.com           → VPS IP

VPS host:
  nginx (port 80, opsiyonel 443)
    ├─ server_name grid.enerjione.com  → 127.0.0.1:8080  (EnerjiOne frontend)
    └─ server_name solar.enerjione.com → 127.0.0.1:8081  (Solar frontend)

Docker stack'leri:
  /opt/enerjione-grid → container frontend-web :8080 bind localhost
  /opt/solar     → container frontend-web :8081 bind localhost
```

## Kurulum Adımları (VPS)

### 1. DNS kayıtları
DNS panelinde her iki subdomain için A kaydı:
```
grid    A  77.83.37.44     → grid.enerjione.com
solar   A  77.83.37.44     → solar.enerjione.com
```

### 2. Host nginx kur
```bash
sudo apt update
sudo apt install -y nginx
```

### 3. Mevcut EnerjiOne'ı localhost'a çevir
`/opt/enerjione-grid/.env` dosyasına ekle:
```
FRONTEND_HTTP_PORT=127.0.0.1:8080
```
Sonra:
```bash
cd /opt/enerjione-grid && docker compose up -d frontend-web
```

### 4. Solar'ı kur (eğer henüz değilse)
```bash
sudo git clone https://github.com/<USER>/EnerjiOneSolar.git /opt/solar
cd /opt/solar && ./install.sh
```
Solar zaten `127.0.0.1:8081` bind ile gelir.

### 5. Nginx config dosyalarını yerleştir
Bu dizindeki `enerjione-grid.conf` ve `solar.conf` dosyalarını VPS'e kopyala:
```bash
sudo cp enerjione-grid.conf /etc/nginx/sites-available/enerjione-grid
sudo cp solar.conf /etc/nginx/sites-available/solar

sudo ln -sf /etc/nginx/sites-available/enerjione-grid /etc/nginx/sites-enabled/
sudo ln -sf /etc/nginx/sites-available/solar /etc/nginx/sites-enabled/

# Default config'i devre dışı bırak
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t      # syntax check
sudo systemctl reload nginx
```

### 6. SSL (Let's Encrypt) — opsiyonel ama önerilen
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d grid.enerjione.com -d solar.enerjione.com
# Otomatik renewal cron: certbot kendi kuruyor
```

## Test

```bash
curl -H "Host: grid.enerjione.com" http://localhost
curl -H "Host: solar.enerjione.com" http://localhost
```

İkisi de 200 dönüyorsa nginx config doğru, subdomain routing çalışıyor.

## Notlar

- WebSocket için `Upgrade` header'ı proxy edilir (canli telemetri için kritik).
- `client_max_body_size 100M` — yedek upload + cert upload icin yeterli.
- Backend healthcheck nginx'i bypass eder; her uygulama kendi container
  içinde direkt 8000 portuna check yapar, host nginx down olsa bile
  containerlar çalışır.
