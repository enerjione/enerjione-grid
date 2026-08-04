"""Telemetri kalicilastirma tuketicisi — NATS JetStream uzerinden.

NEREDE KOSAR
------------
HTTP surecinde DEGIL. `leader` bu isi yalnizca arka plan rolundeki surecte
acar (bkz. core/service_role.py) ve varsayilan topolojide o surec
`e1-grid-backend-worker` container'idir; `e1-grid-backend-api` `SERVICE_ROLE=api`
ile kosar ve HICBIR JetStream tuketicisi acmaz — yalnizca HTTP alir ve
gateway'den geleni `outbox -> jetstream_bus` uzerinden akisa YAYINLAR.

HANGI AKISTAN OKUR
------------------
Hedef: tag-engine CIKISI (`e1.telemetry.normalized.>`). Boylece arsivdeki
deger ile alarm-service / iec104-outbound / modbus-outbound'un gordugu deger
AYNI normalizasyondan gecer. Onceden bu tuketici ham akisi (`e1.telemetry.raw.>`)
okuyordu ve iki farkli karar zinciri olusuyordu.

Gecis TEK ADIMDA YAPILMAZ — eski RAW durable'inda HENUZ YAZILMAMIS olcumler
birikmis olabilir (olcum: 6.5M). Bu yuzden iki fazli:

  FAZ 1 (drenaj)  : eski durable AYNEN devralinir (ayni isim, ayni stream,
                    ayni konum). JetStream mesajlari kaldigi yerden verir —
                    tek mesaj atlanmaz, tek mesaj tekrarlanmaz.
  FAZ 2 (gecis)   : RAW durable'inda ISLENMEMIS TEK MESAJ KALMAYINCA
                    (`_drenaj_bitti_mi` — sunucunun `num_pending` VE
                    `num_ack_pending` sayaclari) NORMALIZED durable'i
                    olusturulur, biraz geri sarilarak (bkz.
                    telemetry_persist_cutover_overlap_sec); sonra eski
                    durable SILINIR.

Faz gecisi kendiliginden olur (`TELEMETRY_PERSIST_SOURCE=auto`, varsayilan);
operator adimina bagli DEGILDIR.

CIFT KAYIT NEDEN OLMAZ
----------------------
  * Tek surec, tek fetch dongusu: iki abonelik AYNI ANDA acik kalmaz.
  * Kumede tek lider (Postgres advisory lock) — guncelleme sirasinda eski
    surec kilidi birakmadan yeni surec HIC tuketmez.
  * Geri sarilan pencerede tekrar gelen olcumler `processed_messages`
    defteriyle elenir; `CONSUMER_NAME` bilerek DEGISTIRILMEDI, aksi halde
    defter sifirlanir ve gecis aninda her olcum ikinci kez yazilirdi.

Asyncio loop ayri bir thread'de calisir; NATS reconnect ve durable consumer
sayesinde process restart'inda kaldigi yerden devam eder.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.device import Device
from app.models.processed_message import ProcessedMessage
from app.schemas.telemetry import TelemetryIn
from app.services.ws_broadcaster import broadcaster as ws_broadcaster

logger = logging.getLogger(__name__)

# IDEMPOTENCY DEFTERININ ANAHTARI — `processed_messages.consumer_name`.
#
# BU DEGER DEGISTIRILEMEZ. NATS durable adiyla ILGISI YOK; kalicilastirma
# hangi akistan (raw/normalized) beslenirse beslensin, hangi container'da
# kosarsa kossun ayni kalmak ZORUNDA. Degistirilirse defter bir anda BOS
# gorunur ve gecis penceresinde geri sarilan her olcum IKINCI KEZ yazilir.
CONSUMER_NAME = "backend-api.telemetry-persister"

# Kalicilastirmanin okudugu akis.
KAYNAK_RAW = "raw"
KAYNAK_NORMALIZED = "normalized"
# `telemetry_persist_source` icin gecerli degerler ("auto" = otomatik gecis).
KAYNAK_AUTO = "auto"

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _kaynak_tercihi() -> str:
    """`TELEMETRY_PERSIST_SOURCE` — taninmayan deger `auto`ya duser."""
    ham = (settings.telemetry_persist_source or "").strip().lower()
    if ham in (KAYNAK_AUTO, KAYNAK_RAW, KAYNAK_NORMALIZED):
        return ham
    if ham:
        logger.warning(
            "telemetry_persist_source_unknown deger=%r — `auto` varsayildi "
            "(gecerli: auto, raw, normalized)",
            ham,
        )
    return KAYNAK_AUTO


def _cutover_geri_sarma_sec() -> int:
    """Gecis aninda NORMALIZED akisinda ne kadar geri sarilacak (saniye).

    TAVAN `processed_messages` defterinin OMRUDUR ve burada ZORLANIR.
    Defterden daha uzun geri sarmak, dedup kaydi ZATEN SILINMIS olcumleri
    tekrar getirmek demektir — yani tam da onlemeye calistigimiz cift kayit.
    Bu yuzden yapilandirma degeri sessizce kirpilmaz, kirpilir VE loglanir.
    """
    tavan = max(0, int(settings.processed_messages_retention_hours) * 3600)
    istenen = max(0, int(settings.telemetry_persist_cutover_overlap_sec))
    if istenen > tavan:
        logger.warning(
            "telemetry_persist_cutover_overlap_clamped istenen=%ds tavan=%ds — "
            "geri sarma penceresi dedup defterinin (processed_messages) omrunu "
            "asamaz, yoksa ayni olcum IKINCI KEZ yazilirdi",
            istenen,
            tavan,
        )
        return tavan
    return istenen


# --------------------------------------------------------------------------
# ISLEM TELEMETRISI — "tuketici yetisiyor mu?"
#
# NEDEN GEREKLI: telemetri akisi NATS stream'inde tamponlanir ve stream
# `discard=old` ile calisir. Yani tuketici gelis hizinin GERISINE duserse
# tampon dolar ve EN ESKI mesajlar SESSIZCE dusurulur. Ekranda hata yok,
# alarm yok; sadece bazi okumalar hic gelmemis olur.
#
# Bu sessizlik bilincli bir takas (sistem durmasin diye) ama gorunurluk
# olmadan tehlikeli. Asagidaki sayaclar "yetisiyor muyuz" sorusunun tek
# cevabidir.
#
# BACKLOG BEDAVA OLCULUR: JetStream her mesajin metadata'sinda `num_pending`
# tasir — tuketicinin ONUNDE bekleyen mesaj sayisi. Ayrica bir consumer_info
# cagrisi yapmaya gerek yok.
# --------------------------------------------------------------------------

_stats_lock = threading.Lock()
_stats: dict[str, Any] = {
    "running": False,
    "connected": False,
    # Hangi akistan besleniyor: "raw" (gecis oncesi drenaj) | "normalized"
    # (hedef mimari) | None (henuz baglanmadi). Sistem Durumu bunu gosterir;
    # aksi halde gecisin tamamlanip tamamlanmadigi ancak NATS CLI ile
    # anlasilirdi.
    "source": None,
    "last_fetch_at": None,        # ISO string
    "last_batch_size": 0,
    "last_batch_duration_sec": 0.0,
    "backlog": None,              # num_pending — tuketicinin onunde bekleyen
    "processed_total": 0,
    "bad_total": 0,
    "reconnects": 0,
    "last_error": None,
}
# Throughput icin kayan pencere: (zaman, islenen_adet) ciftleri.
_throughput_window: list[tuple[float, int]] = []
_THROUGHPUT_WINDOW_SEC = 60.0


def _stats_update(**kwargs: Any) -> None:
    with _stats_lock:
        _stats.update(kwargs)


def _stats_record_batch(*, size: int, duration: float, backlog: int | None, bad: int) -> None:
    now = _time.monotonic()
    with _stats_lock:
        _stats["last_fetch_at"] = datetime.now(timezone.utc).isoformat()
        _stats["last_batch_size"] = size
        _stats["last_batch_duration_sec"] = round(duration, 4)
        _stats["processed_total"] += size
        _stats["bad_total"] += bad
        if backlog is not None:
            _stats["backlog"] = backlog
        _throughput_window.append((now, size))
        cutoff = now - _THROUGHPUT_WINDOW_SEC
        while _throughput_window and _throughput_window[0][0] < cutoff:
            _throughput_window.pop(0)


# "Henuz hic uyarilmadi" = None. 0.0 DEGIL — bilincli.
#
# `time.monotonic()` sabit bir baslangic noktasi vermez; Linux'ta makine
# acilisindan beri gecen suredir. Baslangic degeri 0.0 olsaydi acilistan
# sonraki ILK `telemetry_backlog_warn_interval_sec` saniye boyunca
# `now - 0.0 < interval` cikar ve ILK uyari bastirilirdi — hem de tam
# backlog'un en yuksek oldugu an, yeniden baslatmanin hemen ardindan.
# (Windows'ta uptime buyuk oldugu icin bu davranis gorunmuyordu; hatayi
# Linux CI ortaya cikardi.)
_last_backlog_warn_at: float | None = None


def _warn_if_backlog_high(backlog: int | None) -> None:
    """Backlog esigi asilirsa loglar ve denetim kaydina yazar.

    Bu, `discard=old` tercihinin karsiligidir: tampon tasarsa mesajlar
    SESSIZCE dusuruluyor, dolayisiyla tasmaya YAKLASILDIGINI haber verecek
    bir sinyal sart. Esik, stream tavaninin cok altinda tutulur ki operatorun
    mudahale etmesi icin zaman kalsin.

    Olay kaydi rate-limit'lidir; backlog saatlerce yuksek kalsa bile
    `system_events` tablosunu doldurmaz.
    """
    global _last_backlog_warn_at
    if backlog is None or backlog < settings.telemetry_backlog_warn_threshold:
        return
    now = _time.monotonic()
    # ILK uyari her zaman gecer; rate-limit yalnizca IKINCIDEN itibaren isler.
    # Mutlak degeri karsilastirmak yerine "daha once uyardik mi" sorusunu
    # sormak, monotonic()'in baslangic noktasindan bagimsiz kilar.
    if (
        _last_backlog_warn_at is not None
        and now - _last_backlog_warn_at < settings.telemetry_backlog_warn_interval_sec
    ):
        return
    _last_backlog_warn_at = now
    snapshot = get_stats()
    logger.warning(
        "telemetry_backlog_high backlog=%d threshold=%d throughput=%.1f msg/s "
        "(tuketici gelis hizinin gerisinde — tampon tasarsa VERI KAYBI baslar)",
        backlog,
        settings.telemetry_backlog_warn_threshold,
        snapshot.get("throughput_msgs_per_sec", 0.0),
    )
    try:
        from app.services.event_service import record_event

        db = SessionLocal()
        try:
            record_event(
                db,
                category="telemetry",
                event_type="telemetry_backlog_high",
                severity="warning",
                message=(
                    f"Telemetri tuketicisi geride: {backlog} mesaj bekliyor "
                    f"({snapshot.get('throughput_msgs_per_sec', 0.0)} msg/sn isleniyor)"
                ),
                metadata={
                    "backlog": backlog,
                    "threshold": settings.telemetry_backlog_warn_threshold,
                    "throughput_msgs_per_sec": snapshot.get("throughput_msgs_per_sec"),
                    "last_batch_duration_sec": snapshot.get("last_batch_duration_sec"),
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.debug("backlog_event_record_failed", exc_info=True)


def get_stats() -> dict[str, Any]:
    """Tuketicinin anlik durumu. Sistem Durumu sayfasi bunu okur.

    `throughput_msgs_per_sec` son 60 saniyelik kayan ortalamadir; anlik
    dalgalanmalari yumusatir. `backlog` en son fetch anindaki num_pending —
    surekli 0 civarinda olmasi beklenir; kalici olarak buyuyorsa tuketici
    gelis hizinin gerisindedir ve tampon tasarsa VERI KAYBI baslar.
    """
    with _stats_lock:
        snapshot = dict(_stats)
        if _throughput_window:
            span = max(1e-6, _throughput_window[-1][0] - _throughput_window[0][0])
            total = sum(n for _t, n in _throughput_window)
            # Tek ornek varsa span ~0 olur; bolmeyi anlamli tutmak icin
            # en az 1 saniye kabul ediyoruz.
            snapshot["throughput_msgs_per_sec"] = round(total / max(1.0, span), 2)
        else:
            snapshot["throughput_msgs_per_sec"] = 0.0
    return snapshot


def _persist_batch(msgs: list) -> tuple[list, list, list, list]:  # noqa: ANN001
    """Bir fetch batch'ini TEK session + TEK commit ile isler.

    Onceki tasarim her mesaji ayri SessionLocal + ayri commit ile isliyordu;
    200 cihaz yukunde DB round-trip gelis hizindan yavas kalip 1.4M mesajlik
    backlog + ack_pending tikanmasi (consumer DONMASI) yaratti. Burada 256-500
    mesaj tek transaction'da yazilir -> DB round-trip ~batch kadar azalir,
    throughput gelis hizini gecer, backlog erir.

    Idempotency: batch'teki message_id'ler TEK `IN` sorgusuyla kontrol edilir
    (256 ayri SELECT yerine). Device lookup batch-ici cache ile tekrar sorgu
    yapmaz. Historian ayni commit'e dahil (ayri session/commit kaldirildi).

    Donus: (ok_msgs, bad_msgs, ok_payloads, outbound_payloads)
      - ok_msgs: commit BASARILI olduktan sonra ack edilecek NATS mesajlari
        (yeni islenmis + zaten islenmis skip'ler).
      - bad_msgs: parse/validation hatasi olan mesajlar -> caller DLQ/nak yapar.
      - ok_payloads: commit sonrasi WS broadcast icin ham payload'lar.
      - outbound_payloads: commit sonrasi IEC104/REST/MQTT dispatch payload'lari.

    Kismi hata: bir mesajin process'i patlarsa TUM batch nak EDILMEZ; o mesaj
    bad_msgs'e ayrilir, kalan batch commit'e girer. Tek poison mesaj 255
    saglikli mesaji redeliver dongusune sokmaz.
    """
    from app.models.telemetry import Telemetry
    from app.models.telemetry_history import TelemetryHistory
    from app.models.telemetry_latest import TelemetryLatest
    from app.services.device_clock_service import assess_device_timestamp
    from app.services import historian_policy
    from app.services.tag_engine_service import (
        map_quality_to_status,
        normalize_quality,
        process_telemetry_reading,
        should_write_last_update,
    )
    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    ok_msgs: list = []
    bad_msgs: list = []
    ok_payloads: list = []
    outbound_payloads: list = []  # commit sonrasi IEC104/REST/MQTT dispatch

    # 1) Parse + message_id'leri topla. Parse hatasi -> bad_msgs (DLQ/nak).
    parsed: list[tuple[Any, dict, str]] = []  # (msg, payload, message_id)
    for msg in msgs:
        try:
            payload = json.loads(msg.data.decode("utf-8"))
        except Exception:  # noqa: BLE001
            bad_msgs.append(msg)
            continue
        message_id = str(payload.get("message_id") or "")
        if not message_id:
            message_id = str(uuid4())
            payload["message_id"] = message_id
        parsed.append((msg, payload, message_id))

    if not parsed:
        return ok_msgs, bad_msgs, ok_payloads, outbound_payloads

    db = SessionLocal()
    try:
        # 2) Idempotency + device lookup: batch basina IKISER sorgu. Onceki
        # davranis mesaj basina device SELECT yapiyordu (500 mesaj -> yuzlerce
        # DB round-trip). Tum code'lari tek IN sorgusuyla cache'le.
        ids = [mid for (_m, _p, mid) in parsed]
        seen: set[str] = set(
            db.scalars(
                select(ProcessedMessage.message_id).where(
                    ProcessedMessage.consumer_name == CONSUMER_NAME,
                    ProcessedMessage.message_id.in_(ids),
                )
            ).all()
        )
        device_codes = {
            str(payload.get("device_code") or "") for (_m, payload, _mid) in parsed
        }
        devices = db.scalars(
            select(Device).where(Device.code.in_(device_codes))
        ).all()
        device_cache: dict[str, Device] = {device.code: device for device in devices}
        historian_rows: list[dict[str, Any]] = []
        # (device_id, signal_key) -> son deger satiri. Sozluk cunku ayni batch'te
        # ayni cift birden fazla gelebilir ve tek INSERT icinde ON CONFLICT ayni
        # satiri iki kez guncelleyemez ("cannot affect row a second time").
        latest_rows: dict[tuple[int, str], dict[str, Any]] = {}

        for msg, payload, message_id in parsed:
            if message_id in seen:
                ok_msgs.append(msg)  # zaten islenmis -> sadece ack
                continue
            try:
                reading = TelemetryIn(**payload)
            except ValidationError as exc:
                logger.warning(
                    "telemetry-consumer-invalid-payload msg=%s error=%s", message_id, exc
                )
                bad_msgs.append(msg)
                continue

            dcode = reading.device_code
            device = device_cache.get(dcode)

            if device is None:
                logger.warning(
                    "telemetry-consumer-device-not-found msg=%s device_code=%s",
                    message_id, dcode,
                )
                # Bilinmeyen cihaz: idempotency isaretle, mesaji ack'le (sonsuz
                # redeliver olmasin). Cihaz sonradan eklenirse yeni mesajlar gelir.
                db.add(ProcessedMessage(
                    consumer_name=CONSUMER_NAME,
                    message_id=message_id,
                    processed_at=datetime.now(timezone.utc),
                ))
                seen.add(message_id)
                ok_msgs.append(msg)
                continue

            # process_telemetry_reading: Telemetry obj + device mutasyonu (ayni
            # db, commit yok). Patlarsa TUM batch'i rollback ETME -> o mesaji
            # bad_msgs'e ayir, minimal fallback ile devam.
            telemetry = None
            try:
                telemetry, _event = process_telemetry_reading(device, reading, db=db)
            except SQLAlchemyError:
                # DB/transaction hatasinda fallback ile devam edilmez: session
                # aborted olabilir. Batch exception ile cikar, hicbir msg ack
                # edilmez; JetStream redeliver eder (veri kaybi yok).
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "telemetry-consumer-process-error msg=%s error=%s", message_id, exc
                )
                # Saf is-mantigi hatasinda minimal telemetry + status fallback.
                nq = normalize_quality(reading.quality)
                device.communication_status = map_quality_to_status(nq)
                if device.communication_status.value == "online":
                    # Karar ana yolla AYNI fonksiyondan geliyor; kosulu
                    # kopyalamak, birinin sessizce eski davranisa donmesi
                    # demekti.
                    _simdi = datetime.now(timezone.utc)
                    if should_write_last_update(device.last_update_at, _simdi):
                        device.last_update_at = _simdi
                _fb_at, _fb_quality = assess_device_timestamp(
                    getattr(reading, "device_event_at", None),
                    reported_quality=getattr(reading, "timestamp_quality", None),
                )
                telemetry = Telemetry(
                    device_id=device.id,
                    signal_key=reading.signal_key,
                    value=reading.value,
                    value_string=reading.value_string,
                    quality=nq,
                    source_timestamp=reading.source_timestamp,
                    # Fallback yolunda da damgalanir: aksi halde is-mantigi
                    # hatasi alan mesajlarda saat durumu SESSIZCE kaybolurdu.
                    device_event_at=_fb_at,
                    timestamp_quality=_fb_quality,
                )

            db.add(telemetry)
            db.add(ProcessedMessage(
                consumer_name=CONSUMER_NAME,
                message_id=message_id,
                processed_at=datetime.now(timezone.utc),
            ))
            # Historian row'unu biriktir; dongu SONUNDA tum batch TEK
            # INSERT ... VALUES (...), (...) ile gider (mesaj basina execute yok).
            # Cihazin kendi olay zamani (varsa) + makullugu. `source_timestamp`
            # AYNEN kaliyor (PK/partition kolonu); bu ikisi yalnizca analiz
            # icin ayri kolonlarda duruyor. Bkz. device_clock_service.
            #
            # Degerlendirme TEKRAR yapilmaz: canli satiri kuran
            # `process_telemetry_reading` zaten damgaladi. Ikinci kez cagirmak
            # 7 gunluk pencerenin tam sinirinda iki satirin FARKLI kalite
            # almasina yol acabilirdi (araya gecen mikrosaniyeler yuzunden).
            _dev_at = telemetry.device_event_at
            _ts_quality = telemetry.timestamp_quality
            # Kalite BIR KEZ normalize edilir ve hem arsiv KARARINA hem
            # yazilan satirlara ayni deger gider. Karara gecirilmesi sart:
            # olu bant yalnizca sayiya bakarsa, esik icinde donmus bir
            # olcumde good->invalid/comm_lost gecisi arsive HIC girmez.
            _kalite = normalize_quality(reading.quality)
            # ARSIV POLITIKASI — her okuma arsive yazilmaz.
            #
            # Gercek SCADA pratigi: anlik deger her zaman guncel tutulur
            # (`telemetry_latest`, hemen asagida) ama arsive yalnizca
            # isaretlenen tag'ler, olu bant suzgecinden gecerek yazilir.
            # Alarm dogrulugu ETKILENMEZ: alarm-service akis tabanli
            # calisiyor, gecmis sorgusu yapmiyor.
            if historian_policy.should_archive(
                db,
                device_id=device.id,
                signal_key=reading.signal_key,
                value=reading.value,
                quality=_kalite,
            ):
                historian_rows.append({
                    "device_id": device.id,
                    "signal_key": reading.signal_key,
                    "value": reading.value,
                    "value_string": reading.value_string,
                    "quality": _kalite,
                    "source_timestamp": reading.source_timestamp,
                    "device_event_at": _dev_at,
                    "timestamp_quality": _ts_quality,
                })
            # `telemetry_latest` — canli ekranin okudugu SON deger tablosu.
            # Ayni batch'te ayni (cihaz, sinyal) birden fazla kez gelebilir;
            # ON CONFLICT tek bir INSERT icinde ayni satiri iki kez
            # guncelleyemez ("cannot affect row a second time"), o yuzden
            # burada sozlukte tekillestirip EN YENISINI birakiyoruz.
            _latest_key = (device.id, reading.signal_key)
            _onceki = latest_rows.get(_latest_key)
            if _onceki is None or reading.source_timestamp >= _onceki["source_timestamp"]:
                latest_rows[_latest_key] = {
                    "device_id": device.id,
                    "signal_key": reading.signal_key,
                    "value": reading.value,
                    "value_string": reading.value_string,
                    "quality": _kalite,
                    "source_timestamp": reading.source_timestamp,
                    "device_event_at": _dev_at,
                    "timestamp_quality": _ts_quality,
                    "updated_at": datetime.now(timezone.utc),
                }
            seen.add(message_id)  # ayni batch'te duplicate message_id'ye karsi
            ok_msgs.append(msg)
            # WS yayini ham gateway payload'unu tasir; saat degerlendirmesini
            # UZERINE YAZIYORUZ. Gateway'in ham bildirimi degil BIZIM
            # degerlendirmemiz otoriter: gateway hic bir sey demese bile
            # 2000-01-01 damgasini "invalid" olarak isaretleyen biziz. Aksi
            # halde canli ekran (WS) ile yenilenmis snapshot (`/signals/live`)
            # ayni satir icin farkli sey gosterirdi.
            payload["device_event_at"] = _dev_at.isoformat() if _dev_at else None
            payload["timestamp_quality"] = _ts_quality
            ok_payloads.append(payload)

            # Outbound dispatch payload'u — status commit ONCESI yakalanir
            # (commit sonrasi device expire olabilir). Dispatch commit SONRASI.
            status_val = (
                device.communication_status.value
                if hasattr(device.communication_status, "value")
                else str(device.communication_status)
            )
            sig_source = None
            if reading.signal_key and "." in reading.signal_key:
                sig_source = reading.signal_key.split(".", 1)[0].lower()
            outbound_payloads.append({
                "message_id": message_id,
                "correlation_id": reading.correlation_id or message_id,
                "event_kind": "telemetry",
                "device_code": reading.device_code,
                "signal_key": reading.signal_key,
                "signal_source": sig_source,
                "source_gateway": reading.source_gateway,
                "value": reading.value,
                "value_string": reading.value_string,
                "quality": reading.quality,
                "status": status_val,
                "source_timestamp": reading.source_timestamp.isoformat() if reading.source_timestamp else None,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            })

        # Historian: tum batch TEK statement, idempotent ON CONFLICT.
        if historian_rows:
            db.execute(
                _pg_insert(TelemetryHistory)
                .values(historian_rows)
                .on_conflict_do_nothing(
                    index_elements=["device_id", "signal_key", "source_timestamp"]
                )
            )

        # `telemetry_latest`: canli ekranin okudugu SON deger tablosu.
        # Tum batch TEK upsert.
        #
        # ESKI DEGER YENIYI EZMEZ — `WHERE` kosulu bunun icin.
        # NATS en-az-bir-kez teslim eder ve mesajlar sira DISINDA gelebilir
        # (redelivery, paralel tuketici, gateway yeniden baglanmasi). Kosulsuz
        # bir `DO UPDATE` bayat bir okumayi "son deger" yapardi ve bu tablo
        # canli ekranin + WS yayininin kaynagi oldugu icin sonuc dogrudan
        # operatore yansirdi: deger geri sicrar, ariza gecisi yanlis gorunur.
        if latest_rows:
            _stmt = _pg_insert(TelemetryLatest).values(list(latest_rows.values()))
            db.execute(
                _stmt.on_conflict_do_update(
                    index_elements=["device_id", "signal_key"],
                    set_={
                        "value": _stmt.excluded.value,
                        "value_string": _stmt.excluded.value_string,
                        "quality": _stmt.excluded.quality,
                        "source_timestamp": _stmt.excluded.source_timestamp,
                        "timestamp_quality": _stmt.excluded.timestamp_quality,
                        "device_event_at": _stmt.excluded.device_event_at,
                        "updated_at": _stmt.excluded.updated_at,
                    },
                    where=(
                        _stmt.excluded.source_timestamp
                        >= TelemetryLatest.source_timestamp
                    ),
                )
            )

        # 3) TEK commit. Basarisizsa (nadir: paralel consumer carpismasi) tum
        # batch redeliver edilir (ack YAPILMADI) -> at-least-once korunur.
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.warning("telemetry-consumer-batch-commit-conflict — redeliver bekleniyor")
            # Commit patladi: hicbir sey ack'leme, hepsi redeliver olsun.
            # (IN pre-check duplicate'leri eledigi icin bu neredeyse hic tetiklenmez.)
            return [], bad_msgs, [], []
        except DataError as exc:
            # VERI BOZUK — redeliver ETMEK ISE YARAMAZ.
            #
            # `DataError` IntegrityError'in KARDESIDIR (ikisi de
            # DatabaseError'dan turer), yani yukaridaki yakalayici onu hic
            # gormuyordu. Istisna disari cikiyor ve cagirandaki genel
            # `except Exception` bunu BAGLANTI HATASI sanip
            # `telemetry_consumer_reconnect` logluyordu — operator sebebi
            # agda ariyordu.
            #
            # Tipik sebep kolon genisligini asan bir deger. Artik `TelemetryIn`
            # uzunluklari dogruladigi icin bu yola normalde HIC girilmez; buraya
            # dusuluyorsa dogrulanmayan yeni bir alan var demektir. Redeliver
            # ayni hatayi 10 kez tekrarlar ve batch'teki SAGLAM olcumleri de
            # goturur; bu yuzden batch bad_msgs'e alinip karantinaya gonderilir.
            db.rollback()
            logger.error(
                "telemetry_batch_data_error mesaj=%d — batch karantinaya alindi "
                "(redeliver ayni hatayi tekrarlardi). Sebep: %s",
                len(ok_msgs),
                str(exc)[:500],
            )
            return [], bad_msgs + ok_msgs, [], []
    finally:
        db.close()

    return ok_msgs, bad_msgs, ok_payloads, outbound_payloads


def _dispatch_outbound(outbound_payloads: list) -> None:  # noqa: ANN001
    """Commit sonrasi IEC104/REST/MQTT dispatch. Kendi kisa session'i ile;
    hata telemetri akisini bozmaz (mevcut _persist_message davranisi)."""
    if not outbound_payloads:
        return
    from app.models.outbound_target import OutboundTarget
    from app.services.outbound_dispatch_service import dispatch_event
    from app.services.outbound_telemetry_batcher import submit as rest_batch_submit
    from app.services.mqtt_publisher_service import submit_telemetry as mqtt_submit

    db = SessionLocal()
    try:
        targets = list(
            db.scalars(select(OutboundTarget).where(OutboundTarget.is_active.is_(True))).all()
        )
        for op in outbound_payloads:
            try:
                dispatch_event(
                    db, event_kind="telemetry", payload=op, targets=targets
                )  # IEC104 anlik
                rest_batch_submit(op)  # REST 5sn batch
                mqtt_submit(op)  # MQTT per-target
            except Exception:  # noqa: BLE001
                logger.debug("outbound_dispatch_failed_telemetry msg=%s",
                             op.get("message_id"), exc_info=True)
    finally:
        db.close()


# --------------------------------------------------------------------------
# KAYNAK SECIMI VE GECIS (raw -> normalized)
#
# Gecisin DURUM DEFTERI JetStream'in kendisidir: "yeni durable var mi?".
# Ayri bir bayrak (DB satiri, dosya) BILEREK tutulmuyor — bayrak ile gercek
# durum ayrisabilirdi ("gectim" diyen bir bayrak ama silinmis bir durable,
# ya da tersi) ve ayrisma ancak veri kaybi/tekrari olarak fark edilirdi.
# --------------------------------------------------------------------------


async def _consumer_var_mi(js, stream: str, durable: str) -> bool:  # noqa: ANN001
    try:
        await js.consumer_info(stream, durable)
        return True
    except Exception:  # noqa: BLE001  (nats.js.errors.NotFoundError dahil)
        return False


def _consumer_cfg(*, durable: str, deliver_policy, opt_start_time: str | None = None):  # noqa: ANN001
    """Pull consumer konfigurasyonu — raw ve normalized icin AYNI parametreler."""
    from nats.js.api import ConsumerConfig

    return ConsumerConfig(
        durable_name=durable,
        deliver_policy=deliver_policy,
        opt_start_time=opt_start_time,
        # persist + WS tipik <100ms; 60sn defansif cap.
        ack_wait=60,
        # Batch-commit consumer: fetch inflight'i sinirlar; batch boyutunun
        # kati olmali. Backlog'u TEK BASINA cozmez, sadece tikanma tavani.
        max_ack_pending=settings.nats_pull_max_ack_pending,
        # Poison message sonsuz redeliver edilmez -> DLQ.
        max_deliver=settings.nats_worker_max_deliver,
    )


async def _bagla(  # noqa: ANN001
    js, *, stream: str, durable: str, deliver_policy, opt_start_time: str | None = None
):
    """Durable YOKSA olustur, sonra ONA BAGLAN (config gondermeden).

    NEDEN `pull_subscribe(config=...)` DEGIL: mevcut bir durable'a her
    baglanista config gondermek, config surustugu anda sunucunun reddetmesi
    demek. Gecisin yaptigi tam olarak budur (`deliver_policy` degisir).
    `add_consumer` + `pull_subscribe_bind` ayrimi, "olusturma parametreleri"
    ile "baglanma" islerini birbirinden ayirir: mevcut durable'in konumu ve
    ayarlari ASLA ezilmez — devralinan 6.5M birikimin kaybolmamasi buna
    bagli.
    """
    if not await _consumer_var_mi(js, stream, durable):
        await js.add_consumer(
            stream,
            config=_consumer_cfg(
                durable=durable,
                deliver_policy=deliver_policy,
                opt_start_time=opt_start_time,
            ),
        )
    return await js.pull_subscribe_bind(consumer=durable, stream=stream)


async def _eski_raw_durable_temizle(js) -> None:  # noqa: ANN001
    """Bosalmis RAW durable'ini siler (yoksa sessizce gecer).

    NEDEN SILINIYOR: birakilirsa bir SURUM GERI DONUSUNDE (rollback) eski
    kod onu kaldigi yerden okumaya devam eder ve NORMALIZED uzerinden ZATEN
    yazilmis olcumleri IKINCI KEZ yazar — `processed_messages` defteri 2
    saatlik oldugu icin o tekrari da yutamaz. Silinmis olursa eski kod onu
    `DeliverPolicy.NEW` ile YENIDEN yaratir, yani akisin basindan degil o
    andan baslar: ne tekrar olur ne de yeni gelen olcum kaybolur.
    """
    try:
        await js.delete_consumer(
            settings.nats_stream_telemetry_raw,
            settings.nats_consumer_telemetry_persist,
        )
        logger.info(
            "telemetry_persist_raw_durable_silindi durable=%s — drenaj bitti",
            settings.nats_consumer_telemetry_persist,
        )
    except Exception:  # noqa: BLE001
        logger.debug("telemetry_persist_raw_durable_delete_skipped", exc_info=True)


async def _normalized_durable_temizle(js) -> None:  # noqa: ANN001
    """Kaynak `raw`a alindiysa NORMALIZED durable'ini siler.

    NEDEN: `raw` gecisi ERTELEME/GERI ALMA kolu. Gecis daha once tamamlanmis
    ve sonra bu kola basilmissa, NORMALIZED durable'i ORTADA KALIR ve onu
    kimse tuketmez — `num_pending` saatlerce buyur. Tercih tekrar `auto`
    yapildiginda (ki `auto` compose varsayilanidir, yani container'in bir
    sonraki yeniden yaratilmasi bunu KENDILIGINDEN yapar) o birikimin
    TAMAMI yeniden islenir. Oysa ayni olcumler bu arada RAW uzerinden ZATEN
    yazilmistir ve `processed_messages` defteri 2 saatlik oldugu icin
    tekrari yutamaz -> `telemetry` tablosunda CIFT KAYIT (bu tabloda dogal
    anahtar yok, ON CONFLICT koruyamaz).

    Silinince `auto`ya donus durable'i sinirli geri sarmayla (bkz.
    `_cutover`) YENIDEN yaratir: ne tekrar olur ne kayip.
    """
    try:
        await js.delete_consumer(
            settings.nats_stream_telemetry_normalized,
            settings.nats_consumer_telemetry_persist_normalized,
        )
        logger.warning(
            "telemetry_persist_normalized_durable_silindi durable=%s — kaynak "
            "`raw`a alindi; tuketilmeyen durable birikip `auto`ya donuste "
            "CIFT KAYIT uretirdi",
            settings.nats_consumer_telemetry_persist_normalized,
        )
    except Exception:  # noqa: BLE001  (yoksa NotFoundError — normal)
        logger.debug("telemetry_persist_normalized_durable_delete_skipped", exc_info=True)


async def _drenaj_bitti_mi(js, *, stream: str, durable: str) -> bool:  # noqa: ANN001
    """Verilen durable'da ISLENMEMIS TEK MESAJ kaldi mi? Gecisin on kosulu.

    HER IKI YON de bunu kullanir: ileri gecis (RAW -> NORMALIZED) ve geri
    donus (NORMALIZED -> RAW). Terk edilecek durable ONCE bosalmali, cunku
    her iki yol da sonunda o durable'i SILER.

    KARAR NEDEN SUNUCUDAN SORULUYOR — fetch timeout'u IKI YONDEN de yanlis:

    1) YANLIS NEGATIF, yani gecis HIC OLMAZ. `fetch(batch, timeout)` sadece
       o pencerede TEK MESAJ BILE gelmediyse TimeoutError atar; kismi batch
       donduren cagri istisna ATMAZ (bkz. nats-py `_fetch_n`: NO_MESSAGES
       gelince eldekini dondurur). Sahada ~1000 msg/sn akarken 5 saniyelik
       BOS bir pencere pratikte hic olusmaz — yani drenaj bitse bile gecis
       tetiklenmez ve kalicilastirma sonsuza kadar RAW'da kalirdi. Bu
       yuzden gecis artik `backlog == 0` olcumunde de deneniyor.

    2) YANLIS POZITIF, yani OLCUM KAYBI. Timeout "teslim edilebilir mesaj
       yok" demek; "is bitti" demek DEGIL. Teslim edilmis ama ack'lenmemis
       mesajlar sunucuda `num_ack_pending` icinde durur ve ancak `ack_wait`
       (60 sn) dolunca yeniden teslim edilir. Somut yol: drenajin sonunda
       bir batch `_persist_batch` icinde `SQLAlchemyError` ile duser ->
       hicbiri ack'lenmez -> dis `except` yeniden baglanir -> ilk fetch 5
       sn'de bos doner (num_pending = 0) -> eski kod GECIS yapip
       `_eski_raw_durable_temizle` ile durable'i SILERDI. O 500 olcum
       yazilmadan ve YENIDEN TESLIM EDILEMEDEN yok olurdu.

    Bilgi okunamazsa (ag/sunucu) `False` doner: karar VERILMEZ, bir sonraki
    turda tekrar sorulur. Guvenli taraf gecisi ertelemektir.
    """
    from nats.js.errors import NotFoundError

    try:
        bilgi = await js.consumer_info(stream, durable)
    except NotFoundError:
        # Durable YOK — devralinacak birikim de yok (temiz kurulum ya da
        # yarim kalmis bir gecisin ardindan). Drenaj tanim geregi bitti.
        return True
    except Exception:  # noqa: BLE001
        logger.debug("telemetry_persist_drenaj_sorgusu_basarisiz", exc_info=True)
        return False

    bekleyen = int(getattr(bilgi, "num_pending", 0) or 0)
    ack_bekleyen = int(getattr(bilgi, "num_ack_pending", 0) or 0)
    if bekleyen or ack_bekleyen:
        logger.debug(
            "telemetry_persist_drenaj_suruyor durable=%s bekleyen=%d ack_bekleyen=%d",
            durable,
            bekleyen,
            ack_bekleyen,
        )
        return False
    return True


async def _raw_drenaj_bitti_mi(js) -> bool:  # noqa: ANN001
    return await _drenaj_bitti_mi(
        js,
        stream=settings.nats_stream_telemetry_raw,
        durable=settings.nats_consumer_telemetry_persist,
    )


async def _normalized_drenaj_bitti_mi(js) -> bool:  # noqa: ANN001
    return await _drenaj_bitti_mi(
        js,
        stream=settings.nats_stream_telemetry_normalized,
        durable=settings.nats_consumer_telemetry_persist_normalized,
    )


async def _gecis_gerekiyorsa_yap(js, psub, kaynak):  # noqa: ANN001
    """Tercih ile gercek kaynak ayrismissa, TERK EDILECEK durable BOSALINCA gecer.

    Iki yon de ayni kurala tabidir — terk edilen durable siliniyor, dolayisiyla
    silinmeden once icinde yazilmamis olcum KALMAMALI:

      RAW -> NORMALIZED : tercih `auto`, ileri gecis (bkz. `_cutover`).
      NORMALIZED -> RAW : tercih `raw`, geri donus. Acilista da denenir
                          (`_kaynaga_bagla`) ama NORMALIZED o an dolu ise
                          ertelenir; bosalinca burada tamamlanir. Aksi halde
                          geri donus, NORMALIZED'de bekleyen olcumleri
                          silerdi — surec uzun sure duruk kaldiysa (yani tam
                          da geri donus istenen durumda) o pencere RAW geri
                          sarmasinin disinda kalir ve KAYBOLURDU.

    Donus: (psub, kaynak) — kosullar saglanmadiysa GIRDIYLE AYNI.
    """
    tercih = _kaynak_tercihi()
    if kaynak == KAYNAK_RAW and tercih == KAYNAK_AUTO:
        if not await _raw_drenaj_bitti_mi(js):
            return psub, kaynak
        psub, kaynak = await _cutover(js, psub)
        _stats_update(source=kaynak)
        return psub, kaynak
    if kaynak == KAYNAK_NORMALIZED and tercih == KAYNAK_RAW:
        if not await _normalized_drenaj_bitti_mi(js):
            return psub, kaynak
        psub, kaynak = await _geri_donus(js, psub)
        _stats_update(source=kaynak)
        return psub, kaynak
    return psub, kaynak


async def _kaynaga_bagla(js):  # noqa: ANN001
    """Acilista hangi akistan okunacagini belirler. Donus: (psub, kaynak)."""
    from nats.js.api import DeliverPolicy

    tercih = _kaynak_tercihi()
    norm_durable = settings.nats_consumer_telemetry_persist_normalized
    norm_stream = settings.nats_stream_telemetry_normalized

    # Gecis DAHA ONCE tamamlanmis mi? `raw` tercihi bunu GERI ALMA anlamina
    # gelir ve asagida ayrica ele alinir (bkz. `geri_donus`).
    gecis_yapilmis = await _consumer_var_mi(js, norm_stream, norm_durable)

    if tercih != KAYNAK_RAW and gecis_yapilmis:
        psub = await js.pull_subscribe_bind(consumer=norm_durable, stream=norm_stream)
        # Gecis aninda silme adimina yetisemeden crash olmus olabilir; kalmis
        # olan bos RAW durable'i burada temizlenir (rollback tekrar riski).
        await _eski_raw_durable_temizle(js)
        return psub, KAYNAK_NORMALIZED

    if tercih == KAYNAK_NORMALIZED:
        logger.warning(
            "telemetry_persist_source=normalized — RAW DRENAJI ATLANIYOR. "
            "Eski durable'da (%s) bekleyen olcumler varsa HIC YAZILMAYACAK. "
            "Bu ayar yalnizca temiz kurulum icindir; mevcut sahada `auto` kullanin.",
            settings.nats_consumer_telemetry_persist,
        )
        psub = await _bagla(
            js,
            stream=norm_stream,
            durable=norm_durable,
            deliver_policy=DeliverPolicy.NEW,
        )
        return psub, KAYNAK_NORMALIZED

    # GERI DONUS MU, ERTELEME MI?
    #
    # `raw` iki farkli anlama gelir ve ikisi ayni sekilde ele ALINAMAZ:
    #   * gecis HIC yapilmamis  -> sadece ERTELEME. RAW durable'i yerinde,
    #     konumu korunur, yapacak baska bir sey yok (asagidaki FAZ 1).
    #   * gecis yapilmis        -> GERI DONUS. Ama ONCE NORMALIZED durable'i
    #     BOSALMALI: geri donus onu siliyor ve icinde yazilmamis olcum
    #     kalabilir. Surec bir sureligine duruk kaldiysa — yani tam da geri
    #     donusun istendigi durumda — o birikim RAW geri sarma penceresinin
    #     (varsayilan 15 dk) DISINDA kalir ve silinmesiyle birlikte
    #     KAYBOLURDU. Dolu ise NORMALIZED'de kaliriz; bosalinca gecisi
    #     `_gecis_gerekiyorsa_yap` tamamlar.
    if tercih == KAYNAK_RAW and gecis_yapilmis:
        psub = await js.pull_subscribe_bind(consumer=norm_durable, stream=norm_stream)
        if await _normalized_drenaj_bitti_mi(js):
            return await _geri_donus(js, psub)
        logger.warning(
            "telemetry_persist_geri_donus_erteleniyor durable=%s — NORMALIZED "
            "akisinda yazilmamis olcum var; once o bosaltilacak, sonra RAW'a "
            "donulecek (silinmis durable'daki olcumler geri getirilemezdi)",
            norm_durable,
        )
        return psub, KAYNAK_NORMALIZED

    # FAZ 1 — eski RAW durable'i AYNEN devralinir: ayni isim, ayni stream,
    # ayni konum. JetStream mesajlari kaldigi yerden verir; birikim erir.
    psub = await _bagla(
        js,
        stream=settings.nats_stream_telemetry_raw,
        durable=settings.nats_consumer_telemetry_persist,
        # Yalnizca durable HIC YOKSA (temiz kurulum) gecerli: 2 gunluk
        # history'yi replay etmeyip guncelden baslar.
        deliver_policy=DeliverPolicy.NEW,
    )
    return psub, KAYNAK_RAW


async def _geri_donus(js, norm_psub):  # noqa: ANN001
    """GERI DONUS — NORMALIZED bosaldi, RAW'a don. Donus: (psub, kaynak).

    `_cutover`un AYNASI; adim sirasi ve gerekceler birebir ayni:
      1) RAW durable'i olusturulur. Cutover sirasinda SILINMISTI, yani
         yeniden yaratiliyor. `DeliverPolicy.NEW` ile yaratmak, NORMALIZED
         drenaji ile bu an arasindaki olcumleri atlamak olurdu; bu yuzden
         cutover ile AYNI pencere kadar geri sarilir. Tekrar gelenleri
         `processed_messages` defteri eler — pencere zaten (bkz.
         `_cutover_geri_sarma_sec`) defterin omruyle sinirli.
      2) NORMALIZED aboneligi kapatilir: iki abonelik ayni anda beslenmez.
      3) NORMALIZED durable'i silinir. Birakilsaydi kimse tuketmedigi icin
         birikir ve `auto`ya donuste (ki `auto` compose varsayilanidir)
         birikimin tamami yeniden islenerek CIFT KAYIT uretirdi.
    """
    from nats.js.api import DeliverPolicy

    geri_sarma = _cutover_geri_sarma_sec()
    baslangic = datetime.now(timezone.utc) - timedelta(seconds=geri_sarma)
    psub = await _bagla(
        js,
        stream=settings.nats_stream_telemetry_raw,
        durable=settings.nats_consumer_telemetry_persist,
        deliver_policy=DeliverPolicy.BY_START_TIME,
        opt_start_time=baslangic.isoformat(),
    )
    try:
        await norm_psub.unsubscribe()
    except Exception:  # noqa: BLE001
        logger.debug("telemetry_persist_normalized_unsubscribe_failed", exc_info=True)
    await _normalized_durable_temizle(js)
    logger.warning(
        "telemetry_persist_geri_donus kaynak=normalized->raw durable=%s "
        "geri_sarma=%ds — kalicilastirma yeniden HAM akistan besleniyor",
        settings.nats_consumer_telemetry_persist,
        geri_sarma,
    )
    return psub, KAYNAK_RAW


async def _cutover(js, raw_psub):  # noqa: ANN001
    """FAZ 2 — RAW bosaldi, NORMALIZED'e gec. Donus: (psub, kaynak).

    ADIM SIRASI HAYATIDIR:
      1) YENI durable olusturulur (geri sarilmis baslangicla). Once eskisini
         silmek, aradaki bir hatada hicbir tuketicisi olmayan bir pencere
         birakirdi — o penceredeki olcumler kaybolurdu.
      2) RAW aboneligi kapatilir. Iki abonelik ASLA ayni anda acik kalmaz;
         cift kaydin en olasi kaynagi budur.
      3) Eski durable silinir (rollback tekrarini onler).
    """
    from nats.js.api import DeliverPolicy

    geri_sarma = _cutover_geri_sarma_sec()
    baslangic = datetime.now(timezone.utc) - timedelta(seconds=geri_sarma)
    norm_durable = settings.nats_consumer_telemetry_persist_normalized
    norm_stream = settings.nats_stream_telemetry_normalized

    psub = await _bagla(
        js,
        stream=norm_stream,
        durable=norm_durable,
        deliver_policy=DeliverPolicy.BY_START_TIME,
        opt_start_time=baslangic.isoformat(),
    )
    try:
        await raw_psub.unsubscribe()
    except Exception:  # noqa: BLE001
        logger.debug("telemetry_persist_raw_unsubscribe_failed", exc_info=True)
    await _eski_raw_durable_temizle(js)
    logger.info(
        "telemetry_persist_cutover kaynak=raw->normalized durable=%s "
        "geri_sarma=%ds (tekrar gelen olcumler processed_messages ile elenir)",
        norm_durable,
        geri_sarma,
    )
    return psub, KAYNAK_NORMALIZED


def _consume_loop() -> None:
    """JetStream'den telemetri akisini dinler (bkz. modul docstring'i).

    Durable consumer ile process restart'inda kaldigi yerden devam eder.
    NATS gelmediyse veya bagklanti koparsa kendi icinde exponential backoff
    ile yeniden baglanir.
    """
    try:
        import nats as _nats  # type: ignore[import-not-found]
        from nats.errors import TimeoutError as _NatsTimeoutError  # type: ignore[import-not-found]
    except ImportError:
        logger.error(
            "telemetry_consumer_jetstream_disabled reason=nats_py_missing "
            "(pip install nats-py>=2.6). Telemetri DB'ye YAZILMIYOR!"
        )
        return

    async def _run() -> None:
        backoff = 2
        while not _stop_event.is_set():
            nc = None
            try:
                from app.core.nats_tls import nats_tls_context

                nc = await _nats.connect(
                    servers=[settings.nats_url],
                    connect_timeout=settings.nats_connect_timeout_sec,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=2,
                    name="e1-backend-api-consumer",
                    tls=nats_tls_context(),  # None = TLS kapali (varsayilan)
                )
                js = nc.jetstream()

                async def _handle_bad(msg) -> None:  # noqa: ANN001
                    """Parse/validation hatasi olan TEK mesaj: max_deliver'a gore
                    DLQ'ya tasi + ack, degilse nak ile redeliver."""
                    num_delivered = int(
                        getattr(getattr(msg, "metadata", None), "num_delivered", 1) or 1
                    )
                    is_terminal = num_delivered >= settings.nats_worker_max_deliver
                    logger.warning(
                        "telemetry-consumer-bad-msg subject=%s delivery=%d/%d terminal=%s",
                        getattr(msg, "subject", "?"),
                        num_delivered,
                        settings.nats_worker_max_deliver,
                        is_terminal,
                    )
                    if is_terminal:
                        try:
                            orig_subject = getattr(msg, "subject", "unknown")
                            dlq_subject = f"e1.dlq.backend-api.{orig_subject}"
                            dlq_headers = {
                                "X-DLQ-Reason": "max_deliver_exceeded",
                                "X-DLQ-Service": "backend-api",
                                "X-DLQ-Original-Subject": orig_subject,
                                "X-DLQ-Delivery-Count": str(num_delivered),
                            }
                            await js.publish(dlq_subject, msg.data, headers=dlq_headers)
                            logger.error("telemetry-consumer-dlq subject=%s", dlq_subject)
                            await msg.ack()
                        except Exception:  # noqa: BLE001
                            logger.exception("dlq_publish_failed")
                            try:
                                await msg.nak()
                            except Exception:  # noqa: BLE001
                                logger.debug("js_nak_failed", exc_info=True)
                    else:
                        try:
                            await msg.nak()
                        except Exception:  # noqa: BLE001
                            logger.debug("js_nak_failed", exc_info=True)

                # Durable PULL consumer.
                #
                # NEDEN pull (push degil): push consumer'da NATS mesajlari sunucu
                # hizinda client socket'ine iter; backend her mesajda senkron DB
                # yazimi yaptigi icin socket write buffer'i dolar ve NATS
                # "Slow Consumer Detected" ile BAGLANTIYI DUSURUR. 200 cihaz
                # yukunde bu dongu cihaz durumunu (communication_status) kesik
                # kesik gunceller -> yeni cihazlar UNKNOWN kalir. Pull'da backend
                # kendi hizinda fetch(batch) yapar; slow-consumer imkansiz.
                #
                # Hangi akisa baglanacagi ve gecisin hangi fazda oldugu
                # `_kaynaga_bagla` icinde belirlenir (bkz. modul docstring'i).
                psub, kaynak = await _kaynaga_bagla(js)
                logger.info(
                    "telemetry_consumer_running mode=pull kaynak=%s durable=%s url=%s",
                    kaynak,
                    (
                        settings.nats_consumer_telemetry_persist
                        if kaynak == KAYNAK_RAW
                        else settings.nats_consumer_telemetry_persist_normalized
                    ),
                    settings.nats_url,
                )
                _stats_update(source=kaynak)
                backoff = 2  # connect basarili — backoff sifirla
                # Fetch dongusu: backend kendi hizinda batch ceker, TEK
                # commit ile isler (_persist_batch), commit SONRASI topluca
                # ack'ler. fetch timeout'unda mesaj yoksa TimeoutError normal.
                # Batch-commit sayesinde throughput gelis hizini gecer ->
                # backlog erir, slow-consumer/ack_pending tikanmasi olmaz.
                _stats_update(connected=True, last_error=None)
                while not _stop_event.is_set():
                    try:
                        msgs = await psub.fetch(
                            batch=settings.nats_pull_batch_size, timeout=5
                        )
                    except (asyncio.TimeoutError, _NatsTimeoutError):
                        # Bu pencerede yeni mesaj yok — NORMAL ve ayni zamanda
                        # "backlog bos" demektir; olcume yansitiyoruz ki
                        # ekranda bayat bir backlog degeri asili kalmasin.
                        _stats_update(backlog=0)
                        # Bos pencere gecis icin bir ISARETTIR ama KANIT
                        # DEGILDIR: teslim edilmis ama ack'lenmemis mesajlar
                        # bu pencerede gorunmez. Karari sunucunun sayaclari
                        # verir — bkz. `_drenaj_bitti_mi`.
                        psub, kaynak = await _gecis_gerekiyorsa_yap(js, psub, kaynak)
                        continue
                    if not msgs:
                        _stats_update(backlog=0)
                        continue
                    _batch_started = _time.monotonic()
                    # DB isi ayri thread'de (senkron SQLAlchemy event loop'u
                    # bloke etmesin). _persist_batch TEK commit yapar.
                    ok_msgs, bad_msgs, ok_payloads, outbound_payloads = (
                        await asyncio.to_thread(_persist_batch, msgs)
                    )
                    # DB commit SONRASI ama NATS ack ONCESI yan etkiler. Process
                    # burada crash ederse mesaj redeliver olur; DB idempotency
                    # duplicate'i yutar ve dis akis tekrar denenir (veri kaybi yok).
                    for payload in ok_payloads:
                        try:
                            ws_broadcaster.broadcast(payload)
                        except Exception:  # noqa: BLE001
                            logger.debug("ws_broadcast_failed", exc_info=True)
                    if outbound_payloads:
                        await asyncio.to_thread(_dispatch_outbound, outbound_payloads)
                    # DB + yan etkiler tamam -> iyi/skip mesajlari ack.
                    for m in ok_msgs:
                        try:
                            await m.ack()
                        except Exception:  # noqa: BLE001
                            logger.debug("js_ack_failed", exc_info=True)
                    # Parse/validation hatali mesajlar: DLQ/nak.
                    for m in bad_msgs:
                        await _handle_bad(m)

                    # --- Olcum: yetisiyor muyuz? --------------------------
                    # Backlog'u BEDAVA aliyoruz: JetStream her mesajin
                    # metadata'sinda `num_pending` tasir (bu mesajdan SONRA
                    # tuketicinin onunde bekleyen adet). Ayrica consumer_info
                    # cagrisi yapmaya gerek yok. Son mesaji baz aliyoruz;
                    # batch'in en guncel noktasi orasi.
                    backlog: int | None = None
                    try:
                        meta = getattr(msgs[-1], "metadata", None)
                        if meta is not None and getattr(meta, "num_pending", None) is not None:
                            backlog = int(meta.num_pending)
                    except Exception:  # noqa: BLE001
                        backlog = None
                    _stats_record_batch(
                        size=len(ok_msgs),
                        duration=_time.monotonic() - _batch_started,
                        backlog=backlog,
                        bad=len(bad_msgs),
                    )
                    _warn_if_backlog_high(backlog)

                    # GECISIN ASIL TETIGI BURASI. Sahada telemetri kesintisiz
                    # aktigi icin fetch NEREDEYSE HIC timeout vermez (kismi
                    # batch de mesaj sayilir), yani yalnizca timeout'a bagli
                    # bir gecis kosulu uretimde HIC saglanmazdi. `backlog == 0`
                    # ise "bu batch'in ardinda bekleyen yok" demektir ve
                    # yukun tam ortasinda bile duzenli olarak gorulur.
                    # Kesin karar yine `_drenaj_bitti_mi` ile sunucudan alinir.
                    if backlog == 0:
                        psub, kaynak = await _gecis_gerekiyorsa_yap(js, psub, kaynak)
            except Exception as exc:  # noqa: BLE001
                if _stop_event.is_set():
                    break
                logger.warning(
                    "telemetry_consumer_reconnect error=%s backoff=%ds url=%s",
                    exc,
                    backoff,
                    settings.nats_url,
                )
                with _stats_lock:
                    _stats["connected"] = False
                    _stats["last_error"] = str(exc)[:300]
                    _stats["reconnects"] += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if nc is not None:
                    try:
                        await nc.drain()
                    except Exception:  # noqa: BLE001
                        logger.debug("js_drain_error", exc_info=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _stats_update(running=True, connected=False, last_error=None)
    _thread = threading.Thread(
        target=_consume_loop, name="telemetry-consumer-jetstream", daemon=True
    )
    _thread.start()


def stop() -> None:
    _stop_event.set()
