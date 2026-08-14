"""Genel sorgu (GI) yaniti spontane trafik tarafindan SILINMEMELI.

YASANAN KUSUR (denetim 2026-08-13)
---------------------------------
GI cerceveleri ile spontane telemetri AYNI sinirli kuyruktaydi. `enqueue`
kuyruk dolunca EN ESKIYI atar — cunku spontane bildirimde son deger
gecerlidir. Ama kuyrugun basindaki cerceve cogu zaman bekleyen bir GI
cercevesiydi.

600 cihaz x ~145 IEC104 noktasi = ~87.000 nesnelik bir GI yaniti, saniyede
~1200 spontane telemetri altinda kuyruktan supuruluyordu. En sonda ACT_TERM
gittigi icin SCADA sorgunun BASARIYLA tamamlandigini saniyor ve EKSIK bir
nokta kumesiyle calisiyordu — sessiz yanlis veri.
"""

from __future__ import annotations

import asyncio

import pytest

from iec104_outbound.server import OUTBOX_MAX, _ClientSession


class _SahteWriter:
    def write(self, data):  # noqa: ANN001
        pass

    async def drain(self):
        pass

    def close(self):
        pass


def _oturum() -> _ClientSession:
    return _ClientSession(_SahteWriter(), "test-peer")


@pytest.mark.asyncio
async def test_spontane_tasma_GI_cercevelerini_SILMEZ():
    """Spontane kuyruk tasarken GI kuyrugu DOKUNULMADAN kalmali."""
    s = _oturum()

    # Once GI yaniti kuyruklanir.
    for i in range(50):
        await s.put_gi(f"GI-{i}".encode())

    # Sonra spontane akis kuyrugu KAT KAT tasirir.
    for i in range(OUTBOX_MAX * 3):
        s.enqueue(f"SP-{i}".encode())

    assert s.gi_outbox.qsize() == 50, (
        "GI cerceveleri spontane tasma yuzunden kayboldu — tam da duzeltilen "
        "kusur bu."
    )
    # Spontane tarafta dusme OLMASI beklenir (tasarim geregi).
    assert s.dropped_total > 0
    assert s.outbox.qsize() == OUTBOX_MAX


@pytest.mark.asyncio
async def test_GI_kuyrugu_spontaneden_ONCE_bosaltilir():
    """Drenaj sirasi: once tum GI, sonra spontane.

    Oncelik olmasaydi GI cerceveleri spontane akisin arasina serpistirilir,
    ~87.000 nesnelik bir yanit dakikalarca surer ve master'in GI zaman asimi
    dolardi.
    """
    s = _oturum()
    s.enqueue(b"SP-1")
    await s.put_gi(b"GI-1")
    s.enqueue(b"SP-2")
    await s.put_gi(b"GI-2")

    # `_drain_outbox`in secim mantigi: GI bos degilse ondan al.
    sira = []
    while not (s.gi_outbox.empty() and s.outbox.empty()):
        kaynak = s.gi_outbox if not s.gi_outbox.empty() else s.outbox
        sira.append(kaynak.get_nowait())

    assert sira == [b"GI-1", b"GI-2", b"SP-1", b"SP-2"]


@pytest.mark.asyncio
async def test_veri_var_olayi_her_iki_kuyrukta_da_set_edilir():
    """Yazici gorev tek kuyrukta bloklanamaz; uyandirma olayi ortak.

    Olay set edilmezse drenaj gorevi uyanmaz ve kuyruklar dolu kalirdi —
    yani hicbir sey gonderilmezdi.
    """
    s = _oturum()
    assert not s.veri_var.is_set()

    s.enqueue(b"SP")
    assert s.veri_var.is_set(), "spontane enqueue yazici gorevi uyandirmiyor"

    s.veri_var.clear()
    await s.put_gi(b"GI")
    assert s.veri_var.is_set(), "put_gi yazici gorevi uyandirmiyor"


@pytest.mark.asyncio
async def test_GI_kuyrugu_spontane_kuyruktan_COK_daha_buyuk():
    """~87.000 nesnelik bir GI, 2000'lik spontane tavana sigmaz."""
    s = _oturum()
    assert s.gi_outbox.maxsize >= 87_000 * 2
    assert s.outbox.maxsize == OUTBOX_MAX


@pytest.mark.asyncio
async def test_put_gi_DUSURMEZ_kuyruk_dolunca_bekler():
    """GI yaniti tekrari olmayan bir butun: cerceve atilamaz, beklenir."""
    s = _oturum()
    # Kucuk bir kuyrukla tasma davranisini sina.
    s.gi_outbox = asyncio.Queue(maxsize=2)
    await s.put_gi(b"A")
    await s.put_gi(b"B")

    bekleyen = asyncio.create_task(s.put_gi(b"C"))
    await asyncio.sleep(0)
    assert not bekleyen.done(), "kuyruk doluyken put_gi DUSURDU (beklemeliydi)"

    # Yer acilinca devam etmeli — hicbir cerceve kaybolmaz.
    s.gi_outbox.get_nowait()
    await asyncio.wait_for(bekleyen, timeout=1)
    assert s.gi_outbox.qsize() == 2
