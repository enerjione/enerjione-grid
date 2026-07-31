"""IEC 104: giden trafik SINIRLI olmali.

YASANAN DURUM
-------------
Her deger degisiminde, her oturum icin bir `asyncio.create_task(_send_i(...))`
yaratiliyordu — sinirsiz ve referans tutulmadan.

    for session in list(self._sessions):
        if session.started:
            asyncio.create_task(self._send_i(session, asdu))

SCADA istemcisi yavasladiginda `writer.drain()` bloklanir; her yeni deger
degisimi BIR GOREV DAHA yaratir ve hepsi ayni yazma kilidinde kuyruga girer.
600 cihaz olceginde saniyede binlerce gorev demek — bellek OOM'a kadar buyur.
Ustelik referanssiz gorevler cop toplayici tarafindan yarida kesilebiliyordu
(CPython'un bilinen tuzagi).

Ikinci hata ayni satirdaydi: `_send_i` sira numarasini (`ns`) yazma kilidinin
DISINDA artiriyordu. Iki eszamanli gorev cerceveleri ns sirasindan FARKLI
gonderebiliyordu; IEC 104 master'i sira atlamasi gorunce baglantiyi dusurur.

COZUM
-----
Oturum basina SINIRLI kuyruk + TEK yazici gorev. Kuyruk dolarsa EN ESKI
bildirim dusurulur (spontane bildirimde son deger gecerlidir; yeni geleni
atmak guncel degeri atip bayat degeri gondermek olurdu). ns artik tek yerde,
gonderim aninda atanir.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.iec104.server import (
    MAX_SESSIONS,
    OUTBOX_MAX,
    _ClientSession,
)


class _SahteWriter:
    def __init__(self) -> None:
        self.yazilan: list[bytes] = []
        self.kapali = False

    def write(self, data: bytes) -> None:
        self.yazilan.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.kapali = True

    def get_extra_info(self, _ad):  # pragma: no cover
        return ("127.0.0.1", 1234)


def _oturum() -> _ClientSession:
    return _ClientSession(writer=_SahteWriter(), peer="127.0.0.1:1234")


# ------------------------------------------------------------ kuyruk siniri


def test_kuyruk_sinirsiz_buyumez():
    """Testin ozu: bellek sinirsiz artmamali."""
    s = _oturum()
    for i in range(OUTBOX_MAX * 3):
        s.enqueue(bytes([i % 256]))

    assert s.outbox.qsize() == OUTBOX_MAX, (
        f"kuyruk {s.outbox.qsize()} oge tutuyor — sinir uygulanmamis"
    )


def test_tasma_EN_ESKIYI_atar_en_yeniyi_DEGIL():
    """Yon onemli.

    Spontane bildirimde son deger gecerlidir. Yeni geleni atsaydik, guncel
    olcumu atip bayat olani gondermis olurduk — sessizce YANLIS veri.
    """
    s = _oturum()
    for i in range(OUTBOX_MAX):
        s.enqueue(bytes([0]))          # eski doldurma
    s.enqueue(b"\xAA")                  # tasma: en yeni

    kalanlar = []
    while not s.outbox.empty():
        kalanlar.append(s.outbox.get_nowait())

    assert kalanlar[-1] == b"\xAA", "en yeni bildirim atilmis"
    assert len(kalanlar) == OUTBOX_MAX


def test_dusen_bildirim_SAYILIR():
    """Sessiz kayip olmamali; operator gorebilmeli."""
    s = _oturum()
    for _ in range(OUTBOX_MAX):
        s.enqueue(b"x")
    assert s.dropped_total == 0

    for _ in range(5):
        s.enqueue(b"y")
    assert s.dropped_total == 5


def test_kuyruk_dolmadan_dusurmez():
    s = _oturum()
    for _ in range(OUTBOX_MAX):
        assert s.enqueue(b"x") is True
    assert s.dropped_total == 0


# ------------------------------------------------------ sira numarasi (ns)


@pytest.mark.asyncio
async def test_ns_tel_sirasiyla_TUTARLI():
    """ns yaris durumu regresyonu.

    Eskiden ns kilit DISINDA artiyordu; eszamanli gonderimler cerceveleri ns
    sirasindan farkli yazabiliyordu. Simdi ns tek yazici gorevde, gonderim
    aninda atanir. Bu test cok sayida ardisik gonderimde ns'lerin 0,1,2...
    seklinde ve YAZILMA SIRASIYLA ayni oldugunu dogrular.
    """
    from app.services.iec104.registry import PointAddress, PointRegistry
    from app.services.iec104.server import IEC104Server

    registry = PointRegistry(
        target_id=1,
        default_common_address=1,
        points=[
            PointAddress(
                device_code="DEV1", signal_key="master.current", common_address=1,
                ioa=1, type_id=13,
            )
        ],
    )
    server = IEC104Server(
        name="t", host="127.0.0.1", port=0, registry=registry, allowed_peers=[]
    )
    s = _oturum()
    s.started = True

    for i in range(50):
        s.enqueue(bytes([i % 256]))

    gorev = asyncio.create_task(server._drain_outbox(s))
    try:
        await asyncio.wait_for(s.outbox.join(), timeout=5)
    finally:
        gorev.cancel()

    # ns alani I-frame'in 2. baytindan itibaren (control field 1): (ns << 1)
    ns_dizisi = [
        int.from_bytes(cerceve[2:4], "little") >> 1
        for cerceve in s.writer.yazilan
    ]
    assert ns_dizisi == list(range(50)), f"ns sirasi bozuk: {ns_dizisi[:10]}"


# --------------------------------------------------------- oturum tavani


def test_oturum_tavani_MAKUL():
    """Sinir var ve gercek kurulumu bogacak kadar dusuk degil."""
    assert 1 < MAX_SESSIONS <= 64


# ------------------------------------------------- yapisal: gorev sizintisi


def test_deger_yayini_GOREV_YARATMIYOR():
    """En kritik yapisal koruma.

    Biri ileride `update_point` icine tekrar `create_task` koyarsa sinirsiz
    gorev birikmesi geri gelir — ve bu ancak sahada, bellek dolunca fark
    edilir. Bu test o adimi kirmizi yapar.
    """
    import inspect

    from app.services.iec104.server import IEC104Server

    kaynak = inspect.getsource(IEC104Server.update_point)
    assert "create_task" not in kaynak, (
        "update_point icinde create_task var — sinirsiz gorev birikmesi geri gelmis; "
        "gonderim oturum kuyruguna (session.enqueue) yazilmali"
    )
    assert "enqueue" in kaynak


def test_drain_gorevi_referansta_TUTULUR():
    """Referanssiz gorev cop toplayici tarafindan yarida kesilebilir."""
    import inspect

    from app.services.iec104.server import IEC104Server

    kaynak = inspect.getsource(IEC104Server._handle_client)
    assert "_drain_task" in kaynak, "drain gorevi referansta tutulmuyor"
    assert "cancel()" in kaynak, "oturum kapaninca drain gorevi iptal edilmiyor"
