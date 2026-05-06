"""Proje seviyesi ayarlar — singleton bir satir.

Sadece bir kayit (id=1) olacak; PUT cagrisi olusturma/guncellemeyi kapsar.
Kurulumcu (INSTALLER) tarafindan duzenlenir; login ekrani ve header read-only
sekilde gosterir (auth gerek**siz** GET endpoint'i var).

Logolar base64 data URL olarak saklanir — kucuk PNG/SVG'ler icin yeterli.
Buyuk dosya beklemiyoruz (max ~500 KB); sutun tipi TEXT, sinirsiz.
"""

from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
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
    # Batarya yuzdesi voltajdan turetilirken kullanilan esikler (V).
    # NULL ise fallback default'lar kullanilir (3.40 / 3.71).
    battery_voltage_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_voltage_full: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Tarayici sekmesinde gozukecek baslik (document.title). NULL ise
    # default "Horstmann Smart Logger" kullanilir.
    site_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Tarayici sekmesinde gozukecek favicon (data URL: 'data:image/x-icon;base64,...'
    # veya 'data:image/png;base64,...'). NULL ise public/favicon.ico kullanilir.
    favicon: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Login ekraninda sag taraftaki dekoratif gorsel (data URL). NULL ise
    # default goruntu (varsa) veya bos kullanilir.
    login_image: Mapped[str | None] = mapped_column(Text, nullable=True)
