"""Guvenlik duvari (Guvenlik Duvari sayfasi) icin Pydantic sema'lari.

Kaynak: host'ta root ile calisan `e1-fwd` ajaninin yazdigi state.json /
status.json dosyalari. Backend iptables'i CALISTIRMAZ; istenen yapilandirmayi
request.json olarak yazar, ajan bagimsiz dogrulayip uygular.

TEMEL KURALLAR (ajan tarafinda da bagimsiz uygulanir; en dar olan kazanir):
  * Duvar VARSAYILAN KAPALI. Acmak bilincli bir kullanici kararidir.
  * Kilitlenme korumasi: 22/80/443 TCP + uzaktan bakim tuneli HER ZAMAN acik;
    kullanici kurali bunlari EZEMEZ. Yanlis yapilandirma cihaza erisimi
    kapatirsa duzeltilecek yol da kalmazdi.
  * Port yonlendirme, sistemin yayinladigi portlari (web/SSH/SCADA/FTP)
    dinleyemez — mevcut servisi sessizce golgelemek en sinsi ariza sinifidir.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Ajandaki sabitlerle AYNI olmali (infra/appliance/e1-fwd.py). Uc yerde de
# bagimsiz uygulanir: sema erken uyari verir, nihai otorite ajandir.
MAX_RULES = 50
MAX_FORWARDS = 20
GUARD_TCP_PORTS = (22, 80, 443)
RESERVED_LISTEN_PORTS = frozenset(
    {21, 22, 80, 443, 502, 2404, 2405, 2406, 4222, 5672, 5020, 5021}
    | set(range(30000, 30010))
)

_PORTS_RE = re.compile(r"^\d{1,5}(-\d{1,5})?$")


class FirewallRule(BaseModel):
    """Tek bir izin/engel kurali. Sira ONEMLIDIR: ilk eslesen kazanir."""

    action: Literal["allow", "deny"]
    proto: Literal["tcp", "udp"]
    # "2404" veya "2404-2406" (aralik).
    ports: str
    # Istege bagli kaynak agi (IPv4 CIDR). Bos = her kaynak.
    source: str | None = None
    comment: str | None = Field(default=None, max_length=80)

    @field_validator("ports")
    @classmethod
    def _check_ports(cls, value: str) -> str:
        text = (value or "").strip()
        if not _PORTS_RE.match(text):
            raise ValueError("port '2404' veya '2404-2406' bicimlerinde olmali")
        parts = [int(p) for p in text.split("-")]
        lo, hi = parts[0], parts[-1]
        if not (1 <= lo <= 65535 and 1 <= hi <= 65535 and lo <= hi):
            raise ValueError("port araligi 1-65535 olmali")
        return str(lo) if lo == hi else f"{lo}-{hi}"

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        if not text:
            return None
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise ValueError("gecersiz kaynak agi (IPv4 CIDR bekleniyor)") from exc
        if net.version != 4:
            raise ValueError("kaynak yalnizca IPv4 olabilir")
        return str(net)

    @field_validator("comment")
    @classmethod
    def _clean_comment(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


class FirewallForward(BaseModel):
    """Port yonlendirme: appliance:listen_port -> dest_ip:dest_port (DNAT)."""

    proto: Literal["tcp", "udp"]
    listen_port: int = Field(..., ge=1, le=65535)
    dest_ip: str
    dest_port: int = Field(..., ge=1, le=65535)
    comment: str | None = Field(default=None, max_length=80)

    @field_validator("listen_port")
    @classmethod
    def _check_listen(cls, value: int) -> int:
        if value in RESERVED_LISTEN_PORTS:
            raise ValueError(f"{value} portu sistem tarafindan kullaniliyor")
        return value

    @field_validator("dest_ip")
    @classmethod
    def _check_dest(cls, value: str) -> str:
        try:
            addr = ipaddress.ip_address((value or "").strip())
        except ValueError as exc:
            raise ValueError("gecersiz hedef IP") from exc
        if (
            addr.version != 4
            or addr.is_loopback
            or addr.is_unspecified
            or addr.is_multicast
        ):
            raise ValueError("hedef IP gecersiz")
        return str(addr)

    @field_validator("comment")
    @classmethod
    def _clean_comment(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


class FirewallConfig(BaseModel):
    """Istenen yapilandirmanin TAMAMI. Artimli degisiklik yok: her PUT tum
    listeyi tasir; ajan atomik olarak eskisinin yerine koyar. Boylece istek
    siralamasi/yarisi diye bir sorun sinifi hic dogmaz."""

    enabled: bool
    rules: list[FirewallRule] = Field(default_factory=list, max_length=MAX_RULES)
    forwards: list[FirewallForward] = Field(
        default_factory=list, max_length=MAX_FORWARDS
    )


class FirewallApplyStatus(BaseModel):
    """Ajanin isledigi son istegin sonucu (status.json)."""

    request_id: str | None = None
    action: str | None = None       # set_config
    status: str | None = None       # applying | applied | failed
    error: str | None = None
    at: str | None = None
    applied: dict | None = None


class FirewallStatus(BaseModel):
    """Guvenlik Duvari sayfasinin TEK okuma kaynagi."""

    # Ajan kullanilabilir mi (dizin var/yazilabilir + en az bir rapor yazilmis).
    available: bool
    # state_dir_missing | state_dir_not_writable | agent_never_reported |
    # state_stale
    reason: str | None = None
    # Ajanin bildirdigi kullanilamazlik sebebi: iptables_missing
    agent_reason: str | None = None
    updated_at: str | None = None
    state_age_seconds: float | None = None
    # Host'ta iptables/ip6tables var mi (ajan raporlar).
    iptables: bool = False
    ipv6: bool = False
    # Istenen durum (config.enabled) ve OLCULEN durum (kurallar sahada
    # gercekten kurulu mu). Ikisi ayristiysa `mismatch` dolu gelir.
    enabled: bool = False
    active: bool = False
    # apply_failed | clear_failed
    mismatch: str | None = None
    config: FirewallConfig | None = None
    changed_by: str | None = None
    changed_at: str | None = None
    # Kilitlenme korumasi portlari — UI "bunlar hep acik" diye gosterir.
    guard_tcp_ports: list[int] = Field(default_factory=list)
    # Yonlendirmenin dinleyemeyecegi portlar — UI erken uyari verir.
    reserved_listen_ports: list[int] = Field(default_factory=list)
    max_rules: int = MAX_RULES
    max_forwards: int = MAX_FORWARDS
    # Islenmeyi bekleyen istek — UI "uygulaniyor" gosterir.
    pending: bool = False
    last_apply: FirewallApplyStatus | None = None
    # Yapilandirma yetkisi. UI butonlari buna gore gizler; asil kontrol
    # backend'de (bkz. api/firewall.py).
    can_manage: bool = False


class FirewallConfigAccepted(BaseModel):
    """Istek kuyruga alindi — uygulama ajanda asenkron surer (202)."""

    request_id: str
    action: Literal["set_config"] = "set_config"
