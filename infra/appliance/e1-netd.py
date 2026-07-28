#!/usr/bin/env python3
"""e1-netd — EnerjiOne Grid appliance ag ajani (host tarafi, root).

Neden ayri bir ajan?
--------------------
Backend Docker container'i icinde, non-root (uid 10001) calisir ve host agina
erisimi YOKTUR. Kullanici arayuzunden IP/DNS degistirmek icin container'a
privileged + host network vermek gerekirdi; bu, container ele gecirildiginde
tum makineyi kaybetmek demek. Onun yerine:

    backend (container)  --yazar-->  /var/lib/e1-grid/net/request.json
    e1-netd (host, root) --okur -->  dogrular -> nmcli ile uygular -> reboot

Container'in tek yetkisi bir JSON dosyasi yazmak. Ajan gelen istegi kendi
kurallariyla dogrular (arayuz tipi ethernet mi, IP gecerli mi, AP profiline
dokunuluyor mu) ve sadece izin verdigi seyi yapar.

Kullanim (systemd tarafindan cagrilir):
    e1-netd.py report     -> state.json'i tazele (timer, 30 sn)
    e1-netd.py apply      -> request.json'i isle (path unit, dosya degisince)

Guvenlik agi: WiFi AP (EnerjiOne Grid) her zaman ayaktadir ve ethernet
ayarlarindan etkilenmez. Yanlis statik IP girilse bile cihaza AP uzerinden
http://e1-grid.local ile geri baglanip duzeltmek mumkundur.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

# --- Sabitler ---------------------------------------------------------------
STATE_DIR = os.environ.get("E1_NET_STATE_DIR", "/var/lib/e1-grid/net")
REQUEST_PATH = os.path.join(STATE_DIR, "request.json")
STATE_PATH = os.path.join(STATE_DIR, "state.json")
STATUS_PATH = os.path.join(STATE_DIR, "status.json")
# Islenmis istekler buraya tasinir (audit + tekrar tetiklenmeyi onler).
ARCHIVE_DIR = os.path.join(STATE_DIR, "archive")

# Ajanin yonettigi NetworkManager profil adlari.
ETH_CON_NAME = "e1-grid-eth"
AP_CON_NAME = "e1-grid-ap"
# WiFi client (station) profili — appliance'i mevcut bir aga baglar.
STA_CON_NAME = "e1-grid-wifi"

SCHEMA_VERSION = 2
NMCLI_TIMEOUT_SEC = 20
# WiFi baglantisi DHCP + auth icin daha uzun surebilir.
NMCLI_WIFI_TIMEOUT_SEC = 45
# Reboot oncesi bekleme: status.json diske insin, backend son durumu okuyabilsin.
REBOOT_DELAY_SEC = 3

# --- WiFi client / AP geri donus korumasi ----------------------------------
# Appliance'ta TEK WiFi karti var (setup-appliance.sh AP icin ilk wifi
# arayuzunu secer). Tek radyo ayni anda hem AP hem client olamaz; bu yuzden
# bir aga baglanirken AP DUSER. Yanlis sifre girilir veya ag kaybolursa
# sahadaki cihaza erisim tamamen kopar.
#
# Koruma: baglanti kurulurken bir "muhafiz" dosyasi yazilir. `report`
# komutu (systemd timer, 30 sn) her turda bu dosyayi kontrol eder:
#   - STA baglantisi AKTIF ve IP almis  -> muhafiz silinir, is tamam.
#   - Sure doldu ve hala baglanamamis    -> STA profili kapatilir, AP GERI ACILIR.
# Boylece en kotu durumda cihaz WIFI_GUARD_SEC sonra AP'siyle geri gelir.
GUARD_PATH = os.path.join(STATE_DIR, "wifi-guard.json")
WIFI_GUARD_SEC = int(os.environ.get("E1_WIFI_GUARD_SEC", "180"))
# Tarama sonucu ayri dosyaya yazilir (state.json'i sisirmemek + her
# report turunda pahali rescan yapmamak icin).
SCAN_PATH = os.path.join(STATE_DIR, "wifi-scan.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    """journalctl -u e1-netd icin stdout'a yaz."""
    print(f"[e1-netd] {msg}", flush=True)


# --- nmcli yardimcilari -----------------------------------------------------
def _nmcli(*args: str, check: bool = True, timeout: float | None = None) -> str:
    """nmcli calistir, stdout dondur. Hata durumunda RuntimeError.

    `timeout` verilmezse NMCLI_TIMEOUT_SEC kullanilir; WiFi baglantisi gibi
    uzun surebilecek islemler kendi suresini gecer.
    """
    cmd = ["nmcli", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else NMCLI_TIMEOUT_SEC,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("nmcli bulunamadi — NetworkManager kurulu degil.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"nmcli zaman asimi: {' '.join(cmd)}") from exc
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"nmcli hatasi ({proc.returncode}): {err[:300]}")
    return proc.stdout


def _redact(payload: dict) -> dict:
    """Log/arsiv icin kopya uret — WiFi sifresini ASLA disari sizdirma."""
    safe = dict(payload)
    for key in ("psk", "password", "wifi_psk"):
        if key in safe:
            safe[key] = "***"
    return safe


def _split_terse(line: str) -> list[str]:
    """nmcli -t ciktisini ':' ile bol; deger icindeki '\\:' kacislarini coz.

    MAC adresi gibi alanlarda nmcli ':' karakterini '\\:' olarak kacirir;
    duz split bunu parcalar.
    """
    parts: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in line:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _device_rows() -> list[dict]:
    """`nmcli device status` -> [{device, type, state, connection}]."""
    out = _nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")
    rows: list[dict] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        cols = _split_terse(line)
        if len(cols) < 4:
            continue
        rows.append(
            {
                "ifname": cols[0],
                "type": cols[1],
                "state": cols[2],
                "connection": cols[3] or None,
            }
        )
    return rows


def _device_detail(ifname: str) -> dict:
    """Bir cihazin aktif IP bilgisi (runtime — profil degil, gercek durum)."""
    detail: dict = {"mac": None, "addresses": [], "gateway": None, "dns": []}
    try:
        out = _nmcli(
            "-t",
            "-f",
            "GENERAL.HWADDR,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS",
            "device",
            "show",
            ifname,
        )
    except RuntimeError:
        return detail
    for line in out.splitlines():
        cols = _split_terse(line)
        if len(cols) < 2:
            continue
        key, value = cols[0], ":".join(cols[1:]) if len(cols) > 2 else cols[1]
        value = value.strip()
        if not value or value == "--":
            continue
        if key.startswith("GENERAL.HWADDR"):
            detail["mac"] = value
        elif key.startswith("IP4.ADDRESS"):
            detail["addresses"].append(value)
        elif key.startswith("IP4.GATEWAY"):
            detail["gateway"] = value
        elif key.startswith("IP4.DNS"):
            detail["dns"].append(value)
    return detail


def _connection_ipv4(con_name: str) -> dict:
    """Profilde KAYITLI ipv4 ayarlari (dhcp mi statik mi — kalici niyet)."""
    cfg = {"method": None, "addresses": [], "gateway": None, "dns": []}
    try:
        out = _nmcli(
            "-t",
            "-f",
            "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns",
            "connection",
            "show",
            con_name,
        )
    except RuntimeError:
        return cfg
    for line in out.splitlines():
        cols = _split_terse(line)
        if len(cols) < 2:
            continue
        key = cols[0]
        value = ":".join(cols[1:]).strip()
        if value in ("", "--"):
            continue
        if key == "ipv4.method":
            cfg["method"] = value
        elif key == "ipv4.addresses":
            cfg["addresses"] = [a.strip() for a in value.split(",") if a.strip()]
        elif key == "ipv4.gateway":
            cfg["gateway"] = value
        elif key == "ipv4.dns":
            cfg["dns"] = [d.strip() for d in value.split(",") if d.strip()]
    return cfg


def _ap_info(devices: list[dict]) -> dict:
    """AP profilinin durumu — UI'da 'her zaman acik guvenlik agi' olarak gosterilir."""
    info = {
        "connection": AP_CON_NAME,
        "exists": False,
        "active": False,
        "ssid": None,
        "ifname": None,
        "address": None,
        "secured": False,
    }
    try:
        out = _nmcli(
            "-t",
            "-f",
            "802-11-wireless.ssid,802-11-wireless-security.key-mgmt,ipv4.addresses,connection.interface-name",
            "connection",
            "show",
            AP_CON_NAME,
        )
    except RuntimeError:
        return info
    info["exists"] = True
    for line in out.splitlines():
        cols = _split_terse(line)
        if len(cols) < 2:
            continue
        key = cols[0]
        value = ":".join(cols[1:]).strip()
        if value in ("", "--"):
            continue
        if key == "802-11-wireless.ssid":
            info["ssid"] = value
        elif key == "802-11-wireless-security.key-mgmt":
            info["secured"] = True
        elif key == "connection.interface-name":
            info["ifname"] = value
    # Aktiflik + gercek adres cihaz uzerinden okunur.
    for dev in devices:
        if dev["connection"] == AP_CON_NAME:
            info["active"] = True
            info["ifname"] = dev["ifname"]
            detail = _device_detail(dev["ifname"])
            if detail["addresses"]:
                info["address"] = detail["addresses"][0].split("/")[0]
            break
    return info


# --- WiFi client (station) --------------------------------------------------
def _wifi_ifname(devices: list[dict]) -> str | None:
    """Appliance'in WiFi arayuzu. setup-appliance.sh AP icin ilk wifi
    arayuzunu seciyor; client de AYNI karti kullanir (tek radyo)."""
    for dev in devices:
        if dev["type"] == "wifi":
            return dev["ifname"]
    return None


def _scan_networks(ifname: str) -> list[dict]:
    """Gorunur aglari tara. AP aktifken de calisir (nmcli AP modunda da
    tarama yapabilir, sonuc sinirli olabilir)."""
    out = _nmcli(
        "-t",
        "-f",
        "SSID,SIGNAL,SECURITY,FREQ,IN-USE",
        "device",
        "wifi",
        "list",
        "ifname",
        ifname,
        "--rescan",
        "yes",
        timeout=NMCLI_WIFI_TIMEOUT_SEC,
    )
    best: dict[str, dict] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = _split_terse(line)
        if len(parts) < 5:
            continue
        ssid = parts[0].strip()
        if not ssid:
            continue  # gizli ag — SSID'siz satiri gosterme
        try:
            signal = int(parts[1] or 0)
        except ValueError:
            signal = 0
        security = (parts[2] or "").strip()
        entry = {
            "ssid": ssid,
            "signal": signal,
            "security": security or None,
            "secured": bool(security and security != "--"),
            "freq": (parts[3] or "").strip() or None,
            "in_use": parts[4].strip() == "*",
        }
        # Ayni SSID birden fazla AP'den gorulebilir — en guclusunu tut.
        prev = best.get(ssid)
        if prev is None or entry["signal"] > prev["signal"]:
            best[ssid] = entry
    return sorted(best.values(), key=lambda e: e["signal"], reverse=True)


def _sta_is_online(ifname: str) -> bool:
    """STA profili aktif ve IPv4 adresi almis mi?"""
    try:
        rows = _device_rows()
    except RuntimeError:
        return False
    dev = next((d for d in rows if d["ifname"] == ifname), None)
    if dev is None or dev["connection"] != STA_CON_NAME:
        return False
    if (dev["state"] or "").lower() not in ("connected", "connected (site only)"):
        return False
    detail = _device_detail(ifname)
    return bool(detail.get("addresses"))


def _ap_restore(reason: str) -> None:
    """STA'yi indirip AP'yi geri ac — kurtarma yolu."""
    _log(f"AP geri aciliyor ({reason})")
    try:
        _nmcli("connection", "down", STA_CON_NAME, check=False)
    except RuntimeError:
        pass
    try:
        _nmcli("connection", "up", AP_CON_NAME, timeout=NMCLI_WIFI_TIMEOUT_SEC)
    except RuntimeError as exc:
        _log(f"AP geri acilamadi: {exc}")


# --- AP geri donus muhafizi -------------------------------------------------
def _guard_arm(ifname: str, ssid: str) -> None:
    _write_json(
        GUARD_PATH,
        {
            "schema": SCHEMA_VERSION,
            "ifname": ifname,
            "ssid": ssid,
            "deadline": time.time() + WIFI_GUARD_SEC,
            "armed_at": _now_iso(),
        },
        mode=0o640,
    )


def _guard_clear() -> None:
    try:
        os.remove(GUARD_PATH)
    except OSError:
        pass


def _ensure_ap_when_offline() -> None:
    """DEGISMEZ KURAL: WiFi client'a bagli DEGILSEK, AP acik olmali.

    Amac: cihazin IP'si bilinmese bile her zaman bir giris yolu bulunsun.
    Kullanici "EnerjiOne Grid" agina baglanip http://10.42.0.1 ile girer.

    Tek radyo oldugu icin AP ile client ayni anda calisamaz; bu yuzden kural
    "client baglantisi YOKSA AP'yi ac" seklinde. Baglanti KURULMA ANINDA
    (muhafiz aktifken) dokunmayiz, aksi halde denemeyi bogar.

    `report` her turda (30 sn) cagirir; boylece AP elle kapatilsa veya
    baglanti kopsa bile en gec yarim dakikada geri gelir.
    """
    if os.path.exists(GUARD_PATH):
        return  # baglanma denemesi suruyor — _guard_check ilgilenecek

    try:
        devices = _device_rows()
    except RuntimeError:
        return
    ifname = _wifi_ifname(devices)
    if ifname is None:
        return  # WiFi karti yok

    if _sta_is_online(ifname):
        return  # bir aga bagliyiz, AP zaten olamaz (tek radyo)

    ap = _ap_info(devices)
    if not ap.get("exists"):
        return  # AP profili kurulmamis (SKIP_AP ile kurulmus olabilir)
    if ap.get("active"):
        return  # zaten acik

    _log("WiFi baglantisi yok ve AP kapali — AP aciliyor (erisim garantisi).")
    try:
        _nmcli("connection", "up", AP_CON_NAME, timeout=NMCLI_WIFI_TIMEOUT_SEC)
    except RuntimeError as exc:
        _log(f"AP acilamadi: {exc}")


def _guard_check() -> None:
    """`report` her turda cagirir (30 sn).

    Baglanti kurulduysa muhafizi kaldirir; sure dolmus ve hala baglanti
    yoksa AP'yi geri acar. Bu, tek radyolu cihazda yanlis sifre/kayip ag
    durumunda erisimin tamamen kopmasini onler.
    """
    guard = _read_json(GUARD_PATH)
    if not guard:
        return
    ifname = str(guard.get("ifname") or "")
    if not ifname:
        _guard_clear()
        return
    if _sta_is_online(ifname):
        _log(f"WiFi baglantisi dogrulandi ({guard.get('ssid')}) — muhafiz kaldirildi.")
        _guard_clear()
        return
    try:
        deadline = float(guard.get("deadline") or 0)
    except (TypeError, ValueError):
        deadline = 0
    if time.time() < deadline:
        return  # hala sure var, bekle
    _ap_restore(f"WiFi baglanamadi: {guard.get('ssid')}")
    _guard_clear()
    _write_json(
        STATUS_PATH,
        {
            "schema": SCHEMA_VERSION,
            "request_id": None,
            "status": "failed",
            "error": (
                f"'{guard.get('ssid')}' agina baglanilamadi; erisim noktasi (AP) "
                f"geri acildi. Sifreyi kontrol edip tekrar deneyin."
            ),
            "at": _now_iso(),
        },
    )


# --- Dosya yazma ------------------------------------------------------------
def _write_json(path: str, payload: dict, mode: int = 0o640) -> None:
    """Atomik yaz: once .tmp, sonra rename. Backend yarim dosya okumasin."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    # Grup sahipligi backend container uid'sine ayarlanmis dizinden miras alinir.
    # (AttributeError: os.chown Unix-only — testler Windows'ta da kosabilsin.)
    try:
        st = os.stat(STATE_DIR)
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


# --- report: mevcut durumu state.json'a yaz --------------------------------
def _hostname() -> str:
    try:
        return subprocess.run(
            ["hostnamectl", "--static"], capture_output=True, text=True, timeout=5
        ).stdout.strip() or os.uname().nodename
    except Exception:  # noqa: BLE001
        return os.uname().nodename


def build_state() -> dict:
    devices = _device_rows()
    interfaces: list[dict] = []
    for dev in devices:
        # Loopback ve container/bridge sanal arayuzleri UI'da gurultu yapar.
        if dev["type"] in ("loopback", "bridge", "tun", "veth", "ovs-interface"):
            continue
        if dev["ifname"].startswith(("docker", "br-", "veth", "virbr")):
            continue
        detail = _device_detail(dev["ifname"])
        profile = _connection_ipv4(dev["connection"]) if dev["connection"] else {}
        interfaces.append(
            {
                "ifname": dev["ifname"],
                "type": dev["type"],
                "state": dev["state"],
                "connection": dev["connection"],
                "managed_by_e1": dev["connection"] in (ETH_CON_NAME, AP_CON_NAME),
                "mac": detail["mac"],
                "addresses": detail["addresses"],
                "gateway": detail["gateway"],
                "dns": detail["dns"],
                # Profildeki kalici niyet: auto (DHCP) / manual (statik).
                "method": profile.get("method"),
                "profile_addresses": profile.get("addresses", []),
                "profile_gateway": profile.get("gateway"),
                "profile_dns": profile.get("dns", []),
            }
        )
    host = _hostname()
    return {
        "schema": SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "hostname": host,
        "mdns_name": f"{host}.local",
        "ap": _ap_info(devices),
        "wifi": _wifi_state(devices),
        "interfaces": interfaces,
        "eth_connection": ETH_CON_NAME,
    }


def _wifi_state(devices: list[dict]) -> dict:
    """WiFi client (STA) durumu — UI'daki 'Baglі ag' karti icin."""
    ifname = _wifi_ifname(devices)
    info: dict = {
        "supported": ifname is not None,
        "ifname": ifname,
        "connection": STA_CON_NAME,
        "connected": False,
        "ssid": None,
        "signal": None,
        "addresses": [],
        # Kayitli profil var mi (baglanti kopmus olsa bile).
        "saved": False,
        # Muhafiz aktifse UI geri sayim gosterir.
        "guard_active": False,
        "guard_deadline": None,
    }
    if ifname is None:
        return info

    try:
        out = _nmcli("-t", "-f", "NAME", "connection", "show", check=False)
        info["saved"] = any(
            _split_terse(line)[0].strip() == STA_CON_NAME
            for line in out.splitlines()
            if line.strip()
        )
    except RuntimeError:
        pass

    dev = next((d for d in devices if d["ifname"] == ifname), None)
    if dev is not None and dev["connection"] == STA_CON_NAME:
        detail = _device_detail(ifname)
        info["connected"] = bool(detail.get("addresses"))
        info["addresses"] = detail.get("addresses", [])
        try:
            out = _nmcli(
                "-t", "-f", "IN-USE,SSID,SIGNAL", "device", "wifi", "list",
                "ifname", ifname, "--rescan", "no",
            )
            for line in out.splitlines():
                parts = _split_terse(line)
                if len(parts) >= 3 and parts[0].strip() == "*":
                    info["ssid"] = parts[1].strip() or None
                    try:
                        info["signal"] = int(parts[2] or 0)
                    except ValueError:
                        info["signal"] = None
                    break
        except RuntimeError:
            pass

    guard = _read_json(GUARD_PATH)
    if guard:
        info["guard_active"] = True
        info["guard_deadline"] = guard.get("deadline")
        if not info["ssid"]:
            info["ssid"] = guard.get("ssid")
    return info


def cmd_report() -> int:
    # AP geri donus muhafizi + "bagli degilse AP acik" kurali. Her report
    # turunda (30 sn) kontrol edilir; ayri systemd unit'i gerektirmesin diye
    # bilerek buraya baglandi.
    try:
        _guard_check()
        _ensure_ap_when_offline()
    except Exception as exc:  # noqa: BLE001
        _log(f"wifi muhafiz kontrolu basarisiz: {exc}")

    try:
        state = build_state()
    except RuntimeError as exc:
        _log(f"durum okunamadi: {exc}")
        _write_json(
            STATE_PATH,
            {
                "schema": SCHEMA_VERSION,
                "updated_at": _now_iso(),
                "error": str(exc),
                "interfaces": [],
                "ap": {"exists": False, "active": False},
            },
        )
        return 1
    _write_json(STATE_PATH, state)
    return 0


# --- apply: request.json'i dogrula ve uygula -------------------------------
class RequestError(Exception):
    """Gecersiz istek — uygulanmadan reddedilir."""


IFNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


def _validate(request: dict, devices: list[dict]) -> dict:
    """Istegi dogrula ve normalize et. Gecersizse RequestError."""
    ifname = str(request.get("ifname") or "").strip()
    if not IFNAME_RE.match(ifname):
        raise RequestError(f"Gecersiz arayuz adi: {ifname!r}")

    dev = next((d for d in devices if d["ifname"] == ifname), None)
    if dev is None:
        raise RequestError(f"Arayuz bulunamadi: {ifname}")
    # SADECE ethernet. WiFi AP'ye veya sanal arayuzlere dokunmuyoruz — AP
    # kullanicinin geri donus yolu; onu bozacak bir istek kabul edilmez.
    if dev["type"] != "ethernet":
        raise RequestError(f"{ifname} ethernet degil ({dev['type']}); reddedildi.")
    if dev["connection"] == AP_CON_NAME:
        raise RequestError("AP profili degistirilemez.")

    method = str(request.get("method") or "").strip().lower()
    if method not in ("dhcp", "static"):
        raise RequestError(f"Gecersiz yontem: {method!r} (dhcp|static)")

    normalized = {
        "ifname": ifname,
        "method": method,
        "reboot": bool(request.get("reboot", True)),
    }
    if method == "dhcp":
        return normalized

    # ---- Statik dogrulama ----
    try:
        prefix = int(request.get("prefix", 24))
    except (TypeError, ValueError):
        raise RequestError("prefix sayi olmali (1-32).") from None
    if not 1 <= prefix <= 32:
        raise RequestError("prefix 1-32 araliginda olmali.")

    try:
        addr = ipaddress.IPv4Address(str(request.get("address", "")).strip())
    except ipaddress.AddressValueError:
        raise RequestError("Gecersiz IPv4 adresi.") from None
    if addr.is_loopback or addr.is_multicast or addr.is_reserved:
        raise RequestError("Bu IP adresi arayuze atanamaz (loopback/multicast).")

    network = ipaddress.IPv4Network(f"{addr}/{prefix}", strict=False)
    if addr == network.network_address and prefix < 31:
        raise RequestError("Ag adresi (network address) host IP'si olamaz.")
    if addr == network.broadcast_address and prefix < 31:
        raise RequestError("Broadcast adresi host IP'si olamaz.")

    gateway_raw = str(request.get("gateway") or "").strip()
    gateway = None
    if gateway_raw:
        try:
            gw = ipaddress.IPv4Address(gateway_raw)
        except ipaddress.AddressValueError:
            raise RequestError("Gecersiz ag gecidi (gateway) adresi.") from None
        if gw not in network:
            raise RequestError(
                f"Ag gecidi {gw} secilen alt agda degil ({network}). "
                "Yanlis gateway cihazi erisilemez yapar."
            )
        if gw == addr:
            raise RequestError("Ag gecidi, cihazin kendi IP'si olamaz.")
        gateway = str(gw)

    dns_raw = request.get("dns") or []
    if not isinstance(dns_raw, list):
        raise RequestError("dns bir liste olmali.")
    if len(dns_raw) > 3:
        raise RequestError("En fazla 3 DNS sunucusu girilebilir.")
    dns: list[str] = []
    for item in dns_raw:
        text = str(item).strip()
        if not text:
            continue
        try:
            dns.append(str(ipaddress.IPv4Address(text)))
        except ipaddress.AddressValueError:
            raise RequestError(f"Gecersiz DNS adresi: {text}") from None

    # AP'nin alt agiyla cakisma: ethernet 10.42.0.0/24'e alinirsa AP yonlendirmesi
    # bozulur ve kullanici her iki yoldan da erisemeyebilir.
    ap_net = ipaddress.IPv4Network("10.42.0.0/24")
    if network.overlaps(ap_net):
        raise RequestError(
            "Bu alt ag WiFi AP alt agi (10.42.0.0/24) ile cakisiyor; baska bir aralik secin."
        )

    normalized.update(
        {
            "address": str(addr),
            "prefix": prefix,
            "gateway": gateway,
            "dns": dns,
        }
    )
    return normalized


def _ensure_eth_connection(ifname: str, devices: list[dict]) -> str:
    """Ethernet icin yonetilen profil adini dondur; yoksa olustur.

    Cihazin zaten bir profili varsa (orn. netplan'dan gelen 'Wired connection 1')
    onu kullaniriz — yeni profil ekleyip ikisinin yarismasina izin vermeyiz.
    """
    dev = next((d for d in devices if d["ifname"] == ifname), None)
    existing = dev["connection"] if dev else None
    if existing:
        return existing
    # Cihaza bagli ama aktif olmayan profil var mi?
    out = _nmcli("-t", "-f", "NAME,DEVICE,TYPE", "connection", "show")
    for line in out.splitlines():
        cols = _split_terse(line)
        if len(cols) >= 3 and cols[1] == ifname and cols[2] in ("802-3-ethernet", "ethernet"):
            return cols[0]
    _log(f"{ifname} icin profil yok, olusturuluyor: {ETH_CON_NAME}")
    _nmcli(
        "connection",
        "add",
        "type",
        "ethernet",
        "ifname",
        ifname,
        "con-name",
        ETH_CON_NAME,
        "autoconnect",
        "yes",
    )
    return ETH_CON_NAME


def _apply_ipv4(con_name: str, req: dict) -> None:
    if req["method"] == "dhcp":
        _nmcli(
            "connection",
            "modify",
            con_name,
            "ipv4.method",
            "auto",
            "ipv4.addresses",
            "",
            "ipv4.gateway",
            "",
            "ipv4.dns",
            "",
            "ipv4.ignore-auto-dns",
            "no",
        )
        return
    args = [
        "connection",
        "modify",
        con_name,
        "ipv4.method",
        "manual",
        "ipv4.addresses",
        f"{req['address']}/{req['prefix']}",
        "ipv4.gateway",
        req["gateway"] or "",
    ]
    if req["dns"]:
        # ignore-auto-dns: statik modda DHCP DNS'i zaten gelmez ama profil
        # DHCP'den statige gecerken eski degerler takili kalabiliyor.
        args += ["ipv4.dns", " ".join(req["dns"]), "ipv4.ignore-auto-dns", "yes"]
    else:
        args += ["ipv4.dns", "", "ipv4.ignore-auto-dns", "no"]
    _nmcli(*args)


def _archive_request(raw: dict, result: dict) -> None:
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        os.chmod(ARCHIVE_DIR, 0o750)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        _write_json(
            os.path.join(ARCHIVE_DIR, f"{stamp}-{raw.get('id', 'req')}.json"),
            {"request": raw, "result": result},
        )
    except OSError as exc:
        _log(f"arsivleme atlandi: {exc}")


SSID_MAX_LEN = 32
PSK_MIN_LEN = 8
PSK_MAX_LEN = 63


def _handle_wifi(raw: dict, action: str, devices: list[dict], write_result) -> int:
    """WiFi client aksiyonlari: wifi_scan | wifi_connect | wifi_forget.

    AP ayarlarina DOKUNULMAZ (kurtarma yolu kurulum script'inin kontrolunde).
    Tek radyo oldugu icin wifi_connect AP'yi dusurur; muhafiz devreye girer.
    """
    ifname = _wifi_ifname(devices)
    if ifname is None:
        return write_result(False, "WiFi arayuzu bulunamadi (kart takili mi?).")

    # ---- Tarama: sadece okur, hicbir sey degistirmez ----
    if action == "wifi_scan":
        try:
            networks = _scan_networks(ifname)
        except RuntimeError as exc:
            return write_result(False, f"Tarama basarisiz: {exc}")
        _write_json(
            SCAN_PATH,
            {
                "schema": SCHEMA_VERSION,
                "updated_at": _now_iso(),
                "ifname": ifname,
                "networks": networks,
            },
        )
        _log(f"WiFi tarama: {len(networks)} ag bulundu ({ifname})")
        return write_result(True, None, applied={"action": "wifi_scan", "count": len(networks)})

    # ---- Kayitli agi unut: STA profilini sil, AP'yi geri ac ----
    if action == "wifi_forget":
        try:
            _nmcli("connection", "delete", STA_CON_NAME, check=False)
        except RuntimeError as exc:
            _log(f"STA profili silinemedi: {exc}")
        _guard_clear()
        _ap_restore("kullanici agi unuttu")
        _log("WiFi baglantisi kaldirildi, AP geri acildi.")
        return write_result(True, None, applied={"action": "wifi_forget"})

    # ---- Baglan ----
    ssid = str(raw.get("ssid") or "").strip()
    if not ssid or len(ssid) > SSID_MAX_LEN:
        return write_result(False, "Gecersiz SSID.")
    psk = str(raw.get("psk") or "")
    if psk and not (PSK_MIN_LEN <= len(psk) <= PSK_MAX_LEN):
        return write_result(
            False, f"WiFi sifresi {PSK_MIN_LEN}-{PSK_MAX_LEN} karakter olmali."
        )

    # AP'nin dusecegini KAYIT ALTINA AL: muhafiz once kurulur ki baglanma
    # sirasinda ajan olse bile bir sonraki report turu AP'yi geri acsin.
    _guard_arm(ifname, ssid)

    args = ["device", "wifi", "connect", ssid, "ifname", ifname, "name", STA_CON_NAME]
    if psk:
        args += ["password", psk]
    try:
        _nmcli(*args, timeout=NMCLI_WIFI_TIMEOUT_SEC)
    except RuntimeError as exc:
        # Baglanti kurulamadi — AP'yi HEMEN geri ac, muhafizi bekletme.
        _ap_restore("baglanti hatasi")
        _guard_clear()
        return write_result(False, f"'{ssid}' agina baglanilamadi: {exc}")

    online = _sta_is_online(ifname)
    if online:
        _guard_clear()
        _log(f"WiFi baglandi: {ssid} ({ifname})")
    else:
        # nmcli 0 dondurdu ama IP yok — muhafiz acik kalsin, report turu
        # ya dogrulayacak ya da AP'yi geri acacak.
        _log(f"WiFi baglantisi belirsiz ({ssid}) — muhafiz devrede.")

    detail = _device_detail(ifname)
    return write_result(
        True,
        None,
        applied={
            "action": "wifi_connect",
            "ssid": ssid,
            "ifname": ifname,
            "online": online,
            "addresses": detail.get("addresses", []),
            "guard_seconds": None if online else WIFI_GUARD_SEC,
        },
    )


def cmd_apply() -> int:
    raw = _read_json(REQUEST_PATH)
    if raw is None:
        _log("request.json yok veya bozuk — yapilacak is yok.")
        return 0

    request_id = str(raw.get("id") or "")
    previous = _read_json(STATUS_PATH) or {}
    if request_id and previous.get("request_id") == request_id and previous.get("status") in (
        "applied",
        "failed",
    ):
        _log(f"istek zaten islenmis ({request_id}) — atlandi.")
        return 0

    def _fail(message: str) -> int:
        _log(f"REDDEDILDI: {message}")
        result = {
            "schema": SCHEMA_VERSION,
            "request_id": request_id,
            "status": "failed",
            "error": message,
            "at": _now_iso(),
        }
        _write_json(STATUS_PATH, result)
        # _redact: WiFi sifresi arsive YAZILMAZ.
        _archive_request(_redact(raw), result)
        try:
            os.remove(REQUEST_PATH)
        except OSError:
            pass
        return 1

    def _write_result(ok: bool, error: str | None, applied: dict | None = None) -> int:
        """WiFi aksiyonlarinin ortak sonuc yazicisi (reboot yok)."""
        if not ok:
            return _fail(error or "Bilinmeyen hata")
        result = {
            "schema": SCHEMA_VERSION,
            "request_id": request_id,
            "status": "applied",
            "error": None,
            "at": _now_iso(),
            "applied": applied or {},
        }
        _write_json(STATUS_PATH, result)
        _archive_request(_redact(raw), result)
        try:
            os.remove(REQUEST_PATH)
        except OSError:
            pass
        cmd_report()
        return 0

    _write_json(
        STATUS_PATH,
        {
            "schema": SCHEMA_VERSION,
            "request_id": request_id,
            "status": "applying",
            "error": None,
            "at": _now_iso(),
        },
    )

    try:
        devices = _device_rows()
    except RuntimeError as exc:
        return _fail(str(exc))

    # Aksiyon dagitimi. `action` YOKSA "ipv4" kabul edilir — eski surumun
    # yazdigi request.json'lar (sadece ifname/method iceren) aynen calisir.
    action = str(raw.get("action") or "ipv4").strip().lower()
    if action in ("wifi_scan", "wifi_connect", "wifi_forget"):
        return _handle_wifi(raw, action, devices, _write_result)
    if action != "ipv4":
        return _fail(f"Bilinmeyen aksiyon: {action}")

    try:
        req = _validate(raw, devices)
    except RequestError as exc:
        return _fail(str(exc))

    _log(
        f"uygulaniyor: {req['ifname']} -> {req['method']}"
        + (f" {req.get('address')}/{req.get('prefix')}" if req["method"] == "static" else "")
    )

    try:
        con_name = _ensure_eth_connection(req["ifname"], devices)
        _apply_ipv4(con_name, req)
    except RuntimeError as exc:
        return _fail(f"nmcli uygulanamadi: {exc}")

    result = {
        "schema": SCHEMA_VERSION,
        "request_id": request_id,
        "status": "rebooting" if req["reboot"] else "applied",
        "error": None,
        "at": _now_iso(),
        "applied": {
            "ifname": req["ifname"],
            "connection": con_name,
            "method": req["method"],
            "address": req.get("address"),
            "prefix": req.get("prefix"),
            "gateway": req.get("gateway"),
            "dns": req.get("dns", []),
        },
    }
    _write_json(STATUS_PATH, result)
    _archive_request(_redact(raw), result)
    try:
        os.remove(REQUEST_PATH)
    except OSError:
        pass

    if not req["reboot"]:
        # Reboot'suz mod: profili yeniden yukle (kisa kesinti).
        try:
            _nmcli("connection", "up", con_name)
        except RuntimeError as exc:
            _log(f"profil yeniden yuklenemedi: {exc}")
        cmd_report()
        return 0

    cmd_report()
    _log(f"ayar kaydedildi, {REBOOT_DELAY_SEC} sn icinde yeniden baslatiliyor...")
    time.sleep(REBOOT_DELAY_SEC)
    subprocess.run(["systemctl", "reboot"], check=False)
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "report"
    if os.geteuid() != 0:
        _log("root olarak calistirilmali.")
        return 1
    os.makedirs(STATE_DIR, exist_ok=True)
    if command == "report":
        return cmd_report()
    if command == "apply":
        return cmd_apply()
    _log(f"bilinmeyen komut: {command} (report|apply)")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
