"""Gateway guncelleme uclarinin I/O semalari."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CompatibilityWarningOut(BaseModel):
    """Gateway surumu ile kullanilan ozellik arasindaki uyumsuzluk."""

    feature: str
    required_version: str
    current_version: str | None = None
    affected_devices: int = 0
    message: str


class GatewayUpdateState(BaseModel):
    """`GET /gateways/{code}/update` yaniti — liste ve detay ayni kaynaktan."""

    gateway_code: str

    # --- SURUMLER ---------------------------------------------------------
    #: Gateway'in SU AN calistirdigi surum. Iki kaynak var ve ikisi de
    #: dogru olabilir: ajan (bu cihazda kuruluysa) ve gateway'in kendi
    #: saglik heartbeat'i (uzak gateway'ler icin TEK kaynak).
    current_version: str | None = None
    #: Surumun nereden okundugu — "agent" | "health" | None. Operator
    #: "neden bos" sorusunu ekrandan cevaplayabilmeli.
    current_version_source: str | None = None

    #: Kayit defterindeki, izlenen etiketin isaret ettigi surum.
    available_version: str | None = None
    #: UC DURUMLU: None = BILINMIYOR (kayit defterine ulasilamadi). `False`
    #: ile ayni saymak, sormadan "guncel" demek olurdu.
    update_available: bool | None = None

    #: Hazirlik yapildiysa secilen hedef (digest'e sabitlenmis).
    target_version: str | None = None
    target_image: str | None = None
    expected_digest: str | None = None

    #: Izlenen imaj referansi — "neden guncelleme cikmiyor" sorusu buradan
    #: cevaplanir (surume sabitlenmis bir etiket izleniyor olabilir).
    tracked_image: str | None = None
    #: `stable` | `development` | None. Gelistirme etiketleri (`:main`,
    #: `:sha-*`) uretim hedefi DEGILDIR ve "guncelleme mevcut" diye
    #: sunulmaz.
    channel: str | None = None

    # --- DURUM ------------------------------------------------------------
    status: str = "idle"
    from_version: str | None = None
    from_image: str | None = None
    started_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    is_rollback: bool = False
    #: Geri alinabilir mi (onceki imaj biliniyor mu).
    can_rollback: bool = False

    #: Bu gateway bu cihazda kurulu mu — degilse guncelleme uclari 409
    #: doner. Arayuz bunu "kurulu degil" olarak gostermeli, "guncel" diye
    #: DEGIL.
    installed_locally: bool = False

    compatibility: list[CompatibilityWarningOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GatewayUpdatePrepareRequest(BaseModel):
    """Hazirlik govdesi.

    `target_image` verilmezse gateway'in IZLEDIGI etiket kullanilir. Elle
    verildiginde alternatif kayit defteri de yazilabilir
    (`localhost:5000/...`) — servis tooling'i icin; ajanin kendi
    allowlist/regex modeli aynen gecerlidir.
    """

    target_image: str | None = Field(default=None, max_length=400)
