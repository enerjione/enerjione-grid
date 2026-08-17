"""Bos (istemcisiz) sunucuda kalp atisi GERCEK dongu uzerinden gelmeli.

YASANAN ARIZA
-------------
Saha cihazinda `e1-grid-ftp-server` tam 61 saniyede bir kendini oldurdu ve
yeniden basladi (RestartCount 29). Bekci hakliydi: atis GERCEKTEN gelmiyordu.
Ama sunucu da saglikliydi -- yalniz BOS'tu.

Neden: `serve_forever()` zaman asimi verilmeden cagriliyordu. pyftpdlib'in
`IOLoop.loop()` fonksiyonu bu durumda su dala girer:

    soonest_timeout = None
    while socket_map:
        poll(soonest_timeout)       # <-- ILK yinelemede poll(None)
        soonest_timeout = sched_poll()

`poll(None)` bir SOKET OLAYI gelene kadar suresiz bloklar. Zamanlayici
(`sched_poll`) o satirin ARDINDAN geldigi icin, hicbir istemci baglanmadikca
zamanlanmis kalp atisi HIC calismaz. 60 sn sonra bekci sureci oldurur.

Ariza kendini SAKLAR: ilk istemci baglanir baglanmaz `poll` doner,
`sched_poll()` bir sonraki son tarihi dondurur ve dongu kalici olarak kendine
gelir. Yani sunucu YALNIZCA hic kimse baglanmamisken oluyordu -- sahada da tam
olarak bu gorundu: tek bir TCP baglantisi restart dongusunu aninda ve kalici
olarak durdurdu.

NEDEN MEVCUT TEST YAKALAMADI
----------------------------
`test_bekci.py::test_baglanti_olmadan_da_atis_gelir` zamanlayiciyi
`loop.sched.poll()` ile ELLE suruyor. Bu, "call_every kaydi calisiyor mu"
sorusunu cevaplar; oysa arizanin tamami `loop()`'un o satira HIC
ULASAMAMASIYDI. Bu dosyadaki testler bu yuzden gercek `serve_forever`
dongusunu surer.
"""

from __future__ import annotations

import inspect
import re
import socket
import threading
import time
from contextlib import contextmanager

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.ioloop import IOLoop
from pyftpdlib.servers import FTPServer

from ftp_server import main as ftp_main
from ftp_server import watchdog

#: Testlerde atis araligi (uretimde 5 sn). Sadece testi hizlandirir; olculen
#: sey uretimin GERCEK poll zaman asimi (`IOLOOP_POLL_TIMEOUT_SN`).
ATIS_SN = 0.2

#: Gozlem penceresi: uretim poll zaman asiminin birkac kati.
#:
#: `or 0` GEREKLI: biri sabiti `None` yaparsa modul IMPORT ederken
#: `TypeError` verir ve TUM dosya toplanamaz -- yani asil arizayi anlatan
#: test hic kosmaz, geriye anlamsiz bir collection error kalirdi. Bu haliyle
#: sabit bozuldugunda testler KOSAR ve nedeni soyleyerek duser.
PENCERE_SN = max(3.0, (ftp_main.IOLOOP_POLL_TIMEOUT_SN or 0) * 3)

#: `_atis_bekle` icinde kullanilan geriye alma miktari.
GERIYE_AL_SN = 1000.0


@pytest.fixture(autouse=True)
def _bekciyi_sifirla():
    watchdog.kalp_at()
    watchdog._esik_sn = 0.0
    yield
    watchdog.kalp_at()
    watchdog._esik_sn = 0.0
    IOLoop._instance = None


def _uyandir(port: int) -> None:
    """Bloke dongude tek bir soket olayi uretir (yalnizca teardown icin)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    except OSError:
        pass


@contextmanager
def _bos_sunucu(kok, *, poll_timeout):
    """GERCEK FTPServer + GERCEK call_every + GERCEK serve_forever.

    Hicbir istemci baglanmaz -- olculen sey tam olarak bos dongu davranisi.
    Port 0: cakisma olmasin diye cekirdek secer, hicbir yere sabitlenmez.
    """
    IOLoop._instance = None
    auth = DummyAuthorizer()
    auth.add_user("t", "t", str(kok), perm="elradfmwMT")
    handler = type("_TestHandler", (FTPHandler,), {"authorizer": auth})

    srv = FTPServer(("127.0.0.1", 0), handler)
    port = srv.socket.getsockname()[1]
    IOLoop.instance().call_every(ATIS_SN, watchdog.kalp_at)

    th = threading.Thread(
        target=srv.serve_forever,
        kwargs={"timeout": poll_timeout, "handle_exit": False},
        daemon=True,
    )
    th.start()
    try:
        yield port
    finally:
        _uyandir(port)          # poll(None) bloke ise cozulsun
        try:
            srv.close_all()
        except Exception:       # noqa: BLE001
            pass
        th.join(timeout=3)
        IOLoop._instance = None


def _atis_bekle(pencere_sn: float) -> bool:
    """Pencere icinde EN AZ BIR atis geldi mi. Sabit sleep degil, son tarih."""
    watchdog._son_atis -= GERIYE_AL_SN
    bitis = time.monotonic() + pencere_sn
    while time.monotonic() < bitis:
        if watchdog.gecen_sn() < GERIYE_AL_SN * 0.9:
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# T15 -- ESKI DAVRANISIN YENIDEN URETIMI (mutasyon siniri)
# ---------------------------------------------------------------------------


def test_T15_timeoutsuz_dongude_atis_GELMEZ(tmp_path):
    """Duzeltmeden onceki cagri bicimi: `serve_forever()` (timeout=None).

    Bu test arizanin KENDISIDIR. Yesil kalmasi, duzeltmenin gercekten bir
    seyi degistirdigini kanitlar: ayni kosum duzeltilmis cagri bicimiyle
    (T16) atis uretir, bu bicimle uretmez.
    """
    with _bos_sunucu(tmp_path, poll_timeout=None):
        assert _atis_bekle(PENCERE_SN) is False, (
            "timeout=None ile atis geldi -- pyftpdlib davranisi degismis "
            "olabilir, duzeltmenin gerekcesini yeniden dogrulayin"
        )


# ---------------------------------------------------------------------------
# T02 / T16 -- DUZELTILMIS DAVRANIS
# ---------------------------------------------------------------------------


def test_T16_uretim_zaman_asimiyla_bos_dongude_atis_GELIR(tmp_path):
    """Hicbir istemci baglanmadan atis ilerlemeli.

    UYGULAMA SABITI kullanilir (`IOLOOP_POLL_TIMEOUT_SN`); testin kendi
    uydurdugu kucuk bir deger, uretimdeki degerin dogru oldugunu
    kanitlamazdi.
    """
    with _bos_sunucu(tmp_path, poll_timeout=ftp_main.IOLOOP_POLL_TIMEOUT_SN):
        assert _atis_bekle(PENCERE_SN) is True, (
            "bos sunucuda atis gelmedi -- bekci saglikli sunucuyu oldurur"
        )


def test_T03_bos_sunucu_bekci_esigini_asmaz(tmp_path):
    """Uretim orani korunuyor mu: atislar arasi gecikme << bekci esigi.

    Sahadaki ariza tam olarak bunun ihlaliydi (gecen sure 60 sn'yi asti).
    """
    with _bos_sunucu(tmp_path, poll_timeout=ftp_main.IOLOOP_POLL_TIMEOUT_SN):
        # Once dongunun gercekten dondugunu dogrula, sonra sagligi izle.
        assert _atis_bekle(PENCERE_SN) is True
        watchdog._esik_sn = ftp_main.BEKCI_ESIK_SN

        bitis = time.monotonic() + PENCERE_SN
        en_kotu = 0.0
        while time.monotonic() < bitis:
            en_kotu = max(en_kotu, watchdog.gecen_sn())
            assert watchdog.saglikli() is True, (
                f"bos sunucu saglikli sayilmadi (gecen={watchdog.gecen_sn():.1f}s)"
            )
            time.sleep(0.1)

        assert en_kotu < ftp_main.BEKCI_ESIK_SN / 2, (
            f"atislar arasi en kotu gecikme {en_kotu:.1f}s -- esige fazla yakin"
        )


# ---------------------------------------------------------------------------
# T13 -- BEKCI HALA GERCEK DONMAYI YAKALIYOR MU
# ---------------------------------------------------------------------------


def test_T13_gercekten_donan_dongu_HALA_yakalanir(tmp_path):
    """Duzeltme bekciyi korlestirmemeli.

    Dongunun kendisi bloklanir (zamanlanmis bir cagri icinde uyuma). Bu tam
    olarak uretimdeki 'ioloop kilitlendi' senaryosudur: kayit yerinde durur
    ama dongu donmez.
    """
    with _bos_sunucu(tmp_path, poll_timeout=ftp_main.IOLOOP_POLL_TIMEOUT_SN):
        assert _atis_bekle(PENCERE_SN) is True, "on kosul: dongu donuyor olmali"

        donma_sn = 2.0
        IOLoop.instance().call_later(0.05, lambda: time.sleep(donma_sn))
        time.sleep(0.3)         # donmanin baslamasini bekle

        watchdog._son_atis -= GERIYE_AL_SN
        bitis = time.monotonic() + donma_sn * 0.5
        while time.monotonic() < bitis:
            assert watchdog.gecen_sn() > GERIYE_AL_SN * 0.9, (
                "dongu donmusken atis geldi -- bekci artik gercek donmayi "
                "yakalayamaz"
            )
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# Cagri yeri tripwire
# ---------------------------------------------------------------------------


def test_main_serve_forever_ACIK_zaman_asimiyla_cagirir():
    """Argumani birisi silerse ariza SESSIZCE geri gelir.

    Davranis testleri kutuphane semantigini olcer; bu test uretim cagrisinin
    o semantige gore yazildigini olcer.
    """
    kaynak = inspect.getsource(ftp_main.main)
    assert re.search(r"serve_forever\(\s*timeout\s*=", kaynak), (
        "main() `serve_forever`i acik zaman asimi olmadan cagiriyor -- bos "
        "sunucuda kalp atisi durur ve bekci 60 sn'de bir restart uretir"
    )

    poll = ftp_main.IOLOOP_POLL_TIMEOUT_SN
    assert isinstance(poll, (int, float)) and not isinstance(poll, bool), (
        f"IOLOOP_POLL_TIMEOUT_SN sayi olmali, {poll!r} verilmis -- `None` "
        "pyftpdlib'i suresiz bloklayan dala sokar ve ariza geri gelir"
    )
    assert poll > 0, f"poll zaman asimi pozitif olmali, {poll!r}"
    assert poll < ftp_main.BEKCI_ESIK_SN, (
        f"poll zaman asimi ({poll}) bekci esiginden ({ftp_main.BEKCI_ESIK_SN}) "
        "kucuk olmali; aksi halde tek bir poll bile esigi asabilir"
    )
