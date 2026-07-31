"""Lisans endpoint'leri.

GET  /license/gate     -> arayuz kilidi icin minimal durum (TUM roller)
GET  /license/status   -> cihaz kotasi ve musteri bilgileri
GET  /license/request  -> bu kuruluma bagli .licreq indir
POST /license/import   -> imzali .lic dogrula ve etkinlestir

Yetki: /gate disindakiler engineer + installer.

NOT: Bu router lisans kilidinin (bkz. core/license_gate.py) beyaz
listesindedir — aksi halde lisanssiz sistem kendini asla acamazdi.
"""

import json

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.config import settings
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.license import LicenseGate, LicenseStatus
from app.services import license_service
from app.services.event_service import record_event


router = APIRouter(prefix="/license", tags=["license"])
_LICENSE_ROLES = [UserRole.ENGINEER, UserRole.INSTALLER]


@router.get("/gate", response_model=LicenseGate)
def license_gate(
    _: User = Depends(get_current_user),
    __: Session = Depends(get_db),
):
    """Arayuz kilidi icin minimal durum — TUM rollere acik.

    `/status` ticari bilgi tasidigi icin engineer+installer ile sinirli; ama
    operator/ops_manager da lisanssiz sistemde kilitlenmeli. Burada yalnizca
    "kilitli mi" bilgisi doner, lisans detayi DONMEZ.
    """
    state, reason_code = license_service.get_enforcement_state()
    return LicenseGate(
        locked=state in license_service.ENFORCED_STATES,
        state=state,
        reason_code=reason_code,
    )


@router.get("/status", response_model=LicenseStatus)
def license_status(
    _: User = Depends(require_roles(_LICENSE_ROLES)),
    db: Session = Depends(get_db),
):
    return license_service.get_license_status(db)


@router.get("/request")
def download_license_request(
    current_user: User = Depends(require_roles(_LICENSE_ROLES)),
    db: Session = Depends(get_db),
):
    try:
        request_file = license_service.create_license_request()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "LICENSE_MACHINE_UNAVAILABLE", "message": str(exc)},
        ) from exc
    record_event(
        db,
        category="license",
        event_type="license_request_downloaded",
        severity="info",
        actor_username=current_user.username,
        message="Lisans istek dosyasi indirildi",
        metadata={"request_id": request_file.request_id},
        i18n_key="license_request_downloaded",
    )
    db.commit()
    body = json.dumps(request_file.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    filename = f"enerjione-{request_file.installation_id}.licreq"
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", response_model=LicenseStatus)
async def upload_license(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(_LICENSE_ROLES)),
    db: Session = Depends(get_db),
):
    name = (file.filename or "").strip()
    if not name.lower().endswith(".lic"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LICENSE_EXTENSION_INVALID", "message": "Sadece .lic dosyasi kabul edilir"},
        )
    data = await file.read(settings.license_upload_max_bytes + 1)
    if len(data) > settings.license_upload_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "LICENSE_FILE_TOO_LARGE", "message": "Lisans dosyasi 50 KiB sinirini asiyor"},
        )
    try:
        envelope = license_service.import_license(data)
    except license_service.LicenseCapacityError as exc:
        record_event(
            db,
            category="license",
            event_type="license_import_rejected",
            severity="warning",
            actor_username=current_user.username,
            message=f"Lisans reddedildi: {exc.code}",
            metadata={"reason_code": exc.code},
            i18n_key="license_import_rejected",
            i18n_params={"reason": exc.code},
        )
        db.commit()
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.code == "LICENSE_MACHINE_UNAVAILABLE"
                else status.HTTP_400_BAD_REQUEST
            ),
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except RuntimeError as exc:
        # Sunucu tarafi storage/machine-id problemi — lisansin kendisi degil.
        # 400 (istemci hatasi) yerine 503 don.
        record_event(
            db,
            category="license",
            event_type="license_import_rejected",
            severity="warning",
            actor_username=current_user.username,
            message="Lisans reddedildi: LICENSE_MACHINE_UNAVAILABLE",
            metadata={"reason_code": "LICENSE_MACHINE_UNAVAILABLE"},
            i18n_key="license_import_rejected",
            i18n_params={"reason": "LICENSE_MACHINE_UNAVAILABLE"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "LICENSE_MACHINE_UNAVAILABLE", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        record_event(
            db,
            category="license",
            event_type="license_import_rejected",
            severity="warning",
            actor_username=current_user.username,
            message="Lisans reddedildi: LICENSE_INVALID",
            metadata={"reason_code": "LICENSE_INVALID"},
            i18n_key="license_import_rejected",
            i18n_params={"reason": "LICENSE_INVALID"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LICENSE_INVALID", "message": str(exc)},
        ) from exc

    record_event(
        db,
        category="license",
        event_type="license_imported",
        severity="info",
        actor_username=current_user.username,
        message=(
            f"Lisans etkinlestirildi: {envelope.payload.customer_name} / "
            f"{envelope.payload.project_name}, cihaz limiti {envelope.payload.device_limit}"
        ),
        metadata={
            "license_id": envelope.payload.license_id,
            "customer_code": envelope.payload.customer_code,
            "device_limit": envelope.payload.device_limit,
        },
        i18n_key="license_imported",
        i18n_params={"limit": envelope.payload.device_limit},
    )
    db.commit()
    return license_service.get_license_status(db)
