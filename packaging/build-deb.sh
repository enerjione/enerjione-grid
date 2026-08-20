#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Debian paketi uret
# ===========================================================================
#   bash packaging/build-deb.sh              # VERSION dosyasindaki surum
#   bash packaging/build-deb.sh 2.25.0       # acik surum
#
# Cikti: dist/enerjione-grid_<surum>_all.deb
#
# NE GIRER, NE GIRMEZ
# -------------------
# Paket UYGULAMA KAYNAK KODU ICERMEZ. Musteriye giden sey yalnizca dagitim
# katmanidir: compose tanimi, altyapi konfigurasyonu, kurulum/guncelleme
# scriptleri, systemd unit'i ve yonetim komutu. Servis kodu ghcr.io'daki
# imajlarin icindedir.
#
# apps/ dizini bilerek DISARIDA. Bir gun ihtiyac olursa bile buraya
# eklenmemeli — paketin varlik sebebi musteriye depo/ kaynak vermemek.
# ===========================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="${1:-$(tr -d ' \t\r\n' < VERSION)}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "HATA: surum semver degil: '$VERSION'" >&2
  exit 1
fi

PKG="enerjione-grid"
STAGE="$(mktemp -d)"
OUT_DIR="$ROOT/dist"
DEB="$OUT_DIR/${PKG}_${VERSION}_all.deb"

# mktemp dizini her cikista temizlensin — basarisiz build artik birakmasin.
trap 'rm -rf "$STAGE"' EXIT

echo "==> Paket iskeleti hazirlaniyor (surum ${VERSION})"
APP="$STAGE/opt/enerjione-grid"
mkdir -p "$APP" \
         "$STAGE/DEBIAN" \
         "$STAGE/usr/bin" \
         "$STAGE/lib/systemd/system" \
         "$STAGE/usr/share/doc/$PKG"

# --- Dagitim katmani -------------------------------------------------------
cp docker-compose.yml   "$APP/"
cp .env.example         "$APP/"
cp VERSION              "$APP/"
cp install.sh update.sh uninstall.sh "$APP/"

mkdir -p "$APP/infra"
# NOT: infra/ altindan yalnizca CALISMA ZAMANINDA gereken parcalar.
# host-nginx ve appliance saha cihazinda kullaniliyor, birlikte gidiyor.
cp -r infra/scripts   "$APP/infra/"
cp -r infra/systemd   "$APP/infra/"
cp -r infra/nats      "$APP/infra/"
# rabbitmq/: docker-compose.yml bu dizini BIND MOUNT ediyor
# (rabbitmq.conf -> conf.d/20-e1-disk.conf). Pakete girmezse mount
# kaynagi saha cihazinda YOKTUR; Docker eksik yolu dizin olarak yaratir
# ve RabbitMQ'nun urun farkindali disk esigi devreye girmez — Disk
# Guardian korumasi sessizce olur. Repo checkout'unda dosya var oldugu
# icin CI'nin "compose config" adimi bunu goremez; kilidi
# tests/test_deb_bind_mount_kaynaklari.py tutuyor.
cp -r infra/rabbitmq  "$APP/infra/"
[ -d infra/appliance ]  && cp -r infra/appliance  "$APP/infra/"
[ -d infra/host-nginx ] && cp -r infra/host-nginx "$APP/infra/"

# Render edilmis NATS conf ASLA pakete girmez: icinde o kurulumun bcrypt
# hash'leri var. Her kurulum kendi sifreleriyle kendi conf'unu uretir.
rm -f "$APP/infra/nats/nats-server.conf"

# Derlenmis Python artefaktlari ve yerel gecici dosyalar.
find "$APP" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP" -name '*.pyc' -delete 2>/dev/null || true

# Sahada okunacak dokumanlar.
mkdir -p "$APP/docs"
for d in SAHA-KURULUM.md DEPLOYMENT.md APPLIANCE.md PAKET.md TAILSCALE.md; do
  [ -f "docs/$d" ] && cp "docs/$d" "$APP/docs/"
done
cp README.md "$STAGE/usr/share/doc/$PKG/" 2>/dev/null || true

# --- Yonetim komutu + systemd ---------------------------------------------
install -m 0755 packaging/bin/enerjione-grid "$STAGE/usr/bin/enerjione-grid"
install -m 0644 infra/systemd/enerjione-grid.service \
                "$STAGE/lib/systemd/system/enerjione-grid.service"

# --- DEBIAN kontrol dosyalari ---------------------------------------------
sed "s/__VERSION__/${VERSION}/g" packaging/debian/control > "$STAGE/DEBIAN/control"
for s in postinst prerm postrm; do
  sed "s/__VERSION__/${VERSION}/g" "packaging/debian/$s" > "$STAGE/DEBIAN/$s"
  chmod 0755 "$STAGE/DEBIAN/$s"
done

# conffiles: .env.example paketle gelir ama kullanicinin .env'i ASLA
# paket tarafindan yonetilmez (postinst yalnizca yoksa uretir).
# docker-compose.yml'i conffile YAPMIYORUZ: surumle birlikte degismesi
# gereken bir dosya, dpkg her yukseltmede "degistirdiniz mi" diye sormamali.

echo "==> Boyut ve izinler"
# Installed-Size (KB) — apt bunu disk planlamasi icin gosterir.
SIZE_KB="$(du -sk "$STAGE" | cut -f1)"
printf 'Installed-Size: %s\n' "$SIZE_KB" >> "$STAGE/DEBIAN/control"
# Dizinler 755, dosyalar 644; calistirilabilirler ayrica set edildi.
find "$STAGE/opt" -type d -exec chmod 755 {} +
find "$STAGE/opt" -type f -exec chmod 644 {} +
chmod 755 "$APP"/*.sh "$APP"/infra/scripts/linux/*.sh 2>/dev/null || true
[ -d "$APP/infra/appliance" ] && chmod 755 "$APP"/infra/appliance/*.sh 2>/dev/null || true

echo "==> dpkg-deb ile paketleniyor"
mkdir -p "$OUT_DIR"
# --root-owner-group: build makinesinin uid/gid'i pakete sizmasin
# (aksi halde dosyalar 1000:1000 sahipli kurulur).
dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null

echo "==> Dogrulama"
dpkg-deb --info "$DEB" | sed 's/^/    /'
echo "    --- icerik ozeti ---"
# BORU HATTI ERKEN KAPANMAMALI.
#
# `head -N` (ya da `awk ... exit`) boru hattini N. satirda kapatir;
# onundeki komut hala yaziyorsa SIGPIPE alir ve `set -o pipefail`
# altinda TUM SCRIPT duser.
#
# BU HATA IKI KEZ YAPILDI:
#   1. `... | grep | head -25`  -> `grep: write error: Broken pipe`
#      (v2.38.0'in yayin isi burada dustu; imajlar yayinlanmisti)
#   2. `... | awk '{...; if (++n == 25) exit}'` -> ayni tuzak, bir adim
#      geride: bu kez uretici `dpkg-deb`in tar alt sureci SIGPIPE aldi
#      (`dpkg-deb: error: tar subprocess returned error exit status 2`)
#
# Kirpmayi ERKEN CIKISLA yapmak cozüm degil; sorun `head` degil, BORUYU
# KAPATMAK. Dogru cozum: uretici bitene kadar OKUMAYA DEVAM ET, yalnizca
# yazdirmayi sinirla. Ozet birkac yuz satirlik bir listede kosuyor,
# tamamini okumanin maliyeti yok.
dpkg-deb --contents "$DEB" | awk '$6 ~ /^\.\/(opt|usr|lib)/ && ++n <= 25 { print "    " $6 }'

# Kaynak kod sizintisi kontrolu — paketin varlik sebebi bu.
if dpkg-deb --contents "$DEB" | grep -qE '/(apps|node_modules)/'; then
  echo "HATA: pakete uygulama kaynagi sizmis!" >&2
  exit 1
fi
echo "    kaynak kod sizintisi: yok"

if command -v lintian >/dev/null 2>&1; then
  echo "==> lintian"
  lintian --no-tag-display-limit "$DEB" 2>&1 | sed 's/^/    /' || true
fi

echo
echo "TAMAM: $DEB  ($(du -h "$DEB" | cut -f1))"
echo
echo "Kurulum:"
echo "  sudo apt install ./$(basename "$DEB")"
echo "  sudo enerjione-grid setup"
