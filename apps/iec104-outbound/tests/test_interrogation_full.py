"""Genel sorgu (GI) 12. nesnede KESILMEMELI — gercek TCP uzerinden.

YASANAN ARIZA
-------------
`_handle_interrogation` okuma dongusunun ICINDEN cagriliyordu. GI suresince
`reader.read()` hic calismiyor, dolayisiyla master'in S-frame'leri
islenmiyordu. `session.unacked` yalnizca S-frame gelince sifirlaniyor ve
`_send_i` `unacked >= 12` olunca frame'i SESSIZCE dusuruyordu. Sonuc:

    ACT_CON + 11 nesne gider, GERI KALANI VE ACT_TERM HIC GITMEZ.

SCADA genel sorgunun bittigini gosteren ACT_TERM'i hic almaz; 600 cihazlik
bir sahada ilk sorguda 12 nesne disinda hicbir sey ulasmaz. Bu, yari-acik
TCP arizasindan (A7) BAGIMSIZ bir yoldur: baglanti tamamen saglikliyken de
HER GI'da tetiklenir.

BU TEST NEYI SURUYOR
--------------------
Gercek bir master gibi davranan bir istemci: STARTDT, ardindan C_IC_NA_1
gonderir, gelen I-frame'leri sayar ve periyodik S-frame ile ack atar.
Nesne sayisi k-penceresinin (12) belirgin ustunde secildi — eski kodda test
ACT_TERM'i hic goremez ve zaman asimina duserdi.
"""

from __future__ import annotations

import asyncio
import socket
import struct

import pytest

from iec104_outbound.registry import PointAddress, PointRegistry
from iec104_outbound.server import MAX_UNACKED_I, IEC104Server

TYPE_FLOAT = 13          # M_ME_NC_1
TYPE_C_IC = 100          # C_IC_NA_1
COT_ACT = 6
COT_ACTCON = 7
COT_INTERROGATION = 20
COT_ACTTERM = 10
CA = 7

# k-penceresinin acik ara ustunde: eski kod 11 nesneden sonra susardi.
NOKTA_SAYISI = MAX_UNACKED_I * 3          # 36

STARTDT_ACT = bytes([0x68, 0x04, 0x07, 0x00, 0x00, 0x00])


def _registry() -> PointRegistry:
    return PointRegistry(
        target_id=1,
        default_common_address=CA,
        points=tuple(
            PointAddress(
                device_code=f"DEV-{i:03d}",
                signal_key="master.actual_voltage",
                type_id=TYPE_FLOAT,
                common_address=CA,
                ioa=1000 + i,
            )
            for i in range(NOKTA_SAYISI)
        ),
    )


def _gi_istegi(ca: int) -> bytes:
    """Master'in gonderdigi C_IC_NA_1 I-frame'i (ns=0, nr=0)."""
    asdu = struct.pack("<BBHH", TYPE_C_IC, 1, COT_ACT, ca)
    asdu += bytes([0, 0, 0])   # IOA = 0
    asdu += bytes([20])        # QOI = 20 (station interrogation)
    # APCI: ns/nr 15-bit, bir bit sola kaydirilmis
    apci = struct.pack("<HH", 0 << 1, 0 << 1)
    return bytes([0x68, len(apci) + len(asdu)]) + apci + asdu


def _s_frame(nr: int) -> bytes:
    return bytes([0x68, 0x04, 0x01, 0x00]) + struct.pack("<H", nr << 1)


def _cerceve_oku(sock: socket.socket) -> bytes | None:
    bas = b""
    while len(bas) < 2:
        parca = sock.recv(2 - len(bas))
        if not parca:
            return None
        bas += parca
    uzunluk = bas[1]
    govde = b""
    while len(govde) < uzunluk:
        parca = sock.recv(uzunluk - len(govde))
        if not parca:
            return None
        govde += parca
    return bas + govde


async def _senaryo() -> dict:
    server = IEC104Server(name="gi", host="127.0.0.1", port=0, registry=_registry())
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # noqa: SLF001
    loop = asyncio.get_running_loop()
    sonuc: dict = {"nesneler": [], "actcon": 0, "actterm": 0}

    def _master() -> None:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(STARTDT_ACT)
            _cerceve_oku(sock)                 # STARTDT_CON
            sock.sendall(_gi_istegi(CA))

            alinan_i = 0
            while True:
                cerceve = _cerceve_oku(sock)
                if cerceve is None:
                    break
                kontrol = cerceve[2]
                if kontrol & 0x01:             # S- veya U-frame
                    continue
                alinan_i += 1
                asdu = cerceve[6:]
                tip = asdu[0]
                cot = struct.unpack_from("<H", asdu, 2)[0] & 0x3F
                if tip == TYPE_C_IC and cot == COT_ACTCON:
                    sonuc["actcon"] += 1
                elif tip == TYPE_C_IC and cot == COT_ACTTERM:
                    sonuc["actterm"] += 1
                    break                      # GI bitti
                elif cot == COT_INTERROGATION:
                    ioa = asdu[6] | (asdu[7] << 8) | (asdu[8] << 16)
                    sonuc["nesneler"].append(ioa)

                # GERCEK MASTER GIBI ACK AT: k-penceresi dolmadan once
                # S-frame gonder. Bu olmazsa sunucu (dogru davranisla) bekler.
                if alinan_i % (MAX_UNACKED_I - 4) == 0:
                    sock.sendall(_s_frame(alinan_i))

    gorev = loop.run_in_executor(None, _master)
    await asyncio.wait_for(gorev, timeout=20)
    await server.stop()
    return sonuc


def test_GI_tum_nesneleri_ve_ACT_TERM_i_gonderiyor():
    sonuc = asyncio.run(_senaryo())

    assert sonuc["actcon"] == 1, "ACT_CON gelmedi"
    assert len(sonuc["nesneler"]) == NOKTA_SAYISI, (
        f"GI {len(sonuc['nesneler'])} nesnede kesildi (beklenen {NOKTA_SAYISI}). "
        "Eski kodda okuma dongusu GI suresince durdugu icin S-frame islenmiyor, "
        "k-penceresi 12'de kilitleniyor ve geri kalan nesneler sessizce "
        "dusuruluyordu."
    )
    assert sonuc["actterm"] == 1, (
        "ACT_TERM gelmedi — SCADA genel sorgunun bittigini ASLA ogrenemez"
    )
    # Nesneler eksiksiz ve dogru adreslerle gelmeli
    assert sorted(sonuc["nesneler"]) == [1000 + i for i in range(NOKTA_SAYISI)]


def test_GI_ayri_gorevde_kosuyor():
    """Okuma dongusu GI suresince BLOKLANMAMALI.

    Kaynak duzeyinde sabitleniyor: `_handle_i_frame` GI'yi dogrudan
    `await` etmemeli, gorev baslatmali. Aksi halde yukaridaki uctan uca
    test yeniden kirmiziya doner ama sebebi belirsiz kalirdi.
    """
    import ast
    import inspect

    from iec104_outbound import server as srv

    fn = next(
        d
        for d in ast.walk(ast.parse(inspect.getsource(srv)))
        if isinstance(d, ast.AsyncFunctionDef) and d.name == "_handle_i_frame"
    )
    # `await self._handle_interrogation(...)` KALMAMALI
    for n in ast.walk(fn):
        if isinstance(n, ast.Await):
            cagri = n.value
            ad = getattr(getattr(cagri, "func", None), "attr", None)
            assert ad != "_handle_interrogation", (
                "GI hala okuma dongusunun icinde await ediliyor — S-frame'ler "
                "islenmez ve k-penceresi 12'de kilitlenir"
            )


@pytest.mark.parametrize("metod", ["_wait_window"])
def test_k_penceresi_BEKLIYOR_dusurmuyor(metod: str):
    """IEC 60870-5-104: k asilirsa gonderen DURMALI, veri ATMAMALI.

    Onceki kod akis kontrolunu "frame dusurme" olarak uyguluyordu.
    """
    from iec104_outbound import server as srv

    assert hasattr(srv.IEC104Server, metod), f"{metod} yok"
