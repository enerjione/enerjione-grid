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
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import User
from app.models.user_fcm_token import UserFcmToken
from app.services.fcm import send_push_to_tokens

logger = logging.getLogger(__name__)


def _send_fcm_for_user(
    db: Session,
    username: str | None,
    title: str,
    body: str | None,
    metadata: dict[str, Any] | None,
) -> None:
    """Verilen kullanicinin tum FCM token'larina push gonder. Hata varsa loglar
    ama caller'i bozmaz."""
    try:
        if not username:
            return
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            return
        tokens = list(db.scalars(
            select(UserFcmToken.token).where(UserFcmToken.user_id == user.id)
        ))
        if not tokens:
            return
        data = {}
        if metadata:
            # data payload yalniz string degerler aliyor; ozelleri kaydet
            for k, v in metadata.items():
                if v is not None:
                    data[k] = str(v)
        _, invalid = send_push_to_tokens(
            tokens, title=title, body=body or "", data=data
        )
        if invalid:
            for tok in invalid:
                db.execute(
                    UserFcmToken.__table__.delete().where(UserFcmToken.token == tok)
                )
    except Exception:  # noqa: BLE001
        logger.exception("FCM push gonderiminde beklenmeyen hata")


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

    NOT: Aliciya FCM push da gonderir (varsa cihaz token'i). Commit'ten once
    cagrilir; commit fail olsa bile push gider — bu daha hizli (gerçek-zamanli)
    deneyim icin kabul edilebilir.
    """
    # BASLIK KOLON GENISLIGINE KIRPILIR — savunma derinligi.
    #
    # `Notification.title` String(200) ve Postgres bunu ZORLAR. Uzun bir
    # baslik INSERT'i `StringDataRightTruncation` ile dusurur; hata COMMIT
    # aninda olustugu icin cagiranin o transaction'da yaptigi HER SEY
    # (bildirim disi kayitlar dahil) geri sarilir. Yani tek bir uzun metin,
    # ilgisiz durum guncellemelerini de goturur.
    #
    # Kirpma cagiran basina tekrarlanmak yerine burada bir kez yapiliyor:
    # her yeni cagiran icin ayni tuzagi kapatir. Govde (`body`) String(2000)
    # oldugu icin tam metin orada korunur.
    if title and len(title) > 200:
        title = title[:199] + "…"
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
    # Push goder (alici varsa) — broadcast (recipient_username=None) icin atla
    if recipient_username:
        _send_fcm_for_user(db, recipient_username, title, body, metadata)
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
