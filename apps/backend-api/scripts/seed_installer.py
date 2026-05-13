"""Ilk installer kullanicisini olustur (idempotent).

Davranis:
  - User YOKSA: default username + parola ile yarat, `must_change_password=True`
    isaretle. Kullanici ilk login'de parolayi degistirmek zorunda kalir.
  - User VARSA: HICBIR DEGISIKLIK YAPMA. Eski davranis (sifreyi resetle)
    her install.ps1/install.sh kosumunda admin parolasini ChangeMe123!'a
    geri donduruyordu — guvenlik acigi. Yeni davranista var olan user
    sifresini operator kendi yonetir (forgot password akisi UI'da).

Kurtarma: Admin sifresini unutursa SQL ile manuel reset:
  UPDATE users SET hashed_password=NULL,
                   must_change_password=TRUE,
                   password_reset_token_hash=NULL
   WHERE username='installer';
Sonra UI'dan "Sifremi unuttum" -> mail/link akisi (admin baska bir user
ile login olabilirse panelden de yapilabilir).
"""

from sqlalchemy import select, text

from app.db.session import SessionLocal, engine
from app.models.enums import UserRole
from app.models.user import User
from app.services.auth_service import get_password_hash


DEFAULT_USERNAME = "installer"
DEFAULT_PASSWORD = "ChangeMe123!"
DEFAULT_EMAIL = "installer@local"
DEFAULT_FULL_NAME = "Default Installer"


def ensure_enum_value() -> None:
    """PostgreSQL userrole enum'una INSTALLER degeri ekle (idempotent)."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as ac_conn:
        ac_conn.execute(text("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'INSTALLER'"))


def run() -> None:
    ensure_enum_value()
    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.username == DEFAULT_USERNAME))
        if existing is not None:
            # KRITIK: idempotent run her seferinde sifreyi reset etmiyor.
            # Operator default-sifreyi degistirdiyse korunur.
            print(
                f"Installer user already exists (username={DEFAULT_USERNAME}); "
                "no changes."
            )
            return

        installer = User(
            username=DEFAULT_USERNAME,
            email=DEFAULT_EMAIL,
            full_name=DEFAULT_FULL_NAME,
            hashed_password=get_password_hash(DEFAULT_PASSWORD),
            role=UserRole.INSTALLER,
            must_change_password=True,
        )
        db.add(installer)
        db.commit()
        print(
            f"Installer user created (username={DEFAULT_USERNAME}, "
            f"password={DEFAULT_PASSWORD}). "
            "MUST_CHANGE_PASSWORD=True - ilk login'de degistirmeniz istenecek."
        )
    finally:
        db.close()


if __name__ == "__main__":
    run()
