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

# --- apt yardimcisi: alakasiz bozuk depo kurulumu DURDURMASIN ---------------
# SAHA VAKASI (Dell OEM Ubuntu 24.04): makinede birinin kurdugu Google Chrome
# deposunun imza anahtari yoktu; `apt-get update` 100 dondu ve `set -e` ile
# TUM kurulum orada oldu. Oysa ihtiyacimiz olan depolar (archive.ubuntu.com,
# download.docker.com) saglamdi ve paketler kurulabilirdi.
#
# Ubuntu 24.04 eski apt-key/trusted.gpg keyring'ini kullanmadigi icin eski
# yontemle eklenmis her ucuncu taraf deposu bu hatayi verir.
#
# Karar `apt-get install`'da veriliyor: paket gercekten kurulamiyorsa ORADA
# duruyoruz. Boylece Docker'in KENDI deposu bozuksa yine yakalanir.
_APT_BROKEN=""

_apt_update_tolerant() {
  local log
  log="$(mktemp)"
  apt-get update -y >"$log" 2>&1 || true
  _APT_BROKEN="$(grep -E '^(Err|E|W): ' "$log" 2>/dev/null | head -8 || true)"
  rm -f "$log"
  if [[ -n "$_APT_BROKEN" ]]; then
    echo "  ! apt-get update kismen basarisiz — su depolar atlandi:" >&2
    printf '%s\n' "$_APT_BROKEN" | sed 's/^/        /' >&2
    echo "    Devam ediliyor; gerekli paketler kurulamazsa asagida durulacak." >&2
  fi
}

_apt_install_or_die() {
  if ! apt-get install -y "$@"; then
    echo >&2
    echo "HATA: paketler kurulamadi: $*" >&2
    if [[ -n "$_APT_BROKEN" ]]; then
      echo "  Yukaridaki bozuk apt deposu sebep olabilir. Devre disi birakip tekrar deneyin:" >&2
      echo "    sudo mv /etc/apt/sources.list.d/<depo>.list{,.disabled}" >&2
      echo "    sudo apt-get update" >&2
    fi
    exit 1
  fi
}

echo "[1/5] Apt cache guncelleniyor..."
_apt_update_tolerant

echo "[2/5] On-kosul paketler kuruluyor..."
_apt_install_or_die ca-certificates curl gnupg lsb-release

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
# Bu update Docker'in KENDI deposu eklendikten sonra kosuyor. Yine tolere
# ediyoruz (alakasiz bozuk depolar icin) ama Docker deposu bozuksa asagidaki
# install adimi paketleri bulamaz ve orada duruyoruz — sessizce gecmiyoruz.
_apt_update_tolerant
_apt_install_or_die docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

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
