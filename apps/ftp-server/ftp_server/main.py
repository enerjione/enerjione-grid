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
from .credentials import CredentialPoller
from .health import start_health_server
from .reporter import EventReporter
from . import watchdog

log = logging.getLogger("ftp-server")

#: ioloop'un atissiz kalabilecegi en uzun sure. Kalp 5 sn'de bir attigi icin
#: bu 12 kacirilmis atis demek — gecici bir yavaslama restart uretmez.
BEKCI_ESIK_SN = 60.0
#: Kalp atis araligi.
BEKCI_ATIS_SN = 5.0
#: ioloop'un multiplex cagrisina (select/epoll) verilen zaman asimi.
#:
#: `None` OLMAMALI. pyftpdlib `IOLoop.loop()` fonksiyonu timeout'suz
#: cagrilirsa ILK yinelemede `poll(None)` yapar; bu cagri BIR SOKET OLAYI
#: GELENE KADAR SURESIZ BLOKLAR ve zamanlayici (`sched_poll`) o satirin
#: ARDINDAN geldigi icin, hicbir istemci baglanmadikca zamanlanmis kalp
#: atisi HIC calismaz. Acik zaman asimi verilince `loop()` dogrudan
#: "poll(timeout) -> sched_poll()" dalina girer ve dongu istemci olmasa
#: da doner.
#:
#: Bu deger ISTEMCI/OTURUM zaman asimi DEGILDIR (onlar FTPHandler.timeout
#: ve DTPHandler.timeout); yalnizca olay dongusunun uyanma araligidir.
IOLOOP_POLL_TIMEOUT_SN = 1.0


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
            # Dosya (ve cihazin actigi yeni dizin zinciri) root olustu;
            # backend'in de yazabilmesi icin grubu hemen duzelt.
            _grant_backend_access(file)
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
            _grant_backend_access(file)
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
    """FTP agacinin TAMAMINI backend'in de YAZABILECEGI hale getirir.

    `ftp-data` volume'u iki servis tarafindan paylasilir. Bu servis root
    olarak kosar (port 21 <1024 bind etmesi gerekiyor), dolayisiyla volume
    ve FTP uzerinden olusan HER dizin/dosya `root:root` olusur. backend-api
    ise uid 10001 ile kosar ve o modda yalnizca OKUYABILIR.

    NEDEN OZYINELI (2026-08-05'te sahada yasandi): onceleri yalnizca KOK
    dizin paylasialiyordu. Cihazin (ya da bizim) actigi alt dizinler —
    /SN20/FOTA/ dahil — root:root 0755 kaldi ve "Cihaza uygula" backend'de
    PermissionError ile dustu; arayuzde yalnizca "gonderilemedi" gorundu.
    Belirti ortaya cikana kadar hicbir sey bozuk GORUNMEZ — harita
    karolarindaki arizanin birebir aynisi.

    Cozum: grubu backend'in gid'ine cevir; dizinlere 0770, dosyalara 0660.
    Dunyaya acmak (0777) gereksiz genis olurdu. Calisma zamaninda olusan
    yeni dosyalar icin bkz. _grant_backend_access (upload callback'i).

    Root DEGILSEK sessizce gecilir: bu durumda volume'u zaten baska biri
    hazirlamistir ve zorlamak anlamsiz bir hata uretirdi.
    """
    try:
        os.chown(root, -1, _BACKEND_GID)  # sahibi degistirme, yalnizca grup
        os.chmod(root, 0o770)
        for dirpath, dirnames, filenames in os.walk(root):
            for ad in dirnames:
                yol = os.path.join(dirpath, ad)
                os.chown(yol, -1, _BACKEND_GID)
                os.chmod(yol, 0o770)
            for ad in filenames:
                yol = os.path.join(dirpath, ad)
                os.chown(yol, -1, _BACKEND_GID)
                os.chmod(yol, 0o660)
        log.info(
            "FTP agaci backend ile paylasildi (gid=%s, dizin 0770 / dosya 0660).",
            _BACKEND_GID,
        )
    except PermissionError:
        log.info("FTP izinleri degistirilemedi (root degiliz) — atlaniyor.")
    except OSError as exc:  # noqa: BLE001
        log.warning("FTP izinleri ayarlanamadi: %s", exc)


def _grant_backend_access(path: str) -> None:
    """Yeni olusan dosyayi ve ust dizin zincirini backend'le paylasir.

    Cihaz FTP uzerinden dosya yazdiginda (ve MKD ile dizin actiginda) hepsi
    root:root olusur; backend o dizine sonradan config YAZAMAZ. Upload
    callback'inden cagrilir: dosya + kok dizine kadar ust zincir duzeltilir,
    boylece acilistaki ozyineli duzeltmeden SONRA olusan yollar da acik
    kalir. Yalnizca ucuz syscall'lar — pyftpdlib'in tek thread'ini bloklamaz.
    """
    try:
        kok = os.path.realpath(SETTINGS.ftp_root)
        hedef = os.path.realpath(path)
        if os.path.isfile(hedef):
            os.chown(hedef, -1, _BACKEND_GID)
            os.chmod(hedef, 0o660)
        dizin = os.path.dirname(hedef)
        while dizin.startswith(kok):
            os.chown(dizin, -1, _BACKEND_GID)
            os.chmod(dizin, 0o770)
            if dizin == kok:
                break
            dizin = os.path.dirname(dizin)
    except OSError:
        # Root degilsek ya da yol kayboldu: yan is, asil transferi etkilemez.
        log.debug("izin duzeltilemedi: %s", path, exc_info=True)


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


def _make_authorizer(
    user: str, password: str, root: str, *, eski_kullanici: str | None = None
) -> DummyAuthorizer:
    """Tek hesapli Authorizer kurar.

    perm: e=girme, l=liste, r=oku indir, a=ekle, d=sil, f=yeniden adlandir,
          m=dizin olustur, w=yaz yukle, M=chmod, T=zaman degistir

    CIHAZ hesabi kok icinde tam yetkili olmak ZORUNDA: kendi SN alt dizinini
    yaratir (m), config/debug dosyalarini yazar (w/a) ve firmware indirir (r).
    Hesap `homedir` HAPSINDEDIR (pyftpdlib ust dizine cikisa izin vermez) —
    yani "root olmayan, kendi dizinine kisitli" sart burada saglanir: erisim
    yalnizca paylasilan ftp-data volume'u ile sinirlidir.

    `eski_kullanici`: kimlik degisiminde AKTIF oturumlar hala eski adla
    `has_perm` sorar; adi tablodan tamamen silmek o oturumlari KeyError ile
    dusururdu. Eski ad, TAHMIN EDILEMEZ bir parolayla tabloda tutulur — yeni
    login ALAMAZ ama acik oturum dosya islemini bitirebilir.
    """
    import secrets

    authorizer = DummyAuthorizer()
    authorizer.add_user(user, password, homedir=root, perm="elradfmwMT")
    if eski_kullanici and eski_kullanici != user:
        authorizer.add_user(
            eski_kullanici, secrets.token_urlsafe(32), homedir=root, perm="elradfmwMT"
        )
    return authorizer


#: Smart Navigator 2.0'in FTP ekranindaki standart dizin. Cihazlar config/
#: firmware icin bu yola bakar; acilista otomatik olusturulur ki cihaz ilk
#: baglantida "550 dizin yok" ile donmesin.
STANDARD_DEVICE_DIR = "SN20/FOTA"


def _initial_credentials(poller: CredentialPoller) -> tuple[str, str, str | None]:
    """Acilis kimligi: once backend (arayuzden yonetilen), yoksa env.

    Backend parolasiz yanit da verebilir (henuz ayarlanmamis) — o durumda
    kimlik env'den gelir ama MASQUERADE backend'den alinir: adres kaydedilmis
    olabilir ve PASV'nin dogru calismasi parolaya bagli olmamali.

    Hicbir kimlik yoksa acilis REDDEDILIR: parolasiz FTP sunucusu, sahadaki
    tum cihaz config'lerine anonim yazma izni demek olurdu.
    """
    s = SETTINGS
    kimlik = poller.fetch_once()
    masquerade = kimlik[2] if kimlik is not None else None
    if kimlik is not None and kimlik[0] and kimlik[1]:
        log.info("FTP kimligi backend'den alindi (kullanici: %s).", kimlik[0])
        return kimlik[0], kimlik[1], masquerade
    if s.ftp_password:
        log.info("FTP kimligi env'den alindi (backend'de parola ayarlanmamis ya da erisilemedi).")
        return s.ftp_user, s.ftp_password, masquerade
    log.error(
        "FTP kimligi yok: backend'e erisilemiyor ve FTP_PASSWORD bos — "
        "sunucu baslatilmiyor (guvenlik)."
    )
    raise SystemExit(2)


def _build_server() -> tuple[FTPServer, type[FTPHandler], CredentialPoller]:
    s = SETTINGS

    os.makedirs(s.ftp_root, exist_ok=True)
    # SN2'nin standart dizini hazir olsun — cihaz FTP ekranindaki "Dir"
    # alanina /SN20/FOTA/ girilir ve dizin yoksa cihaz 550 alip durur.
    os.makedirs(os.path.join(s.ftp_root, STANDARD_DEVICE_DIR), exist_ok=True)
    _share_root_with_backend(s.ftp_root)

    # TEK HESAP (bilincli karar, 2026-08-05). Kisa sureligine ikinci bir
    # salt-okunur hesap eklenmisti; kaldirildi. Sebep: cihazin FTP ekranina
    # elle girilen tek bir kimlik var ve ikinci hesap sahada karisiklik
    # uretiyor — hangi hesabin nereye yazdigi belirsizlesiyor. Kimlik bilgisi
    # artik arayuzden yonetiliyor, dolayisiyla "guvenli ikinci hesap" ihtiyaci
    # yerine TEK ve DEGISTIRILEBILIR bir kimlik tercih edildi.
    # Callback SONRADAN baglanir (bkz. CredentialPoller.set_on_change):
    # ilk kimligi poller ceker, handler o kimlikle kurulur, callback handler'a
    # ihtiyac duyar.
    poller = CredentialPoller(
        backend_url=s.backend_url,
        service_token=s.internal_service_token,
    )
    kullanici, parola, masquerade = _initial_credentials(poller)
    poller.prime((kullanici, parola, masquerade))

    reporter = EventReporter(
        backend_url=s.backend_url, service_token=s.internal_service_token
    )
    # ALT SINIF kullaniliyor, `FTPHandler` sinifina dogrudan atama DEGIL:
    # eskiden `handler = FTPHandler` yazip sinif ozniteliklerini degistiriyorduk;
    # bu, ayni surecte ikinci bir sunucu kurulursa ayarlari paylasilan global
    # bir duruma yazmak demekti. Alt sinif her kurulumu yalitir.
    handler = _make_handler(reporter)
    handler.authorizer = _make_authorizer(kullanici, parola, s.ftp_root)
    handler.banner = "EnerjiOne FTP ready."
    # Pasif mod veri portu araligi — docker-compose'ta host'a map edilmeli.
    handler.passive_ports = range(s.pasv_min_port, s.pasv_max_port + 1)
    # PASV yanitinda cihaza bildirilen adres. ZORUNLU dogru olmali: container
    # kendi IP'sini (172.18.x.x) soylerse LAN'daki istemci veri baglantisini
    # kuramaz ve her listeleme/transfer zaman asimina duser (sahada FileZilla
    # ile yasandi). Oncelik: backend'deki "Sunucu adresi" > env fallback.
    if masquerade or s.masquerade_address:
        handler.masquerade_address = masquerade or s.masquerade_address
        log.info("PASV masquerade adresi: %s", handler.masquerade_address)
    else:
        log.warning(
            "PASV masquerade adresi YOK — arayuzden 'Sunucu adresi' girilmedikce "
            "dis istemciler pasif modda veri baglantisi kuramayabilir."
        )

    # Kimlik degisiminde Authorizer TAKAS edilir — tabloyu yerinde degistirmek
    # degil. Sinif ozniteligi atamasi tek referans yazimidir (GIL altinda
    # atomik); login akisi ya tamamen eskisini ya tamamen yenisini gorur.
    # Aktif oturumlarin eski kullanici adi icin bkz. _make_authorizer.
    # Masquerade da ayni kanaldan gelir: arayuzde "Sunucu adresi" degisince
    # PASV yaniti yeniden baslatmasiz duzelir.
    def _kimlik_degisti(
        yeni_kullanici: str | None, yeni_parola: str | None, yeni_masquerade: str | None
    ) -> None:
        # Kimlik yalnizca TAM geldiyse takas edilir; parola henuz
        # ayarlanmamissa mevcut (env) kimlik calismaya devam eder. Masquerade
        # ise HER durumda uygulanir — PASV'nin dogrulugu parolaya bagli olamaz
        # (kullanici arayuzden yalnizca adresi kaydetmis olabilir).
        if yeni_kullanici and yeni_parola:
            eski = next(iter(handler.authorizer.user_table), None)
            handler.authorizer = _make_authorizer(
                yeni_kullanici, yeni_parola, s.ftp_root, eski_kullanici=eski
            )
        hedef = yeni_masquerade or s.masquerade_address or None
        if handler.masquerade_address != hedef:
            handler.masquerade_address = hedef
            log.info("PASV masquerade adresi guncellendi: %s", hedef)

    poller.set_on_change(_kimlik_degisti)

    server = FTPServer((s.listen_host, s.listen_port), handler)
    # Es zamanli baglanti sinirlari (kaynak korumasi).
    server.max_cons = 256
    server.max_cons_per_ip = 16
    return server, handler, poller


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    s = SETTINGS
    server, _handler, poller = _build_server()
    # Kimlik yoklamasi handler kurulduktan SONRA baslar; boylece ilk swap
    # hicbir zaman yarim kurulmus bir sunucuya denk gelmez.
    poller.start()

    start_health_server(
        host=s.health_host,
        port=s.health_port,
        snapshot=lambda: {
            "status": "ok",
            "service": "ftp-server",
            "ftp_port": s.listen_port,
            "ftp_root": s.ftp_root,
            "connections": len(getattr(server, "ip_map", []) or []),
            # AKTIF kullanici adi — arayuzdeki "baglanti durumu" paneli,
            # kimlik degisiminin sunucuya yansiyip yansimadigini buradan
            # gorur. Kimlik takasinda tabloya ilk eklenen YENI kullanicidir
            # (bkz. _make_authorizer); eski ad yalnizca acik oturumlar icin
            # fallback olarak durur.
            "ftp_user": next(iter(_handler.authorizer.user_table), None),
        },
        is_healthy=watchdog.saglikli,
    )

    # Kalp atisi ioloop'un KENDISINE zamanlanir: dongu donuyorsa atis gelir,
    # `serve_forever` icinde bir sey kilitlenirse zamanlanmis cagri hic
    # calismaz ve bekci sureci sonlandirir.
    #
    # Olcumun trafikten bagimsiz olmasi `serve_forever`e verilen ACIK
    # zaman asimina baglidir: timeout'suz cagride bos sunucu ilk `poll`
    # cagrisinda suresiz bloklanir, atis hic gelmez ve bekci SAGLIKLI
    # sunucuyu 60 sn'de bir oldururdu (bkz. IOLOOP_POLL_TIMEOUT_SN ve
    # tests/test_ioloop_bos_dongu.py).
    from pyftpdlib.ioloop import IOLoop

    IOLoop.instance().call_every(BEKCI_ATIS_SN, watchdog.kalp_at)
    watchdog.baslat(BEKCI_ESIK_SN, servis="ftp-server")

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
    server.serve_forever(timeout=IOLOOP_POLL_TIMEOUT_SN)


if __name__ == "__main__":
    main()
