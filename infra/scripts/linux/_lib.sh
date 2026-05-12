#!/usr/bin/env bash
# EnerjiOne Grid — install/update/uninstall icin ortak banner + log helpers.
# Bu dosya 'source' ile dahil edilir, bagimsiz calistirilmaz.

# Renkli output (sadece tty ise) — pipe/journalctl'a duz metin gider.
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
  E1_RED=$'\033[0;31m'
  E1_GREEN=$'\033[0;32m'
  E1_YELLOW=$'\033[1;33m'
  E1_BLUE=$'\033[0;34m'
  E1_CYAN=$'\033[0;36m'
  E1_BOLD=$'\033[1m'
  E1_DIM=$'\033[2m'
  E1_RESET=$'\033[0m'
else
  E1_RED='' E1_GREEN='' E1_YELLOW='' E1_BLUE='' E1_CYAN='' E1_BOLD='' E1_DIM='' E1_RESET=''
fi

# Banner — repo kokunde aynisini yazmak yerine tek yerden basariz.
# Tire + alt-cizgi karisik, "ENERJIONE GRID" stilize.
e1_banner() {
  local subtitle="${1:-}"
  cat <<BANNER
${E1_CYAN}${E1_BOLD}
 _____ _   _ _____ ____    _ _____ ___  _   _ _____    ____ ____  ___ ____
| ____| \\ | | ____|  _ \\  | |_   _/ _ \\| \\ | | ____|  / ___|  _ \\|_ _|  _ \\
|  _| |  \\| |  _| | |_) | | | | || | | |  \\| |  _|   | |  _| |_) || || | | |
| |___| |\\  | |___|  _ <  | | | || |_| | |\\  | |___  | |_| |  _ < | || |_| |
|_____|_| \\_|_____|_| \\_\\_/ | |_| \\___/|_| \\_|_____|  \\____|_| \\_\\___|____/
                       |__/
${E1_RESET}${E1_DIM}                Industrial Grid Monitoring Platform${E1_RESET}
BANNER
  if [[ -n "$subtitle" ]]; then
    echo "${E1_DIM}                $(printf '%*s' $(( (60 - ${#subtitle}) / 2 )) '')${subtitle}${E1_RESET}"
  fi
  echo
}

# Adim sayisi — ana adimlar [1/5] tarzi prefix icin.
E1_STEP_TOTAL=0
E1_STEP_CURRENT=0
e1_set_steps() { E1_STEP_TOTAL="$1"; E1_STEP_CURRENT=0; }
e1_step() {
  E1_STEP_CURRENT=$((E1_STEP_CURRENT + 1))
  echo
  echo "${E1_BOLD}${E1_BLUE}[${E1_STEP_CURRENT}/${E1_STEP_TOTAL}]${E1_RESET} ${E1_BOLD}$*${E1_RESET}"
}

# Log seviyeleri — her birinin renkli prefix'i var.
e1_info() { echo "  ${E1_CYAN}>${E1_RESET} $*"; }
e1_ok()   { echo "  ${E1_GREEN}OK${E1_RESET}  $*"; }
e1_warn() { echo "  ${E1_YELLOW}!${E1_RESET}   $*" >&2; }
e1_err()  { echo "  ${E1_RED}X${E1_RESET}   $*" >&2; }

# Hata + cikis — son hata mesajini vurgulu basar.
e1_die() {
  echo >&2
  echo "${E1_RED}${E1_BOLD}HATA:${E1_RESET} $*" >&2
  echo >&2
  exit 1
}

# Onay sorma — --yes/-y bypass icin ASSUME_YES degiskeni dis kodda set
# edilebilir. tty yoksa default reddet (curl | bash icin onay gerektiren
# adimlari atla).
e1_confirm() {
  local prompt="${1:-Devam edilsin mi?}"
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then return 0; fi
  if [[ ! -t 0 ]]; then
    e1_warn "Interaktif olmayan ortam (tty yok). Default: HAYIR."
    return 1
  fi
  read -r -p "  ${E1_YELLOW}?${E1_RESET}   ${prompt} [y/N]: " ans
  [[ "$ans" =~ ^[yY]([eE][sS])?$ ]]
}

# Root kontrolu — install/uninstall icin zorunlu.
e1_require_root() {
  if [[ $EUID -ne 0 ]]; then
    e1_die "Bu komut root yetkisi ile calismali. Su sekilde:\n         sudo bash $0 $*"
  fi
}

# Cagiran kullanici (SUDO_USER) — sahiplenme icin. Yoksa root kalir.
e1_target_user() {
  local u="${INSTALL_USER:-${SUDO_USER:-}}"
  if [[ -n "$u" ]] && id -u "$u" >/dev/null 2>&1; then
    echo "$u"
  fi
}
e1_chown_target() {
  local u
  u="$(e1_target_user)"
  if [[ -n "$u" ]]; then
    chown "$(id -u "$u"):$(id -g "$u")" "$@" 2>/dev/null || true
  fi
}
e1_chown_target_recursive() {
  local u
  u="$(e1_target_user)"
  if [[ -n "$u" ]]; then
    chown -R "$(id -u "$u"):$(id -g "$u")" "$@" 2>/dev/null || true
  fi
}

# VDS IP'sini bul — banner sonu rehber icin.
e1_detect_ip() {
  curl -fsS --max-time 3 ifconfig.me 2>/dev/null \
    || hostname -I 2>/dev/null | awk '{print $1}' \
    || echo "<vds-ip>"
}
