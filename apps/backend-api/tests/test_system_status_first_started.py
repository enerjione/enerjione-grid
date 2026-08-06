"""Sistem Bilgisi kartindaki "ilk calistirma" damgasi.

Sistem Durumu sayfasi kurulumun kimligini gosteriyor (proje/musteri adi +
sistemin ne zamandir ayakta oldugu). Zaman bilgisinin kaynagi denetim
kaydindaki EN ESKI olaydir; kurulum damgasi tutan ayri bir alan YOK.

Kilitlenen davranislar:
  * Deger, en eski `system_events` satirindan gelir (en yenisinden degil).
  * Zaman UTC-aware ISO-8601 doner — arayuz `new Date(...)` ile ayristirir,
    naive bir metin yerel saat sanilip saatlerce kayardi.
  * Hic olay yoksa None doner (taze kurulum bir hata degildir).
  * Tablo yok / sorgu patlarsa None doner: bu bilgi sayfanin ana isi degil,
    kaynak metrikleri onun yuzunden kaybolmamali.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.system_status import _first_started_at
from app.db.base import Base
from app.models.system_event import SystemEvent


@pytest.fixture
def db():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables["system_events"]]
    )
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()


def _event(session, when: datetime) -> None:
    session.add(
        SystemEvent(
            category="system",
            event_type="test",
            severity="info",
            message="test",
            created_at=when,
        )
    )
    session.commit()


def test_hic_olay_yoksa_none(db):
    # Taze kurulum: gosterilecek bir sey yok, ama HATA da degil.
    assert _first_started_at(db) is None


def test_en_ESKI_olayin_zamani_doner(db):
    eski = datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc)
    _event(db, eski + timedelta(days=200))
    _event(db, eski)
    _event(db, eski + timedelta(days=10))

    sonuc = _first_started_at(db)
    assert sonuc is not None
    # En yenisi degil EN ESKISI: "sistem ne zamandir ayakta" sorusunun cevabi.
    assert sonuc.startswith("2026-01-15")


def test_zaman_UTC_aware_ISO_doner(db):
    _event(db, datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc))
    sonuc = _first_started_at(db)
    assert sonuc is not None
    # Ayristirilabilir olmali; offset TASINMALI (naive metin yerel saat
    # sanilip arayuzde saatlerce kayardi).
    parsed = datetime.fromisoformat(sonuc)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_sorgu_patlarsa_None(db):
    # Tablo dusurulmus / DB kapali: kaynak metrikleri bu yuzden kaybolmamali.
    db.close()
    assert _first_started_at(db) is None
