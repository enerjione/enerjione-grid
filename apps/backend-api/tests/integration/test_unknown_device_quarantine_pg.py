"""Bilinmeyen cihaz karantinasi + replay — GERCEK PostgreSQL 16 / TimescaleDB.

NEDEN GERCEK VERITABANI SART
----------------------------
Telemetri yazim yolu (`_tek_gecis_yaz`) PostgreSQL'e OZGUDUR: `COPY ... FROM
STDIN` ve psycopg2 `execute_values`. SQLite'ta HIC kosmaz. Yani "replay
gercekten telemetri yaziyor mu", "ikinci replay duplicate uretiyor mu" ve
crash pencereleri ancak burada kanitlanabilir.

Ayrica migration'in `create_all` ile CAKISMADIGI (2.100.0'da yasanan
"model create_all + migration ayni nesneyi kurar" arizasi) yalnizca gercek
Postgres'te gorulur.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device import Device
from app.models.gateway import Gateway
from app.models.processed_message import ProcessedMessage
from app.models.telemetry import Telemetry
from app.models.unknown_device_telemetry import UnknownDeviceTelemetry
from app.services import telemetry_consumer
from app.services import unknown_device_quarantine as quarantine
from app.services import unknown_device_replay
from tests.integration import pg_target

pytestmark = pytest.mark.integration

PG_URL = pg_target.pg_url()
if not PG_URL:
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)

# Ad `e1_test_` onekini TASIMAK ZORUNDA: migration guard'i (bkz. pg_target)
# baska hicbir hedefe izin vermez.
DB_ADI = pg_target.yeni_db_adi("unknown_quar")


def _url(ad: str) -> str:
    return pg_target.url_for(ad)


def _db_olustur(ad: str) -> None:
    yonetim = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with yonetim.connect() as c:
        c.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{ad}' AND pid <> pg_backend_pid()"
        ))
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
        c.execute(text(f'CREATE DATABASE "{ad}" TEMPLATE template0'))
    yonetim.dispose()
    # Guard'in en guclu katmani: yalnizca BIZIM olusturdugumuz DB'ye
    # migration kosturulabilir.
    pg_target.kaydet_olusturuldu(ad)


def _db_sil(ad: str) -> None:
    yonetim = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with yonetim.connect() as c:
        c.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{ad}' AND pid <> pg_backend_pid()"
        ))
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
    yonetim.dispose()
    pg_target.unut(ad)


@pytest.fixture()
def pg(monkeypatch):
    _db_olustur(DB_ADI)
    eng = create_engine(_url(DB_ADI))
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True, expire_on_commit=False)

    kur = Session()
    kur.add(Gateway(code="GW-1", name="S1", host="10.0.0.1", listen_port=20000,
                    token="t1", is_active=True))
    kur.add(Gateway(code="GW-2", name="S2", host="10.0.0.2", listen_port=20001,
                    token="t2", is_active=True))
    kur.commit()
    kur.add(Device(code="BILINEN", name="B", gateway_code="GW-1",
                   ip_address="10.0.0.50", latitude=39.0, longitude=35.0))
    kur.commit()
    kur.close()

    monkeypatch.setattr(telemetry_consumer, "SessionLocal", Session)
    quarantine.reset_stats_for_test()

    yield Session

    eng.dispose()
    _db_sil(DB_ADI)


class FakeMsg:
    def __init__(self, payload: dict, *, seq: int, subject: str = "e1.telemetry.normalized.gw1"):
        self.data = json.dumps(payload, default=str).encode()
        self.subject = subject
        self.metadata = type(
            "M", (), {"stream": "TELEMETRY_NORMALIZED",
                      "sequence": type("S", (), {"stream": seq})()}
        )()


def _payload(**kw) -> dict:
    veri = {
        "message_id": "m-1",
        "device_code": "SONRADAN",
        "signal_key": "master.actual_voltage",
        "value": 230.0,
        "quality": "good",
        "source_gateway": "GW-1",
        "source_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    veri.update(kw)
    return veri


def _cihaz_ekle(Session, code="SONRADAN", gateway_code="GW-1"):  # noqa: N803
    db = Session()
    db.add(Device(code=code, name=code, gateway_code=gateway_code,
                  ip_address="10.0.0.60", latitude=39.1, longitude=35.1))
    db.commit()
    db.close()


def _say(Session, model) -> int:  # noqa: N803
    db = Session()
    try:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)
    finally:
        db.close()


# --------------------------------------------------------------------------
# T01 / T24 — bilinen cihaz hizli yolu degismedi
# --------------------------------------------------------------------------
def test_T01_bilinen_cihaz_normal_telemetri_karantina_YOK(pg):
    msg = FakeMsg(_payload(device_code="BILINEN", message_id="k-1"), seq=1)
    ok, bad, _ws, _out = telemetry_consumer._persist_batch([msg])

    assert bad == [] and ok == [msg]
    assert _say(pg, Telemetry) == 1
    assert _say(pg, UnknownDeviceTelemetry) == 0, "bilinen cihaz karantinaya girmez"
    assert _say(pg, ProcessedMessage) == 1, "normal dedup satiri yazilir"


def test_T24_bilinen_cihaz_yolu_karantina_sorgusu_KOSMAZ(pg, monkeypatch):
    """Hizli yol regresyonu: quarantine kodu unknown dali disinda calismamali."""
    cagrildi = {"n": 0}

    def sayarak(*a, **k):
        cagrildi["n"] += 1
        raise AssertionError("bilinen cihaz yolunda karantina yazimi olmamali")

    monkeypatch.setattr(quarantine, "quarantine_batch", sayarak)

    telemetry_consumer._persist_batch(
        [FakeMsg(_payload(device_code="BILINEN", message_id="k-2"), seq=2)]
    )
    assert cagrildi["n"] == 0


# --------------------------------------------------------------------------
# T09 / T10 / T11 — cihaz sonradan olusur, replay, tekrar replay
# --------------------------------------------------------------------------
def test_T09_T10_T11_cihaz_sonradan_olusur_replay_ve_tekrar(pg):
    # 1-2) bilinmeyen mesaj -> karantina
    telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="r-1"), seq=11)])
    assert _say(pg, UnknownDeviceTelemetry) == 1
    assert _say(pg, Telemetry) == 0

    # 3) cihaz sonradan tanimlanir
    _cihaz_ekle(pg)

    # 4-6) replay -> telemetri olusur, kayit replayed olur
    db = pg()
    sonuc = unknown_device_replay.replay(db, device_code="SONRADAN")
    db.close()

    assert sonuc.replayed == 1
    assert _say(pg, Telemetry) == 1, "T10: replay normal telemetri uretir"
    assert _say(pg, ProcessedMessage) == 1

    db = pg()
    satir = db.scalars(select(UnknownDeviceTelemetry)).one()
    assert satir.status == quarantine.STATUS_REPLAYED
    assert satir.replayed_at is not None
    db.close()

    # 7-8) ikinci replay -> duplicate telemetri YOK
    db = pg()
    sonuc2 = unknown_device_replay.replay(db, device_code="SONRADAN")
    db.close()

    assert sonuc2.requested == 0, "replayed kayit tekrar secilmez"
    assert _say(pg, Telemetry) == 1, "T11: duplicate telemetri 0"


def test_T11b_pending_isaretlense_bile_ikinci_replay_duplicate_URETMEZ(pg):
    """Crash sonrasi kayit yanlislikla pending kalmis olsa bile telemetri
    dedup defteri ikinci yazimi engeller (T16'nin ikinci savunma hatti)."""
    telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="r-2"), seq=12)])
    _cihaz_ekle(pg)

    db = pg()
    unknown_device_replay.replay(db, device_code="SONRADAN")
    db.close()
    assert _say(pg, Telemetry) == 1

    # Kaydi zorla pending'e cevir: "telemetri yazildi ama durum guncellenmedi"
    db = pg()
    satir = db.scalars(select(UnknownDeviceTelemetry)).one()
    satir.status = quarantine.STATUS_PENDING
    satir.replayed_at = None
    db.commit()
    db.close()

    db = pg()
    sonuc = unknown_device_replay.replay(db, device_code="SONRADAN")
    db.close()

    assert sonuc.skipped_already_processed == 1
    assert sonuc.replayed == 0
    assert _say(pg, Telemetry) == 1, "T16: duplicate telemetri uretilmedi"


# --------------------------------------------------------------------------
# T14 / T15 — crash pencereleri
# --------------------------------------------------------------------------
def test_T14_commit_ONCESI_crash_satir_BIRAKMAZ(pg, monkeypatch):
    """Karantina insert edildi ama commit edilmeden surec olurse: satir YOK,
    mesaj ack edilmemis olur -> JetStream yeniden teslim eder."""
    gercek_commit = pg().commit

    class Patlayan(Exception):
        pass

    def commit_patlat(self):
        raise Patlayan("commit oncesi crash")

    monkeypatch.setattr(
        telemetry_consumer.SessionLocal.class_, "commit", commit_patlat, raising=False
    )

    with pytest.raises(Patlayan):
        telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="c-1"), seq=21)])

    monkeypatch.undo()
    assert _say(pg, UnknownDeviceTelemetry) == 0, "commit edilmeyen satir kalici olmaz"
    del gercek_commit


def test_T15_commit_SONRASI_ack_oncesi_crash_duplicate_URETMEZ(pg):
    """Commit oldu, ack edilmeden crash: mesaj yeniden teslim edilir ve
    IKINCI bir karantina satiri ACILMAZ (seen_count artar)."""
    p = _payload(message_id="c-2")
    ok1, _b, _w, _o = telemetry_consumer._persist_batch([FakeMsg(p, seq=22)])
    assert len(ok1) == 1
    assert _say(pg, UnknownDeviceTelemetry) == 1

    # ack yapilmadi varsayimi -> ayni mesaj yeniden teslim
    telemetry_consumer._persist_batch([FakeMsg(p, seq=22)])

    assert _say(pg, UnknownDeviceTelemetry) == 1, "duplicate karantina satiri YOK"
    db = pg()
    assert db.scalars(select(UnknownDeviceTelemetry)).one().seen_count == 2
    db.close()


def test_T16_replay_commit_oncesi_crash_telemetri_BIRAKMAZ(pg, monkeypatch):
    """Replay ortasinda crash: ne telemetri ne durum degisikligi kalir."""
    telemetry_consumer._persist_batch([FakeMsg(_payload(message_id="c-3"), seq=23)])
    _cihaz_ekle(pg)

    class Patlayan(Exception):
        pass

    db = pg()
    monkeypatch.setattr(type(db), "commit", lambda self: (_ for _ in ()).throw(Patlayan()))
    with pytest.raises(Patlayan):
        unknown_device_replay.replay(db, device_code="SONRADAN")
    monkeypatch.undo()
    db.rollback()
    db.close()

    assert _say(pg, Telemetry) == 0, "commit edilmemis telemetri kalici olmaz"
    db = pg()
    assert db.scalars(select(UnknownDeviceTelemetry)).one().status == quarantine.STATUS_PENDING
    db.close()


# --------------------------------------------------------------------------
# C09 — kapasite baskisi altinda es zamanlilik (GERCEK advisory lock)
# --------------------------------------------------------------------------
def _pending_doldur(Session, adet: int) -> None:  # noqa: N803
    from app.models.unknown_device_telemetry import UnknownDeviceTelemetry

    simdi = datetime.now(timezone.utc)
    db = Session()
    for i in range(adet):
        t = simdi - timedelta(seconds=adet - i)
        db.add(UnknownDeviceTelemetry(
            consumer_name="c", dedup_key=f"dolgu-{i}", message_id=f"dolgu-{i}",
            device_code="D", payload_json="{}",
            reason=quarantine.REASON_DEVICE_NOT_FOUND,
            status=quarantine.STATUS_PENDING, seen_count=1,
            first_seen_at=t, last_seen_at=t, replay_attempts=0,
            created_at=t, updated_at=t,
        ))
    db.commit()
    db.close()
    quarantine._sayim_onbellegi_bosalt()


def test_C09_es_zamanli_kapasite_baskisi_FAZLADAN_silmez(pg, monkeypatch):
    """Iki worker ayni anda tavana carparsa ASIRI veri dusurulmemeli.

    Kilitsiz davranista ikisi de "N satir eksigim var" deyip ikisi de en eski
    N satiri siler -> 2N kayip. Transaction-kapsamli advisory kilit reclaim
    yolunu seri hale getirir; ikinci worker kilidi aldiginda tabloyu YENIDEN
    sayar ve yer acilmissa hic silmez.
    """
    from app.models.unknown_device_telemetry import UnknownDeviceTelemetry

    TAVAN = 20
    monkeypatch.setattr(
        quarantine.settings, "unknown_telemetry_max_rows", TAVAN, raising=False
    )
    monkeypatch.setattr(
        quarantine.settings, "unknown_telemetry_count_cache_sec", 0, raising=False
    )
    _pending_doldur(pg, TAVAN)

    hatalar: list = []
    bariyer = threading.Barrier(2, timeout=30)

    def worker(no: int) -> None:
        try:
            db = pg()
            try:
                kayit = quarantine.QuarantineEntry(
                    consumer_name="c", dedup_key=f"yeni-{no}",
                    message_id=f"yeni-{no}", device_code="D-YENI",
                    payload_json="{}",
                )
                bariyer.wait()
                sonuc = quarantine.quarantine_batch(db, [kayit])
                db.commit()
                # METRIK SOZLESMESI: sayaclar commit BASARILI olduktan sonra
                # cagiran tarafindan uygulanir (bkz. QuarantineOutcome).
                sonuc.apply_metrics()
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            hatalar.append(exc)

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start(); t2.start()
    t1.join(timeout=60); t2.join(timeout=60)

    assert not hatalar, f"es zamanli reclaim hata verdi (deadlock?): {hatalar}"
    assert not t1.is_alive() and not t2.is_alive(), "deadlock: thread'ler bitmedi"

    db = pg()
    toplam = int(db.scalar(select(func.count()).select_from(UnknownDeviceTelemetry)) or 0)
    yeniler = int(
        db.scalar(
            select(func.count())
            .select_from(UnknownDeviceTelemetry)
            .where(UnknownDeviceTelemetry.device_code == "D-YENI")
        ) or 0
    )
    db.close()

    assert yeniler == 2, "iki yeni kayit da kalici olmali"
    assert toplam == TAVAN, f"tavan ciddi bicimde asilmamali/altina inilmemeli: {toplam}"
    shed = quarantine.get_stats()["unknown_device_quarantine_data_shed_total"]
    assert shed == 2, f"toplam 2 satir dusurulmeliydi, {shed} dusuruldu (asiri silme)"


# --------------------------------------------------------------------------
# T18 — gateway izolasyonu (gercek FK ile)
# --------------------------------------------------------------------------
def test_T18_baska_gatewayin_cihazina_replay_EDILMEZ(pg):
    telemetry_consumer._persist_batch(
        [FakeMsg(_payload(message_id="g-1", source_gateway="GW-1"), seq=31)]
    )
    _cihaz_ekle(pg, code="SONRADAN", gateway_code="GW-2")

    db = pg()
    sonuc = unknown_device_replay.replay(db, device_code="SONRADAN")
    db.close()

    assert sonuc.replayed == 0
    assert sonuc.errors == {"gateway_mismatch": 1}
    assert _say(pg, Telemetry) == 0, "baska sahanin olcumu yazilmadi"


# --------------------------------------------------------------------------
# T30 — migration: temiz kurulum ve yukseltme yollarinin IKISI de
# --------------------------------------------------------------------------
def _alembic_cfg(url: str, monkeypatch):  # noqa: ANN001
    """Dogrulanmis hedefe bagli Alembic yapilandirmasi.

    Tum guard ve yan-etki ele alma `tests/integration/pg_target` icinde tek
    kaynakta duruyor (yanlis hedef + logger susturma). Burada yalnizca ona
    yonlendiriyoruz ki iki ayri kopya sessizce ayrismasin.
    """
    return pg_target.alembic_config(url, monkeypatch)


def _tablo_var_mi(url: str, ad: str) -> bool:
    from sqlalchemy import inspect as _inspect

    eng = create_engine(url)
    try:
        return _inspect(eng).has_table(ad)
    finally:
        eng.dispose()


def _tabloyu_dusur(url: str, ad: str) -> None:
    eng = create_engine(url)
    with eng.connect() as c:
        c.execute(text(f'DROP TABLE IF EXISTS "{ad}"'))
        c.commit()
    eng.dispose()


# NEDEN ZINCIR SIFIRDAN KOSULMUYOR
# --------------------------------
# Migration 0002 `ALTER TYPE ... ADD VALUE` icin baglantiyi AUTOCOMMIT'e
# ceker; alembic'in acik transaction'i yuzunden bos bir DB'de tum zinciri
# TEK surecte oynatmak `InvalidRequestError` verir. Bu bir uretim yolu da
# DEGILDIR: temiz kurulum `stamp <taban>` + `upgrade head` yapar (bkz.
# scripts/migrate_db.py) ve semayi 0072'den alir; 0001-0071 zincirini hic
# kosturmaz. Asagidaki iki test uretimde GERCEKTEN var olan iki yolu
# taklit eder.


def test_T30_yukseltme_yolu_0070_den_head_e(monkeypatch):
    """Sahadaki kurulum (0070) -> `upgrade head` tabloyu KURAR."""
    from alembic import command

    ad = f"{DB_ADI}_mig"
    _db_olustur(ad)
    try:
        url = _url(ad)
        eng = create_engine(url)
        Base.metadata.create_all(eng)
        eng.dispose()
        # 0070 durumunu taklit et: yeni tablo HENUZ yok.
        _tabloyu_dusur(url, "unknown_device_telemetry")

        cfg = _alembic_cfg(url, monkeypatch)
        command.stamp(cfg, "0070")
        assert not _tablo_var_mi(url, "unknown_device_telemetry")

        command.upgrade(cfg, "head")
        assert _tablo_var_mi(url, "unknown_device_telemetry")

        command.downgrade(cfg, "0070")
        assert not _tablo_var_mi(url, "unknown_device_telemetry")
    finally:
        _db_sil(ad)


def test_T30b_temiz_kurulum_yolu_create_all_SONRASI_migration_PATLAMAZ(monkeypatch):
    """2.100.0 REGRESYONUNUN TEKRARI ENGELLENIYOR.

    Temiz kurulum/restore `create_all` ile tabloyu MODELDEN kurar. Eger o
    kurulum eski bir revizyonda damgalanmissa (restore senaryosu) 0071 yine
    kosar ve korumasiz bir `create_table` "already exists" ile duserdi —
    RESTORE TAMAMLANAMAZDI.
    """
    from alembic import command

    ad = f"{DB_ADI}_clean"
    _db_olustur(ad)
    try:
        url = _url(ad)
        eng = create_engine(url)
        Base.metadata.create_all(eng)  # tablo MODELDEN geldi, ZATEN VAR
        eng.dispose()

        cfg = _alembic_cfg(url, monkeypatch)
        command.stamp(cfg, "0070")
        assert _tablo_var_mi(url, "unknown_device_telemetry")

        # Tablo zaten dururken 0071 kosar — patlamamali.
        command.upgrade(cfg, "head")
        assert _tablo_var_mi(url, "unknown_device_telemetry")
    finally:
        _db_sil(ad)


def test_T29_alembic_tek_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = list(script.get_heads())
    # Head SABIT DEGIL: her yeni migration onu ilerletir. Iddia "tek head"
    # olmasidir — dallanma (coklu head) migration'lari sessizce atlatir.
    assert len(heads) == 1, f"tek head olmali, bulunan: {heads}"
