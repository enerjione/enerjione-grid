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
http://enerjione.local ile geri baglanip duzeltmek mumkundur.

WiFi: uc DURUST katman (schema 3)
---------------------------------
Appliance'ta TEK WiFi karti var; tek radyo ayni anda hem erisim noktasi (AP)
hem client OLAMAZ. Onceki surum bunu GORUNMEZ bir kuralla yonetiyordu
("client'a bagli degilsek AP acik olmali") ve UI o KURALI olcum gibi
gosterdigi icin sahada "AP acik" derken AP kapaliydi. Artik uc ayri sey
raporlanir ve hicbiri digerinin yerine gecmez:

  1. radio      — WiFi karti acik mi (yazilim/DONANIM kilidi ayri ayri).
                  Fiziksel onkosul; kapaliysa ne AP ne tarama calisir.
  2. wifi_role  — kartin GOREVI. `mode` = kullanicinin KALICI TERCIHI
                  (ap|client), `effective` = radyonun SU AN ne yaptigi
                  (ap|client|off|idle). Ikisi ayrisabilir ve bu bir hata
                  degil, anlatilacak bir durumdur.
  3. internet   — internet VAR/YOK + hangi arayuzden. "AP acik" ile
                  "internet var" BAMBASKA seylerdir.

DEGISMEZ (bozulamaz): cihaz erisilemez kalmaz. Otomatik dongu asla yikici
davranmaz (calisan bir client baglantisini dusurmez); WiFi'yi kapatmak
yalnizca IP almis bir ethernet varken kabul edilir; radyo kapaliyken kablo
da giderse ajan WiFi'yi kendiliginden geri acar.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
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

SCHEMA_VERSION = 3
NMCLI_TIMEOUT_SEC = 20
# WiFi baglantisi DHCP + auth icin daha uzun surebilir.
NMCLI_WIFI_TIMEOUT_SEC = 45
# `report` turu icinden yapilan yeniden baglanma denemesi. Report unit'i
# TimeoutStartSec ile sinirli; oradaki cagrilar kisa tutulmali.
NMCLI_RETRY_TIMEOUT_SEC = 25
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

# --- WiFi kartinin GOREVI (kalici tercih) -----------------------------------
# Tek radyo ayni anda hem erisim noktasi (AP) hem client OLAMAZ. Kullanicinin
# hangisini istedigi KALICI bir tercihtir ve burada tutulur:
#     "ap"     -> cihaz kendi agini yayinlar (ulasim garantisi, internet yok)
#     "client" -> cihaz kayitli bir aga katilir (internet var, AP kapanir)
# Dosya YOKSA "ap" kabul edilir; bu, bugunku ortuk davranisla ayni.
#
# Tercih ayrica NetworkManager'in kendi `connection.autoconnect` bayragina
# yazilir (bkz. _apply_mode_profiles). Boylece biz hic kosmasak da, boot'ta da
# gecerli olur — 30 sn'lik bir dongunun surekli dayatmasina gerek kalmaz.
MODE_PATH = os.path.join(STATE_DIR, "mode.json")
DEFAULT_MODE = "ap"
# STA profilinin autoconnect onceligi. AP profili setup-appliance.sh'ta
# autoconnect-priority 100 ile kuruluyor; client tercihi secildiginde boot'ta
# AP'nin onune gecmesi icin daha YUKSEK bir deger sart. Aksi halde kullanici
# "internete baglan" dese bile cihaz her reboot'ta AP ile geliyor.
STA_AUTOCONNECT_PRIORITY = 110

# Client moduna gecildi ama baglanti yok: AP'yi hemen acmayiz, once NM'in
# kendi yeniden baglanmasina sans veririz.
CLIENT_GRACE_SEC = int(os.environ.get("E1_CLIENT_GRACE_SEC", "120"))
# Fallback ile AP'ye dondukten sonra kayitli agi ne siklikla yeniden deneriz.
# TEK SEFERLIK fallback, musterinin WiFi'si bir saat kesilirse cihazi sonsuza
# dek internetsiz birakirdi (guncelleme/uzaktan bakim/saat senkronu olur).
CLIENT_RETRY_SEC = int(os.environ.get("E1_CLIENT_RETRY_SEC", "600"))
# Radyo kapaliyken kablo da giderse cihaz TAMAMEN erisilemez kalir ve kimse
# radyoyu geri acamaz. Bu sure boyunca hicbir arayuzde IP yoksa ajan WiFi'yi
# kendiliginden geri acar. 0 = kapali.
RADIO_RESCUE_SEC = int(os.environ.get("E1_RADIO_RESCUE_SEC", "300"))
# `nmcli radio wifi on` DONANIM kilidi varken de basari doner; komuttan sonra
# durumu bu sure boyunca dogrulariz, yalan soylemeyelim.
RADIO_VERIFY_SEC = int(os.environ.get("E1_RADIO_VERIFY_SEC", "10"))
# NM "connectivity" bilgisini vermiyorsa (unknown) kendi TCP kontrolumuz —
# en fazla bu siklikta ve YALNIZCA varsayilan rota varken.
NET_PROBE_SEC = int(os.environ.get("E1_NET_PROBE_SEC", "300"))
NET_PROBE_HOST = os.environ.get("E1_NET_PROBE_HOST", "connectivity-check.ubuntu.com")
NET_PROBE_PORT = int(os.environ.get("E1_NET_PROBE_PORT", "80"))
NET_PROBE_TIMEOUT_SEC = 3
# Varsayilan rota sinamasi icin hedef (paket GONDERILMEZ, sadece cekirdegin
# yonlendirme tablosuna sorulur).
ROUTE_PROBE_TARGET = os.environ.get("E1_ROUTE_TARGET", "1.1.1.1")


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

    LC_ALL/LANG=C ZORUNLU: nmcli durum metinlerini ("connected", "enabled",
    "full") YERELLESTIRIR. Turkce kurulmus bir sistemde "bagli" doner ve
    metne bakan tum kontroller (_sta_is_online, _wifi_client_online, radyo ve
    internet ayristirmalari) SESSIZCE yanlis sonuc verir — cihaz ya AP'yi hic
    acmaz ya da kurulumcunun baglantisini keser.
    """
    cmd = ["nmcli", *args]
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else NMCLI_TIMEOUT_SEC,
            env=env,
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


# --- Saf ayristirma / karar fonksiyonlari -----------------------------------
# Buradakiler subprocess CAGIRMAZ: girdi olarak komut ciktisini (string) veya
# hazir sozluk alirlar. Sahadaki kritik yollar donanima bagli oldugu icin
# dogrulanabilir tek katman burasi — testler bu fonksiyonlari cagirir
# (apps/backend-api/tests/test_netd_radio.py).
def parse_general_status(out: str) -> dict:
    """`nmcli -t -f STATE,CONNECTIVITY,WIFI-HW,WIFI general status` ciktisi.

    Ornek: "connected:limited:enabled:disabled"

    WIFI-HW = NetworkManager WirelessHardwareEnabled -> DONANIM anahtari.
              "disabled" ise fiziksel anahtar/BIOS kapali demektir; yazilimla
              ACILAMAZ, kullaniciya bunu soylemek gerekir.
    WIFI    = WirelessEnabled -> YAZILIM kilidi; bu arayuzden acilabilir.

    `nmcli radio wifi` tek basina yalnizca yazilim durumunu verir ve iki
    durumu AYIRMAZ; bu yuzden kullanilmiyor.
    """
    result: dict = {"state": None, "connectivity": None, "wifi_hw": None, "wifi": None}
    for line in out.splitlines():
        if not line.strip():
            continue
        cols = [c.strip().lower() for c in _split_terse(line)]
        keys = ("state", "connectivity", "wifi_hw", "wifi")
        for idx, key in enumerate(keys):
            if idx < len(cols) and cols[idx]:
                result[key] = cols[idx]
        break  # tek satirlik cikti
    return result


def radio_from_general(general: dict, wifi_device_present: bool) -> dict:
    """Radyo durumu: destekleniyor mu, acik mi, neden kapali.

    Soft/hard block ayrimi olmadan UI ya yalan soyler ("actim" der ama
    acilmaz) ya da hicbir sey soyleyemez. Sahadaki "AP yayinda degil" +
    "gorunur ag bulunamadi" ikilisinin ortak sebebi burada OLCULUR.
    """
    hw = general.get("wifi_hw")
    sw = general.get("wifi")
    hardware_enabled = hw != "disabled"
    # Donanim kilidi varken cihaz satiri kaybolabilir; "disabled" cevabi
    # zaten ortada bir radyo OLDUGUNU kanitlar.
    supported = bool(wifi_device_present) or hw == "disabled"
    if sw is None:
        # Alan okunamadi (eski nmcli / hata): cihaz varsa acik kabul et,
        # uydurma bir "kapali" gostergesi cikarmaktansa sessiz kal.
        enabled = bool(wifi_device_present)
    else:
        enabled = sw == "enabled"
    # DONANIM kilidi yazilim bayragini EZER: NM "WirelessEnabled=true" derken
    # fiziksel anahtar kapali olabilir; o durumda radyo GERCEKTE kapalidir.
    enabled = enabled and hardware_enabled
    blocked_by: str | None = None
    if supported and not enabled:
        blocked_by = "hardware" if not hardware_enabled else "software"
    return {
        "supported": supported,
        "enabled": bool(supported and enabled),
        "hardware_enabled": hardware_enabled,
        "blocked_by": blocked_by,
    }


def parse_ip_route_get(out: str) -> dict:
    """`ip route get <hedef>` ciktisindan cikis arayuzu + gateway.

    Ornek: "1.1.1.1 via 192.168.1.1 dev enp1s0 src 192.168.1.50 uid 0"
    Varsayilan rota yoksa komut hata verir; bos cikti -> hepsi None.
    PAKET GONDERMEZ: yalnizca cekirdegin yonlendirme tablosuna sorar.
    """
    info: dict = {"ifname": None, "gateway": None, "src": None}
    text = " ".join(out.split())
    if not text or "unreachable" in text.lower():
        return info
    parts = text.split(" ")
    for idx, token in enumerate(parts):
        if idx + 1 >= len(parts):
            break
        if token == "dev" and info["ifname"] is None:
            info["ifname"] = parts[idx + 1]
        elif token == "via" and info["gateway"] is None:
            info["gateway"] = parts[idx + 1]
        elif token == "src" and info["src"] is None:
            info["src"] = parts[idx + 1]
    return info


def via_kind(iftype: str | None) -> str | None:
    """nmcli cihaz tipini kullaniciya anlatilabilir bir sinifa cevir."""
    if not iftype:
        return None
    lowered = iftype.lower()
    if lowered in ("ethernet", "802-3-ethernet"):
        return "ethernet"
    if lowered in ("wifi", "802-11-wireless"):
        return "wifi"
    if lowered in ("vpn", "wireguard", "tun"):
        return "vpn"
    return "other"


def resolve_internet(
    connectivity: str | None,
    route: dict,
    via: str | None,
    probe_ok: bool | None = None,
) -> dict:
    """Internet durumunu KATMANLI oku — "AP acik" ile "internet var" ayri seyler.

    Katman 1: NM'in onbellekli CONNECTIVITY degeri (paket gondermez).
    Katman 2: varsayilan rota (paket gondermez, hangi arayuz oldugunu soyler).
    Katman 3: NM "unknown" diyorsa kendi TCP kontrolumuz (probe_ok).

    DEGISMEZ: "unknown" ASLA "internet var" gibi gosterilmez.
    """
    valid = ("full", "portal", "limited", "none")
    ifname = route.get("ifname")
    if not ifname:
        # Varsayilan rota yok: hicbir yere paket cikamaz. Katman 3 hic kosmaz.
        return {
            "state": "none",
            "source": "route",
            "ifname": None,
            "via": None,
            "gateway": None,
        }
    state = connectivity if connectivity in valid else "unknown"
    source = "nm" if state != "unknown" else None
    if state == "unknown" and probe_ok is not None:
        state = "full" if probe_ok else "limited"
        source = "probe"
    return {
        "state": state,
        "source": source,
        "ifname": ifname,
        "via": via,
        "gateway": route.get("gateway"),
    }


def derive_effective(radio_enabled: bool, ap_active: bool, client_online: bool) -> str:
    """WiFi kartinin SU AN ne yaptigi — KURAL DEGIL, OLCUM.

    Sahadaki celiskinin carasi budur: panel "AP acik" derken ust serit
    "yayinda degil" diyordu, cunku panel "client yoksa AP olmali" KURALINI
    olcum gibi gosteriyordu.
    """
    if not radio_enabled:
        return "off"
    if ap_active:
        return "ap"
    if client_online:
        return "client"
    return "idle"


def is_global_ipv4(cidr: str) -> bool:
    """"192.168.1.50/24" gercek bir adres mi? (link-local/loopback sayilmaz)"""
    text = (cidr or "").strip().split("/")[0]
    if not text:
        return False
    try:
        addr = ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError:
        return False
    return not (
        addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified
    )


def pick_wired_fallback(rows: list[dict]) -> dict | None:
    """WiFi kapatilirsa cihaza ulasilacak IKINCI yol var mi?

    Kriter OBJEKTIF tutuldu: bagli + gercek IPv4 almis bir ethernet.
    Kullanicinin "nereden bagli oldugunu" tahmin etmeye calismiyoruz;
    nginx arkasinda `request.client.host` zaten proxy IP'sini verir ve
    guvenilir degildir.
    """
    for row in rows:
        if (row.get("type") or "").lower() != "ethernet":
            continue
        if (row.get("state") or "").lower() not in ("connected", "connected (site only)"):
            continue
        addresses = [a for a in (row.get("addresses") or []) if is_global_ipv4(a)]
        if addresses:
            return {"ifname": row.get("ifname"), "addresses": addresses}
    return None


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


def _wifi_client_online(ifname: str, devices: list[dict]) -> bool:
    """Bu radyoda PROFIL ADINDAN BAGIMSIZ olarak calisan bir client var mi?

    NEDEN AYRI BIR FONKSIYON: `_sta_is_online` yalnizca BIZIM profilimize
    (STA_CON_NAME = "e1-grid-wifi") bakar ve bu bilincli bir tercihtir —
    muhafiz akisi (_guard_check) kendi baglanma denemesini dogrulamak
    zorundadir, baskasinin kurdugu bir baglantiyi "basarili" saymamalidir.

    Ama "AP'yi ac" karari icin o kriter YANLIS ve sahada kurulumu kirdi:
    ilk kurulumda kurulumcu cihaza kendi WiFi'sinden baglaniyor ve o profil
    ASLA "e1-grid-wifi" adini tasimiyor (netplan devri "netplan-wlan0-SSID",
    masaustu applet'i ya da `nmcli device wifi connect` dogrudan SSID adini
    verir; "e1-grid-wifi" adini yalnizca UI uzerinden yapilan WiFi baglama
    uretir ve o da backend ayakta olmadan calismaz). Sonuc: ajan "client
    bagli degil" deyip AP'yi aciyor, tek radyo AP moduna geciyor,
    kurulumcunun SSH oturumu kopuyor ve kurulum yarida kaliyor.

    Bu yuzden AP karari icin RADYONUN GERCEK DURUMUNA bakiyoruz:
    arayuzde aktif bir baglanti var + o baglanti AP profilimiz degil +
    radyo modu 'ap' degil (yani client) + IPv4 adresi almis.
    """
    dev = next((d for d in devices if d["ifname"] == ifname), None)
    if dev is None:
        return False
    con = (dev.get("connection") or "").strip()
    if not con or con == "--" or con == AP_CON_NAME:
        return False
    if (dev.get("state") or "").lower() not in ("connected", "connected (site only)"):
        return False
    # Mod okunamazsa eski NM varsayilani 'infrastructure' = client kabul edilir.
    try:
        mode = _nmcli(
            "-g", "802-11-wireless.mode", "connection", "show", con, check=False
        ).strip()
    except RuntimeError:
        mode = ""
    if mode == "ap":
        return False
    # IPv4 sarti: adressiz link ne SSH tasir ne uplink olur. setup-appliance.sh
    # tarafindaki ap_activation_safe AYNI kritere bakar; ikisi ayrisirsa biri
    # AP'yi bekletirken digeri acar ve taraflar birbiriyle kavga eder.
    return bool(_connection_ipv4_addrs(ifname))


def _connection_ipv4_addrs(ifname: str) -> list[str]:
    """Arayuzun global IPv4 adresleri (yoksa bos liste)."""
    try:
        out = _nmcli("-g", "IP4.ADDRESS", "device", "show", ifname, check=False)
    except RuntimeError:
        return []
    return [p.strip() for p in out.replace("|", "\n").splitlines() if p.strip()]


# --- Radyo (WiFi karti ac/kapa) --------------------------------------------
def _general_status() -> dict:
    """NM'in genel durumu — radyo ve internet icin TEK ucuz cagri.

    D-Bus'tan okur, ag trafigi URETMEZ.
    """
    try:
        out = _nmcli(
            "-t", "-f", "STATE,CONNECTIVITY,WIFI-HW,WIFI", "general", "status",
            check=False,
        )
    except RuntimeError:
        return {}
    return parse_general_status(out)


def _radio_state(devices: list[dict], general: dict | None = None) -> dict:
    if general is None:
        general = _general_status()
    return radio_from_general(general, any(d["type"] == "wifi" for d in devices))


def _wired_fallback(devices: list[dict]) -> dict | None:
    """Kabloda ulasilabilir bir yol var mi (WiFi kapatma onkosulu)."""
    rows = []
    for dev in devices:
        if dev["type"] != "ethernet":
            continue
        rows.append({**dev, "addresses": _connection_ipv4_addrs(dev["ifname"])})
    return pick_wired_fallback(rows)


def _any_global_ipv4(devices: list[dict]) -> bool:
    """Herhangi bir arayuzde gercek bir IPv4 var mi (erisim ihtimali)."""
    for dev in devices:
        if dev["type"] in ("loopback", "bridge", "tun", "veth", "ovs-interface"):
            continue
        if dev["ifname"].startswith(("docker", "br-", "veth", "virbr")):
            continue
        if any(is_global_ipv4(a) for a in _connection_ipv4_addrs(dev["ifname"])):
            return True
    return False


# --- Gorev tercihi (mode.json) ---------------------------------------------
def _read_mode() -> dict:
    """Kalici gorev tercihi + radyo/fallback defterleri. Dosya yoksa 'ap'."""
    raw = _read_json(MODE_PATH) or {}
    mode = raw.get("mode")
    if mode not in ("ap", "client"):
        mode = DEFAULT_MODE
    fallback = raw.get("fallback") if isinstance(raw.get("fallback"), dict) else {}
    probe = raw.get("probe") if isinstance(raw.get("probe"), dict) else {}
    desired = raw.get("radio_desired")
    return {
        "schema": SCHEMA_VERSION,
        "mode": mode,
        "set_by": raw.get("set_by"),
        "set_at": raw.get("set_at"),
        "radio_desired": desired if desired in ("on", "off") else None,
        "radio_changed_at": raw.get("radio_changed_at"),
        "radio_auto_restored_at": raw.get("radio_auto_restored_at"),
        # Radyo kapali + hicbir IP yok durumunun basladigi an (kurtarma sayaci).
        "radio_off_since": _as_float(raw.get("radio_off_since")),
        "fallback": {
            "active": bool(fallback.get("active")),
            "since": _as_float(fallback.get("since")),
            "last_attempt": _as_float(fallback.get("last_attempt")) or 0.0,
        },
        "probe": {
            "at": _as_float(probe.get("at")) or 0.0,
            "ok": probe.get("ok") if isinstance(probe.get("ok"), bool) else None,
        },
    }


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _write_mode(state: dict) -> None:
    _write_json(MODE_PATH, {**state, "schema": SCHEMA_VERSION}, mode=0o640)


def _sta_profile_exists() -> bool:
    try:
        out = _nmcli("-t", "-f", "NAME", "connection", "show", check=False)
    except RuntimeError:
        return False
    return any(
        _split_terse(line)[0].strip() == STA_CON_NAME
        for line in out.splitlines()
        if line.strip()
    )


def _sta_profile_ssid() -> str | None:
    """Kayitli client profilinin SSID'i (baglanti kopukken de bilinir)."""
    try:
        out = _nmcli(
            "-g", "802-11-wireless.ssid", "connection", "show", STA_CON_NAME,
            check=False,
        )
    except RuntimeError:
        return None
    value = out.strip()
    return value or None


def _current_ssid(ifname: str) -> str | None:
    """Radyonun SU AN bagli oldugu ag adi (profil adindan bagimsiz)."""
    try:
        out = _nmcli(
            "-t", "-f", "IN-USE,SSID,SIGNAL", "device", "wifi", "list",
            "ifname", ifname, "--rescan", "no",
        )
    except RuntimeError:
        return None
    for line in out.splitlines():
        parts = _split_terse(line)
        if len(parts) >= 2 and parts[0].strip() == "*":
            return parts[1].strip() or None
    return None


def _apply_mode_profiles(mode: str) -> None:
    """Tercihi NetworkManager'in KENDI bayragina yaz.

    NEDEN 30 sn'lik dongu yerine autoconnect: dongu yalnizca biz kosarken
    gecerlidir; NM boot'ta kendi karariyla kayitli aga kacar ve kullanicinin
    acik tercihi SESSIZCE ezilir ("AP modunu sectim ama gelmiyor").

    AP profiline DOKUNULMAZ: onun autoconnect'i (priority 100) kurtarma
    yolunun ta kendisi. Client tercihinde STA'ya daha yuksek oncelik verilir
    ki boot'ta AP'nin onune gecebilsin.
    """
    if not _sta_profile_exists():
        return
    if mode == "ap":
        _nmcli(
            "connection", "modify", STA_CON_NAME, "connection.autoconnect", "no",
            check=False,
        )
        return
    _nmcli(
        "connection", "modify", STA_CON_NAME,
        "connection.autoconnect", "yes",
        "connection.autoconnect-priority", str(STA_AUTOCONNECT_PRIORITY),
        check=False,
    )


def _ap_has_clients(ifname: str) -> bool:
    """AP'ye bagli bir istemci var mi?

    Sahada biri arayuzu kullanirken AP'yi bir dakikaligina dusurup oturumunu
    kesmeyelim diye yeniden baglanma denemesi ertelenir.
    """
    try:
        proc = subprocess.run(
            ["iw", "dev", ifname, "station", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return "Station" in proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    try:
        proc = subprocess.run(
            ["ip", "neigh", "show", "dev", ifname, "nud", "reachable"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return bool(proc.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    # Olculemedi: kullanici ACIKCA client modu sectiyse denemeyi sonsuza dek
    # engellememeliyiz (muhafiz zaten AP'yi geri aciyor).
    return False


def _radio_rescue(devices: list[dict], radio: dict) -> bool:
    """DEGISMEZ: cihaz erisilemez kalmasin.

    Radyo kapaliyken kablo da giderse hicbir yoldan girilemez ve kimse
    radyoyu geri acamaz. Bu durum RADIO_RESCUE_SEC boyunca KESINTISIZ
    surerse WiFi kendiliginden acilir. TEK YONLUDUR: ajan radyoyu asla
    kendiliginden KAPATMAZ.

    True donerse cagiran taraf cihaz listesini tazelemeli.
    """
    state = _read_mode()

    def _clear() -> None:
        if state.get("radio_off_since"):
            state["radio_off_since"] = None
            _write_mode(state)

    if RADIO_RESCUE_SEC <= 0 or not radio["supported"] or radio["enabled"]:
        _clear()
        return False
    if not radio["hardware_enabled"]:
        return False  # yazilimla acilamaz, sayac tutmanin anlami yok
    if _any_global_ipv4(devices):
        _clear()
        return False
    now = time.time()
    since = state.get("radio_off_since")
    if not since:
        state["radio_off_since"] = now
        _write_mode(state)
        return False
    if now - since < RADIO_RESCUE_SEC:
        return False

    _log("Kablolu baglanti yok ve WiFi kapali — erisim kalsin diye WiFi aciliyor.")
    try:
        _nmcli("radio", "wifi", "on")
    except RuntimeError as exc:
        _log(f"WiFi otomatik acilamadi: {exc}")
        return False
    state["radio_off_since"] = None
    # Kullanicinin tercihi (radio_desired="off") KORUNUR — yalan soylemeyip
    # "kablolu gidince otomatik actik" diyebilelim.
    state["radio_auto_restored_at"] = _now_iso()
    _write_mode(state)
    return True


def _enforce_wifi_mode(devices: list[dict], radio: dict) -> None:
    """Kullanicinin gorev tercihini uygula — ISTEK yikici olabilir, MUHAFIZ asla.

    DEGISMEZ KURAL KORUNUR: client baglantisi yoksa AP acilir; yani cihaza
    her zaman bir giris yolu kalir. Onceki surumden farki, kural artik
    kullanicinin tercihini de tanir:

      mode="ap"     -> AP acik tutulur. Radyoda YABANCI bir client baglantisi
                       varsa DUSURULMEZ (kurulumcunun oturumunu kesmeyelim);
                       durum state'e durustce yazilir, kullanici acik dugmeyle
                       AP'ye gecebilir.
      mode="client" -> once NM'in kendi yeniden baglanmasina sans verilir
                       (grace); olmazsa erisim garantisi icin AP acilir
                       (fallback) ve ag geri geldiginde periyodik olarak
                       yeniden denenir.

    `report` her turda (30 sn) cagirir.
    """
    if os.path.exists(GUARD_PATH):
        return  # baglanma denemesi suruyor — _guard_check ilgilenecek
    ifname = _wifi_ifname(devices)
    if ifname is None:
        return  # WiFi karti yok
    if not radio["enabled"]:
        # Radyo kapali: ne AP ne client mumkun. Her 30 sn'de bir AP'yi acmaya
        # calisip hata loglamak ANLAMSIZ — durum state.json'da zaten yaziyor.
        return

    ap = _ap_info(devices)
    if not ap.get("exists"):
        return  # AP profili kurulmamis (SKIP_AP)

    state = _read_mode()

    # DIKKAT: burada _sta_is_online KULLANILMAZ. O fonksiyon profil ADINA
    # bakar ve ilk kurulumda kurulumcunun WiFi profilini goremez; sahada
    # tam olarak bu yuzden AP acilip SSH oturumu koptu ve kurulum yarida
    # kaldi. Ayrintili gerekce icin bkz. _wifi_client_online.
    client_online = _wifi_client_online(ifname, devices)

    if state["mode"] == "ap":
        if client_online:
            return  # OTOMATIK YIKMA YOK; state.foreign_client durumu anlatir
        if ap.get("active"):
            return
        _log("WiFi baglantisi yok ve AP kapali — AP aciliyor (erisim garantisi).")
        try:
            _nmcli("connection", "up", AP_CON_NAME, timeout=NMCLI_WIFI_TIMEOUT_SEC)
        except RuntimeError as exc:
            _log(f"AP acilamadi: {exc}")
        return

    # ---- mode == "client" ----
    fallback = state["fallback"]
    if client_online:
        if fallback["active"] or fallback["since"] is not None:
            state["fallback"] = {"active": False, "since": None, "last_attempt": 0.0}
            _write_mode(state)
        return

    now = time.time()
    if fallback["since"] is None:
        fallback["since"] = now
        _write_mode(state)
        return
    if now - fallback["since"] < CLIENT_GRACE_SEC:
        return  # NM kendi yeniden baglanmasini denesin

    if not ap.get("active"):
        _log("Kayitli aga ulasilamiyor — erisim garantisi icin AP aciliyor.")
        try:
            _nmcli("connection", "up", AP_CON_NAME, timeout=NMCLI_WIFI_TIMEOUT_SEC)
        except RuntimeError as exc:
            _log(f"AP acilamadi: {exc}")
            return
        fallback["active"] = True
        _write_mode(state)
        return

    # AP acik ama kullanicinin tercihi hala "internete baglan": periyodik dene.
    # Tek seferlik fallback, ag bir saat kesilip geri gelirse cihazi sonsuza
    # dek internetsiz birakirdi.
    if now - fallback["last_attempt"] < CLIENT_RETRY_SEC:
        return
    if not _sta_profile_exists():
        return
    if _ap_has_clients(ifname):
        return  # biri AP uzerinden bagli, oturumunu kesme
    fallback["last_attempt"] = now
    _write_mode(state)
    ssid = _sta_profile_ssid() or ""
    _log(f"Kayitli aga yeniden baglanmayi deniyor: {ssid or STA_CON_NAME}")
    # Muhafiz ONCE kurulur: deneme basarisiz olursa AP geri acilir.
    _guard_arm(ifname, ssid)
    try:
        _nmcli(
            "connection", "up", STA_CON_NAME, timeout=NMCLI_RETRY_TIMEOUT_SEC
        )
    except RuntimeError as exc:
        _log(f"yeniden baglanma basarisiz: {exc}")
        _ap_restore("yeniden baglanma denemesi basarisiz")
        _guard_clear()
        return
    if _sta_is_online(ifname):
        _guard_clear()
        _log(f"Kayitli aga yeniden baglanildi: {ssid or STA_CON_NAME}")


# --- Internet durumu (AP acik olmakla AYNI SEY DEGIL) ----------------------
def _route_lookup() -> dict:
    """Varsayilan rota var mi, hangi arayuzden? PAKET GONDERMEZ."""
    try:
        proc = subprocess.run(
            ["ip", "route", "get", ROUTE_PROBE_TARGET],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {"ifname": None, "gateway": None, "src": None}
    if proc.returncode != 0:
        # "Network is unreachable" — varsayilan rota yok.
        return {"ifname": None, "gateway": None, "src": None}
    return parse_ip_route_get(proc.stdout)


def _tcp_probe() -> bool:
    """Tek TCP baglantisi — yeni bagimlilik yok, HTTP govdesi yok."""
    try:
        with socket.create_connection(
            (NET_PROBE_HOST, NET_PROBE_PORT), timeout=NET_PROBE_TIMEOUT_SEC
        ):
            return True
    except OSError:
        return False


def _internet_state(devices: list[dict], general: dict) -> dict:
    """Internetin VAR/YOK + hangi arayuzden oldugunu durustce raporla.

    Kullanici guncelleme / uzaktan bakim / saat senkronunun calisip
    calismayacagini bilmeli; "erisim noktasi acik" bunu SOYLEMEZ.
    """
    route = _route_lookup()
    via = None
    if route.get("ifname"):
        dev = next((d for d in devices if d["ifname"] == route["ifname"]), None)
        via = via_kind(dev["type"] if dev else None)
    connectivity = general.get("connectivity")

    probe_ok: bool | None = None
    if route.get("ifname") and connectivity not in ("full", "portal", "limited", "none"):
        # NM connectivity kontrolu yapilandirilmamis. Kendi kontrolumuzu
        # SEYREK yapariz; rota yoksa hic yapmayiz (internetsiz sahada SIFIR
        # trafik).
        state = _read_mode()
        now = time.time()
        if now - state["probe"]["at"] >= NET_PROBE_SEC:
            probe_ok = _tcp_probe()
            state["probe"] = {"at": now, "ok": probe_ok}
            _write_mode(state)
        else:
            probe_ok = state["probe"]["ok"]

    result = resolve_internet(connectivity, route, via, probe_ok)
    result["checked_at"] = _now_iso()
    return result


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
    """Atomik yaz: once .tmp, sonra rename. Backend yarim dosya okumasin.

    SYMLINK TAKIBI KAPALI — bu bir YETKI SINIRI.
    ---------------------------------------------
    `.tmp` yolu backend container'inin (uid 10001) YAZABILDIGI paylasilan
    dizindedir; bu ajan ise ROOT olarak calisir. Duz `open(tmp, "w")`
    kullanildiginda container oraya onceden bir symlink birakip root'a
    istedigi host dosyasini truncate + uzerine yazdirabiliyordu.

    Somut saldiri: container icinde
        ln -s /etc/systemd/system/e1-rad-report.service <state_dir>/state.json.tmp
    30 saniye icinde timer root olarak kosar, symlink'i TAKIP eder ve unit
    dosyasini JSON ile ezer. Sonraki boot'ta sure-dolunca-kapatma zorlayicisi
    OLU olur — yani ozelligin tek guvenlik garantisi sessizce devre disi kalir.
    Ayni primitif /etc/shadow, /etc/sudoers.d/* ya da /etc/cron.d icin de
    kullanilabilir; container'daki cap_drop/no-new-privileges/read_only
    sertlestirmesinin TAMAMI bu tek satirdan asiliyordu. `os.replace` sonrasi
    symlink kayboldugu icin iz de birakmiyordu.

    Koruma uc katmanli:
      * O_NOFOLLOW : son bilesen symlink ise acmayi REDDEDER
      * O_EXCL     : dosya zaten varsa reddeder (onceden konmus tuzak)
      * fchmod/fchown: izinler YOLA degil ACIK FD'ye uygulanir; acma ile
        izin verme arasindaki yaris penceresi kapanir
    Bayat bir .tmp kalmissa once silinir — symlink'i silmek hedefe DOKUNMAZ.
    """
    tmp = f"{path}.tmp"
    # Bayat/tuzak .tmp temizligi. Symlink'i unlink etmek yalnizca link'i siler.
    try:
        os.unlink(tmp)
    except (FileNotFoundError, OSError):
        pass
    # O_NOFOLLOW Windows'ta yok; gelistirici makinesinde de import edilebilsin.
    _NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
        # Izinler ACIK FD uzerinden — yol uzerinden degil (TOCTOU kapali).
        try:
            os.fchmod(fh.fileno(), mode)
            st = os.stat(STATE_DIR)
            os.fchown(fh.fileno(), 0, st.st_gid)
        except (AttributeError, PermissionError, OSError):
            pass
    os.replace(tmp, path)


def _read_json(path: str) -> dict | None:
    """Symlink TAKIP ETMEDEN okur.

    Yazma tarafi korundu ama okuma da onemli: container paylasilan dizine
    `/etc/shadow`a isaret eden bir symlink birakirsa, root ajan onu okuyup
    icerigini durum/rapor dosyasina (container'in OKUYABILDIGI) yansitabilir
    — yani dosya SIZDIRMA primitifi olurdu.
    """
    try:
        _NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
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
    general = _general_status()
    radio = _radio_state(devices, general)
    ap = _ap_info(devices)
    return {
        "schema": SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "hostname": host,
        "mdns_name": f"{host}.local",
        "ap": ap,
        "wifi": _wifi_state(devices),
        # Uc DURUST katman: fiziksel onkosul / gorev / internet.
        # Hicbiri digerinin yerine gecmez ve OLCMEDIGIMIZ durum gosterilmez.
        "radio": _radio_block(radio),
        "wifi_role": _wifi_role_state(devices, radio, ap),
        "internet": _internet_state(devices, general),
        "interfaces": interfaces,
        "eth_connection": ETH_CON_NAME,
    }


def _radio_block(radio: dict) -> dict:
    """Olculen radyo durumu + kullanicinin son acik karari."""
    state = _read_mode()
    return {
        **radio,
        # "desired" = kullanicinin arayuzden verdigi son karar; None = hic
        # dokunulmamis. Ajan bunu SUREKLI DAYATMAZ (yerel yoneticiyle
        # kavga etmesin) — tek otomatik degisim _radio_rescue'dur.
        "desired": state["radio_desired"],
        "changed_at": state["radio_changed_at"],
        "auto_restored_at": state["radio_auto_restored_at"],
    }


def _wifi_role_state(devices: list[dict], radio: dict, ap: dict) -> dict:
    """WiFi kartinin gorevi: TERCIH (mode) ve OLCUM (effective) AYRI alanlarda.

    Ikisini tek rozete baglamak sahadaki celiskiyi uretti: panel "AP acik"
    yaziyordu ama bu bir olcum degil, "client yoksa AP olmali" kuralinin
    kendisiydi. Ayrisiyorlarsa (mode=client, effective=ap) bu bir hata
    degil, ANLATILACAK bir durumdur.
    """
    state = _read_mode()
    ifname = _wifi_ifname(devices)
    client_online = bool(ifname) and _wifi_client_online(ifname, devices)
    foreign: str | None = None
    if client_online and ifname:
        dev = next((d for d in devices if d["ifname"] == ifname), None)
        con = (dev.get("connection") or "").strip() if dev else ""
        if con and con != STA_CON_NAME:
            # Baskasinin kurdugu baglanti (kurulumcunun kendi profili olabilir).
            foreign = _current_ssid(ifname) or con
    fallback = state["fallback"]
    next_retry = None
    if state["mode"] == "client" and fallback["active"]:
        next_retry = (fallback["last_attempt"] or 0.0) + CLIENT_RETRY_SEC
    return {
        "mode": state["mode"],
        "effective": derive_effective(radio["enabled"], bool(ap.get("active")), client_online),
        "since": state["set_at"],
        "set_by": state["set_by"],
        "fallback_active": fallback["active"],
        "fallback_since": fallback["since"],
        "next_retry_at": next_retry,
        "foreign_client": foreign,
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

    info["saved"] = _sta_profile_exists()

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
    if info["ssid"] is None and info["saved"]:
        # Baglanti kopuk: hangi agin kayitli oldugunu yine de soyle.
        info["ssid"] = _sta_profile_ssid()

    guard = _read_json(GUARD_PATH)
    if guard:
        info["guard_active"] = True
        info["guard_deadline"] = guard.get("deadline")
        if not info["ssid"]:
            info["ssid"] = guard.get("ssid")
    return info


def _maintain() -> None:
    """Her report turunda (30 sn) kosan bakim: muhafiz + radyo kurtarma + gorev.

    Ayri systemd unit'i gerektirmesin diye bilerek report'a baglandi. Radyo
    BOLUNEMEZ tek bir kaynak; ikinci bir surec ayni karti yonetirse
    setup-appliance.sh'ta adiyla uyarilan yaris (bir taraf AP'yi bekletirken
    digeri acar) geri gelir.
    """
    _guard_check()
    devices = _device_rows()
    radio = _radio_state(devices)
    if _radio_rescue(devices, radio):
        devices = _device_rows()
        radio = _radio_state(devices)
    _enforce_wifi_mode(devices, radio)


def cmd_report() -> int:
    try:
        _maintain()
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
    if action in ("wifi_scan", "wifi_connect") and not _radio_state(devices)["enabled"]:
        # Radyo kapaliyken tarama BOS doner, baglanti kurulamaz. Sahadaki
        # "gorunur ag bulunamadi" sikayetinin sebebi tam olarak buydu; sessiz
        # bos sonuc yerine gercek sebebi soyle.
        # (wifi_forget bu kontrole GIRMEZ: profil silmek radyoya bagli degil.)
        return write_result(False, "WiFi karti kapali; once WiFi'yi acin.")

    # ---- Tarama: sadece okur (derin tarama disinda, bkz. asagisi) ----
    if action == "wifi_scan":
        # DERIN TARAMA: tek radyo AP yayindayken kanal degistiremez; cogu
        # surucude tarama yalnizca AP kanalini ya da hic bir sey dondurur.
        # Kullanici ACIKCA isterse AP kisa sureligine indirilir.
        ap_active = bool(_ap_info(devices).get("active"))
        deep = bool(raw.get("deep")) and ap_active
        if deep:
            _log("Derin tarama: AP gecici olarak indiriliyor.")
            _nmcli("connection", "down", AP_CON_NAME, check=False)
        try:
            networks = _scan_networks(ifname)
        except RuntimeError as exc:
            return write_result(False, f"Tarama basarisiz: {exc}")
        finally:
            # `finally` return'den ONCE kosar: AP her durumda geri gelir.
            if deep:
                # Muhafiz KURULMAZ: ajan tarama sirasinda olurse bir sonraki
                # report turu (_enforce_wifi_mode, mode="ap") AP'yi en gec
                # 30 sn icinde geri acar.
                try:
                    _nmcli("connection", "up", AP_CON_NAME, timeout=NMCLI_WIFI_TIMEOUT_SEC)
                except RuntimeError as exc:
                    _log(f"derin tarama sonrasi AP geri acilamadi: {exc}")
        _write_json(
            SCAN_PATH,
            {
                "schema": SCHEMA_VERSION,
                "updated_at": _now_iso(),
                "ifname": ifname,
                "deep": deep,
                # AP yayindayken tarama sinirli sonuc verebilir — UI bunu
                # soylesin, "hic ag yok" diye YALAN soylemesin.
                "ap_was_active": ap_active and not deep,
                "networks": networks,
            },
        )
        _log(f"WiFi tarama: {len(networks)} ag bulundu ({ifname}, derin={deep})")
        return write_result(
            True, None,
            applied={"action": "wifi_scan", "count": len(networks), "deep": deep},
        )

    # ---- Kayitli agi unut: STA profilini sil, AP'yi geri ac ----
    if action == "wifi_forget":
        try:
            _nmcli("connection", "delete", STA_CON_NAME, check=False)
        except RuntimeError as exc:
            _log(f"STA profili silinemedi: {exc}")
        _guard_clear()
        _set_mode("ap", None)
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

    # Kullanicinin niyeti acik: bir aga baglandiysa gorev artik "client".
    # autoconnect da burada acilir, aksi halde reboot'ta AP geri alir.
    _set_mode("client", str(raw.get("requested_by") or "") or None)

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


def _set_mode(mode: str, actor: str | None) -> None:
    """Kalici gorev tercihini yaz + NM autoconnect bayragini hizala."""
    state = _read_mode()
    state["mode"] = mode
    state["set_at"] = _now_iso()
    if actor:
        state["set_by"] = actor
    state["fallback"] = {"active": False, "since": None, "last_attempt": 0.0}
    _write_mode(state)
    try:
        _apply_mode_profiles(mode)
    except RuntimeError as exc:
        _log(f"autoconnect bayragi ayarlanamadi: {exc}")


def _handle_wifi_radio(raw: dict, devices: list[dict], write_result) -> int:
    """WiFi kartini ac/kapa.

    ILKE: ISTEK (kullanici acikca istedi) yikici olabilir, MUHAFIZ (otomatik)
    asla. Kapatma mesrudur (trafo merkezinde "telsiz yayin yok" politikasi)
    ama tek radyo kapaninca AP de duser; bu yuzden KOSULLUDUR: cihaza
    ulasilacak ikinci bir yol (IP almis bagli ethernet) KANITLANMALI.
    """
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        return write_result(False, "Gecersiz istek: WiFi durumu true/false olmali.")

    radio = _radio_state(devices)
    if not radio["supported"]:
        return write_result(False, "Bu cihazda WiFi karti bulunamadi.")
    if enabled and not radio["hardware_enabled"]:
        return write_result(
            False, "WiFi acilamadi: cihaz uzerindeki WiFi anahtari kapali."
        )
    if not enabled:
        wired = _wired_fallback(devices)
        if wired is None:
            return write_result(
                False,
                "WiFi kapatilirsa cihaza ulasilacak baska yol kalmiyor. "
                "Once kablolu baglantiyi kurun.",
            )

    try:
        _nmcli("radio", "wifi", "on" if enabled else "off")
    except RuntimeError as exc:
        return write_result(False, f"WiFi durumu degistirilemedi: {exc}")

    # DOGRULAMA ZORUNLU: `nmcli radio wifi on` DONANIM kilidi varken de
    # basari (exit 0) doner. Dogrulamazsak UI "actim" der ama hicbir sey
    # acilmaz — duzeltmeye calistigimiz yalanin aynisini uretmis oluruz.
    final = radio
    deadline = time.time() + RADIO_VERIFY_SEC
    while True:
        final = _radio_state(_device_rows())
        if final["enabled"] == enabled or time.time() >= deadline:
            break
        time.sleep(1)
    if final["enabled"] != enabled:
        if enabled and not final["hardware_enabled"]:
            return write_result(
                False,
                "WiFi acilamadi: cihaz uzerindeki WiFi anahtari kapali. "
                "Anahtari acip tekrar deneyin.",
            )
        return write_result(
            False, "WiFi durumu degistirilemedi (surucu istegi kabul etmedi)."
        )

    state = _read_mode()
    state["radio_desired"] = "on" if enabled else "off"
    state["radio_changed_at"] = _now_iso()
    state["radio_off_since"] = None
    if enabled:
        state["radio_auto_restored_at"] = None
    _write_mode(state)
    _log(f"WiFi karti {'acildi' if enabled else 'kapatildi'}.")
    # _write_result -> cmd_report -> _enforce_wifi_mode: radyo acildiysa AP
    # 30 sn beklemeden hemen geri gelir.
    return write_result(True, None, applied={"action": "wifi_radio", "enabled": enabled})


def _handle_wifi_mode(raw: dict, devices: list[dict], write_result) -> int:
    """WiFi kartinin gorevini sec: kendi agini yayinla (ap) | bir aga katil (client).

    Otomatik dongu bir client baglantisini ASLA dusurmez; ama bu ACIK bir
    istektir ve kullanici arayuzde uyarilmistir.
    """
    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in ("ap", "client"):
        return write_result(False, "Gecersiz gorev secimi (ap|client).")

    ifname = _wifi_ifname(devices)
    if ifname is None:
        return write_result(False, "WiFi arayuzu bulunamadi (kart takili mi?).")
    radio = _radio_state(devices)
    if not radio["enabled"]:
        return write_result(False, "WiFi karti kapali; once WiFi'yi acin.")
    if mode == "client" and not _sta_profile_exists():
        return write_result(
            False, "Kayitli WiFi agi yok; once listeden bir ag secip baglanin."
        )
    if mode == "ap" and not _ap_info(devices).get("exists"):
        return write_result(False, "Erisim noktasi profili kurulu degil.")

    actor = str(raw.get("requested_by") or "") or None
    _set_mode(mode, actor)

    if mode == "ap":
        # SIRA KRITIK: once AP'yi KALDIR, client'i ONDAN SONRA dusur.
        #
        # Eskiden tam tersiydi: client dusuruluyor, autoconnect kapatiliyor,
        # sonra AP DENENIYORDU. `up AP` basarisiz olursa (kart AP modunu
        # desteklemiyor, kanal yasak, surucu hatasi) cihazin CALISAN TEK
        # kablosuz yolu kapatilmis, yerine hicbir sey gelmemis oluyordu;
        # ustelik autoconnect "no" kaldigi icin reboot da kurtarmiyordu.
        # `_enforce_wifi_mode` her 30 sn ayni basarisiz komutu tekrarlayip
        # STA'yi asla geri getirmiyordu.
        #
        # Tek radyoda AP'yi kaldirmak client'i zaten dusurur; ayrica `down`
        # cagirmaya gerek yok. AP kalkmazsa client'a DOKUNMAMIS oluruz ve
        # asagida tercihi geri alip cihazi oldugu gibi birakiriz.
        _guard_clear()
        try:
            _nmcli("connection", "up", AP_CON_NAME, timeout=NMCLI_WIFI_TIMEOUT_SEC)
        except RuntimeError as exc:
            # GERI ALMA — client dalindaki davranisla SIMETRIK.
            _set_mode("client", actor)          # autoconnect'i geri ac
            try:
                _nmcli("connection", "up", STA_CON_NAME,
                       timeout=NMCLI_WIFI_TIMEOUT_SEC, check=False)
            except RuntimeError:
                pass
            return write_result(False, f"Erisim noktasi acilamadi: {exc}")
        # AP ayakta: tek radyoda client zaten dustu, profili de kapat ki
        # NetworkManager autoconnect ile geri baglanmaya calismasin.
        _nmcli("connection", "down", STA_CON_NAME, check=False)
        _log("Gorev: erisim noktasi (kendi agini yayinla).")
        return write_result(True, None, applied={"action": "wifi_mode", "mode": mode})

    # ---- client ----
    ssid = _sta_profile_ssid() or ""
    # Muhafiz ONCE kurulur: baglanti kurulamazsa AP geri acilir, cihaz
    # erisilemez kalmaz.
    _guard_arm(ifname, ssid)
    try:
        _nmcli("connection", "up", STA_CON_NAME, timeout=NMCLI_WIFI_TIMEOUT_SEC)
    except RuntimeError as exc:
        _ap_restore("gorev degisimi basarisiz")
        _guard_clear()
        # Tercih "ap"a geri alinir: kullanici yalan bir durum gormesin.
        _set_mode("ap", actor)
        return write_result(False, f"Kayitli aga baglanilamadi: {exc}")
    online = _sta_is_online(ifname)
    if online:
        _guard_clear()
    _log(f"Gorev: internete baglan ({ssid or STA_CON_NAME}), baglandi={online}")
    return write_result(
        True, None,
        applied={
            "action": "wifi_mode",
            "mode": mode,
            "ssid": ssid or None,
            "online": online,
            "guard_seconds": None if online else WIFI_GUARD_SEC,
        },
    )


def _handle_net_check(devices: list[dict], write_result) -> int:
    """Internet durumunu SIMDI sina (kullanici istegi).

    `report` turunda ASLA calistirilmaz — bu cagri ag trafigi uretir ve
    bloklar.
    """
    try:
        _nmcli("networking", "connectivity", "check", timeout=10)
    except RuntimeError as exc:
        _log(f"internet kontrolu tamamlanamadi: {exc}")
    result = _internet_state(devices, _general_status())
    return write_result(True, None, applied={"action": "net_check", **result})


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
    if action == "wifi_radio":
        return _handle_wifi_radio(raw, devices, _write_result)
    if action == "wifi_mode":
        return _handle_wifi_mode(raw, devices, _write_result)
    if action == "net_check":
        return _handle_net_check(devices, _write_result)
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
