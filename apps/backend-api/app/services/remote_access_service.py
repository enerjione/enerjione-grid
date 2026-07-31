"""Uzaktan bakim izni servisi — host ajani (e1-rad) ile dosya koprusu.

Mimari (network_service / gateway_agent_service ile BIREBIR ayni desen):

    UI  --HTTP-->  backend (container, uid 10001)
                       |  request.json yazar  (TEK yetki)
                       v
          /var/lib/e1-grid/remote/   <-- bind mount (root:10001, 0770)
                       ^
                       |  state.json / status.json yazar, istegi uygular
          e1-rad (host, root, systemd path unit + 30 sn timer)

Backend tailscale'i HICBIR ZAMAN calistirmaz. Ajan gelen istegi kendi
kurallariyla yeniden dogrular ve son tarihi KENDI saatinden hesaplar.

NEDEN YENI DB MODELI YOK
------------------------
* Anlik durum: zorlayici host ajanidir, dolayisiyla gercek de oradadir
  (state.json). DB'ye yazilan bir son tarih yalnizca "tavsiye" olurdu ve
  backend/DB kapaliyken sureyi doldurmazdi — iki kaynakli gercek (split-brain).
* Denetim izi: mevcut `system_events` tablosu yeterli. "Gecmis izinler"
  sorusu `GET /events?category=security` ile zaten cevaplaniyor.
Bu yuzden `app/models/` ve `alembic_migrations/` HIC DEGISMEDI.

Ajan kurulu degilse (VPS, tailscale kurulmamis kurulum) fonksiyonlar hata
firlatmaz; `read_status()` available=False + sebep doner ve UI bunu "bu
kurulumda uzaktan bakim modulu yok" olarak gosterir.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system_event import SystemEvent
from app.schemas.remote_access import (
    RemoteAccessApplyStatus,
    RemoteAccessGrant,
    RemoteAccessSession,
    RemoteAccessStatus,
    TailscaleInfo,
)
from app.services.event_service import record_event

STATE_FILE = "state.json"
STATUS_FILE = "status.json"
REQUEST_FILE = "request.json"

# state.json bu suredan eskiyse ajan durmus/timer kapali demektir. Ajan 30
# sn'de bir yazar. UYARI DEGERI YUKSEK: timer durmussa acik bir izin
# KENDILIGINDEN KAPANMAZ.
STALE_STATE_SECONDS = 180

# Semadaki mutlak tavan ile AYNI olmali (schemas/remote_access.py) ve ajandaki
# MAX_MINUTES ile de ayni. Uc yerde de bagimsiz uygulanir; en dusugu kazanir.
HARD_MIN_MINUTES = 15
HARD_MAX_MINUTES = 1440

# Denetim uzlastirmasi tek yazici olsun (deps.py `_last_seen_lock` idiomu).
_reconcile_lock = threading.Lock()


class RemoteAccessError(Exception):
    """Istek kabul edilemedi. Mesaj = makine-okunur HATA KODU (i18n frontend'de)."""


def state_dir() -> Path:
    return Path(settings.remote_access_state_dir)


def _read_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError, PermissionError):
        return None


def _parse_iso(value: str | None) -> datetime | None:
    """Ajanin yazdigi ISO zamanini UTC-aware datetime'a cevir."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Ajan her zaman offset yaziyor; yine de naive gelirse UTC kabul et —
    # naive datetime ile aritmetik TypeError verirdi.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def availability() -> tuple[bool, str | None]:
    """(kullanilabilir_mi, sebep). Sebep sadece kapali durumda dolu."""
    directory = state_dir()
    if not directory.is_dir():
        return False, "state_dir_missing"
    if not os.access(directory, os.W_OK):
        return False, "state_dir_not_writable"
    if not (directory / STATE_FILE).exists():
        # Dizin var ama ajan hic rapor yazmamis — setup-remote-access.sh
        # calistirilmamis veya e1-rad-report.timer kapali.
        return False, "agent_never_reported"
    return True, None


def has_pending_request() -> bool:
    return (state_dir() / REQUEST_FILE).exists()


def duration_bounds() -> tuple[int, int]:
    """(alt_sinir, ust_sinir) dakika.

    Site ayari (`remote_access_max_minutes`) tavani yalnizca ASAGI cekebilir;
    yukari cikaramaz — ayar dosyasindan bir yil yazilabilmesi ozelligi
    anlamsizlastirirdi.
    """
    site_max = int(getattr(settings, "remote_access_max_minutes", HARD_MAX_MINUTES))
    upper = max(HARD_MIN_MINUTES, min(HARD_MAX_MINUTES, site_max))
    return HARD_MIN_MINUTES, upper


def read_status() -> RemoteAccessStatus:
    lower, upper = duration_bounds()
    available, reason = availability()
    if not available:
        return RemoteAccessStatus(
            available=False,
            reason=reason,
            min_duration_minutes=lower,
            max_duration_minutes=upper,
        )

    raw = _read_json(state_dir() / STATE_FILE) or {}

    ts_raw = raw.get("tailscale")
    tailscale = TailscaleInfo(**ts_raw) if isinstance(ts_raw, dict) else TailscaleInfo()

    access_raw = raw.get("access") if isinstance(raw.get("access"), dict) else {}
    access = RemoteAccessGrant(**access_raw)

    # remaining_seconds AJANDAN OKUNMAZ, burada hesaplanir: ajan 30 sn'de bir
    # rapor yazar, arada deger bayatlar ve UI geri sayimi tutmaz. Container
    # ile host AYNI cekirdek saatini paylasir; saat kaymasi yok.
    now = datetime.now(timezone.utc)
    expires = _parse_iso(access.expires_at)
    if expires is not None:
        access.remaining_seconds = int(max(0.0, (expires - now).total_seconds()))
        # `open` DEGISTIRILMEZ: son tarih gecti diye "kapali" demek yalan
        # olurdu — ajan durmus ve tunel hala acik olabilir.
        access.overdue = bool(access.open and expires <= now)
    elif access.open:
        access.remaining_seconds = None

    session_raw = raw.get("last_session")
    last_session = (
        RemoteAccessSession(**session_raw) if isinstance(session_raw, dict) else None
    )

    status_raw = _read_json(state_dir() / STATUS_FILE)
    last_apply = RemoteAccessApplyStatus(**status_raw) if status_raw else None

    age: float | None = None
    try:
        age = max(0.0, time.time() - (state_dir() / STATE_FILE).stat().st_mtime)
    except OSError:
        age = None

    # Ajan kendi tavanini bildiriyorsa onunla da kesistir (ajan surumu eskiyse
    # backend'in izin verdigi sureyi kabul etmeyebilir).
    agent_max = raw.get("max_duration_minutes")
    if isinstance(agent_max, int) and HARD_MIN_MINUTES <= agent_max <= HARD_MAX_MINUTES:
        upper = min(upper, agent_max)

    return RemoteAccessStatus(
        available=True,
        reason="state_stale" if (age is not None and age > STALE_STATE_SECONDS) else None,
        agent_reason=raw.get("reason"),
        updated_at=raw.get("updated_at"),
        state_age_seconds=age,
        lock_mode=raw.get("lock_mode"),
        tailscale=tailscale,
        access=access,
        last_session=last_session,
        pending=has_pending_request(),
        last_apply=last_apply,
        min_duration_minutes=lower,
        max_duration_minutes=upper,
    )


# ---------------------------------------------------------------- istek ---
def _write_request(body: dict, *, allow_overwrite: bool) -> str:
    """request.json'i atomik yaz (tmp + rename) ve request id dondur.

    `allow_overwrite=False` (izin verme): bekleyen istek varsa reddedilir —
    ust uste istek hangisinin gecerli oldugunu belirsizlestirir.
    `allow_overwrite=True` (geri alma): bekleyen istegi KOSULSUZ ezer. Geri
    alma bir EMNIYET aksiyonudur; ezilen sey henuz uygulanmamis bir "ac"
    istegidir ve onu iptal etmek zaten istenen sonuctur.

    Dosya izni 0600: sir icermez ama gereksiz genis izin de vermeyiz.
    """
    available, reason = availability()
    if not available:
        raise RemoteAccessError(reason or "unavailable")
    if not allow_overwrite and has_pending_request():
        raise RemoteAccessError("request_pending")

    target = state_dir() / REQUEST_FILE
    tmp = state_dir() / f"{REQUEST_FILE}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RemoteAccessError(f"write_failed: {exc}") from exc
    return str(body["id"])


def _base_request(
    action: str,
    actor_username: str,
    actor_role: str | None,
    actor_ip: str | None,
    reason: str | None,
) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "action": action,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_by": actor_username,
        "requested_by_role": actor_role,
        "requested_ip": actor_ip,
        "reason": reason,
    }


def request_grant(
    *,
    duration_minutes: int,
    actor_username: str,
    actor_role: str | None,
    actor_ip: str | None = None,
    reason: str | None = None,
    allow_ssh: bool = True,
) -> tuple[str, str]:
    """Sureli izin istegini kuyrukla. (request_id, tahmini_expires_at) doner.

    On kosullar burada ERKEN UYARI icin kontrol edilir; nihai otorite ajandir.
    "Dugmeye bastim, hicbir sey olmadi" sessiz basarisizligini onlemek icin
    tailscale tarafi kullanilamaz durumdaysa istek hic yazilmaz.
    """
    available, avail_reason = availability()
    if not available:
        raise RemoteAccessError(avail_reason or "unavailable")

    current = read_status()
    if not current.tailscale.installed:
        raise RemoteAccessError("tailscale_not_installed")
    if not current.tailscale.daemon_running:
        raise RemoteAccessError("daemon_not_running")
    if not current.tailscale.registered:
        # Otomatik katilma DENENMEZ: ajanda authkey yoktur ve sessizce
        # tailnet'e katilmak yanlis olurdu.
        raise RemoteAccessError("tailscale_not_registered")
    if current.tailscale.key_expired:
        # Kalkani indirmek fayda etmez; dugum tailnet'ten dusmus.
        raise RemoteAccessError("tailscale_key_expired")

    lower, upper = duration_bounds()
    if duration_minutes < lower:
        raise RemoteAccessError("duration_below_minimum")
    if duration_minutes > upper:
        raise RemoteAccessError("duration_exceeds_site_limit")

    body = _base_request("grant", actor_username, actor_role, actor_ip, reason)
    body["duration_minutes"] = int(duration_minutes)
    body["allow_ssh"] = bool(allow_ssh)
    # Bilgi amaclidir: ajan KENDI saatinden hesaplar ve kendi tavaniyla kirpar.
    estimated = (
        datetime.now(timezone.utc) + timedelta(minutes=int(duration_minutes))
    ).isoformat(timespec="seconds")
    body["expires_at"] = estimated

    request_id = _write_request(body, allow_overwrite=False)
    return request_id, estimated


def request_revoke(
    *,
    actor_username: str,
    actor_role: str | None,
    actor_ip: str | None = None,
    reason: str | None = None,
) -> str:
    """Izni kapat. Bekleyen istek OLSA BILE uzerine yazar.

    `request_pending` ile 409 donmek musteriyi "kapatamaz" hale getirirdi;
    kapatmak acmaktan zor olmamali. Kapali olsa bile istek yazilir: state.json
    bayatsa gercekten kapali oldugu boylece dogrulanir.
    """
    body = _base_request("revoke", actor_username, actor_role, actor_ip, reason)
    return _write_request(body, allow_overwrite=True)


# ----------------------------------------------------------- denetim ------
def _last_event_metadata(db: Session, event_type: str) -> dict:
    """Verilen turdeki EN SON YAZILAN olayin metadata sozlugu (yoksa bos).

    Siralama `created_at` DEGIL `id`: bu olaylari `occurred_at` ile, yani
    ajanin bildirdigi GERCEK zamanla yaziyoruz, dolayisiyla `created_at`
    ARTIK MONOTON DEGIL. Saat geriye kayan bir cihazda (RTC pili bitmis mini
    PC — ajanin `clock_skew` sebebiyle izni kapattigi senaryonun ta kendisi)
    yeni satir 1970 tarihiyle yazilir ve `created_at desc` sorgusu eski satiri
    "son" sanip her durum okumasinda ayni olayi TEKRAR yazardi (UI 10 sn'de
    bir yokluyor). `id` ekleme sirasini verir ve tekillestirmenin sordugu soru
    tam olarak budur: "bunu daha once yazdik mi?"
    """
    row = db.execute(
        select(SystemEvent)
        .where(SystemEvent.event_type == event_type)
        .order_by(SystemEvent.id.desc())
        .limit(1)
    ).scalars().first()
    if row is None or not row.metadata_json:
        return {}
    try:
        data = json.loads(row.metadata_json)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def reconcile_audit(db: Session) -> None:
    """Ajanin KENDI kapattigi oturumlari denetime tasi.

    Neden gerekli: izin VERME/GERI ALMA HTTP yolundan senkron kaydediliyor
    (riza kaydi), ama "sure doldu ve kapandi" olayini yapan taraf host
    ajanidir. Onu duyan tek taraf backend'dir; ayri bir zamanlayici bu kadar
    seyrek bir olay icin fazla agir olurdu.

    Idempotent: son yazilan olayin `session_id`si ajanin bildirdigiyle ayniysa
    hicbir sey yapmaz. Hata yutulur — durum okuma akisini bloklamamali.
    """
    if not _reconcile_lock.acquire(blocking=False):
        return  # baska bir istek zaten uzlastiriyor
    try:
        available, _reason = availability()
        if not available:
            return
        raw = _read_json(state_dir() / STATE_FILE) or {}

        session = raw.get("last_session")
        if isinstance(session, dict) and session.get("session_id"):
            previous = _last_event_metadata(db, "remote_access_session_ended")
            if previous.get("session_id") != session.get("session_id"):
                end_reason = str(session.get("end_reason") or "expired")
                record_event(
                    db,
                    category="security",
                    event_type="remote_access_session_ended",
                    severity="info",
                    actor_username=session.get("granted_by"),
                    message=(
                        "Uzaktan bakim izni kapandi "
                        f"({end_reason}); veren: {session.get('granted_by') or '-'}"
                    ),
                    metadata={
                        "session_id": session.get("session_id"),
                        "granted_by": session.get("granted_by"),
                        "granted_by_role": session.get("granted_by_role"),
                        "granted_at": session.get("granted_at"),
                        "expires_at": session.get("expires_at"),
                        "ended_at": session.get("ended_at"),
                        "end_reason": end_reason,
                        "duration_minutes": session.get("duration_minutes"),
                        "reason": session.get("reason"),
                        "source": session.get("source"),
                    },
                    i18n_key="remote_access_session_ended",
                    i18n_params={"reason": end_reason},
                    # GERCEK zamani yaz: olay backend disinda gerceklesti,
                    # "simdi" yazmak denetim zaman cizgisini bozardi.
                    occurred_at=_parse_iso(session.get("ended_at")),
                )
                db.commit()

        # "Musteri dugmeye basti ama olmadi" gorunmez kalmamali.
        status_raw = _read_json(state_dir() / STATUS_FILE) or {}
        if status_raw.get("status") == "failed" and status_raw.get("request_id"):
            previous = _last_event_metadata(db, "remote_access_apply_failed")
            if previous.get("request_id") != status_raw.get("request_id"):
                record_event(
                    db,
                    category="security",
                    event_type="remote_access_apply_failed",
                    severity="error",
                    message=(
                        "Uzaktan bakim istegi uygulanamadi: "
                        f"{str(status_raw.get('error') or '')[:200]}"
                    ),
                    metadata={
                        "request_id": status_raw.get("request_id"),
                        "action": status_raw.get("action"),
                        "error": str(status_raw.get("error") or "")[:300],
                        "at": status_raw.get("at"),
                    },
                    i18n_key="remote_access_apply_failed",
                    i18n_params={"error": str(status_raw.get("error") or "")[:200]},
                    occurred_at=_parse_iso(status_raw.get("at")),
                )
                db.commit()
    except Exception:  # noqa: BLE001
        # Denetim uzlastirmasi durum okumasini ASLA bozmamali.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        _reconcile_lock.release()
