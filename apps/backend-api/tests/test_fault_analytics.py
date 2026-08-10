"""Ariza analizi — sayilar DOGRU ve DURUSTCE sunuluyor mu?

Bu ekran bakim butcesini yonlendirecek. Yanlis bir sayi sessizdir: kimse
patlamis bir sorgu gormez, sadece yanlis hatta ekip gonderilir. Uc risk
sinifi burada kilitleniyor:

  1. KAPSAM SIZINTISI — operator gormemesi gereken hatlarin arizalarini
     toplam sayilar icinde gizlenmis halde gorur.
  2. IYIMSER MTTR — devam eden arizayi "0 surdu" saymak tabloyu oldugundan
     iyi gosterir.
  3. SAHTE KESINLIK — kayitlarin %5'i etiketliyken "en sik sebep agac
     temasi" demek uydurma bir bulgudur.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, Pole, Region
from app.services import fault_analytics_service as analiz


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


class Saha:
    """Test verisi kurucusu — hat/bolge/direk gurultusunu testten uzak tutar."""

    def __init__(self, db) -> None:  # noqa: ANN001
        self.db = db
        self.bolge = Region(name="Merkez", code="MRK")
        db.add(self.bolge)
        db.flush()
        self.dev = Device(
            code="SN2-1", name="F1", ip_address="10.0.0.5", latitude=39.0, longitude=35.0
        )
        db.add(self.dev)
        db.flush()
        self._hatlar: dict[str, Line] = {}
        self._direkler: dict[tuple[int, int], Pole] = {}

    def hat(self, ad: str) -> Line:
        if ad not in self._hatlar:
            h = Line(name=ad, code=ad, region_id=self.bolge.id)
            self.db.add(h)
            self.db.flush()
            self._hatlar[ad] = h
        return self._hatlar[ad]

    def direk(self, hat: Line, seq: int) -> Pole:
        anahtar = (hat.id, seq)
        if anahtar not in self._direkler:
            p = Pole(line_id=hat.id, sequence_no=seq, latitude=39.0 + seq, longitude=35.0)
            self.db.add(p)
            self.db.flush()
            self._direkler[anahtar] = p
        return self._direkler[anahtar]

    def ariza(
        self, hat_adi="HAT-1", *, gun_once=1, sure_saat=None, sebep=None,
        oneri=None, faz=None, aciklik=(1, 2),
    ) -> FaultEvent:
        h = self.hat(hat_adi)
        acilis = datetime.now(timezone.utc) - timedelta(days=gun_once)
        f = FaultEvent(
            line_id=h.id,
            region_id=self.bolge.id,
            last_red_device_id=self.dev.id,
            from_pole_id=self.direk(h, aciklik[0]).id,
            to_pole_id=self.direk(h, aciklik[1]).id,
            from_pole_seq=aciklik[0],
            to_pole_seq=aciklik[1],
            status="closed" if sure_saat is not None else "open",
            opened_at=acilis,
            closed_at=(acilis + timedelta(hours=sure_saat)) if sure_saat is not None else None,
            cause_code=sebep,
            auto_cause_code=oneri,
            phase=faz,
        )
        self.db.add(f)
        self.db.flush()
        return f


# ---- Ozet ------------------------------------------------------------------

def test_bos_veri_COKMEZ(db):
    sonuc = analiz.tum_analiz(db, days=365, visible_line_ids=None)
    assert sonuc["summary"]["total"] == 0
    assert sonuc["summary"]["mttr_hours"] is None
    assert sonuc["top_lines"] == []
    assert sonuc["rule_accuracy"]["accuracy"] is None


def test_MTTR_yalnizca_KAPANMIS_arizalardan(db):
    """Devam edeni '0 surdu' saymak tabloyu oldugundan iyi gosterirdi."""
    s = Saha(db)
    s.ariza(sure_saat=2)
    s.ariza(sure_saat=4)
    s.ariza()  # hala acik — hesaba KATILMAMALI

    ozet = analiz.ozet(db, days=365, visible_line_ids=None)

    assert ozet["total"] == 3
    assert ozet["resolved"] == 2
    assert ozet["open"] == 1
    assert ozet["mttr_hours"] == pytest.approx(3.0, abs=0.05), (
        "acik ariza MTTR'a karisti — ortalama asagi cekildi"
    )


def test_pencere_disi_ariza_SAYILMAZ(db):
    s = Saha(db)
    s.ariza(gun_once=5)
    s.ariza(gun_once=400)
    assert analiz.ozet(db, days=30, visible_line_ids=None)["total"] == 1


# ---- Kapsam ----------------------------------------------------------------

def test_kapsam_DISI_hat_sayilmaz(db):
    """Operator gormemesi gereken hattin arizasini toplamda bile gormemeli."""
    s = Saha(db)
    s.ariza("HAT-1")
    s.ariza("HAT-2")
    gorulen = {s.hat("HAT-1").id}

    ozet = analiz.ozet(db, days=365, visible_line_ids=gorulen)
    hatlar = analiz.hat_siralamasi(db, days=365, visible_line_ids=gorulen)

    assert ozet["total"] == 1
    assert [h["name"] for h in hatlar] == ["HAT-1"]


def test_BOS_kapsam_hicbir_sey_gostermez(db):
    """Bos kume 'tum hatlar' gibi yorumlanirsa tam ters sonuc dogar."""
    s = Saha(db)
    s.ariza("HAT-1")
    assert analiz.ozet(db, days=365, visible_line_ids=set())["total"] == 0


# ---- Siralamalar -----------------------------------------------------------

def test_hat_siralamasi_COKTAN_AZA(db):
    s = Saha(db)
    for _ in range(3):
        s.ariza("HAT-A")
    s.ariza("HAT-B")

    hatlar = analiz.hat_siralamasi(db, days=365, visible_line_ids=None)

    assert [h["name"] for h in hatlar] == ["HAT-A", "HAT-B"]
    assert hatlar[0]["count"] == 3


def test_tekrarlayan_aciklik_TEK_SEFERLIKLERI_gostermez(db):
    """Tek seferlik arizalar listeyi doldurup gercek tekrarlari gizlerdi."""
    s = Saha(db)
    s.ariza("HAT-A", aciklik=(3, 4))
    s.ariza("HAT-A", aciklik=(3, 4))
    s.ariza("HAT-A", aciklik=(7, 8))  # tek seferlik

    tekrar = analiz.tekrarlayan_acikliklar(db, days=365, visible_line_ids=None)

    assert len(tekrar) == 1
    assert tekrar[0]["from_pole_seq"] == 3 and tekrar[0]["to_pole_seq"] == 4
    assert tekrar[0]["count"] == 2


# ---- Veri kalitesi ---------------------------------------------------------

def test_etiketlenme_orani_BILDIRILIYOR(db):
    """Sebep dagilimini yorumlamadan once bakilmasi gereken sayi."""
    s = Saha(db)
    s.ariza(sebep="tree_contact")
    for _ in range(3):
        s.ariza()

    ozet = analiz.ozet(db, days=365, visible_line_ids=None)

    assert ozet["labeled"] == 1
    assert ozet["labeled_ratio"] == 0.25


def test_sebep_dagilimi_ETIKETSIZLERI_dilim_yapmaz(db):
    """Veri eksikligini bir BULGU gibi gostermek yanlis karar urettirir."""
    s = Saha(db)
    s.ariza(sebep="animal")
    s.ariza()

    dagilim = analiz.sebep_dagilimi(db, days=365, visible_line_ids=None)

    assert dagilim == [{"cause_code": "animal", "count": 1}]
    assert all(d["cause_code"] is not None for d in dagilim)


# ---- Kural isabeti ---------------------------------------------------------

def test_isabet_yalnizca_IKISI_DE_dolu_kayitlardan(db):
    """Kuralin oneri uretmedigi kayit 'yanlis' degildir."""
    s = Saha(db)
    s.ariza(sebep="animal", oneri="animal")        # ortusuyor
    s.ariza(sebep="tree_contact", oneri="animal")  # ortusmuyor
    s.ariza(sebep="lightning")                     # oneri yok -> sayilmaz
    s.ariza(oneri="overload")                      # etiket yok -> sayilmaz

    isabet = analiz.kural_isabeti(db, days=365, visible_line_ids=None)

    assert isabet["comparable"] == 2
    assert isabet["agreed"] == 1
    assert isabet["accuracy"] == 0.5


def test_isabet_EN_SIK_YANILMA_ciftlerini_verir(db):
    """Kurali NEREDE duzeltecegini soyleyen sey bu."""
    s = Saha(db)
    for _ in range(3):
        s.ariza(sebep="animal", oneri="tree_contact")
    s.ariza(sebep="lightning", oneri="overload")

    isabet = analiz.kural_isabeti(db, days=365, visible_line_ids=None)

    en_sik = isabet["top_mismatches"][0]
    assert en_sik["suggested"] == "tree_contact"
    assert en_sik["actual"] == "animal"
    assert en_sik["count"] == 3


def test_karsilastirilabilir_kayit_yoksa_isabet_NULL(db):
    """0 kayittan '%0 isabet' uretmek, kurallari haksiz yere kotu gosterirdi."""
    s = Saha(db)
    s.ariza()
    isabet = analiz.kural_isabeti(db, days=365, visible_line_ids=None)
    assert isabet["comparable"] == 0
    assert isabet["accuracy"] is None


# ---- Faz + egilim ----------------------------------------------------------

def test_faz_dagilimi(db):
    s = Saha(db)
    s.ariza(faz="a")
    s.ariza(faz="a")
    s.ariza(faz="abc")
    s.ariza()  # faz yok -> sayilmaz

    dagilim = analiz.faz_dagilimi(db, days=365, visible_line_ids=None)

    assert dagilim[0] == {"phase": "a", "count": 2}
    assert {"phase": "abc", "count": 1} in dagilim


def test_aylik_egilim_KRONOLOJIK(db):
    s = Saha(db)
    s.ariza(gun_once=1)
    s.ariza(gun_once=40)
    egilim = analiz.aylik_egilim(db, days=365, visible_line_ids=None)
    assert len(egilim) >= 1
    aylar = [e["month"] for e in egilim]
    assert aylar == sorted(aylar), "aylar sirali gelmiyor"


# ---- Sozlesme --------------------------------------------------------------

def test_tum_analiz_ekranin_ihtiyaci_olan_HER_SEYI_doner(db):
    Saha(db).ariza(sebep="animal", oneri="animal", faz="b", sure_saat=1)
    sonuc = analiz.tum_analiz(db, days=365, visible_line_ids=None)
    beklenen = {
        "window_days", "summary", "top_lines", "top_regions", "repeat_spans",
        "cause_distribution", "rule_accuracy", "phase_distribution", "monthly_trend",
    }
    assert beklenen <= set(sonuc), f"eksik: {beklenen - set(sonuc)}"
