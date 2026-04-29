"""Cihaz modeli listesi endpoint'i.

Frontend cihaz formundaki Model dropdown'i ve Sinyaller sayfasindaki Model
filtresi bu endpoint'ten beslenir. Eklenen yeni modeller `app.data.device_models`
icindeki MODELS sozlugune yazilarak otomatik buradan goruntulenir.
"""

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.data.device_models import list_models
from app.models.user import User

router = APIRouter(prefix="/device-models", tags=["device-models"])


@router.get("", response_model=list[dict])
def list_device_models(_: User = Depends(get_current_user)):
    return list_models()
