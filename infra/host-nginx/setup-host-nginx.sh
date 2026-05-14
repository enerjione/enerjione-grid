#!/usr/bin/env bash
# Host-level nginx kurulum scripti.
# Bu scripti VPS'te ROOT veya sudo yetkili kullanici ile calistir.
#
# Yaptıgı:
#   1. nginx (host) kurar
#   2. enerjione-grid.conf + solar.conf'u /etc/nginx/sites-available/'a kopyalar
#   3. sites-enabled symlink'lerini olusturur
#   4. nginx -t ile syntax check + reload
#
# Calistirmadan ONCE:
#   - DNS A kayitlari hazir (enerjione-grid.* + solar.* her ikisi VPS IP'ye)
#   - /opt/enerjione frontend'i 127.0.0.1:8080'de bind
#   - /opt/solar frontend'i 127.0.0.1:8081'de bind
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Bu scripti sudo ile calistir: sudo $0"
  exit 1
fi

echo "=== Host nginx kurulumu ==="

# 1) nginx kur (idempotent)
if ! command -v nginx >/dev/null 2>&1; then
  echo "[1/4] nginx kuruluyor..."
  apt-get update -qq
  apt-get install -y nginx
else
  echo "[1/4] nginx zaten kurulu — atlanir"
fi

# 2) Default config'i devre disi birak (bos /var/www/html sayfasi yerine)
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  echo "[2/4] default site devre disi birakiliyor"
  rm -f /etc/nginx/sites-enabled/default
fi

# 3) Config'leri kopyala
echo "[3/4] enerjione-grid + solar config kopyalaniyor"
cp "$SCRIPT_DIR/enerjione-grid.conf" /etc/nginx/sites-available/enerjione-grid
cp "$SCRIPT_DIR/solar.conf"          /etc/nginx/sites-available/solar

ln -sf /etc/nginx/sites-available/enerjione-grid /etc/nginx/sites-enabled/enerjione-grid
ln -sf /etc/nginx/sites-available/solar          /etc/nginx/sites-enabled/solar

# 4) Syntax check + reload
echo "[4/4] nginx config dogrulanip yeniden yukleniyor"
if nginx -t; then
  systemctl reload nginx || systemctl start nginx
  systemctl enable nginx >/dev/null 2>&1 || true
  echo ""
  echo "=== TAMAM ==="
  echo "Test:"
  echo "  curl -I http://enerjione-grid.fikretsafak.com.tr/"
  echo "  curl -I http://solar.fikretsafak.com.tr/"
  echo ""
  echo "SSL icin (opsiyonel):"
  echo "  sudo apt install -y certbot python3-certbot-nginx"
  echo "  sudo certbot --nginx -d enerjione-grid.fikretsafak.com.tr -d solar.fikretsafak.com.tr"
else
  echo "HATA: nginx config gecersiz — durum degistirilmedi."
  exit 1
fi
