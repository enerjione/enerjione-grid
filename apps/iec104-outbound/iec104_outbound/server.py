"""IEC 60870-5-104 asyncio TCP server (cihaz bazli ASDU CA destegi).

Yetenekler:

  1. **Spontaneous transmission (COT=3)** — `update_point()` cagirilirsa server
     ilgili client baglantilarina sinyalin ait oldugu cihazin Common Address'i
     ile ASDU yayinlar.
  2. **General interrogation (C_IC_NA_1)** — client'in gonderdigi ASDU'daki
     CA neyse:
       - 0xFFFF (broadcast)  → tum CA'lara ait noktalari yayinla
       - belirli bir CA      → yalnizca o CA'nin noktalari
     Sonuna ACT_TERM (cause=10) konur.
  3. STARTDT/STOPDT/TESTFR (U-frame) ve clock sync (C_CS_NA_1, ack-only).

Tek TCP oturumunda bir veya birden cok cihazin (her biri ayri CA ile) verisi
yayinlanabilir. K parametresi (MAX_UNACKED_I) ile akis kontrolu yapilir.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass

from iec104_outbound.encoder import (
    APCI_U_START_ACT,
    APCI_U_START_CONFIRM,
    APCI_U_STOP_ACT,
    APCI_U_STOP_CONFIRM,
    APCI_U_TEST_ACT,
    APCI_U_TEST_CONFIRM,
    COT_ACTIVATION_CON,
    COT_ACTIVATION_TERM,
    COT_INTERROGATION,
    COT_SPONTANEOUS,
    START_BYTE,
    TYPE_C_CS_NA_1,
    TYPE_C_IC_NA_1,
    TYPE_M_IT_NA_1,
    TYPE_M_ME_NC_1,
    TYPE_M_SP_NA_1,
    ParsedAPCI,
    build_i_frame_asdu,
    build_s_frame,
    build_u_frame,
    encode_asdu_counter,
    encode_asdu_float,
    encode_asdu_single_point,
    encode_interrogation_confirm,
    parse_apci,
)
from iec104_outbound.registry import (
    BROADCAST_COMMON_ADDRESS,
    PointAddress,
    PointRegistry,
)

logger = logging.getLogger(__name__)

# Client en fazla bu kadar I-frame'i onaylamadan tasiyabilir (K parametresi).
MAX_UNACKED_I = 12


@dataclass
class PointValue:
    value: float | int | bool
    good: bool = True


class _ClientSession:
    """Tek bir TCP client baglantisinin state'i."""

    def __init__(self, writer: asyncio.StreamWriter, peer: str) -> None:
        self.writer = writer
        self.peer = peer
        self.ns = 0
        self.nr = 0
        self.started = False
        self.unacked = 0
        self._write_lock = asyncio.Lock()

    async def send(self, apdu: bytes) -> None:
        async with self._write_lock:
            self.writer.write(apdu)
            await self.writer.drain()


def _parse_asdu_common_address(asdu: bytes) -> int | None:
    """ASDU baslik (DUI) byte 4-5'inden Common Address'i okur (LE 16-bit)."""
    if len(asdu) < 6:
        return None
    return struct.unpack_from("<H", asdu, 4)[0]


class IEC104Server:
    def __init__(
        self,
        *,
        name: str,
        host: str,
        port: int,
        registry: PointRegistry,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.registry = registry
        self._by_key: dict[tuple[str, str], PointAddress] = registry.by_key()
        self._values: dict[tuple[str, str], PointValue] = {}
        self._sessions: set[_ClientSession] = set()
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        logger.info(
            "iec104_server_starting name=%s host=%s port=%s default_ca=%s points=%d distinct_ca=%d",
            self.name, self.host, self.port,
            self.registry.default_common_address,
            len(self.registry.points),
            len(self.registry.unique_common_addresses()),
        )
        # reuse_address=True: SCADA target disable→enable sirasinda port'u
        # TIME_WAIT'den hizlica geri alabilelim. Aksi halde "address already
        # in use" alip yayina hic donemiyorduk.
        self._server = await asyncio.start_server(
            self._handle_client, host=self.host, port=self.port,
            reuse_address=True,
        )

    async def stop(self) -> None:
        logger.info("iec104_server_stopping name=%s", self.name)
        for session in list(self._sessions):
            try:
                session.writer.close()
            except Exception:
                logger.debug("close_writer_error", exc_info=True)
        self._sessions.clear()
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                logger.debug("server_wait_closed_error", exc_info=True)
            self._server = None

    def update_point(self, *, device_code: str, signal_key: str, value, good: bool = True) -> None:
        """Bir veri noktasinin degerini gunceller; **degisim varsa** CA ile COT=3 yayar.

        IEC 104 spontaneous reporting mantigi (Report by Exception):
          - Yeni okuma onceki yayinlanan deger ile ayni VE quality ayni ise APDU
            URETMEZ. SCADA tarafinda bos buffer ve gereksiz trafik olmaz.
          - Iki halde yayinlanir: (1) deger degisti, (2) good->bad veya bad->good
            quality gecisi.
          - Hicbir client bagli degilse de _values cache'i guncellenir; kullanici
            sonradan baglanip GI cektiginde son deger gider (mevcut akis).

        Not: analog sinyaller icin ileride deadband (orn. %1 degisim altinda
        yayinla) eklenebilir; simdilik tam esitlik kontrolu yeterli.
        """
        key = (device_code, signal_key)
        point = self._by_key.get(key)
        if point is None:
            return
        previous = self._values.get(key)
        # Cache'i her zaman yenile (daha sonraki GI dogru deger versin).
        self._values[key] = PointValue(value=value, good=good)
        if previous is not None and previous.good == good and previous.value == value:
            # Hic degisim yok — ne deger ne quality. Spontaneous APDU bastirilir.
            return
        if not self._sessions:
            return
        asdu = self._encode_single_value(point, value=value, good=good, cause=COT_SPONTANEOUS)
        if asdu is None:
            return
        for session in list(self._sessions):
            if session.started:
                asyncio.create_task(self._send_i(session, asdu))

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "?"
        session = _ClientSession(writer=writer, peer=peer_str)
        self._sessions.add(session)
        logger.info("iec104_client_connected name=%s peer=%s", self.name, peer_str)

        buffer = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                while buffer:
                    if buffer[0] != START_BYTE:
                        del buffer[0]
                        continue
                    try:
                        parsed = parse_apci(bytes(buffer))
                    except ValueError as exc:
                        logger.warning(
                            "iec104_parse_error name=%s peer=%s error=%s",
                            self.name, peer_str, exc,
                        )
                        buffer.clear()
                        break
                    if parsed is None:
                        break
                    total = 2 + parsed.length
                    del buffer[:total]
                    await self._dispatch(session, parsed)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("iec104_client_handler_crashed name=%s peer=%s", self.name, peer_str)
        finally:
            self._sessions.discard(session)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("iec104_client_disconnected name=%s peer=%s", self.name, peer_str)

    async def _dispatch(self, session: _ClientSession, frame: ParsedAPCI) -> None:
        if frame.kind.startswith("U-"):
            await self._handle_u_frame(session, frame)
            return
        if frame.kind == "S":
            session.unacked = 0
            return
        if frame.kind == "I":
            session.nr += 1
            await self._handle_i_frame(session, frame)
            if session.nr % MAX_UNACKED_I == 0:
                await session.send(build_s_frame(nr=session.nr))
            return

    async def _handle_u_frame(self, session: _ClientSession, frame: ParsedAPCI) -> None:
        tag = frame.u_tag
        if tag == APCI_U_START_ACT:
            session.started = True
            await session.send(build_u_frame(APCI_U_START_CONFIRM))
        elif tag == APCI_U_STOP_ACT:
            session.started = False
            await session.send(build_u_frame(APCI_U_STOP_CONFIRM))
        elif tag == APCI_U_TEST_ACT:
            await session.send(build_u_frame(APCI_U_TEST_CONFIRM))
        elif tag in (APCI_U_START_CONFIRM, APCI_U_STOP_CONFIRM, APCI_U_TEST_CONFIRM):
            return
        else:
            logger.warning("iec104_unknown_u_frame name=%s tag=%s", self.name, tag)

    async def _handle_i_frame(self, session: _ClientSession, frame: ParsedAPCI) -> None:
        if len(frame.asdu) < 6:
            return
        type_id = frame.asdu[0]
        if type_id == TYPE_C_IC_NA_1:
            requested_ca = _parse_asdu_common_address(frame.asdu) or 0
            await self._handle_interrogation(session, requested_ca=requested_ca)
            return
        if type_id == TYPE_C_CS_NA_1:
            # Clock sync — cevap CA'sini gelen CA ile ayni tut.
            ca = _parse_asdu_common_address(frame.asdu) or self.registry.default_common_address
            confirm = encode_interrogation_confirm(
                common_address=ca, cause=COT_ACTIVATION_CON, qoi=0,
            )
            await self._send_i(session, confirm)
            return
        logger.debug("iec104_unsupported_command name=%s type=%d", self.name, type_id)

    async def _handle_interrogation(
        self, session: _ClientSession, *, requested_ca: int,
    ) -> None:
        """C_IC_NA_1 → ACT_CON + filtrelenmis degerler (COT=20) + ACT_TERM.

        SCADA `requested_ca=0xFFFF` (broadcast) gonderirse tum CA'lara ait
        noktalar yayinlanir; aksi halde yalnizca o CA.
        """
        is_broadcast = requested_ca == BROADCAST_COMMON_ADDRESS
        # ACT_CON gelen CA ile gonderilir (broadcast'te de).
        confirm = encode_interrogation_confirm(
            common_address=requested_ca or self.registry.default_common_address,
            cause=COT_ACTIVATION_CON, qoi=20,
        )
        await self._send_i(session, confirm)

        for point in self.registry.points:
            if not is_broadcast and point.common_address != requested_ca:
                continue
            key = (point.device_code, point.signal_key)
            current = self._values.get(key)
            if current is None:
                default_value: float | int | bool = (
                    False if point.type_id == TYPE_M_SP_NA_1 else 0
                )
                asdu = self._encode_single_value(
                    point, value=default_value, good=False, cause=COT_INTERROGATION
                )
            else:
                asdu = self._encode_single_value(
                    point, value=current.value, good=current.good, cause=COT_INTERROGATION
                )
            if asdu is not None:
                await self._send_i(session, asdu)

        terminate = encode_interrogation_confirm(
            common_address=requested_ca or self.registry.default_common_address,
            cause=COT_ACTIVATION_TERM, qoi=20,
        )
        await self._send_i(session, terminate)

    def _encode_single_value(
        self, point: PointAddress, *, value, good: bool, cause: int,
    ) -> bytes | None:
        ca = point.common_address
        if point.type_id == TYPE_M_SP_NA_1:
            return encode_asdu_single_point(
                common_address=ca, cause=cause, ioa=point.ioa,
                value=bool(value), good=good,
            )
        if point.type_id == TYPE_M_ME_NC_1:
            try:
                fvalue = float(value)
            except (TypeError, ValueError):
                fvalue = 0.0
                good = False
            return encode_asdu_float(
                common_address=ca, cause=cause, ioa=point.ioa,
                value=fvalue, good=good,
            )
        if point.type_id == TYPE_M_IT_NA_1:
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                ivalue = 0
                good = False
            return encode_asdu_counter(
                common_address=ca, cause=cause, ioa=point.ioa,
                value=ivalue, good=good,
            )
        logger.warning("iec104_unknown_type name=%s type=%d", self.name, point.type_id)
        return None

    async def _send_i(self, session: _ClientSession, asdu: bytes) -> None:
        frame = build_i_frame_asdu(asdu=asdu, ns=session.ns, nr=session.nr)
        session.ns += 1
        session.unacked += 1
        try:
            await session.send(frame)
        except Exception:
            logger.warning("iec104_send_failed name=%s peer=%s", self.name, session.peer)


class IEC104ServerManager:
    """Birden fazla outbound target icin server yaratir/duragir; threadsafe update koprusu."""

    def __init__(self) -> None:
        self._servers: dict[int, IEC104Server] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def server_count(self) -> int:
        return len(self._servers)

    async def deploy(
        self, *, target_id: int, name: str, host: str, port: int, registry: PointRegistry,
    ) -> None:
        if target_id in self._servers:
            await self.undeploy(target_id)
        server = IEC104Server(name=name, host=host, port=port, registry=registry)
        await server.start()
        self._servers[target_id] = server

    async def undeploy(self, target_id: int) -> None:
        server = self._servers.pop(target_id, None)
        if server is not None:
            await server.stop()

    async def undeploy_all(self) -> None:
        for target_id in list(self._servers.keys()):
            await self.undeploy(target_id)

    def update_point_threadsafe(
        self, *, device_code: str, signal_key: str, value, good: bool = True,
    ) -> None:
        """Her aktif server'a degerin yayilmasini saglayan thread-safe koprü."""
        if self._loop is None:
            return

        def _apply() -> None:
            for server in self._servers.values():
                server.update_point(
                    device_code=device_code, signal_key=signal_key,
                    value=value, good=good,
                )

        try:
            self._loop.call_soon_threadsafe(_apply)
        except RuntimeError:
            pass


manager = IEC104ServerManager()
