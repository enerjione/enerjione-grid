"""Uzaktan bakim izni servisi — ajan dosyalarini okuma ve istek yazma.

Testler tailscale'i HIC calistirmaz; host ajaninin (e1-rad) yazdigi dosyalar
sahte olarak uretilir. Kilitlenen davranislar:

  * Kullanilamazlik sebepleri (dizin yok / ajan hic yazmamis / bayat durum)
  * `remaining_seconds` AJANIN YAZDIGI DEGERE GUVENMEZ, `expires_at`ten
    hesaplanir (ajan 30 sn'de bir yazar, arada deger bayatlar).
  * Son tarih gecmis ama ajan hala "acik" diyorsa `open` DEGISTIRILMEZ;
    `overdue` isaretlenir (yalan soylemek yerine uyarmak).
  * Izin VERME bekleyen istek varken reddedilir, izin GERI ALMA ETMEZ —
    kapatmak acmaktan zor olmamali (regresyon kilidi).
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.system_event import SystemEvent
from app.services import remote_access_service as ras


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _state(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    state = {
        "schema": 1,
        "updated_at": _iso(now),
        "lock_mode": "shields",
        "min_duration_minutes": 15,
        "max_duration_minutes": 1440,
        "tailscale": {
            "installed": True,
            "version": "1.80.0",
            "daemon_running": True,
            "backend_state": "Running",
            "registered": True,
            "hostname": "e1-grid-test-saha",
            "dns_name": "e1-grid-test-saha.tailnet-x.ts.net",
            "ipv4": "100.101.102.103",
            "tags": ["tag:e1-appliance"],
            "key_expiry": None,
            "key_expired": False,
            "ssh_enabled": False,
            "shields_up": True,
        },
        "access": {
            "open": False,
            "verified": True,
            "mismatch": None,
            "session_id": None,
            "granted_by": None,
            "granted_by_role": None,
            "granted_at": None,
            "expires_at": None,
            "duration_minutes": None,
            "reason": None,
            "source": None,
        },
        "last_session": None,
        "reason": None,
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key] = {**state[key], **value}
        else:
            state[key] = value
    return state


@pytest.fixture
def agent(tmp_path, monkeypatch):
    """Izole ajan dizini. `write()` ile state.json tazelenir."""
    monkeypatch.setattr(ras.settings, "remote_access_state_dir", str(tmp_path))
    monkeypatch.setattr(ras.settings, "remote_access_max_minutes", 1440)

    class Agent:
        dir = tmp_path

        @staticmethod
        def write(state: dict | None = None) -> None:
            (tmp_path / ras.STATE_FILE).write_text(
                json.dumps(state if state is not None else _state()),
                encoding="utf-8",
            )

    return Agent


# ------------------------------------------------------------ durum ------
def test_state_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ras.settings, "remote_access_state_dir", str(tmp_path / "yok")
    )
    result = ras.read_status()
    assert result.available is False
    assert result.reason == "state_dir_missing"
    # Kullanilamazlik hata DEGIL: sinirlar yine de dolu gelmeli ki UI
    # butonlari cizebilsin.
    assert result.max_duration_minutes == 1440


def test_agent_never_reported(agent):
    result = ras.read_status()
    assert result.available is False
    assert result.reason == "agent_never_reported"


def test_valid_state_is_parsed(agent):
    agent.write()
    result = ras.read_status()
    assert result.available is True
    assert result.reason is None
    assert result.tailscale.hostname == "e1-grid-test-saha"
    assert result.tailscale.registered is True
    assert result.tailscale.shields_up is True
    assert result.access.open is False
    assert result.lock_mode == "shields"
    assert result.state_age_seconds is not None and result.state_age_seconds < 30


def test_stale_state_is_flagged(agent):
    agent.write()
    old = ras.time.time() - (ras.STALE_STATE_SECONDS + 60)
    os.utime(agent.dir / ras.STATE_FILE, (old, old))
    result = ras.read_status()
    # Bayatlik BLOKLAMAZ (ajan yeniden basliyor olabilir), sadece uyarir.
    assert result.available is True
    assert result.reason == "state_stale"
    assert result.state_age_seconds > ras.STALE_STATE_SECONDS


def test_remaining_seconds_is_recomputed_not_trusted(agent):
    """Ajan kasitli YANLIS bir kalan sure yazsa bile `expires_at` kazanir."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    agent.write(
        _state(
            access={
                "open": True,
                "session_id": "abc",
                "granted_by": "ayse",
                "granted_by_role": "engineer",
                "granted_at": _iso(datetime.now(timezone.utc)),
                "expires_at": _iso(expires),
                "duration_minutes": 60,
                # Ajanin bayat/yanlis degeri — OKUNMAMALI.
                "remaining_seconds": 999999,
            },
            tailscale={"shields_up": False},
        )
    )
    result = ras.read_status()
    assert result.access.open is True
    assert 1700 < result.access.remaining_seconds <= 1800
    assert result.access.overdue is False


def test_overdue_does_not_lie_about_open(agent):
    """Son tarih gecmis ama ajan hala acik diyor -> open korunur, overdue=True."""
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    agent.write(
        _state(
            access={
                "open": True,
                "session_id": "abc",
                "expires_at": _iso(expired),
                "duration_minutes": 60,
            },
            tailscale={"shields_up": False},
        )
    )
    result = ras.read_status()
    assert result.access.open is True, "ajan durmus olabilir; 'kapali' demek yalan olur"
    assert result.access.overdue is True
    assert result.access.remaining_seconds == 0


def test_last_session_is_parsed(agent):
    agent.write(
        _state(
            last_session={
                "session_id": "s1",
                "granted_by": "ayse",
                "granted_by_role": "engineer",
                "granted_at": _iso(datetime.now(timezone.utc) - timedelta(hours=2)),
                "expires_at": _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
                "ended_at": _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
                "end_reason": "expired",
                "duration_minutes": 60,
                "reason": "Ariza #1234",
                "source": "ui",
            }
        )
    )
    result = ras.read_status()
    assert result.last_session is not None
    assert result.last_session.end_reason == "expired"
    assert result.last_session.granted_by_role == "engineer"


# ------------------------------------------------------------ istek ------
def test_grant_rejected_when_key_expired(agent):
    agent.write(_state(tailscale={"key_expired": True}))
    with pytest.raises(ras.RemoteAccessError) as exc:
        ras.request_grant(
            duration_minutes=60, actor_username="ayse", actor_role="engineer"
        )
    assert str(exc.value) == "tailscale_key_expired"
    assert not (agent.dir / ras.REQUEST_FILE).exists()


def test_grant_rejected_when_not_registered(agent):
    agent.write(_state(tailscale={"registered": False, "backend_state": "NeedsLogin"}))
    with pytest.raises(ras.RemoteAccessError) as exc:
        ras.request_grant(
            duration_minutes=60, actor_username="ayse", actor_role="engineer"
        )
    assert str(exc.value) == "tailscale_not_registered"


def test_grant_rejected_above_site_limit(agent, monkeypatch):
    agent.write()
    monkeypatch.setattr(ras.settings, "remote_access_max_minutes", 480)
    assert ras.duration_bounds() == (15, 480)
    with pytest.raises(ras.RemoteAccessError) as exc:
        ras.request_grant(
            duration_minutes=1440, actor_username="ayse", actor_role="engineer"
        )
    assert str(exc.value) == "duration_exceeds_site_limit"


def test_site_limit_cannot_raise_hard_cap(agent, monkeypatch):
    """Ayar dosyasindan bir yil yazilamaz: mutlak tavan 24 saat."""
    agent.write()
    monkeypatch.setattr(ras.settings, "remote_access_max_minutes", 525600)
    assert ras.duration_bounds() == (15, 1440)


def test_grant_writes_request_body(agent):
    agent.write()
    request_id, estimated = ras.request_grant(
        duration_minutes=60,
        actor_username="ayse",
        actor_role="engineer",
        actor_ip="10.20.30.40",
        reason="Ariza #1234",
    )
    path = agent.dir / ras.REQUEST_FILE
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["id"] == request_id
    assert body["action"] == "grant"
    assert body["duration_minutes"] == 60
    assert body["requested_by"] == "ayse"
    assert body["requested_by_role"] == "engineer"
    assert body["requested_ip"] == "10.20.30.40"
    assert body["reason"] == "Ariza #1234"
    assert body["allow_ssh"] is True
    # Zaman alanlari UTC-aware ISO olmali (naive datetime YOK).
    for field in ("created_at", "expires_at"):
        parsed = datetime.fromisoformat(body[field])
        assert parsed.tzinfo is not None
    assert body["expires_at"] == estimated
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_grant_blocked_by_pending_request(agent):
    agent.write()
    ras.request_grant(duration_minutes=60, actor_username="ayse", actor_role="engineer")
    with pytest.raises(ras.RemoteAccessError) as exc:
        ras.request_grant(
            duration_minutes=60, actor_username="ayse", actor_role="engineer"
        )
    assert str(exc.value) == "request_pending"


def test_revoke_overwrites_pending_request(agent):
    """REGRESYON KILIDI: geri alma bir EMNIYET aksiyonudur, kuyruga takilmaz."""
    agent.write()
    grant_id, _ = ras.request_grant(
        duration_minutes=60, actor_username="ayse", actor_role="engineer"
    )
    revoke_id = ras.request_revoke(
        actor_username="ayse", actor_role="engineer", reason="bitti"
    )
    assert revoke_id != grant_id
    body = json.loads((agent.dir / ras.REQUEST_FILE).read_text(encoding="utf-8"))
    assert body["action"] == "revoke"
    assert body["id"] == revoke_id
    assert "duration_minutes" not in body


def test_revoke_fails_only_when_agent_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ras.settings, "remote_access_state_dir", str(tmp_path / "yok")
    )
    with pytest.raises(ras.RemoteAccessError) as exc:
        ras.request_revoke(actor_username="ayse", actor_role="engineer")
    assert str(exc.value) == "state_dir_missing"


def test_agent_max_narrows_reported_limit(agent):
    """Eski ajan surumu daha dusuk tavan bildiriyorsa UI onu gostermeli."""
    agent.write(_state(max_duration_minutes=480))
    assert ras.read_status().max_duration_minutes == 480


# ----------------------------------------------------------- denetim ------
@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _ended_sessions(db) -> list[SystemEvent]:
    return list(
        db.execute(
            select(SystemEvent)
            .where(SystemEvent.event_type == "remote_access_session_ended")
            .order_by(SystemEvent.id)
        ).scalars()
    )


def _session(session_id: str, ended_at: datetime, end_reason: str = "expired") -> dict:
    return {
        "session_id": session_id,
        "granted_by": "ayse",
        "granted_by_role": "engineer",
        "granted_at": _iso(ended_at - timedelta(hours=1)),
        "expires_at": _iso(ended_at),
        "ended_at": _iso(ended_at),
        "end_reason": end_reason,
        "duration_minutes": 60,
        "reason": None,
        "source": "ui",
    }


def test_closed_session_is_written_once_with_real_time(agent, db):
    """Ajanin kapattigi oturum denetime GERCEK zamaniyla ve BIR KEZ girer."""
    ended = datetime.now(timezone.utc) - timedelta(hours=3)
    agent.write(_state(last_session=_session("s1", ended)))

    ras.reconcile_audit(db)
    ras.reconcile_audit(db)  # tekrar cagri hicbir sey yazmamali

    rows = _ended_sessions(db)
    assert len(rows) == 1
    assert rows[0].category == "security"
    # occurred_at: olay backend'de degil host'ta gerceklesti; "simdi" yazmak
    # denetim zaman cizgisini bozardi.
    assert abs((rows[0].created_at - ended.replace(tzinfo=None)).total_seconds()) < 2
    assert json.loads(rows[0].metadata_json)["session_id"] == "s1"


def test_dedup_survives_backwards_clock(agent, db):
    """REGRESYON KILIDI: saat geriye kayarsa olay TEKRAR TEKRAR yazilmamali.

    `occurred_at` kullandigimiz icin `created_at` monoton DEGIL. RTC pili
    bitmis bir cihazda ajan izni `clock_skew` ile kapatir ve `ended_at` 1970
    olur; tekillestirme `created_at`e gore siralasaydi eski satiri "son" sanip
    her durum okumasinda (10 sn) ayni olayi yeniden yazardi.
    """
    agent.write(_state(last_session=_session("s1", datetime.now(timezone.utc))))
    ras.reconcile_audit(db)

    # Saat 1970'e dondu; ajan izni fail-closed kapatti.
    stone_age = datetime(1970, 1, 1, 0, 5, tzinfo=timezone.utc)
    agent.write(_state(last_session=_session("s2", stone_age, "clock_skew")))
    for _ in range(3):
        ras.reconcile_audit(db)

    rows = _ended_sessions(db)
    assert [json.loads(r.metadata_json)["session_id"] for r in rows] == ["s1", "s2"]


def test_failed_apply_is_audited_once(agent, db):
    """"Dugmeye basti ama olmadi" gorunmez kalmamali — ve spam de yapmamali."""
    agent.write()
    at = datetime.now(timezone.utc) - timedelta(minutes=2)
    (agent.dir / ras.STATUS_FILE).write_text(
        json.dumps(
            {
                "schema": 1,
                "request_id": "r1",
                "action": "grant",
                "status": "failed",
                "error": "verify_failed: prefs_unreadable",
                "at": _iso(at),
            }
        ),
        encoding="utf-8",
    )
    ras.reconcile_audit(db)
    ras.reconcile_audit(db)

    rows = list(
        db.execute(
            select(SystemEvent).where(
                SystemEvent.event_type == "remote_access_apply_failed"
            )
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].severity == "error"
    assert json.loads(rows[0].metadata_json)["request_id"] == "r1"
