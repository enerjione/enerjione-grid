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

# Lokal degisikliklere karsi koruma: git pull --ff-only baska konflikte
# girerse rebase/merge denenmemeli (operator manuel cozsun).
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "HATA: Repo'da commit edilmemis lokal degisiklikler var."
  echo "      'git status' ile inceleyin; stash veya commit edin ve tekrar deneyin."
  exit 1
fi

# DB yedek alma (migration kotu giderse rollback yolu). docker compose
# yukseltmeden once yapilir — postgres container ayakta olmali.
if docker compose ps postgres --status running --quiet | grep -q .; then
  TS=$(date +%Y%m%d-%H%M%S)
  BACKUP_FILE="backups/auto-pre-update-${TS}.sql.gz"
  mkdir -p backups
  echo "[1.5/3] DB yedek aliniyor: ${BACKUP_FILE}"
  # Postgres user/db .env'den okunur; bash ile substitute.
  PG_USER="$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d= -f2- || echo enerjione)"
  PG_DB="$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2- || echo enerjione)"
  if ! docker compose exec -T postgres pg_dump -U "${PG_USER:-enerjione}" -d "${PG_DB:-enerjione}" \
       | gzip > "${BACKUP_FILE}"; then
    echo "UYARI: DB yedek alinamadi; update yine de devam ediyor."
    # Bos/yarim yedek dosyasini sil
    rm -f "${BACKUP_FILE}"
  else
    SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    echo "       Yedek tamam: ${BACKUP_FILE} (${SIZE})"
  fi
else
  echo "[1.5/3] Postgres container ayakta degil — yedek atlandi."
fi

echo "[1/3] git pull..."
git pull --ff-only

# Bootstrap idempotency: .env yoksa olusturulur; NATS sifreleri placeholder'da
# (please-change-me-) ise bootstrap.sh `.env` icindeki cleartext'leri rastgele
# uretip yazar. nats-server.conf yoksa (veya bind mount kazasi sonucu dizin
# olarak kalmissa) sil ve template'ten yeniden render et.
if [[ -d infra/nats/nats-server.conf ]]; then
  echo "[1.6/3] infra/nats/nats-server.conf yanlislikla DIZIN olarak duruyor (docker bind mount kazasi); siliniyor..."
  rm -rf infra/nats/nats-server.conf
fi
need_bootstrap=0
if [[ ! -f .env ]]; then
  echo "[1.7/3] .env yok — bootstrap.sh ile rastgele sifreler uretiliyor."
  need_bootstrap=1
elif grep -qE '^NATS_(GATEWAY|BACKEND|WORKER)_PASSWORD=please-change-me' .env; then
  echo "[1.7/3] .env'de NATS sifreleri hala placeholder — bootstrap.sh ile dolduruluyor."
  need_bootstrap=1
fi
if [[ ! -f infra/nats/nats-server.conf ]]; then
  echo "[1.8/3] nats-server.conf yok — bootstrap.sh ile template'ten render edilecek."
  need_bootstrap=1
fi
if [[ $need_bootstrap -eq 1 ]]; then
  bash infra/scripts/linux/bootstrap.sh --rerender-nats
  # bootstrap.sh basarili olunca burada devam et — bootstrap kendi build/up yapar
  # ama --rerender-nats sonrasi update.sh'in build/up mantigi ZATEN gerekli
  # degildi: idempotent oldugu icin alttaki adimlar (build + up) yine calisir.
fi

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
