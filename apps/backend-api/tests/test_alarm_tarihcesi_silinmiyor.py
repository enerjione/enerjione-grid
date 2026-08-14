"""Gecmis tarihli alarm sayilari — tarihce artik yok edilmiyor.

SORUN
-----
Ariza analizindeki "Alarm Sikligi — Takvim" ve "Cihaz x Zaman" kesitleri
gecmis gunler icin bos gorunuyordu. Grafikler dogruydu; VERI yoktu.
`alarm_events` uc ayri yerde satir SILIYORDU:

  1. Ayni sinyal tekrar tetikleyince, "Onay Bekliyor"da bekleyen onceki
     kayit siliniyordu (panelde tek satir kalsin diye). Tekrar eden bir
     alarm geriye TEK satir birakiyordu.
  2. ONAYLANMIS bir alarm normale donunce satir siliniyordu (internal
     clear ucu ve reconcile isciligi).
  3. Kullanici zaten reset olmus bir alarmi onayladiginda satir ve
     YORUMLARI siliniyordu.

Ucu de bir GORUNUM sorununu veri silerek cozuyordu. 18 ariza kaydina
karsilik 6 alarm satiri kalan bir sahada "gecen ay kac alarm geldi"
sorusunun cevabi yoktu.

COZUM
-----
Satir duruyor, `superseded_at` damgasi yiyor. Canli listeler bu damgayi
suzer; analiz katmani (alarm takvimi / cihaz x zaman matrisi) HIC bakmaz.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.alarm import AlarmComment, AlarmEvent
from app.models.device import Device
from app.services import alarm_engine_service
from app.services.fault_analytics_service import alarm_isi_haritasi, alarm_takvimi


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
def cihaz(db):
    d = Device(code="DEMO-1", name="DEMO-1", ip_address="10.0.0.1",
               latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    return d


def _alarm(db, dev: Device, *, gun_once: int = 0, ack: bool = False,
           reset: bool = False, arsiv: bool = False) -> AlarmEvent:
    an = datetime.now(timezone.utc) - timedelta(days=gun_once)
    a = AlarmEvent(
        device_id=dev.id, title="Asiri akim", description="esik asildi",
        level="critical", signal_key="oc", kind="rule", produces_fault=True,
        acknowledged=ack, reset=reset,
        superseded_at=an if arsiv else None,
        created_at=an,
    )
    db.add(a)
    db.flush()
    return a


# --- Tarihce: analiz katmani ARSIVI DE SAYAR -----------------------------


def test_takvim_ARSIVLENMIS_alarmlari_da_sayar(db, cihaz):
    """Kullanicinin sordugu sey: "gecen ay hangi gun kac alarm geldi"."""
    _alarm(db, cihaz, gun_once=10, ack=True, reset=True, arsiv=True)
    _alarm(db, cihaz, gun_once=10, ack=True, reset=True, arsiv=True)
    _alarm(db, cihaz, gun_once=3)  # canli

    takvim = alarm_takvimi(db, days=30, visible_device_ids=None)

    assert takvim["total"] == 3, "arsivlenmis alarmlar takvimde sayilmamis"
    sayim = {g["date"]: g["count"] for g in takvim["days"]}
    on_gun_once = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    assert sayim[on_gun_once] == 2, "gecmis gunun sayisi kaybolmus"
    assert takvim["max"] == 2


def test_matris_ARSIVLENMIS_alarmlari_da_sayar(db, cihaz):
    """Cihaz x zaman kesiti de ayni tarihceyi okur."""
    _alarm(db, cihaz, gun_once=5, ack=True, reset=True, arsiv=True)
    _alarm(db, cihaz, gun_once=5, ack=True, reset=True, arsiv=True)

    isi = alarm_isi_haritasi(db, days=30, visible_device_ids=None, surekli=True)

    assert isi["devices"], "arsivlenmis alarmi olan cihaz matriste yok"
    assert isi["devices"][0]["total"] == 2
    assert isi["max"] == 2


# --- Canli panel: ARSIV GORUNMEZ ----------------------------------------


def test_canli_liste_ARSIVI_gostermez(db, cihaz):
    """Kapanmis isi operatorun onune tekrar koymuyoruz."""
    _alarm(db, cihaz, gun_once=10, ack=True, reset=True, arsiv=True)
    canli = _alarm(db, cihaz, gun_once=0)

    satirlar = alarm_engine_service.list_alarm_events(db)

    assert [a.id for a in satirlar] == [canli.id]


# --- Silme yollari ARTIK SILMIYOR ---------------------------------------


def test_onaylanmis_reset_alarm_ONAYLANINCA_silinmiyor(db, cihaz):
    """Reset olmus bir alarmi onaylamak eskiden satiri VE yorumlari siliyordu."""
    a = _alarm(db, cihaz, reset=True)
    db.add(AlarmComment(alarm_event_id=a.id, author_username="muh",
                        comment="direk dibinde dal vardi",
                        created_at=datetime.now(timezone.utc)))
    db.flush()

    alarm_engine_service.acknowledge_alarm(db, a.id, "muh")

    kayit = db.get(AlarmEvent, a.id)
    assert kayit is not None, "alarm satiri silinmis"
    assert kayit.superseded_at is not None, "canli listeden dusurulmemis"
    yorum = db.scalar(
        select(func.count()).select_from(AlarmComment)
        .where(AlarmComment.alarm_event_id == a.id)
    )
    assert yorum == 1, "saha yorumu alarmla birlikte silinmis"


def test_ayni_alarm_TEKRAR_tetikleyince_onceki_kayit_kaliyor(db, cihaz):
    """En cok veri goturen yol buydu: her tekrar oncekini siliyordu.

    Uc kez tetikleyip cozulen bir alarm geriye TEK satir birakiyordu; oysa
    takvimin cevaplamasi gereken sey "kac kez geldi".
    """
    from app.api import internal
    from app.core.config import settings
    from app.schemas.internal import InternalAlarmIngest

    # Ilk tetikleme + normale donus (onaylanmamis -> alt panelde bekler).
    ilk = _alarm(db, cihaz, gun_once=2, reset=True)

    internal.ingest_alarm(
        InternalAlarmIngest(
            device_code=cihaz.code, title="Asiri akim", description="esik asildi",
            level="critical", signal_key="oc",
        ),
        db=db,
        x_service_token=settings.internal_service_token,
    )

    assert db.get(AlarmEvent, ilk.id) is not None, "onceki kayit silinmis"
    db.refresh(ilk)
    assert ilk.superseded_at is not None, "onceki kayit arsive alinmamis"
    # Toplam iki satir: biri arsiv, biri canli.
    assert db.scalar(select(func.count()).select_from(AlarmEvent)) == 2
    canli = alarm_engine_service.list_alarm_events(db)
    assert len(canli) == 1, "alt panel eski kayitla dolmus"


# --- Dorduncu silme yolu: CIHAZ SILINMESI -------------------------------
#
# Ustteki uc yol bir GORUNUM sorununu veri silerek cozuyordu. Bu dorduncusu
# daha sessizdi: cihaz silinince alarm satirlari da gidiyordu
# (`device_repository._delete_telemetry_and_alarms_for_device`). Sahada
# olculdu (2026-08-12): demo cihazlari 17 kez silinip yeniden olusturulmus,
# geriye 2 alarm satiri kalmisti. Ayni kurulumda `telemetry_history` silinen
# cihazin satirlarini KORUYORDU — operasyonel kayit, donanim kaydiyla
# birlikte yok olan tek seydi.


def _cihazi_sil(db, dev: Device) -> dict:
    from app.repositories.device_repository import DeviceRepository

    sayilar = DeviceRepository(db).delete(dev)
    db.flush()
    return sayilar


def test_cihaz_silinince_alarm_satiri_KALIYOR(db, cihaz):
    """Asil regresyon: gecmis, donanim kaydiyla birlikte yok olmuyor."""
    eski = _alarm(db, cihaz, gun_once=10, ack=True, reset=True, arsiv=True)
    acik = _alarm(db, cihaz, gun_once=1)

    _cihazi_sil(db, cihaz)

    assert db.get(AlarmEvent, eski.id) is not None, "arsiv satiri cihazla birlikte silinmis"
    assert db.get(AlarmEvent, acik.id) is not None, "acik alarm cihazla birlikte silinmis"
    assert db.scalar(select(func.count()).select_from(AlarmEvent)) == 2


def test_cihaz_silinince_kod_ve_ad_DONDURULUYOR(db, cihaz):
    """Cihaz artik yok; adi `devices`ten okunamaz. Okunamayan bir ad yerine
    gecmiste "#41" yazmak, kaydi kimin urettigini kaybetmek olurdu."""
    a = _alarm(db, cihaz, gun_once=4)
    kod, ad = cihaz.code, cihaz.name

    _cihazi_sil(db, cihaz)

    db.refresh(a)
    assert a.device_id is None, "FK bosaltilmamis"
    assert a.device_code == kod
    assert a.device_name == ad


def test_cihazi_silinen_alarm_CANLI_LISTEDE_gorunmuyor(db, cihaz):
    """Cihaz yoksa alarm uzerinde islem de yapilamaz: onaylanamaz, normale
    donemez, haritada gosterilemez. Damgalanmasaydi ACIK bir alarm panelde
    sonsuza kadar asili kalirdi."""
    _alarm(db, cihaz, gun_once=1)
    baska = Device(code="DEMO-2", name="DEMO-2", ip_address="10.0.0.2",
                   latitude=39.1, longitude=35.1)
    db.add(baska)
    db.flush()
    kalan = _alarm(db, baska, gun_once=1)

    _cihazi_sil(db, cihaz)

    canli = alarm_engine_service.list_alarm_events(db)
    assert [a.id for a in canli] == [kalan.id], "cihazi silinen alarm canli listede"


def test_cihaz_silinince_SAHA_YORUMU_kaliyor(db, cihaz):
    """Operator yorumu ("ekip yonlendirildi") alarmin kendisi kadar gecmistir."""
    a = _alarm(db, cihaz, gun_once=2)
    db.add(AlarmComment(alarm_event_id=a.id, author_username="muh",
                        comment="ekip yonlendirildi",
                        created_at=datetime.now(timezone.utc)))
    db.flush()

    _cihazi_sil(db, cihaz)

    yorum = db.scalar(
        select(func.count()).select_from(AlarmComment)
        .where(AlarmComment.alarm_event_id == a.id)
    )
    assert yorum == 1, "saha yorumu cihazla birlikte silinmis"


def test_cihaz_silinince_TAKVIM_gecmisi_duruyor(db, cihaz):
    """Silmenin gorunur sonucu buydu: analiz takvimi bos cikiyordu."""
    _alarm(db, cihaz, gun_once=6, ack=True, reset=True, arsiv=True)
    _alarm(db, cihaz, gun_once=6, ack=True, reset=True, arsiv=True)

    _cihazi_sil(db, cihaz)

    takvim = alarm_takvimi(db, days=30, visible_device_ids=None)
    assert takvim["total"] == 2, "cihaz silininca takvim gecmisi bosaldi"


# --- Retention: tavan var ama CANLI alarma dokunmuyor --------------------


def test_retention_kosulu_CANLI_alarmi_disarida_birakir():
    """Tabloya tavan koyduk; ama iki yildir ACIK duran bir alarm silinemez.

    Yasi ne olursa olsun acik bir alarm, urunun bildirmesi gereken en onemli
    seydir. Kosul bu yuzden `superseded_at IS NOT NULL` — yani yalnizca
    arsiv satirlari dusebilir.
    """
    import inspect
    import re

    from app.services import telemetry_retention

    kod = inspect.getsource(telemetry_retention.RetentionWorker._purge_alarm_events)
    kod = re.sub(r'""".*?"""', "", kod, flags=re.DOTALL)
    # Her AlarmEvent silme kosulunda arsiv suzgeci bulunmali.
    assert kod.count("superseded_at.is_not(None)") >= 3, (
        "retention kosulunda arsiv suzgeci eksik — canli alarm silinebilir"
    )
