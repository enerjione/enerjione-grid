"""Kritik altyapi olaylari operatore GERCEKTEN ulasiyor mu?

KAPATILAN ACIK
--------------
`record_event(...)` yalnizca `system_events` tablosuna yaziyordu. Bildirim
zinciri sadece CIHAZ ALARMLARI icin isliyordu (alarm -> RabbitMQ ->
notification-worker -> kanallar). Yani

    telemetri tamponu tasti, olcumler islenmeden silindi   (critical)
    disk acil seviyede                                     (critical)
    zamanli yedek alinamadi                                (error)

olaylarinin hicbiri operatore ULASMIYORDU. Saha IPC'sinde bu, "sistem ayakta
ama veri kayboluyor" durumunun birinin arayuze bakmasina bagli kalmasi
demektir.

BU DOSYANIN KILITLEDIKLERI
--------------------------
1. Acik listedeki kritik olaylar bildirim uretir.
2. Listede olmayanlar ve dusuk seviyeliler URETMEZ.
3. Ayni olay 100 kez olussa bile bildirim SINIRLI kalir; denetim kaydi
   ise eksiksiz durur (bastirma yalnizca GONDERIME uygulanir).
4. Farkli olaylar birbirini bastirmaz.
5. Bildirim altyapisinin KENDI hatalari yeni bildirim uretmez (ozyineleme).
6. Kanal/kuyruk arizasi olayi ureten islemi DUSURMEZ.
7. Cihaz alarm akisi DEGISMEZ.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (tum tablolar metadata'ya kayitli olsun)
from app.db.base import Base
from app.models.enums import UserRole
from app.models.infra_notification import InfraNotificationState
from app.models.notification import Notification
from app.models.notification_settings import NotificationSettings
from app.models.system_event import SystemEvent
from app.models.user import User
from app.services import infra_notification_service as ins
from app.services.event_service import record_event


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture()
def muhendis(db):
    u = User(
        username="muh",
        email="muh@ornek.local",
        phone_number="+900000000",
        hashed_password="x",
        full_name="Muhendis",
        role=UserRole.ENGINEER,
    )
    db.add(u)
    db.commit()
    return u


def _olay(
    db,
    *,
    event_type: str,
    severity: str = "critical",
    category: str = "telemetry",
    metadata: dict | None = None,
    yas_dk: float = 0.0,
) -> SystemEvent:
    record_event(
        db,
        category=category,
        event_type=event_type,
        severity=severity,
        message=f"{event_type} olustu",
        metadata=metadata,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=yas_dk),
    )
    db.commit()
    return db.scalars(
        select(SystemEvent).order_by(SystemEvent.id.desc())
    ).first()


def _bildirimler(db) -> list[Notification]:
    return list(db.scalars(select(Notification)).all())


# ==========================================================================
# Test 1 — Kritik disk olayi bildirim uretir
# ==========================================================================


def test_1_kritik_disk_olayi_bildirim_uretir(db, muhendis):
    _olay(
        db,
        event_type="disk_guard_emergency",
        severity="critical",
        category="system",
        metadata={"path": "/var/lib/e1-backups"},
    )

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 1
    bildirimler = _bildirimler(db)
    assert len(bildirimler) == 1
    assert bildirimler[0].recipient_username == "muh"
    assert bildirimler[0].severity == "critical"
    # Soguma defterine yazildi mi (yeniden gondermeyi engelleyen sey bu).
    durum = db.scalars(select(InfraNotificationState)).all()
    assert len(durum) == 1
    assert durum[0].event_type == "disk_guard_emergency"
    assert durum[0].resource_key == "/var/lib/e1-backups"


# ==========================================================================
# Test 2 — info seviyeli olay bildirim URETMEZ
# ==========================================================================


def test_2_info_olay_bildirim_uretmez(db, muhendis):
    # Listede OLMAYAN bir olay
    _olay(db, event_type="device_updated", severity="info", category="device")
    # Listede OLAN ama seviyesi dusuk bir olay: toplu hattin birikimi
    # beklenen bir durumdur, operatoru uyandirmamali.
    _olay(
        db,
        event_type="telemetry_backlog_high",
        severity="warning",
        metadata={"sinif": "bulk"},
    )

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 0
    assert _bildirimler(db) == []


def test_2b_karar_fonksiyonu_seviye_politikasini_uygular():
    """critical -> evet; error -> yalnizca acik listede; warning/info -> hayir."""
    assert ins.bildirim_gerekir_mi("disk_guard_emergency", "critical") is True
    assert ins.bildirim_gerekir_mi("backup_scheduled_failed", "error") is True
    # Ayni tip warning gelirse: seviye politikasi reddeder.
    assert ins.bildirim_gerekir_mi("disk_guard_emergency", "warning") is False
    assert ins.bildirim_gerekir_mi("disk_guard_emergency", "info") is False
    # Acik listede olmayan bir `critical` bile gecmez.
    assert ins.bildirim_gerekir_mi("device_deleted", "critical") is False


# ==========================================================================
# Test 3 — backup_scheduled_failed bildirim uretir
# ==========================================================================


def test_3_yedek_hatasi_bildirim_uretir(db, muhendis):
    _olay(
        db,
        event_type="backup_scheduled_failed",
        severity="error",
        category="backup",
        metadata={"reason": "pg_dump non-zero exit"},
    )

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 1
    (bildirim,) = _bildirimler(db)
    assert "backup" in (bildirim.link or "")


# ==========================================================================
# Test 4 — Kritik telemetri VERI KAYBI olayi bildirim uretir
# ==========================================================================


def test_4_telemetri_veri_kaybi_bildirim_uretir(db, muhendis):
    _olay(
        db,
        event_type="telemetry_tampon_tasmasi",
        severity="critical",
        metadata={"sinif": "prio", "dusen_ust_sinir": 12345},
    )

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 1
    (bildirim,) = _bildirimler(db)
    md = json.loads(bildirim.metadata_json or "{}")
    assert md.get("event_type") == "telemetry_tampon_tasmasi"


# ==========================================================================
# Test 5 — Ayni olay 100 kez: bildirim SINIRLI, denetim kaydi EKSIKSIZ
# ==========================================================================


def test_5_ayni_olay_yuz_kez_tek_bildirim(db, muhendis):
    for _ in range(100):
        _olay(
            db,
            event_type="disk_guard_emergency",
            severity="critical",
            category="system",
            metadata={"path": "/"},
        )

    # Tek turda 100 olay -> tek bildirim.
    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()
    assert gonderilen == 1
    assert len(_bildirimler(db)) == 1

    # Ardisik turlar da soguma penceresi icinde SESSIZ kalmali.
    for _ in range(5):
        ins.dispatch_pending_infra_notifications(db)
        db.commit()
    assert len(_bildirimler(db)) == 1

    # DENETIM KAYDI BASTIRILMADI: 100 olayin 100'u da tabloda.
    assert (
        db.scalar(
            select(func.count()).select_from(SystemEvent).where(
                SystemEvent.event_type == "disk_guard_emergency"
            )
        )
        == 100
    )
    # BASTIRMA OPERATORDEN GIZLENMEZ: tek bildirimin metni, kac olayin tek
    # uyariya katlandigini soylemeli. Aksi halde operator "tek seferlik bir
    # olay" sanip suren bir arizayi kucumserdi.
    (bildirim,) = _bildirimler(db)
    assert "100 kez" in (bildirim.body or ""), (
        f"bildirim metni bastirilan olay sayisini raporlamiyor: {bildirim.body!r}"
    )
    # Sayac gonderim aninda SIFIRLANIR (rapor edildi, borc kapandi) ve
    # filigran en yeni olaya tasinir.
    durum = db.scalars(select(InfraNotificationState)).one()
    assert durum.suppressed_count == 0
    assert durum.last_event_id == db.scalar(
        select(func.max(SystemEvent.id))
    ), "filigran en yeni olaya tasinmali, yoksa ayni olaylar tekrar sayilir"


def test_5c_bastirilan_sayaci_her_turda_YENIDEN_saymaz(db, muhendis):
    """FILIGRAN regresyonu.

    Geriye bakis penceresi (60 dk) tarama araligindan (60 sn) cok genis:
    ayni olay dizisi her turda yeniden gorulur. Filigran olmadan
    `suppressed_count` her turda yeniden sayiliyordu; 100 olay + 15 tur
    ~1500 gibi tamamen yanlis bir sayi uretir ve operatore "bu uyari 1500
    kez olustu" denirdi.
    """
    for _ in range(10):
        _olay(db, event_type="disk_guard_emergency", severity="critical",
              category="system", metadata={"path": "/"})

    ins.dispatch_pending_infra_notifications(db)
    db.commit()
    assert len(_bildirimler(db)) == 1

    # Soguma penceresi icinde bes tur daha — YENI OLAY YOK.
    for _ in range(5):
        ins.dispatch_pending_infra_notifications(db)
        db.commit()

    durum = db.scalars(select(InfraNotificationState)).one()
    assert durum.suppressed_count == 0, (
        f"yeni olay olmadigi halde bastirma sayaci arttti "
        f"({durum.suppressed_count}) — filigran calismiyor"
    )

    # Simdi 3 YENI olay: yalnizca bunlar sayilmali.
    for _ in range(3):
        _olay(db, event_type="disk_guard_emergency", severity="critical",
              category="system", metadata={"path": "/"})
    for _ in range(4):  # dort tur — ayni uc olay tekrar tekrar gorulur
        ins.dispatch_pending_infra_notifications(db)
        db.commit()

    db.refresh(durum)
    assert durum.suppressed_count == 3, (
        f"uc yeni olay bekleniyordu, sayac {durum.suppressed_count} — "
        f"ayni olaylar birden fazla turda sayiliyor"
    )
    assert len(_bildirimler(db)) == 1, "soguma penceresinde ikinci bildirim gitmemeli"


def test_5d_soguma_dolsa_bile_yeni_olay_yoksa_bildirim_gitmez(db, muhendis):
    """Ariza bittiyse soguma dolunca kendiliginden yeni uyari cikmamali."""
    _olay(db, event_type="disk_guard_emergency", severity="critical",
          category="system", metadata={"path": "/"})
    ins.dispatch_pending_infra_notifications(db)
    db.commit()
    assert len(_bildirimler(db)) == 1

    durum = db.scalars(select(InfraNotificationState)).one()
    durum.last_notified_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    # YENI OLAY YOK — eski olay hala pencerede ama hesaba katilmis.
    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 0
    assert len(_bildirimler(db)) == 1


def test_5b_soguma_dolunca_yeniden_bildirilir(db, muhendis):
    _olay(db, event_type="disk_guard_emergency", severity="critical",
          category="system", metadata={"path": "/"})
    ins.dispatch_pending_infra_notifications(db)
    db.commit()
    assert len(_bildirimler(db)) == 1

    # Soguma penceresini geride birak.
    durum = db.scalars(select(InfraNotificationState)).one()
    durum.last_notified_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    _olay(db, event_type="disk_guard_emergency", severity="critical",
          category="system", metadata={"path": "/"})
    ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert len(_bildirimler(db)) == 2, (
        "soguma doldugunda SURMEKTE OLAN ariza yeniden bildirilmeli — aksi "
        "halde tek bir uyaridan sonra sonsuza dek sessiz kalinirdi"
    )


# ==========================================================================
# Test 6 — Iki farkli kritik olay birbirini bastirmaz
# ==========================================================================


def test_6_farkli_olaylar_birbirini_bastirmaz(db, muhendis):
    _olay(db, event_type="disk_guard_emergency", severity="critical",
          category="system", metadata={"path": "/"})
    _olay(db, event_type="telemetry_tampon_tasmasi", severity="critical",
          metadata={"sinif": "prio"})

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 2
    assert len(_bildirimler(db)) == 2


def test_6b_ayni_tipin_farkli_kaynaklari_ayri_kovadir(db, muhendis):
    """Oncelikli hattin tasmasi, toplu hattin sogumasiyla bastirilmamali."""
    _olay(db, event_type="telemetry_tampon_tasmasi", severity="critical",
          metadata={"sinif": "bulk"})
    _olay(db, event_type="telemetry_tampon_tasmasi", severity="critical",
          metadata={"sinif": "prio"})

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 2, "sinif basina ayri soguma kovasi olmali"
    kovalar = {d.resource_key for d in db.scalars(select(InfraNotificationState)).all()}
    assert kovalar == {"bulk", "prio"}


# ==========================================================================
# Test 7 — Kanal/kuyruk arizasi ana islemi DUSURMEZ
# ==========================================================================


def test_7_kanal_arizasi_istisna_sizdirmaz(db, muhendis, monkeypatch):
    """Tum kanallar patlasa bile tarama turu temiz bitmeli."""
    ayar = NotificationSettings(
        id=1, smtp_enabled=True, smtp_host="yok.local",
        sms_enabled=True, sms_provider="generic",
        telegram_enabled=True, telegram_chat_ids="123",
    )
    db.add(ayar)
    db.commit()

    # Kanallarin GERCEKTEN denendigini sayiyoruz; aksi halde bu test bos
    # gecerdi (hicbir kanal cagrilmadan "istisna sizmadi" demek kolaydir).
    denemeler: list[str] = []

    def _patla(kanal):
        def _f(*a, **k):
            denemeler.append(kanal)
            raise RuntimeError("kanal cokmus")
        return _f

    monkeypatch.setattr(
        "app.services.notification_test_service.send_smtp_test", _patla("smtp")
    )
    monkeypatch.setattr(
        "app.services.notification_test_service.send_sms_test", _patla("sms")
    )
    monkeypatch.setattr(
        "app.services.notification_test_service.send_telegram_test", _patla("telegram")
    )

    _olay(db, event_type="disk_guard_emergency", severity="critical",
          category="system", metadata={"path": "/"})

    # ISTISNA FIRLATMAMALI.
    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert set(denemeler) == {"smtp", "sms", "telegram"}, (
        f"beklenen tum kanallar denenmedi: {denemeler} — test bos geciyor olabilir"
    )
    # Uygulama ici bildirim yine yazildi (kanal yapilandirmasindan bagimsiz).
    assert gonderilen == 1
    assert len(_bildirimler(db)) == 1


def test_7b_olay_ureticisi_bildirimden_BAGIMSIZ(db):
    """`record_event` bildirim altyapisini HIC cagirmaz — yapisal yalitim.

    Bu, "notification enqueue FAILED durumunda disk_guard calismaya devam
    etmeli" sartinin en gucu kanitidir: cagri hic yoktur, dolayisiyla
    basarisiz da olamaz.
    """
    import inspect

    from app.services import event_service

    kaynak = inspect.getsource(event_service)
    for yasak in ("infra_notification", "notification_dispatch", "smtp", "requests"):
        assert yasak not in kaynak, (
            f"event_service icinde '{yasak}' gecmemeli — olay yazma yolu "
            f"ag I/O'sundan tamamen ayri kalmali."
        )

    # Ve gercekten: bildirim modulu hic import edilmemis olsa bile olay yazilir.
    _olay(db, event_type="disk_guard_emergency", severity="critical",
          category="system", metadata={"path": "/"})
    assert db.scalar(select(func.count()).select_from(SystemEvent)) == 1


def test_7c_sweeper_turu_istisna_yutmali(monkeypatch):
    """Tarayici ipligi, gonderim patlasa bile ayakta kalmali.

    ONEMLI: `_run`in GERCEKTEN dongu govdesine girip istisnayi yuttugunu
    dogruluyoruz. Onceki hali `_stop.set()` deyip `_run()` cagiriyordu; o
    durumda `while not self._stop.is_set()` HIC calismiyor ve test hicbir
    sey kanitlamiyordu (bos gecen test).
    """
    from app.services import infra_notify_sweeper as sw

    turlar: list[int] = []

    def _patla(*a, **k):
        turlar.append(1)
        raise RuntimeError("DB gitti")

    monkeypatch.setattr(sw, "SessionLocal", _patla)
    worker = sw.InfraNotifySweeper()

    # Tek tur patlasin, sonra dongu bitsin.
    with pytest.raises(RuntimeError):
        worker._sweep()
    assert turlar, "_sweep gercekten cagrilmadi"

    # `_run`: acilis beklemesini ve tur arasi beklemeyi kisaltiyoruz; ilk
    # turdan sonra dur. Boylece dongu govdesi GERCEKTEN kosar ve
    # istisnayi yuttugunu gozlemleriz.
    turlar.clear()
    monkeypatch.setattr(sw, "_tick_sec", lambda: 15)

    gercek_wait = worker._stop.wait

    def _wait(timeout=None):
        # Acilis beklemesi (45 sn) ve tur arasi bekleme aninda donsun;
        # ilk turdan sonra dongu bitsin diye stop'u set ediyoruz.
        if turlar:
            worker._stop.set()
        return gercek_wait(0)

    monkeypatch.setattr(worker._stop, "wait", _wait)

    worker._run()  # ISTISNA DISARI SIZMAMALI

    assert turlar, (
        "_run dongu govdesine hic girmedi — test bos geciyor "
        "(istisnanin yutuldugu kanitlanmiyor)"
    )


def test_7d_uzun_olay_mesaji_baslik_kolonunu_TASIRMAZ(db, muhendis):
    """`Notification.title` VARCHAR(200); baslik kirpilmali.

    YASANAN SINIF: `pg_dump ikilisi bulunamadi` mesaji 217 karakter; ondan
    turetilen baslik 258 olur. Postgres INSERT'i `StringDataRightTruncation`
    ile duser. Hata COMMIT aninda olustugu icin kanallara ZATEN gonderilmis
    olur, ama soguma damgasi yazilamaz -> ayni bildirim her turda tekrar
    gider. Yani bu modulun onlemek icin var oldugu firtinanin ta kendisi.

    SQLite uzunlugu ZORLAMAZ, bu yuzden uzunluk DOGRUDAN olculuyor.
    """
    uzun = "x" * 480  # SystemEvent.message String(500) sinirina yakin
    record_event(
        db,
        category="backup",
        event_type="backup_scheduled_failed",
        severity="error",
        message=f"Zamanli yedek alinamadi: {uzun}",
    )
    db.commit()
    olay = db.scalars(select(SystemEvent).order_by(SystemEvent.id.desc())).first()

    baslik, govde = ins._metin(olay, ins.ACIK_LISTE["backup_scheduled_failed"], 0)

    assert len(baslik) <= 200, (
        f"baslik {len(baslik)} karakter — Notification.title VARCHAR(200)'u "
        f"asiyor ve Postgres'te INSERT duser"
    )
    # Bilgi kaybi olmamali: tam mesaj govdede duruyor.
    assert uzun in govde

    # Uctan uca: gercekten yazilabiliyor ve tek bildirim uretiyor.
    assert ins.dispatch_pending_infra_notifications(db) == 1
    db.commit()
    (bildirim,) = _bildirimler(db)
    assert len(bildirim.title) <= 200


def test_7e_zehirli_kova_diger_kovalarin_damgasini_goturmez(db, muhendis, monkeypatch):
    """Bir kovadaki DB hatasi ayni taramadaki digerlerini DUSURMEMELI.

    Savepoint olmasaydi tek zehirli kova tum transaction'i abort ederdi ve
    HICBIR soguma damgasi yazilamazdi — tum kovalar bir sonraki turda
    yeniden gonderilirdi.
    """
    _olay(db, event_type="disk_guard_emergency", severity="critical",
          category="system", metadata={"path": "/"})
    _olay(db, event_type="telemetry_tampon_tasmasi", severity="critical",
          metadata={"sinif": "prio"})

    gercek = ins._kanallara_gonder

    def _secici_patla(db_, olay, kural, bastirilan):
        if olay.event_type == "disk_guard_emergency":
            raise RuntimeError("bu kova zehirli")
        return gercek(db_, olay, kural, bastirilan)

    monkeypatch.setattr(ins, "_kanallara_gonder", _secici_patla)

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 1, "saglam kova yine de gonderilmeliydi"
    damgalar = {d.event_type for d in db.scalars(select(InfraNotificationState)).all()}
    assert damgalar == {"telemetry_tampon_tasmasi"}, (
        f"zehirli kova saglam kovanin damgasini goturdu: {damgalar}"
    )


def test_7f_sweeper_lider_isine_kayitli(monkeypatch):
    """Ozellik GERCEKTEN devreye giriyor mu — `leader.register` kanitı.

    Servis ve testleri kusursuz olsa bile `main.py`de kayit yoksa hicbir
    sey kosmaz: ozellik olu kod olur. Bu depoda arka plan isleri yalnizca
    `leader.register(...)` ile ayaga kalkar.
    """
    import app.main  # noqa: F401  (kayitlarin islenmesi icin import yeterli)
    from app.core.service_role import leader

    isler = [ad for ad, _s, _t in leader._jobs]
    assert "infra_notify_sweeper" in isler, (
        f"infra_notify_sweeper lider islerine kayitli degil; kayitli olanlar: "
        f"{isler}"
    )


# ==========================================================================
# Test 8 — Bildirim kaynakli hata olaylari OZYINELEME uretmez
# ==========================================================================


@pytest.mark.parametrize(
    "event_type",
    [
        "notification_smtp_test_failed",
        "notification_sms_test_failed",
        "notification_telegram_test_failed",
        "notification_whatsapp_web_test_failed",
        "alarm_notification_failed",
        "infra_notification_failed",
    ],
)
def test_8_bildirim_hatalari_yeni_bildirim_uretmez(db, muhendis, event_type):
    _olay(db, event_type=event_type, severity="error", category="notification")

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 0, (
        f"'{event_type}' bildirim uretti. Zincir: gonderim basarisiz -> olay "
        f"-> bildirim denemesi -> yine basarisiz -> ... Kalici bir SMTP "
        f"arizasinda bu kendini besleyen sonsuz bir dongudur."
    )
    assert _bildirimler(db) == []


def test_8b_yasak_liste_acik_listeyle_cakismaz():
    """Acik listeye yanlislikla bir bildirim olayi girerse import patlamali."""
    for tip in ins.ACIK_LISTE:
        assert not ins.ozyineleme_riski_var_mi(tip), (
            f"'{tip}' hem acik listede hem yasakli onekte — ozyineleme riski"
        )
    # Kalkanin kendisi de calisiyor olmali.
    assert ins.ozyineleme_riski_var_mi("notification_smtp_test_failed") is True
    assert ins.ozyineleme_riski_var_mi("infra_notification_failed") is True
    assert ins.ozyineleme_riski_var_mi("disk_guard_emergency") is False


# ==========================================================================
# Test 9 — Mevcut CIHAZ ALARM akisi degismedi
# ==========================================================================


def test_9_cihaz_alarm_akisi_degismedi(db):
    """Altyapi bildirimi alarm zincirine DOKUNMAMALI.

    Alarm bildirimi hala `notification_hook_service.handle_alarm_created`
    uzerinden gider ve altyapi tarayicisi alarm olaylarini GORMEZ.
    """
    # `alarm_created` / `alarm_triggered` acik listede OLMAMALI.
    for tip in ("alarm_created", "alarm_triggered", "alarm_notification_dispatched"):
        assert tip not in ins.ACIK_LISTE, (
            f"'{tip}' altyapi listesine girmis — cihaz alarmlari kendi "
            f"zinciriyle gider, burada tekrar bildirilmemeli (cift bildirim)."
        )

    # Kritik seviyeli bir alarm olayi bile altyapi bildirimi URETMEZ.
    _olay(db, event_type="alarm_created", severity="critical", category="alarm")
    assert ins.dispatch_pending_infra_notifications(db) == 0

    # Alarm zincirinin giris noktasi hala yerinde ve cagrilabilir.
    from app.services import notification_hook_service

    assert callable(notification_hook_service.handle_alarm_created)


def test_9b_altyapi_olayi_alarm_satiri_URETMEZ(db, muhendis):
    """Altyapi olaylari cihaz alarmi olarak gosterilmemeli."""
    from app.models.alarm import AlarmEvent

    _olay(db, event_type="telemetry_tampon_tasmasi", severity="critical",
          metadata={"sinif": "prio"})
    ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert db.scalar(select(func.count()).select_from(AlarmEvent)) == 0, (
        "altyapi olayi icin Alarm satiri uretilmis — `Alarm.device_id` "
        "zorunludur ve 'disk doldu' hicbir cihaza ait degildir."
    )


# ==========================================================================
# Pencere / yapilandirma
# ==========================================================================


def test_pencere_disindaki_eski_olay_bildirilmez(db, muhendis):
    """Geriye bakis penceresinden eski olaylar acilista SMS firtinasi yapmasin."""
    _olay(
        db,
        event_type="disk_guard_emergency",
        severity="critical",
        category="system",
        metadata={"path": "/"},
        yas_dk=60 * 24,  # 1 gun once
    )

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 0
    assert _bildirimler(db) == []


def test_alici_yoksa_uyari_yayina_duser(db):
    """Engineer/installer yoksa uyari sessizce kaybolmamali."""
    _olay(db, event_type="disk_guard_emergency", severity="critical",
          category="system", metadata={"path": "/"})

    gonderilen = ins.dispatch_pending_infra_notifications(db)
    db.commit()

    assert gonderilen == 1
    (bildirim,) = _bildirimler(db)
    assert bildirim.recipient_username is None, "yayin bildirimi olmali"
