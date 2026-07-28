"""modbus-outbound duman testleri — harici bagimlilik olmadan calisir.

`python -m tests.test_smoke` ile dogrudan kosturulabilir (pytest sart degil);
CI/gelistirici makinesinde pymodbus/nats kurulu olmasa da calisir.

Kapsam:
  * codec: int16 olcekleme/kirpma, float32 word sirasi, uint32 sayac
  * registry: plan -> store yerlestirme, bit/register guncelleme
  * server: PDU seviyesinde FC1/2/3/4 + hata kodlari
  * uctan uca: gercek TCP soketi uzerinden Modbus istegi/cevabi
"""

from __future__ import annotations

import asyncio
import socket
import struct
import sys

from modbus_outbound import codec
from modbus_outbound.registry import build_registry_from_plan
from modbus_outbound.server import (
    EXC_GATEWAY_TARGET_FAILED,
    EXC_ILLEGAL_DATA_ADDRESS,
    EXC_ILLEGAL_DATA_VALUE,
    EXC_ILLEGAL_FUNCTION,
    ModbusTargetServer,
    handle_pdu,
)

FAILURES: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    if not condition:
        FAILURES.append(label)
    print(f"  [{'OK  ' if condition else 'FAIL'}] {label} {extra}")


# --- Ornek plan (backend'in urettigi formatta) ------------------------------
PLAN = {
    "target_id": 1,
    "target_name": "SCADA-A",
    "mode": "block",
    "value_format": "int16",
    "word_order": "big",
    "listen_host": "127.0.0.1",
    "listen_port": 0,
    "is_active": True,
    "allowed_peers": [],
    "devices": [
        {"device_id": 1, "device_code": "DEV-001", "device_name": "Fider 1",
         "slot_index": 0, "unit_id": 1, "block_start": 0},
        {"device_id": 2, "device_code": "DEV-002", "device_name": "Fider 2",
         "slot_index": 1, "unit_id": 1, "block_start": 100},
    ],
    "points": [
        {"device_code": "DEV-001", "device_name": "Fider 1", "signal_key": "master.actual_voltage",
         "label": "Gerilim", "source": "master", "data_type": "analog", "unit": "V",
         "unit_id": 1, "function": 3, "address": 0, "word_count": 1,
         "scale": 0.1, "offset": 0.0, "manual": False},
        {"device_code": "DEV-001", "device_name": "Fider 1", "signal_key": "master.fault_counter",
         "label": "Sayac", "source": "master", "data_type": "counter", "unit": "",
         "unit_id": 1, "function": 3, "address": 10, "word_count": 2,
         "scale": 1.0, "offset": 0.0, "manual": False},
        {"device_code": "DEV-001", "device_name": "Fider 1", "signal_key": "master.fault_flag",
         "label": "Ariza", "source": "master", "data_type": "binary", "unit": "",
         "unit_id": 1, "function": 2, "address": 3, "word_count": 0,
         "scale": 1.0, "offset": 0.0, "manual": False},
        {"device_code": "DEV-001", "device_name": "Fider 1", "signal_key": "master.reset_cmd",
         "label": "Reset", "source": "master", "data_type": "binary_output", "unit": "",
         "unit_id": 1, "function": 1, "address": 2, "word_count": 0,
         "scale": 1.0, "offset": 0.0, "manual": False},
        {"device_code": "DEV-002", "device_name": "Fider 2", "signal_key": "master.actual_voltage",
         "label": "Gerilim", "source": "master", "data_type": "analog", "unit": "V",
         "unit_id": 1, "function": 3, "address": 100, "word_count": 1,
         "scale": 0.1, "offset": 0.0, "manual": False},
    ],
}


def test_codec() -> None:
    print("\n1) codec")
    check("int16 olcekleme 230.5V scale=0.1 -> 2305",
          codec.encode_int16(230.5, scale=0.1, offset=0.0) == [2305],
          str(codec.encode_int16(230.5, scale=0.1, offset=0.0)))
    check("negatif deger two's complement",
          codec.encode_int16(-5, scale=1.0, offset=0.0) == [0xFFFB],
          hex(codec.encode_int16(-5, scale=1.0, offset=0.0)[0]))
    check("ust sinirda kirpilir (sarma yok)",
          codec.encode_int16(999999, scale=1.0, offset=0.0) == [32767])
    check("alt sinirda kirpilir",
          codec.encode_int16(-999999, scale=1.0, offset=0.0) == [0x8000])
    check("NaN -> 0", codec.encode_int16(float("nan"), scale=1.0, offset=0.0) == [0])
    check("offset uygulanir (real=raw*scale+offset)",
          codec.encode_int16(120.0, scale=1.0, offset=100.0) == [20])

    big = codec.encode_float32(1.0, word_order="big")
    little = codec.encode_float32(1.0, word_order="little")
    check("float32 big = [0x3F80, 0x0000]", big == [0x3F80, 0x0000], str([hex(x) for x in big]))
    check("float32 little word-swap", little == [0x0000, 0x3F80], str([hex(x) for x in little]))
    check("float32 geri cozulur",
          struct.unpack(">f", struct.pack(">HH", *big))[0] == 1.0)

    check("uint32 sayac 70000 -> [1, 4464]",
          codec.encode_uint32(70000) == [1, 4464], str(codec.encode_uint32(70000)))
    check("negatif sayac 0'a kilitlenir", codec.encode_uint32(-5) == [0, 0])

    check("bool 'true' metni", codec.coerce_bool("true") is True)
    check("bool 'OFF' metni", codec.coerce_bool("OFF") is False)
    check("bool 1", codec.coerce_bool(1) is True)
    check("sayi cevrilemezse None", codec.coerce_number("abc") is None)


def test_registry() -> None:
    print("\n2) registry")
    reg = build_registry_from_plan(PLAN)
    check("5 nokta yuklendi", reg.point_count == 5, str(reg.point_count))
    check("tek unit (block modu)", reg.unit_ids() == [1], str(reg.unit_ids()))

    written = reg.update("DEV-001", "master.actual_voltage", 230.5)
    check("analog yazildi", written == 1)
    check("register 0 = 2305", reg.read(1, 3, 0, 1) == [2305], str(reg.read(1, 3, 0, 1)))
    check("FC4 ayni degeri verir (ayna)", reg.read(1, 4, 0, 1) == [2305])

    reg.update("DEV-001", "master.fault_counter", 70000)
    check("sayac 2 register", reg.read(1, 3, 10, 2) == [1, 4464], str(reg.read(1, 3, 10, 2)))

    reg.update("DEV-001", "master.fault_flag", True)
    check("discrete input yazildi", reg.read(1, 2, 3, 1) == [True])
    reg.update("DEV-001", "master.reset_cmd", "on")
    check("coil yazildi", reg.read(1, 1, 2, 1) == [True])

    reg.update("DEV-002", "master.actual_voltage", 400.0)
    check("2. cihaz kendi blogunda", reg.read(1, 3, 100, 1) == [4000], str(reg.read(1, 3, 100, 1)))
    check("1. cihazin degeri bozulmadi", reg.read(1, 3, 0, 1) == [2305])

    before = reg.updates_unmapped
    reg.update("DEV-999", "yok.olmayan", 1)
    check("planda olmayan sinyal sayilir", reg.updates_unmapped == before + 1)

    check("bos adres 0 doner", reg.read(1, 3, 5000, 2) == [0, 0])
    check("bilinmeyen unit -> None", reg.read(99, 3, 0, 1) is None)


def test_pdu() -> None:
    print("\n3) PDU isleme")
    reg = build_registry_from_plan(PLAN)
    reg.update("DEV-001", "master.actual_voltage", 230.5)
    reg.update("DEV-001", "master.fault_flag", True)

    # FC3: 1 register oku
    resp = handle_pdu(reg, 1, struct.pack(">BHH", 3, 0, 1))
    check("FC3 cevap formati", resp == struct.pack(">BBH", 3, 2, 2305), resp.hex())

    # FC4: ayni icerik
    resp4 = handle_pdu(reg, 1, struct.pack(">BHH", 4, 0, 1))
    check("FC4 ayna", resp4 == struct.pack(">BBH", 4, 2, 2305), resp4.hex())

    # FC2: discrete input, adres 3 -> 4 bit oku (0,1,2,3) -> son bit set
    resp2 = handle_pdu(reg, 1, struct.pack(">BHH", 2, 0, 4))
    check("FC2 bit paketleme", resp2 == bytes([2, 1, 0b1000]), resp2.hex())

    # FC1: coil adres 2
    resp1 = handle_pdu(reg, 1, struct.pack(">BHH", 1, 0, 3))
    check("FC1 coil (yazilmamis -> 0)", resp1 == bytes([1, 1, 0b000]), resp1.hex())

    # Yazma reddi
    for fc in (5, 6, 15, 16):
        r = handle_pdu(reg, 1, struct.pack(">BHH", fc, 0, 1))
        check(f"FC{fc} yazma reddedildi",
              r == bytes([fc | 0x80, EXC_ILLEGAL_FUNCTION]), r.hex())

    # Bilinmeyen fonksiyon
    r = handle_pdu(reg, 1, struct.pack(">BHH", 99, 0, 1))
    check("bilinmeyen FC -> illegal function", r == bytes([99 | 0x80, EXC_ILLEGAL_FUNCTION]))

    # Sayi sinirlari
    r = handle_pdu(reg, 1, struct.pack(">BHH", 3, 0, 126))
    check("FC3 126 register -> illegal data value",
          r == bytes([3 | 0x80, EXC_ILLEGAL_DATA_VALUE]))
    r = handle_pdu(reg, 1, struct.pack(">BHH", 3, 0, 0))
    check("FC3 0 register -> illegal data value",
          r == bytes([3 | 0x80, EXC_ILLEGAL_DATA_VALUE]))
    r = handle_pdu(reg, 1, struct.pack(">BHH", 1, 0, 2001))
    check("FC1 2001 bit -> illegal data value",
          r == bytes([1 | 0x80, EXC_ILLEGAL_DATA_VALUE]))
    check("FC3 125 register kabul", handle_pdu(reg, 1, struct.pack(">BHH", 3, 0, 125))[0] == 3)
    check("FC1 2000 bit kabul", handle_pdu(reg, 1, struct.pack(">BHH", 1, 0, 2000))[0] == 1)

    # Adres tasmasi
    r = handle_pdu(reg, 1, struct.pack(">BHH", 3, 65535, 2))
    check("adres uzayi tasmasi -> illegal data address",
          r == bytes([3 | 0x80, EXC_ILLEGAL_DATA_ADDRESS]))

    # Bilinmeyen unit
    r = handle_pdu(reg, 77, struct.pack(">BHH", 3, 0, 1))
    check("bilinmeyen unit -> gateway hatasi",
          r == bytes([3 | 0x80, EXC_GATEWAY_TARGET_FAILED]))

    # Kisa PDU
    r = handle_pdu(reg, 1, bytes([3, 0]))
    check("eksik PDU -> illegal data value", r == bytes([3 | 0x80, EXC_ILLEGAL_DATA_VALUE]))


def _modbus_request(sock: socket.socket, unit: int, pdu: bytes, tid: int = 1) -> bytes:
    frame = struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu
    sock.sendall(frame)
    header = sock.recv(7)
    length = struct.unpack(">H", header[4:6])[0]
    body = b""
    while len(body) < length - 1:
        body += sock.recv(length - 1 - len(body))
    return header + body


async def test_tcp_end_to_end() -> None:
    print("\n4) Gercek TCP uzerinden uctan uca")
    reg = build_registry_from_plan(PLAN)
    reg.update("DEV-001", "master.actual_voltage", 230.5)
    reg.update("DEV-002", "master.actual_voltage", 400.0)

    # Port 0 = isletim sistemi bos port versin.
    server = ModbusTargetServer(
        target_id=1, name="test", host="127.0.0.1", port=0, registry=reg,
    )
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # noqa: SLF001
    print(f"     test sunucusu 127.0.0.1:{port}")

    loop = asyncio.get_running_loop()

    def _client_calls() -> dict:
        out = {}
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            resp = _modbus_request(sock, 1, struct.pack(">BHH", 3, 0, 1), tid=0x1234)
            out["voltage"] = resp
            # Ayni baglantida ikinci istek (persistent connection)
            resp2 = _modbus_request(sock, 1, struct.pack(">BHH", 3, 100, 1), tid=0x1235)
            out["dev2"] = resp2
            # Blok okuma: cihaz 1'in tum blogu tek istekte
            resp3 = _modbus_request(sock, 1, struct.pack(">BHH", 3, 0, 100), tid=0x1236)
            out["block"] = resp3
            # Yazma denemesi
            resp4 = _modbus_request(sock, 1, struct.pack(">BHH", 6, 0, 1), tid=0x1237)
            out["write"] = resp4
        return out

    result = await loop.run_in_executor(None, _client_calls)

    tid, pid, length, unit = struct.unpack(">HHHB", result["voltage"][:7])
    check("transaction id aynen doner", tid == 0x1234, hex(tid))
    check("protocol id 0", pid == 0)
    check("unit id aynen doner", unit == 1)
    check("uzunluk alani dogru", length == len(result["voltage"]) - 6, str(length))
    check("gerilim degeri dogru", result["voltage"][7:] == struct.pack(">BBH", 3, 2, 2305),
          result["voltage"][7:].hex())
    check("2. cihaz ayni baglantidan okundu",
          result["dev2"][7:] == struct.pack(">BBH", 3, 2, 4000), result["dev2"][7:].hex())
    check("100 register blok okumasi", len(result["block"]) == 7 + 2 + 200,
          str(len(result["block"])))
    check("blok icinde dogru deger", result["block"][9:11] == struct.pack(">H", 2305))
    check("yazma TCP uzerinden de reddedildi",
          result["write"][7:] == bytes([6 | 0x80, EXC_ILLEGAL_FUNCTION]),
          result["write"][7:].hex())
    check("istek sayaci arttI", server.requests_served == 4, str(server.requests_served))

    await server.stop()

    print("\n5) IP allowlist")
    reg2 = build_registry_from_plan(PLAN)
    guarded = ModbusTargetServer(
        target_id=2, name="guarded", host="127.0.0.1", port=0, registry=reg2,
        allowed_peers=("10.0.0.5",),
    )
    await guarded.start()
    gport = guarded._server.sockets[0].getsockname()[1]  # noqa: SLF001

    def _blocked_call() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", gport), timeout=3) as sock:
                sock.sendall(struct.pack(">HHHB", 1, 0, 6, 1) + struct.pack(">BHH", 3, 0, 1))
                return sock.recv(16) == b""  # baglanti kapatildi -> bos okuma
        except (ConnectionError, OSError):
            return True

    blocked = await loop.run_in_executor(None, _blocked_call)
    check("listede olmayan IP reddedildi", blocked)
    check("red sayaci arttI", guarded.rejected_peers == 1, str(guarded.rejected_peers))
    await guarded.stop()


def main() -> int:
    test_codec()
    test_registry()
    test_pdu()
    asyncio.run(test_tcp_end_to_end())
    print()
    if FAILURES:
        print(f"!! {len(FAILURES)} BASARISIZ: {FAILURES}")
        return 1
    print("Tum kontroller basarili.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
