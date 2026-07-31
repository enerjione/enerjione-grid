"""Yari-acik oturum SESSIZCE olmemeli (denetim A7).

YASANAN SORUN
-------------
`session.unacked` yalnizca gelen bir S-frame ile sifirlaniyordu ve sunucu
tarafinda hicbir zamanlayici yoktu: ne t1/t3, ne okuma zaman asimi, ne TEST_ACT
keepalive.

ZINCIR
------
SCADA master ile arada kalan switch/router yeniden baslar ya da master sureci
donar: TCP baglantisi YARI-ACIK kalir, FIN gelmez.

  * Sunucu 12 I-frame gonderir, S-frame ack gelmez.
  * `unacked` 12'de SABITLENIR.
  * `_send_i` artik writer'a hicbir sey yazmadigi icin TCP de kopuklugu FARK
    EDEMEZ (SO_KEEPALIVE ayarli degil, TEST_ACT gonderilmiyor).
  * Oturum `_sessions` icinde `started=True` olarak kalir.

Sonuc: TUM telemetri degisimleri sessizce duser — SCADA'da veriler DONAR,
IEC 104 cikisi olur ve container yeniden baslatilana kadar kendiliginden
duzelmez. Ustelik her dusen frame icin rate-limit'siz WARNING basiliyordu;
600 cihazlik yukte log dosyalari saniyeler icinde donup ariza aninda
bakilacak diger kayitlari supuruyordu.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from iec104_outbound import server as srv

KAYNAK = Path(srv.__file__).read_text(encoding="utf-8")


# ------------------------------------------------------- zamanlayici var mi


def test_okuma_ZAMAN_ASIMINA_bagli():
    """Suresiz `reader.read()` yari-acik baglantiyi asla fark etmez."""
    kaynak = inspect.getsource(srv.IEC104Server._handle_client)
    assert "asyncio.wait_for(reader.read" in kaynak, (
        "okuma zaman asimi yok — yari-acik baglanti sonsuza kadar yasar"
    )
    assert "T3_IDLE_SEC" in kaynak


def test_bosta_kalinca_TEST_ACT_gonderilir():
    kaynak = inspect.getsource(srv.IEC104Server._handle_client)
    assert "APCI_U_TEST_ACT" in kaynak, "keepalive gonderilmiyor"


def test_cevapsiz_TEST_ACT_oturumu_KAPATIR():
    """Keepalive gondermek yetmez; cevapsizsa oturum dusmeli.

    Dusmezse `started=True` oturum sonsuza kadar kalir ve telemetri sessizce
    o dipsiz kuyuya akmaya devam eder.
    """
    kaynak = inspect.getsource(srv.IEC104Server._handle_client)
    assert "test_pending" in kaynak
    # `if session.test_pending: ... break` kalibi
    agac = ast.parse(inspect.getsource(srv.IEC104Server._handle_client).lstrip())
    breakler = [d for d in ast.walk(agac) if isinstance(d, ast.Break)]
    assert breakler, "cevapsiz keepalive sonrasi oturumdan cikis yok"


def test_veri_gelince_keepalive_durumu_SIFIRLANIR():
    """Aksi halde canli bir oturum ikinci bosluk turunda yanlislikla kapanirdi."""
    kaynak = inspect.getsource(srv.IEC104Server._handle_client)
    assert "session.test_pending = False" in kaynak


# --------------------------------------------------------------- rate-limit


def test_k_penceresi_uyarisi_RATE_LIMITLI():
    """Sinirsiz uyari, ariza aninda bakilacak loglari supuruyordu."""
    kaynak = inspect.getsource(srv.IEC104Server._send_i)
    assert "last_kwindow_warn_at" in kaynak, "k-penceresi uyarisi rate-limit'siz"
    assert "KWINDOW_WARN_INTERVAL_SEC" in kaynak


def test_rate_limit_oturum_BASINA():
    """Global olsaydi bir oturumun gurultusu digerinin uyarisini yutardi."""
    kaynak = inspect.getsource(srv._ClientSession.__init__)
    assert "last_kwindow_warn_at" in kaynak, (
        "rate-limit durumu oturumda degil — global bir sayac oturumlari "
        "birbirinin uyarisini bastirir hale getirirdi"
    )


# ------------------------------------------------------------ sabit degerler


def test_t3_araligi_STANDARDA_yakin():
    """IEC 60870-5-104 t3 varsayilani 20 sn."""
    assert 10.0 <= srv.T3_IDLE_SEC <= 60.0


def test_uyari_araligi_MAKUL():
    """Cok kisa: gurultu geri gelir. Cok uzun: sorun gorunmez olur."""
    assert 5.0 <= srv.KWINDOW_WARN_INTERVAL_SEC <= 300.0


def test_oturum_yeni_alanlarla_kurulabiliyor():
    """Davranis: yeni alanlar varsayilan degerlerle geliyor mu."""

    class _W:
        def is_closing(self):
            return False

    s = srv._ClientSession(writer=_W(), peer="1.2.3.4:1")
    assert s.test_pending is False
    assert s.last_kwindow_warn_at == 0.0
