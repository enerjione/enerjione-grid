"""Sebeke topolojisi (Bolge / Hat / Direk / Segment) CRUD.

Mühendislik > Hat Yönetimi sayfasi bu endpointleri kullanir. Cihaz ekleme
sayfasi tamamen ayri kalir; cihaz-segment baglama bu sayfanin sorumlulugunda.

Yetki:
  - GET: tum kullanicilar (operator dahil — sorumluluk alaninin gerektigi).
  - POST/PATCH/DELETE: ENGINEER + INSTALLER.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
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
    ImportCommitResponse,
    ImportPreviewResponse,
    ImportRowError,
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
    WizardLineRequest,
)
from app.services import grid_import_service
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
            device_id=s.device_id,
            device_position_t=s.device_position_t,
            device_orientation=s.device_orientation,
            created_at=s.created_at,
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
        message=f"Region created: {row.name} ({row.code})",
        metadata={"region_id": row.id},
        i18n_key="region_created",
        i18n_params={"name": row.name, "code": row.code},
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
        message=f"Region updated: {row.name}",
        metadata={"region_id": row.id, "fields": list(changes.keys())},
        i18n_key="region_updated",
        i18n_params={"name": row.name},
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
        message=f"Region deleted: {name} ({code})",
        metadata={"region_id": region_id},
        i18n_key="region_deleted",
        i18n_params={"name": name, "code": code},
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
            device_id=s.device_id,
            device_position_t=s.device_position_t,
            device_orientation=s.device_orientation,
            created_at=s.created_at,
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
        i18n_key="line_created",
        i18n_params={"name": row.name, "code": row.code},
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
    # exclude_unset=True: kullanicinin acik bir sekilde set ettigi alanlar (None
    # dahil) uygulanir, gondermedikleri korunur. exclude_none yanlisti — kullanici
    # bransman'i kaldirmak icin null gonderdiginde es geciliyordu.
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(row, key, value)
    record_event(
        db, category="grid", event_type="line_updated", severity="info",
        actor_username=current_user.username,
        message=f"Line updated: {row.name}",
        metadata={"line_id": row.id, "fields": list(changes.keys())},
        i18n_key="line_updated",
        i18n_params={"name": row.name},
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
        i18n_key="line_deleted",
        i18n_params={"name": name, "code": code},
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

    # Araya direk ekleme: sequence_no zaten kullaniliyorsa, mevcut o seq'li ve
    # sonraki direkleri 1 ileri kaydirip yeni direge yer ac.
    seq = payload.sequence_no
    existing_at_or_after = list(db.scalars(
        select(Pole).where(
            Pole.line_id == payload.line_id,
            Pole.sequence_no >= seq,
        ).order_by(Pole.sequence_no.desc())  # son'dan basa: collision olmasin
    ).all())
    if existing_at_or_after:
        # Once gecici cok yuksek seq'lere kaydir (UNIQUE constraint atlasin),
        # sonra hedef seq+1, seq+2, ... atayalim.
        for idx, p in enumerate(existing_at_or_after):
            p.sequence_no = 100000 + idx  # gecici uniq slot
        db.flush()
        # Geri yaz: en kucuk olanin seq'i 'seq+1' olacak (cunku desc'tan
        # geldigi icin son eleman en kucuk seq'liydi).
        for idx, p in enumerate(reversed(existing_at_or_after)):
            p.sequence_no = seq + idx + 1
        db.flush()

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
        message=f"Pole added: {row.name or '(unnamed)'} (#{row.sequence_no})",
        metadata={"pole_id": row.id, "line_id": row.line_id, "shifted": len(existing_at_or_after)},
        i18n_key="pole_created",
        i18n_params={"name": row.name or "(—)", "seq": row.sequence_no},
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
    moved = "latitude" in changes or "longitude" in changes
    line_id = row.line_id
    for key, value in changes.items():
        setattr(row, key, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sıra numarası çakışıyor.")
    # Direk koordinatlari degistiyse, bu hatta bagli tum cihazlarin konumunu
    # yeniden orta-nokta olarak hesapla.
    if moved:
        _resync_devices_on_line(db, line_id)
    record_event(
        db, category="grid", event_type="pole_updated", severity="info",
        actor_username=current_user.username,
        message=f"Pole updated: #{row.sequence_no}",
        metadata={"pole_id": row.id, "fields": list(changes.keys())},
        i18n_key="pole_updated",
        i18n_params={"seq": row.sequence_no},
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
    db.flush()

    # Renumber: silinen seq'ten sonra gelen tum direkleri 1 azalt.
    # Boylece sequence_no sirali kalir (1, 2, 3, ... gap olmaz).
    # NOT: uq_pole_line_sequence UNIQUE constraint'i nedeniyle once gecici
    # olarak buyuk degerlere kaydirip sonra geri yazmak gerekir; basit
    # yaklasim: silinenden sonra gelenleri tek tek kucult, en yakindan
    # baslayarak (collision olmasin diye).
    affected = list(db.scalars(
        select(Pole).where(
            Pole.line_id == line_id,
            Pole.sequence_no > seq,
        ).order_by(Pole.sequence_no.asc())
    ).all())
    for p in affected:
        p.sequence_no = p.sequence_no - 1
        db.flush()

    record_event(
        db, category="grid", event_type="pole_deleted", severity="warning",
        actor_username=current_user.username,
        message=f"Direk silindi: #{seq}",
        metadata={"pole_id": pole_id, "line_id": line_id, "renumbered": len(affected)},
        i18n_key="pole_deleted",
        i18n_params={"seq": seq},
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
        message=f"Line poles reordered (line {line_id}, {len(payload.items)} poles)",
        metadata={"line_id": line_id, "count": len(payload.items)},
        i18n_key="poles_reordered",
        i18n_params={"line_id": line_id, "count": len(payload.items)},
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
        message=f"Line direction reversed (line {line_id})",
        metadata={"line_id": line_id, "count": n},
        i18n_key="poles_reversed",
        i18n_params={"line_id": line_id},
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
            device_id=s.device_id,
            device_position_t=s.device_position_t,
            device_orientation=s.device_orientation,
            created_at=s.created_at,
            from_pole_seq=pole_seq.get(s.from_pole_id),
            to_pole_seq=pole_seq.get(s.to_pole_id),
            device_code=device_map[s.device_id].code if s.device_id and s.device_id in device_map else None,
            device_name=device_map[s.device_id].name if s.device_id and s.device_id in device_map else None,
        )
        for s in rows
    ]


def _slot_position_for_device(
    from_lat: float, from_lon: float,
    to_lat: float, to_lon: float,
    index: int, total: int,
) -> tuple[float, float]:
    """Bir slot'taki (iki direk arasi) cihazlari, sira ile slot uzerinde esit
    olarak dagit.

    total=1: orta nokta (1/2)
    total=2: 1/3, 2/3
    total=3: 1/4, 2/4, 3/4 ...
    Genel formul: t = (index+1) / (total+1)
    """
    t = (index + 1) / (total + 1)
    lat = from_lat + (to_lat - from_lat) * t
    lon = from_lon + (to_lon - from_lon) * t
    return lat, lon


def _resync_slot(db: Session, from_pole_id: int, to_pole_id: int) -> None:
    """Bir slot'taki (ayni from/to direk ciftine sahip tum) segmentlerin cihaz
    konumlarini sira ile yeniden yaz. Cihaz birden fazla olabilir; her cihaz
    slot uzerinde esit araliklarda dagilir.

    Cagri yerleri:
      - segment olusturma/guncelleme/silme (slot'taki cihaz sayisi degisir)
      - direk tasima (slot'un iki ucu kayar)
    """
    from_pole = db.get(Pole, from_pole_id)
    to_pole = db.get(Pole, to_pole_id)
    if from_pole is None or to_pole is None:
        return
    # Slot'a bagli, cihazi olan tum segmentleri sirayla al.
    # Manuel device_position_t varsa o sirayla, yoksa created_at + id ile
    # deterministik sira (NULL'lar sona koyalim).
    segs = list(db.scalars(
        select(LineSegment).where(
            LineSegment.from_pole_id == from_pole_id,
            LineSegment.to_pole_id == to_pole_id,
            LineSegment.device_id.isnot(None),
        ).order_by(
            LineSegment.device_position_t.asc().nulls_last(),
            LineSegment.created_at,
            LineSegment.id,
        )
    ).all())
    total = len(segs)
    if total == 0:
        return
    devices_by_id = {
        d.id: d for d in db.scalars(
            select(Device).where(Device.id.in_([s.device_id for s in segs]))
        ).all()
    }
    for i, s in enumerate(segs):
        dev = devices_by_id.get(s.device_id) if s.device_id is not None else None
        if dev is None:
            continue
        # Manuel olarak ayarlanmis device_position_t varsa override; yoksa
        # otomatik orta-nokta dagilimi.
        manual_t = s.device_position_t
        if manual_t is not None and 0.0 <= manual_t <= 1.0:
            lat = from_pole.latitude + (to_pole.latitude - from_pole.latitude) * manual_t
            lon = from_pole.longitude + (to_pole.longitude - from_pole.longitude) * manual_t
        else:
            lat, lon = _slot_position_for_device(
                from_pole.latitude, from_pole.longitude,
                to_pole.latitude, to_pole.longitude,
                i, total,
            )
        dev.latitude = lat
        dev.longitude = lon


def _sync_device_position_from_segment(
    db: Session, *, device_id: int | None, from_pole_id: int, to_pole_id: int
) -> None:
    """Bir slot'taki tum cihazlarin konumunu yeniden hesapla.
    Coklu cihaz destegi: ayni iki direk arasinda birden cok cihaz olabilir,
    her biri slot uzerinde esit araliklarla dagilir."""
    _resync_slot(db, from_pole_id, to_pole_id)


def _resync_devices_on_line(db: Session, line_id: int) -> None:
    """Hattaki tum direkler tasinmis olabilir (drag-reorder, reverse, pole patch).
    Tum cihazli segmentleri grupla; her (from, to) slot'unu ayri yeniden hesapla.
    Bu sayede coklu cihaz olan slot'larda da dogru sira+pozisyon olusur."""
    segs = list(db.scalars(
        select(LineSegment).where(
            LineSegment.line_id == line_id,
            LineSegment.device_id.isnot(None),
        )
    ).all())
    if not segs:
        return
    seen_slots: set[tuple[int, int]] = set()
    for s in segs:
        slot_key = (s.from_pole_id, s.to_pole_id)
        if slot_key in seen_slots:
            continue
        seen_slots.add(slot_key)
        _resync_slot(db, s.from_pole_id, s.to_pole_id)


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

    # BRANSMAN BAGLANTISI ISTISNASI
    # -----------------------------
    # Bir bransman kolu AYRI bir hattir (`Line.branched_from_pole_id` ana
    # hattaki dallanma diregini gosterir). Kolun ILK segmenti dogasi geregi
    # iki FARKLI hattin direklerini birlestirir: ana hattaki dallanma diregi
    # -> kolun ilk diregi.
    #
    # "Ikisi de ayni hatta olmali" kurali bu segmenti reddediyordu; sonuc
    # olarak dallanma noktasindaki direge CIHAZ BAGLANAMIYORDU. Oysa saha
    # acisindan en kritik olcum noktalarindan biri tam orasi: dala giden
    # akimi goren cihaz, arizanin ana hatta mi kolda mi oldugunu ayirt eder.
    #
    # Istisna DAR: yalnizca `from_pole`, bu hattin bagli oldugu dallanma
    # diregi ise gecerli. Keyfi iki hat birbirine baglanamaz.
    line = db.get(Line, line_id)
    branch_parent_id = line.branched_from_pole_id if line is not None else None
    from_ok = from_pole.line_id == line_id or (
        branch_parent_id is not None and from_pole.id == branch_parent_id
    )
    if not from_ok or to_pole.line_id != line_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Segment direkleri aynı hatta olmalı (branşman kolunun ilk segmenti hariç).",
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
            detail="Segment kaydedilemedi (cihaz zaten başka bir segmente bağlı olabilir).",
        )
    # Cihaz baglandiysa konumunu segmentin orta noktasina yaz.
    _sync_device_position_from_segment(
        db, device_id=row.device_id,
        from_pole_id=row.from_pole_id, to_pole_id=row.to_pole_id,
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
        i18n_key="segment_created",
        i18n_params={"line_id": row.line_id},
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
    # Cihaz veya direk degisikliginde cihaz konumunu senkronize et.
    _sync_device_position_from_segment(
        db, device_id=row.device_id,
        from_pole_id=row.from_pole_id, to_pole_id=row.to_pole_id,
    )
    record_event(
        db, category="grid", event_type="segment_updated", severity="info",
        actor_username=current_user.username,
        message=f"Line segment updated (segment {row.id})",
        metadata={"segment_id": row.id, "fields": list(changes.keys())},
        i18n_key="segment_updated",
        i18n_params={"segment_id": row.id},
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
    from_id = row.from_pole_id
    to_id = row.to_pole_id
    db.delete(row)
    db.flush()
    # Slot'ta kalan diger cihazlari yeniden dagit (orta noktalari yeniden hesaplansin).
    _resync_slot(db, from_id, to_id)
    record_event(
        db, category="grid", event_type="segment_deleted", severity="warning",
        actor_username=current_user.username,
        message=f"Hat segmenti silindi (segment {segment_id})",
        metadata={"segment_id": segment_id, "line_id": line_id},
        i18n_key="segment_deleted",
        i18n_params={"segment_id": segment_id},
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
        device_id=row.device_id,
        device_position_t=row.device_position_t,
        device_orientation=row.device_orientation,
        created_at=row.created_at,
        from_pole_seq=from_pole.sequence_no if from_pole else None,
        to_pole_seq=to_pole.sequence_no if to_pole else None,
        device_code=device.code if device else None,
        device_name=device.name if device else None,
    )


# ===================== Excel Import (sablon + onizleme + commit) =============

_IMPORT_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


async def _read_xlsx_upload(file: UploadFile) -> bytes:
    """Yuklenen .xlsx dosyasini oku + dogrula (uzanti + boyut). mqtt-cert
    upload pattern'i: kucuk dosya, tumu bellege."""
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yalnız .xlsx dosyası yükleyin.",
        )
    content = await file.read()
    if len(content) > _IMPORT_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Dosya çok büyük (en fazla 2 MB).",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dosya boş.",
        )
    return content


@router.post("/wizard-line", response_model=ImportCommitResponse)
def wizard_line(
    payload: WizardLineRequest,
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    """Soru-cevap sihirbazi: tek hatti (bolge + hat + direk listesi) tek
    istekte kurar. Excel import ile AYNI plan/apply yolundan gecer — segmentler
    otomatik, mevcut hat varsa direkler sonuna eklenir."""
    plan = grid_import_service.plan_for_wizard(
        db,
        region_code=payload.region_code,
        region_name=payload.region_name,
        line_code=payload.line_code,
        line_name=payload.line_name,
        poles=[p.model_dump() for p in payload.poles],
        branch_line_code=payload.branch_line_code,
        branch_pole_seq=payload.branch_pole_seq,
    )
    if plan.errors and not plan.poles:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "; ".join(e.message for e in plan.errors[:5]),
        )
    result = grid_import_service.apply_plan(plan, db)
    record_event(
        db, category="grid", event_type="topology_imported", severity="info",
        actor_username=current_user.username,
        message=(
            f"Hat sihirbazı: {payload.line_code} hattı kuruldu "
            f"({result.poles_created} direk eklendi)."
        ),
        metadata={
            "line_code": payload.line_code,
            "region_code": payload.region_code,
            "poles_created": result.poles_created,
            "errors": len(result.errors),
        },
        i18n_key="topology_imported",
        i18n_params={
            "regions": result.regions_created,
            "lines": result.lines_created,
            "poles": result.poles_created,
        },
    )
    db.commit()
    return ImportCommitResponse(
        regions_created=result.regions_created,
        regions_updated=result.regions_updated,
        lines_created=result.lines_created,
        lines_updated=result.lines_updated,
        poles_created=result.poles_created,
        poles_updated=result.poles_updated,
        segments_created=result.segments_created,
        skipped=result.skipped,
        errors=[ImportRowError(row=e.row, message=e.message) for e in result.errors],
    )


@router.get("/import-template.xlsx")
def download_import_template(
    _: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    """Bos hat topolojisi import sablonu (.xlsx). Cihaz + Direk_Tipi kolonlari
    acilir liste (dropdown). Atanmis cihazlar dropdown'da yer almaz."""
    buf = grid_import_service.build_template_workbook(db)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"hat-topoloji-sablon-{ts}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def import_preview(
    file: UploadFile = File(...),
    _: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    """Onizleme (dry-run): dosyayi parse et, DB'ye YAZMADAN ozet + hatalar."""
    content = await _read_xlsx_upload(file)
    plan = grid_import_service.parse_and_plan(content, db)
    counts = plan.counts()
    return ImportPreviewResponse(
        regions=counts["regions"],
        lines=counts["lines"],
        poles=counts["poles"],
        devices=counts["devices"],
        errors=[ImportRowError(row=e.row, message=e.message) for e in plan.errors],
    )


@router.post("/import/commit", response_model=ImportCommitResponse)
async def import_commit(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(_EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    """Import'i uygula (tek transaction). Onizleme ile ayni dosya tekrar parse
    edilir + apply_plan. Ekle/birlestir: kod varsa guncelle, yoksa yarat."""
    content = await _read_xlsx_upload(file)
    plan = grid_import_service.parse_and_plan(content, db)
    result = grid_import_service.apply_plan(plan, db)
    record_event(
        db, category="grid", event_type="topology_imported", severity="info",
        actor_username=current_user.username,
        message=(
            f"Topoloji içe aktarıldı: {result.regions_created} bölge, "
            f"{result.lines_created} hat, {result.poles_created} direk, "
            f"{result.segments_created} cihaz atandı."
        ),
        metadata={
            "regions_created": result.regions_created,
            "lines_created": result.lines_created,
            "poles_created": result.poles_created,
            "segments_created": result.segments_created,
            "errors": len(result.errors),
        },
        i18n_key="topology_imported",
        i18n_params={
            "regions": result.regions_created,
            "lines": result.lines_created,
            "poles": result.poles_created,
        },
    )
    db.commit()
    return ImportCommitResponse(
        regions_created=result.regions_created,
        regions_updated=result.regions_updated,
        lines_created=result.lines_created,
        lines_updated=result.lines_updated,
        poles_created=result.poles_created,
        poles_updated=result.poles_updated,
        segments_created=result.segments_created,
        skipped=result.skipped,
        errors=[ImportRowError(row=e.row, message=e.message) for e in result.errors],
    )
