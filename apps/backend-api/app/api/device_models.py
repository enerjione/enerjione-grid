"""Cihaz modeli listesi endpoint'i.

Frontend cihaz formundaki Model dropdown'i ve Sinyaller sayfasindaki Model
filtresi bu endpoint'ten beslenir.

Liste IKI kaynagin birlesimi: kod icindeki yerlesik modeller + sinyal
katalogunda tanimli HER model (bkz. `app.data.device_models`). Yani yeni bir
modelin sinyallerini girmek onu secilebilir kilmaya yeter; surum cikarmak
gerekmez.

Endpoint'ler
------------
    GET  /device-models                    modeller (herkes)
    GET  /device-models/settings           model bazli ayarlar (herkes)
    PUT  /device-models/{model}/settings   ayar yaz (installer)

Model bazli ayar NEDEN VAR: batarya esikleri proje genelinde tek bir cifttti
ve tum cihazlara uygulaniyordu. SN 2.0 ile Pole Master Kit'in bataryalari
ayni voltaj araliginda calismaz; tek esik ikisinden birini surekli yanlis
gosterirdi.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.data.device_models import is_valid_model, list_models, model_label
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.device_model_settings import (
    DeviceModelSettingsRead,
    DeviceModelSettingsUpdate,
)
from app.services import device_profile_service
from app.services.event_service import record_event

router = APIRouter(prefix="/device-models", tags=["device-models"])


@router.get("", response_model=list[dict])
def list_device_models(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_models(db)


@router.get("/settings", response_model=list[DeviceModelSettingsRead])
def list_device_model_settings(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Her model icin kayitli + cozulmus ayarlar.

    Kayit bulunmayan modeller de DONER (cozulmus degerleriyle): arayuz tum
    modelleri listeleyip hangisinin ayarlanmadigini gosterebilsin.
    """
    sonuc = []
    for item in list_models(db):
        kod = item.get("code")
        if not kod:
            continue
        veri = device_profile_service.resolve_settings(db, kod)
        veri["label"] = item.get("label") or kod
        sonuc.append(veri)
    return sonuc


@router.put("/{model}/settings", response_model=DeviceModelSettingsRead)
def update_device_model_settings(
    model: str,
    payload: DeviceModelSettingsUpdate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    if not is_valid_model(model, db):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bilinmeyen cihaz modeli.",
        )
    device_profile_service.upsert_settings(
        db,
        model,
        battery_voltage_low=payload.battery_voltage_low,
        battery_voltage_full=payload.battery_voltage_full,
    )
    # Batarya esigi TUM o modeldeki cihazlarin yuzdesini degistirir; kalici
    # ve saha kararlarini etkileyen bir ayar oldugundan denetime yazilir.
    record_event(
        db,
        category="project-settings",
        event_type="device_model_settings_updated",
        severity="info",
        actor_username=current_user.username,
        message=(
            f"Device model settings updated: {model} "
            f"(battery {payload.battery_voltage_low}/{payload.battery_voltage_full})"
        ),
        metadata={
            "model": model,
            "battery_voltage_low": payload.battery_voltage_low,
            "battery_voltage_full": payload.battery_voltage_full,
        },
    )
    db.commit()
    veri = device_profile_service.resolve_settings(db, model)
    veri["label"] = model_label(model, db)
    return veri
