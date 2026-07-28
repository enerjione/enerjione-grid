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
#   - /opt/enerjione-grid frontend'i 127.0.0.1:8080'de bind
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

# Kurulum adresi: https://enerjione.com/grid/install.sh
# Script GitHub'a yonlendirilmez, BU SUNUCUDAN servis edilir — depo private
# oldugu icin raw.githubusercontent.com kimliksiz istemciye 404 doner.
# DNS A kaydi yoksa nginx yine de acilir, sadece o host cozulmez; kurulumu
# bloklamaz. E1_SKIP_GET=1 ile atlanabilir.
if [[ "${E1_SKIP_GET:-0}" != "1" ]] && [[ -f "$SCRIPT_DIR/grid-public.conf" ]]; then
  echo "      + enerjione.com/grid/install.sh (kurulum adresi)"
  cp "$SCRIPT_DIR/grid-public.conf" /etc/nginx/sites-available/e1-grid-public
  ln -sf /etc/nginx/sites-available/e1-grid-public /etc/nginx/sites-enabled/e1-grid-public

  # Eski 302-yonlendirmeli site varsa devre disi birak: private depoda
  # calismaz ve `curl | bash` bos govde calistirmaya kalkar.
  if [[ -L /etc/nginx/sites-enabled/get-enerjione ]]; then
    echo "      - eski get-enerjione (GitHub yonlendirmesi) devre disi"
    rm -f /etc/nginx/sites-enabled/get-enerjione
  fi

  # Scripti ve surum manifestini yayinla (idempotent).
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
  if [[ -x "$REPO_ROOT/infra/scripts/linux/publish-installer.sh" ]] \
     || [[ -f "$REPO_ROOT/infra/scripts/linux/publish-installer.sh" ]]; then
    bash "$REPO_ROOT/infra/scripts/linux/publish-installer.sh" || \
      echo "      ! yayin adimi basarisiz — nginx yine de kuruldu"
  fi
fi

# 4) Syntax check + reload
echo "[4/4] nginx config dogrulanip yeniden yukleniyor"
if nginx -t; then
  systemctl reload nginx || systemctl start nginx
  systemctl enable nginx >/dev/null 2>&1 || true
  echo ""
  echo "=== TAMAM ==="
  echo "Test:"
  echo "  curl -I http://grid.enerjione.com/"
  echo "  curl -I http://solar.enerjione.com/"
  echo "  curl -fsS http://enerjione.com/grid/install.sh | head -3"
  echo "  curl -fsS http://enerjione.com/grid/version.json"
  echo ""
  echo "Kurulum komutu (DNS A kaydi enerjione.com -> bu VPS ise):"
  echo "  curl -fsSL https://enerjione.com/grid/install.sh | sudo bash"
  echo ""
  echo "SSL icin (opsiyonel):"
  echo "  sudo apt install -y certbot python3-certbot-nginx"
  echo "  sudo certbot --nginx -d grid.enerjione.com -d solar.enerjione.com \\"
  echo "                       -d enerjione.com -d www.enerjione.com -d get.enerjione.com"
else
  echo "HATA: nginx config gecersiz — durum degistirilmedi."
  exit 1
fi
