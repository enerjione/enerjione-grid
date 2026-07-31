"""IEC 60870-5-104 asyncio TCP server (cihaz bazli ASDU CA destegi).

Yetenekler:

  1. **Spontaneous transmission (COT=3)** — `update_point()` cagirilirsa server
     ilgili client baglantilarina sinyalin ait oldugu cihazin Common Address'i
     ile ASDU yayinlar.
  2. **General interrogation (C_IC_NA_1)** — client'in gonderdigi ASDU'daki CA
     0xFFFF ise tum CA'lar; aksi halde yalnizca o CA. Sonuna ACT_TERM (cause=10).
  3. STARTDT/STOPDT/TESTFR + clock sync (ack-only).

Bu server backend-api FastAPI lifespan icinde calisir. Daha kuvvetli/paralel
calisma icin disardaki `apps/iec104-outbound` servisi ayni davranisi
implemente eder; iki yer ayni TCP portunu paylasamaz.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass

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
from app.services.iec104.registry import (
    BROADCAST_COMMON_ADDRESS,
    PointAddress,
    PointRegistry,
)

logger = logging.getLogger(__name__)

MAX_UNACKED_I = 12

# Oturum basina giden ASDU kuyrugu tavani.
#
# NEDEN SINIR VAR: burada bir zamanlar her deger degisimi icin
# `asyncio.create_task(self._send_i(...))` cagriliyordu — sinirsiz, referans
# tutulmadan. SCADA istemcisi yavasladiginda `drain()` bloklanir ve her yeni
# degisim BIR GOREV DAHA yaratirdi; 600 cihaz olceginde saniyede binlerce.
# Gorevler yazma kilidinde kuyruga girip bellegi OOM'a kadar sisirirdi.
# (Ayrica referanssiz gorevler cop toplayici tarafindan yarida
# kesilebiliyordu — CPython'un bilinen tuzagi.)
#
# 2000 ASDU ~ birkac saniyelik spontane trafik: gecici tikanikliklari yutar,
# kalici tikanikligi ise dusurerek GORUNUR kilar.
OUTBOX_MAX = 2000

# Bir target'a ayni anda bagli olabilecek en fazla istemci.
#
# NEDEN SINIR VAR: her oturum, her deger degisiminde ek is demektir. Yeniden
# baglanma dongusune giren bir istemci ya da yanlis yapilandirilmis bir SCADA
# onlarca oturum acabilir ve sunucuyu kendi kendine bogar. Gercek kurulumda
# bir target'a birkac master baglanir; 16 fazlasiyla yeterli.
MAX_SESSIONS = 16


@dataclass
class PointValue:
    value: float | int | bool
    good: bool = True


class _ClientSession:
    def __init__(self, writer: asyncio.StreamWriter, peer: str) -> None:
        self.writer = writer
        self.peer = peer
        self.ns = 0
        self.nr = 0
        self.started = False
        self.unacked = 0
        self._write_lock = asyncio.Lock()
        # Runtime istatigi: bu client ne zaman bagli, monotonic saat degil iso ts
        # frontend "X dakika once" hesaplayabilsin diye.
        self.connected_at_iso: str = ""
        # Giden ASDU kuyrugu — SINIRLI. Bkz. IEC104Server.update_value.
        self.outbox: asyncio.Queue[bytes] = asyncio.Queue(maxsize=OUTBOX_MAX)
        self.dropped_total = 0
        self._drain_task: asyncio.Task | None = None

    async def send(self, apdu: bytes) -> None:
        async with self._write_lock:
            self.writer.write(apdu)
            await self.writer.drain()

    def enqueue(self, asdu: bytes) -> bool:
        """ASDU'yu giden kuyruga koyar. Kuyruk doluysa EN ESKIYI atar.

        Neden en eskiyi: IEC 104 spontane bildiriminde son deger gecerlidir.
        Yeni geleni atmak, guncel degeri atip bayat degeri gondermek olurdu —
        yani tam tersi. Dusen sayilir ve loglanir; sessiz kayip olmaz.
        """
        try:
            self.outbox.put_nowait(asdu)
            return True
        except asyncio.QueueFull:
            try:
                self.outbox.get_nowait()
                self.outbox.task_done()
                self.dropped_total += 1
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
            try:
                self.outbox.put_nowait(asdu)
            except asyncio.QueueFull:  # pragma: no cover
                self.dropped_total += 1
                return False
            return False


def _parse_asdu_common_address(asdu: bytes) -> int | None:
    if len(asdu) < 6:
        return None
    return struct.unpack_from("<H", asdu, 4)[0]


class IEC104Server:
    """Tek bir OutboundTarget icin IEC 104 slave server'i."""

    def __init__(
        self,
        *,
        name: str,
        host: str,
        port: int,
        registry: PointRegistry,
        allowed_peers: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.registry = registry
        self.allowed_peers = allowed_peers
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
        # reuse_address=True: target disable→enable sirasinda port'u TIME_WAIT'den
        # hizlica geri alabilelim.
        self._server = await asyncio.start_server(
            self._handle_client, host=self.host, port=self.port,
            reuse_address=True,
        )

    async def stop(self) -> None:
        logger.info("iec104_server_stopping name=%s", self.name)
        for session in list(self._sessions):
            # Once yazici gorevi iptal et, sonra soketi kapat. Ters sirada
            # yapilsaydi gorev kapali sokete yazmaya calisip gurultu uretirdi.
            task = session._drain_task
            session._drain_task = None
            if task is not None:
                task.cancel()
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

    def update_point(self, *, device_code: str, signal_key: str, value, good: bool = True) -> None:
        """Bir veri noktasinin degerini gunceller; **degisim varsa** spontaneous (COT=3) yayar.

        IEC 104 Report by Exception: ayni deger + ayni quality tekrar geldiginde
        APDU uretmez; ne SCADA buffer'i dolar ne ag yuku artar. Yalniz deger
        veya quality degistiginde tetiklenir. Cache her durumda guncellenir
        ki sonradan baglanan client GI cektiginde son deger gitsin.
        """
        key = (device_code, signal_key)
        point = self._by_key.get(key)
        if point is None:
            return
        previous = self._values.get(key)
        self._values[key] = PointValue(value=value, good=good)
        if previous is not None and previous.good == good and previous.value == value:
            return
        if not self._sessions:
            return
        asdu = self._encode_single_value(point, value=value, good=good, cause=COT_SPONTANEOUS)
        if asdu is None:
            return
        for session in list(self._sessions):
            if not session.started:
                continue
            # Gorev YARATMIYORUZ — kuyruga koyuyoruz. Her oturumun TEK bir
            # yazici gorevi var (bkz. _drain_outbox). Bu iki seyi ayni anda
            # cozuyor:
            #   * sinirsiz gorev birikmesi (yavas istemci -> OOM),
            #   * ns/nr yaris durumu: sira numarasi artik TEK yerde, gonderim
            #     aninda atanir, dolayisiyla tel uzerindeki sira ns ile daima
            #     tutarli. Onceden ns kilit DISINDA artiyordu ve iki eszamanli
            #     gorev cerceveleri ns sirasindan FARKLI gonderebiliyordu —
            #     master bunu gorunce baglantiyi dusurur.
            if not session.enqueue(asdu):
                logger.warning(
                    "iec104_outbox_overflow name=%s peer=%s dropped_total=%d — "
                    "istemci akisa yetisemiyor, en eski bildirimler atiliyor",
                    self.name,
                    session.peer,
                    session.dropped_total,
                )

    def connected_clients(self) -> list[dict]:
        """Backend API runtime endpoint'i icin canli baglanti ozeti."""
        return [
            {
                "peer": s.peer,
                "started": s.started,
                "connected_at": s.connected_at_iso,
            }
            for s in list(self._sessions)
        ]

    def _peer_allowed(self, peer_ip: str) -> bool:
        """Whitelist bos = serbest. Dolu ise IP eslesmesi (CIDR yok, sadece IP)."""
        if not self.allowed_peers:
            return True
        return peer_ip in self.allowed_peers

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        from datetime import datetime, timezone
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else ""
        peer_str = f"{peer_ip}:{peer[1]}" if peer else "?"
        if not self._peer_allowed(peer_ip):
            logger.warning(
                "iec104_client_rejected_whitelist name=%s peer=%s", self.name, peer_str,
            )
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
            return
        # Oturum tavani: yeniden baglanma dongusune giren ya da yanlis
        # yapilandirilmis bir istemci onlarca oturum acabilir. Her oturum, HER
        # deger degisiminde ek is demektir; sinirsiz birakmak sunucunun kendi
        # kendini bogmasina yol acar.
        if len(self._sessions) >= MAX_SESSIONS:
            logger.warning(
                "iec104_client_rejected_limit name=%s peer=%s active=%d limit=%d",
                self.name, peer_str, len(self._sessions), MAX_SESSIONS,
            )
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
            return

        session = _ClientSession(writer=writer, peer=peer_str)
        session.connected_at_iso = datetime.now(timezone.utc).isoformat()
        self._sessions.add(session)
        # Oturum basina TEK yazici gorev. Referans session'da tutulur: aksi
        # halde gorev cop toplayici tarafindan yarida kesilebilir.
        session._drain_task = asyncio.create_task(self._drain_outbox(session))
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
        except Exception:  # noqa: BLE001
            logger.exception("iec104_client_handler_crashed name=%s peer=%s", self.name, peer_str)
        finally:
            self._sessions.discard(session)
            # Yazici gorevi MUTLAKA iptal edilmeli: `outbox.get()` uzerinde
            # sonsuza kadar bekler ve oturum kapansa bile yasamaya devam
            # ederdi — her yeniden baglanmada bir gorev daha sizardi.
            task = session._drain_task
            session._drain_task = None
            if task is not None:
                task.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            if session.dropped_total:
                logger.warning(
                    "iec104_client_disconnected_with_drops name=%s peer=%s dropped=%d",
                    self.name, peer_str, session.dropped_total,
                )
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
        is_broadcast = requested_ca == BROADCAST_COMMON_ADDRESS
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
        """TEK bir ASDU'yu I-frame olarak gonderir.

        Sira numarasi (ns) burada, gonderimin hemen oncesinde atanir ve
        cagiran taraf serilestirilmis olmalidir: spontane bildirimler tek
        yazici gorevden (`_drain_outbox`), komut yanitlari ise istemcinin
        kendi okuma dongusunden gelir. Ikisi de ayni oturumda es zamanli
        calismaz, dolayisiyla ns tel sirasiyla daima tutarlidir.
        """
        frame = build_i_frame_asdu(asdu=asdu, ns=session.ns, nr=session.nr)
        session.ns += 1
        session.unacked += 1
        try:
            await session.send(frame)
        except Exception:  # noqa: BLE001
            logger.warning("iec104_send_failed name=%s peer=%s", self.name, session.peer)

    async def _drain_outbox(self, session: _ClientSession) -> None:
        """Oturumun giden kuyrugunu SIRAYLA bosaltir (oturum basina tek gorev).

        Yavas istemci burada geri basinc yaratir: kuyruk dolar ve en eski
        bildirimler dusurulur (bkz. _ClientSession.enqueue). Onceden bunun
        yerine gorev sayisi buyurdu; bellek sinirsiz artiyordu.
        """
        try:
            while True:
                asdu = await session.outbox.get()
                try:
                    await self._send_i(session, asdu)
                except Exception:  # noqa: BLE001
                    logger.debug("iec104_drain_send_error", exc_info=True)
                finally:
                    session.outbox.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                "iec104_drain_crashed name=%s peer=%s", self.name, session.peer
            )


class IEC104ServerManager:
    """Birden fazla target icin server yaratir/duragir; threadsafe update koprusu."""

    def __init__(self) -> None:
        self._servers: dict[int, IEC104Server] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def deploy(
        self,
        *,
        target_id: int,
        name: str,
        host: str,
        port: int,
        registry: PointRegistry,
        allowed_peers: tuple[str, ...] = (),
    ) -> None:
        if target_id in self._servers:
            await self.undeploy(target_id)
        server = IEC104Server(
            name=name, host=host, port=port, registry=registry,
            allowed_peers=allowed_peers,
        )
        await server.start()
        self._servers[target_id] = server

    def connected_clients(self, target_id: int) -> list[dict]:
        """Belirtilen target icin runtime baglanti listesi. Yoksa [] doner."""
        server = self._servers.get(target_id)
        if server is None:
            return []
        return server.connected_clients()

    def is_running(self, target_id: int) -> bool:
        return target_id in self._servers

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


# Modul-seviyesi singleton (FastAPI app state ile paylasilir).
manager = IEC104ServerManager()
