#!/usr/bin/env bash
# Kiosk gecis ekrani (splash) TARAYICI TARAFINDAN ACILABILIR OLMALI.
#
# YASANAN ARIZA
# -------------
# Operator ekraninda Firefox'un hata sayfasi cikti:
#
#     File not found
#     Firefox can't find the file at
#     /usr/local/share/enerjione-grid/kiosk-splash.html
#
# Dosya DISKTE VARDI. Oturum betigindeki `[ -f "$E1_SPLASH" ]` testi de dogru
# donuyordu — donmeseydi kod zaten dogrudan uygulama adresine giderdi. Sebep
# izin de degildi (0644/0755).
#
# Sebep PAKETLEME: Ubuntu'da Firefox bir **snap**tir. Snap sandbox'i `/usr/local`
# altini HIC gormez; `home` arayuzu yalnizca $HOME'un gizli olmayan kismina
# izin verir. Yani dosya kabuk icin VAR, tarayici icin YOK.
#
# TESTIN ASIL DEGERI
# ------------------
# Bu ariza sinifini `[ -f ]` ASLA yakalayamaz: test sandbox DISINDA kosar.
# Dolayisiyla "dosya yazildi mi" diye bakan bir test yesil kalirken ekran bos
# olurdu. Burada sinanan sey varlik degil, HEDEF YOLUN NEREYE ISARET ETTIGI.
#
# Kaynak metninde desen aramiyoruz: uretilen oturum betiginin `_hedef`
# fonksiyonu setup-kiosk.sh'den CIKARILIP GERCEKTEN KOSTURULUYOR. (Bu projede
# kaynak-grep eden bir test daha once davranis bozukken yesil kalmisti.)
set -euo pipefail

KOK="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
KURULUM="${KOK}/infra/appliance/setup-kiosk.sh"
[[ -f "$KURULUM" ]] || { echo "setup-kiosk.sh bulunamadi: $KURULUM" >&2; exit 1; }

gecti=0
basarisiz=0
_kontrol() {  # $1=ad $2=gercek $3=beklenen
  if [[ "$2" == "$3" ]]; then
    gecti=$((gecti + 1))
  else
    echo "  X $1" >&2
    echo "      beklenen: $3" >&2
    echo "      gercek  : $2" >&2
    basarisiz=$((basarisiz + 1))
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Oturum betiginin ilgili parcasini CIKAR -------------------------------
# Tirnakli heredoc govdesi (`<<'E1_SESSION_EOF'` ... `E1_SESSION_EOF`) aynen
# cihaza yazilan metindir. Sonundaki sonsuz dongu/exec bize lazim degil;
# `_hedef` tanimi bitince kesiyoruz.
PARCA="${TMP}/hedef.sh"
awk '
  /^[[:space:]]*cat <<'"'"'E1_SESSION_EOF'"'"'[[:space:]]*$/ { icerde=1; next }
  /^E1_SESSION_EOF[[:space:]]*$/                             { icerde=0 }
  icerde && basladi                                          { print }
  # Ilgi alani: splash kopyalama blogundan _hedef sonuna kadar.
  icerde && /^E1_SPLASH_LOCAL=/ { basladi=1; print }
  basladi && /^}[[:space:]]*$/ && gordum_hedef                { exit }
  /^_hedef\(\) \{/                                           { gordum_hedef=1 }
' "$KURULUM" > "$PARCA"

if ! grep -q '_hedef()' "$PARCA"; then
  echo "  X oturum betiginden _hedef cikarilamadi (setup-kiosk.sh yapisi degisti mi?)" >&2
  exit 1
fi

# --- Kosum yardimcisi -------------------------------------------------------
# $1=splash kaynagi var mi (1/0)  $2=uygulama ayakta mi (1/0)  $3=HOME yazilabilir mi (1/0)
_calistir() {
  local kaynak_var="$1" ayakta="$2" ev_yazilir="$3"
  (
    set +e
    # Her kosum KENDI paylasim dizinini alir: ortak dizin kullanildiginda
    # onceki kosumun yazdigi splash "kaynak yok" senaryosuna sizip testi
    # yaniltiyordu.
    KOSUM="${TMP}/k-${kaynak_var}${ayakta}${ev_yazilir}"
    SHARE="${KOSUM}/usr-local-share"
    mkdir -p "$SHARE"
    if [[ "$kaynak_var" == "1" ]]; then
      printf '<html>splash</html>' > "${SHARE}/kiosk-splash.html"
      printf 'PNG' > "${SHARE}/kiosk-logo.png"
    fi

    if [[ "$ev_yazilir" == "1" ]]; then
      HOME="${KOSUM}/ev"
      mkdir -p "$HOME"
    else
      # Yazilamayan ev. `chmod 0500` ile taklit EDILEMEZ: root icin izin
      # baglayici degil, Git Bash'te ise chmod zaten etkisiz — iki ortamda da
      # test sessizce yanlis dali olcerdi. Bunun yerine ev yolunu DUZ BIR
      # DOSYANIN altina koyuyoruz; `mkdir -p` her yerde kesin basarisiz olur.
      printf 'x' > "${KOSUM}/engel"
      HOME="${KOSUM}/engel/ev"
    fi
    export HOME

    E1_SPLASH="${SHARE}/kiosk-splash.html"
    URL="http://localhost/"
    _log() { :; }
    # `command -v curl` fonksiyonu da bulur; gercek agi hic ellemiyoruz.
    if [[ "$ayakta" == "1" ]]; then curl() { return 0; }; else curl() { return 1; }; fi

    # shellcheck source=/dev/null
    . "$PARCA"
    _hedef
  )
}

# --- 1. Uygulama ayakta: splash'a HIC girilmemeli ---------------------------
_kontrol "uygulama ayaktayken dogrudan adrese gidilmeli" \
  "$(_calistir 1 1 1)" "http://localhost/"

# --- 2. ASIL VAKA: uygulama kapali -> hedef EV ICINDE olmali ----------------
# `/usr/local` gosterirse snap tarayici acamaz; sahada gorulen hata budur.
SONUC="$(_calistir 1 0 1)"
case "$SONUC" in
  file://*/enerjione-grid/kiosk-splash.html) _s=ev ;;
  *usr/local*)                               _s=usr-local ;;
  *)                                         _s="$SONUC" ;;
esac
_kontrol "uygulama kapaliyken splash EV dizininden acilmali" "$_s" "ev"

# --- 3. Hedef dizin GIZLI OLMAMALI ------------------------------------------
# snap'in `home` arayuzu $HOME altindaki nokta ile baslayan yollari engeller;
# `.e1-kiosk` gibi bir dizine koymak ayni hatayi aynen tekrarlardi.
case "${SONUC#file://}" in
  */.*) _g=gizli ;;
  *)    _g=acik ;;
esac
_kontrol "splash gizli dizine konmamali (snap home arayuzu engeller)" "$_g" "acik"

# --- 4. Logo da yaninda olmali ----------------------------------------------
# Sayfa <img src="kiosk-logo.png"> ile GORECELI istiyor; yalniz html
# kopyalanirsa ekranda kirik gorsel cikar.
EV_DIZIN="$(dirname "${SONUC#file://}")"
if [[ -f "${EV_DIZIN}/kiosk-logo.png" ]]; then _l=var; else _l=yok; fi
_kontrol "logo splash'in yanina kopyalanmali" "$_l" "var"

# --- 5. Splash hic uretilememisse: file:// DENENMEMELI ----------------------
# Olmayan bir dosyayi acmak tarayicinin hata sayfasini getirir; o durumda
# dogrudan uygulama adresi daha iyidir (eski davranis korunuyor).
_kontrol "splash yokken dogrudan adrese dusmeli" \
  "$(_calistir 0 0 1)" "http://localhost/"

# --- 6. Ev yazilamiyorsa: paylasim dizini YEDEK kalmali ---------------------
# deb ile kurulu chromium `/usr/local` altini okuyabiliyor; snap yuzunden
# yaptigimiz degisiklik o cihazlarda splash'i KAYBETTIRMEMELI.
SONUC6="$(_calistir 1 0 0)"
case "$SONUC6" in
  file://*usr-local-share/kiosk-splash.html) _y=paylasim ;;
  *)                                         _y="$SONUC6" ;;
esac
_kontrol "ev yazilamazsa paylasim dizini yedek olmali" "$_y" "paylasim"

echo "test-kiosk-splash: ${gecti} gecti, ${basarisiz} basarisiz"
[[ "$basarisiz" -eq 0 ]]
