"""API Key servisi birim testleri (DB'siz)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import api_key_service


def test_token_format() -> None:
    plain, token_hash, prefix = api_key_service.generate_token()
    assert plain.startswith("hsl_pat_")
    assert len(plain) > len("hsl_pat_") + 30
    # SHA-256 hex digest = 64 karakter
    assert len(token_hash) == 64
    assert all(c in "0123456789abcdef" for c in token_hash)
    assert "…" in prefix  # gosterim formati: ilk12+...+son4
    # Plain DB'de hicbir yerde tutulmaz — sadece hash sahibine ait
    assert plain not in token_hash


def test_token_uniqueness() -> None:
    """Iki ardisik token cakismamali."""
    a, _, _ = api_key_service.generate_token()
    b, _, _ = api_key_service.generate_token()
    assert a != b


def test_hash_stable() -> None:
    """Ayni plain icin ayni hash."""
    plain = "hsl_pat_test_value_123"
    assert api_key_service.hash_token(plain) == api_key_service.hash_token(plain)


def test_validate_scopes_default() -> None:
    # Default scope listesi DEFAULT_SCOPES'tan gelir; siralama korunur,
    # validate_scopes None gordugunde direkt birlestirir.
    expected_default = ",".join(api_key_service.DEFAULT_SCOPES)
    assert api_key_service.validate_scopes(None) == expected_default
    assert api_key_service.validate_scopes([]) == expected_default


def test_validate_scopes_reject_unknown() -> None:
    with pytest.raises(ValueError, match="Bilinmeyen scope"):
        api_key_service.validate_scopes(["devices:read", "evil:write"])


def test_validate_scopes_sorted_unique() -> None:
    csv = api_key_service.validate_scopes(["telemetry:read", "devices:read", "devices:read"])
    assert csv == "devices:read,telemetry:read"


def test_is_usable_states() -> None:
    now = datetime.now(timezone.utc)
    # Aktif key
    ok_key = SimpleNamespace(revoked_at=None, is_active=True, expires_at=None)
    assert api_key_service.is_usable(ok_key, now)[0] is True
    # Revoke
    revoked = SimpleNamespace(revoked_at=now, is_active=False, expires_at=None)
    ok, reason = api_key_service.is_usable(revoked, now)
    assert not ok and reason == "revoked"
    # Devre disi
    disabled = SimpleNamespace(revoked_at=None, is_active=False, expires_at=None)
    ok, reason = api_key_service.is_usable(disabled, now)
    assert not ok and reason == "deactivated"
    # Suresi dolmus
    expired = SimpleNamespace(
        revoked_at=None, is_active=True, expires_at=now - timedelta(minutes=1)
    )
    ok, reason = api_key_service.is_usable(expired, now)
    assert not ok and reason == "expired"
    # Gelecekte expires
    not_yet = SimpleNamespace(
        revoked_at=None, is_active=True, expires_at=now + timedelta(hours=1)
    )
    assert api_key_service.is_usable(not_yet, now)[0] is True


def test_ip_allowed() -> None:
    no_restrict = SimpleNamespace(allowed_ips=None)
    assert api_key_service.ip_allowed(no_restrict, "1.2.3.4") is True
    assert api_key_service.ip_allowed(no_restrict, None) is True
    restricted = SimpleNamespace(allowed_ips="1.2.3.4,5.6.7.8")
    assert api_key_service.ip_allowed(restricted, "1.2.3.4") is True
    assert api_key_service.ip_allowed(restricted, "5.6.7.8") is True
    assert api_key_service.ip_allowed(restricted, "9.9.9.9") is False
    assert api_key_service.ip_allowed(restricted, None) is False


def test_has_scope() -> None:
    row = SimpleNamespace(scopes="devices:read,telemetry:read")
    assert api_key_service.has_scope(row, "devices:read") is True
    assert api_key_service.has_scope(row, "telemetry:read") is True
    assert api_key_service.has_scope(row, "system:read") is False
    empty = SimpleNamespace(scopes="")
    assert api_key_service.has_scope(empty, "devices:read") is False
