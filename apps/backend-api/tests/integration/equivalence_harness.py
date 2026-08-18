"""Bilinen cihaz sicak yolunun GOZLEMLENEBILIR ciktisini yakalayan harness.

NICIN VAR
---------
Olcum basina is mantigi `_persist_batch` dongusunden `process_valid_telemetry`
fonksiyonuna cikarildi (canli yol ile karantina replay ayni mantigi
kullansin diye). Bu, sistemin EN SICAK kod yolunda yapilmis bir tasima:
kaynak kodun sekline bakan bir test bunun davranissal esdegerligini
KANITLAMAZ.

Bu harness ayni fixture setini calistirip DB'ye ve donus degerlerine yansiyan
her seyi toplar. Ayni harness baseline commit'in `telemetry_consumer`'i ile de
calistirilabilir (yalnizca `_persist_batch` arayuzune dayanir), boylece
"once/sonra" karsilastirmasi davranis uzerinden yapilir.

ZAMAN BAGIMLILIGI
-----------------
`processed_at`, `updated_at`, `last_update_at` gibi "simdi" turevli MUTLAK
degerler kasten disarida birakilir; onlarin yerine DAVRANIS kaydedilir
(or. `last_update_at` yazildi mi). Kalite/zaman damgasi degerlendirmesi
mutlak fixture damgalariyla yapildigi icin sinirlardan uzak ve kararlidir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text

from app.models.device import Device
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog

MODEL_A = "horstmann_sn_2_0"
MODEL_B = "horstmann_sn_3_0"

# Mutlak, sinirlardan uzak damgalar — "simdi"ye gore kaymayan sonuc icin.
T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
ESKI_CIHAZ_SAATI = datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _ts(offset_sec: int) -> str:
    return (T0 + timedelta(seconds=offset_sec)).isoformat()


# --------------------------------------------------------------------------
# Sema
# --------------------------------------------------------------------------
def setup_schema(Session) -> None:  # noqa: N803
    """Gateway/cihaz/sinyal katalogu — fixture'larin dayandigi gercek sema."""
    db = Session()
    db.add(Gateway(code="GW-A", name="A", host="10.0.0.1", listen_port=20000,
                   token="tok-a", is_active=True))
    db.add(Gateway(code="GW-B", name="B", host="10.0.0.2", listen_port=20001,
                   token="tok-b", is_active=True))
    db.commit()

    db.add(Device(code="EQ-1", name="EQ1", gateway_code="GW-A", model=MODEL_A,
                  ip_address="10.0.0.11", latitude=39.0, longitude=35.0))
    db.add(Device(code="EQ-2", name="EQ2", gateway_code="GW-A", model=MODEL_A,
                  ip_address="10.0.0.12", latitude=39.1, longitude=35.1))
    # Ayni sinyal adinin FARKLI modelde farkli arsiv politikasi tasidigini
    # kapsamak icin ikinci model.
    db.add(Device(code="EQ-3", name="EQ3", gateway_code="GW-B", model=MODEL_B,
                  ip_address="10.0.0.13", latitude=39.2, longitude=35.2))
    db.commit()

    katalog = [
        # (model, key, data_type, historize, deadband)
        (MODEL_A, "master.actual_voltage", "analog", True, 1.0),   # olu bantli
        (MODEL_A, "master.actual_current", "analog", True, 0.0),   # olu bantsiz
        (MODEL_A, "master.temperature", "analog", False, 0.0),     # arsiv KAPALI
        (MODEL_A, "master.fault_passage", "binary", True, 0.0),    # ikili
        (MODEL_A, "master.status_text", "string", True, 0.0),      # metin
        # Ayni ad, BASKA model -> arsiv kapali.
        (MODEL_B, "master.actual_voltage", "analog", False, 0.0),
    ]
    for model, key, dtype, historize, deadband in katalog:
        db.add(SignalCatalog(
            key=key, model=model, label=key, data_type=dtype,
            historize=historize, historize_deadband=deadband,
        ))
    db.commit()
    db.close()


# --------------------------------------------------------------------------
# Fixture'lar
# --------------------------------------------------------------------------
def _ok(mid: str, **kw) -> dict:
    veri = {
        "message_id": mid,
        "device_code": "EQ-1",
        "signal_key": "master.actual_voltage",
        "value": 230.0,
        "quality": "good",
        "source_gateway": "GW-A",
        "source_timestamp": _ts(0),
    }
    veri.update(kw)
    return {"kind": "json", "payload": veri, "id": mid}


def build_fixtures() -> list[dict]:
    """Temsili okuma seti — gercek semanin DESTEKLEDIGI kombinasyonlar.

    Uydurma alan/kombinasyon URETILMEZ; hepsi `TelemetryIn` + SignalCatalog
    ile gercekten olusabilecek durumlar.
    """
    f: list[dict] = []
    n = 0

    def sonraki() -> str:
        nonlocal n
        n += 1
        return f"eq-{n:03d}"

    # --- analog, olu bantli: bant ICINDE ve DISINDA -----------------------
    for i, deger in enumerate([230.0, 230.2, 230.4, 233.0, 233.1, 240.0]):
        f.append(_ok(sonraki(), value=deger, source_timestamp=_ts(10 + i)))

    # --- analog, olu bantsiz: her okuma arsivlenmeli ----------------------
    for i, deger in enumerate([5.0, 5.0, 5.0, 7.5]):
        f.append(_ok(sonraki(), signal_key="master.actual_current",
                     value=deger, source_timestamp=_ts(30 + i)))

    # --- arsivi KAPALI sinyal --------------------------------------------
    for i, deger in enumerate([21.0, 25.0, 30.0]):
        f.append(_ok(sonraki(), signal_key="master.temperature",
                     value=deger, source_timestamp=_ts(50 + i)))

    # --- ikili/durum sinyali: olu bant UYGULANMAMALI ----------------------
    for i, deger in enumerate([0.0, 1.0, 1.0, 0.0]):
        f.append(_ok(sonraki(), signal_key="master.fault_passage",
                     value=deger, source_timestamp=_ts(70 + i)))

    # --- metin sinyali: value None + value_string -------------------------
    for i, metin in enumerate(["OK", "OK", "FAULT", ""]):
        f.append(_ok(sonraki(), signal_key="master.status_text",
                     value=None, value_string=metin,
                     signal_data_type="string", source_timestamp=_ts(90 + i)))

    # --- kalite varyasyonlari (comm_lost -> recovery dahil) ---------------
    for i, kalite in enumerate(
        ["good", "invalid", "comm_lost", "good", "questionable", "comm_lost", "good"]
    ):
        f.append(_ok(sonraki(), quality=kalite, value=231.0 + i,
                     source_timestamp=_ts(110 + i)))

    # --- NORMALIZASYON GEREKTIREN kalite girdileri ------------------------
    #
    # `normalize_quality` = strip().lower(). Yalnizca temiz kucuk harfli
    # degerlerle test etmek onu ETKISIZ birakir: normalizasyonu tamamen
    # kaldiran bir regresyon fark edilmeden gecerdi (mutasyon K bunu
    # gosterdi). Gateway firmware'i buyuk harfli ya da bosluklu kalite
    # yollayabilir; sema bunu engellemiyor.
    for i, ham in enumerate(["GOOD", "  comm_lost  ", "Invalid", "COMM_LOST", ""]):
        f.append(_ok(sonraki(), quality=ham, value=240.0 + i,
                     source_timestamp=_ts(120 + i)))

    # --- cihaz saati: normal / cok eski / hic yok -------------------------
    f.append(_ok(sonraki(), device_event_at=_ts(1), source_timestamp=_ts(130)))
    f.append(_ok(sonraki(), device_event_at=ESKI_CIHAZ_SAATI.isoformat(),
                 source_timestamp=_ts(131)))
    f.append(_ok(sonraki(), source_timestamp=_ts(132)))
    f.append(_ok(sonraki(), device_event_at=_ts(2),
                 timestamp_quality="good", source_timestamp=_ts(133)))

    # --- korelasyon / gateway alanlari ------------------------------------
    f.append(_ok(sonraki(), correlation_id="corr-1", source_timestamp=_ts(140)))
    f.append(_ok(sonraki(), correlation_id=None, source_timestamp=_ts(141)))
    f.append(_ok(sonraki(), source_gateway=None, source_timestamp=_ts(142)))

    # --- ikinci cihaz: ayni sinyal, ayri deadband durumu ------------------
    for i, deger in enumerate([100.0, 100.1, 105.0]):
        f.append(_ok(sonraki(), device_code="EQ-2", value=deger,
                     source_timestamp=_ts(150 + i)))

    # --- ucuncu cihaz: BASKA MODEL, ayni sinyal adi, arsiv kapali ---------
    for i, deger in enumerate([220.0, 240.0, 260.0]):
        f.append(_ok(sonraki(), device_code="EQ-3", source_gateway="GW-B",
                     value=deger, source_timestamp=_ts(160 + i)))

    # --- katalogda OLMAYAN sinyal: bilinmeyen -> arsivlenmeli -------------
    for i, deger in enumerate([1.0, 1.0, 2.0]):
        f.append(_ok(sonraki(), signal_key="master.brand_new_signal",
                     value=deger, source_timestamp=_ts(170 + i)))

    # --- ayni source_timestamp tekrari (arsiv ON CONFLICT DO NOTHING) -----
    f.append(_ok(sonraki(), value=250.0, source_timestamp=_ts(180)))
    f.append(_ok(sonraki(), value=250.0, source_timestamp=_ts(180)))

    # --- ayni message_id tekrari (dedup: ikinci sadece ack) ---------------
    tekrar = _ok("eq-dup", value=260.0, source_timestamp=_ts(190))
    f.append(tekrar)
    f.append(dict(tekrar))

    # --- value None + value_string YOK (numerik dusum davranisi) ----------
    f.append(_ok(sonraki(), value=None, source_timestamp=_ts(200)))

    # --- BOZUK: ACK/DLQ siniflandirmasi ----------------------------------
    # NOT: "JSON gecerli ama nesne degil" ([1,2,3]) BILEREK burada YOK —
    # o vaka bu branch'te KASITLI olarak duzeltildi (baseline'da tum batch'i
    # ack'siz birakiyordu). Esdegerlik seti yalnizca DEGISMEMESI gereken
    # davranisi olcer; o duzeltmenin kendi regresyon testi ayri.
    f.append({"kind": "raw", "raw": b"{bozuk json", "id": "eq-bad-json"})
    f.append({
        "kind": "json",
        "payload": {**_ok("eq-bad-schema")["payload"], "signal_key": "x" * 400},
        "id": "eq-bad-schema",
    })
    return f


# --------------------------------------------------------------------------
# Yakalama
# --------------------------------------------------------------------------
class HarnessMsg:
    """`_persist_batch`in kullandigi mesaj yuzeyi."""

    def __init__(self, data: bytes, ident: str, seq: int):
        import json as _json

        self.data = data
        self.subject = "e1.telemetry.normalized.eq"
        self.ident = ident
        self.metadata = type(
            "M", (), {"stream": "TELEMETRY_NORMALIZED",
                      "sequence": type("S", (), {"stream": seq})()},
        )()
        self._json = _json


def _mesajlari_kur(fixtures: list[dict]) -> list[HarnessMsg]:
    import json as _json

    msgs = []
    for i, fx in enumerate(fixtures):
        if fx["kind"] == "raw":
            data = fx["raw"]
        else:
            data = _json.dumps(fx["payload"], default=str).encode()
        msgs.append(HarnessMsg(data, fx["id"], 1000 + i))
    return msgs


def _satirlar(db, sql: str) -> list[tuple]:  # noqa: ANN001
    return [tuple(r) for r in db.execute(text(sql)).all()]


def capture(Session, persist_batch, *, batch_size: int = 9) -> dict[str, Any]:  # noqa: N803
    """Fixture'lari `persist_batch` ile isler ve gozlemlenebilir sonucu doner.

    `persist_batch` disaridan veriliyor: ayni harness hem mevcut kodla hem
    baseline commit'in tuketicisiyle calistirilabilsin.
    """
    from app.services import historian_policy

    # Arsiv karari modul-ici onbellege (son arsivlenen deger) bagli. Iki kosu
    # arasinda sifirlanmazsa karsilastirma anlamsiz olurdu.
    historian_policy.reset_caches()

    fixtures = build_fixtures()
    msgs = _mesajlari_kur(fixtures)

    ack: dict[str, str] = {}
    ws_kayitlari: list[dict] = []
    outbound_kayitlari: list[dict] = []

    for i in range(0, len(msgs), batch_size):
        parti = msgs[i:i + batch_size]
        ok, bad, ws, outbound = persist_batch(parti)
        for m in ok:
            ack[m.ident] = "ok"
        for m in bad:
            ack[m.ident] = "bad"
        for p in ws:
            ws_kayitlari.append(_temizle(p))
        for p in outbound:
            outbound_kayitlari.append(_temizle(p))

    for m in msgs:
        ack.setdefault(m.ident, "dropped")

    db = Session()
    try:
        telemetry = _satirlar(db, (
            "select d.code, t.signal_key, t.value, t.value_string, t.quality, "
            "t.source_timestamp, t.device_event_at, t.timestamp_quality "
            "from telemetry t join devices d on d.id = t.device_id "
            "order by d.code, t.signal_key, t.source_timestamp"
        ))
        history = _satirlar(db, (
            "select d.code, h.signal_key, h.value, h.value_string, h.quality, "
            "h.source_timestamp, h.device_event_at, h.timestamp_quality "
            "from telemetry_history h join devices d on d.id = h.device_id "
            "order by d.code, h.signal_key, h.source_timestamp"
        ))
        latest = _satirlar(db, (
            "select d.code, l.signal_key, l.value, l.value_string, l.quality, "
            "l.source_timestamp, l.device_event_at, l.timestamp_quality "
            "from telemetry_latest l join devices d on d.id = l.device_id "
            "order by d.code, l.signal_key"
        ))
        dedup = sorted(
            r[0] for r in db.execute(
                text("select message_id from processed_messages")
            ).all()
        )
        cihazlar = []
        for dev in db.scalars(select(Device).order_by(Device.code)).all():
            durum = dev.communication_status
            cihazlar.append({
                "code": dev.code,
                "communication_status": getattr(durum, "value", str(durum)),
                # MUTLAK deger degil DAVRANIS: yazildi mi?
                "last_update_at_written": dev.last_update_at is not None,
            })
    finally:
        db.close()

    return {
        "fixture_count": len(fixtures),
        "ack": ack,
        "telemetry": [_normalize(r) for r in telemetry],
        "telemetry_history": [_normalize(r) for r in history],
        "telemetry_latest": [_normalize(r) for r in latest],
        "processed_messages": dedup,
        "devices": cihazlar,
        "ws": ws_kayitlari,
        "outbound": outbound_kayitlari,
    }


def capture_roundtrips(Session, persist_batch) -> dict[str, int]:  # noqa: N803
    """Bilinen cihaz partisinde DB gidis-donus sayisini olcer.

    ISLEV: sicak yol regresyon nobetcisi. Refactor sonrasi olcum basina
    fazladan sorgu eklenirse (or. karantina tablosuna bakmak) sayi artar ve
    test duser. Zamanlama OLCULMEZ — flaky timing testi yazmiyoruz, ifade
    SAYIYORUZ.

    NOT: `COPY` ve `execute_values` ham psycopg2 kursorunden gider ve
    SQLAlchemy olayina TAKILMAZ; olculen sey ORM/Core ifadeleridir. Bilinen
    cihaz yolunun ek SORGU yapip yapmadigini gormek icin bu yeterli.
    """
    from sqlalchemy import event

    from app.services import historian_policy

    historian_policy.reset_caches()

    sayac = {"total": 0, "unknown_table": 0}
    db_ornek = Session()
    engine = db_ornek.get_bind()
    db_ornek.close()

    def dinleyici(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        sayac["total"] += 1
        if "unknown_device_telemetry" in statement.lower():
            sayac["unknown_table"] += 1

    event.listen(engine, "before_cursor_execute", dinleyici)
    try:
        parti = _mesajlari_kur([
            _ok(f"rt-{i:03d}", value=200.0 + i, source_timestamp=_ts(300 + i))
            for i in range(10)
        ])
        persist_batch(parti)
    finally:
        event.remove(engine, "before_cursor_execute", dinleyici)

    return sayac


def _normalize(satir: tuple) -> list:
    return [_deger(x) for x in satir]


def _deger(x: Any) -> Any:
    if isinstance(x, datetime):
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.astimezone(timezone.utc).isoformat()
    if isinstance(x, float):
        # Kayan nokta gurultusu karsilastirmayi kirmasin.
        return round(x, 9)
    return x


#: "Simdi" turevli MUTLAK alanlar — karsilastirma disi (bkz. modul basligi).
ZAMAN_ALANLARI = frozenset({"processed_at"})


def _temizle(p: dict) -> dict:
    return {k: _deger(v) for k, v in sorted(p.items()) if k not in ZAMAN_ALANLARI}
