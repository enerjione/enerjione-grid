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
#   E1_TAILSCALE_HOSTNAME   tailnet'te gorunecek ad (default: sistem hostname)
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
  e1_step() { printf '\n== %s\n' "$*"; }
fi

AUTHKEY="${E1_TAILSCALE_AUTHKEY:-}"
TAGS="${E1_TAILSCALE_TAGS:-tag:e1-appliance}"
TS_HOSTNAME="${E1_TAILSCALE_HOSTNAME:-$(hostnamectl --static 2>/dev/null || hostname)}"
ENABLE_SSH="${E1_TAILSCALE_SSH:-1}"
ACCEPT_DNS="${E1_TAILSCALE_ACCEPT_DNS:-0}"

# --- Anahtar yoksa sessizce cik: kurulumu ASLA bozma ------------------------
if [[ -z "$AUTHKEY" ]]; then
  e1_info "Tailscale anahtari tanimli degil (E1_TAILSCALE_AUTHKEY) — uzaktan bakim VPN'i atlandi."
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  e1_warn "Tailscale kurulumu root gerektirir — atlandi."
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
[[ "$ENABLE_SSH" == "1" ]] && e1_ok "Tailscale SSH acik (erisim tailnet ACL'i ile sinirli)."
e1_info "Cihaz Tailscale yonetim konsolunda '${TS_HOSTNAME}' adiyla gorunur."
exit 0
