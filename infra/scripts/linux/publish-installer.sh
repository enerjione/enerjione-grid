#!/usr/bin/env bash
# ===========================================================================
# Kurulum scriptini ve surum manifestini web koküne yayinla
# ===========================================================================
# Amac: sahadaki kurulumcunun yazacagi komut
#
#     curl -fsSL https://enerjione.com/grid/install.sh | sudo bash
#
# Neden GitHub'a yonlendirme DEGIL de kopya:
#   Depo private oldugu icin raw.githubusercontent.com kimliksiz istemciye
#   404 doner — 302 yonlendirme calismaz. Ayrica kendi sunucumuzdan servis
#   etmek GitHub kesintisinden ve rate limit'ten bagimsizlastirir.
#
# Ne yayinlanir:
#   install.sh    kurulum scriptinin aynen kopyasi
#   version.json  yayinlanan surum manifesti. Backend'in UPDATE_CHECK_URL'i
#                 bunu okur: private repo oldugu icin GitHub Releases API
#                 kimliksiz calismaz, bu manifest onun yerini tutar.
#
# Kullanim (VDS'te, repo kokunde):
#   sudo bash infra/scripts/linux/publish-installer.sh
#
# update.sh bunu VDS'te otomatik cagirir (web koku varsa).
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/infra/scripts/linux/_lib.sh"

# Yayin dizini. nginx bunu /grid/ altinda servis eder.
WEBROOT="${E1_PUBLIC_WEBROOT:-/var/www/enerjione-grid-public}"

e1_require_root "$@"

VERSION="$(e1_version "$SCRIPT_DIR")"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  e1_die "Surum semver olarak okunamadi ('${VERSION}'). VERSION dosyasini kontrol edin."
fi

mkdir -p "$WEBROOT"

# --- install.sh -----------------------------------------------------------
# Once gecici dosyaya yaz, sonra tasi: yayin sirasinda birinin indirdigi
# script YARIM olmasin (mv ayni dosya sisteminde atomiktir).
install -m 0644 "$SCRIPT_DIR/install.sh" "$WEBROOT/.install.sh.new"
mv -f "$WEBROOT/.install.sh.new" "$WEBROOT/install.sh"

# --- _lib.sh --------------------------------------------------------------
# `curl | bash` modunda install.sh henuz repo klonlamadan _lib.sh'a ihtiyac
# duyar. Depo private oldugu icin bunu da raw.githubusercontent.com'dan
# cekemez; ayni adresten servis ediyoruz.
install -m 0644 "$SCRIPT_DIR/infra/scripts/linux/_lib.sh" "$WEBROOT/._lib.sh.new"
mv -f "$WEBROOT/._lib.sh.new" "$WEBROOT/_lib.sh"

# --- version.json ---------------------------------------------------------
# Backend version_service bu bicimi okur: "version" anahtari yeterli.
# release_notes adresi kullanicinin bakabilecegi yeri gosterir.
cat > "$WEBROOT/.version.json.new" <<JSON
{
  "version": "${VERSION}",
  "published_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "install_url": "https://enerjione.com/grid/install.sh",
  "notes_url": "https://github.com/${E1_REPO_SLUG}/releases/tag/v${VERSION}"
}
JSON
chmod 0644 "$WEBROOT/.version.json.new"
mv -f "$WEBROOT/.version.json.new" "$WEBROOT/version.json"

e1_ok "Yayinlandi: ${WEBROOT}"
e1_kv "Surum" "${VERSION}"
e1_kv "Kurulum" "curl -fsSL https://enerjione.com/grid/install.sh | sudo bash"
e1_kv "Manifest" "https://enerjione.com/grid/version.json"
