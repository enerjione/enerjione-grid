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
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    _ensure_signal_exists(db, payload.signal_key)
    row = AlarmRule(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{rule_id}", response_model=AlarmRuleRead)
def update_alarm_rule(
    rule_id: int,
    payload: AlarmRuleUpdate,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(AlarmRule).where(AlarmRule.id == rule_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm rule not found")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alarm_rule(
    rule_id: int,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(AlarmRule).where(AlarmRule.id == rule_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm rule not found")
    db.delete(row)
    db.commit()
    return None
