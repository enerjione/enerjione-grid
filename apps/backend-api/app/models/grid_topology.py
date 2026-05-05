"""Sebeke topolojisi: Bolge -> Hat -> Direk -> Segment.

Modelleme kararlari:

  Region (Bolge)
    - Coğrafi/işletme grubu (örn. "Marmara Doğu", "Kıyı Şeridi").
    - Hat'ler buraya bağlanır; sorumluluk alanı atanırken kullanılabilir.

  Line (Hat)
    - Bir bölge içindeki fiziksel hat. Üzerinde 3 fazlı iletim var.
    - Sıralı direklerden oluşur (sequence_no 1..N).

  Pole (Direk)
    - Hat üzerindeki tekil direk. Koordinatlı (latitude/longitude).
    - sequence_no: hat üzerinde başlangıçtan bitişe sıra.
    - Direkte cihaz YOK; cihaz iki direk arasındaki segmentte bulunur.
    - UI haritası direkleri sequence_no sırasına göre polyline ile birleştirir.

  LineSegment (Hat Segmenti)
    - İki ardışık direk arasındaki bölüm.
    - device_id: bu segmenti izleyen Horstmann SN2 cihazı (master + sat01 + sat02
      tek bir grupta gelir; arıza fazı popup'ta gösterilir).
    - Bir cihaz tek segmente bağlı (uniq); bir segment ya cihazsız ya tek cihazlı.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Haritada bu bölgeye ait hatların varsayılan rengi (HEX, ör. #2563eb).
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class Line(Base):
    __tablename__ = "lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Bölge rengini override etmek için (boşsa Region.color kullanılır).
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        # (region, code) tek olmalı; aynı bölgede iki "F-1" hattı olamaz.
        UniqueConstraint("region_id", "code", name="uq_line_region_code"),
    )


class Pole(Base):
    __tablename__ = "poles"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Hat üzerinde başlangıçtan bitişe sıra (1, 2, 3, ...). UI bu sırayla polyline çizer.
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # Direk tipi: 'pole' (varsayilan), 'transformer' (trafo), 'breaker', vs.
    # UI hat baslangic/bitis sembolunu bu alana gore degistirir.
    pole_type: Mapped[str] = mapped_column(String(20), nullable=False, default="pole")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("line_id", "sequence_no", name="uq_pole_line_sequence"),
    )


class LineSegment(Base):
    __tablename__ = "line_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="CASCADE"), index=True, nullable=False
    )
    from_pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="CASCADE"), nullable=False
    )
    to_pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="CASCADE"), nullable=False
    )
    # Bu segmenti izleyen cihaz (Horstmann SN2). Bir cihaz tek segmente baglanir.
    # NOT: Ayni (from, to) cifti icin birden fazla LineSegment kaydi olabilir
    # (bir direk arasinda birden cok cihaz). Cihaz UNIQUE kalir; ayni cihaz
    # iki yerde olamaz.
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
