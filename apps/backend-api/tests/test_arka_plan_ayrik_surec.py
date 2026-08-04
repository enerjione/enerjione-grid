"""Tuketiciyi API surecinden AYIRMA — davranis testleri.

NE OLCULUYOR
------------
Telemetri tuketicisi ve outbox yayincisi uvicorn sureci ICINDE thread olarak
kosuyordu; HTTP kabulu ucuz, isleme pahali oldugu icin ayni cekirdek doluyor
ve kuyruk sessizce birikiyordu (olcum: gateway 1135 msg/sn, backend ~480
msg/sn). Cozum arka plani ayri bir surece almak. Bu dosya o ayirmanin UC
kirilma noktasini davranis duzeyinde kilitler:

1. LIDERLIK ERKEN TALEBI — IEC 104 sunuculari TCP portu baglar ve
   `start_background_jobs`tan ONCE acilmak zorunda (asyncio loop'u orada).
   Eskiden yalnizca role bakiliyordu; rol `all` iken bu HER uvicorn
   worker'inda True'dur, yani `--workers 4` dort surecte de bind denerdi.

2. AYIRMANIN OLCULEBILIRLIGI — telemetri sayaclari SUREC ICIDIR. Tuketici
   baska surece tasindiginda `/system-status/telemetry-pipeline` API
   surecinde kalici SAHTE "critical" gosterir ve `backlog` (ayirmanin ise
   yarayip yaramadigini soyleyen tek sayi) tamamen kaybolur.

3. MIGRATION YARISI — backend-api ve backend-worker AYNI imaji, AYNI komutu
   (`migrate_db && uvicorn`) kullanir ve es zamanli acilir. Kilitsiz iki
   `upgrade head` ayni revizyonu oynatip DuplicateColumn ile patlar.

Testler KAYNAK METNI ARAMAZ; fonksiyonlari gercekten cagirir. Bu depoda
kaynak-grep eden testler davranis bozukken yesil kalmisti.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.api import system_status as ss
from app.core import service_role
from app.core.config import settings


# ==========================================================================
# 1) LIDERLIK: erken talep (claim) + is baslatma (try_start)
# ==========================================================================


class _KilitSunucusu:
    """Postgres advisory lock semantigi: AYNI ANDA tek sahip.

    Gercek Postgres'e baglanmak testi ortama bagimli kilardi; dahasi kilit
    SESSION omurlu oldugu icin birakilmayan bir kilit sonraki testleri
    yanlis nedenle yesil/kirmizi yapardi.
    """

    def __init__(self) -> None:
        self.sahip: object | None = None

    def dene(self, oturum: object) -> bool:
        if self.sahip is None:
            self.sahip = oturum
            return True
        return self.sahip is oturum

    def birak(self, oturum: object) -> None:
        if self.sahip is oturum:
            self.sahip = None


class _SahteSonuc:
    def __init__(self, deger):
        self._deger = deger

    def scalar(self):
        return self._deger


class _SahteOturum:
    """Bir `engine.connect()` cagrisinin dondurdugu baglanti = bir oturum."""

    def __init__(self, sunucu: _KilitSunucusu) -> None:
        self._sunucu = sunucu
        self.kapandi = False

    def execute(self, stmt, params=None):
        metin = str(stmt)
        if "pg_try_advisory_lock" in metin:
            return _SahteSonuc(self._sunucu.dene(self))
        if "pg_advisory_unlock" in metin:
            self._sunucu.birak(self)
            return _SahteSonuc(True)
        return _SahteSonuc(None)

    def close(self):
        self._sunucu.birak(self)
        self.kapandi = True


@pytest.fixture
def kilit_sunucusu(monkeypatch):
    """Tum `engine.connect()` cagrilarini TEK kilit sunucusuna baglar."""
    import app.db.session as sess

    sunucu = _KilitSunucusu()
    monkeypatch.setattr(sess.engine, "connect", lambda: _SahteOturum(sunucu), raising=False)
    return sunucu


@pytest.fixture(autouse=True)
def _rolu_geri_al():
    onceki = settings.service_role
    yield
    settings.service_role = onceki


def _lider_kur(ad: str):
    lider = service_role.BackgroundLeader()
    acilan: list[str] = []
    lider.register(ad, lambda: acilan.append(ad), lambda: None)
    return lider, acilan


def test_claim_KILIDI_alir_ama_isleri_ACMAZ(kilit_sunucusu):
    """IEC 104, isler baslamadan once acilmak zorunda — ama tek surecte.

    `claim()` yalnizca liderligi talep eder; arka plan islerini baslatmak
    `try_start`in isidir. Ikisini karistirmak, IEC 104'un asyncio loop'u
    olmayan bir thread'de acilmasi demek olurdu.
    """
    settings.service_role = service_role.ROLE_ALL
    lider, acilan = _lider_kur("is")

    assert lider.claim() is True
    assert acilan == [], "claim() arka plan isini baslatmis"
    assert lider.is_leader is False


def test_IKINCI_surec_claim_alamaz_PORT_cakismasi_olmaz(kilit_sunucusu):
    """Asil regresyon: `--workers 4` ile dort surec de 2404'e bind denerdi.

    Eski kod yalnizca `wants_background()`e bakiyordu ve o bayrak rol `all`
    iken HER surecte True. Ucu EADDRINUSE alip sessizce duserdi; kazanan
    surec rastgeleydi ve LIDER OLMAYABILIRDI — o zaman SCADA istemcisi acik
    ama SESSIZ bir sunucuya baglanirdi.
    """
    settings.service_role = service_role.ROLE_ALL
    birinci, _ = _lider_kur("bir")
    ikinci, _ = _lider_kur("iki")

    assert birinci.claim() is True
    assert ikinci.claim() is False, "iki surec de IEC104 portunu baglamaya kalkiyor"


def test_claim_ETTIKTEN_sonra_try_start_isleri_BASLATIR(kilit_sunucusu):
    """En sinsi tuzak: kendi kilidimize takilmak.

    `claim()` kilidi bir oturumda tutuyorken `try_start` IKINCI bir oturumdan
    `pg_try_advisory_lock` cagirirsa KENDI kilidimiz yuzunden False doner.
    Sonuc: IEC 104 acilan surecte telemetri tuketicisi, outbox yayincisi,
    retention ve yedekleme HIC baslamaz — ve hicbir hata gorunmez.
    """
    settings.service_role = service_role.ROLE_ALL
    lider, acilan = _lider_kur("is")

    assert lider.claim() is True
    assert lider.try_start() is True, "kilidi zaten tutan surec kendi kilidine takildi"
    assert acilan == ["is"]
    assert lider.is_leader is True


def test_claim_eden_surec_DURUNCA_kilit_serbest_kalir(kilit_sunucusu):
    """Worker yeniden baslatildiginda devralma calismali."""
    settings.service_role = service_role.ROLE_ALL
    birinci, _ = _lider_kur("bir")
    ikinci, _ = _lider_kur("iki")

    assert birinci.claim() is True
    assert ikinci.claim() is False

    birinci.stop()

    assert ikinci.claim() is True, "onceki lider durdugu halde kilit serbest kalmamis"


def test_API_rolu_claim_ETMEZ(kilit_sunucusu):
    """`api` rolundeki surec IEC 104 acmamali — arka plan onun isi degil."""
    settings.service_role = service_role.ROLE_API
    lider, _ = _lider_kur("is")

    assert lider.claim() is False
    assert kilit_sunucusu.sahip is None, "api rolu kilidi tutuyor — worker lider olamaz"


@pytest.fixture
def _tekil_lideri_geri_al():
    """Modul duzeyindeki `leader` tekil nesnesini testler arasi sizdirma.

    IEC 104 deploy bayragi da sifirlanir: `main._iec104_deployed` surec
    omurlu, yani onceki bir testte acilan sunucular sonrakinde "zaten acik"
    goruntusu verip testi bedavadan yesil yapardi.
    """
    import app.main as m

    tekil = service_role.leader
    onceki_conn, onceki_started = tekil._conn, tekil._started
    m._iec104_deployed = False
    m._iec104_loop = None
    yield tekil
    tekil._conn, tekil._started = onceki_conn, onceki_started
    m._iec104_deployed = False
    m._iec104_loop = None


def test_IEC104_baska_surec_LIDERKEN_acilmaz(monkeypatch, kilit_sunucusu, _tekil_lideri_geri_al):
    """`main.start_iec104_servers` gercekten liderlige bagli mi?

    Bu, yukaridaki `claim()` testlerinden AYRI bir sey olculuyor: kapinin
    dogru calismasi degil, main.py'nin o kapiyi KULLANMASI. Eski kod
    `wants_background()` cagiriyordu ve rol `all` iken o HER surecte True;
    yani dort uvicorn worker'i da `deploy_all_active_targets` cagirirdi.
    """
    import asyncio

    import app.main as m

    settings.service_role = service_role.ROLE_ALL

    cagrilar = []

    async def _sahte_deploy(db, loop=None):
        cagrilar.append(True)
        return 1

    monkeypatch.setattr(m, "deploy_all_active_targets", _sahte_deploy)
    monkeypatch.setattr(m, "SessionLocal", lambda: type("_S", (), {"close": lambda self: None})())

    # BASKA bir surec (backend-worker) liderligi ONCE almis olsun.
    rakip = service_role.BackgroundLeader()
    assert rakip.claim() is True

    asyncio.run(m.start_iec104_servers())

    assert cagrilar == [], (
        "lider olmayan surec IEC 104 sunucularini acmaya kalkti — ayni netns "
        "icinde 2404'e ikinci bind EADDRINUSE ile duser ve kazanan surec "
        "rastgele olur"
    )


def test_IEC104_LIDER_surecte_acilir(monkeypatch, kilit_sunucusu, _tekil_lideri_geri_al):
    """Kapiyi kapatirken IEC 104'u tamamen kapatmis olmayalim."""
    import asyncio

    import app.main as m

    settings.service_role = service_role.ROLE_ALL

    cagrilar = []

    async def _sahte_deploy(db, loop=None):
        cagrilar.append(True)
        return 1

    monkeypatch.setattr(m, "deploy_all_active_targets", _sahte_deploy)
    monkeypatch.setattr(m, "SessionLocal", lambda: type("_S", (), {"close": lambda self: None})())

    asyncio.run(m.start_iec104_servers())

    assert cagrilar == [True], "tek surecte bile IEC 104 acilmadi"


def test_liderligi_SONRADAN_devralan_surec_IEC104_ACAR(
    monkeypatch, kilit_sunucusu, _tekil_lideri_geri_al
):
    """Kilit acilista MESGULSE vazgecmek yetmez — devralinca acilmali.

    SAHA SENARYOSU (update.sh): eski container SIGKILL edilir; Postgres onun
    advisory lock oturumunu TCP keepalive suresi boyunca (varsayilan saatler)
    acik tutar. Yeni surec acilirken `claim()` False doner ve IEC 104
    ATLANIR. Saniyeler sonra eski oturum dusunce `try_start`in 15 sn'lik
    yeniden denemesi kilidi ALIR: surec LIDER olur, telemetri/outbox/retention
    calisir, `/health` "is_leader: true" der.

    Ama 2404 HIC baglanmamistir. SCADA istemcisi baglanamaz; her ariza-gecis
    bayragi, her kalite degisimi kontrol merkezine ULASMAZ ve hicbir hata
    gorunmez. Ayni sey `--workers N`de IEC 104'u acan worker oldugunde de
    olur.

    Test gercek zamanlamayi taklit eder: loop CANLI kalir ve `try_start`
    (uretimde `threading.Timer` daemon thread'inden gelir) ayri bir
    thread'den cagrilir.
    """
    import asyncio

    import app.main as m

    settings.service_role = service_role.ROLE_ALL

    cagrilar = []

    async def _sahte_deploy(db, loop=None):
        cagrilar.append(True)
        return 1

    monkeypatch.setattr(m, "deploy_all_active_targets", _sahte_deploy)
    monkeypatch.setattr(
        m, "SessionLocal", lambda: type("_S", (), {"close": lambda self: None})()
    )
    # Tekil lider TUM isleri kayitli tasiyor; testte yalnizca IEC 104 isini
    # kosturuyoruz (aksi halde gercek tuketici/yedekleme thread'leri acilirdi).
    iec_isi = [j for j in service_role.leader._jobs if j[0] == "iec104_servers"]
    assert iec_isi, (
        "IEC 104 liderlik islerine KAYITLI DEGIL — devralan surecte sunucular "
        "hic acilmaz"
    )
    monkeypatch.setattr(service_role.leader, "_jobs", iec_isi)

    async def _senaryo():
        # 1) Eski surecin (olmus container) oturumu kilidi hala tutuyor.
        eski = service_role.BackgroundLeader()
        assert eski.claim() is True

        # 2) Yeni surec acilir: IEC 104 hakli olarak atlanir.
        await m.start_iec104_servers()
        assert cagrilar == [], "lider olmayan surec 2404'e bind denedi"

        # 3) Eski oturum duser; liderlik yeniden denemede devralinir.
        eski.stop()
        await asyncio.get_running_loop().run_in_executor(
            None, service_role.leader.try_start
        )
        assert service_role.leader.is_leader is True

        # 4) Devralma sonrasi sunucular acilmis olmali.
        for _ in range(50):
            if cagrilar:
                break
            await asyncio.sleep(0.01)

    asyncio.run(_senaryo())

    assert cagrilar == [True], (
        "surec LIDER oldu ama IEC 104 sunuculari HIC acilmadi — SCADA'ya "
        "giden butun ariza gecisleri sessizce kaybolur"
    )


def test_IEC104_IKI_KEZ_acilmiyor(monkeypatch, kilit_sunucusu, _tekil_lideri_geri_al):
    """Hizli yol ile devralma yolu ust uste binmemeli.

    `manager.deploy` ayni target icin ONCE undeploy yapar; ikinci bir deploy
    bagli SCADA oturumunu bosuna koparir (ve o sirada gonderilen degisimler
    kaybolur).
    """
    import asyncio

    import app.main as m

    settings.service_role = service_role.ROLE_ALL

    cagrilar = []

    async def _sahte_deploy(db, loop=None):
        cagrilar.append(True)
        return 1

    monkeypatch.setattr(m, "deploy_all_active_targets", _sahte_deploy)
    monkeypatch.setattr(
        m, "SessionLocal", lambda: type("_S", (), {"close": lambda self: None})()
    )
    iec_isi = [j for j in service_role.leader._jobs if j[0] == "iec104_servers"]
    monkeypatch.setattr(service_role.leader, "_jobs", iec_isi)

    async def _senaryo():
        # Hizli yol: kilit bos, sunucular acilir.
        await m.start_iec104_servers()
        assert cagrilar == [True]
        # Ardindan isler baslar — ayni surecte ikinci bir deploy OLMAMALI.
        await asyncio.get_running_loop().run_in_executor(
            None, service_role.leader.try_start
        )
        for _ in range(20):
            await asyncio.sleep(0.01)

    asyncio.run(_senaryo())

    assert cagrilar == [True], f"IEC 104 {len(cagrilar)} kez deploy edildi"


# --------------------------------------------------- acilis yolunda yayin YOK


class _SahteDdlSonuc:
    """Bootstrap DDL'inde sonuc yalnizca `.first()` ile tuketiliyor.

    Adi bilerek farkli: modulun basindaki `_SahteSonuc` advisory lock
    sorgulari icin ve `.scalar()` tasiyor; ayni adi kullanmak onu GOLGELER
    ve liderlik testlerini sessizce bozardi.
    """

    def first(self):
        return None


class _SahteDdlBaglanti:
    def execution_options(self, **_kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _stmt, _params=None):
        return _SahteDdlSonuc()


class _SahteDdlEngine:
    """Bootstrap DDL'ini yutan sahte engine.

    Ad `_SahteEngine` DEGIL: bu dosyanin sonunda migration testi icin ayni
    adda baska bir sinif var ve modul duzeyinde SONRAKI tanim oncekini ezer.
    Cakisma sessiz olurdu — yalnizca birinin ihtiyac duydugu metot eksik
    kalirdi.
    """

    def connect(self):
        return _SahteDdlBaglanti()

    def begin(self):
        return _SahteDdlBaglanti()


def test_ACILIS_yolunda_outbox_YAYINI_yapilmiyor(monkeypatch):
    """Acilis bootstrap'i her surecte kosar; oradan yayin yapilamaz.

    `_legacy_bootstrap_ddl` (uvicorn startup event'i) `leader` ya da
    `service_role` korumasinin ARKASINDA DEGIL — `--workers 4` ile dort
    surecte de bastan sona kosar. Burada bir `flush_outbox(db)` cagrisi
    oldugu surece dort surec acilista ayni satirlari yayinlamaya kalkar.

    Bugun `FOR UPDATE SKIP LOCKED` cift yayini engelliyor, ama o TEK basina
    kalan bir koruma olmamali: kilit yalnizca ayni anda kosan yayincilari
    ayirir; sahiplenmenin bir gun gevsemesi (ya da baska bir broker yolu)
    durumunda korumasiz cagri sessizce cift yayina doner. Yayin TEK yerden,
    `leader.register("outbox_flush", ...)` arkasindaki worker'dan yapilir ve
    o en gec OUTBOX_FLUSH_INTERVAL_SEC (0,3 sn) icinde ayni isi gorur.

    Test kaynak metni ARAMAZ: bootstrap gercekten kosturulur ve yayin
    yolundaki `flush_outbox` casuslanir.
    """
    import app.main as m
    from app.services import outbox_service

    yayinlar: list[object] = []
    monkeypatch.setattr(
        outbox_service, "flush_outbox", lambda db, **kw: yayinlar.append(db)
    )
    # `main` modulu ismi kendi ad alanina almis olabilir; ikisini de kapat.
    if hasattr(m, "flush_outbox"):
        monkeypatch.setattr(
            m, "flush_outbox", lambda db, **kw: yayinlar.append(db), raising=False
        )

    monkeypatch.setattr(m, "engine", _SahteDdlEngine())
    monkeypatch.setattr(m.Base.metadata, "create_all", lambda **_kw: None)
    monkeypatch.setattr(
        m, "SessionLocal", lambda: type("_S", (), {"close": lambda self: None})()
    )

    tohumlandi: list[bool] = []

    def _sahte_seed(db, **_kw):
        tohumlandi.append(True)
        return {"skipped": True}

    monkeypatch.setattr(m, "seed_default_signals", _sahte_seed)

    m._legacy_bootstrap_ddl()

    # Once VAKUM KONTROLU: bootstrap gercekten SONUNA kadar kosmus olmali.
    # Aksi halde erken patlayan bir bootstrap bu testi bedavadan yesil yapar.
    assert tohumlandi == [True], (
        "bootstrap sonuna ulasmadi — asagidaki iddia hicbir sey kanitlamaz"
    )
    assert yayinlar == [], (
        "acilis yolundan outbox yayini yapiliyor — bu kod her uvicorn "
        "worker'inda kosar, yani N surec ayni satirlari yayinlamaya kalkar"
    )


# ==========================================================================
# 2) OLCUM: telemetri boru hatti ucu ayrildiktan sonra YALAN SOYLEMEMELI
# ==========================================================================


class _SahteIstek:
    def __init__(self, **basliklar):
        self.headers = {k.lower(): v for k, v in basliklar.items()}


_WORKER_RAPORU = {
    "running": True,
    "connected": True,
    "backlog": 12_345,
    "throughput_msgs_per_sec": 480.0,
    "last_batch_size": 500,
    "last_batch_duration_sec": 0.25,
    "last_fetch_at": None,
    "processed_total": 1_000_000,
    "bad_total": 3,
    "reconnects": 1,
    "last_error": None,
    "backlog_warn_threshold": 50_000,
    "severity": "ok",
}


def _yerel_sayaclari_sifirla(monkeypatch):
    """Surec-ici tuketici sayaclari: hicbir sey calismiyor (api rolu gercegi)."""
    from app.services import telemetry_consumer as tc

    monkeypatch.setattr(
        tc,
        "get_stats",
        lambda: {
            "running": False,
            "connected": False,
            "backlog": None,
            "throughput_msgs_per_sec": 0.0,
            "last_batch_size": 0,
            "last_batch_duration_sec": 0.0,
            "last_fetch_at": None,
            "processed_total": 0,
            "bad_total": 0,
            "reconnects": 0,
            "last_error": None,
        },
    )


def test_api_rolunde_BACKLOG_arka_plan_surecinden_gelir(monkeypatch):
    """Ayirmanin bedeli, ayirmayi olcen gostergeyi kaybetmek OLMAMALI.

    `backlog` "tuketici yetisiyor mu" sorusunun TEK cevabi. Surec-ici
    sayaclara bakan uc, tuketici baska surece tasinir tasinmaz kalici
    `running=False` + `backlog=None` dondururdu.
    """
    settings.service_role = service_role.ROLE_API
    _yerel_sayaclari_sifirla(monkeypatch)
    gonderilen = {}

    def _sahte_cek(credentials):
        gonderilen.update(credentials)
        return dict(_WORKER_RAPORU), None

    monkeypatch.setattr(ss, "_fetch_worker_pipeline", _sahte_cek)

    rapor = ss.get_telemetry_pipeline_status(
        request=_SahteIstek(Authorization="Bearer jwt-123"), _=None
    )

    assert rapor.backlog == 12_345, "backlog arka plan surecinden okunmadi"
    assert rapor.running is True
    assert rapor.throughput_msgs_per_sec == 480.0
    assert rapor.severity == "ok"
    assert gonderilen.get("authorization") == "Bearer jwt-123"


def test_COOKIE_oturumu_da_iletilir(monkeypatch):
    """Bu depoda oturum HttpOnly COOKIE ile tasiniyor.

    `deps.get_current_user` oncelik sirasi cookie > header; tarayicidan gelen
    isteklerin cogunda `Authorization` basligi HIC YOKTUR. Yalnizca
    Authorization iletseydik worker her seferinde 401 doner ve Sistem Durumu
    kalici olarak "arka plan surecine ulasilamiyor" derdi — gercek bir
    arizadan ayirt EDILEMEYEN yanlis-pozitif.
    """
    settings.service_role = service_role.ROLE_API
    _yerel_sayaclari_sifirla(monkeypatch)
    gonderilen = {}

    def _sahte_cek(credentials):
        gonderilen.update(credentials)
        return dict(_WORKER_RAPORU), None

    monkeypatch.setattr(ss, "_fetch_worker_pipeline", _sahte_cek)

    ss.get_telemetry_pipeline_status(
        request=_SahteIstek(Cookie="e1_access=jwt-abc"), _=None
    )

    assert gonderilen.get("cookie") == "e1_access=jwt-abc", (
        "oturum cookie'si arka plan surecine iletilmedi — worker 401 doner"
    )


def test_worker_ULASILAMAZSA_critical_ve_SEBEP_yazili(monkeypatch):
    """Ulasilamiyorsa "critical" DOGRUDUR; ama sebebi okunabilir olmali.

    Sahte yesil gostermek bu urunde en agir hata sinifi: nobetci operator
    "her sey yolunda" gorup telemetrinin akmadigini fark etmez.
    """
    settings.service_role = service_role.ROLE_API
    _yerel_sayaclari_sifirla(monkeypatch)
    monkeypatch.setattr(
        ss,
        "_fetch_worker_pipeline",
        lambda credentials: (None, "Arka plan surecine ulasilamiyor (backend-worker:8000)"),
    )

    rapor = ss.get_telemetry_pipeline_status(request=_SahteIstek(), _=None)

    assert rapor.severity == "critical"
    assert rapor.backlog is None
    assert "ulasilamiyor" in (rapor.last_error or ""), (
        "operator sebebi goremiyor — 'kimlik reddedildi' ile 'worker kapali' "
        "ayni kirmiziya dusuyor"
    )


def test_yetki_reddi_ULASILAMIYORDAN_ayirt_edilir(monkeypatch):
    """401 ile "worker kapali" farkli arizalardir, farkli yerde aranir.

    401 tek bir yapilandirma hatasidir (SECRET_KEY ayrismasi); "kapali" ise
    telemetrinin gercekten islenmedigi anlamina gelir.
    """
    settings.service_role = service_role.ROLE_API
    _yerel_sayaclari_sifirla(monkeypatch)

    import urllib.error

    def _401(url, timeout=None):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _401)

    rapor, hata = ss._fetch_worker_pipeline({"cookie": "x"})

    assert rapor is None
    assert "SECRET_KEY" in (hata or ""), f"401 sebebi anlasilmiyor: {hata!r}"


def test_ALL_rolu_YEREL_sayaclari_kullanir_geriye_uyum(monkeypatch):
    """Tek container kurulumu (varsayilan) HIC degismemeli.

    Guncelleme alan sahalarin cogu tek container. Bu yolda hicbir HTTP
    cagrisi yapilmamali; yapilirsa her Sistem Durumu yenilemesi bosuna
    1 saniyelik timeout yerdi.
    """
    from app.services import telemetry_consumer as tc

    settings.service_role = service_role.ROLE_ALL
    monkeypatch.setattr(
        ss,
        "_fetch_worker_pipeline",
        lambda authorization: pytest.fail("tek container kurulumunda proxy'e gidildi"),
    )
    monkeypatch.setattr(
        tc,
        "get_stats",
        lambda: {
            "running": True,
            "connected": True,
            "backlog": 7,
            "throughput_msgs_per_sec": 42.0,
            "last_batch_size": 10,
            "last_batch_duration_sec": 0.1,
            "last_fetch_at": None,
            "processed_total": 100,
            "bad_total": 0,
            "reconnects": 0,
            "last_error": None,
        },
    )

    rapor = ss.get_telemetry_pipeline_status(request=_SahteIstek(), _=None)

    assert rapor.backlog == 7
    assert rapor.severity == "ok"


def test_IC_PROBE_basligi_zinciri_KIRAR(monkeypatch):
    """`BACKEND_WORKER_HOST` kendini gosterirse sonsuz proxy zinciri olusurdu.

    Her istek bir yenisini dogurur; tek bir Sistem Durumu yenilemesi
    surecleri doldurabilir. Ikinci sicrama bu bayrakla engellenir.
    """
    settings.service_role = service_role.ROLE_API
    _yerel_sayaclari_sifirla(monkeypatch)
    monkeypatch.setattr(
        ss,
        "_fetch_worker_pipeline",
        lambda authorization: pytest.fail("ic sorgu tekrar proxy'lendi — zincir kirilmadi"),
    )

    rapor = ss.get_telemetry_pipeline_status(
        request=_SahteIstek(**{"X-E1-Internal-Probe": "1"}), _=None
    )

    # Yerel sayaclar bos oldugu icin critical; onemli olan ZINCIRIN kirilmasi.
    assert rapor.severity == "critical"


# ------------------------------------------------- servis listesi gorunurlugu


def _probe_stub(monkeypatch):
    """Gercek TCP/DB probe'lari devre disi — burada olculen sey LISTE."""
    from app.api.system_status import ServiceStatus

    def _sahte(ad, **kw):
        return ServiceStatus(name=ad, role="worker", healthy=True, latency_ms=0.0, endpoint="x")

    monkeypatch.setattr(ss, "_check_worker", _sahte)
    for ad, isim in (("_check_database", "db"), ("_check_rabbitmq", "mq"), ("_check_nats", "nats")):
        monkeypatch.setattr(
            ss,
            ad,
            lambda isim=isim: ServiceStatus(
                name=isim, role="db", healthy=True, latency_ms=0.0, endpoint="x"
            ),
        )


def test_ayrik_kurulumda_ARKA_PLAN_SURECI_listede(monkeypatch):
    """Worker olurse Sistem Durumu bunu GORMELI.

    Bugun gormuyor: backend-worker probe listesinde hic yok, yani arka plan
    sureci tamamen olu iken sayfa her seyi YESIL gosterir.
    """
    settings.service_role = service_role.ROLE_API
    _probe_stub(monkeypatch)

    rapor = ss.get_services_status(_=None)
    adlar = [s.name for s in rapor.services]

    assert "Arka Plan Sureci" in adlar, "ayrik kurulumda backend-worker izlenmiyor"


def test_tek_container_kurulumda_ARKA_PLAN_SURECI_listede_DEGIL(monkeypatch):
    """Rol `all` ise ayri bir worker YOK; kosulsuz eklemek kalici kirmizi verirdi.

    Kalici kirmizi bir satir gercek arizayi golgeler — "zaten hep kirmizi".
    """
    settings.service_role = service_role.ROLE_ALL
    _probe_stub(monkeypatch)

    rapor = ss.get_services_status(_=None)
    adlar = [s.name for s in rapor.services]

    assert "Arka Plan Sureci" not in adlar


# ==========================================================================
# 3) MIGRATION YARISI: iki surec ayni anda `upgrade head` kosmamali
# ==========================================================================


class _SahteMigrationBaglantisi:
    def __init__(self, kilit: threading.Lock) -> None:
        self._kilit = kilit
        self._tutuyor = False

    def execution_options(self, **_kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        if self._tutuyor:
            self._kilit.release()
            self._tutuyor = False
        return False

    def execute(self, stmt, params=None):
        metin = str(stmt)
        if "pg_advisory_lock" in metin:
            self._kilit.acquire()
            self._tutuyor = True
        elif "pg_advisory_unlock" in metin:
            if self._tutuyor:
                self._kilit.release()
                self._tutuyor = False
        return None


class _SahteDialect:
    name = "postgresql"


class _SahteEngine:
    dialect = _SahteDialect()

    def __init__(self) -> None:
        self._kilit = threading.Lock()

    def connect(self):
        return _SahteMigrationBaglantisi(self._kilit)


def test_es_zamanli_migration_CAKISMAZ(monkeypatch):
    """backend-api ve backend-worker ayni anda `upgrade head` kosmamali.

    Ikisi de AYNI imaji ve AYNI komutu kullanir, ikisi de `postgres healthy`
    kosuluyla es zamanli acilir. Kilitsiz iki upgrade ayni revizyonu iki kez
    oynatir:

        psycopg2.errors.DuplicateColumn: column ... already exists

    Container cikar, `restart: unless-stopped` ile donguye girer.
    """
    from scripts import migrate_db as md

    monkeypatch.setattr(md, "engine", _SahteEngine())

    ic_erideki = 0
    en_yuksek = 0
    sayac_kilidi = threading.Lock()

    def _sahte_migrate():
        nonlocal ic_erideki, en_yuksek
        with sayac_kilidi:
            ic_erideki += 1
            en_yuksek = max(en_yuksek, ic_erideki)
        time.sleep(0.05)
        with sayac_kilidi:
            ic_erideki -= 1

    monkeypatch.setattr(md, "_migrate_locked", _sahte_migrate)

    threadler = [threading.Thread(target=md.migrate) for _ in range(2)]
    for t in threadler:
        t.start()
    for t in threadler:
        t.join(timeout=10)

    assert en_yuksek == 1, (
        f"{en_yuksek} surec ayni anda `alembic upgrade head` kosturdu — "
        "ayni revizyon iki kez oynatilir ve DuplicateColumn ile patlar"
    )


def test_sqlite_gibi_backendde_migration_KILITSIZ_kosar(monkeypatch):
    """Advisory lock desteklemeyen backend'de migration DURMAMALI.

    Gelistirme/test ortaminda es zamanli ikinci surec zaten yok; kilit
    yokluguna takilip semayi kurmamak butun testleri goturur.
    """
    from scripts import migrate_db as md

    class _SqliteDialect:
        name = "sqlite"

    class _SqliteEngine:
        dialect = _SqliteDialect()

        def connect(self):  # pragma: no cover — cagrilmamali
            raise AssertionError("sqlite'ta advisory lock baglantisi acilmis")

    monkeypatch.setattr(md, "engine", _SqliteEngine())
    kosuldu = []
    monkeypatch.setattr(md, "_migrate_locked", lambda: kosuldu.append(1))

    md.migrate()

    assert kosuldu == [1], "kilitsiz backend'de migration hic kosmadi"
