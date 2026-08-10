"""Analiz uclarinin yaniti ile `frontend-web/src/shared/types.ts` UYUSMALI.

NEDEN BU TEST VAR
-----------------
`types.ts` ELLE yaziliyor; backend yanitindan turetilmiyor. Bir alan adi
degistiginde ya da eklendiginde hicbir sey patlamaz:

  * TypeScript derlemesi gecer (tip dosyasi kendi icinde tutarlidir),
  * backend testleri gecer (kendi sozlukleriyle konusurlar),
  * ekran alani `undefined` okur ve sessizce "—" gosterir.

Bu ekran bakim butcesini yonlendirecek. "Bataryasi en hizli tukenen cihaz"
kartinin bos gorunmesi ile "boyle bir cihaz yok" arasindaki farki kimse
gozle ayirt edemez. Sozlesme burada, CALISTIRILARAK kilitleniyor.

Ayni desen `test_event_export_labels.py` icinde de var (tr.json <-> backend
etiketleri); bu onun analiz katmanindaki karsiligi.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.api.faults import fault_analytics, fault_device_health, fault_system_health
from app.models.enums import UserRole
from app.models.user import User
from app.services import device_health_analytics as dsa
from app.services import fault_analytics_service as fas

# tests/ -> backend-api/ -> apps/ -> frontend-web/...
TYPES_TS = (
    Path(__file__).resolve().parents[2] / "frontend-web" / "src" / "shared" / "types.ts"
)


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


def _muhendis(db) -> User:
    """Kapsam filtresi olmayan rol. Bu dosya BICIMI olcuyor; gorunurluk
    kurallari test_fault_analytics.py icinde kilitli."""
    u = User(
        username="soz", email="soz@f.com", full_name="Soz",
        hashed_password="x", role=UserRole.ENGINEER,
    )
    db.add(u)
    db.flush()
    return u


def _tip_govdesi(tip: str) -> str:
    metin = TYPES_TS.read_text(encoding="utf-8")
    m = re.search(rf"export type {tip} = \{{", metin)
    assert m, f"types.ts icinde `{tip}` yok"
    basla = m.end() - 1
    derinlik = 0
    for i in range(basla, len(metin)):
        if metin[i] == "{":
            derinlik += 1
        elif metin[i] == "}":
            derinlik -= 1
            if derinlik == 0:
                return metin[basla + 1 : i]
    raise AssertionError(f"`{tip}` blogu kapanmiyor")


def _ust_duzey_alanlar(govde: str) -> set[str]:
    """Yalnizca derinlik 0'daki (ic ice objelere girmeyen) alan adlari.

    Alanin BASLADIGI derinlige bakilir, bittigi derinlige degil: `summary: {`
    satiri sondaki `{` yuzunden derinlik 1'de biter, oysa alanin kendisi ust
    duzeydir. Ayirici hem satir sonu hem ';' — tek satira sigan tipler
    (`{ a: number; b: number }[]`) noktali virgulle ayrilir.
    """
    alanlar: set[str] = set()
    derinlik = 0
    parca = ""
    parca_derinligi = 0

    def bosalt() -> None:
        nonlocal parca
        if parca_derinligi == 0:
            m = re.match(r"\s*(\w+)\??\s*:", parca)
            if m:
                alanlar.add(m.group(1))
        parca = ""

    for ch in govde:
        if ch == "\n" or (ch in ";," and derinlik == 0):
            bosalt()
            parca_derinligi = derinlik
            continue
        if parca == "":
            parca_derinligi = derinlik
        parca += ch
        if ch in "{[(":
            derinlik += 1
        elif ch in "}])":
            derinlik -= 1
    bosalt()
    return alanlar


def _alt_blok_alanlari(govde: str, alan: str) -> set[str]:
    """`alan: { ... }` ya da `alan: { ... }[]` blogunun alan adlari."""
    m = re.search(rf"(?m)^\s*{alan}\??\s*:\s*\{{", govde)
    assert m, f"alt blok bulunamadi: {alan}"
    basla = m.end() - 1
    derinlik = 0
    for i in range(basla, len(govde)):
        if govde[i] == "{":
            derinlik += 1
        elif govde[i] == "}":
            derinlik -= 1
            if derinlik == 0:
                return _ust_duzey_alanlar(govde[basla + 1 : i])
    raise AssertionError(f"`{alan}` blogu kapanmiyor")


def _esitle(ad: str, gelen: dict, beklenen: set[str]) -> None:
    var = set(gelen.keys())
    eksik = beklenen - var
    fazla = var - beklenen
    assert not eksik, (
        f"{ad}: types.ts bu alanlari bekliyor ama backend VERMIYOR -> "
        f"{sorted(eksik)}. Ekran bunlari sessizce bos gosterir."
    )
    assert not fazla, (
        f"{ad}: backend bu alanlari veriyor ama types.ts'te YOK -> "
        f"{sorted(fazla)}. Ya tipe ekleyin ya da yanittan cikarin."
    )


# ---------------------------------------------------------------------------
# FaultAnalytics
# ---------------------------------------------------------------------------

def test_ariza_analizi_yaniti_TS_TIPIYLE_ayni_alanlari_tasir(db):
    govde = _tip_govdesi("FaultAnalytics")
    gelen = fault_analytics(days=365, current_user=_muhendis(db), db=db)

    _esitle("FaultAnalytics", gelen, _ust_duzey_alanlar(govde))
    _esitle("FaultAnalytics.summary", gelen["summary"], _alt_blok_alanlari(govde, "summary"))
    _esitle(
        "FaultAnalytics.rule_accuracy",
        gelen["rule_accuracy"],
        _alt_blok_alanlari(govde, "rule_accuracy"),
    )
    _esitle("FaultAnalytics.sankey", gelen["sankey"], _alt_blok_alanlari(govde, "sankey"))


def test_sankey_dugum_onekleri_ARAYUZUN_bekledigi_bicimde(db):
    """Arayuz dugum adini ilk ':' karakterinden bolup onu etikete cevirir
    ve faz dugumlerini L1/L2/L3'e esler. Onek kalkarsa faz renkleri ve
    etiketleri sessizce bozulur."""
    from datetime import datetime, timezone

    from app.models.device import Device
    from app.models.fault import FaultEvent
    from app.models.grid_topology import Line, Pole, Region

    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()
    ln = Line(name="HAT-1", code="HAT-1", region_id=r.id)
    db.add(ln)
    db.flush()
    d = Device(code="SN2-1", name="F1", ip_address="10.0.0.5", latitude=39.05, longitude=35.05)
    db.add(d)
    db.flush()
    p1 = Pole(line_id=ln.id, sequence_no=1, latitude=39.0, longitude=35.0)
    p2 = Pole(line_id=ln.id, sequence_no=2, latitude=39.1, longitude=35.1)
    db.add_all([p1, p2])
    db.flush()
    db.add(
        FaultEvent(
            line_id=ln.id,
            region_id=r.id,
            last_red_device_id=d.id,
            from_pole_id=p1.id,
            to_pole_id=p2.id,
            status="open",
            opened_at=datetime.now(timezone.utc),
            phase="a",
        )
    )
    db.flush()

    akis = fas.sankey_akisi(db, days=365, visible_line_ids=None)
    adlar = {n["name"]: n["tier"] for n in akis["nodes"]}
    assert adlar == {"B:Merkez": "region", "H:HAT-1": "line", "F:A": "phase"}
    # Kenarlar dugum ADIYLA eslesir; onek dusurulurse baglar kopar.
    for l in akis["links"]:
        assert l["source"] in adlar and l["target"] in adlar


# ---------------------------------------------------------------------------
# SystemHealth
# ---------------------------------------------------------------------------

def test_sistem_sagligi_yaniti_TS_TIPIYLE_ayni_alanlari_tasir(db):
    govde = _tip_govdesi("SystemHealth")
    gelen = fault_system_health(days=90, current_user=_muhendis(db), db=db)

    _esitle("SystemHealth", gelen, _ust_duzey_alanlar(govde))
    _esitle(
        "SystemHealth.alarm_summary",
        gelen["alarm_summary"],
        _alt_blok_alanlari(govde, "alarm_summary"),
    )
    # Isi matrisi bos veride de TAM bicimde doner; arayuz `cells.length`
    # kontrolunden once `buckets`/`truncated` okuyor.
    _esitle(
        "SystemHealth.alarm_heatmap",
        gelen["alarm_heatmap"],
        _alt_blok_alanlari(govde, "alarm_heatmap"),
    )


def test_sistem_sagligi_liste_ogeleri_TS_ile_ayni(db):
    """Bos veride liste ogesi cikmaz; ogenin BICIMI yine de kilitlenmeli."""
    from datetime import datetime, timedelta, timezone

    from app.models.alarm import AlarmEvent
    from app.models.device import Device

    d = Device(code="SN2-9", name="Kopan", ip_address="10.0.0.9", latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    simdi = datetime.now(timezone.utc)
    db.add_all(
        [
            AlarmEvent(
                device_id=d.id,
                title="Asiri akim",
                description="Esik asildi",
                level="critical",
                signal_key="oc",
                kind="rule",
                created_at=simdi - timedelta(days=1),
            ),
            AlarmEvent(
                device_id=d.id,
                title="Kopan haberleşme alarmı",
                description="Cihaz yanit vermiyor",
                level="critical",
                kind="comm_loss",
                created_at=simdi - timedelta(days=2),
            ),
        ]
    )
    db.flush()

    govde = _tip_govdesi("SystemHealth")
    gelen = fault_system_health(days=90, current_user=_muhendis(db), db=db)

    assert gelen["top_rules"], "kural alarmi eklendi ama listeye girmedi"
    _esitle("SystemHealth.top_rules[]", gelen["top_rules"][0], _alt_blok_alanlari(govde, "top_rules"))

    assert gelen["flapping_devices"], "haberlesme alarmi eklendi ama listeye girmedi"
    _esitle(
        "SystemHealth.flapping_devices[]",
        gelen["flapping_devices"][0],
        _alt_blok_alanlari(govde, "flapping_devices"),
    )


# ---------------------------------------------------------------------------
# DeviceHealth
# ---------------------------------------------------------------------------

def test_cihaz_sagligi_yaniti_TS_TIPIYLE_ayni_alanlari_tasir(db):
    govde = _tip_govdesi("DeviceHealth")
    gelen = fault_device_health(days=90, current_user=_muhendis(db), db=db)
    _esitle("DeviceHealth", gelen, _ust_duzey_alanlar(govde))


def test_isi_haritasi_ogesi_TS_ile_ayni(db):
    """Isi haritasi noktasi `latitude/longitude/weight` tasimali; arayuzun
    olcekleme cekirdegi (heatField.ts) tam bu uc alani okur."""
    from datetime import datetime, timezone

    from app.models.device import Device
    from app.models.fault import FaultEvent
    from app.models.grid_topology import Line, Pole, Region

    r = Region(name="Merkez", code="MRK")
    db.add(r)
    db.flush()
    ln = Line(name="HAT-1", code="HAT-1", region_id=r.id)
    db.add(ln)
    db.flush()
    d = Device(code="SN2-1", name="F1", ip_address="10.0.0.5", latitude=39.05, longitude=35.05)
    db.add(d)
    db.flush()
    p1 = Pole(line_id=ln.id, sequence_no=1, latitude=39.0, longitude=35.0)
    p2 = Pole(line_id=ln.id, sequence_no=2, latitude=39.1, longitude=35.1)
    db.add_all([p1, p2])
    db.flush()
    db.add(
        FaultEvent(
            line_id=ln.id,
            region_id=r.id,
            last_red_device_id=d.id,
            from_pole_id=p1.id,
            to_pole_id=p2.id,
            status="open",
            opened_at=datetime.now(timezone.utc),
        )
    )
    db.flush()

    govde = _tip_govdesi("DeviceHealth")
    noktalar = dsa.ariza_yogunlugu(db, days=365, visible_line_ids=None)
    assert noktalar, "konumlu ariza eklendi ama isi haritasina girmedi"
    _esitle(
        "DeviceHealth.fault_heatmap[]",
        noktalar[0],
        _alt_blok_alanlari(govde, "fault_heatmap"),
    )
    # Agirlik POZITIF olmali: heatField sifir/negatifi cizmez, nokta
    # haritadan sessizce dusardi.
    assert noktalar[0]["weight"] > 0
