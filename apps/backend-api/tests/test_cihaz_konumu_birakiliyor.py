"""HATTAN KALDIRILAN CIHAZ HARITADA ESKI YERINDE DURUYORDU.

Cihaz bir slot'a oturdugu anda koordinati SEGMENTTEN TURETILIP cihaz
satirina yaziliyor (`_resync_slot`: `dev.latitude = lat`). Yani hatta bagli
bir cihazin koordinati sahadan girilmis bagimsiz bir olcum degil, iki direk
arasindaki interpolasyonun kopyasi.

Atama kalkinca kimse o kopyayi temizlemiyordu. Ana sayfa haritasi konumu
once segmentten turetir, segment yoksa cihazin kendi koordinatina duser —
pin eski yerinin yakininda, hatta bagli cihazlarla BIREBIR AYNI cizilmeye
devam ediyordu. Kullanicinin gozunde hat atamasi KALDIRILAMIYORDU.

Artik dusen cihazin konumu birakiliyor: (0, 0) arayuzde zaten "konum yok"
demek (bkz. DeviceMapTab `gecerliKonum`).

En kritik testi `test_BASKA_SLOTA_TASIMA_konumu_kaybetmez`: tasima akisi
once eski segmentten dusurup sonra hedefe yaziyor; temizlik kor olsaydi
her tasima cihazin konumunu silerdi.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import grid_topology as grid_api
from app.db.base import Base
from app.models.device import Device
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.grid_topology import LineSegmentCreate, LineSegmentUpdate


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
def saha(db):
    """Tek hat, 3 direk (yani 2 slot), bir cihaz."""
    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()
    hat = Line(name="ANA HAT", code="ANA", region_id=r.id)
    db.add(hat)
    db.flush()
    direkler = []
    for i in range(1, 4):
        p = Pole(line_id=hat.id, sequence_no=i, latitude=39.0 + i * 0.01, longitude=35.0)
        db.add(p)
        direkler.append(p)
    d = Device(code="SN-20", name="SN20", ip_address="10.0.0.1",
               latitude=0.0, longitude=0.0)
    db.add(d)
    u = User(username="muh", email="m@f.com", full_name="Muh",
             hashed_password="x", role=UserRole.ENGINEER)
    db.add(u)
    db.flush()
    return {"hat": hat, "direkler": direkler, "cihaz": d, "user": u}


def _bagla(db, saha, from_idx: int, to_idx: int) -> LineSegment:
    okunan = grid_api.create_segment(
        payload=LineSegmentCreate(
            line_id=saha["hat"].id,
            from_pole_id=saha["direkler"][from_idx].id,
            to_pole_id=saha["direkler"][to_idx].id,
            device_id=saha["cihaz"].id,
        ),
        current_user=saha["user"],
        db=db,
    )
    return db.get(LineSegment, okunan.id)


def _konum(db, saha) -> tuple[float, float]:
    db.refresh(saha["cihaz"])
    return (saha["cihaz"].latitude, saha["cihaz"].longitude)


def test_hatta_baglaninca_konum_SEGMENTTEN_turer(db, saha):
    """On kosul: koordinat sahadan degil, slot'tan geliyor."""
    _bagla(db, saha, 0, 1)
    lat, lon = _konum(db, saha)
    assert (lat, lon) != (0.0, 0.0)
    # Iki direk arasinda kalmali.
    assert saha["direkler"][0].latitude <= lat <= saha["direkler"][1].latitude


def test_SEGMENT_SILININCE_konum_birakilir(db, saha):
    """Asil regresyon: 'kaldir' -> segment silinir -> konum bayat kalmasin."""
    seg = _bagla(db, saha, 0, 1)
    assert _konum(db, saha) != (0.0, 0.0)

    grid_api.delete_segment(segment_id=seg.id, current_user=saha["user"], db=db)

    assert _konum(db, saha) == (0.0, 0.0)


def test_device_id_NULL_yapilinca_konum_birakilir(db, saha):
    """Ikinci ayirma yolu: segment durur, cihaz alani bosaltilir."""
    seg = _bagla(db, saha, 0, 1)
    assert _konum(db, saha) != (0.0, 0.0)

    grid_api.update_segment(
        segment_id=seg.id,
        payload=LineSegmentUpdate(device_id=None),
        current_user=saha["user"],
        db=db,
    )

    assert _konum(db, saha) == (0.0, 0.0)


def test_BASKA_SLOTA_TASIMA_konumu_kaybetmez(db, saha):
    """Tasima = once dusur, sonra hedefe yaz. Temizlik kor olsaydi her
    tasima cihazin konumunu silerdi."""
    seg = _bagla(db, saha, 0, 1)
    ilk_konum = _konum(db, saha)

    # 1) Kaynaktan dusur
    grid_api.update_segment(
        segment_id=seg.id,
        payload=LineSegmentUpdate(device_id=None),
        current_user=saha["user"],
        db=db,
    )
    # 2) Hedef slot'a yaz
    _bagla(db, saha, 1, 2)

    son_konum = _konum(db, saha)
    assert son_konum != (0.0, 0.0), "tasimadan sonra cihaz konumsuz kalmis"
    assert son_konum != ilk_konum, "konum yeni slot'a gore yeniden hesaplanmali"
    assert saha["direkler"][1].latitude <= son_konum[0] <= saha["direkler"][2].latitude


def test_HALA_BAGLI_cihazin_konumu_birakilmaz(db, saha):
    """Temizligin kapisi: cihaz bir segmentte duruyorsa konuma dokunulmaz.

    Tasima akisi tam da buna guveniyor. (Bir cihaz ayni anda birden fazla
    segmentte OLAMAZ — `line_segments.device_id` tekil; o yuzden kural
    dogrudan `_konumu_birak` uzerinden olculuyor.)
    """
    _bagla(db, saha, 0, 1)
    bagliyken = _konum(db, saha)
    assert bagliyken != (0.0, 0.0)

    grid_api._konumu_birak(db, saha["cihaz"].id)

    assert _konum(db, saha) == bagliyken, "hala bagli cihazin konumu silinmis"
