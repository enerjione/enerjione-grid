from datetime import datetime

from pydantic import BaseModel, Field


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
    # DNP3 kalite bayraklarini yayinla mi (invalid / restart / forced).
    # Gateway BAZINDA: acmak saha davranisini degistirir (kotu olcumler alarm
    # degerlendirmesinden bloke olur), once tek gateway'de denenebilsin diye.
    publish_dnp3_quality: bool = False
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
    publish_dnp3_quality: bool | None = None
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
    # `token` BILEREK YOK — duz metin donmuyor.
    #
    # YASANAN ACIK: bu sema token'i duz metin tasiyordu ve gateway listesi
    # OPERATOR rolune de aciktir. Token, `POST /telemetry/gateway/{code}` icin
    # TEK kimlik unsuru; yani operator listeden token'i alip kendi alani
    # DISINDAKI cihazlar icin uydurma telemetri gonderebiliyordu — sahte
    # kritik ariza uretmek ya da `fault_indicator`i normal gondererek GERCEK
    # arizayi maskelemek. Ayni token ile `/gateways/{code}/config` cagrilip
    # sahadaki tum cihazlarin IP/DNP3 adres listesi de sizabiliyordu.
    #
    # Liste operator'a ACIK KALDI (canli deger ekrani gateway'in cevrimici
    # olup olmadigini buradan okuyor); kapatilan yalnizca token. Token'a
    # gercekten ihtiyaci olan INSTALLER `GET /gateways/{code}/token` ucunu
    # kullanir; o cagri denetim kaydina yazilir.
    has_token: bool = False
    # DNP3 kalite bayraklarini yayinla mi (invalid / restart / forced).
    # Gateway BAZINDA: acmak saha davranisini degistirir (kotu olcumler alarm
    # degerlendirmesinden bloke olur), once tek gateway'de denenebilsin diye.
    publish_dnp3_quality: bool = False
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
    # ----- gateway v1.12.0: akilli oturum --------------------------------
    #
    # `continuous` (varsayilan) bugune kadarki davranistir: gateway periyodik
    # tarama yapar. `smart`/`auto` ALTI KOMBINASYONDA da gecerlidir
    # (v1.14.0 uc tipi kisitini kaldirdi) ama YAYINA yetenek kapisindan
    # gecerek girer: gateway surumu desteklemiyorsa serializer guvenli tarafa
    # `continuous` dusurur (bkz. api/gateways.py). Boylece eski bir gateway
    # tanimadigi bir deger yuzunden TUM config'i reddedip ilgisiz cihazlari
    # dondurmez.
    #
    # ESKI GATEWAY'I BOZMAZ: v1.11.x cihaz sozlugunu acik alan cikarimiyla
    # okur, tanimadigi anahtarlari hic gormez (ayni gerekce
    # `GatewayConfigCommand.created_at` icin de gecerli).
    session_policy: str = "continuous"
    # Cihaz seviyesi sessizlik esigi. None = "bu cihaz icin OZEL esik yok";
    # gateway kendi env varsayilanina duser. DEVRE DISI DEMEK DEGILDIR.
    smart_max_silence_sec: int | None = None
    # ----- gateway v1.14.0 -----------------------------------------------
    # Ikisi de YALNIZCA destekleyen gateway'e gonderilir (serializer'da
    # yetenek kapisi). 1.14.0 bilinmeyen cihaz alanlarini yok sayar, ama daha
    # eski surumlerde bu garanti YOKTUR.
    #
    # Zamanlanmis rapor araligi (dk). `smart_max_silence_sec`in YERINE
    # GECMEZ, yaninda calisir: rapor gecikince `report_late` bayragi kalkar,
    # haberlesme kaybi SAYILMAZ.
    dial_in_interval_min: int | None = None
    # Listening kanalda yeniden baglanma TAVANI (sn). Ping/probe araligi
    # DEGILDIR. None = kutuphane varsayilani.
    smart_listen_reconnect_max_sec: int | None = None
    poll_interval_sec: int
    timeout_ms: int
    retry_count: int
    # Bu cihazin sinyal seti anahtari — `signals_by_profile` sozlugune girer.
    #
    # DEGERI `devices.signal_profile` KOLONU DEGIL, `devices.model` BELIRLER.
    # Sebep: katalogun gercek ayirici alani `signal_catalog.model` ve backend'in
    # geri kalani (bkz. api/signals.py `catalog_by_model`) cihazi kataloga
    # ZATEN model uzerinden bagliyor. Kolon ise sahada sabit
    # "horstmann_sn2_fixed" degeriyle duruyor: frontend cihaz olustururken onu
    # sabit yaziyor, hicbir yer okumuyor, katalogun model sozlugunde boyle bir
    # deger de yok. Yani kolon olu; anahtar olarak kullanilsaydi
    # `signals_by_profile` sozlugunde KARSILIGI OLMAYAN bir anahtar uretirdi.
    #
    # Ikinci bir "profil" kavrami uretmek yerine mevcut ve calisan baginti
    # kullaniliyor — aksi halde canli deger ekrani bir sinyal setini, gateway
    # baskasini gorurdu.
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
    # Komutun kuyruga alindigi an — UTC, timezone-aware ISO-8601.
    #
    # NEDEN GONDERILIYOR: backend bayat komutu zaten teslim ETMIYOR (bkz.
    # `command_max_age_sec`), ama gateway'in de komutun ne kadar eski
    # oldugunu GORMESI gerekir; teslimat ile fiziksel gonderim arasinda
    # gecen sure yalnizca gateway tarafinda bilinir.
    #
    # ESKI GATEWAY'I BOZMAZ: saha gateway'i komut sozlugunu ACIK ALAN
    # CIKARIMIYLA okuyor (`item["id"]`, `item.get("command")`, ...), kati
    # bir sema ile degil. Tanimadigi anahtarlar hic okunmaz. Bu, calisan
    # saha imajinin icindeki `backend/config_client.py` uzerinden
    # dogrulandi — varsayim degil.
    created_at: datetime | None = None

    # ----- F3C: teslim kirasi ------------------------------------------
    # Yalnizca teslim protokolunu (command_delivery_ack_v1) bildiren
    # gateway'e doldurulur; eski gateway'de None kalir ve alan hic okunmaz.
    # Protokol: docs/f3c-command-delivery-protocol.md

    #: Bu teslimin kimligi. Gateway komutu dayanikli defterine yazdiktan sonra
    #: `POST /gateways/{code}/command-delivery-acks` ile bu jetonu geri
    #: gonderir; ancak o zaman komut `sent` olur. OPAKTIR ve LOGLANMAZ.
    delivery_token: str | None = None

    #: Komutun MUTLAK son kullanma ani (`created_at + COMMAND_MAX_AGE_SEC`),
    #: timezone-aware ISO-8601.
    #:
    #: NEDEN AYRICA GONDERILIYOR: gateway `created_at`ten kendi TTL'siyle de
    #: hesaplayabilirdi, ama o TTL gateway tarafinda YAPILANDIRILABILIR bir
    #: degerdir; daha genis ayarlanirsa backend'in kapattigi pencere gateway'de
    #: acik kalirdi. Backend'in turettigi DEGISMEZ son kullanma ani bu bosluğu
    #: kapatir (savunma derinligi).
    delivery_not_after: datetime | None = None


class CommandDeliveryAckItem(BaseModel):
    """Gateway'in tek bir komut icin dayanikli kabul bildirimi (F3C).

    Gateway `start_dispatch` SQLite COMMIT'i tamamlandiktan SONRA uretir; ACK
    teslimi basarisiz olursa kayit defterde kalir ve proses yeniden
    baslatildiktan sonra tekrar gonderilir.
    """

    command_id: int
    delivery_token: str


class CommandDeliveryAckRequest(BaseModel):
    """`POST /gateways/{code}/command-delivery-acks` govdesi (batch).

    AYRI UC — baslik piggyback'i DEGIL: baslik boyutu sinirina takilmaz,
    dogrulama yapisaldir, parti dogaldir ve proxy/baslik kodlama davranisina
    bagimli degildir. Yeni bir auth sistemi YOK; mevcut `X-Gateway-Token` ve
    gateway sahiplik dogrulamasi kullanilir.
    """

    acks: list[CommandDeliveryAckItem] = Field(default_factory=list)


class CommandDeliveryAckResponse(BaseModel):
    accepted: int = 0
    rejected: int = 0


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
    # --- ADRES SAHIPLIGI --------------------------------------------------
    #
    # KAPSAM: bu yanit DNP3 gateway'ine gider ve YALNIZCA DNP3 sinyalleri
    # tasir. Baska protokoller (or. Modbus konusan Smart Navigator 1.0) AYRI
    # BIR GATEWAY ile calisir; bu sozlesmeye hic girmezler. Yani buradaki
    # "profil" ayrimi protokol ayrimi DEGIL, ayni protokol icindeki MODEL
    # ayrimidir.
    #
    # HEDEF MIMARI: DNP3 adres haritasi (object_group/index/scale/offset)
    # GATEWAY'DE yasar; backend cihaz basina yalnizca TURU (`signal_profile`)
    # soyler. Gerekce: adres haritasi cihaz firmware'inin ozelligidir, musteri
    # kurulumunun degil — her kurulumda ayni. Protokol surucusu de gateway'de
    # oldugu icin adresin sahibi orasidir.
    #
    # Asagidaki iki alan bu hedefe giderken KOPRU gorevi gorur ve KALDIRILMAZ:
    #   * sahadaki 0.4.x/0.5.0 gateway'ler duz `signals` listesine bagimli,
    #   * gateway'in HENUZ yerlesik profili olmayan bir DNP3 modeli icin
    #     backend'in bildirdigi liste tek kaynaktir.
    #
    # Gateway onceligi: yerlesik profil > signals_by_profile > signals.
    #
    # NOT: `signal_catalog` tablosu ORTADAN KALKMAZ — canli deger ekranindaki
    # etiket/birim, alarm kurallari (`supports_alarm`) ve SCADA cikisi
    # (iec104_ioa / modbus_address / mqtt_topic) hep oradan besleniyor.
    # Gateway'e devredilen yalnizca DNP3 ADRESLEMESIDIR.

    # DUZ liste — bu gateway'deki cihazlarin modellerinin BIRLESIMI.
    #
    # Eskiden TUM aktif katalog donuyordu: bu gateway'de hic bulunmayan bir
    # modelin sinyalleri de listeye giriyor ve gateway onlari da yokluyordu.
    # Artik yalnizca gercekten bagli modellerin sinyalleri var (tek modelli
    # kurulumda sonuc AYNI, cok modellide daha az ve dogru).
    signals: list[GatewayConfigSignal]
    # PROFIL BAZLI katalog: {profil_anahtari: [sinyaller]}.
    #
    # NEDEN: duz liste tek modelli kurulumda dogru calisir ama ikinci bir cihaz
    # modeli eklendigi anda BOZULUR. Ayni (object_group, index) cifti iki
    # modelde FARKLI buyuklugu gosterir; gateway hangi cihaz icin hangi sinyal
    # setini kullanacagini bilmedigi icin okudugu degeri YANLIS `signal_key`
    # ile yayinlar. Hata SESSIZDIR: telemetri akar, deger makul gorunur, ama
    # esik alarmi baska bir buyuklugun uzerinden calisir.
    #
    # Anahtar, cihazi olan HER profil icin yazilir — katalogda o modele ait
    # aktif sinyal yoksa BOS LISTE olarak. Bos liste kasitlidir: gateway o
    # cihazi yoklamaz ve eksiklik operator'a gorunur olur. Duz listeye
    # dusurmek, farkli modelli kurulumda YABANCI adresleri yoklamak demektir
    # ve sessiz yanlis veri uretir — gorunur eksik veriden daha kotudur.
    signals_by_profile: dict[str, list[GatewayConfigSignal]] = {}
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
