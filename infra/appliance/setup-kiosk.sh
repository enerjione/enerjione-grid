#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Kiosk modu (masaustu olan cihazlarda)
# ===========================================================================
# Cihaz acildiginda arayuz KENDILIGINDEN tam ekran gelir. Operator hicbir sey
# yapmaz: parola yok, masaustu yok, tarayici penceresi yok.
#
# ROL AYRIMI — bu kurulumun asil sebebi:
#
#   <musteri>  (bu script olusturur)  Operator hesabi. Kurulum aracinda
#                                     girilen MUSTERI ADINDAN turetilir:
#                                     "TPAO" -> `tpao`. Otomatik giris yapar,
#                                     SADECE tam ekran arayuzu gorur.
#                                     sudo YETKISI YOKTUR, parolasi kilitlidir.
#
#   enerjione (kurulumu yapan)        Yonetim hesabi. SSH, sudo, docker,
#                                     update.sh — hepsi burada kalir.
#                                     Ekranda ASLA otomatik acilmaz.
#
# Boylece panonun basindaki kisi cihazi yonetemez; yonetim ayri bir hesapla
# (yerelde ya da Tailscale uzerinden SSH ile) yapilir.
#
# Calistirma:
#   sudo bash infra/appliance/setup-kiosk.sh
#
# Idempotent: tekrar calistirmak guvenli.
#
# Env:
#   E1_KIOSK_USER   operator hesabi adi
#                   (default: musteri adindan turetilir; yoksa e1-kiosk)
#   E1_CUSTOMER     musteri adi — hesap adi bundan uretilir. Kurulum araci
#                   bunu zaten gonderir; sonradan /etc/enerjione-grid/site.env
#                   icindeki E1_CUSTOMER_NAME'den de okunur.
#   E1_KIOSK_URL    acilacak adres             (default: http://localhost/)
#   E1_KIOSK_BROWSER  tarayici yolu            (default: otomatik tespit)
#   E1_KIOSK=0      kurulumu atla
#
# GUI YOKSA bu script hicbir sey yapmadan 0 doner — VPS/sunucu kurulumlari
# etkilenmez.
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../scripts/linux/_lib.sh" ]]; then
  # shellcheck source=/dev/null
  . "${SCRIPT_DIR}/../scripts/linux/_lib.sh"
else
  e1_info() { printf '  · %s\n' "$*"; }
  e1_ok()   { printf '  ✓ %s\n' "$*"; }
  e1_warn() { printf '  ! %s\n' "$*" >&2; }
  e1_hint() { printf '    %s\n' "$*"; }
  e1_step() { printf '\n== %s\n' "$*"; }
fi

KIOSK_URL="${E1_KIOSK_URL:-http://localhost/}"
SESSION_BIN="/usr/local/bin/e1-kiosk-session"
SITE_ENV="${E1_SITE_ENV:-/etc/enerjione-grid/site.env}"
INSTALL_ENV="${E1_INSTALL_ENV:-/etc/enerjione-grid/install.env}"
# Bu hesabi BIZIM actigimizi anlamak icin GECOS'a birakilan imza. Ayni adda
# baska bir hesap varsa (yonetim hesabi, musteri IT'sinin actigi bir kullanici)
# ONA DOKUNMAYIZ — asagida parolasini kilitliyoruz, yanlis hesaba yapilirsa
# birini sistemden kilitler.
KIOSK_GECOS="EnerjiOne Grid operator ekrani"

# --- Operator hesabinin adi -------------------------------------------------
# Kurulum aracinda girilen MUSTERI ADINDAN turetilir: "TPAO" -> `tpao`.
# Boylece sahadaki kisi ekranda kendi kurumunun adini gorur ve birden fazla
# cihazi olan bir bayi icin hesap adlari anlamli kalir.
_read_var() {  # $1=dosya $2=anahtar
  [[ -f "$1" ]] || return 1
  sed -n \
    -e "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\"\(.*\)\"[[:space:]]*\$/\1/p" \
    -e "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\([^\"#][^#]*\).*\$/\1/p" \
    "$1" | tail -1 | sed -e 's/[[:space:]]*$//'
}

# Turkce metin -> gecerli POSIX kullanici adi.
# Debian NAME_REGEX: kucuk harfle basla, [a-z0-9_-] devam et, en fazla 32.
_user_slug() {
  local out
  out="$(printf '%s' "$1" \
    | sed -e 's/Ç/C/g; s/ç/c/g; s/Ğ/G/g; s/ğ/g/g; s/İ/I/g; s/ı/i/g' \
          -e 's/Ö/O/g; s/ö/o/g; s/Ş/S/g; s/ş/s/g; s/Ü/U/g; s/ü/u/g' \
    | tr 'A-Z' 'a-z' \
    | tr -c 'a-z0-9' '-' \
    | sed -e 's/--*/-/g' -e 's/^-//' -e 's/-$//')"
  # Rakamla baslayan ad useradd tarafindan reddedilir; onek ekle.
  [[ "$out" =~ ^[a-z] ]] || out="e1-${out}"
  out="${out:0:32}"
  printf '%s' "${out%-}"
}

if [[ -n "${E1_KIOSK_USER:-}" ]]; then
  KIOSK_USER="$E1_KIOSK_USER"          # operator acikca belirtmis
else
  _customer="${E1_CUSTOMER:-}"
  [[ -z "$_customer" ]] && _customer="$(_read_var "$SITE_ENV" E1_CUSTOMER_NAME || true)"
  [[ -z "$_customer" ]] && _customer="$(_read_var "$INSTALL_ENV" E1_CUSTOMER || true)"
  # Musteri adi yoksa slug'a HIC girme: bos girdi "e1" gibi anlamsiz bir
  # hesap adi uretiyordu.
  if [[ -n "${_customer// /}" ]]; then
    KIOSK_USER="$(_user_slug "$_customer")"
  fi
  [[ -z "${KIOSK_USER:-}" || "$KIOSK_USER" == "e1" ]] && KIOSK_USER="e1-kiosk"
fi

if [[ "${E1_KIOSK:-1}" == "0" ]]; then
  e1_info "E1_KIOSK=0 — kiosk modu atlandi."
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  e1_warn "Kiosk kurulumu root gerektirir — atlandi."
  exit 0
fi

# --- Masaustu var mi? -------------------------------------------------------
# Sunucu/VPS kurulumlarinda ekran yoneticisi yoktur; kiosk anlamsizdir.
# `display-manager.service` tum dagitimlarda ekran yoneticisine isaret eden
# ortak semboldur (gdm3/lightdm/sddm hangisiyse).
detect_dm() {
  if [[ -e /etc/systemd/system/display-manager.service ]]; then
    basename "$(readlink -f /etc/systemd/system/display-manager.service)" .service
    return 0
  fi
  for dm in gdm3 gdm lightdm sddm; do
    if systemctl list-unit-files "${dm}.service" >/dev/null 2>&1 \
       && systemctl cat "${dm}.service" >/dev/null 2>&1; then
      printf '%s' "$dm"
      return 0
    fi
  done
  return 1
}

DM="$(detect_dm || true)"
if [[ -z "$DM" ]]; then
  e1_info "Masaustu (ekran yoneticisi) yok — kiosk modu atlandi."
  e1_hint "Masaustu kurulu bir cihazda: sudo bash ${BASH_SOURCE[0]}"
  exit 0
fi

e1_step "Kiosk modu (operator ekrani)"
e1_ok "Ekran yoneticisi: ${DM}"

# --- 1) Operator hesabi -----------------------------------------------------
# YETKISIZ olmasi kritik: sudo grubuna EKLENMEZ, parolasi kilitlenir
# (`!` ile) — bu hesapla parola girerek baska bir yere gecilemez.
if id -u "$KIOSK_USER" >/dev/null 2>&1; then
  # BASKASININ hesabini ele gecirme. Asagida parola kilitleyip otomatik
  # girise baglayacagiz; yanlis hesaba yapilirsa o kisiyi sistemden kilitler.
  _gecos="$(getent passwd "$KIOSK_USER" | cut -d: -f5)"
  if [[ "$_gecos" != "$KIOSK_GECOS" ]]; then
    e1_warn "'${KIOSK_USER}' adinda BASKA bir hesap zaten var — dokunulmuyor."
    _alt="$(printf '%s' "${KIOSK_USER}-ekran" | cut -c1-32)"
    if id -u "$_alt" >/dev/null 2>&1 \
       && [[ "$(getent passwd "$_alt" | cut -d: -f5)" != "$KIOSK_GECOS" ]]; then
      e1_warn "'${_alt}' de dolu — kiosk hesabi olusturulamadi."
      e1_hint "Elle ad verin: sudo E1_KIOSK_USER=<ad> bash ${BASH_SOURCE[0]}"
      exit 0
    fi
    KIOSK_USER="$_alt"
    e1_info "Operator hesabi icin '${KIOSK_USER}' kullanilacak."
  else
    e1_ok "Operator hesabi zaten var: ${KIOSK_USER}"
  fi
fi
if ! id -u "$KIOSK_USER" >/dev/null 2>&1; then
  useradd --create-home --comment "$KIOSK_GECOS" --shell /bin/bash "$KIOSK_USER"
  e1_ok "Operator hesabi olusturuldu: ${KIOSK_USER}"
fi
# Parolayi kilitle (otomatik giris parola sormaz; elle giris de yapilamasin).
passwd --lock "$KIOSK_USER" >/dev/null 2>&1 || true
# Yanlislikla yetki verilmis olabilir — temizle.
for grp in sudo adm admin wheel docker; do
  if id -nG "$KIOSK_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$grp"; then
    deluser "$KIOSK_USER" "$grp" >/dev/null 2>&1 || true
    e1_warn "'${KIOSK_USER}' hesabi '${grp}' grubundan cikarildi (operator yetkisiz olmali)."
  fi
done

KIOSK_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
KIOSK_HOME="${KIOSK_HOME:-/home/${KIOSK_USER}}"

# Grafik oturum gecerli bir kabuk ister; eski kurulumlarda nologin kalmis
# olabilir. Parola kilitli oldugu icin bu bir zafiyet degil.
usermod --shell /bin/bash "$KIOSK_USER" >/dev/null 2>&1 || true

# --- 2) Tarayici ------------------------------------------------------------
detect_browser() {
  [[ -n "${E1_KIOSK_BROWSER:-}" ]] && { printf '%s' "$E1_KIOSK_BROWSER"; return 0; }
  for b in chromium-browser chromium google-chrome google-chrome-stable firefox; do
    if command -v "$b" >/dev/null 2>&1; then printf '%s' "$(command -v "$b")"; return 0; fi
  done
  return 1
}

BROWSER="$(detect_browser || true)"
if [[ -z "$BROWSER" ]]; then
  e1_info "Tarayici bulunamadi, chromium kuruluyor..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null 2>&1 || true
  # Ubuntu'da `chromium-browser` snap'e yonlendirir; Debian'da gercek paket.
  if apt-get install -y -qq chromium >/dev/null 2>&1 \
     || apt-get install -y -qq chromium-browser >/dev/null 2>&1; then
    BROWSER="$(detect_browser || true)"
  fi
fi
if [[ -z "$BROWSER" ]]; then
  e1_warn "Tarayici kurulamadi — kiosk modu yarim kaldi."
  e1_hint "Elle kurun: sudo apt-get install -y chromium  (veya firefox)"
  exit 0
fi
e1_ok "Tarayici: ${BROWSER}"

# --- 3) Oturum betigi -------------------------------------------------------
# ROOT'A AIT, operator YAZAMAZ (0755): kiosk kullanicisi kendi oturum
# betigini degistirip baska bir komut calistiramasin.
cat > "$SESSION_BIN" <<EOF
#!/usr/bin/env bash
# EnerjiOne Grid kiosk oturumu — setup-kiosk.sh tarafindan uretildi.
# ELLE DUZENLEMEYIN; setup-kiosk.sh tekrar calisinca uzerine yazilir.
set -u

URL="\${E1_KIOSK_URL:-${KIOSK_URL}}"
BROWSER="${BROWSER}"

# Ekran hic sonmesin / kilitlenmesin — pano 7/24 acik kalir.
# gsettings oturum ICINDE calismali (DBus gerekir), bu yuzden burada.
if command -v gsettings >/dev/null 2>&1; then
  gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
  gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
  gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null || true
  gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing 2>/dev/null || true
  gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing 2>/dev/null || true
fi
# X11 oturumunda ekran koruyucuyu da kapat (Wayland'de bu araclar yok).
if [ "\${XDG_SESSION_TYPE:-}" = "x11" ]; then
  command -v xset >/dev/null 2>&1 && { xset s off; xset -dpms; xset s noblank; } 2>/dev/null || true
  command -v unclutter >/dev/null 2>&1 && unclutter -idle 3 &
fi

# Uygulama ayaga kalkmadan tarayiciyi acmak "baglanilamadi" sayfasi gosterir
# ve operator bunu hata saniyor. Once servisi bekle (en fazla 3 dakika).
for _ in \$(seq 1 90); do
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 -o /dev/null "\$URL" && break
  else
    break
  fi
  sleep 2
done

# Tarayici cokerse/kapatilirsa ekran BOS KALMASIN: yeniden baslat.
while true; do
  case "\$BROWSER" in
    *firefox*)
      "\$BROWSER" --kiosk "\$URL"
      ;;
    *)
      # Cokme/oturum uyarilari kiosk'ta kullaniciya gosterilemez; kapatiyoruz.
      "\$BROWSER" \\
        --kiosk \\
        --start-fullscreen \\
        --noerrdialogs \\
        --disable-infobars \\
        --disable-session-crashed-bubble \\
        --disable-features=TranslateUI \\
        --no-first-run \\
        --check-for-update-interval=31536000 \\
        --password-store=basic \\
        --disable-pinch \\
        --overscroll-history-navigation=0 \\
        "\$URL"
      ;;
  esac
  sleep 3
done
EOF
chmod 0755 "$SESSION_BIN"
chown root:root "$SESSION_BIN"
e1_ok "Oturum betigi: ${SESSION_BIN}"

# --- 4) Oturum acilisinda calistir -----------------------------------------
# `install -d -o <user>` kullanici cozulemezse ISI YARIDA BIRAKIR; once
# dizini ac, sahipligi ayrica ver.
mkdir -p "${KIOSK_HOME}/.config/autostart"
cat > "${KIOSK_HOME}/.config/autostart/enerjione-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=EnerjiOne Grid
Exec=${SESSION_BIN}
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
chown -R "$KIOSK_USER:$KIOSK_USER" "${KIOSK_HOME}/.config" 2>/dev/null || true

# GNOME'un ilk acilis sihirbazi ve "hos geldiniz" turu kiosk ekranini kapatir.
touch "${KIOSK_HOME}/.config/gnome-initial-setup-done"
chown -R "$KIOSK_USER:$KIOSK_USER" "${KIOSK_HOME}/.config" 2>/dev/null || true
e1_ok "Oturum acilisina eklendi."

# --- 5) Otomatik giris ------------------------------------------------------
case "$DM" in
  gdm3|gdm)
    GDM_CONF="/etc/gdm3/custom.conf"
    [[ -f "$GDM_CONF" ]] || GDM_CONF="/etc/gdm/custom.conf"
    if [[ ! -f "$GDM_CONF" ]]; then
      install -D -m 0644 /dev/null "$GDM_CONF"
      printf '[daemon]\n' > "$GDM_CONF"
    fi
    cp -a "$GDM_CONF" "${GDM_CONF}.e1-bak" 2>/dev/null || true
    # Mevcut satirlari temizleyip [daemon] bolumune yeniden ekle.
    sed -i '/^\s*AutomaticLoginEnable\s*=/d; /^\s*AutomaticLogin\s*=/d' "$GDM_CONF"
    if grep -q '^\[daemon\]' "$GDM_CONF"; then
      sed -i "0,/^\[daemon\]/s//[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=${KIOSK_USER}/" "$GDM_CONF"
    else
      printf '\n[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=%s\n' "$KIOSK_USER" >> "$GDM_CONF"
    fi
    e1_ok "GDM otomatik girisi: ${KIOSK_USER}"
    ;;
  lightdm)
    install -d -m 0755 /etc/lightdm/lightdm.conf.d
    cat > /etc/lightdm/lightdm.conf.d/50-enerjione-kiosk.conf <<EOF
[Seat:*]
autologin-user=${KIOSK_USER}
autologin-user-timeout=0
user-session=\$(basename "\$(ls /usr/share/xsessions/*.desktop 2>/dev/null | head -1)" .desktop)
EOF
    # user-session satiri cozulemezse LightDM varsayilani kullanir; hatali
    # bir deger oturumu hic actirmaz, o yuzden bos ise satiri siliyoruz.
    sed -i '/^user-session=$/d' /etc/lightdm/lightdm.conf.d/50-enerjione-kiosk.conf
    e1_ok "LightDM otomatik girisi: ${KIOSK_USER}"
    ;;
  sddm)
    install -d -m 0755 /etc/sddm.conf.d
    cat > /etc/sddm.conf.d/50-enerjione-kiosk.conf <<EOF
[Autologin]
User=${KIOSK_USER}
Session=plasma
EOF
    e1_ok "SDDM otomatik girisi: ${KIOSK_USER}"
    ;;
  *)
    e1_warn "Bilinmeyen ekran yoneticisi (${DM}) — otomatik giris ELLE ayarlanmali."
    e1_hint "Oturum betigi hazir: ${SESSION_BIN}"
    ;;
esac

# --- 6) Grafik hedef acilista ----------------------------------------------
# Sunucu profilinden gelen sistemlerde varsayilan hedef multi-user olabilir;
# o zaman ekran hic acilmaz.
if [[ "$(systemctl get-default 2>/dev/null)" != "graphical.target" ]]; then
  systemctl set-default graphical.target >/dev/null 2>&1 \
    && e1_ok "Acilis hedefi graphical.target yapildi." \
    || e1_warn "Acilis hedefi degistirilemedi."
fi

echo
e1_ok "Kiosk modu hazir."
e1_hint "Operator ekrani : ${KIOSK_USER} (otomatik giris, yetkisiz)"
e1_hint "Yonetim hesabi  : $(e1_target_user 2>/dev/null || echo "${SUDO_USER:-enerjione}") (SSH/sudo/update)"
e1_hint "Acilan adres    : ${KIOSK_URL}"
e1_hint "Devre disi      : sudo E1_KIOSK=0 bash ${BASH_SOURCE[0]}  (once bkz. docs)"
exit 0
