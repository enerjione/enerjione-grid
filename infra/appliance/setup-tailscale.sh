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
#   E1_TAILSCALE_REJOIN     1 = dugumu SIL ve kalici olarak yeniden kat.
#                           Zaten ephemeral katilmis bir cihazi duzeltmenin TEK
#                           yolu (bkz. asagida _rejoin_hazirla). Tailnet IP'si
#                           degisir; islem sirasinda cihaza uzaktan ULASILAMAZ.
#                           Bu yuzden ASLA otomatik degil, elle istenir.
#   E1_TAILSCALE_ASSUME_PERSISTENT
#                           1 = "bu dugumun kalici oldugunu konsoldan
#                           dogruladim" beyani. Yeniden katilim YAPILMAZ,
#                           yalnizca kalicilik uyarisi susturulur.
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
    # DIKKAT — BU SATIR ESKIDEN "cihaz tailnet'te kalici" DIYORDU VE YANLISTI.
    # Anahtar suresinin uygulanmamasi, dugumun ephemeral OLMADIGI anlamina
    # GELMEZ: ephemeral dugumlerde de anahtar suresi yoktur. Onlar sureden
    # degil, CEVRIMDISI KALMAKTAN silinir. Yani ephemeral bir cihaz burada
    # "kalici" onayi aliyor, sonra elektrik kesintisinde tailnet'ten
    # kayboluyordu. Kalicilik ayrica raporlanir: _report_kalicilik.
    e1_ok "Anahtar suresi uygulanmiyor — cihaz sure dolmasi nedeniyle dusmez."
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

# --- KALICILIK (ephemeral) MAKBUZU -----------------------------------------
# NEDEN OLCMEK YERINE KAYIT TUTUYORUZ: cihazda "bu dugum ephemeral mi" diye
# sorulabilecek bir yer YOK. `tailscale status --json`, `tailscale debug prefs`
# ve netmap ciktisinin hicbiri bu bayragi tasimiyor; ephemeral olmak kontrol
# duzleminin (Tailscale sunucusunun) tuttugu bir ozelliktir ve istemciye
# bildirilmez. Bu yuzden olcemedigimiz seyi KAYIT ALTINA ALIYORUZ: katilim
# aninda anahtarin kalici olmasini GARANTI EDEBILDIK MI?
#
# Makbuz YOKSA cihaz bu ozellik eklenmeden once katilmistir; kalicilik
# BILINMIYOR demektir. Sahadaki mevcut cihazlarin tamami bu durumdadir.
JOIN_RECEIPT="${E1_TAILSCALE_RECEIPT:-/var/lib/e1-grid/tailscale-join.json}"

_node_id() {
  local json
  json="$(tailscale status --json 2>/dev/null)" || return 1
  [[ -n "$json" ]] || return 1
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print((d.get("Self") or {}).get("ID") or "")' 2>/dev/null
  else
    # Self, ciktida ExitNodeStatus/Peer bloklarindan ONCE gelir; dolayisiyla
    # ilk "ID" alani Self'in kimligidir.
    printf '%s' "$json" | sed -n 's/.*"ID"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
  fi
}

# Makbuz her alani AYRI SATIRA yazar; okumasi tek satirlik sed ile guvenli.
_receipt_get() {  # $1 = alan adi
  [[ -f "$JOIN_RECEIPT" ]] || return 1
  sed -n "s/^[[:space:]]*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",]*\)\"\{0,1\},\{0,1\}[[:space:]]*\$/\1/p" \
    "$JOIN_RECEIPT" | head -1
}

_receipt_write() {  # $1 = kalici_garanti (true/false)   $2 = kaynak
  local nid ts
  nid="$(_node_id 2>/dev/null || true)"
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  mkdir -p "$(dirname "$JOIN_RECEIPT")" 2>/dev/null || return 0
  cat > "$JOIN_RECEIPT" <<EOF
{
  "surum": 1,
  "kalici_garanti": $1,
  "kaynak": "$2",
  "dugum_kimligi": "${nid}",
  "etiketler": "${TAGS}",
  "katilim_utc": "${ts}"
}
EOF
  # Sir icermez (anahtar YAZILMAZ); teshis amacli okunabilir birakiliyor.
  chmod 644 "$JOIN_RECEIPT" 2>/dev/null || true
}

# "kalici" | "bilinmiyor"
_kalicilik() {
  local garanti rid nid
  garanti="$(_receipt_get kalici_garanti || true)"
  [[ "$garanti" == "true" ]] || { printf 'bilinmiyor'; return 0; }
  rid="$(_receipt_get dugum_kimligi || true)"
  nid="$(_node_id 2>/dev/null || true)"
  # Makbuz BASKA bir dugume aitse (cihaz konsoldan silinip elle yeniden
  # katilmis olabilir) tasidigi guvence gecersizdir.
  if [[ -n "$rid" && -n "$nid" && "$rid" != "$nid" ]]; then
    printf 'bilinmiyor'; return 0
  fi
  printf 'kalici'
}

_report_kalicilik() {
  local kaynak
  if [[ "$(_kalicilik)" == "kalici" ]]; then
    e1_ok "Dugum KALICI katilmis — cevrimdisi kalsa da tailnet'ten silinmez."
    return 0
  fi
  kaynak="$(_receipt_get kaynak || true)"
  case "$kaynak" in
    istege-bagli-ephemeral)
      e1_warn "Dugum BILEREK ephemeral katildi (E1_TAILSCALE_EPHEMERAL=1)."
      e1_warn "Cevrimdisi kalinca tailnet'ten silinir; saha cihazinda kullanmayin."
      return 0
      ;;
    duz-auth-key)
      # Duz auth key'de ephemeral/kalici karari anahtar URETILIRKEN konsolda
      # verilir; sonuna '?...' eklenirse anahtarin KENDISI bozulur. Yani
      # cihazdan zorlanamaz, yalnizca hatirlatabiliriz.
      e1_info "Anahtar turu duz auth key — kalicilik konsoldaki ANAHTAR ayarina bagli."
      e1_hint "Anahtari uretirken 'Ephemeral' secenegi ISARETSIZ olmali."
      e1_hint "Konsoldan dogruladiysaniz bu notu susturmak icin:"
      e1_hint "  sudo E1_TAILSCALE_ASSUME_PERSISTENT=1 bash ${SCRIPT_DIR}/setup-tailscale.sh"
      return 0
      ;;
  esac
  # Makbuz yok (ya da baska bir dugume ait): cihaz bu kontrol eklenmeden once
  # katilmis demektir. Sahadaki mevcut cihazlarin tamami buraya duser.
  e1_warn "Dugumun KALICI oldugu dogrulanamadi — ephemeral olabilir."
  e1_warn "Ephemeral dugum cevrimdisi kalinca 30-60 dakika icinde tailnet'ten"
  e1_warn "SILINIR: elektrik kesintisinden sonra cihaz listede hic gorunmez."
  e1_warn "Konsolda cihazin yaninda 'Ephemeral' rozeti var mi kontrol edin."
  e1_warn ""
  e1_warn "Kalici hale getirmek (dugum silinir, ayni adla yeniden katilir):"
  e1_warn "  sudo E1_TAILSCALE_REJOIN=1 bash ${SCRIPT_DIR}/setup-tailscale.sh"
  e1_warn "Konsoldan kalici oldugunu DOGRULADIYSANIZ uyariyi susturmak icin:"
  e1_warn "  sudo E1_TAILSCALE_ASSUME_PERSISTENT=1 bash ${SCRIPT_DIR}/setup-tailscale.sh"
  return 0
}

# --- Ephemeral tamiri -------------------------------------------------------
# Ephemeral bir dugumu kalici yapmanin TEK yolu dugumu SILIP yeniden katilmak.
# `--force-reauth` ISE YARAMAZ: Tailscale yeniden kimlik dogrulamada ephemeral
# bayragini temizlemiyor (tailscale/tailscale#15198, Mart 2025'ten beri acik).
# `tailscale logout` ise ephemeral dugumu ANINDA siler; ardindan
# `?ephemeral=false` ile yapilan taze katilim KALICI bir dugum yaratir.
#
# BEDELI: tailnet IP'si degisir ve katilim tamamlanana kadar cihaza uzaktan
# ULASILAMAZ. Bu yuzden asla otomatik kosmaz; yalnizca E1_TAILSCALE_REJOIN=1.
_ssh_client_ip() {
  local ip pid=$$ hops=0 ppid
  ip="${SSH_CONNECTION%% *}"
  if [[ -n "$ip" ]]; then printf '%s' "$ip"; return 0; fi
  # `sudo` env_reset ile SSH_CONNECTION'i dusurebilir; surec agacinda yukari
  # yuruyup /proc/<pid>/environ icinden okuyoruz (root bunu okuyabilir).
  while [[ $hops -lt 12 && -r "/proc/${pid}/status" ]]; do
    if [[ -r "/proc/${pid}/environ" ]]; then
      ip="$(tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null \
            | sed -n 's/^SSH_CONNECTION=//p' | head -1)"
      ip="${ip%% *}"
      if [[ -n "$ip" ]]; then printf '%s' "$ip"; return 0; fi
    fi
    ppid="$(awk '/^PPid:/{print $2}' "/proc/${pid}/status" 2>/dev/null || true)"
    [[ -n "$ppid" && "$ppid" != "0" && "$ppid" != "$pid" ]] || break
    pid="$ppid"
    hops=$((hops + 1))
  done
  return 1
}

_is_tailnet_ip() {  # 100.64.0.0/10 -> 100.64.x.x .. 100.127.x.x
  local o2
  [[ "$1" =~ ^100\.([0-9]{1,3})\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || return 1
  o2="${BASH_REMATCH[1]}"
  (( o2 >= 64 && o2 <= 127 ))
}

# Basarili olursa `logout` yapilmistir ve cagiran taraf KATILIM yoluna dusmeli.
_rejoin_hazirla() {
  local cip
  if [[ -z "$AUTHKEY" ]]; then
    e1_warn "E1_TAILSCALE_REJOIN=1 verildi ama anahtar tanimli degil."
    e1_warn "Anahtarsiz cikis cihazi tailnet'ten KALICI olarak dusururdu — yapilmadi."
    e1_warn "Anahtari verip tekrar deneyin:"
    e1_warn "  sudo E1_TAILSCALE_REJOIN=1 E1_TAILSCALE_AUTHKEY=<anahtar> \\"
    e1_warn "       bash ${SCRIPT_DIR}/setup-tailscale.sh"
    return 1
  fi
  cip="$(_ssh_client_ip || true)"
  if [[ -n "$cip" ]] && _is_tailnet_ip "$cip"; then
    e1_warn "Bu oturum tailnet uzerinden acilmis (${cip})."
    e1_warn "Yeniden katilim once baglantiyi keser, sonra tailnet IP'si degisir;"
    e1_warn "SSH oturumunuz kopar ve cihaz yarim durumda kalir — yapilmadi."
    e1_warn "Yerel konsoldan ya da yerel agdaki IP uzerinden calistirin."
    return 1
  fi
  e1_info "Dugum tailnet'ten cikariliyor ve kalici olarak yeniden katiliyor..."
  if ! tailscale logout >/dev/null 2>&1; then
    e1_warn "tailscale logout basarisiz — yeniden katilim denenmedi."
    e1_warn "Cihaz eski haliyle tailnet'te kaldi."
    return 1
  fi
  return 0
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
REJOIN_YAPILDI=0
if _node_is_registered; then
  # Operator "konsoldan dogruladim, bu dugum kalici" diyorsa olcemedigimiz
  # seyi onun beyanina dayanarak kaydediyoruz. Yeniden katilim YAPILMAZ.
  if [[ "${E1_TAILSCALE_ASSUME_PERSISTENT:-0}" == "1" ]]; then
    _receipt_write true "elle-onay"
    e1_ok "Kalicilik elle onaylandi — bundan sonra uyari verilmez."
  fi

  if [[ "${E1_TAILSCALE_REJOIN:-0}" == "1" ]] && _rejoin_hazirla; then
    # `logout` yapildi: dugum artik KAYITLI DEGIL. Asagidaki katilim yoluna
    # dusuyoruz; boylece `tailscale up` mantigi tek yerde kalir.
    REJOIN_YAPILDI=1
  else
    TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    e1_ok "Cihaz zaten tailnet'e kayitli${TS_IP:+ (${TS_IP})} — yeniden giris yapilmadi."
    _warn_if_untagged
    _report_key_expiry
    _report_kalicilik
    # Sahadaki mevcut cihazlar uzaktan bakim izni ajanini ILK GUNCELLEMEDE
    # burada alir. Erisim durumuna DOKUNMUYORUZ; karar ajanindir.
    _install_remote_access 0
    exit 0
  fi
fi

# Buradan sonrasi tailnet'e KATILMA yolu; anahtar sart.
if [[ -z "$AUTHKEY" ]]; then
  e1_info "Cihaz tailnet'te degil ve anahtar tanimli degil (E1_TAILSCALE_AUTHKEY) — atlandi."
  exit 0
fi

# --- 3) Tailnet'e katil -----------------------------------------------------
# Kalicilik guvencesini ANAHTARIN KENDISINDEN turetiyoruz; tahmin yok.
EFFECTIVE_KEY="$(_authkey_kalici "${AUTHKEY}")"
if [[ "${E1_TAILSCALE_EPHEMERAL:-0}" == "1" ]]; then
  KALICI_GARANTI=false; KALICI_KAYNAK="istege-bagli-ephemeral"
elif [[ "$EFFECTIVE_KEY" == *"ephemeral=false"* ]]; then
  # OAuth secret'ina parametreyi biz ekledik (ya da operator elle eklemis).
  KALICI_GARANTI=true;  KALICI_KAYNAK="oauth-ephemeral-false"
else
  # Duz auth key: ephemeral karari konsolda, anahtar uretilirken verilmis.
  KALICI_GARANTI=false; KALICI_KAYNAK="duz-auth-key"
fi

UP_ARGS=(
  # ?ephemeral=false — dugum cevrimdisi kalinca tailnet'ten silinmesin.
  --authkey="${EFFECTIVE_KEY}"
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
# Makbuz KATILIMDAN SONRA yazilir: dugum kimligi ancak simdi okunabilir.
# (Etiketsiz geri donus yolu ayni anahtari kullanir; guvence degismez.)
_receipt_write "$KALICI_GARANTI" "$KALICI_KAYNAK"
# Etiketsiz geri donus yolundan katildiysak burada uyari cikar.
_warn_if_untagged
_report_key_expiry
_report_kalicilik
e1_info "Cihaz Tailscale yonetim konsolunda '${TS_HOSTNAME}' adiyla gorunur."
e1_warn "UZAKTAN ERISIM VARSAYILAN KAPALI (--shields-up ile katildi)."
e1_hint "Musterinin yetkili kullanicisi (engineer rolu) arayuzden sureli izin verir:"
e1_hint "  Muhendislik > Sistem > Uzaktan Bakim  (1 saat / 8 saat / 24 saat)"
e1_hint "Izin acikken baglanmak icin: ssh root@${TS_HOSTNAME}"
e1_hint "Baglanti reddedilirse ya izin kapalidir ya da tailnet ACL'inde 'ssh' blogu eksiktir"
e1_hint "(bkz. docs/TAILSCALE.md). Cihazda durum: sudo ${SCRIPT_DIR}/e1-rad.py report"
if [[ "$REJOIN_YAPILDI" == "1" ]]; then
  e1_ok "Dugum KALICI olarak yeniden katildi — tailnet IP'si degismis olabilir."
  # Yeniden katilim bir BAKIM islemidir; kurulum mahsubu YAZILMAZ. Aksi halde
  # musterinin kapali tuttugu kapi, bakim yapildi diye 60 dakika acilirdi.
  _install_remote_access 0
  e1_hint "Erisimi test etmek icin sureli izin verin:"
  e1_hint "  Muhendislik > Sistem > Uzaktan Bakim"
else
  # Taze katilim: kurulumcu ACL/SSH testini bitirebilsin diye kisa sureli
  # kurulum mahsubu yazilir (E1_RAD_GRACE_MIN, varsayilan 60 dk; 0 = yok).
  _install_remote_access 1
fi
exit 0
