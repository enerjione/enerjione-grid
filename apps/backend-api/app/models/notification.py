"""Bildirim merkezi tablosu - kullanici basina bildirimleri saklar.

Sistem'de gerceklesen kullanici-iliskili olaylar (alarm atamasi, yorum,
manuel mesaj, vb.) ilgili kullaniciya/kullanicilara birer notification kaydi
olarak yazilir. Header'daki zil ikonu okunmamislari sayar; dropdown son N
bildirimi (okundu/okunmadi durumu ile) gosterir.

Tasarim notlari:
  * Bir notification daima `recipient_username` icin yazilir; broadcast
    icin recipient_username NULL ile yazilabilir (frontend tum kullanicilar
    icin gosterir). Mevcut akista hepsi spesifik kullanicilara yaziyor.
  * `category` istemcide gruplandirma icin (alarm/system/info gibi).
  * `link` opsiyoneldir — frontend tikladigi anda hangi sayfaya gitmesi
    gerektigini soyler (orn. "/alarms?id=42").
  * `metadata_json` ileride genisletme icin esnek alan; alarm_id, signal_key
    vb. burada saklanabilir.
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Hedef kullanici (NULL = broadcast). Username ile baglanir cunku silinen
    # bir kullanicinin notifleri salt-okunur log olarak kalir; yeni kullanici
    # ayni kullanici adiyla acilirsa notlari ayri olarak yorumlanir (ki bu
    # genelde sorun cikarmaz, audit-only).
    recipient_username: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)

    # Bildirim kategorisi: alarm | alarm_assignment | alarm_comment |
    # system | message | info | warning | error
    category: Mapped[str] = mapped_column(String(40), index=True, default="info")

    # info | warning | error | critical
    severity: Mapped[str] = mapped_column(String(20), default="info")

    # Kisa baslik (zil dropdown'unda kalin gosterilir).
    title: Mapped[str] = mapped_column(String(200))

    # Aciklama metni (gerekiyorsa). Bos olabilir.
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Bildirimi olusturan kullanici (atama yapan, yorum yazan, vs.) — None ise
    # sistem tarafindan otomatik uretilmis demektir (alarm tetiklenmesi vs.)
    actor_username: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Frontend tik aksiyonu icin gidilecek hedef. Orn: /alarms#42
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Genisletme alani (alarm_id, device_code, signal_key vs.)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


# Composite index: ayni kullanicinin okunmamislarini hizla saymak icin.
Index("idx_notif_recipient_unread", Notification.recipient_username, Notification.is_read, Notification.created_at)
