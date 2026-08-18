"""Bilinen cihaz sicak yolu refactor'den SONRA da AYNI davraniyor mu?

NEDEN BU TEST VAR
-----------------
Olcum basina is mantigi `_persist_batch` dongusunden `process_valid_telemetry`
fonksiyonuna cikarildi — canli tuketici ile karantina replay'i ayni mantigi
kullansin diye. Bu, sistemin EN SICAK kod yolunda yapilmis bir tasima.

Kaynak kodun SEKLINE bakan bir test (fonksiyon icinde su cagri geciyor mu)
bunu kanitlamaz: cagrilar yerinde durup davranis yine degisebilir. Bu yuzden
test DAVRANISSAL: temsili bir okuma seti gercek PostgreSQL uzerinde islenir
ve DB'ye + donus degerlerine yansiyan HER SEY karsilastirilir.

GOLDEN NEREDEN GELDI
--------------------
`golden/known_device_equivalence.json`, BASELINE commit'in
(e8c2f7e — `process_valid_telemetry` HENUZ YOKKEN) tuketicisi ayni harness
ile kosturularak uretildi. Baseline kodu branch'e kopyalanmadi; uretim
aninda git'ten cikarildi. Yani bu dosya "refactor oncesi sistemin gercekte
ne yaptigi"nin kaydidir.

Yeniden uretmek icin: tests/integration/equivalence_harness.py + baseline
tuketicisi (bkz. modul basligi).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.services import telemetry_consumer
from tests.integration import equivalence_harness as harness
from tests.integration import pg_target

pytestmark = pytest.mark.integration

if not pg_target.pg_url():
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)

GOLDEN = Path(__file__).parent / "golden" / "known_device_equivalence.json"


@pytest.fixture()
def pg(monkeypatch):
    ad = pg_target.yeni_db_adi("equivalence")
    yon = create_engine(pg_target.pg_url(), isolation_level="AUTOCOMMIT")
    with yon.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
        c.execute(text(f'CREATE DATABASE "{ad}" TEMPLATE template0'))
    yon.dispose()
    pg_target.kaydet_olusturuldu(ad)

    eng = create_engine(pg_target.url_for(ad))
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True, expire_on_commit=False)
    monkeypatch.setattr(telemetry_consumer, "SessionLocal", Session)
    harness.setup_schema(Session)

    yield Session

    eng.dispose()
    yon = create_engine(pg_target.pg_url(), isolation_level="AUTOCOMMIT")
    with yon.connect() as c:
        c.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{ad}' AND pid <> pg_backend_pid()"
        ))
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
    yon.dispose()
    pg_target.unut(ad)


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture()
def mevcut(pg):
    return harness.capture(pg, telemetry_consumer._persist_batch)


@pytest.fixture()
def roundtrips(pg, mevcut):  # noqa: ARG001
    """Gidis-donus olcumu, golden URETILIRKENKI ILE AYNI SIRADA alinir.

    Onemli: olcum DB DURUMUNA duyarli. Bos bir semada calisan parti,
    dolu bir semada calisandan farkli sayida ifade uretir (cihaz durumu
    guncellemesi, canli deger upsert'i...). Golden `capture()`ten SONRA
    olculdugu icin burada da ayni sira izlenir; aksi halde test kodun
    degismedigi durumda bile duserdi.
    """
    return harness.capture_roundtrips(pg, telemetry_consumer._persist_batch)


# --------------------------------------------------------------------------
# Kapsam — golden'in gercekten temsili oldugunu dogrula
# --------------------------------------------------------------------------
def test_fixture_seti_yeterince_genis():
    g = _golden()
    assert g["fixture_count"] >= 50, "en az 50 okuma bekleniyordu"
    # Kapsanan durumlar gercekten uretilmis mi?
    kaliteler = {satir[4] for satir in g["telemetry"]}
    assert len(kaliteler) >= 3, f"kalite varyasyonu yetersiz: {kaliteler}"
    sinyaller = {satir[1] for satir in g["telemetry"]}
    assert len(sinyaller) >= 5, f"sinyal varyasyonu yetersiz: {sinyaller}"
    cihazlar = {satir[0] for satir in g["telemetry"]}
    assert len(cihazlar) >= 3, "birden fazla cihaz/model kapsanmali"
    zaman_kaliteleri = {satir[7] for satir in g["telemetry"]}
    assert len(zaman_kaliteleri) >= 2, "cihaz saati degerlendirmesi kapsanmali"
    assert any(v == "bad" for v in g["ack"].values()), "DLQ siniflandirmasi kapsanmali"
    assert g["telemetry_history"], "arsivlenen okuma yok"
    assert len(g["telemetry_history"]) < len(g["telemetry"]), (
        "arsiv politikasi hic elemiyor — deadband/historize kapsanmamis"
    )


# --------------------------------------------------------------------------
# Esdegerlik — alan alan (hangi boyutun kaydigini gostersin diye ayri testler)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "alan",
    [
        "telemetry",
        "telemetry_history",
        "telemetry_latest",
        "processed_messages",
        "devices",
        "ws",
        "outbound",
        "ack",
    ],
)
def test_gozlemlenebilir_cikti_baseline_ile_AYNI(mevcut, alan):
    g = _golden()
    assert mevcut[alan] == g[alan], (
        f"'{alan}' baseline'dan SAPTI — sicak yol refactor'u davranisi degistirdi"
    )


def test_butun_snapshot_ayni(mevcut):
    """Alan bazli testler kacirsa bile tam snapshot yakalasin."""
    g = _golden()
    g.pop("roundtrips", None)
    assert mevcut == g


# --------------------------------------------------------------------------
# Performans/yapisal nobetci — §21
# --------------------------------------------------------------------------
def test_bilinen_cihaz_yolunda_karantina_sorgusu_YOK(roundtrips):
    assert roundtrips["unknown_table"] == 0, (
        "bilinen cihaz partisi karantina tablosuna DOKUNMAMALI"
    )


def test_db_gidis_donus_sayisi_baseline_uzerine_CIKMADI(roundtrips):
    """Olcum basina fazladan sorgu eklenmedigi kanit.

    Zamanlama olculmez (flaky olurdu); SQLAlchemy'nin urettigi ifade sayisi
    sayilir. Golden'daki deger baseline tuketicisiyle AYNI sirada olculmustur.
    """
    beklenen = _golden()["roundtrips"]["total"]
    assert roundtrips["total"] <= beklenen, (
        f"bilinen cihaz partisinde DB gidis-donusu artti: "
        f"baseline={beklenen}, simdi={roundtrips['total']}"
    )
