"""Ariza (FaultEvent) yeniden hesaplama servisi.

Sorumluluk:
  Sistemdeki AKTIF alarmlardan yola cikarak hat bazli "ariza yerleri"ni
  hesaplar ve fault_events tablosunu surekli senkron tutar. Frontend
  haritasindaki "son RED -> ilk GREEN cihaz arasindaki edge" mantiginin
  backend'deki karsiligidir.

Ne zaman cagirilir:
  - Yeni alarm uretildiginde (POST /internal/alarms)
  - Alarm clear edildiginde (POST /internal/alarms/clear)
  - alarm_reconciliation worker bir alarmi cozdugunde
  - Manuel acknowledge/reset uclarinda (gerekirse)

Algoritma (sade):
  Her hat icin (line_id):
    1) Hatta atanmis cihazlari pole sequence_no + slot ici sirayla diz.
    2) Aktif alarm (reset=False) veren cihazlari isaretle (RED).
    3) En yuksek seq'li RED cihazi bul (last_red).
    4) last_red sonrasinda gelen ilk alarmsiz cihazi bul (first_green) — yoksa NULL.
    5) Pole araligi: last_red'in slot'undan first_green'in slot'una kadar
       kapsayacak sekilde hesapla. Sade yaklasim:
         from_pole = last_red'in oturdugu slot'un from_pole'u
         to_pole = first_green varsa onun slot'unun to_pole'u; yoksa
                   last_red'in slot'unun to_pole'u (hat ucu).
    6) Bir FaultEvent kaydi var mi (status=open, ayni line_id) -> guncelle.
       Yoksa yeni kayit olustur. (Bir hatta tek aktif fault tutulur.)

  Cihazda artik aktif alarm yoksa -> mevcut open fault'i resolved'a cek.

NOT: Bu hesap basit ve "her hat tek aktif fault" varsayimini kullanir.
Ileri seviye (cok bagimsiz fault, bransman propagasyonu) frontend
haritasinda zaten daha sofistike — burada ozet/raporlama icin yeterli.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, LineSegment, Pole, Region

logger = logging.getLogger(__name__)


# Debounce: alarm firtinasinda (orn. tum hat offline -> 50 alarm 200ms icinde)
# her POST /internal/alarms cagrisi recompute_faults'u tetikliyor. Bu fonksiyon
# her line icin tum cihazlari + segmentleri + alarm sayisini scan ediyor; lock
# contention'da DB connection pool tukenebilir. Coalescing strateji:
#   - Bir recompute calismaya BASLAYINCA `_in_flight=True` set edilir.
#   - Onun bittigi ana kadar gelen yeni cagrilar `_pending_request=True` set
#     edip return eder (kendileri recompute YAPMAZ).
#   - Recompute biterken `_pending_request` ise tekrar bir kez calistirilir.
# Sonuc: 50 hizli cagri 2-3 calistirma yapar (ilk + son), 50 yerine.
# Ayrica minimum interval (_MIN_INTERVAL_SEC) ile cok hizli back-to-back
# trigger'lari engelleriz (son recompute'tan beri 0.5sn gecmemisse atla).
_recompute_lock = threading.Lock()
_in_flight = False
_pending_request = False
_last_completed_at: float = 0.0
_MIN_INTERVAL_SEC = float(os.getenv("FAULT_RECOMPUTE_MIN_INTERVAL_SEC", "0.5"))


def recompute_faults_debounced(db: Session) -> bool:
    """Debounced trigger — alarm firtinasinda fault_recompute'i coalescing eder.

    Returns:
        True  -> recompute bu cagrida calistirildi (caller commit yapacak).
        False -> recompute atlandi (zaten in-flight veya minimum interval
                 dolmadi). Caller commit YAPABILIR ama recompute SONUCU
                 bir sonraki tetikte yansiyacak. Caller'in flow'u DEGISMEZ
                 (clear/create akisi devam eder).

    NOT: `False` donen cagri icin alarm-service icin daha sonraki bir
    tetikleyici (yeni alarm/clear) recompute'i tetikler. En kotu durumda
    fault tablosu bir kac saniyelik gecikmeli senkron olur.
    """
    global _in_flight, _pending_request, _last_completed_at

    with _recompute_lock:
        if _in_flight:
            _pending_request = True
            logger.debug("recompute_faults_skipped_in_flight")
            return False
        # Minimum interval kontrolu — son recompute uzerinden _MIN_INTERVAL_SEC
        # gecmemisse bir sonrakine birak.
        now = time.monotonic()
        elapsed = now - _last_completed_at
        if elapsed < _MIN_INTERVAL_SEC:
            _pending_request = True
            logger.debug(
                "recompute_faults_skipped_min_interval elapsed=%.3fs min=%.3fs",
                elapsed,
                _MIN_INTERVAL_SEC,
            )
            return False
        _in_flight = True
        _pending_request = False

    try:
        recompute_faults(db)
    finally:
        with _recompute_lock:
            _last_completed_at = time.monotonic()
            _in_flight = False
            had_pending = _pending_request
            _pending_request = False
        if had_pending:
            # Pending request varsa hemen bir kez daha calistir — son alarm
            # degisiklikleri yansisin. Sonsuz dongu olmasin: yine pending varsa
            # min_interval onunu kesecek.
            logger.debug("recompute_faults_pending_replay")
            try:
                recompute_faults_debounced(db)
            except Exception:  # noqa: BLE001
                logger.exception("recompute_faults_pending_replay_failed")
    return True


def recompute_faults(db: Session) -> None:
    """Tum aktif alarmlardan yola cikarak fault_events tablosunu senkronla."""
    # Aktif (reset=False) VE hat arizasi ureten (produces_fault=True) alarmli
    # cihaz id'leri. produces_fault=False alarmlar (gecici/gurultulu) burada
    # dikkate ALINMAZ: fault araligi hesabina girmez, FaultEvent acmaz/genisletmez.
    # Boylece bu cihazlar "yesil" gibi davranir; alarm yine Alarmlar ekraninda durur.
    active_alarm_device_ids = {
        row[0]
        for row in db.execute(
            select(AlarmEvent.device_id)
            .where(AlarmEvent.reset.is_(False))
            .where(AlarmEvent.produces_fault.is_(True))
        ).all()
    }

    # Hat -> sirali (cihazli) segment listesi
    lines = list(db.scalars(select(Line)).all())
    poles_by_line: dict[int, list[Pole]] = {}
    all_poles = list(db.scalars(select(Pole)).all())
    poles_by_id = {p.id: p for p in all_poles}
    for p in all_poles:
        poles_by_line.setdefault(p.line_id, []).append(p)
    for arr in poles_by_line.values():
        arr.sort(key=lambda p: p.sequence_no)

    segments = list(db.scalars(select(LineSegment)).all())
    devices_by_id = {d.id: d for d in db.scalars(select(Device)).all()}
    regions_by_id = {r.id: r for r in db.scalars(select(Region)).all()}

    # line_id -> sirali cihaz listesi (slot from_pole_seq -> orderInSlot)
    type_seg_list = list[tuple[int, int, int, LineSegment]]
    devices_per_line: dict[int, type_seg_list] = {}
    # Slot anahtarina gore segmentleri grupla (sirali olarak orderInSlot
    # turetilebilsin diye device_position_t / created_at sirasi).
    by_slot: dict[tuple[int, int, int], list[LineSegment]] = {}
    for s in segments:
        if s.device_id is None:
            continue
        by_slot.setdefault((s.line_id, s.from_pole_id, s.to_pole_id), []).append(s)
    for slot, segs in by_slot.items():
        segs.sort(
            key=lambda s: (
                s.device_position_t if s.device_position_t is not None else 0.5,
                s.created_at,
                s.id,
            )
        )
        line_id = slot[0]
        from_pole = poles_by_id.get(slot[1])
        if from_pole is None:
            continue
        for idx, seg in enumerate(segs):
            devices_per_line.setdefault(line_id, []).append(
                (from_pole.sequence_no, idx, seg.id, seg)
            )
    # Hat icindeki cihaz listesini siralayalim (slot fromSeq, sonra slot ici idx)
    for arr in devices_per_line.values():
        arr.sort(key=lambda t: (t[0], t[1], t[2]))

    now = datetime.now(timezone.utc)
    # line_id -> mevcut AKTIF FaultEvent. "Aktif" = closed olmayan tum
    # statusler (open/assigned/in_progress/resolved). Bir hatta tek aktif
    # fault tutuyoruz; yeni alarm degisiklikleri mevcut kaydı GUNCELLER,
    # yeni satir AÇMAZ (aksi halde duplicate olusur).
    open_faults = list(
        db.scalars(
            select(FaultEvent).where(FaultEvent.status != "closed")
        ).all()
    )
    open_by_line: dict[int, FaultEvent] = {}
    # Bir hatta birden fazla aktif kayit varsa (eski drift), en yenisini
    # tut, digerlerini closed yap (silmek riskli — tarihce icin closed).
    for f in sorted(open_faults, key=lambda x: x.opened_at, reverse=True):
        if f.line_id in open_by_line:
            f.status = "closed"
            f.closed_at = now
            if f.resolved_at is None:
                f.resolved_at = now
        else:
            open_by_line[f.line_id] = f

    # Yeni fault'lar (bu turda olusturulanlar) — email dispatch icin biriktir.
    # commit'ten sonra dispatch ederiz ki fault.id atanmis olsun.
    new_faults_for_dispatch: list[tuple[FaultEvent, Pole | None]] = []

    handled_lines: set[int] = set()

    for line in lines:
        line_devices = devices_per_line.get(line.id, [])
        if not line_devices:
            continue
        # Son alarmli cihaz indexini bul
        last_red_idx: int | None = None
        for i, (_, _, _, seg) in enumerate(line_devices):
            if seg.device_id in active_alarm_device_ids:
                last_red_idx = i
        if last_red_idx is None:
            continue  # bu hatta aktif fault yok
        last_red_seg = line_devices[last_red_idx][3]
        next_seg: LineSegment | None = None
        if last_red_idx + 1 < len(line_devices):
            next_seg = line_devices[last_red_idx + 1][3]

        from_pole = poles_by_id.get(last_red_seg.from_pole_id)
        last_red_to_pole = poles_by_id.get(last_red_seg.to_pole_id)
        if from_pole is None or last_red_to_pole is None:
            continue
        # to_pole: next varsa onun slot'unun to'su, yoksa last_red'in to'su
        to_pole: Pole | None
        first_green_id: int | None = None
        if next_seg is not None:
            to_pole = poles_by_id.get(next_seg.to_pole_id) or last_red_to_pole
            first_green_id = next_seg.device_id
        else:
            to_pole = last_red_to_pole

        last_red_dev = devices_by_id.get(last_red_seg.device_id) if last_red_seg.device_id else None
        first_green_dev = devices_by_id.get(first_green_id) if first_green_id else None

        existing = open_by_line.get(line.id)
        if existing is None:
            # Yeni fault olustur — OTOMATIK ATAMA YAPILMAZ. Ariza her zaman
            # "open" olarak acilir; atama, Hat Arizalari sayfasindan manuel
            # yapilir (faults.py assign endpoint). Onceden otomatik operator
            # atanip "assigned" geliyordu; kullanici arizanin once "acik"
            # gorunup sonra elle atanmasini istedi.
            fault = FaultEvent(
                line_id=line.id,
                region_id=line.region_id,
                last_red_device_id=last_red_seg.device_id,  # type: ignore[arg-type]
                first_green_device_id=first_green_id,
                from_pole_id=from_pole.id,
                to_pole_id=to_pole.id if to_pole else last_red_to_pole.id,
                from_pole_seq=from_pole.sequence_no,
                to_pole_seq=(to_pole.sequence_no if to_pole else last_red_to_pole.sequence_no),
                status="open",
                opened_at=now,
                assigned_to_username=None,
                assigned_at=None,
            )
            db.add(fault)
            logger.info(
                "fault_opened line_id=%d from_pole_seq=%s to_pole_seq=%s last_red_dev=%s first_green_dev=%s assigned=None",
                line.id, fault.from_pole_seq, fault.to_pole_seq,
                last_red_dev.code if last_red_dev else None,
                first_green_dev.code if first_green_dev else None,
            )
            # Olay kaydi: yeni fault aciliyor — Olaylar sayfasinda gozuksun.
            # Mesaj kullanici dostu Turkce; metadata'da line/region/device
            # detaylari (gerekirse genisletilebilir).
            try:
                from app.services.event_service import record_event
                region_obj = regions_by_id.get(line.region_id)
                last_red_label = last_red_dev.name if last_red_dev else (
                    last_red_dev.code if last_red_dev else "—"
                )
                msg = (
                    f"Hat arızası açıldı: {line.name}"
                    + (f" ({region_obj.name})" if region_obj else "")
                    + f" — Direk #{fault.from_pole_seq} ↔ #{fault.to_pole_seq}"
                    + f", Son aktif cihaz: {last_red_label}"
                )
                record_event(
                    db,
                    category="fault",
                    event_type="fault_opened",
                    severity="warning",
                    device_code=last_red_dev.code if last_red_dev else None,
                    message=msg,
                    metadata={
                        "line_id": line.id,
                        "line_code": line.code,
                        "line_name": line.name,
                        "region_id": line.region_id,
                        "region_name": region_obj.name if region_obj else None,
                        "from_pole_seq": fault.from_pole_seq,
                        "to_pole_seq": fault.to_pole_seq,
                        "last_red_device_id": fault.last_red_device_id,
                        "first_green_device_id": fault.first_green_device_id,
                        "assigned_to": None,
                    },
                    i18n_key="fault_opened",
                    i18n_params={
                        "line": line.name,
                        "region": region_obj.name if region_obj else "",
                        "from_seq": fault.from_pole_seq,
                        "to_seq": fault.to_pole_seq,
                        "device": last_red_label,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("fault_record_event_failed")
            # Email dispatch icin biriktir; commit sonrasi gonderilecek.
            # Konum: from_pole'un koordinatlarini kullan (saha personeli
            # bu noktaya gider; fault aralık baslangici).
            new_faults_for_dispatch.append((fault, from_pole))
        else:
            # Mevcut fault'i guncelle (cihaz/pole degismis olabilir)
            existing.last_red_device_id = last_red_seg.device_id  # type: ignore[assignment]
            existing.first_green_device_id = first_green_id
            existing.from_pole_id = from_pole.id
            existing.to_pole_id = to_pole.id if to_pole else last_red_to_pole.id
            existing.from_pole_seq = from_pole.sequence_no
            existing.to_pole_seq = (
                to_pole.sequence_no if to_pole else last_red_to_pole.sequence_no
            )
        handled_lines.add(line.id)

    # Bu turda aktif alarm GORULMEYEN hatlardaki acik fault'lari resolve et.
    # ONEMLI: fault zaten "resolved" durumundaysa tekrar yazma + event spam'i
    # olusmasin. Sadece status != "resolved" ise gercek state transition var.
    for line_id, fault in open_by_line.items():
        if line_id in handled_lines:
            continue
        if fault.status == "resolved":
            # Onceki turlarda zaten cozulmus — yeniden resolved'a cevirme,
            # event de yazma. (closed yapmak ayri bir is.) Sessizce gec.
            continue
        fault.status = "resolved"
        fault.resolved_at = now
        logger.info("fault_resolved fault_id=%d line_id=%d", fault.id, line_id)
        # Olay kaydi: ariza normale dondu.
        try:
            from app.services.event_service import record_event
            line_obj = next((l for l in lines if l.id == line_id), None)
            region_obj = regions_by_id.get(line_obj.region_id) if line_obj else None
            line_name = line_obj.name if line_obj else f"#{line_id}"
            msg = (
                f"Hat arızası normale döndü: {line_name}"
                + (f" ({region_obj.name})" if region_obj else "")
            )
            record_event(
                db,
                category="fault",
                event_type="fault_resolved",
                severity="info",
                message=msg,
                metadata={
                    "fault_id": fault.id,
                    "line_id": line_id,
                    "line_name": line_name,
                    "region_name": region_obj.name if region_obj else None,
                },
                i18n_key="fault_resolved",
                i18n_params={
                    "line": line_name,
                    "region": region_obj.name if region_obj else "",
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("fault_resolved_record_event_failed")
    # cagiran fonksiyon commit yapacak
    _ = regions_by_id  # used in API serializer

    # Yeni acilan fault'lar icin email dispatch — flush ile id atanmasi gerek.
    # commit cagiran fonksiyonda yapilacak; biz flush ederek id'leri elde
    # ediyoruz. dispatch ic try/except ile sarili, hatalari yutuyor.
    if new_faults_for_dispatch:
        try:
            db.flush()
        except Exception:  # noqa: BLE001
            logger.exception("fault_flush_failed_before_dispatch")
            return
        try:
            from app.services.notification_dispatch_service import dispatch_fault_notifications
        except Exception:  # noqa: BLE001
            logger.exception("fault_notification_dispatch_import_failed")
            return
        for fault, from_pole in new_faults_for_dispatch:
            try:
                dispatch_fault_notifications(
                    db,
                    fault_id=fault.id,
                    line_id=fault.line_id,
                    region_id=fault.region_id,
                    last_red_device_id=fault.last_red_device_id,
                    first_green_device_id=fault.first_green_device_id,
                    from_pole_seq=fault.from_pole_seq,
                    to_pole_seq=fault.to_pole_seq,
                    latitude=from_pole.latitude if from_pole else None,
                    longitude=from_pole.longitude if from_pole else None,
                    opened_at=fault.opened_at,
                    assigned_to_username=fault.assigned_to_username,
                )
            except Exception:  # noqa: BLE001
                logger.exception("fault_email_dispatch_failed fault_id=%s", fault.id)
