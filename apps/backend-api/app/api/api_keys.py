"""API Key (Personal Access Token) yonetimi.

Kullanici kendi token'larini olusturur, listeler ve revoke eder. Admin
(INSTALLER) baska kullanicilarin token'larini da yonetebilir.

Endpoint'ler:
  POST   /api-keys              — Yeni token uret (plain bir kez doner).
  GET    /api-keys              — Kullanicinin kendi token'lari.
  GET    /api-keys/all          — INSTALLER: tum token'lar (audit icin).
  DELETE /api-keys/{id}         — Token revoke (yumusak: revoked_at set).
  PATCH  /api-keys/{id}/disable — is_active=False (yumusatma; revoke degil).
  PATCH  /api-keys/{id}/enable  — is_active=True (revoke degilse).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead
from app.services import api_key_service
from app.services.auth_service import verify_password  # noqa: F401  (gelecekte 2FA)
from app.api.deps import get_current_user
from app.services.event_service import record_event

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _row_to_read(row: ApiKey) -> ApiKeyRead:
    return ApiKeyRead.model_validate(row)


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Yeni API anahtarı oluştur",
    description=(
        "Kullanıcı kendi adına yeni bir Personal Access Token (PAT) üretir. "
        "Plain token yanıtta **bir kez** döner; kaybedildiğinde yeniden alınamaz."
    ),
)
def create_api_key(
    payload: ApiKeyCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyCreatedResponse:
    try:
        scopes_csv = api_key_service.validate_scopes(payload.scopes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    if payload.expires_at is not None:
        now = datetime.now(timezone.utc)
        # Kullanici naive datetime gondermis olabilir — UTC kabul et.
        exp = payload.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="expires_at gelecekte olmalı",
            )
    else:
        exp = None

    plain, token_hash, prefix_display = api_key_service.generate_token()

    row = ApiKey(
        user_id=user.id,
        name=payload.name,
        token_hash=token_hash,
        token_prefix=prefix_display,
        scopes=scopes_csv,
        created_at=datetime.now(timezone.utc),
        expires_at=exp,
        allowed_ips=",".join(payload.allowed_ips) if payload.allowed_ips else None,
        is_active=True,
    )
    db.add(row)
    db.flush()  # row.id icin
    record_event(
        db, category="security", event_type="api_key_created",
        severity="info",
        actor_username=user.username,
        message=f"API anahtarı oluşturuldu: {payload.name}",
        metadata={
            "api_key_id": row.id,
            "scopes": scopes_csv,
            "expires_at": exp.isoformat() if exp else None,
        },
    )
    db.commit()
    db.refresh(row)

    # Cevap: ApiKeyRead alanlari + plain token
    base = _row_to_read(row).model_dump()
    return ApiKeyCreatedResponse(**base, token=plain)


@router.get(
    "",
    response_model=list[ApiKeyRead],
    summary="Kendi API anahtarlarını listele",
)
def list_my_api_keys(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApiKeyRead]:
    rows = list(db.scalars(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    ).all())
    return [_row_to_read(r) for r in rows]


@router.get(
    "/all",
    response_model=list[ApiKeyRead],
    summary="Tüm API anahtarlarını listele (INSTALLER)",
    description="Sistem yöneticileri tüm kullanıcıların token'larını görür (audit).",
)
def list_all_api_keys(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApiKeyRead]:
    if user.role != UserRole.INSTALLER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="INSTALLER yetkisi gerekli")
    rows = list(db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all())
    return [_row_to_read(r) for r in rows]


def _load_with_owner_check(key_id: int, user: User, db: Session) -> ApiKey:
    row = db.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API anahtarı bulunamadı")
    if row.user_id != user.id and user.role != UserRole.INSTALLER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu anahtara erişim yok")
    return row


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="API anahtarını revoke et",
    description="Anahtar revoke edildiğinde geri açılamaz. Geçici durdurmak için /disable kullanın.",
)
def revoke_api_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _load_with_owner_check(key_id, user, db)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        row.is_active = False
        record_event(
            db, category="security", event_type="api_key_revoked",
            severity="warning",
            actor_username=user.username,
            message=f"API anahtarı revoke edildi: {row.name}",
            metadata={"api_key_id": row.id, "owner_user_id": row.user_id},
        )
        db.commit()
    return None


@router.patch(
    "/{key_id}/disable",
    response_model=ApiKeyRead,
    summary="API anahtarını geçici olarak devre dışı bırak",
)
def disable_api_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyRead:
    row = _load_with_owner_check(key_id, user, db)
    if row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Revoke edilmiş anahtar tekrar açılamaz",
        )
    if row.is_active:
        row.is_active = False
        record_event(
            db, category="security", event_type="api_key_disabled",
            severity="info", actor_username=user.username,
            message=f"API anahtarı devre dışı: {row.name}",
            metadata={"api_key_id": row.id},
        )
        db.commit()
    db.refresh(row)
    return _row_to_read(row)


@router.patch(
    "/{key_id}/enable",
    response_model=ApiKeyRead,
    summary="API anahtarını yeniden etkinleştir",
)
def enable_api_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyRead:
    row = _load_with_owner_check(key_id, user, db)
    if row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Revoke edilmiş anahtar tekrar açılamaz",
        )
    if not row.is_active:
        row.is_active = True
        record_event(
            db, category="security", event_type="api_key_enabled",
            severity="info", actor_username=user.username,
            message=f"API anahtarı yeniden etkinleştirildi: {row.name}",
            metadata={"api_key_id": row.id},
        )
        db.commit()
    db.refresh(row)
    return _row_to_read(row)
