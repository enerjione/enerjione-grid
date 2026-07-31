"""Zorunlu sifre degisimi SUNUCU tarafinda uygulanir (denetim A2).

YASANAN ACIK
------------
`must_change_password` yalnizca login YANITINDA donen bir bayrakti; hicbir
backend kontrolu onu okumuyordu. Yani varsayilan kurulum parolasiyla yapilan
login TAM YETKILI bir JWT + oturum cookie'si veriyordu.

Tek engel SPA'nin gosterdigi "sifreni degistir" modaliydi — ve istemciyi hic
calistirmayan biri (dogrudan HTTP istegi) onu tamamen atlayabiliyordu. O token
ile installer rolunun her seyi yapilabiliyordu: gateway token'larini okumak,
API anahtari uretmek, uzaktan bakim kapisini acmak, yedekten geri yukleme,
kullanici yaratmak.

ARTAKALAN RISK (bilinerek kabul edildi)
---------------------------------------
Varsayilan parola SABIT kalmaya devam ediyor (urun karari). Bu kapi pencereyi
daraltir ama kapatmaz: saldirgan sifre degisimi ucunu kullanip hesabi kendi
uzerine alabilir. Azaltim kurulum sonrasi ilk isin parola degisimi olmasidir;
`E1_INSTALLER_PASSWORD` ile kuruluma ozel parola verilebilir.
"""

from __future__ import annotations

import pytest

from app.api import deps


class _SahteURL:
    def __init__(self, path: str) -> None:
        self.path = path


class _SahteRequest:
    def __init__(self, path: str) -> None:
        self.url = _SahteURL(path)


# ------------------------------------------------------------ izinli uclar


@pytest.mark.parametrize(
    "yol",
    [
        "/api/v1/auth/me",
        "/api/v1/auth/me/change-password",
        "/api/v1/auth/setup-password",
        "/api/v1/auth/logout",
        "/api/v1/auth/me/",           # sondaki slash
    ],
)
def test_sifre_degisimi_icin_gerekli_uclar_ACIK(yol):
    """Kullanici sifresini degistirebilmeli ve cikabilmeli.

    Bu kume kapansaydi hesap tamamen kilitlenirdi — kurulum yapilamaz hale
    gelirdi.
    """
    assert deps._password_change_exempt(_SahteRequest(yol)) is True


# ------------------------------------------------------------ kapali uclar


@pytest.mark.parametrize(
    "yol",
    [
        "/api/v1/gateways",              # gateway token'lari
        "/api/v1/api-keys",              # kalici API anahtari uretimi
        "/api/v1/backups/restore",       # yedekten geri yukleme
        "/api/v1/users",                 # kullanici yaratma
        "/api/v1/remote-access/grant",   # uzaktan bakim kapisi
        "/api/v1/devices",
        "/api/v1/auth/ws-ticket",        # canli veri akisi
        "/api/v1/auth/me/language",      # /auth/me ile karistirilmamali
        "/api/v1/auth/me/fcm-token",
    ],
)
def test_diger_uclar_KAPALI(yol):
    """Denetimde sayilan somut kotuye kullanim yollari.

    `/auth/me/language` ve `/auth/me/fcm-token` ozellikle onemli: `/auth/me`
    izinli oldugu icin naif bir "startswith" kontrolu bunlari da acardi.
    """
    assert deps._password_change_exempt(_SahteRequest(yol)) is False


def test_ws_ticket_KAPALI_olmali():
    """Ayri test: acik kalsaydi sifre degistirmeden canli veri akisi alinirdi."""
    assert deps._password_change_exempt(_SahteRequest("/api/v1/auth/ws-ticket")) is False


# ----------------------------------------------------- yapisal: kapi yerinde


def test_kapi_get_current_user_ICINDE():
    """Kontrol merkezi bagimlilikta olmali.

    Tek tek router'lara serpistirilseydi, yeni eklenen bir uc korumasiz
    kalirdi ve bunu kimse fark etmezdi. `get_current_user` tum yetkili
    uclarin ortak gecididir.
    """
    import inspect

    kaynak = inspect.getsource(deps.get_current_user)
    assert "must_change_password" in kaynak, (
        "sifre degisimi kapisi get_current_user icinde degil — yeni uclar "
        "korumasiz kalir"
    )
    assert "PASSWORD_CHANGE_REQUIRED" in kaynak


def test_seed_installer_bayragi_ACIK_kuruyor():
    """Kapi ancak bayrak kurulursa ise yarar."""
    from pathlib import Path

    kaynak = (
        Path(deps.__file__).resolve().parents[2] / "scripts" / "seed_installer.py"
    ).read_text(encoding="utf-8")
    assert "must_change_password=True" in kaynak
