#!/usr/bin/env bash
# VDS guncelleme — git pull + sadece degisen servisleri build + restart.
#
# Kullanim (repo kokunden):
#   sudo bash infra/scripts/linux/update.sh           # tum degisiklikleri al
#   sudo bash infra/scripts/linux/update.sh frontend  # sadece frontend
#   sudo bash infra/scripts/linux/update.sh backend   # sadece backend-api
#
# Default davranis: git pull + butun servisleri yeniden build edip up et.
# Compose Docker'in build cache'ini kullanir, degismemis layer'lar yeniden
# inmez — gercek "tam yeniden derleme" sadece dosyalari degisen servislerde
# olur.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT_DIR"

TARGET="${1:-all}"

echo "[1/3] git pull..."
git pull --ff-only

case "$TARGET" in
  frontend|frontend-web|web)
    SVC="frontend-web"
    ;;
  backend|api|backend-api)
    SVC="backend-api"
    ;;
  alarm|alarm-service)
    SVC="alarm-service"
    ;;
  tag|tag-engine)
    SVC="tag-engine"
    ;;
  notification|notification-worker)
    SVC="notification-worker"
    ;;
  iec|iec104|iec104-outbound)
    SVC="iec104-outbound"
    ;;
  all|"")
    SVC=""
    ;;
  *)
    echo "Bilinmeyen servis: $TARGET"
    echo "Gecerli isimler: frontend, backend, alarm, tag, notification, iec, all"
    exit 2
    ;;
esac

if [[ -z "$SVC" ]]; then
  echo "[2/3] Tum servisler build ediliyor..."
  docker compose build
  echo "[3/3] Servisler ayaga kaldiriliyor (degisenler yeniden olusturulur)..."
  docker compose up -d
else
  echo "[2/3] Servis '$SVC' build ediliyor..."
  docker compose build "$SVC"
  echo "[3/3] Servis '$SVC' yeniden olusturuluyor..."
  docker compose up -d --force-recreate "$SVC"
fi

echo
echo "Durum:"
docker compose ps
echo
echo "Tamamlandi. Frontend tarayicidan Ctrl+Shift+R ile hard refresh edin."
