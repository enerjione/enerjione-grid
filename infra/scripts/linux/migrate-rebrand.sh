#!/usr/bin/env bash
# Rebrand migration script — eski /opt/enerjione deploy'unu /opt/enerjione-grid'e
# tasir, DB ismini 'enerjione' -> 'enerjione_grid' degisir, container proje
# adini gunceller, volume'lari yeniden adlandirir.
#
# Calistirma (VPS, sudo gerekli):
#   sudo bash infra/scripts/linux/migrate-rebrand.sh
#
# Adimlar:
#   1) Mevcut /opt/enerjione'da DB dump al (gecici dosya)
#   2) Mevcut compose stack'i durdur (volume'lar KORUNUR)
#   3) /opt/enerjione-grid dizinine repo clone
#   4) Eski .env'i kopyala + DB_NAME / DB_USER ayarlarini guncelle
#   5) Yeni stack'i ayaga kaldir (yeni DB ismi ile bos baslar)
#   6) DB dump'i yeni 'enerjione_grid' DB'sine restore
#   7) Eski /opt/enerjione dizinini /opt/enerjione.bak.<timestamp> olarak yedekle
#
# Geri donus: /opt/enerjione.bak.<timestamp> hala mevcut; yeni dir'i silip
# eskiyi geri tasiyabilirsin.
set -euo pipefail

OLD_DIR="${OLD_DIR:-/opt/enerjione}"
NEW_DIR="${NEW_DIR:-/opt/enerjione-grid}"
REPO_URL="${REPO_URL:-https://github.com/fikretsafak/EnerjiOneGrid.git}"
BRANCH="${BRANCH:-docker-linux-deploy}"
TS=$(date +%Y%m%d-%H%M%S)
DUMP_FILE="/tmp/enerjione-pre-rebrand-${TS}.sql.gz"

if [[ $EUID -ne 0 ]]; then
  echo "sudo ile calistir: sudo $0"
  exit 1
fi

if [[ ! -d "$OLD_DIR" ]]; then
  echo "HATA: $OLD_DIR yok — migrate edilecek mevcut deploy bulunamadi."
  exit 1
fi

if [[ -d "$NEW_DIR" ]]; then
  echo "HATA: $NEW_DIR zaten var. Onceki migration kalintilarini temizleyip tekrar dene."
  exit 1
fi

echo "=== EnerjiOne Grid rebrand migration ==="
echo "  Eski : $OLD_DIR"
echo "  Yeni : $NEW_DIR"
echo "  Dump : $DUMP_FILE"
echo

# 1) DB dump — eski stack ayakta olmali. Degilse once start ediyoruz.
echo "[1/7] DB dump aliniyor..."
cd "$OLD_DIR"
OLD_PG_USER=$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d= -f2-)
OLD_PG_DB=$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2-)
OLD_PG_USER="${OLD_PG_USER:-enerjione}"
OLD_PG_DB="${OLD_PG_DB:-enerjione}"

echo "  .env'den okundu -> user=$OLD_PG_USER db=$OLD_PG_DB"

# Eski stack ayakta degilse postgres'i baslat ki dump alalim
if ! docker ps --format '{{.Names}}' | grep -q '^e1-postgres$'; then
  echo "  -> eski postgres container ayakta degil, baslatiyorum..."
  docker compose up -d postgres
  # Hazir olana kadar bekle (max 60sn)
  for i in {1..30}; do
    if docker exec e1-postgres pg_isready -U "$OLD_PG_USER" -d "$OLD_PG_DB" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
fi

# Dump al — stderr'i de yakala ki hata mesajini gor
DUMP_ERR=/tmp/migrate-pg_dump-err.log
if docker exec -i e1-postgres pg_dump -U "$OLD_PG_USER" -d "$OLD_PG_DB" 2>"$DUMP_ERR" | gzip > "$DUMP_FILE"; then
  if [[ -s "$DUMP_ERR" ]]; then
    echo "  uyari: pg_dump stderr:"
    sed 's/^/    /' "$DUMP_ERR"
  fi
  echo "  -> $(du -h "$DUMP_FILE" | cut -f1)"
else
  echo "HATA: DB dump alinamadi. pg_dump stderr:"
  cat "$DUMP_ERR" || true
  echo
  echo "Manuel test:"
  echo "  docker exec -it e1-postgres psql -U $OLD_PG_USER -d $OLD_PG_DB -c '\\l'"
  exit 1
fi

# 2) Eski stack'i durdur
echo "[2/7] Eski compose stack durduruluyor..."
cd "$OLD_DIR"
docker compose down 2>/dev/null || true

# 3) Yeni dir + clone
echo "[3/7] $NEW_DIR'a repo clone..."
git clone --branch "$BRANCH" "$REPO_URL" "$NEW_DIR"

# 4) .env kopyala + guncelle
echo "[4/7] .env tasiniyor + DB ismi guncelleniyor..."
cp "$OLD_DIR/.env" "$NEW_DIR/.env"
# fcm-service-account varsa kopyala
if [[ -f "$OLD_DIR/fcm-service-account.json" ]]; then
  cp "$OLD_DIR/fcm-service-account.json" "$NEW_DIR/fcm-service-account.json"
fi

# POSTGRES_DB ve POSTGRES_USER'i 'enerjione_grid' yap
cd "$NEW_DIR"
sed -i 's/^POSTGRES_DB=.*/POSTGRES_DB=enerjione_grid/' .env
sed -i 's/^POSTGRES_USER=.*/POSTGRES_USER=enerjione_grid/' .env

# DATABASE_URL icindeki db ismini de guncelle (varsa)
sed -i 's|/enerjione|/enerjione_grid|g' .env

# 5) Yeni stack'i ayaga kaldir (bos DB ile)
echo "[5/7] Yeni stack ayaga kalkiyor (yeni 'enerjione_grid' DB ile)..."
docker compose up -d postgres
# postgres healthy olana kadar bekle
sleep 5
for i in {1..30}; do
  if docker compose exec -T postgres pg_isready -U enerjione_grid -d enerjione_grid >/dev/null 2>&1; then
    echo "  -> postgres ready."
    break
  fi
  sleep 2
done

# 6) Dump'i restore et
echo "[6/7] DB dump restore ediliyor..."
gunzip -c "$DUMP_FILE" | docker compose exec -T postgres psql -U enerjione_grid -d enerjione_grid

# 7) Tum stack'i ayaga kaldir
echo "[7/7] Tum stack ayaga kalkiyor..."
docker compose up -d

# Eski dir yedek
mv "$OLD_DIR" "${OLD_DIR}.bak.${TS}"

echo
echo "=== TAMAM ==="
echo "  Yeni deploy : $NEW_DIR"
echo "  Eski yedek  : ${OLD_DIR}.bak.${TS}"
echo "  DB dump     : $DUMP_FILE  (silmek istersen: rm $DUMP_FILE)"
echo
echo "Test:"
echo "  curl http://localhost/api/v1/health"
echo
echo "Geri donus istersen:"
echo "  sudo docker compose -f $NEW_DIR/docker-compose.yml down -v"
echo "  sudo mv ${OLD_DIR}.bak.${TS} $OLD_DIR"
echo "  cd $OLD_DIR && sudo docker compose up -d"
