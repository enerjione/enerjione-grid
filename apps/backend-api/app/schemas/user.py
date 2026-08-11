import re

from pydantic import BaseModel, EmailStr, field_validator

from app.models.enums import UserRole

#: Profil fotografi icin ust sinir (karakter). Base64 ~4/3 sisirir, yani
#: ~150 KB'lik bir goruntuye denk gelir. Istemci 192 piksele kucultup JPEG'e
#: cevirdigi icin tipik boyut 8-15 KB; bu sinir kotu niyetli/yanlis bir
#: istegin kullanici satirini sisirmesini engeller.
AVATAR_MAX_CHARS = 200_000

#: Yalnizca gomulu goruntu kabul edilir. `http(s)://` bir adres KABUL EDILMEZ:
#: disaridan cekilen bir profil resmi her sayfa acilisinda ucuncu bir sunucuya
#: istek atar (izleme) ve kapali sahada zaten yuklenmez.
_AVATAR_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,[A-Za-z0-9+/=]+$")

#: Rakam, bosluk, parantez, tire ve bastaki artı. Bicim ULKEYE GORE degisir;
#: burada amac gecerliligi kanitlamak degil, kolonun (32 karakter) tasmasini
#: ve icine metin kacmasini onlemek.
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-]{5,30}$")


def _normalize_phone(value: str | None) -> str | None:
    """Bos/bosluk -> None (numara SILINDI demek), aksi halde kirpilmis metin."""
    if value is None:
        return None
    temiz = value.strip()
    if not temiz:
        return None
    if not _PHONE_RE.match(temiz):
        raise ValueError("Telefon numarasi gecersiz. Ornek: +90 555 123 45 67")
    return temiz


def _normalize_avatar(value: str | None) -> str | None:
    """Bos -> None (fotograf KALDIRILDI), aksi halde dogrulanmis data URI."""
    if value is None:
        return None
    temiz = value.strip()
    if not temiz:
        return None
    if len(temiz) > AVATAR_MAX_CHARS:
        raise ValueError("Profil fotografi cok buyuk. Daha kucuk bir gorsel secin.")
    if not _AVATAR_RE.match(temiz):
        raise ValueError("Profil fotografi PNG/JPEG/WEBP olmali.")
    return temiz


class UserRead(BaseModel):
    id: int
    username: str
    email: str
    phone_number: str | None = None
    full_name: str
    role: UserRole
    language: str | None = None
    #: Profil fotografi — gomulu `data:` URI'si. NULL = fotograf yok, arayuz
    #: bas harflerden olusan yuvarlagi gosterir.
    avatar_url: str | None = None
    # Davet edildi ama henuz sifre belirlemedi (admin paneli "Davet bekliyor"
    # rozeti gosterir). UI bu durumdaki user'a "Daveti yeniden gonder" butonu
    # sunabilir.
    pending_invitation: bool = False

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    """Direkt admin tarafindan sifre belirlenerek user yaratma (eski akis).

    Yeni davet-tabanli akis icin UserInvite kullanin; bu schema geriye
    uyumluluk icin korunur ama UI'da tercih edilmemeli."""

    username: str
    email: EmailStr
    phone_number: str | None = None
    full_name: str
    password: str
    role: UserRole


class UserInvite(BaseModel):
    """Sifre belirlemeden user davet et — token uretilir, link/mail ile
    kullaniciya gonderilir. Kullanici setup-password sayfasinda ilk sifresini
    kendisi belirler.
    """

    username: str
    email: EmailStr
    phone_number: str | None = None
    full_name: str
    role: UserRole
    # true: SMTP yapilandirilmissa otomatik mail gonder. false ise sadece
    # link uretilir, admin link'i kullaniciya elle iletir.
    send_email: bool = True


class UserUpdate(BaseModel):
    email: EmailStr
    phone_number: str | None = None
    full_name: str
    role: UserRole


class ResetPasswordRequest(BaseModel):
    new_password: str


class SelfProfileUpdateRequest(BaseModel):
    """Kullanicinin KENDI profili.

    `phone_number` ve `avatar_url` buraya sonradan eklendi: ikisi de modelde
    (ve admin panelinde) vardi ama kullanici kendi kaydinda degistiremiyordu.
    Sonuc, bildirim tercihleri ekraninda "SMS: telefon numarasi eklenmemis"
    yazip kullaniciya numarayi girebilecegi HICBIR yer sunmamakti.

    Ikisi de OPSIYONEL ve `None` "degistirme" degil "TEMIZLE" demektir; arayuz
    her kaydetmede mevcut degerleri birlikte gonderir.
    """

    full_name: str
    email: EmailStr
    phone_number: str | None = None
    avatar_url: str | None = None

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalize_phone(v)

    @field_validator("avatar_url")
    @classmethod
    def _avatar(cls, v: str | None) -> str | None:
        return _normalize_avatar(v)


class LanguageUpdateRequest(BaseModel):
    """Kullanicinin tercih ettigi arayuz dili. Desteklenen kodlar: tr, en."""

    language: str


class SelfPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
