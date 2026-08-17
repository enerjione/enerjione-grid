#!/usr/bin/env bash
# ===========================================================================
# UPDATE BACKUP GATE — guncelleme oncesi yedek ZORUNLU (fail-closed)
# ===========================================================================
#
# YASANAN RISK
# ------------
# Yedek adimi FAIL-OPEN'di. Uc ayri yolda yedek alinamiyor ama guncelleme
# YINE DE devam ediyordu:
#
#   diskte 2048 MB'tan az yer -> "yedek ATLANDI",             update devam
#   pg_dump basarisiz         -> "update yine de devam ediyor"
#   Postgres ayakta degil     -> "yedek atlandi",             update devam
#
# Ustelik uretilen dosya HIC DOGRULANMIYORDU: `pg_dump` cikis kodu 0 ise
# yeterli sayiliyordu. Bos, kirpilmis ya da okunamayan bir arsiv "yedek"
# olarak raporlanabiliyordu — geri donus noktasi OLDUGU SANILAN bir dosya.
#
# Sonuc: veri tasiyan bir saha cihazi, elinde geri donulebilir HICBIR NOKTA
# OLMADAN migration'a girebiliyordu.
#
# BU TESTIN OLCTUGU SEY
# ---------------------
# Log metni DEGIL, MUTASYON SAYISI. Her senaryoda `docker`, `git`, `sed` gibi
# durum degistiren komutlar taklit ediliyor ve kac kez cagrildiklari
# sayiliyor. Kapi kapaliyken bu sayac 0 olmak ZORUNDA — "loglara bakip
# durmus gorunuyor" yeterli bir kanit degildir.
#
# Kullanim: bash test-update-backup-gate.sh [repo-koku]
# ===========================================================================
set -euo pipefail

KOK="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
LIB="${KOK}/infra/scripts/linux/_lib.sh"
BETIK="${KOK}/update.sh"

[[ -f "$LIB" ]]    || { echo "HATA: _lib.sh yok: $LIB" >&2; exit 1; }
[[ -f "$BETIK" ]]  || { echo "HATA: update.sh yok: $BETIK" >&2; exit 1; }

gecti=0
basarisiz=0
_ok()   { gecti=$((gecti+1)); printf '  ok   %s\n' "$1"; }
_fail() { basarisiz=$((basarisiz+1)); printf '  FAIL %s\n' "$1" >&2; }
_k() {  # _k <ad> <gercek> <beklenen>
  if [[ "$2" == "$3" ]]; then _ok "$1"; else
    printf '  FAIL %s: beklenen=%s gercek=%s\n' "$1" "$3" "$2" >&2
    basarisiz=$((basarisiz+1))
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
# Kapiyi IZOLE kosturan tezgah.
#
# Gercek `e1_pre_update_backup_gate` fonksiyonu kosar; yalnizca disariya cikan
# komutlar (docker/df/sha256sum) taklit edilir. Boylece test, uretim kod
# yolunun KENDISINI surer.
#
# MUTASYON SAYACI: kapi disindaki her durum-degistiren komut cagrisi
# ${IS}/mutations dosyasina bir satir yazar.
# ---------------------------------------------------------------------------
_kur() {
  IS="${TMP}/$1"; rm -rf "$IS"; mkdir -p "$IS/backups"
  : > "$IS/mutations"
  : > "$IS/log"
  printf 'POSTGRES_USER=e1\nPOSTGRES_DB=e1db\n' > "$IS/.env"
}

# $1=is adi
# Ortam degiskenleriyle davranis secilir:
#   PG_DURUM      : running | stopped | yok
#   DUMP_SONUC    : 0 (basarili) | 1 (hata) | kill (yarim dosya birakip olur)
#   DUMP_ICERIK   : dump dosyasina yazilacak icerik
#   RESTORE_LIST  : 0 (arsiv okunur) | 1 (bozuk)
#   FREE_MB       : df'in dondurecegi bos alan
#   DB_BAYT       : psql'in dondurecegi tahmin bayti
#   BACKUPS_YAZILAMAZ : 1 ise dizin yazilamaz
_kapi_kos() {
  local is="$1"
  (
    cd "${TMP}/${is}" || exit 90
    set +e

    # --- taklitler ---------------------------------------------------------
    docker() {
      case "$*" in
        *"compose ps postgres"*)
          [[ "${PG_DURUM:-running}" == "running" ]] && printf 'abc123\n'
          return 0 ;;
        *"volume ls"*)
          [[ "${PG_DURUM:-running}" == "yok" ]] || printf 'enerjione-grid_postgres-data\n'
          return 0 ;;
        *"compose exec -T postgres psql"*)
          printf '%s\n' "${DB_BAYT:-104857600}"; return 0 ;;
        *"compose exec -T postgres pg_dump"*)
          case "${DUMP_SONUC:-0}" in
            0)    printf '%s' "${DUMP_ICERIK-PGDMPsahte-arsiv-icerigi}"; return 0 ;;
            kill) printf 'PGDMP-yarim'; return 137 ;;   # SIGKILL benzeri
            *)    echo "pg_dump: yapay hata" >&2; return 1 ;;
          esac ;;
        *"compose exec -T postgres pg_restore --list"*)
          cat >/dev/null; return "${RESTORE_LIST:-0}" ;;
        # --- MUTASYON SAYILAN KOMUTLAR ---
        *"compose pull"*|*"compose up"*|*"compose build"*|*"compose restart"*|\
        *"compose stop"*|*"compose rm"*|*" pull "*|*" rmi "*)
          echo "docker $*" >> mutations; return 0 ;;
      esac
      return 0
    }
    git()  { echo "git $*"  >> mutations; return 0; }
    sed()  { echo "sed $*"  >> mutations; return 0; }
    df()   { printf 'Filesystem 1M-blocks Used Available Use%% Mounted\n/dev/x 100000 1 %s 1%% /\n' "${FREE_MB:-50000}"; }
    sha256sum() { printf 'deadbeefcafe  %s\n' "$1"; }
    stat() { command stat "$@"; }

    # _lib.sh'in gurultusunu kes
    e1_step() { :; } ; e1_info() { :; } ; e1_ok() { :; }
    e1_hint() { :; } ; e1_warn() { :; }
    e1_err()  { echo "ERR: $*" >> log; }
    e1_chown_target() { :; }

    # shellcheck disable=SC1090
    source "REPO_LIB" >/dev/null 2>&1

    # Taklitleri source SONRASI yeniden tanimla (lib kendi tanimlarini getirir)
    e1_step() { :; } ; e1_info() { :; } ; e1_ok() { :; }
    e1_hint() { :; } ; e1_warn() { :; }
    e1_err()  { echo "ERR: $*" >> log; }
    e1_chown_target() { :; }

    if [[ "${BACKUPS_YAZILAMAZ:-0}" == "1" ]]; then
      chmod 500 backups 2>/dev/null || true
    fi

    e1_pre_update_backup_gate "2.97.0" "2.98.0" 2>>log
    kod=$?

    # Kapi GECERSE mutasyonlar baslar — gercek update.sh'te oldugu gibi.
    if [[ $kod -eq 0 ]]; then
      docker compose pull >/dev/null 2>&1
      git checkout >/dev/null 2>&1
    fi
    printf '%s' "$kod"
  )
}

# _lib.sh yolunu tezgaha goEM
_kapi_kos_hazirla() {
  local f; f="$(declare -f _kapi_kos)"
  eval "${f//REPO_LIB/$LIB}"
}
_kapi_kos_hazirla

_mut()  { wc -l < "${TMP}/$1/mutations" | tr -d ' '; }
_dump() { find "${TMP}/$1/backups" -maxdepth 1 -type f -name 'auto-pre-update-*' 2>/dev/null | wc -l | tr -d ' '; }
_tmpf() { find "${TMP}/$1/backups" -maxdepth 1 -type f -name '.backup.tmp.*' 2>/dev/null | wc -l | tr -d ' '; }

echo "== T01-T25 / F01-F08 =="

# ---------------------------------------------------------------------------
# T02 / T11 / T12 / T13 / T14 / T19 / T20 — BASARILI YOL
# ---------------------------------------------------------------------------
_kur t02
kod="$(_kapi_kos t02)"
_k  "T02 basarili yedek -> kapi gecer"                "$kod"        "0"
_k  "T02 basarili yedek -> update mutasyonlari kosar" "$(_mut t02)" "2"
_k  "T11 atomik: nihai dosya olustu"                  "$(_dump t02)" "1"
_k  "T10 gecici dosya kalmadi"                        "$(_tmpf t02)" "0"

DOSYA="$(find "${TMP}/t02/backups" -name 'auto-pre-update-*' | head -1)"
_k  "T13 kaynak surum dosya adinda"  "$(basename "$DOSYA" | grep -c '2\.97\.0')" "1"
_k  "T14 hedef surum dosya adinda"   "$(basename "$DOSYA" | grep -c '2\.98\.0')" "1"
_k  "T12 SHA256 loglandi"            "$(grep -c 'sha256=' "${TMP}/t02/log")" "1"
_k  "T19 yedek TAM BIR KEZ alindi"   "$(grep -c 'pre_update_backup_succeeded' "${TMP}/t02/log")" "1"
# `started` ve `succeeded` satirlarinin IKISI de kaynak/hedef tasir.
_k  "metadata: kaynak+hedef loglandi (started+succeeded)" \
    "$(grep -c 'source=2.97.0 target=2.98.0' "${TMP}/t02/log")" "2"

# T15 — loglarda secret olmamali
if grep -qiE 'PGPASSWORD|password=|POSTGRES_PASSWORD' "${TMP}/t02/log"; then
  _fail "T15 loglarda secret var"
else
  _ok "T15 loglarda secret yok"
fi

# ---------------------------------------------------------------------------
# T03 / F01 — pg_dump basarisiz
# ---------------------------------------------------------------------------
_kur t03
kod="$(DUMP_SONUC=1 _kapi_kos t03)"
_k "T03/F01 pg_dump hatasi -> kapi kapali"          "$kod"         "1"
_k "T16/T17/T18 mutasyon sayisi 0"                  "$(_mut t03)"  "0"
_k "T03 yarim dosya birakilmadi"                    "$(_dump t03)" "0"
_k "T03 gecici dosya temizlendi"                    "$(_tmpf t03)" "0"
_k "T03 sebep loglandi"                             "$(grep -c 'reason=pg_dump_failed' "${TMP}/t03/log")" "1"

# ---------------------------------------------------------------------------
# F02 — pg_dump yarida oldurulur (SIGKILL benzeri): NIHAI DOSYA OLUSMAZ
# ---------------------------------------------------------------------------
_kur f02
kod="$(DUMP_SONUC=kill _kapi_kos f02)"
_k "F02 pg_dump oldurulur -> kapi kapali"           "$kod"         "1"
_k "F02 NIHAI yedek adi OLUSMADI"                   "$(_dump f02)" "0"
_k "F02 mutasyon 0"                                 "$(_mut f02)"  "0"

# ---------------------------------------------------------------------------
# T04 / F05 — Postgres erisilemez (ama VERI VAR)
# ---------------------------------------------------------------------------
_kur t04
kod="$(PG_DURUM=stopped _kapi_kos t04)"
_k "T04/F05 Postgres kapali + veri var -> kapi kapali" "$kod"        "1"
_k "T04 mutasyon 0"                                    "$(_mut t04)" "0"
_k "T04 sebep loglandi" \
   "$(grep -c 'reason=postgres_unavailable' "${TMP}/t04/log")" "1"

# ---------------------------------------------------------------------------
# T23 — GERCEK ilk kurulum (veri hic yok): kapi GECMELI
# ---------------------------------------------------------------------------
_kur t23
kod="$(PG_DURUM=yok _kapi_kos t23)"
_k "T23 ilk kurulum -> kapi gecer"          "$kod"         "0"
_k "T23 yedek uretilmedi"                   "$(_dump t23)" "0"
_k "T23 update devam etti"                  "$(_mut t23)"  "2"
_k "T23 sebep loglandi" \
   "$(grep -c 'reason=fresh_install' "${TMP}/t23/log")" "1"

# ---------------------------------------------------------------------------
# T05 / F04 — disk yetersiz
# ---------------------------------------------------------------------------
_kur t05
kod="$(FREE_MB=10 DB_BAYT=5368709120 _kapi_kos t05)"
_k "T05/F04 disk yetersiz -> kapi kapali"   "$kod"         "1"
_k "T05 mutasyon 0"                         "$(_mut t05)"  "0"
_k "T05 gereken+bos alan loglandi" \
   "$(grep -c 'required_mb=.* free_mb=' "${TMP}/t05/log")" "1"

# Disk YETERLI ise gecmeli (kapi korkak olmamali)
_kur t05b
kod="$(FREE_MB=50000 DB_BAYT=104857600 _kapi_kos t05b)"
_k "T05b disk yeterli -> kapi gecer"        "$kod"         "0"

# ---------------------------------------------------------------------------
# T07 / F08 — bos dump
# ---------------------------------------------------------------------------
_kur t07
kod="$(DUMP_ICERIK= _kapi_kos t07)"
_k "T07 bos dump -> kapi kapali"            "$kod"         "1"
_k "T07 nihai dosya olusmadi"               "$(_dump t07)" "0"
_k "T07 mutasyon 0"                         "$(_mut t07)"  "0"

# ---------------------------------------------------------------------------
# T08 / T09 / F06 / F07 — arsiv bozuk (pg_restore --list duser)
# ---------------------------------------------------------------------------
_kur t08
kod="$(RESTORE_LIST=1 _kapi_kos t08)"
_k "T08/T09/F06/F07 bozuk arsiv -> kapi kapali" "$kod"         "1"
_k "T08 nihai dosya olusmadi"                   "$(_dump t08)" "0"
_k "T08 mutasyon 0"                             "$(_mut t08)"  "0"
_k "T08 dogrulama hatasi loglandi" \
   "$(grep -c 'pre_update_backup_validation_failed' "${TMP}/t08/log")" "1"

# ---------------------------------------------------------------------------
# T06 / F03 — backups dizini yazilamaz
# ---------------------------------------------------------------------------
# chmod bu dosya sisteminde ETKILI mi? Git Bash/Windows'ta degil; root da
# her yere yazar. Ikisinde de test ANLAMSIZ olur — yanlis yesil vermektense
# acikca atla (CI Linux'ta gercek kontrolu yapar).
_chmod_etkili=0
_probe="${TMP}/.chmod-probe"; mkdir -p "$_probe"
chmod 500 "$_probe" 2>/dev/null || true
[[ -w "$_probe" ]] || _chmod_etkili=1
chmod 700 "$_probe" 2>/dev/null || true

if [[ "$(id -u)" -eq 0 ]] || [[ "$_chmod_etkili" -eq 0 ]]; then
  _ok "T06/F03 dizin izni testi ATLANDI (root ya da chmod etkisiz dosya sistemi)"
else
  _kur t06
  kod="$(BACKUPS_YAZILAMAZ=1 _kapi_kos t06)"
  _k "T06/F03 dizin yazilamaz -> kapi kapali" "$kod"        "1"
  _k "T06 mutasyon 0"                         "$(_mut t06)" "0"
fi

# ---------------------------------------------------------------------------
# T21 — yeniden deneme onceki yedegin UZERINE YAZMAZ
# ---------------------------------------------------------------------------
_kur t21
_kapi_kos t21 >/dev/null
ilk="$(_dump t21)"
sleep 1                     # dosya adindaki UTC damgasi saniye cozunurluklu
_kapi_kos t21 >/dev/null
_k "T21 ikinci kosu YENI dosya uretti (uzerine yazmadi)" "$(_dump t21)" "2"
_k "T21 ilk kosuda tek dosya vardi"                      "$ilk"         "1"

# ---------------------------------------------------------------------------
# T25 — cikis kodu sozlesmesi
# ---------------------------------------------------------------------------
_kur t25kod
_k "T25 kapi hatasi non-zero doner" "$(DUMP_SONUC=1 _kapi_kos t25kod)" "1"
if grep -q 'E1_EXIT_BACKUP_GATE=42' "$LIB"; then
  _ok "T25 ozel cikis kodu 42 tanimli"
else
  _fail "T25 ozel cikis kodu tanimli degil"
fi

# ---------------------------------------------------------------------------
# T22 — es zamanli guncelleme kilidi
# ---------------------------------------------------------------------------
if grep -q 'flock -n 9' "$BETIK"; then
  _ok "T22 update.sh es zamanli kosuya karsi flock aliyor"
else
  _fail "T22 update kilidi yok — iki updater ayni dosyalara yazabilir"
fi

# Gecici dosya adi PID tasimali (ayni anda iki kosu carpismasin)
if grep -q 'backups/.backup.tmp.\$\$' "$LIB"; then
  _ok "T22 gecici dosya adi PID tasiyor"
else
  _fail "T22 gecici dosya adi PID tasimiyor"
fi

# ---------------------------------------------------------------------------
# T01 / GATE POSITION — kapi ILK MUTASYONDAN once mi?
# ---------------------------------------------------------------------------
kapi_satir="$(grep -n 'if ! e1_pre_update_backup_gate' "$BETIK" | head -1 | cut -d: -f1)"
if [[ -z "$kapi_satir" ]]; then
  _fail "T01 update.sh yedek kapisini CAGIRMIYOR"
else
  _ok "T01 update.sh yedek kapisini cagiriyor (satir ${kapi_satir})"

  # Kapidan ONCE calisan ust duzey satirlarda uretim mutasyonu olmamali.
  # Fonksiyon GOVDELERI haric (girintili) ve yorumlar haric.
  onceki="$(awk -v k="$kapi_satir" 'NR<k && /^[a-zA-Z_]/ && !/^#/' "$BETIK" \
            | grep -cE '^(docker|git (fetch|checkout|reset|pull)|systemctl|dpkg|apt-get install)' || true)"
  _k "T16/T17/T18 kapidan once uretim mutasyonu YOK" "$onceki" "0"

  # `.env` secret onarimi kapidan SONRA olmali (yazma islemidir).
  env_satir="$(grep -n 'e1_env_ensure_secret "FTP_PASSWORD"' "$BETIK" | head -1 | cut -d: -f1)"
  if [[ -n "$env_satir" ]] && [[ "$env_satir" -gt "$kapi_satir" ]]; then
    _ok "T16 .env secret onarimi kapidan SONRA (satir ${env_satir})"
  else
    _fail ".env secret onarimi kapidan ONCE — 'kurulum degismedi' iddiasi yalan olur"
  fi
fi

# ---------------------------------------------------------------------------
# Eski FAIL-OPEN metinleri tamamen gitti mi?
# ---------------------------------------------------------------------------
for kalip in "yedek ATLANDI" "update yine de devam ediyor" "yedek atlandi (ilk kurulum sonrasi?)"; do
  if grep -qF "$kalip" "$BETIK"; then
    _fail "eski fail-open davranis metni hala var: ${kalip}"
  else
    _ok "eski fail-open metni kaldirilmis: ${kalip}"
  fi
done

echo
echo "test-update-backup-gate: ${gecti} gecti, ${basarisiz} basarisiz"
[[ "$basarisiz" -eq 0 ]]
