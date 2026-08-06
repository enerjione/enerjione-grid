"""system_event_service.list_system_events — filtre + sayfalama + toplam.

Olay sayfasi eskiden sabit 1000 kayitla sinirliydi ve filtre yoktu;
sayfalama eklenince toplam sayinin FILTRELI kume uzerinden gelmesi ve
offset/limit'in siralamayi (yeniden eskiye) bozmamasi kritik.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.system_event import SystemEvent
from app.services.system_event_service import list_system_events


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    session = Session()
    yield session
    session.close()


BASE_TIME = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _seed(db, count: int = 30) -> None:
    for i in range(count):
        db.add(
            SystemEvent(
                category="alarm" if i % 2 == 0 else "device",
                event_type="alarm_triggered" if i % 2 == 0 else "device_updated",
                severity="warning" if i % 3 == 0 else "info",
                message=f"Olay {i}",
                actor_username="ali" if i % 5 == 0 else None,
                device_code=f"DEV-{i % 4}",
                created_at=BASE_TIME + timedelta(minutes=i),
            )
        )
    db.commit()


def test_sayfalama_ve_toplam(db):
    _seed(db, 30)
    page1, total = list_system_events(db, limit=10, offset=0)
    page2, _ = list_system_events(db, limit=10, offset=10)
    assert total == 30
    assert len(page1) == 10 and len(page2) == 10
    # Yeniden eskiye: ilk sayfa en yeni kayitla baslar, sayfalar kesismez.
    assert page1[0].message == "Olay 29"
    assert page2[0].message == "Olay 19"
    assert {e.id for e in page1}.isdisjoint({e.id for e in page2})


def test_toplam_filtreli_kume_uzerinden(db):
    _seed(db, 30)
    events, total = list_system_events(db, category="alarm", limit=5)
    assert total == 15
    assert len(events) == 5
    assert all(e.category == "alarm" for e in events)


def test_tarih_araligi(db):
    _seed(db, 30)
    date_from = BASE_TIME + timedelta(minutes=10)
    date_to = BASE_TIME + timedelta(minutes=19)
    events, total = list_system_events(db, date_from=date_from, date_to=date_to)
    assert total == 10
    assert all(date_from <= e.created_at.replace(tzinfo=timezone.utc) <= date_to for e in events)


def test_serbest_metin_arama(db):
    _seed(db, 30)
    # Mesajda gecen metin
    events, total = list_system_events(db, q="Olay 12")
    assert total == 1 and events[0].message == "Olay 12"
    # Olay tipinde gecen metin
    _, total_type = list_system_events(db, q="device_updated")
    assert total_type == 15
    # Kullanici adinda gecen metin
    _, total_actor = list_system_events(db, q="ali")
    assert total_actor == 6


def test_event_type_filtresi(db):
    _seed(db, 30)
    events, total = list_system_events(db, event_type="alarm_triggered")
    assert total == 15
    assert all(e.event_type == "alarm_triggered" for e in events)


def test_event_type_like_desenleri(db):
    _seed(db, 30)
    # Durum grubu filtresi: OR'lanan ILIKE desenleri.
    _, total = list_system_events(db, event_type_like=["%_updated", "%_removed"])
    assert total == 15  # device_updated'lar
    _, total_both = list_system_events(
        db, event_type_like=["alarm_triggered", "%_updated"]
    )
    assert total_both == 30


def test_device_code_filtresi(db):
    _seed(db, 30)
    events, total = list_system_events(db, device_code="DEV-1")
    assert total == 8  # i % 4 == 1 -> 1,5,9,...,29
    assert all(e.device_code == "DEV-1" for e in events)


def test_actor_kismi_eslesme(db):
    _seed(db, 30)
    # "al" parcasi "ali" kullanicisini bulmali (ILIKE %al%).
    _, total = list_system_events(db, actor_username="al")
    assert total == 6
