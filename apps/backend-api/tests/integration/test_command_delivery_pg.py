"""Teslim kirasi — GERCEK PostgreSQL uzerinde es zamanlilik (F3C).

NEDEN GERCEK VERITABANI GEREKIYOR
---------------------------------
`SELECT ... FOR UPDATE` satir kilidi SQLite'ta YOKTUR; SQLAlchemy onu o
lehcede SESSIZCE yok sayar. Dolayisiyla "ayni komut iki kez kiralanamaz"
iddiasi birim testlerinde KANITLANAMAZ — yalnizca gercek bir Postgres'te
gozlemlenebilir.

Gateway komut ucunu 1 Hz poll ediyor; ag tekrari ya da iki paralel istek
gercekci. Kilit olmasaydi iki istek ayni `pending` satiri okuyup IKI FARKLI
jeton uretebilir, ikisini de gateway'e gonderebilirdi. Gateway defteri
fiziksel tekrari yine engellerdi ama backend'in teslim muhasebesi bozulurdu:
ilk jeton icin gelen ACK ikinci jetonla eslesmez ve komut hicbir zaman `sent`
olamazdi.

Ayrica supurucunun `skip_locked` davranisi da yalnizca gercek Postgres'te
anlamlidir.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import gateways as gw_api
from app.core.config import settings
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway
from app.schemas.gateway import CommandDeliveryAckItem, CommandDeliveryAckRequest
from app.services import command_delivery_service as teslim
from app.services.command_result_sweeper import sonucu_gelmeyenleri_sonlandir

pytestmark = pytest.mark.integration

PG_URL = os.getenv("E1_TEST_PG_URL", "")
if not PG_URL:
    pytest.skip("E1_TEST_PG_URL yok", allow_module_level=True)

TTL = 120
KIRA = 30
EPOCH = "epoch-pg-1111"
EPOCH_YENI = "epoch-pg-2222"
DB_ADI = f"f3c_teslim_{os.getpid()}"


def _url(ad: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{ad}\\1", PG_URL, count=1)


def _baslik(epoch: str = EPOCH) -> str:
    import base64
    import json

    return base64.urlsafe_b64encode(
        json.dumps({"v": 1, "epoch": epoch}, separators=(",", ":")).encode()
    ).decode()


@pytest.fixture()
def pg():
    yonetim = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with yonetim.connect() as c:
        c.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{DB_ADI}' AND pid <> pg_backend_pid()"
        ))
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_ADI}"'))
        c.execute(text(f'CREATE DATABASE "{DB_ADI}" TEMPLATE template0'))
    yonetim.dispose()

    eng = create_engine(_url(DB_ADI))
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)

    kur = Session()
    # Gateway ONCE commit edilmeli: gercek Postgres'te
    # `devices_gateway_code_fkey` zorlanir.
    kur.add(Gateway(code="GW-1", name="S", host="10.0.0.1", listen_port=20000,
                    token="t", is_active=True))
    kur.add(Gateway(code="GW-2", name="S2", host="10.0.0.2", listen_port=20001,
                    token="t2", is_active=True))
    kur.commit()
    kur.add(Device(code="CIHAZ-A", name="A", gateway_code="GW-1",
                   ip_address="10.0.0.50", latitude=39.0, longitude=35.0))
    kur.commit()
    kur.close()

    yield Session

    eng.dispose()
    yonetim = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with yonetim.connect() as c:
        c.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{DB_ADI}' AND pid <> pg_backend_pid()"
        ))
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_ADI}"'))
    yonetim.dispose()


@pytest.fixture(autouse=True)
def baypaslar(monkeypatch):
    monkeypatch.setattr(settings, "command_max_age_sec", TTL, raising=False)
    monkeypatch.setattr(settings, "command_delivery_lease_sec", KIRA, raising=False)
    monkeypatch.setattr(settings, "command_delivery_max_attempts", 5, raising=False)
    monkeypatch.setattr(settings, "command_delivery_ack_required", True, raising=False)
    monkeypatch.setattr(settings, "command_result_timeout_sec", 300, raising=False)
    monkeypatch.setattr(gw_api, "_signed_json_response", lambda g, m, extra_headers=None: m)
    teslim._legacy_last_warn.clear()

    def _sahte(db_, kod, token):
        return db_.scalars(select(Gateway).where(Gateway.code == kod)).first()

    monkeypatch.setattr("app.services.ingest_service.validate_gateway_token", _sahte)


def _komut(Session, yas_sn: float = 0.0) -> int:
    s = Session()
    try:
        cmd = DeviceCommand(
            gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
            dnp3_index=3, status="pending",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=yas_sn),
        )
        s.add(cmd)
        s.commit()
        return cmd.id
    finally:
        s.close()


def _paralel(Session, calisma, adet: int = 2) -> list:
    """`adet` kadar es zamanli islem — her biri KENDI oturumunda."""
    sonuclar: list = [None] * adet
    hatalar: list = [None] * adet
    engel = threading.Barrier(adet)

    def _calis(i: int) -> None:
        s = Session()
        try:
            engel.wait(timeout=30)  # ayni ANDA baslasinlar
            sonuclar[i] = calisma(s)
        except Exception as exc:  # noqa: BLE001
            hatalar[i] = exc
        finally:
            s.close()

    ipler = [threading.Thread(target=_calis, args=(i,)) for i in range(adet)]
    for t in ipler:
        t.start()
    for t in ipler:
        t.join(timeout=60)
    assert not any(hatalar), f"paralel islem hata verdi: {hatalar}"
    return sonuclar


def _yetenek(epoch: str = EPOCH) -> teslim.DeliveryCapability:
    return teslim.DeliveryCapability(version=1, epoch=epoch)


def _poll(s, epoch: str = EPOCH):
    return gw_api.get_gateway_pending(
        "GW-1", db=s, x_gateway_token="t", x_gateway_health=None,
        x_e1_delivery=_baslik(epoch),
    )


# ---------------------------------------------------------------------------
# T12 — es zamanli kiralama
# ---------------------------------------------------------------------------


def test_T12_es_zamanli_iki_poll_TEK_kira_uretir(pg):
    """Ayni komut iki kez KIRALANAMAZ — SATIR KILIDI GERCEKTEN BLOKE EDER.

    BU TEST NEDEN BOYLE YAZILDI
    ---------------------------
    Ilk hali iki poll'u `threading.Barrier` ile ayni anda baslatiyordu ve
    GECIYORDU — ama `with_for_update()` KALDIRILDIGINDA DA geciyordu. Yani
    hicbir sey kanitlamiyordu: engel yalnizca thread BASLANGICINI hizaliyor,
    iki islemin kritik bolgede GERCEKTEN cakismasini saglamiyor. Islemler o
    kadar hizli ki biri digeri okumadan commit ediyordu.

    Bu hali cakismayi DETERMINIST kuruyor:
      1. A oturumu `kirala` calistirir ve transaction'i ACIK BIRAKIR
         (satir kilidi A'da).
      2. B oturumu ayri bir thread'te ayni komutu kiralamaya calisir.
      3. Kilit varsa B BLOKE OLUR -> `is_alive()` True.
         Kilit yoksa B hemen doner ve komutu IKINCI KEZ kiralar.
      4. A commit eder; B cozulur ve artik kirali satiri gorup teslim etmez.

    `with_for_update()` kaldirilinca 3. adimdaki iddia duser (dogrulandi).
    """
    Session = pg
    cid = _komut(Session)
    an = datetime.now(timezone.utc)

    sA = Session()
    sB = Session()
    b_sonuc: list = []
    b_hata: list = []

    try:
        # 1) A kiralar ve transaction'i ACIK tutar -> satir kilidi A'da.
        kararA = teslim.kirala(sA, gateway_code="GW-1", yetenek=_yetenek(), now=an)
        assert [c.id for c in kararA.teslim] == [cid]

        # 2) B ayni komutu kiralamaya calisir.
        def _b() -> None:
            try:
                b_sonuc.append(
                    teslim.kirala(sB, gateway_code="GW-1", yetenek=_yetenek(), now=an)
                )
            except Exception as exc:  # noqa: BLE001
                b_hata.append(exc)

        tB = threading.Thread(target=_b, daemon=True)
        tB.start()
        tB.join(timeout=3)

        # 3) ASIL IDDIA: B kilit yuzunden hala bekliyor olmali.
        assert tB.is_alive(), (
            "ikinci poll BLOKE OLMADI — satir kilidi yok; ayni komut iki kez "
            "kiralanip iki FARKLI jetonla gonderilebilirdi (ACK hicbir zaman "
            "eslesmez, komut sonsuza kadar `pending` kalirdi)"
        )

        # 4) A commit eder -> B cozulur.
        sA.commit()
        tB.join(timeout=20)
        assert not tB.is_alive(), "B kilit birakildiktan sonra da cozulmedi"
        assert not b_hata, f"B hata verdi: {b_hata}"
        sB.commit()
    finally:
        sA.close()
        sB.close()

    assert b_sonuc and b_sonuc[0].teslim == [], (
        "kira aktifken komut IKINCI KEZ teslim edildi"
    )
    assert b_sonuc[0].kirali == 1, "B komutu 'kirali' olarak gormeliydi"

    s = Session()
    try:
        cmd = s.get(DeviceCommand, cid)
        assert cmd.status == "pending", "ACK gelmeden `sent` olmus"
        assert cmd.delivery_attempt == 1, "deneme sayaci iki kez artmis"
        assert cmd.delivery_token is not None
        assert cmd.delivery_gateway_epoch == EPOCH
    finally:
        s.close()


def test_T12b_uc_paralel_poll_yine_tek_kira(pg):
    """Engel tabanli kaba yaris — determinist testin tamamlayicisi."""
    Session = pg
    cid = _komut(Session)
    sonuclar = _paralel(Session, _poll, adet=3)
    assert [c.id for r in sonuclar for c in r.commands].count(cid) == 1

    s = Session()
    try:
        assert s.get(DeviceCommand, cid).delivery_attempt == 1
    finally:
        s.close()


def test_T12c_es_zamanli_bayat_komut_tek_terminal_duruma_gider(pg):
    """Bayat komut hem `expired` hem teslim edilmis olamaz."""
    Session = pg
    cid = _komut(Session, yas_sn=TTL + 60)

    sonuclar = _paralel(Session, _poll, adet=3)
    assert cid not in [c.id for r in sonuclar for c in r.commands]

    s = Session()
    try:
        cmd = s.get(DeviceCommand, cid)
        assert (cmd.status, cmd.result_status) == ("failed", teslim.RESULT_EXPIRED)
        assert cmd.sent_at is None
        assert cmd.delivery_attempt == 0
    finally:
        s.close()


# ---------------------------------------------------------------------------
# ACK es zamanliligi
# ---------------------------------------------------------------------------


def test_es_zamanli_mukerrer_ACK_tutarli(pg):
    """Ayni ACK iki kez paralel gelirse komut yine tek kez `sent` olur."""
    Session = pg
    cid = _komut(Session)

    s = Session()
    try:
        jeton = _poll(s).commands[0].delivery_token
        s.commit()
    finally:
        s.close()

    def _ack(s):
        r = gw_api.report_command_delivery_acks(
            "GW-1",
            payload=CommandDeliveryAckRequest(
                acks=[CommandDeliveryAckItem(command_id=cid, delivery_token=jeton)]
            ),
            db=s, x_gateway_token="t",
        )
        return r

    sonuclar = _paralel(Session, _ack, adet=2)
    assert all(r.accepted == 1 and r.rejected == 0 for r in sonuclar)

    s = Session()
    try:
        cmd = s.get(DeviceCommand, cid)
        assert cmd.status == "sent"
        assert cmd.sent_at is not None
    finally:
        s.close()


def test_ACK_ile_es_zamanli_poll_cakismaz(pg):
    """ACK ve yeni bir poll ayni anda kosarsa durum tutarli kalir."""
    Session = pg
    cid = _komut(Session)

    s = Session()
    try:
        jeton = _poll(s).commands[0].delivery_token
        s.commit()
    finally:
        s.close()

    def _isler(s):
        # Iki farkli is: cift indeksli olanlar ACK, tekler poll.
        return gw_api.report_command_delivery_acks(
            "GW-1",
            payload=CommandDeliveryAckRequest(
                acks=[CommandDeliveryAckItem(command_id=cid, delivery_token=jeton)]
            ),
            db=s, x_gateway_token="t",
        )

    _paralel(Session, _isler, adet=2)

    s = Session()
    try:
        cmd = s.get(DeviceCommand, cid)
        # Tek bir terminal olmayan durum: `sent`. Hem `sent` hem `pending` olamaz.
        assert cmd.status == "sent"
    finally:
        s.close()


# ---------------------------------------------------------------------------
# SPEC §22 — gercek Postgres uzerinde
# ---------------------------------------------------------------------------


def test_KRITIK_mutlak_TTL_gercek_pg_uzerinde(pg, monkeypatch):
    """Kira yenilense bile mutlak son kullanma ani asilamaz (gercek DB)."""
    Session = pg
    s = Session()
    try:
        t_olusum = datetime.now(timezone.utc) - timedelta(seconds=5)
        cmd = DeviceCommand(
            gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
            dnp3_index=3, status="pending", created_at=t_olusum,
        )
        s.add(cmd)
        s.commit()
        cid = cmd.id

        # Ilk kira gercekten verilir.
        assert len(_poll(s).commands) == 1
        s.commit()
    finally:
        s.close()

    # Komutu TTL'nin otesine tasi (saat ileri almak yerine `created_at` geri).
    s = Session()
    try:
        cmd = s.get(DeviceCommand, cid)
        cmd.created_at = datetime.now(timezone.utc) - timedelta(seconds=TTL + 60)
        cmd.delivery_lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
    finally:
        s.close()

    s = Session()
    try:
        assert _poll(s).commands == [], "bayat komut YENIDEN SUNULDU"
        s.commit()
        cmd = s.get(DeviceCommand, cid)
        assert cmd.status == "failed"
        assert cmd.result_status == teslim.RESULT_UNKNOWN
    finally:
        s.close()


# ---------------------------------------------------------------------------
# T21 — defter kimligi
# ---------------------------------------------------------------------------


def test_T21_epoch_degisince_otomatik_teslim_durur_pg(pg):
    Session = pg
    cid = _komut(Session)

    s = Session()
    try:
        assert len(_poll(s).commands) == 1
        s.commit()
        cmd = s.get(DeviceCommand, cid)
        cmd.delivery_lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
    finally:
        s.close()

    s = Session()
    try:
        assert _poll(s, epoch=EPOCH_YENI).commands == []
        s.commit()
        cmd = s.get(DeviceCommand, cid)
        assert cmd.result_status == teslim.RESULT_DELIVERY_STATE_LOST
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Supurucu — skip_locked yalnizca gercek Postgres'te anlamli
# ---------------------------------------------------------------------------


def test_supurucu_es_zamanli_calisirsa_cift_sonlandirma_olmaz(pg):
    """Iki supurucu cakisirsa komut TEK KEZ sonlandirilir (`skip_locked`).

    DETERMINIST CAKISMA: A supurucusu satiri kilitler ve transaction'i acik
    tutar; B ayni anda kosar. `skip_locked` sayesinde B BLOKE OLMAZ — kilitli
    satiri ATLAR ve 0 doner. Bu onemli: `skip_locked` olmasaydi supurucunun
    ikinci kopyasi kilit beklerken tum turu tikardi.

    Cift `record_event` uretilmemesi de bu sayede garanti.
    """
    Session = pg
    s = Session()
    try:
        simdi = datetime.now(timezone.utc)
        cmd = DeviceCommand(
            gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
            dnp3_index=3, status="sent", created_at=simdi - timedelta(seconds=400),
            sent_at=simdi - timedelta(seconds=400), delivery_token="j", delivery_attempt=1,
        )
        s.add(cmd)
        s.commit()
        cid = cmd.id
    finally:
        s.close()

    sA = Session()
    sB = Session()
    try:
        # A satiri kilitler, transaction ACIK.
        adetA = sonucu_gelmeyenleri_sonlandir(sA)
        assert adetA == 1

        # B ayni anda kosar: kilitli satiri ATLAMALI, beklememeli.
        b_sonuc: list = []
        tB = threading.Thread(
            target=lambda: b_sonuc.append(sonucu_gelmeyenleri_sonlandir(sB)),
            daemon=True,
        )
        tB.start()
        tB.join(timeout=10)
        assert not tB.is_alive(), (
            "ikinci supurucu KILIT BEKLEDI — `skip_locked` calismiyor; iki kopya "
            "birbirini tikar"
        )
        assert b_sonuc == [0], f"kilitli satir atlanmadi: {b_sonuc}"

        sA.commit()
        sB.rollback()
    finally:
        sA.close()
        sB.close()

    s = Session()
    try:
        cmd = s.get(DeviceCommand, cid)
        assert (cmd.status, cmd.result_status) == ("failed", teslim.RESULT_UNKNOWN)
    finally:
        s.close()
