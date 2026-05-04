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
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SK}|" .env
  sed -i "s|^INTERNAL_SERVICE_TOKEN=.*|INTERNAL_SERVICE_TOKEN=${IT}|" .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PP}|" .env
  sed -i "s|^RABBITMQ_PASSWORD=.*|RABBITMQ_PASSWORD=${RP}|" .env
  echo "      Olusturuldu: $(realpath .env)"
else
  echo "[1/4] .env zaten var, atlandi."
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
