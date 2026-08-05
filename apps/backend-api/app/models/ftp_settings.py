"""FTP sunucu ayarlari — singleton bir satir (id=1).

Cihazlar config/firmware dosyalarini FTP uzerinden alir/yazar. Iki mod var:

  gomulu : cihazlar BIZIM ftp-server container'imiza baglanir. Sahada internet
           yokken tek calisan secenek. Kimlik bilgisi (kullanici/parola) bu
           tablodan yonetilir; ftp-server bunu /internal/ftp-credentials
           ucundan kisa araliklarla ceker — yani parola degisince yeniden
           baslatma GEREKMEZ.
  harici : hem bizim yazilim hem cihazlar MUSTERININ FTP sunucusuna baglanir.
           Biz o sunucuya ftplib ile istemci olarak cikariz: config yazariz,
           cihazin yazdigini yoklama (poll) ile okuruz (bkz. ftp_poll_worker).

NEDEN DB'DE, ENV'DE DEGIL
-------------------------
Parola cihazin FTP ekranina ELLE girilir; sahada parola degistirmek bir
muhendislik ekrani isi olmali, container yeniden baslatma isi degil. Env
yalnizca ILK ACILIS varsayilani olarak kalir (ftp-server backend'e
ulasamazsa oradan devam eder).

Parola `secrets_vault` ile sifreli saklanir (enc:v1:... formati).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Mod degerleri. Turkce — surum kaynaklari ("sablon", "cihazdan_cekildi")
#: ile ayni sozluk uzayinda kalsin.
FTP_MODLARI = ("gomulu", "harici")


class FtpSettings(Base):
    __tablename__ = "ftp_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # "gomulu" | "harici" — bkz. FTP_MODLARI.
    mode: Mapped[str] = mapped_column(String(10), default="gomulu")
    # Harici modda musterinin FTP sunucusu. Gomulu modda BILGI amacli tutulur
    # (cihaz ekranina girilecek adres) ama baglanti icin kullanilmaz.
    host: Mapped[str | None] = mapped_column(String(200), nullable=True)
    port: Mapped[int] = mapped_column(Integer, default=21)
    # Cihaz ekrani sinirlari: kullanici adi <30, parola <20 karakter.
    # Sema katmani da ayni siniri uygular; buradaki genislik DB tavani.
    username: Mapped[str] = mapped_column(String(30), default="device")
    # secrets_vault ile sifreli ("enc:v1:..."). Bos = henuz ayarlanmadi;
    # ftp-server o durumda env'deki FTP_PASSWORD ile devam eder.
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cihazlarin dosya yazdigi/okudugu dizin. Gomulu modda kok dizine gore,
    # harici modda sunucudaki mutlak/goreli yol. Cihaz ekranindaki "Dir"
    # alanina da bu girilir.
    directory: Mapped[str] = mapped_column(String(200), default="/")
    # Harici mod yoklama araligi (saniye). Gomulu modda kullanilmaz — orada
    # olaylar aninda gelir (ftp-server callback'leri).
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    updated_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
