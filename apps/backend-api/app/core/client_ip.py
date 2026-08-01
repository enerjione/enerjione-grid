"""Client IP cozumleme — reverse proxy zinciri arkasinda gercek istemci IP'si.

Zincir: browser -> host-nginx -> frontend-web nginx -> uvicorn.

EN SOLDAKI DEGER ISTEMCININ KONTROLUNDEDIR
------------------------------------------
`$proxy_add_x_forwarded_for` = `$http_x_forwarded_for, $remote_addr`, yani
GELEN header'in SAGINA ekler; SILMEZ. Saldirgan `X-Forwarded-For: 1.2.3.4`
gonderirse zincir sonunda header sudur:

    X-Forwarded-For: 1.2.3.4, <gercek-istemci>, <gercek-istemci>
                     ^^^^^^^ saldirganin yazdigi deger

Eski kod `xff.split(",")[0]` ile TAM DA BU DEGERI aliyordu. Bu IP uc yerde
GUVENLIK KARARI:

  * API anahtari `allowed_ips` kontrolu  -> tek header ile ATLANIYORDU
  * slowapi rate-limit anahtari          -> her istekte farkli IP yazip
                                            limitten kacilabiliyordu
  * denetim kaydi (`user_sessions.ip_address`, olay kayitlari)
                                         -> olay sonrasi inceleme YANLIS
                                            IP'yi kovaliyordu

DOGRU KAYNAK
------------
1. `X-Real-IP`: her nginx katmani bunu KENDI `$remote_addr`inden yazar ve
   istemcinin gonderdigini EZER. Ic nginx'te `real_ip_recursive on` +
   `set_real_ip_from` oldugu icin oradaki `$remote_addr` zaten gercek
   istemcidir.
2. `X-Forwarded-For`in EN SAGDAKI degeri: son proxy'nin kendi gordugu peer.
   Saldirgan sagdan ekleme yapamaz — nginx her zaman en saga kendi degerini
   koyar.
3. Dogrudan TCP peer'i.

BILINEN SINIR
-------------
Ic nginx `set_real_ip_from` listesinde 10.0.0.0/8 ve 192.168.0.0/16 var; bu
araliklar "guvenilen proxy" sayilir. Ayni aralikta duran bir ISTEMCI (ornegin
appliance'in 10.42.0.0/24 WiFi AP'sindeki bir cihaz) XFF uydurabilir ve
`real_ip_recursive` onu atlayarak sola yururken saldirganin degerine
ulasabilir. Bunu kapatmak dagitim topolojisini daraltmayi gerektirir
(host nginx yalnizca 127.0.0.1 uzerinden konusuyor, docker bridge 172.16/12);
LAN'da ters proxy calistiran kurulumlari bozmamak icin simdilik burada
DURULDU. AP tarafinda asil koruma port filtresidir
(`infra/appliance/e1-ap-firewall.sh`).

`login` ve `get_current_user` ayni mantigi kullanmali — aksi halde oturum
satirinda login IP'si ile son-gorulme IP'si farkli bicimde yazilir.
"""

from __future__ import annotations

# Sutun genisligi: user_sessions.ip_address String(64). IPv6 + zone id icin
# yeterli, ama uzun/bozuk header degeri DB hatasina yol acmasin.
_MAX_IP_LEN = 64


def client_ip_from_request(request) -> str | None:
    """Istegin geldigi gercek istemci IP'sini dondur (yoksa None).

    Sira: `X-Real-IP` -> `X-Forwarded-For`in EN SAGDAKI degeri -> TCP peer.
    En SOLDAKI XFF degeri BILEREK kullanilmaz; bkz. modul aciklamasi.
    """
    # 1) X-Real-IP — proxy kendi $remote_addr'inden yazar, istemcininkini ezer.
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip[:_MAX_IP_LEN]

    # 2) XFF'in EN SAGI — son proxy'nin gordugu peer. Istemci sagdan ekleyemez.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parcalar = [p.strip() for p in xff.split(",") if p.strip()]
        if parcalar:
            return parcalar[-1][:_MAX_IP_LEN]

    # 3) Proxy yok (yerel dev / dogrudan erisim).
    if request.client and request.client.host:
        return str(request.client.host)[:_MAX_IP_LEN]
    return None
