#!/usr/bin/env bash
# Sifirdan kurulum: .env yoksa rastgele secret'larla olustur, build, up, seed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT_DIR"

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

# NATS server.conf rendering — `.env` cleartext password'lerini bcrypt'leyip
# template'e gomer. Production deploy oncesi zorunlu; aksi halde NATS auth
# bypass yapar veya server boot olmaz.
if [[ ! -f infra/nats/nats-server.conf ]] || [[ "${1:-}" == "--rerender-nats" ]]; then
  echo "[1.5/4] NATS sifrelerinden bcrypt hash uretiliyor (docker run nats:2.10-alpine)..."
  # .env'den cleartext sifreleri oku (chmod 600 olmasi gerekli)
  set -a; source .env; set +a
  # nats CLI tarafindan bcrypt uret. `-p` flag cleartext'i bcrypt'e cevirir.
  HASH_G=$(docker run --rm nats:2.10-alpine nats server passwd -p "${NATS_GATEWAY_PASSWORD}" 2>/dev/null)
  HASH_B=$(docker run --rm nats:2.10-alpine nats server passwd -p "${NATS_BACKEND_PASSWORD}" 2>/dev/null)
  HASH_W=$(docker run --rm nats:2.10-alpine nats server passwd -p "${NATS_WORKER_PASSWORD}" 2>/dev/null)
  if [[ -z "$HASH_G" || -z "$HASH_B" || -z "$HASH_W" ]]; then
    echo "HATA: NATS bcrypt hash uretilemedi. docker erisimi var mi?" >&2
    exit 1
  fi
  # Template'i render et — `sed` ile placeholder'lari hash'lerle degistir.
  cp infra/nats/nats-server.conf.template infra/nats/nats-server.conf
  sed -i "s|{{NATS_GATEWAY_BCRYPT_HASH}}|${HASH_G//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_BACKEND_BCRYPT_HASH}}|${HASH_B//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_WORKER_BCRYPT_HASH}}|${HASH_W//&/\\&}|" infra/nats/nats-server.conf
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
