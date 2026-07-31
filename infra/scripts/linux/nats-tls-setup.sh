#!/usr/bin/env bash
# NATS TLS sertifikalarini uretir (kendinden imzali CA + sunucu sertifikasi).
#
# NEDEN GEREKLI
#   NATS istemci portu (4222) tum arayuzlere aciktir ve gateway'ler ona
#   `nats://gateway:<parola>@<host>:4222` ile baglanir. TLS olmadan HEM gateway
#   parolasi HEM de tum telemetri DUZ METIN gider. Ayni agdaki (ya da 4G
#   yolundaki) biri parolayi alip sahte telemetri enjekte edebilir: uydurma
#   kritik ariza uretmek ya da `fault_indicator`i normal gonderip GERCEK
#   arizayi maskelemek.
#
# NEDEN KENDINDEN IMZALI
#   Saha cihazlarinin genelde herkese acik bir alan adi ve sabit IP'si yok;
#   Let's Encrypt gibi bir otorite dogrulama yapamaz. Kendinden imzali bir CA
#   ile istemciler YALNIZCA bu CA'ya guvenir — herkese acik guven deposu
#   kullanilmadigi icin baska bir otoriteden alinmis sertifika ISE YARAMAZ.
#
# KULLANIM
#   sudo bash infra/scripts/linux/nats-tls-setup.sh [<sunucu-adi-veya-ip> ...]
#
#   Argumansiz calistirilirsa sertifika yalnizca `nats`, `localhost` ve
#   `127.0.0.1` icin gecerli olur — tek cihazli kurulum icin yeterlidir.
#   UZAKTAKI gateway'ler baglanacaksa cihazin disaridan gorunen adresini
#   arguman olarak verin, aksi halde dogrulama BASARISIZ olur.
#
# SONRASI
#   .env dosyasina `NATS_TLS_ENABLED=true` ekleyip stack'i yeniden baslatin.
#   Sertifikalar uretilmeden bu bayragi acmayin — NATS ayaga kalkmaz.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_lib.sh" 2>/dev/null || {
  e1_info()  { printf '  %s\n' "$*"; }
  e1_warn()  { printf '  UYARI: %s\n' "$*" >&2; }
  e1_die()   { printf '  HATA: %s\n' "$*" >&2; exit 1; }
}

REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CERT_DIR="${REPO_ROOT}/infra/nats/certs"
DAYS_CA=3650      # 10 yil — CA'yi sik degistirmek tum istemcileri kirar
DAYS_SERVER=825   # ~27 ay; tarayici/kutuphane ust sinirlarina uyumlu

command -v openssl >/dev/null 2>&1 || e1_die "openssl bulunamadi. 'apt-get install -y openssl' ile kurun."

mkdir -p "${CERT_DIR}"
# 0700: ozel anahtar burada duruyor. Container'a YALNIZCA sunucu anahtari ve
# sertifikasi read-only mount edilir; CA'nin ozel anahtari HIC mount edilmez.
chmod 700 "${CERT_DIR}"

if [[ -f "${CERT_DIR}/server.key" ]]; then
  e1_warn "Sertifikalar zaten var: ${CERT_DIR}"
  e1_warn "Yeniden uretmek TUM gateway'lerin baglantisini keser (yeni CA'yi"
  e1_warn "almayan istemci dogrulayamaz). Gercekten istiyorsaniz once dizini silin."
  exit 0
fi

# --- Sertifikanin gecerli olacagi adlar --------------------------------------
# `nats` : docker network icindeki servis adi (backend ve worker'lar bunu kullanir)
# localhost/127.0.0.1 : ayni host uzerindeki gateway
ALT_NAMES="DNS:nats,DNS:localhost,IP:127.0.0.1"
for host in "$@"; do
  [[ -z "${host}" ]] && continue
  if [[ "${host}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ALT_NAMES="${ALT_NAMES},IP:${host}"
  else
    ALT_NAMES="${ALT_NAMES},DNS:${host}"
  fi
done

e1_info "Sertifika gecerlilik adlari: ${ALT_NAMES}"

umask 077

# --- 1) CA -------------------------------------------------------------------
openssl req -x509 -newkey rsa:4096 -sha256 -nodes \
  -days "${DAYS_CA}" \
  -keyout "${CERT_DIR}/ca.key" \
  -out    "${CERT_DIR}/ca.crt" \
  -subj "/CN=EnerjiOne Grid NATS CA" \
  >/dev/null 2>&1 || e1_die "CA uretilemedi."

# --- 2) Sunucu sertifikasi ---------------------------------------------------
openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout "${CERT_DIR}/server.key" \
  -out    "${CERT_DIR}/server.csr" \
  -subj "/CN=nats" \
  >/dev/null 2>&1 || e1_die "Sunucu anahtari uretilemedi."

# SAN zorunlu: modern istemciler CN'e BAKMAZ, yalnizca SAN'a bakar. SAN'siz
# sertifika "hostname mismatch" ile reddedilir.
cat > "${CERT_DIR}/server.ext" <<EOF
subjectAltName = ${ALT_NAMES}
extendedKeyUsage = serverAuth
basicConstraints = CA:FALSE
EOF

openssl x509 -req -sha256 -days "${DAYS_SERVER}" \
  -in  "${CERT_DIR}/server.csr" \
  -CA  "${CERT_DIR}/ca.crt" \
  -CAkey "${CERT_DIR}/ca.key" \
  -CAcreateserial \
  -extfile "${CERT_DIR}/server.ext" \
  -out "${CERT_DIR}/server.crt" \
  >/dev/null 2>&1 || e1_die "Sunucu sertifikasi imzalanamadi."

rm -f "${CERT_DIR}/server.csr" "${CERT_DIR}/server.ext" "${CERT_DIR}/ca.srl"

# NATS container'i root DEGIL (nats kullanicisi, uid 1000) calisir; anahtari
# okuyabilmesi gerekir. CA'nin OZEL anahtari 0600 kalir ve mount EDILMEZ.
chmod 644 "${CERT_DIR}/ca.crt" "${CERT_DIR}/server.crt"
chmod 640 "${CERT_DIR}/server.key"
chmod 600 "${CERT_DIR}/ca.key"
chown -R 1000:1000 "${CERT_DIR}" 2>/dev/null || true

e1_info "Sertifikalar uretildi: ${CERT_DIR}"
e1_info "  ca.crt      -> istemcilerin guvenecegi kok (gateway'e de kopyalanir)"
e1_info "  server.crt  -> NATS sunucu sertifikasi"
e1_info "  server.key  -> NATS ozel anahtari (mount: read-only)"
e1_info "  ca.key      -> CA ozel anahtari (SAKLA, mount EDILMEZ, yedekle)"
e1_info ""
e1_info "Simdi .env dosyasina ekleyin ve stack'i yeniden baslatin:"
e1_info "    NATS_TLS_ENABLED=true"
e1_info ""
e1_info "UZAKTAKI gateway icin ca.crt dosyasini gateway host'una kopyalayin ve"
e1_info "gateway .env'ine NATS_CA_FILE=<yol> + NATS_URL=tls://... yazin."
