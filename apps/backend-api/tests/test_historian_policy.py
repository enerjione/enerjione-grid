""" Historian (arsiv) politikasi — her okuma arsive yazilmaz.

GERCEK SCADA PRATIGI
--------------------
Anlik deger (RTDB) her zaman guncel tutulur; arsive yalnizca isaretlenen
tag'ler, olu bant suzgecinden gecerek yazilir.

Bu sistemde iki on kosul da SAGLANIYOR — bu yuzden arsivi kismak alarm
dogrulugunu ETKILEMIYOR:
  * alarm-service JetStream akisini dinliyor, gecmis sorgusu YAPMIYOR,
  * canli deger `telemetry_latest` tablosunda.

OLCUM (saha test cihazi, 15 cihaz): 375 okuma/sn ve hepsi arsive giriyordu.
"""

from __future__ import annotations

import pytest

from app.services import historian_policy as hp


class _SahteDB:
    """`select(...)` sonucunu sabitleyen minimal session."""

    def __init__(self, satirlar, patlat: Exception | None = None):
        self.satirlar = satirlar
        self.patlat = patlat
        self.cagri = 0

    def execute(self, _stmt):
        self.cagri += 1
        if self.patlat is not None:
            raise self.patlat
        return self

    def all(self):
        return self.satirlar


@pytest.fixture(autouse=True)
def _temiz():
    hp.reset_caches()
    yield
    hp.reset_caches()


def _db(*satirlar):
    return _SahteDB(list(satirlar))


# ---------------------------------------------------------------------------
# historize bayragi
# ---------------------------------------------------------------------------

def test_isaretli_sinyal_ARSIVLENIYOR():
    db = _db(("master.actual_current", True, 0.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.actual_current", value=12.0)


def test_isaretsiz_sinyal_ARSIVLENMIYOR():
    """Seri no / firmware gibi statik metadata ve komut noktalari."""
    db = _db(("master.info_serial_number", False, 0.0))
    assert not hp.should_archive(
        db, device_id=1, signal_key="master.info_serial_number", value=12345.0
    )


def test_BILINMEYEN_sinyal_arsivleniyor():
    """Katalogda olmayan anahtari sessizce atmak, yeni eklenen bir sinyalin
    arsivinin HIC olusmamasina yol acardi."""
    db = _db(("master.x", True, 0.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.YENI", value=1.0)


def test_katalog_OKUNAMAZSA_her_sey_arsivleniyor():
    """Migration henuz uygulanmamis olabilir. Sessizce arsivlemeyi KESMEK
    veri kaybi olurdu; guvenli yon fazla yazmaktir."""
    db = _SahteDB([], patlat=RuntimeError("kolon yok"))
    assert hp.should_archive(db, device_id=1, signal_key="master.x", value=1.0)


# ---------------------------------------------------------------------------
# Olu bant
# ---------------------------------------------------------------------------

def test_olu_bant_KAPALIYKEN_her_okuma_arsivleniyor():
    db = _db(("master.v", True, 0.0))
    for deger in (10.0, 10.0, 10.001, 10.002):
        assert hp.should_archive(db, device_id=1, signal_key="master.v", value=deger)


def test_olu_bant_KUCUK_degisimi_eliyor():
    db = _db(("master.v", True, 1.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=230.0)
    assert not hp.should_archive(db, device_id=1, signal_key="master.v", value=230.5)
    assert not hp.should_archive(db, device_id=1, signal_key="master.v", value=229.6)


def test_olu_bant_BUYUK_degisimi_geciriyor():
    db = _db(("master.v", True, 1.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=230.0)
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=232.0)


def test_olu_bant_SON_ARSIVLENEN_degere_gore_calisiyor():
    """Son OKUNAN'a gore olsaydi, esigin altinda surekli tirmanan bir deger
    HIC arsivlenmezdi (sinsi veri kaybi)."""
    db = _db(("master.v", True, 1.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=100.0)
    for deger in (100.4, 100.8, 101.2):
        pass  # her adim 0.4 artiyor
    assert not hp.should_archive(db, device_id=1, signal_key="master.v", value=100.4)
    assert not hp.should_archive(db, device_id=1, signal_key="master.v", value=100.8)
    # 101.2, son ARSIVLENEN 100.0'dan 1.2 uzakta -> gecmeli
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=101.2)


def test_olu_bant_CIHAZ_BAZINDA_ayri():
    """Ortak tutulsaydi bir cihazin degeri digerinin arsivini bastirirdi."""
    db = _db(("master.v", True, 5.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.v", value=100.0)
    assert hp.should_archive(db, device_id=2, signal_key="master.v", value=100.0)


def test_SAYISAL_OLMAYAN_deger_olu_banta_takilmiyor():
    """String/binary sinyalde olu bant anlamsiz."""
    db = _db(("master.s", True, 5.0))
    assert hp.should_archive(db, device_id=1, signal_key="master.s", value=None)
    assert hp.should_archive(db, device_id=1, signal_key="master.s", value=None)


# ---------------------------------------------------------------------------
# Onbellek
# ---------------------------------------------------------------------------

def test_katalog_ONBELLEKLENIYOR():
    """Okuma basina katalog sorgusu, cozmeye calistigimiz sorunu buyuturdu."""
    db = _db(("master.v", True, 0.0))
    for _ in range(50):
        hp.should_archive(db, device_id=1, signal_key="master.v", value=1.0)
    assert db.cagri == 1, f"katalog {db.cagri} kez sorgulandi"


def test_onbellek_SINIRLI():
    """Sinirsiz buyume, 600 cihazda bellek sizintisi olurdu."""
    assert hp._LAST_CACHE_MAX >= 600 * 193, "sinir gercek olcegin altinda"
    assert hp._LAST_CACHE_MAX <= 2_000_000, "sinir pratikte sinirsiz"


# ---------------------------------------------------------------------------
# Tuketici baglantisi
#
# Politikanin dogru olmasi yetmez, TUKETICIDE UYGULANMASI gerekir. Ilk
# yazimda bu testler yoktu ve `if True or should_archive(...)` yapan bir
# mutasyon KACTI — politika calisir gorunurken arsive her sey yaziliyordu.
# ---------------------------------------------------------------------------

def _tuketici_kodu() -> str:
    import inspect
    import re

    from app.services import telemetry_consumer

    kaynak = inspect.getsource(telemetry_consumer._persist_batch)
    kaynak = re.sub(r'""".*?"""', "", kaynak, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", kaynak, flags=re.MULTILINE)


def test_tuketici_politikayi_UYGULUYOR():
    kod = _tuketici_kodu()
    assert "historian_policy.should_archive(" in kod, (
        "arsiv politikasi tuketicide cagrilmiyor — her okuma arsive gider"
    )


def test_arsiv_satiri_politikanin_ICINDE_ekleniyor():
    """Kosul disinda kalirsa politika hicbir sey yapmaz."""
    kod = _tuketici_kodu()
    i_cagri = kod.find("historian_policy.should_archive(")
    i_ekle = kod.find("historian_rows.append(")
    assert i_cagri != -1 and i_ekle != -1
    assert i_cagri < i_ekle, "arsiv satiri kosuldan ONCE ekleniyor"

    # Kontrol `if`in BASINDAN baslamali, cagridan degil.
    #
    # Ilk yazimda cagridan itibaren bakiyordum ve `if True or should_archive(...)`
    # yapan mutasyon KACTI: eklenen `or` cagrinin ONUNDE kaldigi icin
    # dilimin disinda kaliyordu. Mutasyon testi yakaladi.
    i_if = kod.rfind("if ", 0, i_cagri)
    assert i_if != -1, "kosulun basi bulunamadi"
    kosul = kod[i_if:i_ekle]
    for kacamak in (" or ", " and ", "True", "not "):
        assert kacamak not in kosul.replace("should_archive", ""), (
            f"kosul baska bir ifadeyle zayiflatilmis ({kacamak!r}): {kosul!r}"
        )


def test_CANLI_deger_politikadan_ETKILENMIYOR():
    """RTDB her zaman guncel kalmali — arsiv kisilsa bile ekran ve alarm
    son degeri gormeye devam etmeli. Bu, SCADA modelinin temeli."""
    kod = _tuketici_kodu()
    i_kosul = kod.find("historian_policy.should_archive(")
    i_latest = kod.find("latest_rows[_latest_key]")
    assert i_latest != -1, "telemetry_latest yazimi bulunamadi"
    assert i_latest > i_kosul, "beklenmeyen sira"
    # `latest_rows` atamasi arsiv kosulunun govdesinde OLMAMALI: govde
    # `historian_rows.append(` ile bitiyor.
    i_ekle_son = kod.find("})", kod.find("historian_rows.append("))
    assert i_latest > i_ekle_son, (
        "canli deger yazimi arsiv kosulunun ICINDE — arsiv kisilinca ekran "
        "ve alarm da son degeri kaybeder"
    )
