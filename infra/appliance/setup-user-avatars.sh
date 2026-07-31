#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — isletim sistemi hesaplarinin profil resmi
# ===========================================================================
# Cihazdaki insan hesaplarinin giris ekrani avatarini EnerjiOne logosu yapar.
# Amac: musteri sahasindaki makine acildiginda varsayilan gri silueti degil
# urun logosunu gostersin — cihaz "EnerjiOne ekrani" olarak taninsin.
#
# Kullanim:
#   sudo bash setup-user-avatars.sh                # tum insan hesaplari
#   sudo bash setup-user-avatars.sh e1-kiosk ali   # yalnizca verilenler
#
# Idempotent: tekrar calistirmak guvenli.
#
# Env:
#   E1_AVATAR_SRC   kullanilacak PNG (default: <script dizini>/assets/e1-avatar.png)
#   E1_AVATAR_MIN_UID / E1_AVATAR_MAX_UID  insan hesabi araligi (1000 / 60000)
#
# NEDEN IKI YERE YAZIYORUZ: ekran yoneticileri avatari farkli yerlerde arar.
#   * /var/lib/AccountsService/...  -> GNOME/GDM ve modern LightDM greeter'lari
#   * ~/.face                       -> eski LightDM/greeter'lar, bazi masaustleri
# Hangisinin kurulu oldugunu bilemedigimiz icin ikisini de yaziyoruz.
#
# GORSEL NEDEN KARE VE DOLU ZEMINLI: GDM avatari DAIRE olarak kirpar. Seffaf
# kenarli dikdortgen bir logoda kirpim koseleri yiyor ve isaret kuculuyordu;
# assets/e1-avatar.png 512x512, turuncu dolu zeminli ve isaret dairenin
# guvenli alanina olceklenmis durumda.
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AVATAR_SRC="${E1_AVATAR_SRC:-${SCRIPT_DIR}/assets/e1-avatar.png}"
MIN_UID="${E1_AVATAR_MIN_UID:-1000}"
MAX_UID="${E1_AVATAR_MAX_UID:-60000}"

ICON_DIR=/var/lib/AccountsService/icons
USER_DIR=/var/lib/AccountsService/users

C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
ok()   { echo "  ${C_GREEN}✓${C_RESET} $*"; }
info() { echo "  ${C_DIM}·${C_RESET} $*"; }
warn() { echo "  ${C_YELLOW}!${C_RESET} $*" >&2; }

[[ $EUID -eq 0 ]] || { warn "root gerekli: sudo bash $0"; exit 0; }

if [[ ! -f "$AVATAR_SRC" ]]; then
  warn "Avatar gorseli yok (${AVATAR_SRC}) — atlandi."
  exit 0
fi

# --- Hedef hesaplar ---------------------------------------------------------
# Argüman verilmisse onlar; yoksa TUM insan hesaplari.
#
# "Insan hesabi" = uid araliginda + gercek kabuk. Servis hesaplarini
# (nologin/false) disarida birakiyoruz; onlarin giris ekrani yok, avatar
# yazmak sadece kirlilik olur.
_insan_hesaplari() {
  getent passwd | awk -F: -v lo="$MIN_UID" -v hi="$MAX_UID" '
    $3 >= lo && $3 <= hi && $7 !~ /(nologin|\/false|\/sync)$/ { print $1 }
  '
}

if [[ $# -gt 0 ]]; then
  HEDEFLER=("$@")
else
  mapfile -t HEDEFLER < <(_insan_hesaplari)
fi

if [[ ${#HEDEFLER[@]} -eq 0 ]]; then
  info "Profil resmi atanacak hesap bulunamadi."
  exit 0
fi

# --- Tek hesaba uygula ------------------------------------------------------
_avatar_uygula() {
  local kul="$1" ev
  id -u "$kul" >/dev/null 2>&1 || { warn "'${kul}' hesabi yok — atlandi."; return 0; }

  ev="$(getent passwd "$kul" | cut -d: -f6)"
  # ~/.face — sahibi kullanicinin KENDISI olmali, aksi halde greeter okumaz.
  if [[ -n "$ev" && -d "$ev" ]]; then
    install -o "$kul" -g "$(id -gn "$kul")" -m 644 "$AVATAR_SRC" "${ev}/.face" 2>/dev/null \
      || warn "${kul}: ~/.face yazilamadi."
  fi

  # AccountsService yoksa (minimal kurulum) ~/.face ile yetiniyoruz.
  [[ -d /var/lib/AccountsService ]] || return 0

  install -d -m 775 "$ICON_DIR"
  install -d -m 700 "$USER_DIR"
  install -m 644 "$AVATAR_SRC" "${ICON_DIR}/${kul}"

  # users/<kullanici> bir INI ve BASKA anahtarlar tasiyabilir (Session,
  # XSession, SystemAccount...). Kiosk oturumu bunlara bagli oldugu icin
  # dosyayi EZMIYORUZ: Icon varsa guncelle, yoksa [User] bolumune ekle.
  local dosya="${USER_DIR}/${kul}"
  if [[ -f "$dosya" ]]; then
    if grep -qE '^\s*Icon\s*=' "$dosya"; then
      sed -i -E "s|^\s*Icon\s*=.*|Icon=${ICON_DIR}/${kul}|" "$dosya"
    elif grep -qE '^\s*\[User\]' "$dosya"; then
      sed -i -E "0,/^\s*\[User\]/s||[User]\nIcon=${ICON_DIR}/${kul}|" "$dosya"
    else
      printf '[User]\nIcon=%s\n' "${ICON_DIR}/${kul}" >> "$dosya"
    fi
  else
    printf '[User]\nIcon=%s\nSystemAccount=false\n' "${ICON_DIR}/${kul}" > "$dosya"
  fi
  chmod 600 "$dosya"
  return 0
}

_sayac=0
for _kul in "${HEDEFLER[@]}"; do
  _avatar_uygula "$_kul" && _sayac=$((_sayac + 1))
done

ok "Profil resmi ayarlandi: ${HEDEFLER[*]} (${_sayac} hesap)"

# AccountsService calisir durumdaysa degisikligi hemen gorsun; degilse
# bir sonraki acilista zaten okunur.
if systemctl is-active --quiet accounts-daemon 2>/dev/null; then
  systemctl try-restart accounts-daemon >/dev/null 2>&1 || true
  info "accounts-daemon yeniden yuklendi."
fi
exit 0
