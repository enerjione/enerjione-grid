"""Telemetri basina DENETIM KAYDI yazilmamali.

SAHADA OLCULDU (2026-08-01, 15 cihaz, IEC 104 hedefi aktif)
------------------------------------------------------------
`_dispatch_iec104` her telemetri okumasi icin `record_event(...)` cagiriyordu.
Iki ayri sorun vardi:

1. KAYIT HIC OLUSMUYORDU. Cagiran taraf (`_dispatch_outbound`) session'i
   COMMIT ETMEDEN kapatiyor. Yani saniyede yuzlerce ORM nesnesi kuruluyor,
   session'da birikiyor ve cope atiliyordu. Olcum: hedef aktifken
   `system_events` icinde TEK BIR `iec104_point_updated` satiri yoktu.

2. COMMIT EDILSEYDI DAHA KOTU OLURDU. `system_events` denetim kaydi ve
   `system_events_retention_days = 730` (2 YIL). 15 cihazlik test
   kurulumunda bile 375 okuma/sn = gunde 32 milyon denetim satiri; tablo
   denetim amacini tamamen kaybederdi.

Denetim kaydi OPERATOR EYLEMLERI icindir — kalici state degisimi, yetki
kullanimi, komut gonderimi. Nokta guncellemesi bunlardan biri degil.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.services import outbound_dispatch_service as svc


def _kod(kaynak: str) -> str:
    """Yorumlari eler — kontrol KOD'a bakmali.

    Bu depoda metin aramasi defalarca kendi aciklamalarina takildi; burada
    ozellikle riskli, cunku kaldirilan cagrinin ADI aciklamada geciyor."""
    kaynak = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


def test_iec104_nokta_guncellemesi_DENETIM_KAYDI_yazmiyor():
    kod = _kod(inspect.getsource(svc._dispatch_iec104))
    assert "record_event(" not in kod, (
        "nokta basina denetim kaydi geri eklenmis — 15 cihazda bile gunde "
        "32 milyon satir demek ve `system_events` 2 yil saklaniyor"
    )


def test_iec104_nokta_guncellemesi_HALA_yapiliyor():
    """Kaydi kaldirdik, ISLEVI degil."""
    kod = _kod(inspect.getsource(svc._dispatch_iec104))
    assert "update_point_threadsafe(" in kod, (
        "IEC 104 nokta guncellemesi kaldirilmis — SCADA'ya veri gitmez"
    )


def test_ALARM_dispatchi_denetim_kaydi_yazmaya_devam_ediyor():
    """Alarm teslimi GERCEK bir denetim olayi: dis sisteme bilgi gitti mi
    sorusunun cevabi. Onu kaldirmak izlenebilirligi bozardi."""
    kod = _kod(inspect.getsource(svc._dispatch_with_retry))
    assert "record_event(" in kod, (
        "alarm/REST teslim kaydi da kaldirilmis — teslimat izlenemez olur"
    )


def test_telemetri_dispatchinde_COMMIT_yok_ise_DB_yazimi_da_olmamali():
    """Tutarlilik kilidi.

    `_dispatch_outbound` bilerek commit etmiyor (telemetri yolunda DB
    yazimi olmamali). Oyleyse o yoldan cagrilan hicbir sey DB'ye yazmaya
    CALISMAMALI da — calisirsa bosa is uretir.
    """
    from app.services import telemetry_consumer

    kod = _kod(inspect.getsource(telemetry_consumer._dispatch_outbound))
    assert "db.commit()" not in kod, (
        "telemetri dispatch'i commit ediyor — okuma basina DB yazimi demek"
    )
    # Session yine aciliyor cunku `dispatch_event` imzasi onu istiyor ve
    # REST/MQTT yollari hedef sorgusu icin kullaniyor.
    assert "SessionLocal()" in kod


@pytest.mark.parametrize("olay", ["alarm", "fault"])
def test_operator_olaylari_KAYDEDILMEYE_devam(olay: str):
    """Denetim kaydinin ASIL amaci bunlar; kismanin onlara dokunmadigini
    dogruluyoruz."""
    from app.services import alarm_engine_service

    kod = _kod(inspect.getsource(alarm_engine_service))
    assert "record_event(" in kod, f"{olay} olaylari artik kaydedilmiyor"
