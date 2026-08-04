#!/usr/bin/env bash
# Backend yeniden yaratilinca FRONTEND'IN NGINX'I DE TAZELENMELI.
#
# YASANAN ARIZA
# -------------
# nginx upstream adresini YALNIZCA baslangicta cozer ve saklar. Backend
# container'i yeniden yaratilinca yeni bir IP alir; frontend ayakta kaldigi
# icin OLU adrese gitmeye devam eder ve arayuz `502 Bad Gateway` doner.
#
# Kullanici tarafindan gorunusu: "guncelleme sonrasi sisteme giremiyorum".
# Teshisi ZOR, cunku her sey saglikli gorunur:
#   backend-api  -> Up (healthy),  /health 200 (container ICINDEN)
#   postgres     -> healthy
#   loglarda     -> tek bir hata YOK
# Yalnizca nginx UZERINDEN gecen istek 502 doner.
#
# Bu tam olarak test sunucusunda yasandi: backend yeniden yaratildi,
# frontend 2 saattir ayaktaydi, giris ekrani acildi ama API'ye ulasamadi.
#
# NEDEN `compose up -d` YETMIYOR
# ------------------------------
# Frontend imaji degismediyse compose ona DOKUNMAZ — dogru davranis, ama
# bu senaryoda tam da dokunmasi gerekiyor. Bu yuzden acikca restart.
set -euo pipefail

KOK="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
BETIK="${KOK}/update.sh"
[[ -f "$BETIK" ]] || { echo "update.sh bulunamadi: $BETIK" >&2; exit 1; }

gecti=0
basarisiz=0
_k() {
  if [[ "$2" == "$3" ]]; then gecti=$((gecti+1)); else
    echo "  X $1: beklenen='$3' gercek='$2'" >&2; basarisiz=$((basarisiz+1)); fi
}

# --- Tazeleme blogu VAR MI ve DOGRU KOSULDA MI ----------------------------
# Kaynak-grep yerine blogu CIKARIP kosturuyoruz: `docker compose` taklit
# ediliyor ve HANGI komutlarin cagrildigi olculuyor.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PARCA="${TMP}/blok.sh"
awk '/^  # FRONTEND.?IN NGINX.?I DE TAZELENMELI/{i=1} i{print} i&&/^  fi$/{exit}' \
  "$BETIK" > "$PARCA"

if ! grep -q "restart frontend-web" "$PARCA"; then
  echo "  X update.sh backend yeniden yaratilinca frontend'i tazelemiyor" >&2
  echo "      -> sahada 'guncelleme sonrasi giris yapamiyorum' (502) olarak doner" >&2
  exit 1
fi

_kos() {  # $1=RECREATE icerigi  $2=frontend calisiyor mu (id/bos)
  (
    set +e
    CAGRILAR=""
    RECREATE=($1)
    FE_ID="$2"
    e1_step() { :; }
    e1_warn() { :; }
    docker() {
      case "$*" in
        *"compose ps -q frontend-web"*) printf '%s' "$FE_ID" ;;
        *"compose restart frontend-web"*) CAGRILAR="${CAGRILAR}restart;" ;;
      esac
      return 0
    }
    # shellcheck source=/dev/null
    . "$PARCA"
    printf '%s' "$CAGRILAR"
  )
}

# 1. Backend yeniden yaratildi + frontend ayakta -> TAZELENMELI
_k "backend yeniden yaratilinca frontend tazelenmeli" \
   "$(_kos "backend-api backend-worker" "abc123")" "restart;"

# 2. Backend YOK (or. yalnizca tag-engine guncellendi) -> DOKUNULMAMALI
#    Gereksiz restart, calisan bir arayuzu bir saniyeligine dusurur.
_k "backend yeniden yaratilmadiysa dokunulmamali" \
   "$(_kos "tag-engine alarm-service" "abc123")" ""

# 3. Frontend hic calismiyorsa (kapali/kurulmamis) -> restart DENENMEMELI
_k "frontend calismiyorsa restart denenmemeli" \
   "$(_kos "backend-api" "")" ""

echo "test-update-frontend-tazele: ${gecti} gecti, ${basarisiz} basarisiz"
[[ "$basarisiz" -eq 0 ]]
