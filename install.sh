#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Production Installer
# ===========================================================================
# Sifirdan tek-komut kurulum. Ubuntu 22.04/24.04 ve Debian 12 destekli.
#
# Iki kullanim sekli:
#
#   1) Repo'yu manuel klonladiysan, repo kokunde:
#        sudo bash install.sh
#
#   2) Tertemiz VPS'te (curl | bash):
#        TOKEN=ANAHTAR; curl -fsSL -H "Authorization: token $TOKEN" https://raw.githubusercontent.com/enerjione/enerjione-grid/main/install.sh | sudo E1_GHCR_TOKEN=$TOKEN bash
#
# Idempotent: tekrar calistirmak guvenli. Mevcut .env'i koruyup eksik
# alanlari rastgele degerlerle doldurur, Docker'i atlar (zaten kuruluysa),
# servisleri update eder.
#
# Appliance (mini PC) modu OTOMATIKTIR: makinede WiFi karti varsa sifresiz
# musteri adiyla WiFi AP'si (or. "E1GRID-TPAO"), e1-grid.local mDNS ve
# komutla kurulur. VPS'lerde WiFi karti olmadigi icin devreye girmez.
#
# Surum modeli: cihaz bir DALI degil, yayinlanmis bir TAG'i takip eder.
# Varsayilan olarak en son `v*` tag'ine gecer (kararli kanal). Gelistirme
# surumunu denemek icin: E1_REF=main
#
# Imajlar: release CI tarafindan derlenip ghcr.io'ya basilir. Kurulum imaji
# INDIRIR, derlemez — saha PC'sinde 3-8 dakikalik build adimi ortadan kalkar
# ve her cihazda birebir ayni imaj calisir. Imaj cekilemezse (token yok,
# internet yok) otomatik olarak yerel build'e duser.
#
# Env override'lar (hepsi opsiyonel):
#   INSTALL_DIR    hedef dizin (default: /opt/enerjione-grid)
#   E1_REF         checkout edilecek tag/dal (default: en son v* tag, yoksa main)
#   E1_REPO_URL    git remote URL (default: github.com/enerjione/enerjione-grid)
#   E1_GHCR_TOKEN  private imajlari cekmek icin read:packages token'i
#   E1_BUILD=1     imaji cekmek yerine her zaman yerel derle
#   INSTALL_USER   kurulum sonrasi dosya sahibi olacak kullanici
#                  (default: SUDO_USER veya root)
#   ASSUME_YES=1   tum onay sorularini atla
#   E1_APPLIANCE   1 = appliance modunu zorla (WiFi karti yoksa bile)
#                  0 = hic kurma (WiFi karti olsa bile)
#                  bos/auto = WiFi kartina gore otomatik karar (varsayilan)
#
# Saha kimligi (musteri/proje) — kurulumda BIR KEZ sorulur. Uzaktan bakim
# listesinde cihazin hangi ad ile gorunecegini belirler. Soru sorulmasin
# isteniyorsa bastan verilebilir:
#   E1_CUSTOMER    musteri / firma adi     (or. "TPAO")
#   E1_SITE        saha / proje adi        (or. "Batman OSB")
#   E1_SITE_UNIT   ayni sahada 2. kutu ise (or. "pano-2")
#
# Uzaktan bakim VPN'i (Tailscale) — opsiyonel ama saha cihazlarinda onerilir:
#   E1_TAILSCALE_AUTHKEY  auth key (tskey-auth-...) veya OAuth secret
#                         (tskey-client-...). BOS ise VPN adimi hic calismaz.
#   E1_TAILSCALE_TAGS     cihaz etiketi (default: tag:e1-appliance)
#   E1_TAILSCALE_SSH      1 = Tailscale SSH acik (default), 0 = kapali
#   Anahtar `.env` dosyasindan da okunur; repoya ASLA yazilmaz.
# ===========================================================================

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/enerjione-grid}"
# Bootstrap sirasinda (curl | bash) _lib.sh henuz yuklenmedi; asagidaki iki
# deger lib yuklenince oradaki merkezi tanimla hizalanir.
REPO_URL="${E1_REPO_URL:-${REPO_URL:-https://github.com/enerjione/enerjione-grid.git}}"
BRANCH="${E1_BOOTSTRAP_REF:-main}"

# ---- Bootstrap: bu script repo icinden mi yoksa curl | bash mi? ----------
# Iki senaryo: (a) repo kokunden bash install.sh — `infra/scripts/linux/_lib.sh`
# yerel dosya olarak var. (b) curl | bash — script'in dosyasi yok, repo
# henuz klonlanmadi; lib'i once GitHub'dan cek, daha sonra repo klonlanip
# yerel lib'i de _chown_target ile devralir.
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
LIB_LOADED=0
if [[ -n "$SCRIPT_DIR" ]] && [[ -f "$SCRIPT_DIR/infra/scripts/linux/_lib.sh" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/infra/scripts/linux/_lib.sh"
  LIB_LOADED=1
else
  # curl | bash modu — lib'i GitHub'dan cek (tmpfile'a).
  #
  # DEPO PRIVATE: bu istek de yetki ister, aksi halde raw 404 doner.
  # Anahtar E1_GHCR_TOKEN ile gelir; onerilen kurulum komutu onu hem curl'e
  # hem script'e ayni anda verir (bkz. docs/SAHA-KURULUM.md).
  # E1_LIB_URL ile kaynak tamamen degistirilebilir (ozel dagitim, test).
  if command -v curl >/dev/null 2>&1; then
    TMP_LIB="$(mktemp)"
    _CURL_AUTH=()
    [[ -n "${E1_GHCR_TOKEN:-}" ]] && _CURL_AUTH=(-H "Authorization: token ${E1_GHCR_TOKEN}")
    for _src in \
      "${E1_LIB_URL:-}" \
      "https://raw.githubusercontent.com/enerjione/enerjione-grid/${BRANCH}/infra/scripts/linux/_lib.sh"
    do
      [[ -z "$_src" ]] && continue
      if curl -fsSL "${_CURL_AUTH[@]}" "$_src" -o "$TMP_LIB" 2>/dev/null && [[ -s "$TMP_LIB" ]]; then
        # shellcheck disable=SC1090
        source "$TMP_LIB"
        LIB_LOADED=1
        break
      fi
    done
    rm -f "$TMP_LIB"
  fi
fi
if [[ $LIB_LOADED -eq 0 ]]; then
  echo "HATA: kurulum kutuphanesi (_lib.sh) indirilemedi." >&2
  echo >&2
  if [[ -z "${E1_GHCR_TOKEN:-}" ]]; then
    echo "Sebep muhtemelen kurulum anahtarinin verilmemis olmasi: depo ozeldir." >&2
    echo "Dogru kullanim (ANAHTAR yerine size verilen anahtari yazin):" >&2
    echo >&2
    echo '  TOKEN=ANAHTAR; curl -fsSL -H "Authorization: token $TOKEN" \' >&2
    echo '    https://raw.githubusercontent.com/enerjione/enerjione-grid/main/install.sh \' >&2
    echo '    | sudo E1_GHCR_TOKEN=$TOKEN bash' >&2
  else
    echo "Anahtar verilmis ama kabul edilmedi — suresi dolmus veya yetkisi yetersiz olabilir." >&2
    echo "Anahtarin 'Contents: Read' ve 'Packages: Read' yetkisi olmali." >&2
  fi
  exit 1
fi

# ---- Root kontrolu --------------------------------------------------------
e1_require_root "$@"
e1_enable_error_trap
E1_HELP_HINT="Kurulum kilavuzu: docs/SAHA-KURULUM.md"

# ---- Banner + kurulum ozeti ----------------------------------------------
clear 2>/dev/null || true
e1_banner

# Surum burada YAZILMAZ: curl | bash modunda repo henuz klonlanmadigi icin
# numarali surum bilinmiyor. Kurulum sonu ozetinde gosteriliyor.
e1_box "KURULUM"
e1_kv "Hedef dizin" "${INSTALL_DIR}"
[[ -n "${SUDO_USER:-}" ]] && e1_kv "Dosya sahibi" "${SUDO_USER}"
e1_rule "─"

e1_info "Kurulum basliyor..."

e1_set_steps 6

# ---- Kurulum anahtari -----------------------------------------------------
# Depo private oldugu icin hem `git clone` hem `docker login ghcr.io` yetki
# ister; ikisi de AYNI anahtari kullanir.
#
# KURULUM SESSIZDIR — hicbir sey SORULMAZ. Tum girdiler kurulum aracindan
# (EnerjiOne Kurulum Araci — ayri repo) veya ortam degiskeninden gelir.
# Sebep: saha
# kurulumunda ekran basinda kimse beklemesin ve kurulum bir soruda
# takilip kalmasin. Soru sormak eskiden `curl | bash` altinda kurulumu
# tamamen kilitleyebiliyordu.
#
# Sirayla: E1_GHCR_TOKEN ortam degiskeni -> /etc/enerjione-grid/install.env
#          -> mevcut kurulumun .env'i
E1_TOKEN="$(E1_GHCR_TOKEN="${E1_GHCR_TOKEN:-}" e1_resolve_token "${INSTALL_DIR}/.env")"
if [[ -z "$E1_TOKEN" && -f /etc/enerjione-grid/install.env ]]; then
  E1_TOKEN="$(sed -n 's/^[[:space:]]*E1_GHCR_TOKEN[[:space:]]*=[[:space:]]*"\?\([^"#]*\)"\?.*/\1/p' \
    /etc/enerjione-grid/install.env | tail -1 | tr -d '[:space:]')"
fi
if [[ -z "$E1_TOKEN" ]]; then
  e1_warn "Kurulum anahtari yok — depo ve imajlar ozel oldugu icin kurulum basarisiz olur."
  e1_hint "Anahtari kurulum aracindan girin veya:"
  e1_hint "  sudo E1_GHCR_TOKEN=<anahtar> bash install.sh"
fi

# ---- 1/6: Pre-req paketler -----------------------------------------------
e1_step "Pre-req paketler kontrol ediliyor (git, curl, openssl)..."
MISSING=()
for pkg in git curl openssl; do
  if ! command -v "$pkg" >/dev/null 2>&1; then
    MISSING+=("$pkg")
  fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  e1_info "Eksik paketler: ${MISSING[*]} — apt ile kuruluyor..."
  # `-qq` KULLANMIYORUZ: tamamen sessiz kaliyor ve yavas baglantida dakikalarca
  # hicbir cikti olmadigi icin kullanici kurulumun dondugunu saniyor.
  # e1_apt_install: ilerlemeyi gosterir (e1_run), ALAKASIZ bozuk depolar
  # yuzunden durmaz ama paket gercekten kurulamazsa durur. Sahada Chrome
  # deposunun eksik GPG anahtari tum kurulumu oldurmustu; bkz. _lib.sh.
  e1_apt_install "Paketler kuruluyor (${MISSING[*]})" \
    "${MISSING[@]}" ca-certificates
else
  e1_ok "Tum pre-req'ler hazir."
fi

# ---- 2/6: Kaynak hazirla --------------------------------------------------
# UC MOD var:
#   paket : dosyalar .deb ile geldi — git YOK, indirilecek bir sey de yok
#   git   : mevcut klon — fetch + tag checkout
#   yeni  : bos makine — klonla
#
# Paket modu ONCE kontrol edilir: eskiden bu durum "dizin var ama git repo
# degil" hatasina dusuyordu ve `enerjione-grid setup` daha ilk adimda oluyordu.
E1_PACKAGE_MODE=0
if [[ ! -d "${INSTALL_DIR}/.git" ]] && [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
  E1_PACKAGE_MODE=1
fi

if [[ $E1_PACKAGE_MODE -eq 1 ]]; then
  e1_step "Paket kurulumu dogrulaniyor: ${INSTALL_DIR}"
  cd "${INSTALL_DIR}"
  e1_ok "Dosyalar paketle geldi — indirme gerekmiyor."
  e1_hint "Surum yukseltmesi yeni bir .deb kurmakla yapilir."
elif [[ -d "${INSTALL_DIR}/.git" ]]; then
  e1_step "Repo hazirlaniyor: ${INSTALL_DIR}"
  e1_info "Repo zaten mevcut; fetch + checkout..."
  cd "${INSTALL_DIR}"
  if ! git diff --quiet || ! git diff --cached --quiet; then
    e1_warn "Lokal degisiklik var; guncelleme ATLANDI. Mevcut commit ile devam."
  else
    e1_run "Guncellemeler indiriliyor" \
      e1_git_auth "$E1_TOKEN" git fetch --tags --prune origin
  fi
else
  if [[ -e "${INSTALL_DIR}" ]]; then
    e1_die "${INSTALL_DIR} mevcut ama ne git repo ne de paket kurulumu.\n  Once silin veya farkli bir INSTALL_DIR verin."
  fi
  e1_step "Repo hazirlaniyor: ${INSTALL_DIR}"
  mkdir -p "$(dirname "${INSTALL_DIR}")"
  # `--quiet` DEGIL `--progress`: klonlama yavas baglantida dakikalar surer,
  # sessiz kalirsa kullanici kurulumun kilitlendigini saniyor. --progress
  # stderr terminal olmasa bile yuzde gosterir.
  # Token URL'e GOMULMEZ (bkz. e1_git_auth): gomulseydi .git/config'te kalici
  # olarak dururdu ve her `git remote -v` ciktisinda gorunurdu.
  e1_run "Kaynak kod indiriliyor" \
    e1_git_auth "$E1_TOKEN" git clone --branch "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}" \
    || e1_die "Kaynak kod indirilemedi.\n\n  En sik sebep: kurulum anahtari yanlis veya suresi dolmus.\n  Yeniden denemek icin:\n    sudo E1_GHCR_TOKEN=<anahtar> bash install.sh"
  cd "${INSTALL_DIR}"
  e1_git_auth "$E1_TOKEN" git fetch --tags --quiet origin || true
fi

# Kararli kanal: en son yayin tag'i. Paket modunda surum zaten .deb ile
# sabitlenmis; checkout edilecek bir sey yok.
if [[ $E1_PACKAGE_MODE -eq 0 ]]; then
  TARGET_REF="$(e1_target_ref)"
  if git rev-parse --verify --quiet "refs/tags/${TARGET_REF}" >/dev/null; then
    # Tag'e gecince HEAD detached olur — saha cihazinda dogru davranis budur:
    # cihaz bir dalin ucunu degil, TEST EDILMIS bir yayini calistirir.
    git checkout --quiet --detach "refs/tags/${TARGET_REF}"
    e1_info "Yayin surumu: ${TARGET_REF}"
  elif git rev-parse --verify --quiet "refs/remotes/origin/${TARGET_REF}" >/dev/null; then
    git checkout --quiet -B "${TARGET_REF}" "origin/${TARGET_REF}"
    e1_warn "Yayin tag'i bulunamadi — '${TARGET_REF}' dali kullaniliyor (gelistirme surumu)."
  else
    e1_warn "'${TARGET_REF}' bulunamadi; mevcut commit ile devam ediliyor."
  fi
fi

# Surum artik biliniyor — bundan sonraki tum adim basliklarinda gorunur.
E1_VERSION_LABEL="$(e1_version "${INSTALL_DIR}")"
e1_ok "Kaynak hazir — surum ${E1_VERSION_LABEL}"

# Lisans makine bagi host'un sabit OS kimligine dayanir. USB, disk, RAM, MAC
# veya container ID kullanilmaz; bunlar degisince lisans patlamamali.
if [[ ! -s /etc/machine-id ]]; then
  e1_die "/etc/machine-id yok veya bos. Lisans makine kimligi guvenli uretilmeden kurulum devam edemez."
fi
e1_ok "Sabit host machine-id hazir (USB/disk/ag degisikliklerinden etkilenmez)."

# Dizin sahipligini cagiran kullaniciya devret (sudo ile root:root oldu).
if [[ -n "$(e1_target_user)" ]]; then
  e1_info "Dizin sahipligi '$(e1_target_user)' kullanicisina devrediliyor..."
  e1_chown_target_recursive "${INSTALL_DIR}"
fi

# ---- Saha kimligi (musteri / proje) --------------------------------------
# BURADA soruluyor, sonda degil: Docker kurulumu ve imaj indirme 5-15 dakika
# surer; kurulumcu o sirada makinenin basindan ayrilir. Soru sonda olsaydi
# kurulum yarim kalirdi. Repo yeni klonlandi, script erisilebilir.
#
# Amac: her kutunun uzaktan bakim listesinde ADIYLA gorunmesi. Detay ve
# gerekce icin bkz. infra/appliance/setup-site-identity.sh basligi.
if [[ -f "${INSTALL_DIR}/infra/appliance/setup-site-identity.sh" ]]; then
  bash "${INSTALL_DIR}/infra/appliance/setup-site-identity.sh" \
    || e1_warn "Saha kimligi adimi tamamlanamadi; kurulum devam ediyor."
fi

# ---- 3/6: Docker Engine + Compose ----------------------------------------
e1_step "Docker Engine + Compose plugin kontrolu..."
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  e1_ok "Zaten kurulu: $(docker --version | head -1)"
else
  e1_info "Docker yok, kuruluyor..."
  e1_run "Docker Engine indiriliyor ve kuruluyor" \
    bash "${INSTALL_DIR}/infra/scripts/linux/install-docker.sh"
fi

# ---- 4/6: .env (secret'lar) ----------------------------------------------
e1_step ".env hazirlaniyor (secret'lar rastgele uretilir)..."
cd "${INSTALL_DIR}"

# .env yoksa template'ten kopyala.
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  e1_ok ".env olusturuldu (.env.example'dan)."
else
  e1_info ".env zaten mevcut, eksik/placeholder satirlar dolduruluyor (idempotent)."
fi

# Helper: bir key yoksa ekler, varsa placeholder ise gercek deger ile doldurur.
# Placeholder kaliplari: bos, please-change-me*, change-me*, change-this*.
# `sed` replacement icin `|` ayraci kullaniyoruz; degerde `&` varsa escape.
_ensure_env_var() {
  local key="$1"
  local value="$2"
  local escaped_value="${value//&/\\&}"
  if grep -qE "^${key}=" .env; then
    if grep -qE "^${key}=$|^${key}=please-change-me|^${key}=change-me|^${key}=change-this" .env; then
      sed -i "s|^${key}=.*|${key}=${escaped_value}|" .env
      e1_info "${key} guncellendi (placeholder -> rastgele)."
    fi
  else
    echo "${key}=${value}" >> .env
    e1_info "${key} eklendi (.env'de yoktu)."
  fi
}

# Helper: bir env key'i kesin olarak target value'ya ayarlar (placeholder
# olsun olmasin, var olsun olmasin). CORS_ORIGINS gibi `*` default'unu
# overwrite etmek icin gerekli.
_set_env_var() {
  local key="$1"
  local value="$2"
  local escaped_value="${value//&/\\&}"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${escaped_value}|" .env
  else
    echo "${key}=${value}" >> .env
  fi
}

SK=$(openssl rand -hex 32)
IT=$(openssl rand -hex 32)
PP=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
RP=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
NB=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
NW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
NG=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
_ensure_env_var "SECRET_KEY" "$SK"
_ensure_env_var "INTERNAL_SERVICE_TOKEN" "$IT"
_ensure_env_var "POSTGRES_PASSWORD" "$PP"
_ensure_env_var "RABBITMQ_PASSWORD" "$RP"
_ensure_env_var "NATS_BACKEND_PASSWORD" "$NB"
_ensure_env_var "NATS_WORKER_PASSWORD" "$NW"
_ensure_env_var "NATS_GATEWAY_PASSWORD" "$NG"

# Postgres kimligi TEK kanonik isimdir: enerjione_grid. Eski kurulumlarda
# .env'de 'enerjione' / 'hsl' / 'horstman' kalmis olabilir; burada kesin
# olarak hizaliyoruz. Volume zaten eski isimle init edilmisse asagidaki
# db-preflight adimi ALTER ROLE/DATABASE RENAME ile onu da tasir.
# Ilk kurulumda volume bos oldugu icin postgres imaji DB'yi bu isimle
# kendisi olusturur (docker-compose.yml POSTGRES_DB).
_set_env_var "POSTGRES_DB" "enerjione_grid"
_set_env_var "POSTGRES_USER" "enerjione_grid"

# --- Imaj kaynagi + surum -------------------------------------------------
# E1_VERSION 'latest' DEGIL, checkout edilen surum olmali: rollback ancak
# imaj etiketi somut bir semver ise mumkun. E1_REGISTRY compose'un imajlari
# nereden cekecegini belirler.
_set_env_var "E1_REGISTRY" "${E1_REGISTRY:-$E1_REGISTRY_DEFAULT}"
if [[ "$E1_VERSION_LABEL" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  _set_env_var "E1_VERSION" "$E1_VERSION_LABEL"
else
  # Surum okunamadi (yayin oncesi checkout, bozuk VERSION dosyasi): 'latest'
  # ile devam et ama bunu gizleme — rollback imkani olmadigini kullanici bilsin.
  _set_env_var "E1_VERSION" "latest"
  e1_warn "Surum semver olarak okunamadi; imaj etiketi 'latest' olacak (rollback zorlasir)."
fi

# Kurulum anahtarini .env'e yaz — update.sh sonraki guncellemelerde bunu
# kullanir, kurulumcuya bir daha sorulmaz. .env zaten chmod 600.
if [[ -n "$E1_TOKEN" ]]; then
  _set_env_var "GHCR_TOKEN" "$E1_TOKEN"
  _set_env_var "GHCR_USERNAME" "${E1_GHCR_USERNAME:-x-access-token}"
fi

# APP_ENV — install.sh production deploy yapiyor, default production.
# CORS_ORIGINS '*' bu env'de backend tarafindan reddedilir (config.py guard).
# VPS IP'sini otomatik tespit edip whitelist'e koyariz; kullanici sonradan
# domain ekleyince .env'i elle duzenler.
_set_env_var "APP_ENV" "production"

# CORS_ORIGINS: VPS IP + localhost. Default '*' production'da reddedilir.
# IP tespiti kritik: bos donerse browser'in WS Origin check'ini gecemez (403).
# Bu yuzden detect basarisiz olursa fail-fast yap, kullanici elle girsin.
DETECTED_IP="$(e1_detect_ip 2>/dev/null || true)"
if [[ -z "$DETECTED_IP" ]] || [[ "$DETECTED_IP" == "<vds-ip>" ]] || [[ "$DETECTED_IP" == "127.0.0.1" ]]; then
  e1_warn "VPS IP otomatik tespit edilemedi (hostname -I + ifconfig.me ikisi de basarisiz)."
  e1_warn "CORS_ORIGINS sadece localhost olacak — browser'dan VPS IP ile baglanan kullanici"
  e1_warn "WebSocket origin check'i gecemez. .env'i elle duzenleyip:"
  e1_warn "  CORS_ORIGINS=http://<vps-ip>,http://localhost,http://127.0.0.1"
  e1_warn "satirini ekleyip backend-api'yi restart edin."
  CORS_DEFAULT="http://localhost,http://127.0.0.1"
else
  e1_info "VPS IP tespit edildi: ${DETECTED_IP}"
  CORS_DEFAULT="http://${DETECTED_IP},http://localhost,http://127.0.0.1"
fi
# Sadece placeholder/yildiz/bos ise overwrite et; kullanici manuel girdiyse koru.
CURRENT_CORS="$(grep -E '^CORS_ORIGINS=' .env | cut -d= -f2- || echo '')"
if [[ -z "$CURRENT_CORS" ]] || [[ "$CURRENT_CORS" == "*" ]]; then
  _set_env_var "CORS_ORIGINS" "$CORS_DEFAULT"
  e1_info "CORS_ORIGINS: ${CORS_DEFAULT}"
else
  e1_info "CORS_ORIGINS korundu (manuel set): ${CURRENT_CORS}"
fi

# Sanity check: hicbir kritik secret hala placeholder olmasin.
for k in SECRET_KEY INTERNAL_SERVICE_TOKEN POSTGRES_PASSWORD RABBITMQ_PASSWORD \
         NATS_BACKEND_PASSWORD NATS_WORKER_PASSWORD NATS_GATEWAY_PASSWORD; do
  v="$(grep -E "^${k}=" .env | cut -d= -f2- || echo '')"
  if [[ -z "$v" ]] || [[ "$v" == please-change-me* ]] || [[ "$v" == change-me* ]] || [[ "$v" == change-this* ]]; then
    e1_die "${k} hala placeholder/bos! .env'i kontrol edin: ${INSTALL_DIR}/.env"
  fi
done

chmod 600 .env
e1_chown_target .env
e1_ok ".env hazir (chmod 600, sahip: $(e1_target_user || echo root))."

# FCM service account JSON — mobil app push bildirim icin. Opsiyonel:
# yoksa FCM devre disi kalir (email/SMS calismaya devam eder).
# Ama compose mount edebilmek icin dosya MUTLAKA var olmali (yoksa Docker
# bind mount'u DIZIN olarak yaratir, NATS conf kazasi gibi).
# Cozum: dosya yoksa "disabled placeholder" yarat — fcm.py JSON parse
# basarisiz olur, "FCM yuklenirken hata" log atar ama devam eder.
# GUVENLIK: Eski yarim migration veya elle bind mount kazasi nedeniyle
# 'fcm-service-account.json' bir DIZIN olarak kalabilir (docker bunu
# 'dosya yok' tespitinde otomatik olarak dizin yaratir). Eger dizinse
# sil; dosya degilse placeholder yaz.
if [[ -d fcm-service-account.json ]]; then
  e1_warn "fcm-service-account.json bir DIZIN — siliniyor (yanlis bind mount kalintisi)."
  rm -rf fcm-service-account.json
fi
if [[ ! -f fcm-service-account.json ]]; then
  e1_info "fcm-service-account.json yok — placeholder olusturuluyor (FCM devre disi)."
  e1_info "Mobil push icin Firebase Console > Project Settings > Service Accounts >"
  e1_info "Generate new private key, indirilen JSON'u ${INSTALL_DIR}/fcm-service-account.json"
  e1_info "olarak kaydedin, sonra: sudo bash update.sh backend"
  cat > fcm-service-account.json <<'PLACEHOLDER'
{
  "_comment": "FCM service account placeholder. Bu dosya bos JSON; backend fcm.py bunu yukleyemez ve FCM'i devre disi birakir. Gercek service account icin Firebase Console'dan indirip uzerine yazin.",
  "type": "service_account",
  "project_id": "",
  "private_key_id": "",
  "private_key": "",
  "client_email": "",
  "client_id": "",
  "_disabled": true
}
PLACEHOLDER
  # chmod 644: container'daki backend user'i (uid genelde 10001/appuser)
  # host uid 1000'in chmod 600 dosyasini okuyamaz → PermissionError. 644
  # dosyayi world-readable yapar (key icerigine erisim icin zaten host
  # erisimi gerek; multi-tenant host'ta degiliz, single-app deployment).
  chmod 644 fcm-service-account.json
  e1_chown_target fcm-service-account.json
  e1_warn "FCM placeholder yaratildi. Gercek JSON yuklenmeden mobil push CALISMAZ."
else
  e1_info "fcm-service-account.json mevcut — FCM aktif olacak."
  # Mevcut dosya chmod 600 olabilir (eski install veya elle scp'lendi).
  # Container okuyamaz; 644'e cek.
  current_mode="$(stat -c %a fcm-service-account.json 2>/dev/null || echo 600)"
  if [[ "$current_mode" != "644" ]]; then
    chmod 644 fcm-service-account.json
    e1_info "fcm-service-account.json chmod 644 yapildi (container erisimi icin)."
  fi
fi

# NATS bcrypt hash render — host python3 + bcrypt ile.
if [[ ! -f infra/nats/nats-server.conf ]]; then
  e1_info "NATS bcrypt hash'leri uretiliyor (python3 + bcrypt)..."
  if ! python3 -c "import bcrypt" 2>/dev/null; then
    e1_info "python3-bcrypt eksik, apt ile kuruluyor..."
    # Bozuk ucuncu taraf depolari kurulumu durdurmasin; bkz. _lib.sh.
    e1_apt_install "python3-bcrypt kuruluyor" python3-bcrypt
  fi
  set -a; source .env; set +a
  _bcrypt() {
    python3 -c "import sys, bcrypt; print(bcrypt.hashpw(sys.stdin.buffer.read().rstrip(b'\n'), bcrypt.gensalt(rounds=11)).decode())" <<<"$1"
  }
  HASH_G=$(_bcrypt "${NATS_GATEWAY_PASSWORD}")
  HASH_B=$(_bcrypt "${NATS_BACKEND_PASSWORD}")
  HASH_W=$(_bcrypt "${NATS_WORKER_PASSWORD}")
  [[ -z "$HASH_G" || -z "$HASH_B" || -z "$HASH_W" ]] && e1_die "bcrypt uretilemedi."
  cp infra/nats/nats-server.conf.template infra/nats/nats-server.conf
  sed -i "s|{{NATS_GATEWAY_BCRYPT_HASH}}|${HASH_G//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_BACKEND_BCRYPT_HASH}}|${HASH_B//&/\\&}|" infra/nats/nats-server.conf
  sed -i "s|{{NATS_WORKER_BCRYPT_HASH}}|${HASH_W//&/\\&}|" infra/nats/nats-server.conf
  e1_chown_target infra/nats/nats-server.conf
  e1_ok "NATS auth konfigurasyonu render edildi."
else
  e1_ok "NATS auth konfigurasyonu zaten mevcut."
fi

# ---- 5/6: Build + up ------------------------------------------------------
e1_step "Servis imajlari hazirlaniyor..."

# Imajlari CI derler, cihaz sadece INDIRIR. Boylece saha kurulumu dakikalar
# yerine saniyeler surer ve her cihazda bit-bit ayni imaj calisir (derleme
# ortami farkliliklarindan dogan "bende calisiyordu" sinifi biter).
# Cekilemezse yerel derlemeye duseriz: internet kisitli veya token verilmemis
# kurulumlar da calismaya devam etsin.
IMAGE_SOURCE="build"
if [[ "${E1_BUILD:-0}" != "1" ]]; then
  # Anahtar zaten kurulum basinda BIR KEZ alindi (depo klonu icin de ayni
  # anahtar kullanildi); burada tekrar SORULMAZ.
  if [[ -n "$E1_TOKEN" ]]; then
    GHCR_TOKEN="$E1_TOKEN" e1_ghcr_login .env || true
  else
    e1_info "Kurulum anahtari yok — imajlar bu cihazda derlenecek."
  fi
  if e1_run "Hazir imajlar indiriliyor" docker compose pull; then
    IMAGE_SOURCE="pull"
  else
    e1_warn "Imajlar indirilemedi — bu cihazda derlemeye geciliyor."
  fi
fi

if [[ "$IMAGE_SOURCE" == "build" ]]; then
  e1_hint "Derleme 3-8 dakika surer; kurulum devam ediyor, kesmeyin."
  e1_run "Imajlar derleniyor" docker compose build --pull \
    || e1_die "Imaj derlemesi basarisiz. Detay yukarida."
fi

# Postgres'i once tek basina kaldirip kimlik on-kontrolunu yap. Ilk kurulumda
# volume bos -> imaj enerjione_grid rol+DB'sini kendisi olusturur, preflight
# sadece dogrular. Mevcut kuruluma tekrar install.sh calistirilirsa volume
# eski isimle init edilmis olabilir; preflight rename ile hizalar. Bu adim
# olmadan backend 'role does not exist' ile ayaga kalkmaz.
e1_info "Postgres baslatiliyor + kimlik on-kontrolu..."
docker compose up -d postgres
bash infra/scripts/linux/db-preflight.sh \
  || e1_die "Postgres kimlik on-kontrolu basarisiz. Detay yukarida."

# Altyapi (rabbitmq + nats) uygulama servislerinden ONCE ve TEK TEK ayaga
# kaldirilir. Sebep: backend-api bunlara `depends_on: service_healthy` ile
# bagli. Biri saglikli olmazsa duz `docker compose up -d` tek satirlik
# "dependency failed to start: container ... is unhealthy" ile patliyor,
# ERR trap kurulumu kesiyor ve kurulumcu ekranda SEBEBI goremiyordu.
# e1_wait_healthy her birini ayri bekler ve basarisizlikta container'in son
# 40 satir logunu ekrana doker.
e1_info "Altyapi servisleri baslatiliyor (rabbitmq, nats)..."
docker compose up -d rabbitmq nats \
  || e1_die "rabbitmq/nats container'lari baslatilamadi. Detay yukarida."

infra_failed=""
e1_wait_healthy postgres 120 || e1_repair_service postgres 150 || infra_failed+=" postgres"
e1_wait_healthy rabbitmq 240 || e1_repair_service rabbitmq 240 || infra_failed+=" rabbitmq"
e1_wait_healthy nats     120 || e1_repair_service nats     120 || infra_failed+=" nats"

if [[ -n "$infra_failed" ]]; then
  # Otomatik onarim da yetmedi: kurulumcu elle ugrasmasin, tek dosyalik
  # teshis raporu birakip destege gondermesini isteriz.
  report="$(e1_write_diag_report "${INSTALL_DIR}" "$infra_failed")"
  e1_die "Altyapi servisleri hazir olmadi:${infra_failed}\n\n  Otomatik onarim denendi ama sonuc vermedi.\n  Teshis raporu olusturuldu:\n\n    ${report}\n\n  Bu dosyayi teknik destege gonderin."
fi

e1_info "Uygulama servisleri baslatiliyor..."
if ! docker compose up -d; then
  e1_err "Servis durumu:"
  docker compose ps 2>&1 | sed 's/^/      /' >&2 || true
  e1_die "Servisler baslatilamadi. Loglar:\n    cd ${INSTALL_DIR} && sudo docker compose logs --tail 60"
fi
e1_ok "Stack ayakta."

# ---- 6/6: Backend healthy bekle + installer seed -------------------------
e1_step "Backend hazir olana kadar bekleniyor (max 2 dk)..."
backend_ready=0
for i in $(seq 1 60); do
  if docker compose exec -T backend-api curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    e1_ok "backend-api hazir (${i}. denemede)."
    backend_ready=1
    break
  fi
  sleep 2
done

if [[ $backend_ready -eq 1 ]]; then
  e1_info "Installer hesabi olusturuluyor (5 deneme, her birinde stdout/stderr gosterilir)..."
  # Backend healthy olsa bile schema/migration 1-2 saniye sonrasi tamamlanabilir.
  # 5 kez 3 saniye arayla dene. Cikti gizlenmez — kullanici hata sebebini gorur.
  seed_ok=0
  for attempt in 1 2 3 4 5; do
    # Subshell + `|| true` ile `set -e` altinda kirilmaz; cikti tee ile gosterilir.
    seed_output="$(docker compose exec -T backend-api python -m scripts.seed_installer 2>&1)" || true
    if echo "$seed_output" | grep -qE 'Installer user (created|password reset)'; then
      echo "$seed_output" | grep -E 'Installer user' | sed 's/^/      /'
      e1_ok "Installer hesabi hazir (${attempt}. denemede)."
      seed_ok=1
      break
    fi
    e1_warn "seed_installer denemesi ${attempt}/5 basarisiz. Cikti:"
    echo "$seed_output" | sed 's/^/      /'
    if [[ $attempt -lt 5 ]]; then
      sleep 3
    fi
  done
  if [[ $seed_ok -eq 0 ]]; then
    e1_warn "Installer hesabi otomatik olusturulamadi. Manuel calistirin:"
    e1_warn "  cd ${INSTALL_DIR}"
    e1_warn "  sudo docker compose exec -T backend-api python -m scripts.seed_installer"
  fi
else
  e1_warn "backend-api 2 dakikada hazir olmadi. Loglara bakin:"
  e1_warn "  cd ${INSTALL_DIR} && docker compose logs backend-api"
fi

# ---- systemd servis kaydi ------------------------------------------------
# SORU SORULMAZ. systemd kaydi bir tercih degil gereklilik: kaydedilmezse
# cihaz yeniden baslatildiginda sistem AYAGA KALKMAZ ve sahadaki kutu
# sessizce olu kalir. Eskiden sorulup atlanabiliyordu; tam da bu yuzden
# atlanmis kurulumlar cikti.
# Istemeyen kapatabilir:  E1_SYSTEMD=0 sudo bash install.sh
if [[ "${E1_SYSTEMD:-1}" == "0" ]]; then
  e1_info "systemd kaydi E1_SYSTEMD=0 ile atlandi — cihaz acilista OTOMATIK BASLAMAZ."
elif [[ ! -f /etc/systemd/system/enerjione-grid.service ]]; then
  if [[ -f "${INSTALL_DIR}/infra/systemd/setup-systemd.sh" ]]; then
    bash "${INSTALL_DIR}/infra/systemd/setup-systemd.sh" || \
      e1_warn "systemd kaydi tamamlanamadi; cihaz acilista otomatik baslamayabilir."
  fi
fi

# ---- Gateway kurulum ajani (e1-gwd) --------------------------------------
# Arayuzdeki "gateway'i bu cihaza kur" akisi icin gerekli. Backend container'i
# Docker'a erisemez (bilincli); ajan host'ta root olarak compose'u calistirir.
# Appliance'a ozgu degil, VPS'te de kullanilir. Basarisiz olursa kurulum
# durmaz — yalnizca o akis devre disi kalir, "baska cihaza kur" calisir.
if [[ -f "${INSTALL_DIR}/infra/appliance/setup-gateway-agent.sh" ]]; then
  INSTALL_DIR="$INSTALL_DIR" bash "${INSTALL_DIR}/infra/appliance/setup-gateway-agent.sh" \
    >/dev/null 2>&1 && e1_ok "Gateway kurulum ajani (e1-gwd) aktif." \
    || e1_warn "e1-gwd kurulamadi; 'bu cihaza kur' secenegi calismayacak."
fi

# ---- Appliance (mini PC) modu -------------------------------------------
# Sifresiz WiFi AP (musteri adiyla, or. "E1GRID-TPAO"), e1-grid.local mDNS ve UI'dan IP/DNS
# ayari (e1-netd ajani).
#
# OTOMATIK KARAR: makinede WiFi karti varsa bu bir saha mini PC'sidir ->
# appliance modu kurulur. Bulut sunucularinda/VPS'lerde WiFi karti olmadigi
# icin oralarda hic devreye girmez. Boylece tek komut hem VPS'te hem mini
# PC'de dogru olani yapar.
#
# Manuel override:
#   E1_APPLIANCE=1  -> WiFi olmasa bile kur (USB adaptor sonra takilacaksa)
#   E1_APPLIANCE=0  -> WiFi olsa bile kurma (test laptop'u vb.)
APPLIANCE_WANTED=0
case "${E1_APPLIANCE:-auto}" in
  1) APPLIANCE_WANTED=1 ;;
  0) e1_info "Appliance modu E1_APPLIANCE=0 ile devre disi birakildi." ;;
  *)
    if e1_has_wifi; then
      # SORU SORULMAZ: WiFi karti olan makine saha mini PC'sidir ve appliance
      # katmani (AP + mDNS + e1-netd) o cihazin ERISIM YOLUDUR — kurulmazsa
      # IP bilinmediginde cihaza hic girilemez. Kurulumu operator onayina
      # birakmak, tek komutla sahaya cikan bir kutuda en kritik parcayi
      # atlanabilir yapiyordu. Istemeyen `E1_APPLIANCE=0` ile kapatir.
      e1_info "WiFi arayuzu tespit edildi — saha mini PC'si; appliance modu kuruluyor."
      e1_info "Istemiyorsaniz: E1_APPLIANCE=0 sudo bash install.sh"
      APPLIANCE_WANTED=1
    else
      # SESSIZ ATLAMA YOK: bu adim atlaninca ag ajani (e1-netd) kurulmaz ve
      # arayuzdeki "Ag Ayarlari" sayfasi CALISMAZ. Sanal makinelerde WiFi
      # karti olmadigi icin buraya sik dusuluyor ve sayfanin neden bos
      # geldigi anlasilmiyor.
      e1_info "WiFi arayuzu yok — sunucu kurulumu kabul edildi, appliance modu atlandi."
      e1_hint "Bunun sonucu: Muhendislik > Sistem > Ag Ayarlari sayfasi calismaz"
      e1_hint "(IP/DNS ve WiFi yonetimi icin gereken e1-netd ajani kurulmadi)."
      e1_hint "Ag ayarlarini arayuzden yonetmek isterseniz WiFi karti olmadan da kurulabilir:"
      e1_hint "  cd ${INSTALL_DIR} && sudo SKIP_AP=1 bash infra/appliance/setup-appliance.sh"
      e1_hint "veya bastan: E1_APPLIANCE=1 sudo bash install.sh"
    fi
    ;;
esac
if [[ $APPLIANCE_WANTED -eq 1 ]]; then
  if [[ -f "${INSTALL_DIR}/infra/appliance/setup-appliance.sh" ]]; then
    bash "${INSTALL_DIR}/infra/appliance/setup-appliance.sh" \
      || e1_warn "Appliance kurulumu tamamlanamadi; detay yukarida."
    # Ag ajani dizini (state/request) yeni olusmus olabilir; backend'in
    # mount'u gormesi icin recreate. Zaten dogruysa Docker no-op yapar.
    docker compose up -d backend-api >/dev/null 2>&1 || true
  else
    e1_warn "infra/appliance/setup-appliance.sh bulunamadi (repo eski olabilir)."
  fi
fi

# ---- Kiosk modu (masaustu olan cihazlar) ---------------------------------
# Cihaz acilinca arayuz kendiliginden tam ekran gelsin ve operator ayri,
# YETKISIZ bir hesapla girsin. Ekran yoneticisi yoksa (VPS/sunucu) script
# hicbir sey yapmadan doner — appliance moduna da bagli DEGIL, cunku
# masaustlu bir kurulumda WiFi karti olmayabilir.
if [[ -f "${INSTALL_DIR}/infra/appliance/setup-kiosk.sh" ]]; then
  bash "${INSTALL_DIR}/infra/appliance/setup-kiosk.sh" \
    || e1_warn "Kiosk modu tamamlanamadi; kurulum devam ediyor."
fi

# ---- Uzaktan bakim VPN'i (Tailscale) -------------------------------------
# Anahtar tanimliysa cihaz kurulumda OTOMATIK tailnet'e katilir; boylece
# saha PC'sine port acmadan uzaktan bakim yapilabilir. Anahtar yoksa script
# hicbir sey yapmadan doner — anahtarsiz kurulumlar etkilenmez.
#
# Anahtar kaynagi (oncelik sirasiyla):
#   1. Ortam degiskeni       -> E1_TAILSCALE_AUTHKEY=... sudo bash install.sh
#   2. /etc/enerjione-grid/install.env  -> cihaza OZEL anahtar. Repo disindadir,
#      yeniden kurulumda silinmez, disk imajina gomulebilir.
#   3. ${INSTALL_DIR}/.env   -> mevcut kurulumu guncellerken.
# Anahtar repoya ASLA yazilmaz (.env ve /etc/... git disinda). SIFIR
# DOKUNUSLU kurulum icin `packaging/make-provisioner.sh` ile uretilen tek
# dosyayi kullanin: anahtarlari o dosya tasir ve (2)'yi kendisi yazar.
_e1_read_key_from() {
  # `source` etmiyoruz: dosyada kurulum ortamini bozabilecek baska
  # degiskenler olabilir; sadece ilgili satiri cekiyoruz.
  [[ -f "$1" ]] || return 1
  sed -n 's/^[[:space:]]*E1_TAILSCALE_AUTHKEY[[:space:]]*=[[:space:]]*"\?\([^"#]*\)"\?.*/\1/p' \
    "$1" | tail -1 | tr -d '[:space:]'
}
if [[ -z "${E1_TAILSCALE_AUTHKEY:-}" ]]; then
  for _keyfile in /etc/enerjione-grid/install.env "${INSTALL_DIR}/.env"; do
    _k="$(_e1_read_key_from "$_keyfile" || true)"
    if [[ -n "$_k" ]]; then
      E1_TAILSCALE_AUTHKEY="$_k"
      export E1_TAILSCALE_AUTHKEY
      e1_info "Tailscale anahtari bulundu: ${_keyfile}"
      break
    fi
  done
  unset _keyfile _k
fi
# Diger Tailscale ayarlari da ayni dosyadan gelebilsin (etiket, SSH...).
if [[ -f /etc/enerjione-grid/install.env ]]; then
  for _var in E1_TAILSCALE_TAGS E1_TAILSCALE_SSH E1_TAILSCALE_ACCEPT_DNS \
              E1_TAILSCALE_HOSTNAME E1_TAILSCALE_HOSTNAME_PREFIX; do
    if [[ -z "${!_var:-}" ]]; then
      _v="$(sed -n "s/^[[:space:]]*${_var}[[:space:]]*=[[:space:]]*\"\?\([^\"#]*\)\"\?.*/\1/p" \
        /etc/enerjione-grid/install.env | tail -1 | tr -d '[:space:]')"
      [[ -n "$_v" ]] && export "${_var}=${_v}"
    fi
  done
  unset _var _v
fi
# Anahtar ORTAM DEGISKENIYLE verildiyse KALICILASTIR: kurulumcu ayni anahtari
# bir daha yazmasin, sonraki `install.sh`/`update.sh` calistirmalari onu
# kendiliginden bulsun. (Provisioner dosyasiyla kurulumda bu blok no-op'tur:
# anahtar zaten ayni dosyadan gelmistir.)
if [[ -n "${E1_TAILSCALE_AUTHKEY:-}" ]]; then
  _persisted="$(_e1_read_key_from /etc/enerjione-grid/install.env || true)"
  if [[ "$_persisted" != "$E1_TAILSCALE_AUTHKEY" ]]; then
    mkdir -p /etc/enerjione-grid
    # Dosya varsa eski anahtar satirini cikarip yenisini ekle; icindeki
    # diger ayarlar (saha bilgisi, etiket...) KORUNUR.
    _tmp="$(mktemp)"
    if [[ -f /etc/enerjione-grid/install.env ]]; then
      grep -v '^[[:space:]]*E1_TAILSCALE_AUTHKEY[[:space:]]*=' \
        /etc/enerjione-grid/install.env > "$_tmp" || true
    else
      printf '# EnerjiOne Grid — kalici kurulum ayarlari (git disinda).\n' > "$_tmp"
    fi
    printf 'E1_TAILSCALE_AUTHKEY=%s\n' "$E1_TAILSCALE_AUTHKEY" >> "$_tmp"
    # Once izinleri kis, sonra tasi: anahtar hicbir an 644 ile durmasin.
    chmod 600 "$_tmp"
    mv "$_tmp" /etc/enerjione-grid/install.env
    chmod 600 /etc/enerjione-grid/install.env
    e1_ok "Tailscale anahtari kalici olarak kaydedildi (/etc/enerjione-grid/install.env)."
    e1_hint "Sonraki kurulum/guncellemelerde anahtari tekrar vermeniz gerekmez."
  fi
  unset _persisted _tmp
fi

if [[ -f "${INSTALL_DIR}/infra/appliance/setup-tailscale.sh" ]]; then
  bash "${INSTALL_DIR}/infra/appliance/setup-tailscale.sh" \
    || e1_warn "Tailscale adimi tamamlanamadi; kurulum devam ediyor."
fi

# ---- Uzaktan bakim izni ajani (e1-rad) -----------------------------------
# Uzaktan erisim VARSAYILAN KAPALI; musterinin yetkili kullanicisi (engineer)
# arayuzden sureli izin verir ve sure dolunca erisim kendiliginden kapanir.
# Sureyi HOST'ta root ile calisan bu ajan sayar — backend/DB kapali olsa bile.
#
# setup-tailscale.sh bunu zaten cagiriyor; buradaki cagri tailscale hic
# kurulmadigi (anahtar verilmedigi) kurulumlar icin: paylasilan dizin dogru
# izinlerle olussun ki Docker bind mount'u root:root 0755 yaratmasin ve
# backend "dizine yazilamiyor" demesin. Idempotent, mahsup YAZMAZ.
if [[ -f "${INSTALL_DIR}/infra/appliance/setup-remote-access.sh" ]]; then
  INSTALL_DIR="$INSTALL_DIR" bash "${INSTALL_DIR}/infra/appliance/setup-remote-access.sh" \
    >/dev/null 2>&1 && e1_ok "Uzaktan bakim izni ajani (e1-rad) aktif." \
    || e1_warn "e1-rad kurulamadi; 'Uzaktan Bakim' sayfasi calismayacak."
fi

# ---- Final rehber ---------------------------------------------------------
e1_step_done
VPS_IP="$(e1_detect_ip)"

echo
e1_rule "═"
printf '  %s%sKURULUM TAMAMLANDI%s   %ssurum %s · %s%s\n' \
  "${E1_GREEN}" "${E1_BOLD}" "${E1_RESET}" \
  "${E1_DIM}" "$(e1_version "${INSTALL_DIR}")" "$(e1_total_elapsed)" "${E1_RESET}"
e1_rule "═"

# Saha kimligi tanimliysa ozetin en ustunde gorunsun: kurulumcu yanlis
# musteriye kurulum yaptigini burada fark eder.
if [[ -f /etc/enerjione-grid/site.env ]]; then
  # Tirnakli deger once denenir; boylece "Saha #1" gibi adlarda '#' yorum
  # sanilmaz. Kacirilmis tirnaklar geri acilir.
  _e1_site_var() {
    sed -n \
      -e "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\(.*\)\"[[:space:]]*\$/\1/p" \
      -e "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\([^\"#][^#]*\).*\$/\1/p" \
      /etc/enerjione-grid/site.env | tail -1 | sed -e 's/[[:space:]]*$//' -e 's/\\"/"/g'
  }
  _sc="$(_e1_site_var E1_CUSTOMER_NAME)"
  _ss="$(_e1_site_var E1_SITE_NAME)"
  if [[ -n "$_sc$_ss" ]]; then
    e1_box "SAHA"
    e1_kv "Musteri" "${_sc:-—}"
    e1_kv "Saha" "${_ss:-—}"
  fi
  unset _sc _ss
fi

e1_box "1. ERISIM"
if [[ $APPLIANCE_WANTED -eq 1 ]]; then
  # SSID musteri adindan turetiliyor (bkz. setup-appliance.sh); sabit metin
  # yazmak yerine NetworkManager'daki gercek degeri okuyoruz.
  _ap_ssid="$(nmcli -g 802-11-wireless.ssid connection show e1-grid-ap 2>/dev/null || true)"
  e1_kv "WiFi agi" "${_ap_ssid:-EnerjiOne Grid}  (sifresiz)"
  unset _ap_ssid
  e1_kv "Adres" "http://e1-grid.local   veya   http://10.42.0.1"
  e1_kv "Kablolu" "http://${VPS_IP}/"
else
  e1_kv "Web arayuzu" "http://${VPS_IP}/"
fi

e1_box "2. ILK GIRIS"
e1_kv "Kullanici" "${E1_CYAN}installer${E1_RESET}"
e1_kv "Sifre" "${E1_CYAN}ChangeMe123!${E1_RESET}"
echo
printf '  %s%sIlk giriste sifre degistirme ekrani otomatik acilir.%s\n' \
  "${E1_YELLOW}" "${E1_BOLD}" "${E1_RESET}"
printf '  %sYeni sifreyi guvenli bir yere not edin.%s\n' "${E1_YELLOW}" "${E1_RESET}"

e1_box "3. OTOMATIK CALISMA"
e1_ok "Cihaz kapanip acildiginda sistem kendiliginden ayaga kalkar."
e1_hint "Docker acilista baslar; 12 servisin hepsi 'restart: unless-stopped'."
if [[ -f /etc/systemd/system/enerjione-grid.service ]]; then
  e1_ok "systemd servisi kayitli (enerjione-grid)."
else
  e1_warn "systemd servisi kayitli DEGIL — sistem yine acilista kalkar,"
  e1_warn "ancak 'systemctl' ile yonetim icin: sudo bash infra/systemd/setup-systemd.sh"
fi
e1_hint "Dogrulamak icin: sudo reboot  (2-3 dk sonra arayuzu tekrar acin)"

e1_box "4. GUNLUK KULLANIM"
e1_kv "Guncelleme" "cd ${INSTALL_DIR} && sudo bash update.sh"
e1_kv "Servis durumu" "cd ${INSTALL_DIR} && sudo docker compose ps"
e1_kv "Canli log" "cd ${INSTALL_DIR} && sudo docker compose logs -f"
e1_kv "Kaldirma" "cd ${INSTALL_DIR} && sudo bash uninstall.sh"

if [[ $APPLIANCE_WANTED -eq 1 ]]; then
  e1_box "5. AG AYARI"
  e1_info "Kablolu IP/DNS: Muhendislik > Sistem > Ag Ayarlari"
  e1_hint "Yanlis adres girerseniz WiFi agi her zaman acik kalir;"
  e1_hint "http://10.42.0.1 uzerinden duzeltebilirsiniz."
fi

e1_box "TEKNIK BILGILER"
e1_kv "Backend API" "http://${VPS_IP}/api/v1"
e1_kv "NATS" "nats://${VPS_IP}:4222"
e1_kv "IEC 104" "${VPS_IP}:2404-2406"
e1_kv "Gateway ekleme" "Muhendislik > Cihazlar > Yeni Gateway"
if [[ $APPLIANCE_WANTED -eq 1 ]]; then
  e1_kv "Detayli dokuman" "docs/SAHA-KURULUM.md · docs/APPLIANCE.md"
else
  e1_kv "Detayli dokuman" "docs/DEPLOYMENT.md"
  e1_hint "Mini PC modu icin: sudo bash infra/appliance/setup-appliance.sh"
fi
echo
