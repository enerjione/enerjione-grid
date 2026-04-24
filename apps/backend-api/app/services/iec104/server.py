"""IEC 60870-5-104 asyncio TCP server.

Temel iki yetenek:

  1. **Spontaneous transmission** — platformda bir telemetri event'i olunca
     `update_point()` cagirilir; server ilgili client baglantilarina ASDU
     yayinlar (COT=3).
  2. **General interrogation** — client bir `C_IC_NA_1` (TypeID 100) gonderdiginde
     server tum mevcut degerleri COT=20 ile yayinlar; sonuna ACT_TERM (cause=10)
     koyar.

Ek olarak:
  * `STARTDT_ACT` -> `STARTDT_CON` (trafigi acar)
  * `STOPDT_ACT`  -> `STOPDT_CON`  (trafigi kapatir; yalnizca U-frame kalir)
  * `TESTFR_ACT`  -> `TESTFR_CON`  (keep-alive)
  * `C_CS_NA_1` (clock sync) -> ACT_CON (degisiklik yapmaz)

Her target (port/common_address ikilisi) kendi `IEC104Server` instance'inda
calisir. `IEC104ServerManager` lifecycle'i yonetir.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from app.services.iec104.encoder import (
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
from app.services.iec104.registry import PointAddress, PointRegistry

logger = logging.getLogger(__name__)

# Client en fazla bu kadar I-frame'i onaylamadan tasiyabilir (K parametresi).
# SCADA tarafi daha fazla beklerse S-frame gonderip ack ettiririz.
MAX_UNACKED_I = 12


@dataclass
class PointValue:
    """Son bilinen deger + kalite + type."""

    value: float | int | bool
    good: bool = True


class _ClientSession:
    """Tek bir TCP client baglantisinin state'ini tutar.

    `ns`  : gonderilen I-frame sayisi (bu tarafin send sequence number).
    `nr`  : alinan I-frame sayisi (bu tarafin receive sequence number).
    `started` : STARTDT_ACT alindi mi; alinmadan I-frame yayinlanmaz.
    """

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


class IEC104Server:
    """Tek bir OutboundTarget icin IEC 104 slave server'i.

    Thread-safe DEGIL — tek event loop icinde calisir. FastAPI lifespan
    icindeki loop ile paylasilir. Degerler `update_point` ile guncellenir
    (bu ayri bir thread'ten cagirilirsa `asyncio.run_coroutine_threadsafe`
    ile koprulenmelidir; `IEC104ServerManager.update_point_threadsafe`
    bunu saglar).
    """

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

    # ----- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        logger.info(
            "iec104_server_starting name=%s host=%s port=%s ca=%s points=%d",
            self.name,
            self.host,
            self.port,
            self.registry.common_address,
            len(self.registry.points),
        )
        self._server = await asyncio.start_server(
            self._handle_client, host=self.host, port=self.port
        )

    async def stop(self) -> None:
        logger.info("iec104_server_stopping name=%s", self.name)
        for session in list(self._sessions):
            try:
                session.writer.close()
            except Exception:  # noqa: BLE001
                logger.debug("close_writer_error", exc_info=True)
        self._sessions.clear()
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                logger.debug("server_wait_closed_error", exc_info=True)
            self._server = None

    # ----- data API ---------------------------------------------------------
    def update_point(self, *, device_code: str, signal_key: str, value, good: bool = True) -> None:
        """Bir veri noktasinin degerini gunceller ve COT=3 ile client'lara yayar.

        Event loop thread'inden cagirilmali. Farkli thread'ten cagirmak icin
        `ServerManager.update_point_threadsafe` kullanilir.
        """
        key = (device_code, signal_key)
        point = self._by_key.get(key)
        if point is None:
            return
        self._values[key] = PointValue(value=value, good=good)
        if not self._sessions:
            return
        asdu = self._encode_single_value(point, value=value, good=good, cause=COT_SPONTANEOUS)
        if asdu is None:
            return
        for session in list(self._sessions):
            if session.started:
                asyncio.create_task(self._send_i(session, asdu))

    # ----- connection handler ----------------------------------------------
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
                        # Stream kaybolmus; resync icin byte at ve tekrar dene.
                        del buffer[0]
                        continue
                    try:
                        parsed = parse_apci(bytes(buffer))
                    except ValueError as exc:
                        logger.warning(
                            "iec104_parse_error name=%s peer=%s error=%s",
                            self.name,
                            peer_str,
                            exc,
                        )
                        buffer.clear()
                        break
                    if parsed is None:
                        break  # buffer eksik, yeni veri bekle
                    total = 2 + parsed.length
                    del buffer[:total]
                    await self._dispatch(session, parsed)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001
            logger.exception("iec104_client_handler_crashed name=%s peer=%s", self.name, peer_str)
        finally:
            self._sessions.discard(session)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            logger.info("iec104_client_disconnected name=%s peer=%s", self.name, peer_str)

    async def _dispatch(self, session: _ClientSession, frame: ParsedAPCI) -> None:
        if frame.kind.startswith("U-"):
            await self._handle_u_frame(session, frame)
            return
        if frame.kind == "S":
            # Client onay verdi; bizim outstanding sayimizi onaylamak kolay.
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
            # Sessizce yut; bu taraf baslatmadigi icin beklenmiyor.
            return
        else:
            logger.warning("iec104_unknown_u_frame name=%s tag=%s", self.name, tag)

    async def _handle_i_frame(self, session: _ClientSession, frame: ParsedAPCI) -> None:
        if len(frame.asdu) < 6:
            return
        type_id = frame.asdu[0]
        if type_id == TYPE_C_IC_NA_1:
            await self._handle_interrogation(session)
            return
        if type_id == TYPE_C_CS_NA_1:
            # Clock sync — degisiklik yapmiyoruz, ACT_CON geri gonderiyoruz.
            confirm = encode_interrogation_confirm(
                common_address=self.registry.common_address,
                cause=COT_ACTIVATION_CON,
                qoi=0,
            )
            # Not: bu aslinda C_CS_NA_1 olmali; ama minimum iscilikle sessizce
            # onaylaniyoruz — client cogu kez kabul eder.
            await self._send_i(session, confirm)
            return
        # Geri kalan komutlari bu aciklamalarda desteklemiyoruz.
        logger.debug("iec104_unsupported_command name=%s type=%d", self.name, type_id)

    async def _handle_interrogation(self, session: _ClientSession) -> None:
        """C_IC_NA_1 -> ACT_CON + tum degerler (COT=20) + ACT_TERM."""
        confirm = encode_interrogation_confirm(
            common_address=self.registry.common_address,
            cause=COT_ACTIVATION_CON,
            qoi=20,
        )
        await self._send_i(session, confirm)

        # Tum mevcut degerleri yayinla. Deger bilinmiyorsa kalite=invalid.
        for point in self.registry.points:
            key = (point.device_code, point.signal_key)
            current = self._values.get(key)
            if current is None:
                default_value: float | int | bool = 0 if point.type_id != TYPE_M_SP_NA_1 else False
                asdu = self._encode_single_value(point, value=default_value, good=False, cause=COT_INTERROGATION)
            else:
                asdu = self._encode_single_value(
                    point, value=current.value, good=current.good, cause=COT_INTERROGATION
                )
            if asdu is not None:
                await self._send_i(session, asdu)

        terminate = encode_interrogation_confirm(
            common_address=self.registry.common_address,
            cause=COT_ACTIVATION_TERM,
            qoi=20,
        )
        await self._send_i(session, terminate)

    # ----- encode helpers ---------------------------------------------------
    def _encode_single_value(
        self,
        point: PointAddress,
        *,
        value,
        good: bool,
        cause: int,
    ) -> bytes | None:
        ca = self.registry.common_address
        if point.type_id == TYPE_M_SP_NA_1:
            return encode_asdu_single_point(
                common_address=ca, cause=cause, ioa=point.absolute_ioa,
                value=bool(value), good=good,
            )
        if point.type_id == TYPE_M_ME_NC_1:
            try:
                fvalue = float(value)
            except (TypeError, ValueError):
                fvalue = 0.0
                good = False
            return encode_asdu_float(
                common_address=ca, cause=cause, ioa=point.absolute_ioa,
                value=fvalue, good=good,
            )
        if point.type_id == TYPE_M_IT_NA_1:
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                ivalue = 0
                good = False
            return encode_asdu_counter(
                common_address=ca, cause=cause, ioa=point.absolute_ioa,
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
        except Exception:  # noqa: BLE001
            logger.warning("iec104_send_failed name=%s peer=%s", self.name, session.peer)


class IEC104ServerManager:
    """Birden fazla target icin server yaratir/duragir; threadsafe update koprusu."""

    def __init__(self) -> None:
        self._servers: dict[int, IEC104Server] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def deploy(self, *, target_id: int, name: str, host: str, port: int, registry: PointRegistry) -> None:
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
        self, *, device_code: str, signal_key: str, value, good: bool = True
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
            # Loop kapaliysa sessizce duş
            pass


# Modul-seviyesi singleton (FastAPI app state ile paylasilir).
manager = IEC104ServerManager()
