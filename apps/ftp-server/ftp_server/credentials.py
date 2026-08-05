"""FTP kimligini backend'den ceker — parola degisimi YENIDEN BASLATMA istemez.

NEDEN
-----
Kimlik eskiden yalnizca ACILISTA env'den okunuyordu; arayuzden parola
degistirmek container'i yeniden baslatmayi gerektiriyordu. Kimlik artik
backend'de (ftp_settings tablosu, arayuzden yonetilir) ve bu modul onu kisa
araliklarla yoklar: degisince ana modul Authorizer'i tazeler.

NEDEN YOKLAMA, LOGIN ANINDA SORMAK DEGIL
----------------------------------------
pyftpdlib TEK THREAD'de kosar. `validate_authentication` icinde backend'e
HTTP sormak, backend yavasladiginda TUM FTP sunucusunu bloklardi (ayni sinif
hata icin bkz. reporter.py). Yoklama ayri daemon thread'te kosar; login ani
her zaman bellekteki kimlikle, bloklamadan dogrulanir. Bedeli: degisiklik en
gec bir yoklama araligi (30 sn) gecikmeyle etkinlesir — parola degisimi
zaten dakikalar mertebesinde bir saha isidir.

Backend erisilemezse SON BILINEN kimlik gecerli kalir; hic erisilemediyse
env'deki kimlik kullanilir. FTP sunucusu backend'siz de ayakta kalmali.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Callable

log = logging.getLogger("ftp-server.credentials")

#: Yoklama araligi (saniye). Parola degisiminin sahaya yansima ust siniri.
POLL_SEC = 30.0

#: Tek istek zaman asimi. Kisa: bu thread bloklanirsa zarar yok ama uzun
#: timeout'lar art arda birikip yoklamayi anlamsiz seyreltir.
TIMEOUT_SEC = 5


class CredentialPoller:
    """Backend'den (kullanici, parola, masquerade) ceker; degisince callback
    cagirir.

    Masquerade = pasif mod (PASV) yanitinda cihaza bildirilecek adres.
    Container kendi IP'sini (172.18.x.x) soylerse LAN'daki cihaz veri
    baglantisi kuramaz; dogru adres, ayarlardaki "Sunucu adresi"dir ve
    kimlikle ayni yoldan tasinir ki o da yeniden baslatmasiz degissin.
    """

    def __init__(
        self,
        *,
        backend_url: str,
        service_token: str,
        on_change: Callable[[str | None, str | None, str | None], None] | None = None,
    ) -> None:
        self._url = backend_url.rstrip("/") + "/internal/ftp-credentials"
        self._headers = {
            "X-Service-Token": service_token,
            "X-Service-Name": "ftp-server",
        }
        self._on_change = on_change
        self._enabled = bool(backend_url and service_token)
        self._last: tuple[str | None, str | None, str | None] | None = None
        self._unreachable_logged = False

    def set_on_change(self, cb: Callable[[str | None, str | None, str | None], None]) -> None:
        """Degisim callback'ini sonradan baglar.

        Acilista tavuk-yumurta var: ilk kimligi POLLER ceker, ama callback'in
        ihtiyac duydugu handler o kimlikle KURULUR. Bu yuzden poller once
        callback'siz olusturulur, handler kurulunca callback baglanir ve
        ancak ondan sonra `start()` cagrilir.
        """
        self._on_change = cb

    def fetch_once(self) -> tuple[str | None, str | None, str | None] | None:
        """Tek deneme. (kullanici, parola, masquerade) ya da None (ERISIM yok).

        Parola henuz ayarlanmamis olabilir — o durumda kullanici/parola None
        doner ama MASQUERADE yine tasinir. Eskiden parolasiz yanit topyekun
        None sayiliyordu ve masquerade HIC uygulanmiyordu: kullanici arayuzden
        yalnizca adresi kaydettiginde PASV kirik kaliyordu (sahada yasandi).
        Kimligi uygulayip uygulamamak cagiranin karari (bkz. main).
        """
        if not self._enabled:
            return None
        istek = urllib.request.Request(self._url, headers=self._headers)
        try:
            with urllib.request.urlopen(istek, timeout=TIMEOUT_SEC) as yanit:
                veri = json.loads(yanit.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if not self._unreachable_logged:
                log.warning("FTP kimligi backend'den alinamadi: %s", exc)
                self._unreachable_logged = True
            return None
        self._unreachable_logged = False
        kullanici = (veri.get("username") or "").strip() or None
        parola = veri.get("password") or None
        masquerade = (veri.get("masquerade") or "").strip() or None
        if not (kullanici and parola):
            # Bos parolayla hesap acilmaz — kimlik yok sayilir, adres kalir.
            kullanici = None
            parola = None
        return kullanici, parola, masquerade

    def start(self) -> None:
        """Arka plan yoklamasini baslatir (daemon thread)."""
        if not self._enabled:
            log.info(
                "FTP kimlik yoklamasi KAPALI (backend URL / servis token yok) — "
                "env'deki kimlik sabit kalir."
            )
            return
        threading.Thread(
            target=self._run, name="ftp-credential-poller", daemon=True
        ).start()

    def _run(self) -> None:
        import time

        while True:
            kimlik = self.fetch_once()
            if kimlik is not None and kimlik != self._last and self._on_change:
                self._last = kimlik
                try:
                    self._on_change(*kimlik)
                    log.info("FTP kimligi guncellendi (kullanici: %s).", kimlik[0])
                except Exception:  # noqa: BLE001
                    log.exception("FTP kimligi uygulanamadi")
            time.sleep(POLL_SEC)

    def prime(self, kimlik: tuple[str | None, str | None, str | None]) -> None:
        """Acilista uygulanmis kimligi kaydeder ki ilk yoklama onu 'degisti'
        sanip gereksiz bir swap yapmasin."""
        self._last = kimlik
