from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import jetstream_bus as jsb
from app.services.jetstream_bus import JetStreamBus

pytestmark_nats = pytest.mark.skipif(
    not jsb.NATS_AVAILABLE, reason="nats-py kurulu degil"
)


def _bus(**overrides) -> JetStreamBus:
    kwargs = dict(
        url="nats://127.0.0.1:4222",
        stream_raw="TELEMETRY_RAW",
        stream_normalized="TELEMETRY_NORMALIZED",
        stream_dlq="TELEMETRY_DLQ",
        subject_raw_pattern="e1.telemetry.raw.>",
        subject_normalized_pattern="e1.telemetry.normalized.>",
        subject_dlq_pattern="e1.dlq.>",
        max_age_days_raw=7,
        max_age_days_normalized=7,
        max_age_days_dlq=30,
        connect_timeout_sec=1,
    )
    kwargs.update(overrides)
    return JetStreamBus(**kwargs)


class _FakeJs:
    """stream_info/add_stream/update_stream cagrilarini kaydeden sahte JS."""

    def __init__(self, existing_config=None):
        self._existing = existing_config
        self.added: list = []
        self.updated: list = []

    async def stream_info(self, name):  # noqa: ANN001
        if self._existing is None:
            raise RuntimeError("stream not found")
        return SimpleNamespace(config=self._existing)

    async def add_stream(self, cfg):  # noqa: ANN001
        self.added.append(cfg)

    async def update_stream(self, cfg):  # noqa: ANN001
        self.updated.append(cfg)


def _existing(*, subjects, max_age, max_bytes, discard):
    return SimpleNamespace(
        subjects=subjects, max_age=max_age, max_bytes=max_bytes, discard=discard
    )


@pytestmark_nats
def test_new_stream_gets_byte_cap_and_discard_old() -> None:
    """Yeni stream DISK TAVANI ve discard=OLD ile olusturulmali.

    max_age tek basina bayt siniri VERMEZ (disk = hiz x zaman). discard=OLD
    olmazsa tavana carpinca publish REDDEDILIR ve telemetri akisi DURUR.
    """
    bus = _bus()
    fake = _FakeJs(existing_config=None)
    bus._js = fake

    asyncio.run(
        bus._ensure_stream(
            name="TELEMETRY_RAW",
            subject="e1.telemetry.raw.>",
            max_age_sec=7 * 86400,
            max_bytes=8 * 1024**3,
        )
    )

    assert len(fake.added) == 1
    cfg = fake.added[0]
    assert cfg.max_bytes == 8 * 1024**3, "stream bayt tavani olmadan olusturuldu"
    assert getattr(cfg.discard, "value", cfg.discard) == "old", (
        "discard=old degil — tavana carpinca publish reddedilir, akis durur"
    )


@pytestmark_nats
def test_existing_stream_without_byte_cap_is_updated() -> None:
    """REGRESYON TESTI — sessiz no-op hatasi.

    Drift kontrolu bir zamanlar YALNIZCA subjects + max_age karsilastiriyordu.
    Sonucu: stream'e `max_bytes` eklendiginde mevcut kurulumlarda hicbir sey
    degismiyordu (subject/max_age ayni oldugu icin "drift yok" deyip erken
    donuyordu). Yani "disk tavani ekledik" denip release edilir, sahadaki
    diskler dolmaya devam ederdi — fix'in kendisinden tehlikeli bir durum.

    Bu test tam olarak o senaryoyu kurar: her sey ayni, SADECE max_bytes 0.
    update_stream CAGRILMALIDIR.
    """
    bus = _bus()
    fake = _FakeJs(
        existing_config=_existing(
            subjects=["e1.telemetry.raw.>"],
            max_age=7 * 86400,
            max_bytes=0,      # <- eski saha durumu: sinirsiz
            discard="old",
        )
    )
    bus._js = fake

    asyncio.run(
        bus._ensure_stream(
            name="TELEMETRY_RAW",
            subject="e1.telemetry.raw.>",
            max_age_sec=7 * 86400,
            max_bytes=8 * 1024**3,
        )
    )

    assert len(fake.updated) == 1, (
        "max_bytes farkli oldugu halde update_stream cagrilmadi — "
        "disk tavani mevcut kurulumlara UYGULANMAZ"
    )
    assert fake.updated[0].max_bytes == 8 * 1024**3


@pytestmark_nats
def test_existing_stream_with_discard_new_is_updated() -> None:
    """discard=new (publish reddi) tespit edilip old'a cevrilmeli."""
    bus = _bus()
    fake = _FakeJs(
        existing_config=_existing(
            subjects=["e1.telemetry.raw.>"],
            max_age=7 * 86400,
            max_bytes=8 * 1024**3,
            discard="new",  # <- akisi durduran politika
        )
    )
    bus._js = fake

    asyncio.run(
        bus._ensure_stream(
            name="TELEMETRY_RAW",
            subject="e1.telemetry.raw.>",
            max_age_sec=7 * 86400,
            max_bytes=8 * 1024**3,
        )
    )

    assert len(fake.updated) == 1, "discard=new tespit edilmedi"


@pytestmark_nats
def test_matching_stream_is_not_touched() -> None:
    """Her sey ayniysa gereksiz update_stream ATILMAMALI."""
    bus = _bus()
    fake = _FakeJs(
        existing_config=_existing(
            subjects=["e1.telemetry.raw.>"],
            max_age=7 * 86400,
            max_bytes=8 * 1024**3,
            discard="old",
        )
    )
    bus._js = fake

    asyncio.run(
        bus._ensure_stream(
            name="TELEMETRY_RAW",
            subject="e1.telemetry.raw.>",
            max_age_sec=7 * 86400,
            max_bytes=8 * 1024**3,
        )
    )

    assert fake.updated == [] and fake.added == []


def test_publish_raises_when_bus_not_ready() -> None:
    """Outbox row basarisiz NATS publish'te published=True olmasin."""
    bus = JetStreamBus(
        url="nats://127.0.0.1:4222",
        stream_raw="TELEMETRY_RAW",
        stream_normalized="TELEMETRY_NORMALIZED",
        stream_dlq="TELEMETRY_DLQ",
        subject_raw_pattern="e1.telemetry.raw.>",
        subject_normalized_pattern="e1.telemetry.normalized.>",
        subject_dlq_pattern="e1.dlq.>",
        max_age_days_raw=7,
        max_age_days_normalized=7,
        max_age_days_dlq=30,
        connect_timeout_sec=1,
    )

    with pytest.raises(RuntimeError, match="not ready"):
        bus.publish_event(
            "telemetry.raw_received",
            {"source_gateway": "GW", "message_id": "m1"},
            message_id="m1",
        )
