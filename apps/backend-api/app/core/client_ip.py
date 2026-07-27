"""Client IP cozumleme — reverse proxy zinciri arkasinda gercek istemci IP'si.

Zincir: browser -> host-nginx -> frontend-web nginx -> uvicorn. nginx her
katmanda `X-Forwarded-For`a kendi `$remote_addr`ini EKLER (bkz.
`$proxy_add_x_forwarded_for`), yani header soyle gelir:

    X-Forwarded-For: <gercek-istemci>, <host-nginx>, <frontend-nginx>

Gercek istemci en SOLDAKI deger. uvicorn `--proxy-headers
--forwarded-allow-ips=*` ile baslatildigi icin (bkz. backend Dockerfile CMD)
`request.client.host` de zaten bu degere cozulur; header'i ayrica okumamiz
uvicorn proxy-headers kapali kalirsa (yerel dev, farkli bir sunucu) diye
savunma amaclidir.

`login` ve `get_current_user` ayni mantigi kullanmali — aksi halde oturum
satirinda login IP'si ile son-gorulme IP'si farkli bicimde yazilir.
"""

from __future__ import annotations

# Sutun genisligi: user_sessions.ip_address String(64). IPv6 + zone id icin
# yeterli, ama uzun/bozuk header degeri DB hatasina yol acmasin.
_MAX_IP_LEN = 64


def client_ip_from_request(request) -> str | None:
    """Istegin geldigi gercek istemci IP'sini dondur (yoksa None).

    Once `X-Forwarded-For`in en solundaki degere, yoksa `X-Real-IP`e, yoksa
    dogrudan TCP peer'ina (`request.client.host`) bakar.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:_MAX_IP_LEN]

    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip[:_MAX_IP_LEN]

    if request.client and request.client.host:
        return str(request.client.host)[:_MAX_IP_LEN]
    return None
