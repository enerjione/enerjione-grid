"""FTP ayarlarinin is mantigi — okuma, guncelleme, parola uretimi.

Katman: router -> BURASI -> models. Parola sifreleme/cozme yalnizca burada
yapilir; router ve diger servisler acik metinle calisir.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.ftp_settings import FtpSettings
from app.services.secrets_vault import decrypt_secret, encrypt_secret

#: Uretilen parola uzunlugu. Cihaz ekrani <20 karakter kabul ediyor; 16,
#: elle girilebilir ve yeterince guclu (yalitilmis saha aginda FTP kimligi).
_PAROLA_UZUNLUK = 16

#: Belirsiz karakterler DISARIDA (0/O, 1/l/I): parola cihazin FTP ekranina
#: ELLE girilecek ve yanlis okunan her karakter bir saha ziyareti demek.
#: SIMGE YOK — cihaz ekran klavyesinde simge girisi belirsiz/eksik olabilir;
#: harf+rakam her cihazda vardir. (seed_installer._generate_password kalibi,
#: simgesiz varyant.)
_KUCUK = "abcdefghijkmnopqrstuvwxyz"
_BUYUK = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_RAKAM = "23456789"


def generate_password() -> str:
    """Okunabilir, her siniftan en az bir karakter iceren parola uretir."""
    havuz = _KUCUK + _BUYUK + _RAKAM
    zorunlu = [
        secrets.choice(_KUCUK),
        secrets.choice(_BUYUK),
        secrets.choice(_RAKAM),
    ]
    kalan = [secrets.choice(havuz) for _ in range(_PAROLA_UZUNLUK - len(zorunlu))]
    karakterler = zorunlu + kalan
    # Zorunlu karakterler hep basta kalirsa bicim tahmin edilebilir olur.
    secrets.SystemRandom().shuffle(karakterler)
    return "".join(karakterler)


def get_settings(db: Session) -> FtpSettings:
    """Singleton satiri dondurur; yoksa varsayilanlarla olusturur."""
    row = db.get(FtpSettings, 1)
    if row is None:
        row = FtpSettings(id=1)
        db.add(row)
        db.flush()
    return row


def get_password(row: FtpSettings) -> str | None:
    """Harici sunucu parolasini acik metne cevirir. Ayarlanmamissa None."""
    if not row.password_enc:
        return None
    return decrypt_secret(row.password_enc)


def get_embedded_password(row: FtpSettings) -> str | None:
    """Dahili sunucu parolasini acik metne cevirir. Ayarlanmamissa None —
    ftp-server o durumda env'deki FTP_PASSWORD ile devam eder."""
    if not row.embedded_password_enc:
        return None
    return decrypt_secret(row.embedded_password_enc)


#: Acik metin gelen parola alanlari -> sifreli kolon adlari.
_PAROLA_ALANLARI = {
    "password": "password_enc",
    "embedded_password": "embedded_password_enc",
}


def update_settings(
    db: Session, *, updates: dict, actor: str | None = None
) -> FtpSettings:
    """Alanlari gunceller. Parola alanlari acik metin gelir, sifreli yazilir.

    Dahili ve harici kimlikler AYRI alanlardir; birini guncellemek digerine
    dokunmaz (bkz. model docstring — sizinti sahada yasandi).

    Commit ETMEZ — cagiran taraf transaction'i yonetir (audit ile birlikte).
    """
    row = get_settings(db)
    for alan, deger in updates.items():
        hedef_kolon = _PAROLA_ALANLARI.get(alan)
        if hedef_kolon is not None:
            if deger:
                setattr(row, hedef_kolon, encrypt_secret(deger))
            continue
        setattr(row, alan, deger)
    row.updated_by = actor
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row
