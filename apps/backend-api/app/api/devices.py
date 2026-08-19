import logging
import re

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role, require_roles
from app.db.session import get_db
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.enums import UserRole
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.models.telemetry_history import TelemetryHistory
from app.models.user import User
from app.repositories.device_repository import DeviceRepository
from app.schemas.device import (
    DeviceCommandQueued,
    DeviceCommandRequest,
    DeviceCommandRow,
    DeviceCreate,
    DeviceRead,
    DeviceUpdate,
)
from app.schemas.dnp3_extended import (
    Dnp3ExtendedSettings,
    effective_dnp3_extended,
    merge_dnp3_extended,
    validate_session_policy,
)
from app.schemas.telemetry import TelemetryAggregatePoint, TelemetryHistoryPoint
from app.services import (
    device_command_service,
    device_config_service,
    device_kit_service,
    license_service,
)
from app.services.event_service import record_event
from app.services.scope_service import get_visible_device_ids

log = logging.getLogger(__name__)

# Config-turu komut slug'lari — installer-only. Genel + alarm reset komutlari
# engineer+installer'da kalir. Frontend DeviceCommandsPanel meta'daki 'config'
# grubuyla ayni mantik (backend guvenlik siniri, UI ikincil).
_CONFIG_COMMAND_SLUGS = frozenset(
    {
        "config_update",
        "dnp3_config_update",
        "trigger_config_download",
        "trigger_dnp3_config_download",
        "start_csv_file_upload",
    }
)

router = APIRouter(prefix="/devices", tags=["devices"])


# NOT: Onceden burada bir "stale online -> offline" override calisiyordu;
# DB'de "online" yazmasina ragmen son telemetri esikten eskiyse OFFLINE'a
# dusuruyordu. Ancak:
#   1) datetime karsilastirmasi (timezone offset edge-case'leri) flicker
#      yaratiyordu — kullanici "sayfa yenilendikce farkli sonuc" dedi,
#   2) bagliyken yavas telemetri gonderen cihazlari da offline gosteriyordu,
#   3) tag-engine zaten her telemetri'de communication_status'u dogru
#      sekilde guncelliyor (offline/comm_lost/restart -> OFFLINE; iyi
#      okuma -> ONLINE).
# Frontend tarafinda gateway-down kontrolu (effectiveCommStatus) ek
# koruma sagliyor. DB'deki tek gercege guvenmek en stabil yaklasim.


@router.get("", response_model=list[DeviceRead])
def list_devices(
    gateway_code: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    if gateway_code:
        rows = repository.list_devices_by_gateway(gateway_code)
    else:
        rows = repository.list_devices()
    # Operator scope: sadece kendi sorumluluk alanlarindaki cihazlari gosterir.
    visible_ids = get_visible_device_ids(db, current_user)
    if visible_ids is not None:
        # KIT ISTISNASI: fiziksel kit kaydi hicbir hat segmentine baglanmaz
        # (hatta oturan setleridir), bu yuzden kapsam filtresinden HER ZAMAN
        # duserdi. Duserse setlerin "Pole Master" sekmesi kit seviyesindeki
        # degerleri (modem, GPS, besleme) ve komutlari cozemez.
        # Kural: setlerinden en az biri gorunuyorsa kit de gorunur.
        gorunur_parent_ids = {
            d.parent_device_id
            for d in rows
            if d.parent_device_id and d.id in visible_ids
        }
        rows = [
            d for d in rows if d.id in visible_ids or d.id in gorunur_parent_ids
        ]
    return device_kit_service.annotate(db, rows)


@router.post("", response_model=DeviceRead, status_code=status.HTTP_201_CREATED)
def create_device(
    payload: DeviceCreate,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    existing = repository.get_by_code(payload.code)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Device code already exists")
    try:
        set_count = device_kit_service.normalize_set_count(
            payload.model, payload.satellite_set_count
        )
        if set_count is not None:
            device_kit_service.validate_kit_codes(payload.code, set_count)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    # Akilli oturum kombinasyonu (smart + listening) daha CIHAZ OLUSMADAN
    # reddedilir; yaratildiktan sonra duzeltmek gateway'e bir tur gecersiz
    # config gitmesi demekti.
    _require_valid_dnp3_session(None, payload.dnp3_extended)
    try:
        _require_gateway(db, payload.gateway_code)
        _require_unique_endpoint(
            db,
            gateway_code=payload.gateway_code,
            ip_address=getattr(payload, "ip_address", None),
            port=getattr(payload, "dnp3_outstation_port", None),
        )
        license_service.lock_and_assert_device_capacity(db)
        device = repository.create(payload)
        # Sanal setler FIZIKSEL kaydin hemen ardindan, AYNI transaction'da
        # uretilir. Ayri bir istege birakmak, yarim kalmis bir kit (setleri
        # olmayan fiziksel kayit) birakma riski demekti: o kayit telemetriyi
        # hicbir yere yazamaz ve arayuzde bos gorunur.
        if set_count is not None:
            device_kit_service.create_subunits(db, device, set_count)
    except license_service.LicenseCapacityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    record_event(
        db,
        category="device",
        event_type="device_created",
        severity="info",
        actor_username=current_user.username,
        device_code=device.code,
        message=f"Cihaz eklendi: {device.name} ({device.code})",
        metadata={"device_id": device.id, "gateway_code": device.gateway_code},
        i18n_key="device_created",
        i18n_params={"name": device.name, "code": device.code},
    )

    # Cihaz modeli icin varsayilan yapilandirma sablonu varsa ilk surumu simdi
    # uret. Sablon YOKSA `None` doner ve cihaz eklenmesi ETKILENMEZ — config
    # sablonu tanimlanmamis olmasi cihaz eklemeyi engellememeli.
    #
    # Hata YUTULMAZ ama cihazi da dusurmez: bu noktada cihaz kaydi zaten
    # gecerli, yalnizca yardimci bir adim basarisiz olmustur. Eksiklik
    # arayuzde "yapilandirma dosyasi yok" olarak gorunur.
    try:
        device_config_service.ensure_initial_version(
            db, device, actor=current_user.username
        )
    except Exception:  # noqa: BLE001
        log.warning("ilk yapilandirma surumu uretilemedi: %s", device.code, exc_info=True)

    _bump_gateway_config_nonce(db, device.gateway_code)
    db.commit()
    return device_kit_service.annotate_one(db, device)


def _require_unique_endpoint(
    db: Session,
    *,
    gateway_code: str | None,
    ip_address: str | None,
    port: int | None,
    exclude_device_id: int | None = None,
) -> None:
    """Ayni gateway'de ayni (IP, port) ikinci bir cihaza verilmesin.

    YASANAN (2026-08-01, saha test cihazi):
      Iki cihaz yanlislikla ayni uc noktaya (192.168.211.1:20001) ayarlanmisti.
      Horstmann outstation'i `CloseExisting` modunda calisiyor — yeni bir
      istemci baglaninca MEVCUT baglantiyi kapatiyor. Sonuc karsilikli
      tahliye dongusu oldu:

          d baglanir -> d15 baglanir -> outstation d'yi atar
          -> d yeniden baglanir -> d15'i atar -> ...

      Gateway gunlugunde 2.172 `link_close` birikti (iki cihaz icin 1.198 ve
      1.196 — neredeyse esit, cunku sirayla birbirlerini atiyorlardi).
      Cihazlar surekli "bayat" olup toparlandi, telemetri kesintili geldi.

      BELIRTI KAYNAGINA HIC BENZEMIYORDU: gorunen sey "ag kararsiz"di, oysa
      tek bir yanlis port alaniydi. Sistem buna sessizce izin veriyordu.

    Kapsam GATEWAY BAZINDA: farkli gateway'ler farkli ag segmentlerinde
    olabilir ve ayni IP'yi mesru sekilde gorebilirler. Ayni gateway icinde
    ise ayni (IP, port) her zaman hatadir.

    SANAL SETLER HARIC (`parent_device_id IS NOT NULL`): bir Pole Master
    Kit'in setleri ayni outstation'i BILEREK paylasir ve gateway'e poll
    hedefi olarak HIC verilmez (bkz. api/gateways.py) — yani yukaridaki
    tahliye dongusunu uretemezler. Muafiyet yalnizca alt cihazlaradir;
    kontrolun asil amaci (iki BAGIMSIZ cihazin ayni uc noktaya ayarlanmasi)
    aynen korunur.
    """
    if not gateway_code or not ip_address or not port:
        return
    stmt = select(Device.code).where(
        Device.gateway_code == gateway_code,
        Device.ip_address == ip_address,
        Device.dnp3_outstation_port == port,
        Device.parent_device_id.is_(None),
    )
    if exclude_device_id is not None:
        stmt = stmt.where(Device.id != exclude_device_id)
    cakisan = db.scalar(stmt)
    if cakisan is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{cakisan}' cihazi ayni gateway'de ayni adresi kullaniyor "
                f"({ip_address}:{port}). Ayni uc noktaya iki cihaz baglanirsa "
                "outstation surekli birini digeri icin kapatir ve haberlesme "
                "kopuk gorunur."
            ),
        )


#: Denetim kaydinda eski -> yeni gosterilecek DNP3 oturum alanlari.
#:
#: Tum sozlugu diff'lemiyoruz: `dnp3_extended` icinde operatorun nadiren
#: dokundugu bir suru alan var ve hepsini olay kaydina yazmak, gercekten
#: onemli olan degisikligi gurultude bogardi. Bu dort alan haberlesmenin
#: KURULUM SEKLINI degistirir.
_DENETLENEN_DNP3_ALANLARI = (
    "ip_endpoint_type",
    "session_policy",
    "smart_max_silence_sec",
    "master_ip_port",
)


def _dnp3_denetim_farki(
    onceki: Dnp3ExtendedSettings, sonraki: Dnp3ExtendedSettings
) -> dict[str, dict[str, object]]:
    """Denetime girecek alanlarin eski -> yeni farki (degismeyen alan yazilmaz)."""
    fark: dict[str, dict[str, object]] = {}
    for alan in _DENETLENEN_DNP3_ALANLARI:
        eski = getattr(onceki, alan, None)
        yeni = getattr(sonraki, alan, None)
        if eski != yeni:
            fark[alan] = {"old": eski, "new": yeni}
    return fark


def _require_valid_dnp3_session(
    stored: dict | None, incoming: Dnp3ExtendedSettings | None
) -> Dnp3ExtendedSettings:
    """Yazma sonrasi olusacak ayari dogrular ve doner. Gecersizse 422.

    Dogrulama EFEKTIF durum uzerinden yapilir; gerekcesi
    `schemas/dnp3_extended.effective_dnp3_extended` docstring'inde.
    """
    efektif = effective_dnp3_extended(stored, incoming)
    try:
        validate_session_policy(efektif)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return efektif


def _require_gateway(db: Session, gateway_code: str | None) -> None:
    if not gateway_code:
        return
    if db.scalar(select(Gateway.id).where(Gateway.code == gateway_code)) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Gateway bulunamadi: {gateway_code}",
        )


def _bump_gateway_config_nonce(db: Session, gateway_code: str | None) -> None:
    """Cihaz config'i degisti -> ait oldugu gateway'in config_nonce'unu +1 artir.

    Gateway hafif komut-poll'de (1sn) bu artisi gorup config'i HEMEN ceker (5dk
    poll'u beklemez). commit CALLER'a birakilir (mevcut transaction'a katilir).
    """
    if not gateway_code:
        return
    gw = db.scalar(select(Gateway).where(Gateway.code == gateway_code))
    if gw is not None:
        gw.config_nonce = int(getattr(gw, "config_nonce", 0) or 0) + 1


def _ensure_device_visible(db: Session, current_user: User, device) -> None:
    """Object-level authz: engineer kendi sorumluluk alanindaki cihazlari
    duzenleyebilir; installer her cihaza erisebilir.

    `get_visible_device_ids` engineer icin `responsibility_areas` tablosundan
    izinli device id'leri doner. Installer icin None doner (sinirsiz).
    IDOR koruma: device_code bilen engineer baska bolgenin cihazini
    update/delete edemez.
    """
    visible = get_visible_device_ids(db, current_user)
    if visible is not None and device.id not in visible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu cihaza erişim yetkiniz yok (responsibility scope dışı)",
        )


@router.patch("/{device_code}", response_model=DeviceRead)
def update_device(
    device_code: str,
    payload: DeviceUpdate,
    # Cihaz config'i (DNP3 ayarlari, IP/adres, extended) sadece INSTALLER
    # degistirebilir. Engineer artik PATCH edemez (yanlis config saha
    # haberlesmesini bozabilir; en ust rol siniri).
    current_user: User = Depends(require_role(UserRole.INSTALLER)),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    device = repository.get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)
    # Hangi alanlar degisti — operator/muhendis paneli icin event'e koy.
    changes = payload.model_dump(exclude_unset=True)
    old_gateway_code = device.gateway_code
    # DNP3 oturum ayari: yalnizca istemci gercekten dokunduysa dogrulanir.
    # Her PATCH'te dogrulamak, alanla ilgisi olmayan bir istegi (or. yalnizca
    # `name`) eski bir kaydin gecersiz kombinasyonu yuzunden reddederdi.
    dnp3_oncesi = merge_dnp3_extended(device.dnp3_extended)
    dnp3_sonrasi: Dnp3ExtendedSettings | None = None
    if "dnp3_extended" in changes:
        dnp3_sonrasi = _require_valid_dnp3_session(
            device.dnp3_extended, payload.dnp3_extended
        )
    if "gateway_code" in changes:
        _require_gateway(db, payload.gateway_code)
    # Uc nokta cakismasi — ASIL HATA BURADA YAPILMISTI: mevcut bir cihazin
    # portu duzenlenirken baska bir cihazinkiyle ayni yapilmisti. Yeni
    # degerleri (verilmisse) mevcutlarla harmanlayip kontrol ediyoruz;
    # yalnizca `changes` icindekilere bakmak, IP degisip port ayni kaldiginda
    # cakismayi kacirirdi.
    # SANAL SET KAYITLARI BU KONTROLDEN MUAF.
    #
    # Setin kendi uc noktasi YOKTUR; baglanti alanlari kitten kopyalanmis
    # bilgidir ve gateway'e poll hedefi olarak verilmez. Kontrol edilseydi
    # set HER ZAMAN kendi kitiyle cakisirdi (ayni IP:port) ve setin
    # herhangi bir ayarini — uydu atamasi, faz, ad — degistirmek 409 ile
    # reddedilirdi. Yani muafiyet olusturmada vardi ama GUNCELLEMEDE yoktu.
    if device.parent_device_id is None:
        _require_unique_endpoint(
            db,
            gateway_code=changes.get("gateway_code", device.gateway_code),
            ip_address=changes.get("ip_address", device.ip_address),
            port=changes.get("dnp3_outstation_port", device.dnp3_outstation_port),
            exclude_device_id=device.id,
        )
    # --- UYDU ATAMASI ---
    #
    # Kit genelinde BIJEKTIF olmali: ayni uydu iki sete atanirsa bolme
    # haritasi bozulur ve ikinci setin o unitesi HIC veri almaz — arayuzde
    # set saglikli gorunur, yalnizca "bir faz hic olcum vermiyor" diye
    # fark edilir. Dogrulama yazmadan ONCE yapilir.
    if "subunit_satellites" in changes:
        if device.parent_device_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Uydu atamasi yalnizca kit setlerinde degistirilebilir.",
            )
        try:
            payload.subunit_satellites = device_kit_service.normalize_satellites(
                db, device, payload.subunit_satellites
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    updated = repository.update(device, payload)

    # --- SET SAYISI DEGISIKLIGI ---
    #
    # Modelin kendisi de bu istekte degisiyor olabilir (SN2 -> kit); karari
    # GUNCELLENMIS model uzerinden veriyoruz.
    # DEVRALINAN ALANLARI SETLERE YANSIT.
    #
    # Setler kitin gateway/IP/port bilgisini tasir. Kit baska bir gateway'e
    # tasinip setler eski kodda kalirsa, ESKI gateway silindiginde setler
    # silinir ve kit yetim kalir (silme hedefi yalnizca `gateway_code` ile
    # secilir) — hat yerlesimi ve ariza gecmisi geri alinamaz sekilde gider.
    if device_kit_service.is_kit(updated):
        device_kit_service.propagate_to_subunits(db, updated)

    set_degisimi: dict[str, list[str]] | None = None
    if "satellite_set_count" in changes:
        try:
            set_count = device_kit_service.normalize_set_count(
                updated.model, payload.satellite_set_count
            )
            if set_count is not None:
                set_degisimi = device_kit_service.sync_subunits(db, updated, set_count)
                # Yeni uretilen setler zaten guncel degerlerle olusur; bu
                # cagri, sync sirasinda kalan eski setleri de hizalar.
                device_kit_service.propagate_to_subunits(db, updated)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    # Fark, yazma SONRASI efektif ayar uzerinden. `dnp3_sonrasi` yalnizca
    # istemci alana dokunduysa dolu; dokunmadiysa fark da yoktur.
    dnp3_farki = (
        _dnp3_denetim_farki(dnp3_oncesi, dnp3_sonrasi) if dnp3_sonrasi is not None else {}
    )

    record_event(
        db,
        category="device",
        event_type="device_updated",
        severity="info",
        actor_username=current_user.username,
        device_code=updated.code,
        message=f"Device updated: {updated.name} ({updated.code})",
        metadata={
            "device_id": updated.id,
            "fields": list(changes.keys()),
            # Haberlesmenin kurulum seklini degistiren alanlar ESKI -> YENI
            # yazilir. Yalnizca alan ADINI kaydetmek ("dnp3_extended degisti")
            # denetim sorusunu cevaplamiyordu: cihaz ne zaman akilli moda
            # alindi, esigi kim degistirdi?
            **({"dnp3_changes": dnp3_farki} if dnp3_farki else {}),
        },
        i18n_key="device_updated",
        i18n_params={"name": updated.name, "code": updated.code},
    )
    if set_degisimi and (set_degisimi["created"] or set_degisimi["deleted"]):
        # Set silmek VERI SILER (telemetri, alarm, ariza gecmisi, hat
        # yerlesimi). Denetim kaydi olmadan bunun izi kalmazdi.
        record_event(
            db,
            category="device",
            event_type="device_subunits_synced",
            severity="warning" if set_degisimi["deleted"] else "info",
            actor_username=current_user.username,
            device_code=updated.code,
            message=(
                f"{updated.code}: set sayisi guncellendi "
                f"(eklenen={len(set_degisimi['created'])}, "
                f"silinen={len(set_degisimi['deleted'])})"
            ),
            metadata={"device_id": updated.id, **set_degisimi},
        )
    if updated.gateway_code == old_gateway_code:
        _bump_gateway_config_nonce(db, updated.gateway_code)
    else:
        _bump_gateway_config_nonce(db, old_gateway_code)
        _bump_gateway_config_nonce(db, updated.gateway_code)
    db.commit()
    return device_kit_service.annotate_one(db, updated)


@router.delete("/{device_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    device_code: str,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    repository = DeviceRepository(db)
    device = repository.get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)
    name = device.name
    code = device.code
    device_id = device.id
    device_gateway = device.gateway_code

    # ALT CIHAZLAR ONCE VE TEK TEK SILINIR.
    #
    # `devices.parent_device_id` CASCADE oldugu icin veritabani setleri
    # kitle birlikte dusuruyor — ama setlerin `telemetry`,
    # `telemetry_history` ve `alarm_events` satirlarinin FK'sinde ondelete
    # YOK. Kaskad o satirlara carpiyor ve TUM silme FK ihlaliyle geri
    # aliniyordu: kullanici "Cihaz silinemedi" goruyor, sebebi hicbir yerde
    # gorunmuyordu.
    #
    # `repository.delete` yalnizca KENDI verdigimiz cihazin telemetrisini
    # temizliyor; her seti ayri ayri gecirmek zorundayiz. Ayrica bu yol
    # setlerin alarm kurali filtrelerinden de dusurulmesini sagliyor.
    silinen_setler: list[str] = []
    for alt in device_kit_service.list_subunits(db, device.id):
        repository.delete(alt)
        silinen_setler.append(alt.code)

    deleted_counts = repository.delete(device)
    _bump_gateway_config_nonce(db, device_gateway)
    record_event(
        db,
        category="device",
        event_type="device_deleted",
        severity="warning",
        actor_username=current_user.username,
        device_code=code,
        message=f"Cihaz silindi: {name} ({code})",
        metadata={
            "device_id": device_id,
            "gateway_code": device_gateway,
            "cleanup": deleted_counts,
            # Kit silindiyse hangi setlerin birlikte gittigi denetim
            # kaydinda gorunmeli; sonradan "bu setler nereye gitti"
            # sorusunun tek cevabi bu satir olur.
            "deleted_subunits": silinen_setler,
        },
        i18n_key="device_deleted",
        i18n_params={"name": name, "code": code},
    )
    db.commit()
    return None


@router.post("/{device_code}/command", response_model=DeviceCommandQueued)
def queue_device_command(
    device_code: str,
    payload: DeviceCommandRequest,
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Cihaza DNP3 binary output (CROB) komutu KUYRUGA ALIR.

    Yetki: ENGINEER, INSTALLER. Engineer scope disi cihaza komut gonderemez.

    Gateway NAT arkasinda oldugundan backend gateway'e ulasamaz. Komut config-poll
    ile iletilir: burada `device_commands` tablosuna status='pending' satir yazilir,
    gateway her config poll'de (~config_refresh_sec, default 30sn) pending komutlari
    ceker, CROB gonderir ve sonucu `POST /gateways/{code}/command-results` ile bildirir.

    Yanit ANLIK DEGIL: {id, status:'pending'} doner. Gercek sonuc
    `GET /devices/{code}/commands` ile takip edilir.

    Akis: cihaz bul -> gateway bul -> slug'i SignalCatalog binary_output'tan
    dnp3_index'e cevir (allowlist; ham index yok) -> pending satir + audit.
    """
    device = DeviceRepository(db).get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)

    # Config-turu komutlar installer-only. Genel + alarm reset komutlari
    # engineer+installer'da (endpoint seviyesi). Slug config grubundaysa
    # engineer 403 alir. Bu kontrol ARAYUZE OZGU: rol kavrami yalnizca burada
    # var, dolayisiyla servise degil router'a ait.
    slug = payload.command.strip()
    if slug in _CONFIG_COMMAND_SLUGS and current_user.role != UserRole.INSTALLER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Config komutlari yalnizca installer tarafindan gonderilebilir",
        )

    # Gateway kontrolu, SignalCatalog allowlist'i, satir yazimi ve audit
    # ORTAK serviste. IEC 104 yolu da ayni fonksiyonu cagiriyor; dogrulamayi
    # burada tekrarlamak iki kapinin zamanla ayrismasi demekti.
    try:
        queued = device_command_service.queue_command(
            db,
            device=device,
            slug=slug,
            actor=current_user.username,
            origin="ui",
            count=payload.count,
            on_time_ms=payload.on_time_ms,
            off_time_ms=payload.off_time_ms,
        )
    except device_command_service.CommandRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail
        ) from exc
    db.commit()
    return DeviceCommandQueued(
        id=queued.id, status=queued.status,
        command=queued.command, dnp3_index=queued.dnp3_index,
    )


@router.get("/{device_code}/report.pdf")
def device_report_pdf(
    device_code: str,
    sections: str | None = Query(
        default=None,
        description=(
            "Virgulle ayrilmis bolum anahtarlari; yalnizca bu bolumler basilir. "
            "Bos = hepsi. Gecerli anahtarlar: "
            "ozet, konum, kunye, baglanti, kanallar, sinyaller, haberlesme, "
            "setler, alarmlar, olaylar."
        ),
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cihazin tek dosyalik DURUM RAPORU (A4 dikey, konum haritali).

    NE ICERIR: kunye ve baglanti, konum, olcum kanallari (unite/uydu/seri/
    faz/pil), kanal kanal sinyal tablolari, RTU (ya da kit seviyesi)
    degerler, aktif alarmlar ve son olaylar. Iskelet CIHAZ TURUNE gore
    degisir — Pole Master Kit'in kendi raporunda olcum kanali yoktur, bagli
    setler listesi vardir; setin raporunda ise haberlesme degerleri KIT
    kaydindan gelir. Ayrintili gerekce: `services/device_report_service.py`.

    Yetki: cihaz listesiyle AYNI kapsam — operator/engineer yalnizca kendi
    sorumluluk alanindaki cihazin raporunu alabilir. Rapor musteri logosu,
    hat/direk bilgisi ve GPS koordinati tasidigi icin kapsam disina sizmasi
    kabul edilemez.

    KIT ISTISNASI: fiziksel kit kaydi hicbir hat kesimine baglanmaz (hatta
    oturan setleridir), bu yuzden kapsam filtresinden HER ZAMAN duserdi —
    setleri gorunen bir kullanici kitin raporunu alamazdi. Kural liste
    ucundakiyle ayni: setlerinden en az biri gorunuyorsa kit de gorunur.
    """
    from app.models.project_settings import ProjectSettings
    from app.services.device_report_map import render_device_map_for
    from app.services.device_report_service import (
        build_device_report_pdf,
        collect_device_report,
        parse_sections,
    )

    secili = parse_sections(sections)

    device = DeviceRepository(db).get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    visible = get_visible_device_ids(db, current_user)
    if visible is not None and device.id not in visible:
        gorunur_set = any(
            child.id in visible for child in device_kit_service.list_subunits(db, device.id)
        )
        if not gorunur_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bu cihaza erişim yetkiniz yok (responsibility scope dışı)",
            )

    data = collect_device_report(db, device)
    # Harita ZORUNLU DEGIL: karo yoksa (cevrimdisi kurulum, indirilmemis alan)
    # ya da cihaz hatta yerlestirilmemisse rapor haritasiz cikar. Raporun hic
    # uretilmemesi, eksik bir figurden cok daha kotu olurdu.
    #
    # Konum bolumu SECILMEDIYSE karo hic cekilmez: figur uretimi on kadar
    # HTTP istegi ve birkac saniye demek, ve cikti zaten kullanilmayacak.
    map_image = render_device_map_for(db, device) if "konum" in secili else None

    pdf = build_device_report_pdf(
        data,
        settings_row=db.get(ProjectSettings, 1),
        map_image=map_image,
        generated_by=current_user.full_name or current_user.username,
        sections=secili,
    )

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M")
    # Dosya adindaki cihaz kodu dosya sistemine giriyor: kod `devices.code`
    # uzerinde serbest metin, `/` ya da `"` iceren bir kod Content-Disposition
    # basligini kirardi.
    safe_code = re.sub(r"[^A-Za-z0-9._-]+", "-", device.code).strip("-") or "cihaz"
    filename = f"cihaz-{safe_code}-{stamp}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{device_code}/commands", response_model=list[DeviceCommandRow])
def list_device_commands(
    device_code: str,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER])),
    db: Session = Depends(get_db),
):
    """Cihazin son komutlari + durumlari (UI takip listesi). En yeni once."""
    device = DeviceRepository(db).get_by_code(device_code)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)
    rows = db.scalars(
        select(DeviceCommand)
        .where(DeviceCommand.device_code == device.code)
        .order_by(DeviceCommand.id.desc())
        .limit(limit)
    ).all()
    return list(rows)


@router.get(
    "/{device_code}/history",
    response_model=list[TelemetryHistoryPoint] | list[TelemetryAggregatePoint],
)
def device_signal_history(
    device_code: str,
    signal_key: str = Query(..., description="Sinyal key'i (orn. master.actual_current)"),
    bucket: Literal["raw", "10s", "1m", "5m", "1h"] = Query(
        "raw",
        description=(
            "raw=ham historian; 1m/1h=continuous aggregate (materialized);"
            " 10s/5m=raw'dan on-the-fly time_bucket (kisa aralik icin)"
        ),
    ),
    since: datetime | None = Query(None, description="Bu zamandan sonra (UTC)"),
    until: datetime | None = Query(None, description="Bu zamana kadar (UTC)"),
    limit: int = Query(1000, ge=1, le=10000),
    # Tum roller okuyabilir (salt-okuma grafik). Operator kendi scope'undaki
    # cihazi gorur; _ensure_device_visible IDOR korur.
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Historian zaman serisi — cihaz detay grafikleri icin.

    `telemetry_history` hypertable (bucket=raw) veya continuous aggregate
    view'lari (bucket=1m/1h). Aggregate view'lar sadece TimescaleDB'de var;
    vanilla postgres'te (dev) 1m/1h istegi otomatik ham veriye duser.
    """
    device = db.scalar(select(Device).where(Device.code == device_code))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _ensure_device_visible(db, current_user, device)

    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)

    if bucket == "raw":
        stmt = select(TelemetryHistory).where(
            TelemetryHistory.device_id == device.id,
            TelemetryHistory.signal_key == signal_key,
        )
        if since is not None:
            stmt = stmt.where(TelemetryHistory.source_timestamp >= since)
        if until is not None:
            stmt = stmt.where(TelemetryHistory.source_timestamp <= until)
        stmt = stmt.order_by(TelemetryHistory.source_timestamp.asc()).limit(limit)
        rows = db.scalars(stmt).all()
        return [
            TelemetryHistoryPoint(
                signal_key=r.signal_key,
                value=r.value,
                value_string=r.value_string,
                quality=r.quality,
                source_timestamp=r.source_timestamp,
            )
            for r in rows
        ]

    # 1m/1h: materialized continuous aggregate view. 10s/5m: raw hypertable'dan
    # on-the-fly time_bucket (materialized view yok, raw 90 gun + index yeterli;
    # kisa aralik grafikleri icin). Ikisi de ayni cikti semasini doner.
    if bucket in ("1m", "1h"):
        view = "telemetry_history_1m" if bucket == "1m" else "telemetry_history_1h"
        sql = text(
            f"SELECT signal_key, bucket, avg_value, min_value, max_value, sample_count"
            f" FROM {view}"
            " WHERE device_id = :device_id AND signal_key = :signal_key"
            "   AND (:since IS NULL OR bucket >= :since)"
            "   AND (:until IS NULL OR bucket <= :until)"
            " ORDER BY bucket ASC"
            " LIMIT :limit"
        )
    else:
        # 10s / 5m -> raw'dan time_bucket. TimescaleDB time_bucket(interval, ts).
        interval = "10 seconds" if bucket == "10s" else "5 minutes"
        sql = text(
            "SELECT signal_key,"
            f" time_bucket(INTERVAL '{interval}', source_timestamp) AS bucket,"
            " avg(value) AS avg_value, min(value) AS min_value,"
            " max(value) AS max_value, count(value) AS sample_count"
            " FROM telemetry_history"
            " WHERE device_id = :device_id AND signal_key = :signal_key"
            "   AND (:since IS NULL OR source_timestamp >= :since)"
            "   AND (:until IS NULL OR source_timestamp <= :until)"
            " GROUP BY signal_key, bucket"
            " ORDER BY bucket ASC"
            " LIMIT :limit"
        )
    try:
        result = db.execute(
            sql,
            {
                "device_id": device.id,
                "signal_key": signal_key,
                "since": since,
                "until": until,
                "limit": limit,
            },
        )
        agg_rows = result.mappings().all()
    except ProgrammingError:
        # View/time_bucket yok (vanilla postgres / migration uygulanmadi) -> ham veriye dus.
        db.rollback()
        return device_signal_history(
            device_code=device_code,
            signal_key=signal_key,
            bucket="raw",
            since=since,
            until=until,
            limit=limit,
            current_user=current_user,
            db=db,
        )
    return [
        TelemetryAggregatePoint(
            signal_key=m["signal_key"],
            bucket=m["bucket"],
            avg_value=m["avg_value"],
            min_value=m["min_value"],
            max_value=m["max_value"],
            sample_count=m["sample_count"],
        )
        for m in agg_rows
    ]
