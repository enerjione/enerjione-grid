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
import time as _time
from dataclasses import dataclass
from datetime import datetime

from iec104_outbound.command_client import (
    COMMAND_RESULT_POLL_SEC,
    COMMAND_RESULT_TIMEOUT_SEC,
    TERMINAL_FAIL,
    TERMINAL_OK,
    CommandClient,
    CommandRejected,
)
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
    TYPE_C_SC_NA_1,
    TYPE_M_IT_NA_1,
    TYPE_M_ME_NC_1,
    TYPE_M_SP_NA_1,
    ParsedAPCI,
    build_i_frame_asdu,
    build_s_frame,
    build_u_frame,
    encode_asdu_counter,
    encode_asdu_float,
    encode_asdu_float_t,
    encode_asdu_single_point,
    encode_asdu_single_point_t,
    encode_interrogation_confirm,
    encode_single_command_reply,
    parse_apci,
    parse_single_command,
)
from iec104_outbound.registry import (
    BROADCAST_COMMON_ADDRESS,
    PointAddress,
    PointRegistry,
)

logger = logging.getLogger(__name__)

# Client en fazla bu kadar I-frame'i onaylamadan tasiyabilir (K parametresi).
MAX_UNACKED_I = 12

# IEC 60870-5-104 t3 — bosta kalma suresi. Bu kadar sure hicbir sey gelmezse
# TEST_ACT (keepalive) gonderilir; standardin onerdigi varsayilan 20 sn.
#
# NEDEN SART: bu zamanlayici olmadan yari-acik bir TCP baglantisi HIC fark
# edilmiyordu. Master donar ya da aradaki switch yeniden baslarsa FIN gelmez;
# sunucu 12 I-frame gonderip S-frame bekler, `unacked` 12'de sabitlenir ve o
# oturuma giden TUM telemetri sessizce duser. Yazma da durdugu icin TCP'nin
# kendisi de kopuklugu anlayamaz.
T3_IDLE_SEC = 20.0

# k-penceresi dolu uyarisinin oturum basina en sik basilma araligi.
# Rate-limit yokken her dusen frame bir WARNING uretiyordu; 600 cihaz yukunde
# log dosyalari saniyeler icinde donuyor ve ariza aninda bakilacak diger
# kayitlar supuruluyordu.
KWINDOW_WARN_INTERVAL_SEC = 30.0

# Oturum basina giden ASDU kuyrugu tavani.
#
# NEDEN SINIR VAR: burada her deger degisimi icin
# `asyncio.create_task(self._send_i(...))` cagriliyordu — sinirsiz ve referans
# tutulmadan. SCADA istemcisi yavasladiginda `drain()` bloklanir ve her yeni
# degisim BIR GOREV DAHA yaratirdi; 600 cihaz olceginde saniyede binlerce.
# Gorevler yazma kilidinde kuyruga girip bellegi OOM'a kadar sisirirdi.
# Ayrica referanssiz gorevler cop toplayici tarafindan yarida kesilebiliyordu
# (CPython'un bilinen tuzagi) ve `ns` sira numarasi gorevler arasinda YARISA
# giriyordu — tel sirasi bozulunca master tum oturumu reddedebiliyordu.
#
# NOT: Bu sertlestirme once yalnizca backend icindeki IEC104 kopyasina
# uygulanmisti. SCADA'nin gercekten baglandigi servis BU DOSYADIR
# (docker-compose 2404-2406 portlarini bu container'da yayinliyor), yani
# koruma calismayan tarafa konmustu.
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

# k-penceresi dolduysa yazici gorev bu kadar bekler; acilmazsa oturum OLU
# sayilir. Master S-frame ile ack atmayi tamamen birakmissa daha fazla
# beklemenin anlami yok.
K_WINDOW_WAIT_SEC = 30.0


@dataclass
class PointValue:
    value: float | int | bool
    good: bool = True
    # Telemetri kaynak zaman damgasi (UTC). CP56Time2a tasiyan tip'lerde
    # (M_SP_TB_1, M_ME_TF_1) bu deger frame'e gomulur. None ise gonderim
    # sirasinda "simdiki" zaman kullanilir.
    timestamp: "datetime | None" = None


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
        self.connected_at_iso: str = ""
        # TEST_ACT gonderildi, cevap (TESTFR_CON) bekleniyor. Ikinci bir
        # bosluk turunda hala True ise baglanti OLU sayilir.
        self.test_pending = False
        # k-penceresi dolu uyarisinin son basildigi an (monotonic).
        # Rate-limit YOK iken her dusen frame icin WARNING basiliyordu; 600
        # cihaz yukunde bu, log dosyalarini saniyeler icinde dondurup DIGER
        # tum teshis kayitlarini supuruyordu.
        self.last_kwindow_warn_at: float = 0.0
        # Giden ASDU kuyrugu — SINIRLI. Bkz. OUTBOX_MAX.
        self.outbox: asyncio.Queue[bytes] = asyncio.Queue(maxsize=OUTBOX_MAX)
        self.dropped_total = 0
        self._drain_task: "asyncio.Task | None" = None
        # Genel sorgu (GI) AYRI gorevde kosar; bkz. _start_interrogation.
        self._gi_task: "asyncio.Task | None" = None
        # k-penceresinde yer acildiginda set edilir (S-frame geldiginde).
        # Baslangicta acik.
        self.window_event: asyncio.Event = asyncio.Event()
        self.window_event.set()

    async def send(self, apdu: bytes) -> None:
        async with self._write_lock:
            self.writer.write(apdu)
            await self.writer.drain()

    def enqueue(self, asdu: bytes) -> bool:
        """ASDU'yu giden kuyruga koyar. Kuyruk doluysa EN ESKIYI atar.

        Neden en eskiyi: IEC 104 spontane bildiriminde SON deger gecerlidir.
        Yeni geleni atmak, guncel degeri atip bayat degeri gondermek olurdu —
        yani tam tersi. Dusen sayilir ve loglanir; sessiz kayip olmaz.

        Donus: True = yer vardi, False = bir ASDU dusuruldu.
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
        allowed_peers: tuple[str, ...] = (),
        command_client: CommandClient | None = None,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.registry = registry
        self.allowed_peers = allowed_peers
        self._by_key: dict[tuple[str, str], PointAddress] = registry.by_key()
        # Kontrol noktalari. `command_client` None ise komut kabul EDILMEZ
        # (negatif onay) — yapilandirilmamis bir kurulumda sessizce
        # kuyruga alinmis gibi davranmak en kotu sonuc olurdu.
        self.command_client = command_client
        self._commands_by_address = registry.command_by_address()
        self._values: dict[tuple[str, str], PointValue] = {}
        self._sessions: set[_ClientSession] = set()
        self._server: asyncio.base_events.Server | None = None

    def connected_clients(self) -> list[dict]:
        return [
            {"peer": s.peer, "started": s.started, "connected_at": s.connected_at_iso}
            for s in list(self._sessions)
        ]

    def _peer_allowed(self, peer_ip: str) -> bool:
        if not self.allowed_peers:
            return True
        return peer_ip in self.allowed_peers

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
            # Yazici gorevi de iptal et; aksi halde target durdurulup yeniden
            # kurulduginda (cihaz ekleme/pasiflestirme bunu tetikliyor) her
            # turda bir gorev daha geride kalirdi.
            if session._drain_task is not None:
                session._drain_task.cancel()
            if session._gi_task is not None:
                session._gi_task.cancel()
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

    def update_point(
        self,
        *,
        device_code: str,
        signal_key: str,
        value,
        good: bool = True,
        timestamp: datetime | None = None,
    ) -> None:
        """Bir veri noktasinin degerini gunceller; **degisim varsa** CA ile COT=3 yayar.

        IEC 104 spontaneous reporting mantigi (Report by Exception):
          - Yeni okuma onceki yayinlanan deger ile ayni VE quality ayni ise APDU
            URETMEZ. SCADA tarafinda bos buffer ve gereksiz trafik olmaz.
          - Iki halde yayinlanir: (1) deger degisti, (2) good->bad veya bad->good
            quality gecisi.
          - Hicbir client bagli degilse de _values cache'i guncellenir; kullanici
            sonradan baglanip GI cektiginde son deger gider (mevcut akis).

        timestamp: telemetri kaynak zaman damgasi. Sinyal `with_timestamp` ile
        konfigure edilmisse CP56Time2a olarak frame'e gomulur; aksi halde
        ihmal edilir.

        Not: analog sinyaller icin ileride deadband (orn. %1 degisim altinda
        yayinla) eklenebilir; simdilik tam esitlik kontrolu yeterli.
        """
        key = (device_code, signal_key)
        point = self._by_key.get(key)
        if point is None:
            return
        previous = self._values.get(key)
        # Cache'i her zaman yenile (daha sonraki GI dogru deger versin).
        self._values[key] = PointValue(value=value, good=good, timestamp=timestamp)
        if previous is not None and previous.good == good and previous.value == value:
            # Hic degisim yok — ne deger ne quality. Spontaneous APDU bastirilir.
            return
        if not self._sessions:
            return
        asdu = self._encode_single_value(
            point, value=value, good=good, cause=COT_SPONTANEOUS, timestamp=timestamp
        )
        if asdu is None:
            return
        for session in list(self._sessions):
            if not session.started:
                continue
            # Sinirsiz `create_task` YERINE sinirli kuyruk: oturum basina tek
            # yazici gorev bosaltir (bkz. _drain_outbox). Yavas istemci burada
            # geri basinc yaratir, bellek buyumesi degil.
            if not session.enqueue(asdu):
                simdi = _time.monotonic()
                if simdi - session.last_kwindow_warn_at >= KWINDOW_WARN_INTERVAL_SEC:
                    session.last_kwindow_warn_at = simdi
                    logger.warning(
                        "iec104_outbox_dolu name=%s peer=%s dusen_toplam=%d "
                        "(istemci yavas veya yaniti kesilmis)",
                        self.name, session.peer, session.dropped_total,
                    )

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
            except Exception:
                pass
            return
        # OTURUM TAVANI: yeniden baglanma dongusune giren ya da yanlis
        # yapilandirilmis bir master onlarca oturum acabilir; her oturum her
        # deger degisiminde ek is demektir ve sunucu kendi kendini bogar.
        if len(self._sessions) >= MAX_SESSIONS:
            logger.warning(
                "iec104_client_rejected_session_limit name=%s peer=%s aktif=%d tavan=%d",
                self.name, peer_str, len(self._sessions), MAX_SESSIONS,
            )
            try:
                writer.close()
            except Exception:
                pass
            return

        session = _ClientSession(writer=writer, peer=peer_str)
        session.connected_at_iso = datetime.now(timezone.utc).isoformat()
        self._sessions.add(session)
        # Oturum basina TEK yazici gorev: spontane bildirimler yalnizca buradan
        # gider, dolayisiyla `ns` sira numarasi tel sirasiyla tutarli kalir.
        session._drain_task = asyncio.create_task(self._drain_outbox(session))
        logger.info("iec104_client_connected name=%s peer=%s", self.name, peer_str)

        buffer = bytearray()
        try:
            while True:
                # OKUMA ZAMAN ASIMI + TEST_ACT KEEPALIVE (IEC 60870-5-104 t1/t3)
                #
                # YASANAN SORUN: `reader.read()` suresiz bekliyordu ve sunucu
                # tarafinda hicbir zamanlayici yoktu. Aradaki switch/router
                # yeniden baslarsa ya da master sureci donarsa TCP YARI-ACIK
                # kalir; FIN gelmez.
                #
                # Sonuc zinciri: sunucu 12 I-frame gonderir, S-frame ack
                # gelmez, `unacked` 12'de SABITLENIR. `_send_i` artik hicbir
                # sey yazmadigi icin TCP de kopuklugu FARK EDEMEZ (SO_KEEPALIVE
                # yok, TEST_ACT gonderilmiyor). Oturum `started=True` olarak
                # sonsuza kadar kalir ve TUM telemetri sessizce duser: SCADA'da
                # veriler donar, IEC 104 cikisi olur ve container yeniden
                # baslatilana kadar KENDILIGINDEN duzelmez.
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=T3_IDLE_SEC)
                except asyncio.TimeoutError:
                    if session.test_pending:
                        # Onceki TEST_ACT'e cevap gelmedi -> baglanti olu.
                        logger.warning(
                            "iec104_session_dead name=%s peer=%s — TEST_ACT cevapsiz, "
                            "oturum kapatiliyor",
                            self.name, peer_str,
                        )
                        break
                    session.test_pending = True
                    try:
                        await session.send(build_u_frame(APCI_U_TEST_ACT))
                    except Exception:  # noqa: BLE001
                        break
                    continue
                if not chunk:
                    break
                # Herhangi bir veri geldi: baglanti canli.
                session.test_pending = False
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
            # Yazici gorevi MUTLAKA iptal et: aksi halde kopan her oturum
            # arkasinda `outbox.get()` uzerinde sonsuza kadar bekleyen bir
            # gorev birakir — tam da onlemek istedigimiz sinirsiz buyume.
            if session._drain_task is not None:
                session._drain_task.cancel()
            # GI gorevi de iptal edilmeli: kuyruk doluyken `put` uzerinde
            # bekliyor olabilir ve yazici gorev gittigi icin ASLA uyanmaz.
            if session._gi_task is not None:
                session._gi_task.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if session.dropped_total:
                logger.warning(
                    "iec104_client_disconnected name=%s peer=%s dusen_asdu=%d",
                    self.name, peer_str, session.dropped_total,
                )
            logger.info("iec104_client_disconnected name=%s peer=%s", self.name, peer_str)

    async def _dispatch(self, session: _ClientSession, frame: ParsedAPCI) -> None:
        if frame.kind.startswith("U-"):
            await self._handle_u_frame(session, frame)
            return
        if frame.kind == "S":
            session.unacked = 0
            # k-penceresinde yer acildi — bekleyen yazici gorevi uyandir.
            session.window_event.set()
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
            self._start_interrogation(session, requested_ca=requested_ca)
            return
        if type_id == TYPE_C_CS_NA_1:
            # Clock sync — cevap CA'sini gelen CA ile ayni tut.
            ca = _parse_asdu_common_address(frame.asdu) or self.registry.default_common_address
            confirm = encode_interrogation_confirm(
                common_address=ca, cause=COT_ACTIVATION_CON, qoi=0,
            )
            await self._send_i(session, confirm)
            return
        if type_id == TYPE_C_SC_NA_1:
            await self._handle_single_command(session, frame.asdu)
            return
        # DESTEKLENMEYEN KOMUT — SESSIZCE YUTMA.
        #
        # KAPSAM (urun karari): IEC 104 uzerinden kabul edilen komutlar
        # yalnizca genel sorgu (C_IC_NA_1), saat esitleme (C_CS_NA_1) ve
        # ileride RESET'tir. ANALOG CIKIS / setpoint (C_SE_NA/NB/NC) ve diger
        # kontrol tipleri KAPSAM DISIDIR.
        #
        # Eskiden bu dal yalnizca `logger.debug` yaziyordu. SCADA operatoru
        # komutu gonderiyor, hicbir yanit almiyor ve komutun kabul edilip
        # edilmedigini ANLAYAMIYORDU; varsayilan log seviyesinde iz de yoktu.
        # IEC 60870-5-104'te dogru davranis NEGATIF onaydir (COT'un P/N biti):
        # master komutun REDDEDILDIGINI ogrenir.
        ca = _parse_asdu_common_address(frame.asdu) or self.registry.default_common_address
        logger.warning(
            "iec104_desteklenmeyen_komut name=%s peer=%s type=%d — negatif onay "
            "gonderiliyor (kapsam: yalnizca genel sorgu, saat esitleme, reset)",
            self.name, session.peer, type_id,
        )
        try:
            # `negative=True` COT'un P/N bitini (0x40) kurar. Bayragi `cause`
            # icine OR'lamak ISE YARAMAZDI: `_encode_dui` cause'u `& 0x3F` ile
            # maskeliyor, yani bit sessizce dusurdu.
            red = encode_interrogation_confirm(
                common_address=ca,
                cause=COT_ACTIVATION_CON,
                qoi=0,
                negative=True,
            )
        except Exception:  # noqa: BLE001
            logger.debug("iec104_negatif_onay_uretilemedi", exc_info=True)
            return
        await self._send_i(session, red)


    async def _handle_single_command(
        self, session: _ClientSession, asdu: bytes,
    ) -> None:
        """C_SC_NA_1 (tip 45) — ariza gostergesi RESET komutu.

        KAPSAM: IEC 104 uzerinden kabul edilen TEK kontrol komutu budur.
        Adres kayitli degilse ya da izin verilen slug'a bakmiyorsa NEGATIF
        onay doner (bkz. registry.ALLOWED_COMMAND_SLUGS).

        ONAY ile SONLANDIRMA AYRI:
          ACT_CON  -> komut KABUL EDILDI (backend kuyruga aldi)
          ACT_TERM -> komut TAMAMLANDI  (cihaz sonucu bildirdi)
        Cihaz NAT arkasindaki gateway'in ardinda ve komut ona config-poll ile
        gidiyor (~30 sn); ikisini birlestirmek operatore "reset yapildi"
        demek olurdu, oysa yalnizca kuyruga alinmistir.
        """
        cmd = parse_single_command(asdu)
        if cmd is None:
            logger.warning(
                "iec104_komut_cozulemedi name=%s peer=%s — bicim bozuk ya da "
                "coklu nesne; tek nesneli C_SC_NA_1 bekleniyor",
                self.name, session.peer,
            )
            return

        hedef = self._commands_by_address.get((cmd.common_address, cmd.ioa))
        if hedef is None:
            logger.warning(
                "iec104_komut_adresi_taninmadi name=%s peer=%s ca=%d ioa=%d — "
                "negatif onay",
                self.name, session.peer, cmd.common_address, cmd.ioa,
            )
            await self._command_reply(session, cmd, COT_ACTIVATION_CON, negative=True)
            return

        # SELECT/EXECUTE: Horstmann reset'i tek asamali. SELECT gelirse
        # olumlu onaylayip BEKLERIZ; komut yalnizca EXECUTE'ta uygulanir.
        # SELECT'i uygulamak, operatorun "sec ve dogrula" adimini reset'e
        # cevirirdi.
        if cmd.select:
            logger.info(
                "iec104_komut_select name=%s peer=%s device=%s — onaylandi, "
                "EXECUTE bekleniyor",
                self.name, session.peer, hedef.device_code,
            )
            await self._command_reply(session, cmd, COT_ACTIVATION_CON)
            return

        if not cmd.on:
            # SCS=OFF bir reset icin anlamsiz; sessizce yok saymak yerine
            # reddediyoruz ki master yanlis kullandigini ogrensin.
            logger.warning(
                "iec104_komut_scs_off name=%s peer=%s device=%s — reset icin "
                "SCS=ON bekleniyor, negatif onay",
                self.name, session.peer, hedef.device_code,
            )
            await self._command_reply(session, cmd, COT_ACTIVATION_CON, negative=True)
            return

        if self.command_client is None:
            logger.error(
                "iec104_komut_istemcisi_yok name=%s peer=%s device=%s — komut "
                "REDDEDILDI (backend baglantisi yapilandirilmamis)",
                self.name, session.peer, hedef.device_code,
            )
            await self._command_reply(session, cmd, COT_ACTIVATION_CON, negative=True)
            return

        # Backend cagrisi SENKRON `requests` kullaniyor; olay dongusunu
        # bloklamamak icin thread'e aliniyor. Bloklasaydi bu sure boyunca
        # HICBIR oturum okunamaz, S-frame'ler islenmez ve k-penceresi
        # dolardi (ayni tuzaga genel sorguda dusulmustu).
        try:
            kabul = await asyncio.to_thread(
                self.command_client.queue,
                device_code=hedef.device_code,
                command=hedef.command_slug,
                peer=session.peer,
            )
        except CommandRejected as exc:
            logger.warning(
                "iec104_komut_reddedildi name=%s peer=%s device=%s reason=%s: %s",
                self.name, session.peer, hedef.device_code, exc.reason, exc.detail,
            )
            await self._command_reply(session, cmd, COT_ACTIVATION_CON, negative=True)
            return

        logger.info(
            "iec104_komut_kuyruga_alindi name=%s peer=%s device=%s slug=%s id=%d",
            self.name, session.peer, hedef.device_code, hedef.command_slug, kabul.id,
        )
        await self._command_reply(session, cmd, COT_ACTIVATION_CON)

        # Sonucu AYRI gorevde bekle: okuma dongusu calismaya devam etsin.
        asyncio.create_task(
            self._await_command_result(session, cmd, kabul.id, hedef.device_code)
        )

    async def _await_command_result(
        self, session: _ClientSession, cmd, command_id: int, device_code: str,
    ) -> None:
        """Komut sonucunu yoklar ve ACT_TERM gonderir.

        Zaman asiminda NEGATIF ACT_TERM gider. Sessiz kalmak master'i
        suresiz bekletirdi ve operator reset'in olup olmadigini bilemezdi.
        """
        # Zamanlama istemciden okunuyor (bkz. CommandClient.__init__).
        ust_sinir = getattr(
            self.command_client, "result_timeout_sec", COMMAND_RESULT_TIMEOUT_SEC
        )
        adim = getattr(self.command_client, "result_poll_sec", COMMAND_RESULT_POLL_SEC)
        gecen = 0.0
        while gecen < ust_sinir:
            await asyncio.sleep(adim)
            gecen += adim
            if session.writer.is_closing():
                return  # master gitti; ACT_TERM'in alicisi yok
            durum, sonuc = await asyncio.to_thread(
                self.command_client.result, command_id
            )
            if durum in TERMINAL_OK or (sonuc or "") in TERMINAL_OK:
                logger.info(
                    "iec104_komut_tamamlandi name=%s device=%s id=%d",
                    self.name, device_code, command_id,
                )
                await self._command_reply(session, cmd, COT_ACTIVATION_TERM)
                return
            if durum in TERMINAL_FAIL or (sonuc or "") in TERMINAL_FAIL:
                logger.warning(
                    "iec104_komut_basarisiz name=%s device=%s id=%d durum=%s sonuc=%s",
                    self.name, device_code, command_id, durum, sonuc,
                )
                await self._command_reply(
                    session, cmd, COT_ACTIVATION_TERM, negative=True
                )
                return
        logger.warning(
            "iec104_komut_zaman_asimi name=%s device=%s id=%d sure=%.0fs — "
            "negatif ACT_TERM",
            self.name, device_code, command_id, ust_sinir,
        )
        await self._command_reply(session, cmd, COT_ACTIVATION_TERM, negative=True)

    async def _command_reply(
        self, session: _ClientSession, cmd, cause: int, *, negative: bool = False,
    ) -> None:
        """Komutu AYNEN yansitan onay/sonlandirma cercevesi gonderir."""
        try:
            frame = encode_single_command_reply(
                common_address=cmd.common_address,
                ioa=cmd.ioa,
                sco=cmd.sco,
                cause=cause,
                negative=negative,
            )
        except Exception:  # noqa: BLE001
            logger.debug("iec104_komut_yaniti_uretilemedi", exc_info=True)
            return
        await self._send_i(session, frame)

    def _start_interrogation(self, session: _ClientSession, *, requested_ca: int) -> None:
        """Genel sorguyu AYRI GOREVDE baslatir.

        NEDEN AYRI GOREV — GI 12. NESNEDE KESILIYORDU
        ----------------------------------------------
        `_handle_interrogation` okuma dongusunun ICINDEN cagriliyordu. GI
        suresince `reader.read()` HIC calismiyor, dolayisiyla master'in
        S-frame'leri islenmiyordu. `session.unacked` yalnizca S-frame gelince
        sifirlandigi ve `_send_i` `unacked >= 12` olunca frame'i sessizce
        dusurdugu icin sonuc suydu:

            ACT_CON + 11 nesne gider, GERI KALANI VE ACT_TERM HIC GITMEZ.

        SCADA GI'nin bittigini gosteren ACT_TERM'i hic almaz; 600 cihazlik bir
        sahada ilk genel sorguda 12 nesne disinda HICBIR SEY ulasmaz. Bu, A7
        (yari-acik TCP) ile ILGISIZ ve ondan BAGIMSIZ bir yol: baglanti tamamen
        saglikliyken de her GI'da tetikleniyor.

        Ayri gorevde kostugunda okuma dongusu calismaya devam eder, S-frame'ler
        islenir, `unacked` sifirlanir ve yazici gorev kaldigi yerden devam eder.
        """
        if session._gi_task is not None and not session._gi_task.done():
            # Ust uste GI: oncekini bozmadan yenisini yok say. Master genelde
            # ACT_TERM gelmedigi icin tekrar sorar; eskisini iptal etmek
            # yarim kalmis bir GI daha uretirdi.
            logger.warning(
                "iec104_gi_zaten_calisiyor name=%s peer=%s — yeni istek yok sayildi",
                self.name, session.peer,
            )
            return
        session._gi_task = asyncio.create_task(
            self._handle_interrogation(session, requested_ca=requested_ca)
        )

    async def _handle_interrogation(
        self, session: _ClientSession, *, requested_ca: int,
    ) -> None:
        """C_IC_NA_1 → ACT_CON + filtrelenmis degerler (COT=20) + ACT_TERM.

        SCADA `requested_ca=0xFFFF` (broadcast) gonderirse tum CA'lara ait
        noktalar yayinlanir; aksi halde yalnizca o CA.

        Cerceveler kuyruga BLOKLAYAN `put` ile konur (spontane bildirimlerin
        `enqueue` ile en-eskiyi-dusuren yolu DEGIL): GI yaniti eksiksiz olmak
        zorundadir. 600 cihazlik bir sahada nesne sayisi kuyruk tavanini rahat
        asar; `enqueue` kullanilsaydi GI'nin ilk yarisi sessizce dusurulurdu.
        Kuyruk dolunca bu gorev bekler, yazici gorev bosaltmaya devam eder.
        """
        is_broadcast = requested_ca == BROADCAST_COMMON_ADDRESS
        # ACT_CON gelen CA ile gonderilir (broadcast'te de).
        confirm = encode_interrogation_confirm(
            common_address=requested_ca or self.registry.default_common_address,
            cause=COT_ACTIVATION_CON, qoi=20,
        )
        await session.outbox.put(confirm)

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
                    point, value=default_value, good=False, cause=COT_INTERROGATION,
                    timestamp=None,
                )
            else:
                asdu = self._encode_single_value(
                    point, value=current.value, good=current.good,
                    cause=COT_INTERROGATION, timestamp=current.timestamp,
                )
            if asdu is not None:
                await session.outbox.put(asdu)

        terminate = encode_interrogation_confirm(
            common_address=requested_ca or self.registry.default_common_address,
            cause=COT_ACTIVATION_TERM, qoi=20,
        )
        await session.outbox.put(terminate)
        logger.info(
            "iec104_gi_kuyruklandi name=%s peer=%s ca=%s",
            self.name, session.peer, "broadcast" if is_broadcast else requested_ca,
        )

    def _encode_single_value(
        self,
        point: PointAddress,
        *,
        value,
        good: bool,
        cause: int,
        timestamp: datetime | None = None,
    ) -> bytes | None:
        """Sinyal type'ina gore uygun ASDU encoder'i secer.

        with_timestamp=True ise CP56Time2a tasiyan tip kullanilir:
          - M_SP_NA_1 (1)  -> M_SP_TB_1 (30)
          - M_ME_NC_1 (13) -> M_ME_TF_1 (36)
          - M_IT_NA_1 (15) -> simdilik timestamp'siz kalir (M_IT_TB_1 = 37
            ileride eklenebilir).
        """
        ca = point.common_address
        if point.type_id == TYPE_M_SP_NA_1:
            if point.with_timestamp:
                return encode_asdu_single_point_t(
                    common_address=ca, cause=cause, ioa=point.ioa,
                    value=bool(value), good=good, timestamp=timestamp,
                )
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
            if point.with_timestamp:
                return encode_asdu_float_t(
                    common_address=ca, cause=cause, ioa=point.ioa,
                    value=fvalue, good=good, timestamp=timestamp,
                )
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
        # K window backpressure: unacked >= 12 ise yeni I-frame yollama,
        # SCADA S-frame ile ack atana kadar bekle (max 1 sn).
        # IEC 60870-5-104 spec'i: k aşılırsa master pause etmeli.
        # Sonsuz beklemek yerine drop + log — slow SCADA tum kuyrugu tikamasin.
        if session.unacked >= 12:
            # Uyari OTURUM BASINA rate-limit'li. Sinirsiz iken her dusen frame
            # bir WARNING basiyordu; 600 cihaz yukunde log dosyalari saniyeler
            # icinde donuyor ve DIGER tum teshis kayitlari supuruluyordu —
            # yani ariza aninda bakilacak log da kalmiyordu.
            simdi = _time.monotonic()
            if simdi - session.last_kwindow_warn_at >= KWINDOW_WARN_INTERVAL_SEC:
                session.last_kwindow_warn_at = simdi
                logger.warning(
                    "iec104_k_window_full name=%s peer=%s unacked=%d — frame drop "
                    "(bu uyari %ds'de bir basilir)",
                    self.name,
                    session.peer,
                    session.unacked,
                    int(KWINDOW_WARN_INTERVAL_SEC),
                )
            return
        # ns wrap: IEC 60870-5-104'te sequence number 15-bit (mod 32768).
        # Eski kod `session.ns += 1` 32768'de build_i_frame_asdu ValueError
        # firlatiyor; session bozuk hale geliyor. Mod ile wrap.
        try:
            frame = build_i_frame_asdu(asdu=asdu, ns=session.ns, nr=session.nr)
        except ValueError as exc:
            logger.warning(
                "iec104_frame_build_failed name=%s peer=%s ns=%d error=%s — ns reset",
                self.name,
                session.peer,
                session.ns,
                exc,
            )
            session.ns = 0
            return
        session.ns = (session.ns + 1) & 0x7FFF  # 15-bit mask
        session.unacked += 1
        # Writer kapaliysa send'e gitme — dead session leak'i onle.
        try:
            if session.writer is None or session.writer.is_closing():
                # Session bozuk; sessions setinden cikar (idempotent discard).
                self._sessions.discard(session)
                logger.info(
                    "iec104_session_discarded_closed name=%s peer=%s",
                    self.name,
                    session.peer,
                )
                return
            await session.send(frame)
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            logger.warning(
                "iec104_send_failed name=%s peer=%s error=%s — session removed",
                self.name,
                session.peer,
                exc,
            )
            # TCP fault: session'i cikar + writer kapat.
            self._sessions.discard(session)
            try:
                if session.writer is not None:
                    session.writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _wait_window(self, session: _ClientSession) -> bool:
        """k-penceresinde yer acilana kadar bekler.

        IEC 60870-5-104: gonderen taraf `k` (burada 12) onaylanmamis I-frame'i
        astiginda DURMALIDIR. Onceki kod durmak yerine frame'i DUSURUYORDU;
        akis kontrolu "veri kaybi" olarak uygulanmis oluyordu.

        Donus: True = pencere acik, False = zaman asimi (oturum olu).
        """
        while session.unacked >= MAX_UNACKED_I:
            session.window_event.clear()
            # clear() ile bekleme arasinda S-frame gelmis olabilir.
            if session.unacked < MAX_UNACKED_I:
                break
            try:
                await asyncio.wait_for(
                    session.window_event.wait(), timeout=K_WINDOW_WAIT_SEC
                )
            except asyncio.TimeoutError:
                return False
        return True

    async def _drain_outbox(self, session: _ClientSession) -> None:
        """Oturumun giden kuyrugunu SIRAYLA bosaltir (oturum basina tek gorev).

        NEDEN TEK GOREV: onceden her deger degisimi kendi `create_task`'ini
        aciyordu. Bunun iki bedeli vardi:
          1. Yavas istemcide gorev sayisi sinirsiz buyuyor, bellek OOM'a
             gidiyordu (600 cihazda saniyede binlerce gorev).
          2. `ns` sira numarasi gorevler arasinda YARISA giriyordu; tel
             sirasi bozulunca master oturumu tamamen reddedebiliyordu.

        Tek yazici gorev ikisini de kokten cozer: geri basinc kuyrukta
        olusur (en eski ASDU dusurulur, bkz. `enqueue`) ve `ns` daima
        gonderim sirasinda atanir.
        """
        try:
            while True:
                asdu = await session.outbox.get()
                try:
                    # k-penceresi dolduysa BEKLE, dusurme. `_send_i` tek basina
                    # `unacked >= 12` gorunce frame'i atiyordu; GI'de bu, sorgu
                    # yanitinin 12. nesneden sonrasinin sessizce kaybolmasi
                    # demekti. Okuma dongusu paralel calistigi icin S-frame
                    # gelince pencere acilir ve buradan devam edilir.
                    if not await self._wait_window(session):
                        logger.warning(
                            "iec104_k_window_timeout name=%s peer=%s — master "
                            "%.0f sn ack atmadi, oturum kapatiliyor",
                            self.name, session.peer, K_WINDOW_WAIT_SEC,
                        )
                        try:
                            session.writer.close()
                        except Exception:  # noqa: BLE001
                            pass
                        return
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
    """Birden fazla outbound target icin server yaratir/duragir; threadsafe update koprusu."""

    def __init__(self) -> None:
        self._servers: dict[int, IEC104Server] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        # Kontrol komutlarini backend'e ileten istemci. `main` set eder.
        # None kalirsa server komutlari REDDEDER (negatif onay) — sessizce
        # kabul edilmis gibi davranmaktan iyidir.
        self.command_client: CommandClient | None = None

    def bind_command_client(self, client: CommandClient | None) -> None:
        self.command_client = client

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def server_count(self) -> int:
        return len(self._servers)

    async def deploy(
        self, *, target_id: int, name: str, host: str, port: int, registry: PointRegistry,
        allowed_peers: tuple[str, ...] = (),
    ) -> None:
        if target_id in self._servers:
            await self.undeploy(target_id)
        server = IEC104Server(
            name=name, host=host, port=port, registry=registry,
            allowed_peers=allowed_peers,
            command_client=self.command_client,
        )
        await server.start()
        self._servers[target_id] = server

    def connected_clients(self, target_id: int) -> list[dict]:
        server = self._servers.get(target_id)
        return server.connected_clients() if server is not None else []

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
        self,
        *,
        device_code: str,
        signal_key: str,
        value,
        good: bool = True,
        timestamp: datetime | None = None,
    ) -> None:
        """Her aktif server'a degerin yayilmasini saglayan thread-safe koprü.

        timestamp: telemetri kaynak zaman damgasi. Sinyalin IEC 104
        konfigurasyonunda `with_timestamp` aktifse CP56Time2a olarak
        frame'e gomulur; aksi halde server tarafinda ihmal edilir.
        """
        if self._loop is None:
            return

        def _apply() -> None:
            for server in self._servers.values():
                server.update_point(
                    device_code=device_code, signal_key=signal_key,
                    value=value, good=good, timestamp=timestamp,
                )

        try:
            self._loop.call_soon_threadsafe(_apply)
        except RuntimeError:
            pass


manager = IEC104ServerManager()
