"""Bildirim merkezi yardimcilari.

`create_notification` cagrilarda transaction'a notification ekler — caller
kendi commit'ini yapar (atomicity icin: alarm assign tek transaction'da
hem alarm tablosuna hem notif tablosuna yazar). `mark_*` ve `list_*`
fonksiyonlari API endpoint'lerinden cagirilir.

Mantik:
  * `create_notification(...)` — tek bir kullaniciya bildirim ekler. caller
    commit eder.
  * `notify_users(usernames, ...)` — coklu kullaniciya ayni bildirimi
    cogaltir. Tipik kullanim: bir alarmi tum mühendislere bildirmek.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.notification import Notification


def create_notification(
    db: Session,
    *,
    recipient_username: str | None,
    title: str,
    body: str | None = None,
    category: str = "info",
    severity: str = "info",
    actor_username: str | None = None,
    link: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Notification:
    """Tek bir bildirim ekler. caller transaction commit'ini yapar.

    `recipient_username=None` ise broadcast olarak yazilir (frontend kullanici
    fark etmeksizin gosterir).
    """
    row = Notification(
        recipient_username=recipient_username,
        title=title,
        body=body,
        category=category,
        severity=severity,
        actor_username=actor_username,
        link=link,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    return row


def notify_users(
    db: Session,
    recipients: Iterable[str],
    *,
    title: str,
    body: str | None = None,
    category: str = "info",
    severity: str = "info",
    actor_username: str | None = None,
    link: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Cok kullaniciya bildirim cogalt. Aktor varsa onu listeden cikarir
    (kullanici kendi olusturdugu olay icin bildirim almaz)."""
    count = 0
    seen: set[str] = set()
    for username in recipients:
        if not username:
            continue
        username = username.strip()
        if not username or username in seen:
            continue
        if actor_username and username == actor_username:
            continue
        seen.add(username)
        create_notification(
            db,
            recipient_username=username,
            title=title,
            body=body,
            category=category,
            severity=severity,
            actor_username=actor_username,
            link=link,
            metadata=metadata,
        )
        count += 1
    return count


def list_notifications(
    db: Session,
    *,
    username: str,
    only_unread: bool = False,
    limit: int = 100,
) -> list[Notification]:
    """Bir kullanicinin bildirimlerini doner.

    Bir kullanicinin bildirimleri = recipient_username == username (spesifik)
    + recipient_username IS NULL (broadcast).
    """
    stmt = (
        select(Notification)
        .where(
            (Notification.recipient_username == username)
            | (Notification.recipient_username.is_(None))
        )
        .order_by(Notification.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    if only_unread:
        stmt = stmt.where(Notification.is_read.is_(False))
    return list(db.scalars(stmt).all())


def count_unread(db: Session, username: str) -> int:
    stmt = (
        select(func.count(Notification.id))
        .where(
            (Notification.recipient_username == username)
            | (Notification.recipient_username.is_(None))
        )
        .where(Notification.is_read.is_(False))
    )
    return int(db.scalar(stmt) or 0)


def mark_as_read(db: Session, *, username: str, notification_id: int) -> bool:
    """Tek bildirimi okundu isaretle. Sadece kullanici kendi bildirimini
    isaretleyebilir; baskasinin spesifik bildirimine erisemez."""
    row = db.get(Notification, notification_id)
    if row is None:
        return False
    if row.recipient_username is not None and row.recipient_username != username:
        return False
    if not row.is_read:
        row.is_read = True
        row.read_at = datetime.now(timezone.utc)
    return True


def mark_all_as_read(db: Session, username: str) -> int:
    """Kullanicinin tum okunmamis bildirimlerini okundu isaretler."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(Notification)
        .where(
            (Notification.recipient_username == username)
            | (Notification.recipient_username.is_(None))
        )
        .where(Notification.is_read.is_(False))
        .values(is_read=True, read_at=now)
    )
    result = db.execute(stmt)
    return int(result.rowcount or 0)
