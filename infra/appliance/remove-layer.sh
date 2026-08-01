#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — TEK bir ek katmani kaldirir
# ===========================================================================
# Kullanim:
#   sudo bash remove-layer.sh tailscale   # uzaktan bakim VPN'i + izin ajani
#   sudo bash remove-layer.sh kiosk       # operator ekrani + kiosk hesabi
#   sudo bash remove-layer.sh appliance   # WiFi AP + ag ajani + mDNS
#
# NEDEN AYRI BETIK:
#   `uninstall.sh` "her seyi kaldir" icindir. Sahada cogu zaman gereken sey
#   ise TEK bir katmani geri almaktir: kiosk hesabini silip cihazi normal
#   masaustune dondurmek, ya da VPN'i kaldirip cihazi tailnet'ten cikarmak.
#   Onceden bunun icin belgelenmis bir yol yoktu; operator elle systemctl /
#   userdel komutlari calistiriyordu ve her seferinde bir seyi atliyordu.
#
#   Kaldirma mantigi BILEREK repoda: kurulum mantiginin yaninda durur, cihazla
#   birlikte surumlenir ve kurulum araci yalnizca bunu cagirir. Aksi halde
#   ayni bilgi iki yerde yasar ve biri gunceldenmedigi gun yarim temizlik
#   yapilir.
#
# Uygulama katmanina (container/veritabani) DOKUNMAZ.
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../scripts/linux/_lib.sh" ]]; then
  # shellcheck source=/dev/null
  . "${SCRIPT_DIR}/../scripts/linux/_lib.sh"
else
  e1_info() { printf '  · %s\n' "$*"; }
  e1_ok()   { printf '  ✓ %s\n' "$*"; }
  e1_warn() { printf '  ! %s\n' "$*" >&2; }
  e1_step() { printf '\n== %s\n' "$*"; }
fi

LAYER="${1:-}"
[[ "$(id -u)" -eq 0 ]] || { e1_warn "root gerekli (sudo)."; exit 1; }

_units_remove() {  # $@ = unit adlari
  local u
  for u in "$@"; do
    systemctl stop "$u" 2>/dev/null || true
    systemctl disable "$u" 2>/dev/null || true
    rm -f "/etc/systemd/system/${u}"
  done
  systemctl daemon-reload 2>/dev/null || true
}

case "$LAYER" in
  tailscale)
    e1_step "Uzaktan bakim VPN'i kaldiriliyor"
    # SIRA ONEMLI: once kapiyi kapat. Ters sirada, kapatmayi yapacak ajan
    # kaldirilmis olur ve acik bir izin sonsuza kadar acik kalirdi.
    if [[ -x "${SCRIPT_DIR}/e1-rad.py" ]]; then
      "${SCRIPT_DIR}/e1-rad.py" close --reason remove-layer >/dev/null 2>&1 \
        || python3 "${SCRIPT_DIR}/e1-rad.py" close --reason remove-layer >/dev/null 2>&1 \
        || e1_warn "Izin kapatilamadi; asagidaki logout yine de uygulanacak."
    fi
    _units_remove e1-rad.path e1-rad-report.timer e1-rad.service e1-rad-report.service
    if command -v tailscale >/dev/null 2>&1; then
      tailscale logout >/dev/null 2>&1 \
        && e1_ok "Cihaz tailnet'ten cikarildi." \
        || e1_warn "tailscale logout basarisiz — dugumu yonetim panelinden silin."
    fi
    # Kalicilik makbuzu artik gecersiz; birakilirsa sonraki kurulum yanlis
    # "kalici" raporu verir.
    rm -f /var/lib/e1-grid/tailscale-join.json 2>/dev/null || true
    rm -rf /var/lib/e1-grid/remote /var/lib/e1-grid/remote-priv 2>/dev/null || true
    e1_ok "Uzaktan bakim VPN'i kaldirildi. (tailscale paketi DURUYOR)"
    e1_info "Paketi de silmek icin: sudo apt-get purge tailscale"
    ;;

  kiosk)
    e1_step "Operator ekrani (kiosk) kaldiriliyor"
    # Hesap adi kuruluma gore degisir (musteri adindan turer). Tahmin etmiyoruz:
    # GECOS imzasindan buluyoruz.
    mapfile -t _users < <(getent passwd | awk -F: '$5 ~ /EnerjiOne Grid/ {print $1}' 2>/dev/null || true)
    if [[ ${#_users[@]} -eq 0 ]]; then
      e1_info "Kiosk hesabi bulunamadi (kurulmamis olabilir)."
    fi
    for u in "${_users[@]}"; do
      [[ -n "$u" && "$u" != "root" ]] || continue
      e1_info "Hesap kaldiriliyor: ${u}"
      pkill -KILL -u "$u" 2>/dev/null || true
      userdel --remove "$u" 2>/dev/null \
        || deluser --remove-home "$u" >/dev/null 2>&1 \
        || e1_warn "Hesap silinemedi: ${u} (oturumu acik olabilir; cihazi yeniden baslatip tekrar deneyin)"
      rm -f "/var/lib/AccountsService/users/${u}" "/var/lib/AccountsService/icons/${u}" 2>/dev/null || true
    done
    # Otomatik giris: yalnizca bizim yazdigimiz anahtarlar silinir, dosya kalir.
    for dm in /etc/gdm3/daemon.conf /etc/gdm3/custom.conf /etc/gdm/custom.conf \
              /etc/lightdm/lightdm.conf /etc/sddm.conf; do
      [[ -f "$dm" ]] || continue
      sed -i -E '/^[[:space:]]*(AutomaticLogin(Enable)?|autologin-user|User)[[:space:]]*=/d' "$dm" 2>/dev/null || true
    done
    rm -f /usr/share/xsessions/enerjione-kiosk.desktop 2>/dev/null || true
    rm -f /etc/sddm.conf.d/e1-kiosk.conf /etc/lightdm/lightdm.conf.d/e1-kiosk.conf 2>/dev/null || true
    e1_ok "Kiosk kaldirildi. Cihaz normal masaustu oturumuna doner."
    e1_info "Degisikligin gorunmesi icin yeniden baslatin."
    ;;

  appliance)
    e1_step "Ag ve erisim katmani kaldiriliyor"
    _units_remove e1-netd.path e1-netd-report.timer e1-netd.service e1-netd-report.service \
                  e1-gwd.path  e1-gwd-report.timer  e1-gwd.service  e1-gwd-report.service
    if command -v nmcli >/dev/null 2>&1; then
      nmcli connection down e1-grid-ap 2>/dev/null || true
      nmcli connection delete e1-grid-ap 2>/dev/null || true
    fi
    rm -f /etc/NetworkManager/dnsmasq-shared.d/e1-grid.conf 2>/dev/null || true
    rm -rf /var/lib/e1-grid/net /var/lib/e1-grid/gw 2>/dev/null || true
    # mDNS takma adi (eski e1-grid.local yayini). Hostname'in KENDISI korunur:
    # sistem genelinde baska seyleri etkileyebilir, kullaniciya birakiyoruz.
    _units_remove e1-grid-mdns-alias.service
    rm -f /etc/avahi/services/e1-grid*.service 2>/dev/null || true
    e1_ok "WiFi AP, ag ajani ve mDNS takma adi kaldirildi."
    e1_warn "DIKKAT: cihaza yalnizca bu AP uzerinden eristiyseniz baglanti KOPAR."
    e1_info "Hostname korundu. Geri almak: sudo hostnamectl set-hostname <eski-ad>"
    ;;

  *)
    e1_warn "Kullanim: remove-layer.sh {tailscale|kiosk|appliance}"
    exit 1
    ;;
esac
exit 0
