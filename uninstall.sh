#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Uninstall
# ===========================================================================
# Sistemi tamamen kaldirir:
#   - docker compose down -v  (container + volume = TUM VERI silinir)
#   - e1-grid/* (+ eski e1/*) docker image'larini sil (--keep-images ile koru)
#   - opsiyonel: install dizinini de sil (--purge-dir)
#
# Kullanim (repo kokunde):
#   sudo bash uninstall.sh                # interaktif onayli
#   sudo bash uninstall.sh --yes          # tum onaylari atla
#   sudo bash uninstall.sh --keep-images  # image'lari koru (sadece data sil)
#   sudo bash uninstall.sh --purge-dir    # /opt/enerjione-grid dizinini de sil
#   sudo bash uninstall.sh --yes --purge-dir   # stack + dizin
#   sudo bash uninstall.sh --yes --purge-all   # HICBIR IZ BIRAKMA (asagiya bak)
#
# --purge-all: --purge-dir'in yaptiklarina EK olarak, kurulumun sistemde
# biraktigi geri kalan her seyi siler:
#   * /etc/enerjione-grid/    -> install.env (TAILSCALE ANAHTARI + GHCR TOKEN),
#                                site.env, e1-rad.env. EN ONEMLISI BU: --purge-dir
#                                bu dizine DOKUNMUYORDU; cihaz elden cikarilsa
#                                bile canli anahtarlar diskte kaliyordu.
#   * kiosk kullanicisi       -> hesap + ev dizini + otomatik giris ayari +
#                                /usr/share/xsessions/*.desktop + avatar
#   * mDNS takma adi          -> e1-grid.local yayini (avahi)
#   * tailscale paketi        -> yalnizca --purge-tailscale ile (baska bir is
#                                icin kullaniliyor olabilir)
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/infra/scripts/linux/_lib.sh"

e1_require_root "$@"

# ---- Arg parsing ----------------------------------------------------------
ASSUME_YES=0
KEEP_IMAGES=0
PURGE_DIR=0
PURGE_ALL=0
PURGE_TAILSCALE=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)        ASSUME_YES=1 ;;
    --keep-images)   KEEP_IMAGES=1 ;;
    --purge-dir)     PURGE_DIR=1 ;;
    # --purge-all dizin silmeyi de KAPSAR; ayrica yazmak gerekmesin.
    --purge-all)     PURGE_ALL=1; PURGE_DIR=1 ;;
    --purge-tailscale) PURGE_TAILSCALE=1 ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^#\s*//' | head -30
      exit 0
      ;;
    *) e1_die "Bilinmeyen parametre: $arg (--help icin --help)" ;;
  esac
done
export ASSUME_YES

cd "$SCRIPT_DIR"

# ---- Banner ---------------------------------------------------------------
clear 2>/dev/null || true
e1_banner
echo "  ${E1_RED}${E1_BOLD}DIKKAT:${E1_RESET} Bu islem TUM verilerinizi silecek!"
echo
echo "  ${E1_DIM}Dizin       :${E1_RESET} ${SCRIPT_DIR}"
echo "  ${E1_DIM}Image'lar   :${E1_RESET} $([[ $KEEP_IMAGES -eq 1 ]] && echo 'KORUNACAK (--keep-images)' || echo 'SILINECEK')"
echo "  ${E1_DIM}Dizini sil  :${E1_RESET} $([[ $PURGE_DIR -eq 1 ]] && echo 'EVET (--purge-dir)' || echo 'hayir')"
echo "  ${E1_DIM}Onay        :${E1_RESET} $([[ $ASSUME_YES -eq 1 ]] && echo 'OTOMATIK (--yes)' || echo 'interaktif')"
echo

if [[ $ASSUME_YES -ne 1 ]]; then
  e1_warn "Silinecekler:"
  e1_warn "  * Tum e1-* container'lar"
  e1_warn "  * postgres-data, rabbitmq-data, nats-data, backup-data volume'lari"
  e1_warn "  * Tum telemetri, alarm, kullanici, gateway, sinyal verileri"
  [[ $KEEP_IMAGES -ne 1 ]] && e1_warn "  * Docker image'lari (e1-grid/* + eski e1/*)"
  [[ $PURGE_DIR -eq 1 ]]   && e1_warn "  * ${SCRIPT_DIR} dizini (.env DAHIL)"
  if e1_appliance_installed; then
    e1_warn "  * Appliance katmani: WiFi AP 'EnerjiOne Grid' + ag ajani (ayrica sorulacak)"
  fi
  echo
  if ! e1_confirm "Gercekten devam edilsin mi?"; then
    e1_info "Iptal edildi."
    exit 0
  fi
fi

APPLIANCE_PRESENT=0
if e1_appliance_installed; then
  APPLIANCE_PRESENT=1
  e1_set_steps 7
else
  e1_set_steps 6
fi

# ---- Appliance host katmani (varsa) --------------------------------------
# Ag ajani + WiFi AP + mDNS host'ta kalir; uygulama silinince bunlarin da
# temizlenmesi gerekir, aksi halde sifresiz bir AP yayinda kalir.
# Hostname ve netplan renderer degisikligi GERI ALINMAZ (sistem genelinde
# baska seyleri etkileyebilir) — kullaniciya nasil geri alacagi soylenir.
if [[ $APPLIANCE_PRESENT -eq 1 ]]; then
  e1_step "Appliance host katmani (ag ajani + WiFi AP)..."
  if [[ $ASSUME_YES -eq 1 ]] || e1_confirm "WiFi AP 'EnerjiOne Grid' ve ag ajani da kaldirilsin mi?"; then
    # ---- UZAKTAN BAKIM KAPISI ONCE KAPATILIR --------------------------------
    #
    # YASANAN RISK: temizlik yalnizca `e1-netd*` unit'lerini kaldiriyordu.
    # `e1-rad*` (uzaktan bakim) ve `e1-gwd*` (gateway ajani) unit'leri sistemde
    # KALIYORDU. `--purge-dir` ile ajan betigi de silindigi icin, suresi dolan
    # izni kapatacak HICBIR SEY kalmiyordu.
    #
    # Somut senaryo: musteri cihazi sahadan geri cekerken aktif bir uzaktan
    # bakim izni acik (kalkan inik + Tailscale SSH acik). `uninstall.sh --yes
    # --purge-dir` kosar. `e1-rad-report.timer` enabled kalir ama betik
    # silinmistir; birim her 30 sn'de 203/EXEC ile duser ve HICBIR kapanma
    # yapilmaz. Cihaz tailnet'e kayitli, KALKANI INIK ve root SSH acik halde
    # musterinin agina bagli kalir — uygulama silindigi icin arayuzden
    # "geri al" da yapilamaz.
    #
    # Sira onemli: once kapiyi kapat, sonra unit'leri kaldir. Ters sirada
    # kapatmayi yapacak betik/servis artik olmazdi.
    if [[ -x "$SCRIPT_DIR/infra/appliance/e1-rad.py" ]]; then
      e1_info "Uzaktan bakim izni kapatiliyor (kalkan kaldiriliyor, SSH kapatiliyor)..."
      "$SCRIPT_DIR/infra/appliance/e1-rad.py" close --reason uninstall >/dev/null 2>&1 \
        || python3 "$SCRIPT_DIR/infra/appliance/e1-rad.py" close --reason uninstall >/dev/null 2>&1 \
        || e1_warn "Uzaktan bakim kapatilamadi — asagidaki tailscale adimini MUTLAKA uygulayin."
    fi

    # Uc ajan ailesinin TAMAMI kaldirilir. Onceden yalnizca e1-netd
    # temizleniyordu; digerleri geride kalip 203/EXEC ile donup duruyordu.
    for unit in \
      e1-netd.path e1-netd-report.timer e1-netd.service e1-netd-report.service \
      e1-rad.path  e1-rad-report.timer  e1-rad.service  e1-rad-report.service \
      e1-gwd.path  e1-gwd-report.timer  e1-gwd.service  e1-gwd-report.service; do
      systemctl stop "$unit" 2>/dev/null || true
      systemctl disable "$unit" 2>/dev/null || true
      rm -f "/etc/systemd/system/${unit}"
    done
    systemctl daemon-reload 2>/dev/null || true

    # ---- Tailnet uyeligi ----------------------------------------------------
    # Kapi kapansa bile cihaz tailnet'e KAYITLI kalir. Sahadan cekilen bir
    # cihazin musterinin ozel agina dugum olarak asili kalmasi istenmez.
    # Karar kullaniciya birakilir: `--yes` ile calistirildiginda otomatik
    # cikilir (belgelenmis "full nuke" davranisi bunu bekler).
    if command -v tailscale >/dev/null 2>&1; then
      if [[ $ASSUME_YES -eq 1 ]] || e1_confirm "Cihaz Tailscale aginizdan (tailnet) da cikarilsin mi?"; then
        tailscale logout >/dev/null 2>&1 \
          && e1_ok "Tailnet uyeligi kaldirildi." \
          || e1_warn "tailscale logout basarisiz — yonetim panelinden dugumu elle silin."
      else
        e1_warn "Cihaz tailnet'te KAYITLI kaldi. Gerekirse yonetim panelinden silin."
      fi
    fi
    if command -v nmcli >/dev/null 2>&1; then
      nmcli connection down e1-grid-ap 2>/dev/null || true
      nmcli connection delete e1-grid-ap 2>/dev/null || true
    fi
    rm -f /etc/NetworkManager/dnsmasq-shared.d/e1-grid.conf
    rm -rf /var/lib/e1-grid
    e1_ok "Ag ajani, AP profili ve durum dizini kaldirildi."
    e1_info "Hostname (e1-grid) ve netplan renderer degisikligi KORUNDU."
    e1_info "  Hostname geri al : sudo hostnamectl set-hostname <eski-ad>"
    e1_info "  Netplan geri al  : sudo rm /etc/netplan/99-e1-grid-nm.yaml && sudo netplan apply"
    e1_info "  Netplan yedegi   : /var/backups/e1-grid-netplan-*"
  else
    e1_info "Appliance katmani korundu."
  fi
fi

# ---- 0/6: systemd unit (varsa) ------------------------------------------
e1_step "systemd servis kaydi (varsa)..."
if systemctl list-unit-files 2>/dev/null | grep -q '^enerjione-grid.service'; then
  systemctl stop enerjione-grid 2>/dev/null || true
  systemctl disable enerjione-grid 2>/dev/null || true
  rm -f /etc/systemd/system/enerjione-grid.service
  systemctl daemon-reload 2>/dev/null || true
  e1_ok "systemd unit kaldirildi."
else
  e1_ok "systemd unit kaydi yok."
fi

# ---- 1/5: Compose down -v ------------------------------------------------
e1_step "Compose stack durduruluyor ve volume'lar siliniyor..."
if [[ -f docker-compose.yml ]]; then
  docker compose down -v --remove-orphans 2>/dev/null || true
  e1_ok "Compose stack down + volume'lar silindi."
else
  e1_warn "docker-compose.yml bulunamadi, atlandi."
fi

# ---- 2/5: Image'lar ------------------------------------------------------
e1_step "Docker image'lari..."
if [[ $KEEP_IMAGES -ne 1 ]]; then
  # Eski 'e1/*' + yeni 'e1-grid/*' image namespace'lerini kapsa.
  # Solar 'e1-solar/*' kullanir; bu uninstall scripti onu silmemeli.
  OLD_IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
    | grep -E '^(e1/|e1-grid/)' \
    | grep -vE '^e1-solar/' || true)
  if [[ -n "$OLD_IMAGES" ]]; then
    echo "$OLD_IMAGES" | while read -r img; do
      docker rmi -f "$img" 2>/dev/null || true
    done
    e1_ok "Image'lar silindi (e1-grid/* + eski e1/*)."
  else
    e1_ok "Silinecek image yok."
  fi
else
  e1_info "Image'lar korundu (--keep-images)."
fi

# Dangling image temizligi (build cache'i ile birlikte sislenmis layer'lar)
docker image prune -f >/dev/null 2>&1 || true

# ---- 3/5: Orphan volume'lar ----------------------------------------------
# Eski compose proje adlariyla (enerjione_*, enerjione-grid_*) olusmus
# volume'lar — compose down -v genelde silmis olur ama eski proje adi
# degisikligi sonrasi takintilar kalabilir. Yeni proje adi 'enerjione-grid'
# (docker-compose.yml: `name: enerjione-grid`).
# NOT: Solar uygulamasi 'enerjione-solar_*' kullanir; bu pattern dahil
# edilmedi — Solar uninstall'i ayri bir scriptle yapilir.
e1_step "Orphan volume'lar taraniyor..."
ORPHAN_VOLS=$(docker volume ls --format '{{.Name}}' 2>/dev/null \
  | grep -E '^(enerjione_|enerjione-grid_)' \
  | grep -vE '^enerjione-solar' \
  | grep -E '(postgres-data|rabbitmq-data|nats-data|backup-data)$' || true)
if [[ -n "$ORPHAN_VOLS" ]]; then
  echo "$ORPHAN_VOLS" | while read -r vol; do
    docker volume rm "$vol" 2>/dev/null || true
  done
  e1_ok "Orphan volume'lar silindi."
else
  e1_ok "Orphan volume yok."
fi

# ---- 4/5: Network temizligi ----------------------------------------------
e1_step "Network temizligi..."
OLD_NETS=$(docker network ls --format '{{.Name}}' 2>/dev/null \
  | grep -E '^(enerjione_|enerjione-grid_)' \
  | grep -vE '^enerjione-solar' || true)
if [[ -n "$OLD_NETS" ]]; then
  echo "$OLD_NETS" | while read -r net; do
    docker network rm "$net" 2>/dev/null || true
  done
  e1_ok "Network'lar silindi."
else
  e1_ok "Silinecek network yok."
fi

# Dangling image temizligi
docker image prune -f >/dev/null 2>&1 || true

# ---- 5/5: Install dizinini sil (opsiyonel) -------------------------------
e1_step "Install dizini..."
if [[ $PURGE_DIR -eq 1 ]]; then
  PARENT_DIR="$(dirname "$SCRIPT_DIR")"
  DIR_NAME="$(basename "$SCRIPT_DIR")"
  e1_info "Dizin siliniyor: ${SCRIPT_DIR}"
  cd "$PARENT_DIR"
  rm -rf "$SCRIPT_DIR"
  e1_ok "${DIR_NAME} silindi."
  echo
  echo "${E1_GREEN}${E1_BOLD}EnerjiOne Grid tamamen kaldirildi.${E1_RESET}"
  echo "Yeniden kurmak icin:"
  echo "  TOKEN=ANAHTAR; curl -fsSL -H "Authorization: token $TOKEN" https://raw.githubusercontent.com/enerjione/enerjione-grid/main/install.sh | sudo E1_GHCR_TOKEN=$TOKEN bash"
else
  e1_info "Install dizini korundu: ${SCRIPT_DIR}"
  e1_info "Tamamen silmek icin: sudo rm -rf ${SCRIPT_DIR}"
  echo
  echo "${E1_GREEN}${E1_BOLD}EnerjiOne Grid kaldirildi (dizin disinda).${E1_RESET}"
  echo "Yeniden kurmak icin: cd ${SCRIPT_DIR} && sudo bash install.sh"
fi
echo
