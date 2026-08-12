"""Alarm otomatik temizlenmesi — olay kaydi opsiyonel, ama HER ZAMAN degil.

SORUN
-----
Dalgalanan bir sinyal dakikalar icinde binlerce tetiklen/temizlen cifti
uretiyor. Sahada olculdu: `system_events` icinde 1420 `alarm_triggered` +
1415 `alarm_auto_cleared`. GERCEK operator olaylari (yetki kullanimi, komut
gonderimi, ayar degisikligi) bu yiginin icinde kayboluyor. Denetim kaydinin
degeri okunabilirliginde.

KRITIK AYRIM — IKI TEMIZLENME YOLU AYNI DEGIL
----------------------------------------------
* ONAYLANMAMIS alarm temizlenir  -> satir CANLI KALIR (`reset=True`).
  Alt panelde "onay bekliyor" olarak durur. Olay kaydi TEKRAR eder;
  kapatilabilir.
* ONAYLANMIS alarm temizlenir    -> satir ARSIVE duser (`superseded_at`).
  Yasam dongusu bitmistir, canli listede yeri yoktur. Bu SEYREK bir olaydir
  (birinin onaylamis olmasi gerekir) ve gurultu uretmez; olay kaydi
  kapatilamaz.

Ikisini ayni bayrakla kapatmak, onaylanmis bir alarmin sahada olup bittigine
dair operator izini gurultuye kurban ederdi.

NOT: onaylanmis dal ESKIDEN `db.delete` yapiyordu ve olay kaydi geriye kalan
TEK iz oluyordu. Artik satir duruyor (tarihce icin), olay kaydi ise operator
izi olarak yine kosulsuz yaziliyor.
"""

from __future__ import annotations

import inspect
import re

from app.api import internal
from app.core.config import settings


def _kod(kaynak: str) -> str:
    kaynak = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


def _clear_fn():
    for ad in dir(internal):
        fn = getattr(internal, ad)
        if callable(fn) and "alarm" in ad and "clear" in ad:
            return fn
    raise AssertionError("alarm clear fonksiyonu bulunamadi")


def test_varsayilan_KAPALI():
    """Gurultunun kaynagi buydu; varsayilan kapali olmali."""
    assert settings.alarm_auto_clear_events is False


def test_ONAYLANMAMIS_temizlenme_kaydi_BAYRAGA_bagli():
    """Gurultunun kaynagi bu dal: dalgalanan sinyal binlerce cift uretiyor."""
    kod = _kod(inspect.getsource(_clear_fn()))
    # ONAYLANMAMIS dal, onaylanmis dalin ARDINDAN gelir; son `reset = True`
    # atamasindan itibaren bakiyoruz (ilk atama onaylanmis daldadir).
    i_reset = kod.rfind("existing.reset = True")
    assert i_reset != -1, "reset atamasi bulunamadi"
    kalan = kod[i_reset:]
    i_bayrak = kalan.find("settings.alarm_auto_clear_events")
    i_kayit = kalan.find("record_event(")
    assert i_bayrak != -1, "onaylanmamis dalda bayrak kontrolu yok"
    assert i_kayit != -1
    assert i_bayrak < i_kayit, "kayit bayraktan ONCE yaziliyor"


def test_ONAYLANMIS_temizlenme_kaydi_HER_ZAMAN_yaziliyor():
    """Seyrek ve operator izli bir olay; gurultu uretmez, kapatilamaz."""
    kod = _kod(inspect.getsource(_clear_fn()))
    i_arsiv = kod.find("existing.superseded_at")
    assert i_arsiv != -1, "onaylanmis dalda arsivleme damgasi bulunamadi"
    i_kayit = kod.find("record_event(", i_arsiv)
    assert i_kayit != -1, "onaylanmis dalda olay kaydi yok"
    arasi = kod[i_arsiv:i_kayit]
    assert "alarm_auto_clear_events" not in arasi, (
        "onaylanmis alarmin temizlenme kaydi da kapatilabilir yapilmis — "
        "bu dal seyrek ve operator izli, gurultuye girmez"
    )


def test_ONAYLANMIS_alarm_SILINMIYOR():
    """Tarihce: satir arsive duser, yok edilmez.

    Silinseydi "gecen ay hangi gun kac alarm geldi" sorusunun cevabi
    kalmazdi — ariza analizindeki alarm takvimi ve cihaz x zaman matrisi
    gecmis gunler icin bos gorunurdu.
    """
    kod = _kod(inspect.getsource(_clear_fn()))
    assert "db.delete(existing)" not in kod, "alarm satiri hala siliniyor"


def test_reset_bilgisi_ALARM_SATIRINDA_duruyor():
    """Olay kaydini kapatmanin bilgi kaybettirmemesinin sebebi bu."""
    from app.models.alarm import AlarmEvent

    kolonlar = {c.name for c in AlarmEvent.__table__.columns}
    assert "reset" in kolonlar
    assert "reset_at" in kolonlar


def test_alarm_TETIKLENME_kaydi_KAPATILMADI():
    """Alarmin olusmasi gercek bir operasyonel olay; her zaman kaydedilmeli."""
    from app.services import alarm_engine_service

    kod = _kod(inspect.getsource(alarm_engine_service))
    assert "record_event(" in kod
    assert "alarm_auto_clear_events" not in kod, (
        "tetiklenme kaydi da bayraga baglanmis"
    )
