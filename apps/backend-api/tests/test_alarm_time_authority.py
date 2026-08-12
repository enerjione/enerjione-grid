"""ZAMAN OTORITESI: alarm ve ariza saatleri DAIMA backend saatidir.

KURAL
-----
Cihazin/gateway'in bildirdigi zaman TESHIS verisidir; alarmin ve arizanin
"ne zaman oldu"su ise backend'in olayi ALGILADIGI andir. Bu ikisi asla yer
degistirmez.

NEDEN — cihaz saatini alarm saati yapmanin somut sonuclari:
  * RTC pili biten cihaz 2000-01-01 damgalar. Alarm listede 26 yil once
    gorunur; liste varsayilan olarak yeniden eskiye sirali oldugu icin
    operator o alarmi HIC gormez. Yani alarm uretilir ama goruntusuz kalir.
  * Saati ileri kaymis bir cihazin alarmi listenin tepesine cakilir ve gercek
    yeni alarmlari asagi iter.
  * Mudahale/SLA suresi "biz ne zaman haberdar olduk"tan itibaren isler.
    Cihaz saatine baglanirsa olcum anlamini yitirir.
  * `AlarmEvent.created_at` indeksli ve dedup sorgularinda `order_by(...desc())`
    ile kullaniliyor (api/internal.py, alarm_engine_service). Monotonlugunu
    kaybederse "en son acik alarm" yanlis satiri secer.

Cihazin kendi zamani AYRI kolonlarda duruyor (telemetry.device_event_at /
telemetry_history.device_event_at) ve saat bozuklugu kullaniciya sinyal
statusunde bildiriliyor — ama alarm akisina KARISMIYOR.

Bu dosya kurali iki yonden kilitler: davranis (gercekten simdi mi yaziliyor)
ve yapi (biri ileride cihaz zamanini oraya baglarsa AST kontrolu kirilir).
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# Cihaz saatinin 26 yil geride oldugu senaryo — RTC pili bitmis bir Smart
# Navigator'in gercekte urettigi damga.
BOZUK_CIHAZ_SAATI = datetime(2000, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------- davranis


@pytest.fixture()
def db_session():
    """Alarm yolunun dokundugu tablolarla sqlite in-memory oturum."""
    from app.models.alarm import AlarmDailyCount, AlarmEvent
    # Haberlesme alarmi artik STANDART KURALDAN uretiliyor (seviye, baslik,
    # hat arizasi uretip uretmeyecegi); tablo olmadan uc okunamaz.
    from app.models.alarm_rule import AlarmRule
    from app.models.outbox_event import OutboxEvent
    from app.models.system_event import SystemEvent

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            AlarmEvent.__table__,
            # Alarm satiri eklenince gunluk sayac da artiyor (ORM olayi,
            # bkz. models/alarm.py) — tablosu olmadan alarm ACILAMAZ.
            AlarmDailyCount.__table__,
            AlarmRule.__table__,
            SystemEvent.__table__,
            OutboxEvent.__table__,
        ],
    )
    # autoflush ACIK: `handle_telemetry_alarm_event` commit ETMEZ (cagiran
    # transaction'i yonetir). Testteki sorgunun bekleyen INSERT'i gormesi icin
    # flush gerekiyor.
    factory = sessionmaker(bind=engine, autoflush=True, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def test_bozuk_cihaz_saati_alarm_saatini_ETKILEMEZ(db_session):
    """En kritik davranis testi.

    Payload cihazin/gateway'in saatini 2000 yilinda bildiriyor. Alarm yine de
    SIMDI acilmali; aksi halde operator alarmi listede hic gormez.
    """
    from app.models.alarm import AlarmEvent
    from app.services.alarm_engine_service import handle_telemetry_alarm_event

    onces = datetime.now(timezone.utc)
    handle_telemetry_alarm_event(
        db_session,
        {
            "device_id": 1,
            "device_code": "DEV1",
            "device_name": "Direk-12",
            "signal_key": "master.comm",
            "quality": "bad",
            # Cihaz/gateway ne derse desin:
            "source_timestamp": BOZUK_CIHAZ_SAATI.isoformat(),
            "device_event_at": BOZUK_CIHAZ_SAATI.isoformat(),
            "timestamp_quality": "invalid",
        },
    )
    sonras = datetime.now(timezone.utc)

    alarm = db_session.query(AlarmEvent).one()
    created = alarm.created_at
    if created.tzinfo is None:  # sqlite tz bilgisini dusurur
        created = created.replace(tzinfo=timezone.utc)

    assert created >= onces - timedelta(seconds=1), (
        f"alarm saati gecmise dustu: {created} — cihaz saati sizmis olabilir"
    )
    assert created <= sonras + timedelta(seconds=1)
    assert created.year != 2000, "cihazin bozuk saati alarm saatine yazilmis"


def test_ileri_kaymis_cihaz_saati_de_ETKILEMEZ(db_session):
    """Gelecege damgali cihaz alarmi listenin tepesine cakmamali."""
    from app.models.alarm import AlarmEvent
    from app.services.alarm_engine_service import handle_telemetry_alarm_event

    gelecek = datetime.now(timezone.utc) + timedelta(days=365)
    handle_telemetry_alarm_event(
        db_session,
        {
            "device_id": 2,
            "device_code": "DEV2",
            "device_name": "Direk-13",
            "signal_key": "master.comm",
            "quality": "offline",
            "source_timestamp": gelecek.isoformat(),
            "device_event_at": gelecek.isoformat(),
        },
    )

    alarm = db_session.query(AlarmEvent).one()
    created = alarm.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    assert created < gelecek - timedelta(days=300), (
        "alarm gelecege damgalanmis — cihaz saati sizmis"
    )


def test_saat_bilgisi_HIC_gelmese_de_alarm_acilir(db_session):
    """0.4.x gateway zaman alani gondermez; alarm yine normal acilmali."""
    from app.models.alarm import AlarmEvent
    from app.services.alarm_engine_service import handle_telemetry_alarm_event

    handle_telemetry_alarm_event(
        db_session,
        {
            "device_id": 3,
            "device_code": "DEV3",
            "device_name": "Direk-14",
            "signal_key": "master.comm",
            "quality": "invalid",
        },
    )
    assert db_session.query(AlarmEvent).count() == 1


# -------------------------------------------------------------------- yapi
#
# Davranis testi yalnizca BUGUNKU yollari korur. Yarin biri yeni bir alarm
# olusturma yolu ekleyip oraya cihaz zamanini baglarsa davranis testi bunu
# gormez. Asagidaki AST kontrolu tum dosyalari tarar.

# Alarm/ariza zaman alanlarina BAGLANMASI YASAK isimler. Hepsi cihazdan veya
# gateway'den gelen zaman kaynaklaridir.
_YASAK_ZAMAN_KAYNAKLARI = frozenset(
    {
        "source_timestamp",
        "device_event_at",
        "last_update_at",
        "last_seen_at",
    }
)

# Zaman otoritesi kurali bu alanlar icin gecerlidir.
_KORUNAN_ALANLAR = {
    "AlarmEvent": ("created_at", "acknowledged_at", "reset_at"),
    "FaultEvent": ("opened_at", "resolved_at", "closed_at"),
}


def _isimleri_topla(node: ast.AST) -> set[str]:
    """Bir ifade agacindaki tum Name id'leri ve Attribute adlari."""
    bulunan: set[str] = set()
    for alt in ast.walk(node):
        if isinstance(alt, ast.Name):
            bulunan.add(alt.id)
        elif isinstance(alt, ast.Attribute):
            bulunan.add(alt.attr)
    return bulunan


def _ihlalleri_bul() -> list[str]:
    ihlaller: list[str] = []
    for py in APP_ROOT.rglob("*.py"):
        try:
            agac = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for cagri in ast.walk(agac):
            if not isinstance(cagri, ast.Call):
                continue
            hedef = cagri.func
            ad = hedef.id if isinstance(hedef, ast.Name) else None
            if ad not in _KORUNAN_ALANLAR:
                continue
            for kw in cagri.keywords:
                if kw.arg not in _KORUNAN_ALANLAR[ad]:
                    continue
                sizanlar = _isimleri_topla(kw.value) & _YASAK_ZAMAN_KAYNAKLARI
                if sizanlar:
                    ihlaller.append(
                        f"{py.relative_to(APP_ROOT.parent)}:{kw.value.lineno} "
                        f"{ad}.{kw.arg} <- {sorted(sizanlar)}"
                    )
    return ihlaller


def test_cihaz_ZAMANI_alarm_ariza_saatlerine_BAGLANMAMIS():
    """Kurali kod tabaninin tamaminda uygular.

    Kirmizi olursa mesaj tam olarak hangi dosya/satirda cihaz zamaninin bir
    alarm/ariza zaman alanina baglandigini soyler.
    """
    ihlaller = _ihlalleri_bul()
    assert not ihlaller, (
        "Cihaz/gateway zamani alarm veya ariza saatine baglanmis:\n  "
        + "\n  ".join(ihlaller)
        + "\n\nAlarm saati DAIMA backend'in algiladigi an olmalidir "
        "(datetime.now(timezone.utc)). Cihaz zamani teshis icin "
        "telemetry.device_event_at kolonunda durur."
    )


def test_AST_kontrolu_gercekten_YAKALIYOR():
    """Kontrolun kendisi bozulursa sessizce yesil kalmasin.

    Yukaridaki test bos liste bekliyor; kontrol yanlislikla hicbir sey
    taramaz hale gelse de yesil kalirdi. Bu test tarayicinin ihlali
    gercekten gordugunu dogrular.
    """
    kod = "AlarmEvent(device_id=1, created_at=payload.source_timestamp)"
    cagri = ast.parse(kod).body[0].value
    created_kw = next(kw for kw in cagri.keywords if kw.arg == "created_at")
    assert _isimleri_topla(created_kw.value) & _YASAK_ZAMAN_KAYNAKLARI

    # Mesru kullanim (mevcut alarmin saatini kopyalamak) YANLIS POZITIF
    # uretmemeli — `_finalize_acknowledged_reset` tam olarak bunu yapiyor.
    mesru = ast.parse("AlarmEvent(created_at=alarm.created_at)").body[0].value
    assert not (_isimleri_topla(mesru.keywords[0].value) & _YASAK_ZAMAN_KAYNAKLARI)


def test_taranan_dosya_sayisi_MAKUL():
    """Yol yanlis olursa (rglob bos donerse) test sessizce yesil kalirdi."""
    assert len(list(APP_ROOT.rglob("*.py"))) > 50, (
        f"app/ altinda beklenenden az dosya tarandi: {APP_ROOT}"
    )
