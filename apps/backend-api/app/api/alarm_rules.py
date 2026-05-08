from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.alarm_rule import AlarmRule
from app.models.enums import UserRole
from app.models.signal_catalog import SignalCatalog
from app.models.user import User
from app.schemas.alarm_rule import AlarmRuleCreate, AlarmRuleRead, AlarmRuleUpdate
from app.services.event_service import record_event

router = APIRouter(prefix="/alarm-rules", tags=["alarm-rules"])


@router.get("", response_model=list[AlarmRuleRead])
def list_alarm_rules(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(AlarmRule).order_by(AlarmRule.signal_key.asc(), AlarmRule.level.asc())
    return list(db.scalars(stmt).all())


def _ensure_signal_exists(db: Session, signal_key: str) -> SignalCatalog:
    """Sinyalin katalog'da var olduğunu doğrular.

    `supports_alarm` bayrağı eskiden kuralın oluşmasını engelliyordu; artık
    her sinyale (binary/analog/counter) kural eklenebilir. Bayrak yalnızca
    UI'da bilgi amaçlı tutulur."""
    signal = db.scalar(select(SignalCatalog).where(SignalCatalog.key == signal_key))
    if signal is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signal not found in catalog")
    return signal


@router.post("", response_model=AlarmRuleRead, status_code=status.HTTP_201_CREATED)
def create_alarm_rule(
    payload: AlarmRuleCreate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    _ensure_signal_exists(db, payload.signal_key)
    row = AlarmRule(**payload.model_dump())
    db.add(row)
    db.flush()
    record_event(
        db,
        category="alarm-rule",
        event_type="alarm_rule_created",
        severity="info",
        actor_username=current_user.username,
        message=f"Alarm kuralı eklendi: {row.name} ({row.signal_key})",
        metadata={"rule_id": row.id, "signal_key": row.signal_key, "level": row.level},
        i18n_key="alarm_rule_created",
        i18n_params={"name": row.name, "signal": row.signal_key},
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{rule_id}", response_model=AlarmRuleRead)
def update_alarm_rule(
    rule_id: int,
    payload: AlarmRuleUpdate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(AlarmRule).where(AlarmRule.id == rule_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm rule not found")
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(row, key, value)
    record_event(
        db,
        category="alarm-rule",
        event_type="alarm_rule_updated",
        severity="info",
        actor_username=current_user.username,
        message=f"Alarm kuralı güncellendi: {row.name}",
        metadata={"rule_id": row.id, "fields": list(changes.keys())},
        i18n_key="alarm_rule_updated",
        i18n_params={"name": row.name},
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alarm_rule(
    rule_id: int,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(AlarmRule).where(AlarmRule.id == rule_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm rule not found")
    name = row.name
    sig = row.signal_key
    db.delete(row)
    record_event(
        db,
        category="alarm-rule",
        event_type="alarm_rule_deleted",
        severity="warning",
        actor_username=current_user.username,
        message=f"Alarm kuralı silindi: {name} ({sig})",
        metadata={"rule_id": rule_id, "signal_key": sig},
        i18n_key="alarm_rule_deleted",
        i18n_params={"name": name, "signal": sig},
    )
    db.commit()
    return None
