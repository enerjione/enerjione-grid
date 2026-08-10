"""Hat arizasi bildirimi HER KOSULDA cikmali.

YASANAN ARIZA (saha)
--------------------
"Hat arizasi geldi, WhatsApp grubu secili, e-posta acik — hicbiri gelmedi."

Tek bir hata degil, ZINCIR KOPUKLUGU:

  1. `fault_recompute_service` arizayi acar ama gonderim YAPMAZ; satir ici
     gonderim `notification_inline_dispatch_enabled` bayragina bagli ve
     bayrak production'da VARSAYILAN KAPALI (SMTP'yi ariza motorunun icinde
     kostururken ariza kaydi commit edilmeden asili kaliyordu).
  2. Gonderimi tetikleyen TEK yer notification-worker'in ALARM yolu:
     `/internal/notifications/dispatch/{alarm_id}`.
  3. Ariza kaydini acan `recompute_faults_debounced` COALESCING yapar ve
     hesaplamayi sonraki tetige birakabilir.

Sonuc: alarmin dispatch'i ariza satiri HENUZ YOKKEN kosuyor, `pending` bos
donuyor ve mesaj "islenmis" damgalaniyor. Debounce arizayi sonra aciyor ama
onu gonderecek kimse kalmiyor. Yeni alarm dusmezse ariza sonsuza dek
`notified_at IS NULL` kaliyor — yani TEKIL arizada (en kritik durumda)
bildirim hic gitmiyor.

Bu testler uc korumayi kilitler:
  * bekleyen ariza alarm akisindan BAGIMSIZ suruluyor (sweeper),
  * `notified_at` damgasi tek gonderimi garantiliyor,
  * WhatsApp grup yayini alici kumesinden ve SMTP'den bagimsiz.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, Pole, Region
from app.models.notification_settings import NotificationSettings
from app.services import notification_dispatch_service as nds


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


def _kurulum(db, *, whatsapp_group: bool = True, smtp: bool = False) -> FaultEvent:
    db.add(
        NotificationSettings(
            id=1,
            smtp_enabled=smtp,
            smtp_host="smtp.firma.com" if smtp else "",
            smtp_port=587,
            sms_enabled=False,
            whatsapp_web_enabled=whatsapp_group,
            whatsapp_web_group_mode=whatsapp_group,
            whatsapp_web_group_jids="120363000000000000@g.us" if whatsapp_group else "",
        )
    )
    region = Region(name="Merkez", code="MRK")
    db.add(region)
    db.flush()
    line = Line(name="HAT-1", code="HAT-1", region_id=region.id)
    db.add(line)
    db.flush()
    dev = Device(
        code="SN2-0001", name="Fider 1", ip_address="10.0.0.5",
        latitude=39.0, longitude=35.0,
    )
    db.add(dev)
    db.flush()
    p3 = Pole(line_id=line.id, sequence_no=3, latitude=39.01, longitude=35.01)
    p4 = Pole(line_id=line.id, sequence_no=4, latitude=39.02, longitude=35.02)
    db.add_all([p3, p4])
    db.flush()
    fault = FaultEvent(
        line_id=line.id,
        region_id=region.id,
        last_red_device_id=dev.id,
        from_pole_id=p3.id,
        to_pole_id=p4.id,
        from_pole_seq=3,
        to_pole_seq=4,
        status="open",
        opened_at=datetime.now(timezone.utc),
        notified_at=None,
    )
    db.add(fault)
    db.flush()
    return fault


def test_bekleyen_ariza_ALARM_OLMADAN_gonderilir(db, monkeypatch):
    """Asil ariza: gonderim yalnizca alarm dispatch'ine bagliydi."""
    gonderilen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        nds.whatsapp_web_client_service,
        "send_test_message",
        lambda hedef, mesaj: gonderilen.append((hedef, mesaj)),
    )
    _kurulum(db)

    sent = nds.dispatch_pending_fault_notifications(db)

    assert sent == 1, "bekleyen ariza gonderilmedi"
    assert gonderilen, (
        "WhatsApp grubuna hicbir sey dusmedi — sahada yasanan tam olarak buydu"
    )
    hedef, mesaj = gonderilen[0]
    assert hedef == "120363000000000000@g.us"
    assert "HAT-1" in mesaj and "HAT ARIZASI" in mesaj


def test_ayni_ariza_IKI_KEZ_gonderilmez(db, monkeypatch):
    """`notified_at` damgasi hizli yol ile sweeper'i cakismaktan korur."""
    gonderilen: list[str] = []
    monkeypatch.setattr(
        nds.whatsapp_web_client_service,
        "send_test_message",
        lambda hedef, mesaj: gonderilen.append(hedef),
    )
    _kurulum(db)

    assert nds.dispatch_pending_fault_notifications(db) == 1
    assert nds.dispatch_pending_fault_notifications(db) == 0, "ariza tekrar gonderildi"
    assert len(gonderilen) == 1


def test_kapanmis_ariza_gonderilmez(db, monkeypatch):
    gonderilen: list[str] = []
    monkeypatch.setattr(
        nds.whatsapp_web_client_service,
        "send_test_message",
        lambda hedef, mesaj: gonderilen.append(hedef),
    )
    fault = _kurulum(db)
    fault.status = "resolved"
    db.flush()

    assert nds.dispatch_pending_fault_notifications(db) == 0
    assert not gonderilen


def test_SMTP_kapali_olsa_bile_WhatsApp_grubuna_duser(db, monkeypatch):
    """Kanal kapilari BAGIMSIZ olmali; biri kapaliyken digeri susmamali."""
    gonderilen: list[str] = []
    monkeypatch.setattr(
        nds.whatsapp_web_client_service,
        "send_test_message",
        lambda hedef, mesaj: gonderilen.append(hedef),
    )
    _kurulum(db, whatsapp_group=True, smtp=False)

    nds.dispatch_pending_fault_notifications(db)

    assert gonderilen, "SMTP kapali diye WhatsApp da susturuldu"


def test_whatsapp_hatasi_damgayi_engellemez(db, monkeypatch):
    """Kalici bir kanal hatasi her turda yeniden denenip dispatch'i
    yavaslatmamali — hata loglanir, damga basilir."""
    def _patla(hedef, mesaj):  # noqa: ANN001
        raise RuntimeError("whatsapp gateway down")

    monkeypatch.setattr(nds.whatsapp_web_client_service, "send_test_message", _patla)
    fault = _kurulum(db)

    nds.dispatch_pending_fault_notifications(db)

    assert fault.notified_at is not None, "hata sonrasi damga basilmadi"
    assert nds.dispatch_pending_fault_notifications(db) == 0


def test_sweeper_arka_plan_ISLERINE_KAYITLI():
    """Sweeper leader islerine KAYITLI olmali.

    Modulun var olmasi yetmez — `leader.register` cagrilmazsa dosya olu
    koddur ve bekleyen ariza hicbir zaman taranmaz. Zincirdeki kopukluk tam
    olarak bu tur bir "yazildi ama baglanmadi" durumuydu.
    """
    import app.main  # noqa: F401  (kayitlar import an'inda yapiliyor)
    from app.core.service_role import leader

    isimler = [ad for ad, _start, _stop in leader._jobs]  # noqa: SLF001
    assert "fault_notify_sweeper" in isimler, (
        f"sweeper arka plan islerine kaydedilmemis; kayitli olanlar: {isimler}"
    )


def test_sweeper_araligi_SAHA_icin_makul():
    """Ariza bildirimi sahaya ekip cikarir; dakikalarca beklenemez."""
    from app.services import fault_notify_sweeper

    assert 15 <= fault_notify_sweeper._tick_sec() <= 300  # noqa: SLF001


def test_tekrarlanan_alarm_mesaji_ariza_gonderimini_ENGELLEMEZ():
    """Idempotency erken donusu ariza yolunu da susturuyordu.

    Worker'in retry'i cogu kez tam da ariza kaydi olustuktan SONRA gelir;
    `duplicate_ignored` ile erken donmek o arizayi ebediyen bekletiyordu.
    Bu test kod yolunu (kaynak) dogrular — endpoint'i ayaga kaldirmak
    httpx bagimliligi ister (bkz. test_route_auth_boundary.py gerekcesi).
    """
    import ast
    import inspect

    from app.api import internal

    src = inspect.getsource(internal.dispatch_notification_for_alarm)
    tree = ast.parse(inspect.cleandoc(src))

    # `duplicate_ignored` donduren dalda ariza gonderimi cagrilmis olmali.
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        govde = ast.dump(node)
        if "duplicate_ignored" not in govde:
            continue
        assert "_bekleyen_arizalari_gonder" in govde, (
            "tekrarlanan alarm mesajinda ariza bildirimi denenmiyor — "
            "debounce ile sonradan acilan ariza sonsuza dek bekler"
        )
        break
    else:  # pragma: no cover
        raise AssertionError("duplicate_ignored dali bulunamadi")
