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

# --- Postgres GERCEKTEN hazir mi? -----------------------------------------
# TUZAK: resmi imaj ILK KURULUMDA iki asamali baslar. Once `initdb` icin
# GECICI bir sunucu acar — bu sunucu YALNIZCA unix socket'i dinler — isi
# bitince onu KAPATIR ("the database system is shutting down") ve asil
# sunucuyu baslatir.
#
# Socket uzerinden `pg_isready` o gecici sunucuya "hazir" der. Tam kapanma
# aninda DDL gonderirsek `ALTER ROLE` FATAL alir ve kurulum, sebebi
# anlasilmaz bir "rol parolasi hizalanamadi" hatasiyla durur.
#
# COZUM: TCP uzerinden bekle. Gecici init sunucusu TCP DINLEMEZ; yani
# `pg_isready -h 127.0.0.1` ancak ASIL sunucu ayaga kalktiginda basarili
# olur. Ustune bir de gercek sorgu kosuyoruz — "kabul ediyor ama kapaniyor"
# ara durumunu da eliyor.
#
# Sure: initdb + TimescaleDB extension yavas diskte 2 dakikayi bulabiliyor.
e1_info "Postgres hazir olmasi bekleniyor (ilk kurulumda 1-2 dk surebilir)..."
pg_up=0
for _ in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -h 127.0.0.1 -q >/dev/null 2>&1 \
     && docker compose exec -T postgres psql -U postgres -d postgres -tAq \
          -c 'SELECT 1' >/dev/null 2>&1; then
    pg_up=1; break
  fi
  # Rol adi 'postgres' olmayabilir (volume baska isimle init edilmis);
  # o durumda yukaridaki sorgu basarisiz olur ama TCP hazirsa yeter.
  if docker compose exec -T postgres pg_isready -h 127.0.0.1 -q >/dev/null 2>&1; then
    # TCP hazir — asil sunucu ayakta. Rol tespitini asagidaki blok yapacak.
    sleep 2
    pg_up=1; break
  fi
  sleep 2
done
[[ "$pg_up" -eq 1 ]] || e1_die "Postgres 2 dakikada hazir olmadi. Log: docker compose logs postgres"
e1_ok "Postgres hazir (TCP dinliyor)."

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
  local try out rc
  # Uc deneme: Postgres acilis/kapanis gecislerinde ("the database system is
  # starting up / shutting down") tek seferlik FATAL donebiliyor. Bu gecici
  # bir durum; tek denemede pes etmek kurulumu sebepsiz oldururdu.
  # Gercek hatalarda (sozdizimi, yetki) uc deneme de ayni sonucu verir ve
  # son ciktiyi gosteririz.
  for try in 1 2 3; do
    # `else` SART: `if cmd; then ...; fi` yapisinda kosul basarisiz olup
    # hicbir dal calismazsa `$?` SIFIR doner. Disarida `rc=$?` yazsaydik
    # gercek bir DDL hatasini "basarili" sayardik.
    if out="$(docker compose exec -T postgres \
                psql -U "$role" -d postgres -tAq -v ON_ERROR_STOP=1 "$@" 2>&1)"; then
      printf '%s' "$out"
      return 0
    else
      rc=$?
    fi
    case "$out" in
      *"starting up"*|*"shutting down"*|*"not yet accepting"*)
        e1_info "Postgres henuz gecis halinde, ${try}/3 tekrar deneniyor..."
        sleep 4
        ;;
      *) break ;;
    esac
  done
  printf '%s' "${out:-}" >&2
  return "${rc:-1}"
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
_tcp_ok() {
  docker compose exec -T -e PGPASSWORD="$PG_PASSWORD" postgres \
    psql -h 127.0.0.1 -U "$PG_USER" -d "$PG_DB" -tAq -c 'SELECT 1' >/dev/null 2>&1
}

if _tcp_ok; then
  e1_ok "Backend'in kullandigi baglanti dogrulandi: ${PG_USER}@postgres/${PG_DB}"
else
  # ---- Otomatik onarim: parola sifreleme yontemi uyumsuzlugu -------------
  # En sik sebep: rol parolasi MD5 olarak saklanmis ama pg_hba SCRAM istiyor
  # (ya da tersi). Bu, volume eski bir postgres imaji ile init edilip sonra
  # imaj degistiginde olusur — `password_encryption` postgresql.conf'ta eski
  # degerle kalir. ALTER ROLE "basarili" doner ama uretilen dogrulayici
  # pg_hba'nin bekledigi turden degildir; giris sessizce reddedilir.
  #
  # Onarim: sunucunun sifreleme yontemini pg_hba ile hizala ve parolayi
  # YENIDEN yaz (mevcut hash donusturulemez, yeniden uretilmeli).
  CUR_ENC="$(_psql "$ACTUAL_ROLE" -c 'SHOW password_encryption' | tr -d '[:space:]')"
  HBA_WANTS="$(docker compose exec -T postgres \
      sh -c "grep -hE '^[[:space:]]*host' /var/lib/postgresql/data/pg_hba.conf 2>/dev/null | awk '{print \$NF}' | grep -E 'scram-sha-256|md5' | head -1" \
      2>/dev/null | tr -d '[:space:]')"
  e1_warn "TCP girisi reddedildi. Parola sifreleme kontrolu:"
  e1_info "  sunucu (password_encryption) : ${CUR_ENC:-bilinmiyor}"
  e1_info "  pg_hba'nin bekledigi          : ${HBA_WANTS:-bilinmiyor}"

  if [[ -n "$HBA_WANTS" && -n "$CUR_ENC" && "$CUR_ENC" != "$HBA_WANTS" ]]; then
    e1_warn "Uyumsuzluk tespit edildi — sunucu '${HBA_WANTS}' olarak hizalaniyor."
    _psql_ddl "$ACTUAL_ROLE" -c "ALTER SYSTEM SET password_encryption = $(_lit "$HBA_WANTS")" >/dev/null \
      || e1_die "password_encryption ayarlanamadi."
    _psql "$ACTUAL_ROLE" -c 'SELECT pg_reload_conf()' >/dev/null || true
    # Parolayi YENIDEN yaz: eski hash donusturulemez, yeni yontemle uretilmeli.
    _psql_ddl "$ACTUAL_ROLE" -c "ALTER ROLE $(_ident "$PG_USER") WITH LOGIN PASSWORD $(_lit "$PG_PASSWORD")" >/dev/null \
      || e1_die "Parola yeniden yazilamadi."
    if _tcp_ok; then
      e1_ok "Onarildi: parola '${HBA_WANTS}' ile yeniden uretildi, baglanti calisiyor."
    else
      e1_err "Onarim denendi ama baglanti hala basarisiz."
      e1_err "Postgres loglarinin son satirlari:"
      docker compose logs --tail 30 --no-color postgres 2>&1 | sed 's/^/      /' >&2 || true
      e1_die "TCP+parola ile baglanti kurulamadi (${PG_USER}@${PG_DB})."
    fi
  else
    # Sifreleme uyumlu ama giris yine reddediliyor — sebep baska.
    # Kullaniciyi tahmine birakmak yerine kanitlari ekrana dokuyoruz.
    e1_err "Sifreleme yontemi uyumlu gorunuyor; sebep baska."
    e1_err "pg_hba 'host' satirlari:"
    docker compose exec -T postgres \
      sh -c "grep -hE '^[[:space:]]*host' /var/lib/postgresql/data/pg_hba.conf" 2>&1 \
      | sed 's/^/      /' >&2 || true
    e1_err "Postgres loglarinin son satirlari:"
    docker compose logs --tail 30 --no-color postgres 2>&1 | sed 's/^/      /' >&2 || true
    e1_die "TCP+parola ile baglanti hala basarisiz (${PG_USER}@${PG_DB})."
  fi
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
