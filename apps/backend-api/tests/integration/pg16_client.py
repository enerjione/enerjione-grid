"""PG16 istemci sarmalayicisi — YALNIZCA yerel gelistirme icin.

NEDEN VAR
---------
Uretim yigini PostgreSQL 16 + TimescaleDB 2.17.2 (`timescale/timescaledb:
2.17.2-pg16`) ve backend imajinda `postgresql-client-16` kurulu, yani
istemci ile sunucu AYNI ana surumde.

Gelistirici makinesinde ise yalnizca PostgreSQL 18 istemcisi olabilir ve
`resolve_pg_binary` her zaman EN YUKSEK surumu secer. Olculdu:

    pg16 dump -> pg16 restore -> PG16 sunucu   :  rc=0   (uretim yolu)
    pg16 dump -> pg18 restore -> PG16 sunucu   :  rc=1
        ERROR: unrecognized configuration parameter "transaction_timeout"

`pg_restore` 18, PG17+ ile gelen `transaction_timeout` GUC'unu SET etmeye
calisiyor ve PG16 sunucu bunu tanimiyor. Yani yeni bir istemci ESKI bir
sunucuya restore YAPAMIYOR.

Bu sarmalayici, testlerin uretimdekiyle AYNI istemci surumunu kullanmasini
saglar: komutlari PG16 konteynerinin icinde calistirir.

CI'DA GEREKMEZ: orada runner'a `postgresql-client-16` kurulur ve sunucu
servis olarak ayni imajdan gelir; sarmalayici devreye girmez.

YOL CEVIRISI
------------
Yedek dosyalari host'ta `E1_IT_SHARE` dizininde, konteynerde `/shared`
altinda gorunur. Sarmalayici argumanlardaki host yolunu konteyner yoluna
cevirir.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Host tarafindaki paylasim dizini (bind mount kaynagi).
SHARE_HOST = os.getenv("E1_IT_SHARE", r"C:/e1-restore-it-share")
#: Konteyner tarafindaki karsiligi.
SHARE_CONTAINER = "/shared"
#: Calisan PG16 konteynerinin adi.
CONTAINER = os.getenv("E1_IT_CONTAINER", "e1-restore-it")


def _cevir(arg: str) -> str:
    """Host yolunu konteyner yoluna cevir (yalnizca paylasim dizini)."""
    n = arg.replace("\\", "/")
    kok = SHARE_HOST.replace("\\", "/").rstrip("/")
    if n.lower().startswith(kok.lower()):
        return SHARE_CONTAINER + n[len(kok):]
    return arg


def main() -> int:
    if len(sys.argv) < 2:
        print("kullanim: pg16_client.py <arac> [args...]", file=sys.stderr)
        return 2
    arac = sys.argv[1]
    args = [_cevir(a) for a in sys.argv[2:]]
    # Sunucu konteynerin ICINDE, yani host/port argumanlari anlamsiz ve
    # yanlis olur; -h/-p ciftlerini ayikliyoruz.
    temiz: list[str] = []
    atla = False
    for a in args:
        if atla:
            atla = False
            continue
        if a in ("-h", "--host", "-p", "--port"):
            atla = True
            continue
        temiz.append(a)
    ortam = os.environ.copy()
    komut = [
        "docker", "exec",
        "-e", "PGPASSWORD=" + ortam.get("PGPASSWORD", "postgres"),
        "-e", "PGAPPNAME=" + ortam.get("PGAPPNAME", "e1_restore_session"),
        CONTAINER, arac, *temiz,
    ]
    p = subprocess.run(komut, capture_output=True, text=True)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
