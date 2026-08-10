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

Algoritma:
  Her hat icin (line_id):
    1) Hatta atanmis cihazlari pole sequence_no + slot ici sirayla diz.
    2) Aktif alarm (reset=False, produces_fault=True) veren cihazlari
       isaretle (RED), digerleri GREEN.
    3) Sirali listede her MAKSIMAL RED blogu bir ayri ariza bolgesidir:
       blogun son cihazi = last_red, blogun hemen ardindaki cihaz =
       first_green (blok hattin sonunda bitiyorsa first_green NULL).
    4) Her bolge icin pole araligi:
         from_pole = last_red'in oturdugu slot'un from_pole'u
         to_pole   = first_green varsa onun slot'unun to_pole'u; yoksa
                     last_red'in slot'unun to_pole'u (hat ucu).
    5) Hesaplanan bolgeler, o hattin mevcut AKTIF FaultEvent kayitlariyla
       eslestirilir (bkz. `_match_zones_to_faults`): eslesen guncellenir,
       eslesmeyen bolge icin YENI kayit acilir, karsiligi kalmayan kayit
       "resolved" edilir.

COKLU BOLGE (surum notu):
  Onceki surum hat basina yalnizca EN SON RED cihazi buluyordu ve
  `open_by_line` sozlugu ile hat basina tek aktif kayit zorluyordu; fazla
  kayitlari "drift" sayip closed'a cekiyordu. Bu yuzden ayni hatta
  birbirinden bagimsiz iki ariza bolgesi olustugunda (R-G-R-G deseni)
  IKINCISI sessizce kayboluyordu. Artik her RED blogu kendi FaultEvent'ine
  sahip; tek bolgeli (klasik) senaryoda davranis aynen korunur.

  RED/GREEN deseni -> bolgeler:
    R R G G      -> 1 bolge (last_red=#2, first_green=#3)
    R G R G      -> 2 bolge (#1/#2 ve #3/#4)
    R R G R G    -> 2 bolge (#2/#3 ve #4/#5)
    R R R        -> 1 bolge (last_red=#3, first_green=NULL -> hat ucu)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.services.line_distance_service import (
    LineDistanceIndex,
    build_line_distance_index,
)

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


class _FaultZone:
    """Bir hat uzerindeki TEK bagimsiz ariza bolgesi (hesaplanmis, henuz
    DB kaydiyla eslestirilmemis)."""

    __slots__ = (
        "last_red_device_id",
        "first_green_device_id",
        "from_pole",
        "to_pole",
        "start_m",
        "end_m",
    )

    def __init__(
        self,
        last_red_device_id: int,
        first_green_device_id: int | None,
        from_pole: Pole,
        to_pole: Pole,
        start_m: float | None = None,
        end_m: float | None = None,
    ) -> None:
        self.last_red_device_id = last_red_device_id
        self.first_green_device_id = first_green_device_id
        self.from_pole = from_pole
        self.to_pole = to_pole
        # Tel mesafesi (m, hat basindan). Ariza son RED cihaz ile ilk GREEN
        # cihaz ARASINDA oldugu icin sinirlar direk degil CIHAZ konumlaridir;
        # bu yuzden from_pole/to_pole araligindan daha dardir.
        self.start_m = start_m
        self.end_m = end_m

    @property
    def seq_range(self) -> tuple[int, int]:
        a = self.from_pole.sequence_no
        b = self.to_pole.sequence_no
        return (a, b) if a <= b else (b, a)

    @property
    def distance_range(self) -> tuple[float | None, float | None, float | None]:
        """(start_m, end_m, length_m) — kucukten buyuge normalize edilmis.

        Ters siralanmis (to_pole_seq < from_pole_seq) topolojilerde de
        start <= end garantisi verir; herhangi biri NULL ise ucu de NULL."""
        if self.start_m is None or self.end_m is None:
            return (None, None, None)
        lo, hi = (self.start_m, self.end_m)
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi, hi - lo)


def _compute_line_zones(
    line_devices: list[tuple[int, int, int, LineSegment]],
    active_alarm_device_ids: set[int],
    poles_by_id: dict[int, Pole],
    dist_index: LineDistanceIndex,
    line_id: int,
) -> list[_FaultZone]:
    """Sirali cihaz listesinden TUM ariza bolgelerini cikar.

    Her maksimal RED blogunun sonu bir ariza bolgesi uretir. Blogun hemen
    ardindaki cihaz first_green'dir; blok hattin sonunda bitiyorsa
    first_green NULL (ariza hat ucuna kadar uzuyor).

    `dist_index` ile ayni anda tel mesafesi de doldurulur: bolgenin baslangici
    son RED cihazin, bitisi ilk GREEN cihazin (yoksa hattin son diregi) hat
    basindan olculen mesafesidir.
    """
    zones: list[_FaultZone] = []
    total = len(line_devices)
    for i, (_, _, _, seg) in enumerate(line_devices):
        if seg.device_id not in active_alarm_device_ids:
            continue
        # Bu cihaz RED. Blogun SONU mu? (son cihaz ya da sonraki GREEN)
        next_seg: LineSegment | None = None
        if i + 1 < total:
            next_seg = line_devices[i + 1][3]
            if next_seg.device_id in active_alarm_device_ids:
                # Blok devam ediyor — bolgeyi blogun sonunda uretecegiz.
                continue
        from_pole = poles_by_id.get(seg.from_pole_id)
        last_red_to_pole = poles_by_id.get(seg.to_pole_id)
        if from_pole is None or last_red_to_pole is None:
            continue
        first_green_id: int | None = None
        to_pole = last_red_to_pole
        if next_seg is not None:
            first_green_id = next_seg.device_id
            to_pole = poles_by_id.get(next_seg.to_pole_id) or last_red_to_pole
        if seg.device_id is None:
            continue
        # Tel mesafesi sinirlari: baslangic = son RED cihaz, bitis = ilk GREEN
        # cihaz. Ilk GREEN yoksa ariza hat ucuna kadar uzuyor demektir.
        start_m = dist_index.device_distance.get(seg.id)
        end_m = (
            dist_index.device_distance.get(next_seg.id)
            if next_seg is not None
            else dist_index.line_end_distance(line_id)
        )
        zones.append(
            _FaultZone(
                last_red_device_id=seg.device_id,
                first_green_device_id=first_green_id,
                from_pole=from_pole,
                to_pole=to_pole,
                start_m=start_m,
                end_m=end_m,
            )
        )
    return zones


def _seq_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _match_zones_to_faults(
    zones: list[_FaultZone],
    existing: list[FaultEvent],
) -> tuple[list[tuple[_FaultZone, FaultEvent]], list[_FaultZone], list[FaultEvent]]:
    """Hesaplanan bolgeleri mevcut aktif kayitlarla esle.

    Amac: her recompute'ta ayni ariza icin YENI satir acmamak (duplicate) ama
    gercekten yeni bir bolge olustugunda da onu kacirmamak.

    Eslestirme sirasi (once en guclu sinyal):
      1. `last_red_device_id` ayni  — ariza ayni cihazin arkasinda duruyor.
      2. Direk araliklari kesisiyor — ariza bolgesi buyudu/kaydi (cihaz
         degisti ama ayni fiziksel bolge).

    Returns: (eslesenler, yeni_bolgeler, karsiligi_kalmayan_kayitlar)
    """
    unmatched_zones = list(zones)
    unmatched_faults = list(existing)
    pairs: list[tuple[_FaultZone, FaultEvent]] = []

    # 1) last_red_device_id tam eslesme
    for zone in list(unmatched_zones):
        hit = next(
            (f for f in unmatched_faults if f.last_red_device_id == zone.last_red_device_id),
            None,
        )
        if hit is not None:
            pairs.append((zone, hit))
            unmatched_zones.remove(zone)
            unmatched_faults.remove(hit)

    # 2) Direk araligi kesisimi. Komsu bolgeler bir direk PAYLASIR (orn.
    #    1-3 ve 3-5), o yuzden "ilk kesisen"i almak yanlis kayda yazabilir;
    #    en BUYUK kesisime sahip kaydi seciyoruz.
    for zone in list(unmatched_zones):
        zr = zone.seq_range
        best: FaultEvent | None = None
        best_overlap = 0
        for f in unmatched_faults:
            if f.from_pole_seq is None or f.to_pole_seq is None:
                continue
            fr = (
                (f.from_pole_seq, f.to_pole_seq)
                if f.from_pole_seq <= f.to_pole_seq
                else (f.to_pole_seq, f.from_pole_seq)
            )
            if not _seq_overlap(zr, fr):
                continue
            overlap = min(zr[1], fr[1]) - max(zr[0], fr[0]) + 1
            if overlap > best_overlap:
                best, best_overlap = f, overlap
        if best is not None:
            pairs.append((zone, best))
            unmatched_zones.remove(zone)
            unmatched_faults.remove(best)

    return pairs, unmatched_zones, unmatched_faults


def _resolve_fault(
    db: Session,
    fault: FaultEvent,
    line: Line | None,
    regions_by_id: dict[int, Region],
    now: datetime,
) -> None:
    """Bir ariza bolgesi duzeldi -> status="resolved" + olay kaydi.

    Idempotent: kayit zaten "resolved" ise hicbir sey yapmaz (aksi halde her
    recompute turunda "normale dondu" olayi tekrar yazilir -> event spam).
    """
    if fault.status == "resolved":
        return
    fault.status = "resolved"
    fault.resolved_at = now
    logger.info(
        "fault_resolved fault_id=%d line_id=%d poles=%s-%s",
        fault.id, fault.line_id, fault.from_pole_seq, fault.to_pole_seq,
    )
    try:
        from app.services.event_service import record_event

        region_obj = regions_by_id.get(line.region_id) if line else None
        line_name = line.name if line else f"#{fault.line_id}"
        msg = (
            f"Hat arızası normale döndü: {line_name}"
            + (f" ({region_obj.name})" if region_obj else "")
            + f" — Direk #{fault.from_pole_seq} ↔ #{fault.to_pole_seq}"
        )
        record_event(
            db,
            category="fault",
            event_type="fault_resolved",
            severity="info",
            message=msg,
            metadata={
                "fault_id": fault.id,
                "line_id": fault.line_id,
                "line_name": line_name,
                "region_name": region_obj.name if region_obj else None,
                "from_pole_seq": fault.from_pole_seq,
                "to_pole_seq": fault.to_pole_seq,
            },
            i18n_key="fault_resolved",
            i18n_params={
                "line": line_name,
                "region": region_obj.name if region_obj else "",
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("fault_resolved_record_event_failed")


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

    # Tel mesafesi indeksi — direk koordinatlarindan hat boyunca kumulatif
    # mesafeler + cihazlarin slot ici interpolasyonu. Bir kez kurulur, tum
    # hatlarda paylasilir.
    dist_index = build_line_distance_index(lines, all_poles, segments)

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
        # BRANSMAN BAGLANTI SEGMENTI: kolun ilk segmenti ana hattaki dallanma
        # diregiyle baslar, yani `from_pole` BASKA bir hattin diregidir ve
        # sequence_no'su bu kolun numaralandirmasiyla kiyaslanamaz (ana hatta
        # 10, kolun direkleri 1,2,3...). Ham haliyle siralandiginda bu cihaz
        # kolun SONUNA dusuyor; oysa kolun GIRISIDIR — "gordum/gormedim"
        # zinciri ters okunur ve ariza araligi yanlis cikar.
        sira = 0 if from_pole.line_id != line_id else from_pole.sequence_no
        for idx, seg in enumerate(segs):
            devices_per_line.setdefault(line_id, []).append((sira, idx, seg.id, seg))
    # Hat icindeki cihaz listesini siralayalim (slot fromSeq, sonra slot ici idx)
    for arr in devices_per_line.values():
        arr.sort(key=lambda t: (t[0], t[1], t[2]))

    now = datetime.now(timezone.utc)
    # line_id -> mevcut AKTIF FaultEvent listesi. "Aktif" = closed olmayan
    # tum statusler (open/assigned/in_progress/resolved). Ayni hatta birden
    # fazla bagimsiz ariza bolgesi olabilir; her bolge kendi kaydini tutar.
    open_faults = list(
        db.scalars(
            select(FaultEvent).where(FaultEvent.status != "closed")
        ).all()
    )
    faults_by_line: dict[int, list[FaultEvent]] = {}
    for f in sorted(open_faults, key=lambda x: x.opened_at, reverse=True):
        faults_by_line.setdefault(f.line_id, []).append(f)

    # Yeni fault'lar (bu turda olusturulanlar) — email dispatch icin biriktir.
    # commit'ten sonra dispatch ederiz ki fault.id atanmis olsun.
    new_faults_for_dispatch: list[tuple[FaultEvent, Pole | None]] = []

    # Bu turda karsiligi bulunan (yani hala aktif olan) FaultEvent id/objeleri.
    # Tur sonunda bu kumede OLMAYAN aktif kayitlar resolved edilir.
    still_active: set[int] = set()
    # id'si olmayan (yeni eklenen) kayitlari da takip etmemiz gerek.
    newly_created: list[FaultEvent] = []

    for line in lines:
        line_devices = devices_per_line.get(line.id, [])
        if not line_devices:
            continue

        zones = _compute_line_zones(
            line_devices, active_alarm_device_ids, poles_by_id, dist_index, line.id
        )
        existing_faults = faults_by_line.get(line.id, [])
        pairs, fresh_zones, orphan_faults = _match_zones_to_faults(zones, existing_faults)

        if len(zones) > 1:
            logger.info(
                "fault_multi_zone line_id=%d zone_count=%d ranges=%s",
                line.id, len(zones), [z.seq_range for z in zones],
            )

        # --- Eslesen bolgeler: mevcut kaydi guncelle ---
        for zone, existing in pairs:
            existing.last_red_device_id = zone.last_red_device_id
            existing.first_green_device_id = zone.first_green_device_id
            existing.from_pole_id = zone.from_pole.id
            existing.to_pole_id = zone.to_pole.id
            existing.from_pole_seq = zone.from_pole.sequence_no
            existing.to_pole_seq = zone.to_pole.sequence_no
            # Tel mesafesi her turda yeniden yazilir — topoloji koordinati
            # duzeltildiginde eski kayit da guncel degere gecer.
            (
                existing.zone_start_m,
                existing.zone_end_m,
                existing.zone_length_m,
            ) = zone.distance_range
            # Ariza tekrar aktif hale geldiyse (resolved iken yeniden alarm)
            # yeniden "open" yapmayiz — operator akisini bozmamak icin
            # mevcut status korunur; sadece resolved ise geri alinir.
            if existing.status == "resolved":
                existing.status = "open"
                existing.resolved_at = None
                logger.info(
                    "fault_reopened fault_id=%d line_id=%d", existing.id, line.id
                )
            still_active.add(existing.id)

        # --- Yeni bolgeler: yeni FaultEvent ac ---
        for zone in fresh_zones:
            last_red_dev = devices_by_id.get(zone.last_red_device_id)
            first_green_dev = (
                devices_by_id.get(zone.first_green_device_id)
                if zone.first_green_device_id
                else None
            )
            from_pole = zone.from_pole
            zone_start_m, zone_end_m, zone_length_m = zone.distance_range
            # Yeni fault olustur — OTOMATIK ATAMA YAPILMAZ. Ariza her zaman
            # "open" olarak acilir; atama, Hat Arizalari sayfasindan manuel
            # yapilir (faults.py assign endpoint). Onceden otomatik operator
            # atanip "assigned" geliyordu; kullanici arizanin once "acik"
            # gorunup sonra elle atanmasini istedi.
            fault = FaultEvent(
                line_id=line.id,
                region_id=line.region_id,
                last_red_device_id=zone.last_red_device_id,
                first_green_device_id=zone.first_green_device_id,
                from_pole_id=zone.from_pole.id,
                to_pole_id=zone.to_pole.id,
                from_pole_seq=zone.from_pole.sequence_no,
                to_pole_seq=zone.to_pole.sequence_no,
                zone_start_m=zone_start_m,
                zone_end_m=zone_end_m,
                zone_length_m=zone_length_m,
                status="open",
                opened_at=now,
                assigned_to_username=None,
                assigned_at=None,
            )
            db.add(fault)
            # ANALIZ ANLIK GORUNTUSU — arizayi tespit eden cihazin o andaki
            # alarm imzasi + olcumleri kaydin KENDISINE yazilir. Ham telemetri
            # 90 gunde dusuyor; ariza analizi ise yillar boyunca anlamli
            # olmali. Hata yutulur: goruntu alinamazsa ariza yine acilir
            # (bkz. fault_snapshot.apply_snapshot).
            from app.services.fault_snapshot import apply_snapshot

            apply_snapshot(db, fault)
            newly_created.append(fault)
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
                        # Tel mesafesi (m) — raporlama/analiz icin.
                        "zone_start_m": zone_start_m,
                        "zone_end_m": zone_end_m,
                        "zone_length_m": zone_length_m,
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

        # --- Karsiligi kalmayan kayitlar: bu hattaki o bolge duzeldi ---
        # (Hattin tamami duzeldiginde zones bos gelir, tum kayitlar orphan
        # olur; kismi duzelmede sadece ilgili bolge orphan olur.)
        # `orphan_faults` degiskenini burada tuketmiyoruz — asagidaki son
        # suzgec `still_active` uzerinden hepsini toplayacak. Yine de burada
        # resolve etmek log sirasini anlasilir kiliyor (hat bazli).
        for orphan in orphan_faults:
            _resolve_fault(db, orphan, line, regions_by_id, now)

    # Son suzgec: bu turda HICBIR bolgeye eslesmemis tum aktif kayitlari
    # resolve et. Bu, dongu icinde orphan olarak zaten resolve edilenleri de
    # kapsar (_resolve_fault idempotent, tekrar olay yazmaz) ve ayrica hic
    # cihazi kalmamis hatlardaki kayitlari da toplar.
    lines_by_id = {l.id: l for l in lines}
    for line_faults in faults_by_line.values():
        for fault in line_faults:
            if fault.id in still_active:
                continue
            _resolve_fault(db, fault, lines_by_id.get(fault.line_id), regions_by_id, now)
    # cagiran fonksiyon commit yapacak

    # Yeni acilan fault'lar icin email dispatch — flush ile id atanmasi gerek.
    # commit cagiran fonksiyonda yapilacak; biz flush ederek id'leri elde
    # ediyoruz. dispatch ic try/except ile sarili, hatalari yutuyor.
    if new_faults_for_dispatch:
        try:
            db.flush()
        except Exception:  # noqa: BLE001
            logger.exception("fault_flush_failed_before_dispatch")
            return

        # SATIR ICI DISPATCH AYNI BAYRAGA BAGLI — alarm yolundaki gibi.
        #
        # YASANAN TUTARSIZLIK: alarm bildirimleri
        # `notification_inline_dispatch_enabled` ile korunuyordu (varsayilan
        # False; dispatch sorumlulugu notification-worker'da). ARIZA
        # bildirimleri ise bu bayragin DISINDA, kosulsuz calisiyordu. Yani
        # "dispatch'i worker'a aldik" korumasi ariza yolunda hic gecerli
        # degildi.
        #
        # Neden onemli: bu cagri SMTP/HTTP yapiyor ve ariza yeniden hesaplama
        # akisinin ICINDE. Yanit vermeyen bir relay ariza motorunu tutuyor ve
        # ariza kaydi COMMIT EDILMEDEN asili kaliyordu — yani bildirim
        # gecikmesi degil, ARIZANIN KENDISININ kaydedilmemesi.
        # (SMTP timeout'u ayrica eklendi; bu bayrak ikinci savunma katmani.)
        if not settings.notification_inline_dispatch_enabled:
            # Ariza kaydi `notified_at=NULL` ile acik kalir = "bildirim
            # bekliyor". Gonderimi notification-worker'in tetikledigi
            # `/internal/notifications/dispatch/{alarm_id}` ucu yapar
            # (bkz. notification_dispatch_service.
            # dispatch_pending_fault_notifications).
            #
            # ESKIDEN BURADA SADECE `return` VARDI ve worker tarafinda ariza
            # yolu YOKTU — yani production varsayilaninda (bayrak False) hat
            # arizasi bildirimi hicbir zaman gonderilmiyordu.
            logger.info(
                "fault_bildirim_bekliyor count=%d — gonderimi notification-worker "
                "tetikleyecek (notification_inline_dispatch_enabled=false)",
                len(new_faults_for_dispatch),
            )
            return

        try:
            from app.services.notification_dispatch_service import (
                dispatch_pending_fault_notifications,
            )
        except Exception:  # noqa: BLE001
            logger.exception("fault_notification_dispatch_import_failed")
            return
        try:
            # `notified_at` damgasini bu fonksiyon basar; boylece worker
            # yolu ayni arizayi ikinci kez gondermez.
            dispatch_pending_fault_notifications(db)
        except Exception:  # noqa: BLE001
            logger.exception("fault_inline_dispatch_failed")
