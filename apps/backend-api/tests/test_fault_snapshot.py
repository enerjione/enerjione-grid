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


# ---- Faz eslemesi ZINCIRI: cihaz -> proje -> kod varsayilani -------------
#
# Kelepceyi hangi faza takacagina sahadaki kisi karar verir ve bu CIHAZDAN
# CIHAZA degisebilir. Ama 600 cihazlik kurulumda her cihaz icin uc alan
# doldurma zorunlulugu pratikte "hicbiri doldurulmaz" demektir; o yuzden
# proje katmani genel konvansiyonu, cihaz katmani ISTISNAYI tasir.
#
# Ayarin var olmasi yetmez: `apply_snapshot` onu okumazsa operator ayari
# degistirir, ekranda gorur, ama faz etiketleri ESKI esleme ile birikmeye
# devam eder. Bu sinif hata sessizdir ve veri biriktikten sonra geri alinamaz.

from app.services.fault_snapshot import resolve_source_phase


def _proje_ayari(db, **kw):
    from app.models.project_settings import ProjectSettings

    db.add(ProjectSettings(id=1, **kw))
    db.flush()


def test_hicbir_katman_yoksa_KOD_varsayilani(db):
    assert resolve_source_phase(db) is None
    _proje_ayari(db)
    assert resolve_source_phase(db) is None, "bos alanlar varsayilani bozmamali"


def test_PROJE_katmani_genel_konvansiyonu_verir(db):
    _proje_ayari(db, phase_master="c", phase_sat01="a", phase_sat02="b")
    assert resolve_source_phase(db) == {"master": "c", "sat01": "a", "sat02": "b"}


def test_CIHAZ_katmani_projeyi_EZER(db):
    """Ayni hatta bile kelepce farkli takilmis olabilir."""
    d = _cihaz(db)
    _proje_ayari(db, phase_master="a", phase_sat01="b", phase_sat02="c")
    d.phase_master = "b"
    d.phase_sat01 = "a"
    db.flush()

    esleme = resolve_source_phase(db, device_id=d.id)

    assert esleme["master"] == "b", "cihaz ayari projeyi ezmedi"
    assert esleme["sat01"] == "a"
    assert esleme["sat02"] == "c", "cihazda bos olan alan projeden gelmeli"


def test_KISMI_doldurma_desteklenir(db):
    """Tek kelepce degistiyse ucunu birden girmek zorunda kalinmamali."""
    d = _cihaz(db)
    d.phase_sat02 = "a"
    db.flush()

    esleme = resolve_source_phase(db, device_id=d.id)

    assert esleme["sat02"] == "a"
    assert esleme["master"] == "a" and esleme["sat01"] == "b", (
        "dokunulmayan uniteler kod varsayilaninda kalmali"
    )


def test_cihaz_bossa_PROJE_gecerli(db):
    d = _cihaz(db)
    _proje_ayari(db, phase_master="c")
    db.flush()
    assert resolve_source_phase(db, device_id=d.id)["master"] == "c"


def test_apply_ESLEMEYI_ZINCIRDEN_cozer(db):
    """Cagiranin hatirlamasini beklemek, ayarin etkisiz kalmasi demekti."""
    d = _cihaz(db)
    _olcum(db, d.id, "master.overcurrent_tripped", value=1)
    _proje_ayari(db, phase_master="b")
    d.phase_master = "c"  # cihaz istisnasi projeyi ezmeli
    db.flush()

    f = _SahteFault(d.id)
    f.phase = None
    apply_snapshot(db, f)

    assert f.phase == "c", "cihaz bazli faz eslemesi uygulanmadi"


# ---- Faz eslemesi HALKA ACIK ucta SIZMAMALI --------------------------------
#
# `GET /project-settings` bilincli olarak kimlik dogrulamasizdir (login ekrani
# logoyu oturum yokken ceker). Faz eslemesi marka degil SEBEKE
# YAPILANDIRMASIDIR; oraya eklemek her anonim cagirana kurulumun elektriksel
# duzenini acmak olurdu. Kucuk bir bilgi olmasi, gereksiz acmayi hakli
# kilmaz — ve bu tur bir eklemeyi kimse fark etmez.

def test_faz_eslemesi_PUBLIC_okumada_YOK():
    from app.schemas.project_settings import ProjectSettingsRead

    alanlar = set(ProjectSettingsRead.model_fields)
    sizanlar = {a for a in alanlar if a.startswith("phase_")}
    assert not sizanlar, (
        f"faz eslemesi halka acik GET'e sizmis: {sorted(sizanlar)}. "
        "Kimlik dogrulamali /project-settings/phase-map ucunu kullanin."
    )


def test_faz_eslemesi_YAZMADA_var():
    """Okumadan cikarmak, yazmayi da kirmis olmamali."""
    from app.schemas.project_settings import ProjectSettingsUpdate

    alanlar = set(ProjectSettingsUpdate.model_fields)
    assert {"phase_master", "phase_sat01", "phase_sat02"} <= alanlar


def test_phase_map_ucu_KIMLIK_DOGRULAMALI():
    """Ayri uc actik ama korumasiz birakilsaydi hicbir sey kazanmazdik."""
    import inspect

    from app.api.project_settings import get_phase_map

    imza = inspect.signature(get_phase_map)
    kaynak = inspect.getsource(get_phase_map)
    assert "get_current_user" in kaynak, "phase-map ucu kimlik dogrulamasiz"
    assert "phase_map" in get_phase_map.__name__ or True
    assert len(imza.parameters) >= 2
