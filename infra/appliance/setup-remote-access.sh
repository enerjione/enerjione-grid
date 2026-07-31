#!/usr/bin/env bash
# setup-remote-access.sh — e1-rad (uzaktan bakim izni ajani) host kurulumu.
#
# Ne yapar:
#   1. /var/lib/e1-grid/remote      durum dizinini olusturur (root:BACKEND_UID, 0770)
#   2. /var/lib/e1-grid/remote-priv izin belgesi dizinini olusturur (root:root, 0700)
#   3. e1-rad.py'yi calistirilabilir yapar
#   4. systemd unit'lerini kurar ve baslatir (path + report timer)
#   5. GEREKIRSE kisa sureli "kurulum mahsubu" (grace) yazar — bkz. asagi
#
# Neden ayri betik: setup-gateway-agent.sh ile ayni gerekce — bu ozellik
# appliance'a ozgu DEGIL. Tailscale VPS dahil her kurulumda kullanilabiliyor
# (install.sh ve update.sh setup-tailscale.sh'i kosulsuz cagiriyor), oysa
# e1-netd yalnizca setup-appliance.sh ile kuruluyor. Bu ajan e1-netd'ye
# baglansaydi appliance disi kurulumlarda uzaktan bakim izni HIC gorunmezdi.
#
# KURULUM MAHSUBU (grace) — NEDEN VAR:
#   Bu surumden itibaren uzaktan erisim VARSAYILAN KAPALI. Ama `update.sh`
#   cogu zaman Tailscale SSH uzerinden kosuyor: kapi guncelleme sirasinda
#   kapanirsa KENDI OTURUMUMUZU keseriz ve update.sh yarida kalir — cihaz
#   yarim guncellenmis ve erisilemez kalabilir. Bu yuzden betik, tailnet
#   uzerinden kosuldugunu tespit ederse kisa sureli bir izin yazar.
#   Grace SADECE (a) taze katilimda ve (b) tailnet uzerinden kosulan
#   kurulum/guncellemede yazilir. Yerelden kosulan rutin update.sh grace
#   YAZMAZ — aksi halde her guncelleme sessizce kapi acardi.
#
# Env:
#   INSTALL_DIR         varsayilan /opt/enerjione-grid
#   BACKEND_UID         backend container uid'si (varsayilan 10001)
#   E1_RAD_GRACE_MIN    mahsup suresi dk (varsayilan 60; 0 = mahsup yok)
#   E1_RAD_FRESH_JOIN   1 ise taze tailnet katilimi (setup-tailscale.sh set eder)
#   E1_RAD_LOCK_MODE    shields (varsayilan) | down  — ajana aktarilir
#
# Idempotent: her calistirmada guvenle tekrarlanabilir.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/enerjione-grid}"
BACKEND_UID="${BACKEND_UID:-10001}"
RA_STATE_DIR="/var/lib/e1-grid/remote"
RA_PRIV_DIR="/var/lib/e1-grid/remote-priv"
GRACE_MIN="${E1_RAD_GRACE_MIN:-60}"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "setup-remote-access.sh root olarak calistirilmali." >&2
  exit 1
fi

# Unit dosyalari /opt/enerjione-grid yolunu sabit kullanir; repo baska
# yerdeyse ajan bulunamaz.
if [[ "$SRC_DIR" != "${INSTALL_DIR}/infra/appliance" ]]; then
  echo "UYARI: repo ${INSTALL_DIR} altinda degil (${SRC_DIR})." >&2
  echo "UYARI: systemd unit'leri ${INSTALL_DIR}/infra/appliance/e1-rad.py bekler." >&2
fi

chmod 755 "${SRC_DIR}/e1-rad.py"

# Paylasilan IPC dizini: backend (uid ${BACKEND_UID}) buraya request.json
# yazar; ajan state.json/status.json yazar.
mkdir -p "$RA_STATE_DIR"
chown "root:${BACKEND_UID}" "$RA_STATE_DIR"
chmod 0770 "$RA_STATE_DIR"

# Izin belgesi (lease) dizini: SADECE root. Paylasilan dizinde yazma izni
# olan backend, icindeki root'a ait dosyalari da unlink/rename edebilirdi —
# yani kendine izin belgesi uydurabilir ya da bitis tarihini silebilirdi.
mkdir -p "$RA_PRIV_DIR"
chown root:root "$RA_PRIV_DIR"
chmod 0700 "$RA_PRIV_DIR"

# --- Ajan ayarlari (kalici) -------------------------------------------------
# systemd unit'leri kabuk ortamini MIRAS ALMAZ; E1_TAILSCALE_SSH ve
# E1_RAD_LOCK_MODE degerleri timer ile kosan ajana ancak bir env dosyasindan
# ulasir. Dosya varsa mevcut degerler korunur (kurulumda bir kez secilir,
# her guncellemede sifirlanmaz) — acikca yeni bir deger verilmedikce.
RA_ENV_DIR="/etc/enerjione-grid"
RA_ENV_FILE="${RA_ENV_DIR}/e1-rad.env"
mkdir -p "$RA_ENV_DIR"
_ra_env_get() {  # $1=anahtar
  [[ -f "$RA_ENV_FILE" ]] || return 1
  sed -n "s/^[[:space:]]*$1=\(.*\)$/\1/p" "$RA_ENV_FILE" | tail -1
}
RA_SSH="${E1_TAILSCALE_SSH:-$(_ra_env_get E1_TAILSCALE_SSH || true)}"
RA_SSH="${RA_SSH:-1}"
RA_LOCK="${E1_RAD_LOCK_MODE:-$(_ra_env_get E1_RAD_LOCK_MODE || true)}"
RA_LOCK="${RA_LOCK:-shields}"
cat > "$RA_ENV_FILE" <<EOF
# e1-rad (uzaktan bakim izni ajani) ayarlari — setup-remote-access.sh uretir.
# E1_TAILSCALE_SSH : izin VERILDIGINDE Tailscale SSH de acilsin mi (1|0)
# E1_RAD_LOCK_MODE : kapali durumun sertligi
#   shields = dugum ONLINE kalir, tum gelen baglantilar reddedilir (varsayilan)
#   down    = ayrica \`tailscale down\` — cihaz konsolda OFFLINE gorunur
E1_TAILSCALE_SSH=${RA_SSH}
E1_RAD_LOCK_MODE=${RA_LOCK}
EOF
chmod 0644 "$RA_ENV_FILE"
# Bu betigin kendi e1-rad.py cagrilari da ayni ayarlarla kossun.
export E1_TAILSCALE_SSH="$RA_SSH"
export E1_RAD_LOCK_MODE="$RA_LOCK"

install -m 644 "${SRC_DIR}/systemd/e1-rad.service"        /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-rad.path"           /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-rad-report.service" /etc/systemd/system/
install -m 644 "${SRC_DIR}/systemd/e1-rad-report.timer"   /etc/systemd/system/
systemctl daemon-reload
# Timer durursa acik bir izin KAPANMAZ; bu yuzden her kurulumda/guncellemede
# yeniden enable ediliyor. Backend ayrica state.json 180 sn'den eskiyse UI'da
# kirmizi uyari gosterir.
systemctl enable --now e1-rad.path >/dev/null 2>&1 || true
systemctl enable --now e1-rad-report.timer >/dev/null 2>&1 || true

# --- Kurulum mahsubu karari -------------------------------------------------
# Tailnet uzerinden mi kosuluyoruz? SSH_CONNECTION'in ilk alani istemci IP'si;
# 100.64.0.0/10 (CGNAT) tailnet adres blogudur.
#
# NOT: `sudo` env_reset ile SSH_CONNECTION'i dusurebilir. Bu yuzden degisken
# bos ise surec agacinda yukari yuruyup /proc/<pid>/environ icinden okuyoruz
# (root bunu okuyabilir). Boylece `sudo -E` gerekmez.
_ssh_client_ip() {
  local ip pid=$$ hops=0 ppid
  ip="${SSH_CONNECTION%% *}"
  if [[ -n "$ip" ]]; then printf '%s' "$ip"; return 0; fi
  while [[ $hops -lt 12 && -r "/proc/${pid}/status" ]]; do
    if [[ -r "/proc/${pid}/environ" ]]; then
      ip="$(tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null \
            | sed -n 's/^SSH_CONNECTION=//p' | head -1)"
      ip="${ip%% *}"
      if [[ -n "$ip" ]]; then printf '%s' "$ip"; return 0; fi
    fi
    ppid="$(awk '/^PPid:/{print $2}' "/proc/${pid}/status" 2>/dev/null || true)"
    [[ -n "$ppid" && "$ppid" != "0" && "$ppid" != "$pid" ]] || break
    pid="$ppid"
    hops=$((hops + 1))
  done
  return 1
}

_is_tailnet_ip() {  # 100.64.0.0/10  ->  100.64.x.x .. 100.127.x.x
  local o2
  [[ "$1" =~ ^100\.([0-9]{1,3})\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || return 1
  o2="${BASH_REMATCH[1]}"
  (( o2 >= 64 && o2 <= 127 ))
}

GRACE_SOURCE=""
if [[ "$GRACE_MIN" != "0" ]]; then
  CLIENT_IP="$(_ssh_client_ip || true)"
  if [[ -n "${CLIENT_IP:-}" ]] && _is_tailnet_ip "$CLIENT_IP"; then
    # Kapiyi simdi kapatirsak bu betigi calistiran SSH oturumu duser.
    GRACE_SOURCE="update"
  elif [[ "${E1_RAD_FRESH_JOIN:-0}" == "1" ]]; then
    # Taze katilim: kurulumcu ACL/SSH testini bitirebilmeli; o anda cogu
    # zaman musteri tarafinda yetkili kullanici bile tanimli degildir.
    GRACE_SOURCE="install"
  fi
fi

if [[ -n "$GRACE_SOURCE" ]]; then
  # --only-if-idle: zaten aktif bir izin varsa DOKUNMA (rutin guncelleme
  # musterinin verdigi pencereyi uzatmasin).
  "${SRC_DIR}/e1-rad.py" grant \
    --minutes "$GRACE_MIN" \
    --by kurulum \
    --role installer \
    --source "$GRACE_SOURCE" \
    --reason "kurulum/guncelleme mahsubu" \
    --only-if-idle >/dev/null 2>&1 || true
fi

# Ilk turu hemen kos: timer'i beklemeden durum yazilsin ve (mahsup yoksa)
# erisim KAPANSIN.
systemctl start e1-rad-report.service >/dev/null 2>&1 || \
  "${SRC_DIR}/e1-rad.py" report >/dev/null 2>&1 || true

# --- Ozet -------------------------------------------------------------------
RA_OPEN="$(sed -n 's/.*"open"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p' \
           "${RA_STATE_DIR}/state.json" 2>/dev/null | head -1)"
RA_EXP="$(sed -n 's/.*"expires_at"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
          "${RA_STATE_DIR}/state.json" 2>/dev/null | head -1)"
if [[ "$RA_OPEN" == "true" ]]; then
  echo "e1-rad hazir: uzaktan bakim izni ACIK${RA_EXP:+ (bitis ${RA_EXP})}."
else
  echo "e1-rad hazir: uzaktan bakim erisimi KAPALI (varsayilan)."
  echo "  Musterinin yetkili kullanicisi arayuzden izin verir:"
  echo "  Muhendislik > Sistem > Uzaktan Bakim"
fi
