"""Proje ayarlari endpoint'leri.

GET / — auth gerek**siz**. Login ekrani ve header henuz oturum yokken
   logoyu cekebilsin diye public. Sadece display alanlari donulur.
PUT / — sadece INSTALLER. Logo data URL'leri ve isimler.

Singleton tablo: id=1 satiri yoksa GET'te bos donulur, PUT'ta upsert edilir.

Logo/favicon guvenligi:
  Frontend `data:image/...;base64,...` formatinda data URL gonderir. Kabul
  edilen MIME'lar magic-byte ile dogrulanir (PNG, JPEG, WebP, ICO). SVG
  KABUL EDILMEZ — installer kotu niyetli SVG yukleyip `<script>` enjekte
  ederek XSS yapabilir. data URL formati disinda hicbir sey kabul edilmez
  (`http://attacker.com/x.png` gibi external URL'ler reddedilir).
"""

from __future__ import annotations

import base64
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.project_settings import ProjectSettings
from app.models.user import User
from app.schemas.project_settings import ProjectSettingsRead, ProjectSettingsUpdate
from app.services.event_service import record_event

router = APIRouter(prefix="/project-settings", tags=["project-settings"])


# Logo / favicon / login image alanlari icin guvenli MIME beyaz liste +
# magic-byte imzalari. SVG KABUL EDILMEZ (XSS riski; `<script>` enjeksiyon).
_ALLOWED_IMAGE_SIGNATURES: list[tuple[str, bytes]] = [
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/webp", b"RIFF"),  # ek olarak offset 8'de "WEBP" kontrolu yapilir
    ("image/x-icon", b"\x00\x00\x01\x00"),  # ICO
    ("image/vnd.microsoft.icon", b"\x00\x00\x01\x00"),
    ("image/gif", b"GIF87a"),
    # GIF89a icin ayri imza altta
]
_GIF89A = b"GIF89a"
_WEBP_TAG = b"WEBP"

# Max byte boyutu: ~2MB (data URL base64 buyumesi dahil tahmini 2.7MB).
# Tipik logo PNG'si 50-200KB; 2MB cok cok yeterli, DoS bound olarak da OK.
_MAX_IMAGE_BYTES = 2 * 1024 * 1024

_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[A-Za-z0-9\-./+]+);base64,(?P<b64>[A-Za-z0-9+/=]+)$"
)


def _validate_image_data_url(field_name: str, value: str | None) -> None:
    """Logo/favicon vb. data URL'sini guvenli MIME + magic-byte ile dogrula.

    None veya bos → OK (operator alan temizleyebilir).
    `data:image/svg+xml;...` → REDDET (XSS riski).
    External URL veya farkli scheme → REDDET.
    Beyaz liste MIME + dosya magic-byte uyumsuzlugu → REDDET.
    Boyut > 2MB → REDDET.
    """
    if value is None or value == "":
        return
    match = _DATA_URL_RE.match(value.strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name}: gecersiz format — sadece 'data:image/<png|jpeg|webp|ico|gif>;base64,...' kabul edilir",
        )
    mime = match.group("mime").lower().strip()
    if mime.startswith("image/svg"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name}: SVG formati guvenlik nedeniyle kabul edilmez (XSS riski). PNG/JPEG/WebP kullanin.",
        )
    if mime not in {s for s, _ in _ALLOWED_IMAGE_SIGNATURES} and mime != "image/gif":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name}: '{mime}' MIME desteklenmiyor (PNG/JPEG/WebP/ICO/GIF)",
        )
    try:
        raw = base64.b64decode(match.group("b64"), validate=True)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name}: base64 decode hatasi",
        ) from None
    if len(raw) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name}: dosya cok buyuk (max {_MAX_IMAGE_BYTES // 1024} KB)",
        )
    # Magic-byte check: MIME ile dosya icerigi uyusmuyorsa attack vektoru.
    matched_sig = False
    for sig_mime, sig_bytes in _ALLOWED_IMAGE_SIGNATURES:
        if mime == sig_mime and raw.startswith(sig_bytes):
            # WebP icin ek kontrol: offset 8-12 "WEBP" olmali
            if sig_mime == "image/webp" and raw[8:12] != _WEBP_TAG:
                continue
            matched_sig = True
            break
    # GIF89a alternatifi
    if not matched_sig and mime == "image/gif" and raw.startswith(_GIF89A):
        matched_sig = True
    if not matched_sig:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{field_name}: MIME ({mime}) dosya icerigi ile uyusmuyor. "
                "Magic-byte dogrulamasi basarisiz — gecerli bir resim dosyasi degil."
            ),
        )


def _get_or_empty(db: Session) -> ProjectSettings:
    row = db.get(ProjectSettings, 1)
    if row is None:
        row = ProjectSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("", response_model=ProjectSettingsRead)
def get_project_settings(db: Session = Depends(get_db)):
    """Auth gerektirmez — login ekrani ve header public okur."""
    row = db.get(ProjectSettings, 1)
    if row is None:
        return ProjectSettingsRead()
    return row


@router.put("", response_model=ProjectSettingsRead, status_code=status.HTTP_200_OK)
def update_project_settings(
    payload: ProjectSettingsUpdate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = _get_or_empty(db)
    updates = payload.model_dump(exclude_unset=True)
    # Logo / favicon / login image data URL'leri buyuk olabilir; metadata'ya
    # koymak yerine kisa flag tut.
    _BLOB_FIELDS = {"customer_logo", "customer_logo_light", "favicon", "login_image"}
    # Image alanlarini MIME + magic-byte ile dogrula (SVG XSS koruma).
    for field in _BLOB_FIELDS:
        if field in updates:
            _validate_image_data_url(field, updates[field])
    for key, value in updates.items():
        setattr(row, key, value)
    # Toast ayarlari TUM kurulumu etkiler (susturma herkesin alarm baloncugunu
    # kapatir); denetim kaydinda yalnizca alan adi degil DEGERI de dursun.
    _VALUE_FIELDS = {"toast_position", "toast_muted"}
    summary_fields = []
    for k in updates.keys():
        if k in _BLOB_FIELDS:
            summary_fields.append(f"{k}({'set' if updates[k] else 'cleared'})")
        elif k in _VALUE_FIELDS:
            summary_fields.append(f"{k}={updates[k]}")
        else:
            summary_fields.append(k)
    audit_metadata: dict = {"fields": list(updates.keys())}
    for k in _VALUE_FIELDS & updates.keys():
        audit_metadata[k] = updates[k]
    record_event(
        db,
        category="project-settings",
        event_type="project_settings_updated",
        severity="info",
        actor_username=current_user.username,
        message=f"Project settings updated: {', '.join(summary_fields)}",
        metadata=audit_metadata,
        i18n_key="project_settings_updated",
    )
    db.commit()
    db.refresh(row)
    return row
