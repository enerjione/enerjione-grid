"""Alarm kuralinda secilen bildirim kanali SAHADA GERCEKTEN CIKMALI.

YASANAN ARIZA
-------------
Kurulumcu alarm kuralinda "SMS + WhatsApp" isaretliyor, alarm gercekten
olusuyor, operatore hicbir sey gitmiyordu. Uc ayri sessiz kapi vardi:

  1) Dispatcher "kuralda secili VE kullanicinin profil tercihinde acik"
     seklinde AND'liyordu. Profil tercihinde sms/telegram/whatsapp
     varsayilani KAPALI'ydi ve o satir kullanici bildirim ekranini sadece
     ACTIGINDA bile (GET yan etkisi) kapali degerlerle yaziliyordu. Yani
     kuraldaki secim sessizce eziliyordu.
  2) Alarmi tetikleyen kural bulunamayinca (haberlesme/kalite alarmi —
     "Haberlesme arizasi" adinda bir AlarmRule satiri YOK) tum dis kanallar
     kapaniyor, sadece web bildirimi kaliyordu. Yani cihaz haberlesmeden
     dustugunde kimse haber alamiyordu.
  3) WhatsApp grup yayini kuraldaki `notify_whatsapp_web` bayragina
     bagliydi. Operator "bu gruba her ariza dussun" diye grup sectiginde
     bile, tek tek her kuralda WhatsApp'i isaretlemeyi unutmak sessiz kayip
     demekti.

Bu dosya uc kapinin da acik kaldigini davranis uzerinden kilitler.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base


@pytest.fixture()
def db():
    # Tum model modullerini yukle — `create_all` yalnizca o ana kadar import
    # edilmis tablolari kurar, yani eksik import test sirasina gore kirilir.
    import importlib
    import pkgutil

    import app.models

    for m in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{m.name}")

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=True)()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture()
def giden(monkeypatch):
    """Tum dis kanallari yakala — hicbir gercek SMTP/SMS/WhatsApp cagrisi yok."""
    from app.services import notification_dispatch_service as nds
    from app.services import whatsapp_web_client_service as wa

    kayit: dict[str, list] = {"sms": [], "email": [], "whatsapp": [], "telegram": []}
    monkeypatch.setattr(
        nds, "send_sms_test",
        lambda s, *, recipient_phone, message: kayit["sms"].append((recipient_phone, message)),
    )
    monkeypatch.setattr(
        nds, "send_smtp_test",
        lambda s, *, recipient_email, **kw: kayit["email"].append(recipient_email),
    )
    monkeypatch.setattr(
        nds, "send_telegram_test",
        lambda s, *, chat_id, message: kayit["telegram"].append((chat_id, message)),
    )
    monkeypatch.setattr(
        wa, "send_test_message",
        lambda hedef, mesaj: kayit["whatsapp"].append((hedef, mesaj)),
    )
    return kayit


def _kur(db, *, grup_modu=False, grup_jids="", telefon="+905551112233"):
    """Bir muhendis + bir cihaz + tum kanallari acik sistem ayari."""
    from app.models.device import Device
    from app.models.enums import UserRole
    from app.models.notification_settings import NotificationSettings
    from app.models.user import User

    db.add(
        User(
            id=1, username="muh", email="muh@ornek.com", phone_number=telefon,
            full_name="Muhendis", hashed_password="x", role=UserRole.ENGINEER,
        )
    )
    db.add(
        Device(
            id=1, code="DEV-1", name="Direk-12", ip_address="10.0.0.5",
            latitude=41.0, longitude=29.0,
        )
    )
    db.add(
        NotificationSettings(
            id=1, smtp_enabled=True, smtp_from_email="a@b.c",
            sms_enabled=True, telegram_enabled=True, telegram_chat_ids="-100123",
            whatsapp_web_enabled=True,
            whatsapp_web_group_mode=grup_modu, whatsapp_web_group_jids=grup_jids,
        )
    )
    db.commit()


def _alarm(db, *, baslik="Asiri akim", signal_key="master.current_phase_a"):
    from app.models.alarm import AlarmEvent

    a = AlarmEvent(
        device_id=1, level="critical", title=baslik, description="esik asildi",
        signal_key=signal_key, created_at=datetime.now(timezone.utc),
    )
    db.add(a)
    db.commit()
    return a


def _kural(db, *, baslik="Asiri akim", signal_key="master.current_phase_a", **kanallar):
    from app.models.alarm_rule import AlarmRule

    db.add(
        AlarmRule(
            id=1, signal_key=signal_key, name=baslik, level="critical",
            comparator="gt", threshold=100.0, is_active=True,
            notify_email=kanallar.get("email", False),
            notify_sms=kanallar.get("sms", False),
            notify_telegram=kanallar.get("telegram", False),
            notify_whatsapp_web=kanallar.get("whatsapp", False),
        )
    )
    db.commit()


# --------------------------------------------------------------- 1. kapi


def test_kuralda_secilen_SMS_profil_tercihi_YOKKEN_gider(db, giden):
    """Asil arizanin ta kendisi: kullanici hic tercih kaydetmemis olabilir."""
    from app.services.notification_dispatch_service import dispatch_alarm_notifications

    _kur(db)
    _kural(db, sms=True)
    dispatch_alarm_notifications(db, _alarm(db))

    assert giden["sms"], (
        "kuralda SMS secili ama gonderilmedi — kullanici profil tercihi "
        "sessiz ikinci kapi olarak calisiyor"
    )


def test_kullanicinin_BILINCLI_kapatmasi_hala_gecerli(db, giden):
    """Opt-out kaldirilmadi: elle kapatan kullanici SMS almaz."""
    from app.models.user_notification_preference import UserNotificationPreference
    from app.services.notification_dispatch_service import dispatch_alarm_notifications

    _kur(db)
    _kural(db, sms=True)
    db.add(UserNotificationPreference(user_id=1, sms_enabled=False))
    db.commit()

    dispatch_alarm_notifications(db, _alarm(db))
    assert not giden["sms"]


def test_kuralda_secilmeyen_kanal_gitmez(db, giden):
    """Fail-open sadece KURAL YOKKEN gecerli; kural varsa secimi baglayicidir."""
    from app.services.notification_dispatch_service import dispatch_alarm_notifications

    _kur(db)
    _kural(db, email=True)  # SMS/WhatsApp/Telegram secili degil
    dispatch_alarm_notifications(db, _alarm(db))

    assert giden["email"]
    assert not giden["sms"]
    assert not giden["whatsapp"]
    assert not giden["telegram"]


# --------------------------------------------------------------- 2. kapi


def test_kurali_olmayan_HABERLESME_alarmi_dis_kanallardan_cikar(db, giden):
    """Cihaz haberlesmeden dustugunde operator haber ALMALI.

    Bu alarmi alarm-service kural olmadan uretir ("Haberlesme arizasi");
    eskiden `_resolve_active_rule` None donuyor ve tum dis kanallar
    kapaniyordu.
    """
    from app.services.notification_dispatch_service import dispatch_alarm_notifications

    _kur(db)  # hic AlarmRule yok
    dispatch_alarm_notifications(db, _alarm(db, baslik="Haberleşme arızası", signal_key=None))

    assert giden["sms"], "kural bulunamayinca SMS sessizce dusuruldu"
    assert giden["email"], "kural bulunamayinca e-posta sessizce dusuruldu"


# --------------------------------------------------------------- 3. kapi


def test_grup_modunda_alarm_kural_secmese_bile_gruba_duser(db, giden):
    from app.services.notification_dispatch_service import dispatch_alarm_notifications

    _kur(db, grup_modu=True, grup_jids="120363@g.us")
    _kural(db, whatsapp=False)  # kuralda WhatsApp SECILI DEGIL
    dispatch_alarm_notifications(db, _alarm(db))

    hedefler = [h for h, _ in giden["whatsapp"]]
    assert "120363@g.us" in hedefler, (
        "grup secili oldugu halde alarm gruba dusmedi — operator her kuralda "
        "WhatsApp'i isaretlemeyi unutursa sessiz kayip olur"
    )


def test_grup_modunda_KISISEL_numaraya_gitmez(db, giden):
    """Tek mod kurali: grup seciliyse kisiye tek tek mesaj atilmaz."""
    from app.services.notification_dispatch_service import dispatch_alarm_notifications

    _kur(db, grup_modu=True, grup_jids="120363@g.us", telefon="+905551112233")
    _kural(db, whatsapp=True)
    dispatch_alarm_notifications(db, _alarm(db))

    hedefler = [h for h, _ in giden["whatsapp"]]
    assert "+905551112233" not in hedefler
    assert hedefler == ["120363@g.us"]


# ----------------------------------------------------------- ariza yolu


def _ariza(db):
    """Acik bir hat arizasi + topolojisi. Bildirim BEKLIYOR (notified_at NULL)."""
    from app.models.fault import FaultEvent
    from app.models.grid_topology import Line, Pole, Region

    db.add(Region(id=1, name="Bolge-1", code="B1"))
    db.add(Line(id=1, region_id=1, name="Hat-1", code="H1"))
    db.add(Pole(id=1, line_id=1, sequence_no=3, latitude=41.0, longitude=29.0))
    db.add(Pole(id=2, line_id=1, sequence_no=4, latitude=41.1, longitude=29.1))
    db.commit()
    f = FaultEvent(
        id=1, line_id=1, region_id=1, last_red_device_id=1,
        from_pole_id=1, to_pole_id=2, from_pole_seq=3, to_pole_seq=4,
        status="open", opened_at=datetime.now(timezone.utc),
    )
    db.add(f)
    db.commit()
    return f


def test_bekleyen_ariza_bildirimi_gonderilir_ve_damgalanir(db, giden):
    """Ariza bildirimi production varsayilaninda HIC gonderilmiyordu."""
    from app.services.notification_dispatch_service import (
        dispatch_pending_fault_notifications,
    )

    _kur(db)
    ariza = _ariza(db)

    assert dispatch_pending_fault_notifications(db) == 1
    assert giden["email"], "ariza e-postasi gonderilmedi"
    assert giden["sms"], "ariza SMS'i gonderilmedi"
    assert ariza.notified_at is not None


def test_ariza_bildirimi_IKINCI_kez_gitmez(db, giden):
    """Worker retry'inda ayni ariza icin ikinci mail/SMS cikmamali."""
    from app.services.notification_dispatch_service import (
        dispatch_pending_fault_notifications,
    )

    _kur(db)
    _ariza(db)
    dispatch_pending_fault_notifications(db)
    giden["email"].clear()
    giden["sms"].clear()

    assert dispatch_pending_fault_notifications(db) == 0
    assert not giden["email"]
    assert not giden["sms"]


def test_ariza_grup_modunda_WHATSAPP_grubuna_duser(db, giden):
    from app.services.notification_dispatch_service import (
        dispatch_pending_fault_notifications,
    )

    _kur(db, grup_modu=True, grup_jids="120363@g.us")
    _ariza(db)
    dispatch_pending_fault_notifications(db)

    hedefler = [h for h, _ in giden["whatsapp"]]
    assert hedefler == ["120363@g.us"]


def test_ariza_kapsami_MUHENDISE_hep_operatore_EKIBI_kadar(db, giden):
    """Kapsam kurali: engineer/installer her arizayi alir, operator yalnizca
    sorumluluk alanindakini. Baska bir ekibin operatoru bu arizayi ALMAMALI."""
    from app.models.enums import UserRole
    from app.models.user import User
    from app.services.notification_dispatch_service import (
        dispatch_pending_fault_notifications,
    )

    _kur(db)  # id=1 engineer
    db.add(
        User(
            id=2, username="opr", email="opr@ornek.com", phone_number="+905559998877",
            full_name="Operator", hashed_password="x", role=UserRole.OPERATOR,
        )
    )
    db.commit()
    _ariza(db)  # operatore hicbir sorumluluk alani atanmadi
    dispatch_pending_fault_notifications(db)

    assert "muh@ornek.com" in giden["email"], "muhendis her arizadan haberdar olmali"
    assert "opr@ornek.com" not in giden["email"], (
        "kapsam disindaki operatore ariza bildirimi gitti"
    )


def test_SMTP_kapaliyken_bile_ariza_gruba_duser(db, giden):
    """Kanal kapilari bagimsiz: e-posta kapali olmasi WhatsApp'i susturmamali."""
    from app.models.notification_settings import NotificationSettings
    from app.services.notification_dispatch_service import (
        dispatch_pending_fault_notifications,
    )

    _kur(db, grup_modu=True, grup_jids="120363@g.us")
    db.get(NotificationSettings, 1).smtp_enabled = False
    db.commit()
    _ariza(db)
    dispatch_pending_fault_notifications(db)

    assert not giden["email"]
    assert [h for h, _ in giden["whatsapp"]] == ["120363@g.us"]
