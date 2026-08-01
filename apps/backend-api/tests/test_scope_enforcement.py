"""Kapsam SORGUDA uygulanmali, yanitta degil (denetim A3) + token sizintisi (A4).

A3 — YASANAN ACIK
-----------------
`/alarms/events/ack-all` ve `/reset-all` mutasyonu TUM alarmlara uyguluyordu;
`scope_service` filtresi yalnizca DONEN LISTEYE vuruluyordu.

Somut senaryo: hicbir sorumluluk alanina atanmamis bir operator (gorunur cihaz
kumesi BOS) `ack-all` cagirir. Sistemdeki tum acik alarmlar onaylanir,
reset=true olanlar yorumlariyla birlikte KALICI SILINIR. Yanit bos dondugu
icin arayuzde hicbir sey olmamis gorunur. Ardindan `reset-all` ile tum acik
alarmlar resetlenir; `fault_recompute` sonrasi haritadaki GERCEK arizalar
kaybolur ve geriye olay kaydinda tek satir kalir.

A4 — YASANAN ACIK
-----------------
`GET /gateways` yaniti gateway token'ini DUZ METIN tasiyordu ve liste OPERATOR
rolune aciktir. Token `POST /telemetry/gateway/{code}` icin TEK kimlik
unsurudur: operator token'i alip kendi alani disindaki cihazlar icin uydurma
telemetri gonderebiliyordu — sahte kritik ariza uretmek ya da
`fault_indicator`i normal gondererek GERCEK arizayi maskelemek. Ayni token ile
`/gateways/{code}/config` cagrilip sahadaki tum cihazlarin IP/DNP3 adresleri
de sizabiliyordu.
"""

from __future__ import annotations

import inspect

import pytest

from app.schemas.gateway import GatewayRead
from app.services import alarm_engine_service


# ------------------------------------------------------------------ A3


def test_toplu_islemler_KAPSAM_parametresi_aliyor():
    for fn in (
        alarm_engine_service.acknowledge_all_alarms,
        alarm_engine_service.reset_all_alarms,
    ):
        imza = inspect.signature(fn)
        assert "visible_device_ids" in imza.parameters, (
            f"{fn.__name__} kapsam parametresi almiyor — mutasyon tum alarmlara "
            "uygulanir ve filtre yalnizca yanitta kalir"
        )


def test_toplu_islemler_kapsami_LISTELEMEYE_geciriyor():
    """Parametreyi almak yetmez; sorguya AKTARILMALI."""
    for fn in (
        alarm_engine_service.acknowledge_all_alarms,
        alarm_engine_service.reset_all_alarms,
    ):
        kaynak = inspect.getsource(fn)
        assert "list_alarm_events(db, visible_device_ids)" in kaynak, (
            f"{fn.__name__} kapsami sorguya gecirmiyor"
        )


def test_BOS_kume_KISITSIZ_sayilmaz():
    """En kritik ayrim — davranis olarak dogrulanir.

    Hicbir sorumluluk alanina atanmamis operator icin gorunur kume BOS'tur.
    Yaygin hata `if not visible:` yazmaktir: bos kume yanlislikla "kisitsiz"
    sayilir ve tam da yetkisiz kullanici TUM alarmlari gorur/degistirir.
    Bos kume ile None AYRI seylerdir.
    """
    kayit: list[str] = []

    class _SahteDB:
        def scalars(self, _stmt):
            class _R:
                def all(self_inner):
                    return []

            return _R()

    import app.services.alarm_engine_service as svc

    orijinal = svc.select
    svc.select = lambda *a, **k: _SahteSorgu(kayit)
    try:
        svc.list_alarm_events(_SahteDB(), set())
    finally:
        svc.select = orijinal

    assert _kapsam_kosulu_var(kayit), (
        "BOS kume icin sorgu daraltilmadi — yetkisiz kullanici tum alarmlara "
        f"erisir (uygulanan kosullar: {kayit})"
    )


class _SahteSorgu:
    """`select(...).where(...).order_by(...).limit(...)` zincirini kaydeder.

    Kosul METNI kaydediliyor, yalnizca "where cagrildi" bilgisi degil.
    Onceden `where` sayisi sayiliyordu; `list_alarm_events` aktif ve cozulmus
    alarmlar icin AYRI sorgu kurmaya baslayinca sayi degisti ve test, davranis
    dogru oldugu halde kirmizi oldu. Sayi kirilgan bir olcu — asil soru
    "kapsam kosulu uygulandi mi".
    """

    def __init__(self, kayit: list[str]) -> None:
        self._kayit = kayit

    def where(self, *a, **k):
        self._kayit.append(str(a[0]) if a else "where")
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self


def _kapsam_kosulu_var(kayit: list[str]) -> bool:
    """Kayitlarda cihaz kapsamina dair bir kosul var mi?"""
    return any("device_id" in k for k in kayit)


def test_kapsam_verilince_WHERE_uygulanir(monkeypatch):
    """Davranis testi: kapsam verildiginde sorguya gercekten where ekleniyor."""
    kayit: list[str] = []
    monkeypatch.setattr(
        alarm_engine_service, "select", lambda *a, **k: _SahteSorgu(kayit)
    )

    class _SahteDB:
        def scalars(self, _stmt):
            class _R:
                def all(self_inner):
                    return []

            return _R()

    alarm_engine_service.list_alarm_events(_SahteDB(), {1, 2})
    assert _kapsam_kosulu_var(kayit), (
        f"kapsam verildi ama sorgu daraltilmadi (kosullar: {kayit})"
    )

    kayit.clear()
    alarm_engine_service.list_alarm_events(_SahteDB(), None)
    assert not _kapsam_kosulu_var(kayit), (
        f"kisitsiz cagride cihaz kapsami eklendi (kosullar: {kayit})"
    )


# ------------------------------------------------------------------ A4


def test_gateway_semasi_token_DONDURMEZ():
    """Testin ozu: duz metin token liste yanitinda olmamali."""
    assert "token" not in GatewayRead.model_fields, (
        "GatewayRead token'i duz metin donuyor — operator telemetri enjekte "
        "edebilir"
    )


def test_gateway_semasi_token_VARLIGINI_bildirir():
    """Arayuzun ihtiyaci 'kurulu mu' bilgisi; degerin kendisi degil."""
    assert "has_token" in GatewayRead.model_fields


@pytest.mark.parametrize(
    "token,token_hash,beklenen",
    [
        ("abc", None, True),
        (None, "hash", True),
        ("", "", False),
        (None, None, False),
        ("   ", None, False),
    ],
)
def test_has_token_dogru_hesaplanir(token, token_hash, beklenen):
    from app.models.gateway import Gateway

    gw = Gateway(code="GW", name="gw", token=token, token_hash=token_hash)
    assert gw.has_token is beklenen


def test_token_ucu_YALNIZCA_installer():
    """Ayri uc acildi ama yetkisi genisse hicbir sey kazanilmamis olurdu."""
    from app.api.gateways import get_gateway_token

    kaynak = inspect.getsource(get_gateway_token)
    assert "UserRole.INSTALLER" in kaynak
    for rol in ("OPERATOR", "ENGINEER"):
        assert f"UserRole.{rol}" not in kaynak, f"{rol} token ucuna erisebiliyor"


def test_token_okumasi_DENETIM_kaydina_yazilir():
    """Duz metin bir sirrin kimin ne zaman gordugu iz birakmadan gecmemeli."""
    from app.api.gateways import get_gateway_token

    kaynak = inspect.getsource(get_gateway_token)
    assert "record_event" in kaynak
    assert "gateway_token_viewed" in kaynak
