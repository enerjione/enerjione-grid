from pydantic import BaseModel

from app.models.enums import UserRole


class LoginRequest(BaseModel):
    username: str
    password: str
    # "Beni hatirla" akisi — true ise daha uzun TTL'li token verilir
    # (config.remember_me_token_minutes). Frontend checkbox doluysa gonderir.
    remember_me: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    username: str
    # Frontend bunu gorurse ChangePasswordModal'i zorla acar; kullanici
    # parolayi degistirene kadar diger sayfalara navigation engellenebilir
    # (UI takdiri). True donmesi gereken durumlar:
    #   - seed_installer ile yaratilan admin (default ChangeMe123!)
    #   - Admin baska bir kullanicinin sifresini reset etti (must_change=True)
    must_change_password: bool = False


class SetupPasswordRequest(BaseModel):
    """Davet edilmis kullanici ilk sifresini token ile belirler."""

    token: str
    new_password: str


class InviteUserResponse(BaseModel):
    """Admin yeni user davet ettiginde donen sonuc.

    `setup_url` her zaman doludur — operator link'i panoda kopyalayip
    kullaniciya iletebilir. `email_sent` true ise SMTP yapilandirmasi
    aktif ve mail otomatik gonderilmistir.
    """

    user_id: int
    username: str
    setup_url: str
    expires_at: str
    email_sent: bool
