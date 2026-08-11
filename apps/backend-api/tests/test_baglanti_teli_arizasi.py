"""Iki hat noktasini birlestiren BAGLANTI TELI uzerindeki ariza.

SAHA GERCEGI
------------
Ana hattaki dallanma diregi ile bir baska hattin (cogu zaman tek direklik
bir dalin) ilk diregi arasina tek bir tel cekiliyor. Bu tel hicbir hattin
direk siralamasinin ICINDE degil; iki hat noktasini birlestiriyor. Sistemde
kol ayri bir hat olarak modellenir (`Line.branched_from_pole_id`) ve tel de
o kolun ILK acikligidir.

SORUN
-----
Boyle bir aciklikta ariza olunca kayit su hale geliyordu:

    hat=KOL-B   direk 2 -> 1

Buradaki 2 ANA HATTIN diregi, 1 ise KOLUN diregi. Iki farkli numaralandirma
tek bir aralik gibi gosteriliyor:

  * Arayuzde "Direk #2 — Direk #1" yaziyor; geriye dogru giden, anlamsiz
    bir aralik. Sahaya cikan ekip hangi acikliga gidecegini okuyamaz.
  * Araligi GERCEK bir aralik zanneden kod (`alt < seq < ust` taramalari,
    `_seq_overlap` eslestirmesi) iki ayri numaralandirma uzerinde islem
    yapar ve sessizce yanlis sonuc uretir.

Bu dosya, ucun boyle bir kaydi ACIKCA "baglanti teli" diye isaretledigini
dogrular; arayuz de onu aralik gibi degil, iki UCLU bir baglanti olarak
gosterir.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.faults import list_faults
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.enums import UserRole
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.user import User
from app.services.fault_recompute_service import recompute_faults


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
    """ANA HAT (3 direk) + #2'den cikan tek direklik KOL-B.

    Cihazlar: ANA-12 (ana hattin ilk acikligi) ve BAG-B (baglanti teli).
    """
    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()

    ana = Line(name="ANA HAT", code="ANA", region_id=r.id)
    db.add(ana)
    db.flush()
    ap = []
    for i in range(1, 4):
        p = Pole(line_id=ana.id, sequence_no=i, latitude=39.0 + i * 0.01, longitude=35.0)
        db.add(p)
        ap.append(p)
    db.flush()

    kol = Line(name="KOL-B", code="KOLB", region_id=r.id, branched_from_pole_id=ap[1].id)
    db.add(kol)
    db.flush()
    kp = Pole(line_id=kol.id, sequence_no=1, latitude=ap[1].latitude, longitude=35.02)
    db.add(kp)
    db.flush()

    def cihaz(kod: str) -> Device:
        d = Device(code=kod, name=kod, ip_address=f"10.0.0.{len(kod)}",
                   latitude=39.0, longitude=35.0)
        db.add(d)
        db.flush()
        return d

    d_ana = cihaz("ANA-12")
    d_bag = cihaz("BAG-B")
    db.add(LineSegment(line_id=ana.id, from_pole_id=ap[0].id, to_pole_id=ap[1].id,
                       device_id=d_ana.id))
    db.add(LineSegment(line_id=ana.id, from_pole_id=ap[1].id, to_pole_id=ap[2].id))
    # BAGLANTI TELI: ana hattin #2 diregi -> kolun #1 diregi.
    db.add(LineSegment(line_id=kol.id, from_pole_id=ap[1].id, to_pole_id=kp.id,
                       device_id=d_bag.id))
    db.flush()

    u = User(username="muh", email="m@f.com", full_name="Muh",
             hashed_password="x", role=UserRole.ENGINEER)
    db.add(u)
    db.flush()
    return {"ana": ana, "kol": kol, "ap": ap, "kp": kp,
            "d_ana": d_ana, "d_bag": d_bag, "user": u}


def _alarm(db, dev: Device) -> None:
    db.add(AlarmEvent(device_id=dev.id, title=f"{dev.code} asiri akim",
                      description="esik asildi", level="critical", signal_key="oc",
                      kind="rule", produces_fault=True, reset=False,
                      created_at=datetime.now(timezone.utc)))
    db.flush()


def _okunan(db, user):
    """Uc, YENI acilan arizalari `fault_display_delay_sec` dolana kadar
    GIZLIYOR (gec gelen alarmlar birikip ariza dogru aralikla tek seferde
    gorunsun diye). Bu dosya araligin BICIMINI olcuyor, gecikmeyi degil;
    kayitlari olgunlastirip okuyoruz."""
    from datetime import timedelta

    from app.models.fault import FaultEvent

    eski = datetime.now(timezone.utc) - timedelta(hours=1)
    for f in db.query(FaultEvent).all():
        f.opened_at = eski
    db.flush()
    return list_faults(status_filter="active", current_user=user, db=db)


def test_baglanti_telindeki_ariza_KAYIT_aciyor(db, saha):
    """Once en temel sey: tel uzerindeki ariza kayboluyor mu?"""
    _alarm(db, saha["d_bag"])
    recompute_faults(db)
    kayitlar = _okunan(db, saha["user"])
    kol_kayitlari = [f for f in kayitlar if f.line_id == saha["kol"].id]
    assert kol_kayitlari, "baglanti telindeki ariza HIC kayit acmamis"


def test_baglanti_teli_ACIKCA_isaretlenir(db, saha):
    """Aralik iki farkli hattin numaralandirmasini karistiriyor; arayuzun
    bunu aralik gibi gostermemesi icin uc bunu SOYLEMELI."""
    _alarm(db, saha["d_bag"])
    recompute_faults(db)
    f = next(x for x in _okunan(db, saha["user"]) if x.line_id == saha["kol"].id)

    assert f.is_link_span is True, "baglanti teli isaretlenmemis"
    # Baslangic ucu ANA HATTA; arayuz "ANA HAT #2 <-> KOL-B #1" diyebilsin.
    assert f.from_pole_line_name == "ANA HAT"
    assert f.from_pole_seq == 2, "baslangic ucu ana hattin dallanma diregi olmali"
    assert f.to_pole_seq == 1, "bitis ucu kolun ilk diregi olmali"


def test_hat_ICI_ariza_baglanti_teli_SAYILMAZ(db, saha):
    """Bayrak dar olmali: siradan bir hat ici aciklik yanlislikla
    "baglanti teli" diye gosterilirse arayuz onu iki uclu bir baglanti gibi
    cizer ve gercek aralik bilgisi kaybolur."""
    _alarm(db, saha["d_ana"])
    recompute_faults(db)
    f = next(x for x in _okunan(db, saha["user"]) if x.line_id == saha["ana"].id)

    assert f.is_link_span is False
    assert f.from_pole_line_name is None


def test_kol_kaydi_ANA_HATTI_isaret_eder(db, saha):
    """Tel iki hatti birlestirdigi icin operator hangi ana hattan
    dallandigini gormeli; aksi halde "KOL-B" tek basina saharada yer
    tarif etmiyor."""
    _alarm(db, saha["d_bag"])
    recompute_faults(db)
    f = next(x for x in _okunan(db, saha["user"]) if x.line_id == saha["kol"].id)

    assert f.is_branch_line is True
    assert f.parent_line_name == "ANA HAT"


def test_tel_kirmiziyken_ANA_HAT_kaydi_da_dogru_kalir(db, saha):
    """Telden akim gectiyse ana hattin ust kismindan da gecmistir; ana hat
    kaydi bastirilmaz. Ikisi FARKLI seyler soyler:
      * ana hat: "akim su iki cihaz arasinda kayboldu"
      * kol:     "kaybolma yeri bu telin/kolun icinde"
    """
    _alarm(db, saha["d_ana"])
    _alarm(db, saha["d_bag"])
    recompute_faults(db)
    kayitlar = _okunan(db, saha["user"])

    hatlar = {f.line_id for f in kayitlar}
    assert saha["kol"].id in hatlar, "kol kaydi yok"
    assert saha["ana"].id in hatlar, "ana hat kaydi kaybolmus"

    ana_f = next(x for x in kayitlar if x.line_id == saha["ana"].id)
    assert ana_f.is_link_span is False
