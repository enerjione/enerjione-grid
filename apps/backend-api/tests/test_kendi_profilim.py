"""Kullanicinin KENDI profili — telefon ve profil fotografi.

NEDEN VAR
---------
Telefon numarasi modelde ve admin panelinde vardi ama kullanici kendi
kaydinda degistiremiyordu: `SelfProfileUpdateRequest` yalnizca ad ve e-posta
tasiyordu. Bildirim tercihleri ekrani "SMS: telefon numarasi eklenmemis"
yazip kullaniciya numarayi girebilecegi HICBIR yer sunmuyordu.

Bu uctan gecen iki alan da serbest metindir ve dogrudan veritabanina yazilir;
dogrulamanin sessizce gevsemesi telefon kolonuna (32 karakter) sigmayan bir
degerin 500 ile patlamasi ya da profil fotografi diye 5 MB'lik bir dizginin
her kullanici listesinde tasinmasi demektir. Kurallar burada kilitleniyor.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.auth import update_me
from app.db.base import Base
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.user import AVATAR_MAX_CHARS, SelfProfileUpdateRequest

#: Gecerli, minik bir PNG data URI'si (icerik onemsiz — bicim onemli).
GECERLI_AVATAR = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture()
def kullanici(db) -> User:
    u = User(
        username="operator1",
        email="op@firma.com",
        full_name="Op Bir",
        hashed_password="x",
        role=UserRole.OPERATOR,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _istek(**kw) -> SelfProfileUpdateRequest:
    veri = {"full_name": "Op Bir", "email": "op@firma.com"}
    veri.update(kw)
    return SelfProfileUpdateRequest(**veri)


# --------------------------------------------------------------- TELEFON


def test_kullanici_kendi_telefonunu_KAYDEDEBILIR(db, kullanici):
    sonuc = update_me(_istek(phone_number="+90 555 123 45 67"), kullanici, db)
    assert sonuc.phone_number == "+90 555 123 45 67"
    db.refresh(kullanici)
    assert kullanici.phone_number == "+90 555 123 45 67"


def test_bos_telefon_numarayi_SILER(db, kullanici):
    kullanici.phone_number = "+905551234567"
    db.commit()
    update_me(_istek(phone_number="   "), kullanici, db)
    db.refresh(kullanici)
    assert kullanici.phone_number is None, "bos metin 'temizle' demek olmali"


@pytest.mark.parametrize(
    "gecersiz",
    [
        "abc",                       # metin
        "0555 <script>",             # enjeksiyon denemesi
        "12",                        # cok kisa
        "+" + "9" * 40,              # kolona (32) sigmaz
    ],
)
def test_gecersiz_telefon_REDDEDILIR(gecersiz):
    with pytest.raises(ValidationError):
        _istek(phone_number=gecersiz)


def test_yerel_bicimler_kabul_edilir():
    for gecerli in ["05551234567", "0 (555) 123-45-67", "+90 555 123 45 67"]:
        assert _istek(phone_number=gecerli).phone_number == gecerli.strip()


# --------------------------------------------------------- PROFIL FOTOGRAFI


def test_profil_fotografi_kaydedilir_ve_silinir(db, kullanici):
    update_me(_istek(avatar_url=GECERLI_AVATAR), kullanici, db)
    db.refresh(kullanici)
    assert kullanici.avatar_url == GECERLI_AVATAR

    update_me(_istek(avatar_url=""), kullanici, db)
    db.refresh(kullanici)
    assert kullanici.avatar_url is None, "bos metin fotografi kaldirmali"


def test_disaridan_ADRES_kabul_edilmez():
    # `http(s)://` bir adres her sayfa acilisinda ucuncu bir sunucuya istek
    # atar (izleme) ve kapali sahada zaten yuklenmez.
    with pytest.raises(ValidationError):
        _istek(avatar_url="https://ornek.com/foto.png")


def test_goruntu_OLMAYAN_veri_reddedilir():
    with pytest.raises(ValidationError):
        _istek(avatar_url="data:text/html;base64,PHNjcmlwdD4=")


def test_cok_buyuk_fotograf_REDDEDILIR():
    # Sinir olmadan her kullanici listesi megabaytlarca veri tasirdi.
    kocaman = "data:image/png;base64," + "A" * (AVATAR_MAX_CHARS + 1)
    with pytest.raises(ValidationError):
        _istek(avatar_url=kocaman)


# ------------------------------------------------------------- GERIYE UYUM


def test_alanlar_GONDERILMEZSE_temizlenir(db, kullanici):
    # `None` "dokunma" degil "temizle" demek: arayuz her kaydetmede iki alani
    # da mevcut degerleriyle gonderir. Bu sozlesme testte kilitli olmazsa
    # ilerideki bir "kismi guncelleme" degisikligi sessizce veri silerdi.
    kullanici.phone_number = "+905551234567"
    kullanici.avatar_url = GECERLI_AVATAR
    db.commit()

    update_me(_istek(), kullanici, db)
    db.refresh(kullanici)
    assert kullanici.phone_number is None
    assert kullanici.avatar_url is None


def test_ad_ve_eposta_hala_calisiyor(db, kullanici):
    sonuc = update_me(
        _istek(full_name="Yeni Ad", email="yeni@firma.com"), kullanici, db
    )
    assert sonuc.full_name == "Yeni Ad"
    assert sonuc.email == "yeni@firma.com"
