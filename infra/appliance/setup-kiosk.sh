#!/usr/bin/env bash
# ===========================================================================
# EnerjiOne Grid — Kiosk modu (masaustu olan cihazlarda)
# ===========================================================================
# Cihaz acildiginda arayuz KENDILIGINDEN tam ekran gelir. Operator hicbir sey
# yapmaz: parola yok, masaustu yok, tarayici penceresi yok.
#
# NASIL: kiosk KENDI X OTURUMUDUR (/usr/share/xsessions/enerjione-kiosk.desktop)
# ve ekran yoneticisi dogrudan ona yonlendirilir. Eskiden tam bir masaustu
# oturumu acilip tarayici onun USTUNDE calisiyordu; operator once duvar
# kagidini/panelleri goruyordu ve masaustunun ekran kilidi devreye girince
# parolasi KILITLI hesapta cihaz kullanilamaz hale geliyordu. Masaustu hic
# acilmayinca ikisi de kokunden bitiyor.
#
# OTURUM KIMLIGI SABITTIR (`enerjione-kiosk`). Musteri adi yalnizca GORUNEN
# ada girer; kimlige girseydi musteri adi degisince ekran yoneticisinin
# isaretcisi gecersiz kalir ve cihaz KARA EKRANA duserdi.
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
#   E1_KIOSK_SESSION=0  KURTARMA ANAHTARI. Kendi X oturumuna gecme; ekran
#                   yoneticisindeki isaretciyi KALDIR. Cihaz eski davranisa
#                   (masaustu + autostart) doner. Kara ekran halinde:
#                   Ctrl+Alt+F3 -> yonetim hesabi ->
#                   sudo E1_KIOSK_SESSION=0 bash infra/appliance/setup-kiosk.sh
#   E1_KIOSK_KEEP_GECOS=1  giris ekranindaki adi oldugu gibi birak (surum
#                   geri alma senaryosu)
#   E1_KIOSK_WM     pencere yoneticisi komutu  (default: otomatik tespit/kur)
#   E1_KIOSK_NO_SLEEP=1  uyku hedeflerini kapat (SISTEM GENELI, opsiyonel)
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

# X oturumunun kimligi. SABIT — musteri adindan TURETILMEZ: ekran yoneticisine
# yazdigimiz isaretci bu ada bakar, musteri adi degisince bozulmamali.
XSESSION_ID="enerjione-kiosk"
# /usr/local/share/xsessions'i LightDM ve SDDM taramaz; /usr/share zorunlu.
XSESSION_FILE="/usr/share/xsessions/${XSESSION_ID}.desktop"
SHARE_DIR="/usr/local/share/enerjione-grid"
SPLASH_FILE="${SHARE_DIR}/kiosk-splash.html"

# Bu hesabi BIZIM actigimizi anlamak icin birakilan izler. Ayni adda baska bir
# hesap varsa (yonetim hesabi, musteri IT'sinin actigi bir kullanici) ONA
# DOKUNMAYIZ — asagida parolasini kilitliyoruz, yanlis hesaba yapilirsa birini
# sistemden kilitler.
#   KIOSK_GECOS_LEGACY : v2.24 ve oncesinde yazilan SABIT imza. Sahadaki
#                        CALISAN cihazlarda bu var; taninmazsa script kendi
#                        actigi hesabi yabanci sanip IKINCI bir hesap acar.
#   KIOSK_MARK_DIR     : root'a ait imza dosyasi. GECOS artik musteri adiyla
#                        degistigi icin tek basina kanit degil; asil kanit bu.
KIOSK_GECOS_LEGACY="EnerjiOne Grid operator ekrani"
KIOSK_MARK_DIR="/var/lib/enerjione-grid/kiosk-users"

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

# GECOS TEK SATIRDIR; ':' /etc/passwd alan ayiricisi, ',' GECOS alt-alan
# ayiricisidir. Musteri adinda gecerse passwd bozulur -> temizle ve kirp.
# Kirpma UTF-8 ortasindan kesip bozuk bayt birakmasin diye iconv ile dogrula.
_gecos_safe() {
  local out
  out="$(printf '%s' "$1" | tr -d ':,\n\r' | tr -s ' ' | sed -e 's/^ *//' -e 's/ *$//')"
  out="${out:0:48}"
  if command -v iconv >/dev/null 2>&1; then
    while [[ -n "$out" ]] && ! printf '%s' "$out" | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1; do
      out="${out%?}"
    done
  fi
  printf '%s' "$out"
}

# Musteri adi cozumu E1_KIOSK_USER'dan BAGIMSIZ: hesap adi elle verilmis olsa
# bile GORUNEN AD (GECOS) icin musteri adi lazim.
E1_CUSTOMER_RESOLVED="${E1_CUSTOMER:-}"
[[ -z "$E1_CUSTOMER_RESOLVED" ]] && E1_CUSTOMER_RESOLVED="$(_read_var "$SITE_ENV" E1_CUSTOMER_NAME || true)"
[[ -z "$E1_CUSTOMER_RESOLVED" ]] && E1_CUSTOMER_RESOLVED="$(_read_var "$INSTALL_ENV" E1_CUSTOMER || true)"
CUSTOMER_SAFE="$(_gecos_safe "${E1_CUSTOMER_RESOLVED:-}")"

# Giris ekraninda gorunen ad: "EnerjiOne Grid TPAO".
# Musteri adi YOKSA eski metnin TA KENDISI -> boyle cihazlarda sifir regresyon.
if [[ -n "$CUSTOMER_SAFE" ]]; then
  KIOSK_GECOS="EnerjiOne Grid ${CUSTOMER_SAFE}"
else
  KIOSK_GECOS="$KIOSK_GECOS_LEGACY"
fi

if [[ -n "${E1_KIOSK_USER:-}" ]]; then
  KIOSK_USER="$E1_KIOSK_USER"          # operator acikca belirtmis
else
  # Musteri adi yoksa slug'a HIC girme: bos girdi "e1" gibi anlamsiz bir
  # hesap adi uretiyordu.
  if [[ -n "${E1_CUSTOMER_RESOLVED// /}" ]]; then
    KIOSK_USER="$(_user_slug "$E1_CUSTOMER_RESOLVED")"
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
# Verilen hesap BIZIM actigimiz kiosk hesabi mi?
# Sirayla: (1) imza dosyasi, (2) eski SABIT GECOS (sahadaki mevcut cihazlar),
# (3) bu surumun yazacagi GECOS, (4) "EnerjiOne Grid " oneki + BIZIM
# yazdigimiz bir dosyanin varligi.
# (4)'te EK KANIT SART: musterinin actigi "EnerjiOne Grid Test" gibi bir hesabi
# ele gecirip parolasini kilitlemeyelim. Kanit yoksa YABANCI sayilir — hatanin
# guvenli yonu budur (ikinci hesap acilir, kimse sistemden kilitlenmez).
_kiosk_bizim_mi() {
  local kul="$1" gecos ev
  [[ -f "${KIOSK_MARK_DIR}/${kul}" ]] && return 0
  # GECOS'un ilk alt-alani = gorunen ad (chfn virgullu yazabilir).
  gecos="$(getent passwd "$kul" | cut -d: -f5 | cut -d, -f1)"
  [[ "$gecos" == "$KIOSK_GECOS_LEGACY" ]] && return 0
  [[ "$gecos" == "$KIOSK_GECOS" ]] && return 0
  if [[ "$gecos" == "EnerjiOne Grid "* ]]; then
    ev="$(getent passwd "$kul" | cut -d: -f6)"
    [[ -n "$ev" && -f "${ev}/.config/autostart/enerjione-kiosk.desktop" ]] && return 0
  fi
  return 1
}

if id -u "$KIOSK_USER" >/dev/null 2>&1; then
  # BASKASININ hesabini ele gecirme. Asagida parola kilitleyip otomatik
  # girise baglayacagiz; yanlis hesaba yapilirsa o kisiyi sistemden kilitler.
  if ! _kiosk_bizim_mi "$KIOSK_USER"; then
    e1_warn "'${KIOSK_USER}' adinda BASKA bir hesap zaten var — dokunulmuyor."
    _alt="$(printf '%s' "${KIOSK_USER}-ekran" | cut -c1-32)"
    if id -u "$_alt" >/dev/null 2>&1 && ! _kiosk_bizim_mi "$_alt"; then
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

# Imza dosyasi: bundan SONRA sahiplik GECOS'a BAGLI DEGIL. Once bunu yaz,
# sonra adi degistir — ters sirada bir kesinti hesabi "yabanci" birakir ve
# bir sonraki kosuda ikinci bir hesap acilir.
if install -d -m 0755 "$KIOSK_MARK_DIR" 2>/dev/null \
   && printf 'created-by=setup-kiosk.sh\nuser=%s\n' "$KIOSK_USER" \
        > "${KIOSK_MARK_DIR}/${KIOSK_USER}" 2>/dev/null; then
  chmod 0644 "${KIOSK_MARK_DIR}/${KIOSK_USER}" 2>/dev/null || true
else
  e1_warn "Kiosk imza dosyasi yazilamadi (${KIOSK_MARK_DIR}) — ad degisikligi riskli."
fi

# Giris ekraninda gorunen ad musteri adina gore guncellenir (sahadaki eski
# hesaplar dahil). Surum geri alma senaryosunda E1_KIOSK_KEEP_GECOS=1 ile
# kapatilabilir. usermod calisan oturumlarda nadiren reddeder -> chfn yedegi.
_gecos_cur="$(getent passwd "$KIOSK_USER" | cut -d: -f5 | cut -d, -f1)"
if [[ "${E1_KIOSK_KEEP_GECOS:-0}" != "1" && "$_gecos_cur" != "$KIOSK_GECOS" ]]; then
  if usermod -c "$KIOSK_GECOS" "$KIOSK_USER" 2>/dev/null \
     || chfn -f "$KIOSK_GECOS" "$KIOSK_USER" >/dev/null 2>&1; then
    e1_ok "Giris ekranindaki ad: ${KIOSK_GECOS}"
  else
    e1_warn "Gorunen ad guncellenemedi (${KIOSK_GECOS}) — kurulum etkilenmez."
  fi
fi
unset _gecos_cur

KIOSK_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
KIOSK_HOME="${KIOSK_HOME:-/home/${KIOSK_USER}}"

# --- Operator hesabinin profil resmi: EnerjiOne logosu ----------------------
# Giris ekraninda varsayilan gri avatar yerine urun logosu gorunsun.
# Uygulamayi TEKRARLAMIYORUZ: ayni is sunucudaki diger hesaplar icin de
# gerekiyor, o yuzden ortak script'te duruyor (AccountsService + ~/.face,
# ekran yoneticileri farkli yerlere bakiyor).
if [[ -f "${SCRIPT_DIR}/setup-user-avatars.sh" ]]; then
  bash "${SCRIPT_DIR}/setup-user-avatars.sh" "$KIOSK_USER" ||     e1_warn "Profil resmi ayarlanamadi (kurulum etkilenmez)."
fi

# Grafik oturum gecerli bir kabuk ister; eski kurulumlarda nologin kalmis
# olabilir. Parola kilitli oldugu icin bu bir zafiyet degil.
usermod --shell /bin/bash "$KIOSK_USER" >/dev/null 2>&1 || true

# --- 1b) Ekran kilidi / koruyucu: SADECE kiosk hesabi icin kapat ------------
# Parolasi KILITLI bir hesapta kilit ekrani = cihaz reboot'a kadar
# KULLANILAMAZ ("baska bir sifre soylemedin" sikayeti buradan geliyor).
# Asil cozum kiosk'un kendi X oturumu olmasi (masaustu yoksa kilitleyici de
# yok) ama yedek masaustu yoluna dusulurse kilitleyiciler XDG autostart ile
# geri gelir. Kullanicinin kendi autostart dizini sistemdekini AD BAZINDA
# ezer; Hidden=true "bu girdi yok" demektir.
_AUTOSTART_DIR="${KIOSK_HOME}/.config/autostart"
mkdir -p "$_AUTOSTART_DIR"
for _lock in light-locker xscreensaver xfce4-screensaver gnome-screensaver \
             mate-screensaver xfce4-power-manager; do
  cat > "${_AUTOSTART_DIR}/${_lock}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${_lock} (EnerjiOne Grid kiosk: kapali)
Exec=/bin/true
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
EOF
done
unset _lock

# GNOME/dconf ayarlari OTURUM DISINDA yazilir: dbus-run-session gecici bir veri
# yolu acar, gsettings kullanicinin ~/.config/dconf/user dosyasina yazar. Oturum
# icinde yazmak GEC kaliyordu (masaustu kendi varsayilanini uygulamis oluyordu)
# ve DBus yoksa hic calismiyordu.
_as_kiosk() {
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$KIOSK_USER" -- "$@"
  else
    sudo -u "$KIOSK_USER" -H -- "$@"
  fi
}
if command -v dbus-run-session >/dev/null 2>&1 && command -v gsettings >/dev/null 2>&1; then
  _as_kiosk dbus-run-session -- bash -c '
    gsettings set org.gnome.desktop.session idle-delay 0
    gsettings set org.gnome.desktop.screensaver lock-enabled false
    gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
    gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing
    gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing
    exit 0
  ' >/dev/null 2>&1 || true
fi
chown -R "$KIOSK_USER:$KIOSK_USER" "${KIOSK_HOME}/.config" 2>/dev/null || true
e1_ok "Ekran kilidi/kararma kiosk hesabi icin kapatildi."

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

# --- 2b) Pencere yoneticisi -------------------------------------------------
# Ciplak X oturumunda masaustunun WM'i gelmez. WM'siz chromium --kiosk TAM
# EKRAN OLAMAZ (tam ekrani WM'e EWMH ile yaptirir; pencere sol ustte kucuk
# kalir) ve klavye odagi imlecin altinda kalir -> giris formuna yazilamaz.
resolve_wm() {
  [[ -n "${E1_KIOSK_WM:-}" ]] && { printf '%s' "$E1_KIOSK_WM"; return 0; }
  # matchbox once: dekorasyon, kok menu, kisayol, taskbar YOK — operatore
  # kiosk'tan cikacak hicbir tutamac birakmaz. Digerleri "varsa kullan"
  # yedegi; mutter/kwin agir olduklari icin listenin sonunda.
  command -v matchbox-window-manager >/dev/null 2>&1 \
    && { printf '%s' "matchbox-window-manager -use_titlebar no"; return 0; }
  command -v openbox >/dev/null 2>&1 && { printf '%s' "openbox --sm-disable"; return 0; }
  command -v xfwm4 >/dev/null 2>&1 \
    && { printf '%s' "xfwm4 --sm-client-disable --compositor=off"; return 0; }
  command -v marco >/dev/null 2>&1 && { printf '%s' "marco --no-composite"; return 0; }
  for w in fluxbox icewm jwm; do
    command -v "$w" >/dev/null 2>&1 && { printf '%s' "$w"; return 0; }
  done
  command -v mutter >/dev/null 2>&1 && { printf '%s' "mutter --x11"; return 0; }
  command -v kwin_x11 >/dev/null 2>&1 && { printf '%s' "kwin_x11"; return 0; }
  return 1
}

WM_CMD="$(resolve_wm || true)"
if [[ -z "$WM_CMD" ]]; then
  # ~100 KB'lik bir paket; tarayicinin yaninda ihmal edilebilir ve YALNIZCA
  # hicbir WM yokken tetiklenir. Basarisiz olursa akis kirilmaz.
  e1_info "Pencere yoneticisi yok, matchbox kuruluyor..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq --no-install-recommends matchbox-window-manager >/dev/null 2>&1 \
    || apt-get install -y -qq --no-install-recommends openbox >/dev/null 2>&1 || true
  WM_CMD="$(resolve_wm || true)"
fi
if [[ -n "$WM_CMD" ]]; then
  e1_ok "Pencere yoneticisi: ${WM_CMD%% *}"
else
  e1_warn "Pencere yoneticisi bulunamadi — tarayici elle boyutlandirilacak."
fi

# --- 3) Oturum betigi -------------------------------------------------------
# ROOT'A AIT, operator YAZAMAZ (0755): kiosk kullanicisi kendi oturum
# betigini degistirip baska bir komut calistiramasin.
#
# Yapilandirma degerleri basa `printf %q` ile yaziliyor, GOVDE TIRNAKLI
# heredoc: tirnaksiz heredoc icinde $ kacirmak bu dosyada zaten bir hataya yol
# acmisti (LightDM ayarina literal komut-ikamesi yazilmasi) — tekrarlamiyoruz.
{
  printf '%s\n' '#!/usr/bin/env bash'
  printf '%s\n' '# EnerjiOne Grid kiosk oturumu - setup-kiosk.sh tarafindan uretildi.'
  printf '%s\n' '# ELLE DUZENLEMEYIN; setup-kiosk.sh tekrar calisinca uzerine yazilir.'
  printf 'E1_URL_DEFAULT=%q\n' "$KIOSK_URL"
  # Adres kurulumda ACIKCA verildi mi? Verildiyse oturum betigi `.env`den
  # port cikarimi YAPMAZ; operatorun sectigi adres baglayicidir.
  printf 'E1_URL_EXPLICIT=%q\n' "${E1_KIOSK_URL:+1}"
  # Splash'ta gosterilecek musteri adi (kurulum araci girdisi).
  printf 'E1_CUSTOMER_NAME=%q
' "$CUSTOMER_SAFE"
  printf 'E1_BROWSER=%q\n'     "$BROWSER"
  printf 'E1_SPLASH=%q\n'      "$SPLASH_FILE"
  printf 'E1_WM=%q\n'          "$WM_CMD"
  cat <<'E1_SESSION_EOF'
set -u

# Once tanimlanir: asagidaki kilit/dbus bloklari da kullaniyor.
_log() { command -v logger >/dev/null 2>&1 && logger -t e1-kiosk "$*" 2>/dev/null || true; }

# DBUS: ciplak X oturumunda oturum veri yolu olmayabilir; gsettings ve Chromium
# onsuz gurultu cikarir. Kilitten ONCE sarmaliyoruz, cunku exec ile kendimizi
# bastan calistiriyoruz.
# BU BETIK ASLA CIKMAMALI. Cikarsa X oturumu kapanir, ekran yoneticisi
# greeter'a doner ve parolasi KILITLI operator hesabi giris YAPAMAZ —
# cihaz yeniden baslatilana kadar kullanilamaz. Asagidaki iki adim
# (dbus ve kilit) eskiden bu kurali deliyordu; ikisi de artik
# "cikmak yerine bekle/atla" seklinde.

# DBUS: ciplak X oturumunda oturum veri yolu olmayabilir; gsettings ve Chromium
# onsuz gurultu cikarir. Kilitten ONCE sarmaliyoruz.
# `exec` KULLANILMIYOR: dbus-run-session baslatilamazsa exec ile kabugu
# degistirdigimiz icin surec olur ve OTURUM KAPANIRDI. Simdi calistirmayi
# deniyoruz; olmazsa dbus'siz devam ediyoruz (gurultu olur, oturum yasar).
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -z "${E1_KIOSK_DBUS:-}" ] \
   && command -v dbus-run-session >/dev/null 2>&1; then
  E1_KIOSK_DBUS=1; export E1_KIOSK_DBUS
  dbus-run-session -- "$0" "$@"
  # Buraya DUSMEK beklenmez: ic kopya sonsuz dongude kalir. Yine de dondu ise
  # CIKMIYORUZ — cikis = X oturumu kapanir = greeter = parolasi kilitli
  # hesap giremez. Ekranda ne varsa kalsin, oturum ayakta dursun.
  _log "kiosk ic kopyasi dondu — oturum ayakta tutuluyor"
  while true; do sleep 60; done
fi

# TEK CALISMA KILIDI: eski autostart girisi YEDEK yol olarak duruyor (X oturumu
# tutmazsa cihaz eski davranisa duser, kara ekrana degil). Ikisi birden
# tetiklenirse iki tarayici acilmasin.
#
# KILIT ALINAMAZSA CIKMIYORUZ:
#   * fd acilamadi (XDG_RUNTIME_DIR silinmis, /tmp'de root'a ait artik kilit
#     dosyasi) -> `exec 9>` hatasi `|| true` ile yutulur ama fd 9 ACILMAMIS
#     kalirdi; sonraki `flock -n 9` "Bad file descriptor" verip `exit 0`
#     dedigi icin oturum SESSIZCE kapanirdi. Artik kilidi tamamen atliyoruz.
#   * kilit baskasinda -> diger ornek ekrani zaten dolduruyor; biz sadece
#     oturumu ayakta tutmak icin bekliyoruz, CIKMIYORUZ.
if command -v flock >/dev/null 2>&1 \
   && exec 9>"${XDG_RUNTIME_DIR:-/tmp}/e1-kiosk.lock" 2>/dev/null; then
  if ! flock -n 9; then
    _log "kiosk zaten calisiyor — bu oturum bekleme moduna geciyor"
    while true; do sleep 60; done
  fi
fi

# --- Acilacak adres: PORT CALISMA ANINDA COZULUR ---------------------------
# Arayuzun yayinlandigi port `.env` icindeki FRONTEND_HTTP_PORT ile degisir
# (host'un 80'i host-nginx'te ise kurulum bunu 8080 yapar). Adres
# `http://localhost/` olarak SABIT gomuluydu; port 80 DEGILSE:
#   - splash'in yoklamasi hicbir zaman basarili olmaz,
#   - yonlendirme hic tetiklenmez,
#   - operator acilis ekraninda SONSUZA KADAR bekler.
# Sahada "surekli splash ekranda bekliyor" sikayeti tam olarak budur.
#
# Provizyon aninda cozmek YETMEZ: port sonraki bir guncellemede degisebilir
# ve kiosk yeniden kurulmadan bozulurdu. Bu yuzden her oturumda okuyoruz.
_port_cikar() {
  # Deger "8080" olabilir, "127.0.0.1:8080" de (bind adresiyle). Bizi
  # yalnizca port ilgilendiriyor; kiosk zaten ayni makinede.
  printf '%s' "${1##*:}" | tr -cd '0-9'
}

if [ -n "${E1_KIOSK_URL:-}" ]; then
  URL="$E1_KIOSK_URL"                      # ortamdan acikca verilmis
elif [ "${E1_URL_EXPLICIT:-}" = "1" ]; then
  URL="$E1_URL_DEFAULT"                    # kurulumda acikca verilmis
else
  _p=""
  # Liste degisken uzerinden: varsayilan gercek yollar, test bunu saptirir.
  # Sabit yollara bakan bir dongu yalnizca cihazda sinanabilirdi — yani hic.
  for _envf in ${E1_ENV_CANDIDATES:-/opt/enerjione-grid/.env /etc/enerjione-grid/install.env}; do
    [ -f "$_envf" ] || continue
    _p="$(sed -n 's/^[[:space:]]*FRONTEND_HTTP_PORT[[:space:]]*=[[:space:]]*\([^#]*\).*/\1/p' \
          "$_envf" 2>/dev/null | tail -1 | tr -d '"'"'"' \t\r')"
    _p="$(_port_cikar "$_p")"
    [ -n "$_p" ] && break
  done
  case "$_p" in
    "" | 80) URL="$E1_URL_DEFAULT" ;;
    *)       URL="http://localhost:${_p}/" ;;
  esac
  unset _p _envf
fi
_log "kiosk adresi: $URL"

# Surum: guncelleme ile degistigi icin provizyona GOMULMEZ, her oturumda
# okunur. Bulunamazsa splash surum satirini HIC gostermez (uydurmak yerine).
E1_APP_VERSION=""
for _envf in ${E1_ENV_CANDIDATES:-/opt/enerjione-grid/.env /etc/enerjione-grid/install.env}; do
  [ -f "$_envf" ] || continue
  E1_APP_VERSION="$(sed -n 's/^[[:space:]]*E1_VERSION[[:space:]]*=[[:space:]]*\([^#]*\).*//p'     "$_envf" 2>/dev/null | tail -1 | tr -d '"'"'"' 	
')"
  [ -n "$E1_APP_VERSION" ] && break
done
unset _envf

# Ekran hic sonmesin / kilitlenmesin — pano 7/24 acik kalir. Asil ayar kurulum
# aninda offline dconf'a yazildi; burasi ikinci savunma hatti.
if command -v gsettings >/dev/null 2>&1; then
  gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
  gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
  gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null || true
  gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing 2>/dev/null || true
  gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing 2>/dev/null || true
fi

# X sunucusunun KENDI ekran koruyucusu (varsayilan ~10 dk) ve DPMS masaustu
# olmadan da calisir; parola sormaz ama ekrani karartir ve operator bunu ariza
# saniyor. ESKI KOSUL (XDG_SESSION_TYPE=x11) ciplak oturumda bos kalip blogu
# atliyordu — DISPLAY'e bakiyoruz.
if [ -n "${DISPLAY:-}" ]; then
  if command -v xset >/dev/null 2>&1; then
    xset s off -dpms s noblank 2>/dev/null || true
    # Bazi araclar ayari geri acabiliyor; 60 sn'de bir tazele. Oturum bitince
    # bu arka plan da biter, ayri bir unit gerekmiyor.
    ( while sleep 60; do xset s off -dpms 2>/dev/null || true; done ) &
  fi
  # X'in acilisi ile tarayicinin ilk boyamasi arasindaki bosluk gri stipple
  # degil kurumsal renk olsun.
  if command -v xsetroot >/dev/null 2>&1; then
    xsetroot -solid '#0b1220' 2>/dev/null || true
  fi
  command -v unclutter >/dev/null 2>&1 && unclutter -idle 3 &
fi
# Bir sekilde baslamis bir kilitleyici varsa (yedek masaustu yolu) sustur.
command -v xscreensaver-command >/dev/null 2>&1 && { xscreensaver-command -exit >/dev/null 2>&1 || true; }
command -v light-locker-command >/dev/null 2>&1 && { light-locker-command -d >/dev/null 2>&1 || true; }

# Pencere yoneticisi: yalnizca HIC calismiyorsa baslat. Yedek masaustu yoluna
# dusuldugunde zaten bir WM vardir, ikincisi onunla cakisir.
_wm_var() {
  for _p in mutter xfwm4 marco kwin_x11 openbox matchbox-window-manager fluxbox icewm jwm; do
    if pgrep -x "$_p" >/dev/null 2>&1; then return 0; fi
  done
  return 1
}
if [ -n "${DISPLAY:-}" ] && [ -n "$E1_WM" ] && ! _wm_var; then
  # Kelime bolunmesi ISTENIYOR: E1_WM komut + arguman.
  # shellcheck disable=SC2086
  $E1_WM >/dev/null 2>&1 &
  sleep 1
fi

EXTRA=""
if [ -z "$E1_WM" ]; then
  # WM yok: tam ekran yaptirabilecegimiz kimse yok. Cozunurlugu X'ten cozup
  # pencereyi elle kaplatiyoruz — bozuk degil, sadece dusuk kaliteli yedek.
  _res=""
  if command -v xdpyinfo >/dev/null 2>&1; then
    _res="$(xdpyinfo 2>/dev/null | awk '/dimensions:/{print $2; exit}')"
  fi
  if [ -z "$_res" ] && command -v xrandr >/dev/null 2>&1; then
    _res="$(xrandr 2>/dev/null | awk '/\*/{print $1; exit}')"
  fi
  case "$_res" in
    [0-9]*x[0-9]*) EXTRA="--window-position=0,0 --window-size=${_res%x*},${_res#*x}" ;;
  esac
fi

if [ ! -x "$E1_BROWSER" ]; then
  _log "tarayici bulunamadi: $E1_BROWSER"
  command -v xmessage >/dev/null 2>&1 \
    && xmessage -center "EnerjiOne Grid: tarayici bulunamadi. Teknik ekibi arayin." &
  # OTURUMU BITIRME: oturum biterse ekran yoneticisi greeter'a doner, parola
  # ister ve hesabin parolasi KILITLI oldugu icin operator giremez (cihaz
  # yeniden baslatilana kadar kullanilamaz).
  while true; do sleep 60; done
fi

# SPLASH KULLANICININ EVINE KOPYALANIR.
#
# YASANAN ARIZA: ekranda Firefox'un "File not found —
# /usr/local/share/enerjione-grid/kiosk-splash.html" hata sayfasi cikti.
# Dosya DISKTE VARDI; asagidaki `[ -f ]` de dogru donuyordu (donmeseydi zaten
# dogrudan uygulama adresine gidilirdi). Sorun okuma IZNI degil, PAKETLEME:
# Ubuntu'da Firefox bir **snap**tir ve snap sandbox'i `/usr/local` altini HIC
# gormez; `home` arayuzu yalnizca $HOME'un GIZLI OLMAYAN kismina izin verir.
# Ayni sey Flatpak tarayicilar icin de gecerli.
#
# `[ -f ]` bu durumu ASLA yakalayamaz: test kabukta, yani sandbox DISINDA
# kosuyor. Bu yuzden varligi sinamak yerine dosyayi tarayicinin kesin
# okuyabilecegi yere tasiyoruz.
#
# Dizin adi GIZLI DEGIL (`.e1-kiosk` degil): snap'in `home` arayuzu $HOME
# altindaki nokta ile baslayan yollari engeller — gizli dizine koymak hatayi
# aynen tekrarlardi.
E1_SPLASH_LOCAL=""
if [ -f "$E1_SPLASH" ] && [ -n "${HOME:-}" ]; then
  _sp_dir="${HOME}/enerjione-grid"
  if mkdir -p "$_sp_dir" 2>/dev/null \
     && cp -f "$E1_SPLASH" "${_sp_dir}/kiosk-splash.html" 2>/dev/null; then
    # Logo <img src="kiosk-logo.png"> ile GORECELI isteniyor; yaninda olmali.
    cp -f "$(dirname "$E1_SPLASH")/kiosk-logo.png" "$_sp_dir/" 2>/dev/null || true
    E1_SPLASH_LOCAL="${_sp_dir}/kiosk-splash.html"
  else
    _log "splash eve kopyalanamadi: $_sp_dir"
  fi
fi

# Splash'a gecirilen degerler. Dosyaya GOMMEK yerine fragment kullaniyoruz:
# gomulu deger surum/musteri degisince bayatlar, fragment her oturumda o anki
# degeri tasir ve dosyayi yeniden yazmayi gerektirmez. Fragment sunucuya
# gitmez, zaten file:// aciyoruz.
#
# Kacis: deger URL fragment'ine giriyor; bosluk ve ayirici karakterler
# kodlanmali yoksa parametreler birbirine karisir.
_url_kacis() {
  printf '%s' "$1" | sed -e 's/%/%25/g' -e 's/ /%20/g' -e 's/&/%26/g'                          -e 's/#/%23/g' -e 's/+/%2B/g'
}

_splash_frag() {
  printf 'u=%s&c=%s&v=%s'     "$(_url_kacis "$URL")"     "$(_url_kacis "${E1_CUSTOMER_NAME:-}")"     "$(_url_kacis "${E1_APP_VERSION:-}")"
}

# --- Musteri logosu: uygulama ayaktayken diske onbelleklenir ---------------
# Logo uygulamanin veritabaninda data URL olarak durur; splash ise uygulama
# HENUZ AYAGA KALKMADAN gosterilir, yani o an veritabanina ulasilamaz.
# Cozum: uygulama ayaga kalktiktan sonra arka planda cekip diske yaziyoruz.
# Ilk acilista logo YOK (splash onsuz gosterilir), sonraki her acilista VAR.
# Uc public (login ekrani da ayni yerden okuyor), kimlik gerekmiyor.
_logo_onbellek() {
  [ -n "${E1_SPLASH_LOCAL:-}" ] || return 0
  command -v curl >/dev/null 2>&1 || return 0
  command -v base64 >/dev/null 2>&1 || return 0
  _dizin="$(dirname "$E1_SPLASH_LOCAL")"

  # Uygulama ayaga kalkana kadar bekle (en fazla ~10 dk).
  _bek=0
  while [ "$_bek" -lt 300 ]; do
    if curl -fsS --max-time 3 -o /dev/null "$URL" 2>/dev/null; then break; fi
    _bek=$((_bek + 1)); sleep 2
  done
  [ "$_bek" -lt 300 ] || return 0

  _json="$(curl -fsS --max-time 10 "${URL%/}/api/v1/project-settings" 2>/dev/null)" || return 0
  # data URL'in base64 govdesini cikar. `grep -o` ile tek alan aliyoruz;
  # jq bu cihazlarda kurulu olmayabilir ve yalnizca bunun icin bagimlilik
  # eklemek istemiyoruz.
  _b64="$(printf '%s' "$_json"     | grep -o '"customer_logo":"data:image/[^"]*"'     | head -1 | sed -e 's/.*base64,//' -e 's/"$//')"
  [ -n "$_b64" ] || return 0

  if printf '%s' "$_b64" | base64 -d > "${_dizin}/customer-logo.png.tmp" 2>/dev/null      && [ -s "${_dizin}/customer-logo.png.tmp" ]; then
    mv -f "${_dizin}/customer-logo.png.tmp" "${_dizin}/customer-logo.png"
    _log "musteri logosu onbelleklendi"
  else
    rm -f "${_dizin}/customer-logo.png.tmp" 2>/dev/null || true
  fi
}

# Hedef: uygulama ayakta ise dogrudan adres, degilse YEREL splash sayfasi.
# Splash uygulamayi kendisi yoklar ve hazir olunca yonlenir; boylece ekran ILK
# SANIYEDEN itibaren doludur. (Eskiden burada 3 dakikaya kadar suren bir
# bekleme dongusu vardi ve o sure boyunca ekranda masaustu/siyahlik kaliyordu.)
_hedef() {
  if command -v curl >/dev/null 2>&1 \
     && curl -fsS --max-time 2 -o /dev/null "$URL" 2>/dev/null; then
    printf '%s' "$URL"
  elif [ -n "$E1_SPLASH_LOCAL" ] && [ -f "$E1_SPLASH_LOCAL" ]; then
    # Hedef adres FRAGMENT ile veriliyor: splash dosyasina provizyon aninda
    # gomulen adres port degisince bayatlar, fragment ise HER OTURUMDA
    # yukarida cozulen guncel degeri tasir. Fragment sunucuya gitmez ve
    # dosyayi yeniden yazmayi gerektirmez.
    printf 'file://%s#%s' "$E1_SPLASH_LOCAL" "$(_splash_frag)"
  elif [ -f "$E1_SPLASH" ]; then
    # Snap/flatpak OLMAYAN tarayicilar (deb chromium) burayi okuyabiliyor.
    printf 'file://%s#%s' "$E1_SPLASH" "$(_splash_frag)"
  else
    printf '%s' "$URL"
  fi
}

# Musteri logosunu arka planda onbellege al (ilk acilista yok, sonrakilerde
# splash'ta gorunur). Tarayici dongusunu BEKLETMEZ.
_logo_onbellek &

# Tarayici cokerse/kapatilirsa ekran BOS KALMASIN: yeniden baslat. Bu dongu
# ASLA bitmemeli (yukaridaki greeter aciklamasi).
while true; do
  TARGET="$(_hedef)"
  case "$E1_BROWSER" in
    *firefox*)
      "$E1_BROWSER" --kiosk "$TARGET"
      ;;
    *)
      # Cokme/oturum uyarilari kiosk'ta kullaniciya gosterilemez; kapatiyoruz.
      # shellcheck disable=SC2086
      "$E1_BROWSER" \
        --kiosk \
        --start-fullscreen \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-features=TranslateUI \
        --no-first-run \
        --check-for-update-interval=31536000 \
        --password-store=basic \
        --disable-pinch \
        --overscroll-history-navigation=0 \
        $EXTRA \
        "$TARGET"
      ;;
  esac
  # Ekran yoneticileri cok kisa suren oturumlarda otomatik girisi tekrarlamayi
  # birakir; hizli dongu yapma.
  sleep 5
done
E1_SESSION_EOF
} > "${SESSION_BIN}.tmp"
mv -f "${SESSION_BIN}.tmp" "$SESSION_BIN"
chmod 0755 "$SESSION_BIN"
chown root:root "$SESSION_BIN"
e1_ok "Oturum betigi: ${SESSION_BIN}"

# --- 3b) Gecis ekrani (splash) ---------------------------------------------
# Ek paket YOK: sayfa tarayicinin kendisiyle gosteriliyor. file:// kaynagindan
# fetch/XHR CORS'a takilir, <img> yuklemesi takilmaz — uygulamayi favicon ile
# yokluyoruz (onload=ayakta, onerror=henuz degil).
# Yazilamazsa OLUMCUL DEGIL: oturum betigi splash dosyasi yoksa dogrudan
# uygulama adresini acar (eski davranis).
install -d -m 0755 "$SHARE_DIR" 2>/dev/null || true
# Logo: acik (light) surum tercih edilir — splash koyu zeminli (#0b1220),
# koyu logo orada okunmuyordu. Eski varlik yedek olarak kaliyor ki logo
# dosyasi bulunmayan bir kurulumda ekran logosuz kalmasin.
for _logo in e1-logo-light.png e1-avatar.png; do
  if [[ -f "${SCRIPT_DIR}/assets/${_logo}" ]]; then
    install -m 0644 "${SCRIPT_DIR}/assets/${_logo}" "${SHARE_DIR}/kiosk-logo.png" \
      2>/dev/null || true
    break
  fi
done
unset _logo
_sp=1
{ cat > "${SPLASH_FILE}.tmp" <<'E1_SPLASH_EOF'
<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>EnerjiOne Grid</title><style>
 :root{--ink:#e6edf6;--sub:#8fa3bf;--accent:#f59e0b}
 *{box-sizing:border-box}
 html,body{height:100%;margin:0;background:#0b1220;color:var(--ink);cursor:none;
   font:16px/1.55 Arial,"Liberation Sans",Helvetica,sans-serif;overflow:hidden}
 /* Zemin: duz siyah yerine hafif isik huzmesi -- 7/24 acik bir panoda
    duz zemin "cihaz kilitlendi" izlenimi veriyor. */
 body::before{content:"";position:fixed;inset:0;
   background:radial-gradient(60% 50% at 50% 35%,rgba(245,158,11,.10),transparent 70%)}
 .wrap{position:relative;height:100%;display:flex;flex-direction:column;
   align-items:center;justify-content:center;gap:26px;padding:40px}
 /* Musteri logosu: yalnizca onbellekte VARSA gorunur (bkz. _logo_onbellek).
    Yoksa `onerror` ile gizlenir ve ekran musteri adiyla devam eder --
    kirik gorsel simgesi gostermek en kotusu olurdu. */
 #clogo{max-width:340px;max-height:150px;object-fit:contain;display:none}
 #cname{font-size:23px;font-weight:700;letter-spacing:.2px;text-align:center}
 #cname:empty{display:none}
 .status{text-align:center}
 .status p{margin:0;color:var(--sub);font-size:15px}
 .b{margin:18px auto 0;width:240px;height:3px;background:rgba(255,255,255,.09);
   border-radius:2px;overflow:hidden}
 .b i{display:block;width:38%;height:100%;background:var(--accent);
   animation:s 1.4s cubic-bezier(.45,.05,.55,.95) infinite}
 @keyframes s{from{transform:translateX(-110%)}to{transform:translateX(280%)}}
 /* Alt serit: solda urun kimligi (login ekranindaki gibi), sagda kurulum
    bilgisi. Bos alanlar kendiliginden kaybolur. */
 .foot{position:fixed;left:0;right:0;bottom:0;padding:20px 28px;
   display:flex;align-items:flex-end;justify-content:space-between;gap:16px}
 .brand{display:flex;align-items:center;gap:10px;opacity:.92}
 .brand img{width:30px;height:30px;border-radius:7px}
 .brand span{font-size:15px;font-weight:700;letter-spacing:.2px}
 .meta{text-align:right;color:var(--sub);font-size:12.5px;line-height:1.7}
 .meta div:empty{display:none}
 .meta b{color:#c4d2e6;font-weight:600}
</style></head><body>
<div class="wrap">
 <img id="clogo" alt="">
 <div id="cname"></div>
 <div class="status">
  <p id="m">Sistem başlatılıyor…</p>
  <div class="b"><i></i></div>
 </div>
</div>
<div class="foot">
 <div class="brand"><img src="kiosk-logo.png" alt=""><span>EnerjiOne Grid</span></div>
 <div class="meta">
  <div id="mcust"></div>
  <div id="mver"></div>
 </div>
</div>
<script>
 /* Degerler FRAGMENT'ten okunur: u=<adres>&c=<musteri>&v=<surum>.
    Dosyaya gomulen deger port/surum/musteri degisince bayatlar; fragment
    her oturumda oturum betiginin cozdugu guncel degeri tasir. */
 function frag(){var o={},h=(location.hash||"").slice(1);
   h.split("&").forEach(function(p){var i=p.indexOf("=");if(i<0)return;
     try{o[p.slice(0,i)]=decodeURIComponent(p.slice(i+1));}catch(e){}});
   return o;}
 var F=frag();
 var U=F.u||"";
 if(!/^https?:\/\//.test(U))U="__E1_URL__";   /* yedek: provizyon degeri */

 var cus=(F.c||"").trim(), ver=(F.v||"").trim();
 if(cus){document.getElementById("cname").textContent=cus;
         document.getElementById("mcust").innerHTML="<b>"+
           cus.replace(/[<&]/g,function(c){return c==="<"?"&lt;":"&amp;";})+"</b>";}
 if(ver){document.getElementById("mver").textContent="Surum "+
           ver.replace(/[<&]/g,"");}

 /* Musteri logosu varsa adin YERINE gecer; ikisini birden gostermek
    tekrar olurdu. */
 var cl=document.getElementById("clogo");
 cl.onload=function(){cl.style.display="block";
   document.getElementById("cname").textContent="";};
 cl.onerror=function(){};
 cl.src="customer-logo.png?t="+Date.now();

 var n=0;
 function go(){location.replace(U);}
 function msg(t){document.getElementById("m").textContent=t;}
 /* ASLA PES ETME. Eskiden 90 denemeden (3 dk) sonra uygulama ayakta olmasa
    bile adrese gidiliyordu; tarayici hata sayfasi cikiyor, splash gittigi
    icin geri donus olmuyordu ve operator ekranda tarayici hatasi
    goruyordu. Artik surekli yokluyoruz. */
 function probe(){var im=new Image();
   im.onload=go;
   im.onerror=function(){n++;
     if(n===20)msg("Uygulama bekleniyor…");
     else if(n===300)msg("Henüz başlamadı. Cihazı kapatmayın; sorun sürerse teknik destek ile iletişime geçin.");
     setTimeout(probe,2000);};
   im.src=U.replace(/\/$/,"")+"/favicon.png?t="+Date.now();}
 probe();
</script></body></html>
E1_SPLASH_EOF
} 2>/dev/null || _sp=0
# URL'yi tirnakli heredoc'tan SONRA sed ile yerlestiriyoruz: heredoc icinde
# genisletseydik $ / backtick kacis tuzagina girerdik (bkz. LightDM notu).
if [[ "$_sp" == "1" ]] \
   && sed -i "s|__E1_URL__|${KIOSK_URL}|g" "${SPLASH_FILE}.tmp" 2>/dev/null \
   && mv -f "${SPLASH_FILE}.tmp" "$SPLASH_FILE" 2>/dev/null; then
  chmod 0644 "$SPLASH_FILE" 2>/dev/null || true
  e1_ok "Gecis ekrani: ${SPLASH_FILE}"
else
  rm -f "${SPLASH_FILE}.tmp" 2>/dev/null || true
  e1_warn "Gecis ekrani yazilamadi — tarayici dogrudan uygulamayi acacak."
fi
unset _sp

# --- 4) X oturumu -----------------------------------------------------------
# ASIL YOL: kiosk kendi X oturumudur. Masaustu oturum yoneticisi (gnome-session
# /xfce4-session) hic calismaz -> duvar kagidi, panel, ikon ve ekran kilidi HIC
# olusmaz. Dosya adi = oturum kimligi ve SABIT; musteri adi yalnizca Name=
# icine girer.
#
# ONCE .tmp'ye yaz, sonra atomik `mv`: yarim yazilmis bir oturum dosyasi
# ekran yoneticisinde kara ekran demektir.
_sess_name="EnerjiOne Grid"
[[ -n "$CUSTOMER_SAFE" ]] && _sess_name="EnerjiOne Grid (${CUSTOMER_SAFE})"
install -d -m 0755 /usr/share/xsessions 2>/dev/null || true
_xs=1
{ cat > "${XSESSION_FILE}.tmp" <<EOF
[Desktop Entry]
Type=Application
Name=${_sess_name}
GenericName=Operator ekrani
Comment=EnerjiOne Grid tam ekran operator arayuzu
Exec=${SESSION_BIN}
TryExec=${SESSION_BIN}
DesktopNames=E1Kiosk
X-LightDM-DesktopName=EnerjiOne Grid
EOF
} 2>/dev/null || _xs=0
# Salt-okunur /usr olan imajlarda yazma basarisiz olabilir; bu OLUMCUL DEGIL —
# asagidaki dogrulama SESSION_OK'i dusurur, cihaz eski yolda calismaya devam
# eder. Script'in burada olmesi kurulumun kalanini (otomatik giris) kacirirdi.
if [[ "$_xs" == "1" ]] && mv -f "${XSESSION_FILE}.tmp" "$XSESSION_FILE" 2>/dev/null; then
  chmod 0644 "$XSESSION_FILE" 2>/dev/null || true
  chown root:root "$XSESSION_FILE" 2>/dev/null || true
  e1_ok "X oturumu: ${XSESSION_FILE}"
else
  rm -f "${XSESSION_FILE}.tmp" 2>/dev/null || true
  e1_warn "X oturum dosyasi yazilamadi (${XSESSION_FILE})."
fi
unset _xs

# Ekran yoneticisini bu oturuma yonlendirmek RISKLI adimdir: yanlis/eksik bir
# oturum = acilmayan oturum = kara ekran. Once dogrula, sonra yonlendir.
has_xorg() {
  command -v Xorg >/dev/null 2>&1 || command -v X >/dev/null 2>&1 \
    || [[ -x /usr/lib/xorg/Xorg ]] || [[ -x /usr/libexec/Xorg ]]
}
SESSION_OK=1
if [[ "${E1_KIOSK_SESSION:-1}" == "0" ]]; then
  SESSION_OK=0
  e1_warn "E1_KIOSK_SESSION=0 — kendi X oturumu KULLANILMIYOR, isaretci kaldiriliyor."
elif [[ ! -f "$XSESSION_FILE" || ! -x "$SESSION_BIN" ]]; then
  SESSION_OK=0
  e1_warn "Oturum dosyasi/betigi dogrulanamadi — ekran yoneticisine dokunulmuyor."
elif ! has_xorg; then
  SESSION_OK=0
  e1_warn "Xorg bulunamadi (Wayland-only imaj) — ekran yoneticisine dokunulmuyor."
  e1_hint "Elle: sudo apt-get install -y xserver-xorg && sudo bash ${BASH_SOURCE[0]}"
fi
if [[ "$SESSION_OK" == "1" ]] && command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$XSESSION_FILE" >/dev/null 2>&1 \
    || e1_warn "desktop-file-validate uyari verdi — oturum yine de kullanilacak."
fi

# --- 4b) Yedek yol: masaustu autostart girisi -------------------------------
# SILMIYORUZ. Ciplak X oturumunda /etc/X11/Xsession zinciri ~/.config/autostart
# ISLEMEZ, yani bu girdi yeni oturumda zaten calismaz. Ama X oturumu bir sebeple
# tutmazsa (Xorg yok, DM oturumu bulamadi, greeter'dan masaustu secildi) cihaz
# BUGUNKU davranisa duser ve KULLANILABILIR kalir. Silseydik ayni senaryoda
# operator ciplak masaustu ile kalirdi. Cift baslatmayi oturum betigindeki
# flock kilidi engelliyor.
# AYRICA: _kiosk_bizim_mi() bu dosyayi "hesabi biz actik" kaniti olarak
# kullaniyor — kaldirilmamali.
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
e1_ok "Yedek acilis girdisi hazir."

# --- 5) Otomatik giris ------------------------------------------------------
# /var/lib/AccountsService/users/<kul> bir INI ve BASKA anahtarlar tasiyor
# (Icon= avatar script'inden geliyor). Dosyayi EZMIYORUZ; anahtar bazinda
# birlestiriyoruz — setup-user-avatars.sh ile ayni uc dalli mantik.
_as_set() {  # $1=dosya $2=anahtar $3=deger
  local f="$1" k="$2" v="$3"
  if [[ -f "$f" ]] && grep -qE "^[[:space:]]*${k}[[:space:]]*=" "$f"; then
    sed -i -E "s|^[[:space:]]*${k}[[:space:]]*=.*|${k}=${v}|" "$f"
  elif [[ -f "$f" ]] && grep -qE '^[[:space:]]*\[User\]' "$f"; then
    sed -i -E "0,/^[[:space:]]*\[User\]/s||[User]\n${k}=${v}|" "$f"
  else
    printf '[User]\n%s=%s\n' "$k" "$v" >> "$f"
  fi
}
_as_del() {  # $1=dosya $2=anahtar  (E1_KIOSK_SESSION=0 geri alma yolu)
  [[ -f "$1" ]] || return 0
  sed -i -E "/^[[:space:]]*${2}[[:space:]]*=/d" "$1"
}

case "$DM" in
  gdm3|gdm)
    # GDM'in okudugu dosya DAGITIMA GORE DEGISIR:
    #   Debian 12 (bookworm) : /etc/gdm3/daemon.conf   <- custom.conf YOK
    #   Ubuntu               : /etc/gdm3/custom.conf
    #   Fedora/RHEL          : /etc/gdm/custom.conf
    # Eskiden yalnizca son ikisi deneniyordu. Debian 12'de hicbiri
    # bulunmadigi icin `install -D` UYDURMA bir dosya yaratiyor, ayarlar
    # oraya yaziliyor ve ekrana "GDM otomatik girisi ✓" basiliyordu — ama
    # GDM o dosyayi HIC OKUMAZ. Sonuc: otomatik giris yok, greeter aciliyor
    # ve parolasi KILITLI kiosk hesabi giremiyor; cihaz operator icin
    # kullanilamaz hale geliyordu. Debian 12 belgelenmis hedef
    # (bkz. docs/DEPLOYMENT.md, install.sh basligi).
    GDM_CONF=""
    for _c in /etc/gdm3/daemon.conf /etc/gdm3/custom.conf /etc/gdm/custom.conf; do
      [[ -f "$_c" ]] && { GDM_CONF="$_c"; break; }
    done
    if [[ -z "$GDM_CONF" ]]; then
      # Dosya yok: UYDURMA. Yalnizca GDM'in KENDI dizinine yaz.
      if [[ -d /etc/gdm3 ]]; then
        GDM_CONF=/etc/gdm3/daemon.conf
      elif [[ -d /etc/gdm ]]; then
        GDM_CONF=/etc/gdm/custom.conf
      fi
      if [[ -n "$GDM_CONF" ]]; then
        install -D -m 0644 /dev/null "$GDM_CONF"
        printf '[daemon]\n' > "$GDM_CONF"
      fi
    fi
    if [[ -z "$GDM_CONF" ]]; then
      # GDM tespit edildi ama yapilandirma dizini yok — okunmayacak bir
      # dosyaya yazip "basarili" demektense ACIKCA uyariyoruz.
      e1_warn "GDM yapilandirma dizini bulunamadi — otomatik giris KURULAMADI."
      e1_warn "Cihaz acilista giris ekraninda kalir; kiosk hesabinin parolasi"
      e1_warn "kilitli oldugu icin operator giremez. Elle ayarlayin:"
      e1_hint "  GDM ayar dosyasina [daemon] altina:"
      e1_hint "    AutomaticLoginEnable=true"
      e1_hint "    AutomaticLogin=${KIOSK_USER}"
    fi
    if [[ -n "$GDM_CONF" ]]; then
    # Yedek: ILK kosuda alinan orijinali KORU. Her kosuda uzerine yazilirsa
    # ikinci update.sh sonrasi yedek "bizim yazdigimiz" hali gosterir ve
    # geri donus imkani kaybolur.
    [[ -f "${GDM_CONF}.e1-bak" ]] || cp -a "$GDM_CONF" "${GDM_CONF}.e1-bak" 2>/dev/null || true
    # Mevcut satirlari temizleyip [daemon] bolumune yeniden ekle.
    sed -i '/^\s*AutomaticLoginEnable\s*=/d; /^\s*AutomaticLogin\s*=/d' "$GDM_CONF"
    if grep -q '^\[daemon\]' "$GDM_CONF"; then
      sed -i "0,/^\[daemon\]/s//[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=${KIOSK_USER}/" "$GDM_CONF"
    else
      printf '\n[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=%s\n' "$KIOSK_USER" >> "$GDM_CONF"
    fi
    e1_ok "GDM otomatik girisi: ${KIOSK_USER}  (${GDM_CONF})"
    fi
    # GDM oturumu kullanicinin AccountsService dosyasindan cozer (otomatik
    # giriste de). Dosya EZILMEZ: avatar script'i ayni dosyaya Icon= yaziyor,
    # anahtar bazinda birlestiriyoruz.
    # NOT: WaylandEnable=false YAZMIYORUZ — greeter dahil tum sistemi etkileyen
    # buyuk cekic; Xorg eksikse greeter'i da oldurur.
    AS_FILE="/var/lib/AccountsService/users/${KIOSK_USER}"
    if [[ -d /var/lib/AccountsService ]]; then
      install -d -m 700 /var/lib/AccountsService/users
      [[ -f "$AS_FILE" && ! -f "${AS_FILE}.e1-bak" ]] \
        && cp -a "$AS_FILE" "${AS_FILE}.e1-bak" 2>/dev/null || true
      if [[ "$SESSION_OK" == "1" ]]; then
        # XSession= eski anahtar (gdm 3.x), Session= yeni anahtar (>= 3.34),
        # SessionType=x11 tanimayan surumlerde zararsiz, taniyan surumde
        # Wayland denemesini engeller. Ubuntu'nun kendi accountsservice'i de
        # ucunu birden yazar.
        _as_set "$AS_FILE" Session     "$XSESSION_ID"
        _as_set "$AS_FILE" XSession    "$XSESSION_ID"
        _as_set "$AS_FILE" SessionType x11
        e1_ok "GDM oturumu: ${XSESSION_ID}"
      else
        _as_del "$AS_FILE" Session
        _as_del "$AS_FILE" XSession
        _as_del "$AS_FILE" SessionType
        e1_info "GDM oturum isaretcisi kaldirildi (varsayilan masaustu)."
      fi
      chmod 600 "$AS_FILE" 2>/dev/null || true
      # accounts-daemon dosyayi onbellekler.
      systemctl try-restart accounts-daemon >/dev/null 2>&1 || true
    fi
    ;;
  lightdm)
    install -d -m 0755 /etc/lightdm/lightdm.conf.d
    # DIKKAT: heredoc DEGIL, satir satir printf — icinde HIC komut ikamesi
    # yok. Eski surumde tirnaksiz heredoc + kacirilmis "$(...)" bu dosyaya
    # LITERAL bir
    # komut-ikamesi metni yaziliyordu; lightdm.conf bir INI, onu calistirmaz,
    # gecersiz oturum adi bulup VARSAYILAN masaustune donuyordu. Dosya her
    # kosuda bastan yazildigi icin sahadaki bozuk satir da boylece duzelir.
    {
      printf '[Seat:*]\n'
      printf 'autologin-user=%s\n' "$KIOSK_USER"
      printf 'autologin-user-timeout=0\n'
      if [[ "$SESSION_OK" == "1" ]]; then
        # Otomatik giriste once autologin-session'a bakilir, yoksa
        # user-session'a dusulur (LightDM < 1.10 yalnizca ikincisini bilir).
        printf 'autologin-session=%s\n' "$XSESSION_ID"
        printf 'user-session=%s\n' "$XSESSION_ID"
      fi
    } > /etc/lightdm/lightdm.conf.d/50-enerjione-kiosk.conf.tmp
    mv -f /etc/lightdm/lightdm.conf.d/50-enerjione-kiosk.conf.tmp \
          /etc/lightdm/lightdm.conf.d/50-enerjione-kiosk.conf
    chmod 0644 /etc/lightdm/lightdm.conf.d/50-enerjione-kiosk.conf
    e1_ok "LightDM otomatik girisi: ${KIOSK_USER}"
    [[ "$SESSION_OK" == "1" ]] && e1_ok "LightDM oturumu: ${XSESSION_ID}"
    ;;
  sddm)
    install -d -m 0755 /etc/sddm.conf.d
    # Eskiden sabit `Session=plasma` yaziyordu; Plasma kurulu degilse otomatik
    # giris HIC calismiyordu. Kendi oturumumuz her durumda var.
    # `.desktop` uzantili yazilir: eski SDDM (0.13) tam dosya adi ister, yeni
    # surumler ikisini de kabul eder.
    # SDDM'de `[Autologin]` yalnizca User ile CALISMAZ: Session= yoksa
    # otomatik giris sessizce hic olmaz ve cihaz greeter'da kalir —
    # parolasi kilitli hesap giremedigi icin bu, cihazin kullanilamamasi
    # demektir. Bu yuzden kendi oturumumuz dogrulanamadiysa BOS BIRAKMIYORUZ,
    # sistemde kurulu ILK oturuma dusuyoruz (masaustu acilir ama autostart
    # yedegi tarayiciyi yine baslatir; greeter'da kilitli kalmaktan iyidir).
    _sddm_session="$XSESSION_ID"
    if [[ "$SESSION_OK" != "1" ]]; then
      _sddm_session="$(basename "$(ls /usr/share/xsessions/*.desktop 2>/dev/null | head -1)" .desktop 2>/dev/null || true)"
    fi
    {
      printf '[Autologin]\n'
      printf 'User=%s\n' "$KIOSK_USER"
      if [[ -n "$_sddm_session" ]]; then
        printf 'Session=%s.desktop\n' "$_sddm_session"
        # Oturum kapanirsa greeter'da kilitli-parola cikmazina dusmeyelim.
        printf 'Relogin=true\n'
      fi
    } > /etc/sddm.conf.d/50-enerjione-kiosk.conf.tmp
    mv -f /etc/sddm.conf.d/50-enerjione-kiosk.conf.tmp \
          /etc/sddm.conf.d/50-enerjione-kiosk.conf
    chmod 0644 /etc/sddm.conf.d/50-enerjione-kiosk.conf
    e1_ok "SDDM otomatik girisi: ${KIOSK_USER}"
    if [[ "$SESSION_OK" != "1" ]]; then
      if [[ -n "$_sddm_session" ]]; then
        e1_warn "Kendi oturumumuz dogrulanamadi — yedek oturum: ${_sddm_session}"
      else
        e1_warn "Hic oturum bulunamadi — SDDM otomatik girisi ELLE ayarlanmali."
      fi
    fi
    ;;
  *)
    # Cogu DM /usr/share/xsessions'i zaten tarar; oturum dosyasi yazildi ama
    # DM yapilandirmasina DOKUNMUYORUZ (riskli adim yalnizca bildigimiz
    # DM'lerde yapilir). Autostart yedegi duruyor, cihaz calisir kalir.
    e1_warn "Bilinmeyen ekran yoneticisi (${DM}) — otomatik giris ELLE ayarlanmali."
    e1_hint "Oturum: ${XSESSION_ID}   betik: ${SESSION_BIN}"
    ;;
esac

# --- 5b) Bosta kalma eylemi (systemd-logind) --------------------------------
# IdleAction masaustunden BAGIMSIZ calisan tek uyku/kilit tetikleyicisidir.
# SISTEM GENELIDIR (yonetim hesabinin oturumlarini da kapsar) — per-session
# ayarlanamaz. Yazdigimiz deger zaten Debian/Ubuntu VARSAYILANI; amac bir
# golden image'in bunu 'lock/suspend' yapmis olmasini engellemek.
# logind.conf'u DEGISTIRMIYORUZ: drop-in geri almayi tek `rm`e indiriyor.
if [[ -d /etc/systemd ]]; then
  install -d -m 0755 /etc/systemd/logind.conf.d
  _lg=/etc/systemd/logind.conf.d/50-enerjione-kiosk.conf
  _lg_new="$(mktemp)"
  cat > "$_lg_new" <<'EOF'
# EnerjiOne Grid kiosk - pano 7/24 acik kalir, bosta kalma eylemi yok.
# Kaldirmak icin:
#   sudo rm /etc/systemd/logind.conf.d/50-enerjione-kiosk.conf
#   sudo systemctl reload systemd-logind
[Login]
IdleAction=ignore
EOF
  if ! cmp -s "$_lg_new" "$_lg" 2>/dev/null; then
    install -m 0644 "$_lg_new" "$_lg"
    # reload YETER. `restart systemd-logind` acik oturumlari dusurebilir —
    # update.sh sirasinda calisan bir kiosk ekranini karartir. KULLANMA.
    systemctl reload systemd-logind >/dev/null 2>&1 || true
    e1_ok "Bosta kalma eylemi kapatildi (logind)."
  fi
  rm -f "$_lg_new"
fi

# Uyku hedeflerini kapatmak SISTEM GENELIDIR ve geri almayi zorlastirir.
# Masaustu olmayan bir kiosk oturumunda otomatik uyku ZATEN olmaz (uyku bir
# masaustu guc yoneticisi ozelligi), bu yuzden VARSAYILAN KAPALI.
if [[ "${E1_KIOSK_NO_SLEEP:-0}" == "1" ]]; then
  systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target \
    >/dev/null 2>&1 && e1_ok "Uyku hedefleri kapatildi (E1_KIOSK_NO_SLEEP=1)." \
    || e1_warn "Uyku hedefleri kapatilamadi."
fi

# --- 6) Grafik hedef acilista ----------------------------------------------
# Sunucu profilinden gelen sistemlerde varsayilan hedef multi-user olabilir;
# o zaman ekran hic acilmaz.
if [[ "$(systemctl get-default 2>/dev/null)" != "graphical.target" ]]; then
  systemctl set-default graphical.target >/dev/null 2>&1 \
    && e1_ok "Acilis hedefi graphical.target yapildi." \
    || e1_warn "Acilis hedefi degistirilemedi."
fi

ADMIN_USER="$(e1_target_user 2>/dev/null || echo "${SUDO_USER:-enerjione}")"
[[ -n "$ADMIN_USER" ]] || ADMIN_USER="${SUDO_USER:-enerjione}"

echo
e1_ok "Kiosk modu hazir."
e1_hint "Operator ekrani : ${KIOSK_USER}   (giris ekraninda: ${KIOSK_GECOS})"
e1_hint "                  Otomatik giris — ISLETIM SISTEMI PAROLASI YOKTUR."
if [[ "$SESSION_OK" == "1" ]]; then
  e1_hint "Oturum          : ${XSESSION_ID} (kendi X oturumu — masaustu ACILMAZ)"
else
  e1_hint "Oturum          : masaustu + autostart (yedek yol; kendi oturum KAPALI)"
fi
e1_hint "Ekran kilidi    : KAPALI (kararma, kilit ve uyku devre disi)."
e1_hint "Yonetim hesabi  : ${ADMIN_USER}   (SSH / sudo / update — ayri parola)"
e1_hint "Acilan adres    : ${KIOSK_URL}"
echo
e1_info "OPERATORE TESLIM METNI (musteriye aynen okuyun):"
e1_hint "  1) Cihazi acmaniz yeterli. Arayuz kendiliginden tam ekran gelir."
e1_hint "     Bu cihazin bir isletim sistemi parolasi YOKTUR; size verilmedi,"
e1_hint "     cunku hicbir zaman sorulmayacak."
e1_hint "  2) Ekranda kullanici adi/sifre isteyen bir pencere gorurseniz, bu"
e1_hint "     cihazin kilidi DEGILDIR — EnerjiOne Grid uygulamasinin kendi"
e1_hint "     giris ekranidir. Oraya size ayrica verilen UYGULAMA kullanicisini"
e1_hint "     yazin (or. installer)."
e1_hint "  3) Ekran karardiysa once ekrana dokunun / fareyi oynatin. Duzelmezse"
e1_hint "     cihazi kapatip acin: arayuz 1-2 dakika icinde kendiliginden geri"
e1_hint "     gelir. Parola sorulmaz."
e1_hint "  4) Cihaz uzerinde ayar veya guncelleme YAPILMAZ. Bakim, guncelleme ve"
e1_hint "     ag ayarlari yetkili teknik ekipte, ayri bir yonetim hesabindadir."
echo
e1_info "YONETICI NOTU — buna ragmen bir kilit/kara ekran kalirsa:"
e1_hint "  Ctrl+Alt+F3 -> ${ADMIN_USER} ile giris -> sudo loginctl unlock-sessions"
e1_hint "  Eski davranisa don : sudo E1_KIOSK_SESSION=0 bash ${BASH_SOURCE[0]}"
e1_hint "  Duzelmezse         : sudo systemctl restart ${DM}    (son care: reboot)"
e1_hint "  Operator hesabinin parolasi BILEREK kilitlidir; parola aramayin."
e1_hint "Gorunen adi degistir : sudo E1_CUSTOMER='TPAO' bash ${BASH_SOURCE[0]}"
e1_hint "Adi oldugu gibi birak: sudo E1_KIOSK_KEEP_GECOS=1 bash ${BASH_SOURCE[0]}"
e1_hint "Devre disi           : sudo E1_KIOSK=0 bash ${BASH_SOURCE[0]}"
exit 0
