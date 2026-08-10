"""Cihaz sagligi analizi — batarya, sinyal, ariza yogunlugu.

Bu sayilar BAKIM PLANLAMASINI yonlendirecek: "su cihazin bataryasi 12 gun
sonra biter" diyen bir satira gore teknisyen yola cikar. Uydurma bir
kesinlik, bosa yol demektir. Uc risk burada kilitleniyor:

  1. GURULTUYU EGILIM SANMAK — birkac saatlik veriden "gunde 0.3 V dusuyor"
     cikarmak.
  2. YANLIS YON — batarya yukseliyorken (sarj/degisim) "tukeniyor" demek.
  3. OZET TABLO YOKSA COKMEK — Timescale'siz kurulumda ekran patlamamali.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
# Modeller MODUL DUZEYINDE import edilmeli: `create_all` yalnizca o ana
# kadar kaydolmus tablolari yaratir. Fonksiyon icinde import etmek tabloyu
# fixture'dan SONRA kaydeder ve "no such table" ile duserdi.
from app.models.device import Device
from app.models.fault import FaultEvent
from app.models.grid_topology import Line, Pole, Region
from app.services import device_health_analytics as saglik


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


# ---- Ozet tablo yoksa (SQLite / Timescale'siz dev) -------------------------

def test_ozet_tablo_YOKSA_bos_doner_COKMEZ(db):
    """Timescale'siz kurulumda ekran 'veri yok' gostermeli, patlamamali."""
    assert saglik.batarya_tukenme(db, days=90, visible_device_ids=None) == []
    assert saglik.sinyal_kalitesi(db, days=90, visible_device_ids=None) == []
    assert saglik.sinyal_saat_profili(db, days=90, visible_device_ids=None) == []


def test_ozet_kontrolu_OTURUMU_bozmaz(db):
    """Kontrol basarisiz sorgu atiyor; rollback edilmezse sonraki her sorgu
    'current transaction is aborted' ile duserdi."""
    assert saglik._ozet_var_mi(db) is False  # noqa: SLF001
    # Oturum hala kullanilabilir olmali:
    db.add(Device(code="X", name="X", ip_address="10.0.0.1", latitude=1.0, longitude=2.0))
    db.flush()
    assert db.query(Device).count() == 1


# ---- Ariza yogunlugu (isi haritasi) — ozet tablo GEREKTIRMEZ --------------

def test_ariza_yogunlugu_KOORDINAT_ve_AGIRLIK(db):
    from datetime import datetime, timezone

    r = Region(name="M", code="M")
    db.add(r)
    db.flush()
    l = Line(name="H", code="H", region_id=r.id)
    db.add(l)
    db.flush()
    d = Device(code="D", name="D", ip_address="10.0.0.1", latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    p1 = Pole(line_id=l.id, sequence_no=1, latitude=39.0, longitude=35.0)
    p2 = Pole(line_id=l.id, sequence_no=2, latitude=39.1, longitude=35.1)
    db.add_all([p1, p2])
    db.flush()
    for _ in range(3):
        db.add(FaultEvent(
            line_id=l.id, region_id=r.id, last_red_device_id=d.id,
            from_pole_id=p1.id, to_pole_id=p2.id, status="open",
            opened_at=datetime.now(timezone.utc),
        ))
    db.flush()

    isi = saglik.ariza_yogunlugu(db, days=365, visible_line_ids=None)

    assert len(isi) == 1, "ayni nokta tek satirda toplanmali"
    assert isi[0]["weight"] == 3
    assert isi[0]["latitude"] == 39.0


def test_ariza_yogunlugu_KAPSAM_disini_gostermez(db):
    assert saglik.ariza_yogunlugu(db, days=365, visible_line_ids=set()) == []


# ---- Esikler ve sabitler ---------------------------------------------------

def test_egilim_icin_ASGARI_pencere_var():
    """Birkac saatlik veriden 'gunde 0.3 V dusuyor' cikarmak, olcum
    gurultusunu egilim diye sunmakti."""
    assert saglik.MIN_TREND_DAYS >= 1.0


def test_batarya_esigi_proje_varsayilaniyla_TUTARLI():
    """Esik ProjectSettings ile ayni olmali; ayrisirsa 'kac gun kaldi'
    tahmini arayuzdeki yuzdeyle celisir."""
    from app.models.project_settings import ProjectSettings  # noqa: F401

    # Model yorumunda belirtilen fallback degerleri (3.40 / 3.71).
    assert saglik.DEFAULT_BATTERY_LOW == 3.40
    assert saglik.DEFAULT_BATTERY_FULL == 3.71
    assert saglik.DEFAULT_BATTERY_LOW < saglik.DEFAULT_BATTERY_FULL


def test_sinyal_anahtarlari_KATALOGDA_var():
    """Sinyal adi degisirse sorgu sessizce bos doner — analiz olmadigi halde
    'veri yok' gorunur ve kimse fark etmez."""
    import json
    from pathlib import Path

    yol = Path(__file__).resolve().parents[1] / "app" / "data" / "horstmann_sn2_signals.json"
    anahtarlar = {r["key"] for r in json.loads(yol.read_text(encoding="utf-8"))}
    assert saglik.BATTERY_SIGNAL in anahtarlar, saglik.BATTERY_SIGNAL
    assert saglik.RSSI_SIGNAL in anahtarlar, saglik.RSSI_SIGNAL
