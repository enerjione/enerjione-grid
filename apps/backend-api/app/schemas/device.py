from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CommunicationStatus
from app.schemas.dnp3_extended import Dnp3ExtendedSettings, merge_dnp3_extended

#: Gecerli faz kodlari. Serbest metin kabul etmek, "A" / "L1" / "faz-a" gibi
#: birbirinden habersiz yazimlarin birikmesi ve faz gruplamasinin bolunmesi
#: demekti. Kucuk harf zorunlu degil — dogrulama oncesi normalize edilir.
PhaseCode = Literal["a", "b", "c"]


class DeviceScalarBase(BaseModel):
    code: str
    name: str
    # Seri numarasi — config dosya adinin birincil kaynagi. Kurulumda girilir;
    # cihaz baglaninca telemetriden otomatik guncellenir.
    serial_number: str | None = Field(default=None, max_length=20)
    description: str | None = None
    model: str = "horstmann_sn_2_0"
    installation_date: date | None = None
    gateway_code: str | None = None
    ip_address: str
    dnp3_outstation_port: int = Field(default=20001, ge=1, le=65535)
    dnp3_address: int = 1
    poll_interval_sec: int = 2
    timeout_ms: int = 3000
    retry_count: int = 2
    signal_profile: str = "horstmann_sn2_fixed"
    latitude: float
    longitude: float
    # IEC 60870-5-104 ASDU Common Address. NULL ise outbound target'in
    # default CA'si kullanilir. 0..65534 araliginda; 65535 broadcast icin
    # rezerve edildiginden cihaza atanmamasi tavsiye edilir.
    iec104_common_address: int | None = Field(default=None, ge=0, le=65534)
    # --- UNITE -> FAZ ESLEMESI (bu cihaza OZEL) ---
    # SN2'nin uc unitesi (master/sat01/sat02) hatta uc ayri faza kelepcelenir
    # ve hangisinin hangi fazda oldugu sahada kelepceyi takan kisinin
    # kararidir. NULL = "bu cihaz icin ozel bir sey yok, Proje Ayarlari'ndaki
    # kurulum konvansiyonunu kullan". Kismi doldurma serbest.
    phase_master: PhaseCode | None = None
    phase_sat01: PhaseCode | None = None
    phase_sat02: PhaseCode | None = None
    # Pole Master Kit setinde olcum yapan UCUNCU unite. SN2'de karsiligi yok
    # (orada ucuncu unite `master`'dir), bu yuzden yalnizca set kayitlarinda
    # doldurulur.
    phase_sat03: PhaseCode | None = None


class DeviceCreate(DeviceScalarBase):
    dnp3_extended: Dnp3ExtendedSettings | None = None
    # --- KIT: BAGLI SET SAYISI ---
    #
    # Yalnizca sanal set ureten modellerde (Horstmann Pole Master Kit)
    # anlamlidir ve ZORUNLUDUR: kaci takildigi sahada belli olur, tahmin
    # edilemez. Diger modellerde gonderilirse yok sayilir.
    #
    # Her set icin ayri bir `devices` satiri acilir; setler hatta ayri ayri
    # yerlestirilir ve arizalar dogru sete duser.
    satellite_set_count: int | None = Field(default=None, ge=1, le=3)


class DeviceUpdate(BaseModel):
    name: str | None = None
    serial_number: str | None = Field(default=None, max_length=20)
    description: str | None = None
    model: str | None = None
    installation_date: date | None = None
    gateway_code: str | None = None
    ip_address: str | None = None
    dnp3_outstation_port: int | None = Field(default=None, ge=1, le=65535)
    dnp3_address: int | None = None
    poll_interval_sec: int | None = None
    timeout_ms: int | None = None
    retry_count: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    dnp3_extended: Dnp3ExtendedSettings | None = None
    iec104_common_address: int | None = Field(default=None, ge=0, le=65534)
    # --- UNITE -> FAZ ESLEMESI (bu cihaza OZEL) ---
    # SN2'nin uc unitesi (master/sat01/sat02) hatta uc ayri faza kelepcelenir
    # ve hangisinin hangi fazda oldugu sahada kelepceyi takan kisinin
    # kararidir. NULL = "bu cihaz icin ozel bir sey yok, Proje Ayarlari'ndaki
    # kurulum konvansiyonunu kullan". Kismi doldurma serbest.
    phase_master: PhaseCode | None = None
    phase_sat01: PhaseCode | None = None
    phase_sat02: PhaseCode | None = None
    phase_sat03: PhaseCode | None = None
    # Setin uydu atamasi: uc fiziksel uydu numarasi (1..9), unite sirasiyla.
    # Yalnizca sanal set kayitlarinda anlamli. Varsayilan set sirasindan
    # turetilir (1-2-3 / 4-5-6 / 7-8-9) ama sahada baska baglanmis olabilir.
    subunit_satellites: list[int] | None = None
    # Kite bagli set sayisi. Artirilirsa eksik setler uretilir, azaltilirsa
    # fazla setler SILINIR (telemetrisi, alarmlari, arizalari ve hat
    # yerlesimiyle birlikte) — bu yuzden arayuz once acik uyari gosterir.
    satellite_set_count: int | None = Field(default=None, ge=1, le=3)


class DeviceRead(DeviceScalarBase):
    id: int
    battery_percent: float
    communication_status: CommunicationStatus
    alarm_active: bool
    last_update_at: datetime | None
    dnp3_extended: Dnp3ExtendedSettings
    # --- KIT / SET BAGI (salt okunur) ---
    #: Sanal set kaydinin bagli oldugu fiziksel kit. NULL = fiziksel cihaz.
    parent_device_id: int | None = None
    #: Setin kit uzerindeki sirasi (1..3). Fiziksel kayitlarda NULL.
    subunit_index: int | None = None
    #: Setin uydu atamasi (COZULMUS): kayitli deger yoksa set sirasindan
    #: turetilmis hali doner, yani arayuz her zaman gercek atamayi gorur.
    subunit_satellites: list[int] | None = None
    #: Fiziksel kitin kodu — arayuz "PMK-001 / Set 2" diyebilsin diye.
    parent_device_code: str | None = None
    #: Kite bagli set sayisi (yalnizca kit satirlarinda dolu).
    satellite_set_count: int | None = None

    @field_validator("dnp3_extended", mode="before")
    @classmethod
    def _merge_dnp3_extended(cls, v: object) -> Dnp3ExtendedSettings:
        if v is None or v == {}:
            return merge_dnp3_extended(None)
        if isinstance(v, Dnp3ExtendedSettings):
            return v
        if isinstance(v, dict):
            return merge_dnp3_extended(v)
        return merge_dnp3_extended(None)

    model_config = ConfigDict(from_attributes=True)


class DeviceCommandRequest(BaseModel):
    """Cihaza DNP3 CROB komutu gonderme istegi.

    `command` = SignalCatalog'daki binary_output sinyalinin slug'i (key'in
    `master.` sonrasi). Backend bunu katalogdan `dnp3_index`'e cevirir; ham
    index kabul edilmez (allowlist).

    LATCH_ON kullanilir (SN2 PULSE desteklemez); LATCH'te on/off time anlamsiz,
    default 0. count=1 standart CROB.
    """

    command: str = Field(min_length=1, max_length=80)
    count: int = Field(default=1, ge=1, le=10)
    on_time_ms: int = Field(default=0, ge=0, le=60000)
    off_time_ms: int = Field(default=0, ge=0, le=60000)


class DeviceCommandQueued(BaseModel):
    """Komut kuyruga alindi yaniti (anlik sonuc DEGIL).

    Gateway NAT arkasinda; komut config-poll ile ~config_refresh_sec icinde
    iletilir. Gercek sonuc (ok/failed) sonra `GET /devices/{code}/commands`
    ile takip edilir.
    """

    id: int
    status: str  # pending
    command: str
    dnp3_index: int


class DeviceCommandRow(BaseModel):
    """Komut kaydi + durum (UI takip listesi icin)."""

    id: int
    device_code: str
    command: str
    dnp3_index: int
    status: str  # pending | sent | ok | failed | expired | cancelled
    result_status: str | None = None
    result_error: str | None = None
    actor_username: str | None = None  # komutu gonderen kullanici (UI "kim")
    created_at: datetime
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
