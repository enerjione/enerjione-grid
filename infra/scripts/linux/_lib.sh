#!/usr/bin/env bash
# EnerjiOne Grid — install/update/uninstall icin ortak arayuz katmani.
# Bu dosya 'source' ile dahil edilir, bagimsiz calistirilmaz.
#
# Tasarim notlari:
#   * Tum ciktilar 74 karakterlik sabit bir govdeye hizalanir; adim satirlari,
#     kutular ve ozet ayni kenar cizgisini paylasir.
#   * Renk yalnizca stdout bir terminal ise kullanilir — `| tee`, journalctl
#     veya dosyaya yonlendirmede duz metin kalir.
#   * Sorular /dev/tty uzerinden okunur. `curl ... | sudo bash` kullaniminda
#     stdin BORU HATTIDIR; eski surum bu yuzden hicbir soru soramiyor ve
#     sessizce varsayilana dusuyordu (systemd kaydi bu yuzden atlaniyordu).

# ---------------------------------------------------------------------------
# Renk paleti
# ---------------------------------------------------------------------------
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
  E1_RED=$'\033[0;31m'
  E1_GREEN=$'\033[0;32m'
  E1_YELLOW=$'\033[1;33m'
  E1_BLUE=$'\033[0;34m'
  E1_CYAN=$'\033[0;36m'
  E1_WHITE=$'\033[97m'
  # Marka turuncusu (#e67c00). 256 renk paletinde en yakin ton 208; 8 renkli
  # eski terminallerde otomatik olarak sariya duser.
  E1_ORANGE=$'\033[38;5;208m'
  E1_BOLD=$'\033[1m'
  E1_DIM=$'\033[2m'
  E1_RESET=$'\033[0m'
else
  E1_RED='' E1_GREEN='' E1_YELLOW='' E1_BLUE='' E1_CYAN='' E1_WHITE='' E1_ORANGE=''
  E1_BOLD='' E1_DIM='' E1_RESET=''
fi

# Govde genisligi — kutular ve ayraclar bunu kullanir.
E1_WIDTH=74

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
# "ENERJI" BEYAZ, "ONE" ve "GRID" TURUNCU.
# ASCII sanatinda harfler bitisik oldugu icin ayirici bosluk sutunu yok;
# bolme noktasi (45. sutun) harf genislikleri sayilarak bulundu. Sanat
# degistirilirse bu satirlarin ikiye ayrilmasi da yeniden hesaplanmali.
e1_banner() {
  local w="${E1_WHITE}${E1_BOLD}" o="${E1_ORANGE}${E1_BOLD}" r="${E1_RESET}"
  echo
  printf '  %s███████╗███╗   ██╗███████╗██████╗      ██╗██╗%s%s ██████╗ ███╗   ██╗███████╗%s\n' "$w" "$r" "$o" "$r"
  printf '  %s██╔════╝████╗  ██║██╔════╝██╔══██╗     ██║██║%s%s██╔═══██╗████╗  ██║██╔════╝%s\n' "$w" "$r" "$o" "$r"
  printf '  %s█████╗  ██╔██╗ ██║█████╗  ██████╔╝     ██║██║%s%s██║   ██║██╔██╗ ██║█████╗  %s\n' "$w" "$r" "$o" "$r"
  printf '  %s██╔══╝  ██║╚██╗██║██╔══╝  ██╔══██╗██   ██║██║%s%s██║   ██║██║╚██╗██║██╔══╝  %s\n' "$w" "$r" "$o" "$r"
  printf '  %s███████╗██║ ╚████║███████╗██║  ██║╚█████╔╝██║%s%s╚██████╔╝██║ ╚████║███████╗%s\n' "$w" "$r" "$o" "$r"
  printf '  %s╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚════╝ ╚═╝%s%s ╚═════╝ ╚═╝  ╚═══╝╚══════╝%s\n' "$w" "$r" "$o" "$r"
  printf '  %s                       ██████╗ ██████╗ ██╗██████╗ %s\n' "$o" "$r"
  printf '  %s                      ██╔════╝ ██╔══██╗██║██╔══██╗%s\n' "$o" "$r"
  printf '  %s                      ██║  ███╗██████╔╝██║██║  ██║%s\n' "$o" "$r"
  printf '  %s                      ██║   ██║██╔══██╗██║██║  ██║%s\n' "$o" "$r"
  printf '  %s                      ╚██████╔╝██║  ██║██║██████╔╝%s\n' "$o" "$r"
  printf '  %s                       ╚═════╝ ╚═╝  ╚═╝╚═╝╚═════╝ %s\n' "$o" "$r"
  printf '%s             Industrial Grid Monitoring Platform%s\n' "${E1_DIM}" "${E1_RESET}"
}

# Adim basliklarinda tekrarlanan tek satirlik marka seridi.
# Surum numarasi E1_VERSION_LABEL set edildiginde gosterilir — install.sh
# repo klonlandiktan SONRA doldurur (oncesinde bilinmiyor).
E1_VERSION_LABEL="${E1_VERSION_LABEL:-}"
e1_brand() {
  printf '%s%sENERJI%s%s%sONE%s %s%sGRID%s' \
    "${E1_WHITE}" "${E1_BOLD}" "${E1_RESET}" \
    "${E1_ORANGE}" "${E1_BOLD}" "${E1_RESET}" \
    "${E1_ORANGE}" "${E1_BOLD}" "${E1_RESET}"
  if [[ -n "$E1_VERSION_LABEL" ]]; then
    printf '%s  v%s%s' "${E1_DIM}" "$E1_VERSION_LABEL" "${E1_RESET}"
  fi
}

# ---------------------------------------------------------------------------
# Kutular ve satirlar
# ---------------------------------------------------------------------------
e1_rule() {
  local ch="${1:-─}" out=""
  local i
  for ((i = 0; i < E1_WIDTH; i++)); do out+="$ch"; done
  printf '  %s%s%s\n' "${E1_DIM}" "$out" "${E1_RESET}"
}

# Baslikli kutu acilisi: e1_box "KURULUM ÖZETİ"
e1_box() {
  local title="$1"
  echo
  e1_rule "─"
  printf '  %s%s%s\n' "${E1_BOLD}" "$title" "${E1_RESET}"
  e1_rule "─"
}

# Hizali anahtar/deger satiri: e1_kv "Hedef dizin" "/opt/enerjione-grid"
e1_kv() {
  printf '  %s%-16s%s %s\n' "${E1_DIM}" "$1" "${E1_RESET}" "$2"
}

# ---------------------------------------------------------------------------
# Adimlar — sure olcumu ile
# ---------------------------------------------------------------------------
E1_STEP_TOTAL=0
E1_STEP_CURRENT=0
E1_STEP_STARTED_AT=0
# Acik adim var mi? `E1_STEP_STARTED_AT > 0` ile kontrol ETMEYIN: ilk adim
# script'in 0. saniyesinde baslarsa degeri 0 kalir ve suresi hic basilmaz.
E1_STEP_ACTIVE=0
E1_RUN_STARTED_AT=$SECONDS

e1_set_steps() {
  E1_STEP_TOTAL="$1"
  E1_STEP_CURRENT=0
  E1_STEP_ACTIVE=0
  E1_RUN_STARTED_AT=$SECONDS
}

# Saniyeyi "1 dk 12 sn" gibi okunur hale getirir.
e1_fmt_duration() {
  local s="${1:-0}"
  if ((s < 60)); then
    printf '%d sn' "$s"
  else
    printf '%d dk %d sn' "$((s / 60))" "$((s % 60))"
  fi
}

# Onceki adimin suresini kapatip yenisini acar.
e1_step() {
  if ((E1_STEP_ACTIVE == 1)); then
    printf '  %s└ %s%s\n' "${E1_DIM}" "$(e1_fmt_duration $((SECONDS - E1_STEP_STARTED_AT)))" "${E1_RESET}"
  fi
  E1_STEP_CURRENT=$((E1_STEP_CURRENT + 1))
  E1_STEP_STARTED_AT=$SECONDS
  E1_STEP_ACTIVE=1
  echo
  # Marka seridi her adimda tekrarlanir: uzun kurulumda ekranin neresine
  # bakarsa baksin kullanici hangi urunun kuruldugunu ve surumu gorur.
  # Adim sayaci saga yaslanir.
  local brand step_tag pad
  brand="$(e1_brand)"
  step_tag="[${E1_STEP_CURRENT}/${E1_STEP_TOTAL}]"
  # Gorunur uzunluk: renk kodlari haric. `e1_brand` sabit metin urettigi icin
  # dolgu hesabini ham uzunluktan degil bilinen etiketten yapiyoruz.
  local visible="ENERJIONE GRID"
  [[ -n "$E1_VERSION_LABEL" ]] && visible="${visible}  v${E1_VERSION_LABEL}"
  pad=$((E1_WIDTH - ${#visible} - ${#step_tag}))
  ((pad < 1)) && pad=1
  printf '  %s%*s%s%s%s\n' "$brand" "$pad" "" "${E1_BOLD}${E1_BLUE}" "$step_tag" "${E1_RESET}"
  printf '  %s%s%s\n' "${E1_BOLD}" "$*" "${E1_RESET}"
}

# Son adimin suresini kapat (ozet oncesi cagirilir).
e1_step_done() {
  if ((E1_STEP_ACTIVE == 1)); then
    printf '  %s└ %s%s\n' "${E1_DIM}" "$(e1_fmt_duration $((SECONDS - E1_STEP_STARTED_AT)))" "${E1_RESET}"
    E1_STEP_ACTIVE=0
  fi
}

e1_total_elapsed() { e1_fmt_duration $((SECONDS - E1_RUN_STARTED_AT)); }

# ---------------------------------------------------------------------------
# Log seviyeleri
# ---------------------------------------------------------------------------
e1_info() { printf '  %s·%s %s\n' "${E1_CYAN}" "${E1_RESET}" "$*"; }
e1_ok()   { printf '  %s✓%s %s\n' "${E1_GREEN}" "${E1_RESET}" "$*"; }
e1_warn() { printf '  %s!%s %s\n' "${E1_YELLOW}" "${E1_RESET}" "$*" >&2; }
e1_err()  { printf '  %s✗%s %s\n' "${E1_RED}" "${E1_RESET}" "$*" >&2; }

# Uzun surecek adimlar icin beklenti yonetimi — kullanici donduk sanmasin.
e1_hint() { printf '  %s  %s%s\n' "${E1_DIM}" "$*" "${E1_RESET}"; }

# ---------------------------------------------------------------------------
# Uzun komutlari CANLI sayacla calistir
# ---------------------------------------------------------------------------
# apt-get, git clone gibi adimlar cikti uretmeden dakikalarca surebiliyor ve
# kurulumcu ekrana bakip "kilitlendi" diyerek Ctrl+C yapiyordu. Bu sarmalayici
# komutu arka planda calistirip her 3 saniyede gecen sureyi ayni satira yazar;
# ekranda HER ZAMAN hareket olur. Komut cikti uretse bile onu loga alir, hata
# durumunda son satirlari gosterir.
#
#   e1_run "Kaynak kod indiriliyor" git clone --branch x URL DIR
e1_run() {
  local label="$1"; shift
  local logf rc=0 secs=0 pid
  logf="$(mktemp)"

  "$@" >"$logf" 2>&1 &
  pid=$!

  printf '  %s·%s %s… ' "${E1_CYAN}" "${E1_RESET}" "$label"
  # 1 sn'lik tik: gecen sure gercek suredir. 3 sn'lik tikte 4 sn suren bir
  # komut "6 sn" gorunuyordu.
  while kill -0 "$pid" 2>/dev/null; do
    sleep 1
    secs=$((secs + 1))
    # \r ile ayni satiri tazeleriz; log dosyasina yonlendirilmis calistirmada
    # (tty yok) bu satir sadece bir kez yazilir, kirlilik olusturmaz.
    if [[ -t 1 ]]; then
      printf '\r  %s·%s %s… %s%s%s ' \
        "${E1_CYAN}" "${E1_RESET}" "$label" "${E1_DIM}" "$(e1_fmt_duration $secs)" "${E1_RESET}"
    fi
  done
  wait "$pid" || rc=$?
  [[ -t 1 ]] && printf '\r%*s\r' 78 ''

  if ((rc != 0)); then
    e1_err "${label}: BASARISIZ (cikis kodu ${rc})"
    e1_err "Son satirlar:"
    tail -n 20 "$logf" | sed 's/^/      /' >&2
    rm -f "$logf"
    return "$rc"
  fi
  rm -f "$logf"
  e1_ok "${label} — $(e1_fmt_duration $secs)"
  return 0
}

# ---------------------------------------------------------------------------
# Hata cikisi + beklenmeyen hata yakalayici
# ---------------------------------------------------------------------------
E1_DYING=0

e1_die() {
  E1_DYING=1
  e1_step_done
  echo >&2
  e1_rule "═" >&2
  printf '  %s%sKURULUM DURDU%s\n' "${E1_RED}" "${E1_BOLD}" "${E1_RESET}" >&2
  e1_rule "═" >&2
  echo >&2
  printf '  %b\n' "$*" >&2
  echo >&2
  if [[ -n "${E1_HELP_HINT:-}" ]]; then
    printf '  %s%s%s\n\n' "${E1_DIM}" "${E1_HELP_HINT}" "${E1_RESET}" >&2
  fi
  exit 1
}

# `set -e` altinda beklenmeyen bir komut patlarsa sessizce kapanmak yerine
# nerede kirildigini soyler. install.sh/update.sh basinda etkinlestirilir.
e1__on_error() {
  local code="$1" line="$2" cmd="$3"
  [[ "$E1_DYING" == "1" ]] && return 0
  e1_step_done
  echo >&2
  e1_rule "═" >&2
  printf '  %s%sBEKLENMEYEN HATA%s\n' "${E1_RED}" "${E1_BOLD}" "${E1_RESET}" >&2
  e1_rule "═" >&2
  echo >&2
  printf '  %sSatir      :%s %s\n' "${E1_DIM}" "${E1_RESET}" "$line" >&2
  printf '  %sKomut      :%s %s\n' "${E1_DIM}" "${E1_RESET}" "$cmd" >&2
  printf '  %sCikis kodu :%s %s\n' "${E1_DIM}" "${E1_RESET}" "$code" >&2
  echo >&2
  printf '  Bu ciktinin tamamini teknik destege iletin.\n' >&2
  echo >&2
  exit "$code"
}

e1_enable_error_trap() {
  trap 'e1__on_error "$?" "$LINENO" "$BASH_COMMAND"' ERR
}

# ---------------------------------------------------------------------------
# Sorular — /dev/tty uzerinden (curl | bash ile de calisir)
# ---------------------------------------------------------------------------
# Okuma kaynagi: stdin terminal ise stdin, degilse /dev/tty. Ikisi de yoksa
# (systemd, cron, CI) soru sorulamaz; cagiran varsayilana duser.
_e1_read_src() {
  if [[ -t 0 ]]; then printf '/dev/stdin'; return 0; fi
  # `-r` yetmez: kontrol terminali olmayan ortamlarda (systemd, bazi
  # konteynerler) /dev/tty dosya olarak gorunur ama ACILAMAZ ve bash ham
  # bir "No such device or address" hatasi sizdirir. Gercekten acilabiliyor
  # mu diye deneriz.
  if [[ -r /dev/tty ]] && (: < /dev/tty) 2>/dev/null; then
    printf '/dev/tty'
    return 0
  fi
  return 1
}

# Cevap gelmezse sonsuza kadar beklemeyiz: /dev/tty erisilebilir ama kimsenin
# basinda olmadigi bir ortamda (konsola bagli cron/systemd) deploy asili
# kalirdi. Sure dolunca varsayilan uygulanir ve bu ekrana yazilir.
E1_PROMPT_TIMEOUT="${E1_PROMPT_TIMEOUT:-300}"

# Evet/Hayir — varsayilan HAYIR. "e/evet/y/yes" kabul edilir.
e1_confirm() {
  local prompt="${1:-Devam edilsin mi?}" src ans
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then return 0; fi
  if ! src="$(_e1_read_src)"; then
    e1_warn "Terminal yok — soru sorulamadi, varsayilan kullanildi: HAYIR"
    return 1
  fi
  printf '  %s?%s %s %s[e/H]%s ' "${E1_YELLOW}" "${E1_RESET}" "$prompt" "${E1_DIM}" "${E1_RESET}"
  if ! read -r -t "$E1_PROMPT_TIMEOUT" ans < "$src"; then
    ans=""
    echo
    e1_warn "Sure doldu — varsayilan kullanildi: HAYIR"
  fi
  [[ "$ans" =~ ^([eE]([vV][eE][tT])?|[yY]([eE][sS])?)$ ]]
}

# Evet/Hayir — varsayilan EVET. Guclu bir otomatik tespit varken kullanilir.
e1_confirm_yes() {
  local prompt="${1:-Devam edilsin mi?}" src ans
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then return 0; fi
  if ! src="$(_e1_read_src)"; then
    e1_warn "Terminal yok — soru sorulamadi, varsayilan kullanildi: EVET"
    return 0
  fi
  printf '  %s?%s %s %s[E/h]%s ' "${E1_YELLOW}" "${E1_RESET}" "$prompt" "${E1_DIM}" "${E1_RESET}"
  if ! read -r -t "$E1_PROMPT_TIMEOUT" ans < "$src"; then
    ans=""
    echo
    e1_warn "Sure doldu — varsayilan kullanildi: EVET"
  fi
  [[ ! "$ans" =~ ^([hH]([aA][yY][iI][rR])?|[nN]([oO])?)$ ]]
}

# Serbest metin sorusu — bos birakilirsa varsayilan doner.
#   ad="$(e1_ask 'Cihaz adi' 'e1-grid')"
e1_ask() {
  local prompt="$1" default="${2:-}" src ans
  if [[ "${ASSUME_YES:-0}" == "1" ]] || ! src="$(_e1_read_src)"; then
    printf '%s' "$default"
    return 0
  fi
  if [[ -n "$default" ]]; then
    printf '  %s?%s %s %s[%s]%s ' "${E1_YELLOW}" "${E1_RESET}" "$prompt" "${E1_DIM}" "$default" "${E1_RESET}" >&2
  else
    printf '  %s?%s %s ' "${E1_YELLOW}" "${E1_RESET}" "$prompt" >&2
  fi
  read -r -t "$E1_PROMPT_TIMEOUT" ans < "$src" || ans=""
  printf '%s' "${ans:-$default}"
}

# Bu calistirmada soru sorulabilir mi? Ozet metinlerinde kullanilir.
e1_can_prompt() { [[ "${ASSUME_YES:-0}" != "1" ]] && _e1_read_src >/dev/null; }

# ---------------------------------------------------------------------------
# Ortam tespiti
# ---------------------------------------------------------------------------
# Makinede WiFi arayuzu var mi? Appliance (mini PC) modunu otomatik acmak
# icin: VPS'lerde WiFi karti YOKTUR, saha mini PC'lerinde vardir. sysfs
# uzerinden bakariz — nmcli/iw bu asamada henuz kurulu olmayabilir.
e1_has_wifi() {
  local dir
  for dir in /sys/class/net/*/wireless; do
    [[ -d "$dir" ]] && return 0
  done
  # Bazi surucular 'wireless' yerine 'phy80211' symlink'i birakir.
  for dir in /sys/class/net/*/phy80211; do
    [[ -e "$dir" ]] && return 0
  done
  return 1
}

# Appliance modu bu makinede kurulu mu? (setup-appliance.sh calistirilmis mi)
e1_appliance_installed() {
  [[ -f /etc/systemd/system/e1-netd.path ]] || [[ -d /var/lib/e1-grid/net ]]
}

# Urun surumu — tek kanonik kaynak apps/frontend-web/package.json "version".
# Branch adi kullanici icin anlamsiz; ekranlarda numarali surum gosterilir.
# Argüman: repo koku (verilmezse calisilan dizin).
e1_version() {
  local root="${1:-.}" pkg="${1:-.}/apps/frontend-web/package.json" v=""
  [[ -f "$pkg" ]] || { echo "?"; return 0; }
  v="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$pkg" | head -1)"
  echo "${v:-?}"
  unset root
}

# Root kontrolu — install/update/uninstall icin zorunlu.
e1_require_root() {
  if [[ $EUID -ne 0 ]]; then
    e1_die "Bu komut yonetici (root) yetkisi ile calismali:\n\n      sudo bash $0 $*"
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

# VDS IP'sini bul — once `hostname -I` (local interface, instant, offline-safe),
# yoksa veya invalid ise public IP icin curl ifconfig.me. Bash `||` zincirinde
# bos string'in exit=0 dondurmesi tuzak; her adimi explicit kontrol et.
e1_detect_ip() {
  local ip=""
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [[ -n "$ip" ]] && [[ "$ip" != "127.0.0.1" ]] && [[ "$ip" != "<vds-ip>" ]]; then
    echo "$ip"
    return 0
  fi
  ip="$(curl -fsS --max-time 3 ifconfig.me 2>/dev/null || true)"
  if [[ -n "$ip" ]]; then
    echo "$ip"
    return 0
  fi
  echo "<vds-ip>"
}
