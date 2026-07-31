#!/usr/bin/env bash
# update.sh — .env surum geri alma davranisi (denetim A8).
#
# YASANAN RISK
#   `.env`'deki `E1_VERSION` hedef surume, imajlar HAZIR OLMADAN yaziliyordu.
#   Yazmak zorunlu (`docker compose pull` etiketi oradan okur) ama guncelleme
#   imaj asamasinda duserse deger yeni surumde KALIYORDU.
#
#   Somut senaryo: trafo merkezinde 4G hattinda update kosar; `pull` GHCR'a
#   erisemez, yedek `build` de temel imajlari cekemez -> update duser. Eski
#   container'lar CALISMAYA DEVAM ETTIGI icin operator fark etmez. Ilk elektrik
#   kesintisinden sonra systemd `up -d` kosar, `.env`'deki etiket ne yerelde ne
#   registry'de bulunur -> cihaz sahada TAMAMEN OLU kalir.
#
# Bu test update.sh'teki GERCEK fonksiyon metnini cikarip surer — kopya degil,
# kaynagin kendisi. Fonksiyonlar degisirse test de onlari test eder.
#
# Kullanim: bash infra/scripts/linux/tests/test-update-version-rollback.sh <repo-koku>
set -uo pipefail
REPO="$1"
e1_warn() { echo "  [warn] $*"; }
e1_hint() { echo "  [hint] $*"; }

# Fonksiyonlari update.sh'ten AYNEN al (kopya degil, kaynagin kendisi).
eval "$(sed -n '/^E1_ENV_VERSION_PENDING=""/,/^}/p;/^e1_env_version_commit() {/,/^}/p;/^e1_env_version_rollback() {/,/^}/p' "$REPO/update.sh")"

kur() { printf 'E1_VERSION=%s\nOTHER=x\n' "$1" > .env; }
oku() { sed -n 's/^E1_VERSION=//p' .env | head -1; }

cd "$(mktemp -d)"

echo "--- 1) yaz + GERI AL (guncelleme dustu) ---"
kur 2.28.0
e1_env_version_write "2.29.0"
echo "  yazildiktan sonra : $(oku)"
e1_env_version_rollback >/dev/null
echo "  geri alindiktan   : $(oku)   (beklenen 2.28.0)"
[[ "$(oku)" == "2.28.0" ]] && echo "  SONUC: GECTI" || { echo "  SONUC: KALDI"; exit 1; }

echo "--- 2) yaz + COMMIT (imajlar hazir) ---"
kur 2.28.0
e1_env_version_write "2.29.0"
e1_env_version_commit
e1_env_version_rollback >/dev/null
echo "  commit sonrasi    : $(oku)   (beklenen 2.29.0)"
[[ "$(oku)" == "2.29.0" ]] && echo "  SONUC: GECTI" || { echo "  SONUC: KALDI"; exit 1; }

echo "--- 3) E1_VERSION HIC YOKKEN yaz + geri al ---"
printf 'OTHER=x\n' > .env
e1_env_version_write "2.29.0"
echo "  yazildiktan sonra : $(oku)"
e1_env_version_rollback >/dev/null
satir="$(grep -c '^E1_VERSION=' .env || true)"
echo "  geri alinca satir : ${satir}   (beklenen 0)"
[[ "$satir" == "0" ]] && echo "  SONUC: GECTI" || { echo "  SONUC: KALDI"; exit 1; }

echo "--- 4) cift geri alma zararsiz mi ---"
kur 2.28.0
e1_env_version_write "2.29.0"
e1_env_version_rollback >/dev/null
e1_env_version_rollback >/dev/null
echo "  iki kez sonra     : $(oku)   (beklenen 2.28.0)"
[[ "$(oku)" == "2.28.0" ]] && echo "  SONUC: GECTI" || { echo "  SONUC: KALDI"; exit 1; }
echo
echo "TUM SENARYOLAR GECTI"
