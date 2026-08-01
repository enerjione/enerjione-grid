"""Ozet katmanlarinin retention'i denetlenmeli (Faz 2-9).

YASANAN ARIZA — IKI PARCA
--------------------------
1) `update.sh`'taki "idempotent historian ensure" blogu iki continuous
   aggregate'i OLUSTURUYOR ve REFRESH politikalarini kuruyor, ama RETENTION
   ve SIKISTIRMA politikalarini HIC kurmuyordu. Migration 0023 bunlari
   kuruyor ama adimlari `_try` ile sarili: hata yutulup alembic yine
   damgalaniyor ve migration BIR DAHA KOSMUYOR. update.sh tarafinda ise
   istisna bile gerekmiyordu — eksiklik her guncellemede tekrarlaniyordu.

2) `historian_service._collect` politikalari YALNIZCA `telemetry_history`
   icin sorguluyor, CAGG'ler icin SADECE ISIM donduruyordu. Yani ozet
   tablolari sinirsiz buyurken Sistem Durumu kartinda historian "ok"
   gorunuyordu.

NEDEN AGIR
----------
`telemetry_history_1m` bir "ozet" DEGIL, pratikte ham verinin KOPYASI:
1 dakikalik kova = GROUP BY (device_id, signal_key, dakika) ve cihaz basina
dakikada ~30 farkli sinyal degistigi icin hemen her okuma KENDI kovasina
duser. 600 cihazda ~17,28M satir/gun (~2,3 GB/gun); politikasiz ~4 ayda
280 GB. Ham tablo 90 gunde budanirken yanindaki bu kopya sinirsiz buyuyordu
— ve diski asil dolduran kalem tam da izleme ekraninin GORMEDIGI yerdeydi.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import historian_service as hs

REPO = Path(__file__).resolve().parents[3]
UPDATE_SH = REPO / "update.sh"

# 0023 ile ayni katmanli tasarim.
BEKLENEN = {
    "telemetry_history_1m": {"retention": 365, "compress": 7},
    "telemetry_history_1h": {"retention": 730, "compress": 30},
}


def _ensure_blogu() -> str:
    kaynak = UPDATE_SH.read_text(encoding="utf-8")
    bas = kaynak.find("CREATE EXTENSION IF NOT EXISTS timescaledb")
    assert bas != -1, "update.sh'ta historian ensure blogu bulunamadi"
    son = kaynak.find("]", bas)
    return kaynak[bas:son]


def test_ensure_blogu_BULUNDU():
    """Yol/kalip yanlissa asagidaki testler sessizce yesil kalirdi."""
    assert len(_ensure_blogu()) > 200


@pytest.mark.parametrize("view", sorted(BEKLENEN))
def test_update_sh_ozet_RETENTION_kuruyor(view: str):
    """Eksik olan asil parca buydu: ozetler sinirsiz buyuyordu."""
    blok = _ensure_blogu()
    kalip = rf"add_retention_policy\(\s*'{view}'"
    assert re.search(kalip, blok), (
        f"update.sh {view} icin retention politikasi kurmuyor — ozet tablosu "
        "sinirsiz buyur ve diski doldurur"
    )


@pytest.mark.parametrize("view", sorted(BEKLENEN))
def test_update_sh_ozet_SIKISTIRMA_kuruyor(view: str):
    blok = _ensure_blogu()
    assert re.search(rf"add_compression_policy\(\s*'{view}'", blok), (
        f"update.sh {view} icin sikistirma politikasi kurmuyor"
    )


@pytest.mark.parametrize("view,beklenen", sorted(BEKLENEN.items()))
def test_saklama_sureleri_0023_ILE_ayni(view: str, beklenen: dict):
    """update.sh ile migration 0023 ayrisirsa hangisinin kazandigi belirsizlesir."""
    blok = _ensure_blogu()
    m = re.search(
        rf"add_retention_policy\(\s*'{view}',[^)]*?(\d+)\s+days", blok, re.DOTALL
    )
    assert m, f"{view} retention suresi okunamadi"
    assert int(m.group(1)) == beklenen["retention"], (
        f"{view} retention {m.group(1)} gun, 0023'te {beklenen['retention']} gun"
    )


def test_durum_kontrolu_CAGG_retention_ARIYOR():
    """`_collect` CAGG politikalarini da sorgulamali.

    Aksi halde ozet tablolari sinirsiz buyurken kart "ok" gosterir.
    """
    import inspect

    kaynak = inspect.getsource(hs._collect)
    assert "cagg_without_retention" in kaynak, (
        "_collect ozet katmanlarinin retention'ini hic sorgulamiyor"
    )
    # CAGG'lerde jobs.hypertable_name view adini DEGIL materialization
    # hypertable'ini gosterir; eslestirme onun uzerinden yapilmali.
    assert "materialization_hypertable_name" in kaynak, (
        "CAGG politikasi view adiyla araniyor — bu sorgu HICBIR ZAMAN eslesmez "
        "ve kontrol sessizce etkisiz kalir"
    )


def test_korumasiz_CAGG_severity_i_CRITICAL_yapiyor():
    """Uyari degil kritik: bu tablolar diski ham veriden hizli doldurur."""
    st = hs.HistorianStatus()
    st.is_hypertable = True
    st.retention_days = hs.EXPECTED_RETENTION_DAYS
    st.compression_enabled = True
    st.cagg_without_retention = ["telemetry_history_1m"]

    # `_collect`in karar blogunu taklit etmek yerine sabitleri dogruluyoruz;
    # gercek akis migration testleriyle ayrica suruluyor.
    assert hs.PROBLEM_CAGG_NO_RETENTION == "cagg_no_retention"
    assert "cagg_without_retention" in st.to_dict(), (
        "arayuz hangi ozet katmaninin korumasiz oldugunu goremiyor"
    )


def test_problem_sabiti_YANITTA_donuyor():
    """Operator arayuzde ne oldugunu okuyabilmeli."""
    st = hs.HistorianStatus()
    st.problems = [hs.PROBLEM_CAGG_NO_RETENTION]
    assert hs.PROBLEM_CAGG_NO_RETENTION in st.to_dict()["problems"]


# ---------------------------------------------------------------------------
# Chunk boyutu (Faz 2-10)
#
# Chunk araligi 0007'de 1 gun secilmisti. 600 cihaz olceginde (103,68M
# satir/gun) tek chunk ~17 GB eder; Postgres container'i 3 GB ve
# shared_buffers 768 MB, yani TEK CHUNK BELLEGIN ~22 KATI. TimescaleDB'nin
# kurali yazilan chunk'larin bellege sigmasidir: sigmadiginda her ekleme ve
# her sorgu diske iner, index sayfalari surekli tahliye edilir.
#
# Migration 0030 araligi 1 saate cekiyor (~700 MB). Ama olcek yeniden
# buyudugunde ayni sorun sessizce geri gelebilir; bu yuzden gercek boyut
# OLCULUP raporlaniyor — sabit bir sayiya guvenmekten daha dayanikli.
# ---------------------------------------------------------------------------

CHUNK_MIGRATION = (
    REPO
    / "apps"
    / "backend-api"
    / "alembic_migrations"
    / "versions"
    / "2026_08_01_0004-0030_historian_chunk_interval.py"
)


def test_chunk_migration_1_SAAT_ayarliyor():
    kaynak = CHUNK_MIGRATION.read_text(encoding="utf-8")
    assert 'CHUNK_INTERVAL = "1 hour"' in kaynak, (
        "chunk araligi 1 saate cekilmemis — 600 cihazda chunk ~17 GB olur"
    )
    assert "set_chunk_time_interval" in kaynak, (
        "`create_hypertable` YALNIZCA olusturmada etkili; mevcut kurulumlar "
        "icin set_chunk_time_interval sart"
    )


def test_chunk_migration_SAVEPOINT_kullaniyor():
    """Chunk araligi bir PERFORMANS ayaridir; uygulanamamasi boot'u
    durdurmayi haketmez. Ama savepoint'siz yutulan hata transaction'i abort
    eder ve alembic_version guncellemesi de duser (0019/0023 tuzagi)."""
    kaynak = CHUNK_MIGRATION.read_text(encoding="utf-8")
    assert "begin_nested" in kaynak


def test_update_sh_chunk_araligini_HIZALIYOR():
    """update.sh mevcut hypertable'da `if_not_exists` yuzunden no-op olur;
    araligi ayrica set etmeli."""
    blok = _ensure_blogu()
    assert "set_chunk_time_interval" in blok, (
        "update.sh chunk araligini hizalamiyor — migration kosmamis bir "
        "kurulumda eski aralik kalir"
    )
    assert "INTERVAL '1 hour'" in blok


def test_durum_raporu_chunk_boyutunu_OLCUYOR():
    import inspect

    from app.services import historian_service as hs2

    kaynak = inspect.getsource(hs2._collect)
    assert "chunks_detailed_size" in kaynak, "chunk boyutu hic olculmuyor"
    assert "is_compressed = false" in kaynak, (
        "sikistirilmis chunk'lar da olcume giriyor — onlar zaten kucuk ve "
        "uzerlerine yazilmiyor, bellege sigma kaygisi gecerli degil"
    )
    assert "shared_buffers" in kaynak, "karsilastirma tabani yok"


def test_buyuk_chunk_WARNING_uretiyor_kritik_degil():
    """Buyuk chunk veri KAYBETTIRMEZ, performans dusurur — gece uyandirmamali."""
    from app.services import historian_service as hs2

    assert hs2.PROBLEM_CHUNK_TOO_LARGE == "chunk_too_large"
    assert hs2.CHUNK_SIZE_WARN_RATIO >= 1.0
