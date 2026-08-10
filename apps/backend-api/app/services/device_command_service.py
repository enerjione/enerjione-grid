"""Cihaz komutu kuyruga alma — TEK dogrulama yolu.

NEDEN SERVIS
------------
Komut iki ayri kapidan gelebiliyor:

  1. Kullanici arayuzu   -> POST /devices/{code}/command      (ENGINEER/INSTALLER)
  2. IEC 104 SCADA       -> POST /internal/device-commands    (servis token'i)

Ikisinin de AYNI allowlist'ten, AYNI gateway kontrolunden ve AYNI audit
kaydindan gecmesi gerekiyor. Dogrulamayi router'da tekrarlamak, ikinci
kapinin zamanla ilkinden ayrismasi demekti — ve ayrisan taraf her zaman
kontrolun GEVSEK oldugu taraf olur.

PROTOKOL KAPSAMI
----------------
Protokol uzerinden gelen komutlar `PROTOCOL_ALLOWED_SLUGS` ile SIKI sekilde
sinirli (urun karari: yalnizca ariza gostergesi reset'i). Bu, arayuzden
gonderilebilen komut kumesinden KASITLI olarak daha dardir: arayuzde kimlik
dogrulanmis, rolu belli bir kullanici vardir; IEC 104 tarafinda ise yalnizca
TCP baglantisi olan bir master vardir. `firmware_update` veya `config_update`
gibi komutlarin bu yoldan tetiklenebilmesi kabul edilemez.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.services import device_kit_service
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

#: Bir dis protokolun (IEC 104) tetikleyebilecegi komutlar.
#: Genisletmek bilincli bir urun karari olmali — bkz. modul docstring'i.
PROTOCOL_ALLOWED_SLUGS = frozenset({"reset_all_fcis"})


class CommandRejected(Exception):
    """Komut kuyruga ALINAMADI. `reason` makine tarafindan okunabilir."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class QueuedCommand:
    id: int
    status: str
    command: str
    dnp3_index: int
    device_code: str


def resolve_command_index(db: Session, slug: str, *, model: str | None = None) -> tuple[int, str]:
    """Slug'i SignalCatalog uzerinden DNP3 index'ine cevirir.

    Ham index KABUL EDILMEZ: adres DB'den yonetilir, boylece cihaz profili
    degistiginde tek yerden guncellenir ve cagiran taraf yanlis bir index
    uyduramaz.

    MODEL ZORUNLU GIBI DAVRAN: sinyal anahtari artik MODEL BAZINDA tekil
    (bkz. SignalCatalog docstring). Ayni slug iki modelde de bulunabilir ve
    DNP3 indeksleri FARKLI olabilir — olcum: `master.boost_mode` SN2'de 26,
    Pole Master Kit'te 30. Modeli vermeden cozmek, komutu yanlis noktaya
    gondermek demektir; cihaz hicbir hata dondurmez, sadece istenmeyen bir
    sey yapar ya da hicbir sey yapmaz.

    `model=None` yalnizca geriye uyum icin kabul edilir ve tek eslesme
    varsa calisir; birden fazla model ayni slug'i tasiyorsa REDDEDER.
    """
    stmt = select(SignalCatalog).where(
        SignalCatalog.key == f"master.{slug}",
        SignalCatalog.data_type == "binary_output",
        SignalCatalog.is_active.is_(True),
    )
    if model:
        stmt = stmt.where(SignalCatalog.model == model)
    satirlar = list(db.scalars(stmt).all())
    if not satirlar:
        raise CommandRejected("unknown_command", f"Gecersiz veya pasif komut: {slug}")
    if len(satirlar) > 1:
        raise CommandRejected(
            "ambiguous_command",
            f"'{slug}' komutu birden fazla cihaz modelinde tanimli; "
            "hangi model icin cozulecegi belirtilmeli.",
        )
    signal = satirlar[0]
    return int(signal.dnp3_index), str(signal.label or slug)


def queue_command(
    db: Session,
    *,
    device: Device,
    slug: str,
    actor: str,
    origin: str,
    count: int = 1,
    on_time_ms: int = 0,
    off_time_ms: int = 0,
) -> QueuedCommand:
    """Komutu `device_commands` tablosuna pending olarak yazar + audit birakir.

    Commit ETMEZ — cagiran taraf kendi transaction'ini yonetir.

    `origin` audit'te komutun hangi kapidan geldigini gosterir ("ui",
    "iec104"); `actor` ise kullanici adi ya da protokol/peer bilgisi.

    SANAL SET -> FIZIKSEL KIT: bir Pole Master Kit setinin kendi DNP3
    oturumu yoktur. Komut kitin outstation'ina gider ve KIT GENELINDE
    etkilidir ("Tum FCI'lari sifirla" dokuz uydunun hepsini sifirlar).
    Yonlendirme yapilmasaydi komut, gateway'e hic verilmeyen bir cihaz
    koduyla kuyruga girer ve sessizce hicbir yere ulasmazdi.
    """
    hedef = device_kit_service.command_target(db, device)

    if not hedef.gateway_code:
        raise CommandRejected(
            "no_gateway", "Cihaz bir gateway'e bagli degil; komut gonderilemez."
        )
    gateway = db.scalar(select(Gateway).where(Gateway.code == hedef.gateway_code))
    if gateway is None:
        raise CommandRejected("no_gateway", f"Gateway bulunamadi: {hedef.gateway_code}")

    index, label = resolve_command_index(db, slug, model=hedef.model)

    cmd = DeviceCommand(
        gateway_code=gateway.code,
        device_code=hedef.code,
        command=slug,
        dnp3_index=index,
        # Horstmann SN2 Device Profile PULSE DESTEKLEMEZ (yalniz LATCH_ON/OFF).
        op_type="latch_on",
        count=count,
        on_time_ms=on_time_ms,
        off_time_ms=off_time_ms,
        status="pending",
        actor_username=actor,
    )
    db.add(cmd)
    db.flush()  # id icin

    record_event(
        db,
        category="device",
        event_type="device_command_queued",
        severity="info",
        actor_username=actor,
        device_code=hedef.code,
        message=(
            f"Komut kuyruga alindi: {label} ({hedef.code}) #{cmd.id}"
            + (f" — istek: {device.code}" if hedef.code != device.code else "")
        ),
        metadata={
            "command": slug,
            "index": index,
            "command_id": cmd.id,
            # Kapinin hangisi oldugu ADLI INCELEME icin kritik: "bu reset'i
            # kim istedi" sorusunun cevabi UI kullanicisi ile SCADA master'i
            # arasinda ayrilabilmeli.
            "origin": origin,
            # Sanal setten verilen komut fiziksel kite gider; "kim istedi"
            # ile "nereye gitti" AYRI kayitlar olmali.
            "requested_device_code": device.code,
        },
        i18n_key="device_command_queued",
        i18n_params={"command": slug, "code": hedef.code},
    )
    return QueuedCommand(
        id=cmd.id,
        status=cmd.status,
        command=slug,
        dnp3_index=index,
        device_code=hedef.code,
    )


def queue_protocol_command(
    db: Session, *, device_code: str, slug: str, origin: str, peer: str
) -> QueuedCommand:
    """Dis protokol (IEC 104) icin komut kuyruga alir.

    Arayuz yolundan FARKI: slug `PROTOCOL_ALLOWED_SLUGS` ile sinirli.
    """
    if slug not in PROTOCOL_ALLOWED_SLUGS:
        logger.warning(
            "protokol_izinsiz_komut origin=%s peer=%s device=%s slug=%s — "
            "protokol uzerinden yalnizca %s kabul edilir",
            origin, peer, device_code, slug, ",".join(sorted(PROTOCOL_ALLOWED_SLUGS)),
        )
        raise CommandRejected(
            "not_allowed_for_protocol",
            f"'{slug}' protokol uzerinden gonderilemez",
        )

    device = db.scalar(select(Device).where(Device.code == device_code))
    if device is None:
        raise CommandRejected("unknown_device", f"Cihaz bulunamadi: {device_code}")

    return queue_command(
        db,
        device=device,
        slug=slug,
        actor=f"{origin}:{peer}",
        origin=origin,
    )
