"""`/internal/device-runtime-health` — SCADA cikisinin TEK gercek kaynagi.

NEDEN VAR
---------
IEC 104 outbound yalnizca `telemetry.normalized.*` tuketiyordu; oradaki her
sey bir SAHA OLCUMUDUR. Calisma-zamani sagligi ise gateway'in cihazla olan
OTURUMU hakkindadir. Dis SCADA bu yuzden "cihaz uyuyor" ile "haberlesme
kayboldu" arasindaki farki goremiyordu: saglikli, uyuyan bir Horstmann
filosu arizali gibi gorunuyordu.

BAYATLIK KARARI BURADA — VE YALNIZCA BURADA
-------------------------------------------
"Bu gozleme hala guvenilir mi" sorusunun cevabi backend'de
(`device_session_readiness.gozlem_bayat`). IEC 104 tarafinda IKINCI bir
esik tanimlamak, arayuzde `bilinmiyor` gorunen bir cihazin SCADA'da yillar
once kalmis `smart_idle` degeriyle "saglikli" gorunmesine yol acardi.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import internal
from app.db.base import Base
from app.models.device_runtime_health import DeviceRuntimeHealth
from app.services import device_session_readiness as hazirlik

#: Uc `datetime.now()`u FONKSIYON ICINDE import ediyor; sahte saat
#: enjekte etmek uretim kodunu yalnizca test icin egmek olurdu. Bunun
#: yerine damgalar GERCEK saate GORELI kurulur — gercek kod yolu surulur.
def _simdi() -> datetime:
    return datetime.now(timezone.utc)


JETON = "svc-token"


@pytest.fixture()
def db(monkeypatch):
    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    from app.core.config import settings

    monkeypatch.setattr(settings, "internal_service_token", JETON, raising=False)
    yield s
    s.close()


def _satir(db, kod: str, durum: str, *, late: bool = False,  # noqa: ANN001
           yas_sn: float = 0.0) -> DeviceRuntimeHealth:
    """`yas_sn`: gozlem KAC SANIYE once alinmis (0 = simdi)."""
    gozlem = _simdi() - timedelta(seconds=yas_sn)
    r = DeviceRuntimeHealth(
        device_code=kod, gateway_code="GW-1", connection_state=durum,
        connected=durum == "online", reachable=durum == "online",
        report_late=late, updated_at=gozlem,
    )
    db.add(r)
    db.flush()
    return r


def _cagir(db):  # noqa: ANN001
    return internal.device_runtime_health_internal(db=db, x_service_token=JETON)


# ===========================================================================
# TEMEL
# ===========================================================================


def test_her_cihaz_icin_satir_doner(db):
    _satir(db, "A", "online")
    _satir(db, "B", "smart_idle")
    cikti = _cagir(db)
    assert {c["device_code"] for c in cikti} == {"A", "B"}


def test_SMART_IDLE_oldugu_gibi_doner(db):
    """SCADA'ya `lost` DEGIL `smart_idle` gitmeli — isin ana kabul olcutu."""
    _satir(db, "A", "smart_idle")
    (c,) = _cagir(db)
    assert c["state"] == "smart_idle"
    assert c["state"] != "lost"


def test_report_late_AYRI_alan(db):
    """Kanonik durumu EZMEZ: smart_idle + late ikisi birden gorunur."""
    _satir(db, "A", "smart_idle", late=True)
    (c,) = _cagir(db)
    assert c["state"] == "smart_idle"
    assert c["report_late"] is True


def test_durum_metni_NORMALIZE_edilir(db):
    _satir(db, "A", "  ONLINE  ")
    (c,) = _cagir(db)
    assert c["state"] == "online"


def test_bos_durum_UNKNOWN(db):
    """Kolon NOT NULL, ama bos dize yazilabilir. Savunma amacli
    normalizasyon: bos deger bir DURUM IDDIASI degildir."""
    r = _satir(db, "A", "online")
    r.connection_state = ""
    db.flush()
    (c,) = _cagir(db)
    assert c["state"] == "unknown"


# ===========================================================================
# BAYATLIK — TEK KAYNAK
# ===========================================================================


def test_BAYAT_gozlem_UNKNOWN_olur(db):
    """Eski bir `smart_idle` degerini sonsuza kadar "saglikli" diye
    yayinlamak, SCADA'ya dogrulanmamis bir iyimserlik satmak olurdu."""
    _satir(db, "A", "smart_idle", yas_sn=hazirlik.RUNTIME_STALE_AFTER_SEC + 60)
    (c,) = _cagir(db)
    assert c["stale"] is True
    assert c["state"] == "unknown", "bayat gozlem hala saglikli gosteriliyor"


def test_BAYAT_gozlemde_report_late_IDDIA_EDILMEZ(db):
    """Bilmedigimiz seyi `0` (gecikme yok) diye yayinlamak iyi haber
    uydurmak olurdu."""
    _satir(db, "A", "smart_idle", late=True,
           yas_sn=hazirlik.RUNTIME_STALE_AFTER_SEC + 60)
    (c,) = _cagir(db)
    assert c["report_late"] is None


def test_TAZE_gozlem_bayat_sayilmaz(db):
    _satir(db, "A", "smart_idle", yas_sn=hazirlik.RUNTIME_STALE_AFTER_SEC - 30)
    (c,) = _cagir(db)
    assert c["stale"] is False
    assert c["state"] == "smart_idle"


def test_bayatlik_esigi_BACKEND_ile_AYNI():
    """IEC 104 tarafinda IKINCI bir esik tanimlanmamali."""
    import pathlib

    kaynak = (
        pathlib.Path(__file__).resolve().parents[3]
        / "apps/iec104-outbound/iec104_outbound/runtime_health.py"
    ).read_text(encoding="utf-8")
    for yasak in ("STALE", "stale_after", "900", "timedelta"):
        assert yasak not in kaynak, (
            f"IEC 104 tarafinda kendi bayatlik esigi var ({yasak}) — iki "
            "farkli saglik gercegi olusur"
        )


# ===========================================================================
# GERIYE UYUMLULUK
# ===========================================================================


def test_saglik_satiri_YOKSA_cihaz_LISTEDE_YOK(db):
    """Gateway <1.15 saglik gondermez; sahte `smart_idle` URETILMEZ.

    SCADA tarafinda nokta UNKNOWN kalir (deger hic yazilmaz), ki dogru
    olan budur — "bilmiyoruz" bir durum iddiasi degildir.
    """
    assert _cagir(db) == []


def test_yetkisiz_cagri_REDDEDILIR(db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        internal.device_runtime_health_internal(db=db, x_service_token="yanlis")


def test_sirali_doner(db):
    """Deterministik sira: SCADA tarafinda nokta uretimi tekrarlanabilir."""
    for kod in ("C", "A", "B"):
        _satir(db, kod, "online")
    assert [c["device_code"] for c in _cagir(db)] == ["A", "B", "C"]


def test_zaman_damgasi_UTC_farkindalikli(db):
    """Naive damga UTC+3'te uc saat kaymis gorunurdu."""
    r = _satir(db, "A", "online")
    r.updated_at = _simdi().replace(tzinfo=None)
    db.flush()
    (c,) = _cagir(db)
    assert c["updated_at"] is not None
    assert c["updated_at"].tzinfo is not None
