#!/usr/bin/env bash
# ===========================================================================
# e1_prune_pre_update_backups davranis testi (denetim A12)
# ===========================================================================
#
# YASANAN SORUN
# -------------
# `update.sh` her calismasinda `backups/auto-pre-update-*.sql.gz` birakiyordu
# ve bu dosyalar HICBIR ZAMAN silinmiyordu. Dizin BACKUP_DIR volume'u degil,
# compose proje dizini; dolayisiyla ne disk_guard, ne apply_retention, ne de
# UI onlari goruyordu. Birkac guncelleme sonra 500 GB'lik diskin buyuk kismi
# bu dosyalarla doluyor, postgres-data ayni dosya sisteminde oldugu icin
# sonunda Postgres yazamaz hale geliyor ve telemetri akisi duruyordu.
#
# Bu test sozdizimini degil DAVRANISI dogrular: dogru sayida dosya kaliyor mu,
# EN YENILERI mi kaliyor, eski `.sql.gz` yigini da temizleniyor mu, ve
# kapsam disindaki dosyalara dokunuluyor mu.
#
# Kullanim: bash test-pre-update-backup-rotation.sh [repo-koku]
# ===========================================================================
set -euo pipefail

# tests -> linux -> scripts -> infra -> repo koku
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
LIB="${REPO}/infra/scripts/linux/_lib.sh"

[[ -f "$LIB" ]] || { echo "HATA: _lib.sh bulunamadi: $LIB" >&2; exit 1; }

# _lib.sh'i sessiz yukle (e1_info ciktisi testi kirletmesin).
# shellcheck disable=SC1090
source "$LIB" >/dev/null 2>&1
e1_info() { :; }

FAIL=0
_ok()   { printf '  ok   %s\n' "$1"; }
_fail() { printf '  FAIL %s\n' "$1" >&2; FAIL=1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Yasi belirli test dosyalari uret. mtime'i geriye alarak "en yeni" secimini
# dosya adina degil GERCEK zaman damgasina gore sinariz.
_mk() {
  local name="$1" minutes_ago="$2"
  : > "${WORK}/backups/${name}"
  touch -d "${minutes_ago} minutes ago" "${WORK}/backups/${name}"
}

_count() { find "${WORK}/backups" -maxdepth 1 -type f -name 'auto-pre-update-*' | wc -l; }
_exists() { [[ -f "${WORK}/backups/$1" ]]; }

# ---------------------------------------------------------------------------
# 1) En yeni 3 tanesi kalir, digerleri silinir
# ---------------------------------------------------------------------------
rm -rf "${WORK}/backups"; mkdir -p "${WORK}/backups"
_mk "auto-pre-update-20260101-000000.dump" 500
_mk "auto-pre-update-20260102-000000.dump" 400
_mk "auto-pre-update-20260103-000000.dump" 300
_mk "auto-pre-update-20260104-000000.dump" 200
_mk "auto-pre-update-20260105-000000.dump" 100

e1_prune_pre_update_backups 3 "${WORK}/backups"

if [[ "$(_count)" -eq 3 ]]; then
  _ok "keep=3 -> 3 dosya kaldi"
else
  _fail "keep=3 -> beklenen 3, bulunan $(_count)"
fi

if _exists "auto-pre-update-20260105-000000.dump" \
  && _exists "auto-pre-update-20260104-000000.dump" \
  && _exists "auto-pre-update-20260103-000000.dump"; then
  _ok "kalanlar EN YENI uc dosya"
else
  _fail "yanlis dosyalar korunmus (en yeniler silinmis olabilir)"
fi

if _exists "auto-pre-update-20260101-000000.dump"; then
  _fail "en eski dosya silinmemis"
else
  _ok "en eski dosyalar silindi"
fi

# ---------------------------------------------------------------------------
# 2) ESKI SURUMLERIN .sql.gz YIGINI DA TEMIZLENIR
#
# Sahadaki cihazlarda diski dolduran asil kalem bunlar. Kalip yalnizca
# `.dump` uzantisini eslerse bu dosyalar sonsuza kadar kalirdi.
# ---------------------------------------------------------------------------
rm -rf "${WORK}/backups"; mkdir -p "${WORK}/backups"
_mk "auto-pre-update-20251201-000000.sql.gz" 900
_mk "auto-pre-update-20251202-000000.sql.gz" 800
_mk "auto-pre-update-20251203-000000.sql.gz" 700
_mk "auto-pre-update-20260105-000000.dump" 10

e1_prune_pre_update_backups 1 "${WORK}/backups"

if [[ "$(_count)" -eq 1 ]] && _exists "auto-pre-update-20260105-000000.dump"; then
  _ok "eski .sql.gz yigini temizlendi, en yeni .dump kaldi"
else
  _fail "legacy .sql.gz temizlenmedi (kalan: $(_count))"
fi

# ---------------------------------------------------------------------------
# 3) KAPSAM DISI dosyalara DOKUNULMAZ
#
# Ayni dizinde operatorun elle aldigi yedekler olabilir; rotasyonun onlari
# silmesi veri kaybidir.
# ---------------------------------------------------------------------------
rm -rf "${WORK}/backups"; mkdir -p "${WORK}/backups"
_mk "auto-pre-update-20260101-000000.dump" 500
_mk "auto-pre-update-20260102-000000.dump" 400
: > "${WORK}/backups/elle-alinan-yedek.dump"
: > "${WORK}/backups/e1-20260101-000000-manual.dump"

e1_prune_pre_update_backups 1 "${WORK}/backups"

if [[ -f "${WORK}/backups/elle-alinan-yedek.dump" ]] \
  && [[ -f "${WORK}/backups/e1-20260101-000000-manual.dump" ]]; then
  _ok "kapsam disi yedeklere dokunulmadi"
else
  _fail "rotasyon ilgisiz yedekleri sildi (VERI KAYBI)"
fi

# ---------------------------------------------------------------------------
# 4) Dizin yoksa / bos ise sessizce gecer (update'i durdurmamali)
# ---------------------------------------------------------------------------
if e1_prune_pre_update_backups 3 "${WORK}/olmayan-dizin"; then
  _ok "olmayan dizinde hata vermiyor"
else
  _fail "olmayan dizinde hata verdi — update yarida kalirdi"
fi

rm -rf "${WORK}/backups"; mkdir -p "${WORK}/backups"
if e1_prune_pre_update_backups 3 "${WORK}/backups"; then
  _ok "bos dizinde hata vermiyor"
else
  _fail "bos dizinde hata verdi"
fi

# ---------------------------------------------------------------------------
# 5) Gecersiz keep degeri guvenli varsayilana duser (her seyi SILMEZ)
# ---------------------------------------------------------------------------
rm -rf "${WORK}/backups"; mkdir -p "${WORK}/backups"
_mk "auto-pre-update-20260101-000000.dump" 500
_mk "auto-pre-update-20260102-000000.dump" 400

e1_prune_pre_update_backups "abc" "${WORK}/backups"

if [[ "$(_count)" -eq 2 ]]; then
  _ok "gecersiz keep -> varsayilan 3, dosyalar korundu"
else
  _fail "gecersiz keep dosyalari sildi (kalan: $(_count))"
fi

echo
if [[ $FAIL -eq 0 ]]; then
  echo "TAMAM: pre-update yedek rotasyonu dogru calisiyor."
else
  echo "BASARISIZ: rotasyon davranisi bozuk." >&2
fi
exit $FAIL
