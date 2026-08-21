"""UYUYAN CIHAZA YAPILANDIRMA — kalici niyet, dogal uyanma, taze komut.

YASANAN HATA
------------
Horstmann Smart modda modemini BILEREK kapatir; Dial-In araligi 24 saate
kadar cikar. Yapilandirma gonderimi ise komut kuyruguna dayaniyordu ve
komutun tazelik suresi 120 SANIYEDIR:

    10:00  operator "Uygula" dedi
    10:00  dosya FTP'ye yazildi, config_update kuyruga girdi
    10:00  surum.applied_at = now()   -> arayuz "Cihaza gonderildi" dedi
    10:02  komut EXPIRED
    ...
    Ertesi gun 10:00  cihaz uyandi -- ve KIMSE ona "yeni dosyani oku" demedi

Yani uyuyan bir cihaza yapilan HER yapilandirma gonderimi sessizce
basarisiz oluyor, ustelik arayuz basarili gibi gosteriyordu.

COZUMDE NE YAPILMADI
--------------------
Komut omru UZATILMADI. `command_max_age_sec = 120` bir GUVENLIK
INVARYANTIDIR ve ayni kanaldan KESICI komutlari da gecer; uyuyan cihaz icin
o sureyi uzatmak, saatler onceki bir fiziksel kararin bugun calismasina izin
vermek olurdu. Bu dosyanin ILK testi tam da bunu kilitler.

KALICI OLAN SEY KOMUT DEGIL NIYETTIR. Cihaz DOGAL OLARAK uyandiginda backend
O AN yeni ve yine 120 saniyelik bir komut uretir.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.device_config import DeviceConfigVersion
from app.models.device_config_application import (
    BASARISIZ,
    BEKLIYOR,
    DOGRULANDI,
    GECERSIZ,
    ILETILDI,
    KUYRUKTA,
    DeviceConfigApplication,
)
from app.models.device_runtime_health import DeviceRuntimeHealth
from app.models.enums import CommunicationStatus
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.models.telemetry_latest import TelemetryLatest
from app.services import device_config_apply_service as apply_svc
from app.services import device_session_readiness as hazir

MODEL = "horstmann_sn_2_0"
KOD = "SN2-1"
AN = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
ICERIK = b"CONFIG-V1-BYTES"


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True, expire_on_commit=False)()
    s.add(
        Gateway(
            code="GW-1", name="G", host="10.0.0.1", listen_port=20000,
            token="t" * 20, is_active=True,
        )
    )
    s.commit()
    s.add(
        Device(
            code=KOD, name="Direk-1", serial_number=KOD, gateway_code="GW-1",
            model=MODEL, ip_address="10.0.0.10", latitude=39.0, longitude=35.0,
            communication_status=CommunicationStatus.UNKNOWN,
        )
    )
    s.add(
        SignalCatalog(
            key="master.config_update", model=MODEL, label="Config guncelle",
            data_type="binary_output", dnp3_index=0, is_active=True,
        )
    )
    s.commit()

    # FTP dogrulamasi: gercek sunucuya gitmeden, yazilani hatirlayan sahte.
    kova: dict[str, bytes] = {}
    monkeypatch.setattr(
        apply_svc, "_ftp_hala_bizim_mi",
        lambda _db, _did, niyet: kova.get("raw") is not None
        and hashlib.sha256(kova["raw"]).hexdigest() == niyet.ftp_sha256,
    )
    s.info["ftp"] = kova
    yield s
    s.close()


def _cihaz(db) -> Device:  # noqa: ANN001
    return db.scalar(select(Device).where(Device.code == KOD))


def _surum(db, no: int = 1, icerik: bytes = ICERIK) -> DeviceConfigVersion:  # noqa: ANN001
    v = DeviceConfigVersion(
        device_id=_cihaz(db).id, version=no, raw=icerik, source="duzenlendi",
        created_at=AN,
    )
    db.add(v)
    db.flush()
    return v


def _niyet_ac(db, no: int = 1, icerik: bytes = ICERIK, an: datetime = AN):  # noqa: ANN001
    """FTP'ye yazildi + niyet olusturuldu (uc katmaninin yaptigi)."""
    db.info["ftp"]["raw"] = icerik
    return apply_svc.niyet_olustur(
        db, device=_cihaz(db), surum=_surum(db, no, icerik), raw=icerik,
        ftp_path=f"/ftp/{KOD}_Configuration.csv", actor="muh", simdi=an,
    )


def _saglik(db, *, durum: str, reachable: bool, temas: float | None = 1.0,  # noqa: ANN001
           gozlem: datetime = AN) -> DeviceRuntimeHealth:
    satir = db.get(DeviceRuntimeHealth, KOD)
    if satir is None:
        satir = DeviceRuntimeHealth(device_code=KOD)
        db.add(satir)
    satir.gateway_code = "GW-1"
    satir.connection_state = durum
    satir.reachable = reachable
    satir.connected = reachable
    satir.last_valid_contact_epoch = temas
    satir.updated_at = gozlem
    db.flush()
    return satir


def _ilerlet(db, an: datetime = AN):  # noqa: ANN001
    return apply_svc.cihazi_ilerlet(
        db, device=_cihaz(db), saglik=db.get(DeviceRuntimeHealth, KOD), simdi=an
    )


def _komutlar(db) -> int:  # noqa: ANN001
    return int(db.scalar(select(func.count()).select_from(DeviceCommand)) or 0)


def _niyet(db) -> DeviceConfigApplication | None:  # noqa: ANN001
    return db.scalars(
        select(DeviceConfigApplication).order_by(DeviceConfigApplication.id.desc())
    ).first()


# ===========================================================================
# 0) DOKUNULMAYAN INVARYANT — komut omru
# ===========================================================================


def test_KOMUT_TTL_UZATILMADI():
    """Cozum komut omrunu uzatmak DEGILDI; 120 sn guvenlik invaryantidir.

    Bu satir degistiginde ayni kanaldan gecen KESICI komutlari da uzun omurlu
    olurdu: operatorun saatler onceki karari bugun fiziksel sisteme
    uygulanabilirdi.
    """
    from app.core.config import settings

    assert int(settings.command_max_age_sec) == 120


def test_config_slug_icin_OZEL_TTL_YOK():
    """Slug bazli uzun TTL de eklenmedi — kaynakta boyle bir dal olmamali."""
    import pathlib

    kaynak = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app/services/command_delivery_service.py"
    ).read_text(encoding="utf-8")
    assert "config_update" not in kaynak, (
        "teslim katmani slug'a gore davranmaya baslamis — TTL ayrismasi riski"
    )


# ===========================================================================
# A) Uyuyan Smart cihaz: FTP staged, niyet bekliyor, KOMUT YOK
# ===========================================================================


def test_A_uyuyan_cihazda_KOMUT_URETILMEZ(db):
    _niyet_ac(db)
    _saglik(db, durum="smart_idle", reachable=False)
    assert _ilerlet(db) is None
    n = _niyet(db)
    assert n.state == BEKLIYOR
    assert _komutlar(db) == 0
    assert n.last_readiness_reason == hazir.UYKUDA


def test_A2_uyuyan_cihazda_applied_at_YAZILMAZ(db):
    """Eski hata: komut kuyruga girer girmez `applied_at` doluyordu."""
    niyet = _niyet_ac(db)
    _saglik(db, durum="smart_idle", reachable=False)
    _ilerlet(db)
    surum = db.get(DeviceConfigVersion, niyet.config_version_id)
    assert surum.applied_at is None


# ===========================================================================
# B) Dogal uyanma -> TAM BIR taze komut
# ===========================================================================


def test_B_uyaninca_TEK_taze_komut_uretilir(db):
    _niyet_ac(db)
    _saglik(db, durum="smart_idle", reachable=False)
    _ilerlet(db)
    assert _komutlar(db) == 0

    uyanma = AN + timedelta(minutes=60)
    _saglik(db, durum="online", reachable=True, gozlem=uyanma)
    cmd = _ilerlet(db, uyanma)

    assert cmd is not None
    assert _komutlar(db) == 1
    assert _niyet(db).state == KUYRUKTA
    # KOMUT UYANMA ANINDA URETILDI — 60 dakika once degil.
    assert cmd.created_at.replace(tzinfo=timezone.utc) >= uyanma - timedelta(seconds=5)


def test_B2_uretilen_komut_NORMAL_TTL_ile_yasar(db):
    """Uyanmada uretilen komut da 120 saniyeliktir; ayricalik YOK."""
    from app.core.config import settings
    from app.services.command_delivery_service import son_kullanma

    _niyet_ac(db)
    uyanma = AN + timedelta(hours=24)
    _saglik(db, durum="online", reachable=True, gozlem=uyanma)
    cmd = _ilerlet(db, uyanma)

    bitis = son_kullanma(cmd, int(settings.command_max_age_sec))
    assert (bitis - cmd.created_at.replace(tzinfo=timezone.utc)) == timedelta(seconds=120)


# ===========================================================================
# C) Tekrarlanan saglik partisi -> MUKERRER KOMUT YOK
# ===========================================================================


def test_C_tekrarlanan_saglik_MUKERRER_komut_uretmez(db):
    _niyet_ac(db)
    _saglik(db, durum="online", reachable=True)
    assert _ilerlet(db) is not None
    assert _komutlar(db) == 1

    for i in range(5):
        _saglik(db, durum="online", reachable=True, gozlem=AN + timedelta(seconds=i))
        assert _ilerlet(db, AN + timedelta(seconds=i)) is None
    assert _komutlar(db) == 1, "her saglik partisinde yeni komut uretiliyor"


# ===========================================================================
# D) Backend restart -> niyet KORUNUR
# ===========================================================================


def test_D_restart_sonrasi_niyet_KORUNUR_ve_bir_kez_komut(db):
    """Niyet DB'de; surec olse de kaybolmaz."""
    _niyet_ac(db)
    _saglik(db, durum="smart_idle", reachable=False)
    _ilerlet(db)
    db.commit()

    # "Restart": kimlik haritasini bosalt, satirlari DB'den yeniden oku.
    db.expunge_all()
    assert _niyet(db).state == BEKLIYOR

    uyanma = AN + timedelta(hours=12)
    _saglik(db, durum="online", reachable=True, gozlem=uyanma)
    assert _ilerlet(db, uyanma) is not None
    assert _komutlar(db) == 1


# ===========================================================================
# E) Yaris — iki es zamanli istek TEK acik niyet
# ===========================================================================


def test_E_ayni_cihazda_IKI_ACIK_NIYET_olusamaz(db):
    """Exactly-once'in VERITABANI ayagi.

    Uygulama ici kilit yetmez: birden fazla uvicorn worker'i AYRI SURECTIR.
    Kismi unique index bu yuzden tek gercek garantidir.
    """
    from sqlalchemy.exc import IntegrityError

    _niyet_ac(db)
    db.commit()

    ikinci = DeviceConfigApplication(
        device_id=_cihaz(db).id,
        config_version_id=_surum(db, 2, b"BASKA").id,
        state=BEKLIYOR, requested_at=AN, ftp_staged_at=AN, ftp_sha256="a" * 64,
    )
    db.add(ikinci)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_E2_kapali_niyetler_SINIRSIZ(db):
    """Kismi index yalnizca ACIK durumlari kapsamali; gecmis birikebilir."""
    cihaz_id = _cihaz(db).id
    v = _surum(db, 1)
    for durum in (DOGRULANDI, BASARISIZ, GECERSIZ, DOGRULANDI):
        db.add(
            DeviceConfigApplication(
                device_id=cihaz_id, config_version_id=v.id, state=durum,
                requested_at=AN, ftp_staged_at=AN, ftp_sha256="b" * 64,
            )
        )
    db.flush()  # patlamamali
    assert db.scalar(select(func.count()).select_from(DeviceConfigApplication)) == 4


# ===========================================================================
# F) Supersede — v10 beklerken v11 gelirse
# ===========================================================================


def test_F_yeni_surum_eskisini_GECERSIZ_kilar(db):
    eski = _niyet_ac(db, 1, b"V10")
    _saglik(db, durum="smart_idle", reachable=False)
    _ilerlet(db)
    assert eski.state == BEKLIYOR

    yeni = _niyet_ac(db, 2, b"V11", an=AN + timedelta(minutes=5))
    assert eski.state == GECERSIZ
    assert eski.closed_at is not None
    assert yeni.state == BEKLIYOR

    # Uyaninca YALNIZCA yeni surum uygulanir.
    uyanma = AN + timedelta(hours=1)
    _saglik(db, durum="online", reachable=True, gozlem=uyanma)
    _ilerlet(db, uyanma)
    assert _komutlar(db) == 1
    assert _niyet(db).config_version_id == yeni.config_version_id


def test_F2_gecmis_SILINMEZ(db):
    """Gecersiz kilinan niyet denetim kaydidir; satir kalir."""
    _niyet_ac(db, 1, b"V10")
    _niyet_ac(db, 2, b"V11", an=AN + timedelta(minutes=5))
    assert db.scalar(select(func.count()).select_from(DeviceConfigApplication)) == 2


# ===========================================================================
# G/H) FTP tutarsizligi ve komut uretilemezligi
# ===========================================================================


def test_G_FTP_dosyasi_DEGISMISSE_komut_uretilmez(db):
    """v10 niyeti uyandiginda FTP'de v11 duruyorsa yanlis dosya yuklenirdi.

    Dosya adi cihaz basina SABITTIR ve yeni surum eskisinin USTUNE yazar.
    """
    _niyet_ac(db, 1, b"V10")
    db.info["ftp"]["raw"] = b"BASKA-ICERIK"  # baska bir yoldan degismis
    _saglik(db, durum="online", reachable=True)
    assert _ilerlet(db) is None
    n = _niyet(db)
    assert n.state == BASARISIZ
    assert "FTP" in (n.failure_reason or "")
    assert _komutlar(db) == 0


def test_H_komut_uretilemezse_applied_at_YAZILMAZ(db):
    niyet = _niyet_ac(db)
    db.info["ftp"]["raw"] = b"BOZUK"
    _saglik(db, durum="online", reachable=True)
    _ilerlet(db)
    assert db.get(DeviceConfigVersion, niyet.config_version_id).applied_at is None


# ===========================================================================
# I/J) ILETILDI != DOGRULANDI
# ===========================================================================


def _komutu_sonuclandir(db, cmd, *, ok: bool, result: str | None = None):  # noqa: ANN001
    cmd.status = "ok" if ok else "failed"
    cmd.result_status = result
    cmd.completed_at = AN + timedelta(minutes=1)
    db.flush()


def test_I_komut_basarili_ama_HENUZ_DOGRULANMADI(db):
    """Gateway yalnizca komutu ILETTIGINI bilir; cihazin yukledigini DEGIL."""
    niyet = _niyet_ac(db)
    _saglik(db, durum="online", reachable=True)
    cmd = _ilerlet(db)
    _komutu_sonuclandir(db, cmd, ok=True)

    _ilerlet(db, AN + timedelta(minutes=2))
    assert _niyet(db).state == ILETILDI
    assert _niyet(db).verified_at is None
    assert db.get(DeviceConfigVersion, niyet.config_version_id).applied_at is None, (
        "kanit yokken applied_at yazilmis"
    )


def test_J_cihazin_KENDI_DOSYASI_dogrulama_saglar(db):
    """KESIN kanit: baytlari cihaz uretti, yani icerik cihazda GECERLI."""
    niyet = _niyet_ac(db)
    _saglik(db, durum="online", reachable=True)
    cmd = _ilerlet(db)
    _komutu_sonuclandir(db, cmd, ok=True)
    _ilerlet(db, AN + timedelta(minutes=2))

    # Cihaz ayni baytlari FTP'ye kendisi yazdi.
    db.add(
        DeviceConfigVersion(
            device_id=_cihaz(db).id, version=99, raw=ICERIK,
            source="cihazdan_cekildi", created_at=AN + timedelta(minutes=5),
        )
    )
    db.flush()
    _ilerlet(db, AN + timedelta(minutes=6))

    n = _niyet(db)
    assert n.state == DOGRULANDI
    assert n.verified_by == apply_svc.KANIT_CIHAZ_DOSYASI
    assert db.get(DeviceConfigVersion, niyet.config_version_id).applied_at is not None


def test_J2_damga_degisimi_ZAYIF_kanit_olarak_dogrular(db):
    niyet = _niyet_ac(db)
    db.add(
        TelemetryLatest(
            device_id=_cihaz(db).id, signal_key=apply_svc.READBACK_SIGNAL,
            value_string="2026-01-01", updated_at=AN, source_timestamp=AN,
        )
    )
    db.flush()
    _saglik(db, durum="online", reachable=True)
    cmd = _ilerlet(db)
    assert _niyet(db).readback_before == "2026-01-01"
    _komutu_sonuclandir(db, cmd, ok=True)
    _ilerlet(db, AN + timedelta(minutes=2))

    db.query(TelemetryLatest).update({"value_string": "2026-08-21"})
    db.flush()
    _ilerlet(db, AN + timedelta(minutes=3))

    n = _niyet(db)
    assert n.state == DOGRULANDI
    assert n.verified_by == apply_svc.KANIT_DAMGA
    _ = niyet


def test_J3_damga_DEGISMEDIYSE_dogrulama_YOK(db):
    _niyet_ac(db)
    db.add(
        TelemetryLatest(
            device_id=_cihaz(db).id, signal_key=apply_svc.READBACK_SIGNAL,
            value_string="AYNI", updated_at=AN, source_timestamp=AN,
        )
    )
    db.flush()
    _saglik(db, durum="online", reachable=True)
    cmd = _ilerlet(db)
    _komutu_sonuclandir(db, cmd, ok=True)
    _ilerlet(db, AN + timedelta(minutes=2))
    _ilerlet(db, AN + timedelta(minutes=3))
    assert _niyet(db).state == ILETILDI


# ===========================================================================
# K) Komut basarisizligi — gecici vs kalici
# ===========================================================================


def test_K_expired_komut_TEKRAR_BEKLEMEYE_doner(db):
    """Cihaz teslimden once yeniden uyudu; bu bir CIHAZ REDDI degildir."""
    _niyet_ac(db)
    _saglik(db, durum="online", reachable=True)
    cmd = _ilerlet(db)
    _komutu_sonuclandir(db, cmd, ok=False, result="expired")

    _ilerlet(db, AN + timedelta(minutes=3))
    n = _niyet(db)
    assert n.state == BEKLIYOR
    assert n.command_id is None
    assert n.attempt == 1


def test_K1b_AYNI_GOZLEMLE_ikinci_komut_uretilmez(db):
    """KOR TEKRAR YASAK.

    Komut bayatladiktan sonra cihaz hala `online` gorunuyor olabilir — ama
    gozlem AYNI gozlemdir. Onunla hemen ikinci komut uretmek, cihaz
    teslimden once yeniden uyuduysa ayni bayatlamayi tekrarlamak ve deneme
    sayacini bosuna tuketmek olurdu.
    """
    _niyet_ac(db)
    _saglik(db, durum="online", reachable=True, gozlem=AN)
    cmd = _ilerlet(db)
    _komutu_sonuclandir(db, cmd, ok=False, result="expired")

    # Gozlem TAZELENMEDI: yeni komut YOK.
    assert _ilerlet(db, AN + timedelta(minutes=3)) is None
    assert _komutlar(db) == 1
    assert _niyet(db).last_readiness_reason == hazir.YENI_KANIT_BEKLENIYOR


def test_K1c_YENI_gozlem_gelince_tekrar_denenir(db):
    """Gateway cihazi TEKRAR gordu — bu gercek bir uyanma kanitidir."""
    _niyet_ac(db)
    _saglik(db, durum="online", reachable=True, gozlem=AN)
    cmd = _ilerlet(db)
    _komutu_sonuclandir(db, cmd, ok=False, result="expired")
    _ilerlet(db, AN + timedelta(minutes=3))

    yeni_an = AN + timedelta(minutes=10)
    _saglik(db, durum="online", reachable=True, gozlem=yeni_an)
    assert _ilerlet(db, yeni_an) is not None
    assert _komutlar(db) == 2
    assert _niyet(db).attempt == 2


def test_K2_cihaz_REDDI_kalici_basarisizlik(db):
    """DNP3 reddi otomatik tekrarlanmaz; operator gorsun ve karar versin."""
    _niyet_ac(db)
    _saglik(db, durum="online", reachable=True)
    cmd = _ilerlet(db)
    _komutu_sonuclandir(db, cmd, ok=False, result="NOT_SUPPORTED")

    _ilerlet(db, AN + timedelta(minutes=3))
    n = _niyet(db)
    assert n.state == BASARISIZ
    assert "NOT_SUPPORTED" in (n.failure_reason or "")


def test_K3_SONSUZ_DONGU_YOK(db):
    """Uyan-komut-uyu dongusu bir tavana carpar ve operator gorur."""
    _niyet_ac(db)
    an = AN
    for _ in range(apply_svc.AZAMI_DENEME + 3):
        # Her turda gozlem TAZELENIR: yani cihaz gercekten tekrar tekrar
        # uyaniyor. Kor tekrar kapisi bu dongude ACIK — durduran tek sey
        # deneme tavani olmali.
        _saglik(db, durum="online", reachable=True, gozlem=an)
        cmd = _ilerlet(db, an)
        if cmd is None:
            break
        _komutu_sonuclandir(db, cmd, ok=False, result="expired")
        an += timedelta(minutes=10)
        # Sonucu isle (niyet BEKLIYOR'a donsun) ama GOZLEMI TAZELEME:
        # kor tekrar kapisi kapali oldugu icin burada yeni komut cikmaz.
        _ilerlet(db, an)
    n = _niyet(db)
    assert n.state == BASARISIZ
    assert n.attempt == apply_svc.AZAMI_DENEME
    assert _komutlar(db) == apply_svc.AZAMI_DENEME


# ===========================================================================
# L) Geriye uyumluluk — surekli calisan cihaz
# ===========================================================================


def test_L_surekli_cihazda_ANINDA_komut(db):
    """Continuous/Boost deneyimi BOZULMAMALI."""
    _niyet_ac(db)
    _saglik(db, durum="online", reachable=True)
    assert _ilerlet(db) is not None
    assert _niyet(db).state == KUYRUKTA


def test_L2_saglik_kaydi_HIC_YOKSA_eski_davranis(db):
    """Gateway 1.15.0 oncesi / yayinci kapali: `communication_status` ONLINE
    ise yapilandirma eskisi gibi ANINDA gonderilir.

    Aksi halde bu is, saglik kanali olmayan HER sahada yapilandirma
    gonderimini kalici olarak bozardi.
    """
    _niyet_ac(db)
    cihaz = _cihaz(db)
    cihaz.communication_status = CommunicationStatus.ONLINE
    db.flush()
    assert db.get(DeviceRuntimeHealth, KOD) is None
    assert _ilerlet(db) is not None
    assert _niyet(db).state == KUYRUKTA


def test_L3_saglik_kaydi_yok_ve_OFFLINE_ise_beklenir(db):
    _niyet_ac(db)
    _cihaz(db).communication_status = CommunicationStatus.OFFLINE
    db.flush()
    assert _ilerlet(db) is None
    assert _niyet(db).state == BEKLIYOR


# ===========================================================================
# M/N/O) Hazirlik yuklemi — kenar durumlar
# ===========================================================================


def test_M_smart_idle_HAZIR_DEGIL(db):
    _saglik(db, durum="smart_idle", reachable=False)
    k = hazir.degerlendir(
        saglik=db.get(DeviceRuntimeHealth, KOD),
        legacy_status=CommunicationStatus.ONLINE,  # eski alan yaniltmamali
        simdi=AN,
    )
    assert k.hazir is False
    assert k.kaynak == hazir.KAYNAK_SOZLESME


def test_M2_recovering_HAZIR_DEGIL(db):
    _saglik(db, durum="recovering", reachable=False)
    assert (
        hazir.degerlendir(
            saglik=db.get(DeviceRuntimeHealth, KOD), legacy_status=None, simdi=AN
        ).hazir
        is False
    )


def test_M3_SAGLIK_VARSA_eski_alana_DUSULMEZ(db):
    """KRITIK: uyuyan cihazin `communication_status` degeri henuz `online`
    kalmis olabilir. Ona bakip komut uretmek, uyuyan cihaza fiziksel islem
    gondermeye calismak olurdu."""
    _saglik(db, durum="smart_idle", reachable=False)
    k = hazir.degerlendir(
        saglik=db.get(DeviceRuntimeHealth, KOD),
        legacy_status=CommunicationStatus.ONLINE,
        simdi=AN,
    )
    assert k.hazir is False
    assert k.kaynak != hazir.KAYNAK_ESKI


def test_N_online_ama_TEMAS_YOKSA_hazir_degil(db):
    _saglik(db, durum="online", reachable=True, temas=None)
    k = hazir.degerlendir(
        saglik=db.get(DeviceRuntimeHealth, KOD), legacy_status=None, simdi=AN
    )
    assert k.hazir is False
    assert k.sebep == hazir.TEMAS_YOK


def test_N2_erisilemez_ise_hazir_degil(db):
    """Sozlesme `reachable`i birebir 'komut gonderilebilir mi' diye tanimlar."""
    _saglik(db, durum="online", reachable=False)
    k = hazir.degerlendir(
        saglik=db.get(DeviceRuntimeHealth, KOD), legacy_status=None, simdi=AN
    )
    assert k.hazir is False
    assert k.sebep == hazir.ERISILEMEZ


def test_N3_BAYAT_gozlem_hazir_saymaz(db):
    """Gateway susmus; son sozune guvenip komut uretmek yanlis olurdu."""
    _saglik(db, durum="online", reachable=True, gozlem=AN)
    gec = AN + timedelta(seconds=hazir.RUNTIME_STALE_AFTER_SEC + 1)
    k = hazir.degerlendir(
        saglik=db.get(DeviceRuntimeHealth, KOD), legacy_status=None, simdi=gec
    )
    assert k.hazir is False
    assert k.sebep == hazir.BAYAT_GOZLEM


def test_O_online_reachable_temas_var_ISE_hazir(db):
    _saglik(db, durum="online", reachable=True, temas=1.0)
    k = hazir.degerlendir(
        saglik=db.get(DeviceRuntimeHealth, KOD), legacy_status=None, simdi=AN
    )
    assert k.hazir is True
    assert k.sebep == hazir.HAZIR
    assert k.kaynak == hazir.KAYNAK_SOZLESME


def test_O2_esik_TAM_SINIRDA_hala_taze(db):
    _saglik(db, durum="online", reachable=True, gozlem=AN)
    tam = AN + timedelta(seconds=hazir.RUNTIME_STALE_AFTER_SEC)
    assert hazir.degerlendir(
        saglik=db.get(DeviceRuntimeHealth, KOD), legacy_status=None, simdi=tam
    ).hazir is True


def test_O3_naive_updated_at_UTC_sayilir(db):
    """SQLite tzinfo'yu kaybeder; naive degeri yerel saat sanmak UTC+3'te
    her gozlemi 3 saat bayat gosterirdi."""
    satir = _saglik(db, durum="online", reachable=True)
    satir.updated_at = AN.replace(tzinfo=None)
    db.flush()
    assert hazir.degerlendir(saglik=satir, legacy_status=None, simdi=AN).hazir is True


# ===========================================================================
# P) Eski gateway alanlari — cokme YOK, guvensiz komut YOK
# ===========================================================================


def test_P_bilinmeyen_durum_HAZIR_SAYILMAZ(db):
    """Ileride eklenen bir `connection_state` sessizce 'hazir' olmamali."""
    _saglik(db, durum="gelecekteki_durum", reachable=True)
    assert (
        hazir.degerlendir(
            saglik=db.get(DeviceRuntimeHealth, KOD), legacy_status=None, simdi=AN
        ).hazir
        is False
    )


def test_P2_bos_saglik_satiri_COKMEZ(db):
    """Tum alanlar None — hicbir sey iddia edilmez, cokme de olmaz."""
    satir = DeviceRuntimeHealth(device_code="BOS", gateway_code="GW-1")
    k = hazir.degerlendir(saglik=satir, legacy_status=None, simdi=AN)
    assert k.hazir is False


def test_P3_session_started_epoch_UYDURULMADI():
    """Alan sozlesmede YOK; predicate onu TAHMIN ETMEMELI.

    Gateway 1.15.1 ile gelirse guclendirme terimi olarak eklenecek; o gune
    kadar kurulamayan bir sarti "varmis gibi" saymak, uyuyan cihaza komut
    gonderilmesine yol acardi.
    """
    import pathlib

    kaynak = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app/services/device_session_readiness.py"
    ).read_text(encoding="utf-8")
    # Yalnizca YORUMDA gecmeli, calisan kodda DEGIL.
    kod = "\n".join(
        s for s in kaynak.splitlines() if not s.strip().startswith("#")
    )
    assert "saglik.session_started_epoch" not in kod


# ===========================================================================
# PARITE — turetilmis esik ve index kosulu tek yerden
# ===========================================================================


def test_stale_esigi_FRONTEND_ile_AYNI():
    """Kor kopya degil KANITLI AYNA: iki taraf ayni sozlesmeden turetiyor."""
    import pathlib
    import re

    ts = (
        pathlib.Path(__file__).resolve().parents[3]
        / "apps/frontend-web/src/shared/deviceRuntimeState.ts"
    ).read_text(encoding="utf-8")
    m = re.search(r"RUNTIME_STALE_AFTER_MS\s*=\s*([0-9_]+)", ts)
    assert m, "frontend esigi bulunamadi"
    frontend_sn = int(m.group(1).replace("_", "")) // 1000
    assert frontend_sn == hazir.RUNTIME_STALE_AFTER_SEC


def test_kismi_index_kosulu_MIGRATION_ile_AYNI():
    """Ayrisirsa index yanlis satirlari kapsar ve tek-acik-niyet garantisi
    sessizce kaybolur."""
    import importlib.util
    import pathlib

    from app.models.device_config_application import _ACIK_WHERE

    yol = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic_migrations/versions/2026_08_21_0001-0075_device_config_application.py"
    )
    spec = importlib.util.spec_from_file_location("m75", yol)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.ACIK_WHERE == _ACIK_WHERE


# ===========================================================================
# DENETIM IZI — her asama gorunur, ama HER SAGLIK POST'unda olay YOK
# ===========================================================================


def _olaylar(db) -> list[str]:  # noqa: ANN001
    from app.models.system_event import SystemEvent

    return [
        e.event_type
        for e in db.scalars(select(SystemEvent).order_by(SystemEvent.id)).all()
    ]


def test_denetim_izi_TUM_asamalari_kaydeder(db):
    _niyet_ac(db)
    _saglik(db, durum="smart_idle", reachable=False)
    _ilerlet(db)

    uyanma = AN + timedelta(hours=1)
    _saglik(db, durum="online", reachable=True, gozlem=uyanma)
    cmd = _ilerlet(db, uyanma)
    _komutu_sonuclandir(db, cmd, ok=True)
    _ilerlet(db, uyanma + timedelta(minutes=1))

    db.add(
        DeviceConfigVersion(
            device_id=_cihaz(db).id, version=98, raw=ICERIK,
            source="cihazdan_cekildi", created_at=uyanma + timedelta(minutes=2),
        )
    )
    db.flush()
    _ilerlet(db, uyanma + timedelta(minutes=3))

    olaylar = _olaylar(db)
    for beklenen in (
        "config_command_queued",
        "config_command_delivered",
        "config_verified",
    ):
        assert beklenen in olaylar, f"{beklenen} olayi yazilmamis: {olaylar}"


def test_HER_SAGLIK_PARTISINDE_olay_URETILMEZ(db):
    """2 yillik FIFO olay kaydi saglik gurultusuyle dolmamali.

    600 cihaz x 300 saniyede bir snapshot; parti basina olay yazmak gercek
    denetim izini budatirdi.
    """
    _niyet_ac(db)
    _saglik(db, durum="online", reachable=True)
    cmd = _ilerlet(db)
    _komutu_sonuclandir(db, cmd, ok=True)
    _ilerlet(db, AN + timedelta(minutes=1))
    once = len(_olaylar(db))

    for i in range(2, 12):
        _saglik(db, durum="online", reachable=True, gozlem=AN + timedelta(minutes=i))
        _ilerlet(db, AN + timedelta(minutes=i))
    assert len(_olaylar(db)) == once, "tekrarlanan saglik partisi olay uretiyor"


def test_SUPERSEDE_olayi_yazilir(db):
    _niyet_ac(db, 1, b"V10")
    _niyet_ac(db, 2, b"V11", an=AN + timedelta(minutes=5))
    assert "config_superseded" in _olaylar(db)
