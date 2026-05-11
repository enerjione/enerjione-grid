"""API Key (Personal Access Token) icin pydantic semalar."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiKeyCreate(BaseModel):
    """Kullanici yeni token uretir.

    `scopes` virgulle ayrilmis liste (`telemetry:read,devices:read`). Bos veya
    None gelirse default scope (`telemetry:read,devices:read,alarms:read`)
    atanir. Bilinmeyen scope'lar reddedilir (api_key_service.ALLOWED_SCOPES).

    `expires_at` None ise suresiz; aksi halde gelecek bir tarih olmali.
    `allowed_ips` CSV (`1.2.3.4,5.6.7.8`); bos = herhangi bir IP.
    """

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] | None = None
    expires_at: datetime | None = None
    allowed_ips: list[str] | None = None

    @field_validator("scopes", mode="before")
    @classmethod
    def _split_scopes(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator("allowed_ips", mode="before")
    @classmethod
    def _split_ips(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


class ApiKeyRead(BaseModel):
    """API Key listesi/detay donusu — token plain'i ICERMEZ."""

    id: int
    name: str
    token_prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    allowed_ips: list[str] | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)

    @field_validator("scopes", mode="before")
    @classmethod
    def _csv_to_list(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator("allowed_ips", mode="before")
    @classmethod
    def _csv_to_list_ips(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


class ApiKeyCreatedResponse(ApiKeyRead):
    """Yalniz olusturma cevabinda donulen ek alan: `token` (plain).

    Frontend bu degeri kullaniciya **bir kerelik** gosterir; sonra sahibinden
    tekrar erisemez (DB'de sha256 hash saklanir, plain hic tutulmaz).
    """

    token: str
