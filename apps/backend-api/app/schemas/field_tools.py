"""Saha araclari (field tools) request/response semalari.

Ping: mini PC uzerinden bir saha cihazina (FID/gateway) ICMP erisim testi.
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
