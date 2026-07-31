"""Disk guard davranis testleri.

Guard, tum TTL/tavan hesaplarinin YANLIS olmasi ihtimaline karsi konan son
emniyet subabi. Iki ozelligi kritik ve ikisi de sessizce bozulabilir:

  1. ESIK HESABI — rezerv = max(toplam x yuzde, mutlak taban). Yuzde farkli
     disk boyutlarinda dogru olceklenmeli; mutlak taban cok kucuk diskte
     yuzdenin yetersiz kalmasini onlemeli.
  2. NEYE DOKUNULDUGU — guard yalnizca YENIDEN URETILEBILIR veriyi silmeli.
     Denetim kaydina, lisansa, musterinin analiz verisine (historian)
     dokunursa veri kaybi olur ve bunu kimse fark etmez.
"""

from __future__ import annotations

import types

import pytest

from app.core.config import settings
from app.services import disk_guard

GB = 1024**3


# ------------------------------------------------------------- rezerv hesabi


def test_reserve_scales_with_disk_size(monkeypatch):
    """Yuzde tabanli olmasi farkli disk boyutlarinda tek kod yolu saglar."""
    monkeypatch.setattr(settings, "disk_guard_reserve_percent", 10)
    monkeypatch.setattr(settings, "disk_guard_reserve_min_gb", 5)

    assert disk_guard.reserve_for(500 * GB) == 50 * GB   # saha standardi
    assert disk_guard.reserve_for(1000 * GB) == 100 * GB


def test_absolute_floor_protects_small_disks(monkeypatch):
    """Kucuk diskte %10 yetersiz kalir; mutlak taban devreye girmeli.

    Gerekce: PostgreSQL VACUUM / index yeniden kurma / pg_dump calisma alani
    ister. 32 GB'lik bir kutuda %10 = 3.2 GB, bu isler icin yetmez.
    """
    monkeypatch.setattr(settings, "disk_guard_reserve_percent", 10)
    monkeypatch.setattr(settings, "disk_guard_reserve_min_gb", 5)

    assert disk_guard.reserve_for(32 * GB) == 5 * GB, "mutlak taban uygulanmadi"


# ------------------------------------------------------------- seviye esikleri


@pytest.mark.parametrize(
    "free_gb,beklenen",
    [
        (200, disk_guard.LEVEL_OK),          # 2x rezervin uzerinde
        (100, disk_guard.LEVEL_OK),          # tam 2x rezerv -> hala ok
        (60, disk_guard.LEVEL_WARN),         # rezerv ile 2x arasi
        (50, disk_guard.LEVEL_WARN),         # tam rezerv -> uyari
        (40, disk_guard.LEVEL_AGGRESSIVE),   # rezervin altinda
        (25, disk_guard.LEVEL_AGGRESSIVE),   # tam yari rezerv
        (10, disk_guard.LEVEL_EMERGENCY),    # yari rezervin altinda
    ],
)
def test_level_thresholds(free_gb, beklenen):
    """500 GB disk, 50 GB rezerv uzerinden seviye siniri."""
    assert disk_guard.classify(free_gb * GB, 50 * GB) == beklenen


def test_evaluate_reports_usage(monkeypatch):
    monkeypatch.setattr(settings, "disk_guard_reserve_percent", 10)
    monkeypatch.setattr(settings, "disk_guard_reserve_min_gb", 5)
    monkeypatch.setattr(
        disk_guard.shutil,
        "disk_usage",
        lambda p: types.SimpleNamespace(total=500 * GB, used=460 * GB, free=40 * GB),
    )

    st = disk_guard.evaluate(path="/veri")

    assert st is not None
    assert st.reserve_bytes == 50 * GB
    assert st.level == disk_guard.LEVEL_AGGRESSIVE
    assert 0.91 < st.used_ratio < 0.93


def test_evaluate_returns_none_when_path_unreadable(monkeypatch):
    """Disk okunamazsa guard SESSIZCE devre disi kalmali, patlamamali.

    Guard bir emniyet katmani; kendisi sistemi dusuremez.
    """
    def _boom(_p):
        raise OSError("mount yok")

    monkeypatch.setattr(disk_guard.shutil, "disk_usage", _boom)
    assert disk_guard.evaluate(path="/yok") is None


# ------------------------------------------------------------- mudahale sirasi


def _mock_disk(monkeypatch, free_gb: int, total_gb: int = 500):
    monkeypatch.setattr(settings, "disk_guard_enabled", True)
    monkeypatch.setattr(settings, "disk_guard_reserve_percent", 10)
    monkeypatch.setattr(settings, "disk_guard_reserve_min_gb", 5)
    monkeypatch.setattr(
        disk_guard.shutil,
        "disk_usage",
        lambda p: types.SimpleNamespace(
            total=total_gb * GB, used=(total_gb - free_gb) * GB, free=free_gb * GB
        ),
    )


def _spy(monkeypatch):
    """Mudahale fonksiyonlarini ve olay kaydini izle."""
    calls: list[str] = []
    monkeypatch.setattr(
        disk_guard, "_relieve_aggressive", lambda: (calls.append("aggressive"), [])[1]
    )
    monkeypatch.setattr(
        disk_guard, "_relieve_emergency", lambda: (calls.append("emergency"), [])[1]
    )
    monkeypatch.setattr(disk_guard, "_record", lambda st: calls.append(f"event:{st.level}"))
    return calls


def test_ok_level_does_nothing(monkeypatch):
    """Bol alan varken guard HICBIR SEY silmemeli ve olay kaydi yazmamali."""
    _mock_disk(monkeypatch, free_gb=200)
    calls = _spy(monkeypatch)

    st = disk_guard.tick()

    assert st.level == disk_guard.LEVEL_OK
    assert calls == [], f"ok seviyesinde aksiyon alindi: {calls}"


def test_warn_level_records_but_deletes_nothing(monkeypatch):
    """Uyari seviyesi YALNIZCA haber verir — silme YOK.

    Bu ayrim onemli: erken uyari operatore mudahale sansi verir; o asamada
    veri silmek gereksiz ve geri donusu olmayan bir kayiptir.
    """
    _mock_disk(monkeypatch, free_gb=60)
    calls = _spy(monkeypatch)

    st = disk_guard.tick()

    assert st.level == disk_guard.LEVEL_WARN
    assert "aggressive" not in calls and "emergency" not in calls
    assert "event:warn" in calls


def test_aggressive_shortens_retention_but_not_emergency_actions(monkeypatch):
    _mock_disk(monkeypatch, free_gb=40)
    calls = _spy(monkeypatch)

    st = disk_guard.tick()

    assert st.level == disk_guard.LEVEL_AGGRESSIVE
    assert "aggressive" in calls
    assert "emergency" not in calls, "agresif seviyede yedek/harita silinmemeli"


def test_emergency_runs_both_tiers(monkeypatch):
    _mock_disk(monkeypatch, free_gb=10)
    calls = _spy(monkeypatch)

    st = disk_guard.tick()

    assert st.level == disk_guard.LEVEL_EMERGENCY
    assert "aggressive" in calls and "emergency" in calls
    assert "event:emergency" in calls


def test_disabled_guard_is_inert(monkeypatch):
    _mock_disk(monkeypatch, free_gb=1)
    monkeypatch.setattr(settings, "disk_guard_enabled", False)
    calls = _spy(monkeypatch)

    assert disk_guard.tick() is None
    assert calls == []


# ------------------------------------------------------- neye ASLA dokunulmaz


def test_emergency_never_touches_audit_or_historian():
    """ACIL seviyede bile denetim/analiz verisi silinmemeli.

    Guard'in mudahale kodu yalnizca YENIDEN URETILEBILIR kaynaklara
    dokunmali: harita karo onbellegi (internetten yeniden iner) ve fazla
    yedek dosyalari (en yeni basarili yedek korunur).

    Kaynak metnini kontrol ediyoruz cunku asil risk ileride birinin
    "acil durumda historian'i da budayalim" diye eklemesi — o an veri kaybi
    sessiz olur. Bu test o eklemeyi kirmizi yapar.
    """
    import inspect

    src = inspect.getsource(disk_guard._relieve_emergency)
    yasak = [
        "telemetry_history",   # musterinin analiz verisi
        "system_events",       # denetim izi
        "drop_chunks",         # historian chunk budama
        "remove_retention_policy",
        "license",             # lisans dosyalari
        "alarm_events",
        "fault_events",
    ]
    for kelime in yasak:
        assert kelime not in src, (
            f"disk guard acil mudahalesi '{kelime}' iceriyor — bu veri "
            "yeniden uretilemez, otomatik silinmemeli"
        )


def test_aggressive_only_touches_short_lived_tables():
    """Agresif seviye yalnizca kisa omurlu/yeniden uretilebilir tablolara dokunmali."""
    import inspect

    src = inspect.getsource(disk_guard._relieve_aggressive)
    for kelime in ("telemetry_history", "system_events", "alarm_events", "fault_events"):
        assert kelime not in src, (
            f"agresif mudahale '{kelime}' iceriyor — kalici veri silinmemeli"
        )
    # Beklenen hedefler
    assert "processed_messages" in src or "purge_processed_messages" in src
    assert "purge_telemetry" in src
