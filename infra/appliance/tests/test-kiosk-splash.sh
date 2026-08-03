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
  file://*/enerjione-grid/kiosk-splash.html#*) _s=ev ;;
  *usr-local-share*)                           _s=usr-local ;;
  *)                                           _s="$SONUC" ;;
esac
_kontrol "uygulama kapaliyken splash EV dizininden acilmali" "$_s" "ev"

# --- 2b. Hedef adres FRAGMENT ile tasinmali ---------------------------------
# Splash dosyaya GOMULU adresle acilirsa, port `.env` uzerinden degistiginde
# gomulu deger bayatlar: yoklama hicbir zaman tutmaz ve ekran sonsuza kadar
# acilis ekraninda kalir ("surekli splash ekranda bekliyor"). Fragment her
# oturumda o anki adresi tasir.
_kontrol "splash hedefi adresi fragment ile tasimali"   "${SONUC#*#}" "u=http://localhost/&c=&v="

# --- 2c. Musteri adi ve surum de fragment ile gecmeli -----------------------
# Splash uygulama AYAGA KALKMADAN once gosterilir; o an veritabanina
# ulasilamaz. Bu yuzden ekranda gosterilecek musteri adi ve surum, oturum
# betiginin cozdugu degerlerden fragment ile tasinir.
#
# Kacis onemli: musteri adi bosluk icerebilir ("Turkiye Petrolleri").
# Kacisilmazsa fragment parametreleri birbirine karisir ve surum alani bozulur.
FRAG="$(
  set +u
  E1_SPLASH=""
  URL="http://localhost:8080/"
  E1_CUSTOMER_NAME="Turkiye Petrolleri A&B"
  E1_APP_VERSION="2.38.9"
  # shellcheck source=/dev/null
  . "$PARCA"
  _splash_frag
)"
_kontrol "musteri adi ve surum fragment'e kodlanmali"   "$FRAG" "u=http://localhost:8080/&c=Turkiye%20Petrolleri%20A%26B&v=2.38.9"

# Deger yoksa alan BOS kalmali — uydurulmamali.
FRAG2="$(
  set +u
  E1_SPLASH=""
  URL="http://localhost/"
  E1_CUSTOMER_NAME=""
  E1_APP_VERSION=""
  # shellcheck source=/dev/null
  . "$PARCA"
  _splash_frag
)"
_kontrol "deger yoksa alan bos kalmali" "$FRAG2" "u=http://localhost/&c=&v="

# --- 3. Hedef dizin GIZLI OLMAMALI ------------------------------------------
# snap'in `home` arayuzu $HOME altindaki nokta ile baslayan yollari engeller;
# `.e1-kiosk` gibi bir dizine koymak ayni hatayi aynen tekrarlardi.
_YOL="${SONUC#file://}"; _YOL="${_YOL%%#*}"
case "$_YOL" in
  */.*) _g=gizli ;;
  *)    _g=acik ;;
esac
_kontrol "splash gizli dizine konmamali (snap home arayuzu engeller)" "$_g" "acik"

# --- 4. Logo da yaninda olmali ----------------------------------------------
# Sayfa <img src="kiosk-logo.png"> ile GORECELI istiyor; yalniz html
# kopyalanirsa ekranda kirik gorsel cikar.
EV_DIZIN="$(dirname "$_YOL")"
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
  file://*usr-local-share/kiosk-splash.html#*) _y=paylasim ;;
  *)                                           _y="$SONUC6" ;;
esac
_kontrol "ev yazilamazsa paylasim dizini yedek olmali" "$_y" "paylasim"

# ===========================================================================
# ADRES COZUMU — arayuz 80'de OLMAYABILIR
# ===========================================================================
# YASANAN ARIZA: kiosk adresi `http://localhost/` olarak gomuluydu. Host'un
# 80 portu host-nginx'te oldugunda kurulum arayuzu FRONTEND_HTTP_PORT=8080'e
# alir; splash'in yoklamasi o zaman HICBIR ZAMAN tutmaz, yonlendirme
# tetiklenmez ve operator acilis ekraninda sonsuza kadar bekler.
#
# Provizyon aninda cozmek yetmezdi: port sonraki bir guncellemede degisirse
# kiosk yeniden kurulmadan bozulurdu. Bu yuzden her oturumda okunuyor.
COZ="${TMP}/coz.sh"
awk '
  /^_port_cikar\(\) \{$/ { icerde=1 }
  icerde { print }
  /^_log "kiosk adresi: \$URL"$/ { if (icerde) exit }
' "$KURULUM" > "$COZ"
grep -q 'FRONTEND_HTTP_PORT' "$COZ" || {
  echo "  X adres cozum blogu cikarilamadi" >&2; exit 1; }

_adres() {  # $1=.env icerigi ("" ise dosya hic yok)  $2=E1_URL_EXPLICIT
  (
    set +e
    D="$(mktemp -d "${TMP}/env-XXXXXX")"
    [[ -n "$1" ]] && printf '%s\n' "$1" > "${D}/.env"
    E1_ENV_CANDIDATES="${D}/.env"
    E1_URL_DEFAULT="http://localhost/"
    E1_URL_EXPLICIT="$2"
    _log() { :; }
    # shellcheck source=/dev/null
    . "$COZ"
    printf '%s' "$URL"
  )
}

# Varsayilan (80) — adres degismemeli.
_kontrol "port 80 iken adres sade kalmali" \
  "$(_adres 'FRONTEND_HTTP_PORT=80' '')" "http://localhost/"

# ASIL VAKA: host'un 80'i host-nginx'te, arayuz 8080'de.
_kontrol "port 8080 ise adres o porta gitmeli" \
  "$(_adres 'FRONTEND_HTTP_PORT=8080' '')" "http://localhost:8080/"

# Bind adresli bicim: "127.0.0.1:8080". Port dogru cikarilmali.
_kontrol "bind adresli degerden port cikarilmali" \
  "$(_adres 'FRONTEND_HTTP_PORT=127.0.0.1:8080' '')" "http://localhost:8080/"

# `.env` yoksa (kiosk uygulamadan once kuruluyor) varsayilana dusulmeli.
_kontrol ".env yoksa varsayilan adres kullanilmali" \
  "$(_adres '' '')" "http://localhost/"

# Operator kurulumda adresi ACIKCA verdiyse `.env` onu EZMEMELI.
_kontrol "acik verilen adres .env ile ezilmemeli" \
  "$(_adres 'FRONTEND_HTTP_PORT=8080' '1')" "http://localhost/"

# Bozuk/yorumlu satir sayisal olmayan bir sey uretmemeli.
_kontrol "bozuk deger varsayilana dusmeli" \
  "$(_adres 'FRONTEND_HTTP_PORT=   # elle bozulmus' '')" "http://localhost/"

echo "test-kiosk-splash: ${gecti} gecti, ${basarisiz} basarisiz"
[[ "$basarisiz" -eq 0 ]]
