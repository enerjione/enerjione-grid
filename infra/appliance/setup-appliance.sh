#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Appliance (mini PC) modu kurulumu
# ===========================================================================
# Mini PC'yi "acinca calisan cihaz" haline getirir:
#
#   1. Hostname -> e1-grid  (avahi ile http://e1-grid.local calisir)
#   2. NetworkManager ag yoneticisi olur (netplan renderer dahil)
#   3. Sifresiz WiFi AP: "EnerjiOne Grid" — boot'ta otomatik acilir,
#      istemcilere 10.42.0.0/24 DHCP dagitir, e1-grid.local -> 10.42.0.1
#   4. avahi-daemon (mDNS) — kablolu agdan da e1-grid.local calisir
#   5. e1-netd ag ajani + systemd unit'leri (UI'dan IP/DNS ayari)
#
# Calistirma (repo kokunde, kurulum sonrasi):
#   sudo bash infra/appliance/setup-appliance.sh
#
# Idempotent: tekrar calistirmak guvenli.
#
# Env override:
#   AP_SSID        AP adi           (default: "EnerjiOne Grid")
#   AP_CHANNEL     2.4GHz kanal     (default: 6)
#   AP_IFNAME      WiFi arayuzu     (default: otomatik tespit)
#   APPLIANCE_HOSTNAME               (default: e1-grid)
#   BACKEND_UID    backend container uid (default: 10001)
#   SKIP_AP=1      AP kurulumunu atla (WiFi karti yoksa)
# ===========================================================================
set -euo pipefail

AP_SSID="${AP_SSID:-EnerjiOne Grid}"
AP_CON_NAME="e1-grid-ap"
AP_CHANNEL="${AP_CHANNEL:-6}"
APPLIANCE_HOSTNAME="${APPLIANCE_HOSTNAME:-e1-grid}"
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
command -v python3 >/dev/null 2>&1 || NEEDED+=(python3)
# AP modunda NetworkManager dnsmasq'i icsel kullanir (shared mode).
dpkg -s dnsmasq-base >/dev/null 2>&1 || NEEDED+=(dnsmasq-base)
# WiFi kart/regulasyon araclari
command -v iw >/dev/null 2>&1 || NEEDED+=(iw)

if [[ ${#NEEDED[@]} -gt 0 ]]; then
  info "Kuruluyor: ${NEEDED[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEEDED[@]}"
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

systemctl enable --now avahi-daemon >/dev/null 2>&1 || warn "avahi-daemon baslatilamadi."
ok "mDNS aktif — kablolu agdan http://${APPLIANCE_HOSTNAME}.local"

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
    netplan apply >/dev/null 2>&1 || warn "netplan apply uyari verdi (reboot sonrasi gecerli olur)."
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
CONF
    ok "AP DNS kaydi: ${APPLIANCE_HOSTNAME}.local -> ${AP_ADDRESS}"

    # ONEMLI: AP zaten yayindaysa `connection up` yapmiyoruz — script her
    # update.sh calismasinda tekrar kosar ve bu, AP'ye bagli sahadaki
    # kullanicilarin (ve belki de update'i yapan kisinin) baglantisini keser.
    if nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$AP_CON_NAME"; then
      ok "AP zaten yayinda: ${AP_SSID} (kesintiye ugratilmadi)"
      info "SSID/kanal degistirdiyseniz uygulamak icin: nmcli connection up ${AP_CON_NAME}"
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
echo "  ${C_BOLD}Ilk kullanim:${C_RESET}"
echo "    1. Telefon/laptop WiFi listesinden ${C_BOLD}${AP_SSID}${C_RESET} agina baglan (sifre yok)"
echo "    2. Tarayicidan  ${C_BOLD}http://${APPLIANCE_HOSTNAME}.local${C_RESET}  (veya http://${AP_ADDRESS})"
echo "    3. Giris yap -> Muhendislik > Sistem > ${C_BOLD}Ag Ayarlari${C_RESET}"
echo "    4. Kablolu arayuze statik IP ver -> cihaz yeniden baslar"
echo
echo "  ${C_BOLD}Tanilama:${C_RESET}"
echo "    Ag durumu   : cat ${STATE_DIR}/state.json"
echo "    Ajan logu   : sudo journalctl -u e1-netd -n 50"
echo "    AP durumu   : nmcli connection show ${AP_CON_NAME}"
echo "    AP istemci  : nmcli device wifi list ifname <wlan>"
echo
echo "  ${C_YELLOW}Not:${C_RESET} AP sifresizdir ve her zaman aciktir; yanlis statik IP"
echo "  girilse bile cihaza AP uzerinden baglanip duzeltebilirsiniz."
echo
