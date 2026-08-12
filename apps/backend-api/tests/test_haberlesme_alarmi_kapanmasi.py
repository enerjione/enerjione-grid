"""HABERLESME ALARMI KENDILIGINDEN KAPANMALI.

SAHA OLAYI (2026-08-12)
-----------------------
Yedi cihazin "Haberleşme arızası" alarmi 08:23'te acildi. Cihazlar saatlerce
sorunsuz haberlesti, gateway tek bir `comm_lost` bile basmadi — alarmlar YINE
DE acik kaldi. Operator ekranda arizali olmayan cihazlari arizali gordu.

Sebep uc kirigin ust uste gelmesiydi:

  1. alarm-service kapanisa YALNIZCA surec ici bir sozlukten karar veriyor
     (`_QUALITY_STATE`). Servis yeniden basladiginda (deploy) sozluk bosaliyor;
     iyi veri gelince "bu cihaz zaten bozuk degildi" deyip CLEAR gondermiyor.
  2. `alarm_reconciliation` — tam bu drift'i temizlemek icin yazilmis worker —
     alarmi KURALINA gore cozuyordu; haberlesme alarminin kurali yok.
  3. Ayni dongudeki `if not alarm.signal_key` filtresi de eliyordu; haberlesme
     alarmi cihaz seviyesidir, sinyali yok.

Yani sistemin kendi kendini iyilestiren tek yolu bu alarm turunu HIC gormuyordu.
Asagidaki testler kapanma kuralini kilitler.
"""

from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.telemetry import Telemetry
from app.services import alarm_reconciliation as ar


@pytest.fixture()
def db():
    import app.models

    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{module.name}")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class _KapanmayanOturum:
    """`_reconcile_once` isini bitirince `close()` cagiriyor; test oturumu
    kapanmasin diye ince bir sarmalayici."""

    def __init__(self, gercek):
        self._gercek = gercek

    def __getattr__(self, ad):
        return getattr(self._gercek, ad)

    def close(self):  # noqa: D401 - bilerek bos
        pass


def _kur(db, *, kalite_gecmisi: list[str], veri_yasi_sn: int = 10) -> AlarmEvent:
    """Bir cihaz + acik haberlesme alarmi + verilen kalitelerde telemetri."""
    db.add(Device(id=1, code="SN2-1", name="Cihaz", ip_address="10.0.0.1",
                  latitude=38.1, longitude=41.1))
    alarm = AlarmEvent(
        id=1,
        device_id=1,
        title=ar.COMM_ALARM_TITLE,
        description="Cihaz haberleşmesinde sorun var.",
        level="critical",
        kind="comm_loss",
        signal_key=None,          # haberlesme alarminin sinyali YOK
        reset=False,
        acknowledged=False,
        created_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    db.add(alarm)
    simdi = datetime.now(timezone.utc)
    for i, kalite in enumerate(kalite_gecmisi):
        db.add(
            Telemetry(
                device_id=1,
                signal_key=f"master.sig{i}",
                value=1.0,
                quality=kalite,
                source_timestamp=simdi - timedelta(seconds=veri_yasi_sn + i),
            )
        )
    db.commit()
    return alarm


def _tik(db, monkeypatch) -> None:
    monkeypatch.setattr(ar, "SessionLocal", lambda: _KapanmayanOturum(db))
    ar._worker._reconcile_once()


def test_cihaz_haberlesiyorsa_alarm_KENDILIGINDEN_kapanir(db, monkeypatch):
    """Sahadaki tam durum: taze ve iyi kaliteli veri akiyor, alarm acik."""
    alarm = _kur(db, kalite_gecmisi=["good", "good"])

    _tik(db, monkeypatch)

    db.refresh(alarm)
    assert alarm.reset is True, (
        "Cihaz haberlesiyor ama alarm acik kaldi — worker haberlesme alarmini "
        "yine gormuyor demektir (kural/sinyal filtrelerine takiliyor)."
    )
    assert alarm.reset_at is not None


def test_cihaz_SESSIZSE_alarm_ACIK_kalir(db, monkeypatch):
    """Telemetri yoksa alarm HAKLIDIR; kapatmak 'yesil yalan' olurdu."""
    alarm = _kur(db, kalite_gecmisi=[])

    _tik(db, monkeypatch)

    db.refresh(alarm)
    assert alarm.reset is False


def test_ESKI_veri_alarmi_kapatmaz(db, monkeypatch):
    """Pencere disinda kalmis iyi okuma 'toparlandi' sayilmaz."""
    alarm = _kur(db, kalite_gecmisi=["good"], veri_yasi_sn=ar._COMM_RECOVERY_WINDOW_SEC + 60)

    _tik(db, monkeypatch)

    db.refresh(alarm)
    assert alarm.reset is False


@pytest.mark.parametrize("kotu", ["comm_lost", "offline", "invalid", "bad"])
def test_pencerede_BOZUK_okuma_varsa_alarm_ACIK_kalir(db, monkeypatch, kotu):
    """Bir kanal hala kopukken alarmi kapatmak sorunu gizlerdi.

    Cihaz iyi kaliteli baska sinyaller basiyor olsa bile: pencerede tek bir
    bozuk okuma varsa haberlesme henuz duzelmemistir.
    """
    alarm = _kur(db, kalite_gecmisi=["good", kotu])

    _tik(db, monkeypatch)

    db.refresh(alarm)
    assert alarm.reset is False


def test_ONAYLANMIS_alarm_kapaninca_SILINIR(db, monkeypatch):
    """Mevcut kural: onaylanmis alarm cozulunce kayit silinir (gereksiz satir).

    Haberlesme alarmi da ayni yoldan (`_resolve_alarm`) gecmeli — ayri bir
    kapanma semantigi olusturmuyoruz.
    """
    alarm = _kur(db, kalite_gecmisi=["good"])
    alarm.acknowledged = True
    db.commit()

    _tik(db, monkeypatch)

    assert db.get(AlarmEvent, 1) is None
