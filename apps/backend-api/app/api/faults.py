"""Hat Arizalari (Fault) API endpoint'leri.

UI'daki "Hat Arizalari" sayfasi bu uclar uzerinden:
  GET    /faults                   -> liste (status filtresi: open/all)
  GET    /faults/{id}              -> tek ariza detayi
  PATCH  /faults/{id}/assign       -> atanani degistir
  PATCH  /faults/{id}/status       -> status degistir (in_progress, closed)
  PATCH  /faults/{id}/note         -> kisa not guncelle
  GET    /faults/{id}/comments     -> ticket yorumlari
  POST   /faults/{id}/comments     -> yorum/rapor ekle

Yetki:
  - Operator: sadece kendi sorumluluk alanindaki bolge/hatlardaki fault'lari
    gorur (scope_service.get_visible_line_ids).
  - Engineer/Installer: tum fault'lar.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.config import settings
from app.db.session import get_db
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.enums import UserRole
from app.models.fault import FaultComment, FaultEvent
from app.models.grid_topology import Line, Region
from app.models.user import User
from app.schemas.fault import (
    FaultCommentCreate,
    FaultCommentRead,
    FaultEventAssignUpdate,
    FaultEventNoteUpdate,
    FaultEventRead,
    FaultCauseUpdate,
    FaultEventStatusUpdate,
    FaultTriggerAlarm,
)
from app.services.event_service import record_event
from app.services.scope_service import get_visible_line_ids

router = APIRouter(prefix="/faults", tags=["faults"])


class _FaultRefs:
    """Bir ariza listesini serialize etmek icin gereken TUM yan veriler.

    NEDEN: `_serialize_fault` her satir icin 6 ayri sorgu atiyordu (line,
    region, iki device, atanan kullanici, yorum sayisi). Hat Arizalari sayfasi
    `status=all` ile 5 SANIYEDE BIR polling yapiyor; 200 gecmis arizada bu
    5 saniyede 1.200 sorgu demekti — kullanici basina. DB havuzu 30+20.

    Simdi liste basina SABIT 5 sorgu: ilgili id'ler toplanip tek `IN` ile
    cekiliyor, yorum sayilari tek GROUP BY ile geliyor.
    """

    __slots__ = ("lines", "regions", "devices", "users", "comment_counts", "alarms")

    def __init__(self) -> None:
        self.lines: dict[int, Line] = {}
        self.regions: dict[int, Region] = {}
        self.devices: dict[int, Device] = {}
        self.users: dict[str, User] = {}
        self.comment_counts: dict[int, int] = {}
        #: device_id -> o cihazin ACIK alarmlari (en yeni once).
        self.alarms: dict[int, list[AlarmEvent]] = {}


def _load_fault_refs(db: Session, faults: list[FaultEvent]) -> _FaultRefs:
    """Ariza listesi icin yan verileri TOPLU cek (N+1 yerine sabit sorgu)."""
    refs = _FaultRefs()
    if not faults:
        return refs

    line_ids = {f.line_id for f in faults if f.line_id is not None}
    region_ids = {f.region_id for f in faults if f.region_id is not None}
    device_ids = {
        did
        for f in faults
        for did in (f.last_red_device_id, f.first_green_device_id)
        if did is not None
    }
    usernames = {f.assigned_to_username for f in faults if f.assigned_to_username}
    fault_ids = [f.id for f in faults]

    if line_ids:
        refs.lines = {
            row.id: row for row in db.scalars(select(Line).where(Line.id.in_(line_ids))).all()
        }
    if region_ids:
        refs.regions = {
            row.id: row for row in db.scalars(select(Region).where(Region.id.in_(region_ids))).all()
        }
    if device_ids:
        refs.devices = {
            row.id: row for row in db.scalars(select(Device).where(Device.id.in_(device_ids))).all()
        }
    if usernames:
        refs.users = {
            row.username: row
            for row in db.scalars(select(User).where(User.username.in_(usernames))).all()
        }
    # Yorum sayilari: satir basina COUNT yerine tek GROUP BY.
    if fault_ids:
        refs.comment_counts = {
            fid: int(cnt)
            for fid, cnt in db.execute(
                select(FaultComment.fault_id, func.count())
                .where(FaultComment.fault_id.in_(fault_ids))
                .group_by(FaultComment.fault_id)
            )
        }

    # Arizayi doguran alarmlar — yalnizca "gordum" diyen (last_red) cihazlar
    # icin ve yalnizca ACIK olanlar (reset=False). Liste basina TEK sorgu;
    # sayfa 5 sn'de bir polling yaptigi icin satir basina sorgu kabul edilemez.
    red_ids = {f.last_red_device_id for f in faults if f.last_red_device_id is not None}
    if red_ids:
        for row in db.scalars(
            select(AlarmEvent)
            .where(AlarmEvent.device_id.in_(red_ids))
            .where(AlarmEvent.reset.is_(False))
            .order_by(AlarmEvent.created_at.desc())
        ).all():
            refs.alarms.setdefault(row.device_id, []).append(row)
    return refs


def _signal_source(signal_key: str | None) -> str | None:
    """`sat01.current_phase_a` -> `sat01`. Prefix yoksa None.

    Bir SN2 govdesindeki uc sensor (master/sat01/sat02) hattin ayri
    fazlarina takilir; arizanin hangi fazda oldugu bu prefix'ten okunur.
    """
    if not signal_key or "." not in signal_key:
        return None
    return signal_key.split(".", 1)[0].lower()


def _serialize_fault(db: Session, f: FaultEvent, refs: _FaultRefs | None = None) -> FaultEventRead:
    """Tek ariza -> FaultEventRead.

    `refs` verilirse (liste yolu) hicbir ek sorgu atilmaz. Verilmezse (tek
    kayit yolu) yan veriler o kayit icin tek seferlik cekilir.
    """
    if refs is None:
        refs = _load_fault_refs(db, [f])
    line = refs.lines.get(f.line_id) if f.line_id is not None else None
    region = refs.regions.get(f.region_id) if f.region_id is not None else None
    last_red = refs.devices.get(f.last_red_device_id) if f.last_red_device_id else None
    first_green = (
        refs.devices.get(f.first_green_device_id) if f.first_green_device_id else None
    )
    assigned_user = refs.users.get(f.assigned_to_username) if f.assigned_to_username else None
    comment_count = refs.comment_counts.get(f.id, 0)
    triggers = [
        FaultTriggerAlarm(
            id=a.id,
            title=a.title,
            description=a.description or None,
            level=a.level,
            signal_key=a.signal_key,
            signal_source=_signal_source(a.signal_key),
            device_id=a.device_id,
            device_code=last_red.code if last_red else None,
            device_name=last_red.name if last_red else None,
            acknowledged=bool(a.acknowledged),
            created_at=a.created_at,
        )
        for a in refs.alarms.get(f.last_red_device_id, [])
    ]
    return FaultEventRead(
        id=f.id,
        line_id=f.line_id,
        line_name=line.name if line else "",
        region_id=f.region_id,
        region_name=region.name if region else "",
        last_red_device_id=f.last_red_device_id,
        last_red_device_code=last_red.code if last_red else None,
        last_red_device_name=last_red.name if last_red else None,
        first_green_device_id=f.first_green_device_id,
        first_green_device_code=first_green.code if first_green else None,
        first_green_device_name=first_green.name if first_green else None,
        from_pole_id=f.from_pole_id,
        to_pole_id=f.to_pole_id,
        from_pole_seq=f.from_pole_seq,
        to_pole_seq=f.to_pole_seq,
        zone_start_m=f.zone_start_m,
        zone_end_m=f.zone_end_m,
        zone_length_m=f.zone_length_m,
        status=f.status,
        opened_at=f.opened_at,
        resolved_at=f.resolved_at,
        closed_at=f.closed_at,
        note=f.note,
        assigned_to_username=f.assigned_to_username,
        assigned_at=f.assigned_at,
        assigned_to_full_name=assigned_user.full_name if assigned_user else None,
        comment_count=int(comment_count),
        trigger_alarms=triggers,
        # ---- Analiz alanlari ----
        cause_code=f.cause_code,
        cause_detail=f.cause_detail,
        auto_cause_code=f.auto_cause_code,
        fault_kind=f.fault_kind,
        phase=f.phase,
        fault_direction=f.fault_direction,
        trigger_signals=f.trigger_signals,
        fault_current_a=f.fault_current_a,
        load_current_before_a=f.load_current_before_a,
        conductor_temp_c=f.conductor_temp_c,
        momentary_fault_count=f.momentary_fault_count,
        permanent_fault_count=f.permanent_fault_count,
        measured_at=f.measured_at,
    )


@router.get("", response_model=list[FaultEventRead])
def list_faults(
    status_filter: str = Query(default="active", alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """status: 'active' (open|assigned|in_progress|resolved) veya 'all'."""
    stmt = select(FaultEvent).order_by(FaultEvent.opened_at.desc())
    if status_filter == "active":
        # closed olmayan tum kayitlar (open/assigned/in_progress/resolved)
        stmt = stmt.where(FaultEvent.status != "closed")
    elif status_filter == "open":
        stmt = stmt.where(FaultEvent.status.in_(["open", "assigned", "in_progress"]))
    elif status_filter == "closed":
        stmt = stmt.where(FaultEvent.status == "closed")
    # "all" -> filtre yok

    # GOSTERIM GECIKMESI: yeni acilan (henuz "olgunlasmamis") arizalari
    # opened_at + fault_display_delay_sec gecene kadar GIZLE. Boylece
    # haberlesme gecikmesiyle gec gelen alarmlar bu pencere icinde birikip
    # ariza dogru cihaz araligiyla tek seferde gorunur — yanlis konumda
    # gecici ariza gosterilmez. Harita bu filtreden ETKILENMEZ (harita
    # alarmlar uzerinden calisir, faults endpoint'ini kullanmaz).
    #
    # ISTISNA: resolved/closed arizalar gecikme dolmasa bile GORUNUR. Cunku
    # kisa omurlu (test) arizalar 30sn dolmadan normale donebilir; kullanici
    # bunlarin "resetlendi/normale dondu" olarak listede gorunmesini istiyor.
    # NOT: "all" de bu filtreye DAHIL. Frontend "Hat Arizalari" sayfasi tek
    # istekte hem aktif hem gecmis kayitlari cekip iki sekmeye ayiriyor;
    # olgunlasmamis aktif arizalarin o sekmede de gizli kalmasi gerekiyor.
    # resolved/closed her durumda gorunur (asagidaki OR kolu).
    delay = settings.fault_display_delay_sec
    if delay > 0 and status_filter in ("active", "open", "all"):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=delay)
        stmt = stmt.where(
            (FaultEvent.opened_at <= cutoff)
            | (FaultEvent.status.in_(["resolved", "closed"]))
        )

    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None:
        if not line_scope:
            return []
        stmt = stmt.where(FaultEvent.line_id.in_(line_scope))

    rows = list(db.scalars(stmt).all())
    # Yan veriler TOPLU cekilir: liste basina sabit 5 sorgu (satir basina 6 degil).
    refs = _load_fault_refs(db, rows)
    return [_serialize_fault(db, r, refs) for r in rows]


@router.get("/stats")
def fault_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ozet istatistikler — UI'da chip'lerde gosterilmek icin.

    Donus:
      total, open, assigned, in_progress, resolved, closed
      avg_resolution_seconds: kapatilan fault'larin (resolved/closed) ort.
        cozum suresi (saniye). Henuz kapatilmis kayit yoksa null.
      last_30d_count: son 30 gunde acilan fault sayisi.
      resolved_today_count: bugun (yerel gun degil, UTC gun basi) normale
        donen/kapatilan fault sayisi — "Bugun Cozulen" KPI karti icin.

    OLCEK NOTU: onceden TUM FaultEvent satirlari ORM ile cekilip Python'da
    sayiliyordu. Bu uc 30 saniyede bir pollenıyor ve ariza tablosu kalicidir
    (retention yok) — birkac bin kayitta her poll tum tabloyu hidrate ediyordu.
    Simdi iki SQL aggregate sorgusu: satir sayisindan bagimsiz sabit maliyet.
    """
    empty = {
        "total": 0,
        "open": 0,
        "assigned": 0,
        "in_progress": 0,
        "resolved": 0,
        "closed": 0,
        "avg_resolution_seconds": None,
        "last_30d_count": 0,
        "resolved_today_count": 0,
    }
    line_scope = get_visible_line_ids(db, current_user)
    scope_clause = None
    if line_scope is not None:
        if not line_scope:
            return empty
        scope_clause = FaultEvent.line_id.in_(line_scope)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 1) Status bazinda sayim. `total` AYRI hesaplanir: bilinmeyen bir status
    #    degeri varsa (eski/elle girilmis kayit) bes anahtarin toplamina
    #    girmez ama total'a girer — eski davranis buydu, korunuyor.
    status_stmt = select(FaultEvent.status, func.count()).group_by(FaultEvent.status)
    if scope_clause is not None:
        status_stmt = status_stmt.where(scope_clause)
    counts = dict(empty)
    counts["avg_resolution_seconds"] = None
    total = 0
    for status_value, cnt in db.execute(status_stmt):
        cnt = int(cnt)
        total += cnt
        if status_value in ("open", "assigned", "in_progress", "resolved", "closed"):
            counts[status_value] = cnt
    counts["total"] = total

    # 2) Sure/pencere aggregate'leri tek satirda.
    #    `end_at` = closed_at varsa o, yoksa resolved_at (eski COALESCE mantigi).
    end_at = func.coalesce(FaultEvent.closed_at, FaultEvent.resolved_at)
    agg_stmt = select(
        # Ortalama cozum suresi (saniye) — yalnizca kapanmis kayitlar.
        func.avg(
            func.extract("epoch", end_at) - func.extract("epoch", FaultEvent.opened_at)
        ).filter(end_at.isnot(None)),
        func.count().filter(FaultEvent.opened_at >= cutoff),
        func.count().filter(end_at >= today_start),
    )
    if scope_clause is not None:
        agg_stmt = agg_stmt.where(scope_clause)
    avg_res, last_30d_count, resolved_today_count = db.execute(agg_stmt).one()

    counts["avg_resolution_seconds"] = float(avg_res) if avg_res is not None else None
    counts["last_30d_count"] = int(last_30d_count or 0)
    counts["resolved_today_count"] = int(resolved_today_count or 0)
    return counts


@router.get("/{fault_id}", response_model=FaultEventRead)
def get_fault(
    fault_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None and f.line_id not in line_scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu arizaya erisim yetkiniz yok.")
    return _serialize_fault(db, f)


@router.patch("/{fault_id}/assign", response_model=FaultEventRead)
def assign_fault(
    fault_id: int,
    payload: FaultEventAssignUpdate,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    previous_assignee = f.assigned_to_username
    target_username = payload.assigned_to_username or None
    if target_username:
        target = db.scalar(select(User).where(User.username == target_username))
        if target is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Atanan kullanici bulunamadi.")
    f.assigned_to_username = target_username
    f.assigned_at = datetime.now(timezone.utc) if target_username else None
    if target_username and f.status == "open":
        f.status = "assigned"
    record_event(
        db,
        category="fault",
        event_type="fault_assigned",
        severity="info",
        actor_username=current_user.username,
        message=f"Fault assigned: fault {fault_id} -> {target_username or '(none)'}",
        metadata={"fault_id": fault_id, "assigned_to": target_username},
        i18n_key="fault_assigned",
        i18n_params={"fault_id": fault_id, "user": target_username or "—"},
    )
    # Atanan kisi degistiyse (yeni kisi varsa) web bildirim + email gonder.
    # Aynı kisiye yeniden atama bildirim spam'i olusturmasin.
    if target_username and target_username != previous_assignee:
        # Ariza bilgisini topla — bildirim metnini guzellestirir.
        from app.models.grid_topology import Line, Region
        from app.services.notification_service import create_notification
        line_row = db.get(Line, f.line_id) if f.line_id else None
        region_row = db.get(Region, f.region_id) if f.region_id else None
        line_name = line_row.name if line_row else f"#{f.line_id}"
        region_name = region_row.name if region_row else None
        if current_user.username == target_username:
            notif_title = f"Bu arızayı kendi üstünüze aldınız: {line_name}"
            title_i18n_key = "fault_assignment_self"
        else:
            notif_title = f"Size yeni bir arıza atandı: {line_name}"
            title_i18n_key = "fault_assignment_other"
        has_pole = f.from_pole_seq is not None and f.to_pole_seq is not None
        if has_pole and region_name:
            body_i18n_key = "fault_assignment_with_pole_region"
            body_i18n_params = {
                "line": line_name,
                "region": region_name,
                "from": f.from_pole_seq,
                "to": f.to_pole_seq,
            }
        elif has_pole:
            body_i18n_key = "fault_assignment_with_pole"
            body_i18n_params = {
                "line": line_name,
                "from": f.from_pole_seq,
                "to": f.to_pole_seq,
            }
        elif region_name:
            body_i18n_key = "fault_assignment_with_region"
            body_i18n_params = {"line": line_name, "region": region_name}
        else:
            body_i18n_key = "fault_assignment_simple"
            body_i18n_params = {"line": line_name}
        body_text = (
            f"Hat: {line_name}"
            + (f" ({region_name})" if region_name else "")
            + (f", Direk #{f.from_pole_seq} ↔ #{f.to_pole_seq}"
               if has_pole else "")
        )
        try:
            create_notification(
                db,
                recipient_username=target_username,
                category="fault_assignment",
                severity="warning",
                title=notif_title,
                body=body_text,
                actor_username=current_user.username,
                link=f"/faults#fault-{fault_id}",
                metadata={
                    "fault_id": fault_id,
                    "line_id": f.line_id,
                    "line_name": line_name,
                    "region_id": f.region_id,
                    "region_name": region_name,
                    "from_pole_seq": f.from_pole_seq,
                    "to_pole_seq": f.to_pole_seq,
                    "_title_i18n": {"key": title_i18n_key, "params": {"line": line_name}},
                    "_body_i18n": {"key": body_i18n_key, "params": body_i18n_params},
                },
            )
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("fault_assignment_notif_failed")
        # E-posta bildirimi
        try:
            from app.services.alarm_engine_service import _send_assignment_email
            _send_assignment_email(
                db,
                recipient_username=target_username,
                kind="fault",
                title=notif_title,
                description=body_text,
                level="warning",
                actor_username=current_user.username,
                link_path=f"/faults#fault-{fault_id}",
            )
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("fault_assignment_email_failed")
    db.commit()
    db.refresh(f)
    return _serialize_fault(db, f)


@router.get("/causes")
def list_fault_causes():
    """Ariza sebep katalogu — arayuzdeki secim listesi.

    KATALOG TEK KAYNAKTAN gelir (`app/data/fault_causes.py`). Frontend'e
    ayri bir liste gomulseydi ikisi zamanla ayrisir ve arayuzde secilen bir
    kod backend'de taninmaz olurdu.

    Yetki: giris yapmis herkes. Operator de sebep girecegi icin listeyi
    gorebilmeli.
    """
    from app.data.fault_causes import CAUSE_GROUPS, FAULT_CAUSES, FAULT_KINDS, PHASES

    return {
        "causes": [dict(c) for c in FAULT_CAUSES],
        "groups": list(CAUSE_GROUPS),
        "kinds": [
            {"code": k, "label_tr": tr, "label_en": en} for k, tr, en in FAULT_KINDS
        ],
        "phases": sorted(PHASES),
    }


@router.patch("/{fault_id}/cause", response_model=FaultEventRead)
def update_fault_cause(
    fault_id: int,
    payload: FaultCauseUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Saha ekibinin girdigi ariza sebebi.

    DURUMDAN BAGIMSIZ: ekip arizayi kapatirken sebebi bilmeyebilir (kablo
    altyapisi sonradan kazilir) ya da kapattiktan sonra ogrenebilir. Sebebi
    `status` ucuna baglamak yapay kisit dogururdu.

    INSAN ETIKETI KAZANIR: kural onerisi (`auto_cause_code`) ayri kolonda
    durur ve BURADAN EZILMEZ. Ikisini ayri tutmak, kurallarin isabetini
    olcmeyi mumkun kilan tek sey.
    """
    from app.data.fault_causes import CAUSE_CODES, FAULT_KIND_CODES, PHASES

    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    if current_user.role == UserRole.OPERATOR and f.assigned_to_username != current_user.username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu arizaya yetkiniz yok.")

    # Katalogda olmayan kodu REDDET: sessizce kabul etmek, analiz
    # sorgularinda hicbir gruba dusmeyen olu etiketler biriktirirdi.
    kod = (payload.cause_code or "").strip() or None
    if kod is not None and kod not in CAUSE_CODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bilinmeyen ariza sebebi: {kod}",
        )
    tur = (payload.fault_kind or "").strip() or None
    if tur is not None and tur not in FAULT_KIND_CODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Gecersiz ariza turu: {tur}"
        )
    faz = (payload.phase or "").strip().lower() or None
    if faz is not None and faz not in PHASES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Gecersiz faz: {faz}"
        )

    onceki = f.cause_code
    f.cause_code = kod
    f.cause_detail = (payload.cause_detail or "").strip() or None
    # Tur/faz yalnizca GONDERILDIYSE degisir: bunlar cihazdan turetiliyor ve
    # bos gonderimi "sil" saymak, elle duzeltmeyen bir kaydin cihaz verisini
    # silmesine yol acardi.
    if tur is not None:
        f.fault_kind = tur
    if faz is not None:
        f.phase = faz

    record_event(
        db,
        category="fault",
        event_type="fault_cause_set",
        severity="info",
        actor_username=current_user.username,
        message=f"Ariza sebebi: fault {fault_id} -> {kod or '(temizlendi)'}",
        metadata={
            "fault_id": fault_id,
            "cause_code": kod,
            "previous": onceki,
            # Kuralin ne onerdigi de kaydedilir: isabet olcumu gecmise
            # donuk yapilabilsin.
            "auto_cause_code": f.auto_cause_code,
        },
        i18n_key="fault_cause_set",
        i18n_params={"fault_id": fault_id, "cause": kod or "—"},
    )
    db.commit()
    db.refresh(f)
    return _serialize_fault(db, f)


@router.patch("/{fault_id}/status", response_model=FaultEventRead)
def update_fault_status(
    fault_id: int,
    payload: FaultEventStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    # Operator sadece kendine atanmis fault'larin durumunu degistirebilir.
    if current_user.role == UserRole.OPERATOR and f.assigned_to_username != current_user.username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu arizaya yetkiniz yok.")
    new_status = payload.status
    allowed = {"in_progress", "resolved", "closed", "open", "assigned"}
    if new_status not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gecersiz status.")
    f.status = new_status
    if new_status == "resolved" and f.resolved_at is None:
        f.resolved_at = datetime.now(timezone.utc)
    if new_status == "closed":
        f.closed_at = datetime.now(timezone.utc)
        if f.resolved_at is None:
            f.resolved_at = f.closed_at
    record_event(
        db,
        category="fault",
        event_type="fault_status_changed",
        severity="info",
        actor_username=current_user.username,
        message=f"Ariza durumu: fault {fault_id} -> {new_status}",
        metadata={"fault_id": fault_id, "status": new_status},
        i18n_key="fault_status_changed",
        i18n_params={"fault_id": fault_id, "status": new_status},
    )
    db.commit()
    db.refresh(f)
    return _serialize_fault(db, f)


@router.patch("/{fault_id}/note", response_model=FaultEventRead)
def update_fault_note(
    fault_id: int,
    payload: FaultEventNoteUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    if current_user.role == UserRole.OPERATOR and f.assigned_to_username != current_user.username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu arizaya yetkiniz yok.")
    f.note = (payload.note or "").strip() or None
    db.commit()
    db.refresh(f)
    return _serialize_fault(db, f)


@router.get("/{fault_id}/comments", response_model=list[FaultCommentRead])
def list_fault_comments(
    fault_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None and f.line_id not in line_scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Erisim yetkiniz yok.")
    rows = list(
        db.scalars(
            select(FaultComment)
            .where(FaultComment.fault_id == fault_id)
            .order_by(FaultComment.created_at.asc())
        ).all()
    )
    return [FaultCommentRead.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/{fault_id}/comments",
    response_model=FaultCommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_fault_comment(
    fault_id: int,
    payload: FaultCommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None and f.line_id not in line_scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Erisim yetkiniz yok.")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Yorum bos olamaz.")
    comment = FaultComment(
        fault_id=fault_id,
        author_username=current_user.username,
        body=body,
        created_at=datetime.now(timezone.utc),
    )
    db.add(comment)
    record_event(
        db,
        category="fault",
        event_type="fault_comment_added",
        severity="info",
        actor_username=current_user.username,
        message=f"Ariza yorumu eklendi: fault {fault_id}",
        metadata={"fault_id": fault_id},
        i18n_key="fault_comment_added",
        i18n_params={"fault_id": fault_id},
    )
    # Atanan kullanici varsa ve yorum sahibi degilse: web bildirim + email.
    if f.assigned_to_username and f.assigned_to_username != current_user.username:
        from app.models.grid_topology import Line, Region
        from app.services.notification_service import create_notification
        line_row = db.get(Line, f.line_id) if f.line_id else None
        region_row = db.get(Region, f.region_id) if f.region_id else None
        line_name = line_row.name if line_row else f"#{f.line_id}"
        region_name = region_row.name if region_row else None
        notif_title = f"Yeni yorum: {line_name}"
        try:
            create_notification(
                db,
                recipient_username=f.assigned_to_username,
                category="fault_comment",
                severity="info",
                title=notif_title,
                body=body,
                actor_username=current_user.username,
                link=f"/faults#fault-{fault_id}",
                metadata={
                    "fault_id": fault_id,
                    "line_id": f.line_id,
                    "line_name": line_name,
                    "region_id": f.region_id,
                    "region_name": region_name,
                    "_title_i18n": {"key": "fault_comment_new", "params": {"line": line_name}},
                },
            )
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("fault_comment_notif_failed")
        # Email
        try:
            from app.services.alarm_engine_service import _send_assignment_email
            _send_assignment_email(
                db,
                recipient_username=f.assigned_to_username,
                kind="fault",
                title=notif_title,
                description=body,
                level="info",
                actor_username=current_user.username,
                link_path=f"/faults#fault-{fault_id}",
            )
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).exception("fault_comment_email_failed")
    db.commit()
    db.refresh(comment)
    return FaultCommentRead.model_validate(comment, from_attributes=True)
