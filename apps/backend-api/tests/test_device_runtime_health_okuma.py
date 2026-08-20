"""Calisma-zamani sagligi OKUMA yolu — `DeviceRead.runtime_health`.

Alim yolu (`tests/test_device_runtime_health.py`) gateway'den gelen partiyi
`device_runtime_health` tablosuna yaziyordu; bu dosya o verinin cihaz
yanitina KADAR gelmesini kilitler. Ikisi arasindaki bosluk gercekti: alim
calisiyor, tablo doluyor, arayuz `item.runtime_health` bekliyor ama yanitta
boyle bir alan YOKTU — yani her cihaz sonsuza kadar eski davranisa dusuyor
ve `smart_idle` / `recovering` / gecikme HIC gorunmuyordu.

BU DOSYANIN KILITLEDIGI SESSIZ HATALAR
--------------------------------------
* SAGLIK KAPSAM DISINA SIZMASIN. Saglik satirinin AYRI bir yetki yolu
  YOKTUR; otorite cihaz kapsamidir (`scope_service`). Gormedigi cihazin
  sagligi da gorunmez.
* CIHAZ KUMESI DEGISMESIN. Saglik JOIN ile degil AYRI SELECT ile baglanir;
  sayfalama, toplam sayi, filtre ve siralama etkilenemez, satir cogalamaz.
* CIHAZ BASINA BIR SORGU OLMASIN. 600+ cihazli listede satir basina okuma
  N+1 demekti; sorgu sayisi cihaz sayisindan BAGIMSIZ olmali.
* `null` EPOCH 0'A DONMESIN. 0 = 1970; panelde gecerli bir tarih gibi durur.
* BAYRAK DURUMU EZMESIN. `report_late=true` iken `connection_state` HALA
  `smart_idle` donmeli.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (Base.metadata tam olsun)
from app.api import devices as devices_api
from app.api.deps import get_current_user
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.models.device import Device
from app.models.device_runtime_health import DeviceRuntimeHealth
from app.models.enums import CommunicationStatus, UserRole
from app.models.gateway import Gateway
from app.models.responsibility_area import (
    ResponsibilityArea,
    responsibility_area_devices,
    responsibility_area_users,
)
from app.models.user import User
from app.schemas.device import DeviceRead, DeviceRuntimeHealthRead
from app.services import device_kit_service

GW = "GW-1"
TOKEN = "gateway-token-" + "t" * 30


# ---------------------------------------------------------------------------
# Ortam
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    # `check_same_thread=False` + `StaticPool`: ham-ASGI testlerinde FastAPI
    # `def` handler'i BASKA BIR THREAD'de kosar ve `:memory:` semasi
    # kaybolmamali (ayni gerekce alim testlerinde de yazili).
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=True)()
    s.info["engine"] = eng
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture()
def gateway(db) -> Gateway:
    g = Gateway(
        code=GW, name=GW, host="10.0.0.1", listen_port=20000,
        token=TOKEN, is_active=True,
    )
    db.add(g)
    db.commit()
    return g


def _cihaz(db, kod: str, **ustune) -> Device:
    alanlar = dict(
        code=kod,
        name=kod,
        model="horstmann_sn_2_0",
        gateway_code=GW,
        ip_address="10.0.0.9",
        dnp3_outstation_port=20001,
        dnp3_address=1,
        poll_interval_sec=2,
        timeout_ms=3000,
        retry_count=2,
        signal_profile="horstmann_sn2_fixed",
        latitude=41.0,
        longitude=29.0,
        communication_status=CommunicationStatus.OFFLINE,
        alarm_active=False,
        dnp3_extended={},
    )
    alanlar.update(ustune)
    d = Device(**alanlar)
    db.add(d)
    db.commit()
    return d


def _saglik(db, kod: str, **ustune) -> DeviceRuntimeHealth:
    """Alt seviye fixture — DOGRUDAN satir.

    Uctan uca kanit ASAGIDA ve gercek POST ucundan gecer
    (`test_UCTAN_UCA_*`); burasi yalnizca okuma yolunu izole eder.
    """
    alanlar = dict(
        device_code=kod,
        gateway_code=GW,
        connection_state="smart_idle",
        connected=False,
        reachable=False,
        configured_session_policy="auto",
        effective_session_policy="smart",
        operation_mode="smart",
        dial_in_interval_min=720,
        next_expected_report_epoch=1755643200.0,
        report_overdue_sec=0.0,
        report_late=False,
        last_valid_contact_epoch=1755600000.0,
        last_frame_epoch=1755600000.0,
        ip_probe_status="unknown",
        tcp_probe_status="connecting",
        last_probe_epoch=None,
        ip_endpoint_type="listening",
        boot_id=12,
        sequence=1,
        updated_at=datetime.now(timezone.utc),
    )
    alanlar.update(ustune)
    satir = DeviceRuntimeHealth(**alanlar)
    db.add(satir)
    db.commit()
    return satir


def _kullanici(db, rol: UserRole = UserRole.ENGINEER, kod: str = "muh") -> User:
    u = User(
        username=kod, email=f"{kod}@ornek.local", full_name=kod,
        hashed_password="x", role=rol,
    )
    db.add(u)
    db.commit()
    return u


def _liste(db, kullanici: User, gateway_code: str | None = None) -> list[dict]:
    """`GET /devices` handler'ini cagirip SEMADAN GECIRILMIS sozluk doner.

    Sema adimi sart: blokaj tam olarak "sema alani tasimiyor" idi; ORM
    nesnesine bakan bir test bunu YAKALAYAMAZDI.
    """
    satirlar = devices_api.list_devices(
        gateway_code=gateway_code, current_user=kullanici, db=db
    )
    return [DeviceRead.model_validate(s).model_dump(mode="json") for s in satirlar]


def _tek(kayitlar: list[dict], kod: str) -> dict | None:
    return next((k for k in kayitlar if k["code"] == kod), None)


# ---------------------------------------------------------------------------
# 1-2. Temel okuma: satir varsa nesne, yoksa ACIKCA null
# ---------------------------------------------------------------------------


def test_saglik_satiri_OLAN_cihaz_runtime_health_doner(db, gateway):
    _cihaz(db, "SN2-001")
    _saglik(db, "SN2-001")
    kayit = _tek(_liste(db, _kullanici(db)), "SN2-001")

    assert kayit is not None
    rt = kayit["runtime_health"]
    assert rt is not None, "okuma yolu eksik — arayuz her zaman eski davranisa duser"
    assert rt["device_code"] == "SN2-001"
    assert rt["gateway_code"] == GW
    assert rt["connection_state"] == "smart_idle"
    assert rt["connected"] is False
    assert rt["reachable"] is False
    assert rt["configured_session_policy"] == "auto"
    assert rt["effective_session_policy"] == "smart"
    assert rt["operation_mode"] == "smart"
    assert rt["dial_in_interval_min"] == 720
    assert rt["next_expected_report_epoch"] == 1755643200.0
    assert rt["report_overdue_sec"] == 0.0
    assert rt["report_late"] is False
    assert rt["last_valid_contact_epoch"] == 1755600000.0
    assert rt["last_frame_epoch"] == 1755600000.0
    assert rt["ip_probe_status"] == "unknown"
    assert rt["tcp_probe_status"] == "connecting"
    assert rt["ip_endpoint_type"] == "listening"
    assert "updated_at" in rt


def test_saglik_satiri_OLMAYAN_cihaz_ACIKCA_null_doner(db, gateway):
    """Gateway <= 1.14: bu tasiyici YOK. Alan var ve `null` — eksik DEGIL.

    `null` ile "alan yok" arayuzde ayni sonuca cikar ama TESHISTE ayni sey
    degildir: alanin varligi "backend bu cihaz icin haber almadi" der,
    yoklugu "backend bu ozelligi hic tanimiyor" derdi.
    """
    _cihaz(db, "SN2-LEGACY")
    kayit = _tek(_liste(db, _kullanici(db)), "SN2-LEGACY")

    assert kayit is not None
    assert "runtime_health" in kayit
    assert kayit["runtime_health"] is None


def test_ESKI_cihaz_saglik_UYDURULMAZ(db, gateway):
    """Telemetri damgasindan `smart_idle` / gecikme URETILMEZ.

    Cazip kisayol: `last_update_at` + `dial_in_interval` ile bir sonraki
    raporu "tahmin etmek". O an backend, yalnizca gateway'in BILEBILECEGI
    bir seyi tahmin etmis olur; Smart bir cihaz mesru olarak uyurken
    telemetri de susar ve tahmin sistematik olarak yanlis cikar.
    """
    _cihaz(
        db, "SN2-TELEMETRI",
        communication_status=CommunicationStatus.ONLINE,
        last_update_at=datetime.now(timezone.utc),
    )
    kayit = _tek(_liste(db, _kullanici(db)), "SN2-TELEMETRI")
    assert kayit["runtime_health"] is None
    # Eski alan bozulmadan durur: arayuzun duseceği dal odur.
    assert kayit["communication_status"] == "online"


# ---------------------------------------------------------------------------
# 3. `report_late` BAYRAKTIR — kanonik durumu okuma yolunda da EZMEZ
# ---------------------------------------------------------------------------


def test_smart_idle_ve_report_late_AYNI_ANDA_doner(db, gateway):
    _cihaz(db, "DEV-LATE")
    _saglik(db, "DEV-LATE", report_late=True, report_overdue_sec=360.0)
    rt = _tek(_liste(db, _kullanici(db)), "DEV-LATE")["runtime_health"]

    assert rt["connection_state"] == "smart_idle", (
        "gecikme bayragi kanonik durumu ezdi — 'late' bir connection_state DEGIL"
    )
    assert rt["report_late"] is True
    assert rt["report_overdue_sec"] == 360.0


def test_smart_idle_lost_a_CEVRILMEZ(db, gateway):
    """Uyuyan cihaz + susmus telemetri: okuma yolu yine `smart_idle` der.

    Cihazin `communication_status`u OFFLINE (telemetri hattinin karari) ama
    saglik kanali onunla AYNI KOVAYA konmaz; konsaydi uyuyan filo SCADA'da
    arizali gorunurdu.
    """
    _cihaz(db, "DEV-SLEEP", communication_status=CommunicationStatus.OFFLINE)
    _saglik(db, "DEV-SLEEP", connection_state="smart_idle")
    kayit = _tek(_liste(db, _kullanici(db)), "DEV-SLEEP")

    assert kayit["runtime_health"]["connection_state"] == "smart_idle"
    assert kayit["communication_status"] == "offline"  # eski alan DEGISMEDI


@pytest.mark.parametrize(
    "durum",
    ["online", "smart_idle", "recovering", "lost", "listener_error", "unknown"],
)
def test_sozlesmedeki_TUM_durumlar_oldugu_gibi_doner(db, gateway, durum):
    _cihaz(db, "DEV-X")
    _saglik(db, "DEV-X", connection_state=durum)
    rt = _tek(_liste(db, _kullanici(db)), "DEV-X")["runtime_health"]
    assert rt["connection_state"] == durum


# ---------------------------------------------------------------------------
# 4. Bilinmeyen durum: alimda `unknown`a duser, OKUMADA oyle KALIR
# ---------------------------------------------------------------------------


def test_bilinmeyen_durum_okumada_da_unknown_kalir(db, gateway):
    """Gercek POST ucundan gecer: normalizasyon TEK yerde (alim) olmali.

    Okuma tarafinda ikinci bir normalizer olsaydi iki otorite dogar ve
    hangisinin kazandigi kimsenin bakmadigi bir yerde belirlenirdi.
    """
    _cihaz(db, "DEV-YENI")
    _ingest(db, [_wire("DEV-YENI", connection_state="brand_new_state")])
    rt = _tek(_liste(db, _kullanici(db)), "DEV-YENI")["runtime_health"]
    assert rt["connection_state"] == "unknown"


def test_okuma_yolu_late_i_DURUM_olarak_YAYMAZ(db, gateway):
    _cihaz(db, "DEV-BAD")
    _ingest(db, [_wire("DEV-BAD", connection_state="late", report_late=True)])
    rt = _tek(_liste(db, _kullanici(db)), "DEV-BAD")["runtime_health"]
    assert rt["connection_state"] != "late"
    assert rt["connection_state"] == "unknown"
    # Gecikme BILGISI kaybolmaz — bayrak yerinde durur.
    assert rt["report_late"] is True


# ---------------------------------------------------------------------------
# 5. KAPSAM — sagliga AYRI bir yetki yolu YOK
# ---------------------------------------------------------------------------


def _operator_kapsamli(db, gorunur: Device) -> User:
    """Yalnizca `gorunur` cihazini goren bir OPERATOR uret."""
    u = _kullanici(db, UserRole.OPERATOR, kod="op")
    alan = ResponsibilityArea(code="EKIP-A", name="Ekip A", is_active=True)
    db.add(alan)
    db.commit()
    db.execute(
        responsibility_area_users.insert().values(area_id=alan.id, user_id=u.id)
    )
    db.execute(
        responsibility_area_devices.insert().values(
            area_id=alan.id, device_id=gorunur.id
        )
    )
    db.commit()
    return u


def test_kapsam_disi_cihazin_SAGLIGI_sizmaz(db, gateway):
    benim = _cihaz(db, "DEV-BENIM")
    _cihaz(db, "DEV-BASKASININ")
    _saglik(db, "DEV-BENIM", connection_state="online")
    _saglik(db, "DEV-BASKASININ", connection_state="lost")

    op = _operator_kapsamli(db, benim)
    kayitlar = _liste(db, op)

    assert {k["code"] for k in kayitlar} == {"DEV-BENIM"}, "kapsam disi cihaz sizdi"
    assert _tek(kayitlar, "DEV-BASKASININ") is None
    # Ve gorunen cihazin sagligi normal gelir: kapsam saglik okumasini
    # KORLESTIRMEZ, yalnizca daraltir.
    assert _tek(kayitlar, "DEV-BENIM")["runtime_health"]["connection_state"] == "online"


def test_kapsamsiz_operator_HICBIR_saglik_gormez(db, gateway):
    _cihaz(db, "DEV-A")
    _saglik(db, "DEV-A")
    op = _kullanici(db, UserRole.OPERATOR, kod="opsuz")
    assert _liste(db, op) == []


def test_saglik_okumasi_kapsam_disi_kodu_HIC_SORMAZ(db, gateway):
    """Sorgunun KENDISI de kapsam disina cikmamali.

    Yalnizca yaniti filtrelemek yeterli gorunur ama degil: kapsam disi
    kodlari da sorup sonra atmak, bir sonraki degisiklikte (loglama, cache,
    metrik) o veriyi yanlislikla disari verecek bir kapi birakirdi.
    """
    benim = _cihaz(db, "DEV-BENIM")
    _cihaz(db, "DEV-BASKASININ")
    _saglik(db, "DEV-BENIM")
    _saglik(db, "DEV-BASKASININ")
    op = _operator_kapsamli(db, benim)

    with _sorgular(db) as kayit:
        _liste(db, op)

    saglik_sorgulari = [q for q in kayit if "device_runtime_health" in q.lower()]
    assert saglik_sorgulari, "saglik hic sorulmadi"
    assert not any("DEV-BASKASININ" in q for q in saglik_sorgulari), (
        "kapsam disi cihaz kodu saglik sorgusuna girdi"
    )


def test_saglik_KENDI_BASINA_cihaz_gorunur_kilmaz(db, gateway):
    """Cihazi olmayan bir saglik satiri (yetim) listeyi GENISLETMEZ.

    Yetim satir gercek: tabloda FK YOK (gateway backend'in henuz gormedigi
    bir kodu bildirebilir). Saglik bir JOIN ile baglansaydi ve join yonu
    yanlis secilseydi bu satir listeye bir "cihaz" ekleyebilirdi.
    """
    _cihaz(db, "DEV-A")
    _saglik(db, "DEV-A")
    _saglik(db, "HAYALET-CIHAZ", connection_state="online")

    kayitlar = _liste(db, _kullanici(db))
    assert {k["code"] for k in kayitlar} == {"DEV-A"}


# ---------------------------------------------------------------------------
# 6-8. Kume butunlugu: sayfalama / filtre / cogaltma
# ---------------------------------------------------------------------------


def test_saglik_baglama_cihaz_SAYISINI_degistirmez(db, gateway):
    for i in range(7):
        _cihaz(db, f"DEV-{i:02d}")
    # Yalnizca bir kismi saglik satiri tasisin.
    for i in (0, 3, 6):
        _saglik(db, f"DEV-{i:02d}")

    kayitlar = _liste(db, _kullanici(db))
    assert len(kayitlar) == 7
    kodlar = [k["code"] for k in kayitlar]
    assert len(kodlar) == len(set(kodlar)), "saglik baglama satir COGALTTI"
    assert sum(1 for k in kayitlar if k["runtime_health"] is not None) == 3


def test_gateway_filtresi_saglikla_BOZULMAZ(db, gateway):
    db.add(
        Gateway(code="GW-2", name="GW-2", host="10.0.0.2", listen_port=20000,
                token="t" * 40, is_active=True)
    )
    db.commit()
    _cihaz(db, "DEV-GW1", gateway_code=GW)
    _cihaz(db, "DEV-GW2", gateway_code="GW-2")
    _saglik(db, "DEV-GW1")
    _saglik(db, "DEV-GW2", gateway_code="GW-2")

    sadece = _liste(db, _kullanici(db), gateway_code=GW)
    assert {k["code"] for k in sadece} == {"DEV-GW1"}
    assert sadece[0]["runtime_health"] is not None


def test_siralama_saglikla_DEGISMEZ(db, gateway):
    """Liste `name` sirasindadir (repository); saglik ona dokunamaz."""
    _cihaz(db, "DEV-C", name="Cem")
    _cihaz(db, "DEV-A", name="Ali")
    _cihaz(db, "DEV-B", name="Berk")
    _saglik(db, "DEV-B")

    kodlar = [k["code"] for k in _liste(db, _kullanici(db))]
    assert kodlar == ["DEV-A", "DEV-B", "DEV-C"]


class _Yanit:
    """`Response` yerine: yalnizca `headers` sozlugu gerekiyor."""

    def __init__(self):
        self.headers: dict[str, str] = {}


def _ctx(db, rol: UserRole = UserRole.ENGINEER, kod: str = "apikey"):
    """GERCEK `ApiKeyContext` — kapsam cozumu `ctx.user` uzerinden gecer.

    `api_key` alanina dokunulmuyor (bu iki uc yalnizca sahibini okur), ama
    tip sahte olsaydi kapsam kontrolu de sahte olurdu; kullanici gercek.
    """
    from app.api.public_deps import ApiKeyContext

    return ApiKeyContext(api_key=None, user=_kullanici(db, rol, kod))


def test_public_liste_sayfalama_ve_toplam_sayi_BOZULMAZ(db, gateway):
    """`/public/devices` limit/offset + `X-Total-Count` ile calisir.

    Saglik AYRI SELECT ile baglandigi icin sayfalanmis sorgu ve sayim
    sorgusu HIC degismez; bir JOIN olsaydi ikisi de risk altindaydi.
    """
    from app.api import public as public_api

    for i in range(5):
        _cihaz(db, f"PD-{i:02d}", name=f"Cihaz {i:02d}")
    _saglik(db, "PD-01")
    _saglik(db, "PD-03")

    yanit = _Yanit()
    sayfa = public_api.list_devices(
        response=yanit, db=db, ctx=_ctx(db), gateway_code=None, model=None,
        limit=2, offset=2,
    )
    kayitlar = [DeviceRead.model_validate(s).model_dump(mode="json") for s in sayfa]

    assert yanit.headers["X-Total-Count"] == "5", "toplam sayi saglikla degisti"
    assert [k["code"] for k in kayitlar] == ["PD-02", "PD-03"]
    assert kayitlar[0]["runtime_health"] is None
    assert kayitlar[1]["runtime_health"] is not None


def test_public_model_filtresi_saglikla_BOZULMAZ(db, gateway):
    from app.api import public as public_api

    _cihaz(db, "PD-SN2", model="horstmann_sn_2_0")
    _cihaz(db, "PD-KIT", model="horstmann_pole_master_kit")
    _saglik(db, "PD-SN2")
    _saglik(db, "PD-KIT")

    yanit = _Yanit()
    sayfa = public_api.list_devices(
        response=yanit, db=db, ctx=_ctx(db), gateway_code=None,
        model="horstmann_sn_2_0", limit=100, offset=0,
    )
    assert [d.code for d in sayfa] == ["PD-SN2"]
    assert yanit.headers["X-Total-Count"] == "1"


def test_public_DETAY_ucu_liste_ile_TUTARLI(db, gateway):
    """Ayni `DeviceRead` semasi -> ayni alan, ayni deger.

    Liste ve detay ayri zenginlestirme yollari kullansaydi biri saglik
    tasir digeri tasimazdi ve fark ancak sahada gorunurdu.
    """
    from app.api import public as public_api

    _cihaz(db, "PD-DETAY")
    _saglik(db, "PD-DETAY", connection_state="recovering")

    detay = DeviceRead.model_validate(
        public_api.get_device(code="PD-DETAY", db=db, ctx=_ctx(db))
    ).model_dump(mode="json")
    liste = _tek(_liste(db, _kullanici(db)), "PD-DETAY")

    assert detay["runtime_health"] == liste["runtime_health"]
    assert detay["runtime_health"]["connection_state"] == "recovering"


# ---------------------------------------------------------------------------
# N+1 KANITI
# ---------------------------------------------------------------------------


class _sorgular:
    """`with` blogu boyunca calisan SQL ifadelerini toplar."""

    def __init__(self, db):
        self.db = db
        self.kayit: list[str] = []

    def __enter__(self) -> list[str]:
        self.engine = self.db.info["engine"]

        def _dinle(conn, cursor, statement, parameters, context, executemany):
            self.kayit.append(f"{statement} -- {parameters!r}")

        self._dinle = _dinle
        event.listen(self.engine, "before_cursor_execute", _dinle)
        return self.kayit

    def __exit__(self, *a):
        event.remove(self.engine, "before_cursor_execute", self._dinle)
        return False


def test_saglik_okumasi_N_ARTI_1_DEGIL(db, gateway):
    """Sorgu sayisi cihaz sayisindan BAGIMSIZ olmali.

    Olcut mutlak bir sayi degil FARK: 3 cihazlik ve 40 cihazlik listede
    calisan sorgu sayisi AYNI olmali. Mutlak sayiya bagli bir test,
    alakasiz bir sorgu eklendiginde anlamsizca kirilirdi.
    """
    kullanici = _kullanici(db)

    for i in range(3):
        _cihaz(db, f"AZ-{i:02d}")
        _saglik(db, f"AZ-{i:02d}")
    with _sorgular(db) as az:
        _liste(db, kullanici)
    az_sayi = len(az)

    for i in range(40):
        _cihaz(db, f"COK-{i:02d}")
        _saglik(db, f"COK-{i:02d}")
    with _sorgular(db) as cok:
        _liste(db, kullanici)
    cok_sayi = len(cok)

    assert az_sayi == cok_sayi, (
        f"cihaz sayisi artinca sorgu sayisi arti ({az_sayi} -> {cok_sayi}) — N+1"
    )
    # Ve saglik icin GERCEKTEN tek sorgu var.
    saglik_sorgulari = [q for q in cok if "device_runtime_health" in q.lower()]
    assert len(saglik_sorgulari) == 1, (
        f"saglik icin {len(saglik_sorgulari)} sorgu kosuyor, 1 bekleniyordu"
    )


def test_saglik_satiri_YOKKEN_de_tek_sorgu(db, gateway):
    """Satir bulunmamasi cihaz basina bir arama denemesine DONMEMELI."""
    kullanici = _kullanici(db)
    for i in range(12):
        _cihaz(db, f"BOS-{i:02d}")
    with _sorgular(db) as kayit:
        _liste(db, kullanici)
    saglik_sorgulari = [q for q in kayit if "device_runtime_health" in q.lower()]
    assert len(saglik_sorgulari) == 1


def test_BOS_liste_saglik_sorgusu_ACMAZ(db, gateway):
    op = _kullanici(db, UserRole.OPERATOR, kod="bos")
    with _sorgular(db) as kayit:
        _liste(db, op)
    assert not [q for q in kayit if "device_runtime_health" in q.lower()]


# ---------------------------------------------------------------------------
# 10. `null` epoch `null` KALIR
# ---------------------------------------------------------------------------


def test_null_epoch_SIFIRA_donmez(db, gateway):
    """`null` = HIC OLMADI. 0 = 1970 — panelde gecerli bir tarih gibi durur."""
    _cihaz(db, "DEV-NULL")
    _saglik(
        db, "DEV-NULL",
        next_expected_report_epoch=None,
        last_valid_contact_epoch=None,
        last_frame_epoch=None,
        last_probe_epoch=None,
        report_overdue_sec=None,
        dial_in_interval_min=None,
    )
    rt = _tek(_liste(db, _kullanici(db)), "DEV-NULL")["runtime_health"]

    for alan in (
        "next_expected_report_epoch", "last_valid_contact_epoch",
        "last_frame_epoch", "last_probe_epoch", "report_overdue_sec",
        "dial_in_interval_min",
    ):
        assert rt[alan] is None, f"{alan} null iken baska bir degere dondu"


def test_SIFIR_epoch_null_a_donmez(db, gateway):
    """Ters yon de korunur: gercek 0.0 gelirse `null` diye yutulmaz."""
    _cihaz(db, "DEV-ZERO")
    _saglik(db, "DEV-ZERO", report_overdue_sec=0.0)
    rt = _tek(_liste(db, _kullanici(db)), "DEV-ZERO")["runtime_health"]
    assert rt["report_overdue_sec"] == 0.0
    assert rt["report_overdue_sec"] is not None


# ---------------------------------------------------------------------------
# 11. TAZELIK — karar arayuzde, ama damga DOGRU tasinmali
# ---------------------------------------------------------------------------


def test_updated_at_UTC_farkindaligiyla_serialize_edilir(db, gateway):
    """Naive damga tarayicida YEREL saat sanilirdi — UTC+3'te 3 saat kayma.

    O kayma tek basina ozelligi kapatirdi: her gozlem surekli "bayat"
    sayilir ve arayuz kalici olarak eski davranisa duserdi. Bayatlik esigi
    arayuzde (`RUNTIME_STALE_AFTER_MS`); backend'in isi damgayi ANLAMINI
    KAYBETTIRMEDEN tasimak.
    """
    _cihaz(db, "DEV-TS")
    an = datetime(2026, 8, 20, 9, 30, 0, tzinfo=timezone.utc)
    _saglik(db, "DEV-TS", updated_at=an)
    rt = _tek(_liste(db, _kullanici(db)), "DEV-TS")["runtime_health"]

    metin = rt["updated_at"]
    assert metin.endswith("Z") or "+00:00" in metin, (
        f"offset'siz damga: tarayici bunu YEREL saat sanar ({metin!r})"
    )
    cozulen = datetime.fromisoformat(metin.replace("Z", "+00:00"))
    assert cozulen.tzinfo is not None
    assert cozulen.astimezone(timezone.utc) == an


def test_BAYAT_gozlem_yine_de_OLDUGU_GIBI_doner(db, gateway):
    """Backend bayatliga KARAR VERMEZ, damgayi verir.

    Karar arayuzde: `lost` (gateway'in karari) ile "Grid uzun suredir haber
    almadi" AYNI SEY DEGIL. Backend ikincisini birinciye cevirseydi, gateway
    sustugu icin filo `lost` gorunur ve gercek `lost` ayirt edilemezdi.
    """
    _cihaz(db, "DEV-BAYAT")
    eski = datetime.now(timezone.utc) - timedelta(hours=6)
    _saglik(db, "DEV-BAYAT", connection_state="online", updated_at=eski)
    rt = _tek(_liste(db, _kullanici(db)), "DEV-BAYAT")["runtime_health"]

    assert rt["connection_state"] == "online", (
        "backend bayatligi duruma cevirdi — o karar arayuzun"
    )
    cozulen = datetime.fromisoformat(rt["updated_at"].replace("Z", "+00:00"))
    assert (datetime.now(timezone.utc) - cozulen) > timedelta(hours=5)


# ---------------------------------------------------------------------------
# Sema <-> model kaymasi
# ---------------------------------------------------------------------------

#: Modelde OLAN ama okuma semasinda BILEREK olmayan kolonlar. Bunlar
#: bayat-yazma / uzlastirma defteridir, cihazin durumu degil;
#: `gateway_instance_id` ayrica gateway'in kalici ic kimligidir ve `/public`
#: ucundan disari sizmamali.
DISARIDA = {
    "gateway_instance_id",
    "boot_id",
    "sequence",
    "snapshot_id",
    "snapshot_batch_index",
}


def test_okuma_semasi_model_ile_KAYMAZ():
    """Modele yeni sozlesme kolonu eklenip sema unutulursa BURASI kirilir.

    Aksi halde alan sessizce saklanir ve HIC gorunmezdi — bu PR'i acan
    blokajin tam olarak kucuk hali.
    """
    kolonlar = {k.key for k in inspect(DeviceRuntimeHealth).columns}
    sema = set(DeviceRuntimeHealthRead.model_fields)

    eksik = kolonlar - sema - DISARIDA
    assert not eksik, f"modelde var, okuma semasinda YOK: {sorted(eksik)}"

    fazla = sema - kolonlar
    assert not fazla, f"semada var, modelde YOK (uydurma alan): {sorted(fazla)}"

    cakisma = sema & DISARIDA
    assert not cakisma, f"bilerek disarida birakilan alan semaya girmis: {cakisma}"


def test_cihaz_basina_EN_FAZLA_BIR_saglik_satiri():
    """`device_code` BIRINCIL ANAHTAR — varsayim degil, sema gercegi.

    "Sozluge cevirmek veri kaybetmez" ve "join cogaltmaz" iddialari buna
    dayaniyor; dayanak degisirse burasi kirilsin.
    """
    pk = [k.key for k in inspect(DeviceRuntimeHealth).primary_key]
    assert pk == ["device_code"]


# ---------------------------------------------------------------------------
# KIT: setin kendi oturumu yok, sagligi da kitten devralir
# ---------------------------------------------------------------------------


def test_set_sagligi_KITTEN_devralinir(db, gateway):
    """Aksi halde ayni donanim ayni ekranda IKI RENK gosterirdi.

    Uyuyan bir Smart kit gateway'e gore `smart_idle` (mavi, saglikli);
    setleri saglik satiri olmadigi icin eski davranisa duser ve telemetri
    sustugu icin KIRMIZI gorunurdu. `communication_status` zaten ayni
    gerekceyle kitten devraliniyor.
    """
    kit = _cihaz(
        db, "PMK-001", model="horstmann_pole_master_kit",
        communication_status=CommunicationStatus.OFFLINE,
    )
    _cihaz(
        db, "PMK-001-S1", model="horstmann_pmk_set",
        parent_device_id=kit.id, subunit_index=1,
    )
    _saglik(db, "PMK-001", connection_state="smart_idle")

    kayitlar = _liste(db, _kullanici(db))
    set_kaydi = _tek(kayitlar, "PMK-001-S1")
    kit_kaydi = _tek(kayitlar, "PMK-001")

    assert kit_kaydi["runtime_health"]["connection_state"] == "smart_idle"
    assert set_kaydi["runtime_health"] is not None, "set kit sagligini devralmadi"
    assert set_kaydi["runtime_health"]["connection_state"] == "smart_idle"
    # Devralinan satirin sahibi HALA kit: teshis yaniltici olmasin.
    assert set_kaydi["runtime_health"]["device_code"] == "PMK-001"


def test_kit_sagligi_YOKSA_set_de_null_kalir(db, gateway):
    kit = _cihaz(db, "PMK-002", model="horstmann_pole_master_kit")
    _cihaz(
        db, "PMK-002-S1", model="horstmann_pmk_set",
        parent_device_id=kit.id, subunit_index=1,
    )
    kayitlar = _liste(db, _kullanici(db))
    assert _tek(kayitlar, "PMK-002-S1")["runtime_health"] is None


# ---------------------------------------------------------------------------
# ZENGINLESTIRMEDEN GECMEYEN UC: `/internal/devices` SEMAYI PATLATMAMALI
# ---------------------------------------------------------------------------


def test_internal_ucu_ham_satirla_da_SEMADAN_gecer(db, gateway):
    """`runtime_health` varsayilani `None` olmak ZORUNDA.

    O uc ham ORM satirlarini doner (`annotate` cagirmaz). Alan zorunlu
    olsaydi iec104-outbound'un cihaz listesi 500 ile duserdi ve point
    registry HIC kurulmazdi.
    """
    _cihaz(db, "DEV-INT")
    _saglik(db, "DEV-INT")
    ham = db.scalars(select(Device).where(Device.code == "DEV-INT")).all()
    kayit = DeviceRead.model_validate(ham[0]).model_dump(mode="json")
    assert kayit["runtime_health"] is None


# ---------------------------------------------------------------------------
# UCTAN UCA: GERCEK POST -> DB -> GERCEK GET (dogrudan DB yazimi YOK)
# ---------------------------------------------------------------------------


def _wire(kod: str, **ustune) -> dict:
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


def _zarf(devices: list[dict], *, boot_id: int = 12, sequence: int = 1) -> dict:
    return {
        "schema": "device_health_v1",
        "gateway_code": GW,
        "gateway_instance_id": "kalici-instance",
        "boot_id": boot_id,
        "sequence": sequence,
        "snapshot": False,
        "device_total": len(devices),
        "devices": devices,
    }


def _ingest(db, devices: list[dict], **zarf_ustune) -> None:
    """GERCEK gateway ucundan gecir (auth + sema dogrulama + adaptor dahil)."""
    from app.api import gateways as gw_api

    gw_api.report_device_runtime_health(
        gateway_code=GW,
        payload=_zarf(devices, **zarf_ustune),
        db=db,
        x_gateway_token=TOKEN,
        x_gateway_code=GW,
        x_gateway_instance_id="kalici-instance",
        x_request_id="req-1",
    )


def _asgi(method: str, yol: str, govde: dict | None, basliklar: dict[str, str]):
    """TestClient DEGIL ham ASGI: proje httpx'e bagli degil (ayni gerekce
    `test_license_gate.py` ve alim testlerinde de yazili)."""
    from app.main import app

    ham = json.dumps(govde).encode("utf-8") if govde is not None else b""
    mesajlar: list[dict] = []

    async def send(mesaj):
        mesajlar.append(mesaj)

    async def receive():
        return {"type": "http.request", "body": ham, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
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
    baytlar = b"".join(
        m.get("body", b"") for m in mesajlar if m["type"] == "http.response.body"
    )
    return durum, baytlar


def test_UCTAN_UCA_post_saglik_sonra_get_devices(db, gateway):
    """Gateway POST'undan arayuzun okudugu JSON'a kadar TEK akis.

    Dogrudan DB yazimi YOK: veri gercek `/gateways/{kod}/device-health`
    ucundan girer ve gercek `GET /devices` ucundan cikar. Aradaki her halka
    (auth, sema dogrulama, adaptor, upsert, okuma haritasi, pydantic
    serialize) gercekten kosar.
    """
    from app.main import app

    _cihaz(db, "SN2-E2E", communication_status=CommunicationStatus.OFFLINE)
    kullanici = _kullanici(db)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: kullanici
    try:
        durum, _ = _asgi(
            "POST",
            f"{settings.api_prefix}/gateways/{GW}/device-health",
            _zarf([_wire("SN2-E2E", report_late=True, report_overdue_sec=90.0)]),
            {"X-Gateway-Token": TOKEN, "X-Gateway-Code": GW},
        )
        assert durum == 204

        # Kalicilik
        satir = db.get(DeviceRuntimeHealth, "SN2-E2E")
        assert satir is not None and satir.connection_state == "smart_idle"

        # Normal API/auth yolundan okuma
        durum, govde = _asgi("GET", f"{settings.api_prefix}/devices", None, {})
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert durum == 200
    kayit = next(k for k in json.loads(govde) if k["code"] == "SN2-E2E")

    rt = kayit["runtime_health"]
    assert rt is not None, "POST -> DB -> GET zinciri hala kopuk"
    # Uyku SAGLIKLI: kanonik durum `smart_idle`, gecikme AYRI bayrak.
    assert rt["connection_state"] == "smart_idle"
    assert rt["report_late"] is True
    assert rt["report_overdue_sec"] == 90.0
    assert rt["next_expected_report_epoch"] == 1755643200.0
    assert rt["last_probe_epoch"] is None
    # Telemetri turevli eski alan DEGISMEDI — iki gercek yan yana durur.
    assert kayit["communication_status"] == "offline"


def test_UCTAN_UCA_snapshot_uzlastirmasi_okumaya_yansir(db, gateway):
    """Gateway config'inden cikan cihaz: satir silinir, okuma `null` doner."""
    from app.api import gateways as gw_api

    _cihaz(db, "DEV-KALAN")
    _cihaz(db, "DEV-GIDEN")
    kullanici = _kullanici(db)

    _ingest(db, [_wire("DEV-KALAN"), _wire("DEV-GIDEN")], sequence=1)
    assert _tek(_liste(db, kullanici), "DEV-GIDEN")["runtime_health"] is not None

    # Tek partilik TAM snapshot artik yalnizca DEV-KALAN'i bildiriyor.
    govde = _zarf([_wire("DEV-KALAN")], sequence=2)
    govde.update(
        snapshot=True, snapshot_id="12-1",
        snapshot_batch_index=0, snapshot_batch_count=1,
    )
    gw_api.report_device_runtime_health(
        gateway_code=GW, payload=govde, db=db, x_gateway_token=TOKEN,
        x_gateway_code=GW, x_gateway_instance_id="kalici-instance",
        x_request_id="req-2",
    )

    kayitlar = _liste(db, kullanici)
    assert _tek(kayitlar, "DEV-KALAN")["runtime_health"] is not None
    assert _tek(kayitlar, "DEV-GIDEN")["runtime_health"] is None, (
        "uzlastirma sildi ama okuma hala eski satiri gosteriyor"
    )
    # Cihaz kaydinin KENDISI durur — saglik satiri silindi, cihaz degil.
    assert _tek(kayitlar, "DEV-GIDEN") is not None


def test_annotate_dis_sozlesmesi_KORUNUR(db, gateway):
    """`annotate` eski turetilmis alanlari doldurmaya DEVAM eder."""
    kit = _cihaz(db, "PMK-003", model="horstmann_pole_master_kit")
    _cihaz(
        db, "PMK-003-S1", model="horstmann_pmk_set",
        parent_device_id=kit.id, subunit_index=1,
    )
    satirlar = device_kit_service.annotate(
        db, list(db.scalars(select(Device).order_by(Device.code)).all())
    )
    esleme = {d.code: d for d in satirlar}
    assert esleme["PMK-003-S1"].parent_device_code == "PMK-003"
    assert esleme["PMK-003"].satellite_set_count == 1
    assert esleme["PMK-003-S1"].subunit_satellites == [1, 2, 3]
