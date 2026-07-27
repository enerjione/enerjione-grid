#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Postgres / RabbitMQ kimlik on-kontrolu (idempotent)
# ===========================================================================
# NEDEN GEREKLI
# -------------
# `postgres` ve `rabbitmq` imajlari POSTGRES_DB / POSTGRES_USER /
# RABBITMQ_DEFAULT_USER env'lerini SADECE veri volume'u BOSKEN, ilk
# initdb sirasinda isler. Volume bir kez dolduktan sonra bu env'leri
# degistirmek container icindeki rolu/DB'yi DEGISTIRMEZ.
#
# Rebrand (horstman/hsl/enerjione -> enerjione_grid) sirasinda .env'deki
# POSTGRES_USER/POSTGRES_DB guncellenip volume oldugu gibi birakildiysa:
#   backend-api  -> FATAL: role "enerjione_grid" does not exist
#   update.sh    -> "Backend migration/startup 2 dakikada tamamlanmadi"
# seklinde patlar. Hata mesaji nedeni gostermedigi icin teshis zor.
#
# Bu script .env ile volume'un gercek durumunu karsilastirir, fark varsa
# ALTER ROLE/DATABASE RENAME ile hizalar. Uyumluysa hicbir sey yapmaz.
#
# Kullanim (repo kokunde):
#   sudo bash infra/scripts/linux/db-preflight.sh          # kontrol + onar
#   sudo bash infra/scripts/linux/db-preflight.sh --check  # sadece rapor
#
# update.sh backend/all hedefinde bunu otomatik cagirir.
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

cd "$REPO_ROOT"

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

[[ -f .env ]] || e1_die ".env bulunamadi (${REPO_ROOT}/.env). Once install.sh calistirin."

# ---- .env'den oku ---------------------------------------------------------
# `cut -d= -f2-` degerde '=' olsa bile tamamini alir (parolalarda olabilir).
_env_get() { grep -E "^${1}=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }

PG_USER="$(_env_get POSTGRES_USER)";     PG_USER="${PG_USER:-enerjione_grid}"
PG_DB="$(_env_get POSTGRES_DB)";         PG_DB="${PG_DB:-enerjione_grid}"
PG_PASSWORD="$(_env_get POSTGRES_PASSWORD)"
MQ_USER="$(_env_get RABBITMQ_USER)";     MQ_USER="${MQ_USER:-hsl}"
MQ_PASSWORD="$(_env_get RABBITMQ_PASSWORD)"
MQ_VHOST="$(_env_get RABBITMQ_VHOST)";   MQ_VHOST="${MQ_VHOST:-e1}"

[[ -n "$PG_PASSWORD" ]] || e1_die "POSTGRES_PASSWORD .env'de bos. Rol parolasi hizalanamaz."

# Rebrand oncesi kullanilmis olabilecek rol/DB isimleri. Sirali denenir;
# ilk baglanabilen "gercek" kimlik kabul edilir.
LEGACY_NAMES=(enerjione_grid enerjione hsl horstman horstmann postgres)

# ---- Postgres ayakta mi? --------------------------------------------------
e1_info "Postgres container kontrol ediliyor..."
if ! docker compose ps postgres --status running --quiet 2>/dev/null | grep -q .; then
  e1_info "Postgres ayakta degil, baslatiliyor..."
  docker compose up -d postgres >/dev/null
fi

# Socket hazir olana kadar bekle (max 60sn). pg_isready burada rol dogrulamaz;
# sadece "server cevap veriyor mu" bakar — o yuzden -U vermiyoruz.
pg_up=0
for _ in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -q >/dev/null 2>&1; then pg_up=1; break; fi
  sleep 2
done
[[ "$pg_up" -eq 1 ]] || e1_die "Postgres 60 saniyede hazir olmadi. Log: docker compose logs postgres"

# psql helper — container icindeki unix socket uzerinden. Resmi imaj
# 'local all all trust' ile initdb ettigi icin parola gerekmez; bu sayede
# .env parolasi volume'daki parola ile uyusmasa bile onarim yapabiliriz.
_psql() {
  local role="$1"; shift
  docker compose exec -T postgres psql -U "$role" -d postgres -tAq "$@" 2>/dev/null
}

# DDL icin ayri helper: stderr BASTIRILMAZ — rename/alter patlarsa Postgres'in
# gercek hata mesajini gormek gerekiyor. `-v ON_ERROR_STOP=1` olmadan psql
# hatali komutta bile 0 doner.
_psql_ddl() {
  local role="$1"; shift
  docker compose exec -T postgres psql -U "$role" -d postgres -tAq -v ON_ERROR_STOP=1 "$@"
}

# SQL literal/identifier escape — psql'in `-v` degisken interpolasyonu `-c`
# ile psql surumune gore farkli davraniyor; belirsizlige birakmadan SQL'i
# burada guvenle kuruyoruz.
#   _lit  'a'b'  -> 'a''b'      (tek tirnak ikile)
#   _ident a"b   -> "a""b"      (cift tirnak ikile)
_lit()   { printf "'%s'" "${1//\'/\'\'}"; }
_ident() { printf '"%s"' "${1//\"/\"\"}"; }

# ---- Volume'daki gercek rolu bul -----------------------------------------
ACTUAL_ROLE=""
for cand in "$PG_USER" "${LEGACY_NAMES[@]}"; do
  [[ -n "$cand" ]] || continue
  if _psql "$cand" -c 'SELECT 1' >/dev/null; then ACTUAL_ROLE="$cand"; break; fi
done

if [[ -z "$ACTUAL_ROLE" ]]; then
  e1_err "Postgres volume'unda bilinen hicbir superuser rolu ile baglanilamadi."
  e1_err "Denenen roller: ${PG_USER} ${LEGACY_NAMES[*]}"
  e1_err "Mevcut rolleri gormek icin:"
  e1_err "  docker compose exec postgres psql -U <bilinen-rol> -c '\\du'"
  e1_die "Rol adi tespit edilemedi; .env'deki POSTGRES_USER'i volume'daki gercek rol ile eslestirin."
fi

ROLE_EXISTS="$(_psql "$ACTUAL_ROLE" -c "SELECT 1 FROM pg_roles WHERE rolname=$(_lit "$PG_USER")")"
DB_EXISTS="$(_psql "$ACTUAL_ROLE" -c "SELECT 1 FROM pg_database WHERE datname=$(_lit "$PG_DB")")"

echo
e1_info "Beklenen (.env)      : user=${PG_USER} db=${PG_DB}"
e1_info "Volume'da baglanilan : user=${ACTUAL_ROLE}"
e1_info "Rol '${PG_USER}' var mi?  : $([[ -n "$ROLE_EXISTS" ]] && echo evet || echo HAYIR)"
e1_info "DB  '${PG_DB}' var mi?  : $([[ -n "$DB_EXISTS" ]] && echo evet || echo HAYIR)"

NEEDS_REPAIR=0
[[ -z "$ROLE_EXISTS" ]] && NEEDS_REPAIR=1
[[ -z "$DB_EXISTS" ]] && NEEDS_REPAIR=1

if [[ "$NEEDS_REPAIR" -eq 0 ]]; then
  # Isimler dogru. Parola .env ile uyusmayabilir (rebrand sirasinda .env'e
  # yeni parola yazildiysa) — idempotent olarak hizala.
  if [[ "$CHECK_ONLY" -eq 0 ]]; then
    _psql_ddl "$ACTUAL_ROLE" -c "ALTER ROLE $(_ident "$PG_USER") WITH LOGIN PASSWORD $(_lit "$PG_PASSWORD")" >/dev/null \
      || e1_die "Rol parolasi .env ile hizalanamadi (ALTER ROLE ${PG_USER})."
    e1_ok "Postgres kimligi .env ile uyumlu (parola da hizalandi)."
  else
    e1_ok "Postgres kimligi .env ile uyumlu."
  fi
else
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    e1_die "Postgres kimligi .env ile UYUSMUYOR. Onarim icin: sudo bash ${BASH_SOURCE[0]}"
  fi

  e1_warn "Volume eski isimlerle init edilmis; .env ile hizalaniyor (rename)."

  # Rename sirasinda hedef DB'ye acik oturum olmamali. Backend'i durdur.
  e1_info "backend-api durduruluyor (rename icin acik oturum olmamali)..."
  docker compose stop backend-api >/dev/null 2>&1 || true

  # 1) DB rename — kaynak DB, roller listesindeki ilk mevcut olan.
  if [[ -z "$DB_EXISTS" ]]; then
    SRC_DB=""
    for cand in "${LEGACY_NAMES[@]}"; do
      [[ "$cand" == "$PG_DB" || "$cand" == "postgres" ]] && continue
      if [[ -n "$(_psql "$ACTUAL_ROLE" -c "SELECT 1 FROM pg_database WHERE datname=$(_lit "$cand")")" ]]; then
        SRC_DB="$cand"; break
      fi
    done
    [[ -n "$SRC_DB" ]] || e1_die "Yeniden adlandirilacak kaynak DB bulunamadi. Mevcut DB'ler: $(_psql "$ACTUAL_ROLE" -c 'SELECT datname FROM pg_database WHERE NOT datistemplate' | tr '\n' ' ')"

    e1_info "DB rename: '${SRC_DB}' -> '${PG_DB}'"
    _psql "$ACTUAL_ROLE" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$(_lit "$SRC_DB") AND pid <> pg_backend_pid()" >/dev/null || true
    _psql_ddl "$ACTUAL_ROLE" -c "ALTER DATABASE $(_ident "$SRC_DB") RENAME TO $(_ident "$PG_DB")" >/dev/null \
      || e1_die "DB rename basarisiz. Manuel: docker compose exec postgres psql -U ${ACTUAL_ROLE} -c 'ALTER DATABASE \"${SRC_DB}\" RENAME TO \"${PG_DB}\"'"
    e1_ok "DB adi guncellendi: ${PG_DB}"
  fi

  # 2) Rol rename — ACTUAL_ROLE zaten volume'daki superuser.
  #    NOT: ALTER ROLE RENAME, MD5 parolayi gecersiz kilar; hemen altta
  #    .env parolasi ile yeniden set ediyoruz.
  if [[ -z "$ROLE_EXISTS" ]]; then
    [[ "$ACTUAL_ROLE" != "postgres" ]] || e1_die "Volume 'postgres' superuser'i ile init edilmis; '${PG_USER}' rolunu rename etmek yerine .env'i POSTGRES_USER=postgres yapin veya rolu elle olusturun."
    e1_info "Rol rename: '${ACTUAL_ROLE}' -> '${PG_USER}'"
    _psql_ddl "$ACTUAL_ROLE" -c "ALTER ROLE $(_ident "$ACTUAL_ROLE") RENAME TO $(_ident "$PG_USER")" >/dev/null \
      || e1_die "Rol rename basarisiz. Manuel: docker compose exec postgres psql -U ${ACTUAL_ROLE} -c 'ALTER ROLE \"${ACTUAL_ROLE}\" RENAME TO \"${PG_USER}\"'"
    ACTUAL_ROLE="$PG_USER"
    e1_ok "Rol adi guncellendi: ${PG_USER}"
  fi

  # 3) Parola + sahiplik hizala. RENAME sonrasi eski parola hash'i gecersiz
  #    oldugu icin bu adim ZORUNLU — atlanirsa backend scram ile giremez.
  _psql_ddl "$ACTUAL_ROLE" -c "ALTER ROLE $(_ident "$PG_USER") WITH LOGIN PASSWORD $(_lit "$PG_PASSWORD")" >/dev/null \
    || e1_die "Rol parolasi set edilemedi (ALTER ROLE ${PG_USER})."
  _psql "$ACTUAL_ROLE" -c "ALTER DATABASE $(_ident "$PG_DB") OWNER TO $(_ident "$PG_USER")" >/dev/null || true
  e1_ok "Parola ve DB sahipligi .env ile hizalandi."
fi

# ---- Son dogrulama: backend'in kullandigi TCP + parola yolu --------------
# Yukarisi unix socket (trust) uzerinden calisti. Backend TCP + scram
# kullaniyor; asil kritik olan bu yolun calismasi.
if docker compose exec -T -e PGPASSWORD="$PG_PASSWORD" postgres \
     psql -h 127.0.0.1 -U "$PG_USER" -d "$PG_DB" -tAq -c 'SELECT 1' >/dev/null 2>&1; then
  e1_ok "Backend'in kullandigi baglanti dogrulandi: ${PG_USER}@postgres/${PG_DB}"
else
  e1_die "TCP+parola ile baglanti hala basarisiz (${PG_USER}@${PG_DB}). Log: docker compose logs postgres"
fi

# ---- RabbitMQ admin kullanicisi ------------------------------------------
# Ayni sinif hata: RABBITMQ_DEFAULT_USER da sadece bos volume'da islenir.
# Rebrand'de RABBITMQ_USER degistiyse backend broker'a baglanamaz.
if docker compose ps rabbitmq --status running --quiet 2>/dev/null | grep -q .; then
  if [[ -n "$MQ_PASSWORD" ]]; then
    if docker compose exec -T rabbitmq rabbitmqctl list_users 2>/dev/null | awk '{print $1}' | grep -qx "$MQ_USER"; then
      e1_ok "RabbitMQ kullanicisi mevcut: ${MQ_USER}"
    elif [[ "$CHECK_ONLY" -eq 1 ]]; then
      e1_warn "RabbitMQ kullanicisi '${MQ_USER}' YOK (volume eski kullanici ile init edilmis)."
    else
      e1_warn "RabbitMQ kullanicisi '${MQ_USER}' yok — olusturuluyor."
      docker compose exec -T rabbitmq rabbitmqctl add_user "$MQ_USER" "$MQ_PASSWORD" >/dev/null 2>&1 || true
      docker compose exec -T rabbitmq rabbitmqctl set_user_tags "$MQ_USER" administrator >/dev/null 2>&1 || true
      docker compose exec -T rabbitmq rabbitmqctl add_vhost "$MQ_VHOST" >/dev/null 2>&1 || true
      docker compose exec -T rabbitmq rabbitmqctl set_permissions -p "$MQ_VHOST" "$MQ_USER" '.*' '.*' '.*' >/dev/null 2>&1 || true
      e1_ok "RabbitMQ kullanicisi olusturuldu: ${MQ_USER} (vhost ${MQ_VHOST})"
    fi
  fi
else
  e1_info "RabbitMQ ayakta degil — kullanici kontrolu atlandi."
fi

echo
e1_ok "DB on-kontrolu tamam."
