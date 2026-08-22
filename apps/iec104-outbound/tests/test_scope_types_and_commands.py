"""IEC 104 kapsami: string YOK, analog cikis YOK, komut olarak yalnizca reset.

URUN KARARI
-----------
IEC 104 ile YALNIZCA izleme tipleri yayinlanir:

    1  M_SP_NA_1  tek nokta (binary)
    13 M_ME_NC_1  kisa kayan nokta (analog olcum)
    15 M_IT_NA_1  sayac

Kapsam disi: STRING (DNP3 Group 110 / octet string) ve ANALOG CIKIS /
setpoint. Komut tarafinda genel sorgu + saat esitleme + (ileride) reset
disinda bir sey kabul edilmez.

NEDEN KODDA ZORLANIYOR
----------------------
Fabrika katalogunda string (30 sinyal) ve binary_output (18 sinyal) zaten
`iec104_type_id = NULL` ile geliyor, yani VERI uygun. Ama
`PATCH /signals/{key}` bu alani DUZENLENEBILIR yapiyor. Biri string bir
sinyale tip verirse nokta registry'ye girer, sonra `_encode_single_value`
onu tanimayip `None` doner ve bildirim HER SEFERINDE SESSIZCE duser — yani
yanlis yapilandirma "calisiyor" gorunur ve SCADA'da "adres listesinde var
ama veri gelmiyor" olarak yasanir.

Komut tarafinda da eski davranis `logger.debug` idi: operator komut
gonderiyor, hicbir yanit almiyor ve kabul edilip edilmedigini
anlayamiyordu.
"""

from __future__ import annotations

import asyncio
import socket
import struct

import pytest

from iec104_outbound.registry import (
    SUPPORTED_MONITORING_TYPES,
    build_point_registry,
)

CA = 7


def _cihaz(kod="DEV-001"):
    return {"code": kod, "is_active": True, "iec104_common_address": CA}


def _sinyal(key, type_id, ioa, data_type="analog"):
    return {
        "key": key,
        "is_active": True,
        "data_type": data_type,
        "iec104_type_id": type_id,
        "iec104_ioa": ioa,
    }


def test_desteklenen_tipler_YALNIZCA_izleme():
    assert SUPPORTED_MONITORING_TYPES == frozenset({1, 13, 15})



def _katalog_noktalari(reg):
    """KATALOG kaynakli noktalar — sistem noktalari HARIC.

    Calisma-zamani sagligi (`system.*`) bir DNP3 katalog sinyali DEGIL;
    gateway'in cihazla olan oturumu hakkinda ayri bir alandir ve her cihaza
    kosulsuz eklenir (bkz. `runtime_health` modul basligi). Bu dosyanin
    konusu ise katalog sinyallerinin TIP KAPSAMI — ikisi karistirilmamali.
    """
    return tuple(p for p in reg.points if not p.signal_key.startswith("system."))

@pytest.mark.parametrize(
    "tip,aciklama",
    [
        (110, "string / octet string (DNP3 G110)"),
        (45, "C_SC_NA_1 tek komut"),
        (49, "C_SE_NB_1 setpoint (analog cikis)"),
        (50, "C_SE_NC_1 setpoint float (analog cikis)"),
        (58, "C_SC_TA_1 zaman etiketli komut"),
    ],
)
def test_kapsam_disi_tip_REGISTRY_e_girmiyor(tip: int, aciklama: str):
    """Girerse nokta yayinlanmaz ama adres listesinde GORUNUR."""
    reg = build_point_registry(
        target_id=1,
        default_common_address=CA,
        devices=[_cihaz()],
        signals=[_sinyal("master.x", tip, 1001)],
    )
    assert _katalog_noktalari(reg) == (), f"kapsam disi tip registry'ye girdi: {aciklama}"


@pytest.mark.parametrize("tip", sorted(SUPPORTED_MONITORING_TYPES))
def test_desteklenen_tip_REGISTRY_e_giriyor(tip: int):
    """Filtre normal akisi bozmamali."""
    reg = build_point_registry(
        target_id=1,
        default_common_address=CA,
        devices=[_cihaz()],
        signals=[_sinyal("master.x", tip, 1001)],
    )
    assert len(_katalog_noktalari(reg)) == 1
    assert _katalog_noktalari(reg)[0].type_id == tip


def test_karisik_katalogda_yalnizca_izleme_tipleri_kaliyor():
    """Gercekci senaryo: fabrika katalogu + elle verilmis bir string tipi."""
    reg = build_point_registry(
        target_id=1,
        default_common_address=CA,
        devices=[_cihaz()],
        signals=[
            _sinyal("master.voltage", 13, 1001),
            _sinyal("master.fault", 1, 1002, data_type="binary"),
            _sinyal("master.counter", 15, 1003, data_type="counter"),
            _sinyal("master.firmware", 110, 1004, data_type="string"),
            _sinyal("master.setpoint", 49, 1005, data_type="analog_output"),
        ],
    )
    anahtarlar = {p.signal_key for p in _katalog_noktalari(reg)}
    assert anahtarlar == {"master.voltage", "master.fault", "master.counter"}


def test_kapsam_disi_tip_UYARILIYOR(caplog):
    """Sessiz kalirsa "adres listesinde var ama veri gelmiyor" olarak yasanir."""
    import logging

    with caplog.at_level(logging.WARNING, logger="iec104_outbound.registry"):
        build_point_registry(
            target_id=1,
            default_common_address=CA,
            devices=[_cihaz()],
            signals=[_sinyal("master.firmware", 110, 1004, data_type="string")],
        )
    assert any("kapsamdisi_tip" in r.getMessage() for r in caplog.records), (
        "kapsam disi tip sessizce atlandi"
    )


# ---------------------------------------------------------------------------
# Komut tarafi
# ---------------------------------------------------------------------------

STARTDT_ACT = bytes([0x68, 0x04, 0x07, 0x00, 0x00, 0x00])
TYPE_C_SE_NC = 50           # setpoint float — KAPSAM DISI
COT_ACT = 6


def _komut_frame(type_id: int, ca: int) -> bytes:
    asdu = struct.pack("<BBHH", type_id, 1, COT_ACT, ca)
    asdu += bytes([0, 0, 0])       # IOA
    asdu += struct.pack("<f", 1.0) + bytes([0])  # deger + QOS
    apci = struct.pack("<HH", 0, 0)
    return bytes([0x68, len(apci) + len(asdu)]) + apci + asdu


def _cerceve_oku(sock) -> bytes | None:
    bas = b""
    while len(bas) < 2:
        p = sock.recv(2 - len(bas))
        if not p:
            return None
        bas += p
    govde = b""
    while len(govde) < bas[1]:
        p = sock.recv(bas[1] - len(govde))
        if not p:
            return None
        govde += p
    return bas + govde


async def _senaryo() -> dict:
    from iec104_outbound.registry import PointAddress, PointRegistry
    from iec104_outbound.server import IEC104Server

    reg = PointRegistry(
        target_id=1,
        default_common_address=CA,
        points=(
            PointAddress(
                device_code="DEV-001", signal_key="master.v",
                type_id=13, common_address=CA, ioa=1001,
            ),
        ),
    )
    server = IEC104Server(name="t", host="127.0.0.1", port=0, registry=reg)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # noqa: SLF001
    loop = asyncio.get_running_loop()
    sonuc: dict = {}

    def _master() -> None:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(STARTDT_ACT)
            _cerceve_oku(sock)                     # STARTDT_CON
            sock.sendall(_komut_frame(TYPE_C_SE_NC, CA))
            cerceve = _cerceve_oku(sock)
            sonuc["yanit"] = cerceve

    gorev = loop.run_in_executor(None, _master)
    await asyncio.wait_for(gorev, timeout=10)
    await server.stop()
    return sonuc


def test_kapsam_disi_komut_NEGATIF_onay_aliyor():
    """Sessizlik yerine acik red.

    IEC 60870-5-104'te dogru davranis COT'un P/N bitini kurmaktir; master
    komutun REDDEDILDIGINI ogrenir. Eski kod yalnizca `logger.debug`
    yaziyordu — operator komutun akibetini hic ogrenemiyordu.
    """
    sonuc = asyncio.run(_senaryo())
    cerceve = sonuc.get("yanit")
    assert cerceve is not None, "kapsam disi komuta HICBIR yanit gelmedi"

    asdu = cerceve[6:]
    cot_byte = asdu[2]
    assert cot_byte & 0x40, (
        f"P/N biti kurulmamis (COT=0x{cot_byte:02x}) — master komutun "
        "reddedildigini anlayamaz"
    )
