"""Ariza KISIYE ya da EKIBE atanabilir.

NEDEN EKIP
----------
Gece vardiyasinda ya da nobet devrinde isi USTLENECEK kisi belli degildir;
ariza yine de sahipsiz kalmamali. Atama yalnizca kisiye yapilabildiginde
operator "birine" atiyor ve o kisi izinliyse kayit sessizce bekliyordu.
Ekip (sorumluluk alani) zaten sistemde var ve "bakim/operasyon ekibini
temsil eder" — cihazlar ve kullanicilar ona bagli.

BU DOSYANIN KILITLEDIGI SEY
---------------------------
  1. Ikisi AYNI ANDA olmaz — "sorumlu kim" sorusunun iki cevabi olamaz.
  2. Bir tarafa atama digerini TEMIZLER (devralma).
  3. Ekibe atamada ekibin TUM uyeleri haberdar olur; kimse tek tek
     secilmedigi icin haber vermezsek atama ekranda durur ama kimse bilmez.
  4. Kisi atamasinda kartta gosterilecek PROFIL FOTOGRAFI yanitta doner.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.faults import assign_fault
from app.db.base import Base
from app.models.device import Device
from app.models.enums import UserRole
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, Pole, Region
from app.models.notification import Notification
from app.models.responsibility_area import (
    ResponsibilityArea,
    responsibility_area_users,
)
from app.models.user import User
from app.schemas.fault import FaultEventAssignUpdate


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


def _kullanici(db, ad: str, *, rol=UserRole.OPERATOR, avatar: str | None = None) -> User:
    u = User(
        username=ad, email=f"{ad}@f.com", full_name=ad.upper(),
        hashed_password="x", role=rol, avatar_url=avatar,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def saha(db):
    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()
    hat = Line(name="ANA HAT", code="ANA", region_id=r.id)
    db.add(hat)
    db.flush()
    p1 = Pole(line_id=hat.id, sequence_no=1, latitude=39.0, longitude=35.0)
    p2 = Pole(line_id=hat.id, sequence_no=2, latitude=39.1, longitude=35.1)
    d = Device(code="D1", name="D1", ip_address="10.0.0.1", latitude=39.0, longitude=35.0)
    db.add_all([p1, p2, d])
    db.flush()
    ariza = FaultEvent(
        line_id=hat.id, region_id=r.id, last_red_device_id=d.id,
        from_pole_id=p1.id, to_pole_id=p2.id, from_pole_seq=1, to_pole_seq=2,
        status="open", opened_at=datetime.now(timezone.utc),
    )
    db.add(ariza)
    ekip = ResponsibilityArea(code="EKIP-A", name="Saha Ekibi A")
    db.add(ekip)
    db.flush()
    muh = _kullanici(db, "muh", rol=UserRole.ENGINEER)
    return {"ariza": ariza, "ekip": ekip, "muhendis": muh}


def test_EKIBE_atanabilir(db, saha):
    sonuc = assign_fault(
        saha["ariza"].id,
        FaultEventAssignUpdate(assigned_to_area_id=saha["ekip"].id),
        current_user=saha["muhendis"],
        db=db,
    )

    assert sonuc.assigned_to_area_id == saha["ekip"].id
    assert sonuc.assigned_to_area_name == "Saha Ekibi A"
    assert sonuc.assigned_to_username is None
    assert sonuc.status == "assigned", "atanan ariza artik 'acik' degil"
    assert sonuc.assigned_at is not None


def test_IKISI_BIRDEN_gonderilirse_REDDEDILIR(db, saha):
    """Sorumlu TEKTIR; iki cevap sahada iki ekibin ayni yere gitmesidir."""
    kisi = _kullanici(db, "ali")

    with pytest.raises(HTTPException) as exc:
        assign_fault(
            saha["ariza"].id,
            FaultEventAssignUpdate(
                assigned_to_username=kisi.username, assigned_to_area_id=saha["ekip"].id
            ),
            current_user=saha["muhendis"],
            db=db,
        )

    assert exc.value.status_code == 400


def test_KISIYE_atama_EKIP_atamasini_temizler(db, saha):
    """Devralma: ekip isi aldi, sonra bir kisi ustlendi."""
    kisi = _kullanici(db, "veli")
    assign_fault(
        saha["ariza"].id,
        FaultEventAssignUpdate(assigned_to_area_id=saha["ekip"].id),
        current_user=saha["muhendis"], db=db,
    )

    sonuc = assign_fault(
        saha["ariza"].id,
        FaultEventAssignUpdate(assigned_to_username=kisi.username),
        current_user=saha["muhendis"], db=db,
    )

    assert sonuc.assigned_to_username == "veli"
    assert sonuc.assigned_to_area_id is None, "iki atama ust uste birikemez"
    assert sonuc.assigned_to_area_name is None


def test_atama_KALDIRILABILIR(db, saha):
    assign_fault(
        saha["ariza"].id,
        FaultEventAssignUpdate(assigned_to_area_id=saha["ekip"].id),
        current_user=saha["muhendis"], db=db,
    )

    sonuc = assign_fault(
        saha["ariza"].id, FaultEventAssignUpdate(),
        current_user=saha["muhendis"], db=db,
    )

    assert sonuc.assigned_to_area_id is None and sonuc.assigned_to_username is None
    assert sonuc.assigned_at is None


def test_EKIP_UYELERININ_HEPSI_haberdar_olur(db, saha):
    """Kimse tek tek secilmedi; haber vermezsek atama sessiz kalir."""
    a = _kullanici(db, "uye1")
    b = _kullanici(db, "uye2")
    _kullanici(db, "disarida")
    db.execute(
        responsibility_area_users.insert(),
        [
            {"area_id": saha["ekip"].id, "user_id": a.id},
            {"area_id": saha["ekip"].id, "user_id": b.id},
        ],
    )
    db.flush()

    assign_fault(
        saha["ariza"].id,
        FaultEventAssignUpdate(assigned_to_area_id=saha["ekip"].id),
        current_user=saha["muhendis"], db=db,
    )

    alicilar = {
        n.recipient_username
        for n in db.scalars(
            select(Notification).where(Notification.category == "fault_assignment")
        ).all()
    }
    assert alicilar == {"uye1", "uye2"}, "ekip disindaki kullaniciya bildirim gitmemeli"


def test_AYNI_ekibe_yeniden_atama_bildirim_URETMEZ(db, saha):
    """Spam onlemi — kisi atamasinda zaten boyleydi."""
    a = _kullanici(db, "uye1")
    db.execute(
        responsibility_area_users.insert(),
        [{"area_id": saha["ekip"].id, "user_id": a.id}],
    )
    db.flush()
    guncelle = FaultEventAssignUpdate(assigned_to_area_id=saha["ekip"].id)
    assign_fault(saha["ariza"].id, guncelle, current_user=saha["muhendis"], db=db)
    assign_fault(saha["ariza"].id, guncelle, current_user=saha["muhendis"], db=db)

    adet = len(
        db.scalars(
            select(Notification).where(Notification.category == "fault_assignment")
        ).all()
    )
    assert adet == 1


def test_OLMAYAN_ekip_REDDEDILIR(db, saha):
    with pytest.raises(HTTPException) as exc:
        assign_fault(
            saha["ariza"].id,
            FaultEventAssignUpdate(assigned_to_area_id=999999),
            current_user=saha["muhendis"], db=db,
        )
    assert exc.value.status_code == 400


def test_migration_KOLONU_ekler():
    """0059 GERCEKTEN kosturulur: SQLite'ta FK eklenemez, kolon yine de gelmeli.

    Migration'in SQLite dalini test etmezsek gelistirici makinesinde
    `alembic upgrade` patlar ve uretimde calisan bir degisiklik burada test
    edilemez hale gelirdi. Tablo modelden DEGIL ham SQL ile kuruluyor: model
    kolonu zaten tanimliyor, oysa test edilen sey "kolonu OLMAYAN bir semada
    migration ne yapiyor".
    """
    import importlib.util
    import pathlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    yol = next(
        (pathlib.Path(__file__).resolve().parents[1] / "alembic_migrations" / "versions")
        .glob("*0059_fault_assign_to_team.py")
    )
    spec = importlib.util.spec_from_file_location("migration_0059", yol)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)

    eng = create_engine("sqlite:///:memory:")
    try:
        with eng.begin() as baglanti:
            baglanti.execute(
                text("CREATE TABLE fault_events (id INTEGER PRIMARY KEY, line_id INTEGER)")
            )
            baglanti.execute(
                text("CREATE TABLE responsibility_areas (id INTEGER PRIMARY KEY, name TEXT)")
            )
            modul.op = Operations(MigrationContext.configure(baglanti))

            modul.upgrade()

            denetci = inspect(baglanti)
            assert "assigned_to_area_id" in {
                c["name"] for c in denetci.get_columns("fault_events")
            }
            assert "ix_fault_events_assigned_to_area_id" in {
                i["name"] for i in denetci.get_indexes("fault_events")
            }
    finally:
        eng.dispose()


def test_KISININ_FOTOGRAFI_yanitta_doner(db, saha):
    """Kartta bas harf yerine yuz gosterilebilsin diye."""
    _kullanici(db, "foto", avatar="/media/avatars/foto.png")

    sonuc = assign_fault(
        saha["ariza"].id,
        FaultEventAssignUpdate(assigned_to_username="foto"),
        current_user=saha["muhendis"], db=db,
    )

    assert sonuc.assigned_to_avatar_url == "/media/avatars/foto.png"
