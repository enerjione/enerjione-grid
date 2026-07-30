#!/usr/bin/env bash
# setup-gateway-agent.sh — e1-gwd (gateway kurulum ajani) host kurulumu.
#
# Ne yapar:
#   1. /var/lib/e1-grid/gw durum dizinini olusturur (root:BACKEND_UID, 0770)
#   2. e1-gwd.py'yi calistirilabilir yapar
#   3. systemd unit'lerini kurar ve baslatir (path + report timer)
#
# Neden ayri betik: bu ozellik appliance'a ozgu DEGIL. Docker'in calistigi
# her kurulumda (VPS dahil) "gateway'i bu cihaza kur" akisi kullanilabilir.
# Hem install.sh hem setup-appliance.sh bunu cagirir; idempotenttir.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/enerjione-grid}"
BACKEND_UID="${BACKEND_UID:-10001}"
GW_STATE_DIR="/var/lib/e1-grid/gw"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "setup-gateway-agent.sh root olarak calistirilmali." >&2
  exit 1
fi

# Unit dosyalari /opt/enerjione-grid yolunu sabit kullanir; repo baska
# yerdeyse ajan bulunamaz.
if [[ "$SRC_DIR" != "${INSTALL_DIR}/infra/appliance" ]]; then
  echo "UYARI: repo ${INSTALL_DIR} altinda degil (${SRC_DIR})." >&2
  echo "UYARI: systemd unit'leri ${INSTALL_DIR}/infra/appliance/e1-gwd.py bekler." >&2
fi

chmod 755 "${SRC_DIR}/e1-gwd.py"

# Durum dizini: backend container (uid ${BACKEND_UID}) buraya request.json
# yazar; ajan state.json/status.json yazar.
mkdir -p "$GW_STATE_DIR"
chown "root:${BACKEND_UID}" "$GW_STATE_DIR"
chmod 0770 "$GW_STATE_DIR"

# Gateway compose dosyalarinin kok dizini — sadece root. Icinde gateway
# token'i gecen compose dosyalari durur.
mkdir -p "${INSTALL_DIR}/gateways"
chown root:root "${INSTALL_DIR}/gateways"
chmod 0750 "${INSTALL_DIR}/gateways"

install -m 644 "${SRC_DIR}/systemd/e1-gwd.service"        /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-gwd.path"           /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-gwd-report.service" /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-gwd-report.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now e1-gwd.path >/dev/null 2>&1 || true
systemctl enable --now e1-gwd-report.timer >/dev/null 2>&1 || true
systemctl start e1-gwd-report.service >/dev/null 2>&1 || true

echo "e1-gwd hazir: ${GW_STATE_DIR} (root:${BACKEND_UID}, 0770)"
