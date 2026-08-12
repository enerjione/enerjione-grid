from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlarmEvent(Base):
    __tablename__ = "alarm_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
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


class AlarmComment(Base):
    __tablename__ = "alarm_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    alarm_event_id: Mapped[int] = mapped_column(ForeignKey("alarm_events.id"), index=True)
    author_username: Mapped[str] = mapped_column(String(120), index=True)
    comment: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
