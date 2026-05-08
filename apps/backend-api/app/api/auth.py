from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import LanguageUpdateRequest, SelfPasswordChangeRequest, SelfProfileUpdateRequest, UserRead

# Frontend i18n ile uyumlu desteklenen diller. Yeni dil eklerken hem
# bu listeye hem de frontend resources/<code>.json dosyasina ekle.
SUPPORTED_LANGUAGES = {"tr", "en"}
from app.api.deps import get_current_user
from app.services.auth_service import create_access_token, get_password_hash, verify_password
from app.services.event_service import record_event

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    stmt = select(User).where(User.username == payload.username)
    user = db.scalar(stmt)
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    access_token = create_access_token(user.username)
    record_event(
        db,
        category="auth",
        event_type="user_login",
        severity="info",
        actor_username=user.username,
        message=f"{user.username} sisteme giriş yaptı",
    )
    db.commit()
    return TokenResponse(access_token=access_token, role=user.role, username=user.username)


@router.get("/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserRead)
def update_me(
    payload: SelfProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.full_name = payload.full_name
    current_user.email = payload.email
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/me/language", response_model=UserRead)
def update_my_language(
    payload: LanguageUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    code = (payload.language or "").strip().lower()
    if code not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Desteklenmeyen dil: {payload.language}",
        )
    current_user.language = code
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(
    payload: SelfPasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong")

    current_user.hashed_password = get_password_hash(payload.new_password)
    record_event(
        db,
        category="auth",
        event_type="password_changed",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} şifresini değiştirdi",
    )
    db.commit()
    return None


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    record_event(
        db,
        category="auth",
        event_type="user_logout",
        severity="info",
        actor_username=current_user.username,
        message=f"{current_user.username} sistemden çıkış yaptı",
    )
    db.commit()
    return None
