"""Canli telemetry WebSocket endpoint'i.

Frontend `/ws/live-values` endpoint'ine baglandiktan sonra her telemetri
mesajini anlik olarak alir. Polling'e gore avantaj:
  - Cihaz->frontend gecikme: ~10sn (polling) -> ~200ms (WS push).
  - Backend yuku: 600 cihaz × frontend sayisi × 2sn polling yerine,
    600 cihaz × 1 broadcast.
  - DB load: live-values endpoint'i her polling'de tum cihazlar icin
    SELECT yapardi; WS ile sadece degisen sinyaller iletilir.

Auth:
  WebSocket connection sirasinda query param `?ticket=<bilet>` (onerilen) veya
  `?token=<bearer>` (legacy) zorunlu. Gecersizse close(code=1008).

Yetki (KAPSAM):
  Operator rolu YALNIZCA kendi sorumluluk alanindaki cihazlarin telemetrisini
  alir. Kapsam SUNUCUDA hesaplanir; `?devices=` yalnizca DARALTABILIR, kapsam
  disina cikaramaz. (Onceden cihaz filtresi tamamen istemciden geliyordu ve
  filtre gonderilmezse operator TUM cihazlarin telemetrisini aliyordu.)

Oturum iptali:
  Uzun-omurlu baglanti handshake aninda VE her heartbeat'te (30sn) yeniden
  dogrulanir. Installer "oturumu at" dediginde soket 1008 ile kapanir; aksi
  halde iptal edilmis bir oturum akmaya devam ederdi.

Mesaj formati (server -> client):
  {
    "type": "telemetry",
    "device_code": "...",
    "signal_key": "...",
    "value": 12.34,
    "value_string": null,
    "quality": "good",
    "source_timestamp": "...",
    "signal_data_type": "analog"
  }

Heartbeat:
  Server her 30sn bir `{"type": "ping"}` gonderir; client reply
  `{"type": "pong"}` zorunlu degil — connection alive olmasi yeterli.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.ws_broadcaster import broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


# Heartbeat suresi: client connection alive mi kontrol icin server'dan ping.
# 30sn fazla, 5sn cok az; 30sn dengeli.
_HEARTBEAT_INTERVAL_SEC = 30


def _validate_token(token: str | None) -> tuple[str, str | None] | None:
    """JWT bearer token validate. Basariliysa (username, jti); aksi halde None.

    NOT: burada yalnizca imza/exp dogrulanir. Oturum iptali (revocation)
    `_is_session_revoked` ile AYRICA kontrol edilir — imza gecerli olsa bile
    logout edilmis bir token WS acmamali.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username = payload.get("sub")
        if not username or not isinstance(username, str):
            return None
        jti = payload.get("jti")
        return username, (str(jti) if jti else None)
    except JWTError:
        return None


def _is_session_revoked(jti: str | None) -> bool:
    """Oturum iptal edilmis mi? (in-memory blacklist + DB `revoked_at`)

    Kisa-omurlu bir session acar ve HEMEN kapatir: WS baglantisi uzun omurlu
    oldugu icin `Depends(get_db)` ile session tutmak her client basina bir
    havuz baglantisini sonsuza kadar rezerve ederdi (havuz 30+20).

    jti yoksa (cok eski token) iptal kontrolu yapilamaz — False doner.
    """
    if not jti:
        return False
    from app.services.auth_service import is_jti_revoked

    if is_jti_revoked(jti):
        return True
    db = SessionLocal()
    try:
        from app.models.user_session import UserSession

        sess = db.get(UserSession, jti)
        return sess is not None and sess.revoked_at is not None
    except Exception:  # noqa: BLE001
        # DB erisilemiyorsa baglantiyi kesmiyoruz; in-memory blacklist zaten
        # bakildi. Aksi halde gecici bir DB hatasi tum canli ekranlari dusurur.
        return False
    finally:
        db.close()


def _effective_device_filter(
    allowed: set[str] | None, requested: set[str] | None
) -> set[str] | None:
    """Kapsam ile istemci filtresini birlestir — GUVENLIK SINIRI.

    `allowed`   sunucunun hesapladigi kapsam (None = kisit yok)
    `requested` istemcinin `?devices=` filtresi (None = filtre yok)

    Kural: istemci filtresi yalnizca DARALTIR. Kapsam disi kodlar sessizce
    dusurulur; bos set "hicbir cihaz" demektir ve broadcaster hicbir mesaj
    gecirmez. Bu fonksiyon ayri durur ki davranisi dogrudan test edilebilsin.
    """
    if allowed is None:
        return requested
    if requested is None:
        return allowed
    return allowed & requested


def _resolve_allowed_device_codes(username: str) -> tuple[bool, set[str] | None]:
    """Kullanicinin telemetri alabilecegi cihaz kodlarini doner.

    Donus: (kullanici_bulundu, izinli_kodlar)
      izinli_kodlar None  -> kisit yok (installer/engineer/ops_manager)
      izinli_kodlar set() -> hicbir cihaz (atanmamis operator)

    Kapsam BAGLANTI ANINDA hesaplanir. Baglanti acikken operatorun alanina
    yeni cihaz eklenirse o cihaz reconnect'e kadar akista gorunmez; periyodik
    `/signals/live` snapshot'i degeri yine gosterdigi icin kabul edilebilir.
    """
    db = SessionLocal()
    try:
        from app.models.device import Device
        from app.models.user import User
        from app.services.scope_service import get_visible_device_ids

        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            return False, set()
        visible_ids = get_visible_device_ids(db, user)
        if visible_ids is None:
            return True, None  # kisit yok
        if not visible_ids:
            return True, set()
        codes = set(
            db.scalars(select(Device.code).where(Device.id.in_(visible_ids))).all()
        )
        return True, codes
    finally:
        db.close()


_ALLOWED_WS_ORIGINS_FALLBACK = ("http://localhost", "http://127.0.0.1")


def _normalize_origin(origin: str) -> str:
    """Origin'i karsilastirma icin normalize et.

    - lowercase scheme + host
    - default portlari (80 http, 443 https) kaldir
    - trailing slash kaldir
    Boylece `http://1.2.3.4` ile `http://1.2.3.4:80/` ayni sayilir.
    """
    from urllib.parse import urlparse

    s = origin.strip().lower().rstrip("/")
    try:
        p = urlparse(s)
    except Exception:  # noqa: BLE001
        return s
    if not p.scheme or not p.hostname:
        return s
    # Port default'sa kaldir
    port = p.port
    if (p.scheme == "http" and port == 80) or (p.scheme == "https" and port == 443):
        return f"{p.scheme}://{p.hostname}"
    if port is None:
        return f"{p.scheme}://{p.hostname}"
    return f"{p.scheme}://{p.hostname}:{port}"


def _is_origin_allowed(origin: str | None, host: str | None = None) -> bool:
    """WebSocket Origin header'i izinli listede mi?

    Sira:
      1) Origin yok → izin ver (curl/postman, non-browser zaten ticket alamaz).
      2) Loopback (localhost / 127.0.0.1) → izin ver (dev).
      3) **Same-origin**: Origin'in host kismi == Host header → izin ver.
         Browser tarayicidan ayni host'tan gelen WS = CSWSH degil per definition.
         Bu sayede kullanici `.env`'e CORS_ORIGINS yazmadan da WS calisir;
         `http://77.83.37.44`, `https://app.example.com` vb. otomatik kabul.
      4) CORS_ORIGINS whitelist'inde tam eslesme → izin ver. (Cross-origin
         senaryolar: mobil app, ayri admin domain'i, vb.)
      5) Hicbiri tutmazsa reddet + diagnostic log.

    CSWSH koruma'nin orijinal amaci: attacker.com sayfasi kullanicinin tarayicisini
    kullanip target backend'e WS acmasin. Same-origin'de attacker.com'dan istek
    cikamaz, dolayisi ile bu kontrol guvenli.
    """
    if not origin:
        return True
    from app.core.config import settings as _s
    from urllib.parse import urlparse

    norm_origin = _normalize_origin(origin)

    # Loopback fallback — dev/staging icin hep izinli.
    for fallback in _ALLOWED_WS_ORIGINS_FALLBACK:
        if norm_origin == _normalize_origin(fallback) or norm_origin.startswith(
            _normalize_origin(fallback) + ":"
        ):
            return True

    # Same-origin: Origin'in host kismi Host header ile ayni mi? Browser
    # cross-site WS'i suspect eden CSWSH koruma BURADA degil — attacker.com
    # tarayicidan WS acsa Origin attacker.com olur, Host bizim host'umuz; not
    # equal. Bizim Host'umuza Origin = bizim Host = same-origin demektir.
    if host:
        try:
            origin_host = urlparse(norm_origin).hostname
            # Host header port iceriyor olabilir (ornek "1.2.3.4:80")
            host_only = host.split(":", 1)[0].strip().lower()
            if origin_host and origin_host == host_only:
                return True
        except Exception:  # noqa: BLE001
            pass

    # CORS_ORIGINS whitelist — ekstra (cross-origin) durumlari icin.
    for allowed in _s.cors_origin_list:
        if allowed.strip() == "*":
            return True
        if _normalize_origin(allowed) == norm_origin:
            return True

    # Eslesme yok — teshis icin neyin neyle karsilastirildigini logla.
    logger.warning(
        "ws_origin_rejected origin=%r normalized=%r host=%r allowed=%r",
        origin,
        norm_origin,
        host,
        [_normalize_origin(a) for a in _s.cors_origin_list],
    )
    return False


@router.websocket("/ws/live-values")
async def live_values_ws(
    websocket: WebSocket,
    ticket: str | None = Query(
        default=None,
        description="WS ticket (POST /auth/ws-ticket ile alinan kisa omurlu bilet — onerilen)",
    ),
    token: str | None = Query(
        default=None,
        description="(LEGACY) JWT access token URL'de. Yeni client'lar ticket kullansin; bu yol nginx access_log'a sizar.",
    ),
    devices: str | None = Query(
        default=None,
        description="(Opsiyonel) Virgulle ayrilmis cihaz kodlari; sadece bu cihazlarin telemetrisi gelir. Bos = hepsi.",
    ),
):
    """Canli telemetry akisi. Authenticate edilmis user kendi scope'undaki
    tum cihazlarin (veya filter'a uygun olanlarin) anlik degerlerini alir.

    Auth: `?ticket=<TICKET>` (yeni, onerilen) veya `?token=<JWT>` (legacy).
    Ticket /auth/ws-ticket'tan alinir, 30sn TTL + tek kullanim.

    Origin guard: CSWSH'i (Cross-Site WebSocket Hijacking) onlemek icin
    Origin header'i CORS whitelist'iyle karsilastirilir. Browser bu header'i
    spoof edemez (servlet kontrolu); non-browser client'lar zaten ticket
    alamadigi icin etkilenmez.
    """
    # Origin check — CSWSH koruma. Same-origin (Origin host == Host header)
    # her zaman izinli; cross-origin CORS_ORIGINS whitelist'ten gecmek zorunda.
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if not _is_origin_allowed(origin, host):
        await websocket.close(code=1008, reason="origin_not_allowed")
        return

    username: str | None = None
    jti: str | None = None
    if ticket:
        # Yeni yol: ticket consume (tek kullanim, 30sn TTL).
        from app.services.auth_service import consume_ws_ticket

        consumed = consume_ws_ticket(ticket)
        if consumed is not None:
            username, jti = consumed
    if username is None and token:
        # Legacy: JWT URL query. Bir sonraki release'te kaldirilacak.
        validated = _validate_token(token)
        if validated is not None:
            username, jti = validated
    if username is None:
        # WebSocket spec: 1008 = policy violation.
        await websocket.close(code=1008, reason="invalid_credentials")
        return

    # Oturum iptali — imza gecerli olsa bile logout/"oturumu at" sonrasi
    # baglanti acilmamali. DB'ye gittigi icin thread'e aliyoruz (event loop
    # senkron sorguyla bloklanmasin).
    if await asyncio.to_thread(_is_session_revoked, jti):
        logger.warning("ws_live_values_revoked_session user=%s", username)
        await websocket.close(code=1008, reason="session_revoked")
        return

    # KAPSAM — sunucu tarafinda hesaplanir. Istemcinin `?devices=` filtresi
    # yalnizca DARALTIR; kapsam disina cikaramaz.
    found, allowed_codes = await asyncio.to_thread(_resolve_allowed_device_codes, username)
    if not found:
        logger.warning("ws_live_values_user_not_found user=%s", username)
        await websocket.close(code=1008, reason="invalid_credentials")
        return

    # Istemci filtresi: ?devices=DEV001,DEV002
    requested_codes: set[str] | None = None
    if devices:
        requested_codes = {c.strip() for c in devices.split(",") if c.strip()}
        if not requested_codes:
            requested_codes = None

    device_codes = _effective_device_filter(allowed_codes, requested_codes)

    await websocket.accept()
    logger.info(
        "ws_live_values_connected user=%s scoped=%s filter_devices=%s",
        username,
        allowed_codes is not None,
        "all" if device_codes is None else len(device_codes),
    )

    loop = asyncio.get_running_loop()
    sub = broadcaster.subscribe(loop, device_codes=device_codes)

    # Heartbeat task: her 30sn ping gonderir; client disconnect olursa
    # send'de exception alip cikariz.
    #
    # Ayni turda oturum iptali de yeniden kontrol edilir: baglanti saatlerce
    # acik kalabiliyor, "oturumu at" aksiyonunun etkili olmasi icin tek
    # handshake kontrolu yeterli degil. Maliyet baglanti basina 30sn'de bir
    # tek indeksli SELECT.
    async def _heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SEC)
                if await asyncio.to_thread(_is_session_revoked, jti):
                    logger.info(
                        "ws_live_values_closing_revoked user=%s", username
                    )
                    await websocket.close(code=1008, reason="session_revoked")
                    return
                await websocket.send_text('{"type":"ping"}')
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            return

    heartbeat_task = asyncio.create_task(_heartbeat())

    # Initial hello: client biliyor olsun bagli. `scoped` alani istemciye
    # filtrenin SUNUCUDA daraltildigini soyler (istedigi cihazlarin bir kismi
    # kapsam disi kalmis olabilir).
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "user": username,
                "scoped": allowed_codes is not None,
                "filter": "all" if device_codes is None else sorted(device_codes),
            }
        )
    except Exception:  # noqa: BLE001
        broadcaster.unsubscribe(sub)
        heartbeat_task.cancel()
        return

    try:
        while True:
            payload: dict[str, Any] = await sub.queue.get()
            # payload zaten _persist_message'in calistirdigi telemetry mesaji
            # (gateway'den gelen TelemetryIn formatti). Frontend'e "type":"telemetry"
            # ile gonder ki client mesaj tipini ayirt edebilsin.
            event = {"type": "telemetry", **payload}
            await websocket.send_text(json.dumps(event, ensure_ascii=False))
    except WebSocketDisconnect:
        logger.info("ws_live_values_disconnected user=%s", username)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws_live_values_error user=%s error=%s", username, exc)
        try:
            await websocket.close(code=1011, reason="server_error")
        except Exception:  # noqa: BLE001
            pass
    finally:
        broadcaster.unsubscribe(sub)
        heartbeat_task.cancel()
