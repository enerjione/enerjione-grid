"""Spontane bildirim GERCEK TCP uzerinden gidiyor mu (A7 eksigi — regresyon).

NEDEN GEREKLI
-------------
`update_point` bu depoda HICBIR test tarafindan kosturulmuyordu. Sinirsiz
`create_task` yerine kuyruk + tek yazici gorev koyarken tam da o yolu
degistirdim; kosmayan bir yolu degistirmek, sessizce kirmanin en kolay yolu.

Bu test zinciri ucundan ucuna surer:

    update_point -> session.enqueue -> _drain_outbox -> _send_i -> TCP

Kirilirsa SCADA'ya spontane bildirim HIC gitmez: degerler master tarafinda
donar, ariza gecisleri gorunmez ve hicbir hata logu olusmaz — yani en
tehlikeli hata sinifi (sessiz yanlis veri).
"""

from __future__ import annotations

import asyncio
import socket
import struct

from iec104_outbound.registry import PointAddress, PointRegistry
from iec104_outbound.server import IEC104Server

# M_ME_NC_1 (13) = kisa kayan nokta, zaman etiketsiz.
# NOT: bu sunucu yalnizca 1 / 13 / 15 tiplerini kodluyor; desteklenmeyen
# bir tip secilirse `_encode_single_value` None doner ve bildirim SESSIZCE
# dusar (ilk denemede tam da bu oldu).
TYPE_FLOAT = 13
CA = 7
IOA = 1001

STARTDT_ACT = bytes([0x68, 0x04, 0x07, 0x00, 0x00, 0x00])


def _registry() -> PointRegistry:
    return PointRegistry(
        target_id=1,
        default_common_address=CA,
        points=(
            PointAddress(
                device_code="DEV-001",
                signal_key="master.actual_voltage",
                type_id=TYPE_FLOAT,
                common_address=CA,
                ioa=IOA,
            ),
        ),
    )


async def _senaryo() -> dict:
    server = IEC104Server(
        name="test", host="127.0.0.1", port=0, registry=_registry()
    )
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # noqa: SLF001

    loop = asyncio.get_running_loop()
    sonuc: dict = {}

    def _istemci() -> None:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            # STARTDT_ACT -> sunucu STARTDT_CON doner ve oturum "started" olur.
            sock.sendall(STARTDT_ACT)
            sonuc["startdt_con"] = sock.recv(6)
            # Sunucu tarafi update_point'i cagirana kadar burada bekleriz;
            # recv zaten bloklar (5 sn timeout).
            baslik = sock.recv(6)          # APCI
            uzunluk = baslik[1]
            govde = b""
            while len(govde) < uzunluk - 4:
                govde += sock.recv(uzunluk - 4 - len(govde))
            sonuc["i_frame"] = baslik + govde

    # Istemciyi thread'de baslat; STARTDT tamamlansin diye kisa bekleme.
    gorev = loop.run_in_executor(None, _istemci)
    for _ in range(50):
        await asyncio.sleep(0.02)
        if any(s.started for s in server._sessions):  # noqa: SLF001
            break

    sonuc["oturum_sayisi"] = len(server._sessions)  # noqa: SLF001
    sonuc["started"] = any(s.started for s in server._sessions)  # noqa: SLF001

    # ASIL OLAY: deger degisimi -> spontane bildirim
    server.update_point(
        device_code="DEV-001", signal_key="master.actual_voltage", value=230.5
    )

    await asyncio.wait_for(gorev, timeout=5)
    await server.stop()
    return sonuc


def test_spontane_bildirim_TELE_ULASIYOR():
    sonuc = asyncio.run(_senaryo())

    assert sonuc["oturum_sayisi"] == 1, "istemci baglanamadi"
    assert sonuc["started"] is True, "STARTDT_ACT sonrasi oturum baslamadi"

    frame = sonuc["i_frame"]
    assert frame[0] == 0x68, f"APCI baslangici yanlis: {frame[:2].hex()}"
    # I-frame: kontrol alaninin ilk biti 0
    assert frame[2] & 0x01 == 0, f"I-frame degil: {frame[2:6].hex()}"

    asdu = frame[6:]
    assert asdu[0] == TYPE_FLOAT, f"type id {asdu[0]}, beklenen {TYPE_FLOAT}"
    # ASDU DUI byte 4-5: Common Address (LE 16-bit)
    assert struct.unpack_from("<H", asdu, 4)[0] == CA
    # IOA: 3 bayt LE, DUI'den hemen sonra
    ioa = asdu[6] | (asdu[7] << 8) | (asdu[8] << 16)
    assert ioa == IOA, f"IOA {ioa}, beklenen {IOA}"
    # Deger: IOA'dan sonra IEEE-754 float32 LE
    assert abs(struct.unpack_from("<f", asdu, 9)[0] - 230.5) < 0.01


def test_ayni_deger_TEKRAR_yayinlanmiyor():
    """Report-by-exception: degismeyen deger APDU uretmemeli.

    Bu davranis kuyruga gecerken kaybolsaydi 600 cihazda kuyruk surekli dolu
    kalir ve geri basinc yanlis yere kurulurdu.
    """

    async def _kos() -> int:
        server = IEC104Server(
            name="t", host="127.0.0.1", port=0, registry=_registry()
        )

        class _Sahte:
            def write(self, d): pass
            async def drain(self): pass
            def is_closing(self): return False
            def close(self): pass

        from iec104_outbound.server import _ClientSession

        s = _ClientSession(writer=_Sahte(), peer="x")
        s.started = True
        server._sessions.add(s)  # noqa: SLF001

        for _ in range(5):
            server.update_point(
                device_code="DEV-001", signal_key="master.actual_voltage", value=100
            )
        return s.outbox.qsize()

    assert asyncio.run(_kos()) == 1, "ayni deger tekrar tekrar kuyruga girdi"
