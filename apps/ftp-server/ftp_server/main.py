"""EnerjiOne gomulu FTP sunucusu.

Horstmann SN2 cihazlari config/firmware dosyalarini bu sunucuya yazar/okur
(cihaz FTP ekrani: Server/Port 21/User/Pass/Dir orn /SN20/FOTA/). Bizim yazilim
FTP kok dizinini (volume) okuyup config'i UI'da duzenler.

Deneme surumu: tek ortak kullanici (SETTINGS.ftp_user/ftp_password), tam yetki
(oku/yaz/liste/sil/dizin). Kok dizin FTP_ROOT (docker volume). Cihaz kendi SN
alt dizinini olusturur.

pyftpdlib kullanir (saf-python, C bagimliligi yok). Non-blocking asyncore
tabanli; ayri thread'te HTTP /health.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from .config import SETTINGS
from .health import start_health_server

log = logging.getLogger("ftp-server")


def _build_server() -> FTPServer:
    s = SETTINGS

    if not s.ftp_password:
        log.error("FTP_PASSWORD bos — sunucu baslatilmiyor (guvenlik).")
        raise SystemExit(2)

    os.makedirs(s.ftp_root, exist_ok=True)

    authorizer = DummyAuthorizer()
    # perm: e=girme, l=liste, r=oku indir, a=ekle, d=sil, f=yeniden adlandir,
    #       m=dizin olustur, w=yaz yukle, M=chmod, T=zaman degistir
    authorizer.add_user(
        s.ftp_user,
        s.ftp_password,
        homedir=s.ftp_root,
        perm="elradfmwMT",
    )

    handler = FTPHandler
    handler.authorizer = authorizer
    handler.banner = "EnerjiOne FTP ready."
    # Pasif mod veri portu araligi — docker-compose'ta host'a map edilmeli.
    handler.passive_ports = range(s.pasv_min_port, s.pasv_max_port + 1)
    if s.masquerade_address:
        # NAT arkasindaysa cihaza gorunen dis IP.
        handler.masquerade_address = s.masquerade_address

    server = FTPServer((s.listen_host, s.listen_port), handler)
    # Es zamanli baglanti sinirlari (kaynak korumasi).
    server.max_cons = 256
    server.max_cons_per_ip = 16
    return server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    s = SETTINGS
    server = _build_server()

    start_health_server(
        host=s.health_host,
        port=s.health_port,
        snapshot=lambda: {
            "status": "ok",
            "service": "ftp-server",
            "ftp_port": s.listen_port,
            "ftp_root": s.ftp_root,
            "connections": len(getattr(server, "ip_map", []) or []),
        },
    )

    def _shutdown(signum, _frame):
        log.info("Sinyal %s — kapatiliyor.", signum)
        try:
            server.close_all()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info(
        "FTP sunucusu %s:%s dinliyor (root=%s, pasv=%s-%s).",
        s.listen_host, s.listen_port, s.ftp_root, s.pasv_min_port, s.pasv_max_port,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
