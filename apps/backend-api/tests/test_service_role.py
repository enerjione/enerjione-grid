"""Arka plan islerinin TEKILLIGI — rol + advisory lock.

NEDEN
-----
Backend sureci HTTP'nin yaninda 12 arka plan isi calistiriyor. Tek uvicorn
worker varken tekillik kendiliginden saglaniyordu. Olcek buyudukce (600
cihaz) arka plani ayri container'a almak ve API'yi coklu worker ile
calistirmak gerekiyor; ikisi de ayni tehlikeyi doguruyor: is birden fazla
surecte TEKRAR kosar.

Sonuclari sessiz ve pahali:
  * yedek iki kez alinir (pg_dump CPU + disk iki katina cikar, retention
    sayimi bozulur),
  * toplu bildirim iki kez gider,
  * retention ayni satirlari iki kez silmeye calisir,
  * IEC 104 sunucusu portu ikinci kez baglayamaz.

IKI KATMAN
----------
Rol yapilandirmasi NIYETI soyler; advisory lock gercekten tekil OLDUGUNU
garanti eder. Yalnizca role guvenmek yetmez — iki container da `all` kalirsa
is iki kez kosardi ve bunu kimse fark etmezdi.
"""

from __future__ import annotations

import pytest

from app.core import service_role
from app.core.config import settings


@pytest.fixture(autouse=True)
def _rolu_geri_al():
    onceki = settings.service_role
    yield
    settings.service_role = onceki


@pytest.fixture(autouse=True)
def _kilidi_taklit_et(monkeypatch):
    """Kilit alma/birakma taklit edilir.

    Gercek Postgres'e baglanmak testleri ortama bagimli kilar; dahasi kilit
    SESSION omurlu oldugu icin bir test kilidi birakmazsa SONRAKI testler
    "baskasi lider" deyip sessizce atlanirdi — testler yanlis nedenle yesil
    ya da kirmizi olurdu. Kilit MANTIGI ayrica asagida taklit baglanti ile
    test ediliyor.
    """
    monkeypatch.setattr(
        service_role.BackgroundLeader, "_acquire", lambda self: True, raising=True
    )
    monkeypatch.setattr(
        service_role.BackgroundLeader, "_release", lambda self: None, raising=True
    )


# ------------------------------------------------------------------- roller


def test_varsayilan_rol_ALL():
    """EN KRITIK GERIYE UYUM.

    Sahadaki kurulumlar tek container calisiyor ve `update.sh` ile
    guncelleniyor. Varsayilan `api` olsaydi guncelleme sonrasi TUM arka plan
    isi sessizce dururdu: telemetri DB'ye yazilmaz, alarm uretilmez, yedek
    alinmaz — ve hicbir hata gorunmezdi.
    """
    alan = settings.__class__.model_fields["service_role"]
    assert alan.default == service_role.ROLE_ALL


@pytest.mark.parametrize(
    "rol,arka_plan,http",
    [
        (service_role.ROLE_ALL, True, True),
        (service_role.ROLE_WORKER, True, False),
        (service_role.ROLE_API, False, True),
    ],
)
def test_rol_sorumluluklari(rol, arka_plan, http):
    settings.service_role = rol
    assert service_role.wants_background() is arka_plan
    assert service_role.serves_http() is http


@pytest.mark.parametrize("bozuk", ["", "  ", "worker2", "API-node", "hepsi"])
def test_taninmayan_rol_ALL_a_duser(bozuk):
    """Yazim hatasi arka plan islerini SESSIZCE durdurmamali.

    Guvenli taraf `all`: yanlis yazilmis bir rol yuzunden yedeklerin
    durmasindansa, isin iki yerde kosma ihtimali yeglenir — kilit zaten onu
    engelliyor.
    """
    settings.service_role = bozuk
    assert service_role.current_role() == service_role.ROLE_ALL
    assert service_role.wants_background() is True


# -------------------------------------------------------------- liderlik


def _lider_kur():
    lider = service_role.BackgroundLeader()
    kayit = {"basladi": [], "durdu": []}
    for ad in ("bir", "iki"):
        lider.register(
            ad,
            lambda ad=ad: kayit["basladi"].append(ad),
            lambda ad=ad: kayit["durdu"].append(ad),
        )
    return lider, kayit


def test_api_rolu_arka_plan_isi_ACMAZ():
    settings.service_role = service_role.ROLE_API
    lider, kayit = _lider_kur()

    assert lider.try_start() is False
    assert kayit["basladi"] == [], "api rolunde arka plan isi acilmis"
    assert lider.is_leader is False


def test_worker_rolu_isleri_SIRAYLA_acar():
    settings.service_role = service_role.ROLE_WORKER
    lider, kayit = _lider_kur()

    assert lider.try_start() is True
    assert kayit["basladi"] == ["bir", "iki"]
    assert lider.is_leader is True


def test_durdurma_TERS_sirada():
    """Baslatma sirasinin bagimliliklari olabilir; durdurma tersten olmali."""
    settings.service_role = service_role.ROLE_ALL
    lider, kayit = _lider_kur()
    lider.try_start()

    lider.stop()

    assert kayit["durdu"] == ["iki", "bir"]
    assert lider.is_leader is False


def test_bir_isin_patlamasi_DIGERLERINI_goturmez():
    """Tek bir worker'in acilmamasi tum arka plani durdurmamali."""
    settings.service_role = service_role.ROLE_ALL
    lider = service_role.BackgroundLeader()
    acilanlar = []

    def _patla():
        raise RuntimeError("bu is acilamadi")

    lider.register("bozuk", _patla, lambda: None)
    lider.register("saglam", lambda: acilanlar.append("saglam"), lambda: None)

    assert lider.try_start() is True
    assert acilanlar == ["saglam"]


def test_ikinci_try_start_isleri_TEKRAR_acmaz():
    """Idempotent: cifte startup event'i isleri iki kez baslatmamali."""
    settings.service_role = service_role.ROLE_ALL
    lider, kayit = _lider_kur()

    lider.try_start()
    lider.try_start()

    assert kayit["basladi"] == ["bir", "iki"]


def test_durum_ozeti_HEALTH_icin():
    """Kimsenin lider olmamasi sessiz bir ariza olurdu; gorunur olmali."""
    settings.service_role = service_role.ROLE_WORKER
    lider, _ = _lider_kur()
    lider.try_start()

    durum = lider.status()
    assert durum["role"] == service_role.ROLE_WORKER
    assert durum["is_leader"] is True
    assert durum["jobs"] == ["bir", "iki"]


# ------------------------------------------------------ yapisal koruma


def test_arka_plan_isleri_LIDERE_kayitli():
    """main.py'de dogrudan `X.start()` cagiran bir startup event'i kalmamali.

    Biri ileride yeni bir arka plan isini eski usulle eklerse tekillik
    korumasinin DISINDA kalir ve coklu worker'da iki kez kosar.
    """
    from pathlib import Path

    kaynak = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
        encoding="utf-8"
    )
    for is_adi in (
        "telemetry_consumer",
        "outbox_flush",
        "outbound_telemetry_batcher",
        "mqtt_publisher",
        "bulk_notification_scheduler",
        "telemetry_retention",
        "alarm_reconciliation",
        "backup_scheduler",
    ):
        assert f'leader.register("{is_adi}"' in kaynak, f"{is_adi} lidere kayitli degil"


def test_arka_plan_isi_DOGRUDAN_startup_event_i_ile_acilmiyor():
    """Eski usul kayit tekillik korumasinin DISINDA kalir.

    Bir arka plan isi `@app.on_event("startup")` icinden dogrudan
    `X.start()` ile acilirsa coklu worker'da HER worker'da kosar.
    """
    import re
    from pathlib import Path

    kaynak = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
        encoding="utf-8"
    )
    tekil_isler = (
        "telemetry_consumer",
        "outbox_flush_worker",
        "telemetry_retention",
        "alarm_reconciliation",
        "backup_scheduler",
    )
    # `@app.on_event("startup")` ile baslayan her blogu tara.
    bloklar = re.split(r'@app\.on_event\("startup"\)', kaynak)[1:]
    for blok in bloklar:
        govde = blok.split("@app.on_event")[0]
        for mod in tekil_isler:
            assert f"{mod}.start()" not in govde, (
                f"{mod}.start() dogrudan bir startup event'inde cagriliyor — "
                "tekillik korumasinin disinda kalir, coklu worker'da iki kez kosar"
            )


# ------------------------------------------------- kilit mantiginin kendisi
#
# Yukaridaki testlerde kilit taklit edilir. Burada TAM TERSI: kilidi taklit
# etmeyip `_acquire`in gercek govdesini taklit bir baglanti ile suruyoruz.


class _SahteSonuc:
    def __init__(self, deger):
        self._deger = deger

    def scalar(self):
        return self._deger


class _SahteBaglanti:
    def __init__(self, kilit_alinabilir: bool, *, destekliyor: bool = True):
        self._alinabilir = kilit_alinabilir
        self._destekliyor = destekliyor
        self.kapandi = False
        self.calisan: list[str] = []

    def execute(self, stmt, params=None):
        metin = str(stmt)
        self.calisan.append(metin)
        if not self._destekliyor:
            raise RuntimeError("advisory lock desteklenmiyor")
        return _SahteSonuc(self._alinabilir)

    def close(self):
        self.kapandi = True


def _engine_taklit(monkeypatch, baglanti):
    import app.db.session as sess

    monkeypatch.setattr(sess.engine, "connect", lambda: baglanti, raising=False)


def test_kilit_ALINAMAZSA_isler_ACILMAZ(monkeypatch, request):
    """Ikinci surec arka plan isini calistirmamali — asil garanti bu."""
    request.getfixturevalue("_rolu_geri_al")
    monkeypatch.undo()  # autouse taklidini kaldir, gercek _acquire kossun
    settings.service_role = service_role.ROLE_ALL
    baglanti = _SahteBaglanti(kilit_alinabilir=False)
    _engine_taklit(monkeypatch, baglanti)

    lider = service_role.BackgroundLeader()
    acilanlar = []
    lider.register("is", lambda: acilanlar.append("is"), lambda: None)
    monkeypatch.setattr(lider, "_schedule_retry", lambda: None)

    assert lider.try_start() is False
    assert acilanlar == [], "kilit alinamadigi halde is acilmis"
    assert baglanti.kapandi is True, "kilit alinamayinca baglanti birakilmamis"


def test_kilit_ALINIRSA_baglanti_ACIK_kalir(monkeypatch):
    """Advisory lock SESSION omurlu: baglanti kapanirsa kilit serbest kalir.

    Baglantiyi kapatsaydik kilit aninda duser ve ikinci bir surec de lider
    olurdu — koruma tamamen kagit uzerinde kalirdi.
    """
    monkeypatch.undo()
    settings.service_role = service_role.ROLE_WORKER
    baglanti = _SahteBaglanti(kilit_alinabilir=True)
    _engine_taklit(monkeypatch, baglanti)

    lider = service_role.BackgroundLeader()
    lider.register("is", lambda: None, lambda: None)

    assert lider.try_start() is True
    assert baglanti.kapandi is False, "kilidi tutan baglanti kapatilmis"

    lider.stop()
    assert baglanti.kapandi is True, "durdurmada baglanti birakilmamis"


def test_advisory_lock_DESTEKLENMEYEN_backendde_isler_calisir(monkeypatch):
    """SQLite (gelistirme/test) advisory lock bilmez.

    Kilit alinamadi diye isleri kapatsaydik gelistirme ortaminda hicbir arka
    plan isi acilmazdi.
    """
    monkeypatch.undo()
    settings.service_role = service_role.ROLE_ALL
    baglanti = _SahteBaglanti(kilit_alinabilir=False, destekliyor=False)
    _engine_taklit(monkeypatch, baglanti)

    lider = service_role.BackgroundLeader()
    acilanlar = []
    lider.register("is", lambda: acilanlar.append("is"), lambda: None)

    assert lider.try_start() is True
    assert acilanlar == ["is"]
