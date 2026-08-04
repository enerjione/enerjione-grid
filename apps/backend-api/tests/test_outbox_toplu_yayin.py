"""Outbox yayincisi: SATIR SAHIPLENME (SKIP LOCKED) + TOPLU YAYIN.

OLCUM (saha test cihazi 192.168.2.99, 100 cihaz / 176 sinyal)
-------------------------------------------------------------
Gateway 1135 msj/sn basiyordu, backend ~480 msj/sn isliyordu; dakikada
~39.000 kayit birikiyordu. Tavan yayincidaydi ve iki sebebi vardi:

  1. YAYIN TEK TEKTI. Parti SELECT toplu aliniyordu ama her satir icin ayri
     bir asyncio-loop gecisi + ayri bir PubAck beklemesi yapiliyordu. Yani
     hiz `1 / RTT` idi ve PARTI BUYUKLUGUNDEN BAGIMSIZDI.
  2. SATIR SAHIPLENME YOKTU. Sorgu duz bir SELECT'ti: iki yayinci ayni anda
     kostugunda IKISI DE ayni ilk N satiri okur, IKISI DE broker'a basardi.
     Kilit cekismesi bile olusmaz — sessizce CIFT YAYIN. Bu, `--workers N`e
     gecmenin onundeki asil engeldi (SCADA'ya ayni olay N kez giderdi).

BU TESTLER NEYI KORUR
---------------------
Ikisi de "calisiyor gibi gorunen" turden kusurlar: cift yayin ancak SCADA
tarafinda fark edilir, ardisik yayin ise yalnizca yuk altinda olculur. Bu
yuzden burada KAYNAK metni degil DAVRANIS sinaniyor: gercek PostgreSQL
uzerinde iki es zamanli yayinci, ve sahte bir JetStream uzerinde olculen
es zamanlilik.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.outbox_event import OutboxEvent
from app.services import outbox_service
from app.services.jetstream_bus import JetStreamBus


def _satir(dedup_key: str) -> OutboxEvent:
    return OutboxEvent(
        topic="telemetry.raw_received",
        dedup_key=dedup_key,
        payload_json='{"source_gateway": "GW1", "message_id": "%s"}' % dedup_key,
        published=False,
        created_at=datetime.now(timezone.utc),
        attempts=0,
    )


# ---------------------------------------------------------------------------
# 1) Uretilen SQL — sahiplenme gercekten isteniyor mu
# ---------------------------------------------------------------------------

def test_flush_sorgusu_SATIRI_SAHIPLENIYOR_ve_KILITLIYI_ATLIYOR():
    """`FOR UPDATE SKIP LOCKED` olmadan iki yayinci ayni satiri yayinlar.

    SKIP LOCKED'siz duz `FOR UPDATE` de dogru sonuc verirdi ama ikinci
    yayinciyi birincinin hizina zincirlerdi; olcek amaci kaybolurdu. Bunun
    davranis karsiligi asagidaki es zamanlilik testinde.
    """
    from sqlalchemy.dialects import postgresql

    sql = str(
        outbox_service.pending_outbox_stmt(200).compile(dialect=postgresql.dialect())
    ).upper()
    assert "FOR UPDATE" in sql, "satirlar sahiplenilmiyor — iki yayinci ayni satiri okur"
    assert "SKIP LOCKED" in sql, (
        "kilitli satir ATLANMIYOR — ikinci yayinci birincinin arkasinda bekler"
    )


# ---------------------------------------------------------------------------
# 2) Toplu yayin — tavanin asil sebebi
# ---------------------------------------------------------------------------

class _SayanBus:
    """Kac cagriyla kac mesaj gonderildigini sayar."""

    def __init__(self) -> None:
        self.cagrilar: list[int] = []
        self.mesajlar: list[str] = []

    def publish_events(self, items):  # noqa: ANN001
        self.cagrilar.append(len(items))
        self.mesajlar.extend(m for _t, _p, m in items)
        return [None] * len(items)


@pytest.fixture()
def sqlite_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    OutboxEvent.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False, future=True)()
    try:
        yield session
    finally:
        session.close()


def test_TUM_parti_TEK_yayin_cagrisiyla_gidiyor(sqlite_db, monkeypatch):
    """Satir basina cagri = satir basina PubAck beklemesi = ~480 msj/sn tavani."""
    bus = _SayanBus()
    monkeypatch.setattr(outbox_service, "event_bus", bus)
    for i in range(50):
        sqlite_db.add(_satir(f"m-{i:03d}"))
    sqlite_db.commit()

    published = outbox_service.flush_outbox(sqlite_db, limit=50)

    assert published == 50
    assert bus.cagrilar == [50], (
        f"parti tek cagriyla verilmiyor (cagrilar={bus.cagrilar}) — her mesaj "
        "icin ayri thread gecisi + ayri PubAck beklemesi olur"
    )
    assert bus.mesajlar == [f"m-{i:03d}" for i in range(50)], "sira bozuldu"


def test_bos_kuyrukta_BROKER_a_HIC_gidilmiyor(sqlite_db, monkeypatch):
    bus = _SayanBus()
    monkeypatch.setattr(outbox_service, "event_bus", bus)

    assert outbox_service.flush_outbox(sqlite_db, limit=200) == 0
    assert bus.cagrilar == [], "bos partide bile broker cagrildi"


# ---------------------------------------------------------------------------
# 3) IKI ES ZAMANLI YAYINCI — cift yayin olmamali (gercek PostgreSQL)
# ---------------------------------------------------------------------------

PG_URL = os.getenv(
    "E1_TEST_PG_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
)


@pytest.fixture()
def pg_engine():
    """Izole bir sema icinde gercek `outbox_events` tablosu.

    SQLite ile yapilamaz: `FOR UPDATE SKIP LOCKED` orada sessizce yok sayilir,
    yani test yesil kalir ama HICBIR SEY kanitlamaz.
    """
    try:
        deneme = create_engine(PG_URL, connect_args={"connect_timeout": 3})
        with deneme.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL erisilebilir degil ({type(exc).__name__}): {PG_URL}")

    sema = f"e1_outbox_test_{os.getpid()}"
    with deneme.begin() as c:
        c.execute(text(f"DROP SCHEMA IF EXISTS {sema} CASCADE"))
        c.execute(text(f"CREATE SCHEMA {sema}"))
    deneme.dispose()

    engine = create_engine(
        PG_URL,
        connect_args={"connect_timeout": 3, "options": f"-csearch_path={sema}"},
        pool_size=5,
    )
    OutboxEvent.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        temizlik = create_engine(PG_URL, connect_args={"connect_timeout": 3})
        with temizlik.begin() as c:
            c.execute(text(f"DROP SCHEMA IF EXISTS {sema} CASCADE"))
        temizlik.dispose()


def test_IKI_ES_ZAMANLI_yayinci_AYNI_satiri_ISLEMIYOR(pg_engine, monkeypatch):
    """Asil kusur buydu: iki yayinci ayni satirlari ikiser kez yayinliyordu.

    Kurgu: A yayinciyi SELECT'ten hemen sonra (kilitler elindeyken) durduruyoruz
    ve B'yi tam o anda kosturuyoruz.
      * SKIP LOCKED varsa: B kilitli satirlari ATLAR, 0 satirla hemen doner.
      * Hic kilit yoksa:   B ayni satirlari okur ve YENIDEN yayinlar (cift yayin).
      * Kilit var ama SKIP yoksa: B, A commit edene kadar BEKLER (zincirlenme).
    Ucunu de ayirt edebilmek icin hem cift yayin hem de "B bekledi mi" olculuyor.
    """
    Session = sessionmaker(bind=pg_engine, autoflush=False, future=True)
    hazirlik = Session()
    for i in range(5):
        hazirlik.add(_satir(f"m-{i}"))
    hazirlik.commit()
    hazirlik.close()

    a_sahiplendi = threading.Event()
    b_bitti = threading.Event()
    kilit = threading.Lock()
    yayinlananlar: list[str] = []
    olculen: dict[str, object] = {}

    class _EsZamanliBus:
        def publish_events(self, items):  # noqa: ANN001
            if threading.current_thread().name == "yayinci-A":
                # SELECT bitti, satirlar A'nin elinde. B'yi simdi kostur.
                a_sahiplendi.set()
                olculen["b_engellenmedi"] = b_bitti.wait(5)
            with kilit:
                yayinlananlar.extend(m for _t, _p, m in items)
            return [None] * len(items)

    monkeypatch.setattr(outbox_service, "event_bus", _EsZamanliBus())

    hatalar: list[BaseException] = []

    def _kos(ad: str, once_bekle: threading.Event | None) -> None:
        try:
            if once_bekle is not None:
                once_bekle.wait(5)
            db = Session()
            try:
                olculen[ad] = outbox_service.flush_outbox(db, limit=10)
            finally:
                db.close()
        except BaseException as exc:  # noqa: BLE001
            hatalar.append(exc)
        finally:
            if ad == "B":
                b_bitti.set()

    ta = threading.Thread(target=_kos, args=("A", None), name="yayinci-A")
    tb = threading.Thread(target=_kos, args=("B", a_sahiplendi), name="yayinci-B")
    ta.start()
    tb.start()
    ta.join(20)
    tb.join(20)

    assert not hatalar, f"yayinci patladi: {hatalar}"
    assert len(yayinlananlar) == len(set(yayinlananlar)), (
        f"CIFT YAYIN: {sorted(yayinlananlar)} — iki yayinci ayni satiri isledi"
    )
    assert sorted(yayinlananlar) == [f"m-{i}" for i in range(5)]
    assert olculen["A"] == 5 and olculen["B"] == 0
    assert olculen.get("b_engellenmedi") is True, (
        "B, A'nin kilitleri elindeyken BEKLEDI — SKIP LOCKED yok, ikinci "
        "yayinci birincinin hizina zincirlenir"
    )

    # Satirlarin durumu da tutarli olmali: hepsi TEK kez published.
    kontrol = Session()
    try:
        rows = list(kontrol.scalars(select(OutboxEvent)).all())
        assert all(r.published for r in rows)
        assert all(r.attempts == 0 for r in rows), "gereksiz basarisiz deneme sayildi"
    finally:
        kontrol.close()


# ---------------------------------------------------------------------------
# 4) JetStream partisi — ack'ler PARALEL beklenmeli
# ---------------------------------------------------------------------------

class _SahteJs:
    """Es zamanli publish sayisini olcen sahte JetStream context'i."""

    def __init__(self, *, gecikme: float = 0.02, patlayan_subject: str = "") -> None:
        self.anlik = 0
        self.zirve = 0
        self.gecikme = gecikme
        self.patlayan_subject = patlayan_subject
        self.gonderilen: list[tuple[str, dict | None]] = []

    async def publish(self, subject, body, headers=None):  # noqa: ANN001
        self.anlik += 1
        self.zirve = max(self.zirve, self.anlik)
        try:
            await asyncio.sleep(self.gecikme)
            if self.patlayan_subject and subject == self.patlayan_subject:
                raise ValueError("nats reddetti")
            self.gonderilen.append((subject, headers))
            return object()
        finally:
            self.anlik -= 1


@pytest.fixture()
def hazir_bus():
    bus = JetStreamBus(
        url="nats://127.0.0.1:4222",
        stream_raw="TELEMETRY_RAW",
        stream_normalized="TELEMETRY_NORMALIZED",
        stream_dlq="TELEMETRY_DLQ",
        subject_raw_pattern="e1.telemetry.raw.>",
        subject_normalized_pattern="e1.telemetry.normalized.>",
        subject_dlq_pattern="e1.dlq.>",
        max_age_days_raw=7,
        max_age_days_normalized=7,
        max_age_days_dlq=30,
        connect_timeout_sec=1,
    )
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    bus._loop = loop
    bus._ready.set()
    try:
        yield bus
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)
        loop.close()


def _items(n: int, *, gateway: str = "GW1"):
    return [
        (
            "telemetry.raw_received",
            {"source_gateway": gateway, "message_id": f"m{i}"},
            f"m{i}",
        )
        for i in range(n)
    ]


def test_parti_TEK_gecisle_PARALEL_yayinlaniyor(hazir_bus):
    """Ardisik yayinda 20 mesaj = 20 x RTT; paralelde ~1 x RTT."""
    js = _SahteJs(gecikme=0.05)
    hazir_bus._js = js

    t0 = time.perf_counter()
    sonuc = hazir_bus.publish_events(_items(20))
    sure = time.perf_counter() - t0

    assert sonuc == [None] * 20
    assert js.zirve == 20, (
        f"mesajlar ARDISIK yayinlanmis (zirve es zamanlilik={js.zirve}) — "
        "yayin hizi parti buyuklugunden bagimsiz kalir"
    )
    assert sure < 0.5, f"parti {sure:.2f} sn surdu — ardisik yayina yakin (20 x 0.05)"
    assert all(s == "e1.telemetry.raw.GW1" for s, _h in js.gonderilen)


def test_parti_icinde_dedup_basligi_MESAJ_BASINA_korunuyor(hazir_bus):
    """`Nats-Msg-Id` broker tarafi idempotency'nin ta kendisi; toplu yayinda
    dusurulurse JetStream dedup penceresi ise yaramaz."""
    js = _SahteJs(gecikme=0.0)
    hazir_bus._js = js

    hazir_bus.publish_events(_items(5))

    assert [h["Nats-Msg-Id"] for _s, h in js.gonderilen] == [f"m{i}" for i in range(5)]


def test_partideki_TEK_hatali_mesaj_DIGERLERINI_dusurmuyor(hazir_bus):
    """Sonuc listesi mesaj basina olmasaydi tek zehirli mesaj 200'luk partiyi
    geri cevirir ve hepsi yeniden yayinlanirdi."""
    js = _SahteJs(gecikme=0.0, patlayan_subject="e1.telemetry.raw.KOTU")
    hazir_bus._js = js

    items = _items(3) + _items(1, gateway="KOTU")
    sonuc = hazir_bus.publish_events(items)

    assert sonuc[:3] == [None, None, None]
    assert isinstance(sonuc[3], Exception), "hatali mesaj basarili sayildi"
    assert len(js.gonderilen) == 3


def test_bus_HAZIR_DEGILSE_parti_yayilmiyor(hazir_bus):
    """Sistemik ariza: satirlar published=False kalmali."""
    hazir_bus._ready.clear()
    with pytest.raises(RuntimeError, match="not ready"):
        hazir_bus.publish_events(_items(3))


def test_IPTAL_edilen_yayin_BASARILI_sayilmiyor(hazir_bus):
    """En sinsi kayip: `CancelledError` `Exception` DEGIL, `BaseException`dir.

    `gather(return_exceptions=True)` iptal edilen cocugu sonuc listesine
    NESNE olarak koyar. `isinstance(r, Exception)` arayan bir esleme onu
    None'a — yani BASARILI'ya — cevirir; `flush_outbox` satiri
    published=True yapar, retention birkac dakika sonra siler ve mesaj
    JetStream'e HIC gitmemis olur. Alarm uretilmez, SCADA'ya gitmez, geri
    donusu yoktur.

    Gercek tetikleyici: NATS oturumu ack beklenirken kapanir (broker
    restart, `update.sh nats`, ag kesintisi) ve bekleyen publish gorevleri
    iptal edilir.
    """
    class _IptalEdenJs:
        """Ikinci mesajin publish'i iptal edilir."""

        def __init__(self) -> None:
            self.sayac = 0

        async def publish(self, subject, body, headers=None):  # noqa: ANN001
            self.sayac += 1
            if self.sayac == 2:
                raise asyncio.CancelledError()
            return object()

    hazir_bus._js = _IptalEdenJs()

    sonuc = hazir_bus.publish_events(_items(3))

    assert sonuc[0] is None and sonuc[2] is None
    assert sonuc[1] is not None, (
        "iptal edilen yayin BASARILI sayildi — outbox satiri published=True "
        "olur ve mesaj JetStream'e hic gitmeden kalici olarak kaybolur"
    )
    # `flush_outbox` tumu basarisizsa ilk hatayi RAISE eder ve
    # `outbox_flush_worker._run` yalnizca `except Exception` yakalar; ciplak
    # bir BaseException yayin thread'ini oldururdu.
    assert isinstance(sonuc[1], Exception), (
        f"hata {type(sonuc[1]).__name__} olarak donuyor — outbox flush "
        "thread'i bunu yakalayamaz ve sessizce olur"
    )


# ---------------------------------------------------------------------------
# 5) Karisik parti — sonuc listesi MESAJLA HIZALI kalmali
#
# Hizalama kayarsa `flush_outbox` YANLIS satiri published isaretler: gitmemis
# bir olay kuyruktan duser (sessiz telemetri kaybi), giden bir olay ise
# yeniden yayinlanir. Bu, testsiz fark edilmesi en zor kusur turu.
# ---------------------------------------------------------------------------

def test_karisik_partide_SONUC_MESAJLA_HIZALI(monkeypatch):
    from app.services import event_bus as eb

    bus = object.__new__(eb.RabbitMqEventBus)
    bus._subscribers = {}

    rabbit: list[str] = []

    def _rabbit(topic, payload, *, message_id):  # noqa: ANN001
        if message_id == "a-kotu":
            raise RuntimeError("rabbit reddetti")
        rabbit.append(message_id)

    bus._publish_to_rabbitmq = _rabbit

    class _SahteJetStream:
        def __init__(self) -> None:
            self.partiler: list[list[str]] = []

        def publish_events(self, items):  # noqa: ANN001
            self.partiler.append([m for _t, _p, m in items])
            return [ValueError("nats reddetti") if m == "t-kotu" else None
                    for _t, _p, m in items]

    js = _SahteJetStream()
    monkeypatch.setattr("app.services.jetstream_bus.get_bus", lambda: js)

    items = [
        ("telemetry.raw_received", {"source_gateway": "GW"}, "t-iyi"),
        ("alarm.raised", {}, "a-kotu"),
        ("telemetry.raw_received", {"source_gateway": "GW"}, "t-kotu"),
        ("alarm.raised", {}, "a-iyi"),
    ]
    sonuc = bus.publish_events(items)

    assert sonuc[0] is None, "basarili telemetri hatali sayildi"
    assert isinstance(sonuc[1], Exception), "patlayan alarm basarili sayildi"
    assert isinstance(sonuc[2], Exception), "patlayan telemetri basarili sayildi"
    assert sonuc[3] is None
    # Telemetri TEK partide gitti; alarm RabbitMQ'ya (dusuk hacim) tek tek.
    assert js.partiler == [["t-iyi", "t-kotu"]]
    assert rabbit == ["a-iyi"]


def test_jetstream_YOKSA_telemetri_partisi_TAMAMEN_basarisiz(monkeypatch):
    """Bus kurulamamissa satirlar published=False kalmali; aksi halde NATS
    kesintisinde sessiz telemetri kaybi olur."""
    from app.services import event_bus as eb

    bus = object.__new__(eb.RabbitMqEventBus)
    bus._subscribers = {}
    monkeypatch.setattr("app.services.jetstream_bus.get_bus", lambda: None)

    sonuc = bus.publish_events(
        [("telemetry.raw_received", {"source_gateway": "GW"}, f"m{i}") for i in range(3)]
    )

    assert all(isinstance(h, RuntimeError) for h in sonuc), (
        "bus yokken mesajlar basarili sayildi"
    )


# ---------------------------------------------------------------------------
# 6) Parti boyutu ENV ile ayarlanabilir olmali
# ---------------------------------------------------------------------------

def test_parti_boyutu_OUTBOX_FLUSH_BATCH_ile_ayarlanabiliyor(monkeypatch):
    """Toplu yayindan sonra RTT parti BASINA dustugu icin parti boyutu asil
    ayar dugmesi. Env okunmayip kod sabiti kullanilirsa saha bunu ceviremez."""
    from app.services import outbox_flush_worker as w

    monkeypatch.setenv("OUTBOX_FLUSH_BATCH", "777")
    worker = w.OutboxFlushWorker()
    gorulen: list[int] = []

    class _SahteSession:
        def close(self) -> None:
            pass

    def _sahte_flush(db, *, limit):  # noqa: ANN001
        gorulen.append(limit)
        worker._stop.set()
        return 0

    monkeypatch.setattr(w, "SessionLocal", _SahteSession)
    monkeypatch.setattr(w, "flush_outbox", _sahte_flush)

    worker._run()

    assert gorulen == [777], (
        f"OUTBOX_FLUSH_BATCH flush'a ulasmiyor (gorulen={gorulen})"
    )
