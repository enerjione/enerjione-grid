from pydantic import BaseModel, Field


class ProjectSettingsRead(BaseModel):
    """Login ekrani + header tarafindan auth-siz okunabilir; private alan yok."""

    project_name: str | None = None
    customer_name: str | None = None
    customer_logo: str | None = None
    customer_logo_light: str | None = None

    class Config:
        from_attributes = True


class ProjectSettingsUpdate(BaseModel):
    """PUT ile gonderilen alanlardan sadece set olanlar uygulanir."""

    project_name: str | None = Field(default=None, max_length=200)
    customer_name: str | None = Field(default=None, max_length=200)
    # Boyut sinirini kabaca buyuk PNG/SVG'leri kapsayacak sekilde tutuyoruz
    # (~750 KB base64 ~ 1 MB orig). Frontend buyuk dosya secerse 413 doner.
    customer_logo: str | None = Field(default=None, max_length=1_500_000)
    customer_logo_light: str | None = Field(default=None, max_length=1_500_000)
