"""Sebeke topolojisi (Bolge / Hat / Direk / Segment) CRUD.

Mühendislik > Hat Yönetimi sayfasi bu endpointleri kullanir. Cihaz ekleme
sayfasi tamamen ayri kalir; cihaz-segment baglama bu sayfanin sorumlulugunda.

Yetki:
  - GET: tum kullanicilar (operator dahil — sorumluluk alaninin gerektigi).
  - POST/PATCH/DELETE: ENGINEER + INSTALLER.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.enums import UserRole
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.user import User
from app.schemas.grid_topology import (
    GridSnapshot,
    LineCreate,
    LineDetail,
    LineRead,
    LineSegmentCreate,
    LineSegmentRead,
    LineSegmentUpdate,
    LineUpdate,
    PoleCreate,
    PoleRead,
    PoleReorderRequest,
    PoleUpdate,
    RegionCreate,
    RegionRead,
    RegionUpdate,
)
from app.services.event_service import record_event

router = APIRouter(prefix="/grid", tags=["grid-topology"])

_EDIT_ROLES = [UserRole.ENGINEER, UserRole.INSTALLER]


@router.get("/snapshot", response_model=GridSnapshot)
def grid_snapshot(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Anasayfa haritasi icin tek istekte tum sebeke topolojisi.

    Cok cihazli (600+) sahada bile tek HTTP cagrisinda render verisini doner.
    Operator scope filtrelemesi BU AŞAMADA YAPILMAZ — saha gorseli butun.
    """
    regions = list(db.scalars(select(Region).order_by(Region.name.asc())).all())
    lines = list(db.scalars(select(Line)).all())
    poles = list(db.scalars(select(Pole).order_by(Pole.line_id, Pole.sequence_no)).all())
    segments_raw = list(db.scalars(select(LineSegment)).all())

    # Region: line_count enrich
    line_counts: dict[int, int] = {}
    for line_obj in lines:
        line_counts[line_obj.region_id] = line_counts.get(line_obj.region_id, 0) + 1
    region_reads: list[RegionRead] = []
    for r in regions:
        item = RegionRead.model_validate(r, from_attributes=True)
        region_reads.append(item.model_copy(update={"line_count": line_counts.get(r.id, 0)}))

    # Line: pole_count, segment_count enrich
    pole_count_by_line: dict[int, int] = {}
    seg_count_by_line: dict[int, int] = {}
    for p in poles:
        pole_count_by_line[p.line_id] = pole_count_by_line.get(p.line_id, 0) + 1
    for s in segments_raw:
        seg_count_by_line[s.line_id] = seg_count_by_line.get(s.line_id, 0) + 1
    line_reads: list[LineRead] = []
    for l in lines:
        item = LineRead.model_validate(l, from_attributes=True)
        line_reads.append(item.model_copy(update={
            "pole_count": pole_count_by_line.get(l.id, 0),
            "segment_count": seg_count_by_line.get(l.id, 0),
        }))

    # Segment: device + pole sequence enrichment (tek seferde toplu)
    pole_seq_map: dict[int, int] = {p.id: p.sequence_no for p in poles}
    device_ids = [s.device_id for s in segments_raw if s.device_id is not None]
    device_map: dict[int, Device] = {}
    if device_ids:
        for d in db.scalars(select(Device).where(Device.id.in_(device_ids))).all():
            device_map[d.id] = d
    seg_reads: list[LineSegmentRead] = []
    for s in segments_raw:
        dev = device_map.get(s.device_id) if s.device_id else None
        seg_reads.append(LineSegmentRead(
            id=s.id, line_id=s.line_id,
            from_pole_id=s.from_pole_id, to_pole_id=s.to_pole_id,
            device_id=s.device_id, created_at=s.created_at,
            from_pole_seq=pole_seq_map.get(s.from_pole_id),
            to_pole_seq=pole_seq_map.get(s.to_pole_id),
            device_code=dev.code if dev else None,
            device_name=dev.name if dev else None,
        ))

    pole_reads = [PoleRead.model_validate(p, from_attributes=True) for p in poles]
    return GridSnapshot(
        regions=region_reads, lines=line_reads,
        poles=pole_reads, segments=seg_reads,
    )


# ============= REGIONS =============

@router.get("/regions", response_model=list[RegionRead])
def list_regions(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(select(Region).order_by(Region.name.asc())).all())
    # line_count enjekte
    line_counts: dict[int, int] = dict(
        (rid, int(cnt))
        for rid, cnt in db.execute(
            select(Line.region_id, func.count(Line.id)).group_by(Line.region_id)
        ).all()
    )
    result: list[RegionRead] = []
    for r in rows:
        item = RegionRead.model_validate(r, from_attributes=True)
        result.append(item.model_copy(update={"line_count": line_counts.get(r.id, 0)}))
    return result


@router.post("/regions", response_model=RegionRead, status_code=status.HTTP_201_CREATED)
def create_region(
    payload: RegionCreate,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Region).where(Region.code == payload.code)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bölge kodu zaten kullanılıyor.")
    row = Region(**payload.model_dump())
    db.add(row)
    db.flush()
    record_event(
        db, category="grid", event_type="region_created", severity="info",
        actor_username=current_user.username,
        message=f"Bölge eklendi: {row.name} ({row.code})",
        metadata={"region_id": row.id},
    )
    db.commit()
    db.refresh(row)
    return RegionRead.model_validate(row, from_attributes=True)


@router.patch("/regions/{region_id}", response_model=RegionRead)
def update_region(
    region_id: int,
    payload: RegionUpdate,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(Region, region_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bölge bulunamadı.")
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(row, key, value)
    record_event(
        db, category="grid", event_type="region_updated", severity="info",
        actor_username=current_user.username,
        message=f"Bölge güncellendi: {row.name}",
        metadata={"region_id": row.id, "fields": list(changes.keys())},
    )
    db.commit()
    db.refresh(row)
    return RegionRead.model_validate(row, from_attributes=True)


@router.delete("/regions/{region_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_region(
    region_id: int,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(Region, region_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bölge bulunamadı.")
    name, code = row.name, row.code
    db.delete(row)
    record_event(
        db, category="grid", event_type="region_deleted", severity="warning",
        actor_username=current_user.username,
        message=f"Bölge silindi: {name} ({code})",
        metadata={"region_id": region_id},
    )
    db.commit()
    return None


# ============= LINES =============

@router.get("/lines", response_model=list[LineRead])
def list_lines(
    region_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Line)
    if region_id is not None:
        stmt = stmt.where(Line.region_id == region_id)
    rows = list(db.scalars(stmt.order_by(Line.name.asc())).all())
    pole_counts = _counts_by_line(db, Pole)
    seg_counts = _counts_by_line(db, LineSegment)
    out: list[LineRead] = []
    for r in rows:
        item = LineRead.model_validate(r, from_attributes=True)
        out.append(item.model_copy(update={
            "pole_count": int(pole_counts.get(r.id, 0)),
            "segment_count": int(seg_counts.get(r.id, 0)),
        }))
    return out


def _counts_by_line(db: Session, model) -> dict[int, int]:
    from sqlalchemy import func
    rows = db.execute(
        select(model.line_id, func.count(model.id)).group_by(model.line_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


@router.get("/lines/{line_id}", response_model=LineDetail)
def get_line_detail(
    line_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hat + direkleri (sequence_no sıralı) + segmentler (cihaz adıyla expand edilmiş)."""
    line = db.get(Line, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hat bulunamadı.")
    poles = list(db.scalars(
        select(Pole).where(Pole.line_id == line_id).order_by(Pole.sequence_no.asc())
    ).all())
    pole_seq = {p.id: p.sequence_no for p in poles}
    segments = list(db.scalars(
        select(LineSegment).where(LineSegment.line_id == line_id)
    ).all())
    # Cihaz adi için tek seferde fetch
    device_ids = [s.device_id for s in segments if s.device_id is not None]
    device_map: dict[int, Device] = {}
    if device_ids:
        for d in db.scalars(select(Device).where(Device.id.in_(device_ids))).all():
            device_map[d.id] = d
    seg_reads: list[LineSegmentRead] = []
    for s in segments:
        dev = device_map.get(s.device_id) if s.device_id else None
        seg_reads.append(LineSegmentRead(
            id=s.id, line_id=s.line_id,
            from_pole_id=s.from_pole_id, to_pole_id=s.to_pole_id,
            device_id=s.device_id, created_at=s.created_at,
            from_pole_seq=pole_seq.get(s.from_pole_id),
            to_pole_seq=pole_seq.get(s.to_pole_id),
            device_code=dev.code if dev else None,
            device_name=dev.name if dev else None,
        ))
    line_read = LineRead.model_validate(line, from_attributes=True).model_copy(update={
        "pole_count": len(poles),
        "segment_count": len(segments),
    })
    return LineDetail(
        line=line_read,
        poles=[PoleRead.model_validate(p, from_attributes=True) for p in poles],
        segments=seg_reads,
    )


@router.post("/lines", response_model=LineRead, status_code=status.HTTP_201_CREATED)
def create_line(
    payload: LineCreate,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    if db.get(Region, payload.region_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bölge bulunamadı.")
    try:
        row = Line(**payload.model_dump())
        db.add(row)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bu bölgede aynı kodlu hat zaten var.")
    record_event(
        db, category="grid", event_type="line_created", severity="info",
        actor_username=current_user.username,
        message=f"Hat eklendi: {row.name} ({row.code})",
        metadata={"line_id": row.id, "region_id": row.region_id},
    )
    db.commit()
    db.refresh(row)
    return LineRead.model_validate(row, from_attributes=True)


@router.patch("/lines/{line_id}", response_model=LineRead)
def update_line(
    line_id: int,
    payload: LineUpdate,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(Line, line_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hat bulunamadı.")
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(row, key, value)
    record_event(
        db, category="grid", event_type="line_updated", severity="info",
        actor_username=current_user.username,
        message=f"Hat güncellendi: {row.name}",
        metadata={"line_id": row.id, "fields": list(changes.keys())},
    )
    db.commit()
    db.refresh(row)
    return LineRead.model_validate(row, from_attributes=True)


@router.delete("/lines/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_line(
    line_id: int,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(Line, line_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hat bulunamadı.")
    name, code = row.name, row.code
    db.delete(row)
    record_event(
        db, category="grid", event_type="line_deleted", severity="warning",
        actor_username=current_user.username,
        message=f"Hat silindi: {name} ({code})",
        metadata={"line_id": line_id},
    )
    db.commit()
    return None


# ============= POLES =============

@router.get("/poles", response_model=list[PoleRead])
def list_poles(
    line_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Pole)
    if line_id is not None:
        stmt = stmt.where(Pole.line_id == line_id)
    return list(db.scalars(stmt.order_by(Pole.line_id, Pole.sequence_no)).all())


@router.post("/poles", response_model=PoleRead, status_code=status.HTTP_201_CREATED)
def create_pole(
    payload: PoleCreate,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    line = db.get(Line, payload.line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hat bulunamadı.")
    try:
        row = Pole(**payload.model_dump())
        db.add(row)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bu hatta {payload.sequence_no} sıra numaralı direk zaten var.",
        )
    record_event(
        db, category="grid", event_type="pole_created", severity="info",
        actor_username=current_user.username,
        message=f"Direk eklendi: {row.name or '(adsız)'} (#{row.sequence_no})",
        metadata={"pole_id": row.id, "line_id": row.line_id},
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/poles/{pole_id}", response_model=PoleRead)
def update_pole(
    pole_id: int,
    payload: PoleUpdate,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(Pole, pole_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Direk bulunamadı.")
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(row, key, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sıra numarası çakışıyor.")
    record_event(
        db, category="grid", event_type="pole_updated", severity="info",
        actor_username=current_user.username,
        message=f"Direk güncellendi: #{row.sequence_no}",
        metadata={"pole_id": row.id, "fields": list(changes.keys())},
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/poles/{pole_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pole(
    pole_id: int,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(Pole, pole_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Direk bulunamadı.")
    # Direk silinince ona bağlı segmentler CASCADE ile silinir.
    seq, line_id = row.sequence_no, row.line_id
    db.delete(row)
    record_event(
        db, category="grid", event_type="pole_deleted", severity="warning",
        actor_username=current_user.username,
        message=f"Direk silindi: #{seq}",
        metadata={"pole_id": pole_id, "line_id": line_id},
    )
    db.commit()
    return None


@router.post("/lines/{line_id}/reorder-poles", response_model=list[PoleRead])
def reorder_poles(
    line_id: int,
    payload: PoleReorderRequest,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    """Drag-to-reorder sonrasi tum direklerin sequence_no'larini topluca gunceller.

    Iki gecisli yazim:
      1) Hepsini geçici negatif degerlere tasi (UNIQUE constraint'in iki kayit
         arasinda swap'a engel olmamasi icin).
      2) Hedef pozitif sequence_no'lari yaz.
    """
    if payload.line_id != line_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload.line_id ile URL line_id eslesmiyor.",
        )
    line = db.get(Line, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hat bulunamadı.")
    # DB'deki tüm direkler
    poles_in_db = list(db.scalars(select(Pole).where(Pole.line_id == line_id)).all())
    db_ids = {p.id for p in poles_in_db}
    sent_ids = {item.pole_id for item in payload.items}
    if db_ids != sent_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yeniden siralama icin bu hattaki TUM direkler gonderilmeli.",
        )
    seq_set = {item.sequence_no for item in payload.items}
    if len(seq_set) != len(payload.items):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Sıra numaraları benzersiz olmalı.",
        )

    pole_by_id = {p.id: p for p in poles_in_db}
    # 1) Geçici negatif değerlere taşı
    for idx, item in enumerate(payload.items, start=1):
        pole_by_id[item.pole_id].sequence_no = -idx
    db.flush()
    # 2) Hedef sıralar
    for item in payload.items:
        pole_by_id[item.pole_id].sequence_no = item.sequence_no
    db.flush()
    record_event(
        db, category="grid", event_type="poles_reordered", severity="info",
        actor_username=current_user.username,
        message=f"Hat direkleri yeniden sıralandı (line {line_id}, {len(payload.items)} direk)",
        metadata={"line_id": line_id, "count": len(payload.items)},
    )
    db.commit()
    # Yeni sırayla döndür
    return list(db.scalars(
        select(Pole).where(Pole.line_id == line_id).order_by(Pole.sequence_no)
    ).all())


@router.post("/lines/{line_id}/reverse-poles", response_model=list[PoleRead])
def reverse_poles(
    line_id: int,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    """Hat direklerinin sırasını tersine çevirir (baş ↔ son swap)."""
    line = db.get(Line, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hat bulunamadı.")
    poles = list(db.scalars(
        select(Pole).where(Pole.line_id == line_id).order_by(Pole.sequence_no)
    ).all())
    if len(poles) < 2:
        return poles
    n = len(poles)
    # 1) Önce negatif geçici
    for idx, p in enumerate(poles, start=1):
        p.sequence_no = -idx
    db.flush()
    # 2) Tersine numaralandır
    for idx, p in enumerate(poles):
        p.sequence_no = n - idx
    db.flush()
    record_event(
        db, category="grid", event_type="poles_reversed", severity="info",
        actor_username=current_user.username,
        message=f"Hat sırası tersine çevrildi (line {line_id})",
        metadata={"line_id": line_id, "count": n},
    )
    db.commit()
    return list(db.scalars(
        select(Pole).where(Pole.line_id == line_id).order_by(Pole.sequence_no)
    ).all())


# ============= SEGMENTS =============

@router.get("/segments", response_model=list[LineSegmentRead])
def list_segments(
    line_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(LineSegment)
    if line_id is not None:
        stmt = stmt.where(LineSegment.line_id == line_id)
    rows = list(db.scalars(stmt).all())
    pole_seq: dict[int, int] = {}
    for p in db.scalars(select(Pole)).all():
        pole_seq[p.id] = p.sequence_no
    device_ids = [s.device_id for s in rows if s.device_id is not None]
    device_map: dict[int, Device] = {}
    if device_ids:
        for d in db.scalars(select(Device).where(Device.id.in_(device_ids))).all():
            device_map[d.id] = d
    return [
        LineSegmentRead(
            id=s.id, line_id=s.line_id,
            from_pole_id=s.from_pole_id, to_pole_id=s.to_pole_id,
            device_id=s.device_id, created_at=s.created_at,
            from_pole_seq=pole_seq.get(s.from_pole_id),
            to_pole_seq=pole_seq.get(s.to_pole_id),
            device_code=device_map[s.device_id].code if s.device_id and s.device_id in device_map else None,
            device_name=device_map[s.device_id].name if s.device_id and s.device_id in device_map else None,
        )
        for s in rows
    ]


def _validate_segment_endpoints(db: Session, line_id: int, from_id: int, to_id: int) -> None:
    if from_id == to_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Segmentin başlangıç ve bitiş direkleri aynı olamaz.",
        )
    from_pole = db.get(Pole, from_id)
    to_pole = db.get(Pole, to_id)
    if from_pole is None or to_pole is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Direk bulunamadı.")
    if from_pole.line_id != line_id or to_pole.line_id != line_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Segment direkleri aynı hatta olmalı.",
        )


@router.post("/segments", response_model=LineSegmentRead, status_code=status.HTTP_201_CREATED)
def create_segment(
    payload: LineSegmentCreate,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    _validate_segment_endpoints(db, payload.line_id, payload.from_pole_id, payload.to_pole_id)
    if payload.device_id is not None:
        device = db.get(Device, payload.device_id)
        if device is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cihaz bulunamadı.")
        existing = db.scalar(
            select(LineSegment).where(LineSegment.device_id == payload.device_id)
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bu cihaz zaten başka bir segmente bağlı.",
            )
    try:
        row = LineSegment(**payload.model_dump())
        db.add(row)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aynı (başlangıç, bitiş) çiftine sahip segment zaten var.",
        )
    record_event(
        db, category="grid", event_type="segment_created", severity="info",
        actor_username=current_user.username,
        message=f"Hat segmenti eklendi (line {row.line_id})",
        metadata={
            "segment_id": row.id, "line_id": row.line_id,
            "from_pole_id": row.from_pole_id, "to_pole_id": row.to_pole_id,
            "device_id": row.device_id,
        },
    )
    db.commit()
    db.refresh(row)
    return _segment_to_read(db, row)


@router.patch("/segments/{segment_id}", response_model=LineSegmentRead)
def update_segment(
    segment_id: int,
    payload: LineSegmentUpdate,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(LineSegment, segment_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment bulunamadı.")
    changes = payload.model_dump(exclude_unset=True)
    new_from = changes.get("from_pole_id", row.from_pole_id)
    new_to = changes.get("to_pole_id", row.to_pole_id)
    if "from_pole_id" in changes or "to_pole_id" in changes:
        _validate_segment_endpoints(db, row.line_id, new_from, new_to)
    if "device_id" in changes and changes["device_id"] is not None:
        new_device_id = changes["device_id"]
        if db.get(Device, new_device_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cihaz bulunamadı.")
        conflict = db.scalar(
            select(LineSegment).where(
                LineSegment.device_id == new_device_id,
                LineSegment.id != segment_id,
            )
        )
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bu cihaz zaten başka bir segmente bağlı.",
            )
    for key, value in changes.items():
        setattr(row, key, value)
    record_event(
        db, category="grid", event_type="segment_updated", severity="info",
        actor_username=current_user.username,
        message=f"Hat segmenti güncellendi (segment {row.id})",
        metadata={"segment_id": row.id, "fields": list(changes.keys())},
    )
    db.commit()
    db.refresh(row)
    return _segment_to_read(db, row)


@router.delete("/segments/{segment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_segment(
    segment_id: int,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.get(LineSegment, segment_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment bulunamadı.")
    line_id = row.line_id
    db.delete(row)
    record_event(
        db, category="grid", event_type="segment_deleted", severity="warning",
        actor_username=current_user.username,
        message=f"Hat segmenti silindi (segment {segment_id})",
        metadata={"segment_id": segment_id, "line_id": line_id},
    )
    db.commit()
    return None


def _segment_to_read(db: Session, row: LineSegment) -> LineSegmentRead:
    from_pole = db.get(Pole, row.from_pole_id)
    to_pole = db.get(Pole, row.to_pole_id)
    device = db.get(Device, row.device_id) if row.device_id else None
    return LineSegmentRead(
        id=row.id, line_id=row.line_id,
        from_pole_id=row.from_pole_id, to_pole_id=row.to_pole_id,
        device_id=row.device_id, created_at=row.created_at,
        from_pole_seq=from_pole.sequence_no if from_pole else None,
        to_pole_seq=to_pole.sequence_no if to_pole else None,
        device_code=device.code if device else None,
        device_name=device.name if device else None,
    )
