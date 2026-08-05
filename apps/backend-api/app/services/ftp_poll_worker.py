"""Harici FTP yoklayicisi — musterinin sunucusundaki config dosyalarini izler.

NEDEN VAR
---------
Gomulu modda cihazin yazdigi dosyayi ftp-server callback'i ANINDA bildirir
(bkz. apps/ftp-server reporter + /internal/ftp-events). Harici modda sunucu
BIZIM DEGIL — callback yok. Cihazin `start_csv_upload` ile yazdigi
`<seri>_Configuration.csv` dosyasini gorebilmenin tek yolu YOKLAMA: belirli
araliklarla dizini tara, yeni/degisen dosyayi indir, surume cevir.

CALISMA BICIMI
--------------
* 30 saniyede bir uyanir; mod `harici` DEGILSE hicbir sey yapmaz. Boylece
  arayuzden mod degistirmek yeniden baslatma istemez.
* Gercek tarama araligi ayarlardaki `poll_interval_sec` — musteri sunucusunu
  gereksiz yormamak icin varsayilan 5 dakika.
* Degisiklik tespiti once SIZE+MDTM onbellegi ile (indirme tasarrufu), sonra
  icerik karsilastirmasiyla (ingest_pulled_config ayni baytlara surum acmaz).
  MDTM/SIZE desteklemeyen sunucuda her turda indirilir — dosyalar ~1 KB,
  bunun maliyeti onemsiz.
* Hata durumu DURUM DEGISIMI olarak kaydedilir (ok->hata bir olay, hata->ok
  bir olay); her turda olay uretmek olay kaydini kendi kendine doldururdu.
"""

from __future__ import annotations

import logging
import threading

from app.db.session import SessionLocal
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

#: Uyanma araligi (mod kontrolu). Tarama araligi ayarlardan gelir.
_WAKE_SEC = 30.0


class FtpPollWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: path -> (size, mtime) — degismeyen dosyayi indirmemek icin.
        self._seen: dict[str, tuple[int | None, str | None]] = {}
        self._son_tarama = 0.0
        self._hata_durumunda = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ftp-config-poll", daemon=True
        )
        self._thread.start()
        logger.info("ftp_poll_worker_started wake=%.0fs", _WAKE_SEC)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        import time

        while not self._stop.is_set():
            try:
                db = SessionLocal()
                try:
                    from app.services import ftp_settings_service

                    ayar = ftp_settings_service.get_settings(db)
                    aralik = float(ayar.poll_interval_sec or 300)
                    if (
                        ayar.mode == "harici"
                        and time.monotonic() - self._son_tarama >= aralik
                    ):
                        self._son_tarama = time.monotonic()
                        self._sweep(db)
                finally:
                    db.close()
            except Exception:  # noqa: BLE001
                logger.exception("ftp_poll_tur_hatasi")
            self._stop.wait(_WAKE_SEC)

    def _sweep(self, db) -> None:
        """Tek tarama turu: listele, degiseni indir, surume cevir."""
        from app.services import device_config_service as cfg_svc
        from app.services import ftp_client_service as ftp
        from app.services.horstmann_config_codec import ConfigParseError

        try:
            dosyalar = ftp.read_remote_configs(db)
        except ftp.FtpAccessError as exc:
            self._durum_degisti(db, hata=str(exc))
            db.commit()
            return
        self._durum_degisti(db, hata=None)

        for uzak in dosyalar:
            onceki = self._seen.get(uzak.path)
            imza = (uzak.size, uzak.mtime)
            # SIZE ve MDTM ikisi de bilinmiyorsa imza karsilastirmasi anlamsiz
            # — her turda indir; icerik karsilastirmasi son savunma hatti.
            if onceki == imza and (uzak.size is not None or uzak.mtime is not None):
                continue

            seri = uzak.filename.split("_", 1)[0]
            device_id = cfg_svc.find_device_id_by_serial(db, seri)
            if device_id is None:
                # Eslesmeyen seri her turda yeniden loglanmasin diye imzayi
                # yine de kaydet; cihaz sisteme eklenince MDTM degismese de
                # bir sonraki dosya degisiminde yakalanir. Log bir kez duser.
                if onceki != imza:
                    logger.warning(
                        "ftp_poll eslesmeyen seri: %s (%s)", seri, uzak.path
                    )
                self._seen[uzak.path] = imza
                continue

            try:
                ham = ftp.download_remote(db, uzak.path)
                surum = cfg_svc.ingest_pulled_config(
                    db, device_id=device_id, ham=ham, filename=uzak.filename
                )
            except (ftp.FtpAccessError, ConfigParseError) as exc:
                record_event(
                    db,
                    category="ftp",
                    event_type="ftp_poll_error",
                    severity="warning",
                    message=f"Harici FTP'deki dosya islenemedi: {uzak.filename} — {exc}",
                    metadata={"path": uzak.path, "error": str(exc)},
                )
                db.commit()
                continue

            self._seen[uzak.path] = imza
            if surum is not None:
                record_event(
                    db,
                    category="ftp",
                    event_type="ftp_poll_ingested",
                    severity="info",
                    message=(
                        f"Harici FTP'den yapilandirma alindi: {uzak.filename} "
                        f"(v{surum.version})"
                    ),
                    metadata={
                        "path": uzak.path,
                        "filename": uzak.filename,
                        "version": surum.version,
                        "device_id": device_id,
                    },
                )
            db.commit()

    def _durum_degisti(self, db, *, hata: str | None) -> None:
        """Baglanti durumu degisimini BIR KEZ olay olarak kaydeder."""
        if hata is not None and not self._hata_durumunda:
            self._hata_durumunda = True
            logger.warning("ftp_poll baglanti hatasi: %s", hata)
            record_event(
                db,
                category="ftp",
                event_type="ftp_poll_unreachable",
                severity="warning",
                message=f"Harici FTP sunucusuna erisilemiyor: {hata}",
                metadata={"error": hata},
            )
        elif hata is None and self._hata_durumunda:
            self._hata_durumunda = False
            logger.info("ftp_poll baglanti normale dondu")
            record_event(
                db,
                category="ftp",
                event_type="ftp_poll_recovered",
                severity="info",
                message="Harici FTP sunucusuna erisim normale dondu.",
            )


_worker = FtpPollWorker()


def start() -> None:
    _worker.start()


def stop() -> None:
    _worker.stop()
