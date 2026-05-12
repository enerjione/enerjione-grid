#!/usr/bin/env bash
# Sifirdan kurulum: .env yoksa rastgele secret'larla olustur, build, up, seed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT_DIR"

# Sudo ile cagrildiysa (SUDO_USER set), `.env` ve uretilecek dosyalarin
# sahibini root degil cagiran kullanici yap. Aksi halde:
#   * .env chmod 600 + root:root → non-root shell'den `docker compose` "open .env:
#     permission denied" verir.
#   * `infra/nats/nats-server.conf` da root sahipli olur, ileride update.sh
#     git pull sirasinda bu dosyaya dokunamaz.
# `INSTALL_USER` env override'i da kabul ederiz (advance kullanim).
TARGET_USER="${INSTALL_USER:-${SUDO_USER:-}}"
TARGET_UID=""
TARGET_GID=""
if [[ -n "$TARGET_USER" ]] && id -u "$TARGET_USER" >/dev/null 2>&1; then
  TARGET_UID="$(id -u "$TARGET_USER")"
  TARGET_GID="$(id -g "$TARGET_USER")"
fi
# Sonradan secret dosyalarinin sahipligini hizaya getiren helper.
_chown_target() {
  if [[ -n "$TARGET_UID" && -n "$TARGET_GID" ]]; then
    chown "${TARGET_UID}:${TARGET_GID}" "$@" 2>/dev/null || true
  fi
}

# Helper: .env'de bir env satiri yoksa ekler, varsa value'su bos/placeholder
# ise gercek deger ile doldurur (idempotent). Eski deploylarda .env onceden
# olusturulmus olabilir; sonradan eklenen yeni env'ler (orn. NATS_*_PASSWORD)
# orada hic olmayabilir — `set -u` ile sourced'da unbound variable patlar.
_ensure_env_var() {
  local key="$1"
  local value="$2"
  if grep -qE "^${key}=" .env; then
    # Var ama bos veya placeholder ise doldur (please-change-me-* veya bos)
    if grep -qE "^${key}=$|^${key}=please-change-me" .env; then
      sed -i "s|^${key}=.*|${key}=${value}|" .env
      echo "      ${key} guncellendi (placeholder -> rastgele)."
    fi
  else
    # Yok — yeni satir ekle
    echo "${key}=${value}" >> .env
    echo "      ${key} eklendi (.env'de yoktu)."
  fi
}

if [[ ! -f .env ]]; then
  echo "[1/4] .env dosyasi olusturuluyor (rastgele secret'larla)..."
  cp .env.example .env
  chmod 600 .env
  _chown_target .env
fi
# Idempotent: .env zaten olsa bile eksik / placeholder secret'lari doldur.
# Boylece eski deploylar yeni eklenen env'leri (NATS_*_PASSWORD vb.) otomatik
# kazanir. Random secret'lar her cagrida YENIDEN URETILMEZ — yalniz eksikse.
echo "[1/4] .env secret kontrolu (idempotent)..."
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
# Secret leak koruma: .env dosyasini sadece sahibi okuyabilsin.
# Multi-user host'larda umask=022 (default) world-readable yapardi.
if [[ "$(stat -c %a .env 2>/dev/null)" != "600" ]]; then
  chmod 600 .env
  echo "      .env izinleri 600'e dusuruldu."
fi
# Sahiplik hizalama: sudo ile cagrildiysa .env root:root olmasin; aksi halde
# cagiran kullanici (`fikretsafak` vs.) `docker compose` ile .env'i okuyamaz
# (compose .env'i client tarafinda okur, container'a mount etmez).
_chown_target .env

# NATS server.conf rendering — `.env` cleartext password'lerini bcrypt'leyip
# template'e gomer. Production deploy oncesi zorunlu; aksi halde NATS auth
# bypass yapar veya server boot olmaz.
#
# Bcrypt uretimi: HOST'ta python3 + bcrypt modulu ile yapilir. Onceki versiyon
# `docker run nats:2.10-alpine nats server passwd` cagiriyordu ama o komut
# resmi nats-server image'inde YOKTUR (ayri `nats-cli` paketinde). Hash sessiz
# bos doner, script "HATA" demeden ya da NATS auth bypass'la calismaya devam
# edebiliyordu. Python bcrypt deterministic ve image'den bagimsiz — NATS
# `$2a$...` formatini herhangi bcrypt uretiminden kabul eder.
if [[ ! -f infra/nats/nats-server.conf ]] || [[ "${1:-}" == "--rerender-nats" ]]; then
  echo "[1.5/4] NATS sifrelerinden bcrypt hash uretiliyor (host python3 + bcrypt)..."
  # .env'den cleartext sifreleri oku (chmod 600 olmasi gerekli)
  set -a; source .env; set +a

  # python3 + bcrypt modulu var mi kontrol et; yoksa apt ile yukle.
  if ! python3 -c "import bcrypt" 2>/dev/null; then
    echo "      python3-bcrypt eksik, apt ile kuruluyor..."
    if command -v apt-get >/dev/null 2>&1; then
      DEBIAN_FRONTEND=noninteractive apt-get update -qq
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-bcrypt
    else
      echo "HATA: apt-get bulunamadi. python3-bcrypt'i elle kurun:" >&2
      echo "      pip3 install bcrypt   (veya distro paket yoneticinizle)" >&2
      exit 1
    fi
  fi

  # Python ile bcrypt uret. NATS server `$2a$...` veya `$2b$...` her ikisini
  # kabul eder; bcrypt modulu default `$2b$` uretir, sorun yok.
  # Cleartext'i stdin'den ver — komut satirinda sifre process listesinde
  # gozukmesin (ps -ef ile baska kullanicilar yakalamasin).
  _bcrypt() {
    local pw="$1"
    python3 -c "import sys, bcrypt; print(bcrypt.hashpw(sys.stdin.buffer.read().rstrip(b'\n'), bcrypt.gensalt(rounds=11)).decode())" <<<"$pw"
  }
  HASH_G=$(_bcrypt "${NATS_GATEWAY_PASSWORD}")
  HASH_B=$(_bcrypt "${NATS_BACKEND_PASSWORD}")
  HASH_W=$(_bcrypt "${NATS_WORKER_PASSWORD}")
  if [[ -z "$HASH_G" || -z "$HASH_B" || -z "$HASH_W" ]]; then
    echo "HATA: bcrypt hash uretilemedi (python3-bcrypt calismadi)." >&2
    exit 1
  fi
  # Template'i render et — `sed` ile placeholder'lari hash'lerle degistir.
  cp infra/nats/nats-server.conf.template infra/nats/nats-server.conf
  sed -i "s|{{NATS_GATEWAY_BCRYPT_HASH}}|${HASH_G//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_BACKEND_BCRYPT_HASH}}|${HASH_B//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_WORKER_BCRYPT_HASH}}|${HASH_W//&/\\&}|" infra/nats/nats-server.conf
  _chown_target infra/nats/nats-server.conf
  echo "      nats-server.conf render edildi."
fi

echo "[2/4] Imajlar build ediliyor..."
docker compose build --pull

echo "[3/4] Servisler ayaga kaldiriliyor..."
docker compose up -d

echo "[4/4] Backend hazir olana kadar bekleniyor..."
backend_ready=0
for i in $(seq 1 60); do
  if docker compose exec -T backend-api curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    echo "      backend-api hazir (${i}. denemede)."
    backend_ready=1
    break
  fi
  sleep 2
done

if [[ $backend_ready -eq 1 ]]; then
  # Default installer hesabini idempotent olarak olustur. Zaten varsa script
  # no-op'tur. Manuel asama kalmasin diye otomatik cagriyoruz; ilk girisinde
  # operator sifreyi degistirir.
  echo "[4.5/4] Default installer hesabi olusturuluyor/dogrulaniyor..."
  if docker compose exec -T backend-api python -m scripts.seed_installer 2>&1; then
    echo "      Installer hesabi hazir."
  else
    echo "      UYARI: seed_installer basarisiz oldu. Manuel calistirin:"
    echo "        docker compose exec backend-api python -m scripts.seed_installer"
  fi
else
  echo "UYARI: backend-api 2 dakika icinde hazir olmadi. Loglara bakin:"
  echo "  docker compose logs backend-api"
  echo "Installer hesabini manuel kurmak icin:"
  echo "  docker compose exec backend-api python -m scripts.seed_installer"
fi

# Kurulum sonrasi rehberi — operator hangi adresi acmali, hangi credentials
# ile girmeli, gateway nasil eklemeli.
VPS_IP=$(curl -fsS --max-time 3 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "<vds-ip>")
echo
echo "============================================================"
echo "Kurulum tamamlandi. Sahip oldugun servisler:"
echo "  * Frontend     : http://${VPS_IP}/"
echo "  * Backend API  : http://${VPS_IP}:8000/api/v1"
echo "  * NATS         : nats://${VPS_IP}:4222 (auth: gateway/backend/worker)"
echo "  * RabbitMQ UI  : http://${VPS_IP}:15672 (yalnizca localhost'tan)"
echo
echo "Ilk giris:"
echo "  Kullanici : installer"
echo "  Sifre     : ChangeMe123!"
echo "  >>> Giriste sifreni MUTLAKA degistir <<<"
echo
echo "Yeni gateway eklemek icin (sahaya kurulacak DNP3 gateway):"
echo "  1. Frontend > Muhendislik > Gateway Yonetimi > 'Yeni Gateway'"
echo "  2. Kod ve isim ver, 'Olustur'"
echo "  3. 'Compose dosyasini indir' butonu — dosya OTOMATIK olarak NATS"
echo "     parola gomulu halde gelir, sahaya yukle ve 'docker compose up -d'."
echo "============================================================"
