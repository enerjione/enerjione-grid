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

e1_set_steps 5

# ---- 1/5: Lokal degisiklik kontrolu --------------------------------------
e1_step "Lokal degisiklik kontrolu..."
if ! git diff --quiet || ! git diff --cached --quiet; then
  e1_die "Repo'da commit edilmemis lokal degisiklik var. 'git status' ile inceleyin, stash veya commit edin."
fi
e1_ok "Calisma agaci temiz."

# ---- 2/5: DB yedek (otomatik, postgres ayaktaysa) ------------------------
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

# ---- 3/5: Git pull --------------------------------------------------------
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

# ---- 4/5: Build + up ------------------------------------------------------
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

NEEDS_BACKEND=0
if [[ -z "$SVC" || "$SVC" == "backend-api" ]]; then
  NEEDS_BACKEND=1
fi

if [[ "$NEEDS_BACKEND" -eq 1 ]]; then
  # Historian (TimescaleDB) gibi DB image degisiklikleri backend migration'dan
  # once uygulanmali. postgres named volume korunur; sadece image/compose farki
  # varsa container recreate edilir. `pull` local/offline kurulumda hata verirse
  # update'i bozmasin; compose eldeki image ile devam eder.
  e1_step "Postgres image/compose senkronize ediliyor (TimescaleDB hazirligi)..."
  docker compose pull postgres 2>/dev/null || true
  docker compose up -d postgres
  e1_ok "Postgres hazir."
fi

if [[ -z "$SVC" ]]; then
  e1_step "Tum servisler yeniden derleniyor + ayaga kalkiyor..."
  docker compose build
  docker compose up -d
else
  e1_step "Servis '$SVC' yeniden derleniyor + force-recreate..."
  docker compose build "$SVC"
  docker compose up -d --force-recreate "$SVC"
fi

# ---- 5/5: Alembic migration (backend/all) ---------------------------------
if [[ "$NEEDS_BACKEND" -eq 1 ]]; then
  e1_step "DB migration uygulanıyor (alembic upgrade head)..."
  # Eski kurulumlar (create_all + legacy bootstrap) Alembic'e stamp'lenmemis
  # olabilir. alembic_version yoksa mevcut schema'yi 0006 kabul edip sadece yeni
  # migration'lari (0007+) uygula. Aksi halde 0001..0006 tekrar calisip mevcut
  # tablo/kolonlarda patlar. Yeni/temiz DB'de backend startup create_all zaten
  # mevcut metadata'yi kurar; 0006 stamp + 0007 upgrade yine dogru sonucu verir.
  if ! docker compose exec -T backend-api python - <<'PY' | grep -q '^YES$'; then
from sqlalchemy import create_engine, text
from app.core.config import settings
engine = create_engine(settings.database_url, pool_pre_ping=True)
with engine.connect() as conn:
    exists = conn.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
print('YES' if exists else 'NO')
PY
    e1_info "alembic_version yok — mevcut schema 0006 olarak stamp'leniyor..."
    docker compose exec -T backend-api alembic stamp 0006
  fi
  docker compose exec -T backend-api alembic upgrade head
  e1_ok "DB migration tamam."

  # Historian (TimescaleDB) idempotent ensure. Migration 0007 extension'i
  # `CREATE EXTENSION` ile kurar AMA shared_preload_libraries ayarlanmamis
  # eski volume'de bu adim basarisiz olabilir (extension kurulamaz -> hypertable
  # /aggregate atlanir, migration yine 0007'ye ilerler). Compose'a
  # shared_preload_libraries=timescaledb eklendikten + postgres recreate
  # edildikten sonra burada eksik parcalari (extension/hypertable/policy/
  # aggregate) IF NOT EXISTS ile tamamlariz. Tumu idempotent; her update'te
  # guvenle tekrar calisir.
  e1_step "Historian (TimescaleDB hypertable) dogrulaniyor..."
  if docker compose exec -T backend-api python - <<'PY'
import sys
from sqlalchemy import create_engine, text
from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, isolation_level="AUTOCOMMIT")
stmts = [
    "CREATE EXTENSION IF NOT EXISTS timescaledb",
    "SELECT create_hypertable('telemetry_history','source_timestamp',"
    " chunk_time_interval => INTERVAL '1 day', migrate_data => TRUE, if_not_exists => TRUE)",
    "SELECT add_retention_policy('telemetry_history', INTERVAL '90 days', if_not_exists => TRUE)",
    "ALTER TABLE telemetry_history SET ("
    " timescaledb.compress,"
    " timescaledb.compress_segmentby = 'device_id, signal_key',"
    " timescaledb.compress_orderby = 'source_timestamp DESC')",
    "SELECT add_compression_policy('telemetry_history', INTERVAL '7 days', if_not_exists => TRUE)",
    "CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_history_1m"
    " WITH (timescaledb.continuous) AS"
    " SELECT device_id, signal_key,"
    "        time_bucket(INTERVAL '1 minute', source_timestamp) AS bucket,"
    "        avg(value) AS avg_value, min(value) AS min_value,"
    "        max(value) AS max_value, count(*) AS sample_count"
    " FROM telemetry_history GROUP BY device_id, signal_key, bucket"
    " WITH NO DATA",
    "SELECT add_continuous_aggregate_policy('telemetry_history_1m',"
    " start_offset => INTERVAL '3 hours', end_offset => INTERVAL '1 minute',"
    " schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE)",
    "CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_history_1h"
    " WITH (timescaledb.continuous) AS"
    " SELECT device_id, signal_key,"
    "        time_bucket(INTERVAL '1 hour', source_timestamp) AS bucket,"
    "        avg(value) AS avg_value, min(value) AS min_value,"
    "        max(value) AS max_value, count(*) AS sample_count"
    " FROM telemetry_history GROUP BY device_id, signal_key, bucket"
    " WITH NO DATA",
    "SELECT add_continuous_aggregate_policy('telemetry_history_1h',"
    " start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',"
    " schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE)",
]
with engine.connect() as conn:
    for s in stmts:
        try:
            conn.execute(text(s))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN atlandi: {exc}", file=sys.stderr)
    hyper = conn.scalar(text(
        "SELECT count(*) FROM timescaledb_information.hypertables"
        " WHERE hypertable_name='telemetry_history'"
    ))
print("HYPERTABLE_OK" if hyper else "HYPERTABLE_MISSING")
PY
  then
    e1_ok "Historian hazir (hypertable + aggregate + retention)."
  else
    e1_warn "Historian ensure calisti ama dogrulama beklenenden farkli — loglari kontrol edin."
  fi
fi

echo
echo "${E1_GREEN}${E1_BOLD}Update tamamlandi.${E1_RESET}"
echo
echo "  ${E1_BOLD}Servis durumu:${E1_RESET}"
docker compose ps
echo
echo "  ${E1_DIM}Tarayicidan Ctrl+Shift+R ile hard refresh edin (yeni frontend).${E1_RESET}"
echo
