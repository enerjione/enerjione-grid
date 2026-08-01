"""Teslim edilemeyen deger "gonderildi" sayilmamali (Faz 2-11).

YASANAN ARIZA
-------------
`_drain_buffer()` dedup haritasini (`_last_sent`) gonderimden ONCE
guncelliyordu; POST ise cok sonra atiliyordu. POST patlasa bile deger
"gonderildi" isaretleniyor ve cihaz ayni degeri tekrarladigi surece
`submit()` onu bir daha ASLA buffer'a koymuyordu.

  > 4G uc dakika kopar. `master.permanent_fault` 0 -> 1 olur. Deger drain
  > edilir, `_last_sent=1` yazilir, POST timeout'a duser. 4G geri gelir.
  > Cihaz hala 1 yayinlar; `submit()` `last_val == new_val` gorup buffer'a
  > KOYMAZ. Musterinin webhook'u arizayi HIC gormez — ta ki ariza kalkip
  > deger 0'a donene kadar.

Yani tam da bildirilmesi gereken olay sessizce yutuluyordu. Bu "gecikme"
degil KALICI KAYIP: degisim sinyali bir kez kacirildiginda geri gelmiyor.

DOGRU DAVRANIS
--------------
Isaretleme gonderimden SONRA ve yalnizca BASARIDA. Basarisiz deger
isaretlenmedigi icin cihaz onu tekrar yayinladiginda yeniden buffer'a girer
ve kendiliginden yeniden denenir.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from app.services import outbound_telemetry_batcher as batcher


@pytest.fixture(autouse=True)
def _temiz_durum():
    """Modul duzeyinde global durum var; testler birbirini etkilemesin."""
    with batcher._lock:
        batcher._buffer.clear()
        batcher._last_sent.clear()
    yield
    with batcher._lock:
        batcher._buffer.clear()
        batcher._last_sent.clear()


def _okuma(deger, device="DEV-001", signal="master.permanent_fault") -> dict:
    return {
        "device_code": device,
        "signal_key": signal,
        "value": deger,
        "source_timestamp": "2026-08-01T00:00:00+00:00",
    }


def test_drain_dedup_haritasini_GUNCELLEMIYOR():
    """Asil ariza buydu: drain, gonderim olmadan "gonderildi" diyordu."""
    with batcher._lock:
        batcher._buffer[("DEV-001", "master.permanent_fault")] = _okuma(1)

    okumalar = batcher._drain_buffer()

    assert len(okumalar) == 1
    assert batcher._last_sent == {}, (
        "drain dedup haritasini guncelledi — POST patlasa bile deger "
        "'gonderildi' sayilir ve ariza gecisi kalici olarak kaybolur"
    )


def test_mark_sent_BASARIDA_isaretliyor():
    """Basarili teslimden sonra dedup calismali; aksi halde her turda tekrar
    gonderilir ve webhook gereksiz yere doldurulur."""
    okumalar = [_okuma(1)]
    batcher._mark_sent(okumalar)
    assert batcher._last_sent[("DEV-001", "master.permanent_fault")] == 1


def test_isaretlenmeyen_deger_YENIDEN_tamponlaniyor():
    """Kurtarma yolu: basarisiz deger, cihaz tekrarladiginda geri gelmeli."""
    # Gonderim basarisiz kabul edilip isaretlenmedi.
    batcher._drain_buffer()
    assert batcher._last_sent == {}

    # Cihaz ayni degeri tekrar yayinliyor.
    batcher.submit(_okuma(1))

    with batcher._lock:
        assert ("DEV-001", "master.permanent_fault") in batcher._buffer, (
            "teslim edilemeyen deger yeniden tamponlanmadi — ariza gecisi "
            "webhook'a HIC gitmez"
        )


def test_isaretlenen_deger_TEKRAR_tamponlanmiyor():
    """Dedup korunmali: basarili gonderimden sonra ayni deger susmali."""
    batcher._mark_sent([_okuma(1)])
    batcher.submit(_okuma(1))

    with batcher._lock:
        assert ("DEV-001", "master.permanent_fault") not in batcher._buffer, (
            "dedup calismiyor — ayni deger her telemetride tekrar gonderilir"
        )


def test_deger_DEGISINCE_dedup_gecmiyor():
    """Asil is: degisim her zaman gitmeli."""
    batcher._mark_sent([_okuma(0)])
    batcher.submit(_okuma(1))

    with batcher._lock:
        assert ("DEV-001", "master.permanent_fault") in batcher._buffer


# ---------------------------------------------------------------------------
# `_flush_once` DAVRANIS testleri
#
# Ilk yazdigim test "`_mark_sent` bir `if` dalinin icinde mi" diye bakiyordu
# ve YETERSIZDI: mutasyon olarak kosulu `if True:` yapinca test yine GECTI.
# Yani asil arizayi (basarisiz gonderimin 'gonderildi' sayilmasi) yakalamazdi.
# Asagidakiler gercek akisi surer.
# ---------------------------------------------------------------------------


class _SahteHedef:
    id = 1
    name = "webhook"
    protocol = "rest"
    event_filter = "all"
    is_active = True
    endpoint = "http://ornek/webhook"
    auth_header = None
    auth_token = None


class _SahteDB:
    def scalars(self, _stmt):
        class _R:
            def all(self_inner):
                return [_SahteHedef()]

        return _R()

    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


@pytest.fixture()
def _sahte_ortam(monkeypatch):
    """`_flush_once`'i DB ve ag olmadan kosturulabilir kil."""
    monkeypatch.setattr(batcher, "SessionLocal", lambda: _SahteDB())
    monkeypatch.setattr(batcher, "record_event", lambda *a, **k: None)
    from app.services import outbound_dispatch_service as ods

    monkeypatch.setattr(ods, "_record_delivery", lambda *a, **k: None)


def test_flush_GONDERIM_PATLARSA_isaretlemiyor(_sahte_ortam, monkeypatch):
    """ASIL ARIZA: POST patlayinca deger 'gonderildi' sayiliyordu.

    Isaretlenirse cihaz ayni degeri tekrarladigi surece bir daha buffer'a
    girmez ve ariza gecisi webhook'a HIC gitmez.
    """
    def _patla(target, payload):
        raise TimeoutError("4G koptu")

    monkeypatch.setattr(batcher, "_send_rest_batch", _patla)
    batcher.submit(_okuma(1))

    batcher._flush_once()

    assert batcher._last_sent == {}, (
        "gonderim patladi ama deger 'gonderildi' isaretlendi — ariza gecisi "
        "kalici olarak kaybolur"
    )

    # Kurtarma: cihaz ayni degeri tekrarlayinca yeniden tamponlanmali.
    batcher.submit(_okuma(1))
    with batcher._lock:
        assert ("DEV-001", "master.permanent_fault") in batcher._buffer


def test_flush_BASARIDA_isaretliyor(_sahte_ortam, monkeypatch):
    """Basarili gonderimden sonra dedup calismali; aksi halde webhook her
    turda ayni degerle doldurulur."""
    monkeypatch.setattr(batcher, "_send_rest_batch", lambda target, payload: None)
    batcher.submit(_okuma(1))

    batcher._flush_once()

    assert batcher._last_sent.get(("DEV-001", "master.permanent_fault")) == 1

    # Ayni deger artik tamponlanmamali.
    batcher.submit(_okuma(1))
    with batcher._lock:
        assert ("DEV-001", "master.permanent_fault") not in batcher._buffer


def test_flush_HEDEF_YOKKEN_isaretliyor(monkeypatch):
    """Hedef yoksa bu bir basarisizlik DEGIL.

    Isaretlemezsek ayni degerler her turda bosuna yeniden tamponlanir.
    (Basarisiz GONDERIM ile karistirilmamali.)
    """
    class _BosDB(_SahteDB):
        def scalars(self, _stmt):
            class _R:
                def all(self_inner):
                    return []

            return _R()

    monkeypatch.setattr(batcher, "SessionLocal", lambda: _BosDB())
    batcher.submit(_okuma(1))

    batcher._flush_once()

    assert batcher._last_sent.get(("DEV-001", "master.permanent_fault")) == 1
