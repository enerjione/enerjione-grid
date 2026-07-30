"""Gateway kurulum ajani (e1-gwd) icin Pydantic sema'lari.

Kaynak: host'ta root ile calisan `e1-gwd` ajaninin yazdigi state.json /
status.json dosyalari. Backend bu dosyalari okur, kurulum istegini
request.json olarak yazar; docker'i backend CALISTIRMAZ.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LocalGateway(BaseModel):
    """Bu cihazda kurulu bir gateway container'i."""

    code: str
    name: str | None = None
    container: str | None = None
    # running | exited | created | absent | unknown
    state: str = "unknown"
    status: str | None = None
    image: str | None = None
    ports: str | None = None
    installed_at: str | None = None


class GatewayApplyStatus(BaseModel):
    """Ajanin isledigi son istegin sonucu."""

    id: str | None = None
    action: str | None = None
    code: str | None = None
    ok: bool | None = None
    # validate | pull | up | down | restart | cleanup | done | docker
    stage: str | None = None
    message: str | None = None
    # Docker ciktisinin son satirlari — hata ayiklama icin UI'da gosterilir.
    detail: str | None = None
    # Ajan hala calisiyor mu (asamalar arasinda True yazilir).
    running: bool = False
    at: str | None = None


class GatewayAgentStatus(BaseModel):
    available: bool
    # state_dir_missing | state_dir_not_writable | agent_never_reported |
    # state_stale
    reason: str | None = None
    docker_available: bool = False
    updated_at: str | None = None
    state_age_seconds: float | None = None
    gateways: list[LocalGateway] = Field(default_factory=list)
    pending: bool = False
    last_apply: GatewayApplyStatus | None = None


class LocalInstallRequest(BaseModel):
    """"Gateway'i bu cihaza kur" istegi.

    backend_url verilmezse host'un kendi nginx'i (host.docker.internal:80)
    kullanilir — gateway ayni makinede oldugu icin LAN IP'sine gerek yok ve
    IP degisse bile kurulum bozulmaz.
    """

    backend_url: str | None = None
    nats_url: str | None = None
    host_port: int | None = Field(default=None, ge=1, le=65535)
    image: str | None = None
    app_environment: Literal["development", "staging", "production"] = "production"


class LocalInstallResponse(BaseModel):
    request_id: str
    code: str
