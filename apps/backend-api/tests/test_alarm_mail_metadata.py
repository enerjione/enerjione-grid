"""Alarm maili metadata'yi DOGRU alarmdan almali.

YASANAN
-------
`_build_alarm_email` zenginlestirilmis alanlari (Olcum, Esik, Kaynak/faz, Hat,
Bolge) bagli `Notification` kaydindan okuyor. Sorgu "kategori=alarm olan EN SON
yayin bildirimi" diyordu; cektigi satirin `alarm_id`si tutmazsa metadata
tamamen bosaltiliyordu.

Uretimde bu ISTISNA DEGIL KURAL:
`notification_inline_dispatch_enabled` varsayilani False, yani mail alarm
ingest'inden AYRI bir HTTP isteginde uretiliyor (notification-worker ->
`/internal/notifications/dispatch/{alarm_id}`). Arada ingest edilen tek bir
alarm bile eslesmeyi bozuyordu — TEK bir arizanin uc faz alarminda uc mailin
ikisi sakat gidiyordu.

Kaybolan sey mailin operatoru sahaya yonlendiren TUM icerigiydi; geriye cihaz
adi ve zaman kaliyordu. Hicbir hata, hicbir log yoktu.

Bu test araya BASKA bir alarmin bildirimini sokar ve mailin hala kendi
verisini tasidigini dogrular.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (tum tablolari kaydeder)
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.models.notification import Notification
from app.services.notification_dispatch_service import _build_alarm_email


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    oturum = sessionmaker(bind=engine)()
    try:
        yield oturum
    finally:
        oturum.close()


def _kurulum(db):
    cihaz = Device(
        code="DEV-1",
        name="Fider 3 Direk 12",
        model="horstmann_sn_2_0",
        ip_address="10.0.0.1",
        latitude=39.9,
        longitude=32.8,
    )
    db.add(cihaz)
    db.flush()

    t0 = datetime.now(timezone.utc)
    ilk = AlarmEvent(
        device_id=cihaz.id,
        title="Asiri Akim",
        description="esik asildi",
        level="critical",
        created_at=t0,
    )
    sonraki = AlarmEvent(
        device_id=cihaz.id,
        title="Gerilim Kaybi",
        description="baska bir alarm",
        level="warning",
        created_at=t0 + timedelta(seconds=1),
    )
    db.add_all([ilk, sonraki])
    db.flush()
    return ilk, sonraki


def _bildirim(db, alarm: AlarmEvent, **meta) -> None:
    """Alarm ingest'inin yazdigi YAYIN bildirimi (recipient_username = NULL)."""
    db.add(
        Notification(
            category="alarm",
            recipient_username=None,
            title=alarm.title,
            body="-",
            created_at=alarm.created_at,
            metadata_json=json.dumps({"alarm_id": alarm.id, **meta}),
        )
    )
    db.flush()


ZENGIN = dict(
    device_name="Fider 3 Direk 12",
    device_code="DEV-1",
    signal_source="sat07",
    line_name="Fider 3",
    region_name="Merkez",
    value=612.4,
    threshold=500,
    operator="gt",
)

#: Mailde GORUNMESI gereken, yalnizca metadata'dan gelebilen degerler.
BEKLENEN = ("612.4", "500", "Fider 3", "Merkez", "Satellite 07")


def test_araya_baska_alarm_girse_de_mail_KENDI_verisini_tasir(db):
    ilk, sonraki = _kurulum(db)
    _bildirim(db, ilk, **ZENGIN)
    # ARAYA GIREN: daha yeni ve zenginlestirilmemis bir yayin bildirimi.
    # Eski sorgu bunu cekip ilk alarmin metadata'sini tamamen dusuruyordu.
    _bildirim(db, sonraki, device_name="Fider 3 Direk 12", device_code="DEV-1")

    _konu, _duz, html = _build_alarm_email(db, ilk, "Test Kurulumu")

    eksik = [deger for deger in BEKLENEN if deger not in html]
    assert not eksik, f"mail kendi metadata'sini kaybetti: {eksik}"


def test_tek_bildirim_varken_de_calisir(db):
    """Gerileme koruması: capalama, normal (tek alarm) yolu bozmamali."""
    ilk, _ = _kurulum(db)
    _bildirim(db, ilk, **ZENGIN)

    _konu, _duz, html = _build_alarm_email(db, ilk, "Test Kurulumu")
    assert all(deger in html for deger in BEKLENEN)


def test_bildirim_yoksa_cihaz_bilgisi_yine_de_gelir(db):
    """Metadata hic yoksa mail URETILMELI; cihaz adi Device satirindan doler.

    Bildirim kaydi henuz yazilmamis olabilir (yarisma) ya da temizlenmis
    olabilir; bu durumda mailin BOS gitmesi degil, elde olanla gitmesi dogru.
    """
    ilk, _ = _kurulum(db)

    konu, _duz, html = _build_alarm_email(db, ilk, "Test Kurulumu")
    assert "Fider 3 Direk 12" in html
    assert konu
