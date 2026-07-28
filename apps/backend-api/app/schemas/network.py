"""Appliance ag ayarlari (Ag Ayarlari sayfasi) icin Pydantic sema'lari.

Kaynak: host'ta root ile calisan `e1-netd` ajaninin yazdigi state.json /
status.json dosyalari. Backend bu dosyalari okur, degisiklik istegini
request.json olarak yazar; nmcli'yi backend CALISTIRMAZ.
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class NetworkInterface(BaseModel):
    ifname: str
    type: str
    state: str | None = None
    connection: str | None = None
    managed_by_e1: bool = False
    mac: str | None = None
    # Cihazin SU ANKI (runtime) adresleri — "192.168.1.50/24" formatinda.
    addresses: list[str] = Field(default_factory=list)
    gateway: str | None = None
    dns: list[str] = Field(default_factory=list)
    # Profildeki KALICI niyet: "auto" (DHCP) | "manual" (statik).
    method: str | None = None
    profile_addresses: list[str] = Field(default_factory=list)
    profile_gateway: str | None = None
    profile_dns: list[str] = Field(default_factory=list)


class AccessPointInfo(BaseModel):
    connection: str | None = None
    exists: bool = False
    active: bool = False
    ssid: str | None = None
    ifname: str | None = None
    address: str | None = None
    secured: bool = False


class NetworkApplyStatus(BaseModel):
    request_id: str | None = None
    #  applying | applied | rebooting | failed
    status: str | None = None
    error: str | None = None
    at: str | None = None
    applied: dict | None = None


class WifiState(BaseModel):
    """Appliance'in WiFi CLIENT (station) durumu.

    AP (erisim noktasi) ayri bir sey ve `AccessPointInfo` ile temsil edilir;
    arayuzden degistirilemez — kurtarma yolu olarak korunur.
    """

    supported: bool = False
    ifname: str | None = None
    connection: str | None = None
    connected: bool = False
    ssid: str | None = None
    signal: int | None = None
    addresses: list[str] = Field(default_factory=list)
    # Kayitli profil var mi (baglanti kopuk olsa bile).
    saved: bool = False
    # AP geri donus muhafizi aktif mi + ne zaman dolacak (epoch).
    guard_active: bool = False
    guard_deadline: float | None = None


class WifiNetwork(BaseModel):
    """Taramada gorunen tek bir ag."""

    ssid: str
    signal: int = 0
    security: str | None = None
    secured: bool = True
    freq: str | None = None
    in_use: bool = False


class WifiScanResult(BaseModel):
    available: bool = False
    updated_at: str | None = None
    ifname: str | None = None
    networks: list[WifiNetwork] = Field(default_factory=list)
    # Tarama sonucunun yasi (sn) — UI "x sn once tarandi" gosterir.
    age_seconds: float | None = None


class WifiConnectRequest(BaseModel):
    """Bir aga baglanma istegi.

    UYARI (tek radyo): appliance'ta tek WiFi karti var; baglanti sirasinda
    AP DUSER. Baglanti kurulamazsa host ajani muhafiz suresi sonunda AP'yi
    otomatik geri acar (bkz. e1-netd `_guard_check`).
    """

    ssid: str = Field(..., min_length=1, max_length=32)
    # Sifresiz (open) aglar icin bos birakilir. Saklanmaz: yalnizca ajana
    # iletilir, ajan da arsive yazmaz.
    # NOT: Uzunluk kisiti Field'da DEGIL validator'da — `min_length=8` bos
    # string'i de reddediyordu, oysa bos = "sifresiz ag" demek.
    psk: str | None = None

    @field_validator("ssid")
    @classmethod
    def _check_ssid(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("SSID bos olamaz.")
        return cleaned

    @field_validator("psk")
    @classmethod
    def _check_psk(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None  # sifresiz (open) ag
        if not 8 <= len(value) <= 63:
            raise ValueError("WiFi sifresi 8-63 karakter olmali.")
        return value


class NetworkStatus(BaseModel):
    """Ag Ayarlari sayfasinin tek okuma kaynagi."""

    # Appliance modu aktif mi? (host ajani dizini erisilebilir mi)
    available: bool
    # Kapali ise kullaniciya gosterilecek sebep (mount yok / ajan hic calismamis).
    reason: str | None = None
    hostname: str | None = None
    mdns_name: str | None = None
    updated_at: str | None = None
    # state.json'in yasi (sn). Buyukse ajan durmus demektir.
    state_age_seconds: float | None = None
    ap: AccessPointInfo = Field(default_factory=AccessPointInfo)
    wifi: WifiState = Field(default_factory=WifiState)
    interfaces: list[NetworkInterface] = Field(default_factory=list)
    # Islenmeyi bekleyen istek (varsa) — UI "uygulaniyor" gosterir.
    pending: bool = False
    last_apply: NetworkApplyStatus | None = None


class NetworkConfigUpdate(BaseModel):
    """UI'dan gelen ag ayari istegi.

    Burada yapilan dogrulama "erken uyari" icindir; nihai otorite host
    ajanidir (o da ayni kurallari bagimsiz uygular). Container'dan gelen
    veriye ajan asla korukorune guvenmez.
    """

    ifname: str = Field(..., min_length=1, max_length=15)
    method: Literal["dhcp", "static"]
    address: str | None = None
    prefix: int | None = Field(default=None, ge=1, le=32)
    gateway: str | None = None
    dns: list[str] = Field(default_factory=list, max_length=3)
    # Kullanici akisi: kaydet -> yeniden baslat -> yeni IP gecerli.
    reboot: bool = True

    @field_validator("ifname")
    @classmethod
    def _check_ifname(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or not all(ch.isalnum() or ch in "_.-" for ch in cleaned):
            raise ValueError("Gecersiz arayuz adi.")
        return cleaned

    @field_validator("address", "gateway")
    @classmethod
    def _check_ipv4(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        try:
            return str(ipaddress.IPv4Address(str(value).strip()))
        except ipaddress.AddressValueError:
            raise ValueError("Gecersiz IPv4 adresi.") from None

    @field_validator("dns")
    @classmethod
    def _check_dns(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            try:
                cleaned.append(str(ipaddress.IPv4Address(text)))
            except ipaddress.AddressValueError:
                raise ValueError(f"Gecersiz DNS adresi: {text}") from None
        return cleaned

    @model_validator(mode="after")
    def _check_static(self) -> "NetworkConfigUpdate":
        if self.method != "static":
            return self
        if not self.address:
            raise ValueError("Statik modda IP adresi zorunlu.")
        prefix = self.prefix or 24
        network = ipaddress.IPv4Network(f"{self.address}/{prefix}", strict=False)
        addr = ipaddress.IPv4Address(self.address)
        if prefix < 31 and addr in (network.network_address, network.broadcast_address):
            raise ValueError("Ag veya broadcast adresi host IP'si olamaz.")
        if self.gateway:
            gw = ipaddress.IPv4Address(self.gateway)
            if gw not in network:
                raise ValueError(
                    f"Ag gecidi {gw} secilen alt agda degil ({network})."
                )
            if gw == addr:
                raise ValueError("Ag gecidi cihazin kendi IP'si olamaz.")
        # AP alt agi (10.42.0.0/24) ile cakisma cihazi erisilemez yapar.
        if network.overlaps(ipaddress.IPv4Network("10.42.0.0/24")):
            raise ValueError(
                "Bu alt ag WiFi AP agi (10.42.0.0/24) ile cakisiyor."
            )
        self.prefix = prefix
        return self


class NetworkConfigAccepted(BaseModel):
    """Istek kuyruga alindi — uygulama host ajaninda asenkron surer."""

    request_id: str
    reboot: bool
    # Reboot sonrasi kullanicinin gidecegi adres (statikse yeni IP).
    next_url: str | None = None
