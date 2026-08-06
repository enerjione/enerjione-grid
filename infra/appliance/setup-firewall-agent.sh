#!/usr/bin/env bash
# setup-firewall-agent.sh — e1-fwd (guvenlik duvari ajani) host kurulumu.
#
# Ne yapar:
#   1. /var/lib/e1-grid/fw      durum dizinini olusturur (root:BACKEND_UID, 0770)
#   2. /var/lib/e1-grid/fw-priv yapilandirma dizinini olusturur (root:root, 0700)
#   3. e1-fwd.py'yi calistirilabilir yapar
#   4. systemd unit'lerini kurar ve baslatir (path + report timer)
#
# Neden ayri betik (e1-netd'ye eklenmedi): setup-remote-access.sh ile ayni
# gerekce — e1-netd yalnizca setup-appliance.sh (mini PC modu) ile kurulur,
# guvenlik duvari ise VPS dahil HER kurulumda anlamli (hatta VPS'te daha
# anlamli: yayinlanan SCADA portlari dogrudan internete acik).
#
# GUVENLIK NOTU: bu betik hicbir kurali degistirmez. Ajan varsayilan olarak
# KAPALI kurulur; mevcut sahadaki cihazlar guncellemeyi alinca davranis
# degismez, duvari kullanici arayuzden bilerek acar.
#
# Env:
#   INSTALL_DIR   varsayilan /opt/enerjione-grid
#   BACKEND_UID   backend container uid'si (varsayilan 10001)
#
# Idempotent: her calistirmada guvenle tekrarlanabilir.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/enerjione-grid}"
BACKEND_UID="${BACKEND_UID:-10001}"
FW_STATE_DIR="/var/lib/e1-grid/fw"
FW_PRIV_DIR="/var/lib/e1-grid/fw-priv"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "setup-firewall-agent.sh root olarak calistirilmali." >&2
  exit 1
fi

# Unit dosyalari /opt/enerjione-grid yolunu sabit kullanir; repo baska
# yerdeyse ajan bulunamaz.
if [[ "$SRC_DIR" != "${INSTALL_DIR}/infra/appliance" ]]; then
  echo "UYARI: repo ${INSTALL_DIR} altinda degil (${SRC_DIR})." >&2
  echo "UYARI: systemd unit'leri ${INSTALL_DIR}/infra/appliance/e1-fwd.py bekler." >&2
fi

chmod 755 "${SRC_DIR}/e1-fwd.py"

# Paylasilan IPC dizini: backend (uid ${BACKEND_UID}) buraya request.json
# yazar; ajan state.json/status.json yazar.
mkdir -p "$FW_STATE_DIR"
chown "root:${BACKEND_UID}" "$FW_STATE_DIR"
chmod 0770 "$FW_STATE_DIR"

# Yetkili yapilandirma dizini: SADECE root. Paylasilan dizinde yazma izni
# olan backend, icindeki root'a ait dosyalari unlink/rename edebilirdi —
# yani kendine kural uydurabilirdi (e1-rad remote-priv ile ayni gerekce).
mkdir -p "$FW_PRIV_DIR"
chown root:root "$FW_PRIV_DIR"
chmod 0700 "$FW_PRIV_DIR"

install -m 644 "${SRC_DIR}/systemd/e1-fwd.service"        /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-fwd.path"           /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-fwd-report.service" /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-fwd-report.timer"   /etc/systemd/system/
systemctl daemon-reload
# Timer durursa reboot sonrasi duvar GERI KURULMAZ (iptables kalici degil);
# bu yuzden her kurulumda/guncellemede yeniden enable ediliyor.
systemctl enable --now e1-fwd.path >/dev/null 2>&1 || true
systemctl enable --now e1-fwd-report.timer >/dev/null 2>&1 || true

# Ilk turu hemen kos: timer'i beklemeden state.json yazilsin ve arayuz
# "ajan hic rapor yazmamis" demesin.
systemctl start e1-fwd-report.service >/dev/null 2>&1 || \
  "${SRC_DIR}/e1-fwd.py" report >/dev/null 2>&1 || true

FW_ENABLED="$(sed -n 's/.*"enabled"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p' \
              "${FW_STATE_DIR}/state.json" 2>/dev/null | head -1)"
if [[ "$FW_ENABLED" == "true" ]]; then
  echo "e1-fwd hazir: guvenlik duvari ACIK (mevcut kurallar korundu)."
else
  echo "e1-fwd hazir: guvenlik duvari KAPALI (varsayilan)."
  echo "  Yonetim: Muhendislik > Cihaz Ayarlari > Guvenlik Duvari"
fi
