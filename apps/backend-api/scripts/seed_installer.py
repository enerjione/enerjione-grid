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

# SABIT VARSAYILAN PAROLA YOK — parola her kurulumda RASTGELE uretilir.
#
# ESKIDEN `DEFAULT_PASSWORD = "ChangeMe123!"` sabiti vardi ve artakalan risk
# "bilinerek kabul edilmis"ti. Kabulun dayanagi deponun OZEL olmasiydi: parolayi
# ogrenmek icin once kaynak koda erisim gerekiyordu. Depo 2026-08-05'te herkese
# acik hale gelince o dayanak ortadan kalkti — parola artik tek bir arama
# sorgusu uzakta ve saldirgan hangi urunu aradigini da biliyor.
#
# Risk soyleydi: kurulum bittikten sonra siz ilk login'i yapana kadar gecen
# surede ayni aga erisen biri bu hesapla girip parolayi KENDISI degistirir;
# o andan itibaren en yetkili hesap onun olur ve siz disarida kalirsiniz.
# `must_change_password` zorlamasi (app/api/deps.py) bu pencereyi DARALTIR ama
# KAPATMAZ: bayrak acikken sifre degisimi ucu calisir — tam da saldirganin
# ihtiyaci olan sey.
#
# Rastgele parola bu pencereyi tamamen kapatir: parola yalnizca kurulum
# ciktisinda gorunur, hicbir yerde yazili degildir.
#
# `E1_INSTALLER_PASSWORD` ile kuruluma ozel parola verilebilir; verildiginde
# uretim yapilmaz. Toplu saha kurulumlarinda merkezi parola dagitimi icin.
_PASSWORD_ENV = "E1_INSTALLER_PASSWORD"

# Parola ilk girişte zaten degistiriliyor; uzunluk kirilmazlik icin degil,
# kurulum ciktisindan ELLE KOPYALANABILIR olmasi icin sinirli tutuldu.
_GENERATED_LENGTH = 20


def _generate_password() -> str:
    """Kriptografik olarak guvenli, parola politikasini KESIN saglayan parola.

    `secrets.token_urlsafe` TEK BASINA YETMEZ: ciktisi yalnizca harf, rakam,
    `-` ve `_` icerir ve her kosumda buyuk harf/rakam GARANTI DEGILDIR. Parola
    politikasi (app/services/auth_service.py) her sinifi zorunlu tutuyor; garanti
    olmayan bir uretici, binde bir kosumda politikayi saglamayan parola uretir
    ve kurulum o cihazda -yalnizca o cihazda- sifre degistirilemez halde kalir.
    Bu yuzden her siniftan en az bir karakter ONCE secilip sonra karistiriliyor.

    Belirsiz karakterler (0/O, 1/l/I) DISARIDA: parola kurulum ciktisindan elle
    okunup yazilacak; "sifre yanlis" diye geri donen her cihaz saha ziyareti
    demektir.
    """
    import secrets

    kucuk = "abcdefghijkmnopqrstuvwxyz"  # l yok
    buyuk = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # I, O yok
    rakam = "23456789"  # 0, 1 yok
    simge = "!@#%^&*+-="  # kabuk/URL'de sorun cikaranlar disarida

    havuz = kucuk + buyuk + rakam + simge
    zorunlu = [
        secrets.choice(kucuk),
        secrets.choice(buyuk),
        secrets.choice(rakam),
        secrets.choice(simge),
    ]
    kalan = [secrets.choice(havuz) for _ in range(_GENERATED_LENGTH - len(zorunlu))]

    karakterler = zorunlu + kalan
    # `random.shuffle` DEGIL: zorunlu karakterler hep bastaki 4 pozisyonda
    # kalirsa parola bicimi tahmin edilebilir olur.
    secrets.SystemRandom().shuffle(karakterler)
    return "".join(karakterler)


def _make_password() -> tuple[str, bool]:
    """(parola, disaridan_mi_geldi) doner."""
    import os

    disaridan = (os.getenv(_PASSWORD_ENV) or "").strip()
    if disaridan:
        return disaridan, True
    return _generate_password(), False


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
