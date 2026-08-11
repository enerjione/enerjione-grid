from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeviceModelSettings(Base):
    """Cihaz MODELINE ozel ayarlar (cihaz profili).

    NEDEN AYRI TABLO, NEDEN PROJE AYARLARINDA DEGIL
    -----------------------------------------------
    Batarya esikleri proje genelinde TEK bir (low, full) ciftiydi ve tum
    cihazlara uygulaniyordu. Ayni kurulumda birden fazla model bulununca bu
    sessizce yanlis olur: Horstmann SN 2.0'in lityum hucresi ile Pole Master
    Kit'in bataryasi ayni voltaj araliginda calismaz. Tek esikle yuzde
    hesaplanirsa bir model surekli "dolu", digeri surekli "bitmek uzere"
    gorunur — hicbir hata uretmeden, yalnizca yanlis bir sayi olarak.

    Bu tablo model basina TEK satirdir; `model` birincil anahtardir.

    COZUM ZINCIRI (bkz. device_profile_service.battery_thresholds)
    -------------------------------------------------------------
        model ayari  ->  proje ayari  ->  kod varsayilani

    NULL = "bu model icin ozel bir sey yok, ust katmani kullan". Kismi
    doldurma serbest: yalnizca `low` girilirse `full` ust katmandan gelir.
    Boylece mevcut kurulumlar hicbir sey yapmadan bugunku davranisla
    calismaya devam eder; yeni model eklendiginde yalnizca o modelin satiri
    doldurulur.

    Tabloya satir yazilmasi ZORUNLU DEGILDIR — kayit yoksa zincir bir ust
    katmana duser. Bu yuzden model listesi burada tutulmaz; modeller
    `app.data.device_models` uzerinden gelir ve bu tablo yalnizca ISTISNA
    kaydidir.
    """

    __tablename__ = "device_model_settings"

    #: Model kodu (orn. "horstmann_sn_2_0"). `app.data.device_models`
    #: listesindeki kodla ayni; FK YOK cunku model listesi kismen kodda,
    #: kismen sinyal katalogunda yasar (tek bir tabloya baglanamaz).
    model: Mapped[str] = mapped_column(String(80), primary_key=True)

    #: Bataryanin BOS sayildigi voltaj (bu degerde yuzde 0).
    battery_voltage_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Bataryanin DOLU sayildigi voltaj (bu degerde yuzde 100).
    battery_voltage_full: Mapped[float | None] = mapped_column(Float, nullable=True)

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
