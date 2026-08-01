#!/usr/bin/env bash
# ===========================================================================
# e1_nats_tls_prepare / e1_nats_conf_render_tls davranis testi
# ===========================================================================
#
# YASANAN SORUN
# -------------
# NATS TLS'in calismasi UC seyin AYNI ANDA dogru olmasina bagli:
#   (1) infra/nats/certs/ altinda sunucu sertifikasi + CA,
#   (2) istemcilerin okudugu NATS_CA_FILE,
#   (3) NATS_URL* semalarinin `tls://` olmasi.
# Ucunden biri eksik kalirsa ariza SESSIZ olur: NATS'in kendi healthcheck'i
# TLS'siz izleme portunu (8222) prob ettigi icin container "healthy" gorunur
# ama backend el sikisamaz.
#
# Ayrica render adimi eskiden gomulu bir Python heredoc'uydu ve string
# literalleri bozuktu (SyntaxError). `set -euo pipefail` altinda betik TAM BU
# NOKTADA oluyordu; nats-server.conf icinde `{{NATS_TLS_BLOCK}}` yer tutucusu
# HAM halde kaliyor, NATS o dosyayi ayristiramayip crash-loop'a giriyordu.
# Yani NATS_TLS_ENABLED=true yapan HER kurulum sessizce kiriliyordu.
#
# Bu test sozdizimini degil DAVRANISI dogrular: fonksiyonlar gercekten
# calistirilir, uretilen .env ve .conf iceriklerine bakilir.
#
# Kullanim: bash test-nats-tls-prepare.sh [repo-koku]
# ===========================================================================
set -euo pipefail

# tests -> linux -> scripts -> infra -> repo koku
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
LIB="${REPO}/infra/scripts/linux/_lib.sh"

[[ -f "$LIB" ]] || { echo "HATA: _lib.sh bulunamadi: $LIB" >&2; exit 1; }

# shellcheck disable=SC1090
source "$LIB" >/dev/null 2>&1
# Ciktilar testi kirletmesin.
e1_info() { :; }
e1_ok()   { :; }
e1_warn() { :; }

FAIL=0
_ok()   { printf '  ok   %s\n' "$1"; }
_fail() { printf '  FAIL %s\n' "$1" >&2; FAIL=1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Fonksiyonlar goreli yol (infra/nats/certs/...) kullaniyor; sahte bir repo
# koku kurup oraya gecerek gercek kurulum duzenini taklit ediyoruz.
_kur() {
  rm -rf "${WORK:?}/repo"
  mkdir -p "$WORK/repo/infra/nats/certs"
  cd "$WORK/repo"
}

_sertifika_uret() {
  printf 'CRT\n' > infra/nats/certs/server.crt
  printf 'KEY\n' > infra/nats/certs/server.key
  printf 'CA\n'  > infra/nats/certs/ca.crt
}

# --------------------------------------------------------------------------
# 1) TLS KAPALI — yarim kalmis `tls://` semalari geri alinmali
# --------------------------------------------------------------------------
_kur
cat > .env <<'EOF'
NATS_TLS_ENABLED=false
NATS_URL=tls://backend:pw@nats:4222
NATS_URL_BACKEND=tls://backend:pw@nats:4222
NATS_URL_WORKER=nats://worker:pw@nats:4222
EOF

e1_nats_tls_prepare ".env"

if grep -q '^NATS_URL=nats://' .env && grep -q '^NATS_URL_BACKEND=nats://' .env; then
  _ok "TLS kapali: tls:// semalari nats:// yapildi"
else
  _fail "TLS kapali: tls:// semalari geri alinmadi ($(grep '^NATS_URL=' .env))"
fi

if grep -q '^NATS_URL_WORKER=nats://worker:pw@nats:4222$' .env; then
  _ok "TLS kapali: zaten dogru olan deger BOZULMADI"
else
  _fail "TLS kapali: dokunulmamasi gereken deger degisti"
fi

if [[ "$E1_NATS_TLS_BLOCK" == \#* ]]; then
  _ok "TLS kapali: conf blogu yorum satiri"
else
  _fail "TLS kapali: conf blogu yorum degil -> NATS TLS dinlemeye calisir"
fi

# Parolayi iceren degerin log'a sizmadigini dogrula (e1_info susturuldu ama
# fonksiyon parolayi baska bir yoldan basmamali).
CIKTI="$(e1_nats_tls_prepare ".env" 2>&1 || true)"
if [[ "$CIKTI" != *"pw@nats"* ]]; then
  _ok "TLS kapali: parola iceren URL ciktiya basilmadi"
else
  _fail "TLS kapali: parola ciktiya sizdi"
fi

# --------------------------------------------------------------------------
# 2) TLS ACIK ama SERTIFIKA YOK — acik hata ile durmali (sessiz gecmemeli)
# --------------------------------------------------------------------------
_kur
printf 'NATS_TLS_ENABLED=true\n' > .env
# CA'yi BILEREK koyuyoruz: yoksa ilerideki CA kontrolu de ayni hatayi
# yakalar ve bu adim sertifika kontrolunu GERCEKTEN sinamamis olur. Ilk
# yazimda CA da eksikti ve sertifika kontrolunu tamamen etkisizlestiren bir
# mutasyon test tarafindan KACIRILDI.
printf 'CA\n' > infra/nats/certs/ca.crt

if ( e1_nats_tls_prepare ".env" ) >/dev/null 2>&1; then
  _fail "sertifikasiz TLS: hata vermeden gecti — NATS crash-loop'a girer"
else
  _ok "sertifikasiz TLS: acik hata ile durdu"
fi

# --------------------------------------------------------------------------
# 3) TLS ACIK ama CA YOK — istemci sunucuyu dogrulayamaz, durmali
# --------------------------------------------------------------------------
_kur
printf 'NATS_TLS_ENABLED=true\n' > .env
printf 'CRT\n' > infra/nats/certs/server.crt
printf 'KEY\n' > infra/nats/certs/server.key

if ( e1_nats_tls_prepare ".env" ) >/dev/null 2>&1; then
  _fail "CA'siz TLS: hata vermeden gecti"
else
  _ok "CA'siz TLS: acik hata ile durdu"
fi

# --------------------------------------------------------------------------
# 4) TLS ACIK ve TAM — uc parca da hizalanmali
# --------------------------------------------------------------------------
_kur
_sertifika_uret
cat > .env <<'EOF'
NATS_TLS_ENABLED=true
NATS_CA_FILE=
NATS_URL=nats://backend:pw@nats:4222
NATS_URL_BACKEND=nats://backend:pw@nats:4222
NATS_URL_WORKER=nats://worker:pw@nats:4222
EOF

e1_nats_tls_prepare ".env"

if grep -q '^NATS_CA_FILE=/etc/nats/certs/ca.crt$' .env; then
  _ok "TLS acik: bos NATS_CA_FILE container ici yol ile dolduruldu"
else
  _fail "TLS acik: NATS_CA_FILE doldurulmadi ($(grep '^NATS_CA_FILE=' .env))"
fi

EKSIK=""
for k in NATS_URL NATS_URL_BACKEND NATS_URL_WORKER; do
  grep -q "^${k}=tls://" .env || EKSIK="$EKSIK $k"
done
if [[ -z "$EKSIK" ]]; then
  _ok "TLS acik: tum NATS_URL* semalari tls:// oldu"
else
  _fail "TLS acik: sema hizalanmayan anahtarlar:$EKSIK"
fi

if [[ "$E1_NATS_TLS_BLOCK" == *"cert_file"* && "$E1_NATS_TLS_BLOCK" == *"key_file"* ]]; then
  _ok "TLS acik: conf blogu sertifika yollarini iceriyor"
else
  _fail "TLS acik: conf blogu eksik"
fi

# --------------------------------------------------------------------------
# 5) Render — yer tutucu GERCEKTEN degistirilmeli
#
# Asil regresyon buydu: yer tutucu ham kalirsa NATS conf'u ayristiramaz.
# --------------------------------------------------------------------------
printf 'server_name: x\n{{NATS_TLS_BLOCK}}\nmax_payload: 1MB\n' > infra/nats/test.conf
e1_nats_conf_render_tls infra/nats/test.conf

if grep -q '{{NATS_TLS_BLOCK}}' infra/nats/test.conf; then
  _fail "render: yer tutucu HAM kaldi — NATS conf'u ayristiramaz"
else
  _ok "render: yer tutucu degistirildi"
fi

if grep -q 'cert_file' infra/nats/test.conf; then
  _ok "render: TLS blogu conf'a yazildi"
else
  _fail "render: TLS blogu conf'a yazilmadi"
fi

if grep -q '^max_payload: 1MB$' infra/nats/test.conf; then
  _ok "render: dosyanin geri kalani KORUNDU"
else
  _fail "render: dosyanin geri kalani bozuldu"
fi

# TLS kapaliyken de yer tutucu temizlenmeli (yorum satirina donusur).
_kur
printf 'NATS_TLS_ENABLED=false\n' > .env
e1_nats_tls_prepare ".env"
printf 'server_name: x\n{{NATS_TLS_BLOCK}}\n' > infra/nats/test.conf
e1_nats_conf_render_tls infra/nats/test.conf
if grep -q '{{NATS_TLS_BLOCK}}' infra/nats/test.conf; then
  _fail "render (TLS kapali): yer tutucu HAM kaldi"
else
  _ok "render (TLS kapali): yer tutucu temizlendi"
fi

# --------------------------------------------------------------------------
# 6) Gercek sablon bu yer tutucuyu ICERMELI
#
# Icermezse prepare bosuna calisir ve TLS hicbir zaman etkinlesmez.
# --------------------------------------------------------------------------
if grep -q '{{NATS_TLS_BLOCK}}' "${REPO}/infra/nats/nats-server.conf.template"; then
  _ok "sablon: {{NATS_TLS_BLOCK}} yer tutucusu mevcut"
else
  _fail "sablon: yer tutucu YOK — TLS ayari conf'a hic yansimaz"
fi

# NOT: "NATS istemcisi olan her servise CA dizini mount edilmis mi" kontrolu
# BILEREK burada DEGIL: compose YAML anchor kullaniyor ve dosyayi satir satir
# dilimlemek kirilgan cikti (CRLF satir sonlari yuzunden awk blok siniri
# kaciriyordu ve mount tamamen silindigi halde test GECIYORDU). Gercek YAML
# ayristiricisiyla yapiliyor:
#   apps/backend-api/tests/test_config_consistency.py ::
#     test_nats_istemcilerine_ca_dizini_mount_edilmis

cd "$REPO"
if [[ "$FAIL" -eq 0 ]]; then
  echo "NATS TLS hazirlama testleri: TUMU GECTI"
else
  echo "NATS TLS hazirlama testleri: BASARISIZ" >&2
fi
exit "$FAIL"
