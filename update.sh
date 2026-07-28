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
#   sudo bash update.sh modbus         # modbus-outbound (Modbus TCP yayini)
#   sudo bash update.sh ftp            # ftp-server (cihaz config transfer)
#   sudo bash update.sh whatsapp       # whatsapp-web-gateway (Baileys sidecar)
#
# Appliance (mini PC) kurulumlarinda host katmani (ag ajani e1-netd, systemd
# unit'leri, WiFi AP profili, mDNS) da otomatik guncellenir — ayrica bir
# komut gerekmez. Zorlamak/kapatmak icin: E1_APPLIANCE=1 / E1_APPLIANCE=0.
#
# Idempotent. Compose Docker build cache'i kullanir; degismemis layer'lar
# yeniden indirilmez.
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/infra/scripts/linux/_lib.sh"

e1_require_root "$@"
e1_enable_error_trap
E1_HELP_HINT="Sorun giderme: docs/SAHA-KURULUM.md"

# ---- Argumanlar -----------------------------------------------------------
#   [servis]              sadece o servisi guncelle (bkz. basliktaki liste)
#   --version X.Y.Z       belirli bir surume gec (rollback dahil)
#   --edge                yayin tag'i yerine ana dali kullan (gelistirme)
#   --build               hazir imaji indirmek yerine bu cihazda derle
#   --yes                 tum onay sorularini atla (CI/otomatik deploy)
TARGET="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      [[ -n "${2:-}" ]] || e1_die "--version bir surum bekler, orn: --version 2.25.0"
      # 'v' onekiyle de yazilabilsin: --version v2.25.0 == --version 2.25.0
      E1_REF="v${2#v}"
      shift 2
      ;;
    --version=*) _v="${1#--version=}"; E1_REF="v${_v#v}"; shift ;;
    --edge)      E1_REF="$E1_BOOTSTRAP_REF"; shift ;;
    --build)     E1_BUILD=1; shift ;;
    --yes|-y)    ASSUME_YES=1; shift ;;
    -*)          e1_die "Bilinmeyen secenek: $1" ;;
    *)           TARGET="$1"; shift ;;
  esac
done

cd "$SCRIPT_DIR"

# Update oncesi HEAD — sonda "neler degisti" ozeti icin.
PREV_HEAD="$(git rev-parse HEAD 2>/dev/null || echo '')"

# ---- Banner ---------------------------------------------------------------
clear 2>/dev/null || true
e1_banner

PREV_VERSION="$(e1_version "$SCRIPT_DIR")"
e1_box "GUNCELLEME"
e1_kv "Dizin" "${SCRIPT_DIR}"
e1_kv "Hedef" "$([[ "$TARGET" == "all" ]] && echo "Tum servisler" || echo "$TARGET")"
e1_kv "Mevcut surum" "${PREV_VERSION}"
e1_rule "─"

# Appliance (mini PC) modu kurulu mu? Kuruluysa her update'te host katmani da
# (ag ajani, systemd unit'leri, AP profili) yeni surume tazelenir — kullanici
# ayrica bir komut calistirmak zorunda kalmasin.
#   E1_APPLIANCE=1 -> kurulu degilse bile kur (mini PC'ye sonradan gecis)
#   E1_APPLIANCE=0 -> kurulu olsa bile dokunma
APPLIANCE_REFRESH=0
case "${E1_APPLIANCE:-auto}" in
  1) APPLIANCE_REFRESH=1 ;;
  0) APPLIANCE_REFRESH=0 ;;
  *) if e1_appliance_installed; then APPLIANCE_REFRESH=1; fi ;;
esac

# Backend'i kapsayan hedeflerde ek adimlar var (postgres sync, db-preflight,
# alembic, historian ensure); digerlerinde sadece ilk 4 adim kosar.
case "$TARGET" in
  all|""|backend|api|backend-api) STEP_COUNT=8 ;;
  *)                              STEP_COUNT=4 ;;
esac
if [[ $APPLIANCE_REFRESH -eq 1 ]]; then
  STEP_COUNT=$((STEP_COUNT + 1))
fi
e1_set_steps "$STEP_COUNT"

# ---- 1/5: Lokal degisiklik kontrolu --------------------------------------
e1_step "Lokal degisiklik kontrolu..."
# NOT: Sunucuda dosya IZIN biti (chmod +x) degisimi de "degisiklik" sayilir ve
# operator icerik degistirmedigi halde update kilitlenir. Bu yaygin ve zararsiz
# durumu gercek icerik degisikliginden ayirt ediyoruz.
if ! git diff --quiet || ! git diff --cached --quiet; then
  # Sadece izin biti mi degismis? (icerik ayni)
  content_changed="$(git diff --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null)"
  mode_only="$(git -c core.fileMode=false diff --name-only 2>/dev/null; \
               git -c core.fileMode=false diff --cached --name-only 2>/dev/null)"
  if [[ -n "$content_changed" && -z "$mode_only" ]]; then
    e1_warn "Sadece dosya izin biti (chmod) degismis; icerik ayni. Yok sayiliyor."
    git config core.fileMode false
    e1_ok "core.fileMode=false ayarlandi (bu repo icin kalici)."
  else
    echo
    e1_err "Repo'da commit edilmemis lokal degisiklik var:"
    echo
    git status --short | sed 's/^/    /'
    echo
    e1_err "Secenekler:"
    e1_err "  Degisiklikler size ait DEGILSE (deploy artefakti vb.) at:"
    e1_err "      git checkout -- . && sudo bash update.sh"
    e1_err "  Bilerek yaptiysaniz sakla, update sonrasi geri al:"
    e1_err "      git stash && sudo bash update.sh && git stash pop"
    e1_die "Calisma agaci temizlenmeden update devam edemez."
  fi
else
  e1_ok "Calisma agaci temiz."
fi

# Lisans host machine-id'ye bagli. USB/disk/RAM/MAC degisimi etkisizdir;
# machine-id yoksa backend fail-closed cihaz eklemeyi kapatir.
if [[ ! -s /etc/machine-id ]]; then
  e1_die "/etc/machine-id yok veya bos; lisans makine bagi dogrulanamaz."
fi

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

# ---- 3/5: Yayin surumune gec ----------------------------------------------
# Cihaz bir DALIN UCUNU degil, yayinlanmis bir TAG'i takip eder: guncelleme
# ancak release CI'dan gecmis bir surume gecirir. `--version X.Y.Z` ile belirli
# bir surume (ileri veya GERI) gecilebilir — rollback bu komuttur.
e1_step "Yeni surum indiriliyor..."
e1_hint "Internet hizina gore birkac saniye ile 1 dakika arasi surer."

# Depo yeni organizasyona tasindi. Eski adrese bakan kurulumlar guncelleme
# alamaz; remote'u sessizce degil, GORUNUR sekilde tasiriz. Kullanici kendi
# fork'unu kullaniyorsa (ne eski ne yeni adres) dokunmayiz.
CURRENT_REMOTE="$(git remote get-url origin 2>/dev/null || echo '')"
if [[ -n "$CURRENT_REMOTE" && "$CURRENT_REMOTE" != "$E1_REPO_URL" ]]; then
  if [[ "$CURRENT_REMOTE" == *"fikretsafak/EnerjiOneGrid"* ]]; then
    e1_warn "Depo adresi degismis. origin guncelleniyor:"
    e1_hint "  eski: ${CURRENT_REMOTE}"
    e1_hint "  yeni: ${E1_REPO_URL}"
    git remote set-url origin "$E1_REPO_URL"
    e1_ok "origin yeni depoya tasindi."
  fi
fi

e1_run "Surum listesi guncelleniyor" git fetch --tags --prune --force origin \
  || e1_die "Uzak repo'ya erisilemedi. Internet baglantisini kontrol edin."

TARGET_REF="$(e1_target_ref)"
if git rev-parse --verify --quiet "refs/tags/${TARGET_REF}" >/dev/null; then
  git checkout --quiet --detach "refs/tags/${TARGET_REF}"
  e1_ok "Yayin surumu: ${TARGET_REF}"
elif git rev-parse --verify --quiet "refs/remotes/origin/${TARGET_REF}" >/dev/null; then
  git checkout --quiet -B "${TARGET_REF}" "origin/${TARGET_REF}"
  e1_warn "'${TARGET_REF}' bir yayin tag'i degil, dal — gelistirme surumu kuruluyor."
else
  e1_die "'${TARGET_REF}' bulunamadi. Mevcut surumler:\n$(git tag -l 'v[0-9]*' --sort=-v:refname | head -10 | sed 's/^/    /')"
fi
NEW_HEAD="$(git rev-parse --short HEAD)"
e1_ok "Yeni HEAD: ${NEW_HEAD}"

# Imaj etiketi checkout edilen surumle AYNI olmali; aksi halde yeni kod eski
# imajla veya tam tersi calisir.
NEW_VERSION="$(e1_version "$SCRIPT_DIR")"
if [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  if grep -qE '^E1_VERSION=' .env; then
    sed -i "s|^E1_VERSION=.*|E1_VERSION=${NEW_VERSION}|" .env
  else
    echo "E1_VERSION=${NEW_VERSION}" >> .env
  fi
fi
if ! grep -qE '^E1_REGISTRY=' .env; then
  echo "E1_REGISTRY=${E1_REGISTRY_DEFAULT}" >> .env
fi

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
    DEBIAN_FRONTEND=noninteractive apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-bcrypt
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

# ---- Appliance host katmani (ag ajani + AP + mDNS) -----------------------
# Build'den ONCE calisir: /var/lib/e1-grid/net dizin izinleri (root:10001,
# 0770) backend container ayaga kalkmadan dogru olsun. Script idempotent —
# mevcut AP yayindaysa kesintiye ugratmaz.
if [[ $APPLIANCE_REFRESH -eq 1 ]]; then
  e1_step "Appliance host katmani guncelleniyor (ag ajani + AP + mDNS)..."
  if [[ -f infra/appliance/setup-appliance.sh ]]; then
    if bash infra/appliance/setup-appliance.sh; then
      e1_ok "Appliance katmani guncel."
    else
      e1_warn "Appliance guncellemesi tamamlanamadi; uygulama guncellemesi devam ediyor."
    fi
  else
    e1_warn "infra/appliance/setup-appliance.sh yok — appliance katmani atlandi."
  fi
fi

# Kurulum adresini (enerjione.com/grid/install.sh) yayinlayan sunucuda,
# yayinlanan kopya bu surumle tazelenir. Yayin dizini yoksa burasi hic
# calismaz — saha PC'leri ve normal VPS'ler etkilenmez.
E1_PUBLIC_WEBROOT="${E1_PUBLIC_WEBROOT:-/var/www/enerjione-grid-public}"
if [[ -d "$E1_PUBLIC_WEBROOT" && -f infra/scripts/linux/publish-installer.sh ]]; then
  if bash infra/scripts/linux/publish-installer.sh >/dev/null 2>&1; then
    e1_ok "Kurulum adresi yeni surumle tazelendi (${E1_PUBLIC_WEBROOT})."
  else
    e1_warn "Kurulum adresi tazelenemedi; guncelleme devam ediyor."
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
  modbus|modbus-outbound)           SVC="modbus-outbound" ;;
  ftp|ftp-server)                   SVC="ftp-server" ;;
  whatsapp|whatsapp-web-gateway)    SVC="whatsapp-web-gateway" ;;
  all|"")                           SVC="" ;;
  *)
    e1_die "Bilinmeyen servis: $TARGET. Gecerli: frontend, backend, alarm, tag, notification, iec, modbus, ftp, whatsapp, all"
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

  # POSTGRES_DB/POSTGRES_USER (ve RABBITMQ_USER) yalnizca BOS volume'da initdb
  # sirasinda islenir. Rebrand'de .env degisip volume ayni kaldiysa backend
  # 'role does not exist' ile baglanamaz ve asagidaki healthcheck dongusu
  # sebebi gostermeden 2 dakika sonra die eder. Preflight bunu once tespit
  # edip rename ile hizalar; uyumluysa hicbir sey yapmaz (idempotent).
  e1_step ".env <-> Postgres kimlik uyumu dogrulaniyor..."
  bash infra/scripts/linux/db-preflight.sh \
    || e1_die "DB on-kontrolu basarisiz. Detay yukarida; duzeltmeden update devam edemez."
fi

# Imajlar: release CI'da derlendi, cihaz sadece indirir. `--build` verilirse
# veya indirme basarisiz olursa yerel derlemeye duseriz.
_e1_prepare_images() {
  local svc="${1:-}"
  if [[ "${E1_BUILD:-0}" == "1" ]]; then
    e1_run "Imajlar derleniyor${svc:+ (${svc})}" docker compose build ${svc:+"$svc"} \
      || e1_die "Imaj derlemesi basarisiz. Detay yukarida."
    return 0
  fi
  e1_ghcr_login .env || true
  if e1_run "Yeni imajlar indiriliyor${svc:+ (${svc})}" docker compose pull ${svc:+"$svc"}; then
    return 0
  fi
  e1_warn "Imajlar indirilemedi — bu cihazda derlemeye geciliyor."
  e1_run "Imajlar derleniyor${svc:+ (${svc})}" docker compose build ${svc:+"$svc"} \
    || e1_die "Ne indirme ne derleme basarili oldu. Detay yukarida."
}

if [[ -z "$SVC" ]]; then
  e1_step "Servis imajlari hazirlaniyor + ayaga kalkiyor..."
  _e1_prepare_images
  # Altyapi once ve tek tek: backend-api rabbitmq/nats'a `service_healthy` ile
  # bagli oldugu icin biri saglikli olmazsa duz `up -d` tek satirlik
  # "dependency failed to start" ile patliyor ve sebep ekranda gorunmuyor.
  docker compose up -d rabbitmq nats || true
  # Onarimin veri-silme asamasi update'te KAPALI: calisan bir kurulumda
  # gateway kullanicilari/kuyruklar duruyor olabilir. Sadece container
  # yeniden yaratma denenir (E1_WIPEABLE_SERVICES bos).
  E1_WIPEABLE_SERVICES=""
  wait_failed=""
  e1_wait_healthy rabbitmq 240 || e1_repair_service rabbitmq 240 || wait_failed+=" rabbitmq"
  e1_wait_healthy nats     120 || e1_repair_service nats     120 || wait_failed+=" nats"
  if [[ -n "$wait_failed" ]]; then
    report="$(e1_write_diag_report "$(pwd)" "$wait_failed")"
    e1_die "Altyapi servisleri hazir olmadi:${wait_failed}\n\n  Teshis raporu:\n\n    ${report}\n\n  Bu dosyayi teknik destege gonderin."
  fi
  docker compose up -d
else
  e1_step "Servis '$SVC' guncelleniyor + force-recreate..."
  _e1_prepare_images "$SVC"
  docker compose up -d --force-recreate "$SVC"
fi

# ---- 5/5: Alembic migration (backend/all) ---------------------------------
if [[ "$NEEDS_BACKEND" -eq 1 ]]; then
  e1_step "DB migration uygulanıyor (alembic upgrade head)..."
  # Container entrypoint migration runner'i uvicorn'dan once calistirir.
  # Ayni runner'i eszamanli baslatma; backend healthy olunca migration bitmistir.
  backend_ready=0
  for i in $(seq 1 60); do
    if docker compose exec -T backend-api curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
      backend_ready=1
      break
    fi
    sleep 2
  done
  [[ "$backend_ready" -eq 1 ]] || e1_die "Backend migration/startup 2 dakikada tamamlanmadi. Log: docker compose logs backend-api"
  docker compose exec -T backend-api alembic current
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

e1_step_done

NEW_VERSION="$(e1_version "$SCRIPT_DIR")"
echo
e1_rule "═"
if [[ "$PREV_VERSION" != "$NEW_VERSION" ]]; then
  printf '  %s%sGUNCELLEME TAMAMLANDI%s   %s%s → %s · %s%s\n' \
    "${E1_GREEN}" "${E1_BOLD}" "${E1_RESET}" \
    "${E1_DIM}" "$PREV_VERSION" "$NEW_VERSION" "$(e1_total_elapsed)" "${E1_RESET}"
else
  printf '  %s%sGUNCELLEME TAMAMLANDI%s   %ssurum %s · %s%s\n' \
    "${E1_GREEN}" "${E1_BOLD}" "${E1_RESET}" \
    "${E1_DIM}" "$NEW_VERSION" "$(e1_total_elapsed)" "${E1_RESET}"
fi
e1_rule "═"

# Bu update'te ne degisti? Operator neyi test edecegini bilsin.
NEW_HEAD_FULL="$(git rev-parse HEAD 2>/dev/null || echo '')"
if [[ -n "$PREV_HEAD" && -n "$NEW_HEAD_FULL" && "$PREV_HEAD" != "$NEW_HEAD_FULL" ]]; then
  CHANGE_COUNT="$(git rev-list --count "${PREV_HEAD}..${NEW_HEAD_FULL}" 2>/dev/null || echo 0)"
  e1_box "BU GUNCELLEMEDE NELER DEGISTI (${CHANGE_COUNT} degisiklik)"
  git --no-pager log "${PREV_HEAD}..${NEW_HEAD_FULL}" \
      --format="  · %s" --no-merges 2>/dev/null | head -15
  if [[ "$CHANGE_COUNT" -gt 15 ]]; then
    e1_hint "... ve $((CHANGE_COUNT - 15)) degisiklik daha"
  fi
else
  e1_box "SURUM"
  e1_info "Yeni degisiklik yoktu — zaten guncel surumdesiniz."
fi

e1_box "SERVIS DURUMU"
docker compose ps
UNHEALTHY="$(docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null \
             | grep -viE 'running|up' | wc -l || echo 0)"
echo
if [[ "$UNHEALTHY" -gt 0 ]]; then
  e1_warn "${UNHEALTHY} servis calismiyor gorunuyor. Log: sudo docker compose logs --tail 50"
else
  e1_ok "Tum servisler calisiyor."
fi

if [[ $APPLIANCE_REFRESH -eq 1 ]]; then
  APPLIANCE_HOST="$(hostnamectl --static 2>/dev/null || echo e1-grid)"
  e1_box "APPLIANCE"
  e1_kv "WiFi agi" "EnerjiOne Grid  (sifresiz)"
  e1_kv "Adres" "http://${APPLIANCE_HOST}.local   veya   http://10.42.0.1"
  e1_kv "Ag durumu" "cat /var/lib/e1-grid/net/state.json"
fi

echo
printf '  %s%sSON ADIM:%s tarayicida %sCtrl + Shift + R%s ile sayfayi yenileyin.\n' \
  "${E1_YELLOW}" "${E1_BOLD}" "${E1_RESET}" "${E1_BOLD}" "${E1_RESET}"
printf '  %sYenilemezseniz tarayici eski arayuzu gostermeye devam eder.%s\n' \
  "${E1_DIM}" "${E1_RESET}"
echo
