"""Proje seviyesi ayarlar — singleton bir satir.

Sadece bir kayit (id=1) olacak; PUT cagrisi olusturma/guncellemeyi kapsar.
Kurulumcu (INSTALLER) tarafindan duzenlenir; login ekrani ve header read-only
sekilde gosterir (auth gerek**siz** GET endpoint'i var).

Logolar base64 data URL olarak saklanir — kucuk PNG/SVG'ler icin yeterli.
Buyuk dosya beklemiyoruz (max ~500 KB); sutun tipi TEXT, sinirsiz.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectSettings(Base):
    __tablename__ = "project_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    project_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Login ekraninda gosterilecek buyuk logo (data URL: 'data:image/png;base64,...').
    customer_logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Header'da gosterilecek koyu zemin uyumlu kucuk logo (data URL).
    customer_logo_light: Mapped[str | None] = mapped_column(Text, nullable=True)
