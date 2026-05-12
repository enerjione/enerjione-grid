from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.user import ResetPasswordRequest, UserCreate, UserRead, UserUpdate
from app.services.auth_service import get_password_hash
from app.services.event_service import record_event

router = APIRouter(prefix="/users", tags=["users"])

_INSTALLER_OR_ENGINEER = [UserRole.INSTALLER, UserRole.ENGINEER]


def _assert_engineer_may_use_installer_role(
    current_user: User, *, target: User | None, new_role: UserRole | None
) -> None:
    """Mühendis: kurulumcu hesabına müdahale edemez, kurulumcu rolü atayamaz."""
    if current_user.role != UserRole.ENGINEER:
        return
    if target is not None and target.role == UserRole.INSTALLER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Kurulumcu hesaplarını yalnızca kurulumcu yönetebilir.",
        )
    if new_role == UserRole.INSTALLER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Kurulumcu rolü yalnızca kurulumcu tarafından atanabilir.",
        )


@router.get("", response_model=list[UserRead])
def list_users(
    current_user: User = Depends(require_roles(_INSTALLER_OR_ENGINEER)),
    db: Session = Depends(get_db),
):
    stmt = select(User).order_by(User.username.asc())
    if current_user.role == UserRole.ENGINEER:
        stmt = stmt.where(User.role.in_((UserRole.OPERATOR, UserRole.ENGINEER)))
    return list(db.scalars(stmt).all())


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    current_user: User = Depends(require_roles(_INSTALLER_OR_ENGINEER)),
    db: Session = Depends(get_db),
):
    _assert_engineer_may_use_installer_role(current_user, target=None, new_role=payload.role)
    existing_username = db.scalar(select(User).where(User.username == payload.username))
    if existing_username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    existing_email = db.scalar(select(User).where(User.email == payload.email))
    if existing_email:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    user = User(
        username=payload.username,
        email=payload.email,
        phone_number=payload.phone_number,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
    )
    db.add(user)
    record_event(
        db,
        category="user",
        event_type="user_created",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} created new user: {user.username}",
        metadata={"target_username": user.username, "target_role": user.role.value},
        i18n_key="user_created",
        i18n_params={"actor": current_user.username, "user": user.username},
    )
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    current_user: User = Depends(require_roles(_INSTALLER_OR_ENGINEER)),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_engineer_may_use_installer_role(current_user, target=target, new_role=None)
    if target.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")

    deleted_username = target.username
    db.delete(target)
    record_event(
        db,
        category="user",
        event_type="user_deleted",
        severity="warning",
        actor_username=current_user.username,
        message=f"{current_user.username} deleted user: {deleted_username}",
        metadata={"target_username": deleted_username},
        i18n_key="user_deleted",
        i18n_params={"actor": current_user.username, "user": deleted_username},
    )
    db.commit()
    return None


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(require_roles(_INSTALLER_OR_ENGINEER)),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_engineer_may_use_installer_role(current_user, target=target, new_role=payload.role)

    target.email = payload.email
    target.phone_number = payload.phone_number
    target.full_name = payload.full_name
    target.role = payload.role
    record_event(
        db,
        category="user",
        event_type="user_updated",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} updated user: {target.username}",
        metadata={"target_username": target.username, "target_role": target.role.value},
        i18n_key="user_updated",
        i18n_params={"actor": current_user.username, "user": target.username},
    )
    db.commit()
    db.refresh(target)
    return target


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: int,
    payload: ResetPasswordRequest,
    current_user: User = Depends(require_roles(_INSTALLER_OR_ENGINEER)),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_engineer_may_use_installer_role(current_user, target=target, new_role=None)

    target.hashed_password = get_password_hash(payload.new_password)
    record_event(
        db,
        category="user",
        event_type="password_reset",
        severity="warning",
        actor_username=current_user.username,
        message=f"{current_user.username} reset password for {target.username}",
        metadata={"target_username": target.username},
        i18n_key="password_reset",
        i18n_params={"actor": current_user.username, "user": target.username},
    )
    db.commit()
    return None
