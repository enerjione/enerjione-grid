"""Surec rolu ve arka plan islerinin TEKILLIGI.

NEDEN VAR
---------
Backend sureci HTTP servisinin yaninda bir dizi arka plan isi de calistiriyor:
telemetri tuketicisi, outbox flush, MQTT yayini, toplu bildirim zamanlayici,
retention, alarm mutabakati, yedekleme... Bunlarin TAMAMI tekil olmali.
Kayitli isler `leader.status()["jobs"]` ile (yani `/health` govdesinde)
gorulur; sayiyi burada tekrar yazmak dokuman kaymasi uretiyordu.

Tek uvicorn worker ile bu kendiliginden saglaniyordu. Olcek buyudukce
(600 cihaz) iki sey gerekiyor:
  * arka plan isini HTTP'den AYIRMAK (GIL ve CPU rekabeti),
  * API'yi COKLU worker ile calistirmak.
Ikisi de ayni tehlikeyi doguruyor: is birden fazla surecte tekrar calisir.
Sonuclari sessiz ve pahali — yedek iki kez alinir, toplu bildirim iki kez
gider, retention ayni satirlari iki kez silmeye calisir, IEC104 sunucusu
portu ikinci kez baglayamaz.

IKI KATMANLI KORUMA
-------------------
1. ROL (yapilandirma): surecin NE YAPMASI gerektigini soyler.
     all    — hepsi (VARSAYILAN; bugunku davranis, tek container kurulumlar)
     api    — yalnizca HTTP/WS; arka plan isi YOK
     worker — yalnizca arka plan isi

2. ADVISORY LOCK (calisma zamani): gercekten tekil OLDUGUNU garanti eder.
   Rol yapilandirmasi niyeti soyler ama yanlis yapilandirma sessizce ise
   yarar: iki container da `all` kalirsa is iki kez kosardi. Postgres
   session-level advisory lock bunu YAPISAL olarak imkansiz kilar.

NEDEN VARSAYILAN `all`
----------------------
Sahadaki kurulumlar tek container calisiyor ve `update.sh` ile guncelleniyor.
Varsayilan `api` olsaydi guncelleme sonrasi TUM arka plan isi sessizce
dururdu: telemetri DB'ye yazilmaz, alarm uretilmez, yedek alinmaz — ve
hicbir hata gorunmezdi. Yeni davranis yalnizca compose'da acikca rol
verildiginde devreye girer.

LIDERLIK NEDEN YENIDEN DENENIR
------------------------------
Kilit alinamadiginda is bir daha HIC denenmeseydi, worker container'inin
yeniden baslatilmasi kalici bir kesinti yaratirdi: eski surec kilidi hala
tutarken yeni surec "birisi calisiyor" deyip vazgecer, eski surec olunce de
kimse devralmaz. Bu yuzden arka planda periyodik olarak yeniden denenir.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from sqlalchemy import text

from app.core.config import settings

logger = logging.getLogger(__name__)

ROLE_ALL = "all"
ROLE_API = "api"
ROLE_WORKER = "worker"
_VALID_ROLES = (ROLE_ALL, ROLE_API, ROLE_WORKER)

# Postgres advisory lock anahtari. Sabit ve keyfi; yalnizca bu uygulamanin
# kendi surecleri arasinda anlamli. 64-bit alanin icinde kalir.
_BACKGROUND_LOCK_KEY = 0x0E1_6D_10AD

# Kilit alinamazsa yeniden deneme araligi.
_RETRY_INTERVAL_SEC = 15.0


def current_role() -> str:
    """Yapilandirilmis rol. Taninmayan deger `all`a duser (guvenli taraf)."""
    raw = (settings.service_role or "").strip().lower()
    if raw in _VALID_ROLES:
        return raw
    if raw:
        logger.warning(
            "service_role_unknown role=%r — `all` varsayildi (arka plan isleri "
            "calisir). Gecerli degerler: %s",
            raw,
            ", ".join(_VALID_ROLES),
        )
    return ROLE_ALL


def wants_background() -> bool:
    """Bu surec arka plan islerini calistirmayi AMACLIYOR mu (rol geregi)?

    "Amacliyor" — gercekten calistiracagi advisory lock ile belirlenir.
    """
    return current_role() in (ROLE_ALL, ROLE_WORKER)


def serves_http() -> bool:
    """Bu surec HTTP/WS trafigine hizmet ediyor mu?

    `worker` rolu de uvicorn altinda ayaga kalkar (ayni imaj) ama trafik
    almaz; saglik ucu disinda bir sey beklenmez.
    """
    return current_role() in (ROLE_ALL, ROLE_API)


class BackgroundLeader:
    """Arka plan islerini kumede TEK surecte calistirir.

    Postgres session-level advisory lock kullanir: kilit, kilidi tutan
    baglanti kapandigi anda (surec olse bile) OTOMATIK serbest kalir. Bu,
    "lider oldu ama olduğunu kimse bilmiyor" durumunu imkansiz kilar — TTL
    veya heartbeat tablosu tutmaya gerek yok.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn = None  # kilidi TUTAN baglanti; acik kalmali
        self._jobs: list[tuple[str, Callable[[], None], Callable[[], None]]] = []
        self._started = False
        self._retry_timer: threading.Timer | None = None
        self._stopping = False

    # ------------------------------------------------------------------ api

    def register(self, name: str, start: Callable[[], None], stop: Callable[[], None]) -> None:
        """Bir arka plan isini kaydet. Sira KORUNUR (baslatma sirasi onemli)."""
        self._jobs.append((name, start, stop))

    def claim(self) -> bool:
        """Liderligi ISLERI BASLATMADAN talep et.

        NEDEN AYRI BIR METOD
        --------------------
        IEC 104 sunuculari TCP portu baglar, yani kumede TEK surecte
        acilmalidir. Ama acilislari `start_background_jobs`tan ONCEKI bir
        FastAPI startup event'inde olmak zorunda: `deploy_all_active_targets`
        calisan bir asyncio loop'u ister ve o loop yalnizca async startup
        event'inde elde edilir.

        O noktada `is_leader` HENUZ False oldugu icin duz bir liderlik
        kontrolu HER ZAMAN False dondururdu ve IEC 104 HIC acilmazdi. Bu
        metod kilidi erkenden alir; `try_start` daha sonra ayni kilidi
        TEKRAR almaya calismaz (bkz. `_acquire` icindeki koruma).

        Yalnizca role bakmak (eski davranis) yetmiyordu: rol `all` iken
        `wants_background()` HER uvicorn worker'inda True'dur, yani
        `--workers 4` ile dort surec de ayni porta bind denerdi.
        """
        if not wants_background():
            return False
        with self._lock:
            if self._stopping:
                return False
            if self._started:
                return True
            return self._acquire()

    def try_start(self) -> bool:
        """Kilidi almayi dene; alirsa isleri baslat. Alinamadiysa yeniden dener."""
        if not wants_background():
            logger.info(
                "background_jobs_skipped role=%s — bu surec yalnizca HTTP/WS servis eder",
                current_role(),
            )
            return False

        with self._lock:
            if self._started or self._stopping:
                return self._started
            if not self._acquire():
                logger.info(
                    "background_jobs_standby — arka plan isleri BASKA bir surecte "
                    "calisiyor; %ds sonra yeniden denenecek",
                    int(_RETRY_INTERVAL_SEC),
                )
                self._schedule_retry()
                return False
            self._start_jobs()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            if self._retry_timer is not None:
                self._retry_timer.cancel()
                self._retry_timer = None
            if not self._started:
                self._release()
                return
            # Ters sirada durdur: baslatma sirasinin bagimliliklari olabilir.
            for name, _start, stop in reversed(self._jobs):
                try:
                    stop()
                except Exception:  # noqa: BLE001
                    logger.warning("background_job_stop_failed job=%s", name, exc_info=True)
            self._started = False
            self._release()

    @property
    def is_leader(self) -> bool:
        return self._started

    def status(self) -> dict:
        """/health icin. Kimsenin lider OLMAMASI sessiz bir ariza olurdu."""
        return {
            "role": current_role(),
            "wants_background": wants_background(),
            "is_leader": self._started,
            "jobs": [name for name, _s, _t in self._jobs],
        }

    # -------------------------------------------------------------- dahili

    def _acquire(self) -> bool:
        from app.db.session import engine

        if self._conn is not None:
            # Kilit BU SURECTE zaten tutuluyor (`claim()` erkenden almis
            # olabilir). Ikinci bir baglantidan `pg_try_advisory_lock`
            # cagirmak KENDI kilidimize takilir ve False donerdi: `try_start`
            # "baska surec lider" deyip hicbir isi acmaz, 15sn'de bir sonsuza
            # kadar yeniden dener. Yani IEC 104 acilan surecte arka plan
            # isleri HIC baslamazdi.
            return True

        try:
            conn = engine.connect()
        except Exception:  # noqa: BLE001
            logger.warning("background_lock_connect_failed", exc_info=True)
            return False
        try:
            acquired = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": _BACKGROUND_LOCK_KEY}
                ).scalar()
            )
        except Exception:  # noqa: BLE001
            # SQLite (testler) ya da advisory lock desteklemeyen bir backend:
            # kilit YOKSAYILIR ve isler calisir. Aksi halde test/gelistirme
            # ortaminda hicbir arka plan isi acilmazdi.
            logger.debug("background_lock_unsupported — kilitsiz devam", exc_info=True)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None
            return True
        if not acquired:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return False
        # Baglanti ACIK BIRAKILIR — kilit session omru boyunca gecerli.
        self._conn = conn
        return True

    def _release(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _BACKGROUND_LOCK_KEY})
        except Exception:  # noqa: BLE001
            logger.debug("background_lock_unlock_failed", exc_info=True)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _start_jobs(self) -> None:
        logger.info(
            "background_jobs_starting role=%s jobs=%d — bu surec LIDER",
            current_role(),
            len(self._jobs),
        )
        for name, start, _stop in self._jobs:
            try:
                start()
            except Exception:  # noqa: BLE001
                # Tek bir isin patlamasi digerlerini goturmesin; hangisinin
                # acilmadigi log'da gorunur.
                logger.exception("background_job_start_failed job=%s", name)
        self._started = True

    def _schedule_retry(self) -> None:
        timer = threading.Timer(_RETRY_INTERVAL_SEC, self._retry)
        timer.daemon = True
        self._retry_timer = timer
        timer.start()

    def _retry(self) -> None:
        with self._lock:
            self._retry_timer = None
            if self._started or self._stopping:
                return
            if not self._acquire():
                self._schedule_retry()
                return
            self._start_jobs()


leader = BackgroundLeader()
