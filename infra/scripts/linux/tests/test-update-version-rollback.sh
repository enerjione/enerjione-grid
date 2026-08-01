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

e1_err()  { echo "  [err]  $*"; }
SCRIPT_DIR="(test)"
E1_GIT_ROLLBACK_TO=""

# Fonksiyonlari update.sh'ten AYNEN al (kopya degil, kaynagin kendisi).
#
# DIKKAT: sed araliklari CAKISMAMALI. `/^E1_GIT_ROLLBACK_TO=""/,/^}/p` gibi bir
# aralik bir sonraki fonksiyonun sonuna kadar uzuyor ve o fonksiyon icin ayrica
# bir aralik daha varsa govde IKI KEZ basiliyor. Sonucu sessiz ve sinsi:
#     e1_git_checkpoint() { e1_git_checkpoint() { ...; }; }
# yani ilk cagri yalnizca fonksiyonu YENIDEN TANIMLIYOR, is yapmiyor —
# checkpoint hic alinmamis oluyor ve test yanlis sebepten kaliyordu.
eval "$(sed -n '/^E1_ENV_VERSION_PENDING=""/,/^}/p;/^e1_env_version_commit() {/,/^}/p;/^e1_env_version_rollback() {/,/^}/p;/^e1_git_checkpoint() {/,/^}/p;/^e1_git_rollback() {/,/^}/p' "$REPO/update.sh")"

# Cikarma gercekten calisti mi? Fonksiyon tanimsiz kalirsa asagidaki
# senaryolar "gecti" gorunurdu.
for _fn in e1_env_version_write e1_env_version_commit e1_env_version_rollback \
           e1_git_checkpoint e1_git_rollback; do
  declare -F "$_fn" >/dev/null || { echo "HATA: $_fn cikarilamadi"; exit 1; }
done

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

# ---------------------------------------------------------------------------
# CALISMA AGACI GERI ALMA
#
# `.env`'i geri almak TEK BASINA YETMIYORDU. Guncelleme once
# `git checkout <yeni tag>` yapip sonra imajlari hazirliyor; imaj adiminda
# duserse `.env` eski surume doner ama DOSYALAR yeni surumde kalir. Yani
# cihazda yeni `docker-compose.yml` + eski imaj etiketi olur. Yeni compose
# genelde yeni bir zorunlu env ya da yeni bir servis bekledigi icin ilk
# reboot'ta systemd unit'i ayaga kalkamaz — A8'in onlemeye calistigi
# senaryonun ta kendisi, bir katman yukarida.
# ---------------------------------------------------------------------------
echo "--- 5) calisma agaci: checkout sonrasi GERI ALINIYOR mu ---"
depo="$(mktemp -d)"
(
  cd "$depo"
  git init -q .
  git config user.email t@t; git config user.name t
  echo "eski" > surum.txt && git add -A && git commit -qm eski
  git tag v1
  echo "yeni" > surum.txt && git add -A && git commit -qm yeni
  git tag v2
  git checkout -q --detach v1     # "guncelleme oncesi" durum
) || { echo "  test deposu kurulamadi"; exit 1; }

cd "$depo"
onceki="$(git rev-parse HEAD)"
e1_git_checkpoint
git checkout -q --detach v2       # update.sh'in yaptigi checkout
[[ "$(cat surum.txt)" == "yeni" ]] || { echo "  fixture bozuk"; exit 1; }
e1_git_rollback >/dev/null
sonraki="$(git rev-parse HEAD)"
echo "  icerik geri alinca: $(cat surum.txt)   (beklenen eski)"
if [[ "$sonraki" == "$onceki" ]] && [[ "$(cat surum.txt)" == "eski" ]]; then
  echo "  SONUC: GECTI"
else
  echo "  SONUC: KALDI — dosyalar yeni surumde kaldi, cihaz reboot'ta olur"
  exit 1
fi

echo "--- 6) commit sonrasi calisma agaci KORUNUYOR mu ---"
e1_git_checkpoint
git checkout -q --detach v2
e1_env_version_commit             # basari yolu: geri almayi iptal eder
e1_git_rollback >/dev/null
echo "  commit sonrasi    : $(cat surum.txt)   (beklenen yeni)"
[[ "$(cat surum.txt)" == "yeni" ]] && echo "  SONUC: GECTI" || { echo "  SONUC: KALDI"; exit 1; }

echo "--- 7) checkpoint alinmadiysa zararsiz (paket modu, git yok) ---"
E1_GIT_ROLLBACK_TO=""
if e1_git_rollback >/dev/null 2>&1; then
  echo "  SONUC: GECTI"
else
  echo "  SONUC: KALDI — git'siz kurulumda update'i durdururdu"
  exit 1
fi

echo
echo "TUM SENARYOLAR GECTI"
