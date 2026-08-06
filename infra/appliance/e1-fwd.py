#!/usr/bin/env python3
"""e1-fwd — EnerjiOne Grid guvenlik duvari ajani (host tarafi, root).

NE ISE YARAR
------------
Kullanicinin arayuzden yonettigi host guvenlik duvarini uygular: acma/kapama,
izin/engel kurallari ve port yonlendirmeleri. docker-compose'un yayinladigi
TUM portlar 0.0.0.0'a bind oldugu icin (bkz. e1-ap-firewall.sh docstring'i:
21 FTP, 502 Modbus, 2404-2406 IEC104, 4222 NATS, 5672 RabbitMQ, 30000-30009
FTP pasif) varsayilan kurulumda kimlik dogrulamasiz SCADA protokolleri tum
aga aciktir. Bu ajan o yuzeyi kullanicinin sectigi kurallara indirger.

NEDEN ufw DEGIL, iptables
-------------------------
Container portlari FORWARD yolundan gecer (DNAT sonrasi) ve ufw'nin INPUT
kurallari bu trafigi HIC GORMEZ — ufw ile "502'yi kapat" demek Modbus'u
kapatmaz, sessizce hicbir sey yapmaz. Docker'in kendi kurallarindan ONCE
gezdigi ve ASLA temizlemedigi tek zincir DOCKER-USER'dir; kurallar oraya
takilir (e1-ap-firewall.sh ile ayni gerekce ve ayni iki tuzak):
  * FORWARD'a gelen paket ZATEN DNAT'lanmistir; `--dport` container portunu
    gosterir (8080), yayinlanan host portunu (80) DEGIL. Orijinal hedef port
    `--ctorigdstport` ile eslestirilir.
  * DOCKER-USER'daki kurallar yalnizca hedefi APPLIANCE olan trafige
    uygulanmali (`--ctstate DNAT`); aksi halde AP istemcilerinin internet
    NAT'i da kesilirdi.

KILITLENME KORUMASI (guardrail) — PAZARLIKSIZ
---------------------------------------------
Su portlar HER ZAMAN acik kalir ve kullanici kurali bunlari EZEMEZ:
    TCP 22 (SSH), TCP 80/443 (web arayuzu), UDP 41641 (uzaktan bakim tuneli)
Ayrica loopback, ICMP, kurulmus baglantilar ve AP'nin calismasi icin sart
olan DHCP/DNS/mDNS acik kalir. Gerekce: guvenlik duvarini yanlis yapilandiran
kullanici cihaza erisimi tamamen kaybederse, duzeltecek yol da kalmaz.
Acil durumda konsoldan: sudo /opt/enerjione-grid/infra/appliance/e1-fwd.py disable

MIMARI (e1-netd / e1-rad ile ayni desen)
----------------------------------------
    backend (container)  --yazar-->  /var/lib/e1-grid/fw/request.json
    e1-fwd  (host, root) --okur -->  dogrular -> iptables ile uygular

Backend iptables'i CALISTIRMAZ. Tek yetkisi bir JSON dosyasi yazmaktir; ajan
istegi kendi kurallariyla bagimsiz dogrular.

IKI DIZIN — NEDEN (e1-rad ile ayni gerekce)
-------------------------------------------
    /var/lib/e1-grid/fw       root:10001 0770  (paylasilan IPC)
        request.json  state.json  status.json
    /var/lib/e1-grid/fw-priv  root:root  0700  (yalniz ajan)
        config.json  archive/
Yetkili yapilandirma (config.json) paylasilan dizinde DURAMAZ: 0770 dizinde
yazma izni olan backend root'a ait dosyalari da unlink/rename edebilir, yani
kendine kural uydurabilirdi.

ZINCIRLER
---------
    filter/E1-FW-IN      -> INPUT'a takili (host uzerinde dinleyen servisler)
    filter/E1-FW-DOCKER  -> DOCKER-USER'a takili (container'a giden trafik)
    nat/E1-FW-DNAT       -> PREROUTING (port yonlendirme)
    nat/E1-FW-MASQ       -> POSTROUTING (yonlendirme donus yolu)
Kapali durumda zincirler BOSALTILIR ama kancalar yerinde kalir (bos zincir
no-op'tur; her acmada kanca kurmakla ugrasilmaz).

DURUMU OLCUYORUZ, VARSAYMIYORUZ (e1-rad felsefesi)
--------------------------------------------------
Her zincirin basina hic eslesemeyen bir "imza kurali" konur (kaynak
255.255.255.255, comment: e1-fw:<parmak-izi>). report turu bu imzayi
`iptables -S` ile OKUR; istenen yapilandirmanin parmak iziyle uyusmuyorsa
(reboot sonrasi bos tablo, elle silinmis kural, docker restart...) tum
zincirler yeniden kurulur. Kalicilik icin ayri bir iptables-persistent
bagimliligi YOKTUR — 60 saniyelik report timer'i tek yakinsama noktasidir.

IPv6
----
docker-proxy yayinlanan portlari IPv6'da da dinler (userland proxy). O yol
INPUT'tan gectigi icin ip6tables'ta yalnizca E1-FW-IN zinciri kurulur: ayni
port kurallari uygulanir, IPv4 kaynak (CIDR) iceren kurallar atlanir. NDP
(ipv6-icmp) ve DHCPv6 istemci portu acik kalir yoksa ag coker. ip6tables
yoksa state.json'da ipv6:false gorunur.

Kullanim (systemd tarafindan cagrilir):
    e1-fwd.py report    -> durumu olc + gerekiyorsa yeniden uygula (60 sn)
    e1-fwd.py apply     -> request.json'i isle (path unit)
    e1-fwd.py disable   -> acil elle kapatma (konsoldan kurtarma yolu)
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# --- Sabitler ---------------------------------------------------------------
STATE_DIR = os.environ.get("E1_FWD_STATE_DIR", "/var/lib/e1-grid/fw")
REQUEST_PATH = os.path.join(STATE_DIR, "request.json")
STATE_PATH = os.path.join(STATE_DIR, "state.json")
STATUS_PATH = os.path.join(STATE_DIR, "status.json")

# Yalniz ajanin gordugu dizin — backend buraya ERISEMEZ (mount edilmez).
PRIV_DIR = os.environ.get("E1_FWD_PRIV_DIR", "/var/lib/e1-grid/fw-priv")
CONFIG_PATH = os.path.join(PRIV_DIR, "config.json")
ARCHIVE_DIR = os.path.join(PRIV_DIR, "archive")
ARCHIVE_KEEP = 50

SCHEMA_VERSION = 1
IPT_TIMEOUT_SEC = 10

# Sinirlar AJANDA sabit: backend ne gonderirse gondersin asilamaz.
MAX_RULES = 50
MAX_FORWARDS = 20
COMMENT_LIMIT = 80

# Kilitlenme korumasi: bu portlar HER ZAMAN acik, kural EZEMEZ (bkz. docstring).
GUARD_TCP = (22, 80, 443)
GUARD_UDP = (41641,)  # uzaktan bakim (WireGuard/Tailscale) — kurtarma yolu

# Port yonlendirme bu portlari DINLEYEMEZ: guard portlari + compose'un
# yayinladigi portlar (yonlendirme Docker'in DNAT'indan ONCE calisir ve
# mevcut servisi sessizce golgelerdi).
RESERVED_LISTEN = frozenset(
    {21, 22, 80, 443, 502, 2404, 2405, 2406, 4222, 5672, 5020, 5021}
    | set(range(30000, 30010))
)

CHAIN_IN = "E1-FW-IN"
CHAIN_FWD = "E1-FW-DOCKER"
CHAIN_DNAT = "E1-FW-DNAT"
CHAIN_MASQ = "E1-FW-MASQ"

# Imza kurali hic eslesmemeli: 255.255.255.255 kaynak adresi gecersizdir
# (yayin adresi kaynak olamaz); v6'da multicast kaynak ayni sekilde gecersiz.
_MARKER_SRC_V4 = "255.255.255.255/32"
_MARKER_SRC_V6 = "ff00::/8"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    """journalctl -u e1-fwd icin stdout'a yaz."""
    print(f"[e1-fwd] {msg}", flush=True)


class RequestError(Exception):
    """Gecersiz istek — uygulanmadan reddedilir."""


# --- Dosya yazma (e1-rad `_write_json` ile ayni sozlesme) -------------------
def _write_json(path: str, payload: dict, mode: int = 0o640,
                group_from: str | None = STATE_DIR) -> None:
    """Atomik yaz (tmp + rename). Symlink TAKIBI KAPALI — yetki siniri.

    Paylasilan dizine backend (uid 10001) yazabilir; bu ajan root calisir.
    O_NOFOLLOW + O_EXCL + fchmod/fchown uclusunun gerekcesi e1-rad.py
    `_write_json` docstring'inde ayrintili anlatiliyor (symlink ile root'a
    istenen host dosyasini ezdirme saldirisi); ayni sozlesme burada da
    aynen gecerli.
    """
    tmp = f"{path}.tmp"
    try:
        os.unlink(tmp)
    except (FileNotFoundError, OSError):
        pass
    _NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
        try:
            os.fchmod(fh.fileno(), mode)
        except (AttributeError, PermissionError, OSError):
            pass
        if group_from:
            try:
                st = os.stat(group_from)
                os.fchown(fh.fileno(), 0, st.st_gid)
            except (AttributeError, PermissionError, OSError):
                pass
    os.replace(tmp, path)


def _read_json(path: str) -> dict | None:
    """Symlink TAKIP ETMEDEN okur (e1-rad ile ayni: sizdirma primitifi olmasin)."""
    try:
        _NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
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
    """Islenmis istek kaydini arsivle (denetim + tekrar tetiklenmeme)."""
    try:
        os.makedirs(ARCHIVE_DIR, mode=0o700, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        _write_json(
            os.path.join(ARCHIVE_DIR, f"{stamp}-{name}.json"),
            payload, mode=0o600, group_from=None,
        )
        entries = sorted(os.listdir(ARCHIVE_DIR))
        for old in entries[:-ARCHIVE_KEEP]:
            _remove(os.path.join(ARCHIVE_DIR, old))
    except OSError as exc:
        _log(f"arsivleme basarisiz: {exc}")


# --- iptables yardimcilari --------------------------------------------------
def _run(binary: str, *args: str, check: bool = True) -> tuple[int, str]:
    """iptables/ip6tables calistir. (returncode, stdout) dondur.

    SOZLESME: kullanici degerleri (port, CIDR) once _validate_config'ten
    gecer ve argv ELEMANI olarak verilir — kabuk yok, enjeksiyon yuzeyi yok.
    """
    cmd = [binary, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=IPT_TIMEOUT_SEC
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{binary} bulunamadi.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{binary} zaman asimi: {' '.join(cmd)}") from exc
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{binary} hatasi ({proc.returncode}): {err[:300]}")
    return proc.returncode, proc.stdout


def _has_iptables() -> bool:
    return shutil.which("iptables") is not None


def _has_ip6tables() -> bool:
    return shutil.which("ip6tables") is not None


def _chain_exists(binary: str, table: str, chain: str) -> bool:
    rc, _ = _run(binary, "-t", table, "-n", "-L", chain, check=False)
    return rc == 0


def _ensure_chain(binary: str, table: str, chain: str) -> None:
    """Zincir varsa BOSALT, yoksa YARAT (e1-ap-firewall `_zincir_hazirla`)."""
    if _chain_exists(binary, table, chain):
        _run(binary, "-t", table, "-F", chain)
    else:
        _run(binary, "-t", table, "-N", chain)


def _ensure_hook(binary: str, table: str, parent: str, chain: str) -> None:
    """Ust zincire jump'i idempotent tak (once -C ile var mi bak)."""
    rc, _ = _run(binary, "-t", table, "-C", parent, "-j", chain, check=False)
    if rc != 0:
        _run(binary, "-t", table, "-I", parent, "1", "-j", chain)


def _flush_if_exists(binary: str, table: str, chain: str) -> None:
    if _chain_exists(binary, table, chain):
        _run(binary, "-t", table, "-F", chain)


def _ensure_docker_user(binary: str) -> None:
    """DOCKER-USER Docker tarafindan yaratilir; Docker henuz yoksa olustur
    ki kural kaybolmasin (e1-ap-firewall ile ayni)."""
    if not _chain_exists(binary, "filter", "DOCKER-USER"):
        _run(binary, "-N", "DOCKER-USER")
        rc, _ = _run(binary, "-C", "FORWARD", "-j", "DOCKER-USER", check=False)
        if rc != 0:
            _run(binary, "-I", "FORWARD", "1", "-j", "DOCKER-USER")


def _fingerprint(config: dict) -> str:
    """Yapilandirmanin kararli parmak izi — imza kuralinda tasinir."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _marker_args(fp: str, v6: bool = False) -> list[str]:
    src = _MARKER_SRC_V6 if v6 else _MARKER_SRC_V4
    return ["-s", src, "-m", "comment", "--comment", f"e1-fw:{fp}", "-j", "RETURN"]


def _read_marker(binary: str, chain: str) -> str | None:
    """Zincirdeki imza kuralinin parmak izini oku; yoksa None."""
    rc, out = _run(binary, "-S", chain, check=False)
    if rc != 0:
        return None
    for line in out.splitlines():
        if "e1-fw:" in line:
            # ... --comment e1-fw:abc123 ... (iptables tirnaklayabilir)
            token = line.split("e1-fw:", 1)[1]
            return token.split('"')[0].split()[0].strip()
    return None


# --- Dogrulama --------------------------------------------------------------
def _clean_text(value, limit: int) -> str | None:
    if value is None:
        return None
    text = "".join(ch for ch in str(value) if ch.isprintable())
    text = text.strip()[:limit]
    return text or None


def _parse_ports(value) -> tuple[int, int]:
    """"2404" veya "2404-2406" -> (alt, ust). Gecersizse RequestError."""
    text = str(value or "").strip()
    parts = text.split("-", 1)
    try:
        lo = int(parts[0])
        hi = int(parts[1]) if len(parts) == 2 else lo
    except (ValueError, IndexError) as exc:
        raise RequestError(f"Gecersiz port: {text!r}") from exc
    if not (1 <= lo <= 65535 and 1 <= hi <= 65535 and lo <= hi):
        raise RequestError(f"Port araligi 1-65535 olmali: {text!r}")
    return lo, hi


_RULE_KEYS = frozenset({"action", "proto", "ports", "source", "comment"})
_FORWARD_KEYS = frozenset({"proto", "listen_port", "dest_ip", "dest_port", "comment"})
_CONFIG_KEYS = frozenset({"enabled", "rules", "forwards"})


def _validate_rule(raw: dict, index: int) -> dict:
    unknown = sorted(set(raw.keys()) - _RULE_KEYS)
    if unknown:
        raise RequestError(f"kural {index}: bilinmeyen alan(lar): {', '.join(unknown)}")
    action = str(raw.get("action") or "").strip().lower()
    if action not in ("allow", "deny"):
        raise RequestError(f"kural {index}: action allow|deny olmali.")
    proto = str(raw.get("proto") or "").strip().lower()
    if proto not in ("tcp", "udp"):
        raise RequestError(f"kural {index}: proto tcp|udp olmali.")
    lo, hi = _parse_ports(raw.get("ports"))
    source = None
    if raw.get("source"):
        try:
            net = ipaddress.ip_network(str(raw["source"]).strip(), strict=False)
        except ValueError as exc:
            raise RequestError(f"kural {index}: gecersiz kaynak agi.") from exc
        if net.version != 4:
            raise RequestError(f"kural {index}: kaynak yalnizca IPv4 olabilir.")
        source = str(net)
    return {
        "action": action,
        "proto": proto,
        "ports": str(lo) if lo == hi else f"{lo}-{hi}",
        "source": source,
        "comment": _clean_text(raw.get("comment"), COMMENT_LIMIT),
    }


def _validate_forward(raw: dict, index: int) -> dict:
    unknown = sorted(set(raw.keys()) - _FORWARD_KEYS)
    if unknown:
        raise RequestError(
            f"yonlendirme {index}: bilinmeyen alan(lar): {', '.join(unknown)}"
        )
    proto = str(raw.get("proto") or "").strip().lower()
    if proto not in ("tcp", "udp"):
        raise RequestError(f"yonlendirme {index}: proto tcp|udp olmali.")
    listen = raw.get("listen_port")
    if isinstance(listen, bool) or not isinstance(listen, int):
        raise RequestError(f"yonlendirme {index}: listen_port tamsayi olmali.")
    if not 1 <= listen <= 65535:
        raise RequestError(f"yonlendirme {index}: listen_port 1-65535 olmali.")
    if listen in RESERVED_LISTEN:
        # Mevcut servisi (web/SSH/SCADA/FTP...) sessizce golgelemek en sinsi
        # ariza sinifi olurdu; acikca reddediyoruz.
        raise RequestError(
            f"yonlendirme {index}: {listen} portu sistem tarafindan kullaniliyor."
        )
    dest_port = raw.get("dest_port")
    if isinstance(dest_port, bool) or not isinstance(dest_port, int):
        raise RequestError(f"yonlendirme {index}: dest_port tamsayi olmali.")
    if not 1 <= dest_port <= 65535:
        raise RequestError(f"yonlendirme {index}: dest_port 1-65535 olmali.")
    try:
        dest = ipaddress.ip_address(str(raw.get("dest_ip") or "").strip())
    except ValueError as exc:
        raise RequestError(f"yonlendirme {index}: gecersiz hedef IP.") from exc
    if dest.version != 4 or dest.is_loopback or dest.is_unspecified or dest.is_multicast:
        raise RequestError(f"yonlendirme {index}: hedef IP gecersiz.")
    return {
        "proto": proto,
        "listen_port": listen,
        "dest_ip": str(dest),
        "dest_port": dest_port,
        "comment": _clean_text(raw.get("comment"), COMMENT_LIMIT),
    }


def _validate_config(raw: dict) -> dict:
    """Istenen yapilandirmayi bagimsiz dogrula. Backend'e KORUKORUNE guvenilmez."""
    if not isinstance(raw, dict):
        raise RequestError("config bir nesne olmali.")
    unknown = sorted(set(raw.keys()) - _CONFIG_KEYS)
    if unknown:
        raise RequestError(f"config: bilinmeyen alan(lar): {', '.join(unknown)}")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise RequestError("enabled true/false olmali.")
    rules_raw = raw.get("rules") or []
    forwards_raw = raw.get("forwards") or []
    if not isinstance(rules_raw, list) or not isinstance(forwards_raw, list):
        raise RequestError("rules/forwards liste olmali.")
    if len(rules_raw) > MAX_RULES:
        raise RequestError(f"en fazla {MAX_RULES} kural tanimlanabilir.")
    if len(forwards_raw) > MAX_FORWARDS:
        raise RequestError(f"en fazla {MAX_FORWARDS} yonlendirme tanimlanabilir.")
    rules = [
        _validate_rule(item if isinstance(item, dict) else {}, i + 1)
        for i, item in enumerate(rules_raw)
    ]
    forwards = [
        _validate_forward(item if isinstance(item, dict) else {}, i + 1)
        for i, item in enumerate(forwards_raw)
    ]
    seen: set[tuple[str, int]] = set()
    for fwd in forwards:
        key = (fwd["proto"], fwd["listen_port"])
        if key in seen:
            raise RequestError(
                f"ayni portu dinleyen iki yonlendirme var: {fwd['listen_port']}/{fwd['proto']}"
            )
        seen.add(key)
    return {"enabled": enabled, "rules": rules, "forwards": forwards}


# --- Uygulama (iptables kurulumu) -------------------------------------------
def _ports_colon(ports: str) -> str:
    """"2404-2406" -> "2404:2406" (iptables aralik ayraci)."""
    return ports.replace("-", ":")


def _build_input_v4(fp: str, rules: list[dict]) -> None:
    ipt = "iptables"
    _ensure_chain(ipt, "filter", CHAIN_IN)
    add = lambda *a: _run(ipt, "-A", CHAIN_IN, *a)  # noqa: E731
    add("-i", "lo", "-j", "RETURN")
    _run(ipt, "-A", CHAIN_IN, *_marker_args(fp))
    add("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN")
    add("-p", "icmp", "-j", "RETURN")
    # AP'nin CALISMASI icin sart olanlar (e1-ap-firewall ile ayni liste):
    # kapatilirsa AP istemcisi IP bile alamaz.
    add("-p", "udp", "--dport", "67", "-j", "RETURN")
    add("-p", "udp", "--dport", "53", "-j", "RETURN")
    add("-p", "tcp", "--dport", "53", "-j", "RETURN")
    add("-p", "udp", "--dport", "5353", "-j", "RETURN")
    for port in GUARD_TCP:
        add("-p", "tcp", "--dport", str(port), "-j", "RETURN")
    for port in GUARD_UDP:
        add("-p", "udp", "--dport", str(port), "-j", "RETURN")
    for rule in rules:
        args = []
        if rule["source"]:
            args += ["-s", rule["source"]]
        args += ["-p", rule["proto"], "--dport", _ports_colon(rule["ports"])]
        args += ["-j", "RETURN" if rule["action"] == "allow" else "DROP"]
        add(*args)
    add("-j", "DROP")
    _ensure_hook(ipt, "filter", "INPUT", CHAIN_IN)


def _build_docker_v4(fp: str, rules: list[dict], forwards: list[dict]) -> None:
    ipt = "iptables"
    _ensure_docker_user(ipt)
    _ensure_chain(ipt, "filter", CHAIN_FWD)
    add = lambda *a: _run(ipt, "-A", CHAIN_FWD, *a)  # noqa: E731
    # Container'dan CIKAN trafik (kaynak arayuzu docker koprusu) filtrelenmez.
    add("-i", "docker0", "-j", "RETURN")
    add("-i", "br-+", "-j", "RETURN")
    _run(ipt, "-A", CHAIN_FWD, *_marker_args(fp))
    # ACCEPT (RETURN degil): LAN'a yonlendirilen baglantinin DONUS paketleri
    # Docker zincirlerinde eslesmez ve FORWARD policy DROP'a duserdi.
    add("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
    # Port yonlendirmeleri: bizim DNAT'imizla hedefe giden trafik. ACCEPT —
    # RETURN olsaydi Docker'a ait olmayan forward FORWARD policy'ye duserdi.
    for fwd in forwards:
        add(
            "-d", fwd["dest_ip"], "-p", fwd["proto"],
            "--dport", str(fwd["dest_port"]),
            "-m", "conntrack", "--ctstate", "DNAT", "-j", "ACCEPT",
        )
    # Asagidaki HER kural `--ctstate DNAT` tasir: yalnizca hedefi appliance
    # olan (Docker'in DNAT'ladigi) trafik. AP istemcisinin internet NAT'i
    # DNAT degildir ve dokunulmaz (e1-ap-firewall 3. tasarim notu).
    for port in (80, 443):
        add(
            "-p", "tcp", "-m", "conntrack",
            "--ctstate", "DNAT", "--ctorigdstport", str(port), "-j", "RETURN",
        )
    for rule in rules:
        args = ["-p", rule["proto"]]
        if rule["source"]:
            args += ["-s", rule["source"]]
        # DNAT sonrasi --dport container portudur; orijinal hedef port
        # --ctorigdstport ile eslesir (klasik tuzak, bkz. dosya basi).
        args += [
            "-m", "conntrack", "--ctstate", "DNAT",
            "--ctorigdstport", _ports_colon(rule["ports"]),
        ]
        args += ["-j", "RETURN" if rule["action"] == "allow" else "DROP"]
        add(*args)
    add("-m", "conntrack", "--ctstate", "DNAT", "-j", "DROP")
    _ensure_hook(ipt, "filter", "DOCKER-USER", CHAIN_FWD)


def _build_nat_v4(forwards: list[dict]) -> None:
    ipt = "iptables"
    _ensure_chain(ipt, "nat", CHAIN_DNAT)
    _ensure_chain(ipt, "nat", CHAIN_MASQ)
    for fwd in forwards:
        _run(
            ipt, "-t", "nat", "-A", CHAIN_DNAT,
            "-p", fwd["proto"], "--dport", str(fwd["listen_port"]),
            "-j", "DNAT", "--to-destination",
            f"{fwd['dest_ip']}:{fwd['dest_port']}",
        )
        # Donus paketi appliance uzerinden geri donsun: hedef cihaz istemciye
        # dogrudan degil, baglantiyi kuran appliance'a cevap vermeli.
        _run(
            ipt, "-t", "nat", "-A", CHAIN_MASQ,
            "-d", fwd["dest_ip"], "-p", fwd["proto"],
            "--dport", str(fwd["dest_port"]), "-j", "MASQUERADE",
        )
    _ensure_hook(ipt, "nat", "PREROUTING", CHAIN_DNAT)
    _ensure_hook(ipt, "nat", "POSTROUTING", CHAIN_MASQ)


def _build_input_v6(fp: str, rules: list[dict]) -> None:
    ipt = "ip6tables"
    _ensure_chain(ipt, "filter", CHAIN_IN)
    add = lambda *a: _run(ipt, "-A", CHAIN_IN, *a)  # noqa: E731
    add("-i", "lo", "-j", "RETURN")
    _run(ipt, "-A", CHAIN_IN, *_marker_args(fp, v6=True))
    add("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN")
    # NDP/RA ipv6-icmp uzerinden yurur; kapatilirsa IPv6 agi tamamen coker.
    add("-p", "ipv6-icmp", "-j", "RETURN")
    add("-p", "udp", "--dport", "546", "-j", "RETURN")  # DHCPv6 istemci
    for port in GUARD_TCP:
        add("-p", "tcp", "--dport", str(port), "-j", "RETURN")
    for port in GUARD_UDP:
        add("-p", "udp", "--dport", str(port), "-j", "RETURN")
    for rule in rules:
        if rule["source"]:
            continue  # IPv4 CIDR'li kural v6'ya cevrilemez — atla (belgeli)
        add(
            "-p", rule["proto"], "--dport", _ports_colon(rule["ports"]),
            "-j", "RETURN" if rule["action"] == "allow" else "DROP",
        )
    add("-j", "DROP")
    _ensure_hook(ipt, "filter", "INPUT", CHAIN_IN)


def _apply_ruleset(config: dict) -> None:
    """Istenen yapilandirmayi sifirdan kur (idempotent: zincirler bosaltilir)."""
    fp = _fingerprint(config)
    if config["enabled"]:
        _build_input_v4(fp, config["rules"])
        _build_docker_v4(fp, config["rules"], config["forwards"])
        _build_nat_v4(config["forwards"])
        if _has_ip6tables():
            _build_input_v6(fp, config["rules"])
    else:
        _clear_ruleset()


def _clear_ruleset() -> None:
    """Zincirleri bosalt; kancalari YERINDE birak (bos zincir no-op'tur)."""
    if not _has_iptables():
        # iptables yoksa kurulmus kural da yoktur; "kapat" istegi yine de
        # basariyla sonuclanmali (temizlenecek bir sey yok).
        return
    ipt = "iptables"
    _flush_if_exists(ipt, "filter", CHAIN_IN)
    _flush_if_exists(ipt, "filter", CHAIN_FWD)
    _flush_if_exists(ipt, "nat", CHAIN_DNAT)
    _flush_if_exists(ipt, "nat", CHAIN_MASQ)
    if _has_ip6tables():
        _flush_if_exists("ip6tables", "filter", CHAIN_IN)


def _measure(config: dict) -> dict:
    """Gercek durumu OLC: zincirlerdeki imza, istenen yapilandirmayla uyusuyor mu?

    Donen: {active, in_sync}. `active` = kurallar sahada gercekten kurulu.
    Reboot sonrasi tablolar bosalir, biri elle `iptables -F` yapabilir,
    docker restart zincir sirasini bozabilir — hepsi burada yakalanir ve
    report turu yeniden uygular.
    """
    if not _has_iptables():
        return {"active": False, "in_sync": not config["enabled"]}
    fp = _fingerprint(config)
    mark_in = _read_marker("iptables", CHAIN_IN)
    mark_fwd = _read_marker("iptables", CHAIN_FWD)
    if config["enabled"]:
        in_sync = mark_in == fp and mark_fwd == fp
        if in_sync and _has_ip6tables():
            in_sync = _read_marker("ip6tables", CHAIN_IN) == fp
        # Kancalar da yerinde mi? Zincir dolu ama INPUT'tan kopuksa etkisiz.
        if in_sync:
            rc1, _ = _run("iptables", "-C", "INPUT", "-j", CHAIN_IN, check=False)
            rc2, _ = _run(
                "iptables", "-C", "DOCKER-USER", "-j", CHAIN_FWD, check=False
            ) if _chain_exists("iptables", "filter", "DOCKER-USER") else (1, "")
            in_sync = rc1 == 0 and rc2 == 0
        return {"active": in_sync, "in_sync": in_sync}
    # Kapali: hicbir zincirde imza olmamali.
    return {"active": False, "in_sync": mark_in is None and mark_fwd is None}


# --- Yetkili yapilandirma (config.json) -------------------------------------
def _default_config() -> dict:
    # VARSAYILAN KAPALI: ajanin kurulmasi tek basina hicbir trafigi kesmez.
    # Mevcut sahadaki cihazlar guncellemeyi alinca davranis DEGISMEZ;
    # duvari kullanici bilerek acar.
    return {"enabled": False, "rules": [], "forwards": []}


def _read_config() -> dict:
    data = _read_json(CONFIG_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("config"), dict):
        return {"schema": SCHEMA_VERSION, "config": _default_config()}
    try:
        data["config"] = _validate_config(data["config"])
    except RequestError:
        # Bozuk config guvenli yone duser: duvari acmayiz ama KAPATMAYIZ da
        # — mevcut kurallar dururken hepsini silmek yuzeyi sessizce acardi.
        # in_sync olcumu zaten uyusmazligi state.json'a yansitir.
        _log("config.json bozuk — varsayilana donuldu (duvar kapali).")
        return {"schema": SCHEMA_VERSION, "config": _default_config()}
    return data


def _write_config(config: dict, *, request_id: str | None,
                  updated_by: str | None, updated_by_role: str | None) -> dict:
    payload = {
        "schema": SCHEMA_VERSION,
        "config": config,
        "updated_at": _now_iso(),
        "updated_by": updated_by,
        "updated_by_role": updated_by_role,
        "request_id": request_id,
    }
    os.makedirs(PRIV_DIR, mode=0o700, exist_ok=True)
    _write_json(CONFIG_PATH, payload, mode=0o600, group_from=None)
    return payload


# --- report: cekirdek (yakinsama BURADA) ------------------------------------
def cmd_report() -> int:
    """Durumu olc, gerekiyorsa yeniden uygula, state.json yaz.

    Kalicilik garantisi bu fonksiyona baglidir: iptables kurallari reboot'ta
    ucar, ayri bir persistence paketi YOK — 60 sn'lik timer her turda istenen
    yapilandirmayla olculen durumu karsilastirir ve farkliysa yakinsar
    (e1-rad'in lease yakinsamasiyla ayni felsefe).
    """
    stored = _read_config()
    config = stored["config"]
    mismatch: str | None = None
    reason: str | None = None
    measured_active = False

    if not _has_iptables():
        reason = "iptables_missing"
        if config["enabled"]:
            _log("iptables yok — istenen duvar UYGULANAMIYOR.")
    else:
        measured = _measure(config)
        if not measured["in_sync"]:
            try:
                _apply_ruleset(config)
                _log(
                    "yapilandirma yeniden uygulandi "
                    f"({'acik' if config['enabled'] else 'kapali'})."
                )
            except RuntimeError as exc:
                _log(f"yapilandirma uygulanamadi: {exc}")
            measured = _measure(config)
        if not measured["in_sync"]:
            mismatch = "apply_failed" if config["enabled"] else "clear_failed"
        measured_active = bool(measured["active"])

    state = {
        "schema": SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "iptables": _has_iptables(),
        "ipv6": _has_ip6tables(),
        "enabled": bool(config["enabled"]),
        # OLCULEN deger — niyet degil (imza kurali sahada gercekten var).
        "active": bool(measured_active),
        "mismatch": mismatch,
        "config": config,
        "changed_by": stored.get("updated_by"),
        "changed_at": stored.get("updated_at"),
        "guard_tcp_ports": list(GUARD_TCP),
        "reserved_listen_ports": sorted(RESERVED_LISTEN),
        "max_rules": MAX_RULES,
        "max_forwards": MAX_FORWARDS,
        "reason": reason,
    }
    _write_json(STATE_PATH, state)
    return 0


# --- apply: request.json'i dogrula ve uygula --------------------------------
_REQUEST_KEYS = frozenset({
    "id", "action", "created_at", "requested_by", "requested_by_role",
    "requested_ip", "reason", "config",
})


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

    def _finish(ok: bool, error: str | None, applied: dict | None = None) -> int:
        result = {
            "schema": SCHEMA_VERSION,
            "request_id": request_id or None,
            "action": "set_config",
            "status": "applied" if ok else "failed",
            "error": error,
            "at": _now_iso(),
            "applied": applied or {},
        }
        _write_json(STATUS_PATH, result)
        _archive(
            f"request-{request_id[:12] or 'x'}", {"request": raw, "result": result}
        )
        _remove(REQUEST_PATH)
        if not ok:
            _log(f"REDDEDILDI: {error}")
        return 0 if ok else 1

    _write_json(
        STATUS_PATH,
        {
            "schema": SCHEMA_VERSION,
            "request_id": request_id or None,
            "action": "set_config",
            "status": "applying",
            "error": None,
            "at": _now_iso(),
        },
    )

    try:
        if str(raw.get("action") or "").strip().lower() != "set_config":
            raise RequestError(f"Bilinmeyen aksiyon: {raw.get('action')!r}")
        unknown = sorted(set(raw.keys()) - _REQUEST_KEYS)
        if unknown:
            raise RequestError(f"Bilinmeyen alan(lar): {', '.join(unknown)}")
        config = _validate_config(
            raw.get("config") if isinstance(raw.get("config"), dict) else {}
        )
    except RequestError as exc:
        return _finish(False, str(exc))

    if config["enabled"] and not _has_iptables():
        return _finish(False, "iptables_missing")

    _write_config(
        config,
        request_id=request_id or None,
        updated_by=_clean_text(raw.get("requested_by"), 64),
        updated_by_role=_clean_text(raw.get("requested_by_role"), 32),
    )

    try:
        _apply_ruleset(config)
    except RuntimeError as exc:
        cmd_report()
        return _finish(False, f"apply_failed: {exc}")

    cmd_report()
    state = _read_json(STATE_PATH) or {}
    if config["enabled"] and not state.get("active"):
        return _finish(False, f"verify_failed: {state.get('mismatch') or 'kurulamadi'}")
    return _finish(
        True, None,
        applied={
            "enabled": config["enabled"],
            "rule_count": len(config["rules"]),
            "forward_count": len(config["forwards"]),
        },
    )


# --- CLI: acil kapatma -------------------------------------------------------
def cmd_disable() -> int:
    """Kurtarma yolu: sudo e1-fwd.py disable — duvari kapat, kurallari koru.

    Yalnizca `enabled` false yapilir; kural/yonlendirme listesi SILINMEZ,
    kullanici arayuzden tek tikla geri acabilir.
    """
    stored = _read_config()
    config = dict(stored["config"])
    config["enabled"] = False
    _write_config(
        config,
        request_id=None,
        updated_by="konsol",
        updated_by_role=None,
    )
    try:
        _clear_ruleset()
    except RuntimeError as exc:
        _log(f"zincirler bosaltilamadi: {exc}")
    _log("guvenlik duvari elle KAPATILDI (kurallar korunuyor).")
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
    if command == "disable":
        return cmd_disable()
    _log(f"bilinmeyen komut: {command} (report|apply|disable)")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
