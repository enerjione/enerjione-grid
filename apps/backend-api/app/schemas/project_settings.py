from typing import Literal

from pydantic import BaseModel, Field

# Toast bildiriminin ekrandaki kosesi. Sira/degerler frontend ile SOZLESMEDIR:
# `shared/types.ts` ToastPosition ve `styles.css` .toast-container--<deger>
# sinifi birebir ayni metni kullanir.
ToastPosition = Literal["bottom-right", "bottom-left", "top-right", "top-left"]

#: Faz kodu — cihaz semasiyla AYNI kume (tek tanim, ayrisma olmasin).
from app.schemas.device import PhaseCode  # noqa: E402


class ProjectSettingsRead(BaseModel):
    """Login ekrani + header tarafindan auth-siz okunabilir; private alan yok.

    `toast_position` / `toast_muted` de bilincli olarak buradadir: kimlik
    dogrulamasiz bir cagirana yalnizca "bu kurulumda toast'lar su kosede ve
    susturulmus" bilgisini verirler. PII yok, altyapi/ag bilgisi yok, guvenlik
    kontrolu degil — alarm uretimi, e-posta/SMS/Telegram/push yollari bu
    ayarlardan ETKILENMEZ; yalnizca tarayicidaki gecici baloncuk etkilenir.
    Yazma yolu (PUT) public DEGIL, dolayisiyla anonim biri operatoru
    susturamaz.
    """

    project_name: str | None = None
    customer_name: str | None = None
    customer_logo: str | None = None
    customer_logo_light: str | None = None
    battery_voltage_low: float | None = None
    battery_voltage_full: float | None = None
    # UYDU (sat01/sat02/sat03) hucreleri master ile ayni aralikta calismaz.
    # NULL = uydular master esigini kullanir (mevcut davranis).
    battery_voltage_low_sat: float | None = None
    battery_voltage_full_sat: float | None = None
    site_title: str | None = None
    favicon: str | None = None
    login_image: str | None = None
    # NULL = "bottom-right" (mevcut davranis).
    toast_position: ToastPosition | None = None
    # NULL/False = bildirimler gorunur (mevcut davranis).
    toast_muted: bool | None = None
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
    battery_voltage_low_sat: float | None = Field(default=None, ge=0, le=10)
    battery_voltage_full_sat: float | None = Field(default=None, ge=0, le=10)
    site_title: str | None = Field(default=None, max_length=200)
    # Favicon kucuk olur (genelde <50KB), buyuk login_image icin 3MB yeterli marj.
    favicon: str | None = Field(default=None, max_length=300_000)
    login_image: str | None = Field(default=None, max_length=3_000_000)
    # Serbest metin DEGIL: gecersiz bir deger CSS'te karsiligi olmayan bir
    # sinif uretir ve toast ekranin disinda kalabilir. Literal ile kapiyoruz.
    toast_position: ToastPosition | None = Field(default=None)
    # Yalnizca KENDILIGINDEN gelen bildirimleri susturur; kullanici eyleminin
    # sonucu olan toast'lar her zaman gosterilir (bkz. model yorumu).
    toast_muted: bool | None = Field(default=None)
    # Kurulumun genel faz konvansiyonu. Serbest metin DEGIL: "A" / "L1" /
    # "faz-a" gibi birbirinden habersiz yazimlar faz gruplamasini bolerdi.
    phase_master: PhaseCode | None = Field(default=None)
    phase_sat01: PhaseCode | None = Field(default=None)
    phase_sat02: PhaseCode | None = Field(default=None)


class PhaseMapRead(BaseModel):
    """Unite -> faz eslemesi (kurulumun genel konvansiyonu).

    NEDEN `ProjectSettingsRead` ICINDE DEGIL: `GET /project-settings`
    bilincli olarak HALKA ACIK — login ekrani ve header oturum yokken
    logoyu ceker. Oraya eklenen her alan anonim bir cagirana acilir
    (bkz. tests/test_toast_bildirim_ayarlari.PUBLIC_ALANLAR).

    Faz eslemesi marka degil SEBEKE YAPILANDIRMASIDIR ve login ekraninin
    ona ihtiyaci yok; bu yuzden ayri ve KIMLIK DOGRULAMALI bir uctan
    servis edilir. Kucuk bir bilgi olmasi, gereksiz yere acmayi hakli
    kilmaz.
    """

    phase_master: PhaseCode | None = None
    phase_sat01: PhaseCode | None = None
    phase_sat02: PhaseCode | None = None
