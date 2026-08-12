"""HABERLESME ALARMI HAT ARIZASI URETMEZ.

YASANAN
-------
Haberlesmesi kopan bir cihaz haritada kirmizi cikiyor ve kendisi ile bir
sonraki cihaz arasindaki aralikta HAT ARIZASI aciliyordu: operatore bildirim
gidiyor, ekip sahaya cikiyordu. Oysa sessiz kalan cihaz ariza akimi GORMUS
DEGILDIR — sadece bilmiyoruzdur. "Bilmiyorum"u "ariza var" diye okumak, hem
yanlis yere ekip gonderir hem de gercek arizalarin arasini gurultuyle doldurur.

SEBEP
-----
`produces_fault` bayragi alarm kurallarinda arayuzden yonetiliyor ("Hat
Arızası" secenegi) ama haberlesme alarminin KURALI YOK ve iki ureticisi de
alani hic gondermiyordu; her iki tarafta da varsayilan True devreye giriyordu.

Bu dosya ucunu birden kilitler: iki uretici + motorun bayraga saygisi.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.services.fault_recompute_service import recompute_faults

KOK = pathlib.Path(__file__).resolve().parents[3]


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
    """Tek hat, 3 direk, iki cihaz: D1 | D2."""
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
    db.flush()
    cihazlar = []
    for i in range(2):
        d = Device(code=f"D{i + 1}", name=f"D{i + 1}", ip_address=f"10.0.0.{i + 1}",
                   latitude=39.0, longitude=35.0)
        db.add(d)
        db.flush()
        db.add(
            LineSegment(
                line_id=hat.id, from_pole_id=direkler[i].id,
                to_pole_id=direkler[i + 1].id, device_id=d.id, device_position_t=0.5,
            )
        )
        cihazlar.append(d)
    db.flush()
    return {"hat": hat, "cihazlar": cihazlar}


def _alarm(db, dev: Device, *, produces_fault: bool, kind: str, baslik: str) -> None:
    db.add(
        AlarmEvent(
            device_id=dev.id, title=baslik, description="-", level="critical",
            signal_key=None, kind=kind, produces_fault=produces_fault, reset=False,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.flush()


# --- 1. MOTOR: bayraga saygi -----------------------------------------------

def test_haberlesme_alarmi_HAT_ARIZASI_ACMAZ(db, saha):
    d1, _ = saha["cihazlar"]
    _alarm(db, d1, produces_fault=False, kind="comm_loss", baslik="Haberleşme arızası")

    recompute_faults(db)

    assert db.scalars(select(FaultEvent)).all() == [], (
        "sessiz kalan cihaz 'arizayi gordum' sayildi — ekip bosuna sahaya cikar"
    )


def test_gercek_ariza_alarmi_HALA_ariza_acar(db, saha):
    """Kapinin fazla genis kapanmadiginin kaniti."""
    d1, _ = saha["cihazlar"]
    _alarm(db, d1, produces_fault=True, kind="rule", baslik="Aşırı akım")

    recompute_faults(db)

    assert len(db.scalars(select(FaultEvent)).all()) == 1


def test_haberlesme_alarmi_gercek_arizanin_ARALIGINI_kaydirmaz(db, saha):
    """D1 gercek ariza gordu, D2'nin haberlesmesi koptu.

    D2 "gordum" sayilsaydi aralik bir kademe ILERI kayar ve ekip yanlis
    acikliga giderdi.
    """
    d1, d2 = saha["cihazlar"]
    _alarm(db, d1, produces_fault=True, kind="rule", baslik="Aşırı akım")
    _alarm(db, d2, produces_fault=False, kind="comm_loss", baslik="Haberleşme arızası")

    recompute_faults(db)

    (ariza,) = db.scalars(select(FaultEvent)).all()
    assert ariza.last_red_device_id == d1.id
    assert ariza.first_green_device_id == d2.id


# --- 2. URETICILER: bayragi gercekten yolluyorlar mi ------------------------

def test_backend_haberlesme_alarmini_KURALDAN_acar():
    """`handle_telemetry_alarm_event` — backend tarafi uretici.

    Bayrak artik sabit degil KURALDAN geliyor; model varsayilanina (True)
    dusmedigini davranis testleri (yukarida) zaten dogruluyor. Burada
    kaydin dogru ETIKETLE acildigini kilitliyoruz.
    """
    import inspect

    from app.services import alarm_engine_service

    kaynak = inspect.getsource(alarm_engine_service.handle_telemetry_alarm_event)
    assert "produces_fault=ariza_uretir" in kaynak
    assert 'kind="comm_loss"' in kaynak


def test_alarm_service_haberlesme_alarmini_ARIZA_URETMEZ_diye_yollar():
    """`_build_quality_alarm` — alarm-service tarafi uretici.

    Ayri bir pakette oldugu icin import edilemiyor; kaynak metninden
    dogruluyoruz (bkz. test_alarm_quality_gate.py'deki ayni yontem).
    """
    kaynak = (KOK / "apps" / "alarm-service" / "alarm_service" / "main.py").read_text(
        encoding="utf-8"
    )
    govde = kaynak.split("def _build_quality_alarm(", 1)[1].split("\ndef ", 1)[0]
    assert '"produces_fault": bool(getattr(rule, "produces_fault", False))' in govde, (
        "alarm-service produces_fault'u kuraldan okumuyor; alan hic "
        "gonderilmezse backend semasi True varsayar ve hat arizasi acilir"
    )
    assert '"kind": "comm_loss"' in govde, (
        "kind gonderilmezse backend 'rule' yazar; haberlesme kararsizligi "
        "analizi bu alarmlari kural alarmi sayar"
    )


def test_migration_MEVCUT_haberlesme_alarmlarini_duzeltir(db):
    """0057 gercekten kosturulur — desen kacisi lehceye gore bozulabiliyor.

    Ureticiler duzeltildi ama ACIK kayitlar veritabaninda True kaliyor ve
    cihaz sessizken alarm yeniden gonderilmiyor (dedup); bayrak
    kendiliginden duzelmez. Migration bu yuzden var.
    """
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    d = Device(code="X1", name="X1", ip_address="10.9.9.9", latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    # Uc farkli surumun biraktigi uc iz + dokunulmamasi gereken kural alarmi.
    for baslik, kind in (
        ("Haberleşme arızası", "rule"),          # alarm-service uretimi
        ("SN2-7 haberleşme alarmı", None),        # backend'in eski basligi
        ("Haberleşme arızası", "comm_loss"),      # dogru etiketli
        ("Aşırı akım", "rule"),                   # GERCEK ariza — dokunma
    ):
        db.add(
            AlarmEvent(
                device_id=d.id, title=baslik, description="-", level="critical",
                kind=kind, produces_fault=True, reset=False,
                created_at=datetime.now(timezone.utc),
            )
        )
    db.commit()

    yol = next(
        (KOK / "apps" / "backend-api" / "alembic_migrations" / "versions").glob(
            "*0057_comm_alarms_no_fault.py"
        )
    )
    spec = importlib.util.spec_from_file_location("migration_0057", yol)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)

    baglanti = db.connection()
    modul.op = Operations(MigrationContext.configure(baglanti))
    modul.upgrade()

    satirlar = {
        (a.title, a.kind): a.produces_fault
        for a in db.scalars(select(AlarmEvent)).all()
    }
    assert satirlar[("Haberleşme arızası", "comm_loss")] is False
    assert satirlar[("SN2-7 haberleşme alarmı", "comm_loss")] is False, (
        "eski baslikli kayit hem bayrak hem etiket olarak duzelmeli"
    )
    assert satirlar[("Aşırı akım", "rule")] is True, "gercek ariza alarmi bozuldu"


# --- 3. STANDART KURAL: haberlesme alarmi artik yonetilebilir --------------

def _standart_kural(db, **degisiklikler):
    from app.models.alarm_rule import AlarmRule

    alanlar = {
        "signal_key": "__comm_loss__",
        "name": "Haberleşme arızası",
        "description": "-",
        "level": "critical",
        "rule_kind": "comm_loss",
        "comparator": "eq",
        "threshold": 0.0,
        "is_active": True,
        "produces_fault": False,
    }
    alanlar.update(degisiklikler)
    kural = AlarmRule(**alanlar)
    db.add(kural)
    db.flush()
    return kural


def test_migration_STANDART_kurali_ekler(db):
    """0058 gercekten kosturulur — kural yoksa haberlesme alarmi hic uretilmez."""
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.models.alarm_rule import AlarmRule

    yol = next(
        (KOK / "apps" / "backend-api" / "alembic_migrations" / "versions").glob(
            "*0058_comm_loss_standard_rule.py"
        )
    )
    spec = importlib.util.spec_from_file_location("migration_0058", yol)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    modul.op = Operations(MigrationContext.configure(db.connection()))

    modul.upgrade()
    modul.upgrade()  # IDEMPOTENT olmali

    kurallar = list(db.scalars(select(AlarmRule).where(AlarmRule.rule_kind == "comm_loss")))
    assert len(kurallar) == 1, "migration tekrarinda ikinci kayit acilmamali"
    kural = kurallar[0]
    assert kural.is_active is True
    assert kural.produces_fault is False, "varsayilan: haberlesme hat arizasi uretmez"
    assert kural.name == "Haberleşme arızası", (
        "baslik ile kural adi AYNI olmali — bildirim kanali secimi bunu esliyor"
    )
    assert kural.notify_email is True, (
        "bugunku davranis 'tum kanallar'di; kural eklenince bildirim KESILMEMELI"
    )


def test_kural_PASIFSE_haberlesme_alarmi_acilmaz(db):
    """Operatorun kapatma yolu: kod degisikligi degil, Aktif secenegi."""
    from app.models.alarm import AlarmEvent as AE
    from app.services.alarm_engine_service import handle_telemetry_alarm_event

    d = Device(code="Q1", name="Q1", ip_address="10.8.8.8", latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    _standart_kural(db, is_active=False)

    handle_telemetry_alarm_event(db, {"device_id": d.id, "quality": "offline",
                                      "signal_key": "master.x"})

    assert db.scalars(select(AE)).all() == []


def test_kural_AKTIFSE_alarm_kuraldan_uretilir(db):
    from app.models.alarm import AlarmEvent as AE
    from app.services.alarm_engine_service import handle_telemetry_alarm_event

    d = Device(code="Q2", name="Q2", ip_address="10.8.8.7", latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    _standart_kural(db, level="warning", name="Haberleşme kesildi", produces_fault=True)

    handle_telemetry_alarm_event(db, {"device_id": d.id, "quality": "offline",
                                      "signal_key": "master.x"})

    (alarm,) = db.scalars(select(AE)).all()
    assert alarm.level == "warning", "seviye kuraldan gelmeli"
    assert alarm.title == "Haberleşme kesildi", (
        "baslik kural ADI olmali — bildirim kanali secimi baslikla esliyor"
    )
    assert alarm.produces_fault is True, "operator acikca istediyse ariza uretir"
    assert alarm.kind == "comm_loss"


def test_standart_kural_SILINEMEZ(db):
    """Silinseydi haberlesme alarmi susar ve fark etmenin yolu olmazdi."""
    from fastapi import HTTPException

    from app.api.alarm_rules import delete_alarm_rule
    from app.models.enums import UserRole
    from app.models.user import User

    kural = _standart_kural(db)
    kullanici = User(username="ins", email="i@f.com", full_name="Ins",
                     hashed_password="x", role=UserRole.INSTALLER)
    db.add(kullanici)
    db.flush()

    with pytest.raises(HTTPException) as exc:
        delete_alarm_rule(kural.id, current_user=kullanici, db=db)
    assert exc.value.status_code == 409


def test_standart_kuralin_SINYALI_ve_TURU_duzenlemede_korunur(db):
    """Tur degisseydi motor kurali bulamaz, alarm sessizce susardi."""
    from app.api.alarm_rules import update_alarm_rule
    from app.models.enums import UserRole
    from app.models.user import User
    from app.schemas.alarm_rule import AlarmRuleUpdate

    kural = _standart_kural(db)
    kullanici = User(username="ins2", email="i2@f.com", full_name="Ins",
                     hashed_password="x", role=UserRole.INSTALLER)
    db.add(kullanici)
    db.flush()

    update_alarm_rule(
        kural.id,
        AlarmRuleUpdate(rule_kind="simple", signal_key="master.actual_current",
                        level="info"),
        current_user=kullanici,
        db=db,
    )

    assert kural.rule_kind == "comm_loss"
    assert kural.signal_key == "__comm_loss__"
    assert kural.level == "info", "duzenlenebilir alanlar ETKILENMEMELI"


def test_alarm_service_kurali_okuyor_ve_katalog_suzgecinden_muaf():
    """Kaynak duzeyinde kilit — kural onbellege girmezse ekrandaki kayit
    motorda HIC yok demektir."""
    kaynak = (KOK / "apps" / "alarm-service" / "alarm_service" / "rules.py").read_text(
        encoding="utf-8"
    )
    assert 'rule_kind != "comm_loss" and item["signal_key"] not in alarmable' in kaynak, (
        "haberlesme kurali katalog suzgecine takilir ve sessizce dusurulur"
    )
    assert "def comm_rule(" in kaynak

    ana = (KOK / "apps" / "alarm-service" / "alarm_service" / "main.py").read_text(
        encoding="utf-8"
    )
    govde = ana.split("def _process_device_comm_alarm(", 1)[1].split("\ndef ", 1)[0]
    assert "_CACHE.comm_rule()" in govde, "alarm-service kurali okumuyor"
    assert "is_ready()" in govde, (
        "onbellek dolmadan alarm susturulursa acilisin ilk saniyelerinde "
        "cihaz kopmasi gizlenir"
    )


def test_ingest_ucu_kind_alanini_KABUL_eder():
    """Sema alani tasimazsa uretici bosuna gonderir."""
    from app.schemas.internal import InternalAlarmIngest

    assert "kind" in InternalAlarmIngest.model_fields
    ornek = InternalAlarmIngest(title="x", description="y", kind="comm_loss")
    assert ornek.kind == "comm_loss"
    # Alan gelmezse eski davranis (rule) korunmali.
    assert InternalAlarmIngest(title="x", description="y").kind is None
