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
#   sudo bash uninstall.sh --yes --purge-dir   # full nuke
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
for arg in "$@"; do
  case "$arg" in
    --yes|-y)        ASSUME_YES=1 ;;
    --keep-images)   KEEP_IMAGES=1 ;;
    --purge-dir)     PURGE_DIR=1 ;;
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
  echo
  if ! e1_confirm "Gercekten devam edilsin mi?"; then
    e1_info "Iptal edildi."
    exit 0
  fi
fi

e1_set_steps 6

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
  echo "  curl -fsSL https://raw.githubusercontent.com/fikretsafak/EnerjiOneGrid/docker-linux-deploy/install.sh | sudo bash"
else
  e1_info "Install dizini korundu: ${SCRIPT_DIR}"
  e1_info "Tamamen silmek icin: sudo rm -rf ${SCRIPT_DIR}"
  echo
  echo "${E1_GREEN}${E1_BOLD}EnerjiOne Grid kaldirildi (dizin disinda).${E1_RESET}"
  echo "Yeniden kurmak icin: cd ${SCRIPT_DIR} && sudo bash install.sh"
fi
echo
