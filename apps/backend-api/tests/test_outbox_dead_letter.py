"""Outbox dead-letter — TEK ZEHIRLI SATIR TUM KUYRUGU TIKAMASIN.

YASANAN KUSUR
-------------
`flush_outbox` dongusunde satir bazli hata yalitimi YOKTU. Bir satirin
`publish_event` cagrisi patlayinca exception disari cikiyor, `db.commit()`
HIC calismiyor ve batch'teki HICBIR satir published isaretlenmiyordu. Worker
hatayi yutup ayni batch'i sonsuza kadar yeniden deniyordu (HEAD-OF-LINE
BLOCK). Ustelik `published=False` satir hicbir zaman silinmedigi icin tablo
sinirsiz buyuyordu — sahada 1.7 GB / 2.32M satir.

BURADA SINANAN DAVRANISLAR
--------------------------
  1. Zehirli satir batch'i tikamaz; arkasindakiler yayinlanir.
  2. Deneme tavani asilinca satir dead-letter'a damgalanir.
  3. Damgali satir flush sorgusundan DUSER (yoksa `ORDER BY id ASC` onu her
     turda yine en basta gorurdu ve damga hicbir ise yaramazdi).
  4. GECICI ariza (broker kapali) sayaci ARTIRMAZ — aksi halde uzun bir
     kesinti tum kuyrugu dead-letter'a dusurur ve bu veri kaybi demektir.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.models.outbox_event import OutboxEvent
from app.services import outbox_service


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[OutboxEvent.__table__])
    session = sessionmaker(bind=engine, autoflush=False, future=True)()
    try:
        yield session
    finally:
        session.close()


class _SahteBus:
    """`dedup_key`i `zehir_*` olan satirlarda patlayan sahte broker.

    Toplu arayuz (`publish_events`) gercek bus'larla ayni sozlesmeyi tasir:
    girdiyle AYNI SIRADA, mesaj basina sonuc doner (basari = None).
    """

    def __init__(self, *, hepsi_patlasin: bool = False) -> None:
        self.yayinlananlar: list[str] = []
        self.hepsi_patlasin = hepsi_patlasin
        self.parti_sayisi = 0

    def publish_event(self, topic, payload, *, message_id):  # noqa: ANN001
        if self.hepsi_patlasin:
            raise RuntimeError("broker unavailable")
        if message_id.startswith("zehir"):
            raise ValueError("payload reddedildi")
        self.yayinlananlar.append(message_id)

    def publish_events(self, items):  # noqa: ANN001
        self.parti_sayisi += 1
        sonuc: list[Exception | None] = []
        for topic, payload, message_id in items:
            try:
                self.publish_event(topic, payload, message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                sonuc.append(exc)
            else:
                sonuc.append(None)
        return sonuc


def _ekle(db, dedup_key: str, **kw) -> OutboxEvent:
    row = OutboxEvent(
        topic="telemetry.raw_received",
        dedup_key=dedup_key,
        payload_json="{}",
        published=False,
        created_at=datetime.now(timezone.utc),
        attempts=kw.pop("attempts", 0),
        **kw,
    )
    db.add(row)
    db.commit()
    return row


def _bus_kur(monkeypatch, bus) -> None:
    monkeypatch.setattr(outbox_service, "event_bus", bus)


def test_zehirli_satir_ARKASINDAKILERI_tikamiyor(db, monkeypatch):
    """Asil kusur buydu: tek satir tum yayini durduruyordu."""
    bus = _SahteBus()
    _bus_kur(monkeypatch, bus)
    _ekle(db, "zehir-1")  # id=1, sirada ONCE
    _ekle(db, "iyi-1")
    _ekle(db, "iyi-2")

    published = outbox_service.flush_outbox(db, limit=10)

    assert published == 2, "zehirli satir arkasindakileri hala tikiyor"
    assert bus.yayinlananlar == ["iyi-1", "iyi-2"]


def test_zehirli_satir_TAVANA_gelince_damgalaniyor(db, monkeypatch):
    monkeypatch.setattr(settings, "outbox_max_publish_attempts", 3)
    bus = _SahteBus()
    _bus_kur(monkeypatch, bus)
    _ekle(db, "zehir-1")
    _ekle(db, "iyi-1")

    for tur in range(3):
        # Her turda en az bir saglam satir gecmeli ki hata SATIRA OZGU sayilsin.
        _ekle(db, f"iyi-tur{tur}")
        outbox_service.flush_outbox(db, limit=10)

    zehir = db.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == "zehir-1"))
    assert zehir.attempts == 3
    assert zehir.dead_letter_at is not None, "tavan asildi ama damga yok"
    assert zehir.last_error and "ValueError" in zehir.last_error, (
        "last_error bos — dead-letter satirin TEK teshis kaniti bu sutun"
    )


def test_damgali_satir_FLUSH_sorgusundan_dusuyor(db, monkeypatch):
    """Damga tek basina yetmez: satir sorguda kalirsa `ORDER BY id ASC`
    onu her turda yine EN BASTA gorur ve tikanma damgaya RAGMEN surer."""
    monkeypatch.setattr(settings, "outbox_max_publish_attempts", 1)
    bus = _SahteBus()
    _bus_kur(monkeypatch, bus)
    _ekle(db, "zehir-1")
    _ekle(db, "iyi-1")
    outbox_service.flush_outbox(db, limit=10)  # zehir damgalanir

    _ekle(db, "iyi-2")
    outbox_service.flush_outbox(db, limit=10)

    # Ikinci turda zehirli satir HIC denenmemis olmali: attempts artmadi.
    zehir = db.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == "zehir-1"))
    assert zehir.attempts == 1, (
        "damgali satir flush sorgusuna hala giriyor — damga ise yaramiyor"
    )


def test_GECICI_broker_arizasi_sayaci_ARTIRMIYOR(db, monkeypatch):
    """Broker kesintisi TUM satirlari esit etkiler; bu veri kaybi degil,
    beklemedir.

    Hicbir satir gecmediyse ariza sistemiktir. Sayac artsaydi 5 turluk bir
    kesinti butun kuyrugu dead-letter'a dusururdu — yani yayinlanmasi gereken
    gercek olaylar sessizce kuyruktan cikardi.
    """
    monkeypatch.setattr(settings, "outbox_max_publish_attempts", 2)
    _bus_kur(monkeypatch, _SahteBus(hepsi_patlasin=True))
    _ekle(db, "iyi-1")
    _ekle(db, "iyi-2")

    for _ in range(5):
        with pytest.raises(RuntimeError):
            outbox_service.flush_outbox(db, limit=10)
        db.rollback()

    rows = list(db.scalars(select(OutboxEvent)).all())
    assert [r.attempts for r in rows] == [0, 0], (
        "gecici broker arizasi sayaci artirdi — uzun kesinti tum kuyrugu "
        "dead-letter'a dusurur"
    )
    assert all(r.dead_letter_at is None for r in rows)
    assert all(r.published is False for r in rows)


def test_SISTEMIK_ariza_caller_a_YAYILIYOR(db, monkeypatch):
    """Kesinti gorunur kalmali: worker'in hata sayaci/geri cekilme mantigi
    ancak exception ile calisir. Sessizce 0 donseydi broker kapaliyken
    hicbir uyari uretilmezdi."""
    _bus_kur(monkeypatch, _SahteBus(hepsi_patlasin=True))
    _ekle(db, "iyi-1")

    with pytest.raises(RuntimeError):
        outbox_service.flush_outbox(db, limit=10)


def test_basarili_satirlar_ZEHIRLIYE_ragmen_KALICI(db, monkeypatch):
    """Kismi ilerleme commit edilmeli; yoksa her tur bastan baslar."""
    bus = _SahteBus()
    _bus_kur(monkeypatch, bus)
    _ekle(db, "zehir-1")
    _ekle(db, "iyi-1")

    outbox_service.flush_outbox(db, limit=10)
    db.expire_all()

    iyi = db.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == "iyi-1"))
    assert iyi.published is True
    assert iyi.published_at is not None
