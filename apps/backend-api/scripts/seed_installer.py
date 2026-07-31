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
DEFAULT_EMAIL = "installer@local"
DEFAULT_FULL_NAME = "Default Installer"

DEFAULT_PASSWORD = "ChangeMe123!"

# Kurulum kolayligi icin varsayilan parola SABIT tutuluyor (bilincli karar).
#
# ARTAKALAN RISK — bilinerek kabul edildi:
#   Parola herkese acik kaynak kodda yaziyor. Kurulum bittikten sonra, siz ilk
#   login'i yapana kadar gecen surede ayni aga erisen biri bu hesapla girip
#   parolayi KENDISI degistirebilir; o andan itibaren en yetkili hesap onun
#   olur ve siz disarida kalirsiniz.
#
#   `must_change_password` zorlamasi (app/api/deps.py) bu pencereyi DARALTIR
#   ama KAPATMAZ: bayrak acikken yalnizca /auth/me, sifre degisimi ve cikis
#   uclari calisir — ama sifre degisimi tam da saldirganin ihtiyaci olan sey.
#
#   Azaltim: kurulumdan hemen sonra ilk isiniz parolayi degistirmek olsun.
#
# `E1_INSTALLER_PASSWORD` ile kuruluma ozel parola verilebilir; verildiginde
# sabit varsayilan HIC kullanilmaz. Sahaya cikan cihazlarda bu onerilir.
_PASSWORD_ENV = "E1_INSTALLER_PASSWORD"


def _make_password() -> tuple[str, bool]:
    """(parola, disaridan_mi_geldi) doner."""
    import os

    disaridan = (os.getenv(_PASSWORD_ENV) or "").strip()
    if disaridan:
        return disaridan, True
    return DEFAULT_PASSWORD, False


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

        password, disaridan = _make_password()
        installer = User(
            username=DEFAULT_USERNAME,
            email=DEFAULT_EMAIL,
            full_name=DEFAULT_FULL_NAME,
            hashed_password=get_password_hash(password),
            role=UserRole.INSTALLER,
            # Backend bunu ARTIK ZORLUYOR: bayrak aciksa yalnizca /auth/me,
            # sifre degisimi ve cikis uclari calisir; digerleri 403 doner.
            # Bkz. app/api/deps.py. Onceden bu yalnizca yanittaki bir bayrakti
            # ve arayuzu hic calistirmayan biri tamamen atlayabiliyordu.
            must_change_password=True,
        )
        db.add(installer)
        db.commit()
        print(
            f"Installer user created (username={DEFAULT_USERNAME}, "
            f"password={password})."
        )
        if not disaridan:
            print(
                "UYARI: bu parola SABIT ve kaynak kodda aciktir. Kurulumdan "
                "sonra ilk isiniz onu degistirmek olsun — siz degistirene kadar "
                "ayni aga erisen biri hesabi ele gecirebilir. Kuruluma ozel "
                f"parola icin {_PASSWORD_ENV} ortam degiskenini kullanin."
            )
        print(
            "MUST_CHANGE_PASSWORD=True - ilk login'de degistirmeniz istenecek "
            "(backend zorunlu kilar; degistirilene kadar diger uclar 403 doner)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    run()
