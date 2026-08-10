"""Makineye bagli Ed25519 lisans dogrulama ve cihaz kotasi."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.device import Device
from app.schemas.license import LicenseEnvelope, LicenseRequestFile, LicenseStatus


_PRODUCT_ID = "enerjione-grid"
_LICENSE_FILENAME = "license.lic"
_INSTALLATION_ID_FILENAME = "installation-id"
_ADVISORY_LOCK_ID = 1_165_137_741

_PUBLIC_KEYS_PEM: dict[str, bytes] = {
    "e1-license-2026-01": b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAzrHF6yaCnvQH0mrrQXOd1meyV/Jzgn+B0lQLiYM2398=
-----END PUBLIC KEY-----
""",
}


class LicenseCapacityError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def canonical_payload(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _license_dir() -> Path:
    path = Path(settings.license_dir).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError("Lisans dizini hazirlanamadi") from exc
    return path


def _atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "xb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _atomic_create(path: Path, data: bytes) -> None:
    """Dosyayi yalniz yoksa, tamamlanmis icerikle atomik olarak yayinla."""
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "xb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.link(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def get_installation_id() -> str:
    path = _license_dir() / _INSTALLATION_ID_FILENAME
    try:
        value = path.read_text(encoding="ascii").strip().lower()
        return str(uuid.UUID(value))
    except FileNotFoundError:
        value = str(uuid.uuid4())
        try:
            _atomic_create(path, (value + "\n").encode("ascii"))
        except FileExistsError:
            pass
        except OSError as exc:
            raise RuntimeError("Kurulum kimligi yazilamadi") from exc
        try:
            return str(uuid.UUID(path.read_text(encoding="ascii").strip()))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Kurulum kimligi okunamadi") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError("Kurulum kimligi okunamadi") from exc


def _read_windows_machine_guid() -> str:
    import winreg

    access = winreg.KEY_READ
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        access |= winreg.KEY_WOW64_64KEY
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Cryptography",
        0,
        access,
    ) as key:
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
    return str(value)


def _read_machine_id() -> str:
    if sys.platform == "win32":
        raw = _read_windows_machine_guid()
    else:
        candidates = [Path(settings.host_machine_id_path)]
        if settings.app_env.strip().lower() not in ("production", "prod"):
            candidates.extend([Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")])
        raw = ""
        for path in candidates:
            try:
                raw = path.read_text(encoding="ascii").strip()
            except OSError:
                continue
            if raw:
                break
    normalized = "".join(raw.lower().split())
    if len(normalized) < 8:
        raise RuntimeError("Sabit makine kimligi okunamadi")
    return normalized


def get_machine_fingerprint() -> str:
    raw = _read_machine_id()
    return hashlib.sha256(f"{_PRODUCT_ID}\0{raw}".encode("utf-8")).hexdigest()


def create_license_request() -> LicenseRequestFile:
    return LicenseRequestFile(
        schema_version=1,
        product_id=_PRODUCT_ID,
        request_id=str(uuid.uuid4()),
        installation_id=get_installation_id(),
        machine_fingerprint=get_machine_fingerprint(),
        created_at=_utc_iso(),
    )


def _parse_and_verify(data: bytes) -> LicenseEnvelope:
    if len(data) > settings.license_upload_max_bytes:
        raise ValueError("Lisans dosyasi boyut sinirini asiyor")
    try:
        raw = json.loads(data.decode("utf-8"))
        envelope = LicenseEnvelope.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("Lisans dosyasi formati gecersiz") from exc

    public_pem = _PUBLIC_KEYS_PEM.get(envelope.kid)
    if public_pem is None:
        raise ValueError("Lisans imza anahtari taninmiyor")
    try:
        signature = base64.b64decode(envelope.signature, validate=True)
        public_key = serialization.load_pem_public_key(public_pem)
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("Lisans public key tipi gecersiz")
        public_key.verify(signature, canonical_payload(envelope.payload.model_dump(mode="json")))
    except (binascii.Error, InvalidSignature) as exc:
        raise ValueError("Lisans imzasi gecersiz") from exc

    installation_id = get_installation_id()
    if envelope.payload.installation_id != installation_id:
        raise LicenseCapacityError(
            "LICENSE_INSTALLATION_MISMATCH",
            "Lisans bu kuruluma ait degil",
        )
    try:
        fingerprint = get_machine_fingerprint()
    except RuntimeError as exc:
        raise LicenseCapacityError(
            "LICENSE_MACHINE_UNAVAILABLE",
            "Sabit makine kimligi okunamadi",
        ) from exc
    if envelope.payload.machine_fingerprint != fingerprint:
        raise LicenseCapacityError(
            "LICENSE_MACHINE_MISMATCH",
            "Lisans bu makineye ait degil",
        )
    return envelope


def verify_license_bytes(data: bytes) -> LicenseEnvelope:
    return _parse_and_verify(data)


def import_license(data: bytes) -> LicenseEnvelope:
    envelope = _parse_and_verify(data)
    normalized = json.dumps(
        envelope.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8") + b"\n"
    try:
        _atomic_write(_license_dir() / _LICENSE_FILENAME, normalized)
    except OSError as exc:
        raise RuntimeError("Lisans dosyasi yazilamadi") from exc
    # Yeni lisans yazildi — API kilidi bir sonraki istekte ACILSIN, TTL'in
    # dolmasini bekleme (kullanici lisansi yukleyip hemen devam edebilmeli).
    invalidate_enforcement_cache()
    return envelope


def _read_active_license() -> tuple[LicenseEnvelope | None, str, str]:
    path = _license_dir() / _LICENSE_FILENAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, "unlicensed", "LICENSE_REQUIRED"
    except OSError:
        return None, "invalid", "LICENSE_READ_FAILED"
    try:
        return _parse_and_verify(data), "valid", "LICENSE_VALID"
    except LicenseCapacityError as exc:
        if exc.code == "LICENSE_MACHINE_MISMATCH":
            return None, "machine_mismatch", exc.code
        if exc.code == "LICENSE_MACHINE_UNAVAILABLE":
            return None, "machine_unavailable", exc.code
        return None, "invalid", exc.code
    except ValueError:
        return None, "invalid", "LICENSE_INVALID"


# --- API kilidi (enforcement) -------------------------------------------------
#
# Lisanssiz kurulumda API'nin TAMAMEN kapatildigi durumlar. Frontend'deki
# `LICENSE_GATE_STATES` (App.tsx) ile AYNI olmak ZORUNDA — biri degisirse
# digeri de degismeli, yoksa arayuz ile backend farkli seyler soyler.
#
# Neden sadece "unlicensed":
#   invalid / machine_mismatch -> lisans VAR ama bu makinede gecerli degil.
#     Sahada calisan bir SCADA'yi tamamen kapatmak izlemeyi de durdurur;
#     ariza takibi lisans sorunundan daha kritiktir.
#   machine_unavailable -> bizim tarafta depolama/machine-id arizasi; bir
#     lisans durumu DEGIL. Gecici dosya izni hatasi sistemi kapatmamali.
#   kota dolu -> lisans gecerli; sadece yeni cihaz ekleme reddedilir
#     (bkz. lock_and_assert_device_capacity).
ENFORCED_STATES: frozenset[str] = frozenset({"unlicensed"})

# Kilit kontrolu HER istekte calisir; lisans dosyasini okuyup Ed25519 imzasini
# dogrulamak istek basina yapilamayacak kadar pahali. Kisa TTL'li cache +
# lisans yuklendiginde explicit invalidation kullaniyoruz.
_GATE_CACHE_TTL_SEC = float(os.getenv("LICENSE_GATE_CACHE_TTL_SEC", "30"))
_gate_cache: tuple[float, str, str] | None = None
_gate_lock = threading.Lock()


def invalidate_enforcement_cache() -> None:
    """Lisans dosyasi degistiginde cache'i dusur (import sonrasi cagrilir)."""
    global _gate_cache
    with _gate_lock:
        _gate_cache = None


def get_enforcement_state(*, refresh: bool = False) -> tuple[str, str]:
    """(state, reason_code) — SADECE lisans dosyasini okur, DB'ye gitmez.

    `get_license_status`ten farki: cihaz sayimi yok (DB sorgusu yok) ve sonuc
    cache'lenir. Middleware bu fonksiyonu kullanir.
    """
    global _gate_cache
    if not refresh:
        with _gate_lock:
            cached = _gate_cache
        if cached is not None and (time.monotonic() - cached[0]) < _GATE_CACHE_TTL_SEC:
            return cached[1], cached[2]
    try:
        _, state, reason_code = _read_active_license()
    except RuntimeError:
        # Lisans dizini hazirlanamadi — sunucu tarafi arizasi, lisans durumu
        # degil. ENFORCED_STATES icinde olmadigi icin API'yi KILITLEMEZ.
        state, reason_code = "machine_unavailable", "LICENSE_MACHINE_UNAVAILABLE"
    with _gate_lock:
        _gate_cache = (time.monotonic(), state, reason_code)
    return state, reason_code


def is_api_locked() -> bool:
    """Lisanssizlik nedeniyle API kapali mi?"""
    state, _ = get_enforcement_state()
    return state in ENFORCED_STATES


def count_licensed_devices(db: Session) -> int:
    """Lisans kotasindan DUSEN cihaz sayisi.

    Yalnizca FIZIKSEL kayitlar sayilir (`parent_device_id IS NULL`). Bir
    Horstmann Pole Master Kit tek bir donanimdir; kullanicinin gordugu uc
    "set" o donanimin ic bolumlemesidir ve ayri satir tutulmalarinin nedeni
    lisanslama degil, topolojidir (`line_segments.device_id` tekildir).

    Sayilsalardi, tek kit alan musteri kotasindan UC slot yer ve "cihaz
    ekle" butonu sessizce kilitlenirdi — satin aldigi seyle iliskisi olmayan
    bir sinir.
    """
    return int(
        db.scalar(select(func.count(Device.id)).where(Device.parent_device_id.is_(None)))
        or 0
    )


def get_license_status(db: Session, *, device_count: int | None = None) -> LicenseStatus:
    count = (
        int(device_count) if device_count is not None else count_licensed_devices(db)
    )
    # Kurulum kimligi/lisans dizini okunamazsa (permission/bozuk dosya) 500
    # yerine fail-closed kontrollu status don: izleme acik kalir, cihaz ekleme
    # kapanir. UI/device-create bunu duzgun bir hata olarak gosterir.
    try:
        installation_id = get_installation_id()
    except RuntimeError:
        return LicenseStatus(
            state="machine_unavailable",
            reason_code="LICENSE_MACHINE_UNAVAILABLE",
            is_valid=False,
            can_add_device=False,
            quota_state="unavailable",
            installation_id="",
            machine_fingerprint=None,
            device_count=count,
        )
    try:
        fingerprint = get_machine_fingerprint()
    except RuntimeError:
        fingerprint = None
    try:
        envelope, state, reason_code = _read_active_license()
    except RuntimeError:
        return LicenseStatus(
            state="machine_unavailable",
            reason_code="LICENSE_MACHINE_UNAVAILABLE",
            is_valid=False,
            can_add_device=False,
            quota_state="unavailable",
            installation_id=installation_id,
            machine_fingerprint=fingerprint,
            device_count=count,
        )
    if envelope is None:
        return LicenseStatus(
            state=state,
            reason_code=reason_code,
            is_valid=False,
            can_add_device=False,
            quota_state="unavailable",
            installation_id=installation_id,
            machine_fingerprint=fingerprint,
            device_count=count,
        )

    payload = envelope.payload
    remaining = max(payload.device_limit - count, 0)
    quota_state = (
        "over_limit"
        if count > payload.device_limit
        else "full"
        if count == payload.device_limit
        else "available"
    )
    return LicenseStatus(
        state="valid",
        reason_code="LICENSE_VALID" if remaining > 0 else "DEVICE_LIMIT_REACHED",
        is_valid=True,
        can_add_device=remaining > 0,
        quota_state=quota_state,
        installation_id=installation_id,
        machine_fingerprint=fingerprint,
        license_id=payload.license_id,
        customer_code=payload.customer_code,
        customer_name=payload.customer_name,
        project_name=payload.project_name,
        note=payload.note,
        device_limit=payload.device_limit,
        device_count=count,
        remaining=remaining,
        issued_at=payload.issued_at,
    )


def lock_and_assert_device_capacity(db: Session) -> LicenseStatus:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _ADVISORY_LOCK_ID})
    count = count_licensed_devices(db)
    status = get_license_status(db, device_count=count)
    if status.can_add_device:
        return status
    messages = {
        "LICENSE_REQUIRED": "Cihaz eklemek icin lisans yukleyin",
        "LICENSE_MACHINE_MISMATCH": "Lisans bu makineye ait degil",
        "LICENSE_MACHINE_UNAVAILABLE": "Sabit makine kimligi okunamadi",
        "DEVICE_LIMIT_REACHED": "Lisans cihaz limiti doldu",
    }
    raise LicenseCapacityError(
        status.reason_code,
        messages.get(status.reason_code, "Lisans gecersiz; cihaz eklenemez"),
    )
