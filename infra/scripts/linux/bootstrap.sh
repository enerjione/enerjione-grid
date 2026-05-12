#!/usr/bin/env bash
# Sifirdan kurulum: .env yoksa rastgele secret'larla olustur, build, up, seed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "[1/4] .env dosyasi olusturuluyor (rastgele secret'larla)..."
  cp .env.example .env
  # Rastgele secret'lar uret
  SK=$(openssl rand -hex 32)
  IT=$(openssl rand -hex 32)
  PP=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
  RP=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
  # NATS (gateway / backend / worker) sifreleri
  NB=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
  NW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
  NG=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SK}|" .env
  sed -i "s|^INTERNAL_SERVICE_TOKEN=.*|INTERNAL_SERVICE_TOKEN=${IT}|" .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PP}|" .env
  sed -i "s|^RABBITMQ_PASSWORD=.*|RABBITMQ_PASSWORD=${RP}|" .env
  sed -i "s|^NATS_BACKEND_PASSWORD=.*|NATS_BACKEND_PASSWORD=${NB}|" .env
  sed -i "s|^NATS_WORKER_PASSWORD=.*|NATS_WORKER_PASSWORD=${NW}|" .env
  sed -i "s|^NATS_GATEWAY_PASSWORD=.*|NATS_GATEWAY_PASSWORD=${NG}|" .env
  # Secret leak koruma: .env dosyasini sadece sahibi okuyabilsin.
  # Multi-user host'larda umask=022 (default) world-readable yapardi.
  chmod 600 .env
  echo "      Olusturuldu: $(realpath .env) (chmod 600)"
else
  echo "[1/4] .env zaten var, atlandi."
  # Eski .env'in izinleri laxsa sertlestir (idempotent).
  if [[ "$(stat -c %a .env 2>/dev/null)" != "600" ]]; then
    chmod 600 .env
    echo "      .env izinleri 600'e dusuruldu."
  fi
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
for i in $(seq 1 30); do
  if docker compose exec -T backend-api curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    echo "      backend-api hazir."
    break
  fi
  sleep 2
done

echo
echo "Default installer hesabini olusturmak/sifirlamak icin:"
echo "  docker compose exec backend-api python -m scripts.seed_installer"
echo
echo "Sonra browser'dan acin:  http://<vds-ip>/"
echo "  Kullanici: installer"
echo "  Sifre:    ChangeMe123!  (giriste mutlaka degistirin)"
