"""Bekci: asili kalan FTP sunucusu kendini sonlandirmali, SAGLIKLI olan YASAMALI.

NEDEN VAR
---------
`restart: unless-stopped` yalnizca CIKIS yapan sureci geri kaldirir. pyftpdlib
ioloop'u kilitlenen bir sunucu "calisiyor" gorunur: container ayakta, surec
ayakta, ama cihazlar dosya yazamiyor. Docker healthcheck tek basina yetmez:
unhealthy container'i yeniden BASLATMAZ, yalnizca isaretler.

Bu serviste atis ioloop'un KENDISINE zamanlanir (`call_every`). Dongu
donuyorsa atis gelir; `serve_forever` icinde bir sey kilitlenirse zamanlanmis
cagri hic calismaz. Olcum TRAFIKTEN BAGIMSIZDIR: ioloop soketleri zaman
asimiyla yoklar, yani hicbir cihaz baglanmasa da doner — bunu koruyan test
`test_baglanti_olmadan_da_atis_gelir`. Bozulursa sakin bir gecede saglikli
servis oldurulur ki bu, cozdugumuz sorundan daha kotu bir ariza olurdu.
"""

from __future__ import annotations

import time

import pytest

from ftp_server import watchdog


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


def test_baglanti_olmadan_da_atis_gelir():
    """HICBIR CIHAZ BAGLANMASA BILE atis uretilmeli.

    Gercek `IOLoop.call_every` kaydi ve gercek zamanlayici kullanilir; sahte
    bir zamanlayici "kayit gercekten calisiyor mu" sorusunu cevaplamazdi.
    Zamanlayici `sched.poll()` ile surulur — `ioloop.poll()` degil, cunku o
    Windows'ta bos fd listesiyle `select` cagirip OSError veriyor; oysa
    sinanan sey soket yoklamasi degil, ZAMANLAYICI kaydi.

    Hicbir soket kayitli degil: atis yine de geliyorsa olcum trafikten
    bagimsiz demektir.
    """
    from pyftpdlib.ioloop import IOLoop

    loop = IOLoop()
    try:
        loop.call_every(0.01, watchdog.kalp_at)
        watchdog._son_atis -= 100.0

        bitis = time.monotonic() + 0.3
        while time.monotonic() < bitis:
            loop.sched.poll()
            time.sleep(0.01)

        assert watchdog.gecen_sn() < 1.0
    finally:
        loop.close()


def test_dongu_donmezse_atis_GELMEZ():
    """Zamanlayici kayitli olsa da dongu donmuyorsa atis olmamali.

    Yakalamasi gereken ariza tam olarak budur: kayit duruyor ama
    `serve_forever` icinde bir sey kilitlenmis.
    """
    from pyftpdlib.ioloop import IOLoop

    loop = IOLoop()
    try:
        loop.call_every(0.01, watchdog.kalp_at)
        watchdog._son_atis -= 100.0

        # Dongu HIC dondurulmuyor.
        time.sleep(0.05)

        assert watchdog.gecen_sn() >= 100.0
    finally:
        loop.close()
