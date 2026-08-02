from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.data.device_models import is_valid_model
from app.models.device import Device
from app.models.enums import UserRole
from app.models.signal_catalog import SignalCatalog
from app.models.telemetry_latest import TelemetryLatest
from app.models.user import User
from app.schemas.signal_catalog import (
    SignalCatalogCreate,
    SignalCatalogRead,
    SignalCatalogUpdate,
    SignalLiveValue,
)
from app.services.event_service import record_event
from app.services.signal_catalog_seed import (
    _MUTABLE_FIELDS as _SEED_MUTABLE_FIELDS,
    clear_user_overrides,
    load_default_signals,
    seed_default_signals,
)

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=list[SignalCatalogRead])
def list_signals(
    model: str | None = Query(default=None, description="Cihaz modeli koduna gore filtrele"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tum roller standart sinyal listesini okuyabilir.

    `model` query parametresi gonderilirse sadece o modele ait sinyaller doner.
    """
    stmt = select(SignalCatalog)
    if model:
        # `db` gecmek SART: katalogta sinyali olan bir modeli
        # "bilinmiyor" diye reddetmek, kullanicinin kendi girdigi
        # veriyi listeleyememesi demekti.
        if not is_valid_model(model, db):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown device model")
        stmt = stmt.where(SignalCatalog.model == model)
    stmt = stmt.order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
    return list(db.scalars(stmt).all())


@router.post("", response_model=SignalCatalogRead, status_code=status.HTTP_201_CREATED)
def create_signal(
    payload: SignalCatalogCreate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(SignalCatalog).where(SignalCatalog.key == payload.key))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Signal key already exists")
    row = SignalCatalog(**payload.model_dump())
    db.add(row)
    db.flush()
    record_event(
        db,
        category="signal",
        event_type="signal_created",
        severity="info",
        actor_username=current_user.username,
        message=f"Sinyal eklendi: {row.label} ({row.key})",
        metadata={"signal_key": row.key, "model": row.model},
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{signal_key}", response_model=SignalCatalogRead)
def update_signal(
    signal_key: str,
    payload: SignalCatalogUpdate,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(SignalCatalog).where(SignalCatalog.key == signal_key))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(row, key, value)

    # DEGISTIRILEN ALANLARI ISARETLE — yoksa acilistaki seed geri alir.
    #
    # `seed_default_signals` her backend acilisinda kosuyor ve guncelledigi
    # `_MUTABLE_FIELDS` listesi tam da burada duzenlenebilen alanlari
    # iceriyor. Isaret olmadan sistem "kaydedildi" der, denetim kaydi tutar,
    # sonra ilk yeniden baslatmada sessizce fabrika degerine doner.
    mevcut = set(row.user_overrides or [])
    mevcut.update(k for k in changes if k in _SEED_MUTABLE_FIELDS)
    # JSON kolonu: yeni liste ata (yerinde degistirme SQLAlchemy'ye "degisti"
    # sinyali vermez).
    row.user_overrides = sorted(mevcut)
    record_event(
        db,
        category="signal",
        event_type="signal_updated",
        severity="info",
        actor_username=current_user.username,
        message=f"Signal updated: {row.label} ({row.key})",
        metadata={"signal_key": row.key, "fields": list(changes.keys())},
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{signal_key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_signal(
    signal_key: str,
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(SignalCatalog).where(SignalCatalog.key == signal_key))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")
    label = row.label
    key = row.key
    db.delete(row)
    record_event(
        db,
        category="signal",
        event_type="signal_deleted",
        severity="warning",
        actor_username=current_user.username,
        message=f"Sinyal silindi: {label} ({key})",
        metadata={"signal_key": key},
    )
    db.commit()
    return None


@router.post("/reset-to-defaults")
def reset_signals_to_defaults(
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    """Sinyal katalogunu Horstmann SN2 fabrika listesine dondurur."""
    defaults = load_default_signals()
    if not defaults:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Horstmann SN2 seed dosyasi bulunamadi.",
        )
    # Fabrikaya donmek OPERATORUN BILINCLI tercihidir: burada hem silme
    # (strict) hem de kullanici degisikliklerini ezme ACIKTIR. Isaretleri
    # once temizliyoruz; kalirsa seed o alanlari atlar ve "dondurdum" denen
    # islem yarim kalirdi.
    cleared = clear_user_overrides(db)
    stats = seed_default_signals(db, strict=True, respect_user_overrides=False)
    stats["cleared_overrides"] = cleared
    record_event(
        db,
        category="signal",
        event_type="signal_reset_defaults",
        severity="warning",
        actor_username=current_user.username,
        message=(
            f"Sinyal kataloğu fabrika ayarlarına döndürüldü "
            f"(eklenen: {stats.get('inserted', 0)}, "
            f"güncellenen: {stats.get('updated', 0)}, "
            f"silinen: {stats.get('removed', 0)})"
        ),
        metadata=stats,
    )
    db.commit()
    return {
        "removed": stats.get("removed", 0),
        "inserted": stats.get("inserted", 0),
        "updated": stats.get("updated", 0),
        "total_defaults": len(defaults),
    }


@router.get("/live", response_model=list[SignalLiveValue])
def list_live_values(
    device_codes: str | None = Query(
        default=None,
        description=(
            "Virgulle ayrilmis cihaz kodlari; yalnizca bu cihazlarin satirlari "
            "doner. Bos = kullanicinin gordugu tum cihazlar."
        ),
    ),
    signal_keys: str | None = Query(
        default=None,
        description=(
            "Virgulle ayrilmis sinyal anahtarlari; yalnizca bu sinyallerin "
            "satirlari doner. Bos = tum aktif katalog. Anasayfa gibi birkac "
            "sinyal okuyan ekranlar bunu KULLANMALI — aksi halde yanit "
            "cihaz x 193 sinyal kartezyeni olur."
        ),
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aktif sinyal kataloğu × cihazlar. Telemetri yoksa değer/kalite/zaman boş döner.

    Operator rolü için kapsam (scope) uygulanır: yalnızca kendi sorumluluk
    alanlarındaki cihazların satırları döner.

    OLCEK NOTU: yanit cihaz x sinyal kartezyen carpimi (Horstmann SN2 = 193
    sinyal/cihaz). 600 cihazda ~115.800 satir eder; bu yuzden
      * `device_codes` ile daraltmak MUMKUN (tek cihaz ekrani bunu kullanmali),
      * kapsam filtresi Python'da DEGIL SQL'de uygulanir (asagida),
      * telemetri sorgusu ORM entity yerine kolon tuple'i ceker (115.800 ORM
        nesnesi hidrate etmek tek basina saniyeler suruyordu).
    """
    from app.services.scope_service import get_visible_device_ids

    device_stmt = select(Device).order_by(Device.name.asc())

    # Kapsam filtresi SQL'e iner: operator 5 cihaz goruyorsa 600 cihazi
    # cekip Python'da atmak yerine 5 satir doner.
    visible_ids = get_visible_device_ids(db, current_user)
    if visible_ids is not None:
        if not visible_ids:
            return []
        device_stmt = device_stmt.where(Device.id.in_(visible_ids))

    # Istemci daraltmasi — kapsamla BIRLIKTE uygulanir (kapsam disina cikilamaz).
    requested_codes = {c.strip() for c in (device_codes or "").split(",") if c.strip()}
    if requested_codes:
        device_stmt = device_stmt.where(Device.code.in_(requested_codes))

    device_rows: list[Device] = list(db.scalars(device_stmt).all())
    if not device_rows:
        return []
    # Sinyal daraltmasi da SQL'e iner: anasayfa 193 sinyalden yalnizca birkac
    # tanesini okuyor, hepsini uretip aga koymanin anlami yok.
    requested_signal_keys = {
        s.strip() for s in (signal_keys or "").split(",") if s.strip()
    }
    catalog_stmt = select(SignalCatalog).where(SignalCatalog.is_active.is_(True))
    if requested_signal_keys:
        catalog_stmt = catalog_stmt.where(SignalCatalog.key.in_(requested_signal_keys))
    catalog_rows: list[SignalCatalog] = list(
        db.scalars(
            catalog_stmt.order_by(
                SignalCatalog.display_order.asc(), SignalCatalog.key.asc()
            )
        ).all()
    )

    # Cihaz varken katalog hic doldurulmadiysa (ilk kurulum, seed atlanmis vb.) self-heal.
    # `device_rows` bos olma durumu yukarida ele alindi; buraya cihaz VARSA gelinir.
    #
    # DIKKAT: yalnizca DARALTMA YOKKEN. `signal_keys` verildiginde bos sonuc
    # MESRUDUR (istenen anahtar katalogda olmayabilir ya da pasiflestirilmis
    # olabilir) — burada seed tetiklemek her istekte tum katalogu yeniden
    # senkronlamak olurdu.
    if not catalog_rows and not requested_signal_keys:
        defaults = load_default_signals()
        if defaults:
            seed_default_signals(db, strict=False)
            catalog_rows = list(
                db.scalars(
                    select(SignalCatalog)
                    .where(SignalCatalog.is_active.is_(True))
                    .order_by(SignalCatalog.display_order.asc(), SignalCatalog.key.asc())
                ).all()
            )

    if not catalog_rows:
        return []

    # Son degerler `telemetry_latest` tablosundan gelir (migration 0028).
    #
    # ONCEDEN: `telemetry` uzerinde `DISTINCT ON (device_id, signal_key)`.
    # Yorumda "index-only scan, <50 ms" yaziyordu ve 200 cihazda dogruydu; 600
    # cihazda DEGIL. `DISTINCT ON` PostgreSQL'de skip-scan DEGILDIR: 30
    # dakikalik pencerenin TAMAMI (~2,16M satir) okunup 12.000 satira
    # indirgeniyordu. Ustelik anasayfa bu ucu `device_codes` VERMEDEN cagiriyor
    # ve WS koptugunda periyot 30 sn -> 5 sn'ye dusuyor, yani yuk 6 katlaniyor.
    # Istek basina ~311 MB bellek tepesi olusuyor, container tavani 2 GB ve
    # uc-bes esizamanli anasayfa backend-api'yi OOM'a goturuyordu.
    #
    # Yeni tabloda her cift icin TEK satir var; okuma PK uzerinden gider ve
    # telemetri penceresinin buyuklugunden BAGIMSIZDIR.
    #
    # ORM entity DEGIL kolon tuple'i: `select(TelemetryLatest)` her cift icin
    # bir nesne hidrate ederdi (identity map + attribute state); 115.800
    # ciftte bu tek basina sorgudan pahaliydi.
    device_ids = [d.id for d in device_rows]
    latest_telemetry_stmt = select(
        TelemetryLatest.device_id,
        TelemetryLatest.signal_key,
        TelemetryLatest.value,
        TelemetryLatest.value_string,
        TelemetryLatest.quality,
        TelemetryLatest.source_timestamp,
        TelemetryLatest.timestamp_quality,
        TelemetryLatest.device_event_at,
    ).where(TelemetryLatest.device_id.in_(device_ids))
    # (device_id, signal_key) -> (value, value_string, quality, source_timestamp,
    #                             timestamp_quality, device_event_at)
    latest_by_pair: dict[tuple[int, str], tuple] = {}
    for (
        dev_id,
        sig_key,
        value,
        value_string,
        quality,
        source_ts,
        ts_quality,
        device_event_at,
    ) in db.execute(latest_telemetry_stmt):
        latest_by_pair[(dev_id, sig_key)] = (
            value,
            value_string,
            quality,
            source_ts,
            ts_quality,
            device_event_at,
        )

    # Modele gore on-grupla, ki her cihaz icin sadece kendi modelinin sinyallerini iterate edelim.
    catalog_by_model: dict[str, list[SignalCatalog]] = {}
    for signal in catalog_rows:
        catalog_by_model.setdefault(signal.model, []).append(signal)

    result: list[SignalLiveValue] = []
    for device in device_rows:
        device_signals = catalog_by_model.get(device.model, [])
        for signal in device_signals:
            key = (device.id, signal.key)
            row = latest_by_pair.get(key)
            if row is not None:
                (
                    value,
                    value_string,
                    quality,
                    source_ts,
                    ts_quality,
                    device_event_at,
                ) = row
                result.append(
                    SignalLiveValue(
                        signal_key=signal.key,
                        signal_label=signal.label,
                        unit=signal.unit,
                        source=signal.source,
                        data_type=signal.data_type,
                        device_id=device.id,
                        device_code=device.code,
                        device_name=device.name,
                        value=value,
                        value_string=value_string,
                        quality=quality,
                        source_timestamp=source_ts.isoformat() if source_ts else None,
                        timestamp_quality=ts_quality,
                        device_event_at=(
                            device_event_at.isoformat() if device_event_at else None
                        ),
                    )
                )
            else:
                result.append(
                    SignalLiveValue(
                        signal_key=signal.key,
                        signal_label=signal.label,
                        unit=signal.unit,
                        source=signal.source,
                        data_type=signal.data_type,
                        device_id=device.id,
                        device_code=device.code,
                        device_name=device.name,
                        value=None,
                        value_string=None,
                        quality=None,
                        source_timestamp=None,
                    )
                )
    result.sort(key=lambda item: (item.device_code, item.source, item.signal_key))
    return result
