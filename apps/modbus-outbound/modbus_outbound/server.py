"""Modbus TCP sunucusu (asyncio, salt okunur).

Neden elle yazildi: salt-okunur bir sunucu icin protokolun tamami 4 fonksiyon
kodundan ibaret (FC1/2/3/4) ve MBAP cercevesi 7 bayt. Harici bir kutuphane
(pymodbus) surum bagimliligi ve buyuk bir yazma/istemci yuzeyi getirirdi;
IEC 104 servisi de ayni sebeple kendi kodlayicisini kullaniyor.

MBAP cercevesi (Modbus Application Protocol header):
    [0:2] transaction id   — istemci ne yolladiysa aynen geri doner
    [2:4] protocol id      — 0 (Modbus)
    [4:6] length           — bu alandan SONRAKI bayt sayisi (unit + PDU)
    [6:7] unit id          — hedef slave (bizde: cihaz veya blok modunda sabit)
    [7:]  PDU              — function code + veri

Desteklenen fonksiyonlar:
    1  Read Coils              -> binary_output sinyalleri
    2  Read Discrete Inputs    -> binary sinyalleri
    3  Read Holding Registers  -> analog + counter
    4  Read Input Registers    -> analog + counter (3 ile AYNI icerik)

Yazma fonksiyonlari (5/6/15/16) bilincli olarak reddedilir: bu kanal
salt-okunur bir SCADA beslemesidir. Modbus'ta kimlik dogrulama yoktur; yazmaya
izin vermek, agdaki herkese cihaz komutu verme yetkisi vermek olurdu.
"""

from __future__ import annotations

import asyncio
import logging
import struct

from modbus_outbound.registry import (
    FC_COIL,
    FC_DISCRETE_INPUT,
    FC_HOLDING_REGISTER,
    FC_INPUT_REGISTER,
    MODBUS_ADDRESS_SPACE,
    PointRegistry,
)

logger = logging.getLogger(__name__)

MBAP_HEADER_LEN = 7
MODBUS_PROTOCOL_ID = 0
MAX_PDU_LEN = 253

# Istek basina okuma sinirlari (Modbus spesifikasyonu).
MAX_READ_REGISTERS = 125
MAX_READ_BITS = 2000

READ_FUNCTIONS = (FC_COIL, FC_DISCRETE_INPUT, FC_HOLDING_REGISTER, FC_INPUT_REGISTER)
WRITE_FUNCTIONS = (5, 6, 15, 16, 22, 23)

# Modbus exception kodlari
EXC_ILLEGAL_FUNCTION = 0x01
EXC_ILLEGAL_DATA_ADDRESS = 0x02
EXC_ILLEGAL_DATA_VALUE = 0x03
EXC_GATEWAY_TARGET_FAILED = 0x0B

# Bosta bekleyen baglantiyi kapatma suresi. SCADA genelde surekli sorar;
# yarim kalan TCP oturumlari birikip dosya tanimlayicisi tuketmesin.
IDLE_TIMEOUT_SEC = 300


def _bits_to_bytes(bits: list[bool]) -> bytes:
    """Bit listesi -> Modbus'un bekledigi paketlenmis bayt dizisi (LSB once)."""
    out = bytearray((len(bits) + 7) // 8)
    for index, bit in enumerate(bits):
        if bit:
            out[index // 8] |= 1 << (index % 8)
    return bytes(out)


def build_exception(function: int, code: int) -> bytes:
    return struct.pack(">BB", function | 0x80, code)


def handle_pdu(registry: PointRegistry, unit_id: int, pdu: bytes) -> bytes:
    """Bir Modbus PDU'sunu isle, cevap PDU'sunu dondur.

    Saf fonksiyon (IO yok) — testten dogrudan cagrilabilir.
    """
    if not pdu:
        return build_exception(0, EXC_ILLEGAL_FUNCTION)

    function = pdu[0]

    if function in WRITE_FUNCTIONS:
        # Salt okunur kanal: yazma denemesi acikca reddedilir.
        logger.warning(
            "modbus_write_rejected target=%d unit=%d fc=%d",
            registry.target_id, unit_id, function,
        )
        return build_exception(function, EXC_ILLEGAL_FUNCTION)

    if function not in READ_FUNCTIONS:
        return build_exception(function, EXC_ILLEGAL_FUNCTION)

    if len(pdu) < 5:
        return build_exception(function, EXC_ILLEGAL_DATA_VALUE)

    address, count = struct.unpack(">HH", pdu[1:5])

    is_bit_read = function in (FC_COIL, FC_DISCRETE_INPUT)
    max_count = MAX_READ_BITS if is_bit_read else MAX_READ_REGISTERS
    if count < 1 or count > max_count:
        return build_exception(function, EXC_ILLEGAL_DATA_VALUE)
    if address + count > MODBUS_ADDRESS_SPACE:
        return build_exception(function, EXC_ILLEGAL_DATA_ADDRESS)

    values = registry.read(unit_id, function, address, count)
    if values is None:
        # Tanimsiz unit id — Modbus TCP'de gateway'in verdigi standart cevap.
        return build_exception(function, EXC_GATEWAY_TARGET_FAILED)

    if is_bit_read:
        payload = _bits_to_bytes(list(values))
    else:
        payload = b"".join(struct.pack(">H", int(v) & 0xFFFF) for v in values)
    return struct.pack(">BB", function, len(payload)) + payload


class ModbusTargetServer:
    """Tek bir outbound hedefi icin TCP sunucusu."""

    def __init__(
        self,
        *,
        target_id: int,
        name: str,
        host: str,
        port: int,
        registry: PointRegistry,
        allowed_peers: tuple[str, ...] = (),
    ) -> None:
        self.target_id = target_id
        self.name = name
        self.host = host
        self.port = port
        self.registry = registry
        self.allowed_peers = allowed_peers
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self.requests_served = 0
        self.rejected_peers = 0

    @property
    def connected_clients(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, host=self.host, port=self.port
        )
        logger.info(
            "modbus_server_started target=%d name=%s addr=%s:%d units=%d points=%d",
            self.target_id, self.name, self.host, self.port,
            len(self.registry.stores), self.registry.point_count,
        )

    async def stop(self) -> None:
        for writer in list(self._clients):
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        logger.info("modbus_server_stopped target=%d name=%s", self.target_id, self.name)

    def _peer_allowed(self, peer_ip: str) -> bool:
        if not self.allowed_peers:
            return True
        return peer_ip in self.allowed_peers

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        peer_ip = str(peer[0])
        if not self._peer_allowed(peer_ip):
            self.rejected_peers += 1
            logger.warning(
                "modbus_peer_rejected target=%d peer=%s (allowlist)",
                self.target_id, peer_ip,
            )
            writer.close()
            return

        self._clients.add(writer)
        logger.info("modbus_client_connected target=%d peer=%s", self.target_id, peer_ip)
        try:
            while True:
                try:
                    header = await asyncio.wait_for(
                        reader.readexactly(MBAP_HEADER_LEN), timeout=IDLE_TIMEOUT_SEC
                    )
                except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                    break
                except ConnectionError:
                    break

                tid, pid, length, unit_id = struct.unpack(">HHHB", header)
                if pid != MODBUS_PROTOCOL_ID:
                    break
                # length = unit(1) + PDU. Bozuk/asiri uzun cerceve -> baglantiyi kes;
                # cerceve senkronu kaybedilmisse devam etmenin anlami yok.
                pdu_len = length - 1
                if pdu_len < 1 or pdu_len > MAX_PDU_LEN:
                    break
                try:
                    pdu = await reader.readexactly(pdu_len)
                except (asyncio.IncompleteReadError, ConnectionError):
                    break

                response_pdu = handle_pdu(self.registry, unit_id, pdu)
                frame = struct.pack(
                    ">HHHB", tid, MODBUS_PROTOCOL_ID, len(response_pdu) + 1, unit_id
                ) + response_pdu
                writer.write(frame)
                try:
                    await writer.drain()
                except ConnectionError:
                    break
                self.requests_served += 1
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
            logger.info(
                "modbus_client_disconnected target=%d peer=%s served=%d",
                self.target_id, peer_ip, self.requests_served,
            )


class ModbusServerManager:
    """Tum aktif Modbus hedeflerini yonetir (deploy/undeploy/guncelleme)."""

    def __init__(self) -> None:
        self._servers: dict[int, ModbusTargetServer] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def server_count(self) -> int:
        return len(self._servers)

    def is_running(self, target_id: int) -> bool:
        return target_id in self._servers

    def connected_clients(self, target_id: int) -> int:
        server = self._servers.get(target_id)
        return server.connected_clients if server else 0

    def runtime_snapshot(self) -> list[dict]:
        return [
            {
                "target_id": s.target_id,
                "name": s.name,
                "listen": f"{s.host}:{s.port}",
                "units": len(s.registry.stores),
                "points": s.registry.point_count,
                "connected_clients": s.connected_clients,
                "requests_served": s.requests_served,
                "rejected_peers": s.rejected_peers,
                "updates_applied": s.registry.updates_applied,
                "updates_unmapped": s.registry.updates_unmapped,
                "updates_uncoercible": s.registry.updates_uncoercible,
                # Son bilinen deger tazelemesi (bkz. registry.apply_snapshot).
                "snapshot_seeded": s.registry.snapshot_seeded,
                "snapshot_refreshed": s.registry.snapshot_refreshed,
                "snapshot_stale_skipped": s.registry.snapshot_stale_skipped,
                "snapshot_unmapped": s.registry.snapshot_unmapped,
            }
            for s in self._servers.values()
        ]

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
        """Hedefi (yeniden) ayaga kaldir.

        Mevcut degerler KORUNUR: plan degistiginde (yeni cihaz eklendi vb.)
        server yeniden kurulur ama daha once alinmis olcumler yeni registry'ye
        tasinir; aksi halde her plan degisikliginde SCADA bir sonraki telemetri
        turuna kadar sifir gorurdu.
        """
        existing = self._servers.get(target_id)
        if existing is not None:
            _carry_over_values(existing.registry, registry)
            await existing.stop()
            self._servers.pop(target_id, None)

        server = ModbusTargetServer(
            target_id=target_id, name=name, host=host, port=port,
            registry=registry, allowed_peers=allowed_peers,
        )
        await server.start()
        self._servers[target_id] = server

    async def undeploy(self, target_id: int) -> None:
        server = self._servers.pop(target_id, None)
        if server is not None:
            await server.stop()

    async def undeploy_all(self) -> None:
        for target_id in list(self._servers.keys()):
            await self.undeploy(target_id)

    def update_point(
        self, device_code: str, signal_key: str, value, source_timestamp=None
    ) -> int:
        """Tum hedeflerde bu sinyali guncelle. Thread-safe (registry kilitli)."""
        total = 0
        for server in list(self._servers.values()):
            total += server.registry.update(
                device_code, signal_key, value, source_timestamp
            )
        return total

    def apply_snapshot(self, rows: list[dict]) -> dict:
        """Son bilinen degerleri TUM hedeflere uygula; toplam sayaclari doner.

        Her hedefin kendi adres plani ve kendi damga defteri var; ayni satir
        bir hedefte "ilk deger" digerinde "bayat" olabilir.
        """
        toplam = {
            "targets": 0, "seeded": 0, "refreshed": 0,
            "stale": 0, "unmapped": 0, "uncoercible": 0,
        }
        for server in list(self._servers.values()):
            sonuc = server.registry.apply_snapshot(rows)
            toplam["targets"] += 1
            for anahtar in ("seeded", "refreshed", "stale", "unmapped", "uncoercible"):
                toplam[anahtar] += sonuc[anahtar]
        return toplam


def _carry_over_values(old: PointRegistry, new: PointRegistry) -> None:
    """Yeniden deploy'da mevcut olcumleri yeni registry'ye tasi.

    Adresler degismis olabilir; bu yuzden ham store kopyalanmaz, eski
    store'daki degerler AYNI (unit, fonksiyon, adres) uclusune yazilir.
    Adresi degisen noktalar bir sonraki telemetride yerine oturur.

    DAMGA DEFTERI (`_last_ts`) BILINCLI OLARAK TASINMAZ. Tasinsa, adresi
    degisen bir nokta icin snapshot tazelemesi "bu deger zaten guncel" der ve
    YENI adres bir sonraki canli olcume kadar 0 kalirdi — duzeltmeye
    calistigimiz arizanin ta kendisi. Bos defterle bir sonraki tazeleme tur
    (plan degisiminden hemen sonra zorlanir) her noktayi yeniden yazar.
    """
    for unit_id, store in old.stores.items():
        target_store = new.stores.get(unit_id)
        if target_store is None:
            continue
        target_store.registers.update(store.registers)
        target_store.discrete_inputs.update(store.discrete_inputs)
        target_store.coils.update(store.coils)


manager = ModbusServerManager()
