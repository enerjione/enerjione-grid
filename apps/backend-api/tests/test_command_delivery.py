"""Komut teslim kirasi ve dayanikli kabul — backend yarisi (F3C).

KAPATILAN ARIZA
---------------
Backend `/pending` yanitini uretirken komutu `sent` isaretliyordu. Iki pencere
acikti ve ikisi de SESSIZ komut kaybina cikiyordu:

  A) backend `sent` COMMIT etti, HTTP yaniti gateway'e ULASMADI (ag/timeout)
  B) gateway yaniti bellege aldi, dayanikli deftere yazmadan oldu

Her ikisinde de: backend `sent`, gateway defteri bos, cihaz komutu hic almadi,
komut sonsuza kadar `sent` kaliyor. Operator "gonderildi" goruyor; kesici
surulmemis.

BU DOSYANIN KILITLEDIGI EN ONEMLI SEY
-------------------------------------
Teslimin yeniden denenebilir olmasi, FIZIKSEL KOMUTUN yeniden denenmesi
DEGILDIR. Ozellikle `test_KRITIK_kira_yenilense_bile_mutlak_TTL_asilmaz`
(spec §22) bunu kanitlar: 10:00'da uretilip teslimi kaybolan bir komut
10:03'te YENIDEN SUNULAMAZ.

SAAT DONDURULUYOR, `sleep` YOK
------------------------------
Kira ve TTL sinirlari gercek zamana dayanamaz. `now` uc noktasina disaridan
enjekte ediliyor (`_DonmusSaat`), boylece 119.999 / 120.000 / 120.001 ayrimi
KESIN yapiliyor.

GERCEK POSTGRES
---------------
Es zamanli iki poll'un ayni komutu iki kez kiralayamamasi `FOR UPDATE` satir
kilidine dayanir ve SQLite bunu SESSIZCE yok sayar. O iddia burada DEGIL,
`tests/integration/test_command_delivery_pg.py` icinde gercek Postgres
uzerinde kanitlanir.
"""

from __future__ import annotations

import base64
import json
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
from app.schemas.gateway import CommandDeliveryAckItem, CommandDeliveryAckRequest
from app.services import command_delivery_service as teslim

TTL = 120
KIRA = 30
EPOCH = "epoch-aaaa-1111"
EPOCH_YENI = "epoch-bbbb-2222"


# ---------------------------------------------------------------------------
# Kurulum
# ---------------------------------------------------------------------------


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
def ayarlar(monkeypatch):
    monkeypatch.setattr(settings, "command_max_age_sec", TTL, raising=False)
    monkeypatch.setattr(settings, "command_delivery_lease_sec", KIRA, raising=False)
    monkeypatch.setattr(settings, "command_delivery_max_attempts", 5, raising=False)
    monkeypatch.setattr(settings, "command_delivery_ack_required", True, raising=False)
    # Hiz sinirli uyari sayaci testler arasinda sizmasin.
    teslim._legacy_last_warn.clear()


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
    db.add(Device(
        code="CIHAZ-B", name="B", gateway_code="GW-2",
        ip_address="10.0.0.51", latitude=39.0, longitude=35.0,
    ))
    db.commit()
    return g


@pytest.fixture(autouse=True)
def token_dogrulamasi_baypas(monkeypatch, request):
    if "gateway" not in request.fixturenames:
        return

    def _sahte(db_, kod, token):
        return db_.scalars(select(Gateway).where(Gateway.code == kod)).first()

    monkeypatch.setattr("app.services.ingest_service.validate_gateway_token", _sahte)


@pytest.fixture(autouse=True)
def imza_baypas(monkeypatch):
    monkeypatch.setattr(
        gw_api, "_signed_json_response", lambda g, m, extra_headers=None: m
    )


class _DonmusSaat(datetime):
    """Uc noktasindaki `datetime.now()` icin sabit an."""

    _an: datetime = None  # type: ignore[assignment]

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return cls._an if tz is None else cls._an.astimezone(tz)


def baslik(epoch: str = EPOCH, surum: int = 1) -> str:
    ham = json.dumps({"v": surum, "epoch": epoch}, separators=(",", ":"))
    return base64.urlsafe_b64encode(ham.encode()).decode()


def komut_ekle(db, *, gateway_code="GW-1", device_code="CIHAZ-A", yas_sn=0.0, **kw):
    cmd = DeviceCommand(
        gateway_code=gateway_code,
        device_code=device_code,
        command="fault_reset",
        dnp3_index=3,
        status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=yas_sn),
        **kw,
    )
    db.add(cmd)
    db.commit()
    return cmd


def poll(db, *, kod="GW-1", epoch: str | None = EPOCH, an: datetime | None = None,
         monkeypatch=None, surum: int = 1):
    """`/pending` cagrisi. `epoch=None` -> ESKI gateway (baslik yok)."""
    h = baslik(epoch, surum) if epoch is not None else None
    if an is None:
        return gw_api.get_gateway_pending(
            kod, db=db, x_gateway_token="t", x_gateway_health=None, x_e1_delivery=h
        )
    _DonmusSaat._an = an
    monkeypatch.setattr(gw_api, "datetime", _DonmusSaat)
    monkeypatch.setattr(teslim, "datetime", _DonmusSaat)
    try:
        return gw_api.get_gateway_pending(
            kod, db=db, x_gateway_token="t", x_gateway_health=None, x_e1_delivery=h
        )
    finally:
        monkeypatch.setattr(gw_api, "datetime", datetime)
        monkeypatch.setattr(teslim, "datetime", datetime)


def ack(db, *, kod="GW-1", ciftler):
    return gw_api.report_command_delivery_acks(
        kod,
        payload=CommandDeliveryAckRequest(
            acks=[CommandDeliveryAckItem(command_id=c, delivery_token=t) for c, t in ciftler]
        ),
        db=db,
        x_gateway_token="t",
    )


# ---------------------------------------------------------------------------
# T01 — mutlu yol
# ---------------------------------------------------------------------------


def test_T01_kirala_ack_sent(db, gateway):
    """Teslim -> dayanikli ACK -> `sent`. Tek fiziksel teslim."""
    cmd = komut_ekle(db)
    yanit = poll(db)

    assert [c.id for c in yanit.commands] == [cmd.id]
    jeton = yanit.commands[0].delivery_token
    assert jeton, "teslim jetonu gonderilmedi"

    db.refresh(cmd)
    # EN ONEMLI IDDIA: yanit uretildi diye komut `sent` OLMAZ.
    assert cmd.status == "pending", "ACK'siz `sent` — A/B penceresi hala acik"
    assert cmd.sent_at is None
    assert cmd.delivery_attempt == 1
    assert cmd.delivery_gateway_epoch == EPOCH

    sonuc = ack(db, ciftler=[(cmd.id, jeton)])
    assert (sonuc.accepted, sonuc.rejected) == (1, 0)

    db.refresh(cmd)
    assert cmd.status == "sent"
    assert cmd.sent_at is not None


def test_T01b_delivery_not_after_created_at_arti_TTL(db, gateway):
    """Son kullanma ani MUTLAK: `created_at + COMMAND_MAX_AGE_SEC`."""
    cmd = komut_ekle(db, yas_sn=10)
    k = poll(db).commands[0]
    beklenen = cmd.created_at.replace(tzinfo=timezone.utc) + timedelta(seconds=TTL)
    assert k.delivery_not_after == beklenen


# ---------------------------------------------------------------------------
# T02 / T13 — teslim kaybi ve GUVENLI yeniden teslim
# ---------------------------------------------------------------------------


def test_T02_yanit_kaybolursa_kira_dolunca_yeniden_sunulur(db, gateway, monkeypatch):
    """Yanit gateway'e ulasmadi: kira dolunca ayni komut YENIDEN sunulur.

    Bu TESLIM yeniden denemesidir; fiziksel calistirma degildir. Gateway
    defteri ayni `command_id` icin ikinci CROB uretmez.
    """
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db)

    ilk = poll(db, an=t0, monkeypatch=monkeypatch).commands[0]
    # Kira sururken YENIDEN SUNULMAZ.
    assert poll(db, an=t0 + timedelta(seconds=KIRA - 1), monkeypatch=monkeypatch).commands == []
    # Kira dolunca sunulur.
    ikinci = poll(db, an=t0 + timedelta(seconds=KIRA + 1), monkeypatch=monkeypatch).commands
    assert [c.id for c in ikinci] == [cmd.id]

    # JETON DONDURULMEZ — teslim kimligi sabit kalir.
    assert ikinci[0].delivery_token == ilk.delivery_token
    db.refresh(cmd)
    assert cmd.delivery_attempt == 2
    assert cmd.status == "pending"


def test_T13_kira_dolmadan_ikinci_poll_bos_doner(db, gateway, monkeypatch):
    t0 = datetime.now(timezone.utc)
    komut_ekle(db)
    assert len(poll(db, an=t0, monkeypatch=monkeypatch).commands) == 1
    assert poll(db, an=t0 + timedelta(seconds=1), monkeypatch=monkeypatch).commands == []


# ---------------------------------------------------------------------------
# T14 / T22 (spec §22) — MUTLAK TTL, kiralama onu OTELEMEZ
# ---------------------------------------------------------------------------


def test_KRITIK_kira_yenilense_bile_mutlak_TTL_asilmaz(db, gateway, monkeypatch):
    """SPEC §22 — bu test olmadan F3C PASS SAYILMAZ.

        COMMAND_MAX_AGE_SEC=120
        10:00:00  komut olusturuldu
        10:00:05  ilk kira COMMIT edildi
                  HTTP yaniti gateway'e ULASMADI
        10:03:00  kira suresi coktan doldu -> yeniden teslim FIRSATI

    Beklenen: komut KESINLIKLE yeniden sunulmaz. "TTL yalnizca hic
    kiralanmamis komutta uygulanir" kurali burada 3 dakikalik bir kesici
    komutunu fiziksel sisteme uygulatirdi.
    """
    t_olusum = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
    cmd = komut_ekle(db)
    cmd.created_at = t_olusum
    db.commit()

    # 10:00:05 — ilk kira
    ilk = poll(db, an=t_olusum + timedelta(seconds=5), monkeypatch=monkeypatch)
    assert len(ilk.commands) == 1, "ilk teslim yapilmaliydi"
    db.refresh(cmd)
    assert cmd.delivery_attempt == 1

    # 10:03:00 — yanit kayboldu, kira doldu, TTL de doldu
    gec = poll(db, an=t_olusum + timedelta(seconds=180), monkeypatch=monkeypatch)
    assert gec.commands == [], (
        "BAYAT KOMUT YENIDEN SUNULDU — kiralama mutlak son kullanma anini "
        "oteledi; 3 dakikalik komut fiziksel olarak uygulanabilirdi"
    )

    db.refresh(cmd)
    assert cmd.status == "failed"
    # Kiralanmisti: gateway kabul etmis OLABILIR, bu yuzden belirsizlik
    # durustce temsil edilir (ve gec gelen gercek sonuc bunu duzeltebilir).
    assert cmd.result_status == teslim.RESULT_UNKNOWN


def test_T14_hic_kiralanmamis_bayat_komut_expired_olur(db, gateway, monkeypatch):
    """Hic sunulmamis komut KESINLIKLE cihaza gitmedi -> `expired`."""
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db, yas_sn=TTL + 60)
    assert poll(db, an=t0, monkeypatch=monkeypatch).commands == []

    db.refresh(cmd)
    assert (cmd.status, cmd.result_status) == ("failed", teslim.RESULT_EXPIRED)
    assert cmd.sent_at is None
    assert cmd.delivery_attempt == 0


def test_T15_uzun_sure_cevrimdisi_gateway_bayat_komutu_calistiramaz(db, gateway, monkeypatch):
    """Gateway 4 saat sonra dondu: bekleyen komut teslim EDILMEZ."""
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db)
    assert poll(db, an=t0 + timedelta(hours=4), monkeypatch=monkeypatch).commands == []
    db.refresh(cmd)
    assert cmd.status == "failed"


def test_T14b_tam_TTL_sinirinda_hala_teslim_edilir(db, gateway, monkeypatch):
    """SINIR: `now == delivery_not_after` henuz gecmis DEGILDIR."""
    t_olusum = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
    cmd = komut_ekle(db)
    cmd.created_at = t_olusum
    db.commit()
    tam = t_olusum + timedelta(seconds=TTL)
    assert len(poll(db, an=tam, monkeypatch=monkeypatch).commands) == 1
    # Bir mikrosaniye sonrasi BAYAT.
    db.refresh(cmd)
    cmd.delivery_lease_until = None
    cmd.delivery_attempt = 0
    db.commit()
    assert poll(db, an=tam + timedelta(microseconds=1), monkeypatch=monkeypatch).commands == []


# ---------------------------------------------------------------------------
# T21 — DEFTER KIMLIGI (epoch) surekliligi
# ---------------------------------------------------------------------------


def test_T21_defter_sifirlanirsa_OTOMATIK_TESLIM_YOK(db, gateway, monkeypatch):
    """Gateway defteri sifirlandi -> tekrar-onleme kaniti yok -> FAIL-CLOSED.

    Defter sifirlandiktan sonra komutu yeniden sunmak, gateway'in onu YENI
    sanip CROB'u TEKRARLAMASI demek olurdu.
    """
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db)
    poll(db, an=t0, monkeypatch=monkeypatch)
    db.refresh(cmd)
    assert cmd.delivery_gateway_epoch == EPOCH

    # Kira doldu VE gateway yeni bir defter kimligiyle geldi.
    sonra = poll(
        db, epoch=EPOCH_YENI, an=t0 + timedelta(seconds=KIRA + 1), monkeypatch=monkeypatch
    )
    assert sonra.commands == [], "defter sifirlanmisken komut YENIDEN SUNULDU"

    db.refresh(cmd)
    assert cmd.status == "failed"
    assert cmd.result_status == teslim.RESULT_DELIVERY_STATE_LOST
    assert "dogrulayin" in (cmd.result_error or "")


def test_T21b_ayni_epoch_devam_ederse_yeniden_teslim_normal(db, gateway, monkeypatch):
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db)
    poll(db, an=t0, monkeypatch=monkeypatch)
    sonra = poll(db, epoch=EPOCH, an=t0 + timedelta(seconds=KIRA + 1), monkeypatch=monkeypatch)
    assert [c.id for c in sonra.commands] == [cmd.id]


# ---------------------------------------------------------------------------
# Deneme siniri
# ---------------------------------------------------------------------------


def test_deneme_siniri_asilinca_terminal(db, gateway, monkeypatch):
    """Gateway hicbir zaman ACK gondermezse komut sonsuza kadar sunulmaz."""
    monkeypatch.setattr(settings, "command_delivery_max_attempts", 3, raising=False)
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db)

    for i in range(3):
        an = t0 + timedelta(seconds=i * (KIRA + 1))
        assert len(poll(db, an=an, monkeypatch=monkeypatch).commands) == 1

    son = poll(db, an=t0 + timedelta(seconds=3 * (KIRA + 1)), monkeypatch=monkeypatch)
    assert son.commands == []
    db.refresh(cmd)
    assert (cmd.status, cmd.result_status) == ("failed", teslim.RESULT_DELIVERY_FAILED)


# ---------------------------------------------------------------------------
# T05 / T06 / T24 / T25 — ACK dogrulama
# ---------------------------------------------------------------------------


def test_T06_mukerrer_ACK_idempotent(db, gateway):
    """ACK yaniti kaybolursa gateway tekrar gonderir — no-op ve KABUL."""
    cmd = komut_ekle(db)
    jeton = poll(db).commands[0].delivery_token

    ilk = ack(db, ciftler=[(cmd.id, jeton)])
    ikinci = ack(db, ciftler=[(cmd.id, jeton)])
    assert (ilk.accepted, ilk.rejected) == (1, 0)
    assert (ikinci.accepted, ikinci.rejected) == (1, 0), (
        "mukerrer ACK reddedilirse gateway sonsuz yeniden denemeye girer"
    )

    db.refresh(cmd)
    assert cmd.status == "sent"


def test_T25_yanlis_jeton_reddedilir(db, gateway):
    cmd = komut_ekle(db)
    poll(db)
    sonuc = ack(db, ciftler=[(cmd.id, "uydurma-jeton")])
    assert (sonuc.accepted, sonuc.rejected) == (0, 1)
    db.refresh(cmd)
    assert cmd.status == "pending", "yanlis jetonla `sent` yapildi"


def test_T24_baska_gateway_ACK_edemez(db, gateway):
    """GW-2, GW-1'in komutunu ACK edemez (IDOR koruma)."""
    cmd = komut_ekle(db)
    jeton = poll(db).commands[0].delivery_token

    sonuc = ack(db, kod="GW-2", ciftler=[(cmd.id, jeton)])
    assert (sonuc.accepted, sonuc.rejected) == (0, 1)
    db.refresh(cmd)
    assert cmd.status == "pending"


def test_kiralanmamis_komut_ACK_edilemez(db, gateway):
    """Jeton hic uretilmemisse ACK kabul edilmez."""
    cmd = komut_ekle(db)
    sonuc = ack(db, ciftler=[(cmd.id, "her-neyse")])
    assert (sonuc.accepted, sonuc.rejected) == (0, 1)


def test_terminal_komut_ACK_ile_dirilmez(db, gateway, monkeypatch):
    """`failed` bir komutu ACK `sent`e geri alamaz."""
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db)
    jeton = poll(db, an=t0, monkeypatch=monkeypatch).commands[0].delivery_token
    poll(db, an=t0 + timedelta(seconds=TTL + KIRA + 10), monkeypatch=monkeypatch)
    db.refresh(cmd)
    assert cmd.status == "failed"

    sonuc = ack(db, ciftler=[(cmd.id, jeton)])
    assert (sonuc.accepted, sonuc.rejected) == (0, 1)
    db.refresh(cmd)
    assert cmd.status == "failed", "terminal komut ACK ile dirildi"


def test_bilinmeyen_komut_ACK_reddedilir(db, gateway):
    sonuc = ack(db, ciftler=[(999999, "x")])
    assert (sonuc.accepted, sonuc.rejected) == (0, 1)


def test_ACK_partisi_sinirli(db, gateway):
    from fastapi import HTTPException

    fazla = [(i, "x") for i in range(teslim.MAX_ACK_BATCH + 1)]
    with pytest.raises(HTTPException) as e:
        ack(db, ciftler=fazla)
    assert e.value.status_code == 413


def test_jeton_loglanmaz(db, gateway, caplog):
    """Teslim jetonu bir yetki kanitidir; log'a DUSMEMELI."""
    import logging

    cmd = komut_ekle(db)
    with caplog.at_level(logging.DEBUG):
        jeton = poll(db).commands[0].delivery_token
        ack(db, ciftler=[(cmd.id, jeton)])
        ack(db, ciftler=[(cmd.id, "yanlis")])
    assert jeton not in caplog.text, "teslim jetonu loglandi"


# ---------------------------------------------------------------------------
# T16 — eski gateway
# ---------------------------------------------------------------------------


def test_T16_eski_gateway_fail_closed(db, gateway, monkeypatch):
    """Yetenek bildirmeyen gateway'e komut TESLIM EDILMEZ (varsayilan)."""
    cmd = komut_ekle(db)
    yanit = poll(db, epoch=None)
    assert yanit.commands == []
    db.refresh(cmd)
    # Kuyrukta BIRAKILIR: gateway yukseltilirse hala TTL icindeyse teslim edilir.
    assert cmd.status == "pending"
    assert cmd.delivery_attempt == 0


def test_T16b_eski_gateway_override_ile_calisir(db, gateway, monkeypatch):
    """Gecis kaldiraci: `COMMAND_DELIVERY_ACK_REQUIRED=false` -> v2.96 davranisi."""
    monkeypatch.setattr(settings, "command_delivery_ack_required", False, raising=False)
    cmd = komut_ekle(db)
    yanit = poll(db, epoch=None)
    assert [c.id for c in yanit.commands] == [cmd.id]
    assert yanit.commands[0].delivery_token is None
    db.refresh(cmd)
    assert cmd.status == "sent", "eski yolda `sent` aninda olmali"


def test_T16c_eski_gateway_yukseltilince_bekleyen_komut_teslim_edilir(db, gateway):
    """Fail-closed komutu OLDURMEZ; gateway yukseltilince teslim edilir."""
    cmd = komut_ekle(db)
    assert poll(db, epoch=None).commands == []
    assert [c.id for c in poll(db, epoch=EPOCH).commands] == [cmd.id]


def test_bozuk_teslim_basligi_eski_gateway_sayilir(db, gateway):
    """Bozuk baslik sessizce YENI protokole gecmez — fail-closed."""
    cmd = komut_ekle(db)
    yanit = gw_api.get_gateway_pending(
        "GW-1", db=db, x_gateway_token="t", x_gateway_health=None,
        x_e1_delivery="bu-base64-degil!!!",
    )
    assert yanit.commands == []
    db.refresh(cmd)
    assert cmd.status == "pending"


# ---------------------------------------------------------------------------
# Izolasyon
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bagimsiz inceleme bulgulari — REGRESYON KILITLERI
# ---------------------------------------------------------------------------


def test_REGRESYON_kiralanmis_komut_ESKI_YOLA_dusemez(db, gateway, monkeypatch):
    """BULGU: eski yol jeton/kira/defter kimligi SORMUYORDU.

    Senaryo: komut yeni protokolle kiralandi (jeton + epoch yazildi), sonra
    gateway baslik gondermeyen bir surume geri alindi ve operator gecis icin
    `COMMAND_DELIVERY_ACK_REQUIRED=false` yapti. Eski yol komutu hicbir
    kontrolden gecirmeden yeniden teslim ediyor ve ANINDA `sent` yaziyordu.
    Gateway defteri de kaybolmussa ayni komut cihazda IKINCI KEZ uygulanirdi.
    """
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db)

    # 1) Yeni protokolle kiralandi.
    assert len(poll(db, an=t0, monkeypatch=monkeypatch).commands) == 1
    db.refresh(cmd)
    assert cmd.delivery_token and cmd.delivery_gateway_epoch

    # 2) Gateway geri alindi (baslik yok) + gecis ayari acildi.
    monkeypatch.setattr(settings, "command_delivery_ack_required", False, raising=False)
    yanit = poll(db, epoch=None, an=t0 + timedelta(seconds=1), monkeypatch=monkeypatch)

    assert yanit.commands == [], (
        "kiralanmis komut ESKI YOLDAN yeniden teslim edildi — defter kimligi "
        "hic sorulmadi; cift fiziksel calistirma riski"
    )
    db.refresh(cmd)
    assert cmd.status == "pending", "eski yol komutu dogrudan `sent` yapti"


def test_REGRESYON_fail_closed_dalinda_TTL_ISLER(db, gateway, monkeypatch):
    """BULGU: fail-closed dalinda komut SONSUZA KADAR `pending` kaliyordu.

    `kirala` yalnizca yetenek bildiren gateway'de kosuyor, sonuc supurucusu
    ise `status='sent'` ariyor. Yani eski gateway'e fail-closed davranildiginda
    hicbir sey o satirlara bakmiyordu ve "TTL dolunca expired olur" sozu
    tutulmuyordu.
    """
    t0 = datetime.now(timezone.utc)
    cmd = komut_ekle(db)

    # Yetenek yok + fail-closed: teslim edilmez ama TTL dolmadi -> beklemeye devam.
    assert poll(db, epoch=None, an=t0, monkeypatch=monkeypatch).commands == []
    db.refresh(cmd)
    assert cmd.status == "pending"

    # TTL dolunca SONLANDIRILMALI.
    assert poll(
        db, epoch=None, an=t0 + timedelta(seconds=TTL + 1), monkeypatch=monkeypatch
    ).commands == []
    db.refresh(cmd)
    assert cmd.status == "failed", "eski gateway'de komut sonsuza kadar `pending` kaldi"
    assert cmd.result_status == teslim.RESULT_EXPIRED
    assert cmd.delivery_attempt == 0, "hic teslim edilmedi, deneme sayaci artmamali"


def test_REGRESYON_ASCII_disi_jeton_partiyi_dusurmez(db, gateway):
    """BULGU: `compare_digest` ASCII disi str'de TypeError atip TUM PARTIYI dusuruyordu.

    Ayni partideki GECERLI ACK de kayboluyor, uc 500 doner ve gateway 5xx'i
    gecici sayacagi icin ayni partiyi sonsuza kadar yeniden dener.
    """
    cmd1 = komut_ekle(db)
    cmd2 = komut_ekle(db)
    komutlar = {c.id: c.delivery_token for c in poll(db).commands}

    sonuc = ack(db, ciftler=[(cmd1.id, "bozuk-jeton-ç-ğ-ü"), (cmd2.id, komutlar[cmd2.id])])

    assert sonuc.rejected == 1
    assert sonuc.accepted == 1, "ayni partideki GECERLI ACK kayboldu"
    db.refresh(cmd2)
    assert cmd2.status == "sent"


def test_REGRESYON_terminal_gecisler_denetim_kaydi_birakir(db, gateway, monkeypatch):
    """BULGU: kira yolunun terminal gecisleri `record_event` yazmiyordu.

    `delivery_state_lost` ozellikle onemli: gateway defteri sifirlandigi icin
    komutun uygulanip uygulanmadigi BILINMIYOR ve otomatik teslim durduruldu.
    Bu karar operatore ulasmali; yalnizca log'a yazmak onu gorunmez kilardi.
    """
    from app.models.system_event import SystemEvent

    t0 = datetime.now(timezone.utc)
    komut_ekle(db)
    poll(db, an=t0, monkeypatch=monkeypatch)
    poll(db, epoch=EPOCH_YENI, an=t0 + timedelta(seconds=KIRA + 1), monkeypatch=monkeypatch)

    olaylar = db.scalars(
        select(SystemEvent).where(
            SystemEvent.event_type == "device_command_delivery_failed"
        )
    ).all()
    assert len(olaylar) == 1, "defter kimligi kaybi denetim kaydi birakmadi"
    assert teslim.RESULT_DELIVERY_STATE_LOST in (olaylar[0].message or "")


def test_REGRESYON_expired_denetim_kaydi_URETMEZ(db, gateway, monkeypatch):
    """F3B davranisi korunuyor: TTL'nin normal sonucu olay uretmez (gurultu)."""
    from app.models.system_event import SystemEvent

    komut_ekle(db, yas_sn=TTL + 60)
    poll(db, an=datetime.now(timezone.utc), monkeypatch=monkeypatch)

    olaylar = db.scalars(
        select(SystemEvent).where(
            SystemEvent.event_type == "device_command_delivery_failed"
        )
    ).all()
    assert olaylar == []


def test_REGRESYON_ACK_ve_pending_AYNI_KILIT_SIRASINI_kullanir():
    """BULGU: iki uc satirlari FARKLI sirada kilitliyordu -> DEADLOCK.

    Gercek Postgres'te uretildi: `kirala` `id ASC` ile kilitlerken ACK sorgusu
    siralamasiz bir index taramasiyla fiziksel dizilime gore kilitliyordu. Iki
    uc ayni iki komuta ayni anda dokundugunda Postgres deadlock uretti ve
    KURBAN `/pending` oldu — yani SCADA komut poll'u 500 dondu ve o turda
    hicbir komut teslim edilmedi.

    Deadlock'un YOKLUGUNU es zamanlilikla kanitlamak guvenilmez (gecmesi tesadufi
    olabilir); bunun yerine ONU IMKANSIZ KILAN yapisal degismez sabitleniyor:
    her iki sorgu da satirlari `id` sirasina gore kilitler.
    """
    import re

    from sqlalchemy.dialects import postgresql

    def _sql(stmt) -> str:
        return str(stmt.compile(dialect=postgresql.dialect()))

    kirala_sql = _sql(
        select(DeviceCommand)
        .where(DeviceCommand.gateway_code == "GW-1", DeviceCommand.status == "pending")
        .order_by(DeviceCommand.id.asc())
        .with_for_update()
    )
    # Servisteki GERCEK ifadeleri kaynak uzerinden dogrula: her iki sorgu da
    # `.order_by(DeviceCommand.id.asc())` tasimali.
    import inspect as _inspect

    kaynak = _inspect.getsource(teslim)
    for fonksiyon in ("def kirala(", "def ack_uygula(", "def bayatlari_sonlandir("):
        govde = kaynak.split(fonksiyon, 1)[1].split("\ndef ", 1)[0]
        assert "with_for_update()" in govde, f"{fonksiyon} satir kilidi kullanmiyor"
        assert "order_by(DeviceCommand.id.asc())" in govde, (
            f"{fonksiyon} icindeki kilitli sorgu `id` sirasina gore siralanmiyor — "
            "farkli kilit sirasi DEADLOCK uretir ve kurban SCADA komut poll'u olur"
        )

    assert re.search(r"ORDER BY .*\.id ASC", kirala_sql)


def test_baska_gatewayin_komutu_kiralanmaz(db, gateway, monkeypatch):
    komut_ekle(db, gateway_code="GW-2", device_code="CIHAZ-B")
    assert poll(db, kod="GW-1").commands == []


def test_T17_IEC104_kaynakli_komut_ayni_yoldan_gecer(db, gateway):
    """IEC 104 kaynakli komut da ayni teslim protokolunden gecer."""
    cmd = komut_ekle(db, actor_username=None)
    jeton = poll(db).commands[0].delivery_token
    ack(db, ciftler=[(cmd.id, jeton)])
    db.refresh(cmd)
    assert cmd.status == "sent"
