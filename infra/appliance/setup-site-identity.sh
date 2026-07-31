#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Saha kimligi (musteri / proje bilgisi)
# ===========================================================================
# Kurulumda BIR KEZ sorulur: bu kutu hangi musteride, hangi sahada?
#
# NEDEN GEREKLI:
#   Sistem hostname'i her cihazda ayni (`e1-grid`) — cunku `e1-grid.local`
#   sahada standart erisim adresidir ve site basina tek cihaz oldugu icin
#   yerel agda cakisma yok. Ama uzaktan bakim VPN'i (Tailscale) TEK bir isim
#   alanidir: ayni adla katilan cihazlar `e1-grid-1`, `e1-grid-2`... diye
#   numaralanir ve hangisinin hangi saha oldugu ANLASILMAZ olur.
#
#   Donanim seri numarasindan turetmeyi de deniyoruz (bkz. setup-tailscale.sh)
#   ama seri no her zaman guvenilir degil: sanal makinelerde yok, bazi
#   uretici BIOS'lari "To Be Filled By O.E.M." birakir ve rastgele bir seri
#   no zaten konsolda hangi saha oldugunu SOYLEMEZ. Kurulumcunun bildigi tek
#   dogru bilgi musteri/saha adidir; onu soruyoruz.
#
# CIKTI: /etc/enerjione-grid/site.env
#   E1_SITE_ID        slug (tailnet adi, dosya adlari icin)  -> tpao-batman-osb
#   E1_CUSTOMER_NAME  okunabilir musteri adi                 -> TPAO
#   E1_SITE_NAME      okunabilir saha adi                    -> Batman OSB
#   E1_SITE_UNIT      ayni sahada birden fazla kutu varsa    -> pano-2
#
#   Bu dosya repo DISINDADIR: yeniden kurulumda silinmez, bir kez doldurulur
#   ve sonraki kurulumlarda soru tekrar sorulmaz.
#
# Env (soru sormadan / imajdan kurulum icin):
#   E1_CUSTOMER    musteri adi     (verilirse o alan sorulmaz)
#   E1_SITE        saha/proje adi  (verilirse o alan sorulmaz)
#   E1_SITE_UNIT   cihaz/pano no   (opsiyonel)
#   E1_SITE_ID     hepsini atla, slug'i dogrudan ver
#   E1_SITE_FORCE=1  kayitli bilgi olsa bile yeniden sor
#
# Soru sorulamayan ortamda (ASSUME_YES=1, terminal yok) HICBIR SEY YAZMAZ ve
# 0 doner — kurulum bozulmaz, tailnet adi donanimdan turetilmeye devam eder.
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
  e1_hint() { printf '    %s\n' "$*"; }
  e1_step() { printf '\n== %s\n' "$*"; }
  # Soru sorma yardimcilari BILEREK YOK: bu script hicbir sey sormaz,
  # butun girdiler ortam degiskeni / install.env uzerinden gelir.
fi

SITE_ENV="${E1_SITE_ENV:-/etc/enerjione-grid/site.env}"

# --- Slug: Turkce metin -> DNS-guvenli ad ----------------------------------
# "TPAO Batman OSB" -> "tpao-batman-osb". Turkce harfler once ASCII
# karsiligina cevrilir; `tr` cok baytli karakterleri dogru isleyemedigi icin
# bu adim `sed` ile yapilmali.
_slug() {
  printf '%s' "$1" \
    | sed -e 's/Ç/C/g; s/ç/c/g; s/Ğ/G/g; s/ğ/g/g; s/İ/I/g; s/ı/i/g' \
          -e 's/Ö/O/g; s/ö/o/g; s/Ş/S/g; s/ş/s/g; s/Ü/U/g; s/ü/u/g' \
    | tr 'A-Z' 'a-z' \
    | tr -c 'a-z0-9' '-' \
    | sed -e 's/--*/-/g' -e 's/^-//' -e 's/-$//'
}

# --- Zaten kayitli mi? (idempotent) ----------------------------------------
_read_var() {  # $1=dosya  $2=anahtar
  # `source` etmiyoruz: dosyada kurulum ortamini bozabilecek baska satirlar
  # olabilir; sadece istenen anahtari cekiyoruz.
  #
  # Iki bicim: tirnakli ve tirnaksiz. Tirnakli once denenir ve icerik oldugu
  # gibi alinir — "Saha #1" gibi degerlerde '#' YORUM DEGILDIR. Kacirilmis
  # tirnaklar (\") en sonda geri acilir.
  [[ -f "$1" ]] || return 1
  sed -n \
    -e "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\"\(.*\)\"[[:space:]]*\$/\1/p" \
    -e "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\([^\"#][^#]*\).*\$/\1/p" \
    "$1" | tail -1 | sed -e 's/[[:space:]]*$//' -e 's/\\"/"/g'
}

EXISTING_ID="$(_read_var "$SITE_ENV" E1_SITE_ID || true)"
if [[ -n "$EXISTING_ID" && "${E1_SITE_FORCE:-0}" != "1" ]]; then
  EXIST_C="$(_read_var "$SITE_ENV" E1_CUSTOMER_NAME || true)"
  EXIST_S="$(_read_var "$SITE_ENV" E1_SITE_NAME || true)"
  e1_ok "Saha kimligi zaten kayitli: ${EXIST_C:-?} / ${EXIST_S:-?}  (${EXISTING_ID})"
  e1_hint "Degistirmek icin: sudo E1_SITE_FORCE=1 bash ${BASH_SOURCE[0]}"
  exit 0
fi

# Root kontrolu SORULARDAN ONCE: yazamayacaksak kurulumcuyu bos yere
# uc soruya cevap yazdirip cevabini cope atmayalim.
if [[ "$(id -u)" -ne 0 ]]; then
  e1_warn "Saha kimligi root gerektirir (${SITE_ENV}) — atlandi."
  e1_hint "Calistirmak icin: sudo bash ${BASH_SOURCE[0]}"
  exit 0
fi

# --- Bilgileri topla --------------------------------------------------------
CUSTOMER="${E1_CUSTOMER:-}"
SITE="${E1_SITE:-}"
UNIT="${E1_SITE_UNIT:-}"
SITE_ID="${E1_SITE_ID:-}"

# Golden image / otomasyon: bilgiler Tailscale anahtariyla ayni kalici
# dosyaya gomulmus olabilir. Ortam degiskeni daima oncelikli.
INSTALL_ENV="${E1_INSTALL_ENV:-/etc/enerjione-grid/install.env}"
if [[ -f "$INSTALL_ENV" ]]; then
  [[ -z "$CUSTOMER" ]] && CUSTOMER="$(_read_var "$INSTALL_ENV" E1_CUSTOMER || true)"
  [[ -z "$SITE" ]]     && SITE="$(_read_var "$INSTALL_ENV" E1_SITE || true)"
  [[ -z "$UNIT" ]]     && UNIT="$(_read_var "$INSTALL_ENV" E1_SITE_UNIT || true)"
  [[ -z "$SITE_ID" ]]  && SITE_ID="$(_read_var "$INSTALL_ENV" E1_SITE_ID || true)"
  [[ -n "$CUSTOMER$SITE$SITE_ID" ]] && e1_info "Saha bilgisi ${INSTALL_ENV} dosyasindan okundu."
fi

# SORU SORULMAZ. Saha bilgisi kurulum aracindan (EnerjiOne Kurulum Araci,
# ayri repo) veya
# ortam degiskeni / install.env uzerinden gelir. Hicbiri yoksa bu adim
# sessizce atlanir: tailnet adi donanimdan turetilir ve kurulum devam eder.
# Bilgi sonradan tanimlanabilir — bu script tek basina tekrar calistirilabilir.
if [[ -z "$SITE_ID" && -z "$CUSTOMER$SITE" ]]; then
  e1_info "Saha bilgisi verilmedi — tailnet adi donanimdan turetilecek."
  e1_hint "Sonra tanimlamak icin:"
  e1_hint "  sudo E1_CUSTOMER='TPAO' E1_SITE='Batman OSB' bash ${BASH_SOURCE[0]}"
  exit 0
fi

if [[ -z "$SITE_ID" ]]; then
  # Parcalari ayri ayri slug'la, sonra birlestir: aradaki bosluk/isaretler
  # tek tireye iner ve bos alanlar sessizce atlanir.
  PARTS=()
  for raw in "$CUSTOMER" "$SITE" "$UNIT"; do
    s="$(_slug "$raw")"
    [[ -n "$s" ]] && PARTS+=( "$s" )
  done
  SITE_ID="$(IFS='-'; printf '%s' "${PARTS[*]}")"
fi
SITE_ID="$(_slug "$SITE_ID")"
# Tailscale adi 63 karakteri gecemez; onek (`e1-grid-`) icin yer birakiyoruz.
SITE_ID="${SITE_ID:0:40}"
SITE_ID="${SITE_ID%-}"

if [[ -z "$SITE_ID" ]]; then
  e1_warn "Saha adi bos birakildi — tailnet adi donanimdan turetilecek."
  e1_hint "Sonra tanimlamak icin: sudo bash ${BASH_SOURCE[0]}"
  exit 0
fi

# --- Kaydet -----------------------------------------------------------------
mkdir -p "$(dirname "$SITE_ENV")"
# Cift tirnak icindeki tirnaklari kacir: musteri adinda " gecerse dosya
# bozulmasin.
_esc() { printf '%s' "$1" | sed 's/"/\\"/g'; }
cat > "$SITE_ENV" <<EOF
# EnerjiOne Grid — saha kimligi. install.sh tarafindan olusturuldu.
# Bu dosya kurulum/guncellemede SILINMEZ. Degistirmek icin:
#   sudo E1_SITE_FORCE=1 bash /opt/enerjione-grid/infra/appliance/setup-site-identity.sh
E1_SITE_ID="$(_esc "$SITE_ID")"
E1_CUSTOMER_NAME="$(_esc "$CUSTOMER")"
E1_SITE_NAME="$(_esc "$SITE")"
E1_SITE_UNIT="$(_esc "$UNIT")"
EOF
chmod 644 "$SITE_ENV"

e1_ok "Saha kimligi kaydedildi: ${SITE_ID}"
e1_info "Uzaktan bakim listesinde gorunecek ad: ${E1_TAILSCALE_HOSTNAME_PREFIX:-e1-grid}-${SITE_ID}"
exit 0
