import hashlib
import logging
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import require_role, require_roles
from app.core.config import Settings, settings
from app.db.session import get_db
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.enums import UserRole
from app.models.gateway import Gateway
from app.models.gateway_ingest_batch import GatewayIngestBatch
from app.models.signal_catalog import SignalCatalog
from app.models.user import User
from app.repositories.device_repository import DeviceRepository
from app.schemas.gateway import (
    CommandResultItem,
    GatewayConfigCommand,
    GatewayConfigDevice,
    GatewayConfigResponse,
    GatewayConfigSignal,
    GatewayCreate,
    GatewayPendingResponse,
    GatewayRead,
    GatewayUpdate,
)
from app.services.event_service import record_event
from app.services.gateway_compose import (
    ComposeRenderError,
    ComposeRenderInput,
    derive_nats_url,
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


def _signed_json_response(gateway, model, extra_headers: dict[str, str] | None = None):
    """Pydantic model'i DETERMINISTIK byte'larla serialize edip HMAC imzali
    Response doner. Hem config hem pending endpoint kullanir.

    KRITIK: HMAC byte'lari deterministik olmali — gateway ayni byte'lardan imzayi
    dogrular. FastAPI default JSON renderer'i pydantic'in `model_dump_json()`
    ciktisindan farkli byte uretir (separators/ensure_ascii). Bu yuzden manuel
    olarak model_dump_json byte'larini yazip imzayi ondan hesapliyoruz.
    MITM/backend-kompromize koruma: gateway imzasiz/yanlis imzali komutu reddeder.
    """
    import hashlib as _hashlib
    import hmac as _hmac

    from fastapi.responses import Response as _Response

    body_bytes = model.model_dump_json().encode("utf-8")
    headers: dict[str, str] = dict(extra_headers or {})
    try:
        sig = _hmac.new(
            gateway.token.encode("utf-8"), body_bytes, _hashlib.sha256
        ).hexdigest()
        headers["X-Config-Signature"] = sig
    except Exception:  # noqa: BLE001
        logger.exception("gateway_body_signature_failed gateway=%s", gateway.code)
    return _Response(content=body_bytes, media_type="application/json", headers=headers)


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
    # NOT: Gateway artik telemetriyi NATS JetStream'e yayinliyor; RabbitMQ
    # cred provisioning kaldirildi. NATS open-by-default (anonymous publish
    # subject yetkisi) ile calisiyor; ileride TLS/auth eklemek istenirse
    # ayri bir mekanizma kullanilacak.
    data = payload.model_dump()
    # Otomatik port araligi atama: aynı host'ta birden fazla gateway calistirilabilsin
    # diye her gateway'e benzersiz bir 1000'lik blok atanir. Frontend manuel
    # belirleme yapmaz; bu mantik backend'de.
    data.setdefault("initiating_port_base", _allocate_initiating_port_base(db))
    # Token hash'ini hesapla (DB'de plaintext yerine SHA-256). Eski deploy'lardan
    # gelen kayitlar token_hash bos olarak gelir; validate_gateway_token()
    # opportunistic migration ile dogrulama sonrasi hash kolonu doldurur.
    raw_token = data.get("token") or ""
    if raw_token:
        from app.services.ingest_service import hash_gateway_token
        data["token_hash"] = hash_gateway_token(raw_token)
    row = Gateway(**data)
    db.add(row)
    db.flush()
    record_event(
        db,
        category="gateway",
        event_type="gateway_created",
        severity="info",
        actor_username=current_user.username,
        message=f"Gateway created: {row.name} ({row.code})",
        metadata={"gateway_code": row.code},
        i18n_key="gateway_created",
        i18n_params={"name": row.name, "code": row.code},
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
    # Config degisti -> nonce++ (gateway 1sn komut-poll'de gorup hemen ceker).
    row.config_nonce = int(getattr(row, "config_nonce", 0) or 0) + 1
    record_event(
        db,
        category="gateway",
        event_type="gateway_updated",
        severity="info",
        actor_username=current_user.username,
        message=f"Gateway updated: {row.name} ({row.code})",
        metadata={"gateway_code": row.code, "fields": list(changes.keys())},
        i18n_key="gateway_updated",
        i18n_params={"name": row.name, "code": row.code},
    )
    db.commit()
    db.refresh(row)
    return row


def _cleanup_rabbitmq_user(gateway_code: str) -> None:
    """Eski deploylar icin: gateway artik RabbitMQ kullanmiyor, bu cleanup
    no-op'a indirildi. Eski olusturulmus user'lari silmek istenirse, manual
    olarak (rabbitmqctl) yapilabilir; bizim icin pasif kalmalari yeterli."""
    try:
        _rmq_admin().delete_gateway_user(gateway_code=gateway_code)
    except Exception as exc:  # noqa: BLE001
        logger.debug("rabbitmq_user_cleanup_skipped gateway=%s error=%s", gateway_code, exc)


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
        message=f"Gateway deleted: {name} ({code}); {deleted_devices} attached devices also removed",
        metadata={"gateway_code": code, "deleted_devices": deleted_devices},
        i18n_key="gateway_deleted",
        i18n_params={"name": name, "code": code, "count": deleted_devices},
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
        row.config_nonce = int(getattr(row, "config_nonce", 0) or 0) + 1
        record_event(
            db,
            category="gateway",
            event_type="gateway_enabled",
            severity="info",
            actor_username=current_user.username,
            message=f"Gateway {row.name} ({row.code}) enabled",
            metadata={"gateway_code": row.code, "gateway_name": row.name},
            i18n_key="gateway_enabled",
            i18n_params={"name": row.name, "code": row.code},
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
        row.config_nonce = int(getattr(row, "config_nonce", 0) or 0) + 1
        record_event(
            db,
            category="gateway",
            event_type="gateway_disabled",
            severity="warning",
            actor_username=current_user.username,
            message=f"Gateway {row.name} ({row.code}) disabled",
            metadata={"gateway_code": row.code, "gateway_name": row.name},
            i18n_key="gateway_disabled",
            i18n_params={"name": row.name, "code": row.code},
        )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{gateway_code}/refresh-all", response_model=GatewayRead)
def refresh_gateway_all_devices(
    gateway_code: str,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Operator tetikli "tum cihazlara sorgu at" — gateway tarafinda Class
    0+1+2+3 integrity poll yapilir, tum sinyallerin guncel degeri DB'ye
    yazilir.

    Mekanizma: gateways.refresh_nonce sayaci 1 artirilir. Gateway her config
    refresh dongusunde (default 30sn) bu degeri okur; en son gordugu degerden
    farkliysa reader.refresh_all_devices() cagirir.

    Bu nedenle yanit anlik degildir: kullanici butona basinca bayrak DB'ye
    yazilir, gateway en gec config_refresh_sec icinde tetigi yakalar (cogu
    kurulumda <30sn) ve ardindan integrity frame'leri cihazlardan toplar.

    HTTP 200: bayrak set edildi (gateway tetigi yakalayacak).
    """
    row = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    new_nonce = int(getattr(row, "refresh_nonce", 0) or 0) + 1
    row.refresh_nonce = new_nonce
    record_event(
        db,
        category="gateway",
        event_type="gateway_refresh_all_requested",
        severity="info",
        actor_username=current_user.username,
        message=f"{row.name} ({row.code}) — tum cihazlara sorgu istegi (#{new_nonce})",
        metadata={"gateway_code": row.code, "refresh_nonce": new_nonce},
        i18n_key="gateway_refresh_all_requested",
        i18n_params={"name": row.name, "code": row.code, "nonce": new_nonce},
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
        description="(LEGACY/Deprecated) Gateway artik RabbitMQ kullanmiyor; bu parametre goz ardi edilir.",
    ),
    nats_url: str | None = Query(
        None,
        description="(Opsiyonel) NATS JetStream URL (orn. nats://hsl.example.com:4222). Verilmezse backend_url'in host kismindan otomatik turetilir (nats://<host>:4222).",
    ),
    host_port: int | None = Query(
        None,
        ge=1,
        le=65535,
        description="(Opsiyonel) Host'ta health/metrics endpoint icin acilacak port. Verilmezse gateway sirasina gore 8020/8021/... olarak otomatik atanir.",
    ),
    image: str = Query(
        "ghcr.io/fikretsafak/enerjionegrid-dnp3-gateway:latest",
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
        3. Dosya hedef sunucuya kopyalanir; ``docker compose -f e1-gw-XXX.yml up -d``.

    Donus: ``Content-Disposition: attachment`` ile dosya. Token gateway DB'den
    cekilir, frontend'e ayrica gostermeye gerek yoktur (compose icinde gomulu gelir).
    """

    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    # Container icinden erisim icin localhost/127.0.0.1 -> host.docker.internal
    effective_backend_url = normalize_backend_url_for_container(backend_url)
    # NATS URL onceligi:
    #   1) caller endpoint'e ?nats_url= ile explicit override gondermisse
    #   2) aksi halde backend host'undan otomatik turetilir (nats://<host>:4222)
    # Eski rabbitmq_url parametresi DEPRECATED — sessizce goz ardi edilir.
    _ = rabbitmq_url  # legacy param, kullanilmiyor
    if (nats_url or "").strip():
        effective_nats_url = nats_url.strip()
    else:
        # Backend Settings'ten gateway user password'u oku — `infra/nats/
        # nats-server.conf`'taki `gateway` user'ina karsilik gelir.
        # Production'da bos olmamalı (validator boş varsa boot'ta fail eder).
        effective_nats_url = derive_nats_url(
            backend_url,
            gateway_password=settings.nats_gateway_password,
        )
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
            nats_url=effective_nats_url,
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
    # validate_gateway_token: SHA-256 hash + hmac.compare_digest (timing-safe).
    # `token_hash` kolonunu otomatik doldurur (opportunistic migration); eski
    # plaintext compare yolu kaldirildi — bkz. ingest_service.py.
    # `allow_inactive=True`: is_active=False ise 403 atma; gateway poll'i
    # askiya almak icin 200 + is_active=False donmek istiyoruz (yorumda
    # belirtilmis: collector enable/disable mantigi).
    from app.services.ingest_service import validate_gateway_token

    gateway = validate_gateway_token(
        db, gateway_code, x_gateway_token, allow_inactive=True
    )
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

    # Komutlar artik AYRI /pending endpoint'inden gelir (config'ten ayrildi).
    # Config saf ETag/304: config degismemisse fast-path 304 doner (5dk poll'de
    # cogunlukla). Komut varligi config'i etkilemez.
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

    config_resp = GatewayConfigResponse(
        gateway_code=gateway.code,
        gateway_name=gateway.name,
        batch_interval_sec=gateway.batch_interval_sec,
        max_devices=gateway.max_devices,
        is_active=gateway.is_active,
        devices=config_devices,
        signals=config_signals,
        config_version=config_version,
        refresh_nonce=int(getattr(gateway, "refresh_nonce", 0) or 0),
        config_nonce=int(getattr(gateway, "config_nonce", 0) or 0),
        # Komut artik AYRI /pending endpoint'inde; config bos doner (geriye uyum).
        pending_commands=[],
    )

    # Deterministik + HMAC imzali response (ETag gibi mevcut header'lari yansit).
    return _signed_json_response(
        gateway, config_resp, extra_headers=dict(response.headers)
    )


@router.get("/{gateway_code}/pending")
def get_gateway_pending(
    gateway_code: str,
    db: Session = Depends(get_db),
    x_gateway_token: str | None = Header(default=None, alias="X-Gateway-Token"),
):
    """Hafif komut-poll — gateway 1sn'de bir ceker (komut anlik gelsin).

    Config'in AGIR parcalarini (device/signal listesi) TASIMAZ; sadece bekleyen
    komutlar + config_nonce + refresh_nonce. Komut config-poll'den AYRILDI: config
    5dk'da bir cekilir, komut burada 1sn'de.

    Auth: `X-Gateway-Token`. HMAC imza (X-Config-Signature) — MITM/komut enjekte
    koruma; gateway imzayi dogrular.

    `pending -> sent` gecisi BURADA yapilir (config endpoint'te DEGIL). Gateway
    komutu cektikten sonra command_ledger ile idempotent calistirir; ayni komut
    tekrar cekilse bile (sent kalirken) gateway CROB'u tekrar atmaz (ledger).
    """
    from app.services.ingest_service import validate_gateway_token

    gateway = validate_gateway_token(db, gateway_code, x_gateway_token)

    pending_cmds = list(
        db.scalars(
            select(DeviceCommand)
            .where(
                DeviceCommand.gateway_code == gateway.code,
                DeviceCommand.status == "pending",
            )
            .order_by(DeviceCommand.id.asc())
        ).all()
    )
    commands = [
        GatewayConfigCommand(
            id=cmd.id,
            device_code=cmd.device_code,
            command=cmd.command,
            dnp3_index=cmd.dnp3_index,
            op_type=cmd.op_type,
            count=cmd.count,
            on_time_ms=cmd.on_time_ms,
            off_time_ms=cmd.off_time_ms,
        )
        for cmd in pending_cmds
    ]
    # pending -> sent (komut gateway'e teslim edildi). Gateway command_ledger ile
    # idempotent; sent komut tekrar cekilmez (artik pending degil). Sonucu
    # command-results ile bildirir -> ok/failed.
    if pending_cmds:
        now = datetime.now(timezone.utc)
        for cmd in pending_cmds:
            cmd.status = "sent"
            cmd.sent_at = now

    gateway.last_seen_at = datetime.now(timezone.utc)
    resp = GatewayPendingResponse(
        gateway_code=gateway.code,
        is_active=gateway.is_active,
        commands=commands,
        config_nonce=int(getattr(gateway, "config_nonce", 0) or 0),
        refresh_nonce=int(getattr(gateway, "refresh_nonce", 0) or 0),
    )
    db.commit()
    return _signed_json_response(gateway, resp)


@router.post("/{gateway_code}/command-results")
def report_command_results(
    gateway_code: str,
    results: list[CommandResultItem] = Body(...),
    db: Session = Depends(get_db),
    x_gateway_token: str | None = Header(default=None, alias="X-Gateway-Token"),
):
    """Gateway calistirdigi cihaz komutlarinin sonuclarini bildirir (batch).

    Auth: `X-Gateway-Token` (config poll ile ayni). Gateway config'ten cektigi
    pending komutlari CROB ile calistirir, her birinin sonucunu buraya POST eder.

    Her sonuc: {id, ok, status, error?}. Ilgili device_commands satiri (ayni
    gateway_code — IDOR koruma) status='ok'|'failed' yapilir. Bilinmeyen/baska
    gateway'e ait id sessizce atlanir (idempotent; tekrar bildirim zararsiz).
    """
    from app.services.ingest_service import validate_gateway_token

    validate_gateway_token(db, gateway_code, x_gateway_token)

    if not results:
        return {"updated": 0}

    ids = [r.id for r in results]
    rows = {
        row.id: row
        for row in db.scalars(
            select(DeviceCommand).where(
                DeviceCommand.id.in_(ids),
                DeviceCommand.gateway_code == gateway_code,
            )
        ).all()
    }
    now = datetime.now(timezone.utc)
    updated = 0
    for res in results:
        cmd = rows.get(res.id)
        if cmd is None:
            continue  # baska gateway'e ait veya silinmis; atla
        # Terminal durumdaki komutu tekrar guncelleme (idempotent)
        if cmd.status in ("ok", "failed"):
            continue
        cmd.status = "ok" if res.ok else "failed"
        # result_status: gercek DNP3 CommandStatus varsa onu goster (NO_SELECT,
        # NOT_SUPPORTED gibi — cihaz neden reddetti belli olsun), yoksa genel status.
        dnp3_detail = res.dnp3_status or res.status
        cmd.result_status = (dnp3_detail[:40] if dnp3_detail else None)
        # result_error: hata + SBO fazi (SELECT_FAIL/OPERATE_FAIL) birlestir.
        err_parts = []
        if res.error:
            err_parts.append(res.error)
        if res.dnp3_state and "SUCCESS" not in (res.dnp3_state or ""):
            err_parts.append(f"faz={res.dnp3_state}")
        cmd.result_error = ("; ".join(err_parts)[:500] if err_parts else None)
        cmd.completed_at = now
        record_event(
            db,
            category="device",
            event_type="device_command_result",
            severity="info" if res.ok else "warning",
            actor_username=cmd.actor_username,
            device_code=cmd.device_code,
            message=(
                f"Komut sonucu: {cmd.command} ({cmd.device_code}) #{cmd.id} — "
                f"{'OK' if res.ok else 'HATA: ' + (res.dnp3_status or res.error or res.status)}"
            ),
            metadata={
                "command": cmd.command,
                "command_id": cmd.id,
                "ok": res.ok,
                "result_status": res.status,
                "dnp3_status": res.dnp3_status,
                "dnp3_state": res.dnp3_state,
                "dnp3_task": res.dnp3_task,
                "control": res.control,
                "duration_ms": res.duration_ms,
            },
            i18n_key="device_command_result",
            i18n_params={"command": cmd.command, "code": cmd.device_code},
        )
        updated += 1
    db.commit()
    return {"updated": updated}
