from datetime import datetime

from pydantic import BaseModel


class GatewayCreate(BaseModel):
    code: str
    name: str
    host: str
    listen_port: int
    upstream_url: str
    batch_interval_sec: int = 5
    max_devices: int = 200
    device_code_prefix: str | None = None
    token: str
    is_active: bool = True
    control_host: str = "127.0.0.1"
    control_port: int = 0
    # Bu gateway icin acilacak initiating port sayisi (= max initiating cihaz).
    # Default 0: yalniz listening cihazlar (gateway cihaza outbound baglanir).
    # Initiating cihaz icin kullanici frontend'den artirir.
    initiating_port_count: int = 0


class GatewayUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    listen_port: int | None = None
    upstream_url: str | None = None
    batch_interval_sec: int | None = None
    max_devices: int | None = None
    device_code_prefix: str | None = None
    token: str | None = None
    is_active: bool | None = None
    control_host: str | None = None
    control_port: int | None = None
    initiating_port_count: int | None = None


class GatewayRead(BaseModel):
    id: int
    code: str
    name: str
    host: str
    listen_port: int
    upstream_url: str
    batch_interval_sec: int
    max_devices: int
    device_code_prefix: str | None = None
    token: str
    is_active: bool
    last_seen_at: datetime | None = None
    control_host: str = "127.0.0.1"
    control_port: int = 0
    # Initiating mode TCP server portu icin host tarafi baslangici. Default
    # 20100; ek gateway eklenince otomatik 21100, 22100, ... olarak atanir.
    initiating_port_base: int = 20100
    # Bu gateway icin acilacak initiating port sayisi (= max initiating cihaz).
    initiating_port_count: int = 50

    class Config:
        from_attributes = True


class GatewayConfigDevice(BaseModel):
    code: str
    name: str
    ip_address: str
    dnp3_address: int
    dnp3_tcp_port: int
    master_address: int | None = None
    # Initiating (Direct/Initiating End Point): cihaz master'a outbound baglanir;
    # gateway TCP server modunda dinler. Listening: cihaz dinler, gateway client
    # olarak baglanir (default).
    ip_endpoint_type: str = "listening"
    # Initiating mode'da gateway'in dinleyecegi port. Backend cihaz basina
    # otomatik atar (20100..20700). Listening mode'da kullanilmaz.
    master_ip_port: int | None = None
    poll_interval_sec: int
    timeout_ms: int
    retry_count: int
    signal_profile: str


class GatewayConfigSignal(BaseModel):
    """Standart sinyal listesi - tum cihazlar icin ortak DNP3 adresleri.

    `source` alani Horstmann SN2 icin zorunlu: alarmin hangi kaynagi
    (master / sat01 / sat02) uzerinden geldigi collector'da ayirt edilir.
    """

    key: str
    label: str
    unit: str | None = None
    source: str = "master"
    dnp3_class: str = "Class 1"
    data_type: str
    dnp3_object_group: int
    dnp3_index: int
    scale: float
    offset: float
    supports_alarm: bool


class GatewayConfigCommand(BaseModel):
    """Cihaza gonderilecek bekleyen DNP3 CROB komutu.

    Gateway NAT arkasinda; komut config-poll ile iletilir. Gateway bu listedeki
    her komut icin `reader.operate_device(device, index, ...)` cagirir ve sonucu
    `POST /gateways/{code}/command-results` ile geri bildirir. `id` ile idempotent
    (ayni id tekrar gelirse gateway tekrar calistirmaz).
    """

    id: int
    device_code: str
    command: str
    dnp3_index: int
    op_type: str = "latch_on"  # Horstmann SN2 PULSE desteklemez (latch zorunlu)
    count: int = 1
    on_time_ms: int = 100
    off_time_ms: int = 100


class CommandResultItem(BaseModel):
    """Gateway'in bir komut icin bildirdigi sonuc.

    Gateway config'ten cektigi her pending komutu calistirdiktan sonra bunu
    `POST /gateways/{code}/command-results` batch'inde geri gonderir.

    DNP3 alanlari (SELECT-before-OPERATE + LATCH_ON): dnp3_status gercek
    per-point CommandStatus (SUCCESS/NO_SELECT/NOT_SUPPORTED/...), dnp3_state
    SBO fazi (SELECT_SUCCESS/SELECT_FAIL/OPERATE_FAIL/...), dnp3_task transport
    (TaskCompletion). Backend bunlari result_status'a birlestirip UI'da gosterir.
    """

    id: int
    ok: bool
    status: str = "unknown"
    error: str | None = None
    dnp3_status: str | None = None
    dnp3_state: str | None = None
    dnp3_task: str | None = None
    control: str | None = None
    duration_ms: int | None = None


class GatewayConfigResponse(BaseModel):
    gateway_code: str
    gateway_name: str
    batch_interval_sec: int
    max_devices: int
    is_active: bool
    devices: list[GatewayConfigDevice]
    signals: list[GatewayConfigSignal]
    config_version: str
    # Operator/SCADA tarafindan tetiklenen "tum cihazlara sorgu at" sayaci.
    # Gateway her config refresh'te bu degeri okur; en son gordugu degerden
    # buyukse reader.refresh_all_devices() cagirir (Class 0+1+2+3 integrity
    # poll). Default 0 (hic tetiklenmedi).
    refresh_nonce: int = 0
    # Config degisikligi sayaci — gateway hafif komut-poll'de bunu okuyup
    # config'i erken ceker. Geriye uyum: eski gateway bu alani yok sayar.
    config_nonce: int = 0
    # DEPRECATED: komut artik AYRI /pending endpoint'inden gelir (config'ten
    # ayrildi). Geriye uyum icin alan duruyor ama HER ZAMAN BOS doner; eski
    # gateway'ler komutu buradan cekemez, yeni komut-poll kanalini kullanmali.
    pending_commands: list[GatewayConfigCommand] = []


class GatewayPendingResponse(BaseModel):
    """Hafif komut-poll yaniti — GET /gateways/{code}/pending.

    Config'in agir parcalarini (device/signal listesi) TASIMAZ; sadece bekleyen
    komutlar + iki nonce. Gateway bunu 1sn'de bir ceker (komut anlik gelsin) ve
    config_nonce/refresh_nonce degistiyse ilgili tetigi calistirir.
    """

    gateway_code: str
    is_active: bool
    # Bekleyen cihaz komutlari (status='pending'). Gateway CROB gonderir, sonucu
    # command-results ile bildirir. id ile idempotent (command_ledger).
    commands: list[GatewayConfigCommand] = []
    # Config degisti mi (gateway artmissa config'i hemen ceker).
    config_nonce: int = 0
    # "Tum cihazlara integrity poll at" tetigi (mevcut refresh-all mekanizmasi).
    refresh_nonce: int = 0
    # Gateway saglik ozetini KAC SANIYEDE BIR gondersin. Bu uc 1 Hz cagriliyor;
    # sagligi her cagrida gondermek hem bant hem yazma israfi olurdu. Sikligi
    # BACKEND soyluyor ki filo genelinde tek yerden ayarlanabilsin.
    # 0 = saglik gonderme (kill switch).
    #
    # DIKKAT: bu bir kill switch AMA kesilen kanalin kendisinden donuyor —
    # yani baslik nginx tavanini asip istek hic ulasmiyorsa bu deger de
    # gateway'e ULASAMAZ. Gercek koruma gateway tarafinda: baslik yuzunden
    # istek reddedilirse basliksiz yeniden dene.
    heartbeat_interval_sec: int = 30
