"""Migration hedefi ACIK precedence ile secilir — regresyon kanidi.

YASANMIS OLAY
-------------
`alembic_migrations/env.py` hedefi KOSULSUZ `settings.database_url` ile
eziyordu. Cagiran hedefi acikca verse bile sessizce yok sayiliyordu; bir
migration testi bu yuzden GELISTIRICININ veritabanini 0063'ten 0071'e tasidi.

Bu dosya, o davranisin geri gelmesini SESSIZ olmaktan cikarir. Testler SAF:
gercek bir veritabani gerekmez, hicbir sey mutasyona ugramaz.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# `alembic_migrations/` bir paket degil (env.py'yi import etmek migration
# KOSTURUR). Saf hedef-secim modulu dosya yolundan alinir.
_MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic_migrations"
if str(_MIGRATIONS) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS))

import hedef_secimi  # noqa: E402

ACIK = "postgresql+psycopg2://u:p@127.0.0.1:15433/e1_test_acik"
ORTAM = "postgresql+psycopg2://u:p@127.0.0.1:15433/e1_test_ortam"
AYAR = "postgresql+psycopg2://u:p@localhost:5432/enerjione_grid"


# --------------------------------------------------------------------------
# A05 — acikca verilen hedef AYNEN kullanilir
# --------------------------------------------------------------------------
def test_A05_acik_url_aynen_kullanilir():
    url, kaynak = hedef_secimi.hedef_url(ACIK, None, AYAR)
    assert url == ACIK
    assert kaynak == "explicit alembic url"


# --------------------------------------------------------------------------
# A06 — acik hedef, uygulama ayari FARKLI olsa bile EZILMEZ
# --------------------------------------------------------------------------
def test_A06_acik_url_settings_tarafindan_EZILMEZ():
    """Regresyonun tam kalbi: ayar baska bir DB gosteriyor, hedef degismemeli."""
    url, _ = hedef_secimi.hedef_url(ACIK, None, AYAR)
    assert url != AYAR, "acik hedef settings.database_url ile EZILDI"
    assert url == ACIK


def test_A06b_acik_url_ortam_degiskenini_de_yener():
    url, kaynak = hedef_secimi.hedef_url(ACIK, ORTAM, AYAR)
    assert url == ACIK
    assert kaynak == "explicit alembic url"


# --------------------------------------------------------------------------
# Ortam degiskeni basamagi
# --------------------------------------------------------------------------
def test_ortam_degiskeni_ayari_yener():
    url, kaynak = hedef_secimi.hedef_url(None, ORTAM, AYAR)
    assert url == ORTAM
    assert kaynak == hedef_secimi.ORTAM_ADI


# --------------------------------------------------------------------------
# Production CLI yolu — hicbiri yoksa uygulama DB'si
# --------------------------------------------------------------------------
def test_hicbiri_yoksa_uygulama_DB_si():
    """`alembic upgrade head` (normal production) dogru DB'yi bulmali."""
    url, kaynak = hedef_secimi.hedef_url(None, None, AYAR)
    assert url == AYAR
    assert kaynak == "settings.database_url"


@pytest.mark.parametrize("bos", [None, "", "   "])
def test_bos_degerler_acik_hedef_sayilmaz(bos):
    url, _ = hedef_secimi.hedef_url(bos, bos, AYAR)
    assert url == AYAR


def test_eski_placeholder_acik_hedef_SAYILMAZ():
    """`alembic.ini`'deki eski placeholder gercek bir hedef degil.

    "Acik hedef" sayilsaydi HER production `alembic upgrade head` cagrisi
    `placeholder@localhost/placeholder` adresine kosmaya calisirdi.
    """
    url, kaynak = hedef_secimi.hedef_url(hedef_secimi.ESKI_PLACEHOLDER, None, AYAR)
    assert url == AYAR
    assert kaynak == "settings.database_url"


# --------------------------------------------------------------------------
# M1 — MUTASYON: eski (kosulsuz ezen) davranis geri gelirse test PATLAR
# --------------------------------------------------------------------------
def test_M1_kosulsuz_ezme_davranisi_YAKALANIR():
    """Eski tek satirin esdegeri burada canlandirilir ve REDDEDILIR.

    Amac: "bu testler regresyonu gercekten yakaliyor mu?" sorusunun kaniti.
    Asagidaki `eski_davranis` env.py'nin silinen satirinin birebir mantigidir.
    """

    def eski_davranis(acik_url, ortam_url, ayar_url):  # noqa: ANN001, ARG001
        return ayar_url, "settings.database_url"  # kosulsuz ezme

    # Eski davranis A06'yi GECEMEZ:
    eski_url, _ = eski_davranis(ACIK, None, AYAR)
    assert eski_url == AYAR, "mutasyon kurulumu hatali"

    # Yani A06'nin iddiasi eski kod icin YANLIS olurdu -> test gercekten koruyor.
    yeni_url, _ = hedef_secimi.hedef_url(ACIK, None, AYAR)
    assert yeni_url != eski_url, (
        "yeni precedence eski kosulsuz-ezme davranisiyla AYNI sonucu veriyor — "
        "A06 regresyonu YAKALAYAMAZ"
    )


# --------------------------------------------------------------------------
# §19 — gozlemlenebilirlik: log'a PAROLA yazilmaz
# --------------------------------------------------------------------------
def test_parolasiz_ozet_parola_SIZDIRMAZ():
    ozet = hedef_secimi.parolasiz(
        "postgresql+psycopg2://kullanici:COK_GIZLI@db.saha:5432/enerjione_grid"
    )
    assert "COK_GIZLI" not in ozet
    assert "kullanici" not in ozet
    assert ozet == "db.saha:5432/enerjione_grid"


def test_parolasiz_bozuk_url_da_patlamaz():
    """Bicimsiz girdi log satirini PATLATMAMALI — teshis kaybolmasin."""
    ozet = hedef_secimi.parolasiz("bu bir url degil")
    assert isinstance(ozet, str) and ozet


def test_parolasiz_parolayi_her_bicimde_dusurur():
    """Parola URL'in HANGI parcasinda olursa olsun ozete GIRMEZ."""
    for ham in (
        "postgresql+psycopg2://u:GIZLI@h:5432/db",
        "postgresql://u:GIZLI@h/db",
        "postgresql+psycopg2://u:GIZLI@h:5432/db?sslmode=require",
    ):
        assert "GIZLI" not in hedef_secimi.parolasiz(ham)
