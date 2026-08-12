"""Cihaz x zaman alarm yogunlugu.

Bu matris "hangi cihaz gurultulu" sorusuna liste yerine DESEN ile cevap
verir. Yanlis olcekli ya da eksik oldugunu SOYLEMEYEN bir matris, listeden
daha tehlikelidir: gorsel oldugu icin daha ikna edici gorunur.

Kilitlenen sozlesmeler:
  * satirlar en gurultuluden aza; kesilen kisim ACIKCA bildirilir,
  * kova cozunurlugu pencereye gore (kisa -> saat, uzun -> gun),
  * hucre indeksleri satir/sutun dizilerinin ICINDE kalir,
  * alarm uretmemis cihaz satir ACMAZ (bos satir bilgi tasimaz).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.services.fault_analytics_service import (
    HEATMAP_MAX_COLS,
    alarm_isi_haritasi,
)

SIMDI = datetime.now(timezone.utc)


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


def _cihaz(db, kod: str) -> Device:
    d = Device(code=kod, name=f"Cihaz {kod}", ip_address="10.0.0.1",
               latitude=39.0, longitude=35.0)
    db.add(d)
    db.flush()
    return d


def _alarm(db, dev: Device, *, gun_once: float = 1, kind: str = "rule") -> None:
    db.add(
        AlarmEvent(
            device_id=dev.id,
            title="Asiri akim",
            description="Esik asildi",
            level="critical",
            signal_key="oc" if kind == "rule" else None,
            kind=kind,
            created_at=SIMDI - timedelta(days=gun_once),
        )
    )
    db.flush()


def test_veri_yokken_BOS_yapi_doner_patlamaz(db):
    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None)
    assert h["devices"] == [] and h["cells"] == []
    assert h["max"] == 0
    assert h["truncated"] is False
    assert h["device_total"] == 0


def test_satirlar_EN_GURULTULUDEN_aza_sirali(db):
    az = _cihaz(db, "AZ")
    cok = _cihaz(db, "COK")
    _alarm(db, az, gun_once=1)
    for _ in range(5):
        _alarm(db, cok, gun_once=1)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None)
    assert [d["code"] for d in h["devices"]] == ["COK", "AZ"]
    assert [d["total"] for d in h["devices"]] == [5, 1]


def test_alarm_uretmemis_cihaz_SATIR_ACMAZ(db):
    sessiz = _cihaz(db, "SESSIZ")
    gurultulu = _cihaz(db, "GURULTU")
    _alarm(db, gurultulu, gun_once=1)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None)
    # Bos bir satir "bu cihaz izleniyor ama sorunsuz" demez; sadece yer
    # kaplar ve 600 cihazli sahada matrisi okunmaz yapar.
    assert [d["code"] for d in h["devices"]] == ["GURULTU"]
    assert sessiz.id not in {d["device_id"] for d in h["devices"]}


def test_KESILEN_satirlar_sessizce_atilmaz(db):
    for i in range(6):
        d = _cihaz(db, f"C{i}")
        for _ in range(i + 1):
            _alarm(db, d, gun_once=1)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None, limit=3)
    assert len(h["devices"]) == 3
    # "Listede yok" ile "alarm uretmemis" karistirilmasin diye ikisi de
    # yanitta: kac tanesi cizildi ve kac tanesi VAR.
    assert h["truncated"] is True
    assert h["device_total"] == 6


def test_hepsi_siga_gordugunde_truncated_YANLIS_pozitif_vermez(db):
    d = _cihaz(db, "TEK")
    _alarm(db, d, gun_once=1)
    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None, limit=25)
    assert h["truncated"] is False
    assert h["device_total"] == 1


def test_kova_HER_ZAMAN_gunluk(db):
    """Kaynak gunluk sayac oldugu icin kova da gunluk.

    Onceden 2 gunden kisa pencerede saatlik kovaya duselirdi. Iki sebeple
    kaldirildi: matris zaten 30 gune SABIT (bkz. HEATMAP_WINDOW_DAYS), yani
    o dal pratikte hic calismiyordu; ve grafik artik alarm satirlarini degil
    gunluk tetiklenme sayacini okuyor — sayacin tanesi gun.
    """
    d = _cihaz(db, "A")
    _alarm(db, d, gun_once=0.1)
    _alarm(db, d, gun_once=0.5)
    h = alarm_isi_haritasi(db, days=2, visible_device_ids=None)
    assert h["bucket"] == "day"


def test_uzun_pencere_GUNLUK_kova(db):
    d = _cihaz(db, "A")
    _alarm(db, d, gun_once=1)
    h = alarm_isi_haritasi(db, days=90, visible_device_ids=None)
    assert h["bucket"] == "day"


def test_ayni_gunun_alarmlari_TEK_hucrede_toplanir(db):
    d = _cihaz(db, "A")
    for _ in range(4):
        _alarm(db, d, gun_once=3)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None)
    assert len(h["cells"]) == 1
    assert h["cells"][0][2] == 4
    assert h["max"] == 4


def test_hucre_indeksleri_dizilerin_ICINDE(db):
    # Sinir disi bir indeks echarts'ta sessizce cizilmez: hucre kaybolur ve
    # matris eksik veriyi "alarm yok" gibi gosterir.
    for i in range(3):
        d = _cihaz(db, f"C{i}")
        for g in (1, 4, 9):
            _alarm(db, d, gun_once=g)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None)
    assert h["cells"]
    for sutun, satir, adet in h["cells"]:
        assert 0 <= sutun < len(h["buckets"]), f"sutun tasiyor: {sutun}"
        assert 0 <= satir < len(h["devices"]), f"satir tasiyor: {satir}"
        assert adet > 0, "sifirli hucre gonderilmemeli"


def test_BOS_kovalar_sutun_acmaz(db):
    # 365 gunluk pencerede sahanin sessiz gectigi aylar icin sutun acmak
    # matrisi okunmaz genislige tasirdi.
    d = _cihaz(db, "A")
    _alarm(db, d, gun_once=1)
    _alarm(db, d, gun_once=200)

    h = alarm_isi_haritasi(db, days=365, visible_device_ids=None)
    assert len(h["buckets"]) == 2


def test_sutun_TAVANI_asilmaz(db):
    d = _cihaz(db, "A")
    for g in range(HEATMAP_MAX_COLS + 40):
        _alarm(db, d, gun_once=g + 1)

    h = alarm_isi_haritasi(db, days=1095, visible_device_ids=None)
    assert len(h["buckets"]) == HEATMAP_MAX_COLS
    # Tavan EN YENI kovalari tutar: eski donem degil, guncel davranis onemli.
    assert h["buckets"] == sorted(h["buckets"])
    for sutun, _, _ in h["cells"]:
        assert 0 <= sutun < HEATMAP_MAX_COLS


def test_haberlesme_alarmlari_DAHIL_edilir(db):
    """Kasitli: ust seritteki "Toplam alarm" olcusu de haberlesme
    alarmlarini sayiyor. Matris onlari disarida biraksaydi ayni ekrandaki
    iki sayi birbirini tutmazdi ve hangisinin dogru oldugu belirsiz olurdu.
    "En cok tetikleyen KURAL" listesi ayri bir soru sordugu icin orada
    disarida tutuluyor."""
    d = _cihaz(db, "A")
    _alarm(db, d, gun_once=1, kind="rule")
    _alarm(db, d, gun_once=1, kind="comm_loss")

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None)
    assert h["devices"][0]["total"] == 2


def test_kapsam_disi_cihaz_matrise_GIRMEZ(db):
    """Operator yalnizca sorumluluk alanini gorur; matris "tum saha" gibi
    durdugu icin kapsami unutmak gormemesi gereken cihazlari sizdirirdi."""
    gorunur = _cihaz(db, "GORUNUR")
    gizli = _cihaz(db, "GIZLI")
    _alarm(db, gorunur, gun_once=1)
    for _ in range(9):
        _alarm(db, gizli, gun_once=1)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids={gorunur.id})
    assert [d["code"] for d in h["devices"]] == ["GORUNUR"]
    assert h["device_total"] == 1, "kapsam disi cihaz toplamda sayiliyor"


def test_bos_kapsam_HICBIR_sey_dondurmez(db):
    """`set()` = "hicbir hat atanmamis operator". `None` (kisit yok) ile
    karistirilirsa tum sahayi gorurdu."""
    d = _cihaz(db, "A")
    _alarm(db, d, gun_once=1)
    h = alarm_isi_haritasi(db, days=30, visible_device_ids=set())
    assert h["devices"] == [] and h["cells"] == []


# ---- SUREKLI KIP: matris hep son 30 gun ------------------------------------
#
# Ekrandaki matris sayfanin pencere secimini IZLEMEZ. Sebep, kullanicinin
# ilk ekran goruntusunde gordugu sey: sutunlar yalnizca VERI OLAN gunlerde
# aciliyordu ve iki gunluk veri sonsuza kadar iki sutunluk bir grafik
# uretiyordu — ekranda sahanin ritmi degil veri tabaninin sekli goruluyordu.


def test_surekli_kip_BOS_gunlere_de_sutun_acar(db):
    d = _cihaz(db, "A")
    _alarm(db, d, gun_once=1)
    _alarm(db, d, gun_once=20)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None, surekli=True)
    assert len(h["buckets"]) == 30, "sessiz gunler atlandi — matris kisaldi"


def test_surekli_kip_sutunlari_KESINTISIZ(db):
    from datetime import date

    d = _cihaz(db, "A")
    _alarm(db, d, gun_once=1)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None, surekli=True)
    gunler = [date.fromisoformat(b) for b in h["buckets"]]
    assert gunler == sorted(gunler)
    assert {(b - a).days for a, b in zip(gunler, gunler[1:])} == {1}


def test_surekli_kip_VERI_YOKKEN_de_takvim_uretir(db):
    """Hic alarm yokken bile 30 sutun donmeli; arayuz bos matris yerine
    'bu donemde alarm yok' desenini cizebilsin."""
    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None, surekli=True)
    assert len(h["buckets"]) == 30
    assert h["devices"] == [] and h["cells"] == []


def test_surekli_kip_hucre_sutunlari_DIZININ_icinde(db):
    d = _cihaz(db, "A")
    for g in (0, 3, 12, 29):
        _alarm(db, d, gun_once=g)

    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None, surekli=True)
    assert h["cells"], "alarm eklendi ama hucre uretilmedi"
    for sutun, satir, adet in h["cells"]:
        assert 0 <= sutun < len(h["buckets"]), f"sutun tasiyor: {sutun}"
        assert 0 <= satir < len(h["devices"])
        assert adet > 0


def test_matris_KENDI_penceresini_bildirir(db):
    """Sayfa 365 gun secmis olsa bile matris 30 gun gosteriyor. Bunu
    SOYLEMEZSE kullanici pencereyi degistirip matris neden ayni kaldi diye
    dusunur."""
    h = alarm_isi_haritasi(db, days=30, visible_device_ids=None, surekli=True)
    assert h["window_days"] == 30


def test_sistem_sagligi_matrisi_PENCEREDEN_bagimsiz(db):
    from app.services.fault_analytics_service import (
        HEATMAP_WINDOW_DAYS,
        sistem_sagligi,
    )

    d = _cihaz(db, "A")
    _alarm(db, d, gun_once=1)

    s = sistem_sagligi(db, days=365, visible_device_ids=None)
    assert s["alarm_heatmap"]["window_days"] == HEATMAP_WINDOW_DAYS
    assert len(s["alarm_heatmap"]["buckets"]) == HEATMAP_WINDOW_DAYS
    # Takvim ise pencereyi IZLER — iki grafik ayri sorular soruyor.
    assert len(s["alarm_calendar"]["days"]) == 365
