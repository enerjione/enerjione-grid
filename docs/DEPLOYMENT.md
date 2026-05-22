# EnerjiOne Grid — VPS Deployment Rehberi

Sıfırdan production VPS kurulumu, host nginx reverse proxy, SSL ve multi-app
(EnerjiOne Solar yan-yana) deployment için kapsamlı rehber.

> **Hedef ortam:** Ubuntu 22.04/24.04 veya Debian 12, root/sudo erişimli VPS
> **Toplam süre:** ~20-30 dakika (DNS propagasyonu hariç)

---

## 📋 İçindekiler

1. [Ön Hazırlık](#1-ön-hazırlık)
2. [Tek-Komut Kurulum](#2-tek-komut-kurulum)
3. [systemd Servisi Olarak Kayıt](#3-systemd-servisi-olarak-kayıt)
4. [Host nginx Kurulumu](#4-host-nginx-kurulumu-subdomain-routing)
5. [SSL Sertifikası (Let's Encrypt)](#5-ssl-sertifikası-lets-encrypt)
6. [Multi-App: EnerjiOne Solar Yan-Yana](#6-multi-app-enerjione-solar-yan-yana)
7. [Yönetim Komutları](#7-yönetim-komutları)
8. [Güncelleme ve Yedekleme](#8-güncelleme-ve-yedekleme)
9. [Sorun Giderme](#9-sorun-giderme)

---

## 1. Ön Hazırlık

### 1.1 VPS gereksinimler

| Kaynak | Minimum | Önerilen |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 40 GB SSD | 80 GB SSD |
| OS | Ubuntu 22.04 | Ubuntu 24.04 |

### 1.2 DNS A kayıtları

DNS panelinde `enerjione.com` zone'una her uygulama için A kaydı ekle (sağlayıcı: GoDaddy, Cloudflare, Namecheap...):

```
grid     A    <VPS-IP>      TTL: 300   → grid.enerjione.com
```

İkinci uygulama (Solar) eklenecekse şimdiden hazırla:
```
solar    A    <VPS-IP>      TTL: 300   → solar.enerjione.com
```

Propagasyon kontrolü (5-30 dakika beklemen gerekebilir):
```bash
nslookup grid.enerjione.com
# Address: <VPS-IP> dönmeli
```

### 1.3 Firewall

```bash
sudo ufw allow 22/tcp     # SSH (zaten açık olmalı)
sudo ufw allow 80/tcp     # HTTP (nginx + Let's Encrypt challenge)
sudo ufw allow 443/tcp    # HTTPS
sudo ufw enable
sudo ufw status
```

---

## 2. Tek-Komut Kurulum

### 2.1 Otomatik (önerilen)

```bash
curl -fsSL https://raw.githubusercontent.com/fikretsafak/EnerjiOneGrid/docker-linux-deploy/install.sh | sudo bash
```

Script otomatik yapar:
1. Docker Engine + Compose plugin kurar (yoksa)
2. Repo'yu `/opt/enerjione-grid` altına klonlar
3. `.env` üretir (rastgele şifrelerle)
4. Image'ları build eder (`e1-grid/*:latest`)
5. Stack'i ayağa kaldırır
6. Default installer kullanıcısını yaratır
7. Kurulum sonunda **systemd kaydı yapmak isteyip istemediğini sorar** — `E` de.

### 2.2 Manuel adım adım

```bash
# 1. Repo klonla
sudo git clone --branch docker-linux-deploy \
  https://github.com/fikretsafak/EnerjiOneGrid.git \
  /opt/enerjione-grid

# 2. Install script çalıştır
cd /opt/enerjione-grid
sudo bash install.sh
```

### 2.3 Test

```bash
# Container'lar healthy mi?
sudo docker compose ps
# Tümü 'Up X minutes (healthy)' olmalı

# Frontend cevap veriyor mu?
curl -I http://localhost/
# HTTP/1.1 200 OK
```

Tarayıcıda `http://<VPS-IP>/` → giriş ekranı.

**İlk giriş bilgileri:**
- Kullanıcı: `installer`
- Şifre: `ChangeMe123!`

⚠️ **İlk girişten sonra şifreyi mutlaka değiştir** (zorunlu modal otomatik açılır).

---

## 3. systemd Servisi Olarak Kayıt

Eğer install.sh sırasında atladıysan sonradan da ekleyebilirsin:

```bash
sudo bash /opt/enerjione-grid/infra/systemd/setup-systemd.sh
```

Script:
- `/etc/systemd/system/enerjione-grid.service` yaratır
- `systemctl daemon-reload`
- `systemctl enable` (boot'ta otomatik başlat)
- `systemctl start`
- Status raporu basar

### 3.1 systemctl ile yönetim

```bash
sudo systemctl start enerjione-grid       # başlat
sudo systemctl stop enerjione-grid        # durdur (compose down)
sudo systemctl restart enerjione-grid     # yeniden başlat
sudo systemctl status enerjione-grid      # durum
sudo systemctl enable enerjione-grid      # boot'ta otomatik
sudo systemctl disable enerjione-grid     # boot'ta otomatik kapat
```

### 3.2 Log

```bash
sudo journalctl -u enerjione-grid -f       # canlı (Ctrl+C ile çık)
sudo journalctl -u enerjione-grid -n 100   # son 100 satır
```

### 3.3 Boot davranışı

systemd unit `Requires=docker.service After=docker.service` ile yapılandırılmış —
VPS reboot olduğunda:
1. systemd `docker.service`'i başlatır
2. Sonra `enerjione-grid.service` tetiklenir
3. `docker compose up -d` çağrılır
4. `restart: unless-stopped` policy ile container'lar zaten otomatik kalkar
   (çift güvence)

---

## 4. Host nginx Kurulumu (Subdomain Routing)

> Bu adımı **sadece subdomain üzerinden erişim** istiyorsan veya
> **multi-app deployment** yapacaksan uygula. Tek-uygulama VPS'te IP'den
> erişim yeterliyse atla.

### 4.1 Frontend'i localhost'a bind et

Şu an container `0.0.0.0:80` dinliyor. nginx 80'i alacak, container 8080'e iniyor:

```bash
cd /opt/enerjione-grid

# .env'de FRONTEND_HTTP_PORT'u güncelle
sudo sed -i 's|^FRONTEND_HTTP_PORT=.*|FRONTEND_HTTP_PORT=127.0.0.1:8080|' .env

# Frontend container'ı yeniden başlat (yeni bind ile)
sudo docker compose up -d frontend-web

# Doğrula
sudo lsof -i :80      # boş olmalı
sudo lsof -i :8080    # docker-pr LISTEN olmalı
curl -I http://127.0.0.1:8080/    # HTTP/1.1 200 OK
```

### 4.2 Host nginx kur

```bash
sudo bash /opt/enerjione-grid/infra/host-nginx/setup-host-nginx.sh
```

Script:
- `apt install nginx` (zaten kurulu değilse)
- `enerjione-grid.conf` + `solar.conf`'u `/etc/nginx/sites-available/` altına kopyalar
- `sites-enabled/` symlink'leri yapar
- Default config'i devre dışı bırakır
- `nginx -t` ile syntax check + `systemctl reload nginx`

Test:
```bash
curl -I http://grid.enerjione.com/
# HTTP/1.1 200 OK dönmeli
```

> ⚠️ Solar henüz kurulu değilse `solar.conf` 127.0.0.1:8081'e proxy yapacak,
> oradan 502 gelecek. Sorun değil; Solar deploy edildikçe çalışır.

---

## 5. SSL Sertifikası (Let's Encrypt)

### 5.1 Certbot kur

```bash
sudo apt update
sudo apt install -y certbot python3-certbot-nginx
```

### 5.2 Sertifika al

**Sadece Grid (Solar DNS hazır değilse):**
```bash
sudo certbot --nginx -d grid.enerjione.com
```

**Grid + Solar (DNS hazırsa tek komutta):**
```bash
sudo certbot --nginx \
  -d grid.enerjione.com \
  -d solar.enerjione.com
```

Certbot sorularına cevap:
1. **E-mail address:** Yenileme uyarıları için (`example@domain.com`)
2. **Terms of Service:** `A` (agree)
3. **EFF newsletter:** `N` (gerek yok)
4. **HTTP → HTTPS redirect:** **`2`** (Redirect — önerilen)

Certbot otomatik yapar:
- Cert'leri `/etc/letsencrypt/live/<domain>/` altına yazar
- nginx config'e `listen 443 ssl` + `ssl_certificate ...` ekler
- HTTP → HTTPS 301 redirect kuralı ekler
- `systemctl reload nginx`
- **Otomatik yenileme cron job kurar** (her 12 saatte bir; cert 30 günden eski ise yeniler)

### 5.3 Yenileme testi

```bash
sudo certbot renew --dry-run
# 'Congratulations, all simulated renewals succeeded' görmen lazım
```

### 5.4 Test

```bash
# HTTPS çalışıyor mu?
curl -I https://grid.enerjione.com/
# HTTP/2 200 dönmeli

# HTTP → HTTPS redirect?
curl -I http://grid.enerjione.com/
# HTTP/1.1 301 Moved Permanently
# Location: https://grid.enerjione.com/
```

Tarayıcıda `https://grid.enerjione.com` → 🟢 yeşil kilit + login ekranı.

### 5.5 CORS güncelle

Backend artık HTTPS origin'i tanımalı:

```bash
cd /opt/enerjione-grid
sudo sed -i 's|^CORS_ORIGINS=.*|CORS_ORIGINS=https://grid.enerjione.com,http://<VPS-IP>|' .env

# Backend container'ı yenile
sudo docker compose up -d backend-api
```

> `<VPS-IP>`'yi gerçek IP ile değiştir (örn. `77.83.37.44`).

---

## 6. Multi-App: EnerjiOne Solar Yan-Yana

Aynı VPS'te EnerjiOne Solar'ı da kurmak için:

### 6.1 Solar repo'sunu klonla

```bash
sudo git clone https://github.com/<USER>/EnerjiOneSolar.git /opt/enerjione-solar
cd /opt/enerjione-solar
sudo bash install.sh
```

Solar default olarak `127.0.0.1:8081`'e bind ediliyor (Grid'in 8080'i ile çakışmıyor).

### 6.2 nginx config zaten hazır

Host nginx setup script Solar config'i de yerleştirdi (`/etc/nginx/sites-available/solar`).
Solar deploy edildikten sonra otomatik çalışır.

### 6.3 SSL Solar için

Eğer SSL'i sadece Grid için aldıysan, Solar deploy sonrası ekle:

```bash
sudo certbot --nginx -d solar.enerjione.com
# Mevcut Grid cert'i etkilenmez
```

### 6.4 Test

```bash
curl -I https://solar.enerjione.com/
# HTTP/2 200
```

---

## 7. Yönetim Komutları

### 7.1 Hızlı referans

| Aksiyon | Komut |
|---|---|
| Durum | `sudo systemctl status enerjione-grid` |
| Başlat | `sudo systemctl start enerjione-grid` |
| Durdur | `sudo systemctl stop enerjione-grid` |
| Yeniden başlat | `sudo systemctl restart enerjione-grid` |
| Canlı log | `sudo journalctl -u enerjione-grid -f` |
| Container durumu | `cd /opt/enerjione-grid && sudo docker compose ps` |
| Tek servis log | `sudo docker compose logs -f backend-api` |
| Tüm log | `sudo docker compose logs -f` |
| Disk kullanımı | `sudo docker system df` |

### 7.2 Container içine gir

```bash
# Backend container shell
sudo docker exec -it e1-grid-backend-api bash

# Postgres CLI
sudo docker exec -it e1-grid-postgres psql -U enerjione_grid -d enerjione_grid

# RabbitMQ management UI (SSH tunnel)
ssh -L 15672:127.0.0.1:15672 fikretsafak@<VPS-IP>
# Tarayıcıda: http://localhost:15672
```

---

## 8. Güncelleme ve Yedekleme

### 8.1 Sürüm güncelleme

```bash
cd /opt/enerjione-grid
sudo git pull
sudo bash update.sh                # tüm servisler
sudo bash update.sh backend        # sadece backend-api
sudo bash update.sh frontend       # sadece frontend-web
# Diğerleri: alarm / tag / notification / iec
```

`update.sh` her seferinde:
1. Otomatik DB yedeği alır (`/opt/enerjione-grid/backups/`)
2. `docker compose up -d --build <service>` yapar
3. Sağlık check'ini bekler

### 8.2 Manuel yedek

```bash
# UI üzerinden: Mühendislik > Yedekler > Yedek Al
# CLI ile:
cd /opt/enerjione-grid
docker exec e1-grid-postgres pg_dump -U enerjione_grid -d enerjione_grid -Fc \
  > backups/manual-$(date +%Y%m%d-%H%M%S).dump
```

### 8.3 Yedekten geri yükle

UI: **Mühendislik > Yedekler > <dosya> > Geri Yükle** (installer rolü gerek)

CLI:
```bash
cd /opt/enerjione-grid
gunzip -c backups/<dosya>.dump.gz | \
  docker exec -i e1-grid-postgres pg_restore -U enerjione_grid -d enerjione_grid --clean --if-exists
sudo docker compose restart backend-api
```

### 8.4 Offsite yedek

`.env`'de `BACKUP_OFFSITE_DIR=/mnt/nas/backups/enerjione-grid` ayarla.
Her yedek otomatik oraya kopyalanır.

---

## 9. Sorun Giderme

### 9.1 Container ayağa kalkmıyor

```bash
sudo docker compose logs --tail 100 backend-api    # ya da hangi servis
sudo systemctl status enerjione-grid
sudo journalctl -u enerjione-grid -n 100
```

### 9.2 401 / oturum sürekli düşüyor

- Tarayıcı dev tools → Application → Cookies → `e1_session` cookie var mı?
- Çıkış yap → tarayıcıyı yenile → tekrar login
- Backend log: `sudo docker compose logs backend-api | grep auth_401`

### 9.3 DB bağlantı hatası

```bash
docker exec -it e1-grid-postgres psql -U enerjione_grid -d enerjione_grid -c '\l'
```

### 9.4 Disk dolu

```bash
sudo docker system df
sudo docker image prune -a       # kullanılmayan image'lar
sudo docker volume prune         # orphan volume'lar (DİKKAT: backup volume dahil)
```

### 9.5 nginx 502 Bad Gateway

```bash
# Container ayakta mı?
sudo docker compose ps

# Frontend localhost'tan cevap veriyor mu?
curl -I http://127.0.0.1:8080/

# nginx error log
sudo tail -f /var/log/nginx/enerjione-grid.error.log
```

### 9.6 SSL yenileme başarısız

```bash
sudo certbot renew --force-renewal
sudo systemctl reload nginx
```

### 9.7 Mobil push (FCM) çalışmıyor

Firebase Console > Project Settings > Service Accounts > Generate new private key.
İndirdiğin JSON'u `/opt/enerjione-grid/fcm-service-account.json` olarak kaydet:

```bash
sudo cp ~/fcm-service-account.json /opt/enerjione-grid/
sudo chmod 644 /opt/enerjione-grid/fcm-service-account.json
sudo bash /opt/enerjione-grid/update.sh backend
```

### 9.8 Tamamen sıfırlamak istiyorum

```bash
cd /opt/enerjione-grid
sudo bash uninstall.sh --yes --purge-dir
# /opt/enerjione-grid dizini + tüm volume'lar + image'lar silinir.
# DNS kaydı + Let's Encrypt cert'i kalır.

# Tekrar kurmak için:
curl -fsSL https://raw.githubusercontent.com/fikretsafak/EnerjiOneGrid/docker-linux-deploy/install.sh | sudo bash
```

---

## 📞 Yardım

Sorun çözülmediyse:
1. `sudo docker compose logs --tail 200 backend-api` çıktısını kaydet
2. `sudo systemctl status enerjione-grid --no-pager` çıktısını ekle
3. `cat /opt/enerjione-grid/.env | grep -v PASSWORD | grep -v SECRET | grep -v TOKEN` (hassas alanlar maskelenmiş)
4. GitHub Issues'da issue aç: https://github.com/fikretsafak/EnerjiOneGrid/issues

---

## ✅ Kontrol Listesi (Kurulum Sonrası)

- [ ] `sudo docker compose ps` → tüm container'lar `(healthy)`
- [ ] `sudo systemctl is-enabled enerjione-grid` → `enabled`
- [ ] `nslookup grid.enerjione.com` → VPS IP
- [ ] `curl -I https://grid.enerjione.com/` → `HTTP/2 200`
- [ ] Tarayıcıda yeşil kilit + login ekranı
- [ ] `installer` ile giriş yaptım, şifremi değiştirdim
- [ ] `sudo certbot renew --dry-run` → success
- [ ] (Multi-app) Solar deploy edildi ve subdomain üzerinden açılıyor
- [ ] FCM service-account.json yüklendi (mobil push için)
- [ ] SMTP/SMS ayarları yapıldı (Bildirim Ayarları sayfası)
- [ ] Backup retention period uygun (`.env: BACKUP_RETENTION_COUNT`)
- [ ] Offsite backup dizini ayarlandı (`.env: BACKUP_OFFSITE_DIR`)
