"""Bir yapilandirma surumunun CIHAZA UYGULANMA NIYETI ve o niyetin durumu.

NEDEN AYRI TABLO
----------------
`DeviceConfigVersion` bir BELGEDIR: dosyanin baytlari, kim urettigi, ne
zaman. Uygulama ise bir SUREC'tir ve saatlerce surebilir: Horstmann Smart
modda modemini kapatir ve Dial-In araligi 24 saate kadar cikabilir.

Surece ait alanlari belgeye eklemek iki seyi birden bozardi:
  * Ayni surum birden fazla kez uygulanabilir (basarisiz oldu, tekrar
    denendi); tek bir `applied_at` bunu anlatamaz.
  * Surumler append-only ve DEGISTIRILMEZ; surec ise tanimi geregi degisir.

NEDEN DURABLE OLAN "NIYET", "KOMUT" DEGIL
-----------------------------------------
Komutun 120 saniyelik tazelik suresi bir GUVENLIK INVARYANTIDIR: operatorun
4 saat onceki karari sahanin su anki durumu icin gecerli olmayabilir, o
yuzden bayat komut fiziksel sisteme ULASMAZ. Uyuyan cihaz icin o sureyi
uzatmak, tam da o invaryanti kaldirmak olurdu.

Bunun yerine KALICI olan sey niyettir: "bu cihazda su surum gecerli olsun".
Cihaz DOGAL OLARAK uyandiginda backend O AN yeni, taze, normal 120 saniyelik
bir komut uretir. Yani 24 saat bekleyen bir komut YOKTUR; 24 saat bekleyen
bir NIYET vardir.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: --- DURUMLAR ------------------------------------------------------------
#:
#: `staged` YOKTUR. Kayit ancak dosya FTP'ye YAZILDIKTAN sonra olusur:
#: yazma basarisizsa ortada bir niyet de yoktur (bkz. `/config/apply` sirasi
#: — FTP once, cunku cihaza "yeni dosyani oku" deyip eski dosyayi okutmak
#: tam da kapatmaya calistigimiz sessiz hatadir).

#: Dosya FTP'de, cihaz henuz komut alabilecek durumda degil. Uyuyan Smart
#: cihazin NORMAL durumu — ariza degil.
BEKLIYOR = "cihaz_bekleniyor"

#: Taze oturum kaniti gorulduu, komut uretildi ve kuyrukta.
KUYRUKTA = "kuyrukta"

#: Komut gateway'e teslim edildi (gateway kabul ettigini bildirdi).
#: DIKKAT: "cihaz dosyayi yukledi" DEMEK DEGILDIR.
ILETILDI = "iletildi"

#: Cihazin KENDI kaniti goruldu. Tek gercek "uygulandi" durumu.
DOGRULANDI = "dogrulandi"

#: Komut acikca reddedildi/basarisiz oldu ya da FTP tutarsizligi bulundu.
BASARISIZ = "basarisiz"

#: Ayni cihaz icin daha yeni bir niyet olustu; bu artik gecerli degil.
GECERSIZ = "gecersiz_kilindi"

DURUMLAR = (BEKLIYOR, KUYRUKTA, ILETILDI, DOGRULANDI, BASARISIZ, GECERSIZ)

#: Hala sonuca baglanmamis durumlar. Cihaz uyandiginda YALNIZCA bunlar
#: degerlendirilir.
ACIK_DURUMLAR = (BEKLIYOR, KUYRUKTA, ILETILDI)

#: Kismi unique index'in WHERE kosulu. TEK YERDEN uretilir: elle yazilan
#: ikinci bir kopya `ACIK_DURUMLAR` degisince sessizce ayrisirdi ve index
#: yanlis satirlari kapsardi.
_ACIK_WHERE = "state IN (" + ", ".join(f"'{d}'" for d in ACIK_DURUMLAR) + ")"


class DeviceConfigApplication(Base):
    """Bir cihaza bir surumu uygulama niyeti ve yasam dongusu."""

    __tablename__ = "device_config_applications"
    __table_args__ = (
        # CIHAZ BASINA EN FAZLA BIR ACIK NIYET.
        #
        # Kismi (partial) unique index: yalnizca acik durumlardaki satirlari
        # kapsar, gecmis kayitlar serbesttir. Iki es zamanli `/config/apply`
        # istegi ayni cihaz icin iki acik niyet yaratamaz — ikincisi burada
        # patlar. Uygulama katmanindaki kilit tek basina yetmezdi: birden
        # fazla uvicorn worker'i ayri sureclerdir.
        #
        # SQLite kismi index'i destekler, Postgres de. Testler SQLite'ta
        # kosuyor, uretim Postgres — WHERE kosulu iki lehcede de AYNI metin
        # olsun diye tek yerden uretiliyor.
        Index(
            "uq_device_config_app_acik",
            "device_id",
            unique=True,
            sqlite_where=text(_ACIK_WHERE),
            postgresql_where=text(_ACIK_WHERE),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    config_version_id: Mapped[int] = mapped_column(
        ForeignKey("device_config_versions.id", ondelete="CASCADE"), index=True
    )

    state: Mapped[str] = mapped_column(
        String(24), default=BEKLIYOR, server_default=BEKLIYOR, index=True
    )

    #: --- KIM, NE ZAMAN ---------------------------------------------------
    requested_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    #: Dosyanin FTP'ye yazildigi an ve yazildigi yol. Kayit ancak bundan
    #: sonra olustugu icin ikisi de NOT NULL olabilirdi; yol yine de
    #: teshis icin saklanir.
    ftp_staged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ftp_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: FTP'ye YAZILAN BAYTLARIN OZETI (sha256, hex).
    #:
    #: Cihaz uyandiginda komut uretmeden ONCE FTP'deki dosyanin hala BU
    #: niyetin dosyasi oldugu dogrulanir. Dosya adi cihaz basina SABITTIR
    #: (`<seri>_Configuration.csv`) ve yeni surum eskisinin USTUNE yazar;
    #: bu ozet olmadan v10 niyeti uyandiginda v11'in dosyasini yukletebilirdi.
    ftp_sha256: Mapped[str] = mapped_column(String(64))

    #: --- KOMUT -----------------------------------------------------------
    #: Uretilen TAZE komut. Her uyanmada yenisi uretilebilir, bu yuzden
    #: alan tek ve son komutu gosterir; gecmis `device_commands` tablosunda.
    #: `BigInteger`: hedef kolon (device_commands.id) restore'a dayanikli
    #: kimlik icin int8'e genisletildi; FK tipi ONUNLA AYNI olmak zorunda,
    #: yoksa yeni kimlik tasiyan bir komuta baglanmak "integer out of range"
    #: ile patlar.
    command_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("device_commands.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: SON komut uretme ani. Komut bayatlayip niyet beklemeye dondugunde
    #: TEMIZLENMEZ: "en son ne zaman denedik" bilgisi, ayni gozlemle ikinci
    #: bir komut uretilmesini engelleyen kapinin girdisidir.
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: --- DOGRULAMA -------------------------------------------------------
    #: Komut uretilirken cihazin bildirdigi son yapilandirma damgasi
    #: (`master.info_last_configuration_update`, ham metin). Sonradan
    #: DEGISTIYSE cihaz bir yapilandirma yuklemis demektir.
    #:
    #: Bu damga HANGI surumun yuklendigini SOYLEMEZ — yalnizca bir seyin
    #: yuklendigini. O yuzden tek basina `dogrulandi` yapmaz; cihazin FTP'ye
    #: KENDI yazdigi dosya ile birlikte degerlendirilir.
    readback_before: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Dogrulamanin neye dayandigi: `cihaz_dosyasi` (kesin) | `damga_degisti`
    #: (zayif). Kanit sinifi kayitta durmali; "dogrulandi" yazip nedenini
    #: unutmak, sonradan guvenilirligi tartisilamaz hale getirirdi.
    verified_by: Mapped[str | None] = mapped_column(String(24), nullable=True)

    #: --- SONUC -----------------------------------------------------------
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Son hazirlik degerlendirmesinin gerekcesi (`device_session_readiness`
    #: sabitleri). Operator "neden hala bekliyor" sorusunu buradan okur.
    last_readiness_reason: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Kac kez komut uretildi. Sonsuz dongu korumasi: cihaz uyaniyor, komut
    #: uretiliyor, teslim edilemeden tekrar uyuyor... Bu sayac bir tavana
    #: carpinca niyet `basarisiz` olur ve operator gorur.
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
