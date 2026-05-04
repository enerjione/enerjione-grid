#!/usr/bin/env bash
# Ubuntu 22.04/24.04 ve Debian 12 icin Docker Engine + Compose v2 kurulum scripti.
# Idempotent — birden fazla kez calistirmak guvenli.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Bu script root yetkisi ile calismali (sudo)."
  exit 1
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "Docker zaten kurulu: $(docker --version), $(docker compose version)"
  exit 0
fi

echo "[1/5] Apt cache guncelleniyor..."
apt-get update -y

echo "[2/5] On-kosul paketler kuruluyor..."
apt-get install -y ca-certificates curl gnupg lsb-release

echo "[3/5] Docker GPG anahtari ve repo ekleniyor..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo "$ID")/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

DISTRO_ID="$(. /etc/os-release; echo "$ID")"
DISTRO_CODENAME="$(. /etc/os-release; echo "${VERSION_CODENAME}")"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${DISTRO_ID} ${DISTRO_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list

echo "[4/5] Docker Engine + Compose plugin kuruluyor..."
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "[5/5] Docker servisi etkinlestiriliyor..."
systemctl enable --now docker

# SUDO_USER set ise (sudo ile cagrilmissa) onu docker grubuna ekle.
if [[ -n "${SUDO_USER:-}" ]] && id -nG "$SUDO_USER" | grep -qvw docker; then
  usermod -aG docker "$SUDO_USER"
  echo "User '$SUDO_USER' docker grubuna eklendi (yeniden giris yapinca aktif olur)."
fi

echo
echo "Tamamlandi. Versiyonlar:"
docker --version
docker compose version
