#!/usr/bin/env bash
# `set -o pipefail` + `| head` = ZAMANLAMAYA BAGLI CIKIS KODU 2
#
# YASANAN ARIZA
# -------------
# `head -N` boru hattini N. satirda KAPATIR. Onundeki komut hala yaziyorsa
# SIGPIPE alir, "write error: Broken pipe" ile 2 doner ve `set -o pipefail`
# altinda TUM SCRIPT duser.
#
# Ariza ZAMANLAMAYA BAGLI: uretici N satiri yazmadan once biterse hic
# olusmaz. Yerelde tekrarlanamaz, CI'da bazen yesil bazen kirmizi olur.
#
# v2.38.0'in yayin isi tam olarak boyle dustu: TUM imajlar yayinlandi, VDS
# deploy gecti, yalnizca "Debian paketini uret" adimi `dpkg-deb --contents |
# awk | grep | head -25` satirinda 2 dondu ve GitHub Release olusmadi.
#
# KAPSAM — BILEREK DAR
# --------------------
# Once genel bir tarayici yazildi ve 9 YANLIS POZITIF verdi: cok satirli
# `$(...)`, `sh -c "..."` icindeki ic boru hatlari ve duz metin icinde gecen
# `| head -3`. Gurultulu bir test ilk sinirlenen kisi tarafindan kapatilir;
# yanlis guven uretir. Bu yuzden test YALNIZCA duzeltilmis somut satirlarin
# geri donmedigini dogruluyor ve genel bir dedektor OLDUGUNU IDDIA ETMIYOR.
set -euo pipefail

KOK="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
cd "$KOK"

gecti=0
basarisiz=0
_yok() {  # dosya, desen, aciklama
  if grep -qE "$2" "$1"; then
    echo "  X $3" >&2
    echo "      $1: $(grep -nE "$2" "$1" | head -1)" >&2
    basarisiz=$((basarisiz + 1))
  else
    gecti=$((gecti + 1))
  fi
}

# 1. Paket icerik ozeti — v2.38.0'i dusuren satir.
_yok packaging/build-deb.sh '\| *grep .*\| *head' \
  "build-deb.sh icerik ozeti yine 'grep | head' kullaniyor (CI'i dusuren satir)"

# 2. uninstall.sh --help
_yok uninstall.sh "grep '\^#'.*\| *head" \
  "uninstall.sh --help yine 'grep | head' kullaniyor"

# 3. install.sh tag listesi — HATA MESAJI icinde, yani ariza aninda ikinci
#    bir ariza uretirdi.
_yok install.sh "git tag -l.*\| *head" \
  "install.sh tag listesi yine '| head' kullaniyor"

# Duzeltmelerin CALISTIGINI da dogrula (desenin yoklugu yetmez).
if bash -n packaging/build-deb.sh && bash -n uninstall.sh && bash -n install.sh; then
  gecti=$((gecti + 1))
else
  echo "  X duzeltilen dosyalarda sozdizimi hatasi" >&2
  basarisiz=$((basarisiz + 1))
fi

echo "test-pipefail-head: ${gecti} gecti, ${basarisiz} basarisiz"
[[ "$basarisiz" -eq 0 ]]
