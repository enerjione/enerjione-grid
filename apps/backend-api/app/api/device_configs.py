"""Cihaz yapilandirma dosyasi (Horstmann `<seri>_Configuration.csv`) API'si.

Cihaz sayfasi "Yapilandirma" sekmesi ve muhendislik toplu ekrani bu uclari
kullanir:

  GET    /devices/{id}/config                    -> guncel surum + satirlar
  GET    /devices/{id}/config/download           -> ham dosya (indir)
  GET    /devices/{id}/config/versions           -> surum gecmisi
  GET    /devices/{id}/config/versions/{v}/diff  -> bir onceki surumle fark
  POST   /devices/{id}/config/upload             -> dosya yukle (yeni surum)
  PATCH  /devices/{id}/config                    -> deger degistir (yeni surum)
  POST   /devices/{id}/config/versions/{v}/revert-> eski surume don (yeni surum)

  GET    /config-templates                       -> sablon listesi
  POST   /config-templates                       -> sablon yukle
  POST   /config-templates/{id}/default          -> varsayilan yap
  POST   /config-templates/bulk-apply            -> secili cihazlara uygula

Yetki: TUMU `engineer` ve ustu (engineer, installer). Operator ve ops_manager
yapilandirmaya erisemez — yanlis bir deger sahada koruma ayarini bozabilir.

Not: Bu uclar yalnizca SURUM yaratir; dosyayi cihaza GONDERMEK ayri bir
adimdir (FTP'ye yazma + DNP3 komutu). Ikisini birlestirmek "kaydettim sanip
gondermemis olmak" ya da tersini uretirdi.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.device_config import DeviceConfigTemplate
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.device_config import (
    BulkApplyRequest,
    BulkApplyResult,
    ConfigChangeRequest,
    ConfigCurrentRead,
    ConfigDiffRow,
    ConfigVersionRead,
    TemplateRead,
)
from app.services import device_config_service as svc
from app.services.event_service import record_event
from app.services.horstmann_config_codec import ConfigParseError, parse

router = APIRouter(tags=["device-config"])

_YETKI = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER]))

#: Yukleme ust siniri. Gercek dosya ~1 KB; 1 MB fazlasiyla genis ama
#: sinirsiz birakmak, bellege alinan istegi acik bir DoS yuzeyi yapardi.
MAX_UPLOAD = 1024 * 1024


def _device(db: Session, device_id: int) -> Device:
    d = db.get(Device, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cihaz bulunamadi")
    return d


def _version_read(surum, raw: bytes | None = None) -> ConfigVersionRead:
    baytlar = bytes(raw if raw is not None else surum.raw)
    try:
        checksum = parse(baytlar).checksum_valid
    except ConfigParseError:
        # Ayristirilamayan surum de LISTELENEBILMELI; aksi halde bozuk tek bir
        # kayit tum gecmisi goruntulenemez yapardi.
        checksum = False
    return ConfigVersionRead(
        id=surum.id,
        device_id=surum.device_id,
        version=surum.version,
        source=surum.source,
        template_id=surum.template_id,
        note=surum.note,
        created_by=surum.created_by,
        created_at=surum.created_at,
        applied_at=surum.applied_at,
        size_bytes=len(baytlar),
        checksum_valid=checksum,
    )


# --- cihaz yapilandirmasi --------------------------------------------------
@router.get("/devices/{device_id}/config", response_model=ConfigCurrentRead)
def guncel_config(
    device_id: int, db: Session = Depends(get_db), _u: User = _YETKI
) -> ConfigCurrentRead:
    _device(db, device_id)
    surum = svc.current_version(db, device_id)
    if surum is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Bu cihazin yapilandirma dosyasi yok. Sablon tanimlayin ya da "
            "cihazdan cekilen dosyayi yukleyin.",
        )
    raw = bytes(surum.raw)
    return ConfigCurrentRead(
        version=_version_read(surum, raw),
        filename=svc.config_filename(db, device_id),
        rows=svc.describe(raw),
    )


@router.get("/devices/{device_id}/config/download")
def config_indir(
    device_id: int,
    version: int | None = None,
    db: Session = Depends(get_db),
    _u: User = _YETKI,
) -> Response:
    """Ham dosyayi indirir — FTP'ye elle koymak icin.

    `version` verilmezse guncel surum. Ad `<seri>_Configuration.csv`: cihaz
    BASKA hicbir adi tanimaz, o yuzden adi biz uretiyoruz.
    """
    _device(db, device_id)
    if version is None:
        surum = svc.current_version(db, device_id)
    else:
        surum = next(
            (s for s in svc.list_versions(db, device_id) if s.version == version), None
        )
    if surum is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Yapilandirma surumu bulunamadi")

    return Response(
        content=bytes(surum.raw),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{svc.config_filename(db, device_id)}"'
            )
        },
    )


@router.get("/devices/{device_id}/config/versions", response_model=list[ConfigVersionRead])
def surumler(
    device_id: int, db: Session = Depends(get_db), _u: User = _YETKI
) -> list[ConfigVersionRead]:
    _device(db, device_id)
    return [_version_read(s) for s in svc.list_versions(db, device_id)]


@router.get(
    "/devices/{device_id}/config/versions/{version}/diff",
    response_model=list[ConfigDiffRow],
)
def surum_farki(
    device_id: int, version: int, db: Session = Depends(get_db), _u: User = _YETKI
) -> list[ConfigDiffRow]:
    """Bu surum ile BIR ONCEKI arasindaki deger farklari."""
    _device(db, device_id)
    hepsi = {s.version: s for s in svc.list_versions(db, device_id)}
    bu = hepsi.get(version)
    if bu is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"v{version} bulunamadi")
    onceki = hepsi.get(version - 1)
    if onceki is None:
        return []  # ilk surum: karsilastirilacak bir sey yok
    return [ConfigDiffRow(**f) for f in svc.diff(bytes(onceki.raw), bytes(bu.raw))]


@router.patch("/devices/{device_id}/config", response_model=ConfigVersionRead)
def degerleri_degistir(
    device_id: int,
    govde: ConfigChangeRequest,
    db: Session = Depends(get_db),
    user: User = _YETKI,
) -> ConfigVersionRead:
    cihaz = _device(db, device_id)
    try:
        surum = svc.apply_changes(
            db,
            device_id=device_id,
            changes=govde.changes,
            actor=user.username,
            note=govde.note,
        )
    except svc.ConfigNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (ConfigParseError, KeyError) as exc:
        # Sigmayan deger / olmayan girdi: SURUM YARATILMADAN reddedilir.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    record_event(
        db,
        category="device",
        event_type="config_changed",
        message=f"{cihaz.name}: {len(govde.changes)} yapilandirma ayari degistirildi (v{surum.version})",
        actor_username=user.username,
        device_code=cihaz.code,
        metadata={"version": surum.version, "changes": govde.changes},
    )
    db.commit()
    return _version_read(surum)


@router.post(
    "/devices/{device_id}/config/upload",
    response_model=ConfigVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def dosya_yukle(
    device_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = _YETKI,
) -> ConfigVersionRead:
    """Cihazdan cekilen ya da elle hazirlanan dosyayi yeni surum olarak kaydeder.

    BOZUK CHECKSUM REDDEDILMEZ — yalnizca isaretlenir. Cihazdan gelen dosya
    bozuk olabilir ve onu goremezsek sorunu teshis edemeyiz. Sablon tarafi
    farklidir: orada bozuk dosya kabul edilmez (her cihaza yayilirdi).
    """
    cihaz = _device(db, device_id)
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dosya bos")
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Dosya cok buyuk")
    try:
        doc = parse(raw)
    except ConfigParseError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Dosya okunamadi: {exc}"
        ) from exc

    # Dosya adi bu cihaza ait mi? UYUSMAZLIK ENGELLENMEZ (baska bir cihazin
    # dosyasini sablon olarak kullanmak mesru bir ihtiyac) ama SESSIZ DE
    # GECILMEZ: cihaz yalnizca kendi adini tasiyan dosyayi okur, dolayisiyla
    # yanlis adla yuklenen bir config sahada "komut gitti ama bir sey olmadi"
    # olarak ortaya cikar. Denetim kaydi bu izi tutar.
    #
    # Arayuz de yuklemeden once soruyor; buradaki kontrol ondan BAGIMSIZDIR —
    # istemci kontrolu atlanabilir, denetim kaydi atlanamaz.
    try:
        beklenen = svc.config_filename(db, device_id)
    except Exception:  # noqa: BLE001 - seri bilinmiyorsa karsilastirma yapilamaz
        beklenen = None
    seri_uyusmuyor = bool(beklenen and file.filename and file.filename != beklenen)

    surum = svc.create_version(
        db,
        device_id=device_id,
        raw=raw,
        source="yuklendi",
        actor=user.username,
        note=f"'{file.filename}' yuklendi",
    )
    uyari = seri_uyusmuyor or not doc.checksum_valid
    record_event(
        db,
        category="device",
        event_type="config_uploaded",
        message=(
            f"{cihaz.name}: yapilandirma dosyasi yuklendi (v{surum.version})"
            + (
                f" — dosya adi bu cihaza ait DEGIL (beklenen {beklenen}, "
                f"gelen {file.filename})"
                if seri_uyusmuyor
                else ""
            )
        ),
        severity="warning" if uyari else "info",
        actor_username=user.username,
        device_code=cihaz.code,
        metadata={
            "version": surum.version,
            "filename": file.filename,
            "expected_filename": beklenen,
            "serial_mismatch": seri_uyusmuyor,
            "checksum_valid": doc.checksum_valid,
        },
    )
    db.commit()
    return _version_read(surum, raw)


@router.post(
    "/devices/{device_id}/config/versions/{version}/revert",
    response_model=ConfigVersionRead,
)
def geri_al(
    device_id: int, version: int, db: Session = Depends(get_db), user: User = _YETKI
) -> ConfigVersionRead:
    cihaz = _device(db, device_id)
    try:
        surum = svc.revert_to(
            db, device_id=device_id, version=version, actor=user.username
        )
    except svc.ConfigNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    record_event(
        db,
        category="device",
        event_type="config_reverted",
        message=f"{cihaz.name}: v{version} yapilandirmasina geri donuldu (v{surum.version} olarak)",
        actor_username=user.username,
        device_code=cihaz.code,
        metadata={"reverted_to": version, "new_version": surum.version},
    )
    db.commit()
    return _version_read(surum)


# --- sablonlar -------------------------------------------------------------
def _template_read(s: DeviceConfigTemplate) -> TemplateRead:
    return TemplateRead(
        id=s.id,
        name=s.name,
        device_model=s.device_model,
        source_filename=s.source_filename,
        note=s.note,
        is_default=s.is_default,
        created_by=s.created_by,
        created_at=s.created_at,
        size_bytes=len(bytes(s.raw)),
    )


@router.get("/config-templates", response_model=list[TemplateRead])
def sablonlar(
    device_model: str | None = None,
    db: Session = Depends(get_db),
    _u: User = _YETKI,
) -> list[TemplateRead]:
    return [_template_read(s) for s in svc.list_templates(db, device_model)]


@router.post(
    "/config-templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED
)
async def sablon_yukle(
    name: str,
    device_model: str,
    is_default: bool = False,
    note: str | None = None,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = _YETKI,
) -> TemplateRead:
    raw = await file.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Dosya cok buyuk")
    try:
        sablon = svc.create_template(
            db,
            name=name,
            device_model=device_model,
            raw=raw,
            source_filename=file.filename,
            note=note,
            is_default=is_default,
            actor=user.username,
        )
    except ConfigParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    record_event(
        db,
        category="device",
        event_type="config_template_created",
        message=f"Yapilandirma sablonu eklendi: {name} ({device_model})",
        actor_username=user.username,
        metadata={"template_id": sablon.id, "is_default": is_default},
    )
    db.commit()
    return _template_read(sablon)


@router.post("/config-templates/{template_id}/default", response_model=TemplateRead)
def varsayilan_yap(
    template_id: int, db: Session = Depends(get_db), user: User = _YETKI
) -> TemplateRead:
    try:
        sablon = svc.set_default_template(db, template_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    record_event(
        db,
        category="device",
        event_type="config_template_default",
        message=f"'{sablon.name}' sablonu {sablon.device_model} icin varsayilan yapildi",
        actor_username=user.username,
        metadata={"template_id": sablon.id},
    )
    db.commit()
    return _template_read(sablon)


@router.post("/config-templates/bulk-apply", response_model=BulkApplyResult)
def toplu_uygula(
    govde: BulkApplyRequest, db: Session = Depends(get_db), user: User = _YETKI
) -> BulkApplyResult:
    """Bir sablonu SECILI cihazlara yeni surum olarak uygular.

    Basarisiz cihazlar SESSIZCE ATLANMAZ: hangisi neden alamadi doner. Toplu
    islemde sessiz atlama, "500 cihaza uyguladim" deyip 40'ini atlamak
    demektir ve fark edilmesi aylar surer.
    """
    sablon = db.get(DeviceConfigTemplate, govde.template_id)
    if sablon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sablon bulunamadi")

    uygulanan: list[int] = []
    basarisiz: list[dict] = []
    for device_id in govde.device_ids:
        cihaz = db.get(Device, device_id)
        if cihaz is None:
            basarisiz.append({"device_id": device_id, "reason": "cihaz_yok"})
            continue
        if cihaz.model != sablon.device_model:
            # Yanlis tipe config basmak, koruma ayarlarini anlamsiz kilar.
            basarisiz.append(
                {
                    "device_id": device_id,
                    "reason": "model_uyusmuyor",
                    "detail": f"cihaz={cihaz.model} sablon={sablon.device_model}",
                }
            )
            continue
        svc.create_version(
            db,
            device_id=device_id,
            raw=bytes(sablon.raw),
            source="sablon",
            actor=user.username,
            template_id=sablon.id,
            note=govde.note or f"'{sablon.name}' sablonu toplu uygulandi",
        )
        uygulanan.append(device_id)

    record_event(
        db,
        category="device",
        event_type="config_bulk_applied",
        message=(
            f"'{sablon.name}' sablonu {len(uygulanan)} cihaza uygulandi"
            + (f", {len(basarisiz)} cihaz atlandi" if basarisiz else "")
        ),
        severity="warning" if basarisiz else "info",
        actor_username=user.username,
        metadata={
            "template_id": sablon.id,
            "applied": len(uygulanan),
            "failed": basarisiz,
        },
    )
    db.commit()
    return BulkApplyResult(applied=uygulanan, failed=basarisiz)
