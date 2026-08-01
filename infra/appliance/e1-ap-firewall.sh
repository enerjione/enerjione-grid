#!/usr/bin/env bash
# ===========================================================================
# e1-ap-firewall — WiFi AP istemcilerinin erisebilecegi portlari sinirlar
# ===========================================================================
#
# YASANAN ACIK
# ------------
# Appliance kurulumu WiFi erisim noktasini SIFRESIZ aciyor (kurulumcu karari)
# ve `ipv4.method shared` ile 10.42.0.0/24 DHCP+NAT veriyor. docker-compose'
# daki TUM `ports:` girisleri 0.0.0.0'a bind oldugu icin AP istemcisine sunlar
# aciktir:
#
#     80    web arayuzu          (olmasi gereken)
#     21    FTP                  (cihaz config/firmware transferi)
#     502   Modbus TCP           (KIMLIK DOGRULAMASIZ)
#     2404-2406  IEC 104         (KIMLIK DOGRULAMASIZ)
#     4222  NATS                 (parola var ama brute-force'a acik)
#     5672  RabbitMQ             (ayni)
#     5020,5021  ek Modbus       (ayni)
#     30000-30009  FTP pasif     (ayni)
#
# Yani sokaktan telefonla `E1GRID-<musteri>` agina baglanan biri, kimlik
# dogrulamasi OLMADAN tum sahanin ariza/konum/olcum durumunu okuyabiliyor
# (`:2404` ve `:502`), ayrica NATS/RabbitMQ'yu brute-force icin buluyor.
# 600 cihaz = 600 bagimsiz, surekli yayinda giris noktasi.
#
# BU BETIK NE YAPAR / NE YAPMAZ
# ------------------------------
# YAPAR : AP arayuzunden APPLIANCE'A yonelen trafikte yalnizca 80/443'e izin
#         verir; SCADA/NATS/RabbitMQ/FTP portlarini kapatir.
# YAPMAZ: agi sifrelemez. WPA2 EKLENMEDI (operator karari). Yani menzildeki
#         biri hala aga baglanabilir ve giris ekranina DUZ HTTP uzerinden
#         ulasabilir; parola/JWT sifresiz gider. Bu bilincli bir kabul.
#
# TASARIM NOTLARI
# ---------------
# 1) Container portlari FORWARD yolundan gecer (DNAT sonrasi). Kural
#    `DOCKER-USER` zincirine takiliyor: Docker bu zinciri kendi kurallarindan
#    ONCE gezer ve ASLA temizlemez, yani `docker compose up` kurali silmez.
#
# 2) FORWARD'a gelen paket ZATEN DNAT'lanmistir; `--dport` container portunu
#    gosterir (or. 8080), yayinlanan host portunu (80) DEGIL. Bu klasik tuzak.
#    Bu yuzden ORIJINAL hedef port `--ctorigdstport` ile eslestiriliyor.
#
# 3) Guard yalnizca hedefi APPLIANCE OLAN trafige uygulanir
#    (`--ctorigdst <ap-adresi>`). Aksi halde AP istemcisinin internet erisimi
#    de kesilirdi — `ipv4.method shared` NAT sagliyor ve teknisyenin telefonu
#    bu yolu kullanabilir.
#
# 4) Host uzerinde dogrudan dinleyen servisler (SSH, dnsmasq) FORWARD'dan
#    GECMEZ; onlar icin ayri bir INPUT zinciri var. DHCP/DNS/mDNS/ICMP acik
#    kalir — kapanirsa AP'nin kendisi calismaz.
#
# 5) SSH AP UZERINDEN KAPANIR. Kurtarma yolu web arayuzudur (10.42.0.1);
#    uzaktan bakim icin Tailscale, yerelde konsol/kablolu ag var. Gerekirse:
#       E1_AP_ALLOWED_TCP="80 443 22" e1-ap-firewall.sh <arayuz>
#
# Idempotent: tekrar tekrar calistirilabilir (NetworkManager dispatcher her
# `up` olayinda cagirir).
#
# Kullanim: e1-ap-firewall.sh <wifi-arayuzu> [ap-adresi]
# ===========================================================================
set -euo pipefail

WIFI_IF="${1:-${E1_AP_IFACE:-}}"
AP_ADDRESS="${2:-${E1_AP_ADDRESS:-10.42.0.1}}"
ALLOWED_TCP="${E1_AP_ALLOWED_TCP:-80 443}"

CHAIN_FWD="E1-AP-FWD"
CHAIN_IN="E1-AP-IN"

if [[ -z "$WIFI_IF" ]]; then
  echo "kullanim: $0 <wifi-arayuzu> [ap-adresi]" >&2
  exit 2
fi

if ! command -v iptables >/dev/null 2>&1; then
  echo "e1-ap-firewall: iptables yok — AP port filtresi UYGULANMADI" >&2
  exit 1
fi

# Arayuz yoksa sessizce cik: dispatcher baska arayuzler icin de tetiklenir.
if [[ ! -d "/sys/class/net/${WIFI_IF}" ]]; then
  exit 0
fi

_zincir_hazirla() {
  local zincir="$1"
  # Varsa icini bosalt (idempotent), yoksa yarat.
  if iptables -n -L "$zincir" >/dev/null 2>&1; then
    iptables -F "$zincir"
  else
    iptables -N "$zincir"
  fi
}

_bagla() {
  # $1 = ust zincir, $2 = hedef zincir, kalanlar = eslestirme kurallari
  local ust="$1" hedef="$2"
  shift 2
  # -C ile once VAR MI diye bak; yoksa en basa ekle. Tekrar tekrar
  # calistiginda kural yigilmasin.
  if ! iptables -C "$ust" "$@" -j "$hedef" 2>/dev/null; then
    iptables -I "$ust" 1 "$@" -j "$hedef"
  fi
}

# --- 1) Container'lara giden trafik (FORWARD / DOCKER-USER) ----------------
_zincir_hazirla "$CHAIN_FWD"
iptables -A "$CHAIN_FWD" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for port in $ALLOWED_TCP; do
  iptables -A "$CHAIN_FWD" -p tcp -m conntrack --ctorigdstport "$port" -j RETURN
done
iptables -A "$CHAIN_FWD" -j DROP

# DOCKER-USER Docker tarafindan yaratilir; kurulu degilse (Docker henuz yok)
# olustur ki kural kaybolmasin.
if ! iptables -n -L DOCKER-USER >/dev/null 2>&1; then
  iptables -N DOCKER-USER
  iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD 1 -j DOCKER-USER
fi
_bagla DOCKER-USER "$CHAIN_FWD" -i "$WIFI_IF" -m conntrack --ctorigdst "$AP_ADDRESS"

# --- 2) Host uzerinde dinleyen servisler (INPUT) ---------------------------
_zincir_hazirla "$CHAIN_IN"
iptables -A "$CHAIN_IN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
# AP'nin CALISMASI icin sart olanlar — kapatilirsa istemci IP bile alamaz.
iptables -A "$CHAIN_IN" -p udp --dport 67 -j RETURN     # DHCP
iptables -A "$CHAIN_IN" -p udp --dport 53 -j RETURN     # DNS (dnsmasq)
iptables -A "$CHAIN_IN" -p tcp --dport 53 -j RETURN
iptables -A "$CHAIN_IN" -p udp --dport 5353 -j RETURN   # mDNS (.local)
iptables -A "$CHAIN_IN" -p icmp -j RETURN               # teshis (ping)
for port in $ALLOWED_TCP; do
  iptables -A "$CHAIN_IN" -p tcp --dport "$port" -j RETURN
done
iptables -A "$CHAIN_IN" -j DROP

_bagla INPUT "$CHAIN_IN" -i "$WIFI_IF"

echo "e1-ap-firewall: ${WIFI_IF} -> yalnizca TCP {${ALLOWED_TCP}} (+DHCP/DNS/mDNS/ICMP)"
