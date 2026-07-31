#!/usr/bin/env bash
# ===========================================================================
# Kurulum dosyasi uretici (provisioner generator)
# ===========================================================================
# Sunucuya ELDEN yuklenecek TEK dosyayi uretir. Uretilen dosya:
#   1. Anahtarlari kalici yere yazar (/etc/enerjione-grid/install.env)
#   2. install.sh'i private depodan ceker
#   3. Kurulumu calistirir
#
# Kullanim (kendi makinenizde):
#   bash packaging/make-provisioner.sh
#   bash packaging/make-provisioner.sh --out /tmp/musteri-a-kurulum.sh
#
# Anahtarlar sorulur; ortam degiskeniyle de verilebilir:
#   E1_GHCR_TOKEN=...  E1_TAILSCALE_AUTHKEY=...  bash packaging/make-provisioner.sh
#
# ---------------------------------------------------------------------------
# NEDEN BOYLE
# ---------------------------------------------------------------------------
# Anahtar URETILEN dosyada durur, DEPODA DURMAZ. Bu dosya .gitignore'da ve
# uretici onu asla commit etmez. Boylece:
#   - depoya erisen biri anahtarlari goremez
#   - anahtar degistirmek = dosyayi yeniden uretmek (depo gecmisi kirlenmez)
#   - her musteri/saha icin ayri anahtarli dosya uretilebilir
#
# Bir donem anahtari repoda tutan bir "fabrika dosyasi" tasarimi vardi;
# kaldirildi. Depoda hicbir canli anahtar durmuyor — tek kaynak burasi.
# ===========================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT="dist/enerjione-grid-kurulum.sh"
REPO_SLUG="${E1_REPO_SLUG:-enerjione/enerjione-grid}"
REF="${E1_BOOTSTRAP_REF:-main}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)  OUT="$2"; shift 2 ;;
    --ref)  REF="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Bilinmeyen secenek: $1" >&2; exit 1 ;;
  esac
done

# --- Anahtarlari topla -----------------------------------------------------
# `read -s`: ekrana yazilmaz. Terminal yoksa (CI) ortam degiskeni sart.
GHCR="${E1_GHCR_TOKEN:-}"
if [[ -z "$GHCR" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "GitHub kurulum anahtari (ghp_/github_pat_/gho_): " GHCR; echo
  fi
fi
[[ -n "$GHCR" ]] || { echo "HATA: GitHub anahtari verilmedi (E1_GHCR_TOKEN)." >&2; exit 1; }

TS_KEY="${E1_TAILSCALE_AUTHKEY:-}"
if [[ -z "$TS_KEY" && -t 0 ]]; then
  read -r -s -p "Tailscale anahtari (bos birakilabilir): " TS_KEY; echo
fi

TS_TAGS="${E1_TAILSCALE_TAGS:-tag:e1-appliance}"

mkdir -p "$(dirname "$OUT")"

# --- Uretilen dosya --------------------------------------------------------
# Heredoc SINIRLAYICISI TIRNAKLI ('EOS'): govde oldugu gibi yazilir, sadece
# asagida acikca yaptigimiz degistirmeler uygulanir. Tirnaksiz olsaydi
# govdedeki her $ ve ` uretim aninda cozulur, script bozulurdu.
cat > "$OUT" <<'EOS'
#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — sunucu kurulum dosyasi
# ===========================================================================
# BU DOSYA GIZLIDIR: icinde canli kurulum anahtarlari vardir.
# Paylasmayin, depoya koymayin, e-posta ile gondermeyin.
#
# Kullanim (hedef sunucuda):
#   sudo bash enerjione-grid-kurulum.sh
#
# Secenekler:
#   --wipe      kurulum bittikten sonra BU DOSYAYI sil
#   ...         diger tum argumanlar install.sh'e aktarilir
#                (or. ASSUME_YES=1 yerine  --  bkz. install.sh basligi)
# ===========================================================================
set -euo pipefail

# --- Anahtarlar (uretim aninda dolduruldu) ---------------------------------
E1_GHCR_TOKEN='@@GHCR@@'
E1_TAILSCALE_AUTHKEY='@@TSKEY@@'
E1_TAILSCALE_TAGS='@@TSTAGS@@'
E1_REPO_SLUG='@@SLUG@@'
E1_REF_BOOTSTRAP='@@REF@@'

WIPE=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --wipe) WIPE=1 ;;
    *) ARGS+=("$a") ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Bu dosya root ile calistirilmali:  sudo bash $0" >&2
  exit 1
fi

echo
echo "  EnerjiOne Grid — kurulum hazirlaniyor"
echo

# --- 1) Anahtarlari kalici yere yaz ---------------------------------------
# /etc/... altinda durur: repo disinda, kurulum dizini silinse bile kalir ve
# sonraki `update.sh` calistirmalarinda tekrar sorulmaz.
# umask 077: dosya olusurken bile baska kullanici okuyamaz.
install -d -m 700 /etc/enerjione-grid
( umask 077
  {
    echo "# EnerjiOne Grid kurulum anahtarlari — GIZLI"
    echo "# Uretildigi tarih: $(date -Is 2>/dev/null || date)"
    echo "E1_GHCR_TOKEN=${E1_GHCR_TOKEN}"
    [[ -n "$E1_TAILSCALE_AUTHKEY" ]] && echo "E1_TAILSCALE_AUTHKEY=${E1_TAILSCALE_AUTHKEY}"
    [[ -n "$E1_TAILSCALE_TAGS" ]]    && echo "E1_TAILSCALE_TAGS=${E1_TAILSCALE_TAGS}"
  } > /etc/enerjione-grid/install.env
)
chmod 600 /etc/enerjione-grid/install.env
echo "  ✓ Anahtarlar yazildi: /etc/enerjione-grid/install.env (chmod 600)"

# --- 2) Kurulum scriptini private depodan cek ------------------------------
# curl yoksa apt ile kur. DIKKAT: `apt-get update` cikis kodu OLUMCUL DEGIL.
#
# SAHA VAKASI (Dell OEM Ubuntu 24.04): makinede birinin kurdugu Google Chrome
# deposu duruyordu ama imza anahtari yoktu:
#     Err:1 https://dl.google.com/linux/chrome/deb stable InRelease
#       NO_PUBKEY FD533C07C264648F
#     E: The repository '...' is not signed.
# apt 100 dondu ve TUM kurulum orada oldu — oysa ihtiyacimiz olan depolarin
# (archive.ubuntu.com, security.ubuntu.com) hepsi basariliydi ve curl pekala
# kurulabilirdi.
#
# Ubuntu 24.04 eski apt-key/trusted.gpg anahtar deposunu kullanmadigi icin eski
# yontemle eklenmis her ucuncu taraf deposu bu hatayi verir. Musteri
# makinesinde alakasiz bir deponun kurulumu bloke etmesi kabul edilemez.
#
# Karar `apt-get install`'a birakiliyor: paket gercekten kurulamiyorsa ORADA
# duruyoruz ve operatore bozuk depoyu adiyla soyluyoruz.
if ! command -v curl >/dev/null 2>&1; then
  echo "  · curl kuruluyor..."
  APT_LOG="$(mktemp)"
  DEBIAN_FRONTEND=noninteractive apt-get update -q >"$APT_LOG" 2>&1 || true
  APT_BROKEN="$(grep -E '^(Err|E|W): ' "$APT_LOG" 2>/dev/null | head -8 || true)"
  rm -f "$APT_LOG"
  if [[ -n "$APT_BROKEN" ]]; then
    echo "  ! apt-get update kismen basarisiz — su depolar atlandi:" >&2
    printf '%s\n' "$APT_BROKEN" | sed 's/^/        /' >&2
    echo "    Bu depolar EnerjiOne icin gerekli DEGIL; kuruluma devam ediliyor." >&2
  fi
  if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -q curl ca-certificates; then
    echo >&2
    echo "  ✗ curl kurulamadi." >&2
    if [[ -n "$APT_BROKEN" ]]; then
      echo "    Yukaridaki bozuk depo(lar) sebep olabilir. Devre disi birakip tekrar deneyin:" >&2
      echo "      sudo mv /etc/apt/sources.list.d/<depo>.list{,.disabled}" >&2
      echo "      sudo apt-get update" >&2
    fi
    exit 1
  fi
fi

TMP_INSTALL="$(mktemp)"
trap 'rm -f "$TMP_INSTALL"' EXIT

URL="https://raw.githubusercontent.com/${E1_REPO_SLUG}/${E1_REF_BOOTSTRAP}/install.sh"
echo "  · Kurulum scripti indiriliyor..."
if ! curl -fsSL -H "Authorization: token ${E1_GHCR_TOKEN}" "$URL" -o "$TMP_INSTALL" \
   || [[ ! -s "$TMP_INSTALL" ]]; then
  echo >&2
  echo "  ✗ Kurulum scripti indirilemedi." >&2
  echo "    Anahtarin suresi dolmus veya 'Contents: Read' yetkisi olmayabilir." >&2
  echo "    Adres: $URL" >&2
  exit 1
fi
echo "  ✓ Indirildi"
echo

# --- 3) Kurulumu calistir --------------------------------------------------
# Anahtarlar ortam degiskeni olarak da geciyor: install.sh once ortama,
# sonra /etc/enerjione-grid/install.env'e bakar — ikisi de hazir.
export E1_GHCR_TOKEN E1_TAILSCALE_AUTHKEY E1_TAILSCALE_TAGS
bash "$TMP_INSTALL" ${ARGS+"${ARGS[@]}"}
rc=$?

if [[ $WIPE -eq 1 ]]; then
  # NOT: silmek koruma SAGLAMAZ (disk uzerinden kurtarilabilir); yalnizca
  # dosyanin sunucuda unutulmasini onler. Asil koruma dosyayi hic
  # paylasmamaktir. shred yoksa rm'e duseriz.
  SELF="$(readlink -f "$0" 2>/dev/null || echo "$0")"
  if command -v shred >/dev/null 2>&1; then shred -u "$SELF" 2>/dev/null || rm -f "$SELF"
  else rm -f "$SELF"; fi
  echo
  echo "  · Kurulum dosyasi silindi: $SELF"
fi

exit $rc
EOS

# --- Yer tutuculari doldur -------------------------------------------------
# `|` ayraci: anahtarlarda `/` bulunabilir. Anahtarlarda `|` ve `&` yok
# (GitHub ve Tailscale anahtarlari alfanumerik + `-` + `_`), yine de `&`
# escape ediliyor cunku sed'de "eslesenin tamami" anlamina gelir.
_esc() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }
sed -i \
  -e "s|@@GHCR@@|$(_esc "$GHCR")|" \
  -e "s|@@TSKEY@@|$(_esc "$TS_KEY")|" \
  -e "s|@@TSTAGS@@|$(_esc "$TS_TAGS")|" \
  -e "s|@@SLUG@@|$(_esc "$REPO_SLUG")|" \
  -e "s|@@REF@@|$(_esc "$REF")|" \
  "$OUT"

chmod 600 "$OUT"
# Windows/NTFS uzerinde chmod tutmaz. Sessizce gecmek yerine soyleyelim:
# dosyada canli anahtar var, kullanici bunu bilerek tasimali.
_MODE="$(stat -c %a "$OUT" 2>/dev/null || echo '?')"
if [[ "$_MODE" != "600" ]]; then
  echo "UYARI: dosya izni ${_MODE} (600 olamadi — Windows/NTFS olabilir)." >&2
  echo "       Icinde canli anahtar var; kopyalarken dikkatli olun." >&2
fi

# Uretilen dosya calisir durumda mi? Bozuk bir dosyayi sunucuda kesfetmek
# yerine burada yakalayalim.
bash -n "$OUT" || { echo "HATA: uretilen dosya sozdizimi hatali." >&2; exit 1; }

# Yer tutucu kaldi mi? (anahtar bos gecilmisse @@...@@ kalirdi)
if grep -q '@@[A-Z]*@@' "$OUT"; then
  echo "HATA: uretilen dosyada doldurulmamis yer tutucu var." >&2
  grep -n '@@[A-Z]*@@' "$OUT" >&2
  exit 1
fi

echo
echo "TAMAM: $OUT  (chmod 600)"
echo
echo "  Depo    : ${REPO_SLUG} @ ${REF}"
echo "  Tailscale: $([[ -n "$TS_KEY" ]] && echo "anahtar gomuldu (${TS_TAGS})" || echo "yok — VPN adimi calismaz")"
echo
echo "Sunucuya yukleyip calistirin:"
echo "  scp $OUT kullanici@sunucu:~/"
echo "  ssh kullanici@sunucu"
echo "  sudo bash enerjione-grid-kurulum.sh"
echo
echo "BU DOSYA GIZLIDIR — depoya koymayin, paylasmayin."
