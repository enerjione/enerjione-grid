import json
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.outbox_event import OutboxEvent
from app.services.event_bus import event_bus


def enqueue_outbox_event(db: Session, *, topic: str, payload: dict, dedup_key: str) -> None:
    if not dedup_key:
        raise ValueError("outbox dedup_key zorunludur")
    # Atomik INSERT ... ON CONFLICT DO NOTHING. Onceki SELECT-then-INSERT'te
    # race vardi: ayni message_id iki request'te es zamanli gelince ikisi de
    # "yok" gorup INSERT ediyor -> ikincisi UniqueViolation -> TUM ingest
    # commit'i patliyordu (200 cihaz + gateway retry yukunde surekli). ON
    # CONFLICT ile duplicate DB seviyesinde sessizce yutulur; ayni transaction,
    # commit'i caller yapar (davranis degismedi).
    stmt = (
        pg_insert(OutboxEvent)
        .values(
            topic=topic,
            dedup_key=dedup_key,
            payload_json=json.dumps(payload, ensure_ascii=False),
            published=False,
            created_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
    )
    db.execute(stmt)


def flush_outbox(db: Session, *, limit: int = 100) -> int:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published.is_(False))
        .order_by(OutboxEvent.id.asc())
        .limit(limit)
    )
    rows = list(db.scalars(stmt).all())
    published = 0
    for row in rows:
        payload = json.loads(row.payload_json)
        event_bus.publish_event(row.topic, payload, message_id=row.dedup_key)
        row.published = True
        row.published_at = datetime.now(timezone.utc)
        published += 1
    if published:
        db.commit()
    return published


def purge_published_outbox(
    db: Session, *, before: datetime, limit: int = 10_000
) -> int:
    """Yayinlanmis eski outbox satirlarini limitli batch ile siler.

    published=False satira ASLA dokunmaz (at-least-once korunur). Subquery
    LIMIT ile tek transaction'da milyonlarca row silip tabloyu/DB'yi kilitlemez.
    """
    ids = select(OutboxEvent.id).where(
        OutboxEvent.published.is_(True),
        OutboxEvent.published_at.is_not(None),
        OutboxEvent.published_at < before,
    ).order_by(OutboxEvent.id.asc()).limit(limit)
    result = db.execute(delete(OutboxEvent).where(OutboxEvent.id.in_(ids)))
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)
