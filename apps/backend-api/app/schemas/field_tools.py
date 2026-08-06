"""Saha araclari (field tools) request/response semalari.

Sistem uzerinden saha cihazina (FID/gateway) erisim testleri:
ping, TCP port kontrolu, traceroute, DNS cozumleme ve toplu tarama.
"""

from pydantic import BaseModel, Field


class PingRequest(BaseModel):
    # IP adresi veya hostname. Dogrulama service katmaninda (ipaddress +
    # hostname regex) yapilir; serbest metin komuta ASLA gecmez.
    host: str = Field(min_length=1, max_length=253)
    count: int = Field(default=4, ge=1, le=10)


class PingResult(BaseModel):
    host: str
    success: bool
    packets_sent: int
    packets_received: int
    # 0-100; gonderilenden hesaplanir (cikti locale'inden bagimsiz).
    packet_loss_percent: float
    # Yanit gelmediyse None.
    rtt_min_ms: float | None = None
    rtt_avg_ms: float | None = None
    rtt_max_ms: float | None = None
    # Ham ping ciktisi (kirpilmis) — kullanici teshis icin gormek ister.
    output: str
    duration_ms: int


class PortCheckRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    timeout_ms: int = Field(default=2000, ge=200, le=10000)


class PortCheckResult(BaseModel):
    host: str
    port: int
    open: bool
    elapsed_ms: int
    # Kapali/basarisizsa kisa sebep (ornegin "Connection refused").
    error: str | None = None


class TracerouteRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    max_hops: int = Field(default=15, ge=1, le=30)


class TracerouteResult(BaseModel):
    host: str
    # Komut kosup cikti uretti mi (hedefe ulasilamasa da rota degerlidir).
    success: bool
    output: str
    duration_ms: int


class DnsRequest(BaseModel):
    name: str = Field(min_length=1, max_length=253)


class DnsResult(BaseModel):
    name: str
    resolved: bool
    addresses: list[str]
    elapsed_ms: int


class ScanRequest(BaseModel):
    # Frontend cihaz listesini parcalar halinde gonderir (ilerleme gostermek
    # ve tek HTTP istegini dakikalara uzatmamak icin) — o yuzden ust sinir 50.
    device_ids: list[int] = Field(min_length=1, max_length=50)


class DeviceScanResult(BaseModel):
    device_id: int
    host: str | None
    # IP tanimsizsa / ping kosulamadiysa None.
    ping_success: bool | None
    rtt_avg_ms: float | None
    port: int | None
    port_open: bool | None
    # no_ip | invalid_host | ping_unavailable | ping_timeout
    error: str | None = None
