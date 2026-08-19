"""Telemetri ACK kritik yolu — outbound dagitimi ACK'i BLOKLAYAMAZ.

YASANAN ARIZA (2026-08-19, IDLE gate)
-------------------------------------
`dispatch_event(event_kind="telemetry")` yalnizca `rest` ve `mqtt` hedeflerini
atliyordu. Panelden olusturulabilen `modbus` hedefi (desteklenen bir urun
ozelligi; `schemas/outbound.py` icinde tam bir alan blogu var) bu yuzden
`_dispatch_with_retry`'a DUSUYORDU. O fonksiyon modbus'u tanimadigi icin her
payload'da istisna firlatiyor, retry dongusune giriyor ve payload BASINA
0,7 + 1,4 = 2,1 saniye `time.sleep` yapiyordu.

Bu uyku telemetri yolunda DB COMMIT'i ile NATS ACK'i ARASINDA calisiyor
(`telemetry_consumer._hat_dongusu_govde`). Sonuclari sahada olculdu:
  * 500'luk parti ~1.050 sn ACK'siz kaldi (en eski Postgres oturumu 1.040 sn),
  * `telemetry-persist-prio-v1` 8.700+ mesaj birikimiyle kilitlendi,
    `ack_pending` tam 500'de sabitlendi, `consumer_seq` 120 sn boyunca HIC
    ilerlemedi (etkin kapasite 0 msj/sn; varsayim 9.600 msj/sn),
  * Postgres oturumlari 17 dakikaya kadar `idle in transaction` kaldi,
  * uretilen dead-letter denetim kayitlari commit edilmedigi icin operator
    arizayi HICBIR yuzeyden goremedi.

Bu dosyadaki testler D-R1..D-R10 kimlikleriyle o arizayi kilitler.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.services import outbound_dispatch_service as ods


class _SahteHedef(SimpleNamespace):
    """OutboundTarget yerine gecen minimal cift (ORM'e ihtiyac yok)."""


def _hedef(protocol: str, *, tid: int = 1, event_filter: str = "all"):
    return _SahteHedef(
        id=tid,
        name=f"hedef-{protocol}",
        protocol=protocol,
        event_filter=event_filter,
        endpoint="",
        topic=None,
        auth_header=None,
        auth_token=None,
        qos=0,
        retain=False,
    )


@pytest.fixture(autouse=True)
def _rate_limit_sifirla():
    """Her test kendi uyari penceresiyle basliyor (rate-limit sizmasin)."""
    with ods._unsupported_lock:
        ods._unsupported_last_warn.clear()
    yield
    with ods._unsupported_lock:
        ods._unsupported_last_warn.clear()


@pytest.fixture
def uyku_sayaci(monkeypatch):
    """`time.sleep` cagrilarini yakalar — ACK yolunda SIFIR olmali."""
    cagrilar: list[float] = []
    monkeypatch.setattr(ods.time, "sleep", lambda s: cagrilar.append(s))
    return cagrilar


@pytest.fixture
def retry_casusu(monkeypatch):
    """`_dispatch_with_retry` cagrilarini sayar."""
    cagrilar: list[str] = []
    gercek = ods._dispatch_with_retry

    def _sarmal(db, *, target, event_kind, payload):
        cagrilar.append(target.protocol)
        return gercek(db, target=target, event_kind=event_kind, payload=payload)

    monkeypatch.setattr(ods, "_dispatch_with_retry", _sarmal)
    return cagrilar


@pytest.fixture
def olay_yutucu(monkeypatch):
    """`_record_unsupported_protocol`'un DB'ye gitmesini engeller; cagriyi sayar."""
    cagrilar: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ods,
        "_record_unsupported_protocol",
        lambda *, target, event_kind: cagrilar.append((target.protocol, event_kind)),
    )
    return cagrilar


@pytest.fixture
def iec104_casusu(monkeypatch):
    guncellemeler: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ods.iec104_manager,
        "update_point_threadsafe",
        lambda **kw: guncellemeler.append((kw["device_code"], kw["signal_key"])),
    )
    return guncellemeler


def _telemetri(i: int = 0) -> dict:
    return {
        "message_id": f"m-{i}",
        "device_code": "DEV-1",
        "signal_key": "fault_flag",
        "value": 1,
        "quality": "good",
    }


# --------------------------------------------------------------------------
# D-R1 / D-R2 — modbus telemetri yolunda retry'a HIC girmemeli
# --------------------------------------------------------------------------
def test_DR1_telemetry_modbus_dispatch_with_retry_CAGIRMAZ(retry_casusu, olay_yutucu):
    ods.dispatch_event(
        None, event_kind="telemetry", payload=_telemetri(), targets=[_hedef("modbus")]
    )
    assert retry_casusu == [], (
        "telemetry+modbus `_dispatch_with_retry`'a dustu — modbus'un sahibi "
        "modbus-outbound koprusudur, generic dispatcher DEGIL"
    )


def test_DR2_telemetry_modbus_time_sleep_CAGIRMAZ(uyku_sayaci, olay_yutucu):
    ods.dispatch_event(
        None, event_kind="telemetry", payload=_telemetri(), targets=[_hedef("modbus")]
    )
    assert uyku_sayaci == [], f"ACK yolunda time.sleep cagrildi: {uyku_sayaci}"


# --------------------------------------------------------------------------
# D-R3 — 500 payload + modbus hedefi: ACK oncesi bloklama olmamali
# --------------------------------------------------------------------------
def test_DR3_500_payload_modbus_ack_oncesi_BLOKLAMAZ(uyku_sayaci, olay_yutucu):
    hedefler = [_hedef("modbus")]
    basla = time.monotonic()
    for i in range(500):
        ods.dispatch_event(
            None, event_kind="telemetry", payload=_telemetri(i), targets=hedefler
        )
    gecen = time.monotonic() - basla

    assert uyku_sayaci == [], (
        f"500 payload icin {len(uyku_sayaci)} uyku cagrildi "
        f"(toplam {sum(uyku_sayaci):.1f} sn) — eski davranis ~1.050 sn idi"
    )
    # Bellek-ici is: saniyenin cok altinda kalmali. Esik bilerek genis
    # (yavas CI makinesi), ama eski 1.050 sn'lik davranisin YANINDAN gecmez.
    assert gecen < 2.0, f"500 payload {gecen:.2f} sn surdu — ACK yolu blokluyor"


# --------------------------------------------------------------------------
# D-R4 / D-R5 — REST ve MQTT telemetri: senkron gonderim YOK, sahip batcher
# --------------------------------------------------------------------------
@pytest.mark.parametrize("protocol", ["rest", "mqtt"])
def test_DR4_DR5_telemetry_rest_mqtt_senkron_gonderim_YOK(
    protocol, retry_casusu, uyku_sayaci, monkeypatch
):
    gonderimler: list[str] = []
    monkeypatch.setattr(ods, "_send_rest", lambda t, p: gonderimler.append("rest"))
    monkeypatch.setattr(ods, "_send_mqtt", lambda t, p: gonderimler.append("mqtt"))

    ods.dispatch_event(
        None, event_kind="telemetry", payload=_telemetri(), targets=[_hedef(protocol)]
    )

    assert retry_casusu == [], f"telemetry+{protocol} senkron dispatch'e dustu"
    assert gonderimler == [], (
        f"telemetry+{protocol} icin senkron gonderim yapildi — sahiplik "
        "batcher/publisher'da, ayni okuma iki kez giderdi"
    )
    assert uyku_sayaci == []


# --------------------------------------------------------------------------
# D-R6 — IEC104 telemetri: mevcut inline bellek-ici guncelleme KORUNUR
# --------------------------------------------------------------------------
def test_DR6_telemetry_iec104_inline_update_KORUNUR(iec104_casusu, uyku_sayaci):
    ods.dispatch_event(
        None, event_kind="telemetry", payload=_telemetri(), targets=[_hedef("iec104")]
    )
    assert iec104_casusu == [("DEV-1", "fault_flag")], (
        "IEC104 inline nokta guncellemesi kayboldu — bu yol bilincli olarak "
        "senkron ve bellek-ici, degistirilmemeliydi"
    )
    assert uyku_sayaci == []


# --------------------------------------------------------------------------
# D-R7 — gercekten desteklenmeyen protokol: fail-fast, retry/sleep YOK
# --------------------------------------------------------------------------
def test_DR7_bilinmeyen_protokol_FAIL_FAST(uyku_sayaci, olay_yutucu):
    # Alarm yolu: burada `_dispatch_with_retry` GERCEKTEN cagrilir.
    ods.dispatch_event(
        None, event_kind="alarm", payload=_telemetri(), targets=[_hedef("opcua")]
    )
    assert uyku_sayaci == [], (
        "desteklenmeyen protokol icin exponential backoff yapildi — bu bir "
        "yapilandirma hatasi, ag hatasi degil; yeniden deneme anlamsiz"
    )
    assert olay_yutucu == [("opcua", "alarm")], (
        "fail-fast sessiz kaldi — ariza operatore bildirilmeli"
    )


def test_DR7b_desteklenen_protokol_retry_KORUNUR(monkeypatch, uyku_sayaci):
    """Fail-fast, GERCEK ag hatasinin retry'ini bozmamali."""
    denemeler: list[int] = []

    def _patlayan(target, payload):
        denemeler.append(1)
        raise OSError("connection refused")

    monkeypatch.setattr(ods, "_send_rest", _patlayan)
    monkeypatch.setattr(ods, "record_event", lambda *a, **k: None)

    ods._dispatch_with_retry(
        None, target=_hedef("rest"), event_kind="alarm", payload=_telemetri()
    )
    assert len(denemeler) == ods.MAX_RETRY, "ag hatasinda retry kayboldu"
    assert len(uyku_sayaci) == ods.MAX_RETRY - 1, "ag hatasinda backoff kayboldu"


# --------------------------------------------------------------------------
# D-R8 — ariza gorunurlugu: denetim kaydi GERCEKTEN commit olur
# --------------------------------------------------------------------------
def test_DR8_unsupported_protocol_olayi_COMMIT_edilir(monkeypatch):
    """`_record_unsupported_protocol` kendi transaction'ini acip commit etmeli.

    Cagiranin session'ina yazsaydi telemetri yolunda geri sarilirdi
    (`_dispatch_outbound` commit etmiyor) — arizanin ikinci yarisi buydu.
    """
    islemler: list[str] = []
    yazilan: list[dict] = []

    class _SahteSession:
        def commit(self):
            islemler.append("commit")

        def rollback(self):
            islemler.append("rollback")

        def close(self):
            islemler.append("close")

    monkeypatch.setattr(
        "app.db.session.SessionLocal", lambda: _SahteSession()
    )
    monkeypatch.setattr(
        ods, "record_event", lambda db, **kw: yazilan.append(kw)
    )

    ods._record_unsupported_protocol(target=_hedef("modbus"), event_kind="alarm")

    assert "commit" in islemler, "denetim kaydi commit EDILMEDI — operator goremez"
    assert "close" in islemler, "session sizdirildi"
    assert len(yazilan) == 1
    assert yazilan[0]["event_type"] == "outbound_unsupported_protocol"
    assert yazilan[0]["severity"] == "error"


def test_DR8b_unsupported_uyarisi_RATE_LIMITLI(monkeypatch):
    """Ariza bildirimi `system_events`'i doldurmamali (2 yil saklaniyor)."""
    yazilan: list[dict] = []

    class _SahteSession:
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _SahteSession())
    monkeypatch.setattr(ods, "record_event", lambda db, **kw: yazilan.append(kw))

    hedef = _hedef("modbus", tid=42)
    for _ in range(500):
        ods._record_unsupported_protocol(target=hedef, event_kind="telemetry")

    assert len(yazilan) == 1, (
        f"500 payload icin {len(yazilan)} denetim satiri yazildi — rate-limit yok"
    )


# --------------------------------------------------------------------------
# D-R9 — dagitim dongusu ACIK TRANSACTION icinde kosmamali
# --------------------------------------------------------------------------
def test_DR9_dispatch_dongusu_transaction_ACIK_DEGIL(monkeypatch):
    """`_dispatch_outbound` hedefleri cektikten sonra transaction'i kapatmali.

    Sahada bu transaction dagitim boyunca acik kaliyordu ve icindeki
    `time.sleep`'ler yuzunden Postgres oturumlari 17 DAKIKA
    `idle in transaction` kaldi: acik transaction en eski xmin'i sabitler,
    VACUUM ilerleyemez.
    """
    from app.services import telemetry_consumer as tc

    durumlar: list[bool] = []
    olaylar: list[str] = []

    class _SahteSession:
        def __init__(self):
            self._tx = False

        def scalars(self, stmt):
            self._tx = True  # SELECT transaction'i acar

            class _R:
                def all(_s):
                    return [_hedef("modbus")]

            return _R()

        def in_transaction(self):
            return self._tx

        def expunge_all(self):
            olaylar.append("expunge_all")

        def rollback(self):
            olaylar.append("rollback")
            self._tx = False

        def close(self):
            olaylar.append("close")
            self._tx = False

    sahte = _SahteSession()
    monkeypatch.setattr(tc, "SessionLocal", lambda: sahte)
    monkeypatch.setattr(
        ods,
        "dispatch_event",
        lambda db, **kw: durumlar.append(sahte.in_transaction()),
    )
    monkeypatch.setattr(
        "app.services.outbound_telemetry_batcher.submit", lambda p: None
    )
    monkeypatch.setattr(
        "app.services.mqtt_publisher_service.submit_telemetry", lambda p: None
    )

    tc._dispatch_outbound([_telemetri(i) for i in range(10)])

    assert durumlar, "dispatch_event hic cagrilmadi — test kurgusu bozuk"
    assert not any(durumlar), (
        "dagitim dongusu ACIK transaction icinde kostu — idle-in-transaction riski"
    )
    assert olaylar[:2] == ["expunge_all", "rollback"], (
        f"sira yanlis: {olaylar[:2]}. `expunge_all` ONCE gelmeli, yoksa "
        "rollback nesneleri expire eder ve `target.protocol` erisimi YENI bir "
        "transaction acar (SessionLocal expire_on_commit=True)"
    )


# --------------------------------------------------------------------------
# D-R10 — prio hattinin parti isleme suresi modbus hedefiyle SINIRLI kalir
# --------------------------------------------------------------------------
def test_DR10_modbus_hedefi_aktifken_parti_SINIRLI(monkeypatch):
    """500 payload'lik bir parti icin toplam dagitim suresi bounded olmali."""
    from app.services import telemetry_consumer as tc

    class _SahteSession:
        """Session cifti SADIK olmali.

        `add`/`flush` eksik birakilmisti ve bu testi SAHTE gecirdi: regresyon
        geri konuldugunda `record_event` -> `db.add(...)` AttributeError
        firlatiyor, istisna `_dispatch_outbound`'un `except`ine dusuyor ve
        `time.sleep`e HIC ULASILMIYORDU. Yani test, yakalamasi gereken
        mutasyonun altinda yesil kaliyordu (M-R1 ile tespit edildi).
        """

        def scalars(self, stmt):
            class _R:
                def all(_s):
                    return [_hedef("modbus", tid=7), _hedef("mqtt", tid=8)]

            return _R()

        def add(self, row): pass
        def flush(self): pass
        def commit(self): pass
        def expunge_all(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr(tc, "SessionLocal", lambda: _SahteSession())
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _SahteSession())
    monkeypatch.setattr(ods.iec104_manager, "update_point_threadsafe", lambda **kw: None)
    monkeypatch.setattr(
        "app.services.outbound_telemetry_batcher.submit", lambda p: None
    )
    monkeypatch.setattr(
        "app.services.mqtt_publisher_service.submit_telemetry", lambda p: None
    )
    uykular: list[float] = []
    monkeypatch.setattr(ods.time, "sleep", lambda s: uykular.append(s))

    basla = time.monotonic()
    tc._dispatch_outbound([_telemetri(i) for i in range(500)])
    gecen = time.monotonic() - basla

    assert uykular == [], f"parti icinde {len(uykular)} uyku — ACK gecikir"
    assert gecen < 2.0, (
        f"500'luk parti {gecen:.2f} sn surdu; sahada olculen eski deger "
        "~1.050 sn idi ve `ack_pending` 500'de kilitleniyordu"
    )
