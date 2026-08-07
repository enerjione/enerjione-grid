"""NATS JetStream tuketicisi — tag-engine cikisini Modbus store'una yazar.

Kendi thread'inde kendi asyncio loop'unu calistirir; Modbus sunuculari ana
loop'tadir. Iki dunya arasindaki tek temas noktasi `PointRegistry.update()`
ve o kilitle korunuyor, bu yuzden ekstra kopruye gerek yok (IEC 104'te ASDU
yayini asenkron oldugu icin threadsafe kopru gerekiyordu; Modbus'ta sunucu
istek geldiginde okur, biz sadece hafizayi guncelleriz).

Kalite (quality) yonetimi: Modbus'ta kalite biti YOKTUR ve register/bit
alanlarinin "henuz hic yazilmadi" diye bir hali de yoktur — SCADA ne
sorarsa depoda ne varsa onu okur. Once "bozuk kaliteli olcumu atla, son
iyi degeri koru" davranisi vardi; ama bir sinyal HIC iyi kaliteli
gelmezse (2026-08-07: sahada bir hedefte 10K+ mesajin 6K+'si surekli
"bad" — asla bir kez bile "good" olmadi) register hicbir zaman
yazilmiyor ve SCADA'nin gordugu deger sonsuza dek varsayilan 0 kaliyor.
Bu, Canli Degerler ekraninin gosterdigi GERCEK degerden DAHA YANILTICI:
sistemin geri kalani "su an okunan deger buydu" derken Modbus sessizce
"hic olcum yok" gibi 0 gosteriyordu. Artik davranis Canli Degerler ile
BIREBIR AYNI: kalite ne olursa olsun o an gelen deger yazilir; kalite
sayaci yalnizca teshis icin ayrica tutulur, yazmayi ENGELLEMEZ.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
from threading import Event, Thread

import nats

from modbus_outbound.config import Settings
from modbus_outbound.server import ModbusServerManager

logger = logging.getLogger(__name__)

_GOOD_QUALITIES = {"good", "ok", ""}


def _nats_tls_context():
    """NATS_CA_FILE ayarliysa dogrulayici SSL baglami, degilse None.

    NEDEN: NATS istemci portu tum arayuzlere acik ve baglantilar parolali;
    TLS'siz hem parola hem telemetri duz metin gider.

    `create_default_context(cafile=...)` YALNIZCA verilen CA'ya guvenir —
    isletim sisteminin guven deposu kullanilmaz. Bu bilincli: aksi halde
    herkese acik bir otoriteden alinmis sertifika da kabul edilirdi.

    Dosya okunamazsa HATA yukselir; TLS'siz devam etmek, operatorun "TLS
    acik" sandigi bir kurulumda parolayi acikta gondermek olurdu.
    """
    ca_file = (os.getenv("NATS_CA_FILE") or "").strip()
    if not ca_file:
        return None
    return ssl.create_default_context(cafile=ca_file)


def _is_good(quality: str | None) -> bool:
    return str(quality or "good").strip().lower() in _GOOD_QUALITIES


class TelemetryConsumer:
    def __init__(self, *, settings: Settings, manager: ModbusServerManager) -> None:
        self.settings = settings
        self.manager = manager
        self._stop = Event()
        self._thread: Thread | None = None
        self._messages_processed = 0
        self._points_written = 0
        self._bad_quality_count = 0
        self._last_error: str | None = None

    # ---- Yasam dongusu ----------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = Thread(target=self._run, name="modbus-nats-consumer", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._stop.set()

    @property
    def messages_processed(self) -> int:
        return self._messages_processed

    @property
    def points_written(self) -> int:
        return self._points_written

    @property
    def bad_quality_count(self) -> int:
        return self._bad_quality_count

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._consume())
        finally:
            loop.close()

    async def _consume(self) -> None:
        s = self.settings
        backoff = 2
        while not self._stop.is_set():
            nc = None
            try:
                nc = await nats.connect(
                    servers=[s.nats_url],
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=2,
                    name="e1-modbus-outbound",
                    # None = TLS kapali (varsayilan). Bkz. _nats_tls_context.
                    tls=_nats_tls_context(),
                )
                js = nc.jetstream()

                async def _on_message(msg) -> None:  # noqa: ANN001
                    try:
                        payload = json.loads(msg.data.decode("utf-8"))
                        self._handle_payload(payload)
                        self._messages_processed += 1
                    except Exception as exc:  # noqa: BLE001
                        # Modbus yayini "son deger" mantigiyla calisir; bozuk
                        # tek mesaji yeniden denemek bir sey kazandirmaz, bir
                        # sonraki olcum zaten uzerine yazar. Ack'leyip gecelim
                        # ki consumer birikmesin.
                        self._last_error = str(exc)
                        logger.warning("modbus_consumer_handler_error error=%s", exc)
                    finally:
                        try:
                            await msg.ack()
                        except Exception:  # noqa: BLE001
                            pass

                from nats.js.api import ConsumerConfig, DeliverPolicy

                consumer_cfg = ConsumerConfig(
                    durable_name=s.nats_durable,
                    # NEW: yeniden baslatmada eski telemetriyi tekrar oynatma —
                    # Modbus'ta gecmis degerin anlami yok, guncel olan lazim.
                    deliver_policy=DeliverPolicy.NEW,
                    ack_wait=60,
                    max_ack_pending=10000,
                    max_deliver=3,
                )
                sub = await js.subscribe(
                    subject=s.nats_subject,
                    durable=s.nats_durable,
                    cb=_on_message,
                    manual_ack=True,
                    config=consumer_cfg,
                )
                logger.info(
                    "modbus_consumer_running subject=%s durable=%s url=%s",
                    s.nats_subject, s.nats_durable, s.nats_url,
                )
                backoff = 2
                while not self._stop.is_set():
                    await asyncio.sleep(1)
                await sub.unsubscribe()
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    break
                self._last_error = str(exc)
                logger.warning(
                    "modbus_consumer_reconnect error=%s backoff=%ds url=%s",
                    exc, backoff, s.nats_url,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if nc is not None:
                    try:
                        await nc.drain()
                    except Exception:  # noqa: BLE001
                        pass

    # ---- Mesaj isleme ------------------------------------------------------
    def _handle_payload(self, payload: dict) -> None:
        device_code = payload.get("device_code")
        signal_key = payload.get("signal_key")
        if not device_code or not signal_key:
            return
        if not _is_good(payload.get("quality")):
            # ARTIK ATLAMA YOK (bkz. modul docstring'i) — yalnizca sayilir.
            self._bad_quality_count += 1

        # `value` bazi sinyallerde bos gelir, gercek deger `value_string`
        # tasir (orn. DNP3 tarafinin sayisal alani doldurmadigi durumlar —
        # bkz. `_seri_ve_kod_senkronu`daki ayni ikilik). Yalnizca `value`'ya
        # bakmak bu sinyaller icin register'i SESSIZCE hic yazdirmiyordu.
        raw_value = payload.get("value")
        if raw_value is None:
            raw_value = payload.get("value_string")

        written = self.manager.update_point(
            str(device_code), str(signal_key), raw_value
        )
        self._points_written += written
