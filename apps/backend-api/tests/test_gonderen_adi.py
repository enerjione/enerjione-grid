"""Mektupta gorunen GONDEREN ADI — cozum sirasi.

Bu ad musteriye giden her mektupta gorunuyor ve DORT ayri yerden cagriliyor
(alarm e-postasi, atama e-postasi, bildirim dagitimi, SMTP test gonderimi).
Sira yanlis olursa hicbir yer patlamaz; sadece mektup baska bir isimle gider
ve bunu ancak alici fark eder.

Ozellikle iki davranis kilitleniyor:

  * ACIK ALAN KAZANIR. Kullanici Mail Ayarlari'na bir ad girdiyse Proje
    Ayarlari'ndaki hicbir sey onu ezemez.
  * BOSKEN ESKI DAVRANIS SURER. Alan bos olan mevcut kurulumlarda gonderen
    adi bu surumle degismemeli; aksi halde kimse istemeden musteriye giden
    mektuplarin adi degisirdi.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.notification_settings import NotificationSettings
from app.models.project_settings import ProjectSettings
from app.services.notification_settings_service import resolve_sender_name


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


def _mail(db, ad: str = "") -> None:
    db.add(NotificationSettings(id=1, smtp_from_name=ad))
    db.flush()


def _proje(db, **kw) -> None:
    db.add(ProjectSettings(id=1, **kw))
    db.flush()


def test_ACIK_ALAN_proje_ayarlarini_EZER(db):
    _mail(db, "Batman Hat Izleme")
    _proje(db, site_title="Sekme Basligi", project_name="Proje", customer_name="Musteri")
    assert resolve_sender_name(db) == "Batman Hat Izleme"


def test_bos_alanda_ESKI_ZINCIR_surer(db):
    """Alan bosken davranis DEGISMEMELI — yoksa mevcut kurulumlarin gonderen
    adi bu surumle kendiliginden degisirdi."""
    _mail(db, "")
    _proje(db, site_title="Sekme Basligi", project_name="Proje", customer_name="Musteri")
    assert resolve_sender_name(db) == "Sekme Basligi"


def test_zincir_sirasi_site_title_project_name_customer_name(db):
    _mail(db, "")
    _proje(db, project_name="Proje", customer_name="Musteri")
    assert resolve_sender_name(db) == "Proje"

    db.query(ProjectSettings).delete()
    _proje(db, customer_name="Musteri")
    assert resolve_sender_name(db) == "Musteri"


def test_sadece_BOSLUK_girilmis_ad_yok_sayilir(db):
    """Bosluk 'girilmis deger' degildir; zincire dusulmeli."""
    _mail(db, "   ")
    _proje(db, project_name="Proje")
    assert resolve_sender_name(db) == "Proje"


def test_hicbiri_yoksa_None(db):
    """Cagiran taraf PRODUCT_NAME'e duser (bkz. build_from_header)."""
    _mail(db, "")
    assert resolve_sender_name(db) is None


def test_mail_ayari_kaydi_hic_yoksa_zincire_dusulur(db):
    """Kurulumun ilk aninda `notification_settings` satiri henuz olusmamis
    olabilir; bu, gonderen adini cozulemez yapmamali."""
    _proje(db, project_name="Proje")
    assert resolve_sender_name(db) == "Proje"
