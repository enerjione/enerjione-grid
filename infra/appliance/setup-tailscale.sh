#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Tailscale (uzaktan bakim VPN'i) kurulumu
# ===========================================================================
# Cihazi kurulum aninda otomatik olarak tailnet'e katar; boylece saha
# PC'sine port acmadan, statik IP/DDNS olmadan, NAT arkasindan uzaktan
# bakim yapilabilir.
#
# NEDEN TAILSCALE:
#   Saha cihazlari genelde musteri agindadir; disaridan erisim icin port
#   yonlendirme istemek hem guvenlik hem operasyon yuku. Tailscale (WireGuard)
#   giden baglanti kurar, dinleyen port ACMAZ.
#
# ANAHTAR (E1_TAILSCALE_AUTHKEY) — ZORUNLU:
#   Bos ise bu script HICBIR SEY YAPMAZ ve 0 doner (kurulumu bozmaz).
#   Iki tur anahtar da calisir:
#     tskey-auth-...    reusable + pre-approved auth key
#     tskey-client-...  OAuth client secret (tailscale kendi anahtarini uretir)
#   Anahtar ASLA repoya girmez; .env veya ortam degiskeni ile gelir.
#
# ETIKET (E1_TAILSCALE_TAGS):
#   Cihazlar etiketli katilir (varsayilan: tag:e1-appliance). Erisim yetkisi
#   tailnet ACL'inde bu etikete gore verilir — cihaz tailnet'teki her seye
#   erisemez, sadece sizin izin verdiginiz kadar. OAuth anahtari kullaniyorsan
#   etiket ZORUNLUDUR.
#
# Env:
#   E1_TAILSCALE_AUTHKEY    auth key / OAuth secret (bos = kurulum atlanir)
#   E1_TAILSCALE_TAGS       virgullu etiket listesi (default: tag:e1-appliance)
#   E1_TAILSCALE_HOSTNAME   tailnet'te gorunecek ad. Bos ise once saha
#                           kimliginden (/etc/enerjione-grid/site.env),
#                           yoksa donanim seri no'sundan uretilir.
#   E1_TAILSCALE_HOSTNAME_PREFIX  uretilen adin oneki (default: e1-grid)
#   E1_TAILSCALE_SSH        1 = Tailscale SSH ac (default 1), 0 = kapali
#   E1_TAILSCALE_ACCEPT_DNS 1 = tailnet DNS'ini kabul et (default 0)
#                           0 onerilir: saha cihazinin yerel DNS'i bozulmasin.
#
# Idempotent: cihaz zaten tailnet'e bagliysa tekrar login DENENMEZ.
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# _lib.sh varsa ortak ciktilari kullan; yoksa sade fallback tanimla.
if [[ -f "${SCRIPT_DIR}/../scripts/linux/_lib.sh" ]]; then
  # shellcheck source=/dev/null
  . "${SCRIPT_DIR}/../scripts/linux/_lib.sh"
else
  e1_info() { printf '  · %s\n' "$*"; }
  e1_ok()   { printf '  ✓ %s\n' "$*"; }
  e1_warn() { printf '  ! %s\n' "$*" >&2; }
  e1_hint() { printf '    %s\n' "$*"; }
  e1_step() { printf '\n== %s\n' "$*"; }
fi

AUTHKEY="${E1_TAILSCALE_AUTHKEY:-}"
TAGS="${E1_TAILSCALE_TAGS:-tag:e1-appliance}"
ENABLE_SSH="${E1_TAILSCALE_SSH:-1}"
ACCEPT_DNS="${E1_TAILSCALE_ACCEPT_DNS:-0}"
# Tailnet'te gorunecek adin oneki. Cihaza ozel kisim otomatik eklenir.
TS_PREFIX="${E1_TAILSCALE_HOSTNAME_PREFIX:-e1-grid}"

# --- Cihaza OZEL tailnet adi -----------------------------------------------
# SORUN: sistem hostname'i her cihazda ayni (`e1-grid`) — cunku `e1-grid.local`
# sahada standart erisim adresi ve site basina tek cihaz oldugu icin yerel
# agda cakisma yok. Ama tailnet TEK bir isim alanidir; ayni adla katilan
# cihazlari Tailscale `e1-grid-1`, `e1-grid-2`... diye numaralandirir ve
# hangisinin hangi saha oldugu ANLASILMAZ olur.
#
# COZUM: sistem hostname'ine DOKUNMUYORUZ (e1-grid.local calismaya devam
# eder); Tailscale'e ayri, cihaza ozel bir ad veriyoruz. Oncelik sirasi:
#   1. E1_TAILSCALE_HOSTNAME          -> operator elle sabitlemis
#   2. saha kimligi (site.env)        -> kurulumda sorulan musteri/saha adi
#   3. DMI seri no (Dell "Service Tag")
#   4. /etc/machine-id ilk 8 hane
# (2) TERCIH EDILENDIR: konsolda "e1-grid-tpao-batman-osb" gorunur, seri
# numarasi gorunmez. Seri no yedek yoldur — sanal makinelerde bulunmaz ve
# bulundugunda bile hangi saha oldugunu soylemez.
SITE_ENV="${E1_SITE_ENV:-/etc/enerjione-grid/site.env}"

# site.env'i `source` ETMIYORUZ: kurulum ortamini bozabilecek satirlar
# olabilir; sadece istenen anahtari cekiyoruz.
_site_var() {
  [[ -f "$SITE_ENV" ]] || return 1
  sed -n \
    -e "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\(.*\)\"[[:space:]]*\$/\1/p" \
    -e "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\([^\"#][^#]*\).*\$/\1/p" \
    "$SITE_ENV" | tail -1 | sed -e 's/[[:space:]]*$//' -e 's/\\"/"/g'
}

# TS_HOSTNAME ve TS_NAME_SOURCE degiskenlerini DOGRUDAN atar (deger
# dondurmez): kaynagi da bildirmesi gerekiyor ve `$( )` icinde yapilan
# atamalar alt kabukta kalip kaybolurdu.
_derive_ts_hostname() {
  local serial="" f site
  # (2) Saha kimligi — kurulumda sorulmus musteri/saha adi.
  site="${E1_SITE_ID:-$(_site_var E1_SITE_ID || true)}"
  site="$(printf '%s' "$site" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')"
  if [[ -n "$site" ]]; then
    TS_NAME_SOURCE="saha"
    TS_HOSTNAME="${TS_PREFIX}-${site:0:48}"
    return 0
  fi
  TS_NAME_SOURCE="donanim"
  for f in /sys/class/dmi/id/product_serial /sys/class/dmi/id/board_serial; do
    if [[ -r "$f" ]]; then
      serial="$(tr -d '\0' < "$f" 2>/dev/null | tr -d '[:space:]')"
      [[ -n "$serial" ]] && break
    fi
  done
  # Uretici cogu zaman placeholder birakir — bunlari benzersiz sayma.
  case "$(printf '%s' "$serial" | tr 'A-Z' 'a-z')" in
    ""|none|default*|to*befilled*|systemserial*|serialnumber*|0123456789|na|n/a|unknown|invalid|0|00000000)
      serial="" ;;
  esac
  if [[ -z "$serial" && -r /etc/machine-id ]]; then
    # machine-id lisansta da kullaniliyor; her kurulumda benzersiz.
    serial="$(cut -c1-8 /etc/machine-id 2>/dev/null || true)"
  fi
  # DNS-guvenli hale getir: kucuk harf, sadece harf/rakam/tire.
  serial="$(printf '%s' "$serial" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')"
  if [[ -z "$serial" ]]; then
    TS_NAME_SOURCE="yok"
    TS_HOSTNAME="$TS_PREFIX"          # hicbiri yoksa duz onek
  else
    # Tailscale adi 63 karakteri gecmemeli; seri no'yu kirp.
    TS_HOSTNAME="${TS_PREFIX}-${serial:0:24}"
  fi
}

# Operator elle verdiyse ona saygi duy; yoksa saha kimliginden/donanimdan turet.
TS_HOSTNAME=""
TS_NAME_SOURCE=""
if [[ -n "${E1_TAILSCALE_HOSTNAME:-}" ]]; then
  TS_HOSTNAME="$E1_TAILSCALE_HOSTNAME"
  TS_NAME_SOURCE="elle"
else
  _derive_ts_hostname
fi

# --- SSH ve etiket dogrulama yardimcilari -----------------------------------
# NEDEN GEREKLI: `tailscale up --ssh` yalnizca KATILIM aninda uygulanir.
# Daha once katilmis bir cihaz (or. bu bayrak eklenmeden once kurulanlar, ya
# da etiketsiz geri donus yolundan katilanlar) asagidaki idempotent erken
# cikisa takiliyor ve SSH'i HIC acilmiyordu — "tailscale ile ssh
# baglanamiyorum"un sebebi tam olarak bu. Artik durumu okuyup gerekiyorsa
# `tailscale set` ile aciyoruz; bu yeniden giris (authkey) GEREKTIRMEZ.

# `tailscale debug prefs` JSON'unda RunSSH alani var mi? (eski surumlerde yok)
_prefs_has_runssh() {
  tailscale debug prefs 2>/dev/null | grep -q '"RunSSH"'
}

_ssh_is_on() {
  tailscale debug prefs 2>/dev/null | grep -qE '"RunSSH"[[:space:]]*:[[:space:]]*true'
}

_ensure_ssh() {
  if [[ "$ENABLE_SSH" != "1" ]]; then
    # Acikca kapatilmis. Aciksa kapat, degilse dokunma.
    if _ssh_is_on; then
      e1_info "Tailscale SSH kapatiliyor (E1_TAILSCALE_SSH=0)..."
      tailscale set --ssh=false >/dev/null 2>&1 \
        || e1_warn "SSH kapatilamadi. Elle: sudo tailscale set --ssh=false"
    fi
    return 0
  fi

  if ! _prefs_has_runssh; then
    # Durum okunamiyor (eski tailscale). Yine de uygula — `set` idempotent.
    if tailscale set --ssh >/dev/null 2>&1; then
      e1_ok "Tailscale SSH ayari uygulandi (surum eski, durum dogrulanamadi)."
    else
      e1_warn "Tailscale SSH acilamadi. Elle: sudo tailscale set --ssh"
    fi
    return 0
  fi

  if _ssh_is_on; then
    e1_ok "Tailscale SSH acik."
    return 0
  fi

  e1_info "Tailscale SSH kapali — aciliyor..."
  if tailscale set --ssh >/dev/null 2>&1 && _ssh_is_on; then
    e1_ok "Tailscale SSH acildi."
  else
    e1_warn "Tailscale SSH acilamadi. Elle: sudo tailscale set --ssh"
  fi
}

# Cihaz etiketsiz katildiysa ACL'deki `dst: tag:e1-appliance` kurallari
# ESLESMEZ; SSH acik olsa bile baglanti REDDEDILIR. Sessiz kalinirsa
# "SSH acik" mesaji yaniltici olur.
_warn_if_untagged() {
  [[ -n "$TAGS" ]] || return 0
  local prefs advertised
  prefs="$(tailscale debug prefs 2>/dev/null | tr -d ' \n')"
  # Alan HIC yoksa (eski tailscale) durum BILINMIYOR demektir; "etiketsiz"
  # diye uyarmak yanlis olur. Sadece alan var ve BOS ise uyariyoruz.
  printf '%s' "$prefs" | grep -q '"AdvertiseTags"' || return 0
  advertised="$(printf '%s' "$prefs" | sed -n 's/.*"AdvertiseTags":\[\([^]]*\)\].*/\1/p')"
  if [[ -z "$advertised" ]]; then
    e1_warn "Cihaz ETIKETSIZ katilmis (beklenen: ${TAGS})."
    e1_warn "ACL'de 'dst: ${TAGS}' yazan SSH kurallari bu cihaza UYMAZ; baglanti reddedilir."
    e1_warn "Duzeltmek icin anahtari '${TAGS}' etiketiyle uretip:"
    e1_warn "  sudo tailscale up --advertise-tags=${TAGS} --ssh --reset"
  fi
}

if [[ "$(id -u)" -ne 0 ]]; then
  e1_warn "Tailscale kurulumu root gerektirir — atlandi."
  exit 0
fi

# Tailscale hic kurulu degil ve anahtar da yok -> yapacak bir sey yok.
if ! command -v tailscale >/dev/null 2>&1 && [[ -z "$AUTHKEY" ]]; then
  e1_info "Tailscale anahtari tanimli degil (E1_TAILSCALE_AUTHKEY) — uzaktan bakim VPN'i atlandi."
  exit 0
fi

e1_step "Uzaktan bakim VPN'i (Tailscale)"

# --- 1) Kurulum -------------------------------------------------------------
if command -v tailscale >/dev/null 2>&1; then
  e1_ok "tailscale zaten kurulu ($(tailscale version 2>/dev/null | head -1))."
else
  e1_info "tailscale kuruluyor..."
  # Resmi kurulum script'i; dagitim/surum tespitini kendisi yapar.
  if ! curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1; then
    e1_warn "Tailscale kurulamadi (internet yok olabilir) — uzaktan bakim VPN'i atlandi."
    exit 0
  fi
  e1_ok "tailscale kuruldu."
fi

systemctl enable --now tailscaled >/dev/null 2>&1 || {
  e1_warn "tailscaled baslatilamadi — VPN atlandi."
  exit 0
}

# --- 2) Zaten bagli mi? (idempotent) ---------------------------------------
# `tailscale status --json` -> BackendState: Running | NeedsLogin | Stopped ...
CURRENT_STATE="$(tailscale status --json 2>/dev/null | sed -n 's/.*"BackendState"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
if [[ "$CURRENT_STATE" == "Running" ]]; then
  TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  e1_ok "Cihaz zaten tailnet'te${TS_IP:+ (${TS_IP})} — yeniden giris yapilmadi."
  # Yeniden giris YOK ama SSH/etiket durumu her calistirmada DOGRULANIR:
  # anahtar gerekmez, `update.sh` bu sayede eski cihazlarda SSH'i acar.
  _ensure_ssh
  _warn_if_untagged
  exit 0
fi

# Buradan sonrasi tailnet'e KATILMA yolu; anahtar sart.
if [[ -z "$AUTHKEY" ]]; then
  e1_info "Cihaz tailnet'te degil ve anahtar tanimli degil (E1_TAILSCALE_AUTHKEY) — atlandi."
  exit 0
fi

# --- 3) Tailnet'e katil -----------------------------------------------------
UP_ARGS=(
  --authkey="${AUTHKEY}"
  --hostname="${TS_HOSTNAME}"
  # Saha cihazi kalici olmali: yeniden baslatinca ayni dugum olarak donsun.
  --reset
)
[[ -n "$TAGS" ]] && UP_ARGS+=( --advertise-tags="${TAGS}" )
if [[ "$ENABLE_SSH" == "1" ]]; then
  # Tailscale SSH: erisim tailnet ACL'i ile kontrol edilir, ayri anahtar
  # dagitmaya gerek kalmaz. Kapatmak icin E1_TAILSCALE_SSH=0.
  UP_ARGS+=( --ssh )
fi
if [[ "$ACCEPT_DNS" == "1" ]]; then
  UP_ARGS+=( --accept-dns=true )
else
  # Varsayilan KAPALI: cihazin yerel DNS'i (AP dnsmasq, saha DNS'i) bozulmasin.
  UP_ARGS+=( --accept-dns=false )
fi

case "$TS_NAME_SOURCE" in
  saha)
    e1_info "Tailnet adi saha kimliginden uretildi: ${TS_HOSTNAME}"
    e1_hint "Degistirmek icin: sudo E1_SITE_FORCE=1 bash infra/appliance/setup-site-identity.sh"
    ;;
  donanim)
    e1_info "Tailnet adi donanimdan turetildi: ${TS_HOSTNAME}"
    e1_hint "Saha adi tanimlamak daha okunakli: sudo bash infra/appliance/setup-site-identity.sh"
    ;;
  yok)
    # Bu cihaz konsolda 'e1-grid' olarak gorunur; ikinci cihaz gelince
    # Tailscale numaralandirmaya baslar. Ciddi bir uyari, ipucu degil.
    e1_warn "Cihaza ozel ad uretilemedi — tailnet'te '${TS_HOSTNAME}' olarak gorunecek."
    e1_warn "Ikinci bir cihaz katilirsa isimler karisir. Duzeltmek icin:"
    e1_warn "  sudo bash infra/appliance/setup-site-identity.sh"
    ;;
esac
e1_info "Tailnet'e katiliniyor (hostname: ${TS_HOSTNAME}, etiket: ${TAGS:-yok})..."
# NOT: authkey komut satirinda; `ps` ile gorulebilir. Kisa sureli ve cihaz
# zaten operatorun kontrolunde. Log'a DUSURMUYORUZ (asagida maskeli mesaj).
TS_ERR="$(tailscale up "${UP_ARGS[@]}" 2>&1)" && TS_OK=1 || TS_OK=0

# ETIKET UYUMSUZLUGU icin tek seferlik geri donus:
# Anahtar Tailscale konsolunda ETIKETSIZ uretilmisse `--advertise-tags`
# "requested tags are invalid or not permitted" ile reddedilir. Bu cok sik
# yapilan bir hata; kurulumu bosa dusurmek yerine etiketsiz tekrar deniyoruz.
# (Etiketsiz katilan cihaz anahtari ureten KULLANICIYA baglanir ve 6 ayda
#  bir anahtar yenileme ister — bu yuzden uyari veriyoruz.)
if [[ $TS_OK -eq 0 && -n "$TAGS" ]] && printf '%s' "$TS_ERR" | grep -qiE 'tag|not permitted|invalid'; then
  e1_warn "Anahtar '${TAGS}' etiketini tasimiyor — etiketsiz deneniyor."
  UP_RETRY=()
  for a in "${UP_ARGS[@]}"; do [[ "$a" == --advertise-tags=* ]] || UP_RETRY+=( "$a" ); done
  TS_ERR="$(tailscale up "${UP_RETRY[@]}" 2>&1)" && TS_OK=1 || TS_OK=0
  if [[ $TS_OK -eq 1 ]]; then
    e1_warn "Cihaz ETIKETSIZ katildi. Onerilen: anahtari '${TAGS}' etiketiyle"
    e1_warn "yeniden uretip 'sudo tailscale up --advertise-tags=${TAGS}' calistirin."
    e1_warn "Etiketsiz cihazlar periyodik anahtar yenilemesi ister (bkz. docs/TAILSCALE.md)."
  fi
fi

if [[ $TS_OK -eq 0 ]]; then
  e1_warn "Tailnet'e katilamadi: ${TS_ERR:0:200}"
  e1_warn "Kontrol: sudo tailscale up --authkey=<anahtar> --advertise-tags=${TAGS}"
  exit 0
fi

TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
e1_ok "Tailnet'e katildi${TS_IP:+ — ${TS_IP}}"
# SSH durumu VARSAYILMAZ, okunur: `up --ssh` sessizce dusmus olabilir.
# Etiketsiz geri donus yolundan katildiysak da burada uyari cikar.
_ensure_ssh
_warn_if_untagged
e1_info "Cihaz Tailscale yonetim konsolunda '${TS_HOSTNAME}' adiyla gorunur."
if [[ "$ENABLE_SSH" == "1" ]]; then
  e1_hint "Baglanmak icin: ssh root@${TS_HOSTNAME}"
  e1_hint "Baglanti reddedilirse tailnet ACL'inde 'ssh' blogu eksiktir (bkz. docs/TAILSCALE.md)."
fi
exit 0
