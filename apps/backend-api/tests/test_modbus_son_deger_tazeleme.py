"""Modbus: degismeyen sinyallerin son degeri worker'a ulasmali.

YASANAN SORUN (2026-08-13)
-------------------------
SCADA Modbus uzerinden hicbir deger alamiyordu. `modbus-outbound` worker'inin
tek besleme kanali canli NATS akisiydi ve o akis ancak cihaz YENI OLCUM
yayinladiginda akar. Modbus'ta "deger henuz gelmedi" diye bir hal YOKTUR:
yazilmamis her adres 0 doner ve SCADA bunu gercek bir olcum gibi okur.

Sonuc: degismeyen sinyaller (ariza bayraklari, nominal degerler, konum),
yeniden baslatilmis servis (tuketici `DeliverPolicy.NEW` — gecmis
oynatilmaz) ve yeni kurulmus hedefler icin register'lar sonsuza dek 0
kaliyordu. Canli Degerler ekrani gercek degeri gosterirken Modbus 0
gosteriyordu.

`/internal/modbus-values` bu boslugu kapatan uctur: worker periyodik olarak
`telemetry_latest` (Canli Degerler ekraninin AYNI kaynagi) son degerlerini
cekip eksik register'lari doldurur. Bu testler ucun sozlesmesini kilitler.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (Base.metadata dolsun)
from app.api import internal
from app.core.config import settings
from app.db.base import Base
from app.models.device import Device
from app.models.outbound_target import OutboundModbusSlot, OutboundTarget
from app.models.telemetry_latest import TelemetryLatest


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
    d = Device(
        code=kod,
        name=f"Cihaz {kod}",
        ip_address=f"10.0.0.{abs(hash(kod)) % 250 + 1}",
        latitude=39.0,
        longitude=35.0,
    )
    db.add(d)
    db.flush()
    return d


def _hedef(db, *, ad: str = "SCADA-A", aktif: bool = True) -> OutboundTarget:
    # `name` tekil kisitli — her hedefe ayri ad.
    t = OutboundTarget(name=ad, protocol="modbus", is_active=aktif)
    db.add(t)
    db.flush()
    return t


def _slot(db, hedef: OutboundTarget, cihaz: Device, index: int = 0) -> None:
    db.add(
        OutboundModbusSlot(
            target_id=hedef.id, device_id=cihaz.id,
            slot_index=index, unit_id=1, block_start=100 * index,
        )
    )
    db.flush()


def _son_deger(
    db, cihaz: Device, anahtar: str, *, value=None, value_string=None, ts=None
) -> None:
    an = ts or datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    db.add(
        TelemetryLatest(
            device_id=cihaz.id,
            signal_key=anahtar,
            value=value,
            value_string=value_string,
            quality="good",
            source_timestamp=an,
            updated_at=an,
        )
    )
    db.flush()


def _cagir(db, since: str | None = None):
    return internal.list_modbus_values_internal(
        since=since, db=db, x_service_token=settings.internal_service_token
    )


def test_son_degerler_cihaz_koduyla_donuyor(db):
    """Asil duzeltme: worker'in register'a yazacagi son deger geliyor mu?"""
    hedef = _hedef(db)
    cihaz = _cihaz(db, "DEV-001")
    _slot(db, hedef, cihaz)
    _son_deger(db, cihaz, "master.actual_voltage", value=231.5)

    yanit = _cagir(db)

    assert yanit["count"] == 1, yanit
    satir = yanit["values"][0]
    # Worker plani `device_code` ile eslestirir; device_id ile degil.
    assert satir["device_code"] == "DEV-001"
    assert satir["signal_key"] == "master.actual_voltage"
    assert satir["value"] == pytest.approx(231.5)
    # Damga SART: worker bununla "canli akistan gelen deger daha mi taze"
    # sorusunu cevapliyor. Bos gelirse taze deger bayat satirla ezilebilirdi.
    assert satir["source_timestamp"], "kaynak damgasi bos — tazelik karsilastirmasi yapilamaz"


def test_metin_alaninda_tasinan_deger_de_geliyor(db):
    """DNP3 Group 110 sinyallerinde sayisal alan bos, deger `value_string`de.

    Worker bu ikiligi zaten biliyor (bkz. consumer.py `value_string`
    fallback'i); uc de iki alani birden tasimali, aksi halde o sinyaller
    tazelemeye HIC girmez.
    """
    hedef = _hedef(db)
    cihaz = _cihaz(db, "DEV-002")
    _slot(db, hedef, cihaz)
    _son_deger(db, cihaz, "master.actual_current", value=None, value_string="45.0")

    satir = _cagir(db)["values"][0]

    assert satir["value"] is None
    assert satir["value_string"] == "45.0"


def test_modbus_hedefi_yoksa_sorgu_hic_kurulmaz(db):
    """Modbus kullanmayan kurulumda bu uc bedava olmali."""
    cihaz = _cihaz(db, "DEV-003")
    _son_deger(db, cihaz, "master.actual_voltage", value=100.0)

    yanit = _cagir(db)

    assert yanit["values"] == []
    assert yanit["count"] == 0


def test_artimli_cekim_yalnizca_degisen_satirlari_dondurur(db):
    """600 cihazda tam liste ~115.000 satir — her turda cekilemez.

    `/signals/live` bu desenle (istek basina yuz megabaytlik yanit) backend'i
    OOM'a goturmustu. Bu yuzden worker ilk turda tohumlama yapar, sonra
    yalnizca degisenleri ister.
    """
    hedef = _hedef(db)
    cihaz = _cihaz(db, "DEV-009")
    _slot(db, hedef, cihaz)
    eski = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    yeni = datetime(2026, 8, 13, 9, 30, tzinfo=timezone.utc)
    _son_deger(db, cihaz, "master.actual_voltage", value=100.0, ts=eski)
    _son_deger(db, cihaz, "master.actual_current", value=5.0, ts=yeni)

    tam = _cagir(db)
    assert tam["count"] == 2
    assert tam["full"] is True, "since verilmediginde tur TAM olmali"
    assert tam["max_updated_at"], "artimli cekim icin esik dondurulmuyor"

    # Worker esigi backend'in verdigi degerden alir, kendi saatinden DEGIL.
    artimli = _cagir(db, since=tam["max_updated_at"])

    assert artimli["full"] is False
    kodlar = {s["signal_key"] for s in artimli["values"]}
    # Sinir `>=`: esikteki satir tekrar gelir (kaybolmasin), eski satir gelmez.
    assert "master.actual_current" in kodlar
    assert "master.actual_voltage" not in kodlar, (
        "esigin altindaki satir artimli turda da geldi — yanit kuculmuyor"
    )


def test_bozuk_since_tam_listeye_donuyor(db):
    """Esik bozulursa yanit vermemek yerine tohumlamaya donulmeli.

    Aksi halde worker'in esigi bir kez bozuldugunda register'lar sessizce
    guncellenmeyi birakir — teshisi en zor ariza turu.
    """
    hedef = _hedef(db)
    cihaz = _cihaz(db, "DEV-010")
    _slot(db, hedef, cihaz)
    _son_deger(db, cihaz, "master.actual_voltage", value=100.0)

    yanit = _cagir(db, since="bu-bir-tarih-degil")

    assert yanit["full"] is True
    assert yanit["count"] == 1


def test_bos_artimli_turda_esik_dondurulmez(db):
    """Degisen satir yoksa `max_updated_at` bos doner ve worker esigini KORUR.

    Yanlisi: bos turda esigi sifirlamak — her sessiz turdan sonra tam listeye
    donulur ve artimli cekmenin anlami kalmaz.
    """
    hedef = _hedef(db)
    cihaz = _cihaz(db, "DEV-011")
    _slot(db, hedef, cihaz)
    _son_deger(db, cihaz, "master.actual_voltage", value=100.0,
               ts=datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc))

    yanit = _cagir(db, since="2026-08-13T23:00:00+00:00")

    assert yanit["count"] == 0
    assert yanit["max_updated_at"] is None


def test_pasif_hedefin_cihazlari_kapsam_disinda(db):
    """Pasif hedef yayinda degildir; onun cihazlarini tazelemek yuk israfi."""
    pasif = _hedef(db, aktif=False)
    cihaz = _cihaz(db, "DEV-004")
    _slot(db, pasif, cihaz)
    _son_deger(db, cihaz, "master.actual_voltage", value=100.0)

    assert _cagir(db)["count"] == 0


def test_slotu_olmayan_cihaz_donmez(db):
    """Slot yoksa cihaz adres planinda da yok — degeri yazilacak adres yok.

    (Kapasiteye sigmadigi icin plana alinmamis cihazlar bu durumda olur;
    onlar icin sorun tazeleme degil, hedefin tavani — bkz.
    test_modbus_capacity_overflow.py.)
    """
    hedef = _hedef(db)
    planli = _cihaz(db, "DEV-005")
    plansiz = _cihaz(db, "DEV-006")
    _slot(db, hedef, planli)
    _son_deger(db, planli, "master.actual_voltage", value=100.0)
    _son_deger(db, plansiz, "master.actual_voltage", value=200.0)

    kodlar = {s["device_code"] for s in _cagir(db)["values"]}

    assert kodlar == {"DEV-005"}


def test_iki_hedefte_ayni_cihaz_tek_kez_donuyor(db):
    """Ayni cihaz iki Modbus hedefinde olabilir; satir COGALMAMALI.

    Cogalirsa worker ayni degeri hedef sayisi kadar yazar ve sayaclar
    yaniltici sisirir (teshis "kac nokta tazelendi" sorusuna dayanir).
    """
    a = _hedef(db, ad="SCADA-A")
    b = _hedef(db, ad="SCADA-B")
    cihaz = _cihaz(db, "DEV-007")
    _slot(db, a, cihaz)
    _slot(db, b, cihaz)
    _son_deger(db, cihaz, "master.actual_voltage", value=100.0)

    assert _cagir(db)["count"] == 1


def test_gecersiz_token_401(db):
    """Ic uc: yanlis token ile son degerler disari cikmamali."""
    from fastapi import HTTPException

    _hedef(db)
    with pytest.raises(HTTPException) as hata:
        internal.list_modbus_values_internal(db=db, x_service_token="yanlis-token")
    assert hata.value.status_code == 401


def test_damga_iso_formatinda_ve_saat_dilimli(db):
    """Worker damgalari `fromisoformat` ile cozer; naive damga karsilastirmayi
    bozar (aware/naive karsilastirmasi TypeError firlatir)."""
    hedef = _hedef(db)
    cihaz = _cihaz(db, "DEV-008")
    _slot(db, hedef, cihaz)
    an = datetime.now(timezone.utc) - timedelta(minutes=5)
    _son_deger(db, cihaz, "master.actual_voltage", value=1.0, ts=an)

    metin = _cagir(db)["values"][0]["source_timestamp"]
    cozulen = datetime.fromisoformat(metin)

    assert cozulen.tzinfo is not None, (
        "damga saat dilimsiz dondu — worker tarafinda naive/aware "
        "karsilastirmasi tazeleme turunu dusurur"
    )
