"""Yedek dayanikliligi `/health` uzerinden GORUNUR olmali.

YASANAN ARIZA (saha, 2026-08-19 — 192.168.2.99)
-----------------------------------------------
`backup_schedule` satiri `enabled=false, last_run_at=NULL` durumdaydi: o
kurulumda TEK BIR zamanlanmis yedek bile alinmamisti. Model varsayilani
`True` olmasina ragmen — cunku varsayilan YALNIZCA satir ILK KEZ
yaratilirken okunur, mevcut satira dokunmaz. Yani iyilestirme yukseltilmis
sahalara HIC ulasmadi.

Gorunurluk de yoktu: `/health` "ok" diyor, `backup_scheduler` arka plan is
listesinde KAYITLI gorunuyordu (kayitli ama her turda hemen donuyordu).
Operator "yedekleme calisiyor" saniyordu; gercek RPO sinirsiz buyuyordu.

SOZLESME
--------
* Operatorun bilincli `enabled=false` karari OTOMATIK OLARAK DEGISTIRILMEZ.
* Ama sessiz de kalinmaz: durum `degraded` olur.
* 503 DEGIL — sistem calismaya devam eder (saha esnekligi korunur).
"""

from __future__ import annotations

import pytest

from app.api.health import (
    _BACKUP_DISABLED_REASON,
    _BACKUP_NEVER_RAN_REASON,
    _backup_durability,
)


class _SahteSchedule:
    def __init__(self, enabled: bool, last_run_at):  # noqa: ANN001
        self.enabled = enabled
        self.last_run_at = last_run_at
        self.interval_hours = 24
        self.retention_count = 7


class _SahteDb:
    def __init__(self, sonuc):  # noqa: ANN001
        self._sonuc = sonuc

    def get(self, model, pk):  # noqa: ANN001, ARG002
        if isinstance(self._sonuc, Exception):
            raise self._sonuc
        return self._sonuc


def test_B05a_acik_program_saglikli():
    from datetime import datetime, timezone

    d = _backup_durability(_SahteDb(_SahteSchedule(True, datetime.now(timezone.utc))))
    assert d["ok"] is True
    assert d["enabled"] is True
    assert "error" not in d


def test_B05b_kapali_program_degraded_sebebi_uretir():
    from datetime import datetime, timezone

    d = _backup_durability(_SahteDb(_SahteSchedule(False, datetime.now(timezone.utc))))
    assert d["ok"] is False
    assert d["measured"] is True
    assert d["never_ran"] is False
    assert "error" in d


def test_B05c_kapali_VE_hic_kosmamis_ayrica_isaretlenir():
    """Iki durum AYRI: 'operator kapatti' ile 'hic calismamis' farkli sorunlar.

    Ikincisi neredeyse her zaman yukseltmeden gelen sessiz bir bosluktur.
    """
    d = _backup_durability(_SahteDb(_SahteSchedule(False, None)))
    assert d["ok"] is False
    assert d["never_ran"] is True
    assert "hic zamanlanmis" in d["error"]


def test_B05d_olculemezse_degraded_YAPMAZ():
    """Bilgi yoklugu ariza degildir — yanlis alarm uretmemeli."""
    assert _backup_durability(_SahteDb(RuntimeError("tablo yok")))["ok"] is True
    assert _backup_durability(_SahteDb(None))["ok"] is True
    assert _backup_durability(_SahteDb(None))["measured"] is False


@pytest.mark.parametrize(
    "enabled,hic_kosmadi,beklenen",
    [
        (True, False, []),
        (False, False, [_BACKUP_DISABLED_REASON]),
        (False, True, [_BACKUP_DISABLED_REASON, _BACKUP_NEVER_RAN_REASON]),
    ],
)
def test_B05e_health_govdesi_sebepleri_uretir(monkeypatch, enabled, hic_kosmadi, beklenen):  # noqa: ANN001
    """GERCEK `_build_health_body` cagrilir — mantik testte KOPYALANMAZ.

    Kopyalasaydik, sebep uretimi `_build_health_body`'den tamamen kaldirilsa
    bile bu test yesil kalirdi (B05 mutasyonu kacardi).
    """
    from app.api import health as h

    # Diger bagimliliklar saglikli sayilsin: olculen sey yalnizca yedek.
    monkeypatch.setattr(h, "_probe_db", lambda db: (True, None, 1.0))
    monkeypatch.setattr(h, "_probe_jetstream", lambda: (True, None))
    monkeypatch.setattr(h, "_probe_tcp", lambda url, **kw: (True, None, 1.0))
    monkeypatch.setattr(
        h, "_backup_durability",
        lambda db: {
            "ok": enabled, "measured": True, "enabled": enabled,
            "never_ran": hic_kosmadi,
        },
    )

    from app.db import schema_guard

    monkeypatch.setattr(schema_guard, "hazir_mi", lambda: (True, ""))

    govde, kod = h._build_health_body(_SahteDb(_SahteSchedule(enabled, None)))

    assert kod == 200, "yedek durumu HTTP kodunu degistirmemeli"
    yedek_sebepleri = [
        s for s in govde["degraded_reasons"]
        if s in (_BACKUP_DISABLED_REASON, _BACKUP_NEVER_RAN_REASON)
    ]
    assert yedek_sebepleri == beklenen
    if beklenen:
        assert govde["status"] == "degraded"


def test_B05f_kapali_program_503_URETMEZ():
    """Yedek kapali olmak sistemi saglksiz yapmaz — yalnizca degraded.

    Kritik listesi (503 uretenler) yalnizca `database` ve `schema`
    icermelidir; buraya `backup_schedule` sizmasi sahayi bosu bosuna
    karartirdi.
    """
    import inspect

    from app.api import health as h

    kaynak = inspect.getsource(h._build_health_body)
    kritik_satiri = [s for s in kaynak.splitlines() if "kritik = [" in s]
    assert kritik_satiri, "kritik listesi bulunamadi"
    govde = kaynak.split("kritik = [", 1)[1].split("]", 1)[0]
    assert "backup_schedule" not in govde, (
        "yedek programi 503 uretiyor — bilincli kapatma sahayi karartmamali"
    )
