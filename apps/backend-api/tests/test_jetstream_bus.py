from __future__ import annotations

import pytest

from app.services.jetstream_bus import JetStreamBus


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
