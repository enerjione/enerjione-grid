"""Ariza (Fault) lokasyon kaydi.

Mantik:
  Bir AlarmEvent tek bir cihazin "ariza akimi gordum" sinyalidir. Birden
  cok AlarmEvent (ayni hat'ta sirayla yer alan cihazlar) bir ARIZA NOKTASI
  ifade eder. Ariza, son RED cihaz ile ilk GREEN cihaz arasindaki POLE
  aralidir.

  FaultEvent kaydi bu ariza-aralik bilgisini canli olarak saklar:
    - line_id, region_id (kapsam icin)
    - last_red_device_id (son alarm veren)
    - first_green_device_id (sonraki alarm vermeyen — yoksa NULL)
    - from_pole_id, to_pole_id (cihazlarin oturdugu slot ucu)
    - status: "open" (aktif ariza), "resolved" (cozuldu/normale dondu)
    - opened_at, resolved_at

  Bir fault aktif iken (open):
    - alarm-engine reconciliation veya yeni alarm geldikce guncellenir
    - notification dispatcher web+email+sms gonderir (kullanici tercihi
      ve sorumluluk alanina gore)
  Cozuldugunde:
    - status="resolved", resolved_at set
    - tarihce/raporlama icin DB'de kalir

  Frontend "Arizalar" sayfasi:
    - GET /faults?status=open  -> aktif ariza listesi (cizelge)
    - GET /faults?status=all   -> tarih dahil
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FaultEvent(Base):
    __tablename__ = "fault_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Topoloji icindeki konum.
    # Hat/region/direk silindiginde ariza kaydi da otomatik silinir
    # (gecmis kayit barindirma yerine kaskat tercih edildi — silinmis hat
    # icin ariza kaydi mantiksiz). Cihaz silinmesinde de cascade.
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="CASCADE"), index=True
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), index=True
    )

    # Ariza aralik uc noktalari (son RED ile ilk GREEN cihaz arasinda)
    # last_red_device_id NULL olamaz — bu cihaz sayesinde fault tespit edildi.
    last_red_device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # ilk green cihaz: NULL olabilir (hat ucunda alarm: sonraki cihaz yok).
    first_green_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    # Direk araligi (cihazlarin oturdugu slot uclari)
    from_pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="CASCADE"), index=True
    )
    to_pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="CASCADE"), index=True
    )
    # Pole sequence_no'larini aciklayici metin/UI icin saklayalim (denormalized).
    from_pole_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_pole_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # status:
    #   "open"        - yeni acildi, henuz kimse atanmadi
    #   "assigned"    - bir kullaniciya otomatik veya manuel atandi
    #   "in_progress" - atanan kullanici uzerinde calisiyor
    #   "resolved"    - sahada cozuldu (cihaz alarmi kalkti)
    #   "closed"      - kullanici raporu tamamladi ve kapatti (resolved sonrasi)
    status: Mapped[str] = mapped_column(String(20), index=True, default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Ticket atamasi: arizadan sorumlu kullanici (otomatik atanir;
    # engineer/installer manuel degistirebilir).
    assigned_to_username: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Genel aciklama / sahaya gidis sonrasi rapor (opsiyonel; uzun aciklama
    # icin FaultComment kullanin — bu alan basit ozet).
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class FaultComment(Base):
    """Ariza ticket'ina baglı yorum/rapor satirlari.

    Atanan kullanici sahaya gittiginde ne yaptigini (gozlem, bakim islemi,
    onarim adimlari, parca degisimi vb) buraya ekler. Bir fault icin coklu
    yorum birikir (zaman zaman raporlar)."""

    __tablename__ = "fault_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    fault_id: Mapped[int] = mapped_column(
        ForeignKey("fault_events.id", ondelete="CASCADE"), index=True
    )
    author_username: Mapped[str] = mapped_column(String(120), index=True)
    body: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
