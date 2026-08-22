import ipaddress
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CommunicationStatus
from app.schemas.dnp3_extended import Dnp3ExtendedSettings, merge_dnp3_extended

#: Gecerli faz kodlari. Serbest metin kabul etmek, "A" / "L1" / "faz-a" gibi
#: birbirinden habersiz yazimlarin birikmesi ve faz gruplamasinin bolunmesi
#: demekti. Kucuk harf zorunlu degil — dogrulama oncesi normalize edilir.
PhaseCode = Literal["a", "b", "c"]


def dogrula_ip(value: str | None) -> str | None:
    """Cihazin DNP3 uc noktasi — GECERLI bir IPv4 olmak ZORUNDA.

    ONCEDEN HICBIR DOGRULAMA YOKTU: alan duz `str` idi ve "asdf", "192.168",
    "10.0.0.256" gibi her sey kaydediliyordu. Hata sahada, cihaz eklendikten
    gunler sonra ortaya cikiyordu: gateway o adrese baglanmayi deniyor,
    baglanamiyor ve cihaz "haberlesme yok" olarak gorunuyordu. Yani yazim
    hatasi bir ARIZA gibi teshis ediliyor, kimse alan degerine bakmiyordu.

    HOSTNAME KABUL EDILMEZ: gateway bu degeri dogrudan DNP3 TCP baglantisinda
    kullaniyor ve saha aglarinda (APN, izole VLAN) DNS cogu zaman yok. Ad
    kabul etmek, cozulemedigi anda yine "haberlesme yok" uretirdi.

    IPv6 de KABUL EDILMEZ: DNP3 outstation tarafi ve `_require_unique_endpoint`
    esitlik karsilastirmasi bastan sona IPv4 varsayiyor.

    `0.0.0.0` ve multicast REDDEDILIR — bir cihazin adresi olamazlar. Loopback
    ise BILEREK SERBEST: ayni makinede kosan simulatore baglanmak mesru bir
    kurulum (saha oncesi dogrulama boyle yapiliyor).
    """
    if value is None:
        return None
    kirpik = str(value).strip()
    try:
        addr = ipaddress.ip_address(kirpik)
    except ValueError as exc:
        raise ValueError(
            f"Gecersiz IP adresi: {kirpik!r}. Ornek: 192.168.1.50"
        ) from exc
    if addr.version != 4:
        raise ValueError("Yalnizca IPv4 adresi kabul edilir")
    if addr.is_unspecified or addr.is_multicast or addr.is_reserved:
        raise ValueError(f"{addr} bir cihaz adresi olamaz")
    # Normalize edilmis hali yazilir: "010.0.0.1" ve "10.0.0.1" ayni cihazi
    # gosterir ama metin olarak farklidir; tekil uc nokta kontrolu
    # (`_require_unique_endpoint`) esitlige baktigi icin ikisi ayri sayilirdi.
    return str(addr)


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

    @field_validator("ip_address")
    @classmethod
    def _ip_gecerli(cls, v: str) -> str:
        return dogrula_ip(v) or v


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

    @field_validator("ip_address")
    @classmethod
    def _ip_gecerli(cls, v: str | None) -> str | None:
        return dogrula_ip(v)


class DeviceRuntimeHealthRead(BaseModel):
    """Gateway'in bildirdigi CALISMA-ZAMANI sagligi — OKUMA projeksiyonu.

    Sozlesme: `device_health_v1` (gateway 1.15.0+). Kanonik kaynak:
    `infra/gateway-contract/v1.15.1.json`.

    Kaynak `device_runtime_health` SATIRIDIR, wire govdesi degil: alim
    tarafindaki tek adaptor (`device_runtime_health_service._wire_to_model`)
    zaten wire'i modele cevirmis ve `connection_state`i sozlesme kumesine
    ZORLAMISTIR. Bu sema o kolonlari OLDUGU GIBI yayar; ikinci bir yorumlama
    katmani DEGILDIR. Ikinci bir normalizasyon eklenirse iki otorite olur ve
    hangisinin kazandigi kimsenin bakmadigi bir yerde belirlenir.

    `boot_id` / `sequence` / `snapshot_id` / `snapshot_batch_index` /
    `gateway_instance_id` BILEREK DISARIDA: bunlar bayat-yazma ve uzlastirma
    defteridir, cihazin durumu degil. `gateway_instance_id` ayrica gateway'in
    kalici ic kimligidir ve `/public` ucundan disari sizmamali.

    Sema ile model arasindaki kayma testle kilitli
    (`tests/test_device_runtime_health_okuma.py`): modele yeni bir sozlesme
    kolonu eklenip burasi unutulursa test kirilir — alan sessizce saklanip
    hic gorunmemis olmaz.
    """

    #: Satirin birincil anahtari; `devices.code` ile ayni deger.
    device_code: str
    #: Gozlemi bildiren gateway.
    gateway_code: str

    # ---- sozlesme bolum 4 --------------------------------------------------
    #: online | smart_idle | recovering | lost | listener_error | unknown
    #: BAGLANTI KARARININ TEK KAYNAGI. `late` BURADA OLAMAZ — gecikme
    #: `report_late` bayragiyla tasinir (bkz. alim tarafi).
    connection_state: str
    connected: bool
    #: Uyuyan (Smart) cihazda `False` — SAGLIKLI, ariza degil.
    reachable: bool

    configured_session_policy: str | None = None
    effective_session_policy: str | None = None
    operation_mode: str | None = None

    dial_in_interval_min: int | None = None
    #: Unix epoch (saniye, UTC). `None` = HIC OLMADI. 0'A CEVRILMEZ: sifir
    #: 1970 demektir ve panelde gecerli bir tarih gibi gorunur.
    next_expected_report_epoch: float | None = None
    report_overdue_sec: float | None = None
    #: UYARI BAYRAGI — DURUM DEGIL.
    report_late: bool

    last_valid_contact_epoch: float | None = None
    last_frame_epoch: float | None = None

    #: SALT TESHIS. `unreachable` NORMALDIR (ICMP saha aglarinda engelli).
    ip_probe_status: str | None = None
    tcp_probe_status: str | None = None
    last_probe_epoch: float | None = None

    ip_endpoint_type: str | None = None

    # ----- Gateway 1.15.1: CIHAZ RTC SAGLIGI + OTURUM KANITI --------------
    #
    # HEPSI OPSIYONEL. 1.15.0 gateway'i bu alanlari gondermez ve `None`
    # kalirlar; arayuz o durumda hicbir sey IDDIA ETMEZ.

    #: `unknown` | `ok` | `invalid` | `need_time`.
    #:
    #: SALT TESHIS — `connection_state`i ETKILEMEZ. Saati bozuk bir cihaz
    #: `online` olabilir, olcum gonderir ve komut kabul eder. Etkilenen tek
    #: sey CIHAZIN KENDI OLAY DAMGASINA duyulan guvendir. `invalid` gorup
    #: cihazi kopuk saymak saglikli filoyu arizali gosterir.
    device_clock_status: str | None = None

    #: `cihaz_saati - gateway_saati` (saniye, ISARETLI).
    #: Pozitif = cihaz ileri, negatif = geri. `0.0` GECERLI bir degerdir.
    device_clock_offset_sec: float | None = None

    #: Cihazin KENDI bildirdigi son zaman damgasi (unix epoch).
    last_device_time_epoch: float | None = None

    #: IIN1.4 (NEED_TIME). UC DURUMLU: `True` = saat istiyor, `False` =
    #: istemiyor, `None` = HIC IIN gorulmedi. `False`a cevrilmemeli —
    #: saati yanlis AMA saat istemeyen cihaz kendiliginden DUZELMEZ.
    need_time_iin: bool | None = None

    #: Acik DNP3 oturumunun basladigi an (unix epoch).
    #: OTURUM KAPALIYKEN `None` — uyuyan cihazda NORMALDIR, hata degil.
    session_started_epoch: float | None = None

    #: BACKEND saati — "ne zaman haber aldik". TAZELIK KARARI YALNIZCA
    #: BUNDAN VERILIR; arayuz bu damgaya bakip gozlemi bayat ilan eder
    #: (`shared/deviceRuntimeState.ts`, RUNTIME_STALE_AFTER_MS).
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _utc_farkindaligini_zorla(cls, v: datetime) -> datetime:
        """Naive damgayi UTC kabul et — SESSIZ SAAT KAYMASI KORUMASI.

        Kolon `DateTime(timezone=True)` ve yazan taraf her zaman UTC-aware
        yaziyor; ama bazi surucler (SQLite) offset'i DUSURUR ve deger naive
        geri gelir. Naive bir damga ISO-8601'e offset'siz serialize olur;
        tarayici onu YEREL saat sanar. UTC+3'te bu, taze bir gozlemi 3 saat
        eski gostermek demekti: her cihaz surekli "bayat" sayilir ve arayuz
        kalici olarak eski davranisa duserdi — yani kanal calisirken
        gorunmez olurdu.
        """
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    model_config = ConfigDict(from_attributes=True)


class DeviceRead(DeviceScalarBase):
    id: int
    #: NULL = cihaz henuz batarya bildirmedi. Arayuz bunu "—" gosterir;
    #: eskiden varsayilan 100.0 idi ve hic bildirmeyen cihaz DOLU gorunuyordu.
    battery_percent: float | None
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

    # --- CALISMA-ZAMANI SAGLIGI (salt okunur, `device_health_v1`) ---
    #: Gateway'in bu cihaz icin bildirdigi ANLIK gozlem.
    #:
    #: `None` = gateway 1.15.0 ONCESI ya da bu cihaz icin henuz rapor
    #: gelmedi. O zaman arayuz ESKI davranisa duser (`communication_status`);
    #: burada uydurma bir `smart_idle`/`recovering` URETILMEZ — o durumlarin
    #: varligini ancak gateway bilebilir.
    #:
    #: Kolon DEGIL, turetilmis alandir: ayri bir tabloda yasar ve okuma
    #: tarafinda TEK toplu sorguyla baglanir (`device_kit_service.annotate`
    #: -> `device_runtime_health_service.saglik_haritasi`). Varsayilanin
    #: `None` olmasi zorunlu: `annotate`dan gecmeyen uclar (or.
    #: `/internal/devices`) bu alani hic tasimaz ve sema patlamamali.
    runtime_health: DeviceRuntimeHealthRead | None = None

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
    # `strict=True` — SESSIZ TIP DONUSUMU YOK.
    #
    # Lax modda `"1"` -> 1, `True` -> 1 ve `1.0` -> 1 donusuyordu. Bu, HTTP
    # istemcisinin tip hatasini "duzeltip" fiziksel komut uretmek demek:
    # `count=True` gonderen bir cagiran 1 tekrar ISTEMEDI, bir bayrak
    # gonderdigini saniyordu. Dogru tepki tahmin etmek degil reddetmektir.
    # Aralik sinirlari `device_command_service` ile AYNI olmak zorunda
    # (bkz. test_f6b_command_intent: sema/servis kaymasi testi).
    count: int = Field(default=1, ge=1, le=10, strict=True)
    on_time_ms: int = Field(default=0, ge=0, le=60000, strict=True)
    off_time_ms: int = Field(default=0, ge=0, le=60000, strict=True)


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
