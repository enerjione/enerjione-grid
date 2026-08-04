"""Temizlik yuku VERI YOLUNU yavaslatmamali — dagitim davranisi.

NEDEN BU DOSYA VAR (saha olcumu, 2026-08-04)
--------------------------------------------
v2.39.0 outbox temizligini `outbox_flush_worker`dan `RetentionWorker`a tasidi
ve tavani uretimin altindan (1.000 satir/sn) ustune cikardi
(20.000 x 50 / 60sn = ~16.600 satir/sn). Bu DOGRU bir duzeltmeydi: tablo
artik kararli duruma geliyor. Ama iki ayrinti temizligi, veri yolunun
yanindaki en agir DB tuketicisi haline getirdi:

  1. `_run` icindeki `first_iteration` bayragi ilk dongude periyot
     kontrolunu ATLIYOR ve BES purge'u ayni anda, tam tavanla kosturuyordu.
     Taze deployda bu, en kotu ani secmek demek: tuketici SOGUK, NATS
     birikimi eritiliyor ve ayni saniyelerde 1.000.000 satirlik DELETE +
     ~770 MB WAL Postgres'e biniyor. Operatorun olcumu (islenen 116.500 /
     1.380 msj/sn = ~84. saniye) tam da bu gecis penceresine denk geliyordu.
  2. Yakalama turunda partiler ARKA ARKAYA, hic ara vermeden kosuyordu.

Buradaki testler duzeltmenin DAVRANISINI kilitler. Hicbiri saklama
penceresine, tur tavanina, dead-letter yoluna ya da "published=False'a
dokunma" kuralina bakmaz — onlar `test_retention_worker.py`de ve
DEGISMEDILER. Burada tek soru su: ayni is ZAMANA YAYILIYOR mu?

MUTASYONLA DOGRULANDI: her test, korudugu davranis koddan geri alindiginda
(nefes kaldirilinca, acilis dagitimi `first_iteration`a dondurulunce,
kismi partiden sonra da beklenince, disk guard'in muafiyeti silinince)
KIRMIZI donuyor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.models.outbox_event import OutboxEvent
from app.models.processed_message import ProcessedMessage
from app.models.system_event import SystemEvent
from app.services import telemetry_retention as tr


@pytest.fixture()
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            ProcessedMessage.__table__,
            SystemEvent.__table__,
            OutboxEvent.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr(tr, "SessionLocal", factory)
    return factory


def _seed_outbox(factory, count: int, *, prefix: str, yas_dk: float = 60.0) -> None:
    now = datetime.now(timezone.utc)
    db = factory()
    try:
        for i in range(count):
            db.add(
                OutboxEvent(
                    topic="telemetry.raw_received",
                    dedup_key=f"{prefix}-{i}",
                    payload_json="{}",
                    published=True,
                    created_at=now,
                    published_at=now - timedelta(minutes=yas_dk),
                )
            )
        db.commit()
    finally:
        db.close()


def _count(factory, model) -> int:
    db = factory()
    try:
        return int(db.scalar(select(func.count(model.id))) or 0)
    finally:
        db.close()


class _BeklemeKaydi:
    """`_pause`i gozetleyen yardimci: gercekten UYUMAZ, sadece kaydeder."""

    def __init__(self) -> None:
        self.sureler: list[float] = []

    def __call__(self, saniye: float) -> bool:
        self.sureler.append(saniye)
        return False


# ---------------------------------------------------------------------------
# 1) YAKALAMA TURU — partiler arasinda nefes var mi
# ---------------------------------------------------------------------------

def test_YAKALAMA_turunda_partiler_ARASINDA_bekleniyor(session_factory, monkeypatch):
    """Dolu partiden sonra beklenmezse yuk periyoda YAYILMAZ, blok halinde carpar.

    Kurulum: 100 satir, batch 10 -> 10 dolu parti. Her dolu partiden sonra
    bir nefes bekliyoruz; son (kismi) partiden sonra HAYIR.
    """
    monkeypatch.setattr(settings, "retention_delete_batch", 10)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_outbox(session_factory, 100, prefix="eski")

    worker = tr.RetentionWorker()
    kayit = _BeklemeKaydi()
    monkeypatch.setattr(worker, "_pause", kayit)

    silinen = worker.purge_outbox_events()

    assert silinen == 100, "dagitim silmeyi eksiltmis — is AYNI kalmali"
    assert _count(session_factory, OutboxEvent) == 0
    assert kayit.sureler, (
        "partiler arasinda HIC beklenmemis — temizlik yine kesintisiz bir "
        "DELETE akisi olarak veri yolunun yanina biniyor"
    )
    assert all(s >= 0.0 for s in kayit.sureler)


def test_KISMI_partiden_sonra_BEKLENMIYOR(session_factory, monkeypatch):
    """Kararli durum hicbir ek gecikme gormemeli.

    Tur "silinecek sey kalmadi" ile bitiyorsa beklemenin kimseye faydasi
    yok: yalnizca thread'i mesgul tutar ve kapanisi geciktirir. Silinecek
    satirdan AZ olan tek bir parti = tam olarak bu durum.
    """
    monkeypatch.setattr(settings, "retention_delete_batch", 1000)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    _seed_outbox(session_factory, 7, prefix="az")

    worker = tr.RetentionWorker()
    kayit = _BeklemeKaydi()
    monkeypatch.setattr(worker, "_pause", kayit)

    assert worker.purge_outbox_events() == 7
    assert kayit.sureler == [], (
        "kismi partiden sonra da beklenmis — kararli durumdaki hafif turlar "
        "bosuna yavasliyor"
    )


def test_BEKLEME_partinin_KENDI_suresine_oranli(monkeypatch):
    """Sabit uyku yanlis olurdu: hizli diskte gereksiz yavaslatir, yavas
    diskte yetersiz kalir. Bekleme = parti_suresi x katsayi.
    """
    saat = iter([0.0, 2.0, 100.0, 100.0])  # parti 2,0 sn surdu; sonra cikis
    monkeypatch.setattr(tr, "_monotonic", lambda: next(saat))
    monkeypatch.setattr(settings, "retention_delete_batch", 5)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 2)

    class _Sonuc:
        rowcount = 5

    class _Oturum:
        def execute(self, *a, **k):
            return _Sonuc()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tr, "SessionLocal", lambda: _Oturum())
    kayit = _BeklemeKaydi()

    tr._batch_delete(OutboxEvent, OutboxEvent.id > 0, label="t", pause=kayit)

    assert kayit.sureler, "hic beklenmemis"
    beklenen = 2.0 * tr._PARTI_ARASI_KATSAYI
    assert kayit.sureler[0] == pytest.approx(beklenen), (
        f"bekleme parti suresine oranli degil: {kayit.sureler[0]} != {beklenen}"
    )


def test_DAGITILMIS_hiz_hala_URETIMIN_cok_USTUNDE():
    """Yayma, temizligi uretimin ALTINA dusurmemeli.

    Duzeltilen asil kusur buydu: silme hizi (1.000 satir/sn) uretimin
    (~1.074 satir/sn) altinda kalinca tablo kararli duruma HIC gelmiyordu.
    Nefes o kazanimi geri vermemeli.

    Olculen parti maliyeti: 20.000 satir / ~330 ms.
    """
    olculen_uretim = 1074.0
    parti = 20_000.0
    parti_sn = 0.33
    dagitilmis_hiz = parti / (parti_sn * (1.0 + tr._PARTI_ARASI_KATSAYI))
    assert dagitilmis_hiz >= olculen_uretim * 5, (
        f"dagitilmis temizlik hizi {dagitilmis_hiz:.0f} satir/sn — olculen "
        f"uretimin ({olculen_uretim:.0f}) 5 katinin altinda; tablo yine "
        "monoton buyur"
    )


def test_KAPANIS_beklemeyi_ANINDA_kesiyor():
    """`time.sleep` olsaydi `stop()`un 5 sn'lik join'i yakalama turunun
    ortasinda zaman asimina ugrar ve container kapanisi SIGKILL'e kalirdi.

    30 saniyelik bir bekleme isteniyor ama kapanis bayragi acik: cagri
    ANINDA ve True donmeli.
    """
    import time

    worker = tr.RetentionWorker()
    worker._stop.set()

    basladi = time.monotonic()
    sonuc = worker._pause(30.0)
    gecen = time.monotonic() - basladi

    assert sonuc is True, "kapanis istendigi halde `_pause` durmayi bildirmiyor"
    assert gecen < 1.0, (
        f"bekleme kapanista kesilmedi ({gecen:.1f} sn) — duz uyku kullanilmis"
    )


def test_KAPANIS_yakalama_TURUNU_yarida_birakiyor(session_factory, monkeypatch):
    """Kapanis sirasinda tur, kalan 9 partiyi kosmayi SURDURMEMELI."""
    monkeypatch.setattr(settings, "retention_delete_batch", 10)
    monkeypatch.setattr(settings, "retention_max_batches_per_run", 50)
    # Parti suresi sqlite'ta olculemeyecek kadar kisa; saati elle ilerletiyoruz
    # ki bekleme suresi SIFIR olmasin ve gercek bekleme yolu calissin.
    sayac = iter(range(1, 10_000))
    monkeypatch.setattr(tr, "_monotonic", lambda: next(sayac) * 0.1)
    _seed_outbox(session_factory, 100, prefix="eski")

    worker = tr.RetentionWorker()
    worker._stop.set()  # kapanis istendi

    silinen = worker.purge_outbox_events()

    assert silinen == 10, (
        "kapanis istendigi halde partiler surmus — bekleme kapanisi gozetmiyor "
        f"(silinen={silinen})"
    )


def test_DISK_GUARD_beklemiyor():
    """Emniyet subabinda oncelik tersine doner: alan HEMEN acilmali.

    Periyodik yolda nefes dogru (temizlik ingest'i itmesin); disk dolmak
    uzereyken ayni nefes, tam da onlemeye calistigimiz seyi davet eder.
    """
    import inspect
    import time

    from app.services import disk_guard

    kaynak = inspect.getsource(disk_guard._relieve_aggressive)
    assert "RetentionWorker(paced=False)" in kaynak, (
        "disk guard paced worker kullaniyor — disk baskisi altinda temizlik "
        "kendini frenliyor"
    )

    worker = tr.RetentionWorker(paced=False)
    basladi = time.monotonic()
    sonuc = worker._pause(999.0)
    gecen = time.monotonic() - basladi

    assert sonuc is False, "paced=False iken durma sinyali uretilmis"
    assert gecen < 0.5, (
        f"paced=False iken bile {gecen:.1f} sn beklenmis — bayrak yok sayiliyor"
    )


# ---------------------------------------------------------------------------
# 2) ACILIS — hepsi ayni anda, tam tavanla kosmamali
# ---------------------------------------------------------------------------

def _takvim(now: float = 1000.0) -> dict[str, float]:
    """`_seed_schedule` sonrasi her isin ILK kosma anini (now'a gore) verir."""
    worker = tr.RetentionWorker()
    jobs = worker._jobs()
    worker._seed_schedule(jobs, now)
    return {job[0]: (job[3] + float(job[1]())) - now for job in jobs}


def test_ACILISTA_purge_isleri_HEMEN_kosmuyor():
    """Eski `first_iteration` yolu tam olarak bunu yapiyordu.

    Taze deployda tuketici soguk ve NATS birikimini eritiyorken ayni
    saniyelerde 1.000.000 satirlik DELETE + ~770 MB WAL bindirmek, olculen
    Postgres CPU sicramasinin (%31 -> %123) gecis tepesiydi.
    """
    ilk = _takvim()
    for etiket in ("telemetry", "processed_messages", "system_events", "outbox_events"):
        assert ilk[etiket] > 0.0, (
            f"{etiket} purge'u acilista ANINDA kosuyor — soguk baslangicta "
            "veri yolu ile ayni saniyelerde DELETE firtinasi"
        )


def test_ACILISTA_purge_isleri_UST_USTE_binmiyor():
    """Hepsi ayni gecikmeyi paylasirsa yuk yalnizca ERTELENIR, dagilmaz."""
    ilk = _takvim()
    purge_anlari = [
        ilk[e]
        for e in ("telemetry", "processed_messages", "system_events", "outbox_events")
    ]
    assert len(set(purge_anlari)) == len(purge_anlari), (
        f"purge isleri ayni ana yigilmis: {purge_anlari}"
    )


def test_DISK_GUARD_acilista_BEKLETILMIYOR():
    """Emniyet subabi gecikmez: olcumu ucuz, gecikmesi tehlikeli."""
    assert _takvim()["disk_guard"] <= 0.0, (
        "disk guard acilis beklemesine takilmis — disk zaten dolmak uzereyse "
        "mudahale bir dakika gecikir"
    )


def test_ACILIS_beklemesi_PERIYODU_bozmuyor():
    """Gecikme yalnizca ILK tura ait olmali.

    `last_run` gelecege kaydiriliyor; periyot mantigi aynen korunuyor, yani
    uzun bir duraksamadan sonra yakalama hala gecikmesiz calisir.
    """
    now = 1000.0
    worker = tr.RetentionWorker()
    jobs = worker._jobs()
    worker._seed_schedule(jobs, now)
    for job in jobs:
        periyot = float(job[1]())
        ilk_kosma = job[3] + periyot
        assert ilk_kosma >= now - 1e-9, f"{job[0]} icin ilk kosma gecmiste"
        assert ilk_kosma - now <= tr._ILK_TUR_BEKLEME_SEC + len(jobs) * tr._ILK_TUR_KAYDIRMA_SEC, (
            f"{job[0]} icin acilis gecikmesi asiri: {ilk_kosma - now:.0f} sn"
        )


def test_ACILIS_gecikmesi_bir_TURLUK_birikimden_fazlasini_biriktirmiyor():
    """Gecikmenin bedeli olculebilir olmali: en fazla birkac partilik satir.

    Olculen uretim ~1.074 satir/sn. Bekleme boyunca biriken satir sayisi bir
    sonraki turun tavanini (batch x tur_tavani) asarsa erteleme, cozdugunden
    daha buyuk bir sorun yaratirdi.
    """
    olculen_uretim = 1074.0
    en_gec = tr._ILK_TUR_BEKLEME_SEC + 4 * tr._ILK_TUR_KAYDIRMA_SEC
    birikim = olculen_uretim * en_gec
    tur_tavani = settings.retention_delete_batch * settings.retention_max_batches_per_run
    assert birikim < tur_tavani, (
        f"acilis gecikmesi boyunca biriken satir ({birikim:.0f}) tek turun "
        f"tavanini ({tur_tavani}) asiyor"
    )
