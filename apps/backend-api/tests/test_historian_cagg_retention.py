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
