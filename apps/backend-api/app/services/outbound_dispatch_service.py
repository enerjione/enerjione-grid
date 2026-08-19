import json
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.outbound_target import OutboundTarget
from app.services.event_service import record_event
from app.services.iec104.server import manager as iec104_manager

logger = logging.getLogger(__name__)

MAX_RETRY = 3
BASE_BACKOFF_SECONDS = 0.7

#: TELEMETRI akisinda generic dispatcher'in SAHIBI OLMADIGI protokoller.
#:
#: Her biri KENDI sahibi tarafindan ASENKRON tasinir; burada senkron gonderim
#: yapmak ayni okumayi iki kez gondermek olurdu:
#:   rest   -> outbound_telemetry_batcher (5 sn pencere + dedup, kendi commit'i)
#:   mqtt   -> mqtt_publisher_service (hedef basina worker thread + buffer)
#:   modbus -> modbus-outbound koprusu (AYRI container; `e1.telemetry.normalized.>`
#:             konusunu NATS'tan kendi tuketir, plani `/internal/modbus-plans`
#:             ucundan alir). Backend'in telemetri yolunda hicbir rolu YOK.
#:
#: `iec104` bu kumede DEGIL: onu asagida INLINE ve BELLEK-ICI guncelliyoruz
#: (`_dispatch_iec104`) — ag I/O'su yok, bu yuzden ACK yolunda guvenli.
#:
#: NEDEN BIR KUME OLARAK YAZILDI: eskiden burada yalnizca `("rest", "mqtt")`
#: atlaniyordu ve `modbus` sessizce `_dispatch_with_retry`'a DUSUYORDU. O
#: fonksiyon modbus'u tanimadigi icin her payload'da istisna firlatiyor,
#: retry dongusune giriyor ve payload basina 0,7 + 1,4 = 2,1 saniye
#: `time.sleep` yapiyordu. Bu uyku telemetri yolunda DB COMMIT'i ile NATS
#: ACK'i ARASINDA calistigi icin 500'luk bir parti ~1.050 saniye ACK'siz
#: kaliyordu: `telemetry-persist-prio-v1` sahada 8.700+ mesaj birikimiyle
#: kilitlendi ve Postgres oturumu 17 dakika `idle in transaction` kaldi.
#: Yeni bir protokol eklendiginde bu listeye de eklenmeli.
TELEMETRY_NOT_DISPATCHER_OWNED = frozenset({"rest", "mqtt", "modbus"})

#: `_dispatch_with_retry`'in GERCEKTEN gonderebildigi protokoller. Bunun
#: disindaki her deger bir YAPILANDIRMA/SOZLESME hatasidir, ag hatasi DEGIL —
#: yeniden denemek anlamsizdir (bkz. `_dispatch_with_retry`).
RETRYABLE_PROTOCOLS = frozenset({"rest", "mqtt"})

#: Desteklenmeyen protokol uyarisi hedef basina bu araliktan sik yazilmaz.
#: Denetim kaydi bir ARIZA BILDIRIMIDIR, bir olcum akisi degil: alarm yolunda
#: saniyede onlarca event olabilir ve her biri icin satir yazmak
#: `system_events`'i (2 yil saklanir) doldururdu.
_UNSUPPORTED_WARN_INTERVAL_SEC = 300.0
_unsupported_lock = threading.Lock()
_unsupported_last_warn: dict[int, float] = {}

# UI 'Durum' sutunu icin in-memory delivery tracker.
# Restart sonrasi sifir — bilincli (calisan target'lar 1. event'te kendini gosterir).
_delivery_status: dict[int, dict[str, Any]] = {}
_delivery_lock = threading.Lock()


def _record_delivery(target_id: int, *, ok: bool, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _delivery_lock:
        cur = _delivery_status.setdefault(target_id, {
            "last_success_at": None,
            "last_failure_at": None,
            "last_error": None,
            "sent_total": 0,
            "failed_total": 0,
        })
        if ok:
            cur["last_success_at"] = now
            cur["sent_total"] += 1
        else:
            cur["last_failure_at"] = now
            cur["last_error"] = error
            cur["failed_total"] += 1


def delivery_status_snapshot() -> dict[int, dict[str, Any]]:
    """UI'ya `{target_id: {...}}` snapshot don."""
    with _delivery_lock:
        return {tid: dict(v) for tid, v in _delivery_status.items()}

try:
    import paho.mqtt.publish as mqtt_publish
except ImportError:  # pragma: no cover
    mqtt_publish = None


def dispatch_event(
    db: Session,
    *,
    event_kind: str,
    payload: dict,
    targets: list[OutboundTarget] | None = None,
) -> None:
    """Outbound dispatcher entry point.

    SAHIPLIK SOZLESMESI (event_kind x protocol)
    -------------------------------------------
    TELEMETRY — yuksek hacim (hedef olcek 6.810 msj/sn). Bu fonksiyon
    telemetri yolunda YALNIZCA bellek-ici is yapar; ag I/O'su ve bekleme
    YASAKTIR (cagiran `telemetry_consumer._dispatch_outbound` bunu DB
    commit'i ile NATS ACK'i ARASINDA calistirir):
        iec104 -> BU MODUL, inline + bellek-ici (`_dispatch_iec104`)
        rest   -> outbound_telemetry_batcher   (5 sn pencere, kendi commit'i)
        mqtt   -> mqtt_publisher_service       (hedef basina worker + buffer)
        modbus -> modbus-outbound koprusu      (AYRI container, NATS tuketicisi)
    Son ucu icin bkz. `TELEMETRY_NOT_DISPATCHER_OWNED` — orada ATLANIR.

    ALARM (ve telemetri disi diger kind'lar) — dusuk hacim, anlik teslim:
        rest   -> `_dispatch_with_retry` -> `_send_rest`   (senkron + retry)
        mqtt   -> `_dispatch_with_retry` -> `_send_mqtt`   (senkron + retry)
        iec104 -> no-op (`_dispatch_iec104` telemetri disini reddeder)
        modbus -> desteklenmiyor -> FAIL-FAST (retry/sleep YOK)

    DENETIM KAYDI COMMIT SAHIPLIGI
    ------------------------------
    `record_event(db, ...)` yalnizca `db.add(...)` yapar; COMMIT CAGIRANINDIR.
    Bu bilincli: alarm yazimi ile denetim kaydinin ayni transaction'da atomik
    kalmasi gerekir.
        * alarm yolu  -> `api/internal.py` sonunda `db.commit()` YAPAR      (OK)
        * batcher     -> `outbound_telemetry_batcher._flush_once` COMMIT eder (OK)
        * telemetri yolu -> `_dispatch_outbound` COMMIT ETMEZ. Bu yuzden bu
          modul telemetri yolunda cagiranin session'ina denetim kaydi YAZMAZ;
          ariza bildirimi gereken tek yer (`_record_unsupported_protocol`)
          KENDI kisa transaction'ini acar ve kendi commit'ini yapar.
    `record_event` icine global bir commit EKLENMEDI — cagiranlarin
    atomikligini bozardi.

    `targets` batch consumer optimizasyonu: 500 payload icin ayni aktif hedef
    sorgusunu 500 kez yapmak yerine caller bir kez ceker. Diger caller'lar
    None birakir; mevcut davranis degismez.
    """
    if targets is None:
        stmt = select(OutboundTarget).where(OutboundTarget.is_active.is_(True))
        targets = list(db.scalars(stmt).all())
    for target in targets:
        if target.event_filter not in {"all", event_kind}:
            continue
        # IEC 104: retry/backoff yok; server in-memory register gunceller,
        # sonraki interrogation ya da spontaneous transmission otomatik devreye girer.
        if target.protocol == "iec104":
            _dispatch_iec104(db=db, target=target, event_kind=event_kind, payload=payload)
            continue
        # Telemetry icin REST/MQTT/MODBUS = BASKA bir sahibin sorumlulugu.
        # Ayni okumayi iki kez gondermemek (ve ACK yolunu bloklamamak) icin
        # burada atla. Gerekce ve sahip listesi:
        # TELEMETRY_NOT_DISPATCHER_OWNED.
        if event_kind == "telemetry" and target.protocol in TELEMETRY_NOT_DISPATCHER_OWNED:
            continue
        _dispatch_with_retry(db=db, target=target, event_kind=event_kind, payload=payload)


def _record_unsupported_protocol(
    *, target: OutboundTarget, event_kind: str
) -> None:
    """Desteklenmeyen protokol icin BIR KEZ, KENDI transaction'inda kayit yaz.

    NEDEN AYRI SESSION: `record_event(db, ...)` yalnizca `db.add(...)` yapar —
    commit CAGIRANIN sorumlulugudur (bkz. modul sonundaki sahiplik notu).
    Telemetri yolundaki cagiran (`telemetry_consumer._dispatch_outbound`)
    session'i COMMIT ETMEDEN kapatiyor; oraya yazilan her denetim kaydi
    sessizce geri sariliyordu. Sahada olculdu: 12 gun boyunca telemetri
    yolundan TEK BIR `outbound_dead_letter` satiri kalmamisti.

    `record_event`'e global bir commit EKLENMEDI — o fonksiyon baska
    transaction'larin (alarm yazimi gibi) parcasi ve oradaki atomikligi
    bozmak, tam da onlemeye calistigimiz turden sessiz bir hata uretirdi.
    Bunun yerine ariza bildirimi kendi KISA ve SINIRLI transaction'ini
    yonetiyor; cagiranin transaction'ina hic dokunmuyor.

    Rate-limit hedef basinadir: bu bir ariza bildirimidir, olcum akisi degil.
    """
    now = time.monotonic()
    with _unsupported_lock:
        onceki = _unsupported_last_warn.get(target.id)
        if onceki is not None and now - onceki < _UNSUPPORTED_WARN_INTERVAL_SEC:
            return
        _unsupported_last_warn[target.id] = now

    logger.error(
        "outbound_unsupported_protocol target=%s protocol=%s event_kind=%s — "
        "hedef teslim EDILEMIYOR; yeniden deneme YAPILMIYOR (yapilandirma hatasi, "
        "ag hatasi degil). Hedefi pasiflestirin ya da destekli bir protokole alin.",
        target.name,
        target.protocol,
        event_kind,
    )
    # Kendi kisa omurlu session'i — cagiranin transaction'indan bagimsiz.
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        record_event(
            db,
            category="outbound",
            event_type="outbound_unsupported_protocol",
            severity="error",
            message=(
                f"Hedef {target.name} icin '{target.protocol}' protokolu "
                f"desteklenmiyor; teslimat yapilamiyor"
            ),
            metadata={
                "target": target.name,
                "target_id": target.id,
                "protocol": target.protocol,
                "event_kind": event_kind,
            },
            i18n_key="outbound_unsupported_protocol",
            i18n_params={"target": target.name, "protocol": target.protocol},
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.debug("outbound_unsupported_protocol_event_failed", exc_info=True)
    finally:
        db.close()


def _dispatch_with_retry(db: Session, *, target: OutboundTarget, event_kind: str, payload: dict) -> None:
    # DESTEKLENMEYEN PROTOKOL: YENIDEN DENEME YOK — FAIL-FAST.
    #
    # Bu kontrol dongunun ICINDE degil ONUNDE olmak zorunda. Eskiden boyle
    # bir kontrol YOKTU: `_send_rest`/`_send_mqtt` disindaki her protokol
    # dongunun icinde `ValueError` firlatiyor ve retry/backoff'a giriyordu.
    # Sonuc, payload BASINA 0,7 + 1,4 = 2,1 saniye `time.sleep` idi.
    #
    # Yeniden deneme AG hatasi icindir: bir sonraki denemede duzelme SANSI
    # oldugu icin anlamlidir. Desteklenmeyen bir protokol bir YAPILANDIRMA/
    # SOZLESME hatasidir; ikinci denemede de desteklenmeyecektir. Beklemek
    # yalnizca cagiran akisi geciktirir — telemetri yolunda bu gecikme
    # dogrudan NATS ACK'ini erteliyor ve tuketiciyi kilitliyordu.
    if target.protocol not in RETRYABLE_PROTOCOLS:
        _record_unsupported_protocol(target=target, event_kind=event_kind)
        _record_delivery(
            target.id, ok=False, error=f"unsupported protocol: {target.protocol}"
        )
        return

    last_error: Exception | None = None
    event_id = payload.get("message_id") or payload.get("event_id") or "unknown"
    correlation_id = payload.get("correlation_id") or event_id
    for attempt in range(1, MAX_RETRY + 1):
        try:
            if target.protocol == "rest":
                _send_rest(target, payload)
            elif target.protocol == "mqtt":
                _send_mqtt(target, payload)
            else:
                raise ValueError(f"Unsupported outbound protocol: {target.protocol}")
            record_event(
                db,
                category="outbound",
                event_type="outbound_delivered",
                severity="info",
                message=f"Event {event_kind} sent to target {target.name}",
                metadata={
                    "target": target.name,
                    "protocol": target.protocol,
                    "attempt": attempt,
                    "event_id": event_id,
                    "correlation_id": correlation_id,
                },
                i18n_key="outbound_delivered",
                i18n_params={"event_kind": event_kind, "target": target.name},
            )
            _record_delivery(target.id, ok=True)
            return
        except Exception as ex:
            last_error = ex
            if attempt < MAX_RETRY:
                wait_seconds = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                record_event(
                    db,
                    category="outbound",
                    event_type="outbound_retry_scheduled",
                    severity="warning",
                    message=f"Retry scheduled for {target.name} (attempt {attempt}/{MAX_RETRY})",
                    metadata={
                        "target": target.name,
                        "protocol": target.protocol,
                        "attempt": attempt,
                        "backoff_seconds": wait_seconds,
                        "error": str(ex),
                        "event_id": event_id,
                        "correlation_id": correlation_id,
                    },
                    i18n_key="outbound_retry_scheduled",
                    i18n_params={"target": target.name, "attempt": attempt, "max": MAX_RETRY},
                )
                time.sleep(wait_seconds)

    record_event(
        db,
        category="outbound",
        event_type="outbound_dead_letter",
        severity="error",
        message=f"Delivery to {target.name} moved to dead-letter queue",
        metadata={
            "target": target.name,
            "protocol": target.protocol,
            "max_retry": MAX_RETRY,
            "error": str(last_error) if last_error else "unknown",
            "event_id": event_id,
            "correlation_id": correlation_id,
            "payload": payload,
        },
        i18n_key="outbound_dead_letter",
        i18n_params={"target": target.name},
    )
    _record_delivery(target.id, ok=False, error=str(last_error) if last_error else "unknown")


def _send_rest(target: OutboundTarget, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if target.auth_header and target.auth_token:
        headers[target.auth_header] = target.auth_token
    req = urllib.request.Request(target.endpoint, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=8):
        pass


def _send_mqtt(target: OutboundTarget, payload: dict) -> None:
    if mqtt_publish is None:
        raise RuntimeError("MQTT publish için paho-mqtt kurulu değil.")
    if not target.topic:
        raise ValueError("MQTT target için topic zorunludur.")
    mqtt_publish.single(
        target.topic,
        payload=json.dumps(payload, ensure_ascii=False),
        hostname=target.endpoint,
        qos=target.qos,
        retain=target.retain,
    )


def _dispatch_iec104(db: Session, *, target: OutboundTarget, event_kind: str, payload: dict) -> None:
    """IEC 104 server'in in-memory nokta tablosunu gunceller.

    Sadece `telemetry` kind'indaki event'ler gecerlidir (alarm'lar tag degeri
    degildir). Server FastAPI startup'ta deploy edilmis olmali; degilse sessizce
    dusturuluruz (degerli retry olmaz — bir sonraki interrogation'da SCADA zaten
    tekrar ister).
    """
    if event_kind != "telemetry":
        return
    device_code = payload.get("device_code")
    signal_key = payload.get("signal_key")
    if not device_code or not signal_key:
        return
    value = payload.get("value")
    quality = str(payload.get("quality", "good")).lower()
    good = quality in ("good", "ok", "")
    iec104_manager.update_point_threadsafe(
        device_code=str(device_code),
        signal_key=str(signal_key),
        value=value,
        good=good,
    )
    # NOKTA BASINA DENETIM KAYDI YOK — bilerek.
    #
    # Burada eskiden her telemetri okumasi icin `record_event(...)` cagriliyordu
    # ("olay akisi bos kalmasin" gerekcesiyle). Iki ayri sorun vardi:
    #
    # 1. KAYIT HIC OLUSMUYORDU. Cagiran taraf (`telemetry_consumer._dispatch_outbound`)
    #    session'i COMMIT ETMEDEN kapatiyor; dolayisiyla saniyede yuzlerce ORM
    #    nesnesi kuruluyor, session'da birikiyor ve cope atiliyordu. Sahada
    #    olculdu: IEC 104 hedefi aktifken `system_events` icinde TEK BIR
    #    `iec104_point_updated` satiri yoktu.
    #
    # 2. COMMIT EDILSEYDI DAHA KOTU OLURDU. `system_events` denetim kaydi ve
    #    2 YIL saklaniyor. 15 cihazlik test kurulumunda bile 375 okuma/sn
    #    demek gunde 32 milyon denetim satiri demekti; tablo denetim amacini
    #    tamamen kaybederdi (gercek operator olaylari bu gurultunun icinde
    #    kaybolurdu).
    #
    # Nokta guncellemesi zaten IZLENEBILIR: IEC 104 server'in kendi sayaclari
    # ve `/health` ciktisi var. Denetim kaydi OPERATOR EYLEMLERI icin.
