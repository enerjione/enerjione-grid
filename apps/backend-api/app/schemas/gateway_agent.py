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

    # --- Guncelleme durumu -------------------------------------------------
    #
    # Digest KARSILASTIRMASI ile: gateway imaji `:latest` etiketiyle sabit,
    # yani surum numarasi yok. Etiketin isaret ettigi manifest digest'i
    # degistiyse yeni bir imaj yayinlanmis demektir.
    #: Calisan imajin kayit defteri digest'i.
    image_digest: str | None = None
    #: Kayit defterindeki etiketin su anki digest'i.
    remote_digest: str | None = None
    #: UC DURUMLU. `None` = BILINMIYOR (kayit defterine ulasilamadi).
    #: `False` ile ayni sayilmamali: "guncel" demek, sormadan verilmis bir
    #: iddia olurdu ve arayuzde yanlis bir guven yaratirdi.
    update_available: bool | None = None

    # --- Okunabilir surum (yalnizca GOSTERIM) ------------------------------
    #
    # Karar hala digest'e dayali; bu iki alan operatorun ekranda ne
    # gorecegini belirler. `sha256:4a993d21...` hicbir sey soylemiyordu,
    # `0.6.0 -> 0.7.0` soyluyor.
    #
    # OCI `org.opencontainers.image.version` etiketinden okunur. Etiket
    # eksikse ya da kayit defterine ulasilamazsa bos kalir — guncelleme
    # mantigi bundan ETKILENMEZ.
    #
    # Semver OLMAYABILIR: CI dal push'unda etiket dal adini ("main")
    # tasiyabilir. Oldugu gibi gosterilir, ayristirilmaya calisilmaz.
    #: Calisan imajin surumu.
    local_version: str | None = None
    #: Kayit defterindeki imajin surumu.
    remote_version: str | None = None


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
