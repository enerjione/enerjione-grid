"""Karantinadaki bilinmeyen cihaz telemetrisini normal yola geri basar.

SOZLESME
--------
* Replay, canli tuketici ile AYNI is mantigini kullanir
  (`telemetry_consumer.process_valid_telemetry`). Kopyalanmis bir yol iki
  kaynagin sessizce ayrismasi demekti.
* Telemetri yazimi ile karantina durumunun guncellenmesi AYNI transaction'da
  olur; "telemetri yazildi ama kayit hala pending" araligi OLUSAMAZ.
* Ikinci kez replay DUPLICATE URETMEZ: mevcut telemetri dedup defteri
  (`processed_messages`) otorite olarak kullanilir.
* Cihaz hala tanimli degilse kayit KORUNUR (pending kalir); payload asla
  silinmez.
* Cihaz BASKA bir gateway'e aitse replay REDDEDILIR — bir gateway'in
  olcumu baska bir gateway'in cihazina yazilamaz.

Replay hicbir kosulda Device/SignalCatalog/profil URETMEZ.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from app.core.config import settings
from app.models.device import Device
from app.models.processed_message import ProcessedMessage
from app.models.unknown_device_telemetry import UnknownDeviceTelemetry
from app.schemas.telemetry import TelemetryIn
from app.services import unknown_device_quarantine as quarantine

logger = logging.getLogger(__name__)

# Cozulemeyen sebepler — `last_replay_error` alanina yazilir. Kayit her
# durumda PENDING kalir; terminal bir `failed` durumu payload'i operatorun
# gozunden kacirirdi.
ERR_DEVICE_NOT_FOUND = "device_not_found"
ERR_GATEWAY_MISMATCH = "gateway_mismatch"
ERR_INVALID_PAYLOAD = "invalid_payload"


@dataclass
class ReplayResult:
    requested: int = 0
    replayed: int = 0
    skipped_already_processed: int = 0
    still_pending: int = 0
    errors: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "replayed": self.replayed,
            "skipped_already_processed": self.skipped_already_processed,
            "still_pending": self.still_pending,
            "errors": dict(self.errors),
        }

    def _hata(self, ad: str) -> None:
        self.errors[ad] = self.errors.get(ad, 0) + 1
        self.still_pending += 1


def replay(  # noqa: ANN001
    db,
    *,
    device_code: str | None = None,
    gateway_code: str | None = None,
    limit: int = 500,
) -> ReplayResult:
    """Bekleyen karantina kayitlarini normal telemetri yoluna basar.

    `device_code` verilmezse TUM bekleyen kayitlar (limit kadar) denenir;
    cihazi hala tanimsiz olanlar dokunulmadan pending kalir.

    Commit BU FONKSIYONDA yapilir: telemetri satirlari ile karantina durumu
    ayni commit'te durur.
    """
    from app.services.telemetry_consumer import (
        CONSUMER_NAME,
        _tek_gecis_yaz,
        process_valid_telemetry,
    )

    tavan = max(1, min(int(limit), int(settings.unknown_telemetry_replay_max_limit)))

    sorgu = select(UnknownDeviceTelemetry).where(
        UnknownDeviceTelemetry.status == quarantine.STATUS_PENDING
    )
    if device_code:
        sorgu = sorgu.where(UnknownDeviceTelemetry.device_code == device_code)
    if gateway_code:
        sorgu = sorgu.where(UnknownDeviceTelemetry.gateway_code == gateway_code)
    # Eski olcum once: replay sonrasi zaman serisi dogal sirasinda olusur.
    sorgu = sorgu.order_by(UnknownDeviceTelemetry.first_seen_at.asc()).limit(tavan)

    kayitlar = list(db.scalars(sorgu).all())
    sonuc = ReplayResult(requested=len(kayitlar))
    if not kayitlar:
        return sonuc

    # Cihaz aramasi TEK sorguda — kayit basina SELECT yapmak binlerce
    # satirda gereksiz gidis-donus olurdu.
    kodlar = {k.device_code for k in kayitlar}
    cihazlar = {
        d.code: d for d in db.scalars(select(Device).where(Device.code.in_(kodlar))).all()
    }

    simdi = datetime.now(timezone.utc)

    for kayit in kayitlar:
        cihaz = cihazlar.get(kayit.device_code)

        if cihaz is None:
            # CIHAZ HALA YOK — payload korunur, kayit pending kalir.
            kayit.replay_attempts += 1
            kayit.last_replay_error = ERR_DEVICE_NOT_FOUND
            kayit.updated_at = simdi
            sonuc._hata(ERR_DEVICE_NOT_FOUND)
            continue

        # GATEWAY IZOLASYONU.
        #
        # `device_code` kurulum genelinde benzersiz. Olcum A gateway'inden
        # geldiyse ama ayni kodlu cihaz sonradan B gateway'ine tanimlandiysa,
        # bu payload B'nin cihazina AIT DEGILDIR. Yazmak bir sahanin
        # olcumunu baska bir sahaya karistirirdi.
        if (
            kayit.gateway_code
            and cihaz.gateway_code
            and kayit.gateway_code != cihaz.gateway_code
        ):
            kayit.replay_attempts += 1
            kayit.last_replay_error = ERR_GATEWAY_MISMATCH
            kayit.updated_at = simdi
            sonuc._hata(ERR_GATEWAY_MISMATCH)
            continue

        # ZATEN ISLENMIS MI? (canli yol mesaji karantinadan sonra yeniden
        # teslim almis ve normal islemis olabilir.) Telemetri dedup defteri
        # otoritedir; ikinci kez yazmak duplicate olcum uretirdi.
        if _islenmis_mi(db, CONSUMER_NAME, kayit.message_id):
            _replayed_isaretle(kayit, simdi)
            sonuc.skipped_already_processed += 1
            continue

        try:
            payload = json.loads(kayit.payload_json)
            if not isinstance(payload, dict):
                raise ValueError("payload nesne degil")
            reading = TelemetryIn(**payload)
        except (ValueError, ValidationError) as exc:
            # Karantinaya yalnizca dogrulanmis payload girer; buraya
            # dusuluyorsa sema o kayittan SONRA daraltilmis demektir.
            # Payload silinmez, sebep gorunur kalir.
            kayit.replay_attempts += 1
            kayit.last_replay_error = f"{ERR_INVALID_PAYLOAD}: {str(exc)[:400]}"
            kayit.updated_at = simdi
            sonuc._hata(ERR_INVALID_PAYLOAD)
            continue

        try:
            # SAVEPOINT: bir kaydin yazimi patlarsa yalnizca o kayit duser,
            # ayni cagridaki digerleri ve onlarin cihaz mutasyonlari ayakta
            # kalir.
            with db.begin_nested():
                satir = process_valid_telemetry(
                    db,
                    device=cihaz,
                    reading=reading,
                    payload=payload,
                    message_id=kayit.message_id,
                    msg=None,
                )
                # Canli yolla AYNI yazim gecisi: telemetry + processed_messages
                # + arsiv + telemetry_latest.
                _tek_gecis_yaz(db, [satir], simdi)
        except IntegrityError:
            # `processed_messages` bilesik UNIQUE'i "bu olcum ZATEN islenmis"
            # diyor (yukaridaki on kontrolle canli yol arasindaki yaris).
            # Telemetri mevcut; kayit replayed sayilir, duplicate YAZILMAZ.
            _replayed_isaretle(kayit, simdi)
            sonuc.skipped_already_processed += 1
            continue
        except Exception as exc:  # noqa: BLE001
            kayit.replay_attempts += 1
            kayit.last_replay_error = str(exc)[:500]
            kayit.updated_at = simdi
            sonuc._hata("replay_error")
            logger.warning(
                "unknown_device_replay_failed id=%s device=%s error=%s",
                kayit.id,
                kayit.device_code,
                exc,
            )
            continue

        _replayed_isaretle(kayit, simdi)
        sonuc.replayed += 1

    # TEK COMMIT: telemetri satirlari ve karantina durumlari birlikte
    # kalicilasir. Crash commit ONCESI olursa ikisi de geri sarilir ve
    # yeniden replay temiz calisir; commit SONRASI olursa ikisi de yazilmis
    # olur. "Telemetri var ama kayit pending" araligi yok.
    db.commit()

    # METRIKLER COMMIT SONRASI — geri sarilan bir replay "basarili" sayilmaz.
    quarantine._stat_arttir("unknown_device_replay_success_total", sonuc.replayed)
    quarantine._stat_arttir(
        "unknown_device_replay_failed_total", sonuc.errors.get("replay_error", 0)
    )
    quarantine._sayim_onbellegi_bosalt()
    return sonuc


def _islenmis_mi(db, consumer_name: str, message_id: str) -> bool:  # noqa: ANN001
    return (
        db.scalar(
            select(ProcessedMessage.id).where(
                ProcessedMessage.consumer_name == consumer_name,
                ProcessedMessage.message_id == message_id,
            )
        )
        is not None
    )


def _replayed_isaretle(kayit: UnknownDeviceTelemetry, simdi: datetime) -> None:
    kayit.status = quarantine.STATUS_REPLAYED
    kayit.replayed_at = simdi
    kayit.replay_attempts += 1
    kayit.last_replay_error = None
    kayit.updated_at = simdi
