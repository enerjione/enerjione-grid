import hashlib
import json
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
from app.data.device_models import DEFAULT_MODEL
from app.models.user import User
from app.repositories.device_repository import DeviceRepository
from app.schemas.dnp3_extended import merge_dnp3_extended
from app.schemas.gateway_agent import (
    GatewayAgentStatus,
    GatewayLogsResponse,
    LocalInstallRequest,
    LocalInstallResponse,
)
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
from app.services import gateway_agent_service
from app.services.event_service import record_event
from app.services.gateway_agent_service import GatewayAgentError
from app.services.gateway_compose import (
    ComposeRenderError,
    ComposeRenderInput,
    derive_nats_url,
    derive_rabbitmq_url,
    filename_for,
    normalize_backend_url_for_container,
    render_compose,
    render_env,
    validate_render_input,
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


@router.get("/{gateway_code}/token")
def get_gateway_token(
    gateway_code: str,
    current_user: User = Depends(require_roles([UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Gateway token'ini duz metin doner — YALNIZCA INSTALLER.

    NEDEN AYRI UC: token `GET /gateways` yanitindan cikarildi. O liste
    operator'a da acik ve token telemetri gonderiminin TEK kimlik unsuru;
    listede durdugu surece operator kendi alani disindaki cihazlar icin
    uydurma telemetri gonderebiliyordu (sahte ariza uretmek ya da gercek
    arizayi maskelemek).

    Token'a gercekten ihtiyaci olan tek akis gateway kurulumu/degistirmesidir
    ve o installer isidir. Her okuma DENETIM KAYDINA yazilir: duz metin bir
    sirrin kimin ne zaman gordugu iz birakmadan gecmemeli.
    """
    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Gateway bulunamadi"
        )
    record_event(
        db,
        category="gateway",
        event_type="gateway_token_viewed",
        severity="warning",
        actor_username=current_user.username,
        message=f"Gateway token'i goruntulendi: {gateway.code}",
        metadata={"gateway_code": gateway.code},
    )
    db.commit()
    return {"code": gateway.code, "token": gateway.token}


_INITIATING_PORT_BLOCK_SIZE = 1000  # her gateway 1000'lik blok alir
_INITIATING_PORT_BASE_FIRST = 20100  # ilk gateway buradan baslar
_INITIATING_PORT_BASE_MAX = 60000  # 65535 - 1000 buffer; ustu kabul edilmez

# DNP3 gateway imaji. Hem "dosya indir" hem "bu cihaza kur" akisi ayni
# varsayilani kullanmali; aksi halde indirilen compose ile kurulan container
# farkli surumden olur.
_DEFAULT_GATEWAY_IMAGE = "ghcr.io/enerjione/enerjione-grid-dnp3-gateway:latest"

# Cihaz modeli bilinmiyorsa kullanilan profil anahtari.
#
# Model kayit defterinden ALINIR, elle yazilmaz: literal kopyalansaydi
# `DEFAULT_MODEL` degistiginde model'i bos kalmis eski cihaz kayitlari
# sessizce var olmayan bir profile eslesirdi (yani bos sinyal listesi ->
# cihaz yoklanmaz). Yeni model eklerken tek dokunulacak yer
# `app/data/device_models.py` olmali.
_DEFAULT_PROFILE_KEY = DEFAULT_MODEL


def _profile_key_of(device: Device) -> str:
    """Cihazin sinyal seti anahtari.

    `devices.signal_profile` KOLONU KULLANILMAZ — bkz. schemas/gateway.py
    `GatewayConfigDevice.signal_profile`. Ozet: o kolon sahada sabit
    "horstmann_sn2_fixed" degeriyle duruyor, hicbir yer okumuyor ve katalogun
    model sozlugunde karsiligi yok; anahtar olarak kullanilsaydi hicbir profile
    eslesmezdi.
    """
    return (getattr(device, "model", None) or "").strip() or _DEFAULT_PROFILE_KEY


def compute_config_version(
    *,
    gateway_name: str,
    batch_interval_sec: int,
    max_devices: int,
    is_active: bool,
    devices: list[GatewayConfigDevice],
    signals: list[GatewayConfigSignal],
    signals_by_profile: dict[str, list[GatewayConfigSignal]] | None = None,
) -> str:
    """Gonderilecek payload'in KENDISINDEN turetilen config surumu.

    TEK KAYNAK: hem endpoint hem testler bu fonksiyonu cagirir. Daha once
    hesap endpoint'te gomuluydu ve test kendi kopyasini tutuyordu; iki kopya
    sessizce ayrisabilirdi — ki `config_version` hatasinin ilk hali tam olarak
    "hash gonderilen veriyi temsil etmiyor" idi. Kopya birakmiyoruz.
    """
    material = json.dumps(
        {
            "gateway_name": gateway_name,
            "batch_interval_sec": batch_interval_sec,
            "max_devices": max_devices,
            "is_active": is_active,
            "devices": [d.model_dump(mode="json") for d in devices],
            "signals": [sg.model_dump(mode="json") for sg in signals],
            "signals_by_profile": {
                profil: [sg.model_dump(mode="json") for sg in satirlar]
                for profil, satirlar in (signals_by_profile or {}).items()
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha1(
        material.encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]


def _to_config_signal(signal: SignalCatalog) -> GatewayConfigSignal:
    return GatewayConfigSignal(
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

    # TOKEN ROTASYONU — hash'i de guncelle.
    #
    # YASANAN ARIZA: bu uc `setattr` ile TUM alanlari yaziyordu ama
    # `token_hash` DOKUNULMUYORDU. Create yolunda hash hesaplaniyor
    # (`hash_gateway_token`), update yolunda hesaplanmiyordu.
    #
    # `validate_gateway_token` `token_hash` DOLUYSA yalnizca ona bakar
    # (plaintext karsilastirma sadece hash bosken devreye giren legacy yol).
    # Sonuc iki yonlu bozuktu:
    #
    #   * YENI token 401 alir  -> operator "gateway baglanmiyor" der,
    #   * ESKI token CALISMAYA DEVAM EDER -> rotasyon iptal ETMEZ.
    #
    # Yani token sizdigi icin degistirildiginde sizan token hala gecerli
    # kalir; rotasyonun tek amaci tam da bunu engellemekti.
    if "token" in changes:
        from app.services.ingest_service import hash_gateway_token

        yeni_token = changes.get("token") or ""
        row.token_hash = hash_gateway_token(yeni_token) if yeni_token else None

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
    deleted_device_codes, cleanup_counts = repository.delete_all_for_gateway(gateway_code)
    db.execute(delete(GatewayIngestBatch).where(GatewayIngestBatch.gateway_code == gateway_code))
    db.delete(row)
    record_event(
        db,
        category="gateway",
        event_type="gateway_deleted",
        severity="warning",
        actor_username=current_user.username,
        message=f"Gateway deleted: {name} ({code}); {len(deleted_device_codes)} attached devices also removed",
        metadata={
            "gateway_code": code,
            "deleted_devices": deleted_device_codes,
            "cleanup": cleanup_counts,
        },
        i18n_key="gateway_deleted",
        i18n_params={"name": name, "code": code, "count": len(deleted_device_codes)},
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


def _build_render_input(
    db: Session,
    gateway: Gateway,
    *,
    backend_url: str,
    nats_url: str | None,
    host_port: int | None,
    image: str,
    app_environment: str,
) -> ComposeRenderInput:
    """compose/env render girdisini hazirla.

    Hem "baska cihaza kur" (dosya indirme) hem "bu cihaza kur" (host ajani)
    akisi ayni girdiyi kullanir; port/NATS turetme mantigi tek yerde dursun
    diye ayri fonksiyon. Iki akis ayrisirsa uretilen compose da ayrisir ve
    sahada "indirdigimle kurulan ayni degil" hatasi cikar.
    """
    # Container icinden erisim icin localhost/127.0.0.1 -> host.docker.internal
    effective_backend_url = normalize_backend_url_for_container(backend_url)
    # NATS URL onceligi:
    #   1) caller explicit override gondermisse
    #   2) aksi halde backend host'undan otomatik turetilir (nats://<host>:4222)
    if (nats_url or "").strip():
        effective_nats_url = nats_url.strip()
    else:
        effective_nats_url = derive_nats_url(
            backend_url,
            gateway_password=settings.nats_gateway_password,
        )
    if host_port is None:
        # Gateway'in olusturulma sirasina gore 8020 + index. Birden fazla gateway
        # ayni host'ta calisirsa health portlari catismaz.
        all_gw_codes = list(db.scalars(select(Gateway.code).order_by(Gateway.id.asc())).all())
        try:
            index = all_gw_codes.index(gateway.code)
        except ValueError:
            index = 0
        effective_host_port = 8020 + index
    else:
        effective_host_port = host_port

    # Port araligi: gateway'e atanmis benzersiz block + sayisi.
    # initiating_port_count = 0 ise hic port acilmaz (sadece listening cihazlar).
    port_base = int(gateway.initiating_port_base or _INITIATING_PORT_BASE_FIRST)
    port_count = int(gateway.initiating_port_count or 0)
    return ComposeRenderInput(
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
        # Indirilen compose ile "bu cihaza kur" akisi AYNI degeri tasimali;
        # aksi halde ayni gateway iki farkli davranisla kurulurdu.
        publish_dnp3_quality=bool(getattr(gateway, "publish_dnp3_quality", False)),
    )


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
        _DEFAULT_GATEWAY_IMAGE,
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
    # Eski rabbitmq_url parametresi DEPRECATED — sessizce goz ardi edilir.
    _ = rabbitmq_url  # legacy param, kullanilmiyor
    try:
        render_input = _build_render_input(
            db,
            gateway,
            backend_url=backend_url,
            nats_url=nats_url,
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


# --------------------------------------------------------------------------
# "Gateway'i bu cihaza kur" — host ajani (e1-gwd) akisi
#
# Backend Docker'a ERISMEZ; /var/lib/e1-grid/gw/request.json yazar, host'ta
# root ile calisan ajan compose'u kendi kurallariyla dogrulayip calistirir.
# Ajan kurulu degilse endpoint'ler 503 doner ve UI bu secenegi kapali gosterir
# — "baska cihaza kur" akisi bundan etkilenmez.
# --------------------------------------------------------------------------
_AGENT_ERROR_STATUS = {
    "request_pending": status.HTTP_409_CONFLICT,
}

# Ham kod yerine operatorun ANLAYACAGI mesaj.
#
# Oncesinde `message` alanina ham kodun kendisi yaziliyordu ve arayuz onu
# oldugu gibi kirmizi metin olarak basiyordu: ekranda "request_pending"
# goruyordunuz. Ne oldugunu, ne yapilmasi gerektigini soylemiyor.
#
# `code` alani DURUYOR — arayuz makine tarafinda hala ona bakabilir.
# (Ayni desen network.py'de zaten var.)
_AGENT_ERROR_MESSAGE = {
    "request_pending": (
        "Onceki istek hala uygulaniyor. Birkac saniye bekleyip tekrar deneyin."
    ),
    "state_dir_missing": (
        "Bu cihazda gateway ajani kurulu degil; kurulum/guncelleme buradan yapilamaz."
    ),
    "state_dir_not_writable": (
        "Gateway ajaninin calisma dizinine yazilamiyor; kurulumu kontrol edin."
    ),
    "agent_never_reported": (
        "Gateway ajani henuz durum bildirmedi. Ajan servisinin calistigini kontrol edin."
    ),
    "unavailable": "Gateway ajanina ulasilamiyor.",
}


def _agent_http_error(exc: GatewayAgentError) -> HTTPException:
    reason = str(exc)
    code = _AGENT_ERROR_STATUS.get(reason, status.HTTP_503_SERVICE_UNAVAILABLE)
    mesaj = _AGENT_ERROR_MESSAGE.get(reason)
    if mesaj is None:
        # Taninmayan kod: ham hali kalsin — sessizce yutmak teshisi zorlastirir.
        mesaj = reason
    return HTTPException(status_code=code, detail={"code": reason, "message": mesaj})


@router.get("/local-agent", response_model=GatewayAgentStatus)
def get_local_agent_status(
    _: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
):
    """Host ajaninin durumu + bu cihazda kurulu gateway'ler.

    NOT: bu route parametreli `/{gateway_code}/...` route'larindan ONCE
    tanimli olmali degil — bu dosyada `GET /{gateway_code}` yok, cakisma
    olusmuyor. Ileride eklenirse sira onemli hale gelir.
    """
    return gateway_agent_service.read_status()


@router.post(
    "/{gateway_code}/local-install",
    response_model=LocalInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def install_gateway_locally(
    gateway_code: str,
    payload: LocalInstallRequest,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Gateway'i bu cihaza kur (host ajani uzerinden).

    202 doner: kurulum ASENKRONDUR. Ajan imaji ceker (ilk kurulumda dakikalar
    surebilir) ve durumu status.json'a yazar; frontend `GET /local-agent`
    ile ilerlemeyi izler.
    """
    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")

    # Varsayilan backend adresi: host'un kendi nginx'i. Gateway ayni makinede
    # oldugu icin LAN IP'sine gerek yok — ve IP degisse bile kurulum bozulmaz
    # (compose sablonunda extra_hosts ile host-gateway zaten tanimli).
    backend_url = (payload.backend_url or "").strip() or "http://host.docker.internal/api/v1"
    image = (payload.image or "").strip() or _DEFAULT_GATEWAY_IMAGE

    # `_build_render_input` hem parametreleri normalize eder hem dogrular
    # (kod/token/port araliklari). compose'u BURADA uretmiyoruz: ajan kendi
    # sablonundan uretecek. Yine de render_input'u kuruyoruz ki gecersiz
    # parametreler ajana gitmeden 400 ile geri donsun ve "baska cihaza kur"
    # akisiyla ayni dogrulamadan gecsin.
    try:
        render_input = _build_render_input(
            db,
            gateway,
            backend_url=backend_url,
            nats_url=payload.nats_url,
            host_port=payload.host_port,
            image=image,
            app_environment=payload.app_environment,
        )
        validate_render_input(render_input)
    except (ComposeRenderError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        request_id = gateway_agent_service.request_install(
            gateway.code,
            gateway.name,
            current_user.username,
            image=render_input.image,
            token=render_input.token,
            backend_url=render_input.backend_url,
            nats_url=render_input.nats_url,
            host_port=render_input.host_port,
            app_environment=render_input.app_environment,
            initiating_port_base=render_input.initiating_port_base,
            initiating_port_count=render_input.initiating_port_count,
            publish_dnp3_quality=render_input.publish_dnp3_quality,
        )
    except GatewayAgentError as exc:
        raise _agent_http_error(exc) from exc

    record_event(
        db,
        category="gateway",
        event_type="gateway_local_install_requested",
        severity="info",
        actor_username=current_user.username,
        message=f"{gateway.name} ({gateway.code}) — bu cihaza kurulum istegi",
        metadata={"gateway_code": gateway.code, "request_id": request_id, "image": image},
        i18n_key="gateway_local_install_requested",
        i18n_params={"name": gateway.name, "code": gateway.code},
    )
    db.commit()
    return LocalInstallResponse(request_id=request_id, code=gateway.code)


@router.delete("/{gateway_code}/local-install", status_code=status.HTTP_202_ACCEPTED)
def remove_gateway_locally(
    gateway_code: str,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Bu cihazdaki gateway container'ini durdur ve kaldir.

    Gateway KAYDI silinmez — sadece bu makinedeki kurulumu kaldirilir.
    Kaydi silmek icin `DELETE /gateways/{code}` kullanilir.
    """
    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    try:
        request_id = gateway_agent_service.request_remove(gateway.code, current_user.username)
    except GatewayAgentError as exc:
        raise _agent_http_error(exc) from exc

    record_event(
        db,
        category="gateway",
        event_type="gateway_local_remove_requested",
        severity="warning",
        actor_username=current_user.username,
        message=f"{gateway.name} ({gateway.code}) — bu cihazdan kaldirma istegi",
        metadata={"gateway_code": gateway.code, "request_id": request_id},
        i18n_key="gateway_local_remove_requested",
        i18n_params={"name": gateway.name, "code": gateway.code},
    )
    db.commit()
    return LocalInstallResponse(request_id=request_id, code=gateway.code)


@router.post(
    "/{gateway_code}/local-update",
    response_model=LocalInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def update_gateway_locally(
    gateway_code: str,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Gateway'i bu cihazdaki EN GUNCEL imaja yukselt (host ajani uzerinden).

    Yeni imaj cekilir ve container yeniden olusturulur. Cekme basarisiz
    olursa ajan container'a DOKUNMAZ — yarim bir guncelleme yerine calisan
    eski surumde kalinir.

    KESINTI: gateway yeniden baslarken o gateway'e bagli cihazlardan
    telemetri gelmez (tipik olarak birkac saniye). Bu yuzden INSTALLER'a
    ozel ve denetim kaydi birakiyor — panelden tetiklenen her yeniden
    baslatma sahada gorunur bir kesintidir.

    Yanit ANLIK SONUC DEGIL: `{request_id}` doner, ajan istegi asenkron
    isler. Sonuc `GET /gateways/local-agent` icindeki `last_apply` ile
    takip edilir.

    STANDART NATS ROTASI: guncelleme istegiyle birlikte guncel NATS URL'i
    de gonderilir; ajan compose'u yeniden uretip gateway'i NATS-direkt
    telemetriye gecirir. NATS oncesi kurulan gateway'ler boylece HTTP
    fallback'inde takili kalmaz (telemetri backend'e ugramadan JetStream'e
    akar). NATS_GATEWAY_PASSWORD bos ise (dev/test) URL gonderilmez —
    anonim URL NATS deny-all'a takilir, gateway'i calisir halden cikarmak
    yerine mevcut compose korunur.
    """
    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    if not gateway_agent_service.is_installed_locally(gateway.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gateway bu cihazda kurulu degil; once kurulum yapin.",
        )
    standart_nats_url: str | None = None
    if settings.nats_gateway_password.strip():
        # Lokal kurulumda gateway backend ile ayni host'ta: install akisindaki
        # varsayilanla ayni sekilde host.docker.internal uzerinden turetilir
        # (compose 4222'yi host'a publish ediyor).
        standart_nats_url = derive_nats_url(
            "http://host.docker.internal/api/v1",
            gateway_password=settings.nats_gateway_password,
        )
    try:
        request_id = gateway_agent_service.request_update(
            gateway.code, current_user.username, nats_url=standart_nats_url
        )
    except GatewayAgentError as exc:
        raise _agent_http_error(exc) from exc

    record_event(
        db,
        category="gateway",
        event_type="gateway_local_update_requested",
        severity="info",
        actor_username=current_user.username,
        message=f"{gateway.name} ({gateway.code}) — guncelleme istegi",
        metadata={"gateway_code": gateway.code, "request_id": request_id},
        i18n_key="gateway_local_update_requested",
        i18n_params={"name": gateway.name, "code": gateway.code},
    )
    db.commit()
    return LocalInstallResponse(request_id=request_id, code=gateway.code)


# --------------------------------------------------------------------------
# Gateway yasam dongusu: DURDUR / BASLAT / YENIDEN BASLAT
#
# YETKI — ucu de INSTALLER'a ozel, `local-install` / `local-update` ile ayni
# kapi. Durdurmak bu gateway'e bagli TUM cihazlarin veri akisini keser;
# operator (ve engineer) kazara tetikleyememeli. `require_role` HIYERARSIK
# DEGIL, tam eslesme yapar (bkz. deps.require_role) — engineer / ops_manager /
# operator otomatik icerilmez.
#
# DENETIM — her istek `record_event` ile olay kaydina duser. Sahadaki "veri
# neden gelmiyor" sorusunun cevabi orada olmali; durdurma gorunmez olursa
# arayuzun "durduruldu" demesi de kimsenin dogrulayamayacagi bir iddiaya
# doner.
#
# KOMUT GONDERMIYORUZ: backend ajana yalnizca bir EYLEM ADI yazar
# ("stop" / "start" / "restart"). docker argumanlari ajanin icinde sabit
# yazili; hedef container adi da ajanda gateway kodundan turetilip
# dogrulanir (bkz. e1-gwd `_dogrula_hedef`).
# --------------------------------------------------------------------------
_LIFECYCLE_ACTIONS: dict[str, dict[str, str]] = {
    "stop": {
        "event_type": "gateway_local_stop_requested",
        # Veri akisi kesiliyor: bilgi degil UYARI. Olay listesinde
        # kaybolmamali.
        "severity": "warning",
        "ozet": "durdurma istegi (veri akisi duracak)",
    },
    "start": {
        "event_type": "gateway_local_start_requested",
        "severity": "info",
        "ozet": "baslatma istegi",
    },
    "restart": {
        "event_type": "gateway_local_restart_requested",
        # Kesinti kisa ama gercek; `local-update` ile ayni siniflandirma.
        "severity": "info",
        "ozet": "yeniden baslatma istegi",
    },
}


def _lifecycle_request(
    db: Session,
    gateway_code: str,
    current_user: User,
    action: Literal["stop", "start", "restart"],
) -> LocalInstallResponse:
    """Uc yasam dongusu ucunun ortak govdesi.

    202 doner: ajan istegi ASENKRON isler. Sonuc `GET /gateways/local-agent`
    icindeki `last_apply` ile takip edilir (islem saniyeler surer).
    """
    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    if not gateway_agent_service.is_installed_locally(gateway.code):
        # Gateway baska bir cihazda kosuyor olabilir; bu uc yalnizca BU
        # cihazdaki container'i yonetir.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gateway bu cihazda kurulu degil; buradan durdurulup baslatilamaz.",
        )

    istek = {
        "stop": gateway_agent_service.request_stop,
        "start": gateway_agent_service.request_start,
        "restart": gateway_agent_service.request_restart,
    }[action]
    try:
        request_id = istek(gateway.code, current_user.username)
    except GatewayAgentError as exc:
        raise _agent_http_error(exc) from exc

    tanim = _LIFECYCLE_ACTIONS[action]
    record_event(
        db,
        category="gateway",
        event_type=tanim["event_type"],
        severity=tanim["severity"],
        actor_username=current_user.username,
        message=f"{gateway.name} ({gateway.code}) — {tanim['ozet']}",
        metadata={"gateway_code": gateway.code, "request_id": request_id, "action": action},
        i18n_key=tanim["event_type"],
        i18n_params={"name": gateway.name, "code": gateway.code},
    )
    db.commit()
    return LocalInstallResponse(request_id=request_id, code=gateway.code)


@router.post(
    "/{gateway_code}/local-stop",
    response_model=LocalInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def stop_gateway_locally(
    gateway_code: str,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Bu cihazdaki gateway container'ini DURDUR.

    KALDIRMA DEGIL: compose dosyasi ve container yerinde kalir, ajan durumu
    `exited` olarak raporlar. Bu ayrim olmadan arayuz "durduruldu" ile
    "kurulu degil"i ayirt edemez ve durdurulmus gateway'i ariza gibi
    gosterir.

    SONUC: bu gateway'e bagli cihazlardan telemetri GELMEZ. Durdurma kalici
    bir niyettir; cihaz yeniden baslatilsa bile container kalkmaz.
    """
    return _lifecycle_request(db, gateway_code, current_user, "stop")


@router.post(
    "/{gateway_code}/local-start",
    response_model=LocalInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_gateway_locally(
    gateway_code: str,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Durdurulmus gateway container'ini yeniden baslat (imaj cekmeden)."""
    return _lifecycle_request(db, gateway_code, current_user, "start")


@router.post(
    "/{gateway_code}/local-restart",
    response_model=LocalInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def restart_gateway_locally(
    gateway_code: str,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Gateway container'ini ayni imajla yeniden baslat.

    `local-update`ten farki: imaj CEKMEZ, surum degismez. Takilan bir
    gateway'i toparlamak icin en ucuz mudahale — ama yine de kisa bir
    kesintidir.
    """
    return _lifecycle_request(db, gateway_code, current_user, "restart")


# --------------------------------------------------------------------------
# UZAKTAN LOG — "gateway ne diyor" sorusunun cevabi.
#
# YETKI: installer + engineer. Yasam dongusu uclarindan DAHA GENIS, cunku
# log OKUMAK sistemi degistirmez; sahadaki muhendis "veri neden gelmiyor"u
# arastirirken kurulumcuyu beklememeli. operator/ops_manager'a KAPALI:
# container ciktisi ic ayrintilar (host adlari, yollar) icerir.
#
# SIR SIZINTISI: cikti `read_logs` icinde MASKELENIR (token/parola/bearer,
# URL icindeki kimlik). Bkz. gateway_agent_service._SECRET_PATTERNS.
#
# AUDIT: log ISTEGI kalici bir state degisimi degil; olay kaydi YAZILMAZ.
# (Yasam dongusu uclari yaziyor — onlar sahayi etkiliyor.)
# --------------------------------------------------------------------------
@router.post(
    "/{gateway_code}/local-logs",
    response_model=LocalInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_gateway_logs(
    gateway_code: str,
    tail: int = Query(
        default=gateway_agent_service.LOGS_TAIL_DEFAULT,
        ge=gateway_agent_service.LOGS_TAIL_MIN,
        le=gateway_agent_service.LOGS_TAIL_MAX,
        description="Kac satir gerilere bakilacak",
    ),
    current_user: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
    db: Session = Depends(get_db),
):
    """Gateway container loglarini ajandan iste (asenkron, 202).

    Sonuc birkac saniye icinde `GET /gateways/{code}/local-logs` ile okunur.
    """
    gateway = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    if not gateway_agent_service.is_installed_locally(gateway.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gateway bu cihazda kurulu degil; logu buradan alinamaz.",
        )
    try:
        request_id = gateway_agent_service.request_logs(
            gateway.code, current_user.username, tail=tail
        )
    except GatewayAgentError as exc:
        raise _agent_http_error(exc) from exc
    return LocalInstallResponse(request_id=request_id, code=gateway.code)


@router.get("/{gateway_code}/local-logs", response_model=GatewayLogsResponse)
def get_gateway_logs(
    gateway_code: str,
    _: User = Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER])),
):
    """Ajanin yazdigi SON log ciktisi.

    Henuz log alinmamissa `available: false` doner — 404 DEGIL: "hic
    istenmemis" bir hata durumu degil, arayuzun "Log Al" demesi gereken
    normal bir baslangic hali.
    """
    data = gateway_agent_service.read_logs(gateway_code)
    if data is None:
        return GatewayLogsResponse(available=False, code=gateway_code)
    return GatewayLogsResponse(available=True, **data)


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

    # SANAL SETLER POLL HEDEFI DEGILDIR.
    #
    # Bir Horstmann Pole Master Kit'in uc seti AYNI fiziksel outstation'dir;
    # gateway'e uc ayri cihaz olarak verilirse ayni uc noktaya UC TCP oturumu
    # acilir. Horstmann `CloseExisting` modunda calisir — yeni baglanti
    # mevcudu kapatir — ve sonuc karsilikli tahliye dongusudur (aynisi
    # 2026-08-01'de iki cihaz ayni porta ayarlandiginda yasandi: gateway
    # gunlugunde 2.172 `link_close`, telemetri kesintili, belirti "ag
    # kararsiz" gorunuyor, kok neden gorunmuyor).
    #
    # Ustelik initiating modda her cihaza ayri host portu ayrilir (asagida);
    # sanal setler sizarsa portlar bosa harcanir ve `max_devices` muhasebesi
    # sasar. Kitin uzerindeki 9 uydunun TAMAMI zaten fiziksel kaydin sinyal
    # profilinde okunur; bolme telemetri hattinda (tag-engine) yapilir.
    devices: list[Device] = [
        d
        for d in DeviceRepository(db).list_devices_by_gateway(gateway_code)
        if d.parent_device_id is None
    ]
    signals_rows = list(
        db.scalars(
            select(SignalCatalog)
            .where(SignalCatalog.is_active.is_(True))
            .order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
        ).all()
    )


    # `last_seen_at` ETag eslese bile her istekte guncellenmeli — konfigiyuon
    # degismemis bile olsa gateway canlilik sinyali veriyor.
    gateway.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    # Komutlar artik AYRI /pending endpoint'inden gelir (config'ten ayrildi).
    # Config saf ETag/304: config degismemisse fast-path 304 doner (5dk poll'de
    # cogunlukla). Komut varligi config'i etkilemez.
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
        # master_address (DNP3 link layer local addr) — saha cihazi bu adresi
        # BEKLER; yanlis/eksik olursa istegi sessizce atar (bkz.
        # schemas/dnp3_extended.py). Bu yuzden HAM sozluk degil
        # `merge_dnp3_extended` okunur: eksik ya da diske `null` yazilmis
        # kayitlar varsayilana (100) iyilesir. Ham sozlugu okumak, v2.54.1
        # penceresinde null yazilmis cihazi alani bos gondererek gateway'in
        # DNP3_LOCAL_ADDRESS=1 varsayilanina dusuruyor ve haberlesmeyi KESIYORDU.
        ext = device.dnp3_extended if isinstance(device.dnp3_extended, dict) else None
        master_address = merge_dnp3_extended(ext).master_address
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
                # Profil anahtari = cihaz MODELI. Gerekce schemas/gateway.py'de
                # `GatewayConfigDevice.signal_profile` uzerinde ayrintili.
                signal_profile=_profile_key_of(device),
            )
        )

    # --- B3: profil bazli sinyal katalogu ---------------------------------
    #
    # SORUN: `signals` duz bir listeydi ve gateway onu TUM cihazlara ayni
    # sekilde uyguluyordu (poller tek `state.signals()` kullaniyor). Cihaz
    # modeli tek oldugu surece bu tesadufen dogru calisir. Ikinci bir model
    # eklendigi anda ayni (object_group, index) cifti iki farkli buyuklugu
    # gosterir ve okunan deger YANLIS `signal_key` ile yayinlanir: telemetri
    # akmaya devam eder, deger makul gorunur, ama esik alarmi baska bir
    # buyuklugun uzerinden calisir. Hata SESSIZDIR.
    #
    # Not: gateway'in kendi dokumantasyonu ("backend, signals listesini bu
    # profile gore filtreler") bunu zaten VARSAYIYORDU; backend hic yapmiyordu.
    # ANAHTAR HER ZAMAN YAZILIR — bos olsa bile.
    #
    # Ilk tasarimda bos profil ATLANIYORDU ki gateway duz listeye dussun ve
    # cihaz "karanliga" dusmesin. Bu, TEK bir sinyal setinin tum cihazlar icin
    # ust kume oldugu varsayimina dayaniyordu.
    #
    # Varsayim yanlis. Bu gateway yalnizca DNP3 konusur (baska protokoller ayri
    # gateway ile calisir) ama AYNI PROTOKOL ICINDE de modeller ayrisir: baska
    # bir DNP3 modelinin (object_group, index) haritasi bu cihaz icin YABANCI
    # adrestir. Duz liste bir ust kume degil, komsu modellerin adres toplamidir.
    # Onlari yoklamak "veri yok"tan daha kotudur: makul gorunen ama baska bir
    # buyukluge ait degerler yanlis `signal_key` ile yayinlanir ve esik
    # alarmlari onlarin uzerinden calisir.
    #
    # Bu yuzden bos profil de anahtar olarak yazilir. Gateway o cihaz icin
    # hicbir sey yoklamaz; eksiklik ise log'da GORUNUR olur. Sessiz yanlis veri
    # yerine gorunur eksik veri.
    signals_by_profile: dict[str, list[GatewayConfigSignal]] = {}
    empty_profiles: list[str] = []
    for profile_key in sorted({_profile_key_of(d) for d in devices}):
        rows = [s for s in signals_rows if (s.model or "") == profile_key]
        signals_by_profile[profile_key] = [_to_config_signal(s) for s in rows]
        if not rows:
            empty_profiles.append(profile_key)

    if empty_profiles:
        logger.warning(
            "gateway-config-profile-empty gateway=%s profiles=%s",
            gateway_code,
            ",".join(empty_profiles),
        )

    # Duz liste: profillerin BIRLESIMI (tum katalog degil).
    #
    # Eskiden bu gateway'de hic bulunmayan modellerin sinyalleri de listeye
    # giriyordu; gateway onlari da her cihazda yokluyordu. Tek modelli
    # kurulumda sonuc birebir ayni, cok modellide daha az ve dogru.
    #
    # Profil sozlugu BOS kaldiysa (or. katalog modeli cihaz modeliyle hic
    # eslesmiyor) tum aktif katalog donuyor — eski davranis, cihaz karanliga
    # dusmesin.
    if signals_by_profile:
        _seen_keys: set[str] = set()
        config_signals = []
        for _profile_rows in signals_by_profile.values():
            for _sig in _profile_rows:
                if _sig.key in _seen_keys:
                    continue
                _seen_keys.add(_sig.key)
                config_signals.append(_sig)
        # Katalog sırası korunur (display_order, key) — signals_rows zaten o
        # sirada; birlesim sonrasi tekrar siralamak gateway tarafinda
        # gereksiz config_version oynamasini onler.
        _order = {s.key: i for i, s in enumerate(signals_rows)}
        config_signals.sort(key=lambda s: _order.get(s.key, len(_order)))
    else:
        config_signals = [_to_config_signal(s) for s in signals_rows]

    # --- config_version: PAYLOAD'IN KENDISINDEN turetilir ------------------
    #
    # ESKIDEN elle tutulan bir "seed" string'i vardi ve yalnizca su alanlari
    # iceriyordu: code, ip_address, dnp3_address, poll_interval_sec (+ sinyal
    # tarafinda source/key/data_type/group/index/scale).
    # Oysa payload BUNLARDAN FAZLASINI tasiyor: dnp3_tcp_port, master_address,
    # ip_endpoint_type, master_ip_port, timeout_ms, retry_count,
    # signal_profile, sinyal `offset`i...
    #
    # Sonucu CANLI BIR HATAYDI: bir cihazin TCP portunu degistirdiginizde
    # payload degisiyor ama config_version AYNI kaliyordu -> ETag esleşiyor ->
    # gateway 304 aliyor -> DEGISIKLIGI HIC OGRENMIYOR. Ustelik gateway disk
    # cache'ini de tazelemedigi icin, backend erisilemezken restart eden bir
    # gateway ESKI ayarla aciliyordu.
    #
    # Artik hash dogrudan gonderilecek veriden hesaplaniyor: payload degisirse
    # surum DE degisir. Elle liste tutulmadigi icin bir daha sapamaz.
    #
    # MALIYET: 304 durumunda da payload'i INSA ediyoruz (serialize maliyeti),
    # ama ASIL kazanc olan AG TRAFIGI korunuyor — 175 sinyali tel uzerinden
    # tekrar gondermiyoruz. Yerel serialize maliyeti dakikada ~12 cagri icin
    # ihmal edilebilir; sessizce yanlis konfigurasyonla calisan bir saha
    # cihazi ise ihmal edilemez.
    # `signals_by_profile` de hash'e GIRER (bkz. compute_config_version).
    # Zorunlu: sinyal `key`leri katalogda global benzersiz oldugu icin bir
    # sinyali A modelinden B modeline TASIMAK duz listeyi (birlesimi)
    # DEGISTIRMEZ — ayni anahtarlar, ayni sira. Degisen yalnizca profil
    # sozlugudur. Hash'e girmeseydi gateway 304 alir ve sinyalin artik baska
    # bir modele ait oldugunu HIC ogrenmezdi: yukarida anlatilan sessiz
    # sapmanin birebir aynisi.
    config_version = compute_config_version(
        gateway_name=gateway.name,
        batch_interval_sec=gateway.batch_interval_sec,
        max_devices=gateway.max_devices,
        is_active=gateway.is_active,
        devices=config_devices,
        signals=config_signals,
        signals_by_profile=signals_by_profile,
    )
    etag = f'"{config_version}"'

    # NOT: refresh_nonce / config_nonce hash'e DAHIL DEGIL — onlar ayri
    # tetikleyiciler ve kendi mekanizmalari var; hash'e girselerdi her nonce
    # artisi tum sinyal listesinin yeniden gonderilmesine yol acardi.
    normalized_inm = (if_none_match or "").strip()
    if normalized_inm in (etag, config_version):
        response.status_code = status.HTTP_304_NOT_MODIFIED
        response.headers["ETag"] = etag
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    response.headers["ETag"] = etag

    config_resp = GatewayConfigResponse(
        gateway_code=gateway.code,
        gateway_name=gateway.name,
        batch_interval_sec=gateway.batch_interval_sec,
        max_devices=gateway.max_devices,
        is_active=gateway.is_active,
        devices=config_devices,
        signals=config_signals,
        signals_by_profile=signals_by_profile,
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
    x_gateway_health: str | None = Header(default=None, alias="X-E1-Gateway-Health"),
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

    # --- Gateway saglik heartbeat'i (opsiyonel) --------------------------
    # Saha gateway'i NAT arkasinda; backend onun /health ucuna ULASAMAZ.
    # Bu yuzden saglik ozeti gateway'in ZATEN saniyede bir attigi bu istege
    # basligla biniyor (ek istek maliyeti YOK).
    #
    # KOMUT KANALI KUTSAL: burasi SCADA komut yolu. Bozuk base64, gecersiz
    # JSON, devasa baslik ya da DB hatasi bu ucu ASLA dusurmemeli — aksi
    # halde saglik raporlamak icin eklenen ozellik kesici komutlarinin
    # iletilmesini engeller.
    #
    # KENDI SESSION'INDA — try/except TEK BASINA YETMIYORDU (sahada
    # yasandi): saglik INSERT'i request session'inda patlayinca istisna
    # yakalaniyordu ama paylasilan transaction "aborted" kaliyordu; asagidaki
    # `db.commit()` (komut durumu + last_seen) o yuzden 500 veriyordu ve
    # gateway basligi 10 dakika birakiyordu. Ayri session ile saglik
    # yazimindaki HICBIR hata komut kanalinin transaction'ina dokunamaz.
    if x_gateway_health:
        try:
            from app.db.session import SessionLocal
            from app.services.gateway_health_service import (
                parse_health_header,
                record_health,
            )

            payload = parse_health_header(x_gateway_health)
            if payload is not None:
                saglik_db = SessionLocal()
                try:
                    if record_health(saglik_db, gateway.code, payload):
                        saglik_db.commit()
                finally:
                    saglik_db.close()
        except Exception:  # noqa: BLE001
            logger.warning(
                "gateway_health_ingest_failed gateway=%s — komut kanali etkilenmedi",
                gateway_code,
                exc_info=True,
            )

    resp = GatewayPendingResponse(
        gateway_code=gateway.code,
        is_active=gateway.is_active,
        commands=commands,
        config_nonce=int(getattr(gateway, "config_nonce", 0) or 0),
        refresh_nonce=int(getattr(gateway, "refresh_nonce", 0) or 0),
        heartbeat_interval_sec=settings.gateway_heartbeat_interval_sec,
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
