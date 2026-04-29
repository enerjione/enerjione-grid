import hashlib
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import require_role, require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.enums import UserRole
from app.models.gateway import Gateway
from app.models.gateway_ingest_batch import GatewayIngestBatch
from app.models.signal_catalog import SignalCatalog
from app.models.user import User
from app.repositories.device_repository import DeviceRepository
from app.schemas.gateway import (
    GatewayConfigDevice,
    GatewayConfigResponse,
    GatewayConfigSignal,
    GatewayCreate,
    GatewayRead,
    GatewayUpdate,
)
from app.services.gateway_compose import (
    ComposeRenderError,
    ComposeRenderInput,
    filename_for,
    render_compose,
    render_env,
)

router = APIRouter(prefix="/gateways", tags=["gateways"])


@router.get("", response_model=list[GatewayRead])
def list_gateways(
    _: User = Depends(
        require_roles([UserRole.OPERATOR, UserRole.ENGINEER, UserRole.INSTALLER])
    ),
    db: Session = Depends(get_db),
):
    stmt = select(Gateway).order_by(Gateway.name.asc())
    return list(db.scalars(stmt).all())


@router.post("", response_model=GatewayRead, status_code=status.HTTP_201_CREATED)
def create_gateway(
    payload: GatewayCreate,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(Gateway).where(Gateway.code == payload.code))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Gateway code already exists")
    row = Gateway(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{gateway_code}", response_model=GatewayRead)
def update_gateway(
    gateway_code: str,
    payload: GatewayUpdate,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{gateway_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_gateway(
    gateway_code: str,
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    repository = DeviceRepository(db)
    repository.delete_all_for_gateway(gateway_code)
    db.execute(delete(GatewayIngestBatch).where(GatewayIngestBatch.gateway_code == gateway_code))
    db.delete(row)
    db.commit()
    return None


@router.post("/{gateway_code}/enable", response_model=GatewayRead)
def enable_gateway(
    gateway_code: str,
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Gateway'i aktiflestirir. Collector bir sonraki config refresh dongusunde
    bu bayragi gorup polling/publish dongusuyle yayina geri doner."""
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    row.is_active = True
    db.commit()
    db.refresh(row)
    return row


@router.post("/{gateway_code}/disable", response_model=GatewayRead)
def disable_gateway(
    gateway_code: str,
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Gateway'i pasiflestirir. Collector bir sonraki config refresh dongusunde
    is_active=False'i gorup polling'i askiya alir (proses ayakta kalir)."""
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    row.is_active = False
    db.commit()
    db.refresh(row)
    return row


@router.get("/{gateway_code}/docker-compose")
def download_gateway_compose(
    gateway_code: str,
    backend_url: str = Query(
        ...,
        description="Gateway'in backend'e erisecegi public URL (orn. https://hsl.formelektrik.com/api/v1)",
    ),
    rabbitmq_url: str = Query(
        ...,
        description="Gateway'in publish yapacagi AMQP URL (orn. amqp://user:pass@rmq.hsl:5672/)",
    ),
    host_port: int = Query(
        8020,
        ge=1,
        le=65535,
        description="Host'ta health/metrics endpoint icin acilacak port (her instance icin farkli)",
    ),
    image: str = Query(
        "hsl/dnp3-gateway:latest",
        description="Docker image tag (registry/name:tag)",
    ),
    app_environment: Literal["development", "staging", "production"] = Query(
        "production",
        description="Hedef ortam (token uzunluk dogrulamasini etkiler)",
    ),
    fmt: Literal["compose", "env"] = Query(
        "compose",
        description="compose: docker-compose YAML (default) | env: Docker disinda dogrudan calistirma icin .env",
    ),
    _: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Frontend "Yeni gateway ekle" akisi: indirilebilir docker-compose / .env dosyasi.

    Akis:
        1. Operator/installer arayuzde gateway kaydi olusturur (POST /gateways).
        2. Frontend bu endpoint'i cagirir, dosyayi indirir.
        3. Dosya hedef sunucuya kopyalanir; ``docker compose -f hsl-gw-XXX.yml up -d``.

    Donus: ``Content-Disposition: attachment`` ile dosya. Token gateway DB'den
    cekilir, frontend'e ayrica gostermeye gerek yoktur (compose icinde gomulu gelir).
    """

    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    try:
        render_input = ComposeRenderInput(
            code=gateway.code,
            token=gateway.token,
            name=gateway.name,
            backend_url=backend_url,
            rabbitmq_url=rabbitmq_url,
            host_port=host_port,
            image=image,
            app_environment=app_environment,
        )
    except (ComposeRenderError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        body = render_compose(render_input) if fmt == "compose" else render_env(render_input)
    except ComposeRenderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    media_type = "application/x-yaml" if fmt == "compose" else "text/plain"
    filename = filename_for(render_input, kind=fmt)
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        # Frontend Axios/fetch akisinda dosya adini ekspoze etmek gerekir;
        # CORS expose-headers backend tarafinda zaten "*" ise opsiyoneldir.
        "X-Filename": filename,
    }
    return Response(content=body, media_type=media_type, headers=headers)


@router.get("/{gateway_code}/config", response_model=GatewayConfigResponse)
def get_gateway_config(
    gateway_code: str,
    response: Response,
    db: Session = Depends(get_db),
    x_gateway_token: str | None = Header(default=None, alias="X-Gateway-Token"),
    x_gateway_code: str | None = Header(default=None, alias="X-Gateway-Code"),
    x_gateway_instance_id: str | None = Header(default=None, alias="X-Gateway-Instance-Id"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
):
    """Gateway servislerinin kendi konfig ve cihaz listesini çektiği endpoint.

    Auth: `X-Gateway-Token` header ile gateway token doğrulanır (operatör oturumu gerektirmez).

    Opsiyonel: `X-Gateway-Code` gönderilirse **path'teki `gateway_code` ile aynı** olmalıdır
    (yanlış yapılandırılmış istemcileri veya proxy hatalarını erken yakalar).
    `X-Gateway-Instance-Id` / `X-Request-Id` audit ve korelasyon için kabul edilir.

    Performans: Response `ETag: "<config_version>"` doner. Gateway bir sonraki
    istekte `If-None-Match` ile ayni ETag'i geri gonderirse **304 Not Modified**
    doneriz ve signal/device listesini serialize etmeyiz. 6 gateway x 30s
    refresh = dakika basina 12 cagri; konfig nadiren degistigi icin buyuk
    cogunlukta 304 olur, DB ve network yuku dusurur.
    """
    if x_gateway_code is not None and x_gateway_code.strip() != gateway_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Gateway-Code does not match route gateway_code",
        )
    _ = x_gateway_instance_id, x_request_id  # reserved: future audit log / tracing
    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    if not x_gateway_token or x_gateway_token != gateway.token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway token")
    # NOT: is_active=False durumunda 403 atmak yerine 200 + is_active=False
    # donduruyoruz; collector bu bilgiyi gorup kendi polling'ini askiya alir.
    # Boylece "uzaktan durdurma" kontrol panelindeki enable/disable butonlariyla
    # calisir ve collector ayakta kalip bir sonraki enable komutunu bekler.

    devices: list[Device] = DeviceRepository(db).list_devices_by_gateway(gateway_code)
    signals_rows = list(
        db.scalars(
            select(SignalCatalog)
            .where(SignalCatalog.is_active.is_(True))
            .order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
        ).all()
    )

    device_seed = "|".join(
        f"{device.code}:{device.ip_address}:{device.dnp3_address}:{device.poll_interval_sec}"
        for device in devices
    )
    signal_seed = "|".join(
        f"{signal.source}:{signal.key}:{signal.data_type}:{signal.dnp3_object_group}:{signal.dnp3_index}:{signal.scale}"
        for signal in signals_rows
    )
    config_version = hashlib.sha1(
        f"{gateway.code}:{gateway.batch_interval_sec}:{device_seed}::{signal_seed}".encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:12]
    etag = f'"{config_version}"'

    # `last_seen_at` ETag eslese bile her istekte guncellenmeli — konfigiyuon
    # degismemis bile olsa gateway canlilik sinyali veriyor.
    gateway.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    # ETag match -> 304 Not Modified. Signal/device Pydantic serialize yok,
    # response body de yok. Body disindaki headerlari gateway yine kullanir.
    normalized_inm = (if_none_match or "").strip()
    if normalized_inm in (etag, config_version):
        response.status_code = status.HTTP_304_NOT_MODIFIED
        response.headers["ETag"] = etag
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    response.headers["ETag"] = etag

    config_devices = [
        GatewayConfigDevice(
            code=device.code,
            name=device.name,
            ip_address=device.ip_address,
            dnp3_address=device.dnp3_address,
            poll_interval_sec=device.poll_interval_sec,
            timeout_ms=device.timeout_ms,
            retry_count=device.retry_count,
            signal_profile=device.signal_profile,
        )
        for device in devices
    ]
    config_signals = [
        GatewayConfigSignal(
            key=signal.key,
            label=signal.label,
            unit=signal.unit,
            source=signal.source,
            dnp3_class=signal.dnp3_class,
            data_type=signal.data_type,
            dnp3_object_group=signal.dnp3_object_group,
            dnp3_index=signal.dnp3_index,
            scale=signal.scale,
            offset=signal.offset,
            supports_alarm=signal.supports_alarm,
        )
        for signal in signals_rows
    ]

    return GatewayConfigResponse(
        gateway_code=gateway.code,
        gateway_name=gateway.name,
        batch_interval_sec=gateway.batch_interval_sec,
        max_devices=gateway.max_devices,
        is_active=gateway.is_active,
        devices=config_devices,
        signals=config_signals,
        config_version=config_version,
    )
