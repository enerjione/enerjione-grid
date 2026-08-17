"""Bilinmeyen cihaz telemetrisinin dayanikli karantinasi.

SOZLESME — BU MODULUN VAR OLMA SEBEBI
--------------------------------------
    BILINMEYEN CIHAZ + KARANTINA YAZILAMADI = ASLA ACK

Eski davranis bilinmeyen cihazda payload'i atip mesaji ack ediyordu. Burada
payload once DB'ye yazilir; ack karari cagiranin (telemetry_consumer)
commit'ine baglidir. Yazim patlarsa istisna YUKARI FIRLAR, cagiran hicbir
mesaji ack etmez ve JetStream yeniden teslim eder.

Bu modul cihaz/profil/sinyal URETMEZ. Yalnizca veri dayanikliligi saglar.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, text

from app.core.config import settings
from app.models.unknown_device_telemetry import UnknownDeviceTelemetry

logger = logging.getLogger(__name__)

REASON_DEVICE_NOT_FOUND = "device_not_found"
STATUS_PENDING = "pending"
STATUS_REPLAYED = "replayed"


class QuarantineCapacityError(RuntimeError):
    """Karantina kapasitesi dolu — mesaj ack EDILMEMELI.

    Cagiran bunu yakalayip mesajlari ack'siz birakir; JetStream yeniden
    teslim eder. Sessizce atmak bu modulun amacinin tam tersi olurdu.
    """


@dataclass(frozen=True)
class QuarantineEntry:
    """Karantinaya yazilacak tek olcum."""

    consumer_name: str
    dedup_key: str
    message_id: str
    device_code: str
    payload_json: str
    gateway_code: str | None = None
    subject: str | None = None
    stream_sequence: int | None = None
    signal_key: str | None = None
    source_timestamp: datetime | None = None
    reason: str = REASON_DEVICE_NOT_FOUND


# --------------------------------------------------------------------------
# Sayaclar — surec omru boyunca. Health/metrik uclari bunlari okur.
# --------------------------------------------------------------------------
_stats_lock = threading.Lock()
_stats: dict[str, int] = {
    "unknown_device_quarantine_total": 0,
    # SILME UC AYRI ANLAM TASIR — tek sayacta toplanmamalari BILINCLI:
    #   replayed_cleanup : isi bitmis kayit, kayip yok
    #   expired          : cozulmemis ama retention suresini asmis (politika)
    #   data_shed        : suresi DOLMADAN, tavani korumak icin silindi (ACIL)
    # Operator icin "shed > 0" ile "expired > 0" tamamen farkli iki durumdur.
    "unknown_device_quarantine_replayed_cleanup_total": 0,
    "unknown_device_quarantine_expired_total": 0,
    "unknown_device_quarantine_data_shed_total": 0,
    # ARTIK OLAGANDISI: yer acma devreye girdigi icin normal kapasite
    # baskisinda ARTMAZ. Yalnizca yer acilamadiginda (parti tavandan buyuk,
    # ya da silinecek pending kalmamis) artar — yani yapilandirma hatasi
    # gostergesidir. Geriye uyum icin adi korundu.
    "unknown_device_quarantine_capacity_rejected_total": 0,
    "unknown_device_replay_success_total": 0,
    "unknown_device_replay_failed_total": 0,
}

# Cihaz kodu -> son log/olay zamani (monotonic). 1 Hz telemetride her mesaj
# icin log basmak diski, her mesaj icin olay uretmek olay tablosunu
# doldururdu.
_son_bildirim: dict[str, float] = {}
_bildirim_lock = threading.Lock()

# Acil veri dusurme olayinin hiz siniri icin ayrilmis anahtar. Cihaz kodu
# olamayacagi icin ("/" gecersiz) gercek bir kodla CAKISMAZ.
_SHED_OLAY_ANAHTARI = "/data-shed"

# Kapasite sayimi onbellegi: (zaman, sayi).
_sayim_onbellek: tuple[float, int] | None = None
_sayim_lock = threading.Lock()


def _stat_arttir(ad: str, adet: int = 1) -> None:
    with _stats_lock:
        _stats[ad] = _stats.get(ad, 0) + adet


def get_stats() -> dict[str, int]:
    with _stats_lock:
        return dict(_stats)


def reset_stats_for_test() -> None:
    """Yalnizca testler icin — surec sayaclarini sifirlar."""
    with _stats_lock:
        for k in _stats:
            _stats[k] = 0
    with _bildirim_lock:
        _son_bildirim.clear()
    _sayim_onbellegi_bosalt()


def _sayim_onbellegi_bosalt() -> None:
    global _sayim_onbellek
    with _sayim_lock:
        _sayim_onbellek = None


# --------------------------------------------------------------------------
# Tekillestirme anahtari
# --------------------------------------------------------------------------
def dedup_key_for(msg: Any, payload: dict, *, payload_had_message_id: bool) -> str:
    """Yeniden teslimde DEGISMEYEN bir anahtar uretir.

    `message_id` payload'da GERCEKTEN varsa o kullanilir.

    Yoksa consumer her teslimde YENI bir uuid4 uretiyor (bkz.
    `telemetry_consumer._persist_batch` parse adimi), yani o deger yeniden
    teslimde degisir ve dedup anahtari olarak ISE YARAMAZ — ayni fiziksel
    mesaj her teslimde ikinci bir karantina satiri acardi. O durumda broker
    kimligi kullanilir: JetStream `stream_sequence` yeniden teslimde SABIT
    kalir.

    Ikisi de yoksa (metadata okunamayan test/sahte mesaj) uretilmis
    message_id'ye duseriz; tekillestirme garantisi o mesaj icin kaybolur ama
    payload yine de KAYBOLMAZ. Sessiz veri kaybi ile eksik dedup arasinda
    tercih, dedup aleyhinedir.
    """
    if payload_had_message_id:
        mid = str(payload.get("message_id") or "").strip()
        if mid:
            return mid[:200]

    stream, sequence = _broker_kimligi(msg)
    if sequence is not None:
        return f"js:{stream or '?'}:{sequence}"[:200]

    return str(payload.get("message_id") or "")[:200]


def _broker_kimligi(msg: Any) -> tuple[str | None, int | None]:
    """(stream, stream_sequence) — okunamiyorsa (None, None). ASLA patlamaz."""
    try:
        meta = getattr(msg, "metadata", None)
        if meta is None:
            return None, None
        stream = getattr(meta, "stream", None)
        dizi = getattr(meta, "sequence", None)
        seq = getattr(dizi, "stream", None) if dizi is not None else None
        return (str(stream) if stream else None), (int(seq) if seq is not None else None)
    except Exception:  # noqa: BLE001
        return None, None


def entry_from_message(
    msg: Any,
    payload: dict,
    *,
    consumer_name: str,
    message_id: str,
    device_code: str,
    payload_had_message_id: bool,
) -> QuarantineEntry:
    """Ham mesaj + payload'dan karantina satiri kurar."""
    stream, sequence = _broker_kimligi(msg)
    return QuarantineEntry(
        consumer_name=consumer_name,
        dedup_key=dedup_key_for(
            msg, payload, payload_had_message_id=payload_had_message_id
        ),
        message_id=message_id[:120],
        device_code=device_code[:50],
        # Payload AYNEN saklanir: replay onu `TelemetryIn`e geri cozecek.
        # Burada normalize etmek, replay ile canli yolun farkli girdi
        # gormesi demekti.
        payload_json=json.dumps(payload, default=str),
        gateway_code=(str(payload.get("source_gateway"))[:50] if payload.get("source_gateway") else None),
        subject=(str(getattr(msg, "subject", "") or "")[:255] or None),
        stream_sequence=sequence,
        signal_key=(str(payload.get("signal_key"))[:120] if payload.get("signal_key") else None),
        source_timestamp=_zaman_coz(payload.get("source_timestamp")),
    )


def _zaman_coz(deger: Any) -> datetime | None:
    if deger is None:
        return None
    if isinstance(deger, datetime):
        return deger
    try:
        return datetime.fromisoformat(str(deger).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Kapasite
# --------------------------------------------------------------------------
def pending_count(db) -> int:  # noqa: ANN001
    return int(
        db.scalar(
            select(func.count())
            .select_from(UnknownDeviceTelemetry)
            .where(UnknownDeviceTelemetry.status == STATUS_PENDING)
        )
        or 0
    )


def _toplam_satir(db) -> int:  # noqa: ANN001
    """Kapasite karari icin satir sayisi — kisa sureli onbellekli.

    Her bilinmeyen mesajda COUNT(*) kosmak, bilinmeyen kod uretimi hizlandigi
    anda (yani tam da kapasitenin onemli oldugu anda) DB'yi doyururdu.
    """
    global _sayim_onbellek
    simdi = time.monotonic()
    ttl = max(0, int(settings.unknown_telemetry_count_cache_sec))
    with _sayim_lock:
        if _sayim_onbellek is not None and (simdi - _sayim_onbellek[0]) < ttl:
            return _sayim_onbellek[1]
    sayi = int(
        db.scalar(select(func.count()).select_from(UnknownDeviceTelemetry)) or 0
    )
    with _sayim_lock:
        _sayim_onbellek = (simdi, sayi)
    return sayi


def _taze_sayim(db) -> int:  # noqa: ANN001
    """Onbellegi ATLAYAN satir sayisi. Reclaim kararlari bunu kullanir."""
    return int(
        db.scalar(select(func.count()).select_from(UnknownDeviceTelemetry)) or 0
    )


# Reclaim yolunu seri hale getiren transaction-kapsamli advisory kilit.
#
# NEDEN KILIT GEREKLI (kaba global kilit DEGIL):
# Iki worker ayni anda tavana carparsa ikisi de "rows=tavan, N satir eksigim
# var" hesabini yapar ve ikisi de EN ESKI N satiri siler -> 2N satir gider.
# Yani kilitsiz davranis FAZLADAN VERI KAYBI uretir. Kilit YALNIZCA
# bilinmeyen dalinda VE yalnizca tavana carpildiginda alinir; bilinen cihaz
# hizli yolu bu ifadeyi HIC gormez. Transaction-kapsamli oldugu icin
# commit/rollback ile kendiliginden birakilir — sizdirilacak bir kilit yok.
_RECLAIM_LOCK_KEY = 0x0E1_0071


def _reclaim_kilidi(db) -> None:  # noqa: ANN001
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect != "postgresql":
        # SQLite (birim testleri): tek surec, es zamanli ikinci worker yok.
        return
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _RECLAIM_LOCK_KEY})


@dataclass
class ReclaimOutcome:
    """Kapasite icin ne silindi — UC KATEGORI AYRI TUTULUR.

    `replayed_cleanup` : isi bitmis, retention suresi dolmus kayit.
    `expired_pending`  : cozulmemis ama retention suresini ASMIS kayit.
    `data_shed`        : retention suresi DOLMADAN, yalnizca tavani korumak
                         icin silinen kayit. ACIL DURUM verisi kaybidir ve
                         digerleriyle ayni kefeye konulmamalidir.
    """

    replayed_cleanup: int = 0
    expired_pending: int = 0
    data_shed: int = 0
    rows_before: int = 0
    rows_after: int = 0
    hard_limit: int = 0

    @property
    def deleted(self) -> int:
        return self.replayed_cleanup + self.expired_pending + self.data_shed


def _en_eskileri_sil(db, kosul, adet: int) -> int:  # noqa: ANN001
    """`kosul`a uyan EN ESKI `adet` satiri siler. Commit ETMEZ."""
    if adet <= 0:
        return 0
    idler = list(
        db.scalars(
            select(UnknownDeviceTelemetry.id)
            .where(kosul)
            .order_by(UnknownDeviceTelemetry.first_seen_at.asc(),
                      UnknownDeviceTelemetry.id.asc())
            .limit(adet)
        ).all()
    )
    if not idler:
        return 0
    db.execute(
        delete(UnknownDeviceTelemetry).where(UnknownDeviceTelemetry.id.in_(idler))
    )
    return len(idler)


def ensure_capacity(db, eklenecek: int) -> ReclaimOutcome:  # noqa: ANN001
    """Tavan doluysa KONTROLLU yer acar. Cagiranin transaction'inda calisir.

    NEDEN "ACK ETME, REDELIVERY BEKLE" TERK EDILDI
    ----------------------------------------------
    Onceki davranis tavan dolunca mesaji ack etmiyor ve JetStream'in yeniden
    teslimine guveniyordu. Ama consumer `max_deliver` ile kosuyor: ack
    edilmemek mesajin SONSUZA KADAR guvende oldugu anlamina GELMEZ. Ustelik
    kapasite baskisi surerken surekli yeniden teslim retry firtinasi,
    consumer baskisi, backlog buyumesi ve stream retention baskisi uretir —
    yani sonunda ayni veri kaybini, ustune bir de bozulmus bir boru hattiyla
    birlikte getirirdi.

    Simdi yer ACIYORUZ, sirayla:
      1. retention suresi dolmus REPLAYED kayitlar (isi bitmis)
      2. retention suresi dolmus PENDING kayitlar (politika geregi silinebilir)
      3. hala yer yoksa ACIL VERI DUSURME: en eski pending kayitlardan
         YALNIZCA gereken kadar

    3. adim gercek bir veri kaybidir ve SESSIZ DEGILDIR: ayri sayac, ayri
    log ve operasyonel olay uretir.

    Commit YAPMAZ — cagiranin tek commit'ine katilir. Boylece "eski satirlar
    silindi ama yeni satir yazilamadi" araligi OLUSAMAZ (bkz. quarantine_batch).
    """
    tavan = int(settings.unknown_telemetry_max_rows)
    out = ReclaimOutcome(hard_limit=tavan)
    if tavan <= 0:  # 0/negatif = sinir kapali
        return out

    # Hizli yol: onbellekli sayim yeterliyse kilit bile alinmaz.
    if _toplam_satir(db) + eklenecek <= tavan:
        return out

    if eklenecek > tavan:
        # YAPILANDIRMA HATASI: tek parti tavanin tamamindan buyuk. Yer acmak
        # bile yetmez; burada silmeye devam etmek tabloyu bosaltip yine
        # basarisiz olmak olurdu.
        _stat_arttir("unknown_device_quarantine_capacity_rejected_total", eklenecek)
        logger.error(
            "unknown_device_quarantine_batch_exceeds_limit parti=%d tavan=%d — "
            "UNKNOWN_TELEMETRY_MAX_ROWS parti boyutundan buyuk olmali",
            eklenecek,
            tavan,
        )
        raise QuarantineCapacityError(
            f"parti ({eklenecek}) kapasite tavanindan ({tavan}) buyuk"
        )

    _reclaim_kilidi(db)

    # Kilidi bekledik; bu arada baska bir worker yer acmis olabilir.
    mevcut = _taze_sayim(db)
    out.rows_before = mevcut
    if mevcut + eklenecek <= tavan:
        out.rows_after = mevcut
        _sayim_onbellegi_bosalt()
        return out

    gereken = mevcut + eklenecek - tavan
    replayed_kesme, pending_kesme = retention_cutoffs()

    # 1) Suresi dolmus REPLAYED — isi bitmis kayit, kaybi yok.
    if replayed_kesme is not None and gereken > 0:
        n = _en_eskileri_sil(
            db,
            (UnknownDeviceTelemetry.status == STATUS_REPLAYED)
            & (UnknownDeviceTelemetry.replayed_at.is_not(None))
            & (UnknownDeviceTelemetry.replayed_at < replayed_kesme),
            gereken,
        )
        out.replayed_cleanup = n
        gereken -= n

    # 2) Suresi dolmus PENDING — retention politikasinin acik sonucu.
    if pending_kesme is not None and gereken > 0:
        n = _en_eskileri_sil(
            db,
            (UnknownDeviceTelemetry.status == STATUS_PENDING)
            & (UnknownDeviceTelemetry.first_seen_at < pending_kesme),
            gereken,
        )
        out.expired_pending = n
        gereken -= n

    # 3) ACIL VERI DUSURME — suresi DOLMAMIS en eski pending kayitlar.
    if gereken > 0:
        n = _en_eskileri_sil(
            db, UnknownDeviceTelemetry.status == STATUS_PENDING, gereken
        )
        out.data_shed = n
        gereken -= n

    out.rows_after = _taze_sayim(db)
    _sayim_onbellegi_bosalt()

    _stat_arttir("unknown_device_quarantine_replayed_cleanup_total", out.replayed_cleanup)
    _stat_arttir("unknown_device_quarantine_expired_total", out.expired_pending)
    _stat_arttir("unknown_device_quarantine_data_shed_total", out.data_shed)

    if gereken > 0:
        # Silinecek pending kalmadi (tablo replayed-ama-taze kayitlarla dolu).
        # Yer acilamadi -> ack YOK. Bu artik gercekten olagandisi bir durum.
        _stat_arttir("unknown_device_quarantine_capacity_rejected_total", eklenecek)
        logger.error(
            "unknown_device_quarantine_reclaim_insufficient rows=%d tavan=%d eksik=%d",
            out.rows_after,
            tavan,
            gereken,
        )
        raise QuarantineCapacityError(
            f"kapasite acilamadi ({out.rows_after}/{tavan}, eksik {gereken})"
        )

    return out


# --------------------------------------------------------------------------
# Yazim
# --------------------------------------------------------------------------
def quarantine_batch(db, entries: list[QuarantineEntry]) -> int:  # noqa: ANN001
    """Karantina satirlarini TEK ifadeyle yazar. Hata YUKARI FIRLAR.

    Commit YAPILMAZ: cagiranin transaction'ina katilir ki "karantina yazildi
    ama commit edilmedi" araligi olusmasin. Ack karari cagiranin commit'ine
    baglidir.

    Ayni `dedup_key` yeniden gelirse (redelivery ya da es zamanli ikinci
    consumer) YENI SATIR ACILMAZ: `seen_count` artar, `last_seen_at`
    guncellenir. Bu hem idempotency hem yaris korumasidir ve tek atomik
    ifadeyle saglanir — "once SELECT sonra INSERT" yaris penceresi birakirdi.

    KAPASITE ATOMIKLIGI: yer acma (`ensure_capacity`) ve asagidaki upsert
    AYNI transaction'dadir ve ikisi de cagiranin TEK commit'ine baglidir.
    Ayri commit edilselerdi "eski kayitlar silindi, yeni satir yazilamadi,
    mesaj ack edilmedi" durumu olusur ve net sonuc SAF VERI KAYBI olurdu.
    """
    if not entries:
        return 0

    reclaim = ensure_capacity(db, len(_tekillestir(entries)))

    simdi = datetime.now(timezone.utc)
    satirlar = [
        {
            "consumer_name": e.consumer_name,
            "dedup_key": e.dedup_key,
            "message_id": e.message_id,
            "gateway_code": e.gateway_code,
            "device_code": e.device_code,
            "subject": e.subject,
            "stream_sequence": e.stream_sequence,
            "payload_json": e.payload_json,
            "signal_key": e.signal_key,
            "source_timestamp": e.source_timestamp,
            "reason": e.reason,
            "status": STATUS_PENDING,
            "seen_count": 1,
            "first_seen_at": simdi,
            "last_seen_at": simdi,
            "replay_attempts": 0,
            "created_at": simdi,
            "updated_at": simdi,
        }
        for e in _tekillestir(entries)
    ]

    db.execute(_upsert_stmt(db, satirlar))
    _sayim_onbellegi_bosalt()
    _stat_arttir("unknown_device_quarantine_total", len(satirlar))
    # Olay yazimi upsert'ten SONRA ve AYNI transaction'da: silme geri
    # sarilirsa onu duyuran olay da geri sarilir.
    if reclaim.data_shed:
        _shed_olayi(db, reclaim)
    return len(satirlar)


def _shed_olayi(db, reclaim: "ReclaimOutcome") -> None:  # noqa: ANN001
    """Acil veri dusurmeyi TEK, TOPLU bir olayla duyurur.

    SATIR BASINA OLAY YOK: 100 kayit dusuruldugunde 100 olay uretmek olay
    tablosunu doldurur ve operatorun gercek olaylari gormesini engellerdi.
    Bir reclaim = bir olay, ustune de kisa sureli hiz siniri.

    Olay metadata'si YALNIZCA sayilari tasir; payload/kod/sir ICERMEZ.
    """
    if not _shed_olayi_uretilebilir():
        logger.error(
            "unknown_device_quarantine_data_shed deleted=%d rows_before=%d "
            "rows_after=%d hard_limit=%d (olay hiz siniri nedeniyle yazilmadi)",
            reclaim.data_shed, reclaim.rows_before, reclaim.rows_after,
            reclaim.hard_limit,
        )
        return
    logger.error(
        "unknown_device_quarantine_data_shed deleted=%d rows_before=%d "
        "rows_after=%d hard_limit=%d — SURESI DOLMAMIS bilinmeyen cihaz "
        "telemetrisi kapasite icin silindi",
        reclaim.data_shed, reclaim.rows_before, reclaim.rows_after,
        reclaim.hard_limit,
    )
    try:
        from app.services.event_service import record_event

        record_event(
            db,
            category="telemetry",
            event_type="unknown_device_quarantine_data_shed",
            severity="error",
            message=(
                f"Karantina kapasitesi doldu: {reclaim.data_shed} adet suresi "
                "dolmamis bilinmeyen cihaz telemetrisi silindi"
            ),
            metadata={
                "deleted_count": reclaim.data_shed,
                "rows_before": reclaim.rows_before,
                "rows_after": reclaim.rows_after,
                "hard_limit": reclaim.hard_limit,
                "replayed_cleanup": reclaim.replayed_cleanup,
                "expired_pending": reclaim.expired_pending,
            },
            i18n_key="unknown_device_quarantine_data_shed",
            i18n_params={"count": reclaim.data_shed},
        )
    except Exception:  # noqa: BLE001
        logger.debug("unknown_device_shed_event_failed", exc_info=True)


def _shed_olayi_uretilebilir() -> bool:
    aralik = max(0, int(settings.unknown_telemetry_shed_event_interval_sec))
    simdi = time.monotonic()
    with _bildirim_lock:
        son = _son_bildirim.get(_SHED_OLAY_ANAHTARI)
        if son is not None and (simdi - son) < aralik:
            return False
        _son_bildirim[_SHED_OLAY_ANAHTARI] = simdi
        return True


def _tekillestir(entries: list[QuarantineEntry]) -> list[QuarantineEntry]:
    """AYNI batch icinde tekrarlanan dedup_key'i eler.

    Tek `INSERT ... ON CONFLICT DO UPDATE` ayni satiri IKI KEZ guncelleyemez
    ("cannot affect row a second time"); ayni koruma `_tek_gecis_yaz`
    icindeki canli satir tekillestirmesinde de var.
    """
    gorulen: dict[tuple[str, str], QuarantineEntry] = {}
    for e in entries:
        gorulen.setdefault((e.consumer_name, e.dedup_key), e)
    return list(gorulen.values())


def _upsert_stmt(db, satirlar: list[dict]):  # noqa: ANN001
    """Dialect'e gore `ON CONFLICT DO UPDATE` ifadesi kurar."""
    dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:
        from sqlalchemy.dialects.postgresql import insert as _insert

    ins = _insert(UnknownDeviceTelemetry).values(satirlar)
    return ins.on_conflict_do_update(
        index_elements=["consumer_name", "dedup_key"],
        set_={
            "seen_count": UnknownDeviceTelemetry.__table__.c.seen_count + 1,
            "last_seen_at": ins.excluded.last_seen_at,
            "updated_at": ins.excluded.updated_at,
        },
    )


# --------------------------------------------------------------------------
# Bildirim (log + olay) — cihaz basina hiz sinirli
# --------------------------------------------------------------------------
def should_notify(device_code: str) -> bool:
    """Bu cihaz kodu icin simdi log/olay uretilmeli mi?

    1 Hz telemetride her mesaj icin uyari basmak gunde ~86.000 satir log ve
    ayni sayida olay demekti; operator gercek olaylari goremezdi.
    """
    aralik = max(0, int(settings.unknown_telemetry_log_interval_sec))
    simdi = time.monotonic()
    with _bildirim_lock:
        son = _son_bildirim.get(device_code)
        if son is not None and (simdi - son) < aralik:
            return False
        _son_bildirim[device_code] = simdi
        return True


def notify(db, device_code: str, gateway_code: str | None) -> None:  # noqa: ANN001
    """Hiz sinirli uyari logu + operasyonel olay.

    Olay yazimi telemetri akisini BOZMAZ: hata yalnizca loglanir. Karantina
    satiri zaten yazildi; olay uretilememesi payload'i riske atmaz.
    """
    if not should_notify(device_code):
        return
    logger.warning(
        "unknown_device_telemetry_quarantined device_code=%s gateway=%s — "
        "payload karantinada tutuluyor; cihaz tanimlandiktan sonra replay edilebilir",
        device_code,
        gateway_code or "?",
    )
    try:
        from app.services.event_service import record_event

        record_event(
            db,
            category="telemetry",
            event_type="unknown_device_telemetry",
            severity="warning",
            device_code=device_code,
            message=(
                f"Tanimsiz cihaz telemetrisi karantinaya alindi: {device_code}"
                + (f" (gateway {gateway_code})" if gateway_code else "")
            ),
            metadata={"device_code": device_code, "gateway_code": gateway_code},
            i18n_key="unknown_device_telemetry",
            i18n_params={"code": device_code},
        )
    except Exception:  # noqa: BLE001
        logger.debug("unknown_device_event_failed", exc_info=True)


# --------------------------------------------------------------------------
# Gozlemlenebilirlik
# --------------------------------------------------------------------------
def health_snapshot(db) -> dict[str, Any]:  # noqa: ANN001
    """Karantinanin anlik durumu — health/metrik uclari icin."""
    bekleyen = pending_count(db)
    en_eski = db.scalar(
        select(func.min(UnknownDeviceTelemetry.first_seen_at)).where(
            UnknownDeviceTelemetry.status == STATUS_PENDING
        )
    )
    yas = None
    if en_eski is not None:
        if en_eski.tzinfo is None:
            en_eski = en_eski.replace(tzinfo=timezone.utc)
        yas = max(0.0, (datetime.now(timezone.utc) - en_eski).total_seconds())
    tavan = int(settings.unknown_telemetry_max_rows)
    toplam = int(
        db.scalar(select(func.count()).select_from(UnknownDeviceTelemetry)) or 0
    )
    anlik = get_stats()
    anlik.update(
        {
            "unknown_device_quarantine_pending": bekleyen,
            "unknown_device_quarantine_rows": toplam,
            "unknown_device_quarantine_max_rows": tavan,
            "unknown_device_quarantine_capacity_full": bool(tavan > 0 and toplam >= tavan),
            "oldest_pending_age_sec": yas,
        }
    )
    return anlik


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------
def retention_cutoffs(now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    """(replayed_kesme, pending_kesme) — politikanin TEK KAYNAGI.

    Hem deterministik test yolu (`purge`) hem uretimdeki sayfali silme
    (`telemetry_retention.purge_unknown_quarantine`) bunu okur. Iki yerde
    ayri esik hesabi, birinin sessizce otekinden ayrilmasi demekti.

    `None` = o kategori icin retention KAPALI (gun <= 0).
    """
    simdi = now or datetime.now(timezone.utc)
    replayed_gun = int(settings.unknown_telemetry_replayed_retention_days)
    pending_gun = int(settings.unknown_telemetry_pending_retention_days)
    return (
        (simdi - timedelta(days=replayed_gun)) if replayed_gun > 0 else None,
        (simdi - timedelta(days=pending_gun)) if pending_gun > 0 else None,
    )


def purge(db, *, now: datetime | None = None) -> dict[str, int]:  # noqa: ANN001
    """Suresi dolan karantina kayitlarini siler. Commit ETMEZ.

    Iki AYRI politika:
      * `replayed` — is bitti, kayit yalnizca kanit. Kisa sure tutulur.
      * `pending`  — payload HALA kurtarilabilir. Uzun tutulur; silmek veri
        kaybidir, bu yuzden esik acikca daha genistir.

    `now` testler icin disaridan verilebilir: retention deterministik
    olmali, "gercek saate gore bekle" bir test stratejisi degildir.
    """
    silinen = {"replayed": 0, "pending": 0}
    replayed_kesme, pending_kesme = retention_cutoffs(now)

    if replayed_kesme is not None:
        kesme = replayed_kesme
        satirlar = db.scalars(
            select(UnknownDeviceTelemetry).where(
                UnknownDeviceTelemetry.status == STATUS_REPLAYED,
                UnknownDeviceTelemetry.replayed_at.is_not(None),
                UnknownDeviceTelemetry.replayed_at < kesme,
            )
        ).all()
        for satir in satirlar:
            db.delete(satir)
        silinen["replayed"] = len(satirlar)

    if pending_kesme is not None:
        kesme = pending_kesme
        satirlar = db.scalars(
            select(UnknownDeviceTelemetry).where(
                UnknownDeviceTelemetry.status == STATUS_PENDING,
                UnknownDeviceTelemetry.first_seen_at < kesme,
            )
        ).all()
        for satir in satirlar:
            db.delete(satir)
        silinen["pending"] = len(satirlar)

    if silinen["replayed"] or silinen["pending"]:
        _sayim_onbellegi_bosalt()
        _stat_arttir(
            "unknown_device_quarantine_replayed_cleanup_total", silinen["replayed"]
        )
        # Suresi DOLMUS pending = `expired`; kapasite icin suresi dolmadan
        # silinen (`data_shed`) ile ayni sayaca GIRMEZ.
        _stat_arttir("unknown_device_quarantine_expired_total", silinen["pending"])
        logger.info(
            "unknown_device_quarantine_purged replayed=%d expired_pending=%d",
            silinen["replayed"],
            silinen["pending"],
        )
    return silinen
