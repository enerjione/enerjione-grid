"""Yedek yukleme zincirinde EN DUSUK deger kazanir — hizali olmali.

YASANAN ARIZA
-------------
Uc katmanin tavanlari birbirinden habersizdi:

    ic nginx (frontend-web/nginx.conf) : 10 MB   <- kazanan
    host nginx (infra/host-nginx)      : 100 MB
    backend (_BACKUP_UPLOAD_MAX_BYTES) : 2 GiB

Backend'in streaming/413 mantigi HIC calismiyordu: istek uvicorn'a ulasmadan
nginx'in HTML 413'uyle reddediliyor, frontend JSON bekledigi icin kullaniciya
anlamsiz genel bir hata gosteriliyordu.

Felaket kurtarmanin TEK arayuz adimi buydu ve ancak gercekten kurtarma
gerektigi gun fark edilirdi.

SADECE LIMIT YETMEZ
-------------------
frontend-web container'i `read_only: true` + `tmpfs /tmp size=64m` altinda
calisiyor. nginx istek govdesini varsayilan olarak diske tamponlar; 64 MiB'lik
tmpfs'te 2 GB'lik yukleme limit dogru olsa BILE patlar. Bu yuzden
`proxy_request_buffering off` sart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
IC_NGINX = REPO / "apps" / "frontend-web" / "nginx.conf"
HOST_NGINX = REPO / "infra" / "host-nginx" / "enerjione-grid.conf"

_BIRIM = {"k": 1024, "m": 1024**2, "g": 1024**3}


def _bayta_cevir(deger: str) -> int:
    m = re.fullmatch(r"(\d+)([kmgKMG]?)", deger.strip())
    assert m, f"cozulemeyen boyut: {deger!r}"
    return int(m.group(1)) * _BIRIM.get(m.group(2).lower(), 1)


def _backend_tavani() -> int:
    from app.api.backups import _BACKUP_UPLOAD_MAX_BYTES

    return _BACKUP_UPLOAD_MAX_BYTES


def _yedek_yolu() -> str:
    """Yedek uclarinin GERCEK yolu — router prefix'inden turetilir.

    SABIT DIZE YAZILMAZ. Bu test bir donem `location ^~ /api/v1/backups/`
    ariyordu ve YESILDI; oysa router prefix'i `/admin/backups` oldugu icin
    gercek yol `/api/v1/admin/backups` idi ve nginx blogu HIC ESLESMIYORDU.
    Yani test, kusurun kendisini kilitliyordu: yukleme genel `/api/` blogunun
    10 MB limitine takiliyor, felaket kurtarma calismiyordu — ama zincir
    "dogrulanmis" sayiliyordu (denetim 2026-08-13).

    Prefix artik kaynaktan okunur; router tasinirsa test kirmizi olur.

    SONDA SLASH YOK — VE OLMAMALI
    -----------------------------
    Bu yol bir donem sonda slash ile aranıyordu ve nginx blogu da oyle
    yaziliydi. nginx, prefix'i slash ile biten ve `proxy_pass`'e giden bir
    location icin slash'siz istege KENDILIGINDEN 301 uretir; yedek listesi
    ucu (`GET /admin/backups`) tam olarak slash'siz yolda oturdugu icin
    nginx 301 -> FastAPI 307 -> ... sonsuz donguye giriliyordu ve Yedek
    Yonetimi sayfasi hic acilmiyordu. Slash'i buraya geri eklemek o hatayi
    da geri getirir; `^~` prefix'i alt yollari zaten kapsar.
    """
    from app.api.backups import router

    return f"/api/v1{router.prefix}"


def _yedek_blogu(kaynak: str) -> str:
    """Ic nginx'teki yedek `location` blogunu dondurur."""
    yol = _yedek_yolu()
    i = kaynak.find(f"location ^~ {yol}")
    assert i != -1, (
        f"ic nginx'te yedek yolu ({yol}) icin ayri bir location YOK — genel "
        "10 MB limiti uygulanir ve yukleme 413 ile duser"
    )
    derinlik = 0
    basladi = False
    for j in range(i, len(kaynak)):
        if kaynak[j] == "{":
            derinlik += 1
            basladi = True
        elif kaynak[j] == "}":
            derinlik -= 1
            if basladi and derinlik == 0:
                return kaynak[i : j + 1]
    pytest.fail("location blogu kapanmamis")


def test_dosyalar_BULUNDU():
    """Yol yanlissa asagidaki testler sessizce yesil kalirdi."""
    assert IC_NGINX.is_file(), IC_NGINX
    assert HOST_NGINX.is_file(), HOST_NGINX


def test_ic_nginx_yedek_tavani_BACKEND_ile_ayni():
    blok = _yedek_blogu(IC_NGINX.read_text(encoding="utf-8"))
    m = re.search(r"client_max_body_size\s+([0-9kmgKMG]+)\s*;", blok)
    assert m, "yedek blogunda client_max_body_size yok"
    assert _bayta_cevir(m.group(1)) >= _backend_tavani(), (
        "ic nginx tavani backend'in altinda — backend'in 413/streaming mantigi "
        "hic calismaz, kullanici anlamsiz bir hata gorur"
    )


def test_host_nginx_tavani_BACKEND_ile_ayni():
    kaynak = HOST_NGINX.read_text(encoding="utf-8")
    m = re.search(r"^\s*client_max_body_size\s+([0-9kmgKMG]+)\s*;", kaynak, re.MULTILINE)
    assert m, "host nginx'te client_max_body_size yok"
    assert _bayta_cevir(m.group(1)) >= _backend_tavani(), (
        "host nginx tavani backend'in altinda — zincirin en dusugu kazanir"
    )


def test_ic_nginx_istek_TAMPONLAMIYOR():
    """read_only container + 64 MiB tmpfs: tamponlama acikken yukleme patlar."""
    blok = _yedek_blogu(IC_NGINX.read_text(encoding="utf-8"))
    assert re.search(r"proxy_request_buffering\s+off\s*;", blok), (
        "proxy_request_buffering acik — nginx govdeyi 64 MiB'lik tmpfs'e "
        "yazmaya calisir ve buyuk yedek yuklemesi limit dogru olsa bile duser"
    )


def test_host_nginx_istek_TAMPONLAMIYOR():
    kaynak = HOST_NGINX.read_text(encoding="utf-8")
    assert re.search(r"^\s*proxy_request_buffering\s+off\s*;", kaynak, re.MULTILINE), (
        "host nginx govdeyi diske tamponluyor — 2 GiB'lik yukleme diski doldurur"
    )


def test_genel_limit_DAR_kaliyor():
    """Tavan yalnizca yedek yollarinda genis olmali.

    Global 2 GiB yapmak her ucu sinirsiz govdeye acardi; bu bir DoS yuzeyidir.
    """
    kaynak = IC_NGINX.read_text(encoding="utf-8")
    blok = _yedek_blogu(kaynak)
    disarisi = kaynak.replace(blok, "")
    m = re.search(r"client_max_body_size\s+([0-9kmgKMG]+)\s*;", disarisi)
    assert m, "genel client_max_body_size kaybolmus"
    assert _bayta_cevir(m.group(1)) <= 64 * 1024**2, (
        "genel govde limiti genisletilmis — tum uclar buyuk istege acilir"
    )
