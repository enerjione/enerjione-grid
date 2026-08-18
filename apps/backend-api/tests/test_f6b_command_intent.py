"""F6-B — komut NIYETI siniri: tip, aralik ve kombinasyon.

NE KORUYOR
----------
Fiziksel komut uretmeden once backend'in "biz bunu gercekten ISTEDIK mi"
sorusuna kesin cevap vermesi. Gateway'in F6-G dogrulayicisi (v1.11.1)
"cihaz bunu kabul eder mi" sorusunu ayrica cevapliyor; bu dosya onun
kopyasi DEGIL, backend tarafindaki niyet sinirinin testidir.

NEDEN SERVISTE SINANIYOR
------------------------
REST semasi yalnizca ARAYUZ kapisini korur. `queue_command` uc yerden
cagriliyor (arayuz, yapilandirma uygulama, IEC 104) ve sinir hepsinde
gecerli olmali; tek bir ucu duzeltip "gecti" demek P0 bypass'i kacirmakti.

TIP GUVENLIGI NEDEN AYRI BIR MADDE
----------------------------------
Python'da `True == 1` ve `isinstance(True, int)` dogrudur. `isinstance` ile
yazilmis bir kontrol `count=True` girdisini sessizce `count=1`e cevirir —
cagiranin GONDERMEDIGI bir niyet uretilir. Ayni sekilde `"1"` ve `1.0` da
tip hatasidir; dogru tepki duzeltmek degil REDDETMEKTIR.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.services import device_command_service as svc

SLUG = "reset_all_fcis"
MODEL = "horstmann_sn_2_0"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    s = Session()
    s.add(Gateway(code="GW-1", name="G", host="10.0.0.1", listen_port=20000,
                  token="t" * 20, is_active=True))
    s.commit()
    s.add(Device(code="DEV-1", name="D", gateway_code="GW-1", model=MODEL,
                 ip_address="10.0.0.10", latitude=39.0, longitude=35.0))
    s.add(SignalCatalog(key=f"master.{SLUG}", model=MODEL, label="FCI reset",
                        data_type="binary_output", dnp3_index=7, is_active=True))
    s.commit()
    yield s
    s.close()


def _cihaz(db) -> Device:  # noqa: ANN001
    return db.scalar(select(Device).where(Device.code == "DEV-1"))


def _komut_sayisi(db) -> int:  # noqa: ANN001
    return int(db.scalar(select(func.count()).select_from(DeviceCommand)) or 0)


def _kuyrukla(db, **kw):  # noqa: ANN001
    return svc.queue_command(
        db, device=_cihaz(db), slug=SLUG, actor="tester", origin="ui", **kw
    )


# ---------------------------------------------------------------------------
# Gecerli uretim komutu — DAVRANIS DEGISMEMELI
# ---------------------------------------------------------------------------
def test_gecerli_uretim_latch_komutu_KABUL(db):
    q = _kuyrukla(db)
    db.commit()

    assert _komut_sayisi(db) == 1
    row = db.scalars(select(DeviceCommand)).one()
    assert row.op_type == "latch_on"
    assert row.count == 1
    assert row.on_time_ms == 0
    assert row.off_time_ms == 0
    assert row.status == "pending"
    assert row.dnp3_index == 7
    assert q.command == SLUG


def test_acik_gecerli_degerler_de_KABUL(db):
    """Uretim varsayilanlariyla ayni degerler acikca verilirse de gecmeli."""
    _kuyrukla(db, count=1, on_time_ms=0, off_time_ms=0)
    db.commit()
    assert _komut_sayisi(db) == 1


# ---------------------------------------------------------------------------
# op_type sozlesmesi — alias/normalizasyon YOK
# ---------------------------------------------------------------------------
def test_op_type_uretimde_backend_sabiti():
    assert svc.ALLOWED_OP_TYPES == frozenset({"latch_on"})
    assert svc.OP_TYPE_LATCH_ON == "latch_on"


@pytest.mark.parametrize(
    "op_type",
    ["LATCH_ON", "latch-on", "Latch_On", " latch_on", "latch_on ",
     "pulse", "pulse_on", "trip", "latch_off", "", None, 1, True],
)
def test_gecersiz_op_type_REDDEDILIR(op_type):
    """Yazim varyantlari SESSIZCE duzeltilmez — tahmin edip fiziksel komut
    uretmek tam da kapatmaya calistigimiz hata sinifi."""
    with pytest.raises(svc.CommandRejected) as exc:
        svc.validate_command_intent(
            slug=SLUG, op_type=op_type, count=1, on_time_ms=0, off_time_ms=0
        )
    assert exc.value.reason == "invalid_op_type"


# ---------------------------------------------------------------------------
# TIP GUVENLIGI — coercion YOK
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("deger", [True, False, "1", 1.5, 1.0, None, b"1", [1]])
def test_count_gecersiz_TIP_reddedilir(db, deger):
    with pytest.raises(svc.CommandRejected) as exc:
        _kuyrukla(db, count=deger)
    assert exc.value.reason == "invalid_parameter_type"
    assert _komut_sayisi(db) == 0


@pytest.mark.parametrize("alan", ["on_time_ms", "off_time_ms"])
@pytest.mark.parametrize("deger", [True, False, "1", 1.5, 1.0, None])
def test_zamanlama_gecersiz_TIP_reddedilir(db, alan, deger):
    with pytest.raises(svc.CommandRejected) as exc:
        _kuyrukla(db, **{alan: deger})
    assert exc.value.reason == "invalid_parameter_type"
    assert _komut_sayisi(db) == 0


def test_count_True_SESSIZCE_1_olmaz(db):
    """`True == 1` tuzagi: kabul edilseydi cagiranin gondermedigi bir niyet
    uretilirdi."""
    with pytest.raises(svc.CommandRejected):
        _kuyrukla(db, count=True)
    assert _komut_sayisi(db) == 0


# ---------------------------------------------------------------------------
# ARALIK
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("deger", [0, -1, 11, 256, 4294967296])
def test_count_aralik_disi_reddedilir(db, deger):
    with pytest.raises(svc.CommandRejected) as exc:
        _kuyrukla(db, count=deger)
    assert exc.value.reason == "parameter_out_of_range"
    assert _komut_sayisi(db) == 0


def test_count_0_SESSIZCE_1_yapilmaz(db):
    """`count=0` acik bir hata; `or 1` ile duzeltmek niyeti degistirmek olur."""
    with pytest.raises(svc.CommandRejected) as exc:
        _kuyrukla(db, count=0)
    assert exc.value.reason == "parameter_out_of_range"


@pytest.mark.parametrize("alan", ["on_time_ms", "off_time_ms"])
@pytest.mark.parametrize("deger", [-1, 60001, 4294967296])
def test_zamanlama_aralik_disi_reddedilir(db, alan, deger):
    with pytest.raises(svc.CommandRejected) as exc:
        _kuyrukla(db, **{alan: deger})
    assert exc.value.reason == "parameter_out_of_range"
    assert _komut_sayisi(db) == 0


# ---------------------------------------------------------------------------
# DESTEKLENMEYEN KOMBINASYON
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("acik,kapali", [(100, 0), (0, 100), (100, 100)])
def test_latch_ile_zamanlama_REDDEDILIR(db, acik, kapali):
    """SN2 PULSE desteklemez; LATCH'te zamanlama anlamsizdir. Kabul etmek,
    operatore uygulanmayacak bir sure verdigimizi dusundururdu."""
    with pytest.raises(svc.CommandRejected) as exc:
        _kuyrukla(db, on_time_ms=acik, off_time_ms=kapali)
    assert exc.value.reason == "unsupported_parameter_combination"
    assert _komut_sayisi(db) == 0


# ---------------------------------------------------------------------------
# GECERSIZ NIYET -> SATIR YOK -> /pending'e ULASAN KOMUT YOK
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kw",
    [
        {"count": True}, {"count": "1"}, {"count": 0}, {"count": 256},
        {"on_time_ms": -1}, {"on_time_ms": 4294967296}, {"off_time_ms": True},
        {"on_time_ms": 100},
    ],
)
def test_gecersiz_niyet_DeviceCommand_URETMEZ(db, kw):
    with pytest.raises(svc.CommandRejected):
        _kuyrukla(db, **kw)
    db.commit()
    assert _komut_sayisi(db) == 0, "gecersiz niyet satir yazdi"
    bekleyen = db.scalars(
        select(DeviceCommand).where(DeviceCommand.status == "pending")
    ).all()
    assert bekleyen == [], "gecersiz niyet /pending'e ulasabilir durumda"


# ---------------------------------------------------------------------------
# BYPASS AUDIT — P0
# ---------------------------------------------------------------------------
def test_DeviceCommand_TEK_yerde_olusturuluyor():
    """Uretim kodunda `DeviceCommand(...)` yalnizca dogrulayicidan GECEN
    tek noktada kurulmali. Yeni bir olusturma noktasi eklenirse bu test
    duser ve sinir atlanmis olmaz."""
    import pathlib
    import re

    kok = pathlib.Path(__file__).resolve().parents[1] / "app"
    bulunan = []
    for yol in kok.rglob("*.py"):
        metin = yol.read_text(encoding="utf-8")
        for i, satir in enumerate(metin.splitlines(), 1):
            if re.search(r"\bDeviceCommand\s*\(", satir) and "class " not in satir:
                # `as_posix` — Windows'ta ters bolu ile karsilastirma kacardi.
                bulunan.append(f"{yol.relative_to(kok).as_posix()}:{i}")
    dosyalar = {b.rsplit(":", 1)[0] for b in bulunan}
    assert dosyalar == {"services/device_command_service.py"}, (
        f"DeviceCommand beklenmeyen yerde olusturuluyor: {sorted(bulunan)}"
    )


def test_queue_command_dogrulayiciyi_CAGIRIYOR():
    """Cagri kaldirilirsa tum tip/aralik testleri anlamsizlasirdi."""
    import inspect

    kaynak = inspect.getsource(svc.queue_command)
    i_dogrula = kaynak.index("validate_command_intent(")
    i_insert = kaynak.index("DeviceCommand(")
    assert i_dogrula < i_insert, "dogrulama satir yaziminden SONRA cagriliyor"


def test_protokol_yolu_zamanlama_parametresi_KABUL_ETMIYOR():
    """IEC 104 ucunun semasinda count/timing alani YOK — protokol bu
    degerleri etkileyemez, daima backend varsayilanini alir."""
    from app.schemas.internal import InternalCommandRequest

    alanlar = set(InternalCommandRequest.model_fields)
    assert not (alanlar & {"count", "on_time_ms", "off_time_ms", "op_type"}), (
        f"protokol semasi komut parametresi tasiyor: {alanlar}"
    )


def test_ui_semasi_op_type_KABUL_ETMIYOR():
    from app.schemas.device import DeviceCommandRequest

    assert "op_type" not in DeviceCommandRequest.model_fields


# ---------------------------------------------------------------------------
# SEMA <-> SERVIS KAYMASI
# ---------------------------------------------------------------------------
def test_sema_sinirlari_servis_sinirlariyla_AYNI():
    """Iki yerde ayri sinir tutulursa biri sessizce gevser; hangisinin
    gevsedigi de her zaman disa acik olan taraf olur."""
    from app.schemas.device import DeviceCommandRequest

    alanlar = DeviceCommandRequest.model_fields

    def _sinir(ad: str) -> tuple[int, int]:
        alt = ust = None
        for m in alanlar[ad].metadata:
            alt = getattr(m, "ge", alt)
            ust = getattr(m, "le", ust)
        return alt, ust

    assert _sinir("count") == (svc.COUNT_MIN, svc.COUNT_MAX)
    assert _sinir("on_time_ms") == (svc.TIME_MIN, svc.TIME_MAX)
    assert _sinir("off_time_ms") == (svc.TIME_MIN, svc.TIME_MAX)


def test_sema_STRICT_sessiz_donusum_yapmiyor():
    """`"1"` ve `True` sinirda REDDEDILMELI, 1'e cevrilmemeli."""
    from pydantic import ValidationError

    from app.schemas.device import DeviceCommandRequest

    for deger in ("1", True, 1.0):
        with pytest.raises(ValidationError):
            DeviceCommandRequest(command=SLUG, count=deger)


def test_sema_varsayilanlari_uretim_sozlesmesi():
    from app.schemas.device import DeviceCommandRequest

    m = DeviceCommandRequest(command=SLUG)
    assert (m.count, m.on_time_ms, m.off_time_ms) == (1, 0, 0)


# ---------------------------------------------------------------------------
# F6-G CROSS-CHECK — backend'in urettigi gecerli komut gateway'i asmamali
# ---------------------------------------------------------------------------
def test_backend_sinirlari_gateway_fiziksel_sinirinin_ICINDE():
    """Gateway v1.11.1: count uint8 1..255, timing uint32 0..4294967295.
    Backend bu sinirlari ASMAMALI (asarsa gateway reddeder ve komut
    sessizce kaybolur)."""
    assert 1 <= svc.COUNT_MIN <= svc.COUNT_MAX <= 255
    assert 0 <= svc.TIME_MIN <= svc.TIME_MAX <= 4294967295


def test_uretilen_komut_gateway_sozlesmesine_UYGUN(db):
    _kuyrukla(db)
    db.commit()
    row = db.scalars(select(DeviceCommand)).one()

    assert row.op_type in svc.ALLOWED_OP_TYPES
    assert type(row.count) is int and 1 <= row.count <= 255
    for alan in (row.on_time_ms, row.off_time_ms):
        assert type(alan) is int and 0 <= alan <= 4294967295
