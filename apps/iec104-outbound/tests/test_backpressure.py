"""IEC104 outbound: sinirsiz gorev buyumesi ve oturum yigilmasi (A7 eksigi).

NEDEN BU DOSYA VAR
------------------
Bu sertlestirme bir kez YAPILDI ama YANLIS KOPYAYA uygulandi. Depoda iki
IEC 104 sunucusu var:

  * apps/backend-api/app/services/iec104/server.py  — backend ici kopya
  * apps/iec104-outbound/iec104_outbound/server.py  — AYRI CONTAINER

`docker-compose.yml` 2404-2406 portlarini YALNIZCA iec104-outbound
container'inda yayinliyor; yani SCADA'nin gercekten bagladigi kopya BUDUR.
Koruma digerine konmustu, dolayisiyla sahada hicbir sey degismemisti.

KAPATILAN ARIZA
---------------
`update_point` her deger degisimi icin, her oturuma
`asyncio.create_task(self._send_i(...))` aciyordu — sinirsiz ve referans
tutulmadan. Iki bagimsiz sonucu vardi:

  1. BELLEK: SCADA yavasladiginda `drain()` bloklanir; her yeni degisim bir
     gorev daha yaratir. 600 cihaz x 20 sinyal olceginde saniyede binlerce
     gorev birikir ve container OOM'a gider.
  2. SIRA: `ns` sira numarasi gorevler arasinda YARISA girer. IEC 104'te
     sira numarasi tel sirasiyla artmak zorundadir; bozulunca master tum
     oturumu reddedebilir.

Ayrica bagli oturum sayisi sinirsizdi: yeniden baglanma dongusune giren bir
master onlarca oturum acip sunucuyu kendi kendine bogabiliyordu.
"""

from __future__ import annotations

import asyncio

import pytest

from iec104_outbound.server import (
    MAX_SESSIONS,
    OUTBOX_MAX,
    _ClientSession,
)


class _SahteWriter:
    """Yazmayi sonsuza kadar bekleten sahte istemci (yavas SCADA)."""

    def __init__(self) -> None:
        self.yazilanlar: list[bytes] = []
        self.kapali = False

    def write(self, data: bytes) -> None:
        self.yazilanlar.append(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.kapali

    def close(self) -> None:
        self.kapali = True


def _oturum() -> _ClientSession:
    return _ClientSession(writer=_SahteWriter(), peer="10.0.0.9:2404")


def test_kuyruk_TAVANLI():
    """Kuyruk sinirsiz olsaydi yavas istemci bellegi OOM'a kadar sisirirdi."""
    s = _oturum()
    assert s.outbox.maxsize == OUTBOX_MAX
    assert OUTBOX_MAX > 0, "tavan 0/None ise kuyruk yine sinirsiz demektir"


def test_kuyruk_dolunca_EN_ESKI_dusuyor():
    """IEC 104 spontane bildiriminde SON deger gecerlidir.

    Yeni geleni atmak, guncel degeri atip bayat degeri gondermek olurdu —
    yani tam tersi. Bu test o yonu sabitler.
    """
    s = _oturum()
    for i in range(OUTBOX_MAX):
        assert s.enqueue(bytes([i % 256])) is True

    # Kuyruk dolu; bir tane daha koy.
    assert s.enqueue(b"\xff") is False, "tavan asilinca False donmeliydi"
    assert s.dropped_total == 1

    # EN ESKI (ilk konan) dusmus olmali; en yeni ICERIDE olmali.
    ilk = s.outbox.get_nowait()
    assert ilk == bytes([1]), f"en eski yerine baskasi dusmus: {ilk!r}"

    kalanlar = []
    while not s.outbox.empty():
        kalanlar.append(s.outbox.get_nowait())
    assert kalanlar[-1] == b"\xff", "en yeni deger dusurulmus (yanlis yon)"


def test_dusen_sayisi_SAYILIYOR():
    """Sessiz kayip olmamali: dusen ASDU sayisi loglanabilir olmali."""
    s = _oturum()
    for i in range(OUTBOX_MAX + 5):
        s.enqueue(bytes([i % 256]))
    assert s.dropped_total == 5


def test_oturum_tavani_TANIMLI():
    """Sinirsiz oturum: yeniden baglanan bir master sunucuyu boguyordu."""
    assert MAX_SESSIONS > 0
    assert MAX_SESSIONS <= 64, (
        "tavan gercekci olmali; bir target'a birkac master baglanir"
    )


def test_kuyruk_bosaltilinca_sira_KORUNUYOR():
    """`ns` yarisinin kokeni: gonderim sirasi kuyruk sirasi olmali."""

    async def _kos() -> list[bytes]:
        s = _oturum()
        for i in range(5):
            s.enqueue(bytes([i]))
        cikti: list[bytes] = []
        while not s.outbox.empty():
            cikti.append(await s.outbox.get())
        return cikti

    assert asyncio.run(_kos()) == [bytes([i]) for i in range(5)]


@pytest.mark.parametrize("adet", [1, 10, OUTBOX_MAX])
def test_tavan_altinda_hic_dusmuyor(adet: int):
    """Normal yuk etkilenmemeli — geri basinc yalnizca tavanda devreye girer."""
    s = _oturum()
    for i in range(adet):
        assert s.enqueue(bytes([i % 256])) is True
    assert s.dropped_total == 0
