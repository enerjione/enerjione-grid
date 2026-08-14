"""Sonucu hic gelmeyen komutlari sonlandirir (F3C-C).

KAPATILAN ARIZA
---------------
`sent` durumundaki bir komutu HICBIR SEY sonlandirmiyordu. Gateway sonucu
teslim edemezse (defter dead-letter'a aldi, gateway kalici olarak kayboldu,
sonuc kaydi bozuldu) komut SONSUZA KADAR `sent` kaliyordu:

  * arayuzde "gonderildi" gorunuyor, hicbir zaman tamamlanmiyor
  * operator kesicinin surulup surulmedigini ogrenemiyor
  * IEC 104 tarafi kendi zaman asimiyla negatif ACT_TERM gonderiyor ama
    backend kaydi hala acik kaliyor — iki katman farkli sey soyluyor

NEDEN SUPURUCU F3C-B'DEN SONRA GELDI
------------------------------------
F3C-B'den ONCE `sent`, "backend yanita koydu" demekti; teslim garantisi
yoktu. Boyle bir `sent` uzerine "gateway kabul etti ama sonuc gelmedi"
hukmu vermek YANLIS olurdu — komut gateway'e hic ulasmamis olabilirdi.
Teslim ACK'i geldikten sonra `sent` gercekten dayanikli kabul demektir ve
bu hukum dogru olur.

FIZIKSEL YENIDEN DENEME YOK
---------------------------
`sent -> pending` ASLA yapilmaz. Komut cihazda uygulanmis OLABILIR; onu
yeniden kuyruga koymak kesiciyi ikinci kez surmek demek olurdu. Sonuc
BELIRSIZ olarak sonlandirilir ve bu belirsizlik operatore acikca soylenir.

GEC GELEN GERCEK SONUC
----------------------
`result_unknown` bir TAHMINDIR. Gateway sonucu daha sonra teslim ederse
`command-results` ucu onu kabul eder ve kaydi duzeltir (bkz. mutabakat
istisnasi). Bu, supurucunun verdigi hukmun geri alinabilir olmasini saglar.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal

from app.core.config import settings
from app.models.device_command import DeviceCommand
from app.services import command_delivery_service
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

#: Tek turda sonlandirilacak azami komut. Normalde bu sayiya hic ulasilmaz;
#: sinir, patolojik bir durumda (gateway filosu toptan kayboldu) tek bir
#: transaction'in devlesmesini onler. Kalanlar bir sonraki turda alinir.
_BATCH = 500


def sonucu_gelmeyenleri_sonlandir(db: Session, *, now: datetime | None = None) -> int:
    """`sent` kalip sonucu gelmeyen komutlari terminal duruma alir.

    Doner: sonlandirilan komut sayisi.

    Olcut `sent_at`tir, `created_at` DEGIL: sure gateway'in komutu KABUL
    ETTIGI andan itibaren islemeli. `created_at` kullanmak, kuyrukta uzun
    bekleyen bir komutu teslim edilir edilmez sonlandirabilirdi.
    """
    simdi = now or datetime.now(timezone.utc)
    esik = simdi - timedelta(seconds=int(settings.command_result_timeout_sec))

    adaylar = list(
        db.scalars(
            select(DeviceCommand)
            .where(
                DeviceCommand.status == "sent",
                DeviceCommand.sent_at.is_not(None),
                DeviceCommand.sent_at < esik,
            )
            .order_by(DeviceCommand.id.asc())
            .limit(_BATCH)
            .with_for_update(skip_locked=True)
        ).all()
    )
    if not adaylar:
        return 0

    for cmd in adaylar:
        cmd.status = "failed"
        cmd.result_status = command_delivery_service.RESULT_UNKNOWN
        # MESAJ KANITA GORE DEGISIR.
        #
        # Yeni protokolde `sent` = gateway DAYANIKLI olarak kabul etti (ACK).
        # Eski protokolde `sent` yalnizca "backend yanita koydu" demektir ve
        # komutun gateway'e ULASTIGININ kaniti YOKTUR. Ikisine ayni cumleyi
        # yazmak operatore kanitlamadigimiz bir sey soylemek olurdu.
        if cmd.delivery_token:
            cmd.result_error = (
                "Gateway komutu kalici olarak kabul etti ancak cihaz uygulama "
                "sonucu alinamadi. Fiziksel komut uygulanmis olabilir."
            )
        else:
            cmd.result_error = (
                "Komut gateway'e gonderildi ancak ne teslim onayi ne de cihaz "
                "sonucu alinabildi (eski teslim protokolu). Fiziksel komut "
                "uygulanmis olabilir."
            )
        cmd.completed_at = simdi
        logger.warning(
            "event=command_result_timeout gateway_code=%s command_id=%s "
            "device_code=%s command=%s sent_at=%s timeout_sec=%s",
            cmd.gateway_code, cmd.id, cmd.device_code, cmd.command,
            cmd.sent_at.isoformat() if cmd.sent_at else None,
            settings.command_result_timeout_sec,
        )
        # KALICI DURUM DEGISIMI -> denetim kaydi. Supurucu sicak yolda DEGIL
        # (dakikada bir), dolayisiyla olay yazmak komut kanalini etkilemez.
        record_event(
            db,
            category="device",
            event_type="device_command_result_unknown",
            severity="warning",
            actor_username=cmd.actor_username,
            device_code=cmd.device_code,
            message=(
                f"Komut sonucu alinamadi: {cmd.command} ({cmd.device_code}) #{cmd.id} — "
                "gateway kabul etti, cihaz sonucu gelmedi"
            ),
            metadata={
                "command": cmd.command,
                "command_id": cmd.id,
                "gateway_code": cmd.gateway_code,
                "timeout_sec": int(settings.command_result_timeout_sec),
            },
            i18n_key="device_command_result_unknown",
            i18n_params={"command": cmd.command, "code": cmd.device_code},
        )

    return len(adaylar)


class CommandResultSweeper:
    """Periyodik supurucu. `leader` arkasinda TEK surecte kosar.

    Lider korumasi olmasaydi her API worker'i ayni komutlari sonlandirmaya
    calisir; `with_for_update(skip_locked=True)` cift yazmayi engellerdi ama
    ayni olay birden cok kez uretilebilirdi.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="command-result-sweeper", daemon=True
        )
        self._thread.start()
        logger.info(
            "command_result_sweeper_started tick_sec=%d timeout_sec=%d",
            settings.command_result_sweep_interval_sec,
            settings.command_result_timeout_sec,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        # Acilista kisa bekleme: DB/migration hazir olsun. Ayrica backend
        # yeniden baslatildiginda gateway'lerin ACK/sonuc kuyruklarini
        # bosaltmasina firsat verir — aksi halde restart'tan hemen sonra
        # tesliminde sorun olmayan komutlar `result_unknown` damgasi yerdi.
        self._stop.wait(30)
        while not self._stop.is_set():
            try:
                self._sweep()
            except Exception:  # noqa: BLE001
                logger.exception("command_result_sweep_failed")
            self._stop.wait(max(1, int(settings.command_result_sweep_interval_sec)))

    def _sweep(self) -> None:
        db = SessionLocal()
        try:
            adet = sonucu_gelmeyenleri_sonlandir(db)
            if adet:
                db.commit()
                logger.warning("command_result_sweeper_terminated count=%d", adet)
            else:
                db.rollback()
        finally:
            db.close()


_sweeper = CommandResultSweeper()


def start() -> None:
    _sweeper.start()


def stop() -> None:
    _sweeper.stop()
