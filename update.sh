#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Update
# ===========================================================================
# Mevcut kurulumu gunceller: git pull + selective rebuild + DB yedek (otomatik).
#
# Kullanim (repo kokunde, ornegin /opt/enerjione-grid):
#   sudo bash update.sh                # tum servisleri yeniden derle + up
#   sudo bash update.sh frontend       # sadece frontend-web
#   sudo bash update.sh backend        # sadece backend-api
#   sudo bash update.sh alarm          # alarm-service
#   sudo bash update.sh tag            # tag-engine
#   sudo bash update.sh notification   # notification-worker
#   sudo bash update.sh iec            # iec104-outbound
#
# Idempotent. Compose Docker build cache'i kullanir; degismemis layer'lar
# yeniden indirilmez.
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/infra/scripts/linux/_lib.sh"

e1_require_root "$@"

TARGET="${1:-all}"

cd "$SCRIPT_DIR"

# ---- Banner ---------------------------------------------------------------
clear 2>/dev/null || true
e1_banner
echo "  ${E1_DIM}Dizin       :${E1_RESET} ${SCRIPT_DIR}"
echo "  ${E1_DIM}Hedef       :${E1_RESET} ${TARGET}"
echo "  ${E1_DIM}Branch      :${E1_RESET} $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
echo "  ${E1_DIM}Onceki HEAD :${E1_RESET} $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
echo

e1_set_steps 4

# ---- 1/4: Lokal degisiklik kontrolu --------------------------------------
e1_step "Lokal degisiklik kontrolu..."
if ! git diff --quiet || ! git diff --cached --quiet; then
  e1_die "Repo'da commit edilmemis lokal degisiklik var. 'git status' ile inceleyin, stash veya commit edin."
fi
e1_ok "Calisma agaci temiz."

# ---- 2/4: DB yedek (otomatik, postgres ayaktaysa) ------------------------
e1_step "Update oncesi DB yedek aliniyor..."
if docker compose ps postgres --status running --quiet 2>/dev/null | grep -q .; then
  TS=$(date +%Y%m%d-%H%M%S)
  BACKUP_FILE="backups/auto-pre-update-${TS}.sql.gz"
  mkdir -p backups
  PG_USER="$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d= -f2-)"
  PG_DB="$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2-)"
  PG_USER="${PG_USER:-enerjione_grid}"
  PG_DB="${PG_DB:-enerjione_grid}"
  if docker compose exec -T postgres pg_dump -U "${PG_USER}" -d "${PG_DB}" 2>/dev/null | gzip > "${BACKUP_FILE}"; then
    SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    e1_ok "Yedek: ${BACKUP_FILE} (${SIZE})"
    e1_chown_target "${BACKUP_FILE}"
  else
    e1_warn "DB yedek alinamadi; update yine de devam ediyor."
    rm -f "${BACKUP_FILE}"
  fi
else
  e1_info "Postgres ayakta degil — yedek atlandi (ilk kurulum sonrasi?)."
fi

# ---- 3/4: Git pull --------------------------------------------------------
e1_step "Git pull..."
git pull --ff-only
NEW_HEAD="$(git rev-parse --short HEAD)"
e1_ok "Yeni HEAD: ${NEW_HEAD}"

# Docker bind mount kazasi koruma: compose `./infra/nats/nats-server.conf:
# /etc/nats/nats-server.conf:ro` mount'u, host'ta dosya YOKSA Docker bunu
# DIZIN olarak yaratir. Sonra render `sed` "not a regular file" hatasi verir.
# `nats-server.conf` mevcut ama dizin ise NATS container'i durdur + dizini sil.
if [[ -d infra/nats/nats-server.conf ]]; then
  e1_warn "infra/nats/nats-server.conf DIZIN (Docker bind mount kazasi). Temizleniyor..."
  docker compose stop nats 2>/dev/null || true
  docker compose rm -f nats 2>/dev/null || true
  rm -rf infra/nats/nats-server.conf
fi

# Ayni koruma fcm-service-account.json icin. Yeni compose mount eklendigi
# icin eski kurulumlarda dosya yoksa Docker dizin yaratacak.
if [[ -d fcm-service-account.json ]]; then
  e1_warn "fcm-service-account.json DIZIN (Docker bind mount kazasi). Temizleniyor..."
  docker compose stop backend-api 2>/dev/null || true
  docker compose rm -f backend-api 2>/dev/null || true
  rm -rf fcm-service-account.json
fi
if [[ ! -e fcm-service-account.json ]]; then
  e1_info "fcm-service-account.json yok — disabled placeholder olusturuluyor (FCM devre disi)."
  cat > fcm-service-account.json <<'PLACEHOLDER'
{
  "_comment": "FCM disabled placeholder — gercek service account icin Firebase Console'dan indirin.",
  "type": "service_account",
  "project_id": "",
  "private_key": "",
  "client_email": "",
  "_disabled": true
}
PLACEHOLDER
  # chmod 644: container backend user'i (appuser/10001) host uid 1000'in
  # chmod 600 dosyasini okuyamaz → PermissionError. Single-app deployment
  # icin guvenli (host erisimi zaten gerekli).
  chmod 644 fcm-service-account.json
  e1_chown_target fcm-service-account.json
fi
# Mevcut dosya chmod 600 olabilir (eski install veya elle scp'lendi). Backend
# container icindeki user okuyamaz. 644'e cek.
if [[ -f fcm-service-account.json ]]; then
  current_mode="$(stat -c %a fcm-service-account.json 2>/dev/null || echo 600)"
  if [[ "$current_mode" != "644" ]]; then
    chmod 644 fcm-service-account.json
    e1_info "fcm-service-account.json chmod 644 yapildi (container erisimi icin)."
  fi
fi

# NATS auth conf yoksa veya tema (worker permissions vs.) degistiyse render.
# update.sh degisikligi sonrasi WORKER izinlerine $JS.API.STREAM.NAMES eklendi;
# eski render edilmis conf bunu icermez, alarm-service "permissions violation"
# alir. Cozum: template hash'i ile mevcut conf hash'ini karsilastir, fark
# varsa yeniden render.
NEED_NATS_RENDER=0
if [[ ! -f infra/nats/nats-server.conf ]]; then
  NEED_NATS_RENDER=1
elif ! grep -q '$JS.API.STREAM.NAMES' infra/nats/nats-server.conf; then
  # Eski conf — worker stream listeleme izni yok. Yeniden render gerek.
  e1_info "NATS auth conf eski (worker $JS.API.STREAM.NAMES izni yok), yeniden render ediliyor..."
  NEED_NATS_RENDER=1
fi

if [[ $NEED_NATS_RENDER -eq 1 ]]; then
  e1_info "NATS auth conf render ediliyor..."
  if ! python3 -c "import bcrypt" 2>/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-bcrypt
  fi
  set -a; source .env; set +a
  _bcrypt() {
    python3 -c "import sys, bcrypt; print(bcrypt.hashpw(sys.stdin.buffer.read().rstrip(b'\n'), bcrypt.gensalt(rounds=11)).decode())" <<<"$1"
  }
  HASH_G=$(_bcrypt "${NATS_GATEWAY_PASSWORD}")
  HASH_B=$(_bcrypt "${NATS_BACKEND_PASSWORD}")
  HASH_W=$(_bcrypt "${NATS_WORKER_PASSWORD}")
  cp infra/nats/nats-server.conf.template infra/nats/nats-server.conf
  sed -i "s|{{NATS_GATEWAY_BCRYPT_HASH}}|${HASH_G//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_BACKEND_BCRYPT_HASH}}|${HASH_B//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_WORKER_BCRYPT_HASH}}|${HASH_W//&/\\&}|" infra/nats/nats-server.conf
  e1_chown_target infra/nats/nats-server.conf
  e1_ok "NATS auth render edildi."
  # NATS container conf'i yeniden okusun
  if docker compose ps nats --status running --quiet 2>/dev/null | grep -q .; then
    e1_info "NATS container restart ediliyor (yeni conf icin)..."
    docker compose up -d --force-recreate nats
  fi
fi

# ---- 4/4: Build + up ------------------------------------------------------
case "$TARGET" in
  frontend|frontend-web|web)        SVC="frontend-web" ;;
  backend|api|backend-api)          SVC="backend-api" ;;
  alarm|alarm-service)              SVC="alarm-service" ;;
  tag|tag-engine)                   SVC="tag-engine" ;;
  notification|notification-worker) SVC="notification-worker" ;;
  iec|iec104|iec104-outbound)       SVC="iec104-outbound" ;;
  all|"")                           SVC="" ;;
  *)
    e1_die "Bilinmeyen servis: $TARGET. Gecerli: frontend, backend, alarm, tag, notification, iec, all"
    ;;
esac

if [[ -z "$SVC" ]]; then
  e1_step "Tum servisler yeniden derleniyor + ayaga kalkiyor..."
  docker compose build
  docker compose up -d
else
  e1_step "Servis '$SVC' yeniden derleniyor + force-recreate..."
  docker compose build "$SVC"
  docker compose up -d --force-recreate "$SVC"
fi

echo
echo "${E1_GREEN}${E1_BOLD}Update tamamlandi.${E1_RESET}"
echo
echo "  ${E1_BOLD}Servis durumu:${E1_RESET}"
docker compose ps
echo
echo "  ${E1_DIM}Tarayicidan Ctrl+Shift+R ile hard refresh edin (yeni frontend).${E1_RESET}"
echo
