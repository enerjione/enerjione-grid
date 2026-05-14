#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Production Installer
# ===========================================================================
# Sifirdan tek-komut kurulum. Ubuntu 22.04/24.04 ve Debian 12 destekli.
#
# Iki kullanim sekli:
#
#   1) Repo'yu manuel klonladiysan, repo kokunde:
#        sudo bash install.sh
#
#   2) Tertemiz VPS'te (curl | bash):
#        curl -fsSL https://raw.githubusercontent.com/fikretsafak/EnerjiOneGrid/docker-linux-deploy/install.sh | sudo bash
#
# Idempotent: tekrar calistirmak guvenli. Mevcut .env'i koruyup eksik
# alanlari rastgele degerlerle doldurur, Docker'i atlar (zaten kuruluysa),
# servisleri update eder.
#
# Env override'lar (her ikisi de opsiyonel):
#   INSTALL_DIR  hedef dizin (default: /opt/enerjione-grid)
#   BRANCH       checkout edilecek git branch'i (default: docker-linux-deploy)
#   REPO_URL     git remote URL (default: github.com/fikretsafak/EnerjiOneGrid.git)
#   INSTALL_USER kurulum sonrasi dosya sahibi olacak kullanici
#                (default: SUDO_USER veya root)
#   ASSUME_YES=1 tum onay sorularini atla
# ===========================================================================

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/fikretsafak/EnerjiOneGrid.git}"
BRANCH="${BRANCH:-docker-linux-deploy}"
INSTALL_DIR="${INSTALL_DIR:-/opt/enerjione-grid}"

# ---- Bootstrap: bu script repo icinden mi yoksa curl | bash mi? ----------
# Iki senaryo: (a) repo kokunden bash install.sh — `infra/scripts/linux/_lib.sh`
# yerel dosya olarak var. (b) curl | bash — script'in dosyasi yok, repo
# henuz klonlanmadi; lib'i once GitHub'dan cek, daha sonra repo klonlanip
# yerel lib'i de _chown_target ile devralir.
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
LIB_LOADED=0
if [[ -n "$SCRIPT_DIR" ]] && [[ -f "$SCRIPT_DIR/infra/scripts/linux/_lib.sh" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/infra/scripts/linux/_lib.sh"
  LIB_LOADED=1
else
  # curl | bash modu — lib'i GitHub'dan cek (tmpfile'a).
  if command -v curl >/dev/null 2>&1; then
    TMP_LIB="$(mktemp)"
    if curl -fsSL "https://raw.githubusercontent.com/fikretsafak/EnerjiOneGrid/${BRANCH}/infra/scripts/linux/_lib.sh" -o "$TMP_LIB" 2>/dev/null; then
      # shellcheck disable=SC1090
      source "$TMP_LIB"
      LIB_LOADED=1
      rm -f "$TMP_LIB"
    fi
  fi
fi
if [[ $LIB_LOADED -eq 0 ]]; then
  echo "HATA: _lib.sh yuklenemedi (ne yerel ne uzak)." >&2
  exit 1
fi

# ---- Root kontrolu --------------------------------------------------------
e1_require_root "$@"

# ---- Banner ---------------------------------------------------------------
clear 2>/dev/null || true
e1_banner
echo "  ${E1_DIM}Hedef dizin :${E1_RESET} ${INSTALL_DIR}"
echo "  ${E1_DIM}Branch      :${E1_RESET} ${BRANCH}"
echo "  ${E1_DIM}Repo        :${E1_RESET} ${REPO_URL}"
if [[ -n "${SUDO_USER:-}" ]]; then
  echo "  ${E1_DIM}Sahip       :${E1_RESET} ${SUDO_USER}"
fi
echo

e1_set_steps 6

# ---- 1/6: Pre-req paketler -----------------------------------------------
e1_step "Pre-req paketler kontrol ediliyor (git, curl, openssl)..."
MISSING=()
for pkg in git curl openssl; do
  if ! command -v "$pkg" >/dev/null 2>&1; then
    MISSING+=("$pkg")
  fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  e1_info "Eksik paketler: ${MISSING[*]} — apt ile kuruluyor..."
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}" ca-certificates
  e1_ok "Pre-req'ler kuruldu."
else
  e1_ok "Tum pre-req'ler hazir."
fi

# ---- 2/6: Repo klonla / guncelle -----------------------------------------
e1_step "Repo hazirlaniyor: ${INSTALL_DIR}"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  e1_info "Repo zaten mevcut; fetch + checkout..."
  cd "${INSTALL_DIR}"
  if ! git diff --quiet || ! git diff --cached --quiet; then
    e1_warn "Lokal degisiklik var; git pull ATLANDI. Mevcut commit ile devam."
  else
    git fetch --quiet origin "${BRANCH}"
    git checkout --quiet "${BRANCH}"
    git pull --ff-only --quiet
  fi
else
  if [[ -e "${INSTALL_DIR}" ]]; then
    e1_die "${INSTALL_DIR} mevcut ama git repo degil. Once silin veya farkli INSTALL_DIR verin."
  fi
  mkdir -p "$(dirname "${INSTALL_DIR}")"
  git clone --quiet --branch "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
  cd "${INSTALL_DIR}"
fi
e1_ok "Repo hazir: $(git rev-parse --short HEAD) (${BRANCH})"

# Dizin sahipligini cagiran kullaniciya devret (sudo ile root:root oldu).
if [[ -n "$(e1_target_user)" ]]; then
  e1_info "Dizin sahipligi '$(e1_target_user)' kullanicisina devrediliyor..."
  e1_chown_target_recursive "${INSTALL_DIR}"
fi

# ---- 3/6: Docker Engine + Compose ----------------------------------------
e1_step "Docker Engine + Compose plugin kontrolu..."
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  e1_ok "Zaten kurulu: $(docker --version | head -1)"
else
  e1_info "Docker yok, kuruluyor..."
  bash "${INSTALL_DIR}/infra/scripts/linux/install-docker.sh"
  e1_ok "Docker kuruldu."
fi

# ---- 4/6: .env (secret'lar) ----------------------------------------------
e1_step ".env hazirlaniyor (secret'lar rastgele uretilir)..."
cd "${INSTALL_DIR}"

# .env yoksa template'ten kopyala.
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  e1_ok ".env olusturuldu (.env.example'dan)."
else
  e1_info ".env zaten mevcut, eksik/placeholder satirlar dolduruluyor (idempotent)."
fi

# Helper: bir key yoksa ekler, varsa placeholder ise gercek deger ile doldurur.
# Placeholder kaliplari: bos, please-change-me*, change-me*, change-this*.
# `sed` replacement icin `|` ayraci kullaniyoruz; degerde `&` varsa escape.
_ensure_env_var() {
  local key="$1"
  local value="$2"
  local escaped_value="${value//&/\\&}"
  if grep -qE "^${key}=" .env; then
    if grep -qE "^${key}=$|^${key}=please-change-me|^${key}=change-me|^${key}=change-this" .env; then
      sed -i "s|^${key}=.*|${key}=${escaped_value}|" .env
      e1_info "${key} guncellendi (placeholder -> rastgele)."
    fi
  else
    echo "${key}=${value}" >> .env
    e1_info "${key} eklendi (.env'de yoktu)."
  fi
}

# Helper: bir env key'i kesin olarak target value'ya ayarlar (placeholder
# olsun olmasin, var olsun olmasin). CORS_ORIGINS gibi `*` default'unu
# overwrite etmek icin gerekli.
_set_env_var() {
  local key="$1"
  local value="$2"
  local escaped_value="${value//&/\\&}"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${escaped_value}|" .env
  else
    echo "${key}=${value}" >> .env
  fi
}

SK=$(openssl rand -hex 32)
IT=$(openssl rand -hex 32)
PP=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
RP=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
NB=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
NW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
NG=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
_ensure_env_var "SECRET_KEY" "$SK"
_ensure_env_var "INTERNAL_SERVICE_TOKEN" "$IT"
_ensure_env_var "POSTGRES_PASSWORD" "$PP"
_ensure_env_var "RABBITMQ_PASSWORD" "$RP"
_ensure_env_var "NATS_BACKEND_PASSWORD" "$NB"
_ensure_env_var "NATS_WORKER_PASSWORD" "$NW"
_ensure_env_var "NATS_GATEWAY_PASSWORD" "$NG"

# APP_ENV — install.sh production deploy yapiyor, default production.
# CORS_ORIGINS '*' bu env'de backend tarafindan reddedilir (config.py guard).
# VPS IP'sini otomatik tespit edip whitelist'e koyariz; kullanici sonradan
# domain ekleyince .env'i elle duzenler.
_set_env_var "APP_ENV" "production"

# CORS_ORIGINS: VPS IP + localhost. Default '*' production'da reddedilir.
# IP tespiti kritik: bos donerse browser'in WS Origin check'ini gecemez (403).
# Bu yuzden detect basarisiz olursa fail-fast yap, kullanici elle girsin.
DETECTED_IP="$(e1_detect_ip 2>/dev/null || true)"
if [[ -z "$DETECTED_IP" ]] || [[ "$DETECTED_IP" == "<vds-ip>" ]] || [[ "$DETECTED_IP" == "127.0.0.1" ]]; then
  e1_warn "VPS IP otomatik tespit edilemedi (hostname -I + ifconfig.me ikisi de basarisiz)."
  e1_warn "CORS_ORIGINS sadece localhost olacak — browser'dan VPS IP ile baglanan kullanici"
  e1_warn "WebSocket origin check'i gecemez. .env'i elle duzenleyip:"
  e1_warn "  CORS_ORIGINS=http://<vps-ip>,http://localhost,http://127.0.0.1"
  e1_warn "satirini ekleyip backend-api'yi restart edin."
  CORS_DEFAULT="http://localhost,http://127.0.0.1"
else
  e1_info "VPS IP tespit edildi: ${DETECTED_IP}"
  CORS_DEFAULT="http://${DETECTED_IP},http://localhost,http://127.0.0.1"
fi
# Sadece placeholder/yildiz/bos ise overwrite et; kullanici manuel girdiyse koru.
CURRENT_CORS="$(grep -E '^CORS_ORIGINS=' .env | cut -d= -f2- || echo '')"
if [[ -z "$CURRENT_CORS" ]] || [[ "$CURRENT_CORS" == "*" ]]; then
  _set_env_var "CORS_ORIGINS" "$CORS_DEFAULT"
  e1_info "CORS_ORIGINS: ${CORS_DEFAULT}"
else
  e1_info "CORS_ORIGINS korundu (manuel set): ${CURRENT_CORS}"
fi

# Sanity check: hicbir kritik secret hala placeholder olmasin.
for k in SECRET_KEY INTERNAL_SERVICE_TOKEN POSTGRES_PASSWORD RABBITMQ_PASSWORD \
         NATS_BACKEND_PASSWORD NATS_WORKER_PASSWORD NATS_GATEWAY_PASSWORD; do
  v="$(grep -E "^${k}=" .env | cut -d= -f2- || echo '')"
  if [[ -z "$v" ]] || [[ "$v" == please-change-me* ]] || [[ "$v" == change-me* ]] || [[ "$v" == change-this* ]]; then
    e1_die "${k} hala placeholder/bos! .env'i kontrol edin: ${INSTALL_DIR}/.env"
  fi
done

chmod 600 .env
e1_chown_target .env
e1_ok ".env hazir (chmod 600, sahip: $(e1_target_user || echo root))."

# FCM service account JSON — mobil app push bildirim icin. Opsiyonel:
# yoksa FCM devre disi kalir (email/SMS calismaya devam eder).
# Ama compose mount edebilmek icin dosya MUTLAKA var olmali (yoksa Docker
# bind mount'u DIZIN olarak yaratir, NATS conf kazasi gibi).
# Cozum: dosya yoksa "disabled placeholder" yarat — fcm.py JSON parse
# basarisiz olur, "FCM yuklenirken hata" log atar ama devam eder.
# GUVENLIK: Eski yarim migration veya elle bind mount kazasi nedeniyle
# 'fcm-service-account.json' bir DIZIN olarak kalabilir (docker bunu
# 'dosya yok' tespitinde otomatik olarak dizin yaratir). Eger dizinse
# sil; dosya degilse placeholder yaz.
if [[ -d fcm-service-account.json ]]; then
  e1_warn "fcm-service-account.json bir DIZIN — siliniyor (yanlis bind mount kalintisi)."
  rm -rf fcm-service-account.json
fi
if [[ ! -f fcm-service-account.json ]]; then
  e1_info "fcm-service-account.json yok — placeholder olusturuluyor (FCM devre disi)."
  e1_info "Mobil push icin Firebase Console > Project Settings > Service Accounts >"
  e1_info "Generate new private key, indirilen JSON'u ${INSTALL_DIR}/fcm-service-account.json"
  e1_info "olarak kaydedin, sonra: sudo bash update.sh backend"
  cat > fcm-service-account.json <<'PLACEHOLDER'
{
  "_comment": "FCM service account placeholder. Bu dosya bos JSON; backend fcm.py bunu yukleyemez ve FCM'i devre disi birakir. Gercek service account icin Firebase Console'dan indirip uzerine yazin.",
  "type": "service_account",
  "project_id": "",
  "private_key_id": "",
  "private_key": "",
  "client_email": "",
  "client_id": "",
  "_disabled": true
}
PLACEHOLDER
  # chmod 644: container'daki backend user'i (uid genelde 10001/appuser)
  # host uid 1000'in chmod 600 dosyasini okuyamaz → PermissionError. 644
  # dosyayi world-readable yapar (key icerigine erisim icin zaten host
  # erisimi gerek; multi-tenant host'ta degiliz, single-app deployment).
  chmod 644 fcm-service-account.json
  e1_chown_target fcm-service-account.json
  e1_warn "FCM placeholder yaratildi. Gercek JSON yuklenmeden mobil push CALISMAZ."
else
  e1_info "fcm-service-account.json mevcut — FCM aktif olacak."
  # Mevcut dosya chmod 600 olabilir (eski install veya elle scp'lendi).
  # Container okuyamaz; 644'e cek.
  current_mode="$(stat -c %a fcm-service-account.json 2>/dev/null || echo 600)"
  if [[ "$current_mode" != "644" ]]; then
    chmod 644 fcm-service-account.json
    e1_info "fcm-service-account.json chmod 644 yapildi (container erisimi icin)."
  fi
fi

# NATS bcrypt hash render — host python3 + bcrypt ile.
if [[ ! -f infra/nats/nats-server.conf ]]; then
  e1_info "NATS bcrypt hash'leri uretiliyor (python3 + bcrypt)..."
  if ! python3 -c "import bcrypt" 2>/dev/null; then
    e1_info "python3-bcrypt eksik, apt ile kuruluyor..."
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
  [[ -z "$HASH_G" || -z "$HASH_B" || -z "$HASH_W" ]] && e1_die "bcrypt uretilemedi."
  cp infra/nats/nats-server.conf.template infra/nats/nats-server.conf
  sed -i "s|{{NATS_GATEWAY_BCRYPT_HASH}}|${HASH_G//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_BACKEND_BCRYPT_HASH}}|${HASH_B//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_WORKER_BCRYPT_HASH}}|${HASH_W//&/\\&}|" infra/nats/nats-server.conf
  e1_chown_target infra/nats/nats-server.conf
  e1_ok "NATS auth konfigurasyonu render edildi."
else
  e1_ok "NATS auth konfigurasyonu zaten mevcut."
fi

# ---- 5/6: Build + up ------------------------------------------------------
e1_step "Servisler build ediliyor ve ayaga kaldiriliyor..."
e1_info "docker compose build --pull (yaklasik 3-8 dakika)..."
docker compose build --pull
e1_info "docker compose up -d..."
docker compose up -d
e1_ok "Stack ayakta."

# ---- 6/6: Backend healthy bekle + installer seed -------------------------
e1_step "Backend hazir olana kadar bekleniyor (max 2 dk)..."
backend_ready=0
for i in $(seq 1 60); do
  if docker compose exec -T backend-api curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    e1_ok "backend-api hazir (${i}. denemede)."
    backend_ready=1
    break
  fi
  sleep 2
done

if [[ $backend_ready -eq 1 ]]; then
  e1_info "Installer hesabi olusturuluyor (5 deneme, her birinde stdout/stderr gosterilir)..."
  # Backend healthy olsa bile schema/migration 1-2 saniye sonrasi tamamlanabilir.
  # 5 kez 3 saniye arayla dene. Cikti gizlenmez — kullanici hata sebebini gorur.
  seed_ok=0
  for attempt in 1 2 3 4 5; do
    # Subshell + `|| true` ile `set -e` altinda kirilmaz; cikti tee ile gosterilir.
    seed_output="$(docker compose exec -T backend-api python -m scripts.seed_installer 2>&1)" || true
    if echo "$seed_output" | grep -qE 'Installer user (created|password reset)'; then
      echo "$seed_output" | grep -E 'Installer user' | sed 's/^/      /'
      e1_ok "Installer hesabi hazir (${attempt}. denemede)."
      seed_ok=1
      break
    fi
    e1_warn "seed_installer denemesi ${attempt}/5 basarisiz. Cikti:"
    echo "$seed_output" | sed 's/^/      /'
    if [[ $attempt -lt 5 ]]; then
      sleep 3
    fi
  done
  if [[ $seed_ok -eq 0 ]]; then
    e1_warn "Installer hesabi otomatik olusturulamadi. Manuel calistirin:"
    e1_warn "  cd ${INSTALL_DIR}"
    e1_warn "  sudo docker compose exec -T backend-api python -m scripts.seed_installer"
  fi
else
  e1_warn "backend-api 2 dakikada hazir olmadi. Loglara bakin:"
  e1_warn "  cd ${INSTALL_DIR} && docker compose logs backend-api"
fi

# ---- (Opsiyonel) systemd servis kaydi -----------------------------------
# install.sh non-interactive (ASSUME_YES=1) modunda systemd entegrasyonu
# OTOMATIK yapilir. Interaktif modda kullaniciya sorulur. Eger zaten varsa
# (idempotent) bir sey degismez.
if [[ ! -f /etc/systemd/system/enerjione-grid.service ]]; then
  if [[ "${ASSUME_YES:-0}" == "1" ]] || e1_confirm "EnerjiOne Grid'i systemd servisi olarak kaydet? (sudo systemctl ile yonetim)"; then
    if [[ -f "${INSTALL_DIR}/infra/systemd/setup-systemd.sh" ]]; then
      bash "${INSTALL_DIR}/infra/systemd/setup-systemd.sh" || true
    fi
  fi
fi

# ---- Final rehber ---------------------------------------------------------
VPS_IP="$(e1_detect_ip)"
echo
echo "${E1_GREEN}${E1_BOLD}============================================================${E1_RESET}"
echo "${E1_GREEN}${E1_BOLD}  Kurulum tamamlandi.${E1_RESET}"
echo "${E1_GREEN}${E1_BOLD}============================================================${E1_RESET}"
echo
echo "  ${E1_BOLD}Web arayuzu :${E1_RESET}  http://${VPS_IP}/"
echo "  ${E1_BOLD}Backend API :${E1_RESET}  http://${VPS_IP}/api/v1"
echo "  ${E1_BOLD}NATS        :${E1_RESET}  nats://${VPS_IP}:4222 (auth: gateway/backend/worker)"
echo "  ${E1_BOLD}IEC 104 out :${E1_RESET}  ${VPS_IP}:2404-2406"
echo
echo "  ${E1_BOLD}Ilk giris:${E1_RESET}"
echo "    Kullanici : ${E1_CYAN}installer${E1_RESET}"
echo "    Sifre     : ${E1_CYAN}ChangeMe123!${E1_RESET}    ${E1_YELLOW}<<< MUTLAKA DEGISTIR${E1_RESET}"
echo
echo "  ${E1_BOLD}Yonetim komutlari (Docker Compose):${E1_RESET}"
echo "    Guncelleme  : cd ${INSTALL_DIR} && sudo bash update.sh"
echo "    Kaldirma    : cd ${INSTALL_DIR} && sudo bash uninstall.sh"
echo "    Servis log  : cd ${INSTALL_DIR} && docker compose logs -f"
echo "    Servis dur. : cd ${INSTALL_DIR} && docker compose ps"
echo
echo "  ${E1_BOLD}Yonetim komutlari (systemd, kaydedildi ise):${E1_RESET}"
echo "    Baslat      : sudo systemctl start enerjione-grid"
echo "    Durdur      : sudo systemctl stop enerjione-grid"
echo "    Yenile      : sudo systemctl restart enerjione-grid"
echo "    Durum       : sudo systemctl status enerjione-grid"
echo "    Canli log   : sudo journalctl -u enerjione-grid -f"
echo
echo "  ${E1_BOLD}Gateway eklemek icin:${E1_RESET}"
echo "    Web arayuzu > Muhendislik > Gateway Yonetimi > 'Yeni Gateway'"
echo "    Compose dosyasini indir, sahaya yukle, 'docker compose up -d'."
echo
