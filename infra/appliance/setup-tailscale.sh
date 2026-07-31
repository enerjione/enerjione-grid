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
# DAVRANIS DEGISIKLIGI (v2.25+): UZAKTAN ERISIM ARTIK VARSAYILAN KAPALI.
#   Cihaz tailnet'e KAYITLI katilir ama `--shields-up` ile katilir: tum GELEN
#   baglantilar reddedilir. Musterinin yetkili kullanicisi (engineer rolu)
#   arayuzden SURELI izin verir (Muhendislik > Sistem > Uzaktan Bakim); sure
#   dolunca erisim kendiliginden kapanir. Zorlayici host ajani `e1-rad`dir
#   (bkz. infra/appliance/e1-rad.py, setup-remote-access.sh).
#   Bu script artik erisimi ACMAZ — yalnizca cihazi tailnet'e katar ve ajani
#   kurar. Aksi halde her `update.sh` musterinin kapattigi kapiyi geri acardi.
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
#   E1_TAILSCALE_SSH        ANLAMI DEGISTI: artik "SSH surekli acik" degil,
#                           "izin VERILDIGINDE Tailscale SSH de acilsin mi"
#                           (default 1). 0 ise izin acikken de SSH kapali
#                           kalir; yalnizca tailnet IP'sine dogrudan baglanti.
#   E1_TAILSCALE_ACCEPT_DNS 1 = tailnet DNS'ini kabul et (default 0)
#                           0 onerilir: saha cihazinin yerel DNS'i bozulmasin.
#   E1_TAILSCALE_EPHEMERAL  1 = dugum ephemeral katilsin (default 0). Ephemeral
#                           dugum cevrimdisi kalinca tailnet'ten SILINIR; saha
#                           cihazi icin ISTENMEZ. Yalnizca gecici/test
#                           kurulumlari icin acin.
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

# --- Anahtar secenekleri: KALICI dugum ------------------------------------
# SAHA VAKASI — cihaz konsolda "Ephemeral" rozetiyle gorunuyordu.
# Ephemeral dugum, bir sure CEVRIMDISI kalinca tailnet'ten OTOMATIK SILINIR.
# Saha cihazi icin tam ters davranis: elektrik kesilir, cihaz birkac saat
# kapali kalir, geri acildiginda tailnet'te YOKTUR. Uzaktan bakim kalici
# olarak kopar; duzeltmek icin yerinde yeniden kurulum gerekir.
#
# SEBEP: OAuth client secret'i (tskey-client-...) dogrudan --authkey olarak
# verildiginde Tailscale'in URETTIGI anahtar VARSAYILAN OLARAK ephemeral.
# Biz hic istemedik; varsayilan boyle geliyor. OAuth secret'ina sorgu
# parametresi eklenebiliyor, uretilen anahtarin ozellikleri boyle secilir.
#
# Bu YALNIZCA OAuth secret'i icin gecerli: tskey-auth-... anahtarlarinin
# ephemeral/preauthorized ayari konsolda anahtar uretilirken belirlenir,
# sonuna '?...' eklenirse anahtarin KENDISI bozulur ve giris reddedilir.
_authkey_kalici() {
  local k="$1"
  [[ "$k" == tskey-client-* ]]  || { printf '%s' "$k"; return; }  # duz auth key
  [[ "$k" != *'?'* ]]           || { printf '%s' "$k"; return; }  # elle ayarlanmis
  [[ "${E1_TAILSCALE_EPHEMERAL:-0}" != "1" ]] || { printf '%s' "$k"; return; }
  printf '%s?ephemeral=false&preauthorized=true' "$k"
}

# --- Cihaza OZEL tailnet adi -----------------------------------------------
# SORUN: sistem hostname'i her cihazda ayni (`enerjione`) — cunku `enerjione.local`
# sahada standart erisim adresi ve site basina tek cihaz oldugu icin yerel
# agda cakisma yok. Ama tailnet TEK bir isim alanidir; ayni adla katilan
# cihazlari Tailscale `e1-grid-1`, `e1-grid-2`... diye numaralandirir ve
# hangisinin hangi saha oldugu ANLASILMAZ olur.
#
# COZUM: sistem hostname'ine DOKUNMUYORUZ (enerjione.local calismaya devam
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

# --- Kayitlilik / izin ajani yardimcilari -----------------------------------
# ESKI `_ensure_ssh` KALDIRILDI — BILINCLI:
#   Eski surum "SSH kapaliysa ac" yapiyordu ve bu fonksiyon her `update.sh`
#   kosusunda cagriliyordu. Uzaktan bakim izni eklendikten sonra bu davranis
#   musterinin kapattigi kapiyi HER GUNCELLEMEDE sessizce geri acardi; yani
#   ozelligin tamamini iptal ederdi. SSH artik yalnizca izin verildiginde
#   e1-rad tarafindan acilir (`tailscale set --ssh=true`) ve sure dolunca yine
#   e1-rad tarafindan kapatilir.

# Dugum tailnet'e KAYITLI mi? (`down`/`shields-up` kayitliligi BOZMAZ)
#
# NEDEN BackendState=="Running" YETMEZ: E1_RAD_LOCK_MODE=down modunda kapi
# kapaliyken BackendState "Stopped" olur. Eski idempotent erken cikis buna
# takilmiyordu; script asagi duser ve ortamda anahtar varsa
# `tailscale up --authkey --reset` calistirip KAPIYI GERI ACARDI. Kayitliligi
# `LoggedOut` uzerinden okuyoruz — `logout` calistirilmadikca false kalir.
_node_is_registered() {
  local prefs state
  prefs="$(tailscale debug prefs 2>/dev/null || true)"
  if printf '%s' "$prefs" | grep -q '"LoggedOut"'; then
    # `set -e` tuzagi: basarisiz grep'i kosul disinda birakma.
    if printf '%s' "$prefs" | grep -qE '"LoggedOut"[[:space:]]*:[[:space:]]*false'; then
      return 0
    fi
    return 1
  fi
  # Eski tailscale: prefs alani yok — BackendState'e dus. "Stopped" da
  # kayitli demektir (tunel inik ama dugum duruyor).
  state="$(tailscale status --json 2>/dev/null \
    | sed -n 's/.*"BackendState"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  [[ "$state" == "Running" || "$state" == "Stopped" ]]
}

# Uzaktan bakim izni ajanini kur/guncelle ve durumu bas. Ajan kurulu degilse
# erisim KAPANMAZ — bu yuzden her kosuda cagriliyor.
_install_remote_access() {
  if [[ ! -f "${SCRIPT_DIR}/setup-remote-access.sh" ]]; then
    e1_warn "setup-remote-access.sh bulunamadi (repo eski olabilir)."
    e1_warn "Uzaktan bakim izni ajani (e1-rad) kurulamadi; erisim durumu yonetilemez."
    return 0
  fi
  # E1_TAILSCALE_SSH ajana AKTARILIR: systemd unit'leri kabuk ortamini miras
  # ALMAZ, bu yuzden setup-remote-access.sh degeri kalici bir env dosyasina
  # yazar (/etc/enerjione-grid/e1-rad.env).
  E1_RAD_FRESH_JOIN="${1:-0}" E1_TAILSCALE_SSH="$ENABLE_SSH" \
    bash "${SCRIPT_DIR}/setup-remote-access.sh" \
    || e1_warn "Uzaktan bakim izni ajani kurulamadi; detay yukarida."
}

# Cihaz etiketsiz katildiysa ACL'deki `dst: tag:e1-appliance` kurallari
# ESLESMEZ; SSH acik olsa bile baglanti REDDEDILIR. Sessiz kalinirsa
# "SSH acik" mesaji yaniltici olur.
# Cihazin anahtari GERCEKTEN suresiz mi? Etikete bakip tahmin etmek yerine
# durumu OLCUYORUZ: tailscale 1.36+ `status --json` ciktisinda Self.KeyExpiry
# (zaman damgasi) ve Self.Expired (bool) alanlarini veriyor.
#   KeyExpiry yok/null -> sure uygulanmiyor, cihaz kalici (istedigimiz durum)
#   KeyExpiry dolu     -> cihaz O TARIHTE tailnet'ten duser
# Boylece "etiketli katildi ama tailnet politikasi yine de sure uyguluyor"
# gibi durumlar da yakalanir.
_report_key_expiry() {
  local json expiry
  json="$(tailscale status --json 2>/dev/null)" || return 0
  [[ -n "$json" ]] || return 0
  if command -v python3 >/dev/null 2>&1; then
    expiry="$(printf '%s' "$json" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
self_ = d.get("Self") or {}
if self_.get("Expired"):
    print("DOLMUS")
elif self_.get("KeyExpiry"):
    print(self_["KeyExpiry"])
' 2>/dev/null)"
  else
    # python3 yoksa kaba ayristirma; alan yoksa cikti bos kalir.
    expiry="$(printf '%s' "$json" | sed -n 's/.*"KeyExpiry":"\([^"]*\)".*/\1/p')"
  fi

  if [[ -z "$expiry" ]]; then
    e1_ok "Anahtar suresi uygulanmiyor — cihaz tailnet'te kalici."
  elif [[ "$expiry" == "DOLMUS" ]]; then
    e1_warn "Cihazin anahtari DOLMUS — uzaktan erisim su an calismiyor."
    e1_warn "Duzeltme: sudo tailscale up --advertise-tags=${TAGS} --ssh --force-reauth"
  else
    e1_warn "DIKKAT: cihazin anahtari ${expiry%%T*} tarihinde dolacak."
    e1_warn "O tarihte cihaz uzaktan erisimden DUSER. Kalici yapmak icin"
    e1_warn "Tailscale konsolunda cihazi acip 'Disable key expiry' isaretleyin"
    e1_warn "ya da cihazi '${TAGS}' etiketiyle yeniden katin."
  fi
}

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

# --- 2) Zaten kayitli mi? (idempotent) -------------------------------------
# DIKKAT: kosul "Running" DEGIL "kayitli". Kapi kapaliyken (ozellikle
# E1_RAD_LOCK_MODE=down) durum "Stopped" olur; "Running" arayan eski kosul
# buraya takilmaz, asagi duser ve `tailscale up --authkey --reset` ile
# musterinin kapattigi erisimi GERI ACARDI.
if _node_is_registered; then
  TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  e1_ok "Cihaz zaten tailnet'e kayitli${TS_IP:+ (${TS_IP})} — yeniden giris yapilmadi."
  _warn_if_untagged
  _report_key_expiry
  # Sahadaki mevcut cihazlar uzaktan bakim izni ajanini ILK GUNCELLEMEDE
  # burada alir. Erisim durumuna DOKUNMUYORUZ; karar ajanindir.
  _install_remote_access 0
  exit 0
fi

# Buradan sonrasi tailnet'e KATILMA yolu; anahtar sart.
if [[ -z "$AUTHKEY" ]]; then
  e1_info "Cihaz tailnet'te degil ve anahtar tanimli degil (E1_TAILSCALE_AUTHKEY) — atlandi."
  exit 0
fi

# --- 3) Tailnet'e katil -----------------------------------------------------
UP_ARGS=(
  # ?ephemeral=false — dugum cevrimdisi kalinca tailnet'ten silinmesin.
  --authkey="$(_authkey_kalici "${AUTHKEY}")"
  --hostname="${TS_HOSTNAME}"
  # Saha cihazi kalici olmali: yeniden baslatinca ayni dugum olarak donsun.
  --reset
  # VARSAYILAN KAPALI: dugum tailnet'e kayitli olur ama TUM GELEN baglantilar
  # reddedilir. Musterinin yetkili kullanicisi arayuzden sureli izin verince
  # e1-rad `--shields-up=false` yapar; sure dolunca geri kapatir.
  --shields-up
)
[[ -n "$TAGS" ]] && UP_ARGS+=( --advertise-tags="${TAGS}" )
# `--ssh` BILINCLI OLARAK YOK: Tailscale SSH artik katilim aninda degil,
# yalnizca izin verildiginde e1-rad tarafindan acilir (E1_TAILSCALE_SSH=1 ise).
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
  # SAHA VAKASI — "cihazlar bir sure sonra tailnet'ten siliniyor":
  # Bu geri donus yolu eskiden OTOMATIKTI. Etiketsiz katilan cihaz calisir
  # gorunur, kurulumcu ekrandaki uyariyi kaydirip gecer ve cihaz 180 GUN
  # SONRA sessizce tailnet'ten duser (Tailscale varsayilan anahtar suresi).
  # O anda cihaz sahada, uzaktan erisim yok, kimse neden oldugunu bilmiyor.
  #
  # Tailscale dokumantasyonu: "When tags are first applied, the tagged device
  # will have key expiry disabled by default." Yani KALICI baglanti icin
  # cihazin ETIKETLI katilmasi sart.
  #
  # Artik varsayilan olarak ETIKETSIZ KATILMIYORUZ. Kurulumcu ekranin
  # basindayken anahtari duzeltip tekrar denemesi, alti ay sonra cihazi
  # kaybetmekten iyidir. Bilerek etiketsiz istenirse:
  #     E1_TAILSCALE_ALLOW_UNTAGGED=1
  if [[ "${E1_TAILSCALE_ALLOW_UNTAGGED:-0}" != "1" ]]; then
    e1_warn "Anahtar '${TAGS}' etiketini tasimiyor — tailnet'e KATILINMADI."
    e1_warn "Etiketsiz katilan cihazin anahtari 180 gunde dolar ve cihaz"
    e1_warn "uzaktan erisimden DUSER. Bu yuzden otomatik olarak yapilmiyor."
    e1_warn ""
    e1_warn "Yapilacak: Tailscale konsolunda anahtari '${TAGS}' etiketiyle"
    e1_warn "yeniden uretin (Settings > Keys > Generate > Tags), sonra:"
    e1_warn "  sudo E1_TAILSCALE_AUTHKEY=<yeni-anahtar> bash infra/appliance/setup-tailscale.sh"
    e1_warn ""
    e1_warn "Yine de etiketsiz katmak icin: E1_TAILSCALE_ALLOW_UNTAGGED=1"
    exit 0
  fi
  e1_warn "E1_TAILSCALE_ALLOW_UNTAGGED=1 — etiketsiz deneniyor."
  UP_RETRY=()
  for a in "${UP_ARGS[@]}"; do [[ "$a" == --advertise-tags=* ]] || UP_RETRY+=( "$a" ); done
  TS_ERR="$(tailscale up "${UP_RETRY[@]}" 2>&1)" && TS_OK=1 || TS_OK=0
  if [[ $TS_OK -eq 1 ]]; then
    e1_warn "Cihaz ETIKETSIZ katildi — anahtari 180 gunde dolacak."
    e1_warn "Tailscale konsolunda cihazi acip 'Disable key expiry' isaretleyin,"
    e1_warn "yoksa cihaz alti ay sonra uzaktan erisimden duser."
  fi
fi

if [[ $TS_OK -eq 0 ]]; then
  e1_warn "Tailnet'e katilamadi: ${TS_ERR:0:200}"
  e1_warn "Kontrol: sudo tailscale up --authkey=<anahtar> --advertise-tags=${TAGS}"
  exit 0
fi

TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
e1_ok "Tailnet'e katildi${TS_IP:+ — ${TS_IP}}"
# Etiketsiz geri donus yolundan katildiysak burada uyari cikar.
_warn_if_untagged
_report_key_expiry
e1_info "Cihaz Tailscale yonetim konsolunda '${TS_HOSTNAME}' adiyla gorunur."
e1_warn "UZAKTAN ERISIM VARSAYILAN KAPALI (--shields-up ile katildi)."
e1_hint "Musterinin yetkili kullanicisi (engineer rolu) arayuzden sureli izin verir:"
e1_hint "  Muhendislik > Sistem > Uzaktan Bakim  (1 saat / 8 saat / 24 saat)"
e1_hint "Izin acikken baglanmak icin: ssh root@${TS_HOSTNAME}"
e1_hint "Baglanti reddedilirse ya izin kapalidir ya da tailnet ACL'inde 'ssh' blogu eksiktir"
e1_hint "(bkz. docs/TAILSCALE.md). Cihazda durum: sudo ${SCRIPT_DIR}/e1-rad.py report"
# Taze katilim: kurulumcu ACL/SSH testini bitirebilsin diye kisa sureli
# kurulum mahsubu yazilir (E1_RAD_GRACE_MIN, varsayilan 60 dk; 0 = yok).
_install_remote_access 1
exit 0
