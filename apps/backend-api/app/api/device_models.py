"""Cihaz modeli listesi endpoint'i.

Frontend cihaz formundaki Model dropdown'i ve Sinyaller sayfasindaki Model
filtresi bu endpoint'ten beslenir.

Liste IKI kaynagin birlesimi: kod icindeki yerlesik modeller + sinyal
katalogunda tanimli HER model (bkz. `app.data.device_models`). Yani yeni bir
modelin sinyallerini girmek onu secilebilir kilmaya yeter; surum cikarmak
gerekmez.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.device_models import list_models
from app.db.session import get_db
from app.models.user import User

router = APIRouter(prefix="/device-models", tags=["device-models"])


@router.get("", response_model=list[dict])
def list_device_models(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_models(db)
