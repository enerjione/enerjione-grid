from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import settings
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.auth import InviteUserResponse
from app.schemas.user import ResetPasswordRequest, UserCreate, UserInvite, UserRead, UserUpdate
from app.services.auth_service import get_password_hash
from app.services.event_service import record_event
from app.services.invitation_service import (
    build_setup_url,
    generate_invitation_token,
    send_invitation_email,
)

router = APIRouter(prefix="/users", tags=["users"])

_INSTALLER_OR_ENGINEER = [UserRole.INSTALLER, UserRole.ENGINEER]
# User mgmt sayfasi: installer/engineer/ops_manager erisebilir.
# ops_manager YALNIZCA operator rolundeki kullanicilari listeleyebilir,
# olusturabilir, silebilir; diger rollere mudahale edemez (assert_ops_manager_*).
_USER_MGMT_ROLES = [UserRole.INSTALLER, UserRole.ENGINEER, UserRole.OPS_MANAGER]


def _to_user_read(user: User) -> UserRead:
    """UserRead + computed `pending_invitation` flag."""
    return UserRead(
        id=user.id,
        username=user.username,
        email=user.email,
        phone_number=user.phone_number,
        full_name=user.full_name,
        role=user.role,
        language=user.language,
        pending_invitation=user.hashed_password is None,
    )


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
    if target is not None and target.role == UserRole.OPS_MANAGER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operasyon Yöneticisi hesaplarını yalnızca kurulumcu yönetebilir.",
        )
    if new_role == UserRole.OPS_MANAGER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operasyon Yöneticisi rolü yalnızca kurulumcu tarafından atanabilir.",
        )


def _assert_ops_manager_only_operator(
    current_user: User, *, target: User | None, new_role: UserRole | None
) -> None:
    """Operasyon Yoneticisi: SADECE operator rolundeki kullanicilara mudahale.

    - operator-DISI bir target'a (engineer/installer/ops_manager) dokunamaz.
    - operator-DISI bir rolu atayamaz (olustururken veya update'te).
    - Kendine de dokunabilir (kendi profil guncellemesi /me-style icin
      ortogonal — burada user_id ile gelen istek bloklamak yeterli).
    """
    if current_user.role != UserRole.OPS_MANAGER:
        return
    if target is not None and target.role != UserRole.OPERATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operasyon Yöneticisi yalnızca operator hesaplarına müdahale edebilir.",
        )
    if new_role is not None and new_role != UserRole.OPERATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operasyon Yöneticisi yalnızca operator rolü atayabilir.",
        )


@router.get("", response_model=list[UserRead])
def list_users(
    current_user: User = Depends(require_roles(_USER_MGMT_ROLES)),
    db: Session = Depends(get_db),
):
    stmt = select(User).order_by(User.username.asc())
    if current_user.role == UserRole.ENGINEER:
        stmt = stmt.where(User.role.in_((UserRole.OPERATOR, UserRole.ENGINEER)))
    elif current_user.role == UserRole.OPS_MANAGER:
        # Operasyon Yoneticisi yalnizca operator kullanicilari gorebilir.
        stmt = stmt.where(User.role == UserRole.OPERATOR)
    return [_to_user_read(u) for u in db.scalars(stmt).all()]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    current_user: User = Depends(require_roles(_USER_MGMT_ROLES)),
    db: Session = Depends(get_db),
):
    """LEGACY: Admin sifre belirleyerek user yaratir. Yeni akiş /users/invite —
    sifre belirlemeden token gonderir. Bu endpoint geriye uyumluluk icin
    korunur ama yeni kullanim icin /users/invite tercih edilmeli."""
    _assert_engineer_may_use_installer_role(current_user, target=None, new_role=payload.role)
    _assert_ops_manager_only_operator(current_user, target=None, new_role=payload.role)
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
        # Admin sifre belirledigi icin kullaniciyi degistirmeye zorla.
        must_change_password=True,
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


@router.post("/invite", response_model=InviteUserResponse, status_code=status.HTTP_201_CREATED)
def invite_user(
    payload: UserInvite,
    current_user: User = Depends(require_roles(_USER_MGMT_ROLES)),
    db: Session = Depends(get_db),
):
    """Yeni user davet et — sifre admin tarafindan belirlenmez.

    Akis:
      1) User olustur (hashed_password=NULL — login yapamaz)
      2) Token uret + SHA-256 hash'i DB'ye yaz
      3) Setup URL: `/setup-password?token=<raw>` (FRONTEND_BASE_URL ile prefix)
      4) send_email=true ve SMTP aktif ise mail yolla; aksi halde admin
         panel'de gosterilen URL'i kullaniciya elle iletir.

    Token 7 gun gecerli; kullanici setup-password sayfasinda kendi sifresini
    belirler. Tek kullanim — sifre belirlendikten sonra token gecersizlesir.
    """
    _assert_engineer_may_use_installer_role(current_user, target=None, new_role=payload.role)
    _assert_ops_manager_only_operator(current_user, target=None, new_role=payload.role)
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    user = User(
        username=payload.username,
        email=payload.email,
        phone_number=payload.phone_number,
        full_name=payload.full_name,
        hashed_password=None,  # davet token'i ile setup-password'de doldurulur
        role=payload.role,
        must_change_password=False,  # setup zaten yeni sifre yaratiyor
    )
    db.add(user)
    db.flush()  # user.id icin

    raw_token = generate_invitation_token(user)
    setup_url = build_setup_url(raw_token, frontend_base_url=settings.frontend_base_url)
    email_sent = False
    if payload.send_email:
        email_sent = send_invitation_email(user, setup_url)

    record_event(
        db,
        category="user",
        event_type="user_invited",
        severity="info",
        actor_username=current_user.username,
        message=(
            f"{current_user.username} invited new user: {user.username} "
            f"(email_sent={email_sent})"
        ),
        metadata={
            "target_username": user.username,
            "target_role": user.role.value,
            "email_sent": email_sent,
        },
        i18n_key="user_invited",
        i18n_params={"actor": current_user.username, "user": user.username},
    )
    db.commit()
    db.refresh(user)
    return InviteUserResponse(
        user_id=user.id,
        username=user.username,
        setup_url=setup_url,
        expires_at=user.password_reset_token_expires_at.isoformat(),
        email_sent=email_sent,
    )


@router.post("/{user_id}/resend-invite", response_model=InviteUserResponse)
def resend_invite(
    user_id: int,
    current_user: User = Depends(require_roles(_USER_MGMT_ROLES)),
    db: Session = Depends(get_db),
):
    """Davet token'ini yenile ve mail/link uret. Eski token gecersizlesir.

    Sadece henuz sifre belirlememis user (hashed_password=NULL) icin
    kullanilabilir; aksi halde 400 doner — sifresi olan kullanici icin
    "password reset" admin endpoint'i (/{user_id}/reset-password) kullanilmali.
    """
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_engineer_may_use_installer_role(current_user, target=target, new_role=None)
    _assert_ops_manager_only_operator(current_user, target=target, new_role=None)
    if target.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User'in sifresi var. Sifre reset icin reset-password endpoint'ini kullanin.",
        )
    raw_token = generate_invitation_token(target)
    setup_url = build_setup_url(raw_token, frontend_base_url=settings.frontend_base_url)
    email_sent = send_invitation_email(target, setup_url)
    record_event(
        db,
        category="user",
        event_type="user_invite_resent",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} resent invite to: {target.username}",
        metadata={"target_username": target.username, "email_sent": email_sent},
        i18n_key="user_invite_resent",
        i18n_params={"actor": current_user.username, "user": target.username},
    )
    db.commit()
    db.refresh(target)
    return InviteUserResponse(
        user_id=target.id,
        username=target.username,
        setup_url=setup_url,
        expires_at=target.password_reset_token_expires_at.isoformat(),
        email_sent=email_sent,
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    current_user: User = Depends(require_roles(_USER_MGMT_ROLES)),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_engineer_may_use_installer_role(current_user, target=target, new_role=None)
    _assert_ops_manager_only_operator(current_user, target=target, new_role=None)
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
    current_user: User = Depends(require_roles(_USER_MGMT_ROLES)),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_engineer_may_use_installer_role(current_user, target=target, new_role=payload.role)
    _assert_ops_manager_only_operator(current_user, target=target, new_role=payload.role)

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
    current_user: User = Depends(require_roles(_USER_MGMT_ROLES)),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_engineer_may_use_installer_role(current_user, target=target, new_role=None)
    _assert_ops_manager_only_operator(current_user, target=target, new_role=None)

    target.hashed_password = get_password_hash(payload.new_password)
    # Yoneticinin belirledigi parola GECICIDIR: kullanici ilk girisinde
    # degistirmek zorunda. Bayrak set edilmedigi surece hesap, yoneticinin
    # bildigi (ve muhtemelen telefonla ilettigi) bir parolayla sinirsiz
    # kullanilabiliyordu.
    target.must_change_password = True

    # HESAP KILIDINI DE AC — baska bir acma yolu YOK.
    #
    # Kilit `locked_until` ile konuluyor ve parola dogrulamasindan ONCE
    # kontrol ediliyor; `failed_login_count` da kilit suresi dolunca
    # sifirlanmiyor. Bu uc `locked_until`'a hic dokunmadigi icin kilitlenen
    # bir hesabin API uzerinden acilma yolu yoktu. INSTALLER hesabina yalnizca
    # INSTALLER mudahale edebildiginden, tek installer hesabi kilitlenince
    # gateway ekleme, ag ayari, yedek/geri yukleme ve uzaktan bakim izni
    # hep birlikte kilitleniyor ve cozum SAHA ZIYARETI oluyordu.
    target.locked_until = None
    target.failed_login_count = 0

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
