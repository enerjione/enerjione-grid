"""IEC 104 TCP server end-to-end smoke testi.

Gercek bir localhost TCP port acip dummy async client ile konusturuyoruz:
  1. Client baglan -> STARTDT_ACT gonder -> STARTDT_CON bekle.
  2. Server `update_point` cagrilinca spontaneous I-frame gelsin.
  3. Client C_IC_NA_1 (general interrogation) gonder -> ACT_CON + degerler +
     ACT_TERM dizisi gelsin.
"""

from __future__ import annotations

import asyncio
import socket
import struct

import pytest

from app.services.iec104 import (
    APCI_U_START_ACT,
    APCI_U_START_CONFIRM,
    COT_ACTIVATION_CON,
    COT_ACTIVATION_TERM,
    COT_SPONTANEOUS,
    TYPE_C_IC_NA_1,
    TYPE_M_ME_NC_1,
    build_i_frame_asdu,
    build_u_frame,
    parse_apci,
)
from app.services.iec104.registry import PointAddress, PointRegistry
from app.services.iec104.server import IEC104Server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _read_next_apdu(reader: asyncio.StreamReader, buffer: bytearray, timeout: float = 2.0) -> dict:
    async def _pull() -> dict:
        while True:
            parsed = parse_apci(bytes(buffer)) if buffer else None
            if parsed is not None:
                del buffer[: 2 + parsed.length]
                return {"kind": parsed.kind, "u_tag": parsed.u_tag, "asdu": parsed.asdu}
            chunk = await reader.read(4096)
            if not chunk:
                raise AssertionError("stream closed")
            buffer.extend(chunk)

    return await asyncio.wait_for(_pull(), timeout=timeout)


@pytest.mark.asyncio
async def test_server_round_trip_spontaneous_and_interrogation() -> None:
    port = _free_port()
    points = (
        PointAddress(device_code="DEV-A", signal_key="master.v", type_id=TYPE_M_ME_NC_1, absolute_ioa=1000),
    )
    registry = PointRegistry(
        target_id=1, common_address=7, device_stride=10_000, points=points
    )
    server = IEC104Server(name="smoke", host="127.0.0.1", port=port, registry=registry)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        buffer = bytearray()

        # 1) STARTDT handshake
        writer.write(build_u_frame(APCI_U_START_ACT))
        await writer.drain()
        first = await _read_next_apdu(reader, buffer)
        assert first["u_tag"] == APCI_U_START_CONFIRM

        # 2) Spontaneous push
        server.update_point(device_code="DEV-A", signal_key="master.v", value=42.5)
        spontaneous = await _read_next_apdu(reader, buffer)
        assert spontaneous["kind"] == "I"
        asdu = spontaneous["asdu"]
        assert asdu[0] == TYPE_M_ME_NC_1
        assert asdu[2] == COT_SPONTANEOUS
        raw = asdu[-5:-1]
        assert struct.unpack("<f", raw)[0] == pytest.approx(42.5)

        # 3) General interrogation (C_IC_NA_1 COT=ACT, qoi=20)
        #    type=100(64) vsq=01 cot=06 orig=00 ca=07 00 ioa=00 00 00 qoi=14
        interro_asdu = bytes.fromhex("6401060007000000001 4".replace(" ", ""))
        interro_frame = build_i_frame_asdu(asdu=interro_asdu, ns=0, nr=0)
        writer.write(interro_frame)
        await writer.drain()

        act_con = await _read_next_apdu(reader, buffer)
        assert act_con["kind"] == "I"
        assert act_con["asdu"][0] == TYPE_C_IC_NA_1
        assert act_con["asdu"][2] == COT_ACTIVATION_CON

        value_response = await _read_next_apdu(reader, buffer)
        assert value_response["kind"] == "I"
        assert value_response["asdu"][0] == TYPE_M_ME_NC_1

        act_term = await _read_next_apdu(reader, buffer)
        assert act_term["asdu"][0] == TYPE_C_IC_NA_1
        assert act_term["asdu"][2] == COT_ACTIVATION_TERM

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
