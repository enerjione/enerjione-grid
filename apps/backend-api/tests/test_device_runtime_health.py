"""Cihaz basina calisma-zamani sagligi alimi (`device_health_v1`).

Sozlesme: gateway PR #33 (commit bd502c49), vendor kopyasi
`docs/gateway-contract/device-health-api-pr33.md`.

BU DOSYANIN KILITLEDIGI SESSIZ HATALAR
--------------------------------------
* Restart'tan sonraki ilk parti ATILMASIN. `gateway_instance_id` diskte
  KALICIDIR; yalnizca ona bakan bir backend yeni calismanin `sequence=1`
  partisini "eski" sanardi. Siralama `(boot_id, sequence)` ikilisinindir.
* YARIM SNAPSHOT CIHAZ SILMESIN. `device_total` iki snapshot'ta da ayni
  olabilir; ona guvenen "eksikleri sil" mantigi VAR OLAN cihazlari siler.
* SAGLIK KANALI KOMUT DUZLEMINE DOKUNMASIN. `/pending` fiziksel kesici
  komutlarinin tasiyicisi; bu uc onun sirrini istemez, satirlarina degmez.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (Base.metadata tam olsun)
from app.api import gateways as gw_api
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.device_runtime_health import DeviceRuntimeHealth
from app.models.enums import CommunicationStatus
from app.models.gateway import Gateway
from app.services import device_runtime_health_service as saglik

KOD = "GW-1"
TOKEN = "gateway-token-" + "t" * 30
YANLIS_TOKEN = "yanlis-token-" + "y" * 30
INSTANCE = "3f2c1a9e-kalici-instance"


@pytest.fixture()
def db():
    # `check_same_thread=False` + `StaticPool`: asagidaki ham-ASGI testinde
    # FastAPI `def` handler'i BASKA BIR THREAD'de (threadpool) kosar ve
    # varsayilan SQLite baglantisi thread'e baglidir. StaticPool tek
    # baglantiyi paylasir, boylece `:memory:` semasi da kaybolmaz.
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=True)()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture()
def gateway(db) -> Gateway:
    g = Gateway(
        code=KOD,
        name=KOD,
        host="10.0.0.1",
        listen_port=20000,
        token=TOKEN,
        is_active=True,
    )
    db.add(g)
    db.commit()
    return g


def _cihaz_kaydi(kod: str, **ustune) -> dict:
    """Sozlesme bolum 4'teki ornek kayit."""
    kayit = {
        "device_code": kod,
        "connection_state": "smart_idle",
        "connected": False,
        "reachable": False,
        "configured_session_policy": "auto",
        "effective_session_policy": "smart",
        "operation_mode": "smart",
        "dial_in_interval_min": 720,
        "next_expected_report_epoch": 1755643200.0,
        "report_overdue_sec": 0.0,
        "report_late": False,
        "last_valid_contact_epoch": 1755600000.0,
        "last_frame_epoch": 1755600000.0,
        "ip_probe_status": "unknown",
        "tcp_probe_status": "connecting",
        "last_probe_epoch": None,
        "ip_endpoint_type": "listening",
    }
    kayit.update(ustune)
    return kayit


def _zarf(
    *,
    boot_id: int = 12,
    sequence: int = 1,
    devices: list[dict] | None = None,
    snapshot: bool = False,
    snapshot_id: str | None = None,
    batch_index: int | None = None,
    batch_count: int | None = None,
    **ustune,
) -> dict:
    govde = {
        "schema": "device_health_v1",
        "gateway_code": KOD,
        "gateway_instance_id": INSTANCE,
        "boot_id": boot_id,
        "sequence": sequence,
        "snapshot": snapshot,
        "snapshot_id": snapshot_id,
        "snapshot_batch_index": batch_index,
        "snapshot_batch_count": batch_count,
        "device_total": len(devices or []),
        "devices": devices or [],
    }
    govde.update(ustune)
    return govde


def _post(db, govde: dict, *, token: str | None = TOKEN, baslik_kodu: str | None = None):
    return gw_api.report_device_runtime_health(
        gateway_code=KOD,
        payload=govde,
        db=db,
        x_gateway_token=token,
        x_gateway_code=baslik_kodu,
        x_gateway_instance_id=INSTANCE,
        x_request_id="req-1",
    )


def _satir(db, kod: str) -> DeviceRuntimeHealth | None:
    return db.get(DeviceRuntimeHealth, kod)


def _kodlar(db) -> set[str]:
    return set(db.scalars(select(DeviceRuntimeHealth.device_code)).all())


# ---------------------------------------------------------------------------
# Kimlik dogrulama — KANONIK gateway credential'i, yeni sistem YOK
# ---------------------------------------------------------------------------


def test_token_YOKSA_reddedilir(db, gateway):
    with pytest.raises(HTTPException) as e:
        _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001")]), token=None)
    assert e.value.status_code == 401
    assert _kodlar(db) == set()


def test_YANLIS_token_reddedilir(db, gateway):
    with pytest.raises(HTTPException) as e:
        _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001")]), token=YANLIS_TOKEN)
    assert e.value.status_code == 401
    assert _kodlar(db) == set()


def test_komut_jetonu_ISTENMEZ(db, gateway):
    """`X-Gateway-Command-Token` bu ucta BEKLENMEMELI (sozlesme bolum 2).

    Saglik telemetrisi komut yetkisi gerektirmez; o sirri buraya yaymak
    F5A'da ayrilan iki duzlemi yeniden birlestirirdi.
    """
    parametreler = inspect.signature(gw_api.report_device_runtime_health).parameters
    assert "x_gateway_command_token" not in parametreler
    # Ve pratikte: komut jetonu HIC gonderilmeden istek gecer.
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001")]))
    assert _satir(db, "SN2-001") is not None


def test_devre_disi_gateway_reddedilir(db, gateway):
    """Operator gateway'i bilerek kapattiysa gozlemi de kaydetmeyiz."""
    gateway.is_active = False
    db.commit()
    with pytest.raises(HTTPException) as e:
        _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001")]))
    assert e.value.status_code == 403


# ---------------------------------------------------------------------------
# Zarf dogrulama
# ---------------------------------------------------------------------------


def test_yanlis_sema_REDDEDILIR(db, gateway):
    """`schema != "device_health_v1"` -> reddet (sozlesme bolum 3)."""
    with pytest.raises(HTTPException) as e:
        _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001")], schema="device_health_v2"))
    assert e.value.status_code == 400
    assert _kodlar(db) == set()


def test_sema_alani_YOKSA_reddedilir(db, gateway):
    govde = _zarf(devices=[_cihaz_kaydi("SN2-001")])
    govde.pop("schema")
    with pytest.raises(HTTPException) as e:
        _post(db, govde)
    assert e.value.status_code == 400


def test_baslik_gateway_kodu_UYUSMAZSA_reddedilir(db, gateway):
    """Defans derinligi: yanlis yapilandirilmis istemci/proxy erken yakalanir."""
    with pytest.raises(HTTPException) as e:
        _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001")]), baslik_kodu="GW-BASKA")
    assert e.value.status_code == 400
    assert _kodlar(db) == set()


def test_govde_gateway_kodu_UYUSMAZSA_reddedilir(db, gateway):
    with pytest.raises(HTTPException) as e:
        _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001")], gateway_code="GW-BASKA"))
    assert e.value.status_code == 400
    assert _kodlar(db) == set()


def test_gecersiz_sequence_reddedilir(db, gateway):
    """`sequence` siralamanin temeli; eksikse bayat koruma CALISMAZ."""
    with pytest.raises(HTTPException) as e:
        _post(db, _zarf(sequence=0, devices=[_cihaz_kaydi("SN2-001")]))
    assert e.value.status_code == 400


def test_cok_buyuk_parti_reddedilir(db, gateway):
    """Sozlesme bolum 7: parti boyu 1..500."""
    cok = [_cihaz_kaydi(f"SN2-{i:04d}") for i in range(saglik.MAX_PARTI_CIHAZ + 1)]
    with pytest.raises(HTTPException) as e:
        _post(db, _zarf(devices=cok))
    assert e.value.status_code == 413


# ---------------------------------------------------------------------------
# Alan eslemesi
# ---------------------------------------------------------------------------


def test_tum_sozlesme_alanlari_yaziliyor(db, gateway):
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001")]))
    satir = _satir(db, "SN2-001")
    assert satir is not None
    assert satir.gateway_code == KOD
    assert satir.connection_state == "smart_idle"
    assert satir.connected is False
    assert satir.reachable is False
    assert satir.configured_session_policy == "auto"
    assert satir.effective_session_policy == "smart"
    assert satir.operation_mode == "smart"
    assert satir.dial_in_interval_min == 720
    assert satir.next_expected_report_epoch == 1755643200.0
    assert satir.report_overdue_sec == 0.0
    assert satir.report_late is False
    assert satir.last_valid_contact_epoch == 1755600000.0
    assert satir.last_frame_epoch == 1755600000.0
    assert satir.ip_probe_status == "unknown"
    assert satir.tcp_probe_status == "connecting"
    assert satir.ip_endpoint_type == "listening"
    assert satir.gateway_instance_id == INSTANCE
    assert satir.boot_id == 12
    assert satir.sequence == 1


def test_null_epoch_HIC_OLMADI_demek(db, gateway):
    """`null` 0'a CEVRILMEZ — panelde 1970 tarihi cikmasin diye."""
    _post(
        db,
        _zarf(
            devices=[
                _cihaz_kaydi(
                    "SN2-001",
                    last_valid_contact_epoch=None,
                    last_frame_epoch=None,
                    last_probe_epoch=None,
                )
            ]
        ),
    )
    satir = _satir(db, "SN2-001")
    assert satir.last_valid_contact_epoch is None
    assert satir.last_frame_epoch is None
    assert satir.last_probe_epoch is None


def test_bilinmeyen_alanlar_YOK_SAYILIR(db, gateway):
    """Ileri uyumluluk: PR acik, alan eklemek geriye uyumludur."""
    govde = _zarf(
        devices=[
            _cihaz_kaydi(
                "SN2-001",
                gelecekteki_alan="deger",
                satellite_operation_mode=1,
            )
        ],
        gelecekteki_zarf_alani={"a": 1},
    )
    _post(db, govde)  # patlamamali
    satir = _satir(db, "SN2-001")
    assert satir is not None
    assert satir.connection_state == "smart_idle"
    assert not hasattr(satir, "gelecekteki_alan")


def test_bilinmeyen_connection_state_UNKNOWNA_dusurulur(db, gateway, caplog):
    """Tanimadigimiz durum saklanmaz — ama SESSIZCE de yutulmaz.

    NE DEGISTI: bu test eskiden tanimadigimiz degerin AYNEN korunmasini
    istiyordu ("yeni bir durumu sessizce yutmayalim"). Kaygi hakliydi ama
    cozum yanlisti, iki sebeple:

    1. Alan arayuzde bir renge/etikete ceviriliyor; tanimadigimiz deger
       hicbir kovaya girmez ve ekranda CIZILEMEZ.
    2. Sunum kovalari kanonik duruma SIZABILIRDI. `late` KPI'da mesru bir
       kovadir ama `connection_state` DEGILDIR (gecikme `report_late`
       bayragidir, durum `smart_idle` KALIR). Serbest gecis, o kovanin
       durum olarak yazilmasina kapi acardi.

    Yeni davranis ikisini birden korur: `unknown` yazilir VE deger adiyla
    loglanir, yani gercekten yeni bir gateway durumu gozden kacmaz.
    """
    import logging

    with caplog.at_level(logging.WARNING):
        _post(
            db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="hibernating")])
        )
    assert _satir(db, "SN2-001").connection_state == "unknown"
    assert "hibernating" in caplog.text, "bilinmeyen durum sessizce yutuldu"


def test_MUKERRER_kod_ayni_partide_patlamaz(db, gateway):
    """Bozuk bir parti 5xx uretmemeli: gateway onu gecici sayip sonsuza
    kadar yeniden denerdi."""
    _post(
        db,
        _zarf(
            devices=[
                _cihaz_kaydi("SN2-001", connection_state="lost"),
                _cihaz_kaydi("SN2-001", connection_state="online"),
            ]
        ),
    )
    assert _satir(db, "SN2-001").connection_state == "online"


def test_kodsuz_kayit_PARTIYI_dusurmez(db, gateway):
    """Tek bozuk kayit yuzunden 49 saglam cihazin durumu atilmamali."""
    _post(
        db,
        _zarf(
            devices=[
                {"connection_state": "online"},  # device_code YOK
                _cihaz_kaydi("SN2-002"),
            ]
        ),
    )
    assert _kodlar(db) == {"SN2-002"}


# ---------------------------------------------------------------------------
# Bayat yazma korumasi — (boot_id, sequence) LEKSIKOGRAFIK
# ---------------------------------------------------------------------------


def test_BAYAT_sequence_yok_sayilir(db, gateway):
    _post(db, _zarf(boot_id=12, sequence=5, devices=[_cihaz_kaydi("SN2-001", connection_state="online")]))
    _post(db, _zarf(boot_id=12, sequence=4, devices=[_cihaz_kaydi("SN2-001", connection_state="lost")]))
    satir = _satir(db, "SN2-001")
    assert satir.connection_state == "online", "bayat parti uygulanmis"
    assert satir.sequence == 5


def test_AYNI_sequence_yok_sayilir(db, gateway):
    """`gelen <= saklanan -> YOK SAY` — esitlik de bayattir (yeniden gonderim)."""
    _post(db, _zarf(boot_id=12, sequence=5, devices=[_cihaz_kaydi("SN2-001", connection_state="online")]))
    _post(db, _zarf(boot_id=12, sequence=5, devices=[_cihaz_kaydi("SN2-001", connection_state="lost")]))
    assert _satir(db, "SN2-001").connection_state == "online"


def test_bayat_parti_HATA_DEGIL(db, gateway):
    """4xx gecici sayilip yeniden denenir; bayati reddetmek sonsuz dongu olurdu."""
    _post(db, _zarf(boot_id=12, sequence=5, devices=[_cihaz_kaydi("SN2-001")]))
    yanit = _post(db, _zarf(boot_id=12, sequence=1, devices=[_cihaz_kaydi("SN2-001")]))
    assert yanit.status_code == 204


def test_RESTART_sonrasi_sequence_1_UYGULANIR(db, gateway):
    """Eski calismanin `sequence=9999`u yeni calismanin `sequence=1`inden KUCUK.

    `gateway_instance_id` IKI PARTIDE DE AYNI — diskte kalici oldugu icin
    restart'ta degismez. Yalnizca ona bakan bir backend yeni calismanin ilk
    partisini "eski" sanip ATARDI; bu test tam olarak onu kilitler.
    """
    _post(
        db,
        _zarf(boot_id=11, sequence=9999, devices=[_cihaz_kaydi("SN2-001", connection_state="lost")]),
    )
    _post(
        db,
        _zarf(boot_id=12, sequence=1, devices=[_cihaz_kaydi("SN2-001", connection_state="online")]),
    )
    satir = _satir(db, "SN2-001")
    assert satir.connection_state == "online"
    assert (satir.boot_id, satir.sequence) == (12, 1)
    assert satir.gateway_instance_id == INSTANCE


def test_ONCEKI_bootun_gec_gelen_partisi_yok_sayilir(db, gateway):
    _post(db, _zarf(boot_id=12, sequence=1, devices=[_cihaz_kaydi("SN2-001", connection_state="online")]))
    _post(
        db,
        _zarf(boot_id=11, sequence=9999, devices=[_cihaz_kaydi("SN2-001", connection_state="lost")]),
    )
    assert _satir(db, "SN2-001").connection_state == "online"


def test_siralama_saf_fonksiyonu(db):
    """Karsilastirma leksikografik; duvar saati YOK."""
    assert saglik.bayat_mi((11, 9999), (12, 1)) is True
    assert saglik.bayat_mi((12, 1), (11, 9999)) is False
    assert saglik.bayat_mi((12, 5), (12, 5)) is True
    assert saglik.bayat_mi((12, 6), (12, 5)) is False
    assert saglik.bayat_mi((1, 1), None) is False


# ---------------------------------------------------------------------------
# Delta
# ---------------------------------------------------------------------------


def _tam_snapshot(db, kodlar: list[str], *, snap: str, boot: int = 12, seq_bas: int = 1):
    """Tek partilik tam snapshot gonder."""
    _post(
        db,
        _zarf(
            boot_id=boot,
            sequence=seq_bas,
            devices=[_cihaz_kaydi(k) for k in kodlar],
            snapshot=True,
            snapshot_id=snap,
            batch_index=0,
            batch_count=1,
        ),
    )


def test_DELTA_yalnizca_gelen_cihazi_gunceller(db, gateway):
    _tam_snapshot(db, ["SN2-001", "SN2-002"], snap="12-1")
    _post(
        db,
        _zarf(
            boot_id=12,
            sequence=2,
            devices=[_cihaz_kaydi("SN2-001", connection_state="online", connected=True)],
        ),
    )
    assert _satir(db, "SN2-001").connection_state == "online"
    assert _satir(db, "SN2-001").connected is True
    assert _satir(db, "SN2-002").connection_state == "smart_idle", "delta digerine dokunmus"
    assert _kodlar(db) == {"SN2-001", "SN2-002"}, "delta cihaz SILMEZ"


def test_DELTA_snapshot_damgasini_BOZMAZ(db, gateway):
    """Bozsaydi, devam eden snapshot tamamlandiginda o cihaz SILINIRDI.

    Kurgu: 2 partilik snapshot'in ilk partisi geldi, arada delta geldi,
    sonra ikinci parti geldi. Delta'nin dokundugu cihaz snapshot'ta VAR ve
    silinmemeli.
    """
    _tam_snapshot(db, ["SN2-001", "SN2-002"], snap="12-1")
    # Yeni snapshot: 2 parti.
    _post(
        db,
        _zarf(
            boot_id=12, sequence=2, devices=[_cihaz_kaydi("SN2-001")],
            snapshot=True, snapshot_id="12-2", batch_index=0, batch_count=2,
        ),
    )
    # Arada delta — ayni cihaza.
    _post(
        db,
        _zarf(boot_id=12, sequence=3, devices=[_cihaz_kaydi("SN2-001", connection_state="online")]),
    )
    # Snapshot'in ikinci partisi.
    _post(
        db,
        _zarf(
            boot_id=12, sequence=4, devices=[_cihaz_kaydi("SN2-002")],
            snapshot=True, snapshot_id="12-2", batch_index=1, batch_count=2,
        ),
    )
    assert _kodlar(db) == {"SN2-001", "SN2-002"}, "delta gordugu cihaz silinmis"
    assert _satir(db, "SN2-001").connection_state == "online"


# ---------------------------------------------------------------------------
# Snapshot uzlastirmasi
# ---------------------------------------------------------------------------


def test_COK_PARCALI_snapshot_silineni_uzlastirir(db, gateway):
    """Gateway "cihaz silindi" MESAJI GONDERMEZ; cikan cihaz snapshot'ta yoktur."""
    _tam_snapshot(db, ["A", "B", "C", "D"], snap="12-1")
    assert _kodlar(db) == {"A", "B", "C", "D"}

    # Yeni snapshot iki partide gelir ve yalnizca A, B icerir.
    _post(
        db,
        _zarf(boot_id=12, sequence=2, devices=[_cihaz_kaydi("A")],
              snapshot=True, snapshot_id="12-2", batch_index=0, batch_count=2),
    )
    assert _kodlar(db) == {"A", "B", "C", "D"}, "yarim snapshot silmis"
    _post(
        db,
        _zarf(boot_id=12, sequence=3, devices=[_cihaz_kaydi("B")],
              snapshot=True, snapshot_id="12-2", batch_index=1, batch_count=2),
    )
    assert _kodlar(db) == {"A", "B"}


def test_YARIM_snapshot_cihaz_SILMEZ(db, gateway):
    """Sozlesme bolum 6: silme YALNIZCA `snapshot_batch_count` parti sonrasi.

    `device_total` iki turda da ayni olabilir; ona guvenen bir backend yarim
    kalan eski snapshot ile yenisini ayirt edemez ve VAR OLAN CIHAZLARI SILER.
    """
    _tam_snapshot(db, ["A", "B", "C", "D"], snap="12-1")
    # 4 partilik yeni snapshot'in yalnizca ilk ikisi geliyor (gateway
    # yeniden baslatildi / ag koptu).
    for idx, kod in enumerate(["A", "B"]):
        _post(
            db,
            _zarf(boot_id=12, sequence=2 + idx, devices=[_cihaz_kaydi(kod)],
                  snapshot=True, snapshot_id="12-2", batch_index=idx, batch_count=4),
        )
    assert _kodlar(db) == {"A", "B", "C", "D"}, "yarim snapshot cihaz silmis"
    # Ve kalan cihazlarin verisi BOZULMAMIS olmali.
    assert _satir(db, "C").connection_state == "smart_idle"


def test_YENI_snapshot_id_yarim_eskisini_ATAR(db, gateway):
    """Kismi basarisizliktan sonraki yeniden deneme YENI `snapshot_id` uretir.

    Yarim kalan `12-2` kendini asla tamamlayamaz; `12-3` tamamlandiginda
    ondan kalan damgalar da temizlenir.
    """
    _tam_snapshot(db, ["A", "B", "C"], snap="12-1")
    _post(
        db,
        _zarf(boot_id=12, sequence=2, devices=[_cihaz_kaydi("A")],
              snapshot=True, snapshot_id="12-2", batch_index=0, batch_count=4),
    )
    # Yeniden deneme: yeni kimlik, cihaz seti degismis (C dusmus).
    _post(
        db,
        _zarf(boot_id=12, sequence=3, devices=[_cihaz_kaydi("A"), _cihaz_kaydi("B")],
              snapshot=True, snapshot_id="12-3", batch_index=0, batch_count=1),
    )
    assert _kodlar(db) == {"A", "B"}


def test_snapshot_id_YOKSA_silme_YAPILMAZ(db, gateway):
    """Korelasyon kimligi olmadan hangi partilerin ayni snapshot'a ait
    oldugu bilinemez; silmemek tek guvenli davranistir."""
    _tam_snapshot(db, ["A", "B"], snap="12-1")
    _post(
        db,
        _zarf(boot_id=12, sequence=2, devices=[_cihaz_kaydi("A")],
              snapshot=True, snapshot_id=None, batch_index=0, batch_count=1),
    )
    assert _kodlar(db) == {"A", "B"}


def test_BOS_snapshot_filoyu_temizler(db, gateway):
    """Tum cihazlar config'ten cikarsa tek partilik BOS snapshot gelir."""
    _tam_snapshot(db, ["A", "B"], snap="12-1")
    _post(
        db,
        _zarf(boot_id=12, sequence=2, devices=[],
              snapshot=True, snapshot_id="12-2", batch_index=0, batch_count=1),
    )
    assert _kodlar(db) == set()


def test_BASKA_gateway_cihazlari_uzlastirmadan_ETKILENMEZ(db, gateway):
    """Silme HER ZAMAN gateway basina; komsu gateway'in cihazlari durur."""
    db.add(
        DeviceRuntimeHealth(
            device_code="BASKA-1",
            gateway_code="GW-2",
            connection_state="online",
            connected=True,
            reachable=True,
            report_late=False,
            boot_id=1,
            sequence=1,
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    _tam_snapshot(db, ["A"], snap="12-1")
    _post(
        db,
        _zarf(boot_id=12, sequence=2, devices=[],
              snapshot=True, snapshot_id="12-2", batch_index=0, batch_count=1),
    )
    assert _kodlar(db) == {"BASKA-1"}


def test_IKIYUZ_cihazlik_snapshot(db, gateway):
    """Sozlesmedeki gercek olcek: 200 cihaz, 50'lik 4 parti."""
    kodlar = [f"SN2-{i:03d}" for i in range(200)]
    for idx in range(4):
        dilim = kodlar[idx * 50 : (idx + 1) * 50]
        _post(
            db,
            _zarf(
                boot_id=12,
                sequence=idx + 1,
                devices=[_cihaz_kaydi(k) for k in dilim],
                snapshot=True,
                snapshot_id="12-7",
                batch_index=idx,
                batch_count=4,
            ),
        )
    assert len(_kodlar(db)) == 200
    assert _kodlar(db) == set(kodlar)
    # Uzlastirma dogru damgayi gordu: hicbiri silinmedi.
    assert _satir(db, "SN2-199").snapshot_id == "12-7"


# ---------------------------------------------------------------------------
# IZOLASYON — komut duzlemi ve telemetri
# ---------------------------------------------------------------------------


def test_KOMUT_UCLARI_degismedi():
    """`/pending`, `command-results`, `command-delivery-acks` hala komut
    duzlemi credential'i ISTIYOR. Saglik ucu eklenirken bu gevsetilirse
    F5A'da ayrilan iki duzlem yeniden birleserdi.
    """
    for ad in (
        "get_gateway_pending",
        "report_command_results",
        "report_command_delivery_acks",
    ):
        parametreler = inspect.signature(getattr(gw_api, ad)).parameters
        assert "x_gateway_command_token" in parametreler, f"{ad} komut jetonunu birakmis"


def test_saglik_servisi_KOMUT_DUZLEMINE_dokunmaz():
    """Kapsam sizmasi burada durur (GU-15 ile ayni desen)."""
    kaynak = inspect.getsource(saglik).lower()
    for yasak in ("device_command", "crob", "command_delivery", "/pending", "select_before"):
        assert yasak not in kaynak, f"saglik servisinde komut duzlemi izi: {yasak}"


def test_bekleyen_komutlar_ETKILENMEZ(db, gateway):
    """Basarili/basarisiz saglik teslimi komut kuyruguna DEGMEZ."""
    db.add(
        DeviceCommand(
            gateway_code=KOD,
            device_code="SN2-001",
            command="trip",
            dnp3_index=0,
            status="pending",
        )
    )
    db.commit()
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="lost")]))
    komut = db.scalars(select(DeviceCommand)).one()
    assert komut.status == "pending"
    assert komut.result_error is None


def test_telemetri_alanlari_EZILMEZ(db, gateway):
    """`devices.communication_status` telemetri hattinindir.

    `smart_idle` SAGLIKLI bir uyku halidir; telemetri hattinin "haberlesme
    yok" karariyla ayni kovaya konursa uyuyan filo SCADA'da arizali gorunur.
    """
    db.add(
        Device(
            code="SN2-001",
            name="cihaz",
            gateway_code=KOD,
            ip_address="10.0.0.50",
            latitude=39.0,
            longitude=35.0,
            communication_status=CommunicationStatus.ONLINE,
        )
    )
    db.commit()
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="lost")]))
    cihaz = db.scalars(select(Device).where(Device.code == "SN2-001")).one()
    assert cihaz.communication_status == CommunicationStatus.ONLINE
    assert cihaz.last_update_at is None
    assert _satir(db, "SN2-001").connection_state == "lost"


# ---------------------------------------------------------------------------
# Yetenek matrisi
# ---------------------------------------------------------------------------


def test_yetenek_1_15_0_ve_smart_sessiondan_AYRI():
    from app.services import gateway_compatibility as uyum

    assert uyum.FEATURE_MIN_VERSION["device_runtime_health_transport"] == "1.15.0"
    # Gateway 1.14.0'da Smart Listening CALISIR ama bu tasiyici YOKTUR.
    assert uyum.supports("smart_session", "1.14.0") is True
    assert uyum.supports("device_runtime_health_transport", "1.14.0") is False
    assert uyum.supports("device_runtime_health_transport", "1.15.0") is True
    # Surumunu bildirmemis gateway: BILINMIYOR (False DEGIL).
    assert uyum.supports("device_runtime_health_transport", None) is None


def test_yetenek_sapmasi_KENDI_gerekcesiyle_beyanli():
    """PR #33 acik ve 1.15.0 yayinlanmadi — sapma beyansiz kalmamali."""
    from app.services import gateway_compatibility as uyum

    gerekce = uyum.KNOWN_VERSION_DRIFT.get("device_runtime_health_transport")
    assert gerekce, "yeni yetenek KNOWN_VERSION_DRIFT'e beyan edilmemis"
    assert gerekce != uyum._V114_SAPMA_GEREKCESI, "v1.14.0 gerekcesi ODUNC alinmis"
    assert "#33" in gerekce and "1.15.0" in gerekce


# ---------------------------------------------------------------------------
# HTTP kablosu — govde ayristirmasi ve BOS 2xx yanit
# ---------------------------------------------------------------------------
#
# Yukaridaki testler handler'i dogrudan cagirir; bu, FastAPI baglantisini
# (JSON govde ayristirmasi, durum kodu, bos govde) DOGRULAMAZ. Gateway
# yalnizca DURUM KODUNA bakar (sozlesme bolum 2) ve yanit govdesini OKUMAZ.
#
# TestClient DEGIL ham ASGI: `starlette.testclient` httpx gerektiriyor ve bu
# proje httpx'e bagli degil (ayni gerekce `test_route_auth_boundary.py`).


def _asgi_post(yol: str, govde: dict, basliklar: dict[str, str]) -> tuple[int, bytes]:
    import asyncio
    import json as _json

    from app.main import app

    ham = _json.dumps(govde).encode("utf-8")
    mesajlar: list[dict] = []

    async def send(mesaj):
        mesajlar.append(mesaj)

    async def receive():
        return {"type": "http.request", "body": ham, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": yol,
        "raw_path": yol.encode(),
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            *[(k.lower().encode(), v.encode()) for k, v in basliklar.items()],
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    durum = next(m["status"] for m in mesajlar if m["type"] == "http.response.start")
    govde_baytlari = b"".join(
        m.get("body", b"") for m in mesajlar if m["type"] == "http.response.body"
    )
    return durum, govde_baytlari


def test_HTTP_ucu_204_ve_BOS_govde_doner(db, gateway, lisans_kilidi_kapali):
    from app.core.config import settings
    from app.db.session import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        durum, govde = _asgi_post(
            f"{settings.api_prefix}/gateways/{KOD}/device-health",
            _zarf(devices=[_cihaz_kaydi("SN2-001")]),
            {"X-Gateway-Token": TOKEN, "X-Gateway-Code": KOD},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert durum == 204
    assert govde == b"", "gateway govdeyi okumaz; bos donmeli"
    assert _satir(db, "SN2-001") is not None


def test_device_health_ucu_lisans_kilidi_KAPSAMINDA():
    """Lisanssiz kurulumda bu uc de KAPALI olmali — beyaz listeye GIRMEZ.

    Yukaridaki ASGI testi kilidi bilerek acar (yoksa 403'e carpar ve yerelde
    gecip CI'da duserdi). O fixture, kapinin kendisini test disi birakmis
    OLMAMALI: `/gateways/*` beyaz listeye eklenirse lisanssiz bir kurulum
    saglik yazmaya devam eder — urun isi yapmayi birakmasi gerekirken.

    Kurtarma uclari (`/auth`, `/license`, `/network`, `/remote-access`)
    bilerek disaridadir; saglik telemetrisi kurtarma kolu DEGILDIR.
    """
    from app.core.config import settings
    from app.core.license_gate import _is_allowed

    yol = f"{settings.api_prefix}/gateways/{KOD}/device-health"
    assert not _is_allowed(yol, "POST"), (
        "device-health lisans beyaz listesine girmis — lisanssiz kurulum "
        "urun isi yapmaya devam eder"
    )


# ---------------------------------------------------------------------------
# `report_late` BIR BAYRAKTIR, DURUM DEGILDIR
#
# Sozlesme (bolum 5) net: rapor gecikse bile `connection_state` HALA
# `smart_idle`dir. Gecikme ayri bir bayrakta tasinir.
#
# NEDEN AYRI TEST: "gecikmis" kavramini kanonik duruma katmak cok cazip bir
# kisayoldur — KPI'da tek bir alan okumak kolaylasir. Ama o an backend, bir
# UYARIYI bir DURUMA terfi ettirmis olur: gecikme kalkinca hangi duruma
# donulecegi bilgisi KAYBOLUR ve `smart_idle` ile `lost` arasindaki ayrim
# bulanir. Sunum katmani istedigi kovaya koyabilir; KAYNAK degismez.
# ---------------------------------------------------------------------------


def test_report_late_kanonik_durumu_DEGISTIRMEZ(db, gateway):
    """`smart_idle` + `report_late=true` -> durum HALA `smart_idle`."""
    _post(
        db,
        _zarf(
            devices=[
                _cihaz_kaydi(
                    "DEV-LATE",
                    connection_state="smart_idle",
                    report_late=True,
                    report_overdue_sec=360.0,
                )
            ]
        ),
    )
    satir = _satir(db, "DEV-LATE")
    assert satir is not None
    assert satir.connection_state == "smart_idle", (
        "gecikme bayragi kanonik durumu ezdi — 'late' bir connection_state DEGIL"
    )
    assert satir.report_late is True
    assert satir.report_overdue_sec == 360.0


def test_late_gecerli_bir_connection_state_DEGILDIR(db, gateway):
    """Sema `late` diye bir durumu KABUL ETMEZ.

    Enum'u genisletmek, sunum katmanindaki bir kovayi wire sozlesmesine
    sizdirmak olurdu; gateway boyle bir deger GONDERMEZ.
    """
    _post(db, _zarf(devices=[_cihaz_kaydi("DEV-BAD-STATE", connection_state="late")]))
    satir = _satir(db, "DEV-BAD-STATE")
    # Gecersiz durum ya hic yazilmaz ya da `unknown`a duser; "late" OLARAK
    # SAKLANMAZ.
    assert satir is None or satir.connection_state != "late"


def test_gecikme_kalkinca_durum_bozulmadan_devam_eder(db, gateway):
    """Bayrak inip kalkarken kanonik durum saglam kalir."""
    _post(
        db,
        _zarf(
            devices=[_cihaz_kaydi("DEV-FLAP", connection_state="smart_idle", report_late=True)],
            sequence=1,
        ),
    )
    assert _satir(db, "DEV-FLAP").connection_state == "smart_idle"

    _post(
        db,
        _zarf(
            devices=[_cihaz_kaydi("DEV-FLAP", connection_state="smart_idle", report_late=False)],
            sequence=2,
        ),
    )
    satir = _satir(db, "DEV-FLAP")
    assert satir.connection_state == "smart_idle"
    assert satir.report_late is False


# ---------------------------------------------------------------------------
# OLAY KAYDI — yalnizca GERCEK durum degisimi
#
# Operator gecmise donup "bu cihaz ne zaman uyudu, ne zaman uyandi" diye
# bakabilmeli. Ama bu kanal 300 saniyede bir TAM SNAPSHOT gonderiyor: parti
# basina olay yazmak 2 yillik FIFO olay kaydini gurultuyle doldurur ve gercek
# denetim izini budar. Ikisinin arasindaki tek dogru cizgi GECIS.
# ---------------------------------------------------------------------------


def _olaylar(db):
    from app.models.system_event import SystemEvent

    return list(db.scalars(select(SystemEvent).order_by(SystemEvent.id)).all())


def test_ILK_gozlem_olay_URETMEZ(db, gateway):
    """Ilk tam snapshot butun filo icin olay yagdirmamali.

    Uretseydi 600 cihazlik bir kurulumda ilk baglantida 600 satir yazilir ve
    hicbiri bir DEGISIMI anlatmazdi.
    """
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="online")]))
    assert _olaylar(db) == []


def test_AYNI_durum_tekrar_gelince_olay_YAZILMAZ(db, gateway):
    """Snapshot her 300sn'de bir ayni durumu tasiyor — gurultu olmamali."""
    for sira in (1, 2, 3):
        _post(
            db,
            _zarf(
                devices=[_cihaz_kaydi("SN2-001", connection_state="smart_idle")],
                sequence=sira,
            ),
        )
    assert _olaylar(db) == []


def test_UYKUYA_GECIS_olay_yazar(db, gateway):
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="online")], sequence=1))
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="smart_idle")], sequence=2))

    olaylar = _olaylar(db)
    assert len(olaylar) == 1
    o = olaylar[0]
    assert o.device_code == "SN2-001"
    assert o.event_type == "device_runtime_state_changed"
    # UYKU SAGLIKLIDIR: uyari seviyesinde yazilirsa olay listesinde her gece
    # filo boyu sahte alarm gibi gorunur.
    assert o.severity == "info"
    assert '"key": "device_runtime_smart_idle"' in o.metadata_json.replace("'", '"')


def test_UYANMA_olay_yazar(db, gateway):
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="smart_idle")], sequence=1))
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="online")], sequence=2))

    olaylar = _olaylar(db)
    assert len(olaylar) == 1
    assert olaylar[0].severity == "info"
    assert "device_runtime_online" in olaylar[0].metadata_json


def test_KAYIP_uyari_seviyesinde(db, gateway):
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="online")], sequence=1))
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="lost")], sequence=2))

    olaylar = _olaylar(db)
    assert len(olaylar) == 1
    assert olaylar[0].severity == "warning"


def test_olay_i18n_anahtari_ve_parametreleri_tasir(db, gateway):
    """Metin backend'de URETILMEZ — olay listesi kullanicinin dilinde."""
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="online")], sequence=1))
    _post(db, _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="recovering")], sequence=2))

    ham = _olaylar(db)[0].metadata_json
    assert "device_runtime_recovering" in ham
    assert "SN2-001" in ham
    # Gecisin NEREDEN geldigi de saklanir: teshiste "online'dan mi lost'tan mi
    # toparlaniyor" ayrimi onemli.
    assert "online" in ham


def test_report_late_TEK_BASINA_olay_uretmez(db, gateway):
    """Bayrak degisimi DURUM degisimi degildir.

    `report_late` gun icinde inip kalkabilir; olay yazmak gunluk sahte
    "durum degisti" satirlari uretirdi.
    """
    _post(
        db,
        _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="smart_idle", report_late=False)], sequence=1),
    )
    _post(
        db,
        _zarf(devices=[_cihaz_kaydi("SN2-001", connection_state="smart_idle", report_late=True)], sequence=2),
    )
    assert _olaylar(db) == []
