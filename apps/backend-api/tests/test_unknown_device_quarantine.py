"""Bilinmeyen cihaz telemetrisi: karantina + replay (birim testleri).

ANA DAYANIKLILIK INVARIANTI
---------------------------
    BILINMEYEN CIHAZ + KARANTINA PERSIST EDILEMEDI = ASLA ACK

Eski davranis payload'i atip mesaji ack ediyordu; bu dosya o davranisin
geri gelmesini engeller.

NEDEN BURADA COPY YOLU YOK
--------------------------
`_tek_gecis_yaz` PostgreSQL'e ozgudur (COPY + execute_values). Yalnizca
BILINMEYEN cihaz iceren bir batch o yola HIC girmez (`satirlar` bos kalir),
bu yuzden karantina davranisi SQLite uzerinde durustce test edilebilir.
Bilinen cihaz yolu, replay'in gercek telemetri yazimi ve crash pencereleri
gercek PG16/Timescale ile `tests/integration/test_unknown_device_quarantine_pg.py`
icinde dogrulanir.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (Base.metadata kaydi)
from app.db.base import Base
from app.models.device import Device
from app.models.processed_message import ProcessedMessage
from app.models.unknown_device_telemetry import UnknownDeviceTelemetry
from app.services import telemetry_consumer
from app.services import unknown_device_quarantine as quarantine
from app.services import unknown_device_replay


# --------------------------------------------------------------------------
# Kurulum
# --------------------------------------------------------------------------
def _sqlite_savepoint_destegi(engine) -> None:  # noqa: ANN001
    """pysqlite'i SAVEPOINT'lerin GERCEKTEN calisacagi moda alir.

    NEDEN SART: pysqlite surucusu kendi ortuluk transaction yonetimini yapar
    ve SAVEPOINT/nested transaction ile DOGRU CALISMAZ — `RELEASE SAVEPOINT`
    fiilen commit gibi davranir. Bu, karantina yolunun savepoint izolasyonunu
    (yer acma geri sarilmali) SQLite uzerinde SESSIZCE test edilemez hale
    getirirdi: geri sarilmasi gereken satir kalici gorunurdu.

    SQLAlchemy'nin belgeledigi cozum: sürücünün ortulu BEGIN'ini kapat ve
    BEGIN'i kendimiz yay. Uretim PostgreSQL; bu yalnizca birim testlerinin
    uretimle AYNI transaction semantigini gormesi icin.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _baglanti(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn):  # noqa: ANN001
        conn.exec_driver_sql("BEGIN")


@pytest.fixture()
def Session(monkeypatch):  # noqa: N802
    engine = create_engine("sqlite://", future=True)
    _sqlite_savepoint_destegi(engine)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(telemetry_consumer, "SessionLocal", maker)
    quarantine.reset_stats_for_test()
    yield maker
    Base.metadata.drop_all(engine)


class FakeMsg:
    """JetStream mesajinin testte ihtiyac duyulan yuzeyi."""

    def __init__(self, payload: dict, *, seq: int | None = None, subject: str = "e1.telemetry.normalized.gw1"):
        self.data = json.dumps(payload, default=str).encode()
        self.subject = subject
        self.metadata = (
            SimpleNamespace(
                stream="TELEMETRY_NORMALIZED",
                sequence=SimpleNamespace(stream=seq),
            )
            if seq is not None
            else None
        )


def _payload(**kw) -> dict:
    veri = {
        "message_id": "msg-1",
        "device_code": "DEV-YOK",
        "signal_key": "master.actual_voltage",
        "value": 231.4,
        "quality": "good",
        "source_gateway": "GW-001",
        "source_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    veri.update(kw)
    return veri


def _cihaz(db, code="DEV-YOK", gateway_code="GW-001") -> Device:
    d = Device(
        code=code,
        name=code,
        gateway_code=gateway_code,
        ip_address="10.0.0.1",
        latitude=41.0,
        longitude=29.0,
    )
    db.add(d)
    db.commit()
    return d


def _karantina_sayisi(Session) -> int:  # noqa: N803
    with Session() as db:
        return int(db.scalar(select(func.count()).select_from(UnknownDeviceTelemetry)) or 0)


# --------------------------------------------------------------------------
# T02 / T03 / T08 — karantina olusumu ve ack sozlesmesi
# --------------------------------------------------------------------------
def test_T02_bilinmeyen_cihaz_karantina_satiri_uretir(Session):  # noqa: N803
    msg = FakeMsg(_payload(), seq=41)
    ok, bad, _ws, _out = telemetry_consumer._persist_batch([msg])

    assert bad == []
    assert ok == [msg], "karantina yazildi -> mesaj ack edilebilir"

    with Session() as db:
        satir = db.scalars(select(UnknownDeviceTelemetry)).one()
        assert satir.device_code == "DEV-YOK"
        assert satir.gateway_code == "GW-001"
        assert satir.status == quarantine.STATUS_PENDING
        assert satir.reason == quarantine.REASON_DEVICE_NOT_FOUND
        assert satir.seen_count == 1
        # Payload AYNEN korunmali: replay onu geri cozecek.
        assert json.loads(satir.payload_json)["value"] == 231.4


def test_T03_ack_listesi_ancak_persist_sonrasi_dolar(Session):  # noqa: N803
    """Mesaj, karantina satiri DB'de gorunmeden ack listesine girmemeli."""
    msg = FakeMsg(_payload(), seq=42)
    ok, _bad, _ws, _out = telemetry_consumer._persist_batch([msg])

    # ok_msgs dolmussa satir da KALICI olmali (ayni commit).
    assert ok == [msg]
    assert _karantina_sayisi(Session) == 1


def test_T08_bilinmeyen_telemetri_cihaz_URETMEZ(Session):  # noqa: N803
    telemetry_consumer._persist_batch([FakeMsg(_payload(), seq=43)])
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Device)) == 0


def test_bilinmeyen_icin_ProcessedMessage_YAZILMAZ(Session):  # noqa: N803
    """MUTATION C'nin hedefi: terminal dedup satiri replay'i bloke ederdi.

    Eski kod burada `processed_messages` satiri yaziyordu. O satir dururken
    ne yeniden teslim ne de karantinadan replay calisabilirdi.
    """
    telemetry_consumer._persist_batch([FakeMsg(_payload(), seq=44)])
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(ProcessedMessage)) == 0


# --------------------------------------------------------------------------
# T04 / T20 — persist basarisiz => ACK YOK
# --------------------------------------------------------------------------
def test_T04_karantina_yazimi_patlarsa_ACK_YOK(Session, monkeypatch):  # noqa: N803
    """DB hatasi ack'e donusmemeli — bu gorevin ana invarianti."""

    def patla(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(quarantine, "quarantine_batch", patla)

    with pytest.raises(RuntimeError):
        telemetry_consumer._persist_batch([FakeMsg(_payload(), seq=45)])

    # Istisna disari cikti -> cagiran hicbir mesaji ack etmez, JetStream
    # yeniden teslim eder.
    assert _karantina_sayisi(Session) == 0


# --------------------------------------------------------------------------
# C01-C11 — KAPASITE: kontrollu yer acma
#
# ESKI POLITIKA (terk edildi): tavan dolunca mesaji ack ETME, broker'in
# yeniden teslimine guven. Consumer `max_deliver` ile kostugu icin bu
# "guvenli bekleme" DEGILDI; ustelik retry firtinasi + backlog baskisi
# uretip sonunda ayni veri kaybini getiriyordu.
#
# YENI POLITIKA: sirayla suresi dolmus replayed -> suresi dolmus pending ->
# ACIL VERI DUSURME. Ucu de AYRI sayilir; dusurme ayrica olay uretir.
# --------------------------------------------------------------------------
def _pending_ekle(Session, adet: int, *, yas_gun: float = 0.0, onek: str = "eski"):  # noqa: N803
    simdi = datetime.now(timezone.utc)
    with Session() as db:
        for i in range(adet):
            t = simdi - timedelta(days=yas_gun, seconds=adet - i)
            _satir_ekle(db, status=quarantine.STATUS_PENDING, key=f"{onek}-{i}",
                        first_seen=t)
        db.commit()
    quarantine._sayim_onbellegi_bosalt()


def _replayed_ekle(Session, adet: int, *, yas_gun: float, onek: str = "rp"):  # noqa: N803
    simdi = datetime.now(timezone.utc)
    with Session() as db:
        for i in range(adet):
            t = simdi - timedelta(days=yas_gun, seconds=adet - i)
            _satir_ekle(db, status=quarantine.STATUS_REPLAYED, key=f"{onek}-{i}",
                        first_seen=t, replayed_at=t)
        db.commit()
    quarantine._sayim_onbellegi_bosalt()


def _tavan(monkeypatch, n: int) -> None:
    monkeypatch.setattr(
        quarantine.settings, "unknown_telemetry_max_rows", n, raising=False
    )
    quarantine._sayim_onbellegi_bosalt()


def _olaylar(Session, tur: str) -> list:  # noqa: N803
    from app.models.system_event import SystemEvent

    with Session() as db:
        return list(
            db.scalars(select(SystemEvent).where(SystemEvent.event_type == tur)).all()
        )


def test_C01_tavan_altinda_temizlik_YOK_yeni_satir_kalici(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 100)
    _pending_ekle(Session, 3)

    ok, _bad, _w, _o = telemetry_consumer._persist_batch(
        [FakeMsg(_payload(message_id="c01"), seq=101)]
    )

    assert len(ok) == 1, "commit sonrasi ack"
    assert _karantina_sayisi(Session) == 4
    s = quarantine.get_stats()
    assert s["unknown_device_quarantine_data_shed_total"] == 0
    assert s["unknown_device_quarantine_expired_total"] == 0
    assert s["unknown_device_quarantine_replayed_cleanup_total"] == 0


def test_C02_suresi_dolmus_REPLAYED_temizlenir_shed_YOK(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 3)
    _replayed_ekle(Session, 3, yas_gun=40)  # replayed retention 7 gun

    ok, _bad, _w, _o = telemetry_consumer._persist_batch(
        [FakeMsg(_payload(message_id="c02"), seq=102)]
    )

    assert len(ok) == 1
    assert _karantina_sayisi(Session) == 3, "tavan korundu"
    s = quarantine.get_stats()
    assert s["unknown_device_quarantine_replayed_cleanup_total"] == 1
    assert s["unknown_device_quarantine_data_shed_total"] == 0, "shed olmamali"
    assert s["unknown_device_quarantine_expired_total"] == 0


def test_C03_suresi_dolmus_PENDING_temizlenir_expired_artar(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 3)
    _pending_ekle(Session, 3, yas_gun=40)  # pending retention 30 gun

    ok, _bad, _w, _o = telemetry_consumer._persist_batch(
        [FakeMsg(_payload(message_id="c03"), seq=103)]
    )

    assert len(ok) == 1
    assert _karantina_sayisi(Session) == 3
    s = quarantine.get_stats()
    assert s["unknown_device_quarantine_expired_total"] == 1
    assert s["unknown_device_quarantine_data_shed_total"] == 0


def test_C04_hicbiri_suresi_dolmamissa_ACIL_DUSURME(Session, monkeypatch):  # noqa: N803
    """Tavan dolu, silinebilir suresi dolmus kayit yok -> en eski pending gider."""
    _tavan(monkeypatch, 3)
    _pending_ekle(Session, 3, yas_gun=0)  # hepsi taze

    ok, _bad, _w, _o = telemetry_consumer._persist_batch(
        [FakeMsg(_payload(message_id="c04"), seq=104)]
    )

    assert len(ok) == 1, "yeni unknown yine de KALICI ve ack edilir"
    assert _karantina_sayisi(Session) == 3
    s = quarantine.get_stats()
    assert s["unknown_device_quarantine_data_shed_total"] == 1, "tam olarak 1 satir"
    assert s["unknown_device_quarantine_expired_total"] == 0

    with Session() as db:
        anahtarlar = set(db.scalars(select(UnknownDeviceTelemetry.dedup_key)).all())
    assert "eski-0" not in anahtarlar, "EN ESKI kayit dusuruldu"
    assert "c04" in anahtarlar, "yeni kayit yazildi"


def test_C05_acil_dusurme_OLAY_uretir(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 2)
    _pending_ekle(Session, 2, yas_gun=0)

    telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="c05"), seq=105)])

    olaylar = _olaylar(Session, "unknown_device_quarantine_data_shed")
    assert len(olaylar) == 1
    meta = json.loads(olaylar[0].metadata_json)
    assert meta["deleted_count"] == 1
    assert meta["hard_limit"] == 2
    assert meta["rows_before"] == 2
    # Hassas veri SIZMAMALI.
    icerik = (olaylar[0].metadata_json or "") + (olaylar[0].message or "")
    assert "payload" not in icerik.lower()
    assert "231.4" not in icerik, "olcum degeri olaya girmemeli"


def test_C06_cok_satir_dusurulunce_TEK_olay(Session, monkeypatch):  # noqa: N803
    """100 satir silinince 100 olay YOK — bir reclaim = bir olay."""
    _tavan(monkeypatch, 100)
    _pending_ekle(Session, 100, yas_gun=0)
    _tavan(monkeypatch, 60)  # tavani dusur: 41 satir dusurulecek

    telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="c06"), seq=106)])

    olaylar = _olaylar(Session, "unknown_device_quarantine_data_shed")
    assert len(olaylar) == 1, f"tek toplu olay bekleniyordu, {len(olaylar)} bulundu"
    assert json.loads(olaylar[0].metadata_json)["deleted_count"] == 41
    assert quarantine.get_stats()["unknown_device_quarantine_data_shed_total"] == 41


def test_C07_reclaim_basarili_insert_BASARISIZ_ise_hersey_geri_sarilir(  # noqa: N803
    Session, monkeypatch
):
    """Eski satirlar silinip yeni satir yazilamazsa SAF VERI KAYBI olurdu."""
    _tavan(monkeypatch, 3)
    _pending_ekle(Session, 3, yas_gun=0)

    def upsert_patlat(*_a, **_k):
        raise RuntimeError("insert basarisiz")

    monkeypatch.setattr(quarantine, "_upsert_stmt", upsert_patlat)

    with pytest.raises(RuntimeError):
        telemetry_consumer._persist_batch(
            [FakeMsg(_payload(message_id="c07"), seq=107)]
        )

    # Silme AYNI transaction'daydi -> geri sarildi.
    assert _karantina_sayisi(Session) == 3
    with Session() as db:
        anahtarlar = set(db.scalars(select(UnknownDeviceTelemetry.dedup_key)).all())
    assert "eski-0" in anahtarlar, "silinen kayit geri gelmeli"
    assert "c07" not in anahtarlar


def test_C08_commit_basarisizsa_ACK_YOK(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 100)

    class Patlayan(Exception):
        pass

    monkeypatch.setattr(
        telemetry_consumer.SessionLocal.class_,
        "commit",
        lambda self: (_ for _ in ()).throw(Patlayan()),
        raising=False,
    )

    with pytest.raises(Patlayan):
        telemetry_consumer._persist_batch(
            [FakeMsg(_payload(message_id="c08"), seq=108)]
        )

    monkeypatch.undo()
    assert _karantina_sayisi(Session) == 0, "commit edilmeyen satir kalici olmaz"


def test_C10_bilinen_cihaz_kapasiteden_ETKILENMEZ(Session, monkeypatch):  # noqa: N803
    with Session() as db:
        _cihaz(db, code="DEV-VAR")
    _tavan(monkeypatch, 1)
    _pending_ekle(Session, 1, yas_gun=0)

    monkeypatch.setattr(
        telemetry_consumer,
        "_satirlari_yaz",
        lambda db, satirlar, ts: (satirlar, []),
    )

    bilinen = FakeMsg(_payload(message_id="k1", device_code="DEV-VAR"), seq=110)
    bilinmeyen = FakeMsg(_payload(message_id="u1", device_code="DEV-YOK"), seq=111)
    ok, _bad, _w, _o = telemetry_consumer._persist_batch([bilinen, bilinmeyen])

    assert bilinen in ok, "bilinen cihaz olcumu kapasiteden etkilenmemeli"
    assert bilinmeyen in ok, "yer acildi -> bilinmeyen de kalici ve ack edilir"


def test_C11_yeni_unknown_REDELIVERY_beklemeden_kalici(Session, monkeypatch):  # noqa: N803
    """Tavan dolu olsa bile ilk teslimde yazilir; broker tekrarina bagimlilik yok."""
    _tavan(monkeypatch, 2)
    _pending_ekle(Session, 2, yas_gun=0)

    ok, bad, _w, _o = telemetry_consumer._persist_batch(
        [FakeMsg(_payload(message_id="c11"), seq=111)]
    )

    assert len(ok) == 1 and bad == []
    with Session() as db:
        assert db.scalar(
            select(func.count())
            .select_from(UnknownDeviceTelemetry)
            .where(UnknownDeviceTelemetry.dedup_key == "c11")
        ) == 1


def test_parti_tavandan_BUYUKSE_yapilandirma_hatasi(Session, monkeypatch):  # noqa: N803
    """Yer acmak bile yetmez: tabloyu bosaltip yine basarisiz olmak yerine
    acikca reddedilir ve mesaj ack EDILMEZ."""
    _tavan(monkeypatch, 1)

    ok, bad, _w, _o = telemetry_consumer._persist_batch([
        FakeMsg(_payload(message_id="b1"), seq=201),
        FakeMsg(_payload(message_id="b2", device_code="D2"), seq=202),
    ])

    assert ok == [] and bad == []
    assert quarantine.get_stats()["unknown_device_quarantine_capacity_rejected_total"] == 2


# --------------------------------------------------------------------------
# D01-D03 — TAVANDA TEKRAR MESAJI VERI DUSURMEMELI
#
# `ON CONFLICT DO UPDATE` mevcut bir dedup anahtari icin YENI SATIR ACMAZ.
# Kapasite hesabi parti buyuklugu uzerinden yapilirsa, tavan doluyken gelen
# bir YENIDEN TESLIM bosuna bir satirin dusurulmesine yol acar — yani
# broker'in tekrari VERI KAYBI uretir. Hesap yalnizca GERCEKTEN yeni
# anahtarlari saymali.
# --------------------------------------------------------------------------
def test_D01_tavanda_TEKRAR_mesaji_hicbir_seyi_dusurmez(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 3)
    ilk = _payload(message_id="dup-1")
    telemetry_consumer._persist_batch([FakeMsg(ilk, seq=501)])
    _pending_ekle(Session, 2, yas_gun=0, onek="dolgu")
    assert _karantina_sayisi(Session) == 3, "tavan dolu"
    quarantine.reset_stats_for_test()

    ok, bad, _w, _o = telemetry_consumer._persist_batch([FakeMsg(ilk, seq=501)])

    assert len(ok) == 1 and bad == [], "tekrar mesaji yine ack edilir"
    assert _karantina_sayisi(Session) == 3, "satir sayisi DEGISMEMELI"
    s = quarantine.get_stats()
    assert s["unknown_device_quarantine_data_shed_total"] == 0, "hicbir sey dusurulmedi"
    assert s["unknown_device_quarantine_expired_total"] == 0
    assert s["unknown_device_quarantine_replayed_cleanup_total"] == 0
    assert s["unknown_device_quarantine_total"] == 0, "yeni satir acilmadi"
    assert s["unknown_device_quarantine_redelivered_total"] == 1

    with Session() as db:
        satir = db.scalars(
            select(UnknownDeviceTelemetry).where(
                UnknownDeviceTelemetry.dedup_key == "dup-1"
            )
        ).one()
        assert satir.seen_count == 2


def test_D02_tavanda_2_tekrar_1_yeni_TAM_1_satir_acar(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 4)
    p1 = _payload(message_id="d2-a")
    p2 = _payload(message_id="d2-b", device_code="DEV-B")
    telemetry_consumer._persist_batch([FakeMsg(p1, seq=511), FakeMsg(p2, seq=512)])
    _pending_ekle(Session, 2, yas_gun=0, onek="d2dolgu")
    assert _karantina_sayisi(Session) == 4
    quarantine.reset_stats_for_test()

    yeni = _payload(message_id="d2-yeni", device_code="DEV-C")
    ok, _bad, _w, _o = telemetry_consumer._persist_batch([
        FakeMsg(p1, seq=511), FakeMsg(p2, seq=512), FakeMsg(yeni, seq=513),
    ])

    assert len(ok) == 3
    assert _karantina_sayisi(Session) == 4, "tavan korundu"
    s = quarantine.get_stats()
    assert s["unknown_device_quarantine_data_shed_total"] == 1, "TAM 1 satir yer acildi"
    assert s["unknown_device_quarantine_total"] == 1, "TAM 1 yeni satir"
    assert s["unknown_device_quarantine_redelivered_total"] == 2

    with Session() as db:
        anahtarlar = set(db.scalars(select(UnknownDeviceTelemetry.dedup_key)).all())
        assert "d2-yeni" in anahtarlar
        assert {"d2-a", "d2-b"} <= anahtarlar, "tekrarlar dusurulmedi"
        for k in ("d2-a", "d2-b"):
            satir = db.scalars(
                select(UnknownDeviceTelemetry).where(
                    UnknownDeviceTelemetry.dedup_key == k
                )
            ).one()
            assert satir.seen_count == 2


def test_D03_tavanda_tekrarli_teslim_satir_sayisini_ASLA_dusurmez(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 3)
    p = _payload(message_id="d3")
    telemetry_consumer._persist_batch([FakeMsg(p, seq=521)])
    _pending_ekle(Session, 2, yas_gun=0, onek="d3dolgu")
    assert _karantina_sayisi(Session) == 3

    for _ in range(5):
        telemetry_consumer._persist_batch([FakeMsg(p, seq=521)])
        assert _karantina_sayisi(Session) == 3, "her yeniden teslimde sayi sabit"

    assert quarantine.get_stats()["unknown_device_quarantine_data_shed_total"] == 0


# --------------------------------------------------------------------------
# D04-D05 — BASARISIZ RECLAIM GERI SARILMALI
#
# Yer acma icin satir silinip yeni satir YAZILAMAZSA, silmeler kalici
# OLMAMALI. Aksi halde net sonuc saf veri kaybidir. Ama ayni partideki
# BILINEN cihaz telemetrisi gereksiz yere kaybedilmemeli.
# --------------------------------------------------------------------------
def test_D04_yer_acilamazsa_silmeler_geri_gelir_bilinen_telemetri_COMMIT_edilir(  # noqa: N803
    Session, monkeypatch
):
    with Session() as db:
        _cihaz(db, code="DEV-VAR")
    # Tavan 2; tablo suresi dolmus 1 replayed + 1 taze pending.
    _tavan(monkeypatch, 2)
    simdi = datetime.now(timezone.utc)
    with Session() as db:
        _satir_ekle(db, status=quarantine.STATUS_REPLAYED, key="eski-replayed",
                    first_seen=simdi - timedelta(days=40),
                    replayed_at=simdi - timedelta(days=40))
        _satir_ekle(db, status=quarantine.STATUS_PENDING, key="taze-pending",
                    first_seen=simdi)
        db.commit()
    quarantine._sayim_onbellegi_bosalt()

    # Yer acma sonrasi upsert'i patlat -> savepoint geri sarilmali.
    def upsert_patlat(*_a, **_k):
        raise quarantine.QuarantineCapacityError("yer acilamadi (sahte)")

    monkeypatch.setattr(quarantine, "_upsert_stmt", upsert_patlat)

    bilinen = FakeMsg(_payload(message_id="d4-k", device_code="DEV-VAR"), seq=531)
    bilinmeyen = FakeMsg(_payload(message_id="d4-u"), seq=532)
    yazilan: list = []
    monkeypatch.setattr(
        telemetry_consumer, "_satirlari_yaz",
        lambda db, satirlar, ts: (yazilan.extend(satirlar) or satirlar, []),
    )

    ok, bad, _w, _o = telemetry_consumer._persist_batch([bilinen, bilinmeyen])

    assert bilinen in ok, "ayni partideki BILINEN telemetri commit edilmeli"
    assert bilinmeyen not in ok, "bilinmeyen mesaj ack EDILMEMELI"
    assert bad == []

    with Session() as db:
        anahtarlar = set(db.scalars(select(UnknownDeviceTelemetry.dedup_key)).all())
    assert anahtarlar == {"eski-replayed", "taze-pending"}, (
        "yer acmak icin silinen satirlar GERI GELMELI"
    )
    assert "d4-u" not in anahtarlar, "bilinmeyen satir yazilmadi"
    assert yazilan, "bilinen cihaz satirlari yazim yoluna girdi"


def test_D05_reclaim_sonrasi_insert_istisnasi_hersey_geri_sarilir(Session, monkeypatch):  # noqa: N803
    """Kapasite disi bir hata (DB arizasi) da ayni izolasyona tabi."""
    _tavan(monkeypatch, 2)
    _pending_ekle(Session, 2, yas_gun=0, onek="d5")
    quarantine.reset_stats_for_test()

    def upsert_patlat(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(quarantine, "_upsert_stmt", upsert_patlat)

    with pytest.raises(RuntimeError):
        telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="d5-u"), seq=541)])

    with Session() as db:
        anahtarlar = set(db.scalars(select(UnknownDeviceTelemetry.dedup_key)).all())
    assert anahtarlar == {"d5-0", "d5-1"}, "silinen satirlar geri gelmeli"
    s = quarantine.get_stats()
    assert s["unknown_device_quarantine_data_shed_total"] == 0
    assert s["unknown_device_quarantine_total"] == 0


# --------------------------------------------------------------------------
# D06-D09 — METRIKLER COMMIT EDILMIS GERCEGI ANLATMALI
# --------------------------------------------------------------------------
def _commit_patlat(monkeypatch):
    class Patlayan(Exception):
        pass

    monkeypatch.setattr(
        telemetry_consumer.SessionLocal.class_, "commit",
        lambda self: (_ for _ in ()).throw(Patlayan()), raising=False,
    )
    return Patlayan


def test_D06_commit_dusunce_data_shed_metrigi_ARTMAZ(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 2)
    _pending_ekle(Session, 2, yas_gun=0, onek="d6")
    quarantine.reset_stats_for_test()
    Patlayan = _commit_patlat(monkeypatch)

    with pytest.raises(Patlayan):
        telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="d6-u"), seq=551)])
    monkeypatch.undo()

    assert _karantina_sayisi(Session) == 2, "DB satirlari geri geldi"
    assert quarantine.get_stats()["unknown_device_quarantine_data_shed_total"] == 0


def test_D07_commit_dusunce_expired_metrigi_ARTMAZ(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 2)
    _pending_ekle(Session, 2, yas_gun=40, onek="d7")  # suresi dolmus
    quarantine.reset_stats_for_test()
    Patlayan = _commit_patlat(monkeypatch)

    with pytest.raises(Patlayan):
        telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="d7-u"), seq=561)])
    monkeypatch.undo()

    assert _karantina_sayisi(Session) == 2
    assert quarantine.get_stats()["unknown_device_quarantine_expired_total"] == 0


def test_D08_commit_dusunce_quarantine_total_ARTMAZ(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 100)
    quarantine.reset_stats_for_test()
    Patlayan = _commit_patlat(monkeypatch)

    with pytest.raises(Patlayan):
        telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="d8-u"), seq=571)])
    monkeypatch.undo()

    assert _karantina_sayisi(Session) == 0
    assert quarantine.get_stats()["unknown_device_quarantine_total"] == 0


def test_D09_basarili_commit_metrikleri_TAM_BIR_KEZ_artirir(Session, monkeypatch):  # noqa: N803
    _tavan(monkeypatch, 2)
    _pending_ekle(Session, 2, yas_gun=0, onek="d9")
    quarantine.reset_stats_for_test()

    ok, _bad, _w, _o = telemetry_consumer._persist_batch(
        [FakeMsg(_payload(message_id="d9-u"), seq=581)]
    )
    assert len(ok) == 1

    s = quarantine.get_stats()
    assert s["unknown_device_quarantine_total"] == 1
    assert s["unknown_device_quarantine_data_shed_total"] == 1
    assert s["unknown_device_quarantine_redelivered_total"] == 0

    # Ayni mesajin yeniden teslimi YENI VERI degildir: `_total` artmaz.
    telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="d9-u"), seq=581)])
    s2 = quarantine.get_stats()
    assert s2["unknown_device_quarantine_total"] == 1, "tekrar yeni satir saymamali"
    assert s2["unknown_device_quarantine_redelivered_total"] == 1
    assert s2["unknown_device_quarantine_data_shed_total"] == 1, "tekrar dusurme yok"


# --------------------------------------------------------------------------
# T05 / T06 — idempotency ve yaris
# --------------------------------------------------------------------------
def test_T05_ayni_mesaj_yeniden_teslimde_TEK_satir(Session):  # noqa: N803
    msg = FakeMsg(_payload(message_id="sabit-1"), seq=77)
    telemetry_consumer._persist_batch([msg])
    telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="sabit-1"), seq=77)])

    with Session() as db:
        satir = db.scalars(select(UnknownDeviceTelemetry)).one()
        assert satir.seen_count == 2, "ikinci teslim yeni satir ACMAZ, sayaci artirir"


def test_message_id_YOKSA_broker_dizisi_dedup_anahtaridir(Session):  # noqa: N803
    """message_id uretilmisse her teslimde DEGISIR; dedup ona baglanamaz.

    Ayni fiziksel mesaj (ayni stream sequence) iki kez teslim edilir; tek
    satir olusmali.
    """
    p1 = _payload()
    p2 = _payload()
    p1.pop("message_id")
    p2.pop("message_id")

    telemetry_consumer._persist_batch([FakeMsg(p1, seq=999)])
    telemetry_consumer._persist_batch([FakeMsg(p2, seq=999)])

    with Session() as db:
        satir = db.scalars(select(UnknownDeviceTelemetry)).one()
        assert satir.dedup_key == "js:TELEMETRY_NORMALIZED:999"
        assert satir.seen_count == 2


def test_T06_ayni_batch_icinde_duplicate_TEK_satir(Session):  # noqa: N803
    """Tek `ON CONFLICT` ifadesi ayni satiri iki kez guncelleyemez; batch
    ici tekillestirme bunu onler."""
    msgs = [FakeMsg(_payload(message_id="dup"), seq=5) for _ in range(3)]
    ok, _bad, _w, _o = telemetry_consumer._persist_batch(msgs)

    assert len(ok) == 3, "ucu de ack edilebilir"
    assert _karantina_sayisi(Session) == 1


def test_T06b_es_zamanli_iki_consumer_TEK_satir(Session):  # noqa: N803
    """Iki ayri transaction ayni mesaji gorur: UNIQUE kisit tek satir birakir."""
    kayit = quarantine.QuarantineEntry(
        consumer_name="c1",
        dedup_key="ayni-anahtar",
        message_id="m",
        device_code="DEV-YOK",
        payload_json="{}",
    )
    with Session() as db1:
        quarantine.quarantine_batch(db1, [kayit])
        db1.commit()
    with Session() as db2:
        quarantine.quarantine_batch(db2, [kayit])
        db2.commit()

    assert _karantina_sayisi(Session) == 1


# --------------------------------------------------------------------------
# T07 — bozuk payload karantinaya GIRMEZ
# --------------------------------------------------------------------------
def test_T07_bozuk_json_karantinaya_girmez(Session):  # noqa: N803
    kirik = FakeMsg(_payload())
    kirik.data = b"{bu json degil"
    ok, bad, _w, _o = telemetry_consumer._persist_batch([kirik])

    assert ok == [] and bad == [kirik], "bozuk mesaj DLQ yoluna gider"
    assert _karantina_sayisi(Session) == 0


def test_T07b_sema_disi_payload_karantinaya_girmez(Session):  # noqa: N803
    """`TelemetryIn` dogrulamasindan gecemeyen mesaj bicim hatasidir."""
    kotu = _payload(signal_key="x" * 500)  # kolon sinirini asan deger
    ok, bad, _w, _o = telemetry_consumer._persist_batch([FakeMsg(kotu, seq=8)])

    assert ok == [] and len(bad) == 1
    assert _karantina_sayisi(Session) == 0


def test_T07c_json_nesne_degilse_DLQ(Session):  # noqa: N803
    """JSON gecerli ama dizi: eskiden AttributeError ile TUM batch'i
    ack'siz birakip sonsuz yeniden teslime sokuyordu."""
    kirik = FakeMsg(_payload())
    kirik.data = b"[1, 2, 3]"
    ok, bad, _w, _o = telemetry_consumer._persist_batch([kirik])

    assert ok == [] and bad == [kirik]
    assert _karantina_sayisi(Session) == 0


# --------------------------------------------------------------------------
# T12 / T13 / T17 / T18 — replay karar mantigi
# --------------------------------------------------------------------------
def _karantinaya_al(Session, **kw):  # noqa: N803
    varsayilan = dict(message_id="rp-1", device_code="DEV-YOK", source_gateway="GW-001")
    varsayilan.update(kw)
    telemetry_consumer._persist_batch([FakeMsg(_payload(**varsayilan), seq=hash(varsayilan["message_id"]) % 10_000)])


def test_T12_cihaz_hala_yoksa_kayit_PENDING_kalir(Session):  # noqa: N803
    _karantinaya_al(Session)
    with Session() as db:
        sonuc = unknown_device_replay.replay(db, device_code="DEV-YOK")

    assert sonuc.replayed == 0
    assert sonuc.still_pending == 1
    assert sonuc.errors == {"device_not_found": 1}

    with Session() as db:
        satir = db.scalars(select(UnknownDeviceTelemetry)).one()
        assert satir.status == quarantine.STATUS_PENDING
        assert satir.replay_attempts == 1
        assert satir.last_replay_error == "device_not_found"
        assert satir.payload_json, "payload KAYBOLMAMALI"


def test_T18_baska_gateway_cihazina_replay_EDILMEZ(Session):  # noqa: N803
    """GW-001'in olcumu, ayni kodla GW-002'ye tanimlanan cihaza yazilamaz."""
    _karantinaya_al(Session, source_gateway="GW-001")
    with Session() as db:
        _cihaz(db, code="DEV-YOK", gateway_code="GW-002")
        sonuc = unknown_device_replay.replay(db, device_code="DEV-YOK")

    assert sonuc.replayed == 0
    assert sonuc.errors == {"gateway_mismatch": 1}

    with Session() as db:
        satir = db.scalars(select(UnknownDeviceTelemetry)).one()
        assert satir.status == quarantine.STATUS_PENDING
        assert satir.last_replay_error == "gateway_mismatch"


def test_T17_gateway_suzgeci_yalnizca_kendi_kayitlarini_alir(Session):  # noqa: N803
    _karantinaya_al(Session, message_id="a", device_code="D-A", source_gateway="GW-001")
    _karantinaya_al(Session, message_id="b", device_code="D-B", source_gateway="GW-002")

    with Session() as db:
        sonuc = unknown_device_replay.replay(db, gateway_code="GW-001")

    assert sonuc.requested == 1


def test_zaten_islenmis_mesaj_yeniden_YAZILMAZ(Session):  # noqa: N803
    """Canli yol mesaji karantinadan sonra islemisse replay duplicate uretmez."""
    _karantinaya_al(Session, message_id="rp-x")
    with Session() as db:
        _cihaz(db, code="DEV-YOK", gateway_code="GW-001")
        db.add(
            ProcessedMessage(
                consumer_name=telemetry_consumer.CONSUMER_NAME,
                message_id="rp-x",
                processed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        sonuc = unknown_device_replay.replay(db, device_code="DEV-YOK")

    assert sonuc.replayed == 0
    assert sonuc.skipped_already_processed == 1
    with Session() as db:
        satir = db.scalars(select(UnknownDeviceTelemetry)).one()
        assert satir.status == quarantine.STATUS_REPLAYED


def test_T13_replay_hatasi_payloadi_KORUR(Session, monkeypatch):  # noqa: N803
    _karantinaya_al(Session)
    with Session() as db:
        _cihaz(db, code="DEV-YOK", gateway_code="GW-001")

    def patla(*_a, **_k):
        raise RuntimeError("islem hatasi")

    monkeypatch.setattr(telemetry_consumer, "process_valid_telemetry", patla)

    with Session() as db:
        sonuc = unknown_device_replay.replay(db, device_code="DEV-YOK")

    assert sonuc.replayed == 0
    with Session() as db:
        satir = db.scalars(select(UnknownDeviceTelemetry)).one()
        assert satir.status == quarantine.STATUS_PENDING
        assert "islem hatasi" in (satir.last_replay_error or "")
        assert json.loads(satir.payload_json)["device_code"] == "DEV-YOK"


# --------------------------------------------------------------------------
# T19 / T20 — retention (deterministik)
# --------------------------------------------------------------------------
def _satir_ekle(db, *, status, key, first_seen, replayed_at=None):  # noqa: ANN001
    db.add(
        UnknownDeviceTelemetry(
            consumer_name="c",
            dedup_key=key,
            message_id=key,
            device_code="D",
            payload_json="{}",
            reason=quarantine.REASON_DEVICE_NOT_FOUND,
            status=status,
            seen_count=1,
            first_seen_at=first_seen,
            last_seen_at=first_seen,
            replayed_at=replayed_at,
            replay_attempts=0,
            created_at=first_seen,
            updated_at=first_seen,
        )
    )


def test_T19_replayed_kayitlar_suresi_dolunca_silinir(Session):  # noqa: N803
    simdi = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    with Session() as db:
        _satir_ekle(db, status=quarantine.STATUS_REPLAYED, key="eski",
                    first_seen=simdi - timedelta(days=40),
                    replayed_at=simdi - timedelta(days=30))
        _satir_ekle(db, status=quarantine.STATUS_REPLAYED, key="yeni",
                    first_seen=simdi - timedelta(days=2),
                    replayed_at=simdi - timedelta(days=1))
        db.commit()
        silinen = quarantine.purge(db, now=simdi)
        db.commit()

    assert silinen["replayed"] == 1
    with Session() as db:
        kalan = db.scalars(select(UnknownDeviceTelemetry.dedup_key)).all()
        assert kalan == ["yeni"]


def test_T20_pending_kayitlar_daha_UZUN_tutulur(Session):  # noqa: N803
    """Pending payload kurtarilabilir veridir: penceresi replayed'den genis."""
    simdi = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    with Session() as db:
        # replayed esiginin (7 gun) otesinde ama pending esiginin (30) berisinde
        _satir_ekle(db, status=quarantine.STATUS_PENDING, key="orta",
                    first_seen=simdi - timedelta(days=20))
        _satir_ekle(db, status=quarantine.STATUS_PENDING, key="cok-eski",
                    first_seen=simdi - timedelta(days=40))
        db.commit()
        silinen = quarantine.purge(db, now=simdi)
        db.commit()

    assert silinen["pending"] == 1
    with Session() as db:
        assert db.scalars(select(UnknownDeviceTelemetry.dedup_key)).all() == ["orta"]


# --------------------------------------------------------------------------
# T22 / T23 — gozlemlenebilirlik
# --------------------------------------------------------------------------
def test_T22_uyari_logu_cihaz_basina_hiz_sinirli(monkeypatch):
    monkeypatch.setattr(
        quarantine.settings, "unknown_telemetry_log_interval_sec", 300, raising=False
    )
    quarantine.reset_stats_for_test()

    assert quarantine.should_notify("DEV-A") is True
    assert quarantine.should_notify("DEV-A") is False, "1 Hz'de log seli olmamali"
    assert quarantine.should_notify("DEV-B") is True, "baska cihaz ayri sayilir"


def test_T23_metrikler_dogru(Session):  # noqa: N803
    telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="m1"), seq=1)])
    telemetry_consumer._persist_batch(
        [FakeMsg(_payload(message_id="m2", device_code="DEV-YOK-2"), seq=2)]
    )

    with Session() as db:
        anlik = quarantine.health_snapshot(db)

    assert anlik["unknown_device_quarantine_total"] == 2
    assert anlik["unknown_device_quarantine_pending"] == 2
    assert anlik["unknown_device_quarantine_rows"] == 2
    assert anlik["unknown_device_quarantine_capacity_full"] is False
    assert anlik["oldest_pending_age_sec"] is not None
    assert anlik["oldest_pending_age_sec"] >= 0


def test_oldest_pending_age_bekleyen_yoksa_None(Session):  # noqa: N803
    with Session() as db:
        assert quarantine.health_snapshot(db)["oldest_pending_age_sec"] is None


# --------------------------------------------------------------------------
# Sozlesme korumasi — ack sirasi kodda kalmali
# --------------------------------------------------------------------------
def test_ack_sozlesmesi_kaynakta_korunuyor():
    """`ok_msgs.extend(bilinmeyen_msgs)` yalnizca basarili yazimin
    `else` dalinda olmali.

    Bu kontrol MUTATION A icindir: yazimdan ONCE ack listesine eklemek
    testlerin geri kalanina gorunmeden gecebilirdi (SQLite'ta commit
    neredeyse hic patlamaz), ama uretimde sessiz veri kaybi demek.
    """
    import inspect

    kaynak = inspect.getsource(telemetry_consumer._persist_batch)
    idx_try = kaynak.index("quarantine.quarantine_batch(")
    idx_extend = kaynak.index("ok_msgs.extend(bilinmeyen_msgs)")
    assert idx_try < idx_extend, "ack listesi persist'ten ONCE doldurulamaz"
    assert "else:" in kaynak[idx_try:idx_extend], (
        "extend, basarili yazimin `else` dalinda olmali"
    )
    # Yer acma geri sarilabilmeli: karantina denemesi savepoint icinde olmali.
    assert "begin_nested()" in kaynak[:idx_try], (
        "karantina denemesi SAVEPOINT icinde olmali — aksi halde basarisiz bir "
        "kapasite denemesinin sildigi satirlar outer commit ile KALICI olur"
    )
    # Metrikler commit'ten SONRA islenmeli.
    idx_commit = kaynak.index("db.commit()")
    idx_metrik = kaynak.index("karantina_sonucu.apply_metrics()")
    assert idx_commit < idx_metrik, (
        "metrikler commit'ten ONCE islenemez — geri sarilan bir transaction "
        "gerceklesmemis veri kaybi raporlardi"
    )
