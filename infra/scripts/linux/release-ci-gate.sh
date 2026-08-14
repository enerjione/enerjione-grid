#!/usr/bin/env bash
# RELEASE KAPISI — ana CI gecmeden surum yayinlanmaz.
#
# YASANAN ARIZA (v2.95.0)
# -----------------------
# Ana CI `Guvenli restore — PG16 + TimescaleDB` isinde DUSTU. Buna ragmen
# tag ile tetiklenen Release akisi SORUNSUZ tamamlandi: dokuz imaj GHCR'a
# basildi ve GitHub Release yayinlandi. Cunku Release akisinin tek on
# kosulu `Tag == VERSION == package.json` esitligiydi; CI'in sonucuna
# HIC BAKMIYORDU.
#
# Yani "CI kirmizi" ile "surum yayinlandi" ayni anda dogru olabiliyordu.
# Saha cihazlari surumu TAG'den takip ettigi icin bu, test edilmemis bir
# kodun sahaya inmesi demek.
#
# BU BETIK NE YAPAR
# -----------------
# Tag'in GERCEKTEN isaret ettigi kaynak commit icin `ci.yml` akisinin
# sonucunu arar:
#
#   * calisiyorsa BITMESINI BEKLER (sinirli sure),
#   * `success` ise 0 doner (release devam eder),
#   * baska her terminal durumda (failure/cancelled/timed_out/...) 1 doner,
#   * hic kosum bulunamazsa 1 doner — FAIL CLOSED.
#
# "Bulunamadi -> gecir" davranisi kapiyi islevsiz kilardi: CI hic
# tetiklenmemis bir commit en riskli olandir.
#
# NEDEN KAYNAK COMMIT
# -------------------
# Dalin ucuna bakmak yanlis olurdu: tag atildiktan sonra main'e gelen
# yesil bir commit, tag'lenen kirmizi commit'i akladirdi. Cagiran taraf
# annotated tag'i `^{}` ile cozup gercek commit'i vermek zorunda.
#
# KULLANIM
#   release-ci-gate.sh <kaynak-commit-sha>
#
# ORTAM
#   E1_GATE_TIMEOUT_SEC   toplam bekleme siniri (varsayilan 1800)
#   E1_GATE_POLL_SEC      yoklama araligi (varsayilan 20)
#   E1_GATE_WORKFLOW      izlenecek akis dosyasi (varsayilan ci.yml)
#   E1_GATE_QUERY_CMD     kosum listesini uretecek komut. Varsayilan `gh api`.
#                         TEST EDILEBILIRLIK icin buradan degistirilebilir;
#                         komut, GitHub'in `workflow_runs` JSON'unu stdout'a
#                         yazmalidir.
set -euo pipefail

SHA="${1:-}"
if [ -z "$SHA" ]; then
  echo "::error::Kaynak commit SHA verilmedi. Kullanim: $0 <sha>"
  exit 2
fi

# CALISAN Python'u coz. `python3` her yerde var SAYILMAZ: Windows/Git Bash
# ortaminda `python3` Microsoft Store kisayoluna cozulup hicbir sey
# yapmadan cikabiliyor. Bu, kapinin YANLIS SEBEPLE sifirdan farkli
# donmesine — yani testlerin bos gecmesine — yol aciyordu.
PY_BIN=""
for aday in python3 python; do
  if command -v "$aday" >/dev/null 2>&1 && "$aday" -c "import sys" >/dev/null 2>&1; then
    PY_BIN="$aday"; break
  fi
done
if [ -z "$PY_BIN" ]; then
  echo "::error::Calisan bir Python bulunamadi; CI kapisi degerlendirilemiyor. Surum YAYINLANMIYOR."
  exit 1
fi

TIMEOUT_SEC="${E1_GATE_TIMEOUT_SEC:-1800}"
POLL_SEC="${E1_GATE_POLL_SEC:-20}"
WORKFLOW="${E1_GATE_WORKFLOW:-ci.yml}"

# Kosum listesini getiren komut. Varsayilan GitHub CLI; testler bunu
# sabit bir JSON dosyasini basan bir komutla degistirir.
if [ -n "${E1_GATE_QUERY_CMD:-}" ]; then
  sorgula() { eval "$E1_GATE_QUERY_CMD"; }
else
  sorgula() {
    gh api \
      "repos/${GITHUB_REPOSITORY}/actions/workflows/${WORKFLOW}/runs?head_sha=${SHA}&per_page=20"
  }
fi

echo "Release kapisi: ${WORKFLOW} akisinin ${SHA} commit'indeki sonucu bekleniyor"
echo "  zaman siniri: ${TIMEOUT_SEC} sn, yoklama: ${POLL_SEC} sn"

baslangic=$(date +%s)

while :; do
  ham="$(sorgula || true)"

  # SHA eslesmesi BURADA da zorlanir: sorgu parametresine guvenmiyoruz,
  # cunku sahte/degistirilmis bir sorgu komutu (ya da API'nin gevsek
  # eslesmesi) baska bir commit'in yesil kosumunu gecirebilirdi.
  durum="$(printf '%s' "$ham" | "$PY_BIN" -c '
import json, sys
try:
    veri = json.load(sys.stdin)
except Exception:
    print("PARSE_HATASI"); raise SystemExit(0)
sha = sys.argv[1]
kosumlar = [k for k in (veri.get("workflow_runs") or []) if k.get("head_sha") == sha]
if not kosumlar:
    print("YOK"); raise SystemExit(0)
# En yeni kosum belirleyicidir (yeniden calistirma senaryosu).
kosumlar.sort(key=lambda k: k.get("run_started_at") or "", reverse=True)
k = kosumlar[0]
print("%s %s %s" % (k.get("status") or "?", k.get("conclusion") or "-", k.get("html_url") or "-"))
' "$SHA")"

  set -- $durum
  asama="${1:-YOK}"
  sonuc="${2:--}"
  baglanti="${3:--}"

  case "$asama" in
    PARSE_HATASI)
      echo "::warning::CI kosum listesi okunamadi, yeniden denenecek"
      ;;
    YOK)
      echo "  ${SHA} icin ${WORKFLOW} kosumu henuz gorulmedi..."
      ;;
    completed)
      if [ "$sonuc" = "success" ]; then
        echo "Ana CI BASARILI: $baglanti"
        exit 0
      fi
      echo "::error::Ana CI '${sonuc}' ile bitti — SURUM YAYINLANMAYACAK. $baglanti"
      exit 1
      ;;
    *)
      echo "  CI durumu: ${asama} (${sonuc}) — bekleniyor..."
      ;;
  esac

  simdi=$(date +%s)
  if [ $((simdi - baslangic)) -ge "$TIMEOUT_SEC" ]; then
    # FAIL CLOSED: sonucu ogrenemeden yayinlamak, kapinin hic olmamasiyla
    # ayni sonucu verirdi.
    echo "::error::Ana CI sonucu ${TIMEOUT_SEC} sn icinde belirlenemedi (son durum: ${asama}/${sonuc}). Surum YAYINLANMIYOR."
    exit 1
  fi
  sleep "$POLL_SEC"
done
