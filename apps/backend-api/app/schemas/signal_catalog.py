from typing import Literal

from pydantic import BaseModel, Field

from app.models.signal_catalog import OLU_BANT_UST_SINIR


SignalDataType = Literal[
    "analog",
    "binary",
    "counter",
    "string",
    # binary_output (G10) = DNP3 CROB komut kanali; yayinlanmaz, cihaz komutu
    # (Trigger Download, Reset...) icin dnp3_index adresi tutar.
    "binary_output",
    "analog_output",
]

#: Sinyal kaynagi (anahtarin nokta oncesi parcasi).
#:
#: Horstmann SN 2.0 uc unite kullanir (master + iki uydu). Horstmann Pole
#: Master Kit'in FIZIKSEL kaydi DOKUZ uydu tasir, sanal setleri ise ucer.
#:
#: NEDEN BURASI KRITIK: bu tip `SignalCatalogRead.source` alaninda. Dar
#: birakildiginda `sat03`+ satirlari yanit dogrulamasini dusuruyor ve
#: `GET /signals` TUM katalog icin 500 doner — yani tek bir modelin fazladan
#: uydusu, arayuzde HICBIR sinyalin gorunmemesine yol acar. Belirti
#: ("Henuz sinyal tanimli degil") sebebe hic benzemez.
SignalSource = Literal[
    "master",
    "sat01",
    "sat02",
    "sat03",
    "sat04",
    "sat05",
    "sat06",
    "sat07",
    "sat08",
    "sat09",
]


class SignalCatalogBase(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    model: str = Field(default="horstmann_sn_2_0", min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    unit: str | None = None
    description: str | None = None
    source: SignalSource = "master"
    dnp3_class: str = "Class 1"
    data_type: SignalDataType = "analog"
    dnp3_object_group: int = 30
    dnp3_index: int = 0
    scale: float = 1.0
    offset: float = 0.0
    supports_alarm: bool = False
    is_active: bool = True
    display_order: int = 0
    # --- Historian (arsiv) politikasi ------------------------------------
    # Alanlar DB'de migration 0032'den beri var ve `historian_policy` onlara
    # gore karar veriyordu; sozlesmede olmadiklari icin arayuz ne okuyabiliyor
    # ne yazabiliyordu. Yani sistem yaziyi kisiyor ama KIMSE gormuyordu.
    historize: bool = True
    # MUTLAK, sinyalin kendi biriminde. 0 = suzgec kapali. YALNIZCA analog
    # tipte uygulanir (bkz. historian_policy.OLU_BANT_TIPLERI).
    # `le`: ust sinir olmadan `Infinity` gecerdi (`inf >= 0` dogru) — bkz.
    # `OLU_BANT_UST_SINIR`.
    historize_deadband: float = Field(default=0.0, ge=0.0, le=OLU_BANT_UST_SINIR)
    # IEC 60870-5-104 adresleme — `string` ve desteklenmeyen tiplerde NULL.
    iec104_type_id: int | None = Field(default=None, ge=0, le=255)
    # Yeni model: sinyalin mutlak IOA'si. Cihaz bazli ayrim ASDU CA ile yapilir.
    iec104_ioa: int | None = Field(default=None, ge=0, le=16_777_215)
    # Geri uyumluluk: eski deploylarda offset'ti; yeni alan dolu degilse mutlak
    # IOA olarak yorumlanir. Yeni kayitlar `iec104_ioa` doldurmali.
    iec104_ioa_offset: int | None = Field(default=None, ge=0, le=16_777_215)
    # IEC 104 yayini sinyal bazinda gecici kapatma. Default True (yayinla).
    iec104_enabled: bool = True
    # CP56Time2a zaman etiketli ASDU tipinde mi yayinlansin? Default False
    # (analog sinyaller icin gereksiz yuk). Dijital sinyallere migration ile
    # otomatik True yapilir; kullanici sinyal duzenleme formunda secebilir.
    iec104_with_timestamp: bool = False
    # Modbus outbound (Holding=3, Input=4, Coil=1, Discrete=2)
    modbus_function: int | None = Field(default=None, ge=1, le=255)
    modbus_address: int | None = Field(default=None, ge=0, le=65_535)
    # MQTT outbound topic suffix'i (cihaz koduyla birleştirilir).
    mqtt_topic: str | None = Field(default=None, max_length=200)


class SignalCatalogCreate(SignalCatalogBase):
    pass


class SignalCatalogUpdate(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=80)
    label: str | None = None
    unit: str | None = None
    description: str | None = None
    source: SignalSource | None = None
    dnp3_class: str | None = None
    data_type: SignalDataType | None = None
    dnp3_object_group: int | None = None
    dnp3_index: int | None = None
    scale: float | None = None
    offset: float | None = None
    supports_alarm: bool | None = None
    is_active: bool | None = None
    display_order: int | None = None
    historize: bool | None = None
    # DIKKAT: "olu bandi temizle" 0.0 GONDERMELI, null degil. Router
    # `exclude_none=True` uyguladigi icin null "degistirme" anlamina gelir.
    historize_deadband: float | None = Field(default=None, ge=0.0, le=OLU_BANT_UST_SINIR)
    # Ariza gecisi tasiyan bir sinyalin (binary / binary_output) arsivini
    # KAPATMAK icin acik onay. Kural servis katmaninda; arayuzde durmasi
    # yetmezdi, elle atilan bir istek onu atlardi.
    confirm_fault_signals: bool | None = None
    iec104_type_id: int | None = Field(default=None, ge=0, le=255)
    iec104_ioa: int | None = Field(default=None, ge=0, le=16_777_215)
    iec104_ioa_offset: int | None = Field(default=None, ge=0, le=16_777_215)
    iec104_enabled: bool | None = None
    iec104_with_timestamp: bool | None = None
    modbus_function: int | None = Field(default=None, ge=1, le=255)
    modbus_address: int | None = Field(default=None, ge=0, le=65_535)
    mqtt_topic: str | None = Field(default=None, max_length=200)


class SignalCatalogRead(SignalCatalogBase):
    id: int
    # DIS SISTEME yayinlanabilir mi (bkz. SignalCatalog.outbound_eligible).
    # Turetilmis alan: katalogta duran ama o cihaz kaydinda HIC saklanmayan
    # noktalar (Pole Master Kit'in uydu satirlari) SCADA adres tablosuna
    # girmemeli — girerlerse hicbir zaman veri gelmeyen adresler bildirilir.
    outbound_eligible: bool = True
    # Operatorun elle degistirdigi alanlarin adlari. Arayuz bu alanlari
    # "fabrika degerinden farkli" olarak isaretleyebilir; ayrica acilistaki
    # seed'in neden o alanlari guncellemedigini aciklar.
    user_overrides: list[str] | None = None

    class Config:
        from_attributes = True


class SignalHistorianBulkUpdate(BaseModel):
    """Filtreye gore secilmis sinyallerin arsiv ayarini tek istekte degistirir.

    193 sinyali tek tek PATCH etmek 193 istek ve 193 denetim kaydi demekti;
    o hacimde denetim kaydi OKUNAMAZ hale gelir ve ekranin varlik sebebi
    (yazma yukunu bilincli kismak) islkenceye donerdi.
    """

    signal_keys: list[str] = Field(min_length=1, max_length=5_000)
    historize: bool | None = None
    historize_deadband: float | None = Field(default=None, ge=0.0, le=OLU_BANT_UST_SINIR)
    #: Ariza gecisi tasiyan sinyaller secimde varsa ve arsiv KAPATILIYORSA
    #: zorunlu. Arayuz once uyarir, kullanici onaylarsa bunu true gonderir.
    confirm_fault_signals: bool = False


class SignalHistorianBulkResult(BaseModel):
    updated: int
    unchanged: int
    #: Olu bant istendi ama tip analog olmadigi icin UYGULANMAYAN sinyaller.
    #: Sessizce yutulsaydi kullanici calismayan bir ayari yapilmis sanirdi.
    skipped_deadband: list[str] = []
    not_found: list[str] = []


class SignalLiveValue(BaseModel):
    """Canli degerler ekranina donulen tek satir."""

    signal_key: str
    signal_label: str
    unit: str | None = None
    source: str = "master"
    data_type: str | None = None
    device_id: int
    device_code: str
    device_name: str
    value: float | None = None
    # DNP3 Group 110 (Octet String) sinyallerinde numeric value None olur ve
    # gercek metin bu alanda doner. Frontend data_type='string' satirlarda
    # value_string'i gosterir.
    value_string: str | None = None
    quality: str | None = None
    source_timestamp: str | None = None
    # --- CIHAZ SAATI DURUMU ----------------------------------------------
    # `quality`den AYRI alanlar. Saat kaymasi olcumu gecersiz kilmaz; ikisini
    # tek alanda birlestirmek saati bozuk bir cihazi "kalitesiz veri" gibi
    # gosterip alarmlarini bastirirdi.
    #
    # timestamp_quality: "synchronized" | "unsynchronized" | "invalid" | None.
    # None = bilgi yok (0.4.x gateway alani hic gondermiyor) — UI bu durumda
    # HICBIR uyari gostermez, cunku "bilmiyoruz" ile "saat bozuk" ayni sey
    # degildir.
    timestamp_quality: str | None = None
    # Cihazin kendi bildirdigi olay zamani. `source_timestamp` (gateway saati)
    # ile yan yana gosterilince operator kaymanin YONUNU ve BUYUKLUGUNU
    # gorebilir; ham deger saklandigi icin "2000-01-01" gibi bir damga RTC
    # pilinin bittigini dogrudan soyler.
    device_event_at: str | None = None
