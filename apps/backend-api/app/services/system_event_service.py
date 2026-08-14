"""Sistem olaylari (system_events) listeleme servisi.

Filtreler + sayfalama burada uygulanir; endpoint toplam sayiyi
X-Total-Count header'i ile doner (yanit govdesi geriye uyumlu liste kalir).
"""

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.system_event import SystemEvent


def list_system_events(
    db: Session,
    *,
    category: str | None = None,
    severity: str | None = None,
    actor_username: str | None = None,
    event_type: str | None = None,
    event_type_like: list[str] | None = None,
    device_code: str | None = None,
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    exclude_categories: set[str] | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> tuple[list[SystemEvent], int]:
    """Filtrelenmis olaylar (yeniden eskiye) + filtreye uyan TOPLAM sayi.

    `q` serbest metin aramasi: mesaj, kullanici, cihaz kodu ve olay tipi
    uzerinde ILIKE. Ceviri frontend'te yapildigi icin arama ham (TR) mesaj
    uzerinden gecer — kayitlarin kaynagi da TR mesajlar.

    `event_type_like`: ILIKE desen listesi (OR) — frontend "Durum" grubu
    filtresi icin ("%_deleted", "alarm_triggered" gibi). `event_type` ise
    tek tip TAM eslesme; ikisi ayni anda gelirse ikisi de uygulanir (AND).

    `actor_username` kismi eslesir (ILIKE) — kullanici adinin bir parcasini
    yazmak yeterli.
    """
    filters = []
    # `exclude_categories`: cagiranin ROLU geregi hic gormemesi gereken
    # kategoriler. Denetim kaydinin tamami her kimlik dogrulamis kullaniciya
    # aciktı; operator kendi isi olmayan `security`/`auth`/`user` olaylarini
    # (giris denemeleri, parola sifirlama, API anahtari uretimi, kullanici
    # yonetimi) okuyabiliyordu. Filtre SORGUDA uygulanir — sayfalama ve
    # X-Total-Count da dogru kalsin.
    if exclude_categories:
        filters.append(SystemEvent.category.notin_(sorted(exclude_categories)))
    if category:
        filters.append(SystemEvent.category == category)
    if severity:
        filters.append(SystemEvent.severity == severity)
    if actor_username:
        filters.append(SystemEvent.actor_username.ilike(f"%{actor_username.strip()}%"))
    if event_type:
        filters.append(SystemEvent.event_type == event_type)
    if event_type_like:
        filters.append(
            or_(*[SystemEvent.event_type.ilike(pattern) for pattern in event_type_like])
        )
    if device_code:
        filters.append(SystemEvent.device_code == device_code)
    if q and q.strip():
        like = f"%{q.strip()}%"
        filters.append(
            or_(
                SystemEvent.message.ilike(like),
                SystemEvent.actor_username.ilike(like),
                SystemEvent.device_code.ilike(like),
                SystemEvent.event_type.ilike(like),
            )
        )
    if date_from:
        filters.append(SystemEvent.created_at >= date_from)
    if date_to:
        filters.append(SystemEvent.created_at <= date_to)

    total = db.scalar(select(func.count()).select_from(SystemEvent).where(*filters)) or 0
    stmt = (
        select(SystemEvent)
        .where(*filters)
        .order_by(SystemEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt).all()), int(total)
