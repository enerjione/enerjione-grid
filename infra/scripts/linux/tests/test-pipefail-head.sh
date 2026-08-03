#!/usr/bin/env bash
# BORU HATTINI ERKEN KAPATMA — `set -o pipefail` altinda olumcul.
#
# YASANAN ARIZA
# -------------
# `head -N` (ya da `awk ... exit`) boruyu N. satirda kapatir; onundeki komut
# hala yaziyorsa SIGPIPE alir (cikis 141) ve `pipefail` TUM SCRIPTI dusurur.
#
# v2.38.0: `dpkg-deb --contents | awk | grep | head -25`
#          -> "grep: write error: Broken pipe"
#          TUM imajlar yayinlanmisti; yalnizca GitHub Release olusmadi.
#
# v2.38.1: kirpma `awk ... exit`e tasindi -> AYNI TUZAK, bir adim geride:
#          bu kez ureticinin (`dpkg-deb`) tar alt sureci SIGPIPE aldi
#          -> "dpkg-deb: error: tar subprocess returned error exit status 2"
#
# DERS: sorun `head` DEGIL, BORUYU KAPATMAK. Kirpma, uretici bitene kadar
# okumaya devam ederek yapilmali.
#
# Bu test once davranisi URETIR (uc varyanti gercekten kosturur), sonra
# kaynakta duzeltilmis satirin yerinde durdugunu dogrular. Kaynak taramasi
# tek basina yetmiyordu: v2.38.1'de desen degismisti ama ariza duruyordu.
set -euo pipefail

KOK="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
cd "$KOK"

gecti=0
basarisiz=0

# --- 1. DAVRANIS: uc varyantin gercek cikis kodlari --------------------
_uret() { seq 1 60000 | sed 's|^|./opt/x/|'; }

_kod() { ( set -euo pipefail; eval "$1" >/dev/null 2>&1 ); echo $?; }

kod_head="$(_kod "_uret | grep -E '^\./opt' | head -25")"
kod_exit="$(_kod "_uret | awk '\$1 ~ /^\.\/opt/ { print; if (++n == 25) exit }'")"
kod_drain="$(_kod "_uret | awk '\$1 ~ /^\.\/opt/ && ++n <= 25 { print }'")"

# Tuzak GERCEKTEN var mi? (yoksa test hicbir sey kanitlamiyor demektir)
if [[ "$kod_head" != "0" && "$kod_exit" != "0" ]]; then
  gecti=$((gecti + 1))
else
  echo "  ! ortam SIGPIPE uretmedi (head=$kod_head exit=$kod_exit) — test bu makinede anlamsiz" >&2
  gecti=$((gecti + 1))   # ortam farki; basarisiz SAYMIYORUZ
fi

# Onerilen bicim HER ZAMAN 0 donmeli.
if [[ "$kod_drain" == "0" ]]; then
  gecti=$((gecti + 1))
else
  echo "  X onerilen bicim de dustu (kod $kod_drain) — cozum yanlis" >&2
  basarisiz=$((basarisiz + 1))
fi

# --- 2. KAYNAK: duzeltilen satirlar geri donmemis mi -------------------
_yok() {  # dosya, desen, aciklama
  if grep -qE "$2" "$1"; then
    echo "  X $3" >&2
    basarisiz=$((basarisiz + 1))
  else
    gecti=$((gecti + 1))
  fi
}

# Paket icerik ozeti: ne `head` ne de `awk ... exit`.
_yok packaging/build-deb.sh 'dpkg-deb --contents.*\| *(grep|awk).*\| *head' \
  "build-deb.sh yine '| head' kullaniyor (v2.38.0'i dusuren bicim)"
_yok packaging/build-deb.sh 'dpkg-deb --contents.*awk.*exit *\}' \
  "build-deb.sh yine 'awk ... exit' kullaniyor (v2.38.1'i dusuren bicim)"
_yok uninstall.sh "grep '\^#'.*\| *head" \
  "uninstall.sh --help yine '| head' kullaniyor"
_yok install.sh "git tag -l.*\| *head" \
  "install.sh tag listesi yine '| head' kullaniyor"

bash -n packaging/build-deb.sh && bash -n uninstall.sh && bash -n install.sh \
  && gecti=$((gecti + 1)) || { echo "  X sozdizimi hatasi" >&2; basarisiz=$((basarisiz + 1)); }

echo "test-pipefail-head: ${gecti} gecti, ${basarisiz} basarisiz"
[[ "$basarisiz" -eq 0 ]]
