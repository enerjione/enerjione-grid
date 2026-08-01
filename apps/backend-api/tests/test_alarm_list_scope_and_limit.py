"""Alarm listesi: kapsam LIMIT'ten once, acik alarmlar kirpilmaz (A3 eksigi).

YASANAN IKI ARIZA
-----------------
`list_alarm_events` tek parcali bir sorguydu:

    select(AlarmEvent).order_by(created_at.desc()).limit(500)

ve `api/alarms.py` onu KAPSAMSIZ cagirip daraltmayi donen 500 satir uzerinde
Python'da yapiyordu. Servis `visible_device_ids` parametresini zaten
destekliyordu; A3 duzeltmesi ack-all/reset-all yollarina uygulanmis, LISTE
yoluna uygulanmamisti.

  1. OPERATOR KENDI ALARMLARINI GOREMIYOR
     LIMIT tum sahaya, kapsam sonra. 20 cihazdan sorumlu bir operator, 600
     cihazin en yeni 500 kaydi icinden kendine denk gelenleri goruyordu.

  2. ACIK ALARM SESSIZCE KAYBOLUYOR — ve HARITA YESILE DONUYOR
     `alarm_events` tablosunun retention'i yok ve 600 cihazda tek bir
     haberlesme kesintisi 600 satir uretebiliyor. 500'luk pencerenin disina
     dusen ACIK bir alarm API'den HIC donmuyordu. `DeviceMapTab` marker
     rengini bu listeden hesapladigi icin marker YESILE donuyordu.

     > Cuma aksami kalici hat arizasi olusur. Pazartesi sabahi operator
     > haritaya bakar: yesil. "Aktif Alarmlar" sekmesinde de yok. Ariza uc
     > gundur aciktir.

Bir ariza izleme urununde bu "yavaslama" degil SESSIZ YANLIS VERIDIR.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.services.alarm_engine_service import (
    RESOLVED_ALARM_LIMIT,
    list_alarm_events,
)

# Model kayitlari Base.metadata'ya girsin (create_all eksik tablo birakmasin).
import app.models  # noqa: F401,E402


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    # autoflush=True: pending insert'ler sorgudan ONCE yazilsin; aksi halde
    # test "veri yok" saniyordu.
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


def _alarm(db, *, device_id: int, reset: bool, dakika_once: int, baslik: str = "t"):
    a = AlarmEvent(
        device_id=device_id,
        level="critical",
        title=baslik,
        description="d",
        acknowledged=False,
        reset=reset,
        produces_fault=True,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=dakika_once),
    )
    db.add(a)
    return a


def test_acik_alarm_ESKI_olsa_bile_donuyor(db):
    """ASIL ARIZA: eski ama ACIK alarm kirpilmamali.

    Kirpilirsa haritadaki marker yesile doner ve operator arizayi hic gormez.
    """
    # Tavani asacak kadar YENI cozulmus alarm uret.
    for i in range(RESOLVED_ALARM_LIMIT + 50):
        _alarm(db, device_id=1, reset=True, dakika_once=i + 1)
    # ...ve hepsinden ESKI, hala ACIK tek bir alarm.
    _alarm(db, device_id=1, reset=False, dakika_once=100_000, baslik="KALICI-ARIZA")
    db.flush()

    sonuc = list_alarm_events(db)
    basliklar = {a.title for a in sonuc}
    assert "KALICI-ARIZA" in basliklar, (
        "eski ama ACIK alarm listeden dustu — haritadaki marker yesile doner "
        "ve ariza operatore hic gorunmez"
    )


def test_cozulmus_alarmlar_TAVANLI(db):
    """Cozulmusler sinirsiz buyur; onlarin kirpilmasi zararsiz ve gerekli."""
    for i in range(RESOLVED_ALARM_LIMIT + 200):
        _alarm(db, device_id=1, reset=True, dakika_once=i + 1)
    db.flush()

    sonuc = list_alarm_events(db)
    assert len(sonuc) == RESOLVED_ALARM_LIMIT, (
        f"cozulmus alarm tavani uygulanmadi: {len(sonuc)}"
    )


def test_kapsam_SQL_de_uygulaniyor_LIMIT_ten_once(db):
    """Kapsam daraltmasi LIMIT'ten SONRA olursa operator kendi alarmlarini kaybeder.

    Senaryo: operatorun 1 cihazi var, sahada 600 cihaz. Operatorun alarmlari
    en ESKI kayitlar olsun. Kapsam sonradan uygulansaydi, en yeni 500 kaydin
    hepsi baska cihazlara ait olacagi icin operator BOS liste gorurdu.
    """
    # Baskalarinin YENI cozulmus alarmlari (tavani tek basina doldurur)
    for i in range(RESOLVED_ALARM_LIMIT + 100):
        _alarm(db, device_id=99, reset=True, dakika_once=i + 1)
    # Operatorun ESKI cozulmus alarmlari
    for i in range(3):
        _alarm(db, device_id=7, reset=True, dakika_once=50_000 + i, baslik="BENIM")
    db.flush()

    sonuc = list_alarm_events(db, {7})
    assert len(sonuc) == 3, f"kapsam SQL'de daraltmadi: {len(sonuc)}"
    assert all(a.device_id == 7 for a in sonuc)
    assert all(a.title == "BENIM" for a in sonuc)


def test_bos_kume_HICBIR_alarm_dondurmuyor(db):
    """Bos kume "kisitsiz" ile karistirilmamali.

    `if not visible` gibi bir kontrol bos kumeyi kisitsiz sayardi ve hicbir
    sorumluluk alanina atanmamis bir operator TUM sahayi gorurdu.
    """
    _alarm(db, device_id=1, reset=False, dakika_once=1)
    db.flush()

    assert list_alarm_events(db, set()) == []


def test_None_kapsam_KISITSIZ(db):
    """engineer/installer icin daraltma olmamali."""
    _alarm(db, device_id=1, reset=False, dakika_once=1)
    _alarm(db, device_id=2, reset=False, dakika_once=2)
    db.flush()

    assert len(list_alarm_events(db, None)) == 2


def test_siralama_EN_YENI_ustte(db):
    """Arayuz tek bir "en yeni ustte" listesi bekliyor.

    Aktif ve cozulmus ayri sorgulardan geldigi icin birlestirme sonrasi
    yeniden siralama SART; olmazsa liste ikiye bolunmus gorunur.
    """
    _alarm(db, device_id=1, reset=True, dakika_once=10, baslik="orta-cozulmus")
    _alarm(db, device_id=1, reset=False, dakika_once=100, baslik="eski-acik")
    _alarm(db, device_id=1, reset=False, dakika_once=1, baslik="yeni-acik")
    db.flush()

    sonuc = list_alarm_events(db)
    assert [a.title for a in sonuc] == ["yeni-acik", "orta-cozulmus", "eski-acik"]


def test_router_kapsami_SERVISE_geciriyor():
    """Router kapsami servise gecirmeli — Python'da sonradan suzmemeli.

    Kaynak metni degil AST inceleniyor: metin aramasi bu dosyanin kendi
    aciklamalarina ya da docstring'e takilabiliyor.
    """
    import ast
    import inspect

    from app.api import alarms

    fn = next(
        d
        for d in ast.walk(ast.parse(inspect.getsource(alarms)))
        if isinstance(d, ast.FunctionDef) and d.name == "list_alarm_events"
    )
    cagrilar = {
        (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
    }
    assert "get_visible_device_ids" in cagrilar, "router kapsami hic hesaplamiyor"
    assert "_scope_filter_alarms" not in cagrilar, (
        "kapsam hala LIMIT'ten SONRA Python'da uygulaniyor"
    )
    # Servise kapsam ARGUMAN olarak gitmeli (kapsamsiz cagri kalmamali)
    servis_cagrilari = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "list_alarm_events_service"
    ]
    assert servis_cagrilari, "servis hic cagrilmiyor"
    assert all(len(c.args) >= 2 or c.keywords for c in servis_cagrilari), (
        "servis kapsamsiz cagriliyor"
    )
