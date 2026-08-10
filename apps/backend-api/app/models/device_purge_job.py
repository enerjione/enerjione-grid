"""Cihaz silindikten SONRA arka planda temizlenecek arsiv verisi kuyrugu.

NEDEN VAR
---------
Cihaz silme, 500 cihazlik bir kurulumda dakikalarca suruyor ve arayuz zaman
asimina ugruyordu — yani pratikte cihaz SILINEMIYORDU. Sebep tek bir
transaction icinde `telemetry_history`'den o cihaza ait TUM satirlarin
silinmesiydi: 90 gunluk pencerede cihaz basina ~4M satir, ~90 hypertable
chunk'ina yayilmis. Bu:
  * HTTP istegini dakikalarca acik tutar (kullanici "kilitlendi" sanir),
  * WAL'i tepe noktasina cikarir (checkpoint firtinasi),
  * uzun kilit tutar ve telemetri yazimini yavaslatir.

NASIL COZULUYOR
---------------
Silme IKI FAZA ayrildi:
  1. SENKRON (milisaniyeler): kucuk iliskili kayitlar (alarm, komut, kural
     filtresi, canli deger, config) temizlenir ve CIHAZ SATIRI SILINIR.
     Cihaz o an arayuzden, haritadan ve gateway yapilandirmasindan kaybolur.
  2. ARKA PLAN (bu kuyruk): `telemetry_history` satirlari retention
     worker'inin partili silme dongusuyle temizlenir (her parti ayri commit).

NEDEN SOFT-DELETE DEGIL
-----------------------
Cihazi "silinmis" isaretleyip arka planda temizlemek, kod tabanindaki 36
ayri `select(Device)` cagrisinin HEPSINE filtre eklemeyi gerektirirdi. Biri
atlanirsa silinen cihaz haritada/listede HAYALET olarak gorunur — bu urunde
sessiz yanlis veri en agir hata sinifi. Cihaz satirini hemen silmek bu riski
tamamen ortadan kaldirir: hicbir sorgu degismedi.

Bunun bedeli, arsiv satirlarinin kisa sure SAHIPSIZ kalmasidir. Zararsiz:
tum okuma yollari `devices` uzerinden gittigi icin bu satirlar zaten hicbir
ekranda gorunmez; kuyruk da neyin silinecegini ACIKCA tutar (anti-join ile
"sahipsiz satir" aramak devasa tabloda cok pahali olurdu).
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DevicePurgeJob(Base):
    __tablename__ = "device_purge_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # FK YOK — cihaz satiri bu is olusturuldugunda ZATEN silinmis oluyor.
    device_id: Mapped[int] = mapped_column(Integer, index=True)
    # Kimlik bilgisi denetim/goruntu icin kopyalanir; cihaz artik yok.
    device_code: Mapped[str] = mapped_column(String(50))
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # pending -> done | failed. "running" TUTULMAZ: worker tek surecte
    # (advisory lock ile) kosuyor ve parti parti ilerliyor; ara durum
    # tutmak, surec cokerse isi kalici olarak "running"de birakirdi.
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    rows_deleted: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
