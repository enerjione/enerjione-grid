"""Bekci: asili kalan servis kendini sonlandirmali, SAGLIKLI olan YASAMALI.

NEDEN VAR
---------
`restart: unless-stopped` yalnizca CIKIS yapan sureci geri kaldirir. Ana
dongusu kilitlenen bir worker "calisiyor" gorunur ve SCADA'ya veri sessizce
akmayi birakir. Docker healthcheck tek basina yetmez: unhealthy container'i
yeniden BASLATMAZ, yalnizca isaretler.

Bu serviste iki ariza birden aranir: asyncio dongusunun bloklanmasi ve
telemetriyi tasiyan tuketici IPLIGININ olmesi. Ikisi de trafikten BAGIMSIZ
olcumlerdir — testlerin en onemlisi bunu koruyan
`test_sessiz_trafikte_saglikli_kalir`: mesaj gelmeyen sakin bir gecede
saglikli servisi oldurmek, cozdugumuz sorundan daha kotu bir ariza olurdu.
"""

from __future__ import annotations

import asyncio

import pytest

from iec104_outbound import watchdog
from iec104_outbound.config import SETTINGS
from iec104_outbound.main import _kalp_dongusu


class _SahteTuketici:
    """`is_alive()` disinda bir sey yapmayan tuketici taklidi."""

    def __init__(self, canli: bool = True) -> None:
        self.canli = canli

    def is_alive(self) -> bool:
        return self.canli


@pytest.fixture(autouse=True)
def _bekciyi_sifirla():
    """Modul duzeyi durum testler arasinda sizmasin."""
    watchdog.kalp_at()
    watchdog._esik_sn = 0.0
    yield
    watchdog.kalp_at()
    watchdog._esik_sn = 0.0


def test_atis_gecen_sureyi_sifirlar():
    watchdog._son_atis -= 10.0
    assert watchdog.gecen_sn() >= 10.0

    watchdog.kalp_at()

    assert watchdog.gecen_sn() < 1.0


def test_esik_asilinca_saglikli_FALSE_doner():
    watchdog._esik_sn = 5.0
    watchdog._son_atis -= 30.0

    assert watchdog.saglikli() is False


def test_esik_icindeyken_saglikli_kalir():
    watchdog._esik_sn = 60.0
    watchdog._son_atis -= 5.0

    assert watchdog.saglikli() is True


def test_esik_kurulmadan_saglikli_sayilir():
    """Bekci baslamadan once healthcheck 503 dondurmemeli.

    Aksi halde acilis penceresinde container 'unhealthy' damgasi yerdi.
    """
    watchdog._esik_sn = 0.0
    watchdog._son_atis -= 10_000.0

    assert watchdog.saglikli() is True


@pytest.mark.asyncio
async def test_sessiz_trafikte_saglikli_kalir():
    """HIC MESAJ GELMESE BILE atis surer.

    Atis "mesaj isledim"e degil "dongum donuyor"a bagli; bu test o kurali
    kilitler. Bozulursa sakin bir gecede saglikli servis oldurulur.
    """
    tuketici = _SahteTuketici(canli=True)
    watchdog._son_atis -= 100.0
    gorev = asyncio.create_task(_kalp_dongusu(tuketici, aralik_sn=0.01))
    try:
        await asyncio.sleep(0.1)
    finally:
        gorev.cancel()

    # Tek bir mesaj islenmedi ama atis yenilendi.
    assert watchdog.gecen_sn() < 1.0


@pytest.mark.asyncio
async def test_tuketici_ipligi_olunce_atis_DURUR():
    """Olay dongusu donse bile tuketici olduyse atis kesilmeli.

    Bu ariza disaridan gorunmez: asyncio dongusu neseyle doner, ama SCADA'ya
    yayin tamamen durmustur.
    """
    tuketici = _SahteTuketici(canli=False)
    watchdog._son_atis -= 100.0
    gorev = asyncio.create_task(_kalp_dongusu(tuketici, aralik_sn=0.01))
    try:
        await asyncio.sleep(0.1)
    finally:
        gorev.cancel()

    assert watchdog.gecen_sn() >= 100.0


def test_gercek_tuketici_baslamadan_OLU_sayilir():
    """`is_alive` gercek sinifta da iplik durumunu yansitmali."""
    from iec104_outbound.consumer import TelemetryConsumer

    tuketici = TelemetryConsumer(settings=SETTINGS, manager=object())

    # Hic start() edilmedi: iplik yok.
    assert tuketici.is_alive() is False


def test_gercek_tuketici_KAPANISTA_canli_sayilir():
    """Duzgun kapanista bekci sureci oldurmemeli.

    `stop()` sonrasi iplik sonleniyor; bunu "ariza" sayarsak her graceful
    shutdown'da bekci devreye girip cikis kodunu bozardi.
    """
    from iec104_outbound.consumer import TelemetryConsumer

    tuketici = TelemetryConsumer(settings=SETTINGS, manager=object())
    tuketici.stop()

    assert tuketici.is_alive() is True
