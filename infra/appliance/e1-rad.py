#!/usr/bin/env python3
"""e1-rad — EnerjiOne Grid uzaktan bakim izni ajani (host tarafi, root).

NE ISE YARAR
------------
Saha cihazi tailnet'e KAYITLI kalir ama uzaktan erisim VARSAYILAN KAPALIDIR.
Musterinin yetkili kullanicisi arayuzden SURELI izin verir (1 saat / 8 saat /
24 saat); sure dolunca erisim KENDILIGINDEN kapanir.

Neden ayri bir ajan (e1-netd'ye eklenmedi)?
-------------------------------------------
1. KAPSAM: e1-netd yalnizca setup-appliance.sh (mini PC modu) ile kurulur.
   Tailscale ise install.sh/update.sh uzerinden VPS dahil her kurulumda var.
   e1-netd'ye eklenseydi appliance olmayan kurulumlarda bu ozellik hic
   gorunmezdi (setup-gateway-agent.sh de tam bu gerekceyle ayrilmisti).
2. ARIZA YALITIMI: sure dolunca kapanma bu ozelligin TEK guvenlik garantisi.
   e1-netd'nin report turu `nmcli device wifi list --rescan yes` (45 sn)
   icerebiliyor; NetworkManager takilirsa kapanma gecikirdi. Kapanma
   NetworkManager saglığına bagli OLMAMALI.

MIMARI (e1-netd / e1-gwd ile ayni desen)
----------------------------------------
    backend (container)  --yazar-->  /var/lib/e1-grid/remote/request.json
    e1-rad  (host, root) --okur -->  dogrular -> tailscale ile uygular

Backend tailscale'i CALISTIRMAZ. Tek yetkisi bir JSON dosyasi yazmaktir.

IKI DIZIN — NEDEN
-----------------
    /var/lib/e1-grid/remote       root:10001 0770  (paylasilan IPC)
        request.json  state.json  status.json
    /var/lib/e1-grid/remote-priv  root:root  0700  (yalniz ajan)
        lease.json  last-session.json  archive/

Izin belgesi (lease.json) paylasilan dizinde DURAMAZ: 0770 bir dizinde yazma
izni olan backend, icindeki root'a ait dosyalari da unlink/rename edebilir.
Yani backend kendine izin belgesi uydurabilir ya da bitis tarihini silebilirdi.
(e1-gwd token'li compose'u ayni gerekceyle /opt/enerjione-grid/gateways altinda
tutuyor.)

KAPATMA MEKANIZMASI — kalkan + tunel dusurme
-------------------------------------------------------------
Kapali durum = `tailscale set --shields-up=true` + `--ssh=false`
               + kurulu tunelin DUSURULMESI (`down`, ardindan `shields`
                 modunda hemen `up`).

Dugum tailnet'te KAYITLI ve (varsayilan modda) ONLINE kalir; TUM GELEN
baglantilar reddedilir.

TUNELI NEDEN DUSURUYORUZ: `--shields-up` yalnizca YENI baglantilari
reddeder. O an ACIK olan bir oturum (bakim yapan kisinin SSH'i) sure
dolsa bile calismaya devam ederdi — yani "sure bitince erisim kapanir"
vaadi tutmazdi. Bu yuzden kapanista tunel her modda indirilir; `shields`
modunda kalkan acikken hemen geri kaldirilir, boylece asagidaki canlilik
sinyali de korunur.

Neden `tailscale down` varsayilan degil:
  * CANLILIK SINYALI: `down` ile cihaz konsolda OFFLINE gorunur. O andan
    itibaren "elektrik kesik", "internet yok", "cihaz bozuk" ve "musteri izin
    vermemis" AYIRT EDILEMEZ. Kapi aylarca kapali duracagi icin bu bilgi
    kaybi kalicidir.
  * ACMA YOLU: `down` sonrasi acmak `tailscale up` demek, yani kontrol
    duzlemine gitmek. Kontrol duzlemi erisilemezse musteri "Izin ver" der ve
    HICBIR SEY olmaz — ustelik cihaz offline oldugu icin teshis de edilemez.
    `tailscale set` yerel IPC'dir; aninda ve deterministiktir.
  * SESSIZ GERI ACILMA: `down` durumunda BackendState "Stopped" olur ve
    setup-tailscale.sh'in idempotent erken cikisi devreye girmez; script
    asagi duser ve `tailscale up --authkey --reset` calistirip kapiyi geri
    acardi. (O script bu surumde ayrica duzeltildi, ama varsayilanin bu
    tuzagi hic kurmamasi daha saglamdir.)

Tam kapatma isteyen kurulum icin `E1_RAD_LOCK_MODE=down` var: o modda
kapanista `tailscale down` da calisir (musteri kendi guvenlik duvarinda
"hic trafik yok" diye dogrulayabilsin). Kayitlilik her iki modda da korunur:
`logout` HICBIR ZAMAN calistirilmaz, authkey ajanda YOKTUR.

SURE DOLUNCA KAPANMA
--------------------
`/var/lib/e1-grid/remote-priv/lease.json` MUTLAK son tarih tutar (sure degil).
`e1-rad-report.timer` 30 sn'de bir `e1-rad.py report` kosar; her tur "istenen
durum"u lease'ten hesaplar, "gercek durum"u `tailscale debug prefs`ten OLCER
ve farkliysa yakinsar. Backend, DB, container, ag — hicbiri ayakta olmak
zorunda degil. Lease yok / bozuk / saat tutarsiz => KAPAT (fail-closed).

Yeniden baslatma izni SILMEZ ama UZATMAZ: lease mutlak son tarih tuttugu icin
reboot sonrasi kalan pencere aynen surer. Bu bilincli — uzaktan bakimin en sik
sebebi zaten reboot gerektiren islerdir (e1-netd IP degisiminde cihazi yeniden
baslatiyor); reboot izni iptal etseydi teknisyen isin ortasinda kapida kalirdi.

DURUMU OLCUYORUZ, VARSAYMIYORUZ
-------------------------------
state.json'a `access.open = true` yalnizca `tailscale debug prefs` GERCEKTEN
ShieldsUp=false diyorsa yazilir. Alan okunamazsa (cok eski tailscale) `open`
false yazilir ve `mismatch: "prefs_unreadable"` isaretlenir — niyet degil
olcum yayinlanir (setup-tailscale.sh'teki `_report_key_expiry` ayni felsefe).

Kullanim (systemd tarafindan cagrilir):
    e1-rad.py report    -> durumu olc + lease'e yakinsa + state.json yaz (30 sn)
    e1-rad.py apply     -> request.json'i isle (path unit)
    e1-rad.py grant --minutes N [--by AD] [--role ROL] [--source KAYNAK]
                    [--reason METIN] [--only-if-idle]   -> kurulum mahsubu
    e1-rad.py close [--reason METIN]                    -> acil elle kapatma
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# --- Sabitler ---------------------------------------------------------------
STATE_DIR = os.environ.get("E1_RAD_STATE_DIR", "/var/lib/e1-grid/remote")
REQUEST_PATH = os.path.join(STATE_DIR, "request.json")
STATE_PATH = os.path.join(STATE_DIR, "state.json")
STATUS_PATH = os.path.join(STATE_DIR, "status.json")

# Yalniz ajanin gordugu dizin — backend buraya ERISEMEZ (mount edilmez).
PRIV_DIR = os.environ.get("E1_RAD_PRIV_DIR", "/var/lib/e1-grid/remote-priv")
LEASE_PATH = os.path.join(PRIV_DIR, "lease.json")
LAST_SESSION_PATH = os.path.join(PRIV_DIR, "last-session.json")
ARCHIVE_DIR = os.path.join(PRIV_DIR, "archive")
ARCHIVE_KEEP = 50

SCHEMA_VERSION = 1

# `tailscale set` yerel IPC — kisa. `up`/`down` kontrol duzlemine gider.
TS_TIMEOUT_SEC = 20
TS_UP_TIMEOUT_SEC = 45

# Ust sinir AJANDA sabit: backend ne gonderirse gondersin asilamaz. Sinirsiz
# sure "her zaman acik"i geri getirir, yani ozelligin tamamini iptal ederdi.
MIN_MINUTES = 15
MAX_MINUTES = 1440  # 24 saat

# Saat guvenligi: RTC pili bitmis bir mini PC'de boot'ta saat 1970'e donebilir.
# `now` lease'in verilme aninin GERISINDEYSE saat tutarsizdir -> KAPAT.
CLOCK_SKEW_TOLERANCE_SEC = 60

# shields | down  (bkz. dosya basi — varsayilan bilincli olarak `shields`)
LOCK_MODE = (os.environ.get("E1_RAD_LOCK_MODE") or "shields").strip().lower()
if LOCK_MODE not in ("shields", "down"):
    LOCK_MODE = "shields"

# E1_TAILSCALE_SSH'in ANLAMI BU SURUMDE DEGISTI: artik "SSH surekli acik"
# degil, "izin verildiginde Tailscale SSH de acilsin mi". 0 ise izin acikken
# bile SSH kapali kalir (yalnizca tailnet IP'sine dogrudan baglanti).
SSH_ALLOWED = (os.environ.get("E1_TAILSCALE_SSH") or "1").strip() != "0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    """journalctl -u e1-rad icin stdout'a yaz."""
    print(f"[e1-rad] {msg}", flush=True)


class RequestError(Exception):
    """Gecersiz istek — uygulanmadan reddedilir."""


# --- tailscale yardimcilari -------------------------------------------------
def _run(*args: str, check: bool = True, timeout: float | None = None) -> str:
    """tailscale calistir, stdout dondur. Hata durumunda RuntimeError.

    SOZLESME (e1-netd `_nmcli` ile birebir ayni): binary yoksa / zaman asimi /
    sifir olmayan cikis -> RuntimeError. request.json'dan gelen HICBIR deger
    buraya argüman olarak GECMEZ; calistirilan komutlar sabit argv dizileridir.
    """
    cmd = ["tailscale", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else TS_TIMEOUT_SEC,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("tailscale bulunamadi — kurulu degil.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"tailscale zaman asimi: {' '.join(cmd)}") from exc
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"tailscale hatasi ({proc.returncode}): {err[:300]}")
    return proc.stdout


def _run_json(*args: str, timeout: float | None = None) -> dict | None:
    """JSON ciktisi olan bir tailscale komutunu calistir; bozuksa None."""
    try:
        out = _run(*args, check=False, timeout=timeout)
    except RuntimeError as exc:
        _log(f"komut basarisiz ({' '.join(args)}): {exc}")
        return None
    text = (out or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _set_pref(flag: str, critical: bool = True) -> None:
    """`tailscale set <flag>` — bayrak SABIT metindir, disaridan gelmez."""
    try:
        _run("set", flag)
    except RuntimeError as exc:
        if critical:
            raise
        # Eski tailscale surumu bayragi tanimayabilir; kritik olmayanlarda
        # (or. --ssh) kurulum bozulmasin diye yalnizca log'lariz.
        _log(f"'{flag}' uygulanamadi (kritik degil): {exc}")


# --- Dosya yazma (e1-netd `_write_json` ile ayni sozlesme) ------------------
def _write_json(path: str, payload: dict, mode: int = 0o640,
                group_from: str | None = STATE_DIR) -> None:
    """Atomik yaz: once .tmp, sonra rename. Backend yarim dosya okumasin."""
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    if group_from:
        # Grup sahipligi backend container uid'sine ayarlanmis dizinden gelir.
        # (AttributeError: os.chown Unix-only — testler Windows'ta da kossun.)
        try:
            st = os.stat(group_from)
            os.chown(tmp, 0, st.st_gid)
        except (AttributeError, PermissionError, OSError):
            pass
    os.replace(tmp, path)


def _read_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _archive(name: str, payload: dict) -> None:
    """Islenmis istek/oturum kaydini arsivle (denetim + tekrar tetiklenmeme)."""
    try:
        os.makedirs(ARCHIVE_DIR, mode=0o700, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        _write_json(
            os.path.join(ARCHIVE_DIR, f"{stamp}-{name}.json"),
            payload,
            mode=0o600,
            group_from=None,
        )
        entries = sorted(os.listdir(ARCHIVE_DIR))
        for old in entries[:-ARCHIVE_KEEP]:
            _remove(os.path.join(ARCHIVE_DIR, old))
    except OSError as exc:
        _log(f"arsivleme basarisiz: {exc}")


# --- Durum olcumu -----------------------------------------------------------
def _empty_info() -> dict:
    return {
        "installed": False,
        "version": None,
        "daemon_running": False,
        "backend_state": None,
        "registered": False,
        "want_running": False,
        "hostname": None,
        "dns_name": None,
        "ipv4": None,
        "tags": [],
        "key_expiry": None,
        "key_expired": False,
        "ssh_enabled": False,
        # Bilinmiyorken KAPALI varsay: yanlis yon guvenli yondur.
        "shields_up": True,
    }


def _probe() -> dict:
    """Gercek durumu OLC. Donen: {info, verified, measured_open}.

    `verified` = `tailscale debug prefs` okunabildi ve ShieldsUp alani var.
    Dogrulanamayan bir durumda `measured_open` ASLA True olmaz.
    """
    info = _empty_info()
    probe = {"info": info, "verified": False, "measured_open": False}

    if shutil.which("tailscale") is None:
        return probe
    info["installed"] = True

    status = _run_json("status", "--json")
    if status:
        # Daemon cevap veriyor (logged-out olsa bile JSON doner).
        info["daemon_running"] = True
        info["backend_state"] = status.get("BackendState")
        info["version"] = status.get("Version")
        self_ = status.get("Self") or {}
        if isinstance(self_, dict):
            info["hostname"] = self_.get("HostName")
            dns = str(self_.get("DNSName") or "").rstrip(".")
            info["dns_name"] = dns or None
            ips = self_.get("TailscaleIPs") or []
            info["ipv4"] = next(
                (str(i) for i in ips if isinstance(i, str) and ":" not in i), None
            )
            info["tags"] = [str(t) for t in (self_.get("Tags") or [])]
            info["key_expiry"] = self_.get("KeyExpiry")
            info["key_expired"] = bool(self_.get("Expired"))

    prefs = _run_json("debug", "prefs")
    if isinstance(prefs, dict) and "ShieldsUp" in prefs:
        probe["verified"] = True
        info["shields_up"] = bool(prefs.get("ShieldsUp"))
        info["ssh_enabled"] = bool(prefs.get("RunSSH"))
        info["want_running"] = bool(prefs.get("WantRunning"))
        # `logout` calistirilmadigi surece LoggedOut false kalir — `down`
        # dugumu tailnet'ten SILMEZ.
        info["registered"] = not bool(prefs.get("LoggedOut"))
        if not info["tags"]:
            info["tags"] = [str(t) for t in (prefs.get("AdvertiseTags") or [])]
    else:
        # Prefs okunamadi: kayitlilik icin BackendState'e dus. "Stopped" da
        # kayitli demektir (tunel inik ama dugum duruyor).
        info["registered"] = info["backend_state"] in ("Running", "Stopped")

    probe["measured_open"] = bool(
        probe["verified"]
        and info["shields_up"] is False
        and info["backend_state"] == "Running"
    )
    return probe


def _unavailable_reason(info: dict) -> str | None:
    if not info["installed"]:
        return "tailscale_not_installed"
    if not info["daemon_running"]:
        return "daemon_not_running"
    if not info["registered"]:
        return "not_registered"
    if info["key_expired"]:
        return "key_expired"
    return None


# --- Izin belgesi (lease) ---------------------------------------------------
def _read_lease() -> dict | None:
    data = _read_json(LEASE_PATH)
    if not isinstance(data, dict):
        return None
    if not data.get("session_id"):
        return None
    return data


def _desired_from_lease(lease: dict | None, now: float) -> tuple[bool, str | None]:
    """(erisim_acik_olmali_mi, kapatma_sebebi). FAIL-CLOSED."""
    if lease is None:
        return False, "no_lease"
    try:
        granted = float(lease.get("granted_at_epoch") or 0)
        expires = float(lease.get("expires_at_epoch") or 0)
    except (TypeError, ValueError):
        return False, "lease_corrupt"
    if expires <= 0 or granted <= 0:
        return False, "lease_corrupt"
    if now < granted - CLOCK_SKEW_TOLERANCE_SEC:
        # Sistem saati lease'in verildigi andan GERIDE. Mutlak son tarih
        # anlamsiz — dogru yone (kapali) hata veriyoruz.
        return False, "clock_skew"
    if now >= expires:
        return False, "expired"
    return True, None


def _end_lease(lease: dict, reason: str) -> None:
    """Lease'i kapat: son oturum ozetini yaz, arsivle, dosyayi sil."""
    ended = {
        "schema": SCHEMA_VERSION,
        "session_id": lease.get("session_id"),
        "granted_by": lease.get("granted_by"),
        "granted_by_role": lease.get("granted_by_role"),
        "granted_at": lease.get("granted_at"),
        "expires_at": lease.get("expires_at"),
        "ended_at": _now_iso(),
        "end_reason": reason,
        "duration_minutes": lease.get("duration_minutes"),
        "reason": lease.get("reason"),
        "source": lease.get("source"),
    }
    _write_json(LAST_SESSION_PATH, ended, mode=0o600, group_from=None)
    _archive(f"session-{str(lease.get('session_id') or 'x')[:12]}", ended)
    _remove(LEASE_PATH)
    _log(
        f"uzaktan bakim izni kapandi ({reason}) — "
        f"veren: {lease.get('granted_by')}, bitis: {lease.get('expires_at')}"
    )


def _write_lease(
    *,
    session_id: str,
    minutes: int,
    granted_by: str | None,
    granted_by_role: str | None,
    reason: str | None,
    source: str,
    allow_ssh: bool,
) -> dict:
    """Yeni izin belgesi yaz. Son tarihi AJAN kendi saatinden hesaplar.

    Backend'in gonderdigi `expires_at` KULLANILMAZ (bilgi amaclidir): container
    saati/degeri manipule edilebilir, son tarih burada uretilir ve MAX_MINUTES
    ile kirpilir.
    """
    minutes = max(MIN_MINUTES, min(MAX_MINUTES, int(minutes)))
    now = datetime.now(timezone.utc)
    now_epoch = time.time()
    expires = now + timedelta(minutes=minutes)
    lease = {
        "schema": SCHEMA_VERSION,
        "session_id": session_id,
        "granted_at": now.isoformat(timespec="seconds"),
        "granted_at_epoch": now_epoch,
        "expires_at": expires.isoformat(timespec="seconds"),
        # KARAR ANI BU ALAN: her report turunda time.time() ile karsilastirilir.
        "expires_at_epoch": now_epoch + minutes * 60,
        "granted_by": granted_by,
        "granted_by_role": granted_by_role,
        "duration_minutes": minutes,
        "reason": reason,
        "source": source,
        "allow_ssh": bool(allow_ssh),
    }
    os.makedirs(PRIV_DIR, mode=0o700, exist_ok=True)
    _write_json(LEASE_PATH, lease, mode=0o600, group_from=None)
    _log(
        f"uzaktan bakim izni verildi: {minutes} dk (bitis {lease['expires_at']}), "
        f"veren: {granted_by or '-'} [{granted_by_role or '-'}], kaynak: {source}"
    )
    return lease


# --- Erisim durumunu uygula -------------------------------------------------
def _up_restore(shields_up: bool, ssh: bool) -> None:
    """Inik bir tuneli, MEVCUT TERCIHLERI KORUYARAK geri kaldir.

    BAYRAKSIZ `tailscale up` KULLANILAMAZ: CLI, varsayilan olmayan tum
    bayraklarin tekrar belirtilmesini zorunlu kilar ("changing settings via
    'tailscale up' requires mentioning all non-default flags") ve aksi halde
    HICBIR SEY YAPMADAN hata verir. Bizim dugumde --advertise-tags,
    --hostname, --accept-dns=false gibi bayraklar ayarli oldugu icin
    bayraksiz cagri her zaman basarisiz olur.

    `--reset` de KULLANILAMAZ: etiketleri (--advertise-tags) silerdi ve
    etiketsiz dugumun anahtari 180 gunde dolup cihaz tailnet'ten duserdi.

    Bu yuzden bayraklari mevcut tercihlerden OKUYUP tekrar veriyoruz.
    """
    prefs = _run_json("debug", "prefs") or {}
    args = ["up"]

    host = prefs.get("Hostname")
    if host:
        args.append(f"--hostname={host}")
    tags = [str(t) for t in (prefs.get("AdvertiseTags") or [])]
    if tags:
        args.append("--advertise-tags=" + ",".join(tags))
    # CorpDNS = "tailnet DNS'ini kabul et". Saha cihazinda varsayilan KAPALI
    # (yerel DNS/AP dnsmasq bozulmasin); mevcut degeri aynen koruyoruz.
    args.append("--accept-dns=" + ("true" if prefs.get("CorpDNS") else "false"))
    args.append("--ssh=" + ("true" if ssh else "false"))
    args.append("--shields-up=" + ("true" if shields_up else "false"))

    _run(*args, timeout=TS_UP_TIMEOUT_SEC)


def _drop_established() -> None:
    """Kurulu WireGuard tunellerini dusur — SURE DOLUNCA OTURUM KOPSUN.

    `--shields-up` yalnizca YENI baglantilari reddeder; o an ACIK olan bir
    bakim oturumu sure dolsa bile calismaya devam eder. Ozelligin vaadi
    ("sure bitince erisim kapanir") bunu gerektiriyor.

    NEDEN `tailscale down` DEGIL — SAHA VAKASI (v2.26.0):
      Once bu is `down` + ardindan bayraksiz `up` ile yapiliyordu. `up`
      CALISMADI: Tailscale CLI, varsayilan olmayan TUM bayraklarin tekrar
      belirtilmesini zorunlu kilar ("changing settings via 'tailscale up'
      requires mentioning all non-default flags"). Bizim dugumde
      --shields-up, --advertise-tags, --hostname, --accept-dns=false
      ayarli oldugu icin bayraksiz `up` hata verip cikti; dugum `down`
      durumunda KALDI. Sonuc: konsolda OFFLINE, SSH imkansiz. Izin verme
      yolu da ayni bayraksiz `up`i cagirdigi icin durum KENDILIGINDEN
      DUZELMEDI.

    COZUM: daemon'i yeniden baslat. Tunellerin hepsi kopar (istedigimiz bu),
    daemon kayitli tercihlerle (shields-up acik) geri gelir. WantRunning'e
    HIC dokunulmaz, dolayisiyla `up`in bayrak tuzagina da girilmez.
    """
    for unit in ("tailscaled", "tailscale"):
        try:
            subprocess.run(
                ["systemctl", "restart", unit],
                check=True, capture_output=True, timeout=TS_UP_TIMEOUT_SEC,
            )
            _log(f"kurulu oturumlar dusuruldu ({unit} yeniden baslatildi).")
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            continue
    # Daemon yeniden baslatilamadi: kalkan yine de acik, YENI baglanti
    # gelemez. Yalnizca o an acik olan oturum devam edebilir; bunu
    # sessizce gecmiyoruz.
    _log("UYARI: daemon yeniden baslatilamadi — kurulu oturumlar kopmamis olabilir.")


def _apply_access(open_: bool, allow_ssh: bool, backend_state: str | None) -> None:
    """Istenen erisim durumunu uygula.

    ACARKEN once tercihler ayarlanir, EN SON tunel kaldirilir — hicbir ara
    adimda hedeften daha acik olunmaz. KAPATIRKEN once en riskli yuzey
    (Tailscale SSH) kaldirilir, sonra kalkan, en son tunel.
    """
    if open_:
        _set_pref("--shields-up=false")
        _set_pref("--ssh=true" if (allow_ssh and SSH_ALLOWED) else "--ssh=false",
                  critical=False)
        if backend_state != "Running":
            # Tunel inik: ya biri elle `tailscale down` yapmis, ya da bu
            # ajanin ESKI surumu (v2.26.0) kapanista `down` calistirip
            # sonrasinda geri kaldiramamis. Ikinci durumda cihaz konsolda
            # OFFLINE kalir ve SSH calismaz — izin vermek de duzeltmez.
            # Burasi o cihazlari KURTARAN yoldur.
            _up_restore(shields_up=False, ssh=bool(allow_ssh and SSH_ALLOWED))
    else:
        _set_pref("--ssh=false", critical=False)
        _set_pref("--shields-up=true")

        # KURULU OTURUMLARI DUSUR — bu adim ZORUNLU.
        #
        # `--shields-up` yalnizca YENI baglantilari reddeder; o an ACIK olan
        # WireGuard tuneli ve uzerindeki SSH oturumu CALISMAYA DEVAM EDER.
        # Yani sure dolsa bile bakim yapan kisi baglantida kalirdi ve
        # ozelligin butun vaadi ("sure bitince erisim kapanir") coker.
        # Tuneli indirmek tum kurulu oturumlari aninda koparir.
        #
        # `shields` modunda dugumu HEMEN geri kaldiriyoruz: kalkan zaten
        # acik oldugu icin disaridan erisilemez, ama dugum konsolda ONLINE
        # gorunmeye devam eder. Bu, "cihaz elektriksiz/internetsiz" ile
        # "musteri izin vermemis" ayrimini korur. Bayraksiz `up` authkey
        # ISTEMEZ; kayitli dugum oldugu gibi geri gelir.
        _drop_established()


def _at_target(probe: dict, desired_open: bool, allow_ssh: bool) -> bool:
    """Olculen durum hedefe uyuyor mu? Dogrulanamiyorsa ASLA True."""
    if not probe["verified"]:
        return False
    info = probe["info"]
    want_ssh = bool(desired_open and allow_ssh and SSH_ALLOWED)
    if bool(info["ssh_enabled"]) != want_ssh:
        return False
    return bool(probe["measured_open"]) is bool(desired_open)


# --- report: cekirdek (sure dolunca kapatma BURADA) ------------------------
def cmd_report() -> int:
    """Durumu olc, lease'e yakinsa, state.json yaz.

    Sure dolunca kapanma bu fonksiyona baglidir; ayri bir "expire" timer'i,
    `at` isi veya transient `systemd-run` YOK. e1-netd'nin WiFi muhafizi da
    bilerek report turuna baglanmisti ("ayri systemd unit'i gerektirmesin
    diye"); ayni gerekce.
    """
    now = time.time()
    lease = _read_lease()
    desired_open, close_reason = _desired_from_lease(lease, now)
    if lease is not None and not desired_open:
        _end_lease(lease, close_reason or "lease_corrupt")
        lease = None

    allow_ssh = bool(lease.get("allow_ssh", True)) if lease else False
    probe = _probe()
    info = probe["info"]
    mismatch: str | None = None

    reason = _unavailable_reason(info)
    if reason in ("tailscale_not_installed", "daemon_not_running"):
        # Uygulanacak bir sey yok: tailscale yoksa/olmusse erisim zaten yok.
        _log(f"tailscale kullanilamiyor ({reason}) — durum yaziliyor.")
    else:
        if not _at_target(probe, desired_open, allow_ssh):
            # Dogrulanamayan durumda (eski tailscale) her turda iki kez
            # komut kosmayalim; tek deneme yeter.
            attempts = 2 if probe["verified"] else 1
            for attempt in range(1, attempts + 1):
                try:
                    _apply_access(desired_open, allow_ssh, info["backend_state"])
                except RuntimeError as exc:
                    _log(f"erisim durumu uygulanamadi (deneme {attempt}): {exc}")
                probe = _probe()
                info = probe["info"]
                if _at_target(probe, desired_open, allow_ssh):
                    break
            if not _at_target(probe, desired_open, allow_ssh):
                mismatch = (
                    "prefs_unreadable" if not probe["verified"]
                    else ("open_failed" if desired_open else "close_failed")
                )
                _log(
                    "UYARI: erisim durumu hedefe getirilemedi "
                    f"(hedef={'acik' if desired_open else 'kapali'}, {mismatch})"
                )
        reason = _unavailable_reason(info)

    last_session = _read_json(LAST_SESSION_PATH) or None
    if last_session:
        last_session = {k: v for k, v in last_session.items() if k != "schema"}

    state = {
        "schema": SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "lock_mode": LOCK_MODE,
        "ssh_allowed": SSH_ALLOWED,
        "min_duration_minutes": MIN_MINUTES,
        "max_duration_minutes": MAX_MINUTES,
        "tailscale": {
            "installed": info["installed"],
            "version": info["version"],
            "daemon_running": info["daemon_running"],
            "backend_state": info["backend_state"],
            "registered": info["registered"],
            "hostname": info["hostname"],
            "dns_name": info["dns_name"],
            "ipv4": info["ipv4"],
            "tags": info["tags"],
            "key_expiry": info["key_expiry"],
            "key_expired": info["key_expired"],
            "ssh_enabled": info["ssh_enabled"],
            "shields_up": info["shields_up"],
        },
        "access": {
            # OLCULEN deger — niyet degil.
            "open": bool(probe["measured_open"]),
            "verified": bool(probe["verified"]),
            "mismatch": mismatch,
            "session_id": lease.get("session_id") if lease else None,
            "granted_by": lease.get("granted_by") if lease else None,
            "granted_by_role": lease.get("granted_by_role") if lease else None,
            "granted_at": lease.get("granted_at") if lease else None,
            "expires_at": lease.get("expires_at") if lease else None,
            "duration_minutes": lease.get("duration_minutes") if lease else None,
            "reason": lease.get("reason") if lease else None,
            "source": lease.get("source") if lease else None,
        },
        "last_session": last_session,
        "reason": reason,
    }
    _write_json(STATE_PATH, state)
    return 0


# --- apply: request.json'i dogrula ve uygula -------------------------------
# Ortak alanlar + aksiyona ozel alanlar. BILINMEYEN ANAHTAR REDDEDILIR
# (e1-gwd ALLOWED_PARAM_KEYS emsali: bilinmeyen anahtar = protokol
# uyusmazligi veya kotu niyet; sessizce yutmuyoruz).
_COMMON_KEYS = frozenset({
    "id", "action", "created_at", "requested_by", "requested_by_role",
    "requested_ip", "reason",
})
_GRANT_KEYS = _COMMON_KEYS | {"duration_minutes", "expires_at", "allow_ssh"}
_REVOKE_KEYS = frozenset(_COMMON_KEYS)

_ACTOR_RE = re.compile(r"[^A-Za-z0-9._@\- ]")


def _clean_text(value, limit: int) -> str | None:
    """Kontrol karakterlerini at, kirp. Yalnizca goruntu/denetim icindir."""
    if value is None:
        return None
    text = "".join(ch for ch in str(value) if ch.isprintable())
    text = text.strip()[:limit]
    return text or None


def _validate(raw: dict) -> dict:
    """Istegi bagimsiz dogrula. Backend'e KORUKORUNE guvenilmez."""
    action = str(raw.get("action") or "").strip().lower()
    if action not in ("grant", "revoke"):
        raise RequestError(f"Bilinmeyen aksiyon: {action!r} (grant|revoke)")

    allowed = _GRANT_KEYS if action == "grant" else _REVOKE_KEYS
    unknown = sorted(set(raw.keys()) - set(allowed))
    if unknown:
        raise RequestError(f"Bilinmeyen alan(lar): {', '.join(unknown)}")

    actor = _clean_text(raw.get("requested_by"), 64)
    if actor:
        actor = _ACTOR_RE.sub("?", actor)
    role = _clean_text(raw.get("requested_by_role"), 32)
    if role:
        role = _ACTOR_RE.sub("?", role)

    out = {
        "action": action,
        "requested_by": actor,
        "requested_by_role": role,
        "reason": _clean_text(raw.get("reason"), 200),
    }
    if action == "revoke":
        return out

    duration = raw.get("duration_minutes")
    if isinstance(duration, bool) or not isinstance(duration, int):
        raise RequestError("duration_minutes tamsayi olmali.")
    if not MIN_MINUTES <= duration <= MAX_MINUTES:
        raise RequestError(
            f"duration_minutes {MIN_MINUTES}-{MAX_MINUTES} dakika araliginda olmali."
        )
    allow_ssh = raw.get("allow_ssh", True)
    if not isinstance(allow_ssh, bool):
        raise RequestError("allow_ssh true/false olmali.")
    out["duration_minutes"] = duration
    out["allow_ssh"] = allow_ssh
    return out


def cmd_apply() -> int:
    raw = _read_json(REQUEST_PATH)
    if raw is None:
        _log("request.json yok veya bozuk — yapilacak is yok.")
        return 0

    request_id = str(raw.get("id") or "")
    previous = _read_json(STATUS_PATH) or {}
    if request_id and previous.get("request_id") == request_id and previous.get(
        "status"
    ) in ("applied", "failed"):
        _log(f"istek zaten islenmis ({request_id}) — atlandi.")
        return 0

    action_raw = str(raw.get("action") or "")

    def _finish(ok: bool, error: str | None, applied: dict | None = None) -> int:
        result = {
            "schema": SCHEMA_VERSION,
            "request_id": request_id or None,
            "action": action_raw or None,
            "status": "applied" if ok else "failed",
            "error": error,
            "at": _now_iso(),
            "applied": applied or {},
        }
        _write_json(STATUS_PATH, result)
        _archive(f"request-{request_id[:12] or 'x'}", {"request": raw, "result": result})
        _remove(REQUEST_PATH)
        if not ok:
            _log(f"REDDEDILDI: {error}")
        return 0 if ok else 1

    _write_json(
        STATUS_PATH,
        {
            "schema": SCHEMA_VERSION,
            "request_id": request_id or None,
            "action": action_raw or None,
            "status": "applying",
            "error": None,
            "at": _now_iso(),
        },
    )

    try:
        req = _validate(raw)
    except RequestError as exc:
        return _finish(False, str(exc))

    if req["action"] == "revoke":
        lease = _read_lease()
        if lease is not None:
            _end_lease(lease, "revoked")
        else:
            _log("geri alma istegi: aktif izin yoktu — yine de kapali duruma yakinsanacak.")
        cmd_report()
        state = _read_json(STATE_PATH) or {}
        access = state.get("access") or {}
        return _finish(True, None, applied={"open": bool(access.get("open"))})

    # ---- grant ----
    probe = _probe()
    reason = _unavailable_reason(probe["info"])
    if reason:
        # Erisim acilamayacak durumda; sessiz basarisizlik yerine acik hata.
        return _finish(False, reason)

    lease = _write_lease(
        session_id=request_id or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        minutes=int(req["duration_minutes"]),
        granted_by=req["requested_by"],
        granted_by_role=req["requested_by_role"],
        reason=req["reason"],
        source="ui",
        allow_ssh=bool(req["allow_ssh"]),
    )
    cmd_report()
    state = _read_json(STATE_PATH) or {}
    access = state.get("access") or {}
    if not access.get("open"):
        # Lease yazildi ama kapi acilmadi — kullanici "bastim, olmadi" ile
        # bas basa kalmasin.
        return _finish(
            False,
            f"verify_failed: {access.get('mismatch') or 'erisim acilamadi'}",
            applied={"expires_at": lease["expires_at"]},
        )
    return _finish(
        True,
        None,
        applied={
            "expires_at": lease["expires_at"],
            "duration_minutes": lease["duration_minutes"],
            "open": True,
        },
    )


# --- CLI: kurulum mahsubu ve acil kapatma ----------------------------------
def _argval(argv: list[str], name: str) -> str | None:
    if name in argv:
        idx = argv.index(name)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    for item in argv:
        if item.startswith(f"{name}="):
            return item.split("=", 1)[1]
    return None


def cmd_grant(argv: list[str]) -> int:
    """Kurulum/guncelleme mahsubu (grace) ve elle izin verme.

    setup-remote-access.sh bunu `--only-if-idle` ile cagirir: zaten aktif bir
    izin varsa DOKUNMAZ (rutin guncelleme musterinin penceresini uzatmasin).
    """
    try:
        minutes = int(_argval(argv, "--minutes") or "60")
    except ValueError:
        _log("--minutes sayi olmali.")
        return 2
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        _log(f"--minutes {MIN_MINUTES}-{MAX_MINUTES} araliginda olmali.")
        return 2

    if "--only-if-idle" in argv:
        existing = _read_lease()
        open_now, _reason = _desired_from_lease(existing, time.time())
        if open_now:
            _log("aktif izin zaten var — mahsup yazilmadi (uzatma yapilmaz).")
            return 0

    _write_lease(
        session_id=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:20],
        minutes=minutes,
        granted_by=_clean_text(_argval(argv, "--by"), 64) or "kurulum",
        granted_by_role=_clean_text(_argval(argv, "--role"), 32) or "installer",
        reason=_clean_text(_argval(argv, "--reason"), 200),
        source=_clean_text(_argval(argv, "--source"), 16) or "cli",
        allow_ssh="--no-ssh" not in argv,
    )
    return cmd_report()


def cmd_close(argv: list[str]) -> int:
    """Acil elle kapatma: sudo e1-rad.py close"""
    lease = _read_lease()
    if lease is not None:
        _end_lease(lease, _clean_text(_argval(argv, "--reason"), 40) or "cli")
    return cmd_report()


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "report"
    if os.geteuid() != 0:
        _log("root olarak calistirilmali.")
        return 1
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(PRIV_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(PRIV_DIR, 0o700)
    except OSError:
        pass
    if command == "report":
        return cmd_report()
    if command == "apply":
        return cmd_apply()
    if command == "grant":
        return cmd_grant(argv[2:])
    if command == "close":
        return cmd_close(argv[2:])
    _log(f"bilinmeyen komut: {command} (report|apply|grant|close)")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
