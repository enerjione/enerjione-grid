"""Acilistaki seed, operatorun duzenlemelerini EZMEMELI.

YASANAN ARIZA
-------------
`app/main.py` her acilista `seed_default_signals(db, strict=True)` kosuyordu.
Guncelledigi `_MUTABLE_FIELDS` listesi tam da kurulumcunun arayuzden
degistirdigi alanlari iceriyor:

    label, unit, scale, offset, dnp3_index,
    iec104_type_id, iec104_ioa, iec104_ioa_offset

`PATCH /signals/{key}` bu alanlari degistirmeye IZIN veriyor ve olay kaydina
`signal_updated` yaziyor. Yani sistem "kaydedildi" diyor, denetim kaydi
tutuyor, sonra ILK YENIDEN BASLATMADA sessizce geri aliyordu.

  > Devreye alma muhendisi SCADA icin 20 sinyalin IOA'sini duzenler ve akim
  > trafosu icin scale=0.1 yapar. Gece elektrik kesintisi olur. Sabah SCADA
  > YANLIS IOA'dan okur, akim degerleri 10 KAT yanlis gorunur. Hicbir hata
  > logu, hicbir alarm yok.

Seed JSON'unda alan NULL ise daha da agir: `iec104_ioa` NULL'a cekilen sinyal
IEC 104 yayinindan TAMAMEN duser.

Ayrica `strict=True` seed listesinde olmayan HER sinyali siliyordu — ama
`POST /signals` kurulumcuya sinyal YARATMA izni veriyor; o sinyaller ilk
reboot'ta yok oluyordu.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.signal_catalog import SignalCatalog
from app.services import signal_catalog_seed as seed_mod
from app.services.signal_catalog_seed import (
    _MUTABLE_FIELDS,
    clear_user_overrides,
    seed_default_signals,
)

FABRIKA = [
    {
        "key": "master.actual_voltage",
        "model": "horstmann_sn_2_0",
        "label": "Gerilim",
        "unit": "V",
        "source": "master",
        "data_type": "analog",
        "scale": 1.0,
        "offset": 0.0,
        "dnp3_index": 5,
        "iec104_ioa": 1001,
        "display_order": 1,
    },
]


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


@pytest.fixture(autouse=True)
def sahte_fabrika(monkeypatch):
    """Seed kaynagini testte sabitle — gercek JSON degisirse test kirilmasin."""
    monkeypatch.setattr(
        seed_mod, "_load_all_default_items", lambda: (list(FABRIKA), {"horstmann_sn_2_0"})
    )


def _sinyal(db, key: str = "master.actual_voltage") -> SignalCatalog:
    return db.scalar(select(SignalCatalog).where(SignalCatalog.key == key))


def test_elle_degistirilen_alan_acilista_KORUNUYOR(db):
    """Asil ariza: operatorun degeri her acilista fabrikaya donuyordu."""
    seed_default_signals(db)          # ilk kurulum
    row = _sinyal(db)
    assert row.iec104_ioa == 1001

    # Operator arayuzden duzenliyor (PATCH'in yaptigi sey)
    row.iec104_ioa = 5000
    row.scale = 0.1
    row.user_overrides = ["iec104_ioa", "scale"]
    db.commit()

    # Cihaz yeniden basliyor
    sonuc = seed_default_signals(db, strict=False)

    row = _sinyal(db)
    assert row.iec104_ioa == 5000, (
        "IOA fabrika degerine dondu — SCADA YANLIS adresten okur"
    )
    assert row.scale == 0.1, "olcek fabrika degerine dondu — degerler 10 kat sapar"
    assert sonuc["kept"] >= 2


def test_dokunulmamis_alanlara_fabrika_duzeltmesi_GELIYOR(db):
    """Koruma her seyi dondurmamali: elle degismeyen alan guncellenmeli.

    Aksi halde bir sinyale tek dokunus, o satiri fabrika duzeltmelerine
    tamamen kapatirdi.
    """
    seed_default_signals(db)
    row = _sinyal(db)
    row.iec104_ioa = 5000
    row.user_overrides = ["iec104_ioa"]
    db.commit()

    # Fabrika etiketi degistiriyor (ornegin ceviri duzeltmesi)
    FABRIKA[0]["label"] = "Faz Gerilimi"
    try:
        seed_default_signals(db, strict=False)
        row = _sinyal(db)
        assert row.label == "Faz Gerilimi", "dokunulmamis alan guncellenmedi"
        assert row.iec104_ioa == 5000, "korunan alan yine de ezildi"
    finally:
        FABRIKA[0]["label"] = "Gerilim"


def test_operatorun_yarattigi_sinyal_acilista_SILINMIYOR(db):
    """`POST /signals` ile eklenen sinyal ilk reboot'ta yok oluyordu."""
    seed_default_signals(db)
    db.add(
        SignalCatalog(
            key="master.ozel_olcum",
            model="horstmann_sn_2_0",
            label="Ozel",
            source="master",
            data_type="analog",
        )
    )
    db.commit()

    seed_default_signals(db, strict=False)   # acilis senkronu

    assert _sinyal(db, "master.ozel_olcum") is not None, (
        "kurulumcunun ekledigi sinyal acilista silindi"
    )


def test_fabrikaya_donus_KULLANICI_degisikligini_de_geri_aliyor(db):
    """Bilincli fabrika sifirlamasi tam olmali; yarim kalirsa yaniltir."""
    seed_default_signals(db)
    row = _sinyal(db)
    row.iec104_ioa = 5000
    row.user_overrides = ["iec104_ioa"]
    db.commit()

    temizlenen = clear_user_overrides(db)
    seed_default_signals(db, strict=True, respect_user_overrides=False)

    row = _sinyal(db)
    assert temizlenen == 1
    assert row.iec104_ioa == 1001, "fabrika sifirlamasi degeri geri getirmedi"
    assert not row.user_overrides, "isaretler temizlenmedi"


def test_bos_override_listesi_KORUMA_saglamiyor(db):
    """Bos/None liste "hicbir sey elle degismedi" demektir."""
    seed_default_signals(db)
    row = _sinyal(db)
    row.iec104_ioa = 5000
    row.user_overrides = None
    db.commit()

    seed_default_signals(db, strict=False)
    assert _sinyal(db).iec104_ioa == 1001


def test_router_degisen_alanlari_ISARETLIYOR():
    """Isaretleme PATCH ucunda olmali; olmazsa koruma hic devreye girmez."""
    import ast
    import inspect

    from app.api import signals

    fn = next(
        d
        for d in ast.walk(ast.parse(inspect.getsource(signals)))
        if isinstance(d, ast.FunctionDef) and d.name == "update_signal"
    )
    atanan = {
        t.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Attribute)
    }
    assert "user_overrides" in atanan, (
        "PATCH degisen alanlari isaretlemiyor — acilistaki seed hepsini geri alir"
    )


def test_acilis_senkronu_STRICT_DEGIL():
    """Acilista silme yapilmamali; fabrikaya donus ayri ve bilincli bir uctur."""
    import ast
    import inspect

    from app import main

    kaynak = inspect.getsource(main)
    cagrilar = [
        n
        for n in ast.walk(ast.parse(kaynak))
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "seed_default_signals"
    ]
    assert cagrilar, "acilis senkronu bulunamadi"
    for c in cagrilar:
        strict = next((k.value for k in c.keywords if k.arg == "strict"), None)
        assert isinstance(strict, ast.Constant) and strict.value is False, (
            "acilista strict=True — kurulumcunun ekledigi sinyaller silinir"
        )


@pytest.mark.parametrize("alan", ["label", "scale", "offset", "iec104_ioa", "dnp3_index"])
def test_korunan_alanlar_MUTABLE_listesinde(alan: str):
    """Koruma yalnizca seed'in gercekten ezdigi alanlar icin anlamli.

    Alan `_MUTABLE_FIELDS`'ten cikarilirsa koruma da gereksizlesir; bu test
    ikisinin birlikte degismesini saglar.
    """
    assert alan in _MUTABLE_FIELDS
