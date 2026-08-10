"""Ariza aninda cihaz durumu ARIZA KAYDINA yaziliyor mu?

Ham telemetri 90 gunde dusuyor. Anlik goruntu alinmazsa ariza analizi bir
yil sonra "elimizde kanit yok" noktasina duser — ve bu kayip GERI ALINAMAZ,
cunku veri o an yazilmadiysa bir daha uretilemez.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device import Device
from app.models.telemetry_latest import TelemetryLatest
from app.services.fault_snapshot import apply_snapshot, build_snapshot


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


def _cihaz(db) -> Device:
    d = Device(
        code="SN2-0001", name="Fider 1", ip_address="10.0.0.5",
        latitude=39.0, longitude=35.0,
    )
    db.add(d)
    db.flush()
    return d


def _olcum(db, device_id: int, key: str, value=None, value_string=None) -> None:
    db.add(
        TelemetryLatest(
            device_id=device_id,
            signal_key=key,
            value=value,
            value_string=value_string,
            quality="good",
            source_timestamp=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )


def test_alarm_imzasi_TAM_ANAHTARLA_saklanir(db):
    """Onek soyulursa faz bilgisi kaybolur — imza tam anahtar tutmali."""
    d = _cihaz(db)
    _olcum(db, d.id, "sat01.overcurrent_tripped", value=1)
    _olcum(db, d.id, "master.overcurrent_tripped", value=0)
    db.flush()

    snap = build_snapshot(db, device_id=d.id)

    assert snap["trigger_signals"] == ["sat01.overcurrent_tripped"]
    assert snap["phase"] == "b", "sat01 -> B fazi"


def test_pasif_bayrak_imzaya_GIRMEZ(db):
    d = _cihaz(db)
    _olcum(db, d.id, "master.tamper_detection", value=0)
    db.flush()
    snap = build_snapshot(db, device_id=d.id)
    assert snap["trigger_signals"] == []
    assert "auto_cause_code" not in snap


def test_deger_bos_METINDEN_okunur(db):
    """DNP3 bazi noktalarda sayisal alani doldurmaz; yalnizca `value`
    okumak o sinyalleri sessizce pasif gosterirdi."""
    d = _cihaz(db)
    _olcum(db, d.id, "master.tamper_detection", value=None, value_string="true")
    db.flush()
    snap = build_snapshot(db, device_id=d.id)
    assert snap["trigger_signals"] == ["master.tamper_detection"]
    assert snap["auto_cause_code"] == "third_party"


def test_ariza_akimi_UC_FAZIN_EN_BUYUGU(db):
    """Ortalama almak arizali fazi saglam fazlarla seyreltir ve tepeyi gizler."""
    d = _cihaz(db)
    _olcum(db, d.id, "master.fault_current", value=12.0)
    _olcum(db, d.id, "sat01.fault_current", value=840.0)
    _olcum(db, d.id, "sat02.fault_current", value=15.0)
    db.flush()
    snap = build_snapshot(db, device_id=d.id)
    assert snap["fault_current_a"] == 840.0


def test_olcumler_ve_sayaclar_kaydedilir(db):
    d = _cihaz(db)
    _olcum(db, d.id, "master.fault_current", value=500.0)
    _olcum(db, d.id, "master.actual_current", value=88.0)
    _olcum(db, d.id, "master.conductor_temperature", value=61.5)
    _olcum(db, d.id, "master.momentary_fault_counter", value=7)
    _olcum(db, d.id, "master.permanent_fault_counter", value=2)
    db.flush()
    snap = build_snapshot(db, device_id=d.id)
    assert snap["fault_current_a"] == 500.0
    assert snap["load_current_before_a"] == 88.0
    assert snap["conductor_temp_c"] == 61.5
    assert snap["momentary_fault_count"] == 7
    assert snap["permanent_fault_count"] == 2
    assert snap["measured_at"] is not None


def test_cikarim_alanlari_URETILDIYSE_yazilir(db):
    d = _cihaz(db)
    _olcum(db, d.id, "master.current_loss", value=1)
    _olcum(db, d.id, "master.permanent_fault", value=1)
    db.flush()
    snap = build_snapshot(db, device_id=d.id)
    assert snap["auto_cause_code"] == "conductor_break"
    assert snap["fault_kind"] == "permanent"


def test_uretilmeyen_alan_sozlukte_YOK(db):
    """Sozlukte olmayan alan, cagiran tarafta mevcut degeri EZMEZ."""
    d = _cihaz(db)
    _olcum(db, d.id, "master.overcurrent_tripped", value=1)
    db.flush()
    snap = build_snapshot(db, device_id=d.id)
    # Asiri akimda sebep UYDURULMAZ.
    assert "auto_cause_code" not in snap
    # Kalicilik bayragi yok -> tur de yok.
    assert "fault_kind" not in snap


def test_olcumu_olmayan_cihaz_BOS_doner(db):
    d = _cihaz(db)
    db.flush()
    assert build_snapshot(db, device_id=d.id) == {}


def test_faz_eslemesi_gecirilebilir(db):
    d = _cihaz(db)
    _olcum(db, d.id, "master.overcurrent_tripped", value=1)
    db.flush()
    snap = build_snapshot(
        db, device_id=d.id, source_phase={"master": "c", "sat01": "a", "sat02": "b"}
    )
    assert snap["phase"] == "c"


# ---- apply_snapshot: ariza kaydini ASLA dusurmemeli ------------------------

class _SahteFault:
    def __init__(self, device_id: int) -> None:
        self.last_red_device_id = device_id
        self.trigger_signals = None
        self.auto_cause_code = None


def test_apply_alanlari_kayda_yazar(db):
    d = _cihaz(db)
    _olcum(db, d.id, "master.tamper_detection", value=1)
    db.flush()
    f = _SahteFault(d.id)

    apply_snapshot(db, f)

    assert f.trigger_signals == ["master.tamper_detection"]
    assert f.auto_cause_code == "third_party"


def test_apply_HATA_YUTAR_ariza_acilmaya_devam_eder(db, monkeypatch):
    """Anlik goruntu ariza motorunun ICINDE kosuyor. Patlarsa ariza kaydinin
    kendisi acilamaz — bu cok daha agir bir hata olurdu."""
    from app.services import fault_snapshot

    def _patla(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("telemetri okunamadi")

    monkeypatch.setattr(fault_snapshot, "build_snapshot", _patla)
    f = _SahteFault(999)

    fault_snapshot.apply_snapshot(db, f)  # patlamamali

    assert f.trigger_signals is None


# ---- Faz eslemesi Proje Ayarlari'ndan OKUNMALI -----------------------------
#
# Ayarin var olmasi yetmez: `apply_snapshot` onu okumazsa operator ayari
# degistirir, ekranda gorur, ama faz etiketleri ESKI esleme ile birikmeye
# devam eder. Bu sinif hata sessizdir ve veri biriktikten sonra geri alinamaz.

def _proje_ayari(db, **kw):
    from app.models.project_settings import ProjectSettings

    db.add(ProjectSettings(id=1, **kw))
    db.flush()


def test_esleme_TANIMSIZSA_varsayilan(db):
    from app.services.fault_snapshot import resolve_source_phase

    assert resolve_source_phase(db) is None, "satir yokken varsayilan kullanilmali"
    _proje_ayari(db)
    assert resolve_source_phase(db) is None, "bos alanlar varsayilani bozmamali"


def test_esleme_KISMI_olabilir(db):
    """Tek kelepce degistiyse ucunu birden girmek zorunda kalinmamali."""
    from app.services.fault_snapshot import resolve_source_phase

    _proje_ayari(db, phase_master="c")
    esleme = resolve_source_phase(db)
    assert esleme == {"master": "c", "sat01": "b", "sat02": "c"} or esleme["master"] == "c"
    assert esleme["sat01"] == "b", "dokunulmayan unite varsayilanda kalmali"


def test_apply_ESLEMEYI_ayardan_okur(db):
    """Cagiranin hatirlamasini beklemek, ayarin etkisiz kalmasi demekti."""
    d = _cihaz(db)
    _olcum(db, d.id, "master.overcurrent_tripped", value=1)
    _proje_ayari(db, phase_master="c")
    db.flush()

    f = _SahteFault(d.id)
    f.phase = None
    apply_snapshot(db, f)

    assert f.phase == "c", "Proje Ayarlari'ndaki esleme uygulanmadi"
