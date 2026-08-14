from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    event,
    insert,
    update,
)
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlarmEvent(Base):
    __tablename__ = "alarm_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    #: Alarmi ureten cihaz. Cihaz SILINIRSE bu alan NULL'a duser ama satir
    #: KALIR — bkz. `device_code` / `device_name` ve asagidaki not.
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id"), index=True, nullable=True
    )

    #: Cihaz kodu/adi — SILINME ANINDA dondurulan anlik goruntu.
    #:
    #: NEDEN VAR: cihaz silindiginde alarm gecmisi de siliniyordu
    #: (`device_repository._delete_telemetry_and_alarms_for_device`). Sahada
    #: olculdu (2026-08-12): demo cihazlari 17 kez silinip yeniden
    #: olusturulmus ve `alarm_events` 2 satira dusmustu; analiz ekranindaki
    #: takvim ve cihaz x zaman matrisi bos cikiyordu. Operator bunu
    #: "alarm olmamis" diye okuyordu.
    #:
    #: Asimetri de buydu: `telemetry_history` silinen cihazin satirlarini
    #: KORUYOR (aynı olcumde ~20 olu device_id duruyordu), alarm korumuyordu.
    #: Bir ariza izleme urununde operasyonel kaydin donanim kaydiyla birlikte
    #: yok olmasi, saklama politikasi degil VERI KAYBIDIR.
    #:
    #: NULL = cihaz hala duruyor; ad/kod `devices` tablosundan okunur. Dolu =
    #: cihaz silinmis, gecmis bu iki alandan okunur.
    device_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    level: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(1000))
    # Alarmin tetiklendigi sinyalin key'i. Kaynak (master/sat01/sat02) bilgisini
    # frontend prefix'ten turetir, boylece UI'da "Master / Sat 01 / Sat 02"
    # rozeti gosterilir. Eski kayitlar icin NULL olabilir.
    signal_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Bu alarmi ureten kuralin produces_fault degeri alarm satirina "dondurulur".
    # Hat arizasi hesabi (fault_recompute) ve harita kirmizi gosterimi yalniz
    # produces_fault=True alarmlari dikkate alir. Kural sonradan degisse de bu
    # alarmin davranisi sabit kalir. Default True -> eski kayitlar ariza uretir.
    produces_fault: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: Alarm NEREDEN dogdu: "rule" (alarm kurali tetikledi) | "comm_loss"
    #: (cihazla haberlesme koptu, motor kendiliginden acti).
    #:
    #: NEDEN ACIK BIR ALAN: analiz katmani "hangi cihazin haberlesmesi sik
    #: kopuyor" sorusunu cevapliyor ve bunun icin haberlesme alarmlarini
    #: kural alarmlarindan ayirmasi gerek. Yapisal bir ayirt edici YOKTU:
    #: `signal_key` haberlesme alarminda NULL ama sema kural alarmlarinda da
    #: NULL'a izin veriyor, basliga bakmak ise cihaz adina ve dile bagimli.
    #: Sessizce yanlis kovaya atan bir metrik, metrik olmamasindan kotudur —
    #: cunku ona bakip sahaya teknisyen gonderilecek.
    #:
    #: NULL = eski kayit (bu alan eklenmeden once yazilmis).
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    #: Kayit ARSIVE dustu: canli panellerde gorunmez ama TARIHCEDE durur.
    #:
    #: NEDEN VAR: bu satirlar eskiden SILINIYORDU. Iki yerde:
    #:   1) Ayni sinyal icin alarm tekrar tetiklendiginde "Onay Bekliyor"da
    #:      bekleyen onceki kayit siliniyordu (panel tek satir kalsin diye).
    #:   2) ONAYLANMIS bir alarm normale donunce satir tamamen siliniyordu
    #:      ("kullanici zaten gordu, yer kaplamasin").
    #:
    #: Ikisi de bir GORUNUM sorununu VERI SILEREK cozuyordu. Sonucu: "gecen
    #: ay hangi gun kac alarm geldi" sorusunun cevabi yoktu — tekrar eden bir
    #: alarm geriye tek satir birakiyordu. Ariza analizi (takvim, cihaz x
    #: zaman matrisi) bos gorunuyordu; 18 ariza kaydina karsilik 6 alarm
    #: satiri kaliyordu.
    #:
    #: Artik satir DURUYOR, yalnizca damgalaniyor. Canli listeler
    #: `superseded_at IS NULL` suzer; analiz katmani bu alani HIC bakmaz —
    #: tarihce tamdir. NULL = canli kayit.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AlarmDailyCount(Base):
    """GUNLUK ALARM TETIKLENME SAYACI — analiz grafiklerinin zaman serisi.

    NEDEN AYRI TABLO
    ----------------
    "Bugun kac kez alarm tetiklendi" sorusunun cevabi bir SAYIDIR; alarm
    satirlarini saymak degildir. Ikisi ayni sey degil cunku alarm satiri bir
    DURUM kaydi: acilir, onaylanir, normale doner, arsivlenir, gunu gelince
    retention'a takilir. Tetiklenme ise degismez bir OLAYDIR — olduktan
    sonra hicbir sey onu geri almaz.

    Sayaci ayirmanin uc somut faydasi:

      * Tetiklenme sayisi, alarm satirinin omrunden BAGIMSIZ olur. Satir ne
        olursa olsun "12 Agustos'ta 47 alarm tetiklendi" dogru kalir.
      * 365 gunluk takvim, 600 cihazlik sahada yuz binlerce alarm satirini
        taramak yerine gun basina birkac yuz satir okur.
      * Retention penceresinden daha eski donemler icin bile grafik cizilir;
        sayac satiri bir alarm satirindan cok daha ucuzdur.

    TANE: (gun, cihaz). Takvim gune gore toplar, cihaz x zaman matrisi
    ikisini birden kullanir.

    GUN = UTC. Sistemde her zaman damgasi UTC (bkz. proje kilavuzu); yerel
    gune cevirmek raporlarin sunucu saat dilimine gore kaymasi demek olurdu.
    """

    __tablename__ = "alarm_daily_counts"

    #: UTC gun (YYYY-MM-DD).
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    #: O gun o cihaz icin kac kez alarm TETIKLENDI.
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@event.listens_for(AlarmEvent, "after_insert")
def _alarm_gunluk_sayacini_artir(mapper, connection, target) -> None:  # noqa: ANN001
    """Her YENI alarm satiri, o gunun sayacini bir artirir.

    NEDEN ORM OLAYI, ELLE CAGRI DEGIL
    ---------------------------------
    Alarm iki ayri yerde uretiliyor (internal ingest ucu ve haberlesme
    alarmini kendiliginden acan motor) ve ileride ucuncusu eklenebilir.
    Sayaci cagri yerlerine serpistirmek, "yeni bir alarm yolu eklendi ama
    sayac unutuldu" hatasini ZAMAN MESELESI yapardi — ve o hata sessizdir:
    alarm calisir, yalnizca grafik eksik cizer. Burada satir eklenmesi ile
    sayacin artmasi AYNI ISLEMDIR.

    DEDUP SAYILMAZ cunku dedup yolunda YENI SATIR ACILMAZ; ayni alarm zaten
    acikken gelen tekrar mesaji buraya hic ulasmaz.

    SILME AZALTMAZ: tetiklenme olmus bir olaydir. Alarm satiri sonradan
    arsivlense ya da retention'a takilsa da "o gun tetiklendi" dogru kalir.
    """
    if target.device_id is None:
        return
    damga = target.created_at or datetime.now(timezone.utc)
    if damga.tzinfo is None:
        damga = damga.replace(tzinfo=timezone.utc)
    gun = damga.astimezone(timezone.utc).date()

    tablo = AlarmDailyCount.__table__
    lehce = connection.dialect.name
    degerler = {"day": gun, "device_id": target.device_id, "count": 1}
    if lehce in ("postgresql", "sqlite"):
        # Tek ifadede upsert: iki alarm ayni anda dusse bile sayac
        # kaybolmaz (once-SELECT-sonra-INSERT yarisi yok).
        ekle = _pg_insert if lehce == "postgresql" else _sqlite_insert
        stmt = ekle(tablo).values(**degerler)
        connection.execute(
            stmt.on_conflict_do_update(
                index_elements=[tablo.c.day, tablo.c.device_id],
                set_={"count": tablo.c.count + 1},
            )
        )
        return
    vurulan = connection.execute(
        update(tablo)
        .where(tablo.c.day == gun)
        .where(tablo.c.device_id == target.device_id)
        .values(count=tablo.c.count + 1)
    ).rowcount
    if not vurulan:
        connection.execute(insert(tablo).values(**degerler))


class AlarmComment(Base):
    __tablename__ = "alarm_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    alarm_event_id: Mapped[int] = mapped_column(ForeignKey("alarm_events.id"), index=True)
    author_username: Mapped[str] = mapped_column(String(120), index=True)
    comment: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
