from pydantic import BaseModel, Field


class ProjectSettingsRead(BaseModel):
    """Login ekrani + header tarafindan auth-siz okunabilir; private alan yok."""

    project_name: str | None = None
    customer_name: str | None = None
    customer_logo: str | None = None
    customer_logo_light: str | None = None
    battery_voltage_low: float | None = None
    battery_voltage_full: float | None = None
    site_title: str | None = None
    favicon: str | None = None
    login_image: str | None = None

    class Config:
        from_attributes = True


class ProjectSettingsUpdate(BaseModel):
    """PUT ile gonderilen alanlardan sadece set olanlar uygulanir."""

    project_name: str | None = Field(default=None, max_length=200)
    customer_name: str | None = Field(default=None, max_length=200)
    customer_logo: str | None = Field(default=None, max_length=1_500_000)
    customer_logo_light: str | None = Field(default=None, max_length=1_500_000)
    battery_voltage_low: float | None = Field(default=None, ge=0, le=10)
    battery_voltage_full: float | None = Field(default=None, ge=0, le=10)
    site_title: str | None = Field(default=None, max_length=200)
    # Favicon kucuk olur (genelde <50KB), buyuk login_image icin 3MB yeterli marj.
    favicon: str | None = Field(default=None, max_length=300_000)
    login_image: str | None = Field(default=None, max_length=3_000_000)
