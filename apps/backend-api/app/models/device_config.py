"""Cihaz yapilandirma dosyalari — sablonlar ve surumler.

Horstmann SN2.0 cihazi ayarlarini `<seri>_Configuration.csv` dosyasindan okur
(bicim: `app/services/horstmann_config_codec.py`).

NEDEN HAM BAYT SAKLIYORUZ (`LargeBinary`, `Text` DEGIL)
-------------------------------------------------------
Dosyanin sonunda, son CSV satirindan sonra CSV OLMAYAN baytlar var — kuvvetle
muhtemel bir saglama toplami ve HENUZ COZMEDIK. Bu yuzden:

  - Ayristirilmis DEGERLERI saklayip dosyayi geri URETEMEYIZ; kuyrugu
    yeniden hesaplayamadigimiz icin urettigimiz dosya gecersiz olurdu.
  - Metin olarak saklamak da olmaz: herhangi bir kod cozme/donusturme adimi
    (satir sonu normalizasyonu dahil) kuyrugu bozar.

Dolayisiyla surum kaydinin ASLI ham bayttir. Ayristirma yalnizca GOSTERIM ve
KARSILASTIRMA icin, okuma aninda yapilir.

NEDEN SABLONUN BAYTI SURUME KOPYALANIR
--------------------------------------
`DeviceConfigVersion.raw` sablondan uretilse bile sablonun kendisine BAGLI
DEGILDIR; baytlar kopyalanir. Sablon sonradan degistirilirse ya da silinirse
cihazin gecmis surumleri OLDUGU GIBI kalmalidir — aksi halde "bu cihazda o gun
hangi ayar vardi" sorusu cevapsiz kalir ve surum gecmisi denetim degerini
kaybeder. `template_id` yalnizca KOKEN bilgisidir.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Surumun nereden geldigi. Denetimde "bu ayari kim koydu" sorusunun ilk yariti.
#:   sablon          : cihaz eklendiginde tip sablonundan otomatik uretildi
#:   cihazdan_cekildi: `master.start_csv_upload` ile cihazin kendisi yazdi
#:   yuklendi        : kullanici dosyayi elle yukledi
#:   duzenlendi      : kullanici arayuzden deger degistirdi
CONFIG_KAYNAKLARI = ("sablon", "cihazdan_cekildi", "yuklendi", "duzenlendi")


class DeviceConfigTemplate(Base):
    """Cihaz TIPINE gore fabrika/proje sablonu.

    Sifirdan config URETEMEDIGIMIZ icin (bkz. modul docstring) yeni cihazin ilk
    surumu, bilinen-iyi bir dosyanin BAYT BAYT KOPYASI olur. Bunu mumkun kilan
    sey su: CSV govdesi cihaza ozel hicbir kimlik tasimaz — outstation adresi
    XML katalogunda vardir ama CSV'de yoktur. Yani ayni baytlar her cihaz icin
    gecerlidir; cihaza ozel olan tek sey DOSYA ADIdir.
    """

    __tablename__ = "device_config_templates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # `devices.model` ile ayni deger uzayi (orn. "horstmann_sn_2_0").
    device_model: Mapped[str] = mapped_column(String(80), index=True)
    raw: Mapped[bytes] = mapped_column(LargeBinary)
    # Yuklenen dosyanin ozgun adi — kokeni izlemek icin (orn "50984_Configuration.csv").
    source_filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tip basina TEK varsayilan olur; yeni cihaz bunu kullanir.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceConfigVersion(Base):
    """Bir cihazin yapilandirma dosyasinin TEK bir surumu.

    Surumler SILINMEZ ve DEGISTIRILMEZ (append-only). "Geri al" yeni bir surum
    yaratir; eskiyi geri yazmaz. Boylece gecmis her zaman dogrudur.
    """

    __tablename__ = "device_config_versions"
    __table_args__ = (
        # Ayni cihazda ayni surum numarasi iki kez olusmasin. Es zamanli iki
        # kayit denemesinde ikincisi burada patlar; sessizce ustune yazip
        # bir surumu kaybetmekten iyidir.
        UniqueConstraint("device_id", "version", name="uq_device_config_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    # 1'den baslar, cihaz basina artar.
    version: Mapped[int] = mapped_column(Integer)
    raw: Mapped[bytes] = mapped_column(LargeBinary)
    source: Mapped[str] = mapped_column(String(24))
    # Yalnizca KOKEN bilgisi; baytlar kopyalandigi icin sablon degisse/silinse
    # de bu surum etkilenmez.
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("device_config_templates.id", ondelete="SET NULL"), nullable=True
    )
    # Cihaza GERCEKTEN gonderildi mi? Surum yaratmak ile cihaza uygulamak AYRI
    # islemlerdir: dosya FTP'ye yazilir, sonra DNP3 komutu ile tetiklenir ve
    # cihaz onu ancak bir sonraki oturumda alabilir. Ikisini tek bayrakta
    # birlestirmek "gonderildi sanip gonderilmemis" durumunu gizlerdi.
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
