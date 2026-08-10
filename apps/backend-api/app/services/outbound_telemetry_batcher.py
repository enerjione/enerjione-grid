"""Telemetry batch dispatcher.

Tasarim sebebi: telemetry her saniye 600 cihaz x N sinyal geliyor. Her reading
icin webhook'a tek POST atmak:
  - Aliciyi serserm yapar (rate limit, queue overflow)
  - Network gereksiz busy
  - "Onceden bu degerdeydi, yine ayni" mesajlari gereksiz

Cozum:
  1. Her telemetry consumer cagrisinda reading'i `submit()` ile buffer'a koy.
  2. Buffer key: (device_code, signal_key). Sadece ONCEKI gonderimden FARKLI
     value'lar gercekten buffer'a yazilir; ayni deger ise atlanir (dedup).
  3. Arka plan thread her FLUSH_WINDOW_SEC'de bir aktif `telemetry`/`all`
     outbound target'lara batch payload yollar:
       {
         "event_kind": "telemetry_batch",
         "count": 12,
         "window_seconds": 5,
         "sent_at": "2026-05-13T08:30:00Z",
         "readings": [{...}, {...}]
       }
  4. Alarm event'leri batch'e GIRMEZ — anlik gonderilir (outbound_dispatch_service
     dispatch_event() ile).

Thread safety: buffer + last_sent dict'leri Lock ile korunur. Flush worker
ayri thread'de, FastAPI lifespan'inde baslar.

IEC 104: bu modul ATLAR — IEC 104 zaten point registry'sini telemetry_consumer
icinde update ediyor (anlik, batch degil).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.outbound_target import OutboundTarget
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

# Pencere boyutu — bu sure boyunca biriken degisik telemetri batch'lenir.
# Operator tercihi: 5sn (kullanici "5 saniye" sectim dedi).
FLUSH_WINDOW_SEC = 5

# Buffer: key = (device_code, signal_key), value = en son reading dict.
# Ayni device_code+signal_key icin yeni reading eskinin uzerine yazar
# (window icindeki son degeri gonderir, ara degerler iceri girer ama
# flush sirasinda son degerle replace olur).
_buffer: dict[tuple[str, str], dict[str, Any]] = {}

# Son gonderilen degerleri (dedup icin). Yeni reading'in value'su
# burada kayitli son value ile ayni ise buffer'a EKLENMEZ.
_last_sent: dict[tuple[str, str], Any] = {}

_lock = threading.Lock()
_stop_event = threading.Event()
_flusher_thread: threading.Thread | None = None


def submit(reading: dict[str, Any]) -> None:
    """Telemetry consumer'in cagirdigi entry point.

    reading: {
        "message_id": ...,
        "device_code": ...,
        "signal_key": ...,
        "value": ...,
        "value_string": ...,
        "quality": ...,
        "source_timestamp": ...,
        ...
    }
    Sadece value (veya value_string) ONCEKI batch'ten farkliysa buffer'a girer.
    """
    device_code = reading.get("device_code")
    signal_key = reading.get("signal_key")
    if not device_code or not signal_key:
        return
    key = (str(device_code), str(signal_key))

    # Karsilastirilacak deger: numeric value oncelikli, yoksa value_string.
    new_val = reading.get("value")
    if new_val is None:
        new_val = reading.get("value_string")

    with _lock:
        last_val = _last_sent.get(key, _SENTINEL)
        # Ilk kez goruyorsak veya deger degisti ise buffer'a koy.
        if last_val is _SENTINEL or last_val != new_val:
            _buffer[key] = reading


# Module-level sentinel — None gecerli bir telemetry value olabilir.
_SENTINEL = object()


def _drain_buffer() -> list[dict[str, Any]]:
    """Buffer'i bosalt. Lock altinda.

    `_last_sent` BURADA GUNCELLENMEZ — bkz. `_mark_sent`.

    YASANAN ARIZA: dedup haritasi gonderimden ONCE guncelleniyordu. POST
    patlasa bile deger "gonderildi" isaretleniyor ve cihaz ayni degeri
    tekrarladigi surece bir daha ASLA buffer'a girmiyordu:

      > 4G uc dakika kopar. `master.permanent_fault` 0 -> 1 olur. Deger drain
      > edilir, `_last_sent=1` yazilir, POST timeout'a duser. 4G geri gelir.
      > Cihaz hala 1 yayinlar; `submit()` `last_val == new_val` gorup buffer'a
      > KOYMAZ. Musterinin webhook'u arizayi HIC gormez — ta ki ariza kalkip
      > deger 0'a donene kadar.

    Yani tam da bildirilmesi gereken olay sessizce yutuluyordu.
    """
    with _lock:
        if not _buffer:
            return []
        readings = list(_buffer.values())
        _buffer.clear()
        return readings


def _mark_sent(readings: list[dict[str, Any]]) -> None:
    """Basariyla teslim edilen degerleri dedup haritasina yazar.

    YALNIZCA gonderim basarili olduysa cagrilir. Basarisiz kalan deger
    isaretlenmedigi icin cihaz onu bir sonraki telemetride tekrar yayinladigi
    anda yeniden buffer'a girer — yani kendiliginden yeniden denenir.
    """
    with _lock:
        for r in readings:
            dc = r.get("device_code")
            sk = r.get("signal_key")
            if not dc or not sk:
                continue
            v = r.get("value")
            if v is None:
                v = r.get("value_string")
            _last_sent[(str(dc), str(sk))] = v


def _send_rest_batch(target: OutboundTarget, payload: dict) -> None:
    """REST POST — outbound_dispatch_service._send_rest ile ayni mantik."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if target.auth_header and target.auth_token:
        headers[target.auth_header] = target.auth_token
    req = urllib.request.Request(
        target.endpoint, data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15):
        pass


def _send_mqtt_batch(target: OutboundTarget, payload: dict) -> None:
    """MQTT publish — ayni topic, tek mesaj."""
    try:
        import paho.mqtt.publish as mqtt_publish
    except ImportError:  # pragma: no cover
        raise RuntimeError("MQTT publish icin paho-mqtt kurulu degil.") from None
    if not target.topic:
        raise ValueError("MQTT target icin topic zorunlu.")
    mqtt_publish.single(
        target.topic,
        payload=json.dumps(payload, ensure_ascii=False),
        hostname=target.endpoint,
        qos=target.qos,
        retain=target.retain,
    )


def _flush_once() -> None:
    """Buffer'i bosalt ve aktif target'lara gonder."""
    readings = _drain_buffer()
    if not readings:
        return

    # Aktif target'lari DB'den her flush'ta yeniden cek — operator UI'dan
    # target ekleyebilir/silebilir; arada cache invalidation yapmamak icin
    # her flush'ta fresh sorgu. 5sn'de bir tek SELECT — DB icin negligible.
    db = SessionLocal()
    try:
        targets = list(
            db.scalars(
                select(OutboundTarget).where(OutboundTarget.is_active.is_(True))
            ).all()
        )
        # event_filter'i telemetry veya all olanlar + IEC104 olmayanlar
        # (IEC104 zaten consumer'da anlik update yapiyor).
        eligible = [
            t for t in targets
            if t.protocol in ("rest", "mqtt")
            and t.event_filter in ("all", "telemetry")
        ]
        if not eligible:
            # Gonderilecek hedef YOK — bu bir basarisizlik degil. Isaretlemezsek
            # ayni degerler her turda bosuna yeniden tamponlanir. (Basarisiz
            # GONDERIM ile karistirilmamali: orada isaretlememek SART.)
            _mark_sent(readings)
            return

        # Cihaz-bazli flat payload: {device_name, timestamp, <signal>: <value>, ...}
        # Operator tercihi — readings array yerine her cihaz icin tek dict.
        per_device: dict[str, dict[str, Any]] = {}
        per_device_ts: dict[str, str] = {}
        for r in readings:
            device_code = str(r.get("device_code") or "")
            if not device_code:
                continue
            d = per_device.setdefault(device_code, {})
            sk = str(r.get("signal_key") or "")
            if not sk:
                continue
            val = r.get("value")
            if val is None:
                val = r.get("value_string")
            d[sk] = val
            ts = r.get("source_timestamp") or r.get("processed_at")
            if ts:
                cur = per_device_ts.get(device_code)
                if cur is None or ts > cur:
                    per_device_ts[device_code] = ts

        from app.services.outbound_dispatch_service import _record_delivery

        # Dedup haritasi ancak TUM uygun hedefler basarili olursa guncellenir.
        # Bir hedef duserse deger isaretlenmez ve cihaz onu tekrarladiginda
        # yeniden buffer'a girer; digerleri mukerrer alir. Webhook'a ayni
        # degeri iki kez gondermek kabul edilebilir (at-least-once), bir
        # ariza gecisini HIC gondermemek degil.
        tum_hedefler_basarili = True
        for target in eligible:
            # MQTT batcher mqtt_publisher_service tarafindan ele aliniyor;
            # telemetry batcher SADECE REST webhook'lara mesaj atar.
            if target.protocol != "rest":
                continue
            ok_count = 0
            fail_count = 0
            last_err: str | None = None
            for device_code, signals in per_device.items():
                payload: dict[str, Any] = {
                    "device_name": device_code,
                    "timestamp": per_device_ts.get(device_code, datetime.now(timezone.utc).isoformat()),
                    **signals,
                }
                try:
                    _send_rest_batch(target, payload)
                    ok_count += 1
                except Exception as exc:  # noqa: BLE001
                    fail_count += 1
                    last_err = str(exc)
            if fail_count:
                tum_hedefler_basarili = False
            try:
                if ok_count:
                    logger.info(
                        "outbound_batch_delivered target=%s protocol=rest devices=%d failed=%d",
                        target.name, ok_count, fail_count,
                    )
                    _record_delivery(target.id, ok=True)
                if fail_count:
                    raise RuntimeError(last_err or "rest batch failed")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "outbound_batch_failed target=%s protocol=rest devices=%d error=%s",
                    target.name, fail_count, exc,
                )
                _record_delivery(target.id, ok=False, error=str(exc))
                # DB event log — operator UI'da gorebilsin.
                try:
                    record_event(
                        db,
                        category="outbound",
                        event_type="outbound_batch_failed",
                        severity="warning",
                        message=f"Batch delivery to {target.name} failed: {exc}",
                        i18n_key="outbound_batch_failed",
                        i18n_params={"target": target.name, "error": str(exc)},
                        metadata={
                            "target": target.name,
                            "protocol": target.protocol,
                            "count": len(readings),
                            "error": str(exc),
                        },
                    )
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()

        # Dedup haritasi EN SONDA, yalnizca her sey gittiyse guncellenir.
        # Basarisizlikta isaretlemedigimiz icin cihaz ayni degeri tekrar
        # yayinladiginda deger yeniden buffer'a girer ve kendiliginden
        # yeniden denenir.
        if tum_hedefler_basarili:
            _mark_sent(readings)
        else:
            logger.warning(
                "outbound_batch_dedup_atlandi readings=%d — teslim edilemeyen "
                "degerler yeniden denenecek",
                len(readings),
            )
    finally:
        db.close()


def _flusher_loop() -> None:
    """Periyodik flush thread'i. Stop event set olana kadar calisir."""
    logger.info(
        "outbound_telemetry_batcher_started window=%ds", FLUSH_WINDOW_SEC
    )
    while not _stop_event.wait(FLUSH_WINDOW_SEC):
        try:
            _flush_once()
        except Exception:  # noqa: BLE001
            logger.exception("outbound_telemetry_batcher_flush_error")
    logger.info("outbound_telemetry_batcher_stopped")


def start() -> None:
    """FastAPI startup hook'tan cagrilir."""
    global _flusher_thread
    if _flusher_thread is not None and _flusher_thread.is_alive():
        return
    _stop_event.clear()
    _flusher_thread = threading.Thread(
        target=_flusher_loop, name="outbound-telemetry-batcher", daemon=True
    )
    _flusher_thread.start()


def stop() -> None:
    """FastAPI shutdown hook'tan cagrilir. Son flush yapilir."""
    _stop_event.set()
    if _flusher_thread is not None:
        _flusher_thread.join(timeout=FLUSH_WINDOW_SEC + 2)
    # Shutdown sirasinda buffer'da kalan readings'i son kez gonder.
    try:
        _flush_once()
    except Exception:  # noqa: BLE001
        logger.exception("outbound_telemetry_batcher_final_flush_error")
