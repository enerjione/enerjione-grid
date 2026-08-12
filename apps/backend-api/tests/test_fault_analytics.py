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
        oneri=None, faz=None, aciklik=(1, 2), kod=None, tur=None,
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
            zone_code=kod,
            fault_kind=tur,
        )
        self.db.add(f)
        self.db.flush()
        return f


# ---- Aralik risk puani -----------------------------------------------------
#
# Puan bakim onceligi uretecek ve ileride anomali esigi bunun uzerine
# kurulacak. Iki ozelligi test altinda: TAZELIK (eski ariza daha az sey
# soyler) ve MUTLAKLIK (kume icinde normalize edilmez).

def test_puan_TAZELIK_ile_azalir():
    an = datetime.now(timezone.utc)
    taze = analiz.bolge_puani([(an, "permanent")], an)
    eski = analiz.bolge_puani(
        [(an - timedelta(days=analiz.PUAN_YARI_OMUR_GUN), "permanent")], an
    )
    assert taze == 50.0, "tek taze kalici ariza = 50 (dogrusal olmayan doyum egrisi)"
    assert 0 < eski < taze, "yari omurde puan yariya yakin dusmeli"


def test_puan_KALICI_ile_GECICI_arizayi_ayirir():
    an = datetime.now(timezone.utc)
    kalici = analiz.bolge_puani([(an, "permanent")], an)
    gecici = analiz.bolge_puani([(an, "transient")], an)
    assert gecici < kalici, "kendiliginden duzelen ariza ekip cikartmaz"


def test_puan_bir_ARALIK_iyilesince_digerini_YUKSELTMEZ(db):
    """Mutlak puan: normalize edilseydi esik/anomali kurali anlamsizlasirdi."""
    s = Saha(db)
    s.ariza(kod="L1/D1>D2", gun_once=1, tur="permanent")
    s.ariza(kod="L1/D2>D3", gun_once=2, tur="permanent")
    once = {r["zone_code"]: r["score"] for r in analiz.bolge_puanlari(
        db, days=365, visible_line_ids=None
    )}

    s.ariza(kod="L1/D2>D3", gun_once=1, tur="permanent")
    sonra = {r["zone_code"]: r["score"] for r in analiz.bolge_puanlari(
        db, days=365, visible_line_ids=None
    )}

    assert sonra["L1/D2>D3"] > once["L1/D2>D3"]
    assert sonra["L1/D1>D2"] == once["L1/D1>D2"], (
        "baska araligin arizasi bu araligin puanini degistiremez"
    )


def test_siralama_HAT_degil_ARALIK_bazli(db):
    """Ayni hattin iki ucu ayri sorunlardir; ekip hatta degil ARALIGA gider."""
    s = Saha(db)
    for _ in range(3):
        s.ariza(kod="L1/D5>D6", gun_once=3, tur="permanent")
    s.ariza(kod="L1/D1>D2", gun_once=200, tur="transient")

    siralama = analiz.bolge_puanlari(db, days=365, visible_line_ids=None)

    assert [r["zone_code"] for r in siralama] == ["L1/D5>D6", "L1/D1>D2"]
    assert siralama[0]["count"] == 3
    assert siralama[0]["permanent_count"] == 3
    assert siralama[0]["score"] > siralama[1]["score"]


def test_kodsuz_ESKI_kayitlar_siralamaya_girmez(db):
    """Kod olmadan hangi aralik oldugu bilinmiyor — uydurma kova acilmaz."""
    s = Saha(db)
    s.ariza(kod=None)
    assert analiz.bolge_puanlari(db, days=365, visible_line_ids=None) == []


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


# ===========================================================================
# SISTEM SAGLIGI — alarm sikligi ve haberlesme kararliligi
# ===========================================================================
#
# Bu sayilara bakip SAHAYA TEKNISYEN gonderilecek. "Su cihaz gunde 40 kez
# kopuyor" satirinin yanlis olmasi, bosa yol demektir. Ayirt edici alan
# (`kind`) tam bu yuzden eklendi; basliga/`signal_key`e bakan bir sezgi
# sessizce yanlis kovaya atardi.

from app.models.alarm import AlarmEvent  # noqa: E402


def _alarm(db, dev_id: int, *, baslik="Asiri akim", kind="rule",
           gun_once=1, onayli=False, seviye="warning") -> AlarmEvent:
    a = AlarmEvent(
        device_id=dev_id,
        level=seviye,
        title=baslik,
        description="",
        created_at=datetime.now(timezone.utc) - timedelta(days=gun_once),
        acknowledged=onayli,
        kind=kind,
    )
    db.add(a)
    db.flush()
    return a


def test_alarm_sikligi_KURAL_bazinda(db):
    s = Saha(db)
    for _ in range(3):
        _alarm(db, s.dev.id, baslik="Asiri akim")
    _alarm(db, s.dev.id, baslik="Batarya dusuk")

    kurallar = analiz.alarm_sikligi(db, days=365, visible_device_ids=None)

    assert kurallar[0]["rule_name"] == "Asiri akim"
    assert kurallar[0]["count"] == 3
    assert kurallar[1]["count"] == 1


def test_haberlesme_alarmlari_KURAL_siralamasina_karismaz(db):
    """Aksi halde 'en sik alarm' listesi cihaz kopmalariyla dolardi."""
    s = Saha(db)
    _alarm(db, s.dev.id, baslik="Asiri akim")
    for _ in range(5):
        _alarm(db, s.dev.id, baslik="F1 haberleşme alarmı", kind="comm_loss")

    kurallar = analiz.alarm_sikligi(db, days=365, visible_device_ids=None)

    assert [k["rule_name"] for k in kurallar] == ["Asiri akim"]


def test_onay_orani_bildiriliyor(db):
    """Cok tetikleyip HIC onaylanmayan kural, gormezden gelinen kuraldir."""
    s = Saha(db)
    _alarm(db, s.dev.id, onayli=True)
    _alarm(db, s.dev.id, onayli=False)
    _alarm(db, s.dev.id, onayli=False)

    kural = analiz.alarm_sikligi(db, days=365, visible_device_ids=None)[0]

    assert kural["count"] == 3
    assert kural["acknowledged"] == 1


def test_haberlesme_kararsizligi_KESINTI_sayar(db):
    s = Saha(db)
    for _ in range(4):
        _alarm(db, s.dev.id, kind="comm_loss")
    _alarm(db, s.dev.id, kind="rule")  # kural alarmi -> sayilmamali

    kopanlar = analiz.haberlesme_kararsizligi(db, days=365, visible_device_ids=None)

    assert len(kopanlar) == 1
    assert kopanlar[0]["code"] == "SN2-1"
    assert kopanlar[0]["outages"] == 4


def test_SINIFLANMAMIS_eski_kayit_haberlesmeye_SAYILMAZ(db):
    """`kind` NULL = eski kayit. Onlari 'kopma' saymak, olcumu uydurmakti."""
    s = Saha(db)
    _alarm(db, s.dev.id, kind=None, baslik="F1 haberleşme alarmı")

    kopanlar = analiz.haberlesme_kararsizligi(db, days=365, visible_device_ids=None)

    assert kopanlar == []


def test_siniflanmamis_sayisi_GORUNUR(db):
    """Haberlesme sayisinin neden dusuk oldugunu aciklayan tek sey bu."""
    s = Saha(db)
    _alarm(db, s.dev.id, kind=None)
    _alarm(db, s.dev.id, kind="comm_loss")

    ozet = analiz.alarm_ozeti(db, days=365, visible_device_ids=None)

    assert ozet["total"] == 2
    assert ozet["unclassified"] == 1
    assert ozet["comm_outages"] == 1


def test_alarm_kapsami_uygulanir(db):
    s = Saha(db)
    baska = Device(
        code="SN2-2", name="F2", ip_address="10.0.0.6", latitude=39.5, longitude=35.5
    )
    db.add(baska)
    db.flush()
    _alarm(db, s.dev.id)
    _alarm(db, baska.id)

    ozet = analiz.alarm_ozeti(db, days=365, visible_device_ids={s.dev.id})

    assert ozet["total"] == 1


def test_bos_alarm_kapsami_hicbir_sey_gostermez(db):
    s = Saha(db)
    _alarm(db, s.dev.id)
    assert analiz.alarm_ozeti(db, days=365, visible_device_ids=set())["total"] == 0


def test_pencere_disi_alarm_sayilmaz(db):
    s = Saha(db)
    _alarm(db, s.dev.id, gun_once=5)
    _alarm(db, s.dev.id, gun_once=400)
    assert analiz.alarm_ozeti(db, days=30, visible_device_ids=None)["total"] == 1


def test_sistem_sagligi_sozlesmesi(db):
    s = Saha(db)
    _alarm(db, s.dev.id)
    sonuc = analiz.sistem_sagligi(db, days=365, visible_device_ids=None)
    assert {"window_days", "alarm_summary", "alarm_calendar"} <= set(sonuc)


def test_bos_veride_sistem_sagligi_COKMEZ(db):
    sonuc = analiz.sistem_sagligi(db, days=365, visible_device_ids=None)
    assert sonuc["alarm_summary"]["total"] == 0
    assert sonuc["alarm_calendar"]["total"] == 0
    assert sonuc["alarm_calendar"]["max"] == 0


# ---- Alarm takvimi (GitHub katki gorunumu) ---------------------------------
#
# Takvimin tek isi "saha ne zaman gurultuluydu"yu gostermek. O yuzden burada
# olculen sey SAYILAR degil TAKVIMIN SUREKLILIGI: veri olmayan gun de kare
# acmali, yoksa iki gunluk veri sonsuza kadar iki sutunluk bir grafik uretir
# ve ekranda sahanin ritmi degil veri tabaninin sekli gorunur.


def test_takvim_BOS_gunleri_de_kare_acar(db):
    s = Saha(db)
    _alarm(db, s.dev.id)
    takvim = analiz.alarm_takvimi(db, days=30, visible_device_ids=None)
    assert len(takvim["days"]) == 30, "bos gunler atlandi — takvim kisaldi"
    assert takvim["days"][-1]["date"] == takvim["end"]
    assert takvim["days"][0]["date"] == takvim["start"]


def test_takvim_gunleri_KRONOLOJIK_ve_KESINTISIZ(db):
    from datetime import date

    takvim = analiz.alarm_takvimi(db, days=14, visible_device_ids=None)
    gunler = [date.fromisoformat(g["date"]) for g in takvim["days"]]
    assert gunler == sorted(gunler), "takvim kronolojik degil"
    farklar = {(b - a).days for a, b in zip(gunler, gunler[1:])}
    assert farklar == {1}, f"takvimde delik var: {sorted(farklar)}"


def test_takvim_alarmi_DOGRU_gune_yazar(db):
    s = Saha(db)
    _alarm(db, s.dev.id, gun_once=0)  # bugun
    takvim = analiz.alarm_takvimi(db, days=30, visible_device_ids=None)
    bugun = takvim["days"][-1]
    assert bugun["count"] == 1, "bugunku alarm son kareye dusmedi"
    assert takvim["max"] == 1
    assert takvim["total"] == 1


def test_takvim_pencere_TAVANINDA_kesilir(db):
    """Uc yillik pencere 1095 kare demek; okunabilirligin siniri asilir."""
    takvim = analiz.alarm_takvimi(db, days=1095, visible_device_ids=None)
    assert len(takvim["days"]) == analiz.CALENDAR_MAX_DAYS
    assert takvim["truncated"] is True, "kesilme SESSIZ kaldi"


def test_takvim_kapsam_DISI_cihazi_saymaz(db):
    s = Saha(db)
    _alarm(db, s.dev.id)
    takvim = analiz.alarm_takvimi(db, days=30, visible_device_ids=set())
    assert takvim["total"] == 0, "bos kapsam alarm sizdirdi"


# ---- Sankey: Bolge -> Hat -> Faz akisi -------------------------------------

def test_sankey_UC_KADEME_akis_uretir(db):
    s = Saha(db)
    s.ariza("HAT-A", faz="a")
    s.ariza("HAT-A", faz="a")
    s.ariza("HAT-B", faz="c")

    akis = analiz.sankey_akisi(db, days=365, visible_line_ids=None)
    adlar = {n["name"] for n in akis["nodes"]}

    assert "B:Merkez" in adlar and "H:HAT-A" in adlar and "F:A" in adlar
    baglar = {(l["source"], l["target"]): l["value"] for l in akis["links"]}
    assert baglar[("B:Merkez", "H:HAT-A")] == 2
    assert baglar[("H:HAT-A", "F:A")] == 2
    assert baglar[("H:HAT-B", "F:C")] == 1


def test_sankey_bolge_bagi_hatlari_TOPLAR(db):
    """Bir bolgeden cikan akis, altindaki hatlarin toplamina esit olmali."""
    s = Saha(db)
    s.ariza("HAT-A", faz="a")
    s.ariza("HAT-B", faz="b")

    akis = analiz.sankey_akisi(db, days=365, visible_line_ids=None)
    bolgeden = sum(l["value"] for l in akis["links"] if l["source"] == "B:Merkez")
    faza = sum(l["value"] for l in akis["links"] if l["target"].startswith("F:"))

    assert bolgeden == faza == 2


def test_sankey_FAZSIZ_kayit_akisa_girmez(db):
    """'Bilinmiyor' dugumu eklemek, olcum eksikligini akisin bir kolu gibi
    gosterirdi; Sankey'de kalinlik 'gercekten oraya giden miktar' demektir."""
    s = Saha(db)
    s.ariza("HAT-A", faz="a")
    s.ariza("HAT-A")  # faz yok

    akis = analiz.sankey_akisi(db, days=365, visible_line_ids=None)

    assert sum(l["value"] for l in akis["links"] if l["target"].startswith("F:")) == 1
    assert not any("bilinmiyor" in n["name"].lower() for n in akis["nodes"])


def test_sankey_dugum_adlari_BENZERSIZ(db):
    """echarts dugumleri ADA gore eslestirir; ayni ad iki kademede olursa
    akis yanlis dugume baglanir."""
    s = Saha(db)
    s.ariza("HAT-A", faz="a")
    akis = analiz.sankey_akisi(db, days=365, visible_line_ids=None)
    adlar = [n["name"] for n in akis["nodes"]]
    assert len(adlar) == len(set(adlar))


def test_sankey_kapsam_disini_gostermez(db):
    s = Saha(db)
    s.ariza("HAT-A", faz="a")
    s.ariza("HAT-B", faz="b")
    akis = analiz.sankey_akisi(db, days=365, visible_line_ids={s.hat("HAT-A").id})
    assert not any("HAT-B" in n["name"] for n in akis["nodes"])


def test_bos_veride_sankey_COKMEZ(db):
    akis = analiz.sankey_akisi(db, days=365, visible_line_ids=None)
    assert akis == {"nodes": [], "links": []}
