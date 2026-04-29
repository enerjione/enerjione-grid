from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
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

router = APIRouter(prefix="/alarms", tags=["alarms"])


@router.get("/events", response_model=list[AlarmEventRead])
def list_alarm_events(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return list_alarm_events_service(db)


@router.patch("/events/{alarm_id}/assign", response_model=AlarmEventRead)
def assign_alarm(
    alarm_id: int,
    payload: AlarmAssignRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return assign_alarm_service(db, alarm_id, payload.assigned_to, _.username)


@router.get("/events/{alarm_id}/comments", response_model=list[AlarmCommentRead])
def list_alarm_comments(alarm_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return list_alarm_comments_service(db, alarm_id)


@router.post("/events/{alarm_id}/comments", response_model=AlarmCommentRead, status_code=status.HTTP_201_CREATED)
def create_alarm_comment(
    alarm_id: int,
    payload: AlarmCommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return create_alarm_comment_service(db, alarm_id, payload.comment, current_user)


@router.patch("/events/{alarm_id}/ack", response_model=AlarmEventRead)
def acknowledge_alarm(alarm_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return acknowledge_alarm_service(db, alarm_id, _.username)


@router.patch("/events/{alarm_id}/reset", response_model=AlarmEventRead)
def reset_alarm(alarm_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return reset_alarm_service(db, alarm_id, _.username)


@router.post("/events/ack-all", response_model=list[AlarmEventRead])
def acknowledge_all_alarms(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return acknowledge_all_alarms_service(db, _.username)


@router.post("/events/reset-all", response_model=list[AlarmEventRead])
def reset_all_alarms(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return reset_all_alarms_service(db, _.username)


@router.delete("/events/{alarm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alarm(alarm_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Resetlenmis alarmi siler. Acik alarm icin 409 doner."""
    delete_alarm_service(db, alarm_id, _.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
