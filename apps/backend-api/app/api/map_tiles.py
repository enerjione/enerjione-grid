"""Cevrimdisi harita karolari API'si.

Endpoint'ler:
  GET    /map-tiles/{layer}/{z}/{x}/{y}.png  -> karo servisi (TUM haritalar buradan)
  GET    /map-tiles/summary                  -> katmanlar, onbellek boyutu, paketler
  POST   /map-tiles/estimate                 -> secilen alan kac karo / kac MB
  POST   /map-tiles/packs                    -> alan indirmeyi baslat
  DELETE /map-tiles/packs/{pack_id}          -> paketi sil (opsiyonel karolarla)
  POST   /map-tiles/packs/{pack_id}/cancel   -> devam eden indirmeyi durdur

YETKI:
  Karo SERVISI (ilk endpoint) her oturum acmis kullaniciya aciktir — haritayi
  operator de goruyor. Yonetim islemleri (indirme, silme) engineer+installer.

KIMLIK DOGRULAMA NOTU:
  Leaflet karolari <img> ile ister ve Authorization header'i EKLEYEMEZ. Burada
  ek bir sey yapmaya gerek yok: oturum zaten HttpOnly cookie (`e1_session`)
  ile tasiniyor ve karo istegi ayni origin'e gittigi icin cookie kendiliginden
  gidiyor. Token'i sorgu parametresine koymaktan bilerek kaciniyoruz — URL'ler
  log'lara ve Referer header'ina sizar.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.map_tiles import (
    MapAreaEstimate,
    MapEstimateResult,
    MapPack,
    MapPackCreate,
    MapTileSummary,
)
from app.services import map_tile_service
from app.services.event_service import record_event


router = APIRouter(prefix="/map-tiles", tags=["map-tiles"])
_MANAGE_ROLES = [UserRole.ENGINEER, UserRole.INSTALLER]


def _http_error(exc: map_tile_service.MapTileError) -> HTTPException:
    code = status.HTTP_400_BAD_REQUEST
    if exc.code == "MAP_DOWNLOAD_BUSY":
        code = status.HTTP_409_CONFLICT
    elif exc.code == "MAP_TILE_OFFLINE":
        code = status.HTTP_404_NOT_FOUND
    return HTTPException(status_code=code, detail={"code": exc.code, "message": exc.message})


@router.get("/summary", response_model=MapTileSummary)
def map_tile_summary(_: User = Depends(require_roles(_MANAGE_ROLES))):
    return map_tile_service.summary()


@router.post("/estimate", response_model=MapEstimateResult)
def estimate_area(
    payload: MapAreaEstimate,
    _: User = Depends(require_roles(_MANAGE_ROLES)),
):
    try:
        return map_tile_service.estimate(
            payload.bbox, payload.layer, payload.zoom_min, payload.zoom_max
        )
    except map_tile_service.MapTileError as exc:
        raise _http_error(exc) from exc


@router.post("/packs", response_model=MapPack, status_code=status.HTTP_202_ACCEPTED)
def create_pack(
    payload: MapPackCreate,
    current_user: User = Depends(require_roles(_MANAGE_ROLES)),
    db: Session = Depends(get_db),
):
    try:
        pack = map_tile_service.start_pack(
            name=payload.name,
            layer=payload.layer,
            bbox=payload.bbox,
            zoom_min=payload.zoom_min,
            zoom_max=payload.zoom_max,
        )
    except map_tile_service.MapTileError as exc:
        raise _http_error(exc) from exc
    record_event(
        db,
        category="system",
        event_type="map_pack_started",
        severity="info",
        actor_username=current_user.username,
        message=f"Cevrimdisi harita indirme baslatildi: {pack.name} ({pack.tile_total} karo)",
        metadata={
            "pack_id": pack.id,
            "layer": pack.layer,
            "zoom": [pack.zoom_min, pack.zoom_max],
            "tiles": pack.tile_total,
        },
        i18n_key="map_pack_started",
        i18n_params={"name": pack.name, "tiles": pack.tile_total},
    )
    db.commit()
    return pack.to_dict()


@router.post("/packs/{pack_id}/cancel", response_model=MapPack)
def cancel_pack(
    pack_id: str,
    _: User = Depends(require_roles(_MANAGE_ROLES)),
):
    if not map_tile_service.cancel_pack(pack_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MAP_PACK_NOT_RUNNING", "message": "Devam eden bir indirme bulunamadi"},
        )
    pack = map_tile_service.get_pack(pack_id)
    return pack.to_dict() if pack else {}


@router.post("/packs/{pack_id}/restart", response_model=MapPack)
def restart_pack(
    pack_id: str,
    _: User = Depends(require_roles(_MANAGE_ROLES)),
):
    """Yarim kalmis alani kuyruga geri koy. Diskteki karolar atlanir."""
    pack = map_tile_service.restart_pack(pack_id)
    if pack is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "MAP_PACK_NOT_RESUMABLE",
                "message": "Paket bulunamadi veya zaten kuyrukta",
            },
        )
    return pack.to_dict()


@router.delete("/packs/{pack_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pack(
    pack_id: str,
    remove_tiles: bool = Query(False, description="Karolari da diskten sil"),
    current_user: User = Depends(require_roles(_MANAGE_ROLES)),
    db: Session = Depends(get_db),
):
    if not map_tile_service.delete_pack(pack_id, remove_tiles=remove_tiles):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MAP_PACK_NOT_FOUND", "message": "Paket bulunamadi"},
        )
    record_event(
        db,
        category="system",
        event_type="map_pack_deleted",
        severity="info",
        actor_username=current_user.username,
        message=f"Cevrimdisi harita paketi silindi ({pack_id})",
        metadata={"pack_id": pack_id, "remove_tiles": remove_tiles},
        i18n_key="map_pack_deleted",
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Karo servisi -----------------------------------------------------------
# Yol en SONDA tanimli: `/summary`, `/estimate`, `/packs` gibi sabit yollar
# `{layer}` deseniyle catismasin diye (FastAPI sirayla eslestirir).
@router.get("/{layer}/{z}/{x}/{y}.png")
def serve_tile(
    layer: str,
    z: int,
    x: int,
    y: int,
    _: User = Depends(get_current_user),
):
    if z < 0 or z > 22 or x < 0 or y < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gecersiz karo")
    try:
        data, source = map_tile_service.get_tile(layer, z, x, y)
    except map_tile_service.MapTileError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - yukari akis hatasi
        # Internet yok / uzak sunucu hata verdi. Bu bir SUNUCU hatasi degil:
        # karo yok demek. Leaflet 404'te sessizce bos karo gosterir, 500'de
        # konsolu hata mesajiyla doldurur.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MAP_TILE_UNAVAILABLE", "message": str(exc)[:200]},
        ) from exc
    return Response(
        content=data,
        media_type="image/png",
        headers={
            # Onbellekten gelen karo degismez; tarayici tekrar sormasin.
            "Cache-Control": "public, max-age=604800",
            "X-Tile-Source": source,
        },
    )
