"""Arsiv (historian) ayarinin ARAYUZDEN yonetilmesi.

NEDEN BU DOSYA VAR
------------------
`historize` ve `historize_deadband` kolonlari migration 0032'den beri DB'de
duruyor ve `historian_policy` onlara gore karar veriyordu — ama sozlesmede
(pydantic sema + types.ts) YOKTULAR. Yani sistem yazma yukunu kisiyor,
kullanici bunu ne GOREBILIYOR ne DEGISTIREBILIYORDU. Yuk testinde ~15.000
sinyal/sn girip arsive ~2.000 satir/sn dusuyor; farki tam olarak bu iki alan
eliyor. Ayari kullaniciya acmak, dogrudan disk ve yazma yukunu kullanicinin
eline vermek demek.

BU DOSYANIN SINAVI
------------------
Ayari acmak, YANLIS ayarlamanin da yolunu acar ve bu hata SESSIZDIR: ekran
calisir, alarm calisir, canli deger akar. Eksik olan yalnizca "ariza ne zaman
gecti" sorusuna cevap verebilmektir ve bu, ancak bir ariza sonrasi "kayit
yok" denildiginde fark edilir. Bu yuzden testler iki seye bakar:

  1. ARIZA SINYALININ ARSIVI SESSIZCE KAPANAMAZ (binary / binary_output),
  2. OLU BANT, MOTORUN UYGULAMADIGI bir tipte varmis gibi GORUNEMEZ.

Kural SUNUCUDA: arayuzde durmasi yetmezdi, elle atilan bir istek ya da bir
betik onu tamamen atlardi.

NEDEN TestClient DEGIL
----------------------
Proje httpx'e bagli degil (bkz. `test_route_auth_boundary.py`). Router
fonksiyonlari duz fonksiyon; dogrudan cagriliyorlar.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (tum tablolari kaydeder)
from app.api import signals as signals_api
from app.db.base import Base
from app.models.enums import UserRole
from app.models.signal_catalog import SignalCatalog
from app.models.system_event import SystemEvent
from app.models.user import User
from app.schemas.signal_catalog import (
    SignalCatalogRead,
    SignalCatalogUpdate,
    SignalHistorianBulkUpdate,
)
from app.services import historian_policy as hp
from app.services import signal_catalog_service as scs

#: tests/ -> backend-api/ -> apps/ ; kardes paket `apps/frontend-web`.
FE_KOK = Path(__file__).resolve().parents[2] / "frontend-web" / "src"


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


@pytest.fixture()
def kurulumcu():
    return User(id=1, username="kurulumcu", role=UserRole.INSTALLER)


def _sinyal(key: str, data_type: str, **kw) -> SignalCatalog:
    return SignalCatalog(
        key=key,
        model="horstmann_sn_2_0",
        label=key,
        data_type=data_type,
        historize=kw.pop("historize", True),
        historize_deadband=kw.pop("historize_deadband", 0.0),
        **kw,
    )


@pytest.fixture()
def katalog(db):
    """Gercek katalogun kucuk ama TEMSILI bir ornegi."""
    satirlar = [
        _sinyal("master.actual_voltage", "analog", unit="V"),
        _sinyal("sat01.actual_current", "analog", unit="A"),
        _sinyal("sat01.overcurrent_tripped", "binary"),
        _sinyal("sat02.earth_fault_tripped", "binary"),
        _sinyal("master.firmware_update", "binary_output", historize=False),
        _sinyal("master.info_serial_number", "string", historize=False),
        _sinyal("master.imei", "string"),
        _sinyal("master.operating_hours", "counter"),
    ]
    db.add_all(satirlar)
    db.commit()
    return {s.key: s for s in satirlar}


def _guncelle(db, kurulumcu, key: str, **alanlar):
    return signals_api.update_signal(
        signal_key=key,
        payload=SignalCatalogUpdate(**alanlar),
        current_user=kurulumcu,
        db=db,
    )


def _toplu(db, kurulumcu, **alanlar):
    return signals_api.bulk_update_historian(
        payload=SignalHistorianBulkUpdate(**alanlar),
        current_user=kurulumcu,
        db=db,
    )


def _olaylar(db, event_type: str | None = None) -> list[SystemEvent]:
    stmt = select(SystemEvent)
    if event_type:
        stmt = stmt.where(SystemEvent.event_type == event_type)
    return list(db.scalars(stmt).all())


# ---------------------------------------------------------------------------
# SOZLESME BOSLUGU — kapatilan asil hata
# ---------------------------------------------------------------------------

def test_okuma_semasi_ARSIV_alanlarini_donuyor(db, katalog):
    """Alanlar DB'de vardi ama API ne donuyor ne kabul ediyordu."""
    okunan = SignalCatalogRead.model_validate(katalog["master.actual_voltage"])
    assert okunan.historize is True
    assert okunan.historize_deadband == 0.0


def test_guncelleme_semasi_ARSIV_alanlarini_kabul_ediyor():
    alanlar = set(SignalCatalogUpdate.model_fields)
    assert {"historize", "historize_deadband"} <= alanlar, (
        "PATCH bu alanlari kabul etmiyor — arayuz kaydettim der, hicbir sey degismez"
    )


def test_frontend_tipi_BACKEND_ILE_SENKRON():
    """Sema senkronu: backend schemas <-> types.ts. Biri eklenip digeri
    unutulursa alan sessizce `undefined` kalir."""
    metin = (FE_KOK / "shared" / "types.ts").read_text(encoding="utf-8")
    govde = metin[metin.index("export type SignalCatalogRow"):]
    govde = govde[: govde.index("};")]
    for alan in ("historize", "historize_deadband"):
        assert alan in govde, f"types.ts SignalCatalogRow icinde `{alan}` yok"


# ---------------------------------------------------------------------------
# ARIZA KORUMASI — ONAY (engelleme degil)
#
# Gerekce: `binary_output` komut noktalarinin (Trigger Download, Reset...)
# arsivi migration 0032'den beri KAPALI. Sert engel koysaydik arayuz sahadaki
# MEVCUT durumu yonetemez, yanlislikla acilan bir noktayi geri kapatmak
# imkansiz olurdu. Kapi acik ama KILITLI.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["sat01.overcurrent_tripped", "master.firmware_update"])
def test_ariza_sinyalinin_arsivi_ONAYSIZ_kapatilamaz(db, katalog, kurulumcu, key):
    katalog[key].historize = True
    db.commit()
    with pytest.raises(HTTPException) as hata:
        _guncelle(db, kurulumcu, key, historize=False)
    assert hata.value.status_code == 400
    assert hata.value.detail["code"] == "ariza_onayi_gerekli"
    # Hangi sinyal oldugu SOYLENMELI; "bir sorun var" demek onayin amacini
    # bosa cikarirdi.
    assert hata.value.detail["signals"] == [key]
    db.rollback()
    assert db.get(SignalCatalog, katalog[key].id).historize is True, "yine de kapandi"


def test_ariza_sinyali_ONAYLA_kapatilabiliyor(db, katalog, kurulumcu):
    key = "sat01.overcurrent_tripped"
    _guncelle(db, kurulumcu, key, historize=False, confirm_fault_signals=True)
    assert db.scalar(select(SignalCatalog).where(SignalCatalog.key == key)).historize is False


def test_ariza_sinyalini_kapatmak_DENETIM_kaydinda_uyari(db, katalog, kurulumcu):
    """Arsivi kapatmak etiket duzenlemesiyle ayni satirda durmamali."""
    _guncelle(db, kurulumcu, "sat01.overcurrent_tripped",
              historize=False, confirm_fault_signals=True)
    olay = _olaylar(db, "signal_updated")[-1]
    assert olay.severity == "warning", "veri yazmayi durdurmak 'info' degildir"
    md = json.loads(olay.metadata_json)
    assert md["historize"] is False
    assert md["fault_signal"] is True
    assert olay.actor_username == "kurulumcu"


def test_HERHANGI_bir_sinyalin_arsivini_kapatmak_da_UYARI(db, katalog, kurulumcu):
    """Ariza sinyali OLMASA bile arsivi kapatmak veri yazmayi durdurur.

    (Mutasyon sinavi bu testi EKSIK bulmustu: `severity`yi sabit "info"ya
    dusuren mutasyon KACIYORDU, cunku ariza sinyali icin severity ikinci bir
    yerde tekrar "warning" yapiliyor. Ariza DISI sinyalde koruma yoktu.)
    """
    _guncelle(db, kurulumcu, "master.imei", historize=False)
    olay = _olaylar(db, "signal_updated")[-1]
    assert olay.severity == "warning", (
        "arsivi kapatmak etiket duzenlemesiyle ayni denetim sinifinda"
    )
    assert json.loads(olay.metadata_json)["historize"] is False


def test_etiket_duzenlemesi_UYARI_DEGIL(db, katalog, kurulumcu):
    """Ters yon: her degisikligi "warning" yapmak, uyariyi anlamsizlastirir."""
    _guncelle(db, kurulumcu, "master.imei", label="IMEI")
    assert _olaylar(db, "signal_updated")[-1].severity == "info"


def test_ariza_sinyalinin_arsivini_ACMAK_onay_ISTEMIYOR(db, katalog, kurulumcu):
    """Koruma YONLU: guvenli yonde surtunme yaratmak, kullaniciyi onay
    penceresine korlestirirdi."""
    katalog["master.firmware_update"].historize = False
    db.commit()
    _guncelle(db, kurulumcu, "master.firmware_update", historize=True)
    assert db.scalar(
        select(SignalCatalog).where(SignalCatalog.key == "master.firmware_update")
    ).historize is True


def test_ARIZA_OLMAYAN_tip_onaysiz_kapatilabiliyor(db, katalog, kurulumcu):
    """Istegin cekirdegi: "sadece son degeri gosterilecek degerleri bosu
    bosuna historiana kaydetmeyelim". String/analog icin surtunme YOK."""
    for key in ("master.imei", "master.actual_voltage"):
        _guncelle(db, kurulumcu, key, historize=False)
        assert db.scalar(select(SignalCatalog).where(SignalCatalog.key == key)).historize is False


def test_onay_bayragi_MODELE_yazilmiyor(db, katalog, kurulumcu):
    """`confirm_fault_signals` bir sutun degil, bir izin. `changes` icinde
    kalsaydi `setattr` ile modele yapisirdi."""
    row = _guncelle(db, kurulumcu, "sat01.overcurrent_tripped",
                    historize=False, confirm_fault_signals=True)
    assert not hasattr(row, "confirm_fault_signals") or not isinstance(
        getattr(type(row), "confirm_fault_signals", None), property
    )
    md = json.loads(_olaylar(db, "signal_updated")[-1].metadata_json)
    assert "confirm_fault_signals" not in md["fields"]


# ---------------------------------------------------------------------------
# OLU BANT — yalnizca analogda ANLAMLI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key", ["sat01.overcurrent_tripped", "master.imei", "master.operating_hours",
            "master.firmware_update"],
)
def test_analog_olmayana_olu_bant_REDDEDILIYOR(db, katalog, kurulumcu, key):
    """Motor bu tiplerde esigi yok sayiyor. Kaydedip yok saymak, kullaniciya
    CALISMAYAN bir ayar satmak olurdu."""
    with pytest.raises(HTTPException) as hata:
        _guncelle(db, kurulumcu, key, historize_deadband=2.0)
    assert hata.value.status_code == 400
    assert hata.value.detail["code"] == "olu_bant_uygulanmaz"


def test_analoga_olu_bant_uygulanabiliyor(db, katalog, kurulumcu):
    _guncelle(db, kurulumcu, "sat01.actual_current", historize_deadband=0.5)
    row = db.scalar(select(SignalCatalog).where(SignalCatalog.key == "sat01.actual_current"))
    assert row.historize_deadband == 0.5


def test_olu_bant_SIFIRLANABILIYOR(db, katalog, kurulumcu):
    """Router `exclude_none=True` uyguluyor: 0.0 DUSMEZ ama None duser.
    Bu ayrim kaybolursa "olu bandi temizle" sessizce ise yaramaz."""
    katalog["sat01.actual_current"].historize_deadband = 0.5
    db.commit()
    _guncelle(db, kurulumcu, "sat01.actual_current", historize_deadband=0.0)
    row = db.scalar(select(SignalCatalog).where(SignalCatalog.key == "sat01.actual_current"))
    assert row.historize_deadband == 0.0, "0.0 `exclude_none` tarafindan yutuldu"


def test_olu_bant_NEGATIF_olamaz():
    with pytest.raises(ValueError):
        SignalCatalogUpdate(historize_deadband=-1.0)


# ---------------------------------------------------------------------------
# TOPLU ISLEM — "String tipini filtrele -> hepsinin arsivini kapat"
# ---------------------------------------------------------------------------

def test_toplu_kapatma_TEK_istekte(db, katalog, kurulumcu):
    keys = ["master.imei", "master.info_serial_number"]
    sonuc = _toplu(db, kurulumcu, signal_keys=keys, historize=False)
    assert sonuc["updated"] == 1, "zaten kapali olan 'guncellendi' sayildi"
    assert sonuc["unchanged"] == 1
    for k in keys:
        assert db.scalar(select(SignalCatalog).where(SignalCatalog.key == k)).historize is False


def test_toplu_islem_TEK_denetim_kaydi_yaziyor(db, katalog, kurulumcu):
    """193 sinyalde satir basina kayit, denetim kaydini OKUNAMAZ yapardi."""
    _toplu(db, kurulumcu,
           signal_keys=["master.imei", "master.actual_voltage", "master.operating_hours"],
           historize=False)
    olaylar = _olaylar(db, "signal_historian_bulk_updated")
    assert len(olaylar) == 1, f"{len(olaylar)} denetim kaydi yazildi"
    md = json.loads(olaylar[0].metadata_json)
    assert md["updated"] == 3
    assert md["historize"] is False
    assert olaylar[0].severity == "warning"


def test_toplu_islemde_ARIZA_sinyali_ONAYSIZ_gecmiyor(db, katalog, kurulumcu):
    """Ornek is akisi "tumunu filtrele -> kapat" ariza sinyallerini de
    kapsar; sessizce gecerse gecis kaybi olur."""
    keys = ["master.imei", "sat01.overcurrent_tripped", "sat02.earth_fault_tripped"]
    with pytest.raises(HTTPException) as hata:
        _toplu(db, kurulumcu, signal_keys=keys, historize=False)
    assert hata.value.detail["code"] == "ariza_onayi_gerekli"
    assert set(hata.value.detail["signals"]) == {
        "sat01.overcurrent_tripped", "sat02.earth_fault_tripped"
    }


def test_reddedilen_toplu_islem_HICBIR_SEYI_degistirmiyor(db, katalog, kurulumcu):
    """Yariya kadar uygulanip hata veren bir islem, kullaniciyi neyin
    degistigini bilmedigi bir durumda birakirdi."""
    keys = ["master.imei", "sat01.overcurrent_tripped"]
    with pytest.raises(HTTPException):
        _toplu(db, kurulumcu, signal_keys=keys, historize=False)
    db.rollback()
    assert db.scalar(
        select(SignalCatalog).where(SignalCatalog.key == "master.imei")
    ).historize is True, "dogrulama yazmadan ONCE yapilmiyor"


def test_toplu_islemde_ariza_ONAYLANINCA_gecer_ve_denetime_ADIYLA_yazilir(
    db, katalog, kurulumcu
):
    keys = ["master.imei", "sat01.overcurrent_tripped"]
    sonuc = _toplu(db, kurulumcu, signal_keys=keys, historize=False,
                   confirm_fault_signals=True)
    assert sonuc["updated"] == 2
    md = json.loads(_olaylar(db, "signal_historian_bulk_updated")[0].metadata_json)
    assert md["fault_signals"] == ["sat01.overcurrent_tripped"], (
        "hangi ariza sinyalinin arsivden cikarildigi denetim kaydinda YOK"
    )


def test_toplu_olu_bant_ANALOG_OLMAYANI_atliyor_ve_BILDIRIYOR(db, katalog, kurulumcu):
    """Karisik secim NORMALDIR. Tum islemi reddetmek kullaniciyi bogar;
    sessizce yutmak ise yapilmamis bir ayari yapilmis gosterirdi."""
    keys = ["master.actual_voltage", "sat01.actual_current",
            "master.imei", "sat01.overcurrent_tripped"]
    sonuc = _toplu(db, kurulumcu, signal_keys=keys, historize_deadband=1.0)
    assert sonuc["updated"] == 2
    assert set(sonuc["skipped_deadband"]) == {"master.imei", "sat01.overcurrent_tripped"}
    assert db.scalar(
        select(SignalCatalog).where(SignalCatalog.key == "sat01.overcurrent_tripped")
    ).historize_deadband == 0.0, "ariza sinyaline olu bant yazildi"


def test_toplu_olu_bant_SIFIRLANABILIYOR(db, katalog, kurulumcu):
    """"Toplu olu bandi temizle" akisi. Kod `if deadband:` yazarsa 0.0
    yutulur ve islem sessizce hicbir sey yapmaz — kullanici temizlediğini
    sanir, esik yerinde kalir.

    (Mutasyon sinavi bu testi EKSIK bulmustu: tekil yolda ayni kontrol vardi,
    toplu yolda yoktu.)
    """
    for key in ("master.actual_voltage", "sat01.actual_current"):
        katalog[key].historize_deadband = 1.5
    db.commit()
    sonuc = _toplu(
        db, kurulumcu,
        signal_keys=["master.actual_voltage", "sat01.actual_current"],
        historize_deadband=0.0,
    )
    assert sonuc["updated"] == 2, "0.0 gonderildi ama hicbir sey degismedi"
    for key in ("master.actual_voltage", "sat01.actual_current"):
        assert db.scalar(
            select(SignalCatalog).where(SignalCatalog.key == key)
        ).historize_deadband == 0.0


def test_toplu_islem_BOS_istegi_reddediyor(db, katalog, kurulumcu):
    with pytest.raises(HTTPException) as hata:
        _toplu(db, kurulumcu, signal_keys=["master.imei"])
    assert hata.value.detail["code"] == "bos_istek"


def test_toplu_islem_BILINMEYEN_anahtari_bildiriyor(db, katalog, kurulumcu):
    sonuc = _toplu(db, kurulumcu, signal_keys=["master.imei", "yok.olan"], historize=False)
    assert sonuc["not_found"] == ["yok.olan"]
    assert sonuc["updated"] == 1


def test_degisiklik_yoksa_denetim_kaydi_YAZILMIYOR(db, katalog, kurulumcu):
    """Gurultu denetim kaydinin isini gormesini engeller."""
    _toplu(db, kurulumcu, signal_keys=["master.imei"], historize=True)
    assert _olaylar(db, "signal_historian_bulk_updated") == []


# ---------------------------------------------------------------------------
# YETKI — sinyal katalogu KURULUM isidir
# ---------------------------------------------------------------------------

def test_toplu_uc_YOLU_tekil_kalipla_CAKISMIYOR():
    """`/signals/historian` (tek parcali) secilseydi `/signals/{signal_key}`
    ile ayni kalibi paylasirdi ve rota sirasina bagli, sessizce yanlis
    handler'a giden bir hata olurdu. Yol IKI parcali olmali.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_route_auth_boundary import _tum_api_rotalari

    rotalar = {
        (yol, m)
        for r in _tum_api_rotalari()
        for yol in [r.path]
        for m in r.methods
        if m not in ("HEAD", "OPTIONS")
    }
    assert ("/signals/historian/bulk", "POST") in rotalar, "toplu uc kayitli degil"
    # Tek parcali bir kardes yol, `/{signal_key}` ile ayni sablonda olurdu.
    tek_parcali = {y for (y, _) in rotalar if y.startswith("/signals/") and y.count("/") == 2}
    assert "/signals/historian" not in tek_parcali
    assert ("/signals/{signal_key}", "POST") not in rotalar, (
        "POST /signals/{signal_key} eklenmis — toplu uc ile cakisir"
    )


def test_toplu_uc_INSTALLER_istiyor():
    """`require_role` TAM ESITLIK kontrol eder (hiyerarsik degil)."""
    dep = inspect.signature(signals_api.bulk_update_historian).parameters["current_user"].default
    checker = dep.dependency
    assert checker(user=User(id=1, username="k", role=UserRole.INSTALLER)) is not None
    for rol in (UserRole.ENGINEER, UserRole.OPS_MANAGER, UserRole.OPERATOR):
        with pytest.raises(HTTPException) as hata:
            checker(user=User(id=2, username="x", role=rol))
        assert hata.value.status_code == 403, f"{rol} toplu arsiv ayarini degistirebiliyor"


# ---------------------------------------------------------------------------
# TEK KAYNAK — arayuz, motorun UYGULAMADIGI bir ayari gostermemeli
# ---------------------------------------------------------------------------

def test_olu_bant_tipleri_MOTORDAN_okunuyor():
    """Servis kendi listesini tutsaydi ikisi kacinilmaz olarak ayrilirdi."""
    assert scs.OLU_BANT_TIPLERI is hp.OLU_BANT_TIPLERI, (
        "servis olu bant tiplerinin KOPYASINI tutuyor — motorla ayrisir"
    )
    assert hp.OLU_BANT_TIPLERI is hp._OLU_BANT_TIPLERI


def test_ariza_tipleri_ARSIV_KARARINI_veren_tiplerle_ortusuyor():
    """Ikili sinyalde 0 -> 1 bir olaydir; bu tipler olu bant DISINDA olmali."""
    assert scs.ARIZA_TIPLERI == frozenset({"binary", "binary_output"})
    assert not (scs.ARIZA_TIPLERI & hp.OLU_BANT_TIPLERI)


def test_frontend_aynasi_BACKEND_ILE_AYNI():
    """Arayuzdeki kopya kullaniciyi ONCEDEN uyarmak icin; son soz sunucuda.
    Yine de ayrisirsa kullanici sunucunun reddettigi bir ayari secebilir
    ya da (daha kotusu) uyarisiz bir ariza sinyalini kapatabilir."""
    metin = (FE_KOK / "features" / "signals" / "signalCatalogConstants.ts").read_text(
        encoding="utf-8"
    )

    def _liste(ad: str) -> set[str]:
        m = re.search(rf"{ad}:\s*SignalDataType\[\]\s*=\s*\[(.*?)\]", metin, re.DOTALL)
        assert m, f"{ad} bulunamadi"
        return set(re.findall(r'"([^"]+)"', m.group(1)))

    assert _liste("DEADBAND_DATA_TYPES") == set(hp.OLU_BANT_TIPLERI)
    assert _liste("FAULT_DATA_TYPES") == set(scs.ARIZA_TIPLERI)


# ---------------------------------------------------------------------------
# UCTAN UCA — ayar GERCEKTEN arsivi kesiyor mu
#
# Sema/uc dogru olsa bile motor eski katalogu okumaya devam ederse hicbir sey
# degismez. Bu test PATCH -> should_archive zincirini gercekten surer.
# ---------------------------------------------------------------------------

def test_arsivi_kapatilan_sinyal_GERCEKTEN_elenmiyor_dusmuyor(db, katalog, kurulumcu):
    hp.reset_caches()
    try:
        assert hp.should_archive(db, device_id=1, signal_key="master.imei", value=1.0)
        _guncelle(db, kurulumcu, "master.imei", historize=False)
        # Onbellek TTL'i (60 sn) gecmis gibi davran — sahada bekleme suresi budur.
        hp.reset_caches()
        assert not hp.should_archive(db, device_id=1, signal_key="master.imei", value=1.0), (
            "arsiv kapatildi ama motor hala yaziyor"
        )
    finally:
        hp.reset_caches()


def test_olu_bant_ayari_GERCEKTEN_suzuyor(db, katalog, kurulumcu):
    hp.reset_caches()
    try:
        _guncelle(db, kurulumcu, "sat01.actual_current", historize_deadband=0.5)
        hp.reset_caches()
        okuma = dict(device_id=1, signal_key="sat01.actual_current", quality="good")
        assert hp.should_archive(db, value=12.0, **okuma)
        assert not hp.should_archive(db, value=12.2, **okuma), "olu bant uygulanmadi"
        assert hp.should_archive(db, value=13.0, **okuma)
    finally:
        hp.reset_caches()


def test_ONBELLEK_GECIKMESI_arayuzde_yaziyor():
    """Kullanici "kaydettim ama hicbir sey degismedi" sanmamali: katalog
    onbellegi 60 sn TTL'li ve `reset_caches` uretimde cagrilmiyor (tuketici
    ayri surecte kosuyor). Arayuz bu gecikmeyi SOYLEMELI."""
    assert hp._CATALOG_TTL_SEC <= 60.0
    tr = json.loads((FE_KOK / "shared" / "i18n" / "resources" / "tr.json").read_text(
        encoding="utf-8"
    ))
    metin = tr["engineering"]["signals"]["historian"]["effectDelay"]
    assert "dakika" in metin or "sn" in metin, f"gecikme uyarisi anlamsiz: {metin!r}"


# ---------------------------------------------------------------------------
# SAHADAKI VARSAYILANLAR DEGISMEMELI
# ---------------------------------------------------------------------------

def test_seed_arsiv_ayarlarini_EZMIYOR():
    """`seed_default_signals` her acilista kosuyor. Bu iki alan
    `_MUTABLE_FIELDS`'te OLMAMALI; olsaydi kullanicinin ayari ilk yeniden
    baslatmada sessizce geri alinirdi."""
    from app.services.signal_catalog_seed import _MUTABLE_FIELDS

    for alan in ("historize", "historize_deadband"):
        assert alan not in _MUTABLE_FIELDS, (
            f"`{alan}` seed tarafindan eziliyor — kullanici ayari reboot'ta kaybolur"
        )


def test_fabrika_katalogu_arsiv_ayari_TASIMIYOR():
    """Seed JSON'u bu alanlari tasimadigi icin guncelleyen sahada
    varsayilanlar DEGISMEZ; yalnizca yeni INSERT'lerde model defaultlari
    (True / 0.0) uygulanir."""
    yol = Path(__file__).resolve().parents[1] / "app" / "data" / "horstmann_sn2_signals.json"
    ham = json.loads(yol.read_text(encoding="utf-8"))
    sinyaller = ham if isinstance(ham, list) else ham["signals"]
    for s in sinyaller:
        assert "historize" not in s and "historize_deadband" not in s, (
            f"{s.get('key')}: fabrika listesi arsiv ayari tasiyor — guncellemede "
            "sahadaki operator tercihi ezilir"
        )
