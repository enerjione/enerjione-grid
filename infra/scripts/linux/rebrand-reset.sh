#!/usr/bin/env bash
# EnerjiOne rebrand FULL RESET — eski Horstman/HSL kurulumunu komple temizler,
# yeni EnerjiOne stack'ini sifirdan ayaga kaldirir.
#
# Kullanim (repo kokunden):
#   sudo bash infra/scripts/linux/rebrand-reset.sh           # interaktif (DB silmeden once sorar)
#   sudo bash infra/scripts/linux/rebrand-reset.sh --keep-db # DB volume'unu KORUR (mevcut veri kalir)
#   sudo bash infra/scripts/linux/rebrand-reset.sh --yes     # tum onaylari atla, DB DAHIL her seyi sifirla
#
# Yapilan islemler (sirayla):
#   1. Eski hsl-* container'lari durdur + sil
#   2. Yeni e1-* compose stack'i durdur (varsa)
#   3. Eski hsl/* docker image'lari sil (disk temizligi)
#   4. (Opsiyonel) postgres-data + rabbitmq-data + nats-data volume'larini sifirla
#   5. git pull (--ff-only)
#   6. .env'i kontrol et (POSTGRES_DB, RABBITMQ_USER eski isimlerde ise uyari)
#   7. docker compose build + up
#   8. Health check + servis durumu

set -euo pipefail

# Renkli output (terminal destekliyorsa)
if [[ -t 1 ]]; then
  RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi

log()  { echo "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo "${GREEN}[OK]${NC} $*"; }
warn() { echo "${YELLOW}[UYARI]${NC} $*"; }
err()  { echo "${RED}[HATA]${NC} $*" >&2; }

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT_DIR"

# Arg parsing
KEEP_DB=false
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --keep-db) KEEP_DB=true ;;
    --yes|-y)  ASSUME_YES=true ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^#\s*//' | head -25
      exit 0
      ;;
    *) err "Bilinmeyen parametre: $arg"; exit 2 ;;
  esac
done

confirm() {
  local prompt="$1"
  if $ASSUME_YES; then return 0; fi
  read -r -p "$prompt [y/N]: " ans
  [[ "$ans" =~ ^[yY]([eE][sS])?$ ]]
}

# Root kontrolu
if [[ $EUID -ne 0 ]]; then
  err "Bu script sudo ile calistirilmali (docker komutlari root gerektirir)."
  exit 1
fi

echo
echo "${BLUE}============================================================${NC}"
echo "  EnerjiOne Rebrand — Full Reset"
echo "  Eski HSL kurulumu temizlenecek, yeni e1 stack'i ayaga kalkacak"
echo "${BLUE}============================================================${NC}"
echo
echo "  Repo: $ROOT_DIR"
echo "  DB volume: $($KEEP_DB && echo 'KORUNACAK (--keep-db)' || echo 'SIFIRLANACAK')"
echo "  Onay modu: $($ASSUME_YES && echo 'OTOMATIK (--yes)' || echo 'INTERAKTIF')"
echo

if ! $ASSUME_YES; then
  if ! confirm "Devam edilsin mi?"; then
    log "Iptal edildi."
    exit 0
  fi
fi

# ===========================================================================
# 1. Eski hsl-* container'larini durdur ve sil
# ===========================================================================
log "[1/8] Eski hsl-* container'lari durduruluyor ve siliniyor..."
OLD_CONTAINERS=$(docker ps -a --filter "name=^hsl-" --format '{{.Names}}' || true)
if [[ -n "$OLD_CONTAINERS" ]]; then
  echo "$OLD_CONTAINERS" | while read -r name; do
    log "  - $name durduruluyor..."
    docker stop "$name" 2>/dev/null || true
    docker rm "$name" 2>/dev/null || true
  done
  ok "Eski hsl-* container'lari temizlendi."
else
  ok "Eski hsl-* container'i bulunmadi."
fi

# ===========================================================================
# 2. Yeni e1-* compose stack'i durdur (varsa)
# ===========================================================================
log "[2/8] Mevcut compose stack durduruluyor (varsa)..."
docker compose down --remove-orphans 2>/dev/null || true
ok "Compose stack durduruldu."

# ===========================================================================
# 3. Eski hsl/* docker image'lari sil
# ===========================================================================
log "[3/8] Eski hsl/* image'lari temizleniyor..."
OLD_IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^hsl/' || true)
if [[ -n "$OLD_IMAGES" ]]; then
  echo "$OLD_IMAGES" | while read -r img; do
    log "  - $img siliniyor..."
    docker rmi -f "$img" 2>/dev/null || true
  done
  ok "Eski hsl/* image'lari silindi."
else
  ok "Eski hsl/* image'i bulunmadi."
fi

# Dangling/orphan image temizligi
log "  Dangling image temizligi..."
docker image prune -f 2>/dev/null || true

# ===========================================================================
# 4. Volume'lari sifirla (opsiyonel)
# ===========================================================================
log "[4/8] Volume durumu kontrol ediliyor..."
# Hem eski compose proje adlariyla olusmus volume'lar (horstman-smart-logger_*,
# horstman_*) hem de jenerik suffix'liler (postgres-data, rabbitmq-data, ...)
# yakalanir. Yeni compose `name: enerjione` ile `enerjione_*` olusturur.
VOLUMES=$(docker volume ls --format '{{.Name}}' | grep -E '(^horstman|_postgres-data|_rabbitmq-data|_nats-data|_backup-data|^postgres-data$|^rabbitmq-data$|^nats-data$|^backup-data$)' || true)

if $KEEP_DB; then
  warn "DB volume'lari KORUNACAK (--keep-db). Eski POSTGRES_DB ismi (horstman)"
  warn "yeni .env'de POSTGRES_DB=enerjione ile uyumsuz olabilir; cakisirsa"
  warn "elle volume silmeniz gerek: docker volume rm <volume_name>"
else
  if [[ -n "$VOLUMES" ]]; then
    echo
    warn "Asagidaki volume'lar silinecek (TUM TELEMETRI + ALARM + AYAR VERILERI KAYBOLACAK):"
    echo "$VOLUMES" | sed 's/^/    /'
    echo
    if confirm "Volume'lar gercekten silinsin mi?"; then
      echo "$VOLUMES" | while read -r vol; do
        log "  - $vol siliniyor..."
        docker volume rm "$vol" 2>/dev/null || true
      done
      ok "Volume'lar silindi (temiz baslangic)."
    else
      warn "Volume'lar korundu. POSTGRES_DB uyumsuzlugu olabilir."
    fi
  else
    ok "Silinecek volume bulunmadi (zaten temiz)."
  fi
fi

# ===========================================================================
# 5. Git pull
# ===========================================================================
log "[5/8] Git pull (en son commit'i cekiyor)..."
if ! git pull --ff-only; then
  err "Git pull basarisiz. Local degisiklik var mi? 'git status' kontrol edin."
  err "Devam edilemiyor; degisiklikleri stash/commit edip yeniden deneyin."
  exit 1
fi
ok "Repo guncel."

# ===========================================================================
# 6. .env kontrolu
# ===========================================================================
log "[6/8] .env dosyasi kontrol ediliyor..."
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    warn ".env bulunamadi; .env.example'dan kopyalaniyor."
    cp .env.example .env
    warn ".env icindeki SECRET_KEY, INTERNAL_SERVICE_TOKEN, POSTGRES_PASSWORD,"
    warn "RABBITMQ_PASSWORD degerlerini MUTLAKA DEGISTIRIN (openssl rand -hex 32)."
    if ! $ASSUME_YES; then
      read -r -p "  Simdi nano ile acmak ister misiniz? [y/N]: " ans
      [[ "$ans" =~ ^[yY] ]] && ${EDITOR:-nano} .env
    fi
  else
    err ".env de .env.example da bulunamadi. Repo bozuk olabilir."
    exit 1
  fi
fi

# Eski ismler hala duruyorsa uyar
if grep -qE '^POSTGRES_DB=horstman$' .env 2>/dev/null; then
  warn ".env'de POSTGRES_DB=horstman (eski isim). Yeni default: enerjione."
  warn "  - Eski DB volume'unuzu KORUMAK istiyorsaniz .env'de bu degeri TUTUN."
  warn "  - Temiz baslangic istiyorsaniz POSTGRES_DB=enerjione yapin + volume sifirlayin."
fi

# ===========================================================================
# 7. Build + up
# ===========================================================================
log "[7/8] Yeni e1-* stack build ediliyor (yaklasik 3-8 dakika)..."
if ! docker compose build; then
  err "docker compose build basarisiz oldu."
  exit 1
fi
ok "Build tamamlandi."

log "    Servisler ayaga kaldiriliyor..."
if ! docker compose up -d; then
  err "docker compose up basarisiz oldu."
  exit 1
fi
ok "Stack ayakta."

# ===========================================================================
# 8. Health check + durum
# ===========================================================================
log "[8/8] Servis durumu (10 sn bekleyip kontrol)..."
sleep 10

echo
echo "${BLUE}--- docker compose ps ---${NC}"
docker compose ps
echo

# NATS stream'leri ensure oldu mu kontrol (backend startup'ta yapar)
log "Backend log'unda jetstream_bus baslama mesaji araniyor..."
if docker compose logs --tail=200 backend-api 2>/dev/null | grep -q "jetstream_bus_started"; then
  ok "Backend JetStream'e baglandi, stream'ler hazir."
else
  warn "JetStream baslama mesaji henuz gorulmedi. 'docker compose logs backend-api' ile kontrol edin."
fi

# Frontend portu acik mi
FRONTEND_PORT="${FRONTEND_HTTP_PORT:-80}"
if curl -fsS -o /dev/null "http://localhost:${FRONTEND_PORT}" 2>/dev/null; then
  ok "Frontend port ${FRONTEND_PORT} cevap veriyor."
else
  warn "Frontend port ${FRONTEND_PORT} henuz cevap vermiyor; nginx startup biraz daha bekleyebilir."
fi

echo
echo "${GREEN}============================================================${NC}"
echo "${GREEN}  EnerjiOne stack hazir!${NC}"
echo "${GREEN}============================================================${NC}"
echo
echo "  Frontend:        http://<vds-ip>:${FRONTEND_PORT}/"
echo "  Backend health:  http://<vds-ip>:8000/api/v1/health"
echo "  RabbitMQ admin:  http://<vds-ip>:15672/  (sadece localhost; ssh tunel)"
echo "  NATS monitor:    http://<vds-ip>:8222/   (sadece localhost; ssh tunel)"
echo
echo "  Loglari izlemek icin:  docker compose logs -f"
echo "  Servis durumu:         docker compose ps"
echo "  Tarayicidan acarken:   Ctrl+Shift+R (hard refresh; favicon + yeni title)"
echo
