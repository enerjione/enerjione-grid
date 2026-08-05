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
from .reporter import EventReporter

log = logging.getLogger("ftp-server")


def _make_handler(reporter: EventReporter) -> type[FTPHandler]:
    """Olaylari bildiren FTPHandler alt sinifi.

    Callback'ler YALNIZCA kuyruga yazar (bkz. reporter). pyftpdlib tek
    thread'de calistigi icin burada yapilacak her bloklayici is TUM sunucuyu
    durdurur.

    Her callback `try/except` ile sarili: bildirim bir yan istir, ondan cikan
    hata cihazin dosya transferini DUSURMEMELI.
    """

    class ReportingHandler(FTPHandler):
        def on_login(self, username):  # noqa: ANN001, D102
            try:
                reporter.report(
                    "login", username=username, remote_ip=self.remote_ip
                )
            except Exception:  # noqa: BLE001
                log.debug("on_login bildirimi basarisiz", exc_info=True)

        def on_file_received(self, file):  # noqa: ANN001
            """Cihaz -> sunucu. Config cekme akisinin tamamlandigi an."""
            try:
                reporter.report(
                    "upload",
                    path=_rel(file),
                    filename=os.path.basename(file),
                    size=_size(file),
                    remote_ip=self.remote_ip,
                    username=self.username,
                )
            except Exception:  # noqa: BLE001
                log.debug("on_file_received bildirimi basarisiz", exc_info=True)

        def on_file_sent(self, file):  # noqa: ANN001
            """Sunucu -> cihaz. Firmware/config indirmesi."""
            try:
                reporter.report(
                    "download",
                    path=_rel(file),
                    filename=os.path.basename(file),
                    size=_size(file),
                    remote_ip=self.remote_ip,
                    username=self.username,
                )
            except Exception:  # noqa: BLE001
                log.debug("on_file_sent bildirimi basarisiz", exc_info=True)

        def on_incomplete_file_received(self, file):  # noqa: ANN001
            """YARIM dosya. SESSIZ GECILMEZ: yarim bir config dosyasi cihaz
            tarafindan okunursa ne olacagi belirsizdir; ayrica 'cihaz gonderdi
            ama biz gormedik' teshisinin en olasi sebebi budur."""
            try:
                reporter.report(
                    "upload_incomplete",
                    path=_rel(file),
                    filename=os.path.basename(file),
                    size=_size(file),
                    remote_ip=self.remote_ip,
                    username=self.username,
                )
            except Exception:  # noqa: BLE001
                log.debug("on_incomplete bildirimi basarisiz", exc_info=True)

    return ReportingHandler


#: backend-api'nin calistigi uid/gid (bkz. apps/backend-api/Dockerfile).
_BACKEND_GID = 10001


def _share_root_with_backend(root: str) -> None:
    """FTP kok dizinini backend'in de YAZABILECEGI hale getirir.

    `ftp-data` volume'u iki servis tarafindan paylasilir. Bu servis root
    olarak kosar (port 21 <1024 bind etmesi gerekiyor), dolayisiyla volume
    `root:root 0755` olusur. backend-api ise uid 10001 ile kosar ve o modda
    yalnizca OKUYABILIR.

    Bunu duzeltmeden birakmak, tam olarak harita karolarinda yasadigimiz
    arizayi uretirdi: dosya indirilir/uretilir, yazma `PermissionError` ile
    duser, ust katman bunu "ag hatasi" sanip yanlis yone isaret eder.
    Belirti ortaya cikana kadar hicbir sey bozuk GORUNMEZ.

    Cozum: grubu backend'in gid'ine cevir ve gruba yazma ver (0770). Dunyaya
    acmak (0777) gereksiz genis olurdu — iki servis disinda kimse erismiyor.

    Root DEGILSEK sessizce gecilir: bu durumda volume'u zaten baska biri
    hazirlamistir ve zorlamak anlamsiz bir hata uretirdi.
    """
    try:
        os.chown(root, -1, _BACKEND_GID)  # sahibi degistirme, yalnizca grup
        os.chmod(root, 0o770)
        log.info("FTP kok dizini backend ile paylasildi (gid=%s, 0770).", _BACKEND_GID)
    except PermissionError:
        log.info("FTP kok dizini izinleri degistirilemedi (root degiliz) — atlaniyor.")
    except OSError as exc:  # noqa: BLE001
        log.warning("FTP kok dizini izinleri ayarlanamadi: %s", exc)


def _rel(path: str) -> str:
    """Kok dizine gore yol — mutlak host yolu disariya sizmasin."""
    try:
        return os.path.relpath(path, SETTINGS.ftp_root).replace(os.sep, "/")
    except ValueError:  # farkli surucu (Windows) vb.
        return os.path.basename(path)


def _size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _build_server() -> FTPServer:
    s = SETTINGS

    if not s.ftp_password:
        log.error("FTP_PASSWORD bos — sunucu baslatilmiyor (guvenlik).")
        raise SystemExit(2)

    os.makedirs(s.ftp_root, exist_ok=True)
    _share_root_with_backend(s.ftp_root)

    authorizer = DummyAuthorizer()
    # perm: e=girme, l=liste, r=oku indir, a=ekle, d=sil, f=yeniden adlandir,
    #       m=dizin olustur, w=yaz yukle, M=chmod, T=zaman degistir
    #
    # CIHAZ hesabi tam yetkili olmak ZORUNDA: kendi SN alt dizinini yaratir
    # (m), config/debug dosyalarini yazar (w/a) ve firmware indirir (r).
    authorizer.add_user(
        s.ftp_user,
        s.ftp_password,
        homedir=s.ftp_root,
        perm="elradfmwMT",
    )

    # TEK HESAP (bilincli karar, 2026-08-05). Kisa sureligine ikinci bir
    # salt-okunur hesap eklenmisti; kaldirildi. Sebep: cihazin FTP ekranina
    # elle girilen tek bir kimlik var ve ikinci hesap sahada karisiklik
    # uretiyor — hangi hesabin nereye yazdigi belirsizlesiyor. Kimlik bilgisi
    # artik arayuzden yonetiliyor, dolayisiyla "guvenli ikinci hesap" ihtiyaci
    # yerine TEK ve DEGISTIRILEBILIR bir kimlik tercih edildi.

    reporter = EventReporter(
        backend_url=s.backend_url, service_token=s.internal_service_token
    )
    # ALT SINIF kullaniliyor, `FTPHandler` sinifina dogrudan atama DEGIL:
    # eskiden `handler = FTPHandler` yazip sinif ozniteliklerini degistiriyorduk;
    # bu, ayni surecte ikinci bir sunucu kurulursa ayarlari paylasilan global
    # bir duruma yazmak demekti. Alt sinif her kurulumu yalitir.
    handler = _make_handler(reporter)
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
