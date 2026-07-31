#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Appliance (mini PC) modu kurulumu
# ===========================================================================
# Mini PC'yi "acinca calisan cihaz" haline getirir:
#
#   1. Hostname -> enerjione  (avahi ile http://enerjione.local calisir)
#      Eski ad e1-grid.local da yasatiliyor (AP DNS + mDNS takma adi) —
#      sahadaki kurulumlarin yer imleri ve notlari kirilmasin.
#   2. NetworkManager ag yoneticisi olur (netplan renderer dahil)
#   3. Sifresiz WiFi AP: musteri adiyla (or. "E1GRID-TPAO") — boot'ta acilir,
#      istemcilere 10.42.0.0/24 DHCP dagitir, enerjione.local -> 10.42.0.1
#   4. avahi-daemon (mDNS) — kablolu agdan da enerjione.local calisir
#   5. e1-netd ag ajani + systemd unit'leri (UI'dan IP/DNS ayari)
#
# Calistirma (repo kokunde, kurulum sonrasi):
#   sudo bash infra/appliance/setup-appliance.sh
#
# Idempotent: tekrar calistirmak guvenli.
#
# Env override:
#   AP_SSID        AP adi. Verilmezse MUSTERI ADINDAN turetilir:
#                  "TPAO" -> "E1GRID-TPAO". Musteri adi da yoksa
#                  eski ad korunur: "EnerjiOne Grid".
#   E1_CUSTOMER    musteri adi (kurulum araci gonderir; site.env'den
#                  de okunur)
#   AP_CHANNEL     2.4GHz kanal     (default: 6)
#   AP_IFNAME      WiFi arayuzu     (default: otomatik tespit)
#   APPLIANCE_HOSTNAME               (default: enerjione)
#   APPLIANCE_HOSTNAME_LEGACY        eski ad, takma ad olarak yayinlanir
#                                    (default: e1-grid)
#   BACKEND_UID    backend container uid (default: 10001)
#   SKIP_AP=1      AP kurulumunu atla (WiFi karti yoksa)
# ===========================================================================
set -euo pipefail

# --- AP adi -----------------------------------------------------------------
# Kurulum aracinda girilen MUSTERI ADINDAN turetilir: "TPAO" -> `E1GRID-TPAO`.
# Sebep: sahada birden fazla cihaz veya komsu bir kurulum varsa hangi AP'nin
# hangi musteriye ait oldugu WiFi listesinden anlasilsin.
#
# Musteri adi yoksa eski ad ("EnerjiOne Grid") KORUNUR — sahadaki mevcut
# cihazlarin SSID'i sebepsiz degismesin.
_ap_read_var() {  # $1=dosya $2=anahtar
  [[ -f "$1" ]] || return 1
  sed -n \
    -e "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\"\(.*\)\"[[:space:]]*\$/\1/p" \
    -e "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\([^\"#][^#]*\).*\$/\1/p" \
    "$1" | tail -1 | sed -e 's/[[:space:]]*$//'
}

# Turkce metin -> SSID'de guvenli parca; TAMAMI BUYUK harf.
# Turkce harfler once ASCII karsiligina cevrilir (bazi telefonlar ve eski
# cihazlar Turkce SSID'i bozuk gosterir), sonra tumu buyutulur.
# NOT: `tr a-z A-Z` ASCII kuralini uygular; 'i' -> 'I' (Turkce 'I' degil).
_ap_slug() {
  printf '%s' "$1" \
    | sed -e 's/Ç/C/g; s/ç/C/g; s/Ğ/G/g; s/ğ/G/g; s/İ/I/g; s/ı/I/g' \
          -e 's/Ö/O/g; s/ö/O/g; s/Ş/S/g; s/ş/S/g; s/Ü/U/g; s/ü/U/g' \
    | tr 'a-z' 'A-Z' \
    | tr -c 'A-Z0-9-' '-' \
    | sed -e 's/--*/-/g' -e 's/^-//' -e 's/-$//'
}

if [[ -z "${AP_SSID:-}" ]]; then
  _ap_customer="${E1_CUSTOMER:-}"
  [[ -z "$_ap_customer" ]] && _ap_customer="$(_ap_read_var "${E1_SITE_ENV:-/etc/enerjione-grid/site.env}" E1_CUSTOMER_NAME || true)"
  [[ -z "$_ap_customer" ]] && _ap_customer="$(_ap_read_var "${E1_INSTALL_ENV:-/etc/enerjione-grid/install.env}" E1_CUSTOMER || true)"
  if [[ -n "${_ap_customer// /}" ]]; then
    _ap_part="$(_ap_slug "$_ap_customer")"
    # SSID en fazla 32 BAYT olabilir; onek 7 karakter, musteriye 24 birakiyoruz.
    _ap_part="${_ap_part:0:24}"
    _ap_part="${_ap_part%-}"
  fi
  if [[ -n "${_ap_part:-}" ]]; then
    AP_SSID="E1GRID-${_ap_part}"
  else
    AP_SSID="EnerjiOne Grid"
  fi
fi
AP_CON_NAME="e1-grid-ap"
AP_CHANNEL="${AP_CHANNEL:-6}"
# Cihazin mDNS adi: http://enerjione.local
# ESKI AD (e1-grid) sahadaki kurulumlarda kullaniliyordu; asagida AP
# DNS kaydinda ALIAS olarak yasatiliyor ki eski yer imleri kirilmasin.
APPLIANCE_HOSTNAME="${APPLIANCE_HOSTNAME:-enerjione}"
APPLIANCE_HOSTNAME_LEGACY="${APPLIANCE_HOSTNAME_LEGACY:-e1-grid}"
BACKEND_UID="${BACKEND_UID:-10001}"
STATE_DIR="/var/lib/e1-grid/net"
INSTALL_DIR="${INSTALL_DIR:-/opt/enerjione-grid}"
AGENT_SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# AP'nin dagittigi alt ag — NetworkManager 'shared' modunun varsayilani.
AP_ADDRESS="10.42.0.1"

C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_RED=$'\033[0;31m'
C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
ok()   { echo "  ${C_GREEN}✓${C_RESET} $*"; }
info() { echo "  ${C_DIM}·${C_RESET} $*"; }
warn() { echo "  ${C_YELLOW}!${C_RESET} $*"; }
die()  { echo "  ${C_RED}✗ $*${C_RESET}" >&2; exit 1; }
step() { echo; echo "${C_BOLD}$*${C_RESET}"; }

[[ $EUID -eq 0 ]] || die "sudo ile calistirin: sudo bash $0"

echo
echo "${C_BOLD}EnerjiOne Grid — Appliance modu kurulumu${C_RESET}"
echo "${C_DIM}Hostname: ${APPLIANCE_HOSTNAME} · AP: ${AP_SSID} (sifresiz)${C_RESET}"

# ---------------------------------------------------------------------------
step "[1/7] Gerekli paketler"
NEEDED=()
command -v nmcli   >/dev/null 2>&1 || NEEDED+=(network-manager)
command -v avahi-daemon >/dev/null 2>&1 || NEEDED+=(avahi-daemon)
# avahi-publish: eski mDNS adini (e1-grid.local) takma ad olarak yayinlamak
# icin. Olmazsa kurulum bozulmaz, sadece eski ad calismaz.
command -v avahi-publish >/dev/null 2>&1 || NEEDED+=(avahi-utils)
command -v python3 >/dev/null 2>&1 || NEEDED+=(python3)
# AP modunda NetworkManager dnsmasq'i icsel kullanir (shared mode).
dpkg -s dnsmasq-base >/dev/null 2>&1 || NEEDED+=(dnsmasq-base)
# WiFi kart/regulasyon araclari
command -v iw >/dev/null 2>&1 || NEEDED+=(iw)

if [[ ${#NEEDED[@]} -gt 0 ]]; then
  info "Kuruluyor: ${NEEDED[*]}"
  # `apt-get update` cikis kodu OLUMCUL DEGIL: makinede bizimle ilgisi olmayan
  # bozuk bir ucuncu taraf deposu (Chrome, eski PPA...) olabilir ve Ubuntu 24.04
  # eski anahtar deposunu kullanmadigi icin bunlar "NO_PUBKEY / is not signed"
  # verip apt'i 100 ile dondurur. Sahada bu tum kurulumu oldurmustu.
  # Karar `install` adiminda veriliyor.
  _APT_LOG="$(mktemp)"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq >"$_APT_LOG" 2>&1 || true
  _APT_BROKEN="$(grep -E '^(Err|E|W): ' "$_APT_LOG" 2>/dev/null | head -8 || true)"
  rm -f "$_APT_LOG"
  if [[ -n "$_APT_BROKEN" ]]; then
    warn "apt-get update kismen basarisiz — su depolar atlandi:"
    printf '%s\n' "$_APT_BROKEN" | sed 's/^/        /' >&2
    info "Bu depolar EnerjiOne icin gerekli degil; devam ediliyor."
  fi
  if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEEDED[@]}"; then
    [[ -n "$_APT_BROKEN" ]] &&       warn "Yukaridaki bozuk depo(lar) sebep olabilir; devre disi birakip tekrar deneyin."
    die "Appliance paketleri kurulamadi: ${NEEDED[*]}"
  fi
  ok "Paketler kuruldu."
else
  ok "Tum paketler zaten kurulu."
fi

# ---------------------------------------------------------------------------
step "[2/7] Hostname + mDNS (${APPLIANCE_HOSTNAME}.local)"
CURRENT_HOST="$(hostnamectl --static || true)"
if [[ "$CURRENT_HOST" != "$APPLIANCE_HOSTNAME" ]]; then
  hostnamectl set-hostname "$APPLIANCE_HOSTNAME"
  # /etc/hosts'ta 127.0.1.1 satiri guncellenmezse sudo/DNS uyarilari cikar.
  if grep -qE '^127\.0\.1\.1' /etc/hosts; then
    sed -i "s|^127\.0\.1\.1.*|127.0.1.1\t${APPLIANCE_HOSTNAME}|" /etc/hosts
  else
    echo -e "127.0.1.1\t${APPLIANCE_HOSTNAME}" >> /etc/hosts
  fi
  ok "Hostname ayarlandi: ${APPLIANCE_HOSTNAME} (eskisi: ${CURRENT_HOST:-yok})"
else
  ok "Hostname zaten ${APPLIANCE_HOSTNAME}."
fi

# mDNS TUM arayuzlerden cevap vermeli: kullanici cihaza kabloyla da,
# cihazin kendi AP'sinden de, cihazin istemci olarak katildigi WiFi agindan
# da ayni adresle (enerjione.local) ulasabilmeli.
#
# Avahi'nin varsayilani zaten "tum arayuzler" ama BUNA GUVENMIYORUZ: OEM
# imajlari (Dell/Lenovo Ubuntu) avahi-daemon.conf'u kisitli gonderebiliyor
# ve `allow-interfaces=eth0` gibi bir satir tek bir arayuze kilitler. Boyle
# bir cihazda WiFi'den .local sessizce cozulmez. O yuzden kisitlayan
# satirlari ACIKCA etkisizlestiriyoruz.
AVAHI_CONF=/etc/avahi/avahi-daemon.conf
if [[ -f "$AVAHI_CONF" ]]; then
  if grep -qE '^\s*(allow|deny)-interfaces\s*=' "$AVAHI_CONF"; then
    cp -a "$AVAHI_CONF" "${AVAHI_CONF}.e1-yedek" 2>/dev/null || true
    sed -i -E 's/^(\s*)(allow|deny)-interfaces\s*=/\1#\2-interfaces=/' "$AVAHI_CONF"
    ok "avahi arayuz kisiti kaldirildi (tum aglardan .local cozulur)."
  fi
  # IPv4 kapaliysa Windows/Android istemciler cozemez.
  sed -i -E 's/^\s*use-ipv4\s*=.*/use-ipv4=yes/' "$AVAHI_CONF"
fi

systemctl enable --now avahi-daemon >/dev/null 2>&1 || warn "avahi-daemon baslatilamadi."
systemctl reload avahi-daemon >/dev/null 2>&1 || systemctl restart avahi-daemon >/dev/null 2>&1 || true
ok "mDNS aktif — kablolu, AP ve WiFi aglarinin hepsinden http://${APPLIANCE_HOSTNAME}.local"

# --- ESKI AD icin mDNS takma adi -------------------------------------------
# Cihazin adi e1-grid -> enerjione olarak degisti. Sahada e1-grid.local ile
# kurulmus cihazlar, kayitli yer imleri ve yazili kurulum notlari var; ad
# degisince hepsi bir anda calismaz olurdu.
#
# avahi yalnizca KENDI hostname'ini yayinlar; ikinci bir ad icin
# `avahi-publish -a` surekli calisan bir surec ister (kayit, surec yasadigi
# surece durur). Kucuk bir systemd unit'i ile cozuyoruz.
#
# avahi-utils kurulu degilse SESSIZCE geciyoruz: bu bir kolaylik, kurulumu
# bloke etmemeli. Yeni ad her durumda calisir.
if command -v avahi-publish >/dev/null 2>&1 \
   && [[ "$APPLIANCE_HOSTNAME_LEGACY" != "$APPLIANCE_HOSTNAME" ]]; then
  cat > /etc/systemd/system/e1-mdns-alias.service <<UNIT
[Unit]
Description=EnerjiOne Grid - eski mDNS adi (${APPLIANCE_HOSTNAME_LEGACY}.local)
After=avahi-daemon.service
Requires=avahi-daemon.service

[Service]
# -a: adres kaydi, -R: cakisma olursa yeniden dene. Surec yasadigi surece
# kayit durur; bu yuzden Restart=always.
ExecStart=/usr/bin/avahi-publish -a -R ${APPLIANCE_HOSTNAME_LEGACY}.local \$(hostname -I | awk '{print \$1}')
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now e1-mdns-alias.service >/dev/null 2>&1 \
    && ok "Eski ad da calisiyor: http://${APPLIANCE_HOSTNAME_LEGACY}.local" \
    || warn "Eski mDNS adi (${APPLIANCE_HOSTNAME_LEGACY}.local) etkinlestirilemedi."
else
  info "avahi-publish yok — yalnizca ${APPLIANCE_HOSTNAME}.local yayinlanacak."
fi

# ---------------------------------------------------------------------------
step "[3/7] NetworkManager ag yoneticisi yapiliyor"
# Ubuntu Server varsayilani netplan + systemd-networkd'dir; AP ve UI'dan IP
# ayari icin NetworkManager'a devrediyoruz. Mevcut netplan dosyalari
# yedeklenir ve renderer NetworkManager'a cevrilir.
if [[ -d /etc/netplan ]]; then
  NETPLAN_E1=/etc/netplan/99-e1-grid-nm.yaml
  if [[ ! -f "$NETPLAN_E1" ]]; then
    BACKUP_DIR="/var/backups/e1-grid-netplan-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    cp -a /etc/netplan/*.yaml "$BACKUP_DIR"/ 2>/dev/null || true
    info "Mevcut netplan yedegi: ${BACKUP_DIR}"

    # ---- Mevcut adresi NM'e TASI --------------------------------------
    # SAHA VAKASI: burasi eskiden dogrudan `netplan apply` cagiriyordu ve
    # kurulum bu satirda ASILI KALIYORDU. Iki sebep vardi:
    #
    #   1. `netplan apply` ag yonetimini systemd-networkd'den NM'e devreder;
    #      bu sirada SSH oturumunu tasiyan ethernet arayuzu asagi/yukari
    #      edilir. Oturum donar, cikti gelmez, kurulumcu sonsuza kadar bekler.
    #   2. Devir sirasinda MEVCUT ADRES HICBIR YERE TASINMIYORDU. Cihazin
    #      netplan'dan gelen STATIK IP'si varsa NM devralinca o adres
    #      kayboluyor (NM DHCP denemesine dusuyor) ve cihaz TAMAMEN
    #      erisilemez kaliyordu — sahaya tekrar gitmek demek.
    #
    # Cozum: devirden ONCE mevcut adresi ayni degerlerle bir NM profiline
    # yaziyoruz. Adres degismedigi icin TCP oturumu cogu durumda hayatta
    # kalir; kalmasa bile cihaz ayni IP'den geri gelir.
    ETH_IF="$(ip route show default 2>/dev/null | awk '{print $5; exit}')"
    if [[ -n "$ETH_IF" ]]; then
      ETH_CIDR="$(ip -4 -o addr show dev "$ETH_IF" scope global 2>/dev/null | awk '{print $4; exit}')"
      ETH_GW="$(ip route show default 2>/dev/null | awk '{print $3; exit}')"
      # "dynamic" bayragi = adres DHCP'den geldi. DHCP ise NM de DHCP yapar
      # ve ayni MAC ile buyuk olasilikla AYNI adresi geri alir; profil
      # yazmaya gerek yok. Statikse tasimak ZORUNDAYIZ.
      if ip -4 -o addr show dev "$ETH_IF" scope global 2>/dev/null | grep -q ' dynamic '; then
        info "${ETH_IF}: DHCP — NetworkManager ayni adresi yeniden alacak."
      elif [[ -n "$ETH_CIDR" ]]; then
        # Profil adi e1-netd ile AYNI (ETH_CON_NAME) — aksi halde ag ajani
        # kendi profilini ayrica olusturur ve iki profil yarisir.
        # DNS ayiklama iki tuzak barindiriyor:
        #   * `sed 's/.*://'` GREEDY: IPv6 DNS (fe80::1) varsa son iki
        #     noktaya kadar siler ve deger "1" gibi cop olur -> DNS bozulur.
        #     Bu yuzden yalnizca ILK iki nokta kesiliyor.
        #   * /etc/resolv.conf systemd-resolved makinelerde 127.0.0.53 (stub)
        #     gosterir; onu statik DNS diye yazmak anlamsiz. Loopback elenir.
        # ipv6.method ignore verdigimiz icin sadece IPv4 adresler alinir.
        ETH_DNS="$(resolvectl dns "$ETH_IF" 2>/dev/null | sed 's/^[^:]*: *//' \
          | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' \
          | grep -v '^127\.' | tr '\n' ' ' | sed 's/ $//')"
        if [[ -z "$ETH_DNS" ]]; then
          ETH_DNS="$(awk '/^nameserver/{print $2}' /etc/resolv.conf 2>/dev/null \
            | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | grep -v '^127\.' \
            | tr '\n' ' ' | sed 's/ $//')"
        fi
        if nmcli connection show e1-grid-eth >/dev/null 2>&1; then
          ok "e1-grid-eth profili zaten var."
        else
          nmcli connection add type ethernet ifname "$ETH_IF" con-name e1-grid-eth \
            autoconnect yes ipv4.method manual ipv4.addresses "$ETH_CIDR" \
            ${ETH_GW:+ipv4.gateway "$ETH_GW"} \
            ${ETH_DNS:+ipv4.dns "$ETH_DNS"} ipv6.method ignore >/dev/null 2>&1 \
            && ok "Statik adres NM'e tasindi: ${ETH_CIDR} (${ETH_IF})" \
            || warn "NM ethernet profili olusturulamadi — adres degisebilir."
        fi
      fi
    fi

    cat > "$NETPLAN_E1" <<'YAML'
# EnerjiOne Grid appliance — tum arayuzleri NetworkManager yonetir.
# IP/DNS ayarlari artik netplan'dan degil, web arayuzundeki
# "Ag Ayarlari" sayfasindan (e1-netd ajani -> nmcli) yapilir.
network:
  version: 2
  renderer: NetworkManager
YAML
    chmod 600 "$NETPLAN_E1"
    netplan generate >/dev/null 2>&1 || warn "netplan generate uyari verdi."

    # ---- Geri alma muhafizi -------------------------------------------
    # Devir yine de agi koparirsa cihaz kendini KURTARSIN. 120 sn sonra
    # arayuzun IPv4 adresi yoksa netplan yedegi geri yuklenir. Kurulumcu
    # cihazi kaybetmez; en kotu ihtimalle eski ag ayariyla geri gelir.
    setsid nohup bash -c "
      sleep 120
      # Global IPv4 adresi kalmadiysa ag gitmis demektir (lo 'scope host'tur,
      # bu listeye zaten girmez).
      if [ \"\$(ip -4 -o addr show scope global 2>/dev/null | grep -c 'scope global')\" -eq 0 ]; then
        rm -f '$NETPLAN_E1'
        cp -a '$BACKUP_DIR'/*.yaml /etc/netplan/ 2>/dev/null || true
        netplan apply >/dev/null 2>&1 || true
        logger -t e1-appliance 'ag kayboldu — netplan yedegi geri yuklendi'
      fi
    " >/dev/null 2>&1 </dev/null &

    # ---- Devri KOPMAYA DAYANIKLI calistir ------------------------------
    # setsid + nohup: SSH oturumu duserse gelen SIGHUP `netplan apply`i
    # OLDURMESIN, yarim kalmis ag yapilandirmasi birakmasin.
    warn "Ag yoneticisi devrediliyor — baglanti birkac saniye kopabilir."
    setsid nohup netplan apply >/dev/null 2>&1 </dev/null &
    NETPLAN_PID=$!
    # En fazla 60 sn bekle; bitmezse arka planda devam etsin, script asili
    # kalmasin (eski davranistaki sonsuz bekleme tam olarak buydu).
    for _i in $(seq 60); do
      kill -0 "$NETPLAN_PID" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$NETPLAN_PID" 2>/dev/null; then
      warn "netplan apply hala calisiyor — arka planda birakildi, devam ediliyor."
    fi
    ok "netplan renderer -> NetworkManager."
  else
    ok "netplan zaten NetworkManager'a yonlendirilmis."
  fi
fi
# Docker ile cakismayi onle: NetworkManager docker0/veth/br- arayuzlerini
# yonetmeye kalkarsa container aglarini bozabilir (DHCP denemesi, DNS
# ele gecirme, arayuzu "down" etme). Bunlari unmanaged isaretliyoruz.
NM_UNMANAGED=/etc/NetworkManager/conf.d/99-e1-unmanaged.conf
mkdir -p /etc/NetworkManager/conf.d
if [[ ! -f "$NM_UNMANAGED" ]]; then
  cat > "$NM_UNMANAGED" <<'CONF'
# EnerjiOne Grid — Docker'in olusturdugu sanal arayuzlere NetworkManager
# dokunmasin. Aksi halde container aglari kopabilir.
[keyfile]
unmanaged-devices=interface-name:docker*;interface-name:veth*;interface-name:br-*;interface-name:virbr*
CONF
  chmod 644 "$NM_UNMANAGED"
  ok "Docker arayuzleri NetworkManager'dan muaf tutuldu."
else
  ok "Docker muafiyet kurali zaten var."
fi

systemctl enable --now NetworkManager >/dev/null 2>&1 || die "NetworkManager baslatilamadi."
systemctl reload NetworkManager >/dev/null 2>&1 || true
ok "NetworkManager calisiyor."

# --- AP'yi SIMDI acmak mevcut baglantiyi keser mi? --------------------------
# SAHA VAKASI: kurulumcu musteri WiFi'sine SSH ile bagliyken kurulum/guncelleme
# calistirdi; [4/7] adimi `nmcli connection up e1-grid-ap` yapinca SSH oturumu
# koptu, script SIGHUP aldi ve kurulum YARIDA KALDI.
#
# NEDEN: appliance'ta TEK WiFi karti var. Tek radyo ayni anda hem AP hem client
# olamaz. Cihaz bir aga CLIENT olarak bagliyken AP'yi kaldirmak o baglantiyi
# DUSURUR — hem kurulumcunun oturumu hem de cihazin tek internet cikisi
# (docker pull, tailscale, saat senkronu) o link uzerinde olabilir.
#
# COZUM: AP profili HER ZAMAN hazirlanir (asagidaki bloklar degismedi), ama
# AKTIVASYON kesinti yaratacaksa yapilmaz; aktivasyon e1-netd'ye birakilir.
# "Client'a bagli degilsek AP acik olmali" kurali e1-netd-report.timer ile
# 30 sn'de bir kosar (OnBootSec=15s). Ayrica AP profili autoconnect yes +
# autoconnect-priority 100 ile kayitli oldugu icin baglanti bosaldigi an
# NetworkManager'in kendi autoconnect'i de acar. Yani "AP simdi acilmadi"
# KALICI bir durum degildir; en gec ilk reboot'ta AP ile gelinir.
AP_HOLD_REASON=""

# SSH oturumunun geldigi arayuzu bul; bulunamazsa bos doner.
#
# $SSH_CONNECTION'a TEK BASINA GUVENILEMEZ: kanonik kurulum
# `curl -fsSL ... | sudo bash` ve sudo varsayilan olarak env_reset uygular;
# SSH_* degiskenleri env_keep listesinde YOKTUR, yani bu script'e hic ulasmaz.
# root oldugumuz icin ust surec zincirindeki oturumun ortamini dogrudan
# okuyoruz; o da yoksa cekirdekteki kurulu SSH oturumlarina bakiyoruz.
_ssh_ingress_ifname() {
  local peer="" raw="" pid hop=0

  # (a) Ortamda varsa (root shell / `sudo -E`) — en ucuzu.
  [[ -n "${SSH_CONNECTION:-}" ]] && peer="${SSH_CONNECTION%% *}"

  # (b) Ust surec zinciri: sudo degiskeni silse bile giris kabugunun
  #     (ve sshd'nin) environ'unda durur.
  if [[ -z "$peer" ]]; then
    pid=$$
    while [[ -n "$pid" && "$pid" -gt 1 && $hop -lt 20 ]]; do
      if [[ -r "/proc/$pid/environ" ]]; then
        raw="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
               | sed -n 's/^SSH_CONNECTION=//p' | head -1)" || true
        if [[ -n "$raw" ]]; then peer="${raw%% *}"; break; fi
      fi
      # PPid'i /proc/PID/status'tan aliyoruz: /proc/PID/stat'in 2. alani (comm)
      # bosluk/parantez icerebilir ve alan numaralari kayar.
      pid="$(sed -n 's/^PPid:[[:space:]]*//p' "/proc/$pid/status" 2>/dev/null)" || true
      hop=$((hop + 1))
    done
  fi

  # (c) Son care: kurulu SSH oturumlari. Baska bir yoneticinin oturumunu da
  #     kapsar — istenen davranis budur. (Yalnizca 22/tcp.)
  if [[ -z "$peer" ]] && command -v ss >/dev/null 2>&1; then
    peer="$(ss -Htn state established '( sport = :22 )' 2>/dev/null \
            | awk 'NF{print $NF; exit}' \
            | sed -e 's/:[0-9]*$//' -e 's/^\[//' -e 's/\]$//')" || true
  fi

  [[ -z "$peer" ]] && return 0
  # Cevaplarin cikacagi arayuz = oturumu tasiyan arayuz.
  ip route get "$peer" 2>/dev/null \
    | awk '{for (i = 1; i < NF; i++) if ($i == "dev") { print $(i+1); exit }}'
}

# AP aktivasyonu su an guvenli mi?
#   0 = guvenli (acilabilir)   1 = acilmamali (sebep AP_HOLD_REASON'da)
ap_activation_safe() {   # $1 = AP'nin kurulacagi WiFi arayuzu
  local ifname="$1" con mode ssh_if
  AP_HOLD_REASON=""
  [[ -n "$ifname" ]] || return 0

  # --- ANA KRITER: bu radyoda SU AN calisan bir WiFi CLIENT baglantisi -----
  # Kontrol ARAYUZ BAZLI: iki radyolu cihazda AP wlan0'a kurulurken client
  # wlan1'deyse yanlis alarm vermez, eski davranis korunur.
  con="$(nmcli -g GENERAL.CONNECTION device show "$ifname" 2>/dev/null \
         | sed 's/\\:/:/g')" || true
  if [[ -n "$con" && "$con" != "--" && "$con" != "$AP_CON_NAME" ]]; then
    # Profil ADI onemsiz: masaustu NM applet'i, netplan devri veya UI
    # (e1-grid-wifi) kurmus olabilir. Belirleyici olan RADYO MODU.
    mode="$(nmcli -g 802-11-wireless.mode connection show "$con" 2>/dev/null)" || true
    # Bos deger = eski NM'de varsayilan 'infrastructure' (client).
    if [[ "$mode" != "ap" ]]; then
      # IPv4 sarti ONEMLI: IP'siz link ne SSH tasir ne uplink olur. Ayrica
      # e1-netd de "bagli + IP var" diye bakar; ayni kritere bakmazsak ajan
      # 30 sn sonra AP'yi yine acar ve iki taraf birbiriyle kavga eder.
      if ip -4 -o addr show dev "$ifname" scope global 2>/dev/null | grep -q .; then
        AP_HOLD_REASON="${ifname} su an '${con}' agina CLIENT olarak bagli"
        return 1
      fi
      # GECIS ANI: [3/7] netplan devrini ARKA PLANDA baslatiyor. Tam o
      # sirada arayuz baglanmis ama IPv4 henuz gelmemis olabilir
      # (connecting / ip-config). O anda "IP yok, demek client degil" deyip
      # AP'yi acarsak yarisi kaybeder ve baglantiyi keseriz. Belirsizken
      # GUVENLI taraf: bekletmek.
      dev_state="$(nmcli -g GENERAL.STATE device show "$ifname" 2>/dev/null)" || true
      case "$(printf '%s' "$dev_state" | tr 'A-Z' 'a-z')" in
        *connecting*|*config*|*prepare*|*ip-check*)
          AP_HOLD_REASON="${ifname} '${con}' agina baglanmak uzere (durum: ${dev_state})"
          return 1 ;;
      esac
    fi
  fi

  # --- IKINCIL EMNIYET: SSH oturumu bu arayuzden mi geliyor? --------------
  # nmcli durumu yanlis raporlarsa (ekzotik surucu/NM surumu) son savunma.
  ssh_if="$(_ssh_ingress_ifname)" || true
  if [[ -n "$ssh_if" && "$ssh_if" == "$ifname" ]]; then
    AP_HOLD_REASON="SSH oturumu ${ifname} uzerinden geliyor"
    return 1
  fi

  return 0
}

# ---------------------------------------------------------------------------
step "[4/7] WiFi erisim noktasi: ${AP_SSID}"
if [[ "${SKIP_AP:-0}" == "1" ]]; then
  warn "SKIP_AP=1 — AP kurulumu atlandi."
else
  # WiFi arayuzunu bul.
  WIFI_IF="${AP_IFNAME:-}"
  if [[ -z "$WIFI_IF" ]]; then
    WIFI_IF="$(nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="wifi"{print $1; exit}')"
  fi
  if [[ -z "$WIFI_IF" ]]; then
    warn "WiFi arayuzu bulunamadi — AP kurulmadi."
    warn "USB WiFi adaptoru takip tekrar calistirin: sudo bash $0"
  else
    info "WiFi arayuzu: ${WIFI_IF}"
    # Kart AP modunu destekliyor mu? Desteklemiyorsa hostapd/NM sessizce
    # basarisiz olur; kullaniciya simdi soyleyelim.
    if command -v iw >/dev/null 2>&1; then
      if iw list 2>/dev/null | grep -A10 "Supported interface modes" | grep -q "\* AP"; then
        ok "Kart AP modunu destekliyor."
      else
        warn "Kart AP modunu desteklemiyor gorunuyor — AP calismayabilir."
      fi
    fi
    # WiFi radyosu acik olmali (rfkill).
    nmcli radio wifi on >/dev/null 2>&1 || true

    if nmcli -t -f NAME connection show | grep -qx "$AP_CON_NAME"; then
      info "AP profili mevcut, ayarlar guncelleniyor."
    else
      nmcli connection add type wifi ifname "$WIFI_IF" con-name "$AP_CON_NAME" \
        autoconnect yes ssid "$AP_SSID" >/dev/null
      ok "AP profili olusturuldu."
    fi

    # AP ayarlari (idempotent — her calistirmada dogru degere cekilir):
    #   mode ap            : erisim noktasi
    #   band bg / channel  : 2.4GHz, tum telefonlar gorur
    #   ipv4.method shared : NM dnsmasq ile DHCP + NAT (10.42.0.1/24)
    #   key-mgmt none      : SIFRESIZ acik ag (istenen davranis)
    #   autoconnect-priority: boot'ta oncelikli kalksin
    nmcli connection modify "$AP_CON_NAME" \
      connection.interface-name "$WIFI_IF" \
      connection.autoconnect yes \
      connection.autoconnect-priority 100 \
      802-11-wireless.mode ap \
      802-11-wireless.band bg \
      802-11-wireless.channel "$AP_CHANNEL" \
      802-11-wireless.ssid "$AP_SSID" \
      802-11-wireless.powersave 2 \
      ipv4.method shared \
      ipv6.method ignore >/dev/null
    # Onceden sifreli kurulmus olabilir — acik aga cevir.
    nmcli connection modify "$AP_CON_NAME" \
      -802-11-wireless-security.key-mgmt "" >/dev/null 2>&1 || true
    nmcli connection modify "$AP_CON_NAME" \
      802-11-wireless-security.key-mgmt "" >/dev/null 2>&1 || true
    ok "AP profili ayarlandi (sifresiz, kanal ${AP_CHANNEL})."

    # AP istemcileri icin e1-grid.local -> 10.42.0.1. mDNS'e guvenmiyoruz:
    # Android tarayicilar ve bazi Windows surumleri .local'i cozemez; AP'nin
    # kendi DNS'i (NM shared dnsmasq) bu kaydi dogrudan verir.
    mkdir -p /etc/NetworkManager/dnsmasq-shared.d
    cat > /etc/NetworkManager/dnsmasq-shared.d/e1-grid.conf <<CONF
# EnerjiOne Grid AP istemcileri icin sabit isim cozumlemesi.
address=/${APPLIANCE_HOSTNAME}.local/${AP_ADDRESS}
address=/${APPLIANCE_HOSTNAME}/${AP_ADDRESS}
# ESKI AD — sahada e1-grid.local ile kurulmus cihazlar ve yer imleri
# calismaya devam etsin. AP'ye baglanan istemci iki addan da girebilir.
address=/${APPLIANCE_HOSTNAME_LEGACY}.local/${AP_ADDRESS}
address=/${APPLIANCE_HOSTNAME_LEGACY}/${AP_ADDRESS}
CONF
    ok "AP DNS kaydi: ${APPLIANCE_HOSTNAME}.local -> ${AP_ADDRESS} (eski ad: ${APPLIANCE_HOSTNAME_LEGACY}.local)"

    # AP'yi "up" etmek TEK RADYODA mevcut baglantiyi dusurur. Iki koruma:
    #   1. AP zaten yayindaysa dokunma -> script her update.sh calismasinda
    #      tekrar kosar; sahadaki AP kullanicilarinin (ve belki update'i
    #      yapan kisinin) baglantisi dusmesin.
    #   2. Radyo su an CLIENT olarak bir aga bagliysa dokunma -> kurulumcunun
    #      SSH oturumu ve cihazin internet cikisi orada olabilir. Profil
    #      hazir; aktivasyonu e1-netd ustlenir (report timer 30 sn:
    #      "client bagli degilse AP acik olmali").
    if nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$AP_CON_NAME"; then
      ok "AP zaten yayinda: ${AP_SSID} (kesintiye ugratilmadi)"
      info "SSID/kanal degistirdiyseniz uygulamak icin: nmcli connection up ${AP_CON_NAME}"
    elif ! ap_activation_safe "$WIFI_IF"; then
      AP_PENDING=1
      warn "AP simdi ACILMADI: ${AP_HOLD_REASON}."
      info "Tek WiFi karti ayni anda hem AP hem client olamaz; acmak bu"
      info "baglantiyi (ve muhtemelen bu SSH oturumunu) keserdi."
      info "AP profili kayitli ve otomatik: baglanti dustugu an e1-netd 30 sn"
      info "icinde acar; ilk yeniden baslatmada da AP ile gelinir."
      info "Hemen acmak icin (BU baglantiyi keser, konsoldan calistirin):"
      info "  sudo nmcli connection up ${AP_CON_NAME}"

      # BEKCI — AP'nin acilmasini e1-netd'ye TEK BASINA emanet etmiyoruz.
      # Bu karar [4/7]'de veriliyor ama guvenlik agi (e1-netd + timer)
      # [5/7]'de kuruluyor. Script arada olurse (SSH kopmasi, hata, ERR
      # trap) AP profili kayitli kalir ama HIC acilmaz; cihaz client agina
      # bagli kalir ve o ag giderse erisim yolu kalmaz.
      #
      # Bu yuzden hold verildigi anda, netplan geri alma muhafiziyla ayni
      # desende bagimsiz bir bekci baslatiyoruz: 5 dakika sonra hala AP
      # yayinda DEGILSE ve radyoda calisan bir client da yoksa AP'yi acar.
      # Client hala bagliysa DOKUNMAZ — kurulumcunun oturumunu kesmez.
      setsid nohup bash -c "
        sleep 300
        # AP zaten acildiysa (e1-netd ya da NM autoconnect) isimiz yok.
        nmcli -t -f NAME connection show --active 2>/dev/null \
          | grep -qx '${AP_CON_NAME}' && exit 0
        # Radyoda hala calisan bir client varsa DOKUNMA.
        con=\"\$(nmcli -g GENERAL.CONNECTION device show '${WIFI_IF}' 2>/dev/null)\"
        if [ -n \"\$con\" ] && [ \"\$con\" != '--' ] && [ \"\$con\" != '${AP_CON_NAME}' ]; then
          if nmcli -g IP4.ADDRESS device show '${WIFI_IF}' 2>/dev/null | grep -q .; then
            exit 0
          fi
        fi
        nmcli connection up '${AP_CON_NAME}' >/dev/null 2>&1 \
          && logger -t e1-appliance 'bekci: AP acildi (client baglantisi yok)'
      " >/dev/null 2>&1 </dev/null &
    elif nmcli connection up "$AP_CON_NAME" >/dev/null 2>&1; then
      ok "AP yayinda: ${AP_SSID}"
    else
      warn "AP simdi baslatilamadi; reboot sonrasi otomatik denenecek."
    fi
  fi
fi

# ---------------------------------------------------------------------------
step "[5/7] Ag ajani (e1-netd)"
# Ajan repo icinden calisir; unit dosyalari /opt/enerjione-grid yolunu bekler.
if [[ "$AGENT_SRC_DIR" != "${INSTALL_DIR}/infra/appliance" ]]; then
  warn "Repo ${INSTALL_DIR} altinda degil (${AGENT_SRC_DIR})."
  warn "systemd unit'leri ${INSTALL_DIR}/infra/appliance/e1-netd.py yolunu kullanir;"
  warn "farkli dizinde calisiyorsaniz unit dosyalarindaki yolu duzeltin."
fi
chmod 755 "${AGENT_SRC_DIR}/e1-netd.py"

# Durum dizini: backend container (uid ${BACKEND_UID}) buraya request.json
# yazar, ajan state.json/status.json yazar. Grup sahipligi backend uid'si,
# mod 0770 -> baska kullanicilar goremez.
mkdir -p "$STATE_DIR"
chown "root:${BACKEND_UID}" "$STATE_DIR"
chmod 0770 "$STATE_DIR"
ok "Durum dizini: ${STATE_DIR} (root:${BACKEND_UID}, 0770)"

install -m 644 "${AGENT_SRC_DIR}/systemd/e1-netd.service"        /etc/systemd/system/
install -m 644 "${AGENT_SRC_DIR}/systemd/e1-netd.path"           /etc/systemd/system/
install -m 644 "${AGENT_SRC_DIR}/systemd/e1-netd-report.service" /etc/systemd/system/
install -m 644 "${AGENT_SRC_DIR}/systemd/e1-netd-report.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now e1-netd.path >/dev/null 2>&1
systemctl enable --now e1-netd-report.timer >/dev/null 2>&1
systemctl start e1-netd-report.service >/dev/null 2>&1 || true
ok "e1-netd.path + e1-netd-report.timer aktif."

# ---------------------------------------------------------------------------
step "[6/7] Backend baglantisi (docker compose mount)"
if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
  if grep -q "e1-grid/net" "${INSTALL_DIR}/docker-compose.yml"; then
    ok "docker-compose.yml ${STATE_DIR} mount'unu iceriyor."
    info "Backend'i yeni mount ile yeniden olusturun:"
    info "  cd ${INSTALL_DIR} && sudo docker compose up -d backend-api"
  else
    warn "docker-compose.yml'de ${STATE_DIR} mount'u YOK."
    warn "Repoyu guncelleyin (git pull) — Ag Ayarlari sayfasi aksi halde calismaz."
  fi
else
  warn "${INSTALL_DIR}/docker-compose.yml bulunamadi; mount kontrolu atlandi."
fi

# ---------------------------------------------------------------------------
step "[7/7] Ozet"
sleep 1
systemctl start e1-netd-report.service >/dev/null 2>&1 || true
echo
echo "${C_GREEN}${C_BOLD}============================================================${C_RESET}"
echo "${C_GREEN}${C_BOLD}  Appliance modu hazir.${C_RESET}"
echo "${C_GREEN}${C_BOLD}============================================================${C_RESET}"
echo
# Ozet, AP'nin GERCEK durumunu soylemeli. AP bekletildiyse "AP'ye baglan"
# demek kullaniciyi olmayan bir aga yonlendirir ve cihazi erisilemez sanir.
if [[ "${AP_PENDING:-0}" == "1" ]]; then
  echo "  ${C_BOLD}Ilk kullanim:${C_RESET}"
  echo "    ${C_YELLOW}AP su an KAPALI${C_RESET} — ${AP_HOLD_REASON}."
  echo "    Cihaza SU ANDA baglandiginiz adresten erisin:"
  echo "      ${C_BOLD}http://${APPLIANCE_HOSTNAME}.local${C_RESET}"
  _ap_cur_ip="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -3 | tr '\n' ' ')"
  [[ -n "${_ap_cur_ip// /}" ]] && echo "      veya http://${_ap_cur_ip% }"
  echo "    ${C_BOLD}${AP_SSID}${C_RESET} agi, client baglantisi dustugunde"
  echo "    kendiliginden acilir (e1-netd ~30 sn, en gec ilk yeniden baslatmada)."
  echo "    Hemen acmak icin (BU baglantiyi keser, konsoldan): sudo nmcli connection up ${AP_CON_NAME}"
  echo "    Sonra: Giris yap -> Muhendislik > Sistem > ${C_BOLD}Ag Ayarlari${C_RESET}"
else
  echo "  ${C_BOLD}Ilk kullanim:${C_RESET}"
  echo "    1. Telefon/laptop WiFi listesinden ${C_BOLD}${AP_SSID}${C_RESET} agina baglan (sifre yok)"
  echo "    2. Tarayicidan  ${C_BOLD}http://${APPLIANCE_HOSTNAME}.local${C_RESET}  (veya http://${AP_ADDRESS})"
  echo "    3. Giris yap -> Muhendislik > Sistem > ${C_BOLD}Ag Ayarlari${C_RESET}"
  echo "    4. Kablolu arayuze statik IP ver -> cihaz yeniden baslar"
fi
echo
echo "  ${C_BOLD}Tanilama:${C_RESET}"
echo "    Ag durumu   : cat ${STATE_DIR}/state.json"
echo "    Ajan logu   : sudo journalctl -u e1-netd -n 50"
echo "    AP durumu   : nmcli connection show ${AP_CON_NAME}"
echo "    AP istemci  : nmcli device wifi list ifname <wlan>"
echo
echo "  ${C_YELLOW}Not:${C_RESET} AP sifresizdir. Tek WiFi karti hem AP hem client olamadigi"
echo "  icin cihaz bir WiFi agina bagliyken AP KAPALIDIR; o baglanti dustugu an"
echo "  e1-netd AP'yi geri acar. Yani yanlis statik IP girilse bile cihaza"
echo "  AP uzerinden baglanip duzeltebilirsiniz."
echo
