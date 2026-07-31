"""Cihaz bazli offline lisans sozlesmeleri."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_UTC_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"


class LicenseRequestFile(BaseModel):
    schema_version: Literal[1]
    product_id: Literal["enerjione-grid"]
    request_id: str = Field(pattern=_UUID_PATTERN)
    installation_id: str = Field(pattern=_UUID_PATTERN)
    machine_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    created_at: str = Field(pattern=_UTC_PATTERN)

    model_config = ConfigDict(extra="forbid")


class LicensePayload(BaseModel):
    schema_version: Literal[1]
    product_id: Literal["enerjione-grid"]
    license_id: str = Field(pattern=_UUID_PATTERN)
    request_id: str = Field(pattern=_UUID_PATTERN)
    installation_id: str = Field(pattern=_UUID_PATTERN)
    machine_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    customer_code: str = Field(min_length=1, max_length=80)
    customer_name: str = Field(min_length=1, max_length=200)
    project_name: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)
    device_limit: int = Field(ge=1, le=100_000)
    issued_at: str = Field(pattern=_UTC_PATTERN)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LicenseEnvelope(BaseModel):
    schema_version: Literal[1]
    kid: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    payload: LicensePayload
    signature: str = Field(min_length=1, max_length=256)

    model_config = ConfigDict(extra="forbid")


class LicenseGate(BaseModel):
    """Lisans kilidinin MINIMAL gorunumu — her role acik.

    `LicenseStatus` musteri adi / lisans no / cihaz limiti gibi ticari
    bilgileri tasidigi icin yalnizca engineer+installer okuyabiliyor. Ama
    operator/ops_manager'in da arayuzde kilitlenmesi gerekiyor; onlara sadece
    "kilitli mi, neden" bilgisi verilir.
    """

    locked: bool
    state: Literal[
        "valid",
        "unlicensed",
        "invalid",
        "machine_mismatch",
        "machine_unavailable",
    ]
    reason_code: str


class LicenseStatus(BaseModel):
    state: Literal[
        "valid",
        "unlicensed",
        "invalid",
        "machine_mismatch",
        "machine_unavailable",
    ]
    reason_code: str
    is_valid: bool
    can_add_device: bool
    quota_state: Literal["available", "full", "over_limit", "unavailable"]
    installation_id: str
    machine_fingerprint: str | None = None
    license_id: str | None = None
    customer_code: str | None = None
    customer_name: str | None = None
    project_name: str | None = None
    note: str | None = None
    device_limit: int = 0
    device_count: int
    remaining: int = 0
    issued_at: str | None = None
