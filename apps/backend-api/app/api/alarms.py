from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.models.enums import UserRole
from app.db.session import get_db
from app.models.user import User
from app.schemas.alarm import AlarmAssignRequest, AlarmCommentCreate, AlarmCommentRead, AlarmEventRead
from app.services.alarm_engine_service import (
    acknowledge_alarm as acknowledge_alarm_service,
    acknowledge_all_alarms as acknowledge_all_alarms_service,
    assign_alarm as assign_alarm_service,
    create_alarm_comment as create_alarm_comment_service,
    delete_alarm as delete_alarm_service,
    list_alarm_comments as list_alarm_comments_service,
    list_alarm_events as list_alarm_events_service,
    reset_alarm as reset_alarm_service,
    reset_all_alarms as reset_all_alarms_service,
)
from app.services.scope_service import get_visible_device_ids

router = APIRouter(prefix="/alarms", tags=["alarms"])


def _scope_filter_alarms(db: Session, user: User, rows):
    """Operator icin sadece sorumluluk alanindaki cihazlarin alarmlarini birak."""
    visible = get_visible_device_ids(db, user)
    if visible is None:
        return rows
    return [a for a in rows if a.device_id in visible]


def _ensure_can_access_alarm(db: Session, user: User, alarm) -> None:
    """Alarm tek-kayit endpoint'leri icin yetki kontrolu."""
    visible = get_visible_device_ids(db, user)
    if visible is None:
        return
    if alarm.device_id not in visible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Alarm sorumluluk alaniniz disinda")


@router.get("/events", response_model=list[AlarmEventRead])
def list_alarm_events(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Alarm olaylari — kapsam SQL'de, LIMIT'ten ONCE uygulanir.

    Eskiden `list_alarm_events_service(db)` kapsamsiz cagriliyor, daraltma
    donen 500 satir uzerinde Python'da yapiliyordu. Servis
    `visible_device_ids` parametresini DESTEKLIYORDU; A3 duzeltmesi
    ack-all/reset-all yollarina uygulanmis, LISTE yoluna uygulanmamisti.

    Sonucu: 20 cihazdan sorumlu bir operator, 600 cihazin en yeni 500 kaydi
    icinden yalnizca kendine denk gelenleri goruyordu — yani kendi
    alarmlarinin cogunu hic goremiyordu.
    """
    visible = get_visible_device_ids(db, current_user)
    return list_alarm_events_service(db, visible)


def _load_alarm_or_404(db: Session, alarm_id: int):
    from app.models.alarm import AlarmEvent  # local import - circular onlemek icin

    alarm = db.get(AlarmEvent, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm not found")
    return alarm


@router.patch("/events/{alarm_id}/assign", response_model=AlarmEventRead)
def assign_alarm(
    alarm_id: int,
    payload: AlarmAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_can_access_alarm(db, current_user, _load_alarm_or_404(db, alarm_id))
    return assign_alarm_service(db, alarm_id, payload.assigned_to, current_user.username)


@router.get("/events/{alarm_id}/comments", response_model=list[AlarmCommentRead])
def list_alarm_comments(alarm_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _ensure_can_access_alarm(db, current_user, _load_alarm_or_404(db, alarm_id))
    return list_alarm_comments_service(db, alarm_id)


@router.post("/events/{alarm_id}/comments", response_model=AlarmCommentRead, status_code=status.HTTP_201_CREATED)
def create_alarm_comment(
    alarm_id: int,
    payload: AlarmCommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_can_access_alarm(db, current_user, _load_alarm_or_404(db, alarm_id))
    return create_alarm_comment_service(db, alarm_id, payload.comment, current_user)


@router.patch("/events/{alarm_id}/ack", response_model=AlarmEventRead)
def acknowledge_alarm(alarm_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _ensure_can_access_alarm(db, current_user, _load_alarm_or_404(db, alarm_id))
    return acknowledge_alarm_service(db, alarm_id, current_user.username)


@router.patch("/events/{alarm_id}/reset", response_model=AlarmEventRead)
def reset_alarm(alarm_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _ensure_can_access_alarm(db, current_user, _load_alarm_or_404(db, alarm_id))
    return reset_alarm_service(db, alarm_id, current_user.username)


@router.post("/events/ack-all", response_model=list[AlarmEventRead])
def acknowledge_all_alarms(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Kapsam SERVISE gecirilir — yaniti filtrelemek YETMEZ.
    # Eskiden mutasyon tum alarmlara uygulaniyor, filtre yalnizca donen listeye
    # vuruluyordu: operator kendi alani disindaki alarmlari da onayliyor ve
    # resetlenmis olanlari kalici siliyordu; ekranda hicbir sey gorunmuyordu.
    rows = acknowledge_all_alarms_service(
        db, current_user.username, get_visible_device_ids(db, current_user)
    )
    return _scope_filter_alarms(db, current_user, rows)


@router.post("/events/reset-all", response_model=list[AlarmEventRead])
def reset_all_alarms(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = reset_all_alarms_service(
        db, current_user.username, get_visible_device_ids(db, current_user)
    )
    return _scope_filter_alarms(db, current_user, rows)


@router.delete("/events/{alarm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alarm(
    alarm_id: int,
    db: Session = Depends(get_db),
    # Sadece ENGINEER veya INSTALLER alarm silebilir. OPERATOR silmemeli —
    # incident review icin audit trail gerek. Onceki davranis: herhangi
    # login'li kullanici (OPERATOR dahil) silebilirdi → operator alarm'i
    # accidentally veya deliberately silip kayit kaybi yaratabilirdi.
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
):
    """Resetlenmis alarmi siler. Acik alarm icin 409 doner.

    Yetki: ENGINEER veya INSTALLER. Operator silemez (audit trail koruma).
    """
    _ensure_can_access_alarm(db, current_user, _load_alarm_or_404(db, alarm_id))
    delete_alarm_service(db, alarm_id, current_user.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
