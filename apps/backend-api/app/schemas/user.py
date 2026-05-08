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

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    phone_number: str | None = None
    full_name: str
    password: str
    role: UserRole


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
