"""Cihaz yapilandirma dosyasi (Horstmann `<seri>_Configuration.csv`) API'si.

Cihaz sayfasi "Yapilandirma" sekmesi ve muhendislik toplu ekrani bu uclari
kullanir:

  GET    /device-configs/summary                 -> cihaz basina guncel surum ozeti
  GET    /devices/{id}/config                    -> guncel surum + satirlar
  GET    /devices/{id}/config/download           -> ham dosya (indir)
  GET    /devices/{id}/config/versions           -> surum gecmisi
  GET    /devices/{id}/config/versions/{v}/diff  -> bir onceki surumle fark
  POST   /devices/{id}/config/upload             -> dosya yukle (yeni surum)
  PATCH  /devices/{id}/config                    -> deger degistir (yeni surum)
  POST   /devices/{id}/config/versions/{v}/revert-> eski surume don (yeni surum)
  POST   /devices/{id}/config/apply              -> FTP'ye yaz + DNP3 komutu

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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.device_config import DeviceConfigTemplate, DeviceConfigVersion
from app.models.device_runtime_health import DeviceRuntimeHealth
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.dnp3_extended import merge_dnp3_extended
from app.schemas.device_config import (
    BulkApplyRequest,
    BulkApplyResult,
    ConfigApplicationRead,
    ConfigChangeRequest,
    ConfigCurrentRead,
    ConfigDiffRow,
    ConfigRow,
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


def _version_read(
    surum, raw: bytes | None = None, *, ftp_written: bool | None = None
) -> ConfigVersionRead:
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
        ftp_written=ftp_written,
    )


def _ftp_esitle(db: Session, device_id: int, raw: bytes) -> bool | None:
    """Yeni surumu FTP'deki dosyaya da yazar — ekran ile cihazin okuyacagi
    dosya AYNI kalmali (kullanici sarti). Kaydetmek artik dosyayi da
    guncelliyor; "Cihaza uygula" yalnizca guncelleme KOMUTUNU gonderir.

    Basarisizlik SURUMU GERI ALMAZ: surum DB'de dogru kayittir ve kaybi daha
    kotu olurdu. Ama sessiz de gecilmez — False doner (arayuz uyarir) ve
    olay kaydina yazilir. None = seri bilinmedigi icin dosya adi yok,
    yazilacak yer belirsiz.
    """
    from app.services import ftp_client_service

    try:
        dosya_adi = svc.config_filename(db, device_id)
    except Exception:  # noqa: BLE001 - seri yoksa esitlenemez
        return None
    try:
        ftp_client_service.write_config(db, filename=dosya_adi, raw=raw)
        return True
    except ftp_client_service.FtpAccessError as exc:
        record_event(
            db,
            category="ftp",
            event_type="ftp_sync_failed",
            severity="warning",
            message=f"Kaydedilen yapilandirma FTP'ye yazilamadi: {dosya_adi} — {exc}",
            metadata={"device_id": device_id, "filename": dosya_adi, "error": str(exc)},
        )
        return False


# --- cihaz yapilandirmasi --------------------------------------------------
@router.get("/device-configs/summary")
def config_ozeti(db: Session = Depends(get_db), _u: User = _YETKI) -> list[dict]:
    """Cihaz basina GUNCEL surumun ozeti — muhendislik sayfasindaki cihaz
    listesi rozetleri icin.

    Tek sorgu: cihaz basina /config cagirmak, 500 cihazlik sahada listeyi
    acmak icin 500 istek demek olurdu. Ham bayt TASINMAZ; yalnizca surum
    numarasi/kaynak/zamanlar doner.
    """
    from sqlalchemy import func, select

    from app.models.device_config import DeviceConfigVersion as V

    enbuyuk = (
        select(V.device_id, func.max(V.version).label("v"))
        .group_by(V.device_id)
        .subquery()
    )
    satirlar = db.execute(
        select(V).join(
            enbuyuk, (V.device_id == enbuyuk.c.device_id) & (V.version == enbuyuk.c.v)
        )
    ).scalars()
    return [
        {
            "device_id": s.device_id,
            "version": s.version,
            "source": s.source,
            "created_at": s.created_at,
            "applied_at": s.applied_at,
        }
        for s in satirlar
    ]


@router.get("/devices/{device_id}/config", response_model=ConfigCurrentRead)
def guncel_config(
    device_id: int, db: Session = Depends(get_db), _u: User = _YETKI
) -> ConfigCurrentRead:
    cihaz = _device(db, device_id)
    surum = svc.current_version(db, device_id)
    if surum is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Bu cihazin yapilandirma dosyasi yok. Sablon tanimlayin ya da "
            "cihazdan cekilen dosyayi yukleyin.",
        )
    raw = bytes(surum.raw)
    # Seri cozulemiyorsa dosya adi None — kartin acilmasini ENGELLEMEZ.
    # Eskiden burada patlayip 500 donuyordu ve kullanici yalnizca
    # "Yapilandirma alinamadi" goruyordu (sahada yasandi).
    try:
        dosya_adi: str | None = svc.config_filename(db, device_id)
    except Exception:  # noqa: BLE001
        dosya_adi = None
    # Cihazin kendi bildirdigi son guncelleme damgasi — "guncelleme gercekten
    # oldu mu" dogrulamasinin tek cihaz-kaynakli kaniti.
    from sqlalchemy import select as _select

    from app.models.telemetry_latest import TelemetryLatest as _TL

    son_guncelleme = db.execute(
        _select(_TL.value_string).where(
            _TL.device_id == device_id,
            _TL.signal_key == "master.info_last_configuration_update",
        )
    ).scalar()
    # Dial-In: YAPILANDIRILAN (otorite) ile cihazdan OKUNAN (tanilama) ayri
    # bildirilir. Readback saha kosullarinda guvenilir olmadigi icin hicbir
    # gateway karari ona dayanmaz; yalnizca operatore gosterilir.
    _ayar = merge_dnp3_extended(
        cihaz.dnp3_extended if isinstance(cihaz.dnp3_extended, dict) else None
    )
    _yapilandirilan = _ayar.dial_in_interval_min
    _durum, _okunan = svc.dial_in_readback_durumu(db, device_id, _yapilandirilan)

    return ConfigCurrentRead(
        version=_version_read(surum, raw),
        filename=dosya_adi,
        rows=svc.describe(raw),
        device_last_update=(son_guncelleme or None),
        dial_in_configured_min=_yapilandirilan,
        dial_in_readback_min=_okunan,
        dial_in_readback_status=_durum,
        application=_uygulama_durumu(db, cihaz),
    )


def _uygulama_durumu(db: Session, cihaz: Device) -> ConfigApplicationRead | None:
    """Cihazin en guncel uygulama sureci — OKUNURKEN UZLASTIRILARAK.

    NEDEN OKURKEN: komut bes ayri yerde terminal duruma gecebilir (cihaz
    sonucu, mutlak TTL, kira kaybi, teslim hatasi, sonuc supurucusu). Her
    birine bir kanca takmak, ileride eklenen ALTINCI cikis noktasinin
    sessizce atlanmasi demekti — ve atlanan niyet arayuzde sonsuza kadar
    "komut sirada" gorunurdu. Komut satiri zaten tek dogruluk kaynagi;
    durumu ondan TURETIYORUZ.

    Bu ayrica komut boru hattina hicbir kanca eklemeden calisir.
    """
    from datetime import datetime, timezone

    from app.models.device_config_application import DeviceConfigApplication
    from app.services import device_config_apply_service as apply_svc

    niyet = db.scalars(
        select(DeviceConfigApplication)
        .where(DeviceConfigApplication.device_id == cihaz.id)
        .order_by(DeviceConfigApplication.id.desc())
        .limit(1)
    ).first()
    if niyet is None:
        return None

    an = datetime.now(timezone.utc)
    onceki = niyet.state
    apply_svc.komut_durumunu_senkronize_et(db, niyet=niyet, simdi=an)
    if niyet.state != onceki:
        db.commit()

    surum = db.get(DeviceConfigVersion, niyet.config_version_id)
    return ConfigApplicationRead(
        state=niyet.state,
        version=(surum.version if surum is not None else 0),
        requested_at=niyet.requested_at,
        requested_by=niyet.requested_by,
        reason=niyet.last_readiness_reason,
        queued_at=niyet.queued_at,
        delivered_at=niyet.delivered_at,
        verified_at=niyet.verified_at,
        verified_by=niyet.verified_by,
        failure_reason=niyet.failure_reason,
        attempt=int(niyet.attempt or 0),
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

    try:
        indirme_adi = svc.config_filename(db, device_id)
    except Exception:  # noqa: BLE001 - seri yoksa jenerik ad; indirme engellenmez
        indirme_adi = "Configuration.csv"
    return Response(
        content=bytes(surum.raw),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{indirme_adi}"'},
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

    # Ekran == FTP dosyasi: kayit, cihazin okuyacagi dosyayi da gunceller.
    ftp_ok = _ftp_esitle(db, device_id, bytes(surum.raw))
    record_event(
        db,
        category="device",
        event_type="config_changed",
        message=f"{cihaz.name}: {len(govde.changes)} yapilandirma ayari degistirildi (v{surum.version})",
        actor_username=user.username,
        device_code=cihaz.code,
        metadata={"version": surum.version, "changes": govde.changes, "ftp_written": ftp_ok},
    )
    db.commit()
    return _version_read(surum, ftp_written=ftp_ok)


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
    # Ekran == FTP dosyasi: yuklenen dosya cihazin okuyacagi ada yazilir.
    ftp_ok = _ftp_esitle(db, device_id, raw)
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
            "ftp_written": ftp_ok,
        },
    )
    db.commit()
    return _version_read(surum, raw, ftp_written=ftp_ok)


@router.post("/devices/{device_id}/config/pull-from-ftp", response_model=ConfigVersionRead)
def ftpden_sorgula(
    device_id: int, db: Session = Depends(get_db), user: User = _YETKI
) -> ConfigVersionRead:
    """FTP'de cihazin dosyasi VARSA alir ve surume cevirir.

    "Cihazdan cek" DNP3 komutu her cihazda calismiyor; dosya cogu zaman FTP'de
    zaten duruyor (cihaz onceden yazmis). Bu uc, komut gondermeden dogrudan
    FTP'ye bakar. Ayni icerik zaten kayitliysa YENI surum uretmez, mevcut
    surumu doner (200) — gecmis collenmez.
    """
    _device(db, device_id)
    try:
        dosya_adi = svc.config_filename(db, device_id)
    except Exception as exc:  # noqa: BLE001 - seri yoksa acik hata
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    from app.services import ftp_client_service

    try:
        ham = ftp_client_service.find_config_on_ftp(db, dosya_adi)
    except ftp_client_service.FtpAccessError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if ham is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"FTP'de {dosya_adi} bulunamadi. Dosyayi yukleyin ya da sablondan olusturun.",
        )
    try:
        surum = svc.ingest_pulled_config(
            db, device_id=device_id, ham=ham, filename=dosya_adi
        )
    except ConfigParseError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"FTP'deki dosya okunamadi: {exc}"
        ) from exc
    if surum is None:
        # Ayni icerik zaten guncel surum — kayit degismedi, sorun da yok.
        mevcut = svc.current_version(db, device_id)
        db.commit()
        return _version_read(mevcut)
    record_event(
        db,
        category="device",
        event_type="config_pulled_from_ftp",
        message=f"FTP'deki {dosya_adi} yapilandirma surumune cevrildi (v{surum.version})",
        actor_username=user.username,
        device_code=_device(db, device_id).code,
        metadata={"filename": dosya_adi, "version": surum.version},
    )
    db.commit()
    return _version_read(surum)


@router.post(
    "/devices/{device_id}/config/from-template",
    response_model=ConfigVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def sablondan_olustur(
    device_id: int, db: Session = Depends(get_db), user: User = _YETKI
) -> ConfigVersionRead:
    """Yapilandirmasi OLMAYAN cihaz icin varsayilan sablondan ilk surumu uretir.

    Bos durumdaki "Sablondan olustur" dugmesi buraya gelir: kullanici dosyayi
    elle yuklemek istemiyorsa fabrika/proje sablonu tek tikla uygulanir.
    Mevcut yapilandirmasi olan cihazda 409 — ustune sablon basmak kullanicinin
    duzenlemelerini sessizce ezerdi (onun yolu: toplu uygulama, onayli).
    """
    cihaz = _device(db, device_id)
    if svc.current_version(db, device_id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cihazin zaten yapilandirmasi var."
        )
    sablon = svc.default_template(db, cihaz.model)
    if sablon is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{cihaz.model} icin varsayilan sablon tanimli degil. "
            "Cihaz Yapilandirma > Sablonlar'dan yukleyin.",
        )
    surum = svc.create_version(
        db,
        device_id=device_id,
        raw=bytes(sablon.raw),
        source="sablon",
        actor=user.username,
        template_id=sablon.id,
        note=f"'{sablon.name}' sablonundan olusturuldu",
    )
    ftp_ok = _ftp_esitle(db, device_id, bytes(surum.raw))
    record_event(
        db,
        category="device",
        event_type="config_from_template",
        message=f"{cihaz.name}: '{sablon.name}' sablonundan ilk yapilandirma olusturuldu (v{surum.version})",
        actor_username=user.username,
        device_code=cihaz.code,
        metadata={"template_id": sablon.id, "version": surum.version, "ftp_written": ftp_ok},
    )
    db.commit()
    return _version_read(surum, ftp_written=ftp_ok)


@router.post("/devices/{device_id}/config/apply", response_model=ConfigVersionRead)
def cihaza_uygula(
    device_id: int, db: Session = Depends(get_db), user: User = _YETKI
) -> ConfigVersionRead:
    """Guncel surumu FTP'ye yazar ve UYGULAMA NIYETINI kalici hale getirir.

    NE DEGISTI VE NEDEN
    -------------------
    Bu uc eskiden dosyayi yazip HEMEN bir `config_update` komutu kuyruga
    aliyor ve `applied_at` alanini O AN dolduruyordu. Horstmann Smart modda
    modemini BILEREK kapatir ve Dial-In araligi 24 saate kadar cikar; komutun
    tazelik suresi ise 120 SANIYEDIR. Yani uyuyan bir cihaza yapilan her
    gonderim, cihaz uyanmadan cok once oluyordu — ama arayuz "Cihaza
    gonderildi" yaziyordu. Uyuyan cihazda bu duz bir yalandi.

    Simdi:
      1. dosya FTP'ye yazilir (KALICI; cihazi orada bekler),
      2. kalici bir NIYET kaydi olusur (`device_config_applications`),
      3. cihaz SU AN komut alabiliyorsa komut HEMEN uretilir (surekli
         calisan cihazlarda davranis DEGISMEZ),
      4. alamiyorsa niyet `cihaz_bekleniyor` kalir ve cihaz DOGAL OLARAK
         uyandiginda backend o an TAZE bir komut uretir.

    `applied_at` ARTIK BURADA YAZILMAZ. Yalnizca cihazin KENDI kaniti
    goruldugunde dolar (bkz. `device_config_apply_service.dogrulamayi_dene`).

    Komut omru UZATILMADI: 120 saniye bir guvenlik invaryantidir ve ayni
    kanaldan kesici komutlari da gecer. Kalici olan komut degil NIYETTIR.

    Sira onemli: FTP yazimi BASARISIZSA niyet de komut da OLUSMAZ — cihaza
    "yeni dosyani oku" deyip eski dosyayi okutmak, tam da kapatmaya
    calistigimiz hatanin kendisi olurdu.
    """
    from datetime import datetime, timezone

    from app.services import device_config_apply_service as apply_svc
    from app.services import ftp_client_service

    cihaz = _device(db, device_id)
    surum = svc.current_version(db, device_id)
    if surum is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Bu cihazin yapilandirma surumu yok."
        )
    try:
        dosya_adi = svc.config_filename(db, device_id)
    except Exception as exc:  # noqa: BLE001 - seri yoksa acik hata
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    raw = bytes(surum.raw)
    try:
        yazilan_yol = ftp_client_service.write_config(db, filename=dosya_adi, raw=raw)
    except ftp_client_service.FtpAccessError as exc:
        # 409, 502 DEGIL: arayuz 5xx detaylarini bilerek gizler
        # (sanitizeErrorDetail) ve kullanici yalnizca "gonderilemedi" gorur —
        # sahada tam boyle yasandi (volume izin hatasi gorunmez kalmisti).
        # Sebep kullanicinin gorebilecegi kisa bir metin olarak doner.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    an = datetime.now(timezone.utc)
    niyet = apply_svc.niyet_olustur(
        db,
        device=cihaz,
        surum=surum,
        raw=raw,
        ftp_path=yazilan_yol,
        actor=user.username,
        simdi=an,
    )
    record_event(
        db,
        category="device",
        event_type="config_staged",
        message=(
            f"{cihaz.name}: yapilandirma v{surum.version} FTP'ye yazildi "
            f"({yazilan_yol}); cihaza uygulanmasi icin siraya alindi."
        ),
        actor_username=user.username,
        device_code=cihaz.code,
        metadata={
            "version": surum.version,
            "ftp_path": yazilan_yol,
            "application_id": niyet.id,
        },
    )

    # Cihaz SU AN komut alabiliyorsa bekletme: surekli calisan (continuous /
    # Boost) cihazlarda deneyim eskisiyle AYNI kalmali.
    komut = apply_svc.cihazi_ilerlet(
        db,
        device=cihaz,
        saglik=db.get(DeviceRuntimeHealth, cihaz.code),
        simdi=an,
    )
    if komut is None and niyet.state == apply_svc.BEKLIYOR_DURUMU:
        record_event(
            db,
            category="device",
            event_type="config_waiting_for_device",
            message=(
                f"{cihaz.name}: cihaz su an komut alamiyor "
                f"({niyet.last_readiness_reason}); yapilandirma cihaz "
                "uyandiginda uygulanacak."
            ),
            actor_username=user.username,
            device_code=cihaz.code,
            metadata={
                "application_id": niyet.id,
                "reason": niyet.last_readiness_reason,
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

    # Ekran == FTP dosyasi: geri donulen icerik dosyaya da yazilir.
    ftp_ok = _ftp_esitle(db, device_id, bytes(surum.raw))
    record_event(
        db,
        category="device",
        event_type="config_reverted",
        message=f"{cihaz.name}: v{version} yapilandirmasina geri donuldu (v{surum.version} olarak)",
        actor_username=user.username,
        device_code=cihaz.code,
        metadata={"reverted_to": version, "new_version": surum.version, "ftp_written": ftp_ok},
    )
    db.commit()
    return _version_read(surum, ftp_written=ftp_ok)


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


@router.get("/config-templates/{template_id}/rows", response_model=list[ConfigRow])
def sablon_satirlari(
    template_id: int, db: Session = Depends(get_db), _u: User = _YETKI
):
    """Sablonun ayar satirlari — sablon duzenleyicisi cihaz kartiyla AYNI
    izgarayi kullanir; veri de ayni bicimde doner."""
    sablon = db.get(DeviceConfigTemplate, template_id)
    if sablon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sablon bulunamadi")
    return svc.describe(bytes(sablon.raw))


@router.patch("/config-templates/{template_id}", response_model=TemplateRead)
def sablon_duzenle(
    template_id: int,
    govde: ConfigChangeRequest,
    db: Session = Depends(get_db),
    user: User = _YETKI,
) -> TemplateRead:
    """Sablondaki degerleri degistirir (yerinde; checksum yeniden uretilir).

    Gecmis cihaz surumleri ETKILENMEZ (baytlar kopyalanmisti); degisiklik
    yalnizca bundan sonra uygulanacak cihazlara yansir.
    """
    try:
        sablon = svc.apply_template_changes(
            db, template_id=template_id, changes=govde.changes, actor=user.username
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (ConfigParseError, KeyError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    record_event(
        db,
        category="device",
        event_type="config_template_edited",
        message=f"'{sablon.name}' sablonunda {len(govde.changes)} ayar degistirildi",
        actor_username=user.username,
        metadata={"template_id": sablon.id, "changes": govde.changes},
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
