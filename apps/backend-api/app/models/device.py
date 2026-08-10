from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CommunicationStatus


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # Cihazin seri numarasi — config dosya adinin (`<seri>_Configuration.csv`)
    # ve FTP eslestirmesinin BIRINCIL kaynagi. Kurulumda elle girilir; cihaz
    # baglaninca `master.serial_number` telemetrisinden OTOMATIK guncellenir
    # (bkz. telemetry_consumer). Salt telemetriye guvenmek sahada kirildi:
    # cihaz bir an seri=0 gonderdi ve sistem `0_Configuration.csv` uretti.
    serial_number: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model: Mapped[str] = mapped_column(String(80), default="horstmann_sn_2_0", index=True)

    # --- KIT / SANAL SET BAGI -------------------------------------------
    #
    # Horstmann Pole Master Kit TEK bir DNP3 outstation'dir ama uzerindeki 9
    # uydu ucerli setler halinde sahada BIRBIRINDEN BAGIMSIZ noktalara
    # kelepcelenir. Her set kendi direk araliginda oturur, kendi arizasini
    # uretir, kendi detay sayfasi olur — yani kullanici acisindan ayri bir
    # cihazdir.
    #
    # BUNU NEDEN AYRI SATIR OLARAK MODELLIYORUZ: `line_segments.device_id`
    # TEKILDIR, yani bir Device satiri hattin yalnizca TEK noktasina oturabilir.
    # Uc seti tek satirda tutup uc yere koymak sema acisindan MUMKUN DEGIL.
    # Ustelik ariza motoru, IEC104 Common Address'i ve telemetri anahtari
    # bastan sona `device_id` uzerinde calisir; her set ayri satir olunca bu
    # zincirin TAMAMI hicbir degisiklik olmadan dogru calisir.
    #
    #: Sanal set kaydinin bagli oldugu FIZIKSEL kit. NULL = bu satir fiziksel
    #: bir cihazdir (SN2 ya da kitin kendisi).
    #:
    #: CASCADE: kit silinince setleri de gider. Yetim bir set kaydi hicbir
    #: seyi izlemez — telemetrisi gelmez, arizasi guncellenmez — ama arayuzde
    #: saglikli gorunurdu; sessiz yanlislik yerine birlikte silinmesi dogru.
    parent_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: Setin kit uzerindeki sirasi (1 tabanli). Varsayilan uydu atamasi
    #: buradan cikar: set 1 -> Satellite 01/02/03, set 2 -> 04/05/06,
    #: set 3 -> 07/08/09. Fiziksel satirlarda NULL.
    subunit_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Setin GERCEK uydu atamasi — uc fiziksel uydu numarasi (1..9), unite
    #: sirasiyla: [1. unite, 2. unite, 3. unite].
    #:
    #: NEDEN AYRI ALAN, NEDEN `subunit_index`TEN TURETILMIYOR: varsayilan
    #: yerlesim (1-2-3 / 4-5-6 / 7-8-9) en yaygin kurulum ama uyduları
    #: kelepceyi takan kisi baglar ve sira kite gore degil DIREGE gore
    #: olusur. Ikinci sete 4/5/6 yerine 2/7/9 baglanmis bir kurulumda sabit
    #: turetme telemetriyi YANLIS setlere yazardi ve bu hicbir hata
    #: uretmezdi — yalnizca "bu setin akimi tuhaf" diye gorunurdu.
    #:
    #: NULL = varsayilani kullan (geriye uyum). Kit genelinde BIJEKTIF olmali:
    #: ayni uydu iki sete atanirsa olcumlerden biri sessizce kaybolur
    #: (canli tabloda (device_id, signal_key) tekil, en yenisi kazanir).
    subunit_satellites: Mapped[list | None] = mapped_column(JSON, nullable=True)
    installation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gateway_code: Mapped[str | None] = mapped_column(
        ForeignKey("gateways.code", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=True,
        index=True,
    )
    ip_address: Mapped[str] = mapped_column(String(120))
    dnp3_outstation_port: Mapped[int] = mapped_column(Integer, default=20001)
    dnp3_address: Mapped[int] = mapped_column(Integer, default=1)
    dnp3_extended: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=2)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=3000)
    retry_count: Mapped[int] = mapped_column(Integer, default=2)
    signal_profile: Mapped[str] = mapped_column(String(80), default="horstmann_sn2_fixed")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    battery_percent: Mapped[float] = mapped_column(Float, default=100.0)
    communication_status: Mapped[CommunicationStatus] = mapped_column(
        Enum(CommunicationStatus), default=CommunicationStatus.UNKNOWN
    )
    alarm_active: Mapped[bool] = mapped_column(default=False)
    last_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # IEC 60870-5-104 ASDU Common Address (CA). Outbound IEC 104 servisi
    # bu cihazdan gelen sinyalleri ASDU yayinlayacaginda hangi CA ile
    # paketleyecegini buradan ogrenir. NULL ise outbound target'in
    # `iec104_common_address` (default) degeri kullanilir.
    iec104_common_address: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- UNITE -> FAZ ESLEMESI (bu cihaza OZEL) --------------------------
    #
    # Horstmann SN2 tek cihazdir ama uc unitesi (master / sat01 / sat02)
    # hatta UC AYRI FAZA kelepcelenir. Hangi unitenin hangi fazda oldugu
    # sahada kelepceyi takan kisinin kararidir ve CIHAZDAN CIHAZA
    # DEGISEBILIR — ayni hatta bile.
    #
    # COZUM ZINCIRI (fault_snapshot.resolve_source_phase):
    #     cihaz  ->  proje varsayilani  ->  kod varsayilani (a/b/c)
    #
    # NEDEN ZINCIR, NEDEN SADECE CIHAZ DEGIL: 600 cihazlik bir kurulumda
    # her cihaz icin uc alan doldurmak zorunlulugu pratikte "hicbiri
    # doldurulmaz" demektir ve veri, varsayilana guvenmekten DAHA KOTU
    # olur. Kurulumun genel konvansiyonu Proje Ayarlari'nda bir kez
    # girilir; burasi yalnizca ISTISNA cihazlar icindir.
    #
    # NULL = "bu cihaz icin ozel bir sey yok, ustteki katmani kullan".
    # Kismi doldurma desteklenir: yalnizca sat01 farkliysa yalnizca o
    # yazilir, digerleri ust katmandan gelir.
    #
    # HANGI UNITELER: model belirler (bkz. fault_snapshot._phase_fields).
    #   horstmann_sn_2_0  -> master / sat01 / sat02  (master OLCUM yapar)
    #   horstmann_pmk_set -> sat01 / sat02 / sat03   (ucu de uydudur)
    # Bu yuzden `phase_sat03` eklendi ve `phase_master` set kayitlarinda
    # KULLANILMAZ — kitte master bir olcum unitesi degil, ortak RTU'dur.
    phase_master: Mapped[str | None] = mapped_column(String(4), nullable=True)
    phase_sat01: Mapped[str | None] = mapped_column(String(4), nullable=True)
    phase_sat02: Mapped[str | None] = mapped_column(String(4), nullable=True)
    phase_sat03: Mapped[str | None] = mapped_column(String(4), nullable=True)
