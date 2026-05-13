from pydantic import BaseModel, EmailStr

from app.models.enums import UserRole


class UserRead(BaseModel):
    id: int
    username: str
    email: str
    phone_number: str | None = None
    full_name: str
    role: UserRole
    language: str | None = None
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
    full_name: str
    email: EmailStr


class LanguageUpdateRequest(BaseModel):
    """Kullanicinin tercih ettigi arayuz dili. Desteklenen kodlar: tr, en."""

    language: str


class SelfPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
