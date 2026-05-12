#!/usr/bin/env bash
# EnerjiOne Grid — sifirdan tek-komut VPS kurulumu.
#
# Bu script:
#   1. Docker yoksa kurar (install-docker.sh)
#   2. Repo'yu /opt/EnerjiOneGrid altina klonlar (zaten varsa pull eder)
#   3. bootstrap.sh'i calistirir — .env + NATS auth + tum servisler ayaga.
#
# Kullanim (Ubuntu 22.04/24.04 veya Debian 12, root veya sudo yetkili kullanici):
#
#   curl -fsSL https://raw.githubusercontent.com/fikretsafak/EnerjiOneGrid/docker-linux-deploy/infra/scripts/linux/quick-install.sh | sudo bash
#
# Veya parametreli (farkli branch / dizin):
#
#   curl -fsSL ... | sudo BRANCH=main INSTALL_DIR=/srv/enerjione bash
#
# Idempotent — birden fazla kez calistirmak guvenli. Mevcut .env korunur,
# eksik satirlar otomatik tamamlanir.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/fikretsafak/EnerjiOneGrid.git}"
BRANCH="${BRANCH:-docker-linux-deploy}"
INSTALL_DIR="${INSTALL_DIR:-/opt/EnerjiOneGrid}"

# --- Root kontrolu ---
if [[ $EUID -ne 0 ]]; then
  echo "HATA: Bu script root yetkisi ile calismali. Su sekilde calistirin:" >&2
  echo "  curl -fsSL <url> | sudo bash" >&2
  exit 1
fi

echo "============================================================"
echo "  EnerjiOne Grid — sifirdan VPS kurulumu"
echo "============================================================"
echo "  Repo:        ${REPO_URL}"
echo "  Branch:      ${BRANCH}"
echo "  Hedef dizin: ${INSTALL_DIR}"
echo "============================================================"
echo

# --- 1. Pre-req'leri kur (git + curl) ---
echo "[1/4] Pre-req paketler kontrol ediliyor (git, curl, ca-certificates)..."
if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates
fi
echo "      OK."

# --- 2. Repo'yu klonla / guncelle ---
echo "[2/4] Repo hazirlaniyor: ${INSTALL_DIR}"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  echo "      Repo zaten mevcut, git pull yapiliyor..."
  cd "${INSTALL_DIR}"
  # Kullanicinin lokal degisikligi varsa pull'i atla, uyari ver.
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "      UYARI: Lokal degisiklik var; git pull ATLANDI."
    echo "             Devam ediliyor (mevcut commit ile kurulum)."
  else
    git fetch --quiet origin "${BRANCH}"
    git checkout --quiet "${BRANCH}"
    git pull --ff-only --quiet
  fi
else
  if [[ -e "${INSTALL_DIR}" ]]; then
    echo "HATA: ${INSTALL_DIR} mevcut ama git repo degil." >&2
    echo "      Once silin veya farkli INSTALL_DIR verin:" >&2
    echo "      curl ... | sudo INSTALL_DIR=/opt/enerjione2 bash" >&2
    exit 1
  fi
  mkdir -p "$(dirname "${INSTALL_DIR}")"
  git clone --quiet --branch "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
  cd "${INSTALL_DIR}"
fi
echo "      Repo hazir: $(git rev-parse --short HEAD) (${BRANCH})"

# --- 3. Docker kur (yoksa) ---
echo "[3/4] Docker kontrol ediliyor..."
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "      Zaten kurulu: $(docker --version | head -1)"
else
  echo "      Docker yok, kuruluyor (Ubuntu/Debian)..."
  bash infra/scripts/linux/install-docker.sh
fi

# --- 4. Bootstrap (.env + NATS auth + servisleri ayaga kaldir) ---
echo "[4/4] bootstrap.sh calistiriliyor..."
echo
bash infra/scripts/linux/bootstrap.sh

# Bootstrap.sh kendi kurulum sonrasi rehberini bastiriyor; ek mesaj yok.
