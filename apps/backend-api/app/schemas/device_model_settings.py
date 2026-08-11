from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Esiklerin makul fiziksel araligi. Lityum hucre voltajidir; 0-10 V disina
#: cikan bir deger her zaman yazim hatasidir (orn. yuzde girilmis olmasi).
#: Serbest birakmak, "371" yazan bir kullanicinin tum cihazlari kalici
#: olarak %0 batarya gostermesi demekti.
_VOLT_MIN = 0.0
_VOLT_MAX = 10.0


class DeviceModelSettingsUpdate(BaseModel):
    """Model ayarini yaz. NULL = "ust katmani kullan" (ayari temizle)."""

    battery_voltage_low: float | None = Field(default=None, ge=_VOLT_MIN, le=_VOLT_MAX)
    battery_voltage_full: float | None = Field(default=None, ge=_VOLT_MIN, le=_VOLT_MAX)

    @model_validator(mode="after")
    def _dolu_esik_tutarli(self) -> "DeviceModelSettingsUpdate":
        # Ikisi de girildiyse full > low OLMALI: aksi halde yuzde hesabi
        # negatif ya da sifira bolme olur. Tek biri girildiginde digeri ust
        # katmandan gelecegi icin burada karsilastirilamaz; o durum
        # `battery_thresholds` icinde yakalanip koda geri duser.
        if self.battery_voltage_low is not None and self.battery_voltage_full is not None:
            if self.battery_voltage_full <= self.battery_voltage_low:
                raise ValueError(
                    "Dolu voltaji bos voltajindan buyuk olmali."
                )
        return self


class DeviceModelSettingsRead(BaseModel):
    """Modelin ayari: KAYITLI degerler + zincir sonrasi COZULMUS degerler.

    Arayuz ikisini de gorur: alan bos gosterilir ama altinda "su an sunu
    kullaniyor" yazabilir. Yalnizca cozulmus deger donseydi kullanici kendi
    girdigi degerle miras alinan degeri ayirt edemezdi.
    """

    model: str
    label: str | None = None
    #: Bu model icin ACIKCA girilmis degerler (NULL = girilmemis).
    battery_voltage_low: float | None = None
    battery_voltage_full: float | None = None
    #: Zincir uygulandiktan sonra GERCEKTEN kullanilan degerler.
    resolved_battery_voltage_low: float
    resolved_battery_voltage_full: float
    #: Bu modelde bataryayi tasiyan uniteler (bilgi amacli).
    battery_units: list[str] = []
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
