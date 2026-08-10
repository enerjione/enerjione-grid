"""Proje seviyesi ayarlar — singleton bir satir.

Sadece bir kayit (id=1) olacak; PUT cagrisi olusturma/guncellemeyi kapsar.
Kurulumcu (INSTALLER) tarafindan duzenlenir; login ekrani ve header read-only
sekilde gosterir (auth gerek**siz** GET endpoint'i var).

Logolar base64 data URL olarak saklanir — kucuk PNG/SVG'ler icin yeterli.
Buyuk dosya beklemiyoruz (max ~500 KB); sutun tipi TEXT, sinirsiz.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
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
    # index.html'deki varsayilan ("EnerjiOne Grid Dashboard") oldugu gibi kalir.
    site_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Tarayici sekmesinde gozukecek favicon (data URL: 'data:image/x-icon;base64,...'
    # veya 'data:image/png;base64,...'). NULL ise public/favicon.ico kullanilir.
    favicon: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Login ekraninda sag taraftaki dekoratif gorsel (data URL). NULL ise
    # default goruntu (varsa) veya bos kullanilir.
    login_image: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Toast bildirimlerinin ekrandaki kosesi: "bottom-right" (VARSAYILAN,
    # mevcut davranis), "bottom-left", "top-right", "top-left". NULL veya
    # taninmayan deger -> "bottom-right". Kurulum geneli tercih; kullanici
    # basina degil (bir kullanici degistirince herkeste degisir).
    toast_position: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Kendiliginden gelen (alarm) toast bildirimlerini sustur. NULL/False ise
    # bildirimler gorunur — mevcut davranis, guncelleyen sahada hicbir sey
    # degismez.
    #
    # KAPSAM: yalnizca KENDILIGINDEN gelen bildirimler susturulur. Kullanicinin
    # kendi eyleminin sonucu olan toast'lar (kaydedildi / kaydedilemedi / yetki
    # hatasi) HER ZAMAN gosterilir; aksi halde operator "Kaydet"e basip hata
    # aldigini hic ogrenemez ve bu sessiz veri kaybi gibi davranir.
    #
    # Susturma bilgi KAYBETTIRMEZ: alarmlar bildirim canindan, Alarmlar
    # sayfasindan ve e-posta/SMS/Telegram/push kanallarindan erisilebilir
    # kalir; bu ayar yalnizca tarayicidaki gecici baloncugu etkiler.
    toast_muted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- UNITE -> FAZ ESLEMESI ------------------------------------------
    #
    # Horstmann SN2 tek cihazdir ama UC unitesi (master / sat01 / sat02)
    # hatta UC AYRI FAZA kelepcelenir. Hangi unitenin hangi fazda oldugu
    # KURULUM KARARIDIR — sahada kelepceyi takan kisi belirler.
    #
    # NEDEN AYAR: ariza analizinde "tek faz mi uc faz mi" ayrimi sebep
    # cikariminin belirleyici girdisi (tek faz-toprak cogunlukla dis etken,
    # uc faz cogunlukla ekipman/asiri yuk). Varsayilani sabit kabul etmek,
    # farkli kurulmus bir sahada faz etiketlerinin SESSIZCE yanlis
    # birikmesi demekti — ve bu, veri biriktikten sonra geri alinamaz.
    #
    # NULL = varsayilan (master=a, sat01=b, sat02=c). Bkz.
    # fault_inference.DEFAULT_SOURCE_PHASE.
    #
    # UNITE KUMESI MODELE GORE DEGISIR: Pole Master Kit'in bir setinde olcum
    # yapan uc unitenin ucu de uydudur (sat01/sat02/sat03) — orada `master`
    # bir faza kelepcelenmez, ortak RTU'dur. Bu yuzden `phase_sat03` var ve
    # her model yalnizca KENDI unitelerinin alanlarini okur
    # (bkz. fault_snapshot._phase_fields).
    phase_master: Mapped[str | None] = mapped_column(String(4), nullable=True)
    phase_sat01: Mapped[str | None] = mapped_column(String(4), nullable=True)
    phase_sat02: Mapped[str | None] = mapped_column(String(4), nullable=True)
    phase_sat03: Mapped[str | None] = mapped_column(String(4), nullable=True)
