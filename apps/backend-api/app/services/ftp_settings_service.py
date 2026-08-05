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
    """Sifreli parolayi acik metne cevirir. Ayarlanmamissa None."""
    if not row.password_enc:
        return None
    return decrypt_secret(row.password_enc)


def update_settings(
    db: Session, *, updates: dict, actor: str | None = None
) -> FtpSettings:
    """Alanlari gunceller. `password` acik metin gelir, sifreli yazilir.

    Commit ETMEZ — cagiran taraf transaction'i yonetir (audit ile birlikte).
    """
    row = get_settings(db)
    for alan, deger in updates.items():
        if alan == "password":
            if deger:
                row.password_enc = encrypt_secret(deger)
            continue
        setattr(row, alan, deger)
    row.updated_by = actor
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row
