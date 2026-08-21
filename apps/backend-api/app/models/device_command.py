from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.services import command_identity


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DeviceCommand(Base):
    """Cihaza gonderilecek DNP3 CROB komut kuyrugu.

    Gateway NAT arkasinda oldugundan backend gateway'e dogrudan ulasamaz;
    komut config-poll ile iletilir: backend buraya status='pending' satir yazar,
    gateway her config poll'de (~config_refresh_sec) pending komutlari ceker,
    CROB gonderir ve sonucu POST /gateways/{code}/command-results ile bildirir.

    Durum akisi: pending -> sent -> ok | failed.

    `sent` ANLAMI (F3C ile degisti): teslim protokolunu destekleyen gateway'de
    `sent`, "gateway komutu DAYANIKLI olarak kabul etti (ACK)" demektir; yalnizca
    "backend yanita koydu" degil. Eski protokoldeki gateway'de (bkz.
    `COMMAND_DELIVERY_ACK_REQUIRED=false`) eski anlam gecerlidir.

    Terminal `result_status` degerleri:
      expired             TTL doldu, komut gateway'e HIC sunulmadi
      result_unknown      gateway kabul etti (ya da kabul etmis OLABILIR) ama
                          cihaz sonucu alinamadi - fiziksel komut uygulanmis
                          olabilir
      delivery_state_lost gateway defteri sifirlandi; tekrar-onleme kaniti yok,
                          otomatik teslim durduruldu (operator incelemesi)
      delivery_failed     kira defalarca denendi, gateway hicbir zaman kabul
                          ettigini bildirmedi
    """

    __tablename__ = "device_commands"

    # INDEKS MODELDE DE TANIMLI OLMAK ZORUNDA.
    #
    # Temiz kurulum semayi `create_all` + `stamp head` ile uretir ve
    # migration'lar HIC kosmaz (bkz. scripts/migrate_db.py). Indeks yalnizca
    # migration 0069'da tanimlansaydi sifirdan kurulan sahalarda ASLA
    # olusmazdi: yukseltilen kurulumda var, temiz kurulumda yok — sessiz bir
    # sema kaymasi. Kira sorgusu gateway basina 1 Hz kosuyor.
    __table_args__ = (
        Index(
            "ix_device_commands_delivery_lease",
            "gateway_code",
            "status",
            "delivery_lease_until",
        ),
    )

    #: KIMLIK VERITABANINDAN DEGIL UYGULAMADAN GELIR.
    #:
    #: Eskiden bu bir SERIAL'di ve sequence'in degeri VERITABANININ ICINDE
    #: yasiyordu: veritabani daha eski bir ana alindiginda sequence de o ana
    #: doner ve DAGITILMIS kimlikler yeniden uretilirdi. Gateway defteri ise
    #: baska bir makinede durur ve geri gitmez — sahada tam olarak bu yasandi
    #: (GW-002, id 39-42 tekrar kullanildi; gateway fiziksel islemi hakli
    #: olarak tekrarlamadi, backend `token_mismatch` uretti).
    #:
    #: Artik deger `command_identity.yeni_kimlik()` ile uretilir
    #: (`epoch_ms * 1000 + rastgele`) ve INSERT sirasinda ACIKCA verilir.
    #: `autoincrement=False`: sequence'in devreye girip uygulamanin verdigi
    #: degeri ezmesini engeller.
    #:
    #: `BigInteger` ZORUNLU: uretilen deger bugun ~1.79e15 ve int4 tavani
    #: 2.147.483.647. Eski kucuk kimlikler aynen okunmaya devam eder.
    #: `default` MODELDE: kimlik uretimi TEK YERDE olmali. Cagiran tarafa
    #: birakilsaydi, ileride eklenen bir ekleme yolu (ya da bir test)
    #: sessizce kimliksiz satir yazmaya calisir ve kural yalnizca bir
    #: koddan gecerken uygulanirdi.
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        index=True,
        autoincrement=False,
        default=command_identity.yeni_kimlik,
    )
    gateway_code: Mapped[str] = mapped_column(String(50), index=True)
    device_code: Mapped[str] = mapped_column(String(50), index=True)
    # Komut slug'i (SignalCatalog binary_output key'inin master. sonrasi) + cozulmus
    # DNP3 binary output index'i. Index queue zamaninda dondurulur ki sonradan
    # SignalCatalog degisse bile gonderilen komut sabit kalsin.
    command: Mapped[str] = mapped_column(String(80))
    dnp3_index: Mapped[int] = mapped_column(Integer)
    # Horstmann SN2 Device Profile PULSE desteklemez; yalnizca LATCH_ON/LATCH_OFF.
    # Default latch_on (pulse_on cihazda NOT_SUPPORTED / SELECT_FAIL doner).
    op_type: Mapped[str] = mapped_column(String(20), default="latch_on")
    count: Mapped[int] = mapped_column(Integer, default=1)
    on_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    off_time_ms: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    actor_username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ----- F3C: teslim kirasi ve dayanikli kabul (ACK) ---------------------
    #
    # NEDEN: `sent` eskiden "backend HTTP yanitina koydu" demekti; teslim
    # garantisi TASIMIYORDU. Yanit ag uzerinde kaybolursa ya da gateway onu
    # dayanikli deftere yazmadan olurse komut backend'de sonsuza kadar `sent`
    # kalir, cihaza HIC gitmez ve kayip SESSIZ olurdu.
    #
    # Artik komut once KIRALANIR (status `pending` kalir), gateway defterine
    # yazdigini ACK ile bildirince `sent` olur. Boylece `sent` = "gateway
    # komutu dayanikli olarak kabul etti".
    #
    # Protokol ve gateway yukumlulukleri: docs/f3c-command-delivery-protocol.md

    #: Teslim kimligi. ILK kiralamada uretilir ve ACK gelene kadar DEGISMEZ —
    #: kira yenilense de dondurulmez. Sabit kalmasi gateway defterindeki
    #: jetonu her zaman gecerli tutar ve mukerrer ACK'i sadelestirir.
    delivery_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Kiranin bitis ani. Bu ana kadar komut baska bir poll'a SUNULMAZ;
    #: sonrasinda yeniden teslim degerlendirilir (mutlak TTL ve defter
    #: kimligi kontrolleri gecerse).
    delivery_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Kac kez teslim edilmeye calisildi. Sinirsiz yeniden sunumu keser ve
    #: "hic kiralanmis miydi" sorusunu cevaplar (TTL sonlandirmasinda
    #: `expired` ile `result_unknown` ayrimi buna bakar).
    delivery_attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    #: Ilk kiralama anindaki gateway DEFTER KIMLIGI (epoch).
    #:
    #: Gateway defteri bozulup sifirlanirsa tekrar-onleme kaniti kaybolur:
    #: backend daha once teslim ettigi komutu yeniden sunarsa gateway onu YENI
    #: sanip CROB'u TEKRARLAR. Epoch degisimi bunu gorunur kilar ve otomatik
    #: teslim durdurulur (fail-closed).
    delivery_gateway_epoch: Mapped[str | None] = mapped_column(String(64), nullable=True)
