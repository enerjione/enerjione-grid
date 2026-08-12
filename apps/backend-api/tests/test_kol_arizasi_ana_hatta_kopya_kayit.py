"""Kolda ariza varken ANA HATTA kopya kayit acilmasi.

SAHA GERCEGI
------------
Ariza akimi kaynaktan cikip ana hat boyunca ilerler ve dallanma diregindan
kola saparak ariza noktasina ulasir. Yani KOLDAKI bir ariza, ana hattin o
noktaya kadarki cihazlarina da "gordum" dedirtir:

    ANA HAT  1 ─[D1 KIRMIZI]─ 2 ─ 3 ─[D2 YESIL]─ 4
                                  │
                                 KOL ─[DK KIRMIZI]─

SORUN
-----
Bolge hesabi hat hat yapiliyor ve her hat duz bir zincir sayiliyordu;
kollarin varligindan haberi yoktu. Yukaridaki tabloda ana hat icin de bir
"RED blogu" gorunuyor ve TEK fiziksel ariza icin IKI kayit aciliyordu.
Ana hattaki kayit sahada bir ise yaramaz (o kesimde ariza yok) ama listede
"ACIK" durur ve kol duzelene kadar da kapanmaz — kullanicinin gordugu
"onceki ariza normale donmemis gibi duruyor" hali budur.

Harita bu tuzaga hic dusmuyordu: sebekeyi graf olarak yuruyup ayni kirmiziyi
kola yaziyor (frontend `nearestDeviceRed.ts`). Yani harita ile ariza listesi
ayni olay icin farkli sey soyluyordu.

KURAL
-----
Ariza EN DAR bolgeye yazilir: bir bolgenin direk araliginin ICINDEN cikan
kolun kendi bolgesi varsa, ust hattaki kopya bolge kayit uretmez.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, LineSegment, Pole, Region
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
    """ANA HAT (4 direk) + #3'ten cikan KOL.

    Cihazlar:
      D1  ana hattin 1-2 acikliginda   (arizayi goren)
      D2  ana hattin 3-4 acikliginda   (gormeyen -> bolgenin ust ucu)
      DK  kolun ilk acikliginda
    Dallanma diregi #3, ana hat bolgesinin (1..4) TAM ICINDE kalir.
    """
    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()

    ana = Line(name="ANA HAT", code="ANA", region_id=r.id)
    db.add(ana)
    db.flush()
    ap = []
    for i in range(1, 5):
        p = Pole(line_id=ana.id, sequence_no=i, latitude=39.0 + i * 0.01, longitude=35.0)
        db.add(p)
        ap.append(p)
    db.flush()

    kol = Line(name="KOL", code="KOL", region_id=r.id, branched_from_pole_id=ap[2].id)
    db.add(kol)
    db.flush()
    kp = Pole(line_id=kol.id, sequence_no=1, latitude=ap[2].latitude, longitude=35.05)
    db.add(kp)
    db.flush()

    def cihaz(kod: str, son: int) -> Device:
        d = Device(code=kod, name=kod, ip_address=f"10.0.0.{son}",
                   latitude=39.0, longitude=35.0)
        db.add(d)
        db.flush()
        return d

    d1 = cihaz("D1", 1)
    d2 = cihaz("D2", 2)
    dk = cihaz("DK", 3)
    db.add(LineSegment(line_id=ana.id, from_pole_id=ap[0].id, to_pole_id=ap[1].id,
                       device_id=d1.id))
    db.add(LineSegment(line_id=ana.id, from_pole_id=ap[1].id, to_pole_id=ap[2].id))
    db.add(LineSegment(line_id=ana.id, from_pole_id=ap[2].id, to_pole_id=ap[3].id,
                       device_id=d2.id))
    db.add(LineSegment(line_id=kol.id, from_pole_id=ap[2].id, to_pole_id=kp.id,
                       device_id=dk.id))
    db.flush()
    return {"ana": ana, "kol": kol, "ap": ap, "d1": d1, "d2": d2, "dk": dk}


def _alarm(db, dev: Device) -> AlarmEvent:
    a = AlarmEvent(device_id=dev.id, title=f"{dev.code} asiri akim",
                   description="esik asildi", level="critical", signal_key="oc",
                   kind="rule", produces_fault=True, reset=False,
                   created_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    return a


def _aktif(db) -> list[FaultEvent]:
    return [f for f in db.query(FaultEvent).all() if f.status not in ("resolved", "closed")]


def test_kol_arizasinda_ANA_HAT_kaydi_ACILMAZ(db, saha):
    """Tek fiziksel ariza -> TEK kayit, ve o kayit KOLDA."""
    _alarm(db, saha["d1"])   # ana hat: arizayi gordu (akim buradan gecti)
    _alarm(db, saha["dk"])   # kol: ariza burada
    recompute_faults(db)

    hatlar = {f.line_id for f in _aktif(db)}
    assert saha["kol"].id in hatlar, "kol kaydi acilmamis"
    assert saha["ana"].id not in hatlar, (
        "ana hatta kopya kayit acilmis — ariza en dar bolgeye yazilmali"
    )


def test_kol_arizasi_gelince_ACIK_ana_hat_kaydi_NORMALE_DONER(db, saha):
    """Kullanicinin bildirdigi hal: ana hat kaydi zaten aciktir, sonra kolun
    alarmi duser. Kayit acik kalmaya devam ederse operator duzelmis bir
    kesime ekip gonderir."""
    _alarm(db, saha["d1"])
    recompute_faults(db)
    ana_kayit = next(f for f in _aktif(db) if f.line_id == saha["ana"].id)

    _alarm(db, saha["dk"])
    recompute_faults(db)

    db.refresh(ana_kayit)
    assert ana_kayit.status == "resolved", "ana hat kaydi hala acik"
    assert ana_kayit.resolved_at is not None
    assert {f.line_id for f in _aktif(db)} == {saha["kol"].id}


def test_kol_TEMIZKEN_ana_hat_kaydi_DURUR(db, saha):
    """Kural dar olmali: kolda kendi bolgesi YOKSA ana hattaki bolge
    gercektir ve elenirse ariza tamamen kaybolur."""
    _alarm(db, saha["d1"])
    recompute_faults(db)

    hatlar = {f.line_id for f in _aktif(db)}
    assert hatlar == {saha["ana"].id}


def test_dallanma_diregi_bolgenin_DISINDAYSA_eleme_YOK(db, saha):
    """Sinirlar haric: aralik "son goren cihazdan onceki direk" ile "ilk
    gormeyen cihazdan sonraki direk" arasidir; iki uc direk de arizanin
    saglam tarafinda kalir. Dallanma diregi ucta ise kol bu bolgeyi
    aciklamiyordur.

    Burada ana hattaki RED blogu dallanma diregini ASIYOR (D2 de kirmizi),
    yani ariza kolun otesinde de var — iki kayit da gercektir.
    """
    _alarm(db, saha["d1"])
    _alarm(db, saha["d2"])
    _alarm(db, saha["dk"])
    recompute_faults(db)

    hatlar = {f.line_id for f in _aktif(db)}
    assert saha["ana"].id in hatlar, "ana hattaki gercek ariza elenmis"
    assert saha["kol"].id in hatlar


def test_kol_duzelince_ana_hat_kaydi_GERI_GELMEZ(db, saha):
    """Elenen bolge, kol duzelince kendiliginden geri acilmamali: ayni
    fiziksel ariza gectigi icin ust hattaki cihaz da yesile doner."""
    a1 = _alarm(db, saha["d1"])
    ak = _alarm(db, saha["dk"])
    recompute_faults(db)

    # Ariza gecti: her iki cihazin alarmi da kalkti.
    a1.reset = True
    ak.reset = True
    db.flush()
    recompute_faults(db)

    assert _aktif(db) == [], "ariza gectigi halde acik kayit kalmis"


def test_zincir_ariza_EN_DIPTEKI_kola_yazilir(db):
    """Uc kademe: ANA HAT -> KOL -> ALT KOL. Kayit en dipte kalmali,
    aradaki halkalar tek tek elenmeli.

    Karar HAM bolge tablosuna bakar (elenmis tabloya degil): KOL'un kendi
    bolgesi ALT KOL yuzunden elenmis olsa bile ANA HAT icin "asagida bir
    yerde ariza var" delili odur.
    """
    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()

    def hat(ad: str, kod: str, dallandigi: int | None = None) -> Line:
        l = Line(name=ad, code=kod, region_id=r.id, branched_from_pole_id=dallandigi)
        db.add(l)
        db.flush()
        return l

    def direkler(l: Line, n: int, lon: float) -> list[Pole]:
        arr = []
        for i in range(1, n + 1):
            p = Pole(line_id=l.id, sequence_no=i, latitude=39.0 + i * 0.01, longitude=lon)
            db.add(p)
            arr.append(p)
        db.flush()
        return arr

    def cihaz(kod: str, son: int) -> Device:
        d = Device(code=kod, name=kod, ip_address=f"10.0.0.{son}",
                   latitude=39.0, longitude=35.0)
        db.add(d)
        db.flush()
        return d

    ana = hat("ANA HAT", "ANA")
    ap = direkler(ana, 4, 35.0)
    kol = hat("KOL", "KOL", ap[2].id)
    kp = direkler(kol, 4, 35.1)
    alt = hat("ALT KOL", "ALT", kp[2].id)
    apx = direkler(alt, 2, 35.2)

    d_ana_r, d_ana_g = cihaz("D1", 1), cihaz("D2", 2)
    d_kol_r, d_kol_g = cihaz("K1", 3), cihaz("K2", 4)
    d_alt = cihaz("A1", 5)

    # ANA HAT: 1─[D1]─2─3─[D2]─4, KOL #3 diregindan cikiyor (1<3<4).
    db.add(LineSegment(line_id=ana.id, from_pole_id=ap[0].id, to_pole_id=ap[1].id,
                       device_id=d_ana_r.id))
    db.add(LineSegment(line_id=ana.id, from_pole_id=ap[1].id, to_pole_id=ap[2].id))
    db.add(LineSegment(line_id=ana.id, from_pole_id=ap[2].id, to_pole_id=ap[3].id,
                       device_id=d_ana_g.id))
    # KOL: baglanti teli (cihazsiz) + 1─[K1]─2─3─[K2]─4, ALT KOL #3'ten.
    db.add(LineSegment(line_id=kol.id, from_pole_id=ap[2].id, to_pole_id=kp[0].id))
    db.add(LineSegment(line_id=kol.id, from_pole_id=kp[0].id, to_pole_id=kp[1].id,
                       device_id=d_kol_r.id))
    db.add(LineSegment(line_id=kol.id, from_pole_id=kp[1].id, to_pole_id=kp[2].id))
    db.add(LineSegment(line_id=kol.id, from_pole_id=kp[2].id, to_pole_id=kp[3].id,
                       device_id=d_kol_g.id))
    # ALT KOL: ariza BURADA.
    db.add(LineSegment(line_id=alt.id, from_pole_id=kp[2].id, to_pole_id=apx[0].id,
                       device_id=d_alt.id))
    db.flush()

    _alarm(db, d_ana_r)   # ana hat gordu
    _alarm(db, d_kol_r)   # kol gordu
    _alarm(db, d_alt)     # ariza ALT KOL'da
    recompute_faults(db)

    hatlar = {f.line_id for f in _aktif(db)}
    assert hatlar == {alt.id}, f"kayit en dipteki kolda kalmali, bulunan: {hatlar}"


def test_yerlesme_penceresi_kaydi_da_elenir(db, saha):
    """Kol alarmi GEC dusebilir (haberlesme gecikmesi). Ana hat kaydi bu
    arada acilmis olur; kol alarmi gelince yine de elenmeli — yoksa gecikme
    kalici bir fantom kayda donusur."""
    _alarm(db, saha["d1"])
    recompute_faults(db)
    ana_kayit = next(f for f in _aktif(db) if f.line_id == saha["ana"].id)
    # Kaydi yerlesme penceresinin disina tasi (artik "olgun" bir kayit).
    ana_kayit.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.flush()

    _alarm(db, saha["dk"])
    recompute_faults(db)

    db.refresh(ana_kayit)
    assert ana_kayit.status == "resolved"
