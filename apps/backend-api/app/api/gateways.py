import hashlib
import logging
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import require_role, require_roles
from app.core.config import Settings
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
from app.services.event_service import record_event
from app.services.gateway_compose import (
    ComposeRenderError,
    ComposeRenderInput,
    derive_rabbitmq_url,
    filename_for,
    normalize_backend_url_for_container,
    render_compose,
    render_env,
)
from app.services.rabbitmq_admin import (
    RabbitMqAdminClient,
    RabbitMqAdminError,
    RabbitMqUser,
    build_amqp_url,
)

logger = logging.getLogger(__name__)


def _rmq_admin() -> RabbitMqAdminClient:
    s = Settings()
    return RabbitMqAdminClient(
        management_url=s.rabbitmq_management_url,
        admin_username=s.rabbitmq_admin_username,
        admin_password=s.rabbitmq_admin_password,
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


_INITIATING_PORT_BLOCK_SIZE = 1000  # her gateway 1000'lik blok alir
_INITIATING_PORT_BASE_FIRST = 20100  # ilk gateway buradan baslar
_INITIATING_PORT_BASE_MAX = 60000  # 65535 - 1000 buffer; ustu kabul edilmez


def _allocate_initiating_port_base(db: Session) -> int:
    """Yeni gateway icin kullanilmamis port araligi base'i bul.

    Strateji: Mevcut gateway'lerin initiating_port_base'lerine bak; en
    yuksek olana 1000 ekle. Bos slot var ise (ornek: bir gateway silinmis ve
    arada delik kalmis) onu kullan.

    Boylece host'ta birden fazla gateway calistigında port catismasi olmaz:
    Gateway 1: 20100-20699 (host)
    Gateway 2: 21100-21699 (host)
    Gateway 3: 22100-22699 (host)
    ...
    Container'in icindeki port hep 20100-20699 (gateway kodu sabit).
    """
    used_bases = set(
        int(b)
        for (b,) in db.execute(select(Gateway.initiating_port_base)).all()
        if b is not None
    )
    candidate = _INITIATING_PORT_BASE_FIRST
    while candidate <= _INITIATING_PORT_BASE_MAX:
        if candidate not in used_bases:
            return candidate
        candidate += _INITIATING_PORT_BLOCK_SIZE
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Port araligi tukendi: {_INITIATING_PORT_BASE_FIRST}.."
            f"{_INITIATING_PORT_BASE_MAX} arasinda bos blok yok. Eski gateway'leri "
            "silin veya daha yuksek port araligi konfigurasyonu yapin."
        ),
    )


@router.post("", response_model=GatewayRead, status_code=status.HTTP_201_CREATED)
def create_gateway(
    payload: GatewayCreate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(Gateway).where(Gateway.code == payload.code))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Gateway code already exists")
    # RabbitMQ'da gateway icin ayri bir kullanici otomatik olusturulur. Bu
    # sayede sahaya kurulum yapan kullanici bir manuel rabbitmqctl/admin
    # paneli adimi yapmak zorunda kalmaz; compose dosyasi indirilir
    # indirilmez "docker compose up -d" ile saglikli baglantiya gecer.
    rmq_user: RabbitMqUser | None = None
    try:
        rmq_user = _rmq_admin().create_gateway_user(gateway_code=payload.code)
    except RabbitMqAdminError as exc:
        # RabbitMQ ulasilmiyorsa gateway yine de yaratilir; ama compose
        # indirilirken fallback olarak global guest cred kullanilir ve
        # uyari verilir. Production'da Management API up olmasi beklenir.
        logger.warning(
            "rabbitmq_admin_unavailable_at_create gateway=%s error=%s",
            payload.code,
            exc,
        )
    data = payload.model_dump()
    if rmq_user is not None:
        data["rabbitmq_username"] = rmq_user.username
        data["rabbitmq_password"] = rmq_user.password
    # Otomatik port araligi atama: aynı host'ta birden fazla gateway calistirilabilsin
    # diye her gateway'e benzersiz bir 1000'lik blok atanir. Frontend manuel
    # belirleme yapmaz; bu mantik backend'de.
    data.setdefault("initiating_port_base", _allocate_initiating_port_base(db))
    row = Gateway(**data)
    db.add(row)
    db.flush()
    record_event(
        db,
        category="gateway",
        event_type="gateway_created",
        severity="info",
        actor_username=current_user.username,
        message=f"Gateway eklendi: {row.name} ({row.code})",
        metadata={"gateway_code": row.code},
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{gateway_code}", response_model=GatewayRead)
def update_gateway(
    gateway_code: str,
    payload: GatewayUpdate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(row, key, value)
    record_event(
        db,
        category="gateway",
        event_type="gateway_updated",
        severity="info",
        actor_username=current_user.username,
        message=f"Gateway güncellendi: {row.name} ({row.code})",
        metadata={"gateway_code": row.code, "fields": list(changes.keys())},
    )
    db.commit()
    db.refresh(row)
    return row


def _cleanup_rabbitmq_user(gateway_code: str) -> None:
    """Background task: RabbitMQ user temizligi. HTTP response sonrasinda calisir."""
    try:
        _rmq_admin().delete_gateway_user(gateway_code=gateway_code)
    except RabbitMqAdminError as exc:
        logger.warning("rabbitmq_user_cleanup_failed gateway=%s error=%s", gateway_code, exc)


@router.delete("/{gateway_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_gateway(
    gateway_code: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    name = row.name
    code = row.code
    repository = DeviceRepository(db)
    deleted_devices = repository.delete_all_for_gateway(gateway_code)
    db.execute(delete(GatewayIngestBatch).where(GatewayIngestBatch.gateway_code == gateway_code))
    db.delete(row)
    record_event(
        db,
        category="gateway",
        event_type="gateway_deleted",
        severity="warning",
        actor_username=current_user.username,
        message=f"Gateway silindi: {name} ({code}); {deleted_devices} bağlı cihaz da kaldırıldı",
        metadata={"gateway_code": code, "deleted_devices": deleted_devices},
    )
    db.commit()
    # Best-effort RabbitMQ user temizligi response sonrasina ertelenir.
    background_tasks.add_task(_cleanup_rabbitmq_user, gateway_code)
    return None


@router.post("/{gateway_code}/enable", response_model=GatewayRead)
def enable_gateway(
    gateway_code: str,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Gateway'i aktiflestirir. Collector bir sonraki config refresh dongusunde
    bu bayragi gorup polling/publish dongusuyle yayina geri doner."""
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    was_active = row.is_active
    row.is_active = True
    if not was_active:
        record_event(
            db,
            category="gateway",
            event_type="gateway_enabled",
            severity="info",
            actor_username=current_user.username,
            message=f"{row.name} ({row.code}) gateway'i etkinleştirildi",
            metadata={"gateway_code": row.code, "gateway_name": row.name},
        )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{gateway_code}/disable", response_model=GatewayRead)
def disable_gateway(
    gateway_code: str,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Gateway'i pasiflestirir. Collector bir sonraki config refresh dongusunde
    is_active=False'i gorup polling'i askiya alir (proses ayakta kalir)."""
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    was_active = row.is_active
    row.is_active = False
    if was_active:
        record_event(
            db,
            category="gateway",
            event_type="gateway_disabled",
            severity="warning",
            actor_username=current_user.username,
            message=f"{row.name} ({row.code}) gateway'i devre dışı bırakıldı",
            metadata={"gateway_code": row.code, "gateway_name": row.name},
        )
    db.commit()
    db.refresh(row)
    return row


@router.get("/{gateway_code}/docker-compose")
def download_gateway_compose(
    gateway_code: str,
    backend_url: str = Query(
        ...,
        description="Gateway'in backend'e erisecegi public URL (orn. https://hsl.formelektrik.com/api/v1 veya http://192.168.1.50:8000/api/v1)",
    ),
    rabbitmq_url: str | None = Query(
        None,
        description="(Opsiyonel) RabbitMQ AMQP URL. Verilmezse backend_url'in host kismindan otomatik turetilir (amqp://hsl:hsl@<host>:5672/).",
    ),
    host_port: int | None = Query(
        None,
        ge=1,
        le=65535,
        description="(Opsiyonel) Host'ta health/metrics endpoint icin acilacak port. Verilmezse gateway sirasina gore 8020/8021/... olarak otomatik atanir.",
    ),
    image: str = Query(
        "ghcr.io/fikretsafak/horstmann-dnp3-gateway:latest",
        description="Docker image tag (registry/name:tag). Varsayilan GHCR public paketidir; ozel registry kullanilacaksa override edilir.",
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
    # Container icinden erisim icin localhost/127.0.0.1 -> host.docker.internal
    effective_backend_url = normalize_backend_url_for_container(backend_url)
    # RabbitMQ URL onceligi:
    #   1) caller endpoint'e ?rabbitmq_url= ile explicit override gondermisse
    #   2) gateway icin DB'de saklanan dedicated cred (otomatik provisionlanmis)
    #   3) global guest fallback (Management API erisilemediyse — saha kurulumu
    #      icin uyari logu yazilir)
    if (rabbitmq_url or "").strip():
        effective_rabbitmq_url = rabbitmq_url.strip()
    elif gateway.rabbitmq_username and gateway.rabbitmq_password:
        parsed = urlparse(effective_backend_url)
        host = parsed.hostname or "host.docker.internal"
        rmq_user = RabbitMqUser(
            username=gateway.rabbitmq_username,
            password=gateway.rabbitmq_password,
        )
        effective_rabbitmq_url = build_amqp_url(host=host, user=rmq_user)
    else:
        # Cred yoksa (RabbitMQ Management API down idiyse veya eski kayit)
        # son care: gateway'i simdi provisionlamayi bir kez daha denemek
        try:
            user = _rmq_admin().create_gateway_user(gateway_code=gateway.code)
            gateway.rabbitmq_username = user.username
            gateway.rabbitmq_password = user.password
            db.commit()
            from urllib.parse import urlparse as _urlparse

            parsed = _urlparse(effective_backend_url)
            host = parsed.hostname or "host.docker.internal"
            effective_rabbitmq_url = build_amqp_url(host=host, user=user)
        except RabbitMqAdminError as exc:
            logger.warning(
                "rabbitmq_admin_unavailable_at_compose_render gateway=%s error=%s "
                "(falling back to guest cred — saha tarafinda calismayabilir)",
                gateway.code,
                exc,
            )
            effective_rabbitmq_url = derive_rabbitmq_url(backend_url)
    if host_port is None:
        # Gateway'in olusturulma sirasina gore 8020 + index. Birden fazla gateway
        # ayni host'ta calisirsa health portlari catismaz. Frontend kullanicisi
        # explicit port istiyorsa query param ile override edebilir.
        all_gw_codes = list(
            db.scalars(select(Gateway.code).order_by(Gateway.id.asc())).all()
        )
        try:
            index = all_gw_codes.index(gateway.code)
        except ValueError:
            index = 0
        effective_host_port = 8020 + index
    else:
        effective_host_port = host_port
    try:
        # Port araligi: gateway'e atanmis benzersiz block + sayisi.
        # initiating_port_count = 0 ise hic port acilmaz (sadece listening
        # cihazlar). Default 50; kullanici frontend'den artirabilir.
        port_base = int(gateway.initiating_port_base or _INITIATING_PORT_BASE_FIRST)
        # 0 = sadece listening cihazlar; compose'da hic port publish edilmez.
        port_count = int(gateway.initiating_port_count or 0)
        render_input = ComposeRenderInput(
            code=gateway.code,
            token=gateway.token,
            name=gateway.name,
            backend_url=effective_backend_url,
            rabbitmq_url=effective_rabbitmq_url,
            host_port=effective_host_port,
            image=image,
            app_environment=app_environment,
            initiating_port_base=port_base,
            initiating_port_count=port_count,
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

    # Initiating mode cihazlar icin host port range: gateway her cihaz icin
    # ayri bir TCP server portu acar. OpenDNP3'un TCPServer kanali tek
    # client kabul ettigi icin cihaz basina port mecbur. Backend cihaz
    # sirasina gore deterministik atar (DB id'sine gore) — boylece compose
    # YAML'i ayni port'lari expose eder ve cihaz config'inde port sabit kalir.
    #
    # Port araligi: gateway.initiating_port_base + idx. Cok-gateway senaryolarinda
    # her gateway'in ayri bir port blogu var (20100, 21100, 22100, ...) ve
    # bu sayede ayni host'ta calisirken catismaz.
    initiating_devices_sorted = sorted(
        [d for d in devices if (d.dnp3_extended or {}).get("ip_endpoint_type") == "initiating"],
        key=lambda d: d.id,
    )
    initiating_port_map: dict[str, int] = {}
    port_base = int(gateway.initiating_port_base or _INITIATING_PORT_BASE_FIRST)
    for idx, d in enumerate(initiating_devices_sorted):
        # Frontend "Master IP Port" alanini bu portla doldurmali — saha cihazi
        # public IP:port'a outbound TCP baglantisi acar.
        initiating_port_map[d.code] = port_base + idx

    config_devices = []
    for device in devices:
        # Frontend'den gelen extended ayarlardaki master_address (DNP3 link layer
        # local addr) — saha cihazi bu adresi bekler. Yoksa None birakiriz ve
        # gateway kendi env DNP3_LOCAL_ADDRESS varsayilanini kullanir.
        ext = device.dnp3_extended or {}
        master_addr_raw = ext.get("master_address") if isinstance(ext, dict) else None
        try:
            master_address = int(master_addr_raw) if master_addr_raw is not None else None
        except (TypeError, ValueError):
            master_address = None
        endpoint_type = "listening"
        if isinstance(ext, dict):
            raw_endpoint = str(ext.get("ip_endpoint_type") or "listening").strip().lower()
            if raw_endpoint in ("initiating", "listening"):
                endpoint_type = raw_endpoint
        config_devices.append(
            GatewayConfigDevice(
                code=device.code,
                name=device.name,
                ip_address=device.ip_address,
                dnp3_address=device.dnp3_address,
                # Cihaz baglantisinin TCP portu — frontend'de cihaz basina ayarlanir
                # (varsayilan 20001). Bu alan gonderilmezse gateway env varsayilani
                # 20000'e baglanmaya calisir, bu da dogru olmaz.
                dnp3_tcp_port=device.dnp3_outstation_port,
                master_address=master_address,
                ip_endpoint_type=endpoint_type,
                # Initiating mode -> gateway bu portta dinler; cihaz buraya baglanir.
                # Listening mode -> alan kullanilmaz, None.
                master_ip_port=(
                    initiating_port_map.get(device.code) if endpoint_type == "initiating" else None
                ),
                poll_interval_sec=device.poll_interval_sec,
                timeout_ms=device.timeout_ms,
                retry_count=device.retry_count,
                signal_profile=device.signal_profile,
            )
        )
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
