"""Gateway yazilim guncelleme yonetimi (GU-01..GU-20).

NE KILITLENIYOR
---------------
Guncellemeyi yapan mekanizma DEGISMEDI: backend -> request.json -> e1-gwd ->
docker compose. Bu dosya, o mekanizmanin uzerine eklenen dort seyi korur:
hedefi SECMEK (ve digest'e sabitlemek), istegi baslatmak, sonucu izlemek,
denetime yazmak.

Buradaki hatalarin tamami SESSIZDIR:

  * Hedef digest'e sabitlenmezse, operatorun onayladigi surum ile kurulan
    surum ayrisir (etiket arada baska bir imaja kayabilir).
  * Geri alma "en guncel"e duserse, geri almak isteyen operatore TAM TERSI
    yapilir ve kimse fark etmez.
  * Gelistirme etiketi (`:main`, `:sha-*`) "guncelleme mevcut" diye
    sunulursa, saha test edilmemis bir imaja yonlendirilir.
  * Uyumsuz gateway sessiz birakilirsa arayuz "Akilli" derken cihaz surekli
    modda calisir — B5'in kapatmak icin var oldugu hata sinifi.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api import gateways as gateways_api
from app.db.base import Base
from app.models.device import Device
from app.models.enums import UserRole
from app.models.gateway import Gateway
from app.models.gateway_health import GatewayHealth
from app.models.gateway_update import GatewayUpdate
from app.models.system_event import SystemEvent
from app.models.user import User
from app.schemas.dnp3_extended import Dnp3ExtendedSettings
from app.schemas.gateway_agent import (
    GatewayAgentStatus,
    GatewayApplyStatus,
    LocalGateway,
)
from app.schemas.gateway_update import GatewayUpdatePrepareRequest
from app.services import (
    gateway_agent_service,
    gateway_compatibility,
    gateway_release_service,
    gateway_update_service,
)
from app.services.gateway_release_service import RegistryImage
from app.services.ingest_service import hash_gateway_token

GW = "GW-UPD"
REPO = "ghcr.io/enerjione/enerjione-grid-dnp3-gateway"
D_ESKI = "sha256:" + "a" * 64
D_YENI = "sha256:" + "b" * 64


# ---------------------------------------------------------------------------
# Fixture'lar
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


@pytest.fixture()
def kurulumcu() -> User:
    return User(id=1, username="kurulumcu", role=UserRole.INSTALLER)


@pytest.fixture()
def gateway(db) -> Gateway:
    gw = Gateway(
        code=GW,
        name="Saha Gateway",
        host="127.0.0.1",
        listen_port=8100,
        token="tok",
        token_hash=hash_gateway_token("tok"),
    )
    db.add(gw)
    db.flush()
    return gw


class Ajan:
    """Host ajaninin taklidi — istekleri yakalar, durumu doner."""

    def __init__(self) -> None:
        self.istekler: list[dict] = []
        self.gateways: list[LocalGateway] = []
        self.last_apply: GatewayApplyStatus | None = None
        self.hata: Exception | None = None

    def read_status(self) -> GatewayAgentStatus:
        return GatewayAgentStatus(
            available=True,
            docker_available=True,
            gateways=list(self.gateways),
            last_apply=self.last_apply,
        )

    def request_update(self, code, actor, *, nats_url=None, image=None) -> str:
        if self.hata is not None:
            raise self.hata
        self.istekler.append({"code": code, "actor": actor, "image": image})
        return f"req-{len(self.istekler)}"


@pytest.fixture()
def ajan(monkeypatch) -> Ajan:
    a = Ajan()
    a.gateways = [
        LocalGateway(
            code=GW,
            name="Saha Gateway",
            state="running",
            image=f"{REPO}:latest",
            tracked_image=f"{REPO}:latest",
            image_digest=D_ESKI,
            remote_digest=D_YENI,
            update_available=True,
            local_version="1.11.4",
            remote_version="1.13.0",
        )
    ]
    monkeypatch.setattr(gateway_agent_service, "read_status", a.read_status)
    monkeypatch.setattr(gateway_agent_service, "request_update", a.request_update)
    return a


@pytest.fixture()
def kayit_defteri(monkeypatch):
    """Kayit defteri taklidi; testler `sonuc` alanini degistirir."""

    kutu = {"sonuc": RegistryImage(version="1.13.0", digest=D_YENI)}

    def fetch(ref: str) -> RegistryImage:
        return kutu["sonuc"]

    monkeypatch.setattr(gateway_release_service, "fetch", fetch)
    return kutu


def _akilli_cihaz(db, kod: str = "DEV-1") -> Device:
    d = Device(
        code=kod,
        name=kod,
        gateway_code=GW,
        ip_address="10.0.0.5",
        latitude=39.0,
        longitude=35.0,
        dnp3_extended={"ip_endpoint_type": "initiating", "session_policy": "smart"},
    )
    db.add(d)
    db.flush()
    return d


def _hazirla(db, kurulumcu, hedef: str | None = None):
    return gateways_api.prepare_gateway_update(
        gateway_code=GW,
        payload=GatewayUpdatePrepareRequest(target_image=hedef) if hedef else None,
        current_user=kurulumcu,
        db=db,
    )


# ---------------------------------------------------------------------------
# GU-01 / GU-02 / GU-03 — surum gorunumu
# ---------------------------------------------------------------------------


def test_GU_01_mevcut_surum_ajandan_okunur(db, gateway, kurulumcu, ajan):
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.current_version == "1.11.4"
    assert durum.current_version_source == "agent"
    assert durum.installed_locally is True


def test_GU_01_UZAK_gateway_surumu_saglik_heartbeatinden_gelir(db, gateway, kurulumcu, ajan):
    """Bu cihaza kurulu OLMAYAN gateway: ajan onu hic gormez.

    Surumun tek kaynagi gateway'in kendi heartbeat'idir. Bos birakmak,
    uzak sahadaki tum gateway'leri "surum bilinmiyor" yapardi.
    """
    ajan.gateways = []
    db.add(
        GatewayHealth(
            gateway_code=GW,
            status="ok",
            gateway_version="1.13.0",
            reported_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.current_version == "1.13.0"
    assert durum.current_version_source == "health"
    assert durum.installed_locally is False


def test_GU_02_guncelleme_mevcut_gorunur(db, gateway, kurulumcu, ajan):
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.update_available is True
    assert durum.available_version == "1.13.0"


def test_GU_02_bilinmiyor_GUNCEL_diye_gosterilmez(db, gateway, kurulumcu, ajan):
    """Uc durumluluk korunur: kayit defterine ulasilamadiysa `None`."""
    ajan.gateways[0].update_available = None
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.update_available is None


def test_GU_03_zaten_guncelse_hazirlik_REDDEDILIR(db, gateway, kurulumcu, ajan, kayit_defteri):
    ajan.gateways[0].image_digest = D_YENI  # calisan imaj = hedef
    with pytest.raises(HTTPException) as exc:
        _hazirla(db, kurulumcu)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "already_current"


# ---------------------------------------------------------------------------
# GU-04 / GU-05 / GU-12 / GU-13 — hedef secimi ve dogrulama
# ---------------------------------------------------------------------------


def test_GU_04_gecersiz_referans_reddedilir(db, gateway, kurulumcu, ajan, kayit_defteri):
    with pytest.raises(HTTPException) as exc:
        _hazirla(db, kurulumcu, hedef="!!gecersiz!!")
    assert exc.value.status_code == 422


def test_GU_05_hedef_DIGESTE_SABITLENIR(db, gateway, kurulumcu, ajan, kayit_defteri):
    """Checksum'in ta kendisi: ajana giden referans digest tasir.

    Uyusmazlik olursa `docker pull` REDDEDER — kendi SHA256 dogrulayicimizi
    yazmiyoruz, container runtime'in zaten yaptigi isi tekrarlamak olurdu.
    """
    durum = _hazirla(db, kurulumcu)
    assert durum.expected_digest == D_YENI
    assert durum.target_image == f"{REPO}:latest@{D_YENI}"

    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    assert ajan.istekler[-1]["image"] == f"{REPO}:latest@{D_YENI}"


def test_GU_12_alternatif_kayit_defteri_referansi_kabul(db, gateway, kurulumcu, ajan, kayit_defteri):
    """Yerel/alternatif registry — servis tooling'i icin.

    GHCR zorunlu degil; ajanin allowlist/regex modeli aynen gecerli.
    """
    durum = _hazirla(db, kurulumcu, hedef="localhost:5000/e1/gateway:1.13.0")
    assert durum.target_image == f"localhost:5000/e1/gateway:1.13.0@{D_YENI}"


def test_GU_13_kayit_defteri_cevapsizsa_FAIL_CLOSED(db, gateway, kurulumcu, ajan, kayit_defteri):
    """Digest cozulemiyorsa guncelleme HIC baslamaz.

    "Etiketle gonderelim, nasilsa ceker" demek, onaylanan ile kurulanin
    ayrismasina kapi acardi.
    """
    kayit_defteri["sonuc"] = RegistryImage(error="unreachable")
    with pytest.raises(HTTPException) as exc:
        _hazirla(db, kurulumcu)
    assert exc.value.status_code == 502
    assert ajan.istekler == [], "hedef dogrulanmadan ajana istek yazildi"


def test_GU_13_hazirlik_yapilmadan_uygulama_REDDEDILIR(db, gateway, kurulumcu, ajan):
    with pytest.raises(HTTPException) as exc:
        gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "not_prepared"


# ---------------------------------------------------------------------------
# GU-06 / GU-07 — yetki ve es zamanlilik
# ---------------------------------------------------------------------------


def test_GU_06_yazma_uclari_INSTALLER_a_ozel():
    """Guncelleme sahada gorunur bir kesinti; operator/engineer tetikleyememeli."""
    for fn in (
        gateways_api.prepare_gateway_update,
        gateways_api.apply_gateway_update,
        gateways_api.rollback_gateway_update,
    ):
        kaynak = inspect.getsource(fn)
        assert "require_role(UserRole.INSTALLER)" in kaynak, f"{fn.__name__} yetki kapisi yok"


def test_GU_06_okuma_ucu_engineera_da_acik():
    """"Hangi surum kosuyor" teshisin ilk adimi; kurulumcuyu beklememeli."""
    kaynak = inspect.getsource(gateways_api.get_gateway_update)
    assert "UserRole.ENGINEER" in kaynak and "UserRole.INSTALLER" in kaynak


def test_GU_07_devam_eden_guncelleme_varken_yeni_istek_REDDEDILIR(
    db, gateway, kurulumcu, ajan, kayit_defteri
):
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    with pytest.raises(HTTPException) as exc:
        _hazirla(db, kurulumcu)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "update_in_progress"


def test_GU_07_ajan_kuyrugu_doluysa_da_REDDEDILIR(db, gateway, kurulumcu, ajan, kayit_defteri):
    """Ikinci kapi: ajan tarafinda islenmemis istek varsa ustune yazmayiz."""
    _hazirla(db, kurulumcu)
    ajan.hata = gateway_agent_service.GatewayAgentError("request_pending")
    with pytest.raises(HTTPException) as exc:
        gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# GU-08 / GU-09 — sonuc izleme
# ---------------------------------------------------------------------------


def test_GU_09_basarili_gecis(db, gateway, kurulumcu, ajan, kayit_defteri):
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    ajan.last_apply = GatewayApplyStatus(id="req-1", action="update", ok=True, running=False)

    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.status == "succeeded"
    assert durum.from_version == "1.11.4"
    assert durum.target_version == "1.13.0"
    assert durum.finished_at is not None


def test_GU_08_basarisizlik_ve_SEBEBI_gorunur(db, gateway, kurulumcu, ajan, kayit_defteri):
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    ajan.last_apply = GatewayApplyStatus(
        id="req-1", action="update", ok=False, running=False,
        stage="pull", message="imaj indirilemedi", detail="manifest unknown",
    )
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.status == "failed"
    assert "pull" in durum.error and "manifest unknown" in durum.error


def test_BASKA_istegin_sonucu_bizim_durumumuza_YAZILMAZ(db, gateway, kurulumcu, ajan, kayit_defteri):
    """Ajan baska bir istegi (or. restart) isliyorsa onun sonucu bizimki
    sanilmamali — "basarili" demek sormadan verilmis bir iddia olurdu."""
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    ajan.last_apply = GatewayApplyStatus(id="BASKA-ID", action="restart", ok=True)
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.status == "requested"


# ---------------------------------------------------------------------------
# GU-10 — denetim
# ---------------------------------------------------------------------------


def _olaylar(db, tip: str) -> list[SystemEvent]:
    return list(db.query(SystemEvent).filter(SystemEvent.event_type == tip).all())


def test_GU_10_denetim_eski_yeni_ve_digest_tasir(db, gateway, kurulumcu, ajan, kayit_defteri):
    import json

    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)

    for tip in ("gateway_update_requested", "gateway_update_started"):
        olaylar = _olaylar(db, tip)
        assert olaylar, f"{tip} olayi yazilmadi"
        meta = json.loads(olaylar[-1].metadata_json or "{}")
        assert meta["from_version"] == "1.11.4"
        assert meta["to_version"] == "1.13.0"
        assert meta["expected_digest"] == D_YENI


# ---------------------------------------------------------------------------
# GU-11 / GU-19 — geri alma
# ---------------------------------------------------------------------------


def test_GU_11_onceki_imaj_bilinmiyorsa_geri_alma_409(db, gateway, kurulumcu, ajan):
    with pytest.raises(HTTPException) as exc:
        gateways_api.rollback_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "no_rollback_target"
    assert ajan.istekler == [], "hedefi bilinmeyen geri alma ajana gitti"


def test_GU_11_geri_alma_ONCEKI_imaja_doner(db, gateway, kurulumcu, ajan, kayit_defteri):
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    ajan.last_apply = GatewayApplyStatus(id="req-1", action="update", ok=True)
    gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)

    durum = gateways_api.rollback_gateway_update(
        gateway_code=GW, current_user=kurulumcu, db=db
    )
    assert durum.is_rollback is True
    # Hedef: guncellemeden ONCE calisan imaj — digest'e sabitlenmis.
    assert ajan.istekler[-1]["image"] == f"{REPO}:latest@{D_ESKI}"


def test_GU_11_geri_alma_LATESTE_sessizce_DUSMEZ(db, gateway, kurulumcu, ajan, kayit_defteri):
    """Geri alma isteyen operatore "bilmiyorum, en gunceli kurayim" demek
    TAM TERSINI yapmaktir."""
    for istek in ajan.istekler:
        assert not istek["image"].endswith(":latest")


def test_GU_19_geri_alma_sonrasi_imaj_referansi_KORUNUR(
    db, gateway, kurulumcu, ajan, kayit_defteri
):
    """En kritik regresyon: bir sonraki guncelleme geri almayi SESSIZCE
    geri almamali.

    Eski davranis her `update` istegine sabit `:latest` yaziyordu; geri
    alinmis bir kurulum ilk guncellemede yeniden ileri sarardi. Artik
    izlenen referansin sahibi backend'dir ve her istekte ACIKCA gonderilir.
    """
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    ajan.last_apply = GatewayApplyStatus(id="req-1", action="update", ok=True)
    gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    gateways_api.rollback_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)

    geri_alinan = ajan.istekler[-1]["image"]
    assert "@sha256:" in geri_alinan, "geri alma hedefi digest'e sabitlenmemis"
    # Ajana giden HER istek acik bir referans tasidi (compose'daki bayat
    # etikete hic guvenilmedi).
    assert all(i["image"] for i in ajan.istekler)


def test_GU_11_geri_alma_denetime_yazilir(db, gateway, kurulumcu, ajan, kayit_defteri):
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    ajan.last_apply = GatewayApplyStatus(id="req-1", action="update", ok=True)
    gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    gateways_api.rollback_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    assert _olaylar(db, "gateway_rollback_started")


def test_geri_alma_TAMAMLANINCA_durum_rolled_back(db, gateway, kurulumcu, ajan, kayit_defteri):
    """`succeeded` demek yaniltici olurdu: bu bir yukseltme degil, bilincli
    bir geri donus."""
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    ajan.last_apply = GatewayApplyStatus(id="req-1", action="update", ok=True)
    gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    gateways_api.rollback_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    ajan.last_apply = GatewayApplyStatus(id="req-2", action="update", ok=True)
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.status == "rolled_back"


# ---------------------------------------------------------------------------
# GU-14 / GU-16 — uyumluluk
# ---------------------------------------------------------------------------


def test_GU_14_eski_gateway_akilli_cihazla_UYARI_uretir(db, gateway, kurulumcu, ajan):
    _akilli_cihaz(db)
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    uyari = [u for u in durum.compatibility if u.feature == "smart_session"]
    assert uyari, "1.11.4 gateway + akilli cihaz uyari uretmedi"
    assert uyari[0].required_version == "1.12.0"
    assert uyari[0].affected_devices == 1


def test_GU_14_uyumsuzluk_cihaz_yazimini_REDDETMEZ(db, gateway, kurulumcu, ajan):
    """Urun karari: uyar, reddetme.

    Mesru akis "once cihazi yapilandir, sonra gateway'i guncelle"dir;
    reddetmek cihazi gateway surumune rehin ederdi.
    """
    from app.api import devices as devices_api

    kaynak = inspect.getsource(devices_api.update_device)
    assert "gateway_compatibility" not in kaynak, (
        "cihaz yazma yoluna uyumluluk kapisi eklenmis — uyari REDDE donusmus"
    )


def test_GU_14_akilli_cihaz_YOKSA_uyari_uretilmez(db, gateway, kurulumcu, ajan):
    """Her eski gateway'i uyarmak, gercekten etkileneni gurultude bogardi."""
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.compatibility == []


def test_GU_14_surum_bilinmiyorsa_DESTEKLENIYOR_denmez():
    assert gateway_compatibility.supports("smart_session", None) is None
    uyari = gateway_compatibility.smart_session_warning(None, 3)
    assert uyari is not None and "VARSAYILAMAZ" in uyari.message


def test_GU_16_gateway_1_14_minimumu_YUKSELTMEZ():
    """SURUM == OZELLIK VARSAYIMI YAPILMAZ.

    Gateway 1.13/1.14 cikmasi `smart_session`in minimumunu ILERI KAYDIRMAZ:
    minimum, ozelligin gercekten calismaya basladigi surumdur (1.12.0).
    Kaydirmak, calisan sahayi bir gecede "uyumsuz" gosterirdi.
    """
    assert gateway_compatibility.FEATURE_MIN_VERSION["smart_session"] == "1.12.0"
    for surum in ("1.12.0", "1.13.0", "1.14.0"):
        assert gateway_compatibility.supports("smart_session", surum) is True
    assert gateway_compatibility.supports("smart_session", "1.11.4") is False


def test_GU_16_yeni_ozellik_YENI_SATIR_ile_eklenir():
    """Matris ozellik adiyla anahtarlanir; tek bir 'minimum gateway surumu'
    skaleri YOKTUR. Aksi halde bir ozelligin minimumu digerlerini de
    yukseltirdi."""
    assert isinstance(gateway_compatibility.FEATURE_MIN_VERSION, dict)


# ---------------------------------------------------------------------------
# GU-17 / GU-18 — kararli vs gelistirme surumu
# ---------------------------------------------------------------------------


def test_GU_17_gelistirme_etiketi_GUNCELLEME_MEVCUT_diye_sunulmaz(db, gateway, kurulumcu, ajan):
    """`:main` / `:sha-*` release-image.yml'de dal push'undan uretilir.

    `:latest` yalnizca bir surum tag'iyle olusur — kararli olan odur.
    Gelistirme imajini "guncelleme mevcut" diye sunmak, sahayi test
    edilmemis bir imaja yonlendirirdi.
    """
    ajan.gateways[0].tracked_image = f"{REPO}:main"
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.channel == "development"
    assert durum.update_available is None


def test_GU_17_gelistirme_etiketi_HEDEF_olarak_reddedilir(db, gateway, kurulumcu, ajan, kayit_defteri):
    with pytest.raises(HTTPException) as exc:
        _hazirla(db, kurulumcu, hedef=f"{REPO}:sha-abc1234")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "target_not_released"


def test_GU_17_yayinlanmis_surum_etiketi_KARARLI_sayilir(db, gateway, kurulumcu, ajan):
    ajan.gateways[0].tracked_image = f"{REPO}:1.13.0"
    durum = gateways_api.get_gateway_update(gateway_code=GW, _=kurulumcu, db=db)
    assert durum.channel == "stable"


@pytest.mark.parametrize(
    "etiket,gelistirme",
    [("main", True), ("sha-abc1234", True), ("latest", False), ("1.14.0", False), ("1.13.0", False)],
)
def test_GU_18_surum_yayinlaninca_normal_hedef_olur(etiket, gelistirme):
    """1.14 YAYINLANDIGINDA hicbir kod degisikligi gerekmez: `1.14.0` bir
    surum etiketidir ve kararli sayilir. Ayrim etiketin BICIMINDE, elle
    tutulan bir listede DEGIL."""
    assert gateway_update_service.is_development_tag(etiket) is gelistirme


def test_GU_18_yeni_surum_hedeflenince_hazirlik_calisir(db, gateway, kurulumcu, ajan, kayit_defteri):
    kayit_defteri["sonuc"] = RegistryImage(version="1.14.0", digest=D_YENI)
    durum = _hazirla(db, kurulumcu, hedef=f"{REPO}:1.14.0")
    assert durum.target_version == "1.14.0"
    assert durum.target_image == f"{REPO}:1.14.0@{D_YENI}"


# ---------------------------------------------------------------------------
# GU-20 — vendor edilen sozlesme sapmasi
# ---------------------------------------------------------------------------


def test_GU_20_beyansiz_surum_sapmasi_YOK():
    """Grid'in vendor ettigi sozlesme, gerektirdigi ozelliklerin gerisinde
    kalabilir (ozellik once gateway'de cikar). Bu YASAK degil ama BEYANLI
    olmali; beyansiz her sapma burada kirmizi olur.
    """
    from pathlib import Path

    kok = Path(__file__).resolve().parents[3]
    vendored = gateway_compatibility.vendored_contract_version(kok / "infra" / "gateway-contract")
    assert vendored, "vendor edilen sozlesme bulunamadi"
    sapan = gateway_compatibility.undeclared_drift(vendored)
    assert sapan == {}, (
        f"beyansiz surum sapmasi: {sapan}. Grid, resmen desteklemedigi bir "
        "gateway surumune bagimli. Ya sozlesmeyi vendor edin ya da "
        "KNOWN_VERSION_DRIFT'e gerekcesiyle yazin."
    )


def test_GU_20_mevcut_sapma_BEYANLI(db):
    """Bugunku gercek: vendor 1.11.4, `smart_session` 1.12.0 istiyor."""
    from pathlib import Path

    kok = Path(__file__).resolve().parents[3]
    vendored = gateway_compatibility.vendored_contract_version(kok / "infra" / "gateway-contract")
    gerekli = gateway_compatibility.FEATURE_MIN_VERSION["smart_session"]
    if gateway_compatibility.supports("smart_session", vendored) is False:
        assert "smart_session" in gateway_compatibility.KNOWN_VERSION_DRIFT, (
            f"vendor {vendored} < gerekli {gerekli} ama sapma beyan edilmemis"
        )


# ---------------------------------------------------------------------------
# GU-15 — komut duzlemi
# ---------------------------------------------------------------------------


def test_GU_15_guncelleme_katmani_komut_duzlemine_DOKUNMAZ():
    """Fiziksel komut yolu (CROB/SELECT/OPERATE) bu isin kapsaminda DEGIL.

    Guncelleme servisi komut modullerini ne import eder ne de komut
    kavramlarina deger — kapsam sizmasi burada durur.
    """
    kaynak = inspect.getsource(gateway_update_service)
    # NOT: "dnp3" YASAK LISTESINDE DEGIL — urun adinin kendisi
    # (`enerjione-grid-dnp3-gateway`) ve cihaz ayar alani (`dnp3_extended`)
    # o kelimeyi tasiyor. Aranan sey KOMUT duzlemidir, protokol adi degil.
    for yasak in ("device_command", "crob", "operate_device", "/pending", "select_before"):
        assert yasak not in kaynak.lower(), f"guncelleme servisinde komut duzlemi izi: {yasak}"


def test_GU_15_ajana_yalnizca_IMAJ_parametresi_gonderilir(db, gateway, kurulumcu, ajan, kayit_defteri):
    """Serbest komut yok: backend ajana bir eylem adi + dogrulanmis skaler
    yazar. Ajan `UPDATE_PARAM_KEYS` disinda bir sey kabul etmez."""
    _hazirla(db, kurulumcu)
    gateways_api.apply_gateway_update(gateway_code=GW, current_user=kurulumcu, db=db)
    assert set(ajan.istekler[-1]) == {"code", "actor", "image"}


# ---------------------------------------------------------------------------
# Eski uc ile tutarlilik
# ---------------------------------------------------------------------------


def test_ESKI_local_update_ucu_de_durumu_gunceller(db, gateway, kurulumcu, ajan, monkeypatch):
    """Iki yazar, tek durum: eski buton kullanildiginda "Son Guncelleme"
    sessizce bayat kalmamali."""
    monkeypatch.setattr(
        gateway_agent_service, "is_installed_locally", lambda code: True
    )
    gateways_api.update_gateway_locally(gateway_code=GW, current_user=kurulumcu, db=db)
    satir = db.get(GatewayUpdate, GW)
    assert satir is not None and satir.status == "requested"
    assert satir.from_version == "1.11.4"
    # Hedef BILINMIYOR ("en guncel yayina gec") — uydurmuyoruz.
    assert satir.to_version is None
