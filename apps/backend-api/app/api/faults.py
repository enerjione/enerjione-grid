"""Hat Arizalari (Fault) API endpoint'leri.

UI'daki "Hat Arizalari" sayfasi bu uclar uzerinden:
  GET    /faults                   -> liste (status filtresi: open/all)
  GET    /faults/{id}              -> tek ariza detayi
  PATCH  /faults/{id}/assign       -> atanani degistir
  PATCH  /faults/{id}/status       -> status degistir (in_progress, closed)
  PATCH  /faults/{id}/note         -> kisa not guncelle
  GET    /faults/{id}/comments     -> ticket yorumlari
  POST   /faults/{id}/comments     -> yorum/rapor ekle
  GET    /faults/{id}/report.pdf   -> tek dosyalik ariza raporu (A4, haritali)

Yetki:
  - Operator: sadece kendi sorumluluk alanindaki bolge/hatlardaki fault'lari
    gorur (scope_service.get_visible_line_ids).
  - Engineer/Installer: tum fault'lar.

KAPATILMIS ARIZA SALT OKUNURDUR
-------------------------------
`closed` bir kayit ARSIVDIR: raporu her zaman alinir ama icerigi degismez.
Yorum eklemek ya da kisa notu duzeltmek, arsivlenmis kapanis raporunun
sonradan sessizce degismesi demek olurdu — asil olayin uzerinden aylar gecmis
olabilir ve raporu okuyan kisi degisimden haberdar olmaz. Bu yuzden
`POST /comments` ve `PATCH /note` kapali kayitta 409 doner.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.config import settings
from app.db.session import get_db
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.enums import UserRole
from app.models.fault import FaultComment, FaultEvent
from app.models.grid_topology import Line, Pole, Region
from app.models.user import User
from app.schemas.fault import (
    FaultBranchRef,
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

#: "Arizayi acan alarm" penceresinin tolerans payi.
#
#: Alarm ile ariza kaydi ayni olayin iki yuzu ama ayni anda dogmuyorlar:
#: alarm sinyal geldigi anda, ariza kaydi ise kirmizi/yesil cihaz dizilimi
#: cozuldukten sonra yazilir. Aradaki fark saniyeler mertebesinde; pay
#: olmadan aciliste bir kac saniye once dusen alarm pencerenin disinda
#: kalirdi. Ust sinirda da ayni pay var: ariza normale dondukten hemen
#: sonra gelen "reset" alarmi hala O arizanin kaydidir.
_ALARM_PENCERE = timedelta(minutes=10)


def _utc(dt: datetime | None) -> datetime | None:
    """Naive damgayi UTC kabul eder — SQLite testlerinde tzinfo geri gelmez
    ve ciplak karsilastirma patlar (bkz. fault_recompute_service._yas_sn)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _device_scope_from_lines(db: Session, line_scope) -> set[int] | None:  # noqa: ANN001
    """Hat kapsamini CIHAZ kapsamina cevirir.

    Alarm ve olcum verisi CIHAZA baglidir, hatta degil; baglanti
    `line_segments` uzerinden kurulur (cihaz bir segmentin uzerinde oturur).
    Bu cevrimi atlayip hat filtresi uygulamak, sorguyu hic filtrelememek
    anlamina gelirdi.

    None = sinir yok (engineer/installer). Bos kume = hicbir cihaz.
    """
    from app.models.grid_topology import LineSegment

    if line_scope is None:
        return None
    if not line_scope:
        return set()
    return {
        row[0]
        for row in db.execute(
            select(LineSegment.device_id)
            .where(LineSegment.line_id.in_(line_scope))
            .where(LineSegment.device_id.is_not(None))
        ).all()
    }


class _FaultRefs:
    """Bir ariza listesini serialize etmek icin gereken TUM yan veriler.

    NEDEN: `_serialize_fault` her satir icin 6 ayri sorgu atiyordu (line,
    region, iki device, atanan kullanici, yorum sayisi). Hat Arizalari sayfasi
    `status=all` ile 5 SANIYEDE BIR polling yapiyor; 200 gecmis arizada bu
    5 saniyede 1.200 sorgu demekti — kullanici basina. DB havuzu 30+20.

    Simdi liste basina SABIT 5 sorgu: ilgili id'ler toplanip tek `IN` ile
    cekiliyor, yorum sayilari tek GROUP BY ile geliyor.
    """

    __slots__ = (
        "lines",
        "regions",
        "devices",
        "users",
        "areas",
        "comment_counts",
        "alarms",
        "branch_lines",
        "poles",
        "lines_with_open_fault",
    )

    def __init__(self) -> None:
        self.lines: dict[int, Line] = {}
        self.regions: dict[int, Region] = {}
        self.devices: dict[int, Device] = {}
        self.users: dict[str, User] = {}
        #: EKIBE ATANMIS arizalar icin: area_id -> ekip adi.
        self.areas: dict[int, str] = {}
        self.comment_counts: dict[int, int] = {}
        #: device_id -> o cihazin ACIK alarmlari (en yeni once).
        self.alarms: dict[int, list[AlarmEvent]] = {}
        #: dallanma diregi id -> o direkten cikan bransman kollari.
        self.branch_lines: dict[int, list[Line]] = {}
        #: Ariza araligindaki dallanma direklerini bulmak icin: pole.id -> Pole
        self.poles: dict[int, Pole] = {}
        #: Kolda kendi ariza kaydi var mi (arayuz "dogrulandi" der).
        self.lines_with_open_fault: set[int] = set()


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
    # Ekibe atanmis arizalarin ekip ADI — liste basina TEK sorgu.
    area_ids = {f.assigned_to_area_id for f in faults if f.assigned_to_area_id}
    if area_ids:
        from app.models.responsibility_area import ResponsibilityArea

        refs.areas = {
            aid: ad
            for aid, ad in db.execute(
                select(ResponsibilityArea.id, ResponsibilityArea.name).where(
                    ResponsibilityArea.id.in_(area_ids)
                )
            ).all()
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
    # icin. Liste basina TEK sorgu; sayfa 5 sn'de bir polling yaptigi icin
    # satir basina sorgu kabul edilemez.
    #
    # NORMALE DONEN ALARM DA KALIR (`reset=True`).
    # Onceden sorgu `reset.is_(False)` ile yalnizca ACIK alarmlari aliyordu:
    # ariza hala ekranda dururken onu ACAN alarm normale doner donmez blok
    # bosaliyor ve kart "arizayi acan alarm normale donmus" demekten baska
    # bir sey soyleyemiyordu. Oysa sorulan sey "su an alarm var mi" degil,
    # "bu arizayi NE acti" — o kayit alarm sifirlansa da gecerlidir.
    #
    # Filtre kalkinca cihazin TUM alarm gecmisi gelmesin diye pencere
    # zaman ile sinirlaniyor: en eski arizanin acilisindan `_ALARM_PENCERE`
    # kadar oncesi. Alt sinir burada (sorguda), ust sinir kayit basina
    # `_trigger_alarms`'ta — cihaz ayni yerde birden fazla kez arizalanabilir
    # ve eski arizanin alarmi yenisinin kartina dusmemeli.
    red_ids = {f.last_red_device_id for f in faults if f.last_red_device_id is not None}
    if red_ids:
        en_eski = min(
            _utc(f.opened_at) for f in faults if f.last_red_device_id is not None
        )
        for row in db.scalars(
            select(AlarmEvent)
            .where(AlarmEvent.device_id.in_(red_ids))
            .where(AlarmEvent.created_at >= en_eski - _ALARM_PENCERE)
            .order_by(AlarmEvent.created_at.desc())
        ).all():
            refs.alarms.setdefault(row.device_id, []).append(row)

    # --- BRANSMAN KOLLARI -------------------------------------------------
    # Hat tek bir zincir degil: dallanma direklerine bagli kollar var ve her
    # kol AYRI bir Line. Ariza araligi bir dallanma diregini kapsiyorsa o kol
    # da enerjisiz kalir — ekip sahaya ciktiginda kolu da kontrol etmelidir.
    # Bu bilgi hicbir yerde gorunmuyordu.
    #
    # Uc sorgu, liste basina SABIT (satir basina degil): kollar, ilgili
    # hatlarin direkleri ve kollarin kendi acik ariza kayitlari.
    branch_rows = list(
        db.scalars(select(Line).where(Line.branched_from_pole_id.isnot(None))).all()
    )
    for row in branch_rows:
        refs.branch_lines.setdefault(row.branched_from_pole_id, []).append(row)
        # Kolun adi basligta gerekiyor; lines sozlugune de girsin.
        refs.lines.setdefault(row.id, row)
    if branch_rows:
        # Kolun basliginda ANA HAT adi da gerekiyor ("ANA HAT > BR-1 kolu").
        # Dallanma diregi -> onun hatti = ana hat.
        branch_parent_poles = list(
            db.scalars(
                select(Pole).where(
                    Pole.id.in_({r.branched_from_pole_id for r in branch_rows})
                )
            ).all()
        )
        for p in branch_parent_poles:
            refs.poles[p.id] = p
        parent_line_ids = {p.line_id for p in branch_parent_poles}
        if parent_line_ids:
            for row in db.scalars(select(Line).where(Line.id.in_(parent_line_ids))).all():
                refs.lines.setdefault(row.id, row)

    # Ariza araligindaki dallanma direklerini bulabilmek icin ilgili
    # hatlarin TUM direkleri (sequence_no karsilastirmasi yapilacak).
    if line_ids:
        for row in db.scalars(select(Pole).where(Pole.line_id.in_(line_ids))).all():
            refs.poles[row.id] = row

    refs.lines_with_open_fault = {
        lid
        for (lid,) in db.execute(
            select(FaultEvent.line_id).where(FaultEvent.status != "closed").distinct()
        ).all()
    }
    return refs


def _signal_source(signal_key: str | None) -> str | None:
    """`sat01.current_phase_a` -> `sat01`. Prefix yoksa None.

    Bir SN2 govdesindeki uc sensor (master/sat01/sat02) hattin ayri
    fazlarina takilir; arizanin hangi fazda oldugu bu prefix'ten okunur.
    """
    if not signal_key or "." not in signal_key:
        return None
    return signal_key.split(".", 1)[0].lower()


def _trigger_alarms(
    f: FaultEvent, refs: "_FaultRefs", last_red: Device | None
) -> list[FaultTriggerAlarm]:
    """Bu arizayi acan alarmlar — normale donmus olanlar DAHIL.

    Kayit alarmin o anki durumuna degil, arizanin ZAMAN PENCERESINE gore
    secilir: alarm sifirlansa bile "bu arizayi ne acti" sorusunun cevabi
    degismez. Ust sinir sart — ayni cihaz aylar icinde defalarca arizalanir
    ve eski arizanin alarmi yeni kaydin kartinda gorunmemeli.
    """
    bitis = _utc(f.closed_at or f.resolved_at)
    alt = _utc(f.opened_at) - _ALARM_PENCERE
    ust = bitis + _ALARM_PENCERE if bitis else None
    secilen: list[FaultTriggerAlarm] = []
    for a in refs.alarms.get(f.last_red_device_id, []):
        dogus = _utc(a.created_at)
        if dogus is None or dogus < alt:
            continue
        if ust is not None and dogus > ust:
            continue
        secilen.append(
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
                # Alarm normale dondu mu — arayuz kaydi gostermeye devam
                # eder ama "normale dondu" damgasiyla.
                reset=bool(a.reset),
                reset_at=a.reset_at,
                created_at=a.created_at,
            )
        )
    return secilen


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
    triggers = _trigger_alarms(f, refs, last_red)

    # --- Ariza araliginin ICINDE kalan bransman kollari -------------------
    # Aralik direk sira numarasiyla ifade edilir; aradaki her direk icin
    # "bu direkten cikan kol var mi" diye bakariz.
    #
    # SINIR DIREKLERI HARIC (alt < seq < ust).
    # `from_pole_seq`/`to_pole_seq` bolgeyi CEVRELEYEN direklerdir, icindekiler
    # degil: alt sinir son "gordum" cihazindan ONCEKI, ust sinir ilk
    # "gormedim" cihazindan SONRAKI direktir. Ikisi de arizanin saglam
    # tarafinda kalir — ust sinirdaki direge asili kol, arizayi gormeyen
    # cihazin otesinden beslenir, yani enerjisi vardir.
    #
    # Kapsayici (<=) karsilastirma bu iki direge asili kollari "kontrol edin"
    # diye listeliyordu: haritada yemyesil duran bir kol icin ekip sahaya
    # cikiyordu.
    affected: list[FaultBranchRef] = []
    if f.from_pole_seq is not None and f.to_pole_seq is not None and refs.branch_lines:
        alt, ust = sorted((f.from_pole_seq, f.to_pole_seq))
        for pole in refs.poles.values():
            if pole.line_id != f.line_id:
                continue
            if not (alt < pole.sequence_no < ust):
                continue
            for kol in refs.branch_lines.get(pole.id, []):
                affected.append(
                    FaultBranchRef(
                        line_id=kol.id,
                        line_name=kol.name,
                        branch_pole_seq=pole.sequence_no,
                        branch_pole_name=pole.name,
                        has_own_fault=kol.id in refs.lines_with_open_fault,
                    )
                )
        affected.sort(key=lambda b: (b.branch_pole_seq or 0, b.line_name))

    # --- Bu kaydin KENDISI bir kolda mi? ---------------------------------
    is_branch = line is not None and line.branched_from_pole_id is not None
    parent_line_id: int | None = None
    parent_line_name: str | None = None
    if is_branch and line is not None:
        parent_pole = refs.poles.get(line.branched_from_pole_id)
        if parent_pole is not None:
            parent_line_id = parent_pole.line_id
            parent = refs.lines.get(parent_pole.line_id)
            parent_line_name = parent.name if parent else None

    # BAGLANTI TELI: araligin baslangic diregi BASKA bir hatta ise, bu
    # aralik iki hat noktasini birlestiren tekil teldir — bir hattin
    # icindeki ardisik iki direk degil. Iki farkli numaralandirma tek
    # aralikta karisir; arayuz bunu aralik gibi gostermemeli.
    from_pole = refs.poles.get(f.from_pole_id)
    is_link_span = from_pole is not None and from_pole.line_id != f.line_id
    from_pole_line_name = None
    if is_link_span and from_pole is not None:
        kaynak = refs.lines.get(from_pole.line_id)
        from_pole_line_name = kaynak.name if kaynak else None

    return FaultEventRead(
        is_link_span=is_link_span,
        from_pole_line_name=from_pole_line_name,
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
        zone_code=f.zone_code,
        status=f.status,
        opened_at=f.opened_at,
        resolved_at=f.resolved_at,
        closed_at=f.closed_at,
        note=f.note,
        resolution_note=f.resolution_note,
        assigned_to_username=f.assigned_to_username,
        assigned_at=f.assigned_at,
        assigned_to_full_name=assigned_user.full_name if assigned_user else None,
        # Bas harf rozeti yerine YUZ: sahayla telefonda konusan kisi "kim
        # gidiyor" sorusunu isimden once yuzden cevapliyor. Yoksa arayuz
        # bas harflere duser.
        assigned_to_avatar_url=assigned_user.avatar_url if assigned_user else None,
        assigned_to_area_id=f.assigned_to_area_id,
        assigned_to_area_name=(
            refs.areas.get(f.assigned_to_area_id) if f.assigned_to_area_id else None
        ),
        comment_count=int(comment_count),
        trigger_alarms=triggers,
        affected_branches=affected,
        is_branch_line=bool(is_branch),
        parent_line_id=parent_line_id,
        parent_line_name=parent_line_name,
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


# --- SABIT YOLLAR ---
#
# `/analytics` ve `/causes`, `/{fault_id}` deseninden ONCE tanimli olmak
# ZORUNDA. FastAPI yollari SIRAYLA eslestirir; parametreli desen once
# gelirse `GET /faults/analytics` istegi `/{fault_id}` ile eslesir ve
# "analytics" tam sayiya cevrilmeye calisilir:
#   422 — "fault_id: Input should be a valid integer"
# Ariza Analizi sayfasi bu yuzden tamamen bos aciliyordu.
# Yeni sabit yol eklerken de bu blogun ICINE koyun.

@router.get("/analytics")
def fault_analytics(
    days: int = Query(default=365, ge=1, le=3650),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ariza analizi — hangi hat, hangi bolge, hangi sebep, ne kadar surede.

    TEK UC: ekran alti ayri istek atsaydi hepsi ayni pencereyi ve ayni
    kapsami tekrar tekrar hesaplardi; ustelik biri hata verince ekranin bir
    parcasi sessizce bos kalirdi.

    KAPSAM: operator yalnizca sorumluluk alanindaki hatlarin arizalarini
    sayar. Analiz ekrani "tum sahanin ozeti" gibi durdugu icin kapsami
    unutmak, gormemesi gereken hatlari toplam sayilar icinde gizlenmis
    halde sizdirmak olurdu.
    """
    from app.services import fault_analytics_service as analiz

    line_scope = get_visible_line_ids(db, current_user)
    return analiz.tum_analiz(
        db,
        days=days,
        visible_line_ids=set(line_scope) if line_scope is not None else None,
    )


@router.get("/system-health")
def fault_system_health(
    days: int = Query(default=365, ge=1, le=3650),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sistem sagligi: alarm sikligi + haberlesme kararliligi.

    Ariza analizi "sahada ne oldu" der; burasi "SISTEM kendisi nasil
    davraniyor". Ikisi ayri kararlar urettirir:
      * Cok tetikleyip hic onaylanmayan bir kural -> esik yanlis; operator
        onu gormezden gelmeye baslar ve GERCEK alarmi kacirir.
      * Gunde onlarca kez kopan bir cihaz -> sorun arizada degil o
        cihazda/modemde. Tek tek alarmlara bakan biri bunu fark edemez.

    KAPSAM: operator yalnizca gorunur hatlarindaki CIHAZLARIN alarmlarini
    sayar (ariza analiziyle ayni kural, cihaz duzeyinde).
    """
    from app.services import fault_analytics_service as analiz

    line_scope = get_visible_line_ids(db, current_user)
    device_scope = _device_scope_from_lines(db, line_scope)
    return analiz.sistem_sagligi(db, days=days, visible_device_ids=device_scope)


@router.get("/device-health")
def fault_device_health(
    days: int = Query(default=90, ge=1, le=1095),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cihaz sagligi: batarya tukenmesi, sinyal kalitesi, ariza yogunlugu.

    Olcum zaman serisinden (`telemetry_history_1h`, 2 yil) turetilir. Ozet
    tablo yoksa (Timescale'siz dev kurulumu) ilgili bolumler BOS doner;
    arayuz "veri yok" gosterir, ekran patlamaz.

    Varsayilan pencere 90 gun: batarya egilimi ve sinyal deseni icin yeterli,
    365 gunluk tarama ise saatlik kovada gereksiz agir.
    """
    from app.models.project_settings import ProjectSettings
    from app.services import device_health_analytics as saglik
    from app.services import fault_analytics_service as analiz

    line_scope = get_visible_line_ids(db, current_user)
    device_scope = _device_scope_from_lines(db, line_scope)
    lines = set(line_scope) if line_scope is not None else None

    # Batarya esigi kuruluma gore degisir (Proje Ayarlari); sabit varsaymak
    # "kac gun kaldi" tahminini yanlis kalibre ederdi.
    proj = db.get(ProjectSettings, 1)
    esik = getattr(proj, "battery_voltage_low", None) if proj else None

    # Alarm/ariza sayilari BIR KEZ cekilir ve hem karsilastirma tablosunu hem
    # de listeleri besler. Karsilastirma icinde ayrica cekilseydi ayni pencere
    # iki kez taranir, ustelik iki sonuc zamanla ayrisabilirdi.
    alarm_sayilari = analiz.cihaz_alarm_sayilari(
        db, days=days, visible_device_ids=device_scope
    )
    ariza_sayilari = analiz.cihaz_ariza_sayilari(db, days=days, visible_line_ids=lines)

    return {
        "window_days": days,
        "battery_drain": saglik.batarya_tukenme(
            db, days=days, visible_device_ids=device_scope, battery_low=esik
        ),
        "weak_signal": saglik.sinyal_kalitesi(
            db, days=days, visible_device_ids=device_scope
        ),
        "signal_by_hour": saglik.sinyal_saat_profili(
            db, days=days, visible_device_ids=device_scope
        ),
        # --- Sistem sagligindan BURAYA tasindi ---
        # Ikisi de CIHAZ duzeyinde sorular ve cihaz karsilastirmasinin
        # yaninda okunmali, ayri bir sekmede degil.
        #
        # Cihaz x zaman matrisi BURADA DEGIL: o bir YOGUNLUK kesiti ve
        # "Hat Ariza Yogunlugu" sekmesinde takvimle ayni anahtarin altinda
        # duruyor; ikisi de `/faults/system-health` yanitindan geliyor.
        "top_rules": analiz.alarm_sikligi(
            db, days=days, visible_device_ids=device_scope
        ),
        "flapping_devices": analiz.haberlesme_kararsizligi(
            db, days=days, visible_device_ids=device_scope
        ),
        # --- Filo karsilastirmasi (yeni) ---
        "comm_status": analiz.haberlesme_durumu_dagilimi(
            db, visible_device_ids=device_scope
        ),
        "device_comparison": saglik.cihaz_karsilastirmasi(
            db,
            days=days,
            visible_device_ids=device_scope,
            alarm_sayilari=alarm_sayilari,
            ariza_sayilari=ariza_sayilari,
            battery_low=esik,
        ),
    }


@router.get("/causes")
def list_fault_causes(_: User = Depends(get_current_user)):
    """Ariza sebep katalogu — arayuzdeki secim listesi.

    KIMLIK DOGRULAMASI: bu uc `Depends(get_current_user)` ALMIYORDU, yani
    halka aciksti. Fark edilmemesinin sebebi baska bir hataydi: `/causes`
    `/{fault_id}` deseninin ARKASINDA kaldigi icin istek hic buraya
    ulasmiyor, parametreli uce dusup onun auth'una takiliyordu. Yol sirasi
    duzeltilince gercek durum ortaya cikti — bir hata digerini
    maskeliyordu.

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


#: Tekrar penceresi — frontend'deki buildFaultRecurrence ile AYNI (90 gun).
_RECURRENCE_WINDOW_DAYS = 90


@router.get("/{fault_id}/report.pdf")
def fault_report_pdf(
    fault_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Arizanin tek dosyalik PDF raporu (A4, uydu haritali).

    NEDEN SUNUCUDA: eskiden arayuz `window.print()` cagiriyordu; cikan sey
    belge degil EKRANIN kagida dokulmus haliydi (tarayici ustbilgisi, bolunmus
    kartlar) ve KAPALI arizada harita bembeyaz cikiyordu — kirmizi bolge canli
    alarm durumundan turetiliyor, alarm resetlenince kayboluyor. Rapor artik
    KAYITTAN uretilir. Ayrintili gerekce: `services/fault_report_service.py`.

    Yetki: `GET /{fault_id}` ile ayni — operator yalnizca kendi kapsamindaki
    hattin arizasini alabilir. Rapor musteri logosu ve saha notlari tasidigi
    icin kapsam disina sizmasi kabul edilemez.
    """
    from app.models.project_settings import ProjectSettings
    from app.services.fault_report_map import collect_fault_geometry, render_fault_map
    from app.services.fault_report_service import (
        ReportPole,
        ReportRecurrence,
        build_fault_report_pdf,
    )

    f = db.get(FaultEvent, fault_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ariza bulunamadi.")
    line_scope = get_visible_line_ids(db, current_user)
    if line_scope is not None and f.line_id not in line_scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Bu arizaya erisim yetkiniz yok."
        )

    fault_read = _serialize_fault(db, f)
    comments = list(
        db.scalars(
            select(FaultComment)
            .where(FaultComment.fault_id == fault_id)
            .order_by(FaultComment.created_at)
        ).all()
    )

    # --- Ariza araligindaki direkler (koordinatli) -------------------------
    zone_poles: list[ReportPole] = []
    if f.line_id is not None and f.from_pole_seq is not None and f.to_pole_seq is not None:
        low, high = sorted((f.from_pole_seq, f.to_pole_seq))
        for pole in db.scalars(
            select(Pole)
            .where(Pole.line_id == f.line_id)
            .where(Pole.sequence_no >= low)
            .where(Pole.sequence_no <= high)
            .order_by(Pole.sequence_no)
        ).all():
            role = (
                "start"
                if pole.id == f.from_pole_id
                else "end"
                if pole.id == f.to_pole_id
                else "zone"
            )
            zone_poles.append(
                ReportPole(
                    sequence_no=pole.sequence_no,
                    name=pole.name or f"Direk #{pole.sequence_no}",
                    latitude=pole.latitude,
                    longitude=pole.longitude,
                    role=role,
                )
            )

    # --- Tekrar eden ariza -------------------------------------------------
    window_start = f.opened_at - timedelta(days=_RECURRENCE_WINDOW_DAYS)
    previous = db.execute(
        select(FaultEvent.opened_at, FaultEvent.from_pole_seq, FaultEvent.to_pole_seq)
        .where(FaultEvent.line_id == f.line_id)
        .where(FaultEvent.id != f.id)
        .where(FaultEvent.opened_at < f.opened_at)
        .where(FaultEvent.opened_at >= window_start)
    ).all()
    same_section = 0
    for opened_at, from_seq, to_seq in previous:
        # Ucu bilinmeyen aralik "ayni kesim" SAYILMAZ: "bilmiyorum"u "ayni yer"
        # diye okumak sahaya yanlis oncelikle gitmek olur.
        if None in (from_seq, to_seq, f.from_pole_seq, f.to_pole_seq):
            continue
        a_lo, a_hi = sorted((from_seq, to_seq))
        b_lo, b_hi = sorted((f.from_pole_seq, f.to_pole_seq))
        if a_lo <= b_hi and b_lo <= a_hi:
            same_section += 1
    recurrence = ReportRecurrence(
        total=len(previous),
        window_days=_RECURRENCE_WINDOW_DAYS,
        same_section=same_section,
        last_at=max((row[0] for row in previous), default=None),
    )

    # --- Harita figuru -----------------------------------------------------
    # Harita ZORUNLU DEGIL: karo yoksa (cevrimdisi cihaz, indirilmemis alan)
    # ya da topoloji eksikse rapor haritasiz cikar. Rapor hic uretilmemesi,
    # eksik bir figurden cok daha kotu olurdu.
    map_png: bytes | None = None
    try:
        geometry = collect_fault_geometry(db, f)
        if geometry is not None:
            map_png = render_fault_map(geometry)
    except Exception:  # noqa: BLE001
        map_png = None

    pdf = build_fault_report_pdf(
        fault=fault_read,
        comments=comments,
        zone_poles=zone_poles,
        recurrence=recurrence,
        settings_row=db.get(ProjectSettings, 1),
        map_png=map_png,
        generated_by=current_user.full_name or current_user.username,
    )

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M")
    filename = f"ariza-{fault_id}-{stamp}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    from app.models.responsibility_area import (
        ResponsibilityArea,
        responsibility_area_users,
    )

    previous_assignee = f.assigned_to_username
    previous_area_id = f.assigned_to_area_id
    target_username = payload.assigned_to_username or None
    target_area_id = payload.assigned_to_area_id or None

    # KISI ILE EKIP AYNI ANDA OLMAZ: ikisi de doluyken "sorumlu kim"
    # sorusunun iki cevabi olurdu ve sahada bu, iki ekibin ayni arizaya
    # gitmesi ya da ikisinin de digerini beklemesi demek.
    if target_username and target_area_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ariza ya bir kisiye ya bir ekibe atanir; ikisi birden olmaz.",
        )
    if target_username:
        target = db.scalar(select(User).where(User.username == target_username))
        if target is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Atanan kullanici bulunamadi.")
    hedef_ekip: ResponsibilityArea | None = None
    if target_area_id:
        hedef_ekip = db.get(ResponsibilityArea, target_area_id)
        if hedef_ekip is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Atanan ekip bulunamadi."
            )

    f.assigned_to_username = target_username
    f.assigned_to_area_id = target_area_id
    atandi = bool(target_username or target_area_id)
    f.assigned_at = datetime.now(timezone.utc) if atandi else None
    if atandi and f.status == "open":
        f.status = "assigned"
    hedef_etiket = target_username or (hedef_ekip.name if hedef_ekip else None)
    record_event(
        db,
        category="fault",
        event_type="fault_assigned",
        severity="info",
        actor_username=current_user.username,
        message=f"Fault assigned: fault {fault_id} -> {hedef_etiket or '(none)'}",
        metadata={
            "fault_id": fault_id,
            "assigned_to": target_username,
            "assigned_to_area_id": target_area_id,
        },
        i18n_key="fault_assigned",
        i18n_params={"fault_id": fault_id, "user": hedef_etiket or "—"},
    )

    # BILDIRIM ALICILARI: kisiye atandiysa o kisi, ekibe atandiysa ekibin TUM
    # uyeleri. Ekip atamasinda kimse tek tek secilmedigi icin haber vermezsek
    # atama ekranda durur ama kimse bilmez — atamanin amaci tam tersi.
    #
    # AYNI HEDEFE yeniden atama bildirim uretmez (spam).
    alicilar: list[str] = []
    if target_username and target_username != previous_assignee:
        alicilar = [target_username]
    elif hedef_ekip is not None and target_area_id != previous_area_id:
        alicilar = [
            row[0]
            for row in db.execute(
                select(User.username)
                .join(
                    responsibility_area_users,
                    responsibility_area_users.c.user_id == User.id,
                )
                .where(responsibility_area_users.c.area_id == target_area_id)
            ).all()
            if row[0]
        ]
    for target_username in alicilar:
        # Ariza bilgisini topla — bildirim metnini guzellestirir.
        from app.models.grid_topology import Line, Pole, Region
        from app.services.notification_service import create_notification
        line_row = db.get(Line, f.line_id) if f.line_id else None
        region_row = db.get(Region, f.region_id) if f.region_id else None
        line_name = line_row.name if line_row else f"#{f.line_id}"
        region_name = region_row.name if region_row else None
        if hedef_ekip is not None:
            # EKIP ATAMASI: "size atandi" demek yaniltici olurdu — is kisiye
            # degil ekibe verildi, uyeler arasinda paylasilacak.
            notif_title = f"{hedef_ekip.name} ekibine yeni bir arıza atandı: {line_name}"
            title_i18n_key = "fault_assignment_team"
        elif current_user.username == target_username:
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

    # --- KAPATMA KURALI ---
    #
    # Ariza yalnizca SAHADA DUZELDIKTEN sonra kapatilabilir. `resolved`a
    # gecisi kullanici degil cihaz belirler (alarm kalkinca otomatik yazilir);
    # kullanicinin isi duzelen arizayi RAPORLAYIP kapatmaktir.
    #
    # Onceden her gecis serbestti: acik bir ariza dogrudan `closed`
    # yapilabiliyordu. Bu, sahada devam eden bir arizanin ekrandan
    # kaybolmasi demekti — kimse ilgilenmedigi halde kapali gorunurdu.
    if new_status == "closed":
        if f.status != "closed" and f.resolved_at is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ariza sahada duzelmeden kapatilamaz.",
            )
        cozum = (payload.resolution_note or "").strip()
        if not cozum and not (f.resolution_note or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Kapatmak icin cozum notu zorunlu.",
            )
        if cozum:
            f.resolution_note = cozum

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
    # Kapatilmis kayit salt okunur (bkz. modul docstring).
    if f.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Kapatilmis ariza degistirilemez.",
        )
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
    # Kapatilmis kayda yorum eklenemez (bkz. modul docstring). Eski yorumlar
    # okunmaya devam eder; yalnizca YAZMA kapalidir.
    if f.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Kapatilmis arizaya yorum eklenemez.",
        )
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
        from app.models.grid_topology import Line, Pole, Region
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
