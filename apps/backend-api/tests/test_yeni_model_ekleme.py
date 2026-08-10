"""Yeni bir cihaz modeli SURUM CIKARMADAN eklenebilmeli.

NEDEN
-----
Sinyal katalogu API'den duzenlenebiliyor, ama model listesi kodda sabitti.
Sonuc tutarsizdi: yeni bir modelin sinyallerini girebiliyordunuz, ama o
modeli hicbir cihaza atayamiyordunuz ve kendi girdiginiz sinyalleri model
filtresiyle listeleyemiyordunuz (400 "Unknown device model").

Bir DNP3 modelinin adres haritasi VERIDIR. Yeni bir Horstmann modeli — ya da
ileride baska bir marka — eklemek icin kod degistirip imaj cikarmak
gerekmemeli.

Bu testler o yolun ACIK kaldigini kilitler.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data.device_models import (
    BUILTIN_MODELS,
    NON_SELECTABLE_MODELS,
    is_valid_model,
    list_models,
    model_label,
)
from app.db.base import Base
from app.models.signal_catalog import SignalCatalog


@pytest.fixture()
def db_session():
    """Bellek ici SQLite — repo'daki diger DB testleriyle ayni desen."""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


def _sinyal_ekle(db, *, key: str, model: str) -> None:
    db.add(
        SignalCatalog(
            key=key,
            label=key,
            model=model,
            data_type="analog",
            unit="A",
            is_active=True,
        )
    )
    db.commit()


def test_katalogtaki_yeni_model_listede_gorunur(db_session) -> None:
    """Sinyali girilen model, cihaz formunun dropdown'inda cikmali."""
    _sinyal_ekle(db_session, key="master.yeni_akim", model="horstmann_sn_3_0")

    kodlar = [m["code"] for m in list_models(db_session)]
    assert "horstmann_sn_3_0" in kodlar


def test_katalogtaki_yeni_model_gecerli_sayilir(db_session) -> None:
    """`GET /signals?model=...` bu modeli reddetmemeli."""
    _sinyal_ekle(db_session, key="master.yeni_gerilim", model="baska_marka_x1")

    assert is_valid_model("baska_marka_x1", db_session) is True


def test_oturumsuz_cagri_eski_davranisi_korur() -> None:
    """`db` verilmeyen cagirenlar icin davranis DEGISMEMELI."""
    assert is_valid_model("horstmann_sn_2_0") is True
    assert is_valid_model("hic_olmayan_model") is False


def test_bilinmeyen_model_kendi_koduyla_gosterilir(db_session) -> None:
    """Uydurma bir etiket URETILMEZ.

    "Horstmann Smart Navigator 3.0" gibi bir ad tahmin etmek, sahada
    olmayan bir urunu varmis gibi gosterirdi. Kod cirkin ama dogru.
    """
    _sinyal_ekle(db_session, key="master.x", model="horstmann_sn_3_0")

    assert model_label("horstmann_sn_3_0", db_session) == "horstmann_sn_3_0"
    # Yerlesik modelin duzgun adi korunur.
    assert model_label("horstmann_sn_2_0", db_session) == BUILTIN_MODELS["horstmann_sn_2_0"]


def test_yerlesik_modeller_once_listelenir(db_session) -> None:
    """Operator once bildigi modeli gorsun."""
    _sinyal_ekle(db_session, key="master.a", model="aaa_alfabetik_once")

    kodlar = [m["code"] for m in list_models(db_session)]
    assert kodlar[0] == "horstmann_sn_2_0", "yerlesik model basta olmali"
    assert "aaa_alfabetik_once" in kodlar


def test_veritabani_okunamazsa_liste_BOS_DONMEZ() -> None:
    """DB hatasi model listesini dusurmemeli.

    Bos liste, cihaz eklemeyi tamamen engellerdi — katalogdan model
    turetmenin bedeli bu olamaz.
    """

    class _PatlayanDB:
        def scalars(self, *_a, **_k):
            raise RuntimeError("baglanti yok")

    kodlar = [m["code"] for m in list_models(_PatlayanDB())]
    # Sanal set modeli (Pole Master Kit seti) dropdown'da YER ALMAZ: elle
    # olusturulmaz, fiziksel kit eklenirken set sayisi kadar uretilir.
    assert kodlar == [k for k in BUILTIN_MODELS if k not in NON_SELECTABLE_MODELS]
    assert kodlar, "liste bos donmemeli"


def test_bos_ve_None_model_degerleri_listeye_girmez(db_session) -> None:
    """Katalogda model'i bos kalmis satirlar dropdown'i kirletmemeli."""
    _sinyal_ekle(db_session, key="master.bos", model="")

    kodlar = [m["code"] for m in list_models(db_session)]
    assert "" not in kodlar
    assert all(k.strip() for k in kodlar)
