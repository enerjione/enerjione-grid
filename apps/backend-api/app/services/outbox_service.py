import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.config import settings
from app.models.outbox_event import OutboxEvent
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)

#: `last_error` icin ust sinir. Hata metni teshis kaniti; roman degil.
_LAST_ERROR_MAX = 500


def enqueue_outbox_event(db: Session, *, topic: str, payload: dict, dedup_key: str) -> None:
    if not dedup_key:
        raise ValueError("outbox dedup_key zorunludur")
    # Atomik INSERT ... ON CONFLICT DO NOTHING. Onceki SELECT-then-INSERT'te
    # race vardi: ayni message_id iki request'te es zamanli gelince ikisi de
    # "yok" gorup INSERT ediyor -> ikincisi UniqueViolation -> TUM ingest
    # commit'i patliyordu (200 cihaz + gateway retry yukunde surekli). ON
    # CONFLICT ile duplicate DB seviyesinde sessizce yutulur; ayni transaction,
    # commit'i caller yapar (davranis degismedi).
    stmt = (
        pg_insert(OutboxEvent)
        .values(
            topic=topic,
            dedup_key=dedup_key,
            payload_json=json.dumps(payload, ensure_ascii=False),
            published=False,
            created_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
    )
    db.execute(stmt)


def pending_outbox_stmt(limit: int) -> Select:
    """Yayinlanmayi bekleyen satirlari SAHIPLENEREK secen sorgu.

    `FOR UPDATE SKIP LOCKED` — NEDEN
    --------------------------------
    Eskiden bu duz bir SELECT idi: iki yayinci (coklu uvicorn worker, ya da
    acilis flush'i ile arka plan worker'i) ayni anda kostugunda IKISI DE ayni
    ilk N satiri okur, IKISI DE broker'a basar ve ikisi de `published=True`
    yazardi. Kilit cekismesi bile olusmazdi — sessizce CIFT YAYIN.

    `FOR UPDATE` satirlari transaction sonuna kadar sahiplenir; `SKIP LOCKED`
    ise baskasinin sahiplendigi satirda BEKLEMEK yerine ATLAR. Beklemek de
    dogru sonucu verirdi (kilit kalkinca satir published=True gorunur ve
    yuklemden duser) ama ikinci yayinciyi birincinin hizina zincirler —
    yani olcek amaci kaybolurdu.

    SQLite'ta (testler) bu ek sessizce yok sayilir; PostgreSQL'de etkilidir.

    YUKLEM BICIMI: `published.is_(False)` kismi indeksin
    (`ix_outbox_events_unpublished`) yuklemiyle AYNI bicimde yazilmali.
    `== False`e cevrilirse planlayici indeksi eslestiremez ve seq scan'e duser
    (olculdu: 0,012 ms -> 37,7 ms). `dead_letter_at` filtresi olmasaydi damgali
    satirlar `ORDER BY id ASC` sirasinin BASINDA kalir ve tikanma dead-letter'a
    ragmen surerdi.
    """
    return (
        select(OutboxEvent)
        .where(OutboxEvent.published.is_(False), OutboxEvent.dead_letter_at.is_(None))
        .order_by(OutboxEvent.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


def flush_outbox(db: Session, *, limit: int = 100) -> int:
    """Yayinlanmamis outbox satirlarini broker'a TOPLU gonderir.

    TOPLU YAYIN — NEDEN
    -------------------
    Satirlar toplu SELECT ediliyordu ama TEK TEK yayinlaniyordu. Her mesaj
    icin ayri bir asyncio-loop gecisi ve ayri bir PubAck beklemesi vardi;
    yani parti ne kadar buyuk olursa olsun hiz `1 / RTT` ile siniriydi
    (sahada olculen tavan ~480 msj/sn, gateway uretimi 1135 msj/sn). Artik
    parti tek cagriyla veriliyor, ack'ler paralel bekleniyor.

    SATIR BAZLI HATA YALITIMI — NEDEN
    ---------------------------------
    Onceki surumde tek `try` yoktu: bir satirin publish'i patlayinca exception
    disari cikiyor, `db.commit()` HIC calismiyor ve batch'teki HICBIR satir
    published isaretlenmiyordu. Worker hatayi yutup ayni batch'i sonsuza kadar
    yeniden deniyordu (HEAD-OF-LINE BLOCK). Tek zehirli satir hem tum yayini
    kalici durduruyor hem de `published=False` hic silinmedigi icin tabloyu
    sinirsiz buyutuyordu.

    Toplu yayinda da korunuyor: `publish_events` HER MESAJ ICIN ayri bir
    sonuc doner (basari icin None, aksi halde hatanin kendisi). Patlayan
    satirin `attempts` sayaci artar, partinin geri kalani published olur.

    GECICI HATA DEAD-LETTER'A DUSURMEZ — kural: bir broker kesintisi TUM
    satirlari esit etkiler. Bu turda EN AZ BIR satir yayinlanabildiyse hata
    satira ozgudur ve sayac artar; HICBIRI yayinlanamadiysa ariza sistemiktir
    (broker kapali) ve sayac ARTIRILMAZ. Aksi halde uzun bir kesinti butun
    kuyrugu sessizce dead-letter'a dusurur — yani veri kaybi gibi gorunur.

    IDEMPOTENCY BOZULMAZ: `dedup_key` UNIQUE ve tuketici tarafinda bagimsiz
    `processed_messages` defteri var. Kismi ilerlemenin commit edilmesi
    at-least-once semantigini degistirmez.
    """
    rows = list(db.scalars(pending_outbox_stmt(limit)).all())
    if not rows:
        return 0

    failed: list[tuple[OutboxEvent, Exception]] = []
    # Payload cozumu de satir bazli: bozuk JSON tek satiri dusurur, partiyi degil.
    gonderilecek: list[OutboxEvent] = []
    items: list[tuple[str, dict, str]] = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json)
        except Exception as exc:  # noqa: BLE001
            failed.append((row, exc))
            continue
        gonderilecek.append(row)
        items.append((row.topic, payload, row.dedup_key))

    yayinlananlar: list[OutboxEvent] = []
    if items:
        # TEK cagri: N mesaj icin N ayri thread gecisi + N ardisik PubAck
        # beklemesi yerine tek gecis ve paralel ack.
        sonuclar = event_bus.publish_events(items)
        for row, hata in zip(gonderilecek, sonuclar, strict=True):
            if hata is None:
                yayinlananlar.append(row)
            else:
                failed.append((row, hata))
    published = len(yayinlananlar)

    if failed and not published:
        # SISTEMIK ariza (broker kapali gibi): hicbir satir gecmedi. Sayaci
        # ARTIRMA, damgalama, commit etme — ilk hatayi caller'a birak ki
        # worker'in mevcut hata sayaci/geri cekilme mantigi calissin ve
        # kesinti gorunur olsun.
        raise failed[0][1]

    if yayinlananlar:
        # TEK toplu UPDATE — ORM satir satir yazsaydi 200'luk partide 200 ayri
        # UPDATE ifadesi ciderdi. synchronize_session=False guvenli: commit
        # zaten tum nesneleri expire ediyor.
        db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_([r.id for r in yayinlananlar]))
            .values(published=True, published_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )

    dead_lettered = 0
    if failed:
        # Buraya ancak EN AZ BIR satir gectiyse gelinir; yani broker ayakta ve
        # hata satira OZGU. Sayaci artir, tavani asani damgala.
        now = datetime.now(timezone.utc)
        for row, exc in failed:
            row.attempts = (row.attempts or 0) + 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:_LAST_ERROR_MAX]
            if row.attempts >= settings.outbox_max_publish_attempts:
                row.dead_letter_at = now
                dead_lettered += 1

    if published:
        db.commit()
    if dead_lettered:
        logger.error(
            "outbox_dead_lettered count=%d attempts_cap=%d "
            "(bu olaylar ARTIK YAYINLANMAYACAK; last_error sutununa bakin)",
            dead_lettered,
            settings.outbox_max_publish_attempts,
        )
    return published


# NOT: `purge_published_outbox` BURADAN KALDIRILDI (2026-08-03). Tek turluk,
# tavansiz bir DELETE idi ve tam da duzeltilen kusuru (yetismeyen temizlik +
# tek seferde 50.000 satir silme) yeniden davet ediyordu. Outbox temizliginin
# TEK yeri artik `telemetry_retention.purge_outbox_events`: tur tavanli, her
# tur ayri commit, published=False satira dokunmaz.
