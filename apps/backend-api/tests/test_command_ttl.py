"""Bekleyen cihaz komutunun TAZELIK SURESI (TTL).

KAPATILAN ACIK
--------------
Komut `pending` olarak kuyruga giriyor ve gateway'e ancak o poll ettiginde
teslim ediliyor. Gateway cevrimdisi kaldigi surece komut kuyrukta BEKLIYORDU:

    10:00  operator OPEN komutu verdi     (gateway cevrimdisi)
    14:00  gateway geri geldi             -> 4 SAAT ONCEKI niyet fiziksel
                                             sisteme uygulandi

Bir kesici komutunda bu kabul edilemez: operatorun 4 saat onceki karari
sahanin SU ANKI durumu icin gecerli olmayabilir.

KAPSAM: ESKI TESLIM PROTOKOLU (F3C sonrasi)
-------------------------------------------
F3C ile komut teslimi kira/ACK protokolune gecti ve varsayilan FAIL-CLOSED
oldu. Bu dosya, teslim yetenegi bildirmeyen ESKI gateway yolunun TTL
sozlesmesinin bozulmadigini kanitlar (`COMMAND_DELIVERY_ACK_REQUIRED=false`
ile kurulur). Yeni protokolun TTL davranisi — ozellikle KIRALANMIS bir
komutun mutlak son kullanma anini asamamasi — `test_command_delivery.py`
icinde kilitlenir.

BU DOSYANIN KILITLEDIKLERI
--------------------------
1. `age <= TTL` TAZE, `age > TTL` BAYAT (sinir dahil).
2. Bayat komut gateway'e GONDERILMEZ **ve** kuyrukta birakilmaz —
   `failed` + `result_status='expired'` ile sonlandirilir.
3. Taze komutun mevcut davranisi (pending -> sent) DEGISMEZ.
4. Sonlandirilan komut sonraki poll'da tekrar gorunmez/loglanmaz.
5. Yeni bir `status` degeri URETILMEZ; durum sozlesmesi korunur.

SAAT DONDURULUYOR, `sleep` YOK
------------------------------
Sinir testleri gercek zamana dayanamaz: 120,000 sn ile 120,001 sn arasindaki
farki `sleep` ile uretmek hem yavas hem kararsizdir. Bunun yerine komutun
`created_at` degeri GERIYE alinarak istenen yas kurgulaniyor; `now` ise
uc noktasinda bir kez hesaplaniyor.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import gateways as gw_api
from app.core.config import settings
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway

TTL = 120


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


@pytest.fixture(autouse=True)
def ttl_sabit(monkeypatch):
    monkeypatch.setattr(settings, "command_max_age_sec", TTL, raising=False)
    # BU DOSYA ESKI TESLIM PROTOKOLUNU KORUR (bkz. modul docstring'i).
    #
    # F3C ile varsayilan FAIL-CLOSED oldu: teslim yetenegi bildirmeyen
    # gateway'e komut gonderilmez. Buradaki testler `X-E1-Delivery` basligi
    # GONDERMEDIGI icin varsayilan altinda hicbir komut teslim edilmezdi ve
    # TTL davranisi olculemezdi. Ayari burada acikca kapatmak, testlerin neyi
    # kanitladigini gorunur kilar: ESKI yolun TTL sozlesmesi bozulmadi.
    monkeypatch.setattr(settings, "command_delivery_ack_required", False, raising=False)


@pytest.fixture()
def gateway(db):
    g = Gateway(
        code="GW-1", name="Saha 1", host="10.0.0.1", listen_port=20000,
        token="t1", is_active=True,
    )
    db.add(g)
    db.add(Gateway(
        code="GW-2", name="Saha 2", host="10.0.0.2", listen_port=20001,
        token="t2", is_active=True,
    ))
    db.add(Device(
        code="CIHAZ-A", name="A", gateway_code="GW-1",
        ip_address="10.0.0.50", latitude=39.0, longitude=35.0,
    ))
    db.commit()
    return g


@pytest.fixture(autouse=True)
def token_dogrulamasi_baypas(monkeypatch, request):
    """Uc, gateway token dogrulamasini `ingest_service`ten cagiriyor."""
    if "gateway" not in request.fixturenames:
        return

    def _sahte(db_, kod, token):
        return db_.scalars(select(Gateway).where(Gateway.code == kod)).first()

    monkeypatch.setattr(
        "app.services.ingest_service.validate_gateway_token", _sahte
    )


@pytest.fixture(autouse=True)
def imza_baypas(monkeypatch):
    """HMAC imzali yanit yerine duz sozluk don — test icerigi okuyabilsin."""
    def _duz(gateway, model, extra_headers=None):  # noqa: ANN001
        return model

    monkeypatch.setattr(gw_api, "_signed_json_response", _duz)


def komut_ekle(
    db, *, gateway_code="GW-1", device_code="CIHAZ-A", yas_sn: float = 0.0,
    status="pending", **kw,
) -> DeviceCommand:
    """Istenen YASTA bir komut uret — saat geriye alinarak, `sleep` YOK."""
    cmd = DeviceCommand(
        gateway_code=gateway_code,
        device_code=device_code,
        command="fault_reset",
        dnp3_index=3,
        status=status,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=yas_sn),
        **kw,
    )
    db.add(cmd)
    db.commit()
    return cmd


def poll(db, kod="GW-1"):
    return gw_api.get_gateway_pending(kod, db=db, x_gateway_token="t", x_gateway_health=None)


class _DonmusSaat(datetime):
    """Uc noktasindaki `datetime.now()` icin sabit an.

    SINIR TESTLERI GERCEK ZAMANA DAYANAMAZ: fixture ile uc cagrisi arasinda
    milisaniyeler geciyor ve "tam TTL" hicbir zaman tam olmuyordu (olculdu:
    age_sec=120.001). `sleep` ile beklemek hem yavas hem kararsiz olurdu.
    Saati dondurmak, 119.999 / 120.000 / 120.001 ayrimini KESIN yapar.
    """

    _an: datetime = None  # type: ignore[assignment]

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return cls._an if tz is None else cls._an.astimezone(tz)


def poll_sabit_saatte(db, monkeypatch, an: datetime, kod="GW-1"):
    """Ucu, `now` degeri TAM OLARAK `an` iken calistir."""
    _DonmusSaat._an = an
    monkeypatch.setattr(gw_api, "datetime", _DonmusSaat)
    try:
        return poll(db, kod)
    finally:
        monkeypatch.setattr(gw_api, "datetime", datetime)


# ==========================================================================
# T01-T03 — taze komut ve payload
# ==========================================================================


def test_T01_taze_komut_yanitta_ve_sent_olur(db, gateway):
    cmd = komut_ekle(db, yas_sn=1)
    resp = poll(db)

    assert [c.id for c in resp.commands] == [cmd.id]
    db.refresh(cmd)
    assert cmd.status == "sent"
    assert cmd.sent_at is not None


def test_T02_payload_created_at_iceriyor(db, gateway):
    cmd = komut_ekle(db, yas_sn=1)
    resp = poll(db)

    (item,) = resp.commands
    assert item.created_at is not None
    # Payload UTC-aware GARANTI eder; DB surucusu (SQLite) tzinfo'yu
    # kaybederek dondurebilir, bu yuzden karsilastirma normalize edilmis
    # deger uzerinden yapilir.
    db_deger = cmd.created_at
    if db_deger.tzinfo is None:
        db_deger = db_deger.replace(tzinfo=timezone.utc)
    assert item.created_at == db_deger


def test_T03_created_at_UTC_ISO8601_ve_timezone_aware(db, gateway):
    komut_ekle(db, yas_sn=1)
    resp = poll(db)

    (item,) = resp.commands
    assert item.created_at.tzinfo is not None, "timezone-aware olmali"
    assert item.created_at.utcoffset() == timedelta(0), "UTC olmali"
    # Serilestirilmis bicim ISO-8601 + ofset tasimali.
    ham = json.loads(resp.model_dump_json())
    metin = ham["commands"][0]["created_at"]
    assert "T" in metin and ("+00:00" in metin or metin.endswith("Z")), metin


# ==========================================================================
# T04-T06 — SINIR (sleep yok)
# ==========================================================================


def _sinir_kur(db, yas_sn: float):
    """Verilen YASI TAM olarak uretecek (komut, an) ikilisi."""
    an = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
    cmd = DeviceCommand(
        gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
        dnp3_index=3, status="pending",
        created_at=an - timedelta(seconds=yas_sn),
    )
    db.add(cmd)
    db.commit()
    return cmd, an


def test_T04_TTL_eksi_epsilon_TAZE(db, gateway, monkeypatch):
    cmd, an = _sinir_kur(db, TTL - 0.001)
    resp = poll_sabit_saatte(db, monkeypatch, an)

    assert [c.id for c in resp.commands] == [cmd.id], "119.999 sn TAZE olmali"
    db.refresh(cmd)
    assert cmd.status == "sent"


def test_T05_TAM_TTL_TAZE(db, gateway, monkeypatch):
    """`age <= TTL` taze — SINIR DAHIL (tam 120.000 sn)."""
    cmd, an = _sinir_kur(db, TTL)
    resp = poll_sabit_saatte(db, monkeypatch, an)

    assert [c.id for c in resp.commands] == [cmd.id], (
        "tam TTL yasindaki komut TAZE sayilmali (age <= TTL)"
    )
    db.refresh(cmd)
    assert cmd.status == "sent"
    assert cmd.result_status is None


def test_T06_TTL_arti_epsilon_BAYAT(db, gateway, monkeypatch):
    cmd, an = _sinir_kur(db, TTL + 0.001)
    resp = poll_sabit_saatte(db, monkeypatch, an)

    assert resp.commands == [], "120.001 sn BAYAT olmali, gonderilmemeli"
    db.refresh(cmd)
    assert cmd.status == "failed"
    assert cmd.result_status == "expired"


# ==========================================================================
# T07-T09 — bayat komutun DB durumu
# ==========================================================================


def test_T07_bayat_komut_DB_durumu(db, gateway):
    cmd = komut_ekle(db, yas_sn=TTL + 10)
    poll(db)
    db.refresh(cmd)

    assert cmd.status == "failed"
    assert cmd.result_status == "expired"
    assert cmd.completed_at is not None
    assert cmd.sent_at is None, "gateway'e HIC gonderilmedi, sent_at bos kalmali"
    assert cmd.result_error and "zaman asimi" in cmd.result_error.lower()


def test_T08_otuz_dakikalik_komut_bayat(db, gateway):
    cmd = komut_ekle(db, yas_sn=30 * 60)
    resp = poll(db)
    assert resp.commands == []
    db.refresh(cmd)
    assert (cmd.status, cmd.result_status) == ("failed", "expired")


def test_T09_yedi_gunluk_komut_bayat(db, gateway):
    cmd = komut_ekle(db, yas_sn=7 * 24 * 3600)
    resp = poll(db)
    assert resp.commands == []
    db.refresh(cmd)
    assert (cmd.status, cmd.result_status) == ("failed", "expired")


# ==========================================================================
# T10 — sonlandirilan komut tekrar gorunmez
# ==========================================================================


def test_T10_bayat_komut_sonraki_pollda_tekrar_islenmez(db, gateway, caplog):
    komut_ekle(db, yas_sn=TTL + 10)

    with caplog.at_level(logging.WARNING):
        poll(db)
    ilk = [r for r in caplog.records if "command_expired_backend" in r.getMessage()]
    assert len(ilk) == 1

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        resp = poll(db)
    assert resp.commands == []
    tekrar = [r for r in caplog.records if "command_expired_backend" in r.getMessage()]
    assert tekrar == [], "sonlandirilmis komut tekrar loglanmamali"


# ==========================================================================
# T11 — gateway izolasyonu
# ==========================================================================


def test_T11_gateway_izolasyonu(db, gateway):
    """GW-1 poll'u GW-2'nin komutuna DOKUNMAMALI."""
    baskasinin = komut_ekle(db, gateway_code="GW-2", yas_sn=TTL + 100)
    poll(db, "GW-1")
    db.refresh(baskasinin)

    assert baskasinin.status == "pending", "baska gateway'in komutu degismemeli"
    assert baskasinin.result_status is None


# ==========================================================================
# T12-T15 — terminal/diger durumlar etkilenmez
# ==========================================================================


@pytest.mark.parametrize("durum", ["sent", "ok", "failed", "cancelled"])
def test_T12_T15_pending_disi_durumlar_etkilenmez(db, gateway, durum):
    cmd = komut_ekle(db, yas_sn=7 * 24 * 3600, status=durum)
    onceki_sonuc = cmd.result_status
    poll(db)
    db.refresh(cmd)

    assert cmd.status == durum, f"{durum} durumundaki komut degismemeli"
    assert cmd.result_status == onceki_sonuc
    assert cmd.completed_at is None


# ==========================================================================
# T16 — karisik parti
# ==========================================================================


def test_T16_karisik_parti_taze_gider_bayat_sonlanir(db, gateway, monkeypatch):
    """Ayni partide taze ve bayat birlikte — biri digerini etkilemez.

    Saat DONDURULUYOR: parti tam TTL sinirindaki bir komut da iceriyor ve
    o karar gercek zamanla test edilemez.
    """
    an = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)

    def ekle(yas):
        cmd = DeviceCommand(
            gateway_code="GW-1", device_code="CIHAZ-A", command="fault_reset",
            dnp3_index=3, status="pending",
            created_at=an - timedelta(seconds=yas),
        )
        db.add(cmd)
        return cmd

    bayat1 = ekle(TTL + 60)
    taze1 = ekle(5)
    bayat2 = ekle(3600)
    taze2 = ekle(TTL)  # TAM SINIR — taze
    db.commit()

    resp = poll_sabit_saatte(db, monkeypatch, an)

    assert sorted(c.id for c in resp.commands) == sorted([taze1.id, taze2.id])
    for c in (taze1, taze2):
        db.refresh(c)
        assert (c.status, c.sent_at is not None) == ("sent", True)
    for c in (bayat1, bayat2):
        db.refresh(c)
        assert (c.status, c.result_status, c.sent_at) == ("failed", "expired", None)


def test_T16b_taze_komutun_sonuc_alanlarina_dokunulmaz(db, gateway):
    taze = komut_ekle(db, yas_sn=1)
    poll(db)
    db.refresh(taze)

    assert taze.completed_at is None, "taze komutun completed_at'i set edilmemeli"
    assert taze.result_status is None
    assert taze.result_error is None


# ==========================================================================
# T17-T18 — UI ve IEC104 kaynakli komutlar
# ==========================================================================


def test_T17_T18_tum_komutlar_ayni_kuyruktan_gecer():
    """UI ve IEC 104 komutlari TEK yerde uretiliyor -> TTL ikisini de kapsar.

    Bu, kapsamin kaynak koddan kanitidir: `DeviceCommand(...)` satiri
    depoda TEK bir yerde var (`device_command_service`), hem
    `POST /devices/{code}/command` (arayuz) hem
    `POST /internal/device-commands` (IEC 104) oradan geciyor. Ayri bir
    kuyruk olsaydi TTL onu atlardi.
    """
    import subprocess
    from pathlib import Path

    kok = Path(__file__).resolve().parents[1]
    cikti = subprocess.run(
        ["git", "grep", "-n", "DeviceCommand(", "--", "app/"],
        cwd=str(kok), capture_output=True, text=True,
    ).stdout
    uretim_satirlari = [
        s for s in cikti.splitlines()
        if "models/device_command.py" not in s
    ]
    assert len(uretim_satirlari) == 1, (
        f"DeviceCommand birden fazla yerde uretiliyor; TTL kapsami disinda "
        f"bir kuyruk olusmus olabilir:\n" + "\n".join(uretim_satirlari)
    )
    assert "device_command_service" in uretim_satirlari[0]


def test_T18b_IEC104_kaynakli_bayat_komut_da_sonlanir(db, gateway):
    """IEC 104 komutu ayni tabloya yaziliyor; TTL onu da kapsamali."""
    cmd = komut_ekle(db, yas_sn=TTL + 300, actor_username="iec104:10.0.0.5")
    resp = poll(db)

    assert resp.commands == []
    db.refresh(cmd)
    assert (cmd.status, cmd.result_status) == ("failed", "expired")


# ==========================================================================
# T20 — yapilandirma dogrulamasi
# ==========================================================================


@pytest.mark.parametrize("gecersiz", [0, -1, -120])
def test_T20_gecersiz_TTL_reddedilir(gecersiz, monkeypatch):
    """`0 = kapali` gibi bir fail-open yolu OLMAMALI."""
    from app.core.config import Settings

    monkeypatch.setenv("COMMAND_MAX_AGE_SEC", str(gecersiz))
    with pytest.raises(Exception) as hata:
        Settings()
    assert "COMMAND_MAX_AGE_SEC" in str(hata.value)


def test_T20b_varsayilan_120():
    from app.core.config import Settings

    import inspect as _i
    kaynak = _i.getsource(Settings)
    assert "command_max_age_sec: int = 120" in kaynak, (
        "varsayilan 120 sn olmali ve kod icine gomulmus sihirli sayi olmamali"
    )


# ==========================================================================
# T21 — yapisal log
# ==========================================================================


def test_T21_expiration_yapisal_log(db, gateway, caplog):
    cmd = komut_ekle(db, yas_sn=TTL + 45)

    with caplog.at_level(logging.WARNING):
        poll(db)

    kayit = next(
        r for r in caplog.records if "command_expired_backend" in r.getMessage()
    )
    mesaj = kayit.getMessage()
    for alan in (
        "event=command_expired_backend",
        "gateway_code=GW-1",
        f"command_id={cmd.id}",
        "device_code=CIHAZ-A",
        "command=fault_reset",
        "dnp3_index=3",
        "created_at=",
        "age_sec=",
        f"ttl_sec={TTL}",
    ):
        assert alan in mesaj, f"log alani eksik: {alan}\n{mesaj}"
    # Sir/kimlik bilgisi sizmamali.
    for yasak in ("token", "api_key", "password", "secret"):
        assert yasak not in mesaj.lower()


# ==========================================================================
# T22 — gateway uyumlulugu (ekstra alan)
# ==========================================================================


def test_T22_created_at_opsiyonel_ve_eski_gateway_kirilmaz(db, gateway):
    """Ekstra alan mevcut sozlesmeyi BOZMAMALI.

    Saha gateway'i komut sozlugunu ACIK ALAN CIKARIMIYLA okuyor
    (`item["id"]`, `item.get("command")`, ...), kati bir sema ile degil;
    tanimadigi anahtarlari hic okumuyor. Burada backend tarafi kilitleniyor:
    gateway'in ZORUNLU gordugu alanlarin hepsi yerinde ve `created_at`
    yalnizca EKLENMIS bir alan.
    """
    komut_ekle(db, yas_sn=1)
    resp = poll(db)
    ham = json.loads(resp.model_dump_json())["commands"][0]

    # Gateway'in `item[...]` ile ZORUNLU okudugu alanlar.
    for zorunlu in ("id", "device_code", "dnp3_index"):
        assert zorunlu in ham, f"gateway'in zorunlu gordugu alan eksik: {zorunlu}"
    # `.get(...)` ile okudugu alanlar.
    for opsiyonel in ("command", "op_type", "count", "on_time_ms", "off_time_ms"):
        assert opsiyonel in ham
    # Yeni alan yalnizca EKLENMIS.
    assert "created_at" in ham
    # Sema `created_at` olmadan da gecerli olmali (eski kayitlar/uyumluluk).
    from app.schemas.gateway import GatewayConfigCommand

    GatewayConfigCommand(id=1, device_code="X", command="c", dnp3_index=0)
