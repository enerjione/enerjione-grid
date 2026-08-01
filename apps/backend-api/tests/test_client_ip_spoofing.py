"""Istemci IP'si uydurulamamali (Faz 2-13).

YASANAN ACIK
------------
`$proxy_add_x_forwarded_for` = `$http_x_forwarded_for, $remote_addr`, yani
GELEN header'in sagina ekler; SILMEZ. Saldirgan `X-Forwarded-For: 1.2.3.4`
gonderirse zincir sonunda header:

    X-Forwarded-For: 1.2.3.4, <gercek-istemci>, <gercek-istemci>
                     ^^^^^^^ saldirganin yazdigi

Backend `xff.split(",")[0]` ile TAM DA BUNU aliyordu. Bu IP uc yerde
GUVENLIK KARARI:

  * API anahtari `allowed_ips` kontrolu -> tek header ile ATLANIYORDU,
  * slowapi rate-limit anahtari -> her istekte farkli IP yazip limitten
    kacilabiliyordu,
  * denetim kaydi -> olay sonrasi inceleme YANLIS IP'yi kovaliyordu.

DOGRU KAYNAK: `X-Real-IP` (proxy kendi $remote_addr'inden yazar, istemciyi
ezer), yoksa XFF'in EN SAGDAKI degeri (son proxy'nin gordugu peer — istemci
sagdan ekleme yapamaz), yoksa TCP peer'i.
"""

from __future__ import annotations

import pytest

from app.core.client_ip import client_ip_from_request


class _SahteIstemci:
    def __init__(self, host: str | None):
        self.host = host


class _SahteIstek:
    def __init__(self, headers: dict[str, str], peer: str | None = "10.0.0.9"):
        # Starlette header'lari kucuk harfe duyarsiz; sozlugu normalize et.
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.client = _SahteIstemci(peer) if peer else None


def test_UYDURULMUS_en_soldaki_deger_KULLANILMIYOR():
    """Asil acik: saldirgan header'in soluna istedigini yaziyordu."""
    istek = _SahteIstek(
        {"X-Forwarded-For": "1.2.3.4, 203.0.113.7, 203.0.113.7"},
        peer="172.18.0.5",
    )
    assert client_ip_from_request(istek) != "1.2.3.4", (
        "en soldaki (istemcinin uydurdugu) deger kullaniliyor — API anahtari "
        "IP allowlist'i tek header ile atlanir"
    )
    assert client_ip_from_request(istek) == "203.0.113.7"


def test_X_Real_IP_ONCELIKLI():
    """Proxy'nin kendi $remote_addr'inden yazdigi deger en guvenilir olan."""
    istek = _SahteIstek(
        {
            "X-Real-IP": "203.0.113.7",
            "X-Forwarded-For": "1.2.3.4, 9.9.9.9",
        }
    )
    assert client_ip_from_request(istek) == "203.0.113.7"


def test_XFF_tek_degerken_DOGRU():
    """Tek proxy: sag ve sol ayni; davranis degismemeli."""
    istek = _SahteIstek({"X-Forwarded-For": "203.0.113.7"})
    assert client_ip_from_request(istek) == "203.0.113.7"


def test_proxy_yokken_TCP_peer():
    """Yerel dev / dogrudan erisim."""
    istek = _SahteIstek({}, peer="192.168.1.50")
    assert client_ip_from_request(istek) == "192.168.1.50"


def test_hicbir_kaynak_yoksa_None():
    assert client_ip_from_request(_SahteIstek({}, peer=None)) is None


def test_bos_parcalar_ATLANIYOR():
    """Bozuk header (`", , 203.0.113.7"`) cokme uretmemeli."""
    istek = _SahteIstek({"X-Forwarded-For": " , , 203.0.113.7 "})
    assert client_ip_from_request(istek) == "203.0.113.7"


def test_asiri_uzun_deger_KIRPILIYOR():
    """`user_sessions.ip_address` String(64); uzun header DB hatasi uretmemeli."""
    istek = _SahteIstek({"X-Real-IP": "9" * 500})
    sonuc = client_ip_from_request(istek)
    assert sonuc is not None and len(sonuc) <= 64


@pytest.mark.parametrize(
    "conf",
    ["infra/host-nginx/enerjione-grid.conf", "infra/host-nginx/solar.conf"],
)
def test_dis_nginx_XFF_i_EZIYOR(conf: str):
    """Dis kenar istemcinin gonderdigi XFF'i zincire SOKMAMALI.

    `$proxy_add_x_forwarded_for` korur ve ekler; `$remote_addr` ezer.
    """
    from pathlib import Path

    kaynak = (Path(__file__).resolve().parents[3] / conf).read_text(encoding="utf-8")
    satirlar = [
        s.strip()
        for s in kaynak.splitlines()
        if "proxy_set_header" in s and "X-Forwarded-For" in s and not s.strip().startswith("#")
    ]
    assert satirlar, f"{conf}: X-Forwarded-For ayari bulunamadi"
    for s in satirlar:
        assert "$proxy_add_x_forwarded_for" not in s, (
            f"{conf}: dis kenar istemcinin XFF'ini koruyor — saldirgan "
            "zincirin soluna istedigi IP'yi yazabilir"
        )
        assert "$remote_addr" in s, f"{conf}: XFF $remote_addr ile ezilmiyor"
