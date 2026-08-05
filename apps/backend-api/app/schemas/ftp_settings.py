"""FTP sunucu ayarlari I/O semalari.

Cihaz ekrani sinirlari SEMADA uygulanir: kullanici adi <30, parola <20
karakter. Sinir DB'de degil burada — kullanici hatayi kaydederken gormeli,
cihazin basina gidip "parola sigmadi" diye donmemeli.

Parola GET yanitinda ACIK doner: cihazin FTP ekranina elle girilecegi icin
kullanicinin onu OKUYABILMESI gerekir. Bu bir kullanici parolasi degil,
cihaz filosunun ortak servis kimligidir; ucu yalnizca engineer/installer
gorur ve degisiklik denetim kaydina yazilir.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FtpSettingsRead(BaseModel):
    mode: Literal["gomulu", "harici"] = "gomulu"

    # --- dahili (gomulu) sunucu ---
    embedded_username: str = "device"
    # Acik metin (bkz. modul docstring). None = henuz ayarlanmadi; dahili
    # sunucu o durumda .env'deki FTP_PASSWORD ile calismaya devam eder.
    embedded_password: str | None = None
    # PASV adresi — arayuz kaydederken tarayici adresinden otomatik doldurur;
    # kullanicidan IP istenmez. Cihaz ekranina girilecek adres olarak da
    # gosterilir.
    embedded_host: str | None = None

    # --- harici (musteri) sunucusu ---
    host: str | None = None
    port: int = 21
    username: str = "device"
    password: str | None = None

    directory: str = "/SN20/FOTA/"
    poll_interval_sec: int = 300
    updated_by: str | None = None
    updated_at: datetime | None = None


class FtpSettingsUpdate(BaseModel):
    mode: Literal["gomulu", "harici"] | None = None

    # Dahili kimlik — harici alanlardan AYRI; birini gondermek digerine
    # dokunmaz. Cihaz ekrani sinirlari ikisinde de gecerli (kullanici <30,
    # parola <20): cihazlar hangi moddaysa o kimlikle giris yapiyor.
    embedded_username: str | None = Field(default=None, min_length=1, max_length=29)
    embedded_password: str | None = Field(default=None, min_length=6, max_length=19)
    embedded_host: str | None = Field(default=None, max_length=200)

    host: str | None = Field(default=None, max_length=200)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=29)
    password: str | None = Field(default=None, min_length=6, max_length=19)
    directory: str | None = Field(default=None, max_length=200)
    # Alt sinir 60 sn: daha sik yoklama musterinin FTP sunucusunu gereksiz
    # yorar; config degisikligi zaten dakikalar mertebesinde bir istir.
    poll_interval_sec: int | None = Field(default=None, ge=60, le=86400)


class FtpEventRow(BaseModel):
    """Son FTP hareketi — kim baglandi, hangi dosya gitti/geldi.

    `metadata` ham olay meta verisidir (ip, dosya adi, degisen alanlar...);
    arayuz ham `message` yerine bundan derli toplu bir satir kurar. `message`
    yedek olarak kalir — bilinmeyen olay tipi de bos gorunmesin.
    """

    event_type: str
    severity: str
    message: str
    device_code: str | None = None
    created_at: datetime
    metadata: dict | None = None


class FtpServerHealth(BaseModel):
    """Gomulu ftp-server'in anlik durumu (health ucundan)."""

    reachable: bool
    # Sunucunun SU AN kabul ettigi kullanici adi. Ayarlardakiyle farkliysa
    # kimlik degisikligi henuz yansimamis demektir (<=30 sn).
    username: str | None = None
    connections: int | None = None
    # username == ayarlardaki kullanici. Kimlik degisiminden sonraki ~30
    # saniyede False gorunur; kullanici "neden giremiyorum" sorusunun
    # cevabini burada gorur.
    synced: bool | None = None


class FtpStatusRead(BaseModel):
    """FTP baglanti durumu paneli: sunucu sagligi + son hareketler."""

    mode: Literal["gomulu", "harici"]
    # Yalnizca gomulu modda dolu — harici sunucunun health ucu yok; onun
    # durumu 'Baglantiyi sina' ve son yoklama olaylarindan izlenir.
    server: FtpServerHealth | None = None
    events: list[FtpEventRow]


class FtpTestResult(BaseModel):
    """Harici FTP baglanti sinamasi sonucu.

    `ok=False` bir HTTP hatasi DEGILDIR — sinama calisti ve sonuc olumsuz.
    Ayrinti `detail` icinde; kullanici neyin patladigini (baglanti, kimlik,
    dizin) gormeli.
    """

    ok: bool
    detail: str
    # Dizinde gorulen `<seri>_Configuration.csv` sayisi — dogru dizine
    # baktigimizin en hizli kaniti.
    config_files: int | None = None
