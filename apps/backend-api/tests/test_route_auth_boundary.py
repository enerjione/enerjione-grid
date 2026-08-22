"""HICBIR uc yanlislikla halka acik kalmamali (Faz 3-18).

DENETIMIN TEKRARLANAN BULGUSU
-----------------------------
"38 test dosyasi, ama yalnizca 3'u TestClient kullaniyor. 35 router'in
cogunda endpoint testi yok." Ve: "bu 63 bulgunun HICBIRI mevcut testlerle
yakalanmiyordu."

Router basina tam kapsam buyuk bir is; ama en ucuz ve en genis koruma
YETKI SINIRIDIR: her ucun kimlik dogrulamasi olmadan REDDETTIGINI
dogrulamak. Bu tek test:

  * yeni bir router'in `Depends(get_current_user)` unutularak eklenmesini,
  * bir bagimliligin yanlis baglanmasini (500 uretir, 401 degil),
  * ve halka acik uclarin listesinin sessizce buyumesini

yakalar. Kapsam testi degil, SINIR testidir — ama tam da denetimde bulunan
siniflardan biri (A2/A3: kapinin bir yolda uygulanip digerinde unutulmasi)
buraya duser.

NEDEN TestClient DEGIL HAM ASGI
-------------------------------
`starlette.testclient` httpx gerektiriyor ve bu proje httpx'e bagli degil
(bkz. `test_license_gate.py` ayni gerekce). Ham ASGI hem bagimlilik
eklemiyor hem de test edilen sozlesmenin kendisi.

VERITABANI GEREKMEZ
-------------------
Kimlik dogrulama bagimliligi, handler DB'ye dokunmadan ONCE 401 firlatir.
`SessionLocal()` cagrilsa bile SQLAlchemy baglantiyi ilk sorguya kadar
acmaz.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.routing import APIRoute

from app.core.config import settings
from app.main import app

PREFIX = settings.api_prefix

# --- BILEREK HALKA ACIK UCLAR ----------------------------------------------
#
# Bu bir BEYAZ LISTEDIR ve METODA DUYARLIDIR: anahtar "METOD /tam/yol".
# Yol, rotanin tam KALIBIdir ({param} yer tutuculariyla) — somut deger degil.
#
# Metoda duyarli olmasi onemli: `/project-settings` GET halka acik ama PUT
# INSTALLER istiyor. Yol bazli bir beyaz liste PUT'u da kontrol disi
# birakirdi ve yetki gerilemesi sessizce gecerdi.
#
# Buraya bir sey eklemek BILINCLI bir karardir; her madde gerekcesini tasir.
ACIK_UCLAR: dict[str, str] = {
    # Kimlik alma yollari — token buradan uretilir.
    "POST /api/v1/auth/login": "giris; token bu uctan alinir",
    "POST /api/v1/auth/setup-password": "davet linki, tek kullanimlik bilet ile",
    # NOT: `auth/refresh` diye bir uc YOK. Ilk yazimda varsayilmisti ve
    # `test_beyaz_liste_GERCEK_rotalarla_ortusuyor` bunu yakaladi — testin
    # varlik sebebi tam olarak bu: olu bir girdi, ileride ayni yol
    # eklendiginde onu SESSIZCE acik yapardi.

    # Healthcheck — container/orkestrator kimlik dogrulayamaz.
    "GET /api/v1/health": "container healthcheck; kimlik dogrulamasi olamaz",
    "GET /api/v1/health/live": "liveness probe; kimlik dogrulamasi olamaz",
    "GET /api/v1/health/ready": "readiness probe; kimlik dogrulamasi olamaz",

    # Giris ekrani markalama — sema yorumunda da yazili: private alan YOK
    # (proje/musteri adi, logo, favicon, batarya esikleri).
    "GET /api/v1/project-settings": "login ekrani ve header auth'suz okur; private alan yok",

    # GATEWAY KIMLIGI — JWT degil `X-Gateway-Token` ile dogrulanir.
    # `validate_gateway_token` bu uclarin ICINDE cagriliyor; kimlik
    # dogrulamasiz istek 404/422 dondurur, 401 degil.
    "GET /api/v1/gateways/{gateway_code}/config": "gateway token'i ile dogrulanir",
    "GET /api/v1/gateways/{gateway_code}/pending": "gateway token'i ile dogrulanir",
    "POST /api/v1/gateways/{gateway_code}/command-results": "gateway token'i ile dogrulanir",
    # F3C teslim bildirimi. YENI BIR AUTH YUZEYI ACMAZ: `command-results` ile
    # AYNI `X-Gateway-Token` dogrulamasindan gecer ve ayrica komutun gercekten
    # o gateway'e ait oldugu kontrol edilir (IDOR koruma). Teslim jetonu tek
    # basina yetki vermez; gateway kimligi olmadan istek handler'a giremez.
    "POST /api/v1/gateways/{gateway_code}/command-delivery-acks": (
        "gateway token'i ile dogrulanir"
    ),
    # Cihaz basina calisma-zamani sagligi (`device_health_v1`, gateway 1.15.0+).
    # `command-results` ile AYNI `X-Gateway-Token` dogrulamasindan gecer;
    # komut duzlemi sirrini (`X-Gateway-Command-Token`) BILEREK ISTEMEZ —
    # saglik telemetrisi komut yetkisi gerektirmez ve o sirri buraya yaymak
    # F5A'da ayrilan iki duzlemi yeniden birlestirirdi.
    "POST /api/v1/gateways/{gateway_code}/device-health": (
        "gateway token'i ile dogrulanir"
    ),
    "POST /api/v1/telemetry/gateway/{gateway_code}": "gateway token'i ile dogrulanir",
}

# Bu on ekler altindaki HER yol acik kabul edilir.
ACIK_ONEKLER: tuple[tuple[str, str], ...] = (
    (f"{PREFIX}/public/", "halka acik salt-okunur goruntuleme (ayri router)"),
    (f"{PREFIX}/ingest/", "gateway token'i ile dogrulanir (JWT degil)"),
    (f"{PREFIX}/internal/", "servisler arasi token (INTERNAL_SERVICE_TOKEN)"),
)


def _acik_mi(method: str, kalip: str) -> bool:
    """`kalip` rotanin TAM yolu ({param} yer tutuculariyla)."""
    if f"{method} {kalip}" in ACIK_UCLAR:
        return True
    return any(kalip.startswith(o) for o, _ in ACIK_ONEKLER)


def _somut_yol(route: APIRoute) -> str:
    """Rotanin GERCEK mount yolu, path parametreleri doldurulmus halde.

    DIKKAT: `route.path` router'a GORELIDIR (`/public/alarms`), uygulamadaki
    gercek yol degil (`/api/v1/public/alarms`). Goreli yola bakan bir kontrol
    "acik uc" beyaz listesiyle HIC eslesmez ve tum public uclar yanlislikla
    "korunmali" sayilirdi.

    `app.url_path_for` prefix'i de kapsayan tam yolu verir.
    """
    parametreler = {ad: "1" for ad in route.param_convertors}
    try:
        return str(app.url_path_for(route.name, **parametreler))
    except Exception:  # noqa: BLE001
        # Isimsiz/cakisan rota: prefix'i elle ekleyip parametreleri doldur.
        yol = route.path
        for ad in parametreler:
            yol = yol.replace(f"{{{ad}}}", "1")
        while "{" in yol and "}" in yol:
            bas = yol.index("{")
            son = yol.index("}", bas)
            yol = yol[:bas] + "1" + yol[son + 1 :]
        return yol if yol.startswith(PREFIX) else PREFIX + yol


def _istek(method: str, yol: str) -> int | None:
    """Uygulamayi tek istek icin surer; HTTP durum kodunu doner."""
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

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
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    return next((m["status"] for m in sent if m["type"] == "http.response.start"), None)


def _tum_api_rotalari() -> list[APIRoute]:
    """Rotalari OZYINELEMELI topla.

    DIKKAT: bu FastAPI surumunde `app.include_router(...)` rotalari
    duzlestirmiyor; `app.routes` icinde `_IncludedRouter` sarmalayicilari
    duruyor. Yalnizca ust seviyeye bakan bir kesif SIFIR rota bulur ve
    parametrize edilmis testler SESSIZCE hic kosmaz — "test var" sanip
    korumasiz kalmanin ta kendisi. `test_rotalar_BULUNDU` bu yuzden var.
    """
    bulunan: list[APIRoute] = []
    yigin = list(app.routes)
    gorulen: set[int] = set()
    while yigin:
        r = yigin.pop()
        if id(r) in gorulen:
            continue
        gorulen.add(id(r))
        if isinstance(r, APIRoute):
            bulunan.append(r)
            continue
        # FastAPI 0.140: `_IncludedRouter` sarmalayicisi `.routes` DEGIL
        # `.original_router` tasiyor. Yalnizca `.routes` arayan bir kesif
        # sessizce bos doner.
        icerik = getattr(r, "original_router", None)
        if icerik is not None:
            yigin.extend(getattr(icerik, "routes", []) or [])
            continue
        alt = getattr(r, "routes", None)
        if alt:
            yigin.extend(alt)
    return bulunan


def _korunmasi_gereken_rotalar() -> list[tuple[str, str]]:
    cikti: list[tuple[str, str]] = []
    for route in _tum_api_rotalari():
        kalip = PREFIX + route.path
        somut = _somut_yol(route)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            if _acik_mi(method, kalip):
                continue
            cikti.append((method, somut))
    return cikti


ROTALAR = _korunmasi_gereken_rotalar()


def test_rotalar_BULUNDU():
    """Kesif bozulursa testler sessizce yesil kalirdi — en tehlikeli hal."""
    assert len(ROTALAR) > 50, (
        f"yalnizca {len(ROTALAR)} korunmasi gereken rota bulundu; rota kesfi "
        "bozulmus olabilir"
    )


@pytest.mark.parametrize("method,yol", ROTALAR, ids=lambda v: str(v))
def test_uc_kimlik_dogrulamasiz_REDDEDIYOR(method: str, yol: str):
    """Asil sinir: token olmadan hicbir sey donmemeli.

    401/403 bekleniyor. 200 => uc yanlislikla HALKA ACIK.
    500 => bagimlilik zinciri bozuk (yanlis `Depends`, import hatasi).
    """
    durum = _istek(method, yol)

    assert durum is not None, f"{method} {yol}: yanit uretilmedi"
    assert durum != 500, (
        f"{method} {yol}: 500 dondu — bagimlilik zinciri bozuk olabilir "
        "(kimlik dogrulamasi devreye girmeden handler calisti)"
    )
    assert durum in (401, 403), (
        f"{method} {yol}: kimlik dogrulamasiz {durum} dondu. Beklenen 401/403. "
        "Uc yanlislikla halka acik kalmis olabilir; bilincli ise "
        "ACIK_UCLAR/ACIK_ONEKLER listesine GEREKCESIYLE eklenmeli."
    )


def test_acik_uc_listesi_GEREKCELI():
    """Beyaz listeye gerekcesiz ekleme yapilmasin.

    Gerekce zorunlulugu, listeyi "testi yesil yapmak icin" buyutmeyi
    zorlastirir — asil koruma budur.
    """
    for anahtar, gerekce in ACIK_UCLAR.items():
        assert " " in anahtar, f"{anahtar}: 'METOD /yol' bicimi bekleniyor"
        assert gerekce and len(gerekce) > 10, f"{anahtar}: gerekce yetersiz"
    for onek, gerekce in ACIK_ONEKLER:
        assert gerekce and len(gerekce) > 10, f"{onek}: gerekce yetersiz"


def test_beyaz_liste_GERCEK_rotalarla_ortusuyor():
    """Silinmis bir ucu listede tutmak, ileride ayni yolu ACIK acmayi
    kolaylastirir (kimse listeyi tekrar sorgulamaz)."""
    kayitli = {
        f"{m} {PREFIX}{r.path}"
        for r in _tum_api_rotalari()
        for m in (r.methods - {"HEAD", "OPTIONS"})
    }
    olu = [a for a in ACIK_UCLAR if a not in kayitli]
    assert not olu, f"beyaz listede artik var olmayan uclar: {olu}"
