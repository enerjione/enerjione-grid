"""Tek bozuk mesaj TUM telemetri hattini durdurmamali.

YASANAN ZINCIR
--------------
`TelemetryIn.signal_key` ve `quality` alanlarinda `max_length` YOKTU ve
zincirin hicbir yerinde kirpma da yok. Hedef kolonlar `String(120)` ve
`String(50)`. Uzun bir deger pydantic'i geciyor, batch'e giriyor ve ancak
TOPLU INSERT sirasinda `DataError` olarak patliyordu.

Kritik nokta: `_persist_batch`'teki tek yakalayici `except IntegrityError`
idi. `DataError` onun KARDESIDIR (ikisi de `DatabaseError`'dan turer), yani
yakalanmiyordu. Istisna disari cikiyor ve cagirandaki genel
`except Exception` bunu BAGLANTI HATASI sanip `telemetry_consumer_reconnect`
logluyordu.

  > Bir gateway firmware'i 130 karakterlik bir `signal_key` uretir. Batch
  > commit'i patlar, hicbir mesaj ack edilmez. ack_wait(60sn) x
  > max_deliver(10) ile ayni zehirli mesaj 10 kez yeniden dagitilir; her
  > turda ayni batch'teki SAGLAM olcumler de birlikte duser. 10. denemeden
  > sonra NATS hepsini sessizce atar. Operator ekranda "NATS baglantisi
  > koptu" gorur ve sebebi agda arar — gercek sebep TEK BIR UZUN STRING'dir.

DOGRU DAVRANIS
--------------
Sinir sema katmaninda oldugunda mesaj PARSE asamasinda reddedilir ve
`bad_msgs` uzerinden DLQ'ya gider: yalnizca bozuk mesaj karantinaya alinir,
batch'in geri kalani islenmeye devam eder.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.telemetry import Telemetry
from app.models.telemetry_history import TelemetryHistory
from app.schemas.telemetry import TelemetryIn


def _gecerli(**kw) -> dict:
    veri = {
        "device_code": "DEV-001",
        "signal_key": "master.actual_voltage",
        "value": 230.5,
        "quality": "good",
        "source_timestamp": datetime.now(timezone.utc),
    }
    veri.update(kw)
    return veri


def _kolon_uzunlugu(model, ad: str) -> int:
    return model.__table__.columns[ad].type.length


def test_gecerli_okuma_KABUL_ediliyor():
    """Sinirlar normal akisi bozmamali."""
    m = TelemetryIn(**_gecerli())
    assert m.signal_key == "master.actual_voltage"


@pytest.mark.parametrize(
    "alan,kolon_modeli,kolon_adi",
    [
        ("signal_key", Telemetry, "signal_key"),
        ("quality", Telemetry, "quality"),
    ],
)
def test_kolon_genisligini_asan_deger_REDDEDILIYOR(alan, kolon_modeli, kolon_adi):
    """Asil koruma: uzun deger DB'ye kadar GITMEMELI.

    Giderse toplu INSERT `DataError` ile patlar ve batch'in tamami — saglam
    olcumler dahil — 10 kez redeliver edilip sessizce dusurulur.
    """
    sinir = _kolon_uzunlugu(kolon_modeli, kolon_adi)
    with pytest.raises(ValidationError):
        TelemetryIn(**_gecerli(**{alan: "x" * (sinir + 1)}))


@pytest.mark.parametrize(
    "alan,kolon_modeli,kolon_adi",
    [
        ("signal_key", Telemetry, "signal_key"),
        ("quality", Telemetry, "quality"),
    ],
)
def test_tam_sinirdaki_deger_KABUL_ediliyor(alan, kolon_modeli, kolon_adi):
    """Sinir kolon genisligiyle BIREBIR olmali — bir eksik olursa gecerli
    veri reddedilir, bir fazla olursa DB patlar."""
    sinir = _kolon_uzunlugu(kolon_modeli, kolon_adi)
    m = TelemetryIn(**_gecerli(**{alan: "x" * sinir}))
    assert len(getattr(m, alan)) == sinir


def test_sema_sinirlari_KOLONLARLA_ayni():
    """Sema ile model ayrisirsa koruma ya cok dar ya da etkisiz olur."""
    from app.schemas import telemetry as sema

    assert sema._MAX_SIGNAL_KEY == _kolon_uzunlugu(Telemetry, "signal_key")
    assert sema._MAX_QUALITY == _kolon_uzunlugu(Telemetry, "quality")
    # Historian ayni degerleri yaziyor; ikisi ayrisirsa arsiv tarafi patlar.
    assert sema._MAX_SIGNAL_KEY == _kolon_uzunlugu(TelemetryHistory, "signal_key")
    assert sema._MAX_QUALITY == _kolon_uzunlugu(TelemetryHistory, "quality")


def test_consumer_DataError_i_de_yakaliyor():
    """`DataError` IntegrityError'in kardesi — ayri yakalanmali.

    Yakalanmazsa genel `except Exception` onu baglanti hatasi sanar ve
    teshis tamamen yanlis yone gider.
    """
    from app.services import telemetry_consumer as tc

    fn_src = inspect.getsource(tc._persist_batch)
    agac = ast.parse(fn_src.lstrip())

    yakalanan: set[str] = set()
    for h in ast.walk(agac):
        if not isinstance(h, ast.ExceptHandler) or h.type is None:
            continue
        hedefler = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        for t in hedefler:
            ad = getattr(t, "id", None) or getattr(t, "attr", None)
            if ad:
                yakalanan.add(ad)

    assert "DataError" in yakalanan, (
        "_persist_batch DataError'i yakalamiyor — kolon genisligini asan tek "
        "bir deger tum batch'i 10 kez redeliver ettirir ve saglam olcumleri "
        "de goturur"
    )


def test_DataError_batch_i_KARANTINAYA_aliyor():
    """Redeliver ise yaramaz: ayni veri ayni hatayi verir.

    Bozuk batch `bad_msgs` ile donmeli ki DLQ'ya gitsin; aksi halde
    max_deliver tukenene kadar saglam olcumler de birlikte dusurulur.
    """
    from app.services import telemetry_consumer as tc

    fn_src = inspect.getsource(tc._persist_batch)
    agac = ast.parse(fn_src.lstrip())

    for h in ast.walk(agac):
        if not isinstance(h, ast.ExceptHandler) or h.type is None:
            continue
        hedefler = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        adlar = {getattr(t, "id", None) or getattr(t, "attr", None) for t in hedefler}
        if "DataError" not in adlar:
            continue
        donusler = [n for n in ast.walk(h) if isinstance(n, ast.Return)]
        assert donusler, "DataError dalinda return yok"
        # 2. oge bad_msgs olmali ve BOS OLMAMALI (ok_msgs de eklenmeli)
        r = donusler[0].value
        assert isinstance(r, ast.Tuple) and len(r.elts) == 4
        ikinci = ast.dump(r.elts[1])
        assert "bad_msgs" in ikinci and "ok_msgs" in ikinci, (
            "bozuk batch karantinaya alinmiyor — mesajlar redeliver dongusune "
            "girer ve 10. denemede sessizce dusurulur"
        )
        return
    pytest.fail("DataError dali bulunamadi")
