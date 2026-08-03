#!/usr/bin/env bash
# Kurulum yarida kalirsa YAPTIKLARINI geri almali — ama YALNIZCA kendi
# yaptiklarini.
#
# NEDEN BU TEST VAR
# -----------------
# Yarim kalan kurulum cihazda en kotu durumu birakir: container'lar ayakta,
# `.env` uretilmis, systemd unit'i kurulmus ama sistem calismiyor. Kullanici
# tekrar denedigi hangi parcanin eski hangisinin yeni oldugunu bilemez.
#
# ASIL TEHLIKE TERS YONDE: temizlik fazla agresif olursa MUSTERI VERISINI
# (telemetri, olaylar, yedekler) siler. Mevcut bir kurulumun uzerine yapilan
# denemede `docker compose down -v` calistirmak, cozmeye calistigi sorundan
# kat kat pahali bir hataya donusurdu.
#
# Bu yuzden testin agirligi "sildi mi" degil "SILMEMESI GEREKENI SILMEDI MI"
# tarafinda.
set -euo pipefail

KOK="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
LIB="${KOK}/infra/scripts/linux/_lib.sh"

[[ -f "$LIB" ]] || { echo "_lib.sh bulunamadi: $LIB" >&2; exit 1; }

gecti=0
basarisiz=0
_kontrol() {
  if [[ "$2" == "$3" ]]; then
    gecti=$((gecti + 1))
  else
    echo "  X $1: beklenen='$3' gercek='$2'" >&2
    basarisiz=$((basarisiz + 1))
  fi
}

# shellcheck source=/dev/null
source "$LIB"

# --- 1. Kurulmadan (arm edilmeden) hicbir sey calismaz --------------------
# Onemli: script daha hicbir sey OLUSTURMADAN duserse (or. on-kosul hatasi)
# temizlik KOSMAMALI; ortada geri alinacak bir sey yok.
IZ=""
e1_rollback_disarm
e1_rollback_add "IZ='calisti'"
e1_rollback_run
_kontrol "arm edilmeden calismamali" "${IZ}" ""

# --- 2. Arm edilince TERS sirada calisir ----------------------------------
# Sira onemli: en son olusturulan once kaldirilmali (volume'den once
# container). Duz sirada kosarsa "kullanimda" hatalari alinir.
SIRA=""
e1_rollback_disarm
e1_rollback_arm
e1_rollback_add "SIRA=\"\${SIRA}1\""
e1_rollback_add "SIRA=\"\${SIRA}2\""
e1_rollback_add "SIRA=\"\${SIRA}3\""
e1_rollback_run
_kontrol "ters sirada calismali" "${SIRA}" "321"

# --- 3. Bir adim patlarsa DIGERLERI yine kosar ----------------------------
# Yarim geri alma, hic geri almamaktan iyidir.
KALAN=""
e1_rollback_disarm
e1_rollback_arm
e1_rollback_add "KALAN=\"\${KALAN}a\""
e1_rollback_add "false"
e1_rollback_add "KALAN=\"\${KALAN}b\""
e1_rollback_run
_kontrol "patlayan adim digerlerini durdurmamali" "${KALAN}" "ba"

# --- 4. Basarili kurulumda temizlik KOSMAZ --------------------------------
# `e1_rollback_disarm` cagrildiktan sonra (kurulum tamamlandi) hicbir sey
# geri alinmamali; aksi halde basarili kurulum kendi kendini silerdi.
IZ2=""
e1_rollback_arm
e1_rollback_add "IZ2='silindi'"
e1_rollback_disarm
e1_rollback_run
_kontrol "basarili kurulumda calismamali" "${IZ2}" ""

# --- 5a. YUKLEMIN KENDISI: "onceden bir sey var mi?" ----------------------
# 5a/5b yalnizca KARARI (hangi dal) sinar ve yuklemi taklit eder; yuklemin
# kendisi yanlissa ikisi de yesil kalirdi (denendi, mutasyon KACTI).
# Burada `docker` komutu taklit edilip yuklem gercekten kosturuluyor.

# Taklit: global degiskenlerden okur (eval + ic ice tirnak kirilgan olurdu).
_PS_CIKTI=""
_VOL_CIKTI=""
docker() {
  case "$*" in
    *"compose ps -aq"*) printf '%s' "$_PS_CIKTI" ;;
    *"volume ls -q"*)   printf '%s' "$_VOL_CIKTI" ;;
    *) return 0 ;;
  esac
}
_docker_stub() { _PS_CIKTI="$1"; _VOL_CIKTI="$2"; }

_docker_stub "" ""
if e1_compose_has_existing_state; then _r=var; else _r=yok; fi
_kontrol "hicbir sey yokken 'yok' demeli" "$_r" "yok"

_docker_stub "abc123
def456" ""
if e1_compose_has_existing_state; then _r=var; else _r=yok; fi
_kontrol "container varken 'var' demeli" "$_r" "var"

_docker_stub "" "enerjione-grid_pgdata"
if e1_compose_has_existing_state; then _r=var; else _r=yok; fi
_kontrol "volume varken 'var' demeli" "$_r" "var"

# Ilgisiz bir volume bizi yaniltmamali: baska projenin volume'u yuzunden
# "mevcut kurulum var" demek, temiz kurulumun izini birakmasina yol acardi.
_docker_stub "" "baska-proje_data"
if e1_compose_has_existing_state; then _r=var; else _r=yok; fi
_kontrol "ilgisiz volume 'var' saydirmamali" "$_r" "yok"

unset -f docker

# --- 5b. KARAR: hangi dal (yuklem taklit edilir) -- (DAVRANIS testi) -------------------------
# Kaynakta `else` aramak YETMEZ: kosul her zaman-yanlis hale getirilse bile
# yapisal kontrol gecerdi (denendi, KACTI). Bu yuzden fonksiyonun kendisi
# iki senaryoda da kosturuluyor.

# (a) Onceden bir sey VAR -> volume'lere DOKUNULMAMALI
e1_rollback_disarm
e1_rollback_arm
e1_compose_has_existing_state() { return 0; }
e1_rollback_register_compose "/tmp/sahte"
_kontrol "mevcut kurulumda -v KULLANILMAMALI"   "$(printf '%s' "${E1_ROLLBACK_ACTIONS[0]}" | grep -c -- '-v')" "0"

# (b) Ortada hicbir sey YOK -> bu kosumun olusturdugu volume'ler gitsin
e1_rollback_disarm
e1_rollback_arm
e1_compose_has_existing_state() { return 1; }
e1_rollback_register_compose "/tmp/sahte"
_kontrol "temiz kurulumda -v KULLANILMALI"   "$(printf '%s' "${E1_ROLLBACK_ACTIONS[0]}" | grep -c -- '-v')" "1"
e1_rollback_disarm

# --- 6. install.sh: basarida disarm ediliyor mu ---------------------------
KURULUM="${KOK}/install.sh"
if grep -q '^e1_rollback_disarm' "$KURULUM"; then
  gecti=$((gecti + 1))
else
  echo "  X install.sh basarili bitiste disarm etmiyor — kurulum kendini silebilir" >&2
  basarisiz=$((basarisiz + 1))
fi

echo "test-install-rollback: ${gecti} gecti, ${basarisiz} basarisiz"
[[ "$basarisiz" -eq 0 ]]
