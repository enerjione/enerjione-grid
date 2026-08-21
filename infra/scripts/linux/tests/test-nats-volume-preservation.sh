#!/usr/bin/env bash
# OTOMATIK ONARIM, MEVCUT NATS/JETSTREAM VOLUME'UNU SILEMEZ.
#
# NEDEN BU TEST VAR
# -----------------
# `e1_repair_service` iki asamali: once container'i yeniden yaratir (veriye
# dokunmaz), olmazsa VERI ALANINI SIFIRLAR. Ikinci asama `nats` icin de
# aciktir/acikti ve gerekce "JetStream stream'lerini backend acilista ensure
# eder" idi. Bu YARIM DOGRU: stream TANIMI yeniden uretilir ama STORE'DAKI
# MESAJLAR geri gelmez.
#
# Sahadaki senaryo hayali degil: kurulum yarida kaliyor, operator
# `install.sh`i TEKRAR calistiriyor. Mevcut kurulumda NATS force-recreate
# sonrasi hala unhealthy ise, teslim edilmemis telemetri backlog'u sessizce
# siliniyor ve sistem "onarildi" diyordu. Veri silerek saglikli gorunmek,
# cozmeye calistigi sorundan pahalidir.
#
# TESTIN OLCTUGU SEY LOG METNI DEGIL, CAGRILAN KOMUTLAR: `docker volume rm`
# gercekten kosturuldu mu? Metne bakan bir test, gerekce yazisi degistiginde
# yesil kalirken davranis yikici olabilirdi.
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

# Ekrani kirletmesin; testin ilgilendigi sey cagrilan komutlar.
e1_warn() { :; }
e1_err()  { :; }
e1_hint() { :; }
e1_ok()   { :; }
e1_info() { :; }

# --- Docker taklidi --------------------------------------------------------
# Her cagri KAYDEDILIR; ayrica "servis saglikli mi" ve "hangi volume'ler var"
# disaridan ayarlanir.
KOMUTLAR=""
MEVCUT_VOLUMELER=""
SAGLIKLI_MI=1          # 1 = e1_wait_healthy basarisiz (unhealthy kalir)
NATS_VOLUME="enerjione-grid_nats-data"

docker() {
  KOMUTLAR="${KOMUTLAR}
docker $*"
  case "$*" in
    *"compose ps -aq"*)  printf '%s' "nats_container_id" ;;
    *"volume ls -q"*)    printf '%s' "$MEVCUT_VOLUMELER" ;;
    *"inspect -f"*)      printf '%s' "$NATS_VOLUME" ;;
    *)                   return 0 ;;
  esac
}

# `e1_wait_healthy` gercekte docker'i yoklar; testte sonucu biz belirliyoruz.
e1_wait_healthy() { return "$SAGLIKLI_MI"; }

_sifirla() {
  KOMUTLAR=""
  E1_PREEXISTING_VOLUMES=""
  E1_PREEXISTING_SNAPSHOT=0
}

_volume_silindi_mi() {
  if [[ "$KOMUTLAR" == *"docker volume rm ${NATS_VOLUME}"* ]]; then
    printf 'evet'
  else
    printf 'hayir'
  fi
}

echo "== TEST A — mevcut NATS volume'u KORUNUR =================================="
# Mevcut kurulum: volume kurulumdan ONCE de vardi.
_sifirla
MEVCUT_VOLUMELER="$NATS_VOLUME
enerjione-grid_pgdata"
e1_snapshot_preexisting_volumes            # install.sh basinda alinan defter
SAGLIKLI_MI=1                              # force-recreate sonrasi hala bozuk

set +e
e1_repair_service nats 1
_sonuc=$?
set -e

_kontrol "mevcut volume SILINMEMELI" "$(_volume_silindi_mi)" "hayir"
_kontrol "guvenli sekilde basarisiz olmali" "$_sonuc" "1"
# Yikici olmayan kurtarma YINE DE denenmis olmali — "hic ugrasmadi" da kotu.
_kontrol "force-recreate denenmis olmali" \
  "$([[ "$KOMUTLAR" == *"--force-recreate nats"* ]] && echo evet || echo hayir)" "evet"
# Volume kimligi degismemeli: baska bir volume'e de dokunulmamali.
_kontrol "hicbir volume silinmemeli" \
  "$([[ "$KOMUTLAR" == *"volume rm"* ]] && echo evet || echo hayir)" "hayir"

echo "== TEST B — kalici veri sentinel'i onarimdan saglikli cikar ==============="
# Sentinel: volume icindeki kalici durumu temsil eden bir dosya. Gercek
# uretim verisi UZERINDE yikici test kosturmuyoruz; volume'un SILINMEDIGINI
# dosyanin yerinde durmasiyla gosteriyoruz.
SENTINEL_DIR="$(mktemp -d)"
trap 'rm -rf "$SENTINEL_DIR"' EXIT
printf 'jetstream-backlog-42' > "${SENTINEL_DIR}/sentinel"

# `docker volume rm` cagrilirsa taklit ettigimiz volume'un dosyasini da sil:
# boylece "silme komutu kosarsa veri gider" iliskisi testte GERCEKTEN kurulur.
docker() {
  KOMUTLAR="${KOMUTLAR}
docker $*"
  case "$*" in
    *"volume rm ${NATS_VOLUME}"*) rm -f "${SENTINEL_DIR}/sentinel" ;;
    *"compose ps -aq"*)  printf '%s' "nats_container_id" ;;
    *"volume ls -q"*)    printf '%s' "$MEVCUT_VOLUMELER" ;;
    *"inspect -f"*)      printf '%s' "$NATS_VOLUME" ;;
    *)                   return 0 ;;
  esac
}

_sifirla
MEVCUT_VOLUMELER="$NATS_VOLUME"
e1_snapshot_preexisting_volumes
SAGLIKLI_MI=1
set +e
e1_repair_service nats 1
set -e
_kontrol "sentinel onarimdan sonra DURMALI" \
  "$([[ -f "${SENTINEL_DIR}/sentinel" ]] && echo var || echo yok)" "var"

echo "== TEST C — temiz kurulum: BU kosumda olusan volume silinebilir =========="
_sifirla
MEVCUT_VOLUMELER="enerjione-grid_pgdata"   # NATS volume'u HENUZ YOK
e1_snapshot_preexisting_volumes            # defter: nats yok
MEVCUT_VOLUMELER="enerjione-grid_pgdata
$NATS_VOLUME"                              # compose up onu simdi yaratti
SAGLIKLI_MI=1
set +e
e1_repair_service nats 1
set -e
_kontrol "temiz kurulumda sifirlama SERBEST" "$(_volume_silindi_mi)" "evet"

echo "== TEST C2 — defter ALINMAMISSA koru (fail-safe) ========================="
# "Muhtemelen temiz kurulum" gibi bir tahminle veri silinmez.
_sifirla                                   # E1_PREEXISTING_SNAPSHOT=0
MEVCUT_VOLUMELER="$NATS_VOLUME"
SAGLIKLI_MI=1
set +e
e1_repair_service nats 1
_sonuc=$?
set -e
_kontrol "defter yokken SILMEMELI" "$(_volume_silindi_mi)" "hayir"
_kontrol "defter yokken guvenli basarisizlik" "$_sonuc" "1"

echo "== TEST C3 — onarim ILK asamada duzelirse volume'e hic dokunulmaz ========"
_sifirla
MEVCUT_VOLUMELER="$NATS_VOLUME"
e1_snapshot_preexisting_volumes
SAGLIKLI_MI=0                              # force-recreate yetti
set +e
e1_repair_service nats 1
_sonuc=$?
set -e
_kontrol "ilk asamada onarilinca basarili" "$_sonuc" "0"
_kontrol "volume'e dokunulmamali" "$(_volume_silindi_mi)" "hayir"

echo "== TEST D — POSTGRES davranisi DEGISMEDI ================================="
# Postgres hicbir zaman sifirlanamaz: ne wipeable ne persistent listesinde.
_sifirla
MEVCUT_VOLUMELER="enerjione-grid_pgdata"
e1_snapshot_preexisting_volumes
SAGLIKLI_MI=1
set +e
e1_repair_service postgres 1
_sonuc=$?
set -e
_kontrol "postgres icin volume silinmemeli" \
  "$([[ "$KOMUTLAR" == *"volume rm"* ]] && echo evet || echo hayir)" "hayir"
_kontrol "postgres onarimi guvenli basarisizlik" "$_sonuc" "1"

echo "== TEST E — RabbitMQ semantigi KORUNDU =================================="
# Bu gorevde RabbitMQ davranisi bilerek degistirilmedi: kuyruklar gecici ve
# gateway kullanicilarini backend yeniden uretiyor.
RABBIT_VOLUME="enerjione-grid_rabbitmq-data"
docker() {
  KOMUTLAR="${KOMUTLAR}
docker $*"
  case "$*" in
    *"compose ps -aq"*)  printf '%s' "rabbit_container_id" ;;
    *"volume ls -q"*)    printf '%s' "$MEVCUT_VOLUMELER" ;;
    *"inspect -f"*)      printf '%s' "$RABBIT_VOLUME" ;;
    *)                   return 0 ;;
  esac
}
_sifirla
MEVCUT_VOLUMELER="$RABBIT_VOLUME"
e1_snapshot_preexisting_volumes            # ONCEDEN vardi — yine de silinir
SAGLIKLI_MI=1
set +e
e1_repair_service rabbitmq 1
set -e
_kontrol "rabbitmq sifirlamasi korunmali" \
  "$([[ "$KOMUTLAR" == *"docker volume rm ${RABBIT_VOLUME}"* ]] && echo evet || echo hayir)" "evet"

echo "== TEST F — siniflandirma =============================================="
_kontrol "nats wipeable listesinde OLMAMALI" \
  "$([[ "$E1_WIPEABLE_SERVICES" == *" nats "* ]] && echo var || echo yok)" "yok"
_kontrol "nats kalici listesinde OLMALI" \
  "$([[ "$E1_PERSISTENT_SERVICES" == *" nats "* ]] && echo var || echo yok)" "var"
_kontrol "postgres hicbir listede olmamali" \
  "$([[ "$E1_WIPEABLE_SERVICES$E1_PERSISTENT_SERVICES" == *" postgres "* ]] && echo var || echo yok)" "yok"

echo
if (( basarisiz > 0 )); then
  echo "BASARISIZ: ${basarisiz} kontrol (gecen: ${gecti})" >&2
  exit 1
fi
echo "OK: ${gecti} kontrol gecti."
