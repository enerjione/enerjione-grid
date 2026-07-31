"""WebSocket fan-out koprusu — coklu surece gecisin ON KOSULU.

NEDEN BU TESTLER VAR
--------------------
Canli deger yayini bugun bellek-ici: tuketici dogrudan ayni surecteki WS
abonelerine yaziyor. Backend TEK surec oldugu surece calisir.

Coklu surece gecince (API worker'lari + ayri tuketici container'i) bu
kirilir ve ariza SESSIZDIR: soket bagli gorunur, "canli" yazar, sadece
deger akmaz. Sahada teshisi cok zor.

En kritik ve en kolay yapilan hata QUEUE GROUP kullanmaktir: queue group
mesajlari abone surecler ARASINDA PAYLASTIRIR, yani her surec mesajlarin
1/N'ini gorur. Ekran "calisiyor" ama eksik veri gosterir — bellek-ici
yayindan bile kotu bir durum, cunku bozukluk kismi ve fark edilmesi zor.

Asagidaki `test_fanout_her_surece_ULASIR` tam olarak bunu kilitler: IKI
bagimsiz kopru (iki sureci temsil eder) kurulur, BIR mesaj yayinlanir ve
IKISININ DE aldigi dogrulanir. Queue group'a gecilirse bu test kirmizi olur.
"""

from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

import pytest

from app.core.config import settings
from app.services import ws_broadcaster as wsb


def _nats_erisilebilir() -> bool:
    """Yerel NATS ayakta mi? Degilse gercek-baglantili testler atlanir."""
    try:
        parsed = urlparse(settings.nats_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4222
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


nats_gerekli = pytest.mark.skipif(
    not wsb._NATS_AVAILABLE or not _nats_erisilebilir(),
    reason="yerel NATS erisilebilir degil (nats-server calisiyor olmali)",
)


def _bekle(kosul, timeout: float = 5.0, aralik: float = 0.05) -> bool:
    son = time.monotonic() + timeout
    while time.monotonic() < son:
        if kosul():
            return True
        time.sleep(aralik)
    return False


# --------------------------------------------------------------- kopru yokken


def test_kopru_hazir_degilse_yerel_dagitim_yapilir(monkeypatch):
    """NATS yoksa yayin BELLEK-ICI calismaya devam etmeli.

    Tek surecli kurulum NATS olmadan da tam calisir; kopru bir iyilestirme,
    zorunluluk degil. Bu davranis bozulursa NATS'siz her kurulumda canli
    deger ekrani olur.
    """
    teslim: list[dict] = []
    b = wsb.TelemetryWsBroadcaster()
    monkeypatch.setattr(b, "_deliver_local", teslim.append)
    # Kopru hazir degil (varsayilan durum, baslatilmadi)
    monkeypatch.setattr(wsb.bridge, "publish", lambda payload: False)

    b.broadcast({"device_code": "DEV1", "signal_key": "s", "value": 1})

    assert len(teslim) == 1, "kopru yokken yerel dagitim yapilmadi"


def test_kopru_hazirsa_yerel_dagitim_YAPILMAZ(monkeypatch):
    """Kopru yayinladiysa yerelde TEKRAR dagitilmamali — cift teslim olurdu.

    Yayinlayan surec de abone oldugu icin mesaji NATS uzerinden GERI ALIR;
    dagitim orada yapilir. Burada da dagitirsak istemci ayni degeri iki kez
    gorur.
    """
    teslim: list[dict] = []
    b = wsb.TelemetryWsBroadcaster()
    monkeypatch.setattr(b, "_deliver_local", teslim.append)
    monkeypatch.setattr(wsb.bridge, "publish", lambda payload: True)

    b.broadcast({"device_code": "DEV1", "signal_key": "s", "value": 1})

    assert teslim == [], "kopru yayinladigi halde yerelde de dagitildi (cift teslim)"


# ------------------------------------------------------- gercek NATS ile


@nats_gerekli
def test_fanout_her_surece_ULASIR():
    """EN KRITIK TEST: iki ayri surec de AYNI mesaji almali.

    Iki bagimsiz kopru = iki backend sureci. Bir mesaj yayinlanir; ikisinin
    DE almasi gerekir (fan-out). Queue group kullanilsaydi mesaj yalnizca
    BIRINE giderdi ve bu test kirmizi olurdu.
    """
    alinan_a: list[dict] = []
    alinan_b: list[dict] = []
    a = wsb._WsNatsBridge()
    b = wsb._WsNatsBridge()
    try:
        assert a.start(on_message=alinan_a.append), "A koprusu baglanamadi"
        assert b.start(on_message=alinan_b.append), "B koprusu baglanamadi"

        a.publish({"device_code": "DEV-FANOUT", "signal_key": "sig", "value": 42})

        assert _bekle(lambda: alinan_a and alinan_b), (
            f"fan-out basarisiz: A={len(alinan_a)} B={len(alinan_b)} "
            "(queue group kullanildiysa yalnizca biri alir)"
        )
        assert alinan_a[0]["value"] == 42
        assert alinan_b[0]["value"] == 42
        assert alinan_a[0]["device_code"] == "DEV-FANOUT"
    finally:
        a.stop()
        b.stop()


@nats_gerekli
def test_yayinlayan_surec_kendi_mesajini_da_alir():
    """Yayinlayan surec mesaji NATS uzerinden GERI ALMALI.

    Tasarim buna dayaniyor: `broadcast` kopru yayinladiginda yerelde
    dagitmiyor, cunku mesajin geri gelecegini varsayiyor. Bu varsayim
    bozulursa yayinlayan surecteki WS istemcileri HICBIR SEY almaz.
    """
    alinan: list[dict] = []
    a = wsb._WsNatsBridge()
    try:
        assert a.start(on_message=alinan.append)
        a.publish({"device_code": "DEV-SELF", "signal_key": "s", "value": 7})
        assert _bekle(lambda: len(alinan) >= 1), (
            "yayinlayan surec kendi mesajini almadi — broadcast() yerelde de "
            "dagitmadigi icin bu istemciler veri goremezdi"
        )
        assert alinan[0]["value"] == 7
    finally:
        a.stop()


@nats_gerekli
def test_bozuk_payload_kopruyu_dusurmez():
    """Gecersiz JSON gelirse kopru calismaya DEVAM etmeli.

    Kopru tum surecin canli deger akisini tasiyor; tek bir bozuk mesaj
    yuzunden durursa butun ekranlar sessizce olur.
    """
    alinan: list[dict] = []
    a = wsb._WsNatsBridge()
    try:
        assert a.start(on_message=alinan.append)
        # Ham bozuk bayt gonder (kopru json.loads'ta patlayacak ama yutmali)
        import asyncio

        fut = asyncio.run_coroutine_threadsafe(
            a._nc.publish(settings.ws_fanout_subject, b"{bozuk-json"), a._loop
        )
        fut.result(timeout=3)
        time.sleep(0.3)
        # Ardindan gecerli bir mesaj: kopru hala calisiyor olmali
        a.publish({"device_code": "DEV-OK", "signal_key": "s", "value": 1})
        assert _bekle(lambda: any(m.get("device_code") == "DEV-OK" for m in alinan)), (
            "bozuk mesajdan sonra kopru calismayi birakti"
        )
    finally:
        a.stop()


# --------------------------------------------- REGRESYON: kopuk NATS


def test_kopuk_baglantida_is_ready_FALSE_doner(monkeypatch):
    """NATS kopunca kopru "hazirim" DEMEMELI.

    REGRESYON TESTI. `is_ready` bir zamanlar sadece `_ready` mandalina ve
    `_nc is not None` kosuluna bakiyordu. Ikisi de yalnizca stop()'ta
    temizlendigi ve max_reconnect_attempts=-1 yuzunden `_nc` hicbir zaman
    None olmadigi icin, NATS kopsa bile is_ready True kaliyordu.

    Sonucu su zincirdi:
        is_ready True -> publish() True doner
        -> broadcast() erken doner (mesaj NATS'tan geri gelecek sanir)
        -> ama NATS kopuk, mesaj HIC gelmez
        -> YEREL YEDEK YOL CALISMAZ
        -> canli deger ekrani TEK SURECTE BILE kararir.

    Yani kopru, cozmek icin var oldugu sorunu daha kotu bir bicimde
    yaratiyordu. Bu test o zincirin ilk halkasini kilitler.
    """
    b = wsb._WsNatsBridge()
    b._ready.set()

    class _KopukNc:
        is_connected = False

    b._nc = _KopukNc()
    assert b.is_ready is False, (
        "baglanti kopukken is_ready True donuyor — yerel yedek yol devre disi kalir"
    )


def test_kopuk_baglantida_yayin_YEREL_yola_duser(monkeypatch):
    """Zincirin sonu: NATS kopukken mesaj yerel abonelere ULASMALI."""
    teslim: list[dict] = []
    b = wsb.TelemetryWsBroadcaster()
    monkeypatch.setattr(b, "_deliver_local", teslim.append)

    kopru = wsb._WsNatsBridge()
    kopru._ready.set()

    class _KopukNc:
        is_connected = False

    kopru._nc = _KopukNc()
    monkeypatch.setattr(wsb, "bridge", kopru)

    b.broadcast({"device_code": "DEV1", "signal_key": "s", "value": 1})

    assert len(teslim) == 1, (
        "NATS kopukken mesaj yerel abonelere ulasmadi — canli deger ekrani kararir"
    )


def test_publish_hatasi_sayaca_yansir():
    """`publish_failures` OLU OLMAMALI.

    Hata publish task'inin ICINDE olustugu icin disaridaki except'e dusmuyor;
    done-callback olmadan sayac sonsuza kadar 0 kalir ve "yayin calisiyor mu"
    sorusuna yalan soyler.
    """
    import asyncio as _a

    b = wsb._WsNatsBridge()

    async def _kos():
        async def _patla():
            raise RuntimeError("publish patladi")

        task = _a.ensure_future(_patla())
        b._inflight.add(task)
        task.add_done_callback(b._on_publish_done)
        try:
            await task
        except RuntimeError:
            pass
        await _a.sleep(0)

    _a.run(_kos())
    assert b.publish_failures == 1, "publish hatasi sayaca yansimadi (sayac olu)"
