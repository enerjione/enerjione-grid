"""FTP sunucu ayarlari API'si (muhendislik sayfasi "Cihaz Yapilandirma").

  GET  /ftp-settings                   -> guncel ayarlar (parola ACIK doner)
  PUT  /ftp-settings                   -> guncelle (kismi; audit'e yazilir)
  GET  /ftp-settings/status            -> baglanti durumu + son FTP hareketleri
  POST /ftp-settings/generate-password -> okunabilir parola ONERISI (kaydetmez)
  POST /ftp-settings/test              -> harici sunucu baglanti sinamasi

Yetki: engineer + installer — cihaz yapilandirma uclariyla ayni. Operator ve
ops_manager goremez: parola cihaz filosunun ortak kimligidir.

Parola GET'te acik doner cunku cihazin FTP ekranina ELLE girilecek;
gosterilemeyen bir parolanin sahada hicbir degeri yok. Degisiklikler denetim
kaydina yazilir (parolanin KENDISI degil, degistigi bilgisi).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.ftp_settings import (
    FtpEventRow,
    FtpServerHealth,
    FtpSettingsRead,
    FtpSettingsUpdate,
    FtpStatusRead,
    FtpTestResult,
)
from app.services import ftp_client_service, ftp_settings_service
from app.services.event_service import record_event

router = APIRouter(prefix="/ftp-settings", tags=["ftp-settings"])

_YETKI = Depends(require_roles([UserRole.ENGINEER, UserRole.INSTALLER]))


def _read(row) -> FtpSettingsRead:
    return FtpSettingsRead(
        mode=row.mode,
        host=row.host,
        port=row.port,
        username=row.username,
        password=ftp_settings_service.get_password(row),
        directory=row.directory,
        poll_interval_sec=row.poll_interval_sec,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


@router.get("", response_model=FtpSettingsRead)
def ayarlari_getir(db: Session = Depends(get_db), _u: User = _YETKI) -> FtpSettingsRead:
    row = ftp_settings_service.get_settings(db)
    db.commit()  # ilk cagri satiri olusturmus olabilir
    return _read(row)


@router.put("", response_model=FtpSettingsRead)
def ayarlari_guncelle(
    govde: FtpSettingsUpdate, db: Session = Depends(get_db), user: User = _YETKI
) -> FtpSettingsRead:
    updates = govde.model_dump(exclude_unset=True)
    row = ftp_settings_service.update_settings(
        db, updates=updates, actor=user.username
    )
    # Parolanin degeri denetim kaydina YAZILMAZ; degistigi bilgisi yazilir.
    ozet = [
        ("password(degisti)" if k == "password" else f"{k}={v}")
        for k, v in updates.items()
    ]
    record_event(
        db,
        category="ftp",
        event_type="ftp_settings_updated",
        severity="info",
        actor_username=user.username,
        message=f"FTP ayarlari guncellendi: {', '.join(ozet)}",
        metadata={"fields": list(updates.keys()), "mode": row.mode},
    )
    db.commit()
    return _read(row)


@router.get("/status", response_model=FtpStatusRead)
def baglanti_durumu(db: Session = Depends(get_db), _u: User = _YETKI) -> FtpStatusRead:
    """Baglanti durumu paneli: sunucu sagligi + son FTP hareketleri.

    GOMULU modda ftp-server'in health ucu sorgulanir; donen `ftp_user`,
    sunucunun SU AN kabul ettigi kimliktir. Kimlik degistirildiginde sunucu
    onu en gec ~30 saniyede alir — o pencerede `synced=false` gorunur ve
    "parolayi degistirdim ama giremiyorum" durumu teshis edilebilir olur.

    HARICI modda sunucu bizim degil; health ucu yok. Durum, son yoklama
    olaylarindan (erisilemiyor / normale dondu / dosya alindi) izlenir.

    Son hareketler her iki modda da olay kaydindan gelir (category="ftp") —
    cihaz login'leri, yazilan/okunan dosyalar, yarim kalan transferler.
    """
    ayar = ftp_settings_service.get_settings(db)
    db.commit()

    sunucu: FtpServerHealth | None = None
    if ayar.mode == "gomulu":
        sunucu = _gomulu_sunucu_durumu(expected_user=ayar.username)

    from sqlalchemy import select

    from app.models.system_event import SystemEvent

    olaylar = list(
        db.execute(
            select(SystemEvent)
            .where(SystemEvent.category == "ftp")
            .order_by(SystemEvent.created_at.desc())
            .limit(15)
        ).scalars()
    )
    return FtpStatusRead(
        mode=ayar.mode,
        server=sunucu,
        events=[
            FtpEventRow(
                event_type=o.event_type,
                severity=o.severity,
                message=o.message,
                device_code=o.device_code,
                created_at=o.created_at,
            )
            for o in olaylar
        ],
    )


def _gomulu_sunucu_durumu(*, expected_user: str) -> FtpServerHealth:
    """ftp-server health ucunu sorgular. Erisilemezse reachable=False —
    HTTP hatasi FIRLATILMAZ, erisilemeyen sunucu da gosterilecek bir durumdur."""
    import json
    import urllib.request

    from app.core.config import settings as app_settings

    url = app_settings.ftp_server_health_url.rstrip("/") + "/health"
    try:
        # Kisa zaman asimi: bu bir durum paneli, kullaniciyi bekletmemeli.
        with urllib.request.urlopen(url, timeout=3) as yanit:
            veri = json.loads(yanit.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return FtpServerHealth(reachable=False)
    aktif = veri.get("ftp_user")
    return FtpServerHealth(
        reachable=True,
        username=aktif,
        connections=veri.get("connections"),
        synced=(aktif == expected_user) if aktif else None,
    )


@router.post("/generate-password")
def parola_uret(_u: User = _YETKI) -> dict:
    """Okunabilir parola ONERISI dondurur — KAYDETMEZ.

    Kaydetme her zaman PUT ile olur; boylece kullanici parolayi gormeden
    hicbir sey degismez ve 'uret' dugmesine yanlislikla basmak zararsizdir.
    """
    return {"password": ftp_settings_service.generate_password()}


@router.post("/test", response_model=FtpTestResult)
def baglanti_sina(db: Session = Depends(get_db), _u: User = _YETKI) -> FtpTestResult:
    """KAYITLI ayarlarla harici sunucuya baglanti dener.

    Kaydedilmemis form degerlerini sinamak icin once PUT gerekir — bilincli:
    sinanan ile kaydedilen ayni sey olsun, "test gecti ama kaydedilen baska"
    durumu olusamasin.
    """
    ok, detay, sayi = ftp_client_service.test_connection(db)
    return FtpTestResult(ok=ok, detail=detay, config_files=sayi)
