#!/usr/bin/env bash
# EnerjiOne Grid'i systemd servisi olarak kaydet.
#
# Calistirma:
#   sudo bash infra/systemd/setup-systemd.sh
#
# Sonra:
#   sudo systemctl start enerjione-grid       # baslat
#   sudo systemctl stop enerjione-grid        # durdur
#   sudo systemctl restart enerjione-grid     # yeniden baslat
#   sudo systemctl status enerjione-grid      # durum
#   sudo systemctl enable enerjione-grid      # boot'ta otomatik baslat
#   sudo journalctl -u enerjione-grid -f      # canli log
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC="$SCRIPT_DIR/enerjione-grid.service"
UNIT_DST="/etc/systemd/system/enerjione-grid.service"

if [[ $EUID -ne 0 ]]; then
  echo "sudo ile calistir: sudo $0"
  exit 1
fi

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "HATA: $UNIT_SRC bulunamadi."
  exit 1
fi

echo "[1/4] systemd unit dosyasi kopyalaniyor..."
cp "$UNIT_SRC" "$UNIT_DST"
chmod 644 "$UNIT_DST"

echo "[2/4] systemd daemon-reload..."
systemctl daemon-reload

echo "[3/4] Boot'ta otomatik baslat icin enable ediliyor..."
systemctl enable enerjione-grid.service

echo "[4/4] Servis baslatiliyor..."
systemctl start enerjione-grid.service

echo
echo "=== TAMAM ==="
echo
systemctl status enerjione-grid.service --no-pager || true
echo
echo "Kullanim:"
echo "  sudo systemctl start enerjione-grid      # baslat"
echo "  sudo systemctl stop enerjione-grid       # durdur"
echo "  sudo systemctl restart enerjione-grid    # yeniden baslat"
echo "  sudo systemctl status enerjione-grid     # durum"
echo "  sudo journalctl -u enerjione-grid -f     # canli log"
echo
echo "Update sonrasi (kod degisikligi yapildiginda):"
echo "  cd /opt/enerjione-grid && sudo git pull && sudo bash update.sh"
echo "  # update.sh zaten 'docker compose up -d --build' yapar; systemd"
echo "  # restart tetiklemeye gerek yok (container'lar in-place yenilenir)."
