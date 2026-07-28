from __future__ import annotations

import base64
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services import license_service


class _Payload:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self._data)


def _payload(installation_id: str, fingerprint: str, limit: int = 10) -> dict:
    return {
        "schema_version": 1,
        "product_id": "enerjione-grid",
        "license_id": str(uuid.uuid4()),
        "request_id": str(uuid.uuid4()),
        "installation_id": installation_id,
        "machine_fingerprint": fingerprint,
        "customer_code": "TEST-01",
        "customer_name": "Test Musteri",
        "project_name": "Test Proje",
        "note": None,
        "device_limit": limit,
        "issued_at": "2026-07-23T12:00:00Z",
    }


def _envelope(payload: dict, private_key: Ed25519PrivateKey, kid: str = "test-key") -> bytes:
    signature = base64.b64encode(private_key.sign(license_service.canonical_payload(payload))).decode()
    return json.dumps(
        {"schema_version": 1, "kid": kid, "payload": payload, "signature": signature}
    ).encode()


def test_valid_license_and_tamper_rejected(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(license_service.settings, "license_dir", str(tmp_path))
    monkeypatch.setattr(license_service, "get_machine_fingerprint", lambda: "a" * 64)
    installation_id = license_service.get_installation_id()
    monkeypatch.setitem(license_service._PUBLIC_KEYS_PEM, "test-key", public_pem)

    payload = _payload(installation_id, "a" * 64, limit=10)
    data = _envelope(payload, private_key)
    assert license_service.verify_license_bytes(data).payload.device_limit == 10

    tampered = json.loads(data)
    tampered["payload"]["device_limit"] = 1000
    with pytest.raises(ValueError, match="imzasi gecersiz"):
        license_service.verify_license_bytes(json.dumps(tampered).encode())


def test_machine_mismatch_rejected(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(license_service.settings, "license_dir", str(tmp_path))
    monkeypatch.setattr(license_service, "get_machine_fingerprint", lambda: "b" * 64)
    installation_id = license_service.get_installation_id()
    monkeypatch.setitem(license_service._PUBLIC_KEYS_PEM, "test-key", public_pem)
    data = _envelope(_payload(installation_id, "a" * 64), private_key)
    with pytest.raises(license_service.LicenseCapacityError) as exc:
        license_service.verify_license_bytes(data)
    assert exc.value.code == "LICENSE_MACHINE_MISMATCH"


def test_unknown_key_rejected(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(license_service.settings, "license_dir", str(tmp_path))
    monkeypatch.setattr(license_service, "get_machine_fingerprint", lambda: "a" * 64)
    installation_id = license_service.get_installation_id()
    data = _envelope(_payload(installation_id, "a" * 64), private_key, kid="unknown")
    with pytest.raises(ValueError, match="taninmiyor"):
        license_service.verify_license_bytes(data)


def _activate_license(tmp_path, monkeypatch, limit: int) -> None:
    """Test icin gecerli bir lisans uret + import et. license_dir tmp_path olur."""
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(license_service.settings, "license_dir", str(tmp_path))
    monkeypatch.setattr(license_service, "get_machine_fingerprint", lambda: "a" * 64)
    monkeypatch.setitem(license_service._PUBLIC_KEYS_PEM, "test-key", public_pem)
    installation_id = license_service.get_installation_id()
    data = _envelope(_payload(installation_id, "a" * 64, limit=limit), private_key)
    license_service.import_license(data)


def test_installation_id_is_stable_under_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr(license_service.settings, "license_dir", str(tmp_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: license_service.get_installation_id(), range(32)))
    assert len(set(values)) == 1


def test_status_unlicensed(tmp_path, monkeypatch):
    monkeypatch.setattr(license_service.settings, "license_dir", str(tmp_path))
    monkeypatch.setattr(license_service, "get_machine_fingerprint", lambda: "a" * 64)
    status = license_service.get_license_status(None, device_count=0)
    assert status.state == "unlicensed"
    assert status.is_valid is False
    assert status.can_add_device is False
    assert status.reason_code == "LICENSE_REQUIRED"
    assert status.quota_state == "unavailable"


def test_status_available(tmp_path, monkeypatch):
    _activate_license(tmp_path, monkeypatch, limit=2)
    status = license_service.get_license_status(None, device_count=1)
    assert status.state == "valid"
    assert status.is_valid is True
    assert status.can_add_device is True
    assert status.quota_state == "available"
    assert status.remaining == 1
    assert status.device_limit == 2
    assert status.issued_at == "2026-07-23T12:00:00Z"


def test_status_full(tmp_path, monkeypatch):
    _activate_license(tmp_path, monkeypatch, limit=2)
    status = license_service.get_license_status(None, device_count=2)
    # Kota dolu olsa da state "valid" KALIR. Arayuzdeki lisans kilidi
    # (App.tsx LICENSE_GATE_STATES) buna gore karar verir: kotasi dolan
    # sistem calismaya devam eder, lisans sayfasina kilitlenmez.
    assert status.state == "valid"
    assert status.is_valid is True
    assert status.can_add_device is False
    assert status.quota_state == "full"
    assert status.reason_code == "DEVICE_LIMIT_REACHED"
    assert status.remaining == 0


def test_status_over_limit_tolerated(tmp_path, monkeypatch):
    # Mevcut cihaz sayisi limitin uzerinde: cihazlar silinmez, ekleme kapanir.
    _activate_license(tmp_path, monkeypatch, limit=2)
    status = license_service.get_license_status(None, device_count=5)
    assert status.state == "valid"
    assert status.is_valid is True
    assert status.can_add_device is False
    assert status.quota_state == "over_limit"
    assert status.remaining == 0


def test_status_fail_closed_on_storage_error(tmp_path, monkeypatch):
    # Kurulum kimligi okunamazsa 500 degil, kontrollu fail-closed status.
    monkeypatch.setattr(license_service.settings, "license_dir", str(tmp_path))

    def _boom() -> str:
        raise RuntimeError("Kurulum kimligi okunamadi")

    monkeypatch.setattr(license_service, "get_installation_id", _boom)
    status = license_service.get_license_status(None, device_count=3)
    # Sunucu tarafi arizasi; lisans durumu DEGIL. Arayuz kilidi bu state'i
    # kapsam disi birakir, izleme acik kalir.
    assert status.state == "machine_unavailable"
    assert status.is_valid is False
    assert status.can_add_device is False
    assert status.reason_code == "LICENSE_MACHINE_UNAVAILABLE"
    assert status.quota_state == "unavailable"
    assert status.device_count == 3
