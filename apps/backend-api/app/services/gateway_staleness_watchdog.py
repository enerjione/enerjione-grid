"""Gateway bayatlık bekçisi — gateway susarsa cihazları YEŞİL bırakma.

NEDEN VAR
---------
`communication_status` yalnızca bir telemetri okuması geldiğinde
güncelleniyor. Gateway tamamen susarsa (çökme, ağ kopması, yanlış
yapılandırma) hiç okuma gelmez ve tüm cihazları **son durumlarında donar** —
genellikle ONLINE. Harita yeşil kalır, operatör sahayı sağlıklı sanır.

NE **YAPMIYOR** — VE BU AYRIM KRİTİK
------------------------------------
Cihaz bazlı "veri gelmiyor" kontrolü YAPMIYOR. İlk tasarımda tam da o vardı
ve YANLIŞTI:

    Gateway yalnızca DEĞİŞEN değerleri yayınlıyor. Durağan bir fiderde
    değerler değişmezse hiçbir şey yayınlanmaz — ama cihaz gayet ayaktadır.
    Veri yaşına bakan bir bekçi onu OFFLINE işaretler ve **yanlış alarm**
    üretir.

Sahada tam bu görüldü: bir cihaz 1 saat 48 dakika veri göndermemişti; sebep
kopma değil, değerlerinin değişmemesiydi.

"Veri gelmiyor" ile "haberleşme yok" AYNI ŞEY DEĞİL. Bu ayrımı yapabilecek
tek katman gateway'dir, çünkü DNP3 bağlantısı onda:

  * link ölürse gateway `comm_lost` yayınlar → cihaz zaten OFFLINE olur,
  * link açık ama veri eskiyse gateway bunu ayrıca raporlar
    ("link acik gozukuyor ama veri eskidi").

Yani cihaz seviyesi ZATEN doğru çalışıyor. Kapanmayan tek delik gateway'in
KENDİSİNİN susması — bu bekçi yalnızca onu kapatıyor.

NEDEN `UNKNOWN`, `OFFLINE` DEĞİL
--------------------------------
Gateway sustuğunda cihazların gerçekte ne durumda olduğunu **bilmiyoruz**.
"Çevrimdışı" demek, veriye dayanmayan bir iddia olurdu; cihazlar pekâlâ
çalışıyor olabilir ve yalnızca haberi bize ulaşamıyordur. `UNKNOWN` dürüst
olanı söyler.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.device import Device
from app.models.enums import CommunicationStatus
from app.models.gateway import Gateway
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

#: Tarama periyodu (saniye).
DEFAULT_INTERVAL_SEC = 60.0

#: Gateway bu süredir görülmediyse cihazları `UNKNOWN` sayılır.
#:
#: Gateway config-poll'u ~30 saniye; 5 katı pay bırakıyoruz ki tek bir
#: gecikmiş poll turu tüm sahayı bilinmez göstermesin.
DEFAULT_STALE_AFTER_SEC = 180.0


def _interval_sec() -> float:
    try:
        return max(10.0, float(os.getenv("GATEWAY_STALE_CHECK_INTERVAL_SEC", "60")))
    except ValueError:
        return DEFAULT_INTERVAL_SEC


def stale_after_sec() -> float:
    try:
        # Alt sınır 90 sn: config-poll'un ~3 katı. Altına inmek, tek bir
        # gecikmiş poll turunda sahayı bilinmez göstermek demekti.
        return max(90.0, float(os.getenv("GATEWAY_STALE_AFTER_SEC", "180")))
    except ValueError:
        return DEFAULT_STALE_AFTER_SEC


def apply_link_states(db) -> int:
    """Gateway'in bildirdiği CIHAZ BAZINDA link durumunu uygular.

    Bu, veri akışından BAĞIMSIZ tek doğru kaynak: bir arıza göstergesi
    saatlerce sessiz kalabilir (değer değişmezse gateway hiçbir şey
    yayınlamaz) ama linki ayaktadır. Telemetri yaşına bakan hiçbir kontrol
    bu ikisini ayırt edemez; gateway ayırt eder.

    Gateway henüz göndermiyorsa bu fonksiyon hiçbir şey yapmaz — ileriye
    dönük uyumlu.
    """
    from app.models.gateway_health import GatewayHealth
    from app.services.gateway_health_service import device_link_states, smart_counts

    saglik = db.scalars(select(GatewayHealth)).all()
    if not saglik:
        return 0

    degisen = 0
    for satir in saglik:
        durumlar = device_link_states(getattr(satir, "raw_json", None))
        if not durumlar:
            # SAYI BAZLI GUVENLI CIKARIM — cihaz haritasi yoksa (400+ cihazda
            # harita HTTP basligina sigmiyor, gateway yalnizca SAYILARI
            # gonderir) ve gateway "TUM cihazlar koptu" diyorsa
            # (devices_online=0, devices_lost>0), o gateway'in cihazlarini
            # OFFLINE'a cek. Bu, telemetri kuyrugundan BAGIMSIZ calisir:
            # kuyruk tikali ya da purge edilmis olsa bile haberlesme durumu
            # en gec bir tarama periyodunda gercege oturur (sahada iki kez
            # yasandi: comm_lost event'leri kuyrukla birlikte kaybolunca
            # cihazlar ONLINE takili kaldi). Kismi kopmada (bazilari online)
            # hangi cihazin koptugunu sayidan bilemeyiz — o karar telemetri
            # comm_lost event'lerinde kalir.
            kayip = int(getattr(satir, "devices_lost", 0) or 0)
            online = int(getattr(satir, "devices_online", 0) or 0)
            # AKILLI UYKU BU CIKARIMI GECERSIZ KILAR (gateway v1.12.0).
            #
            # Toplu dusurme "hicbir cihaz ayakta degil" varsayimina dayanir.
            # Akilli modda `online=0` bunu ARTIK GOSTERMEZ: cihazlar raporunu
            # gonderip baglantiyi kapatmistir ve gateway onlari `smart_idle`
            # sayar — saglikli uyku. O halde bu satirdaki `lost` bir baska
            # cihaza ait olsa bile, hangi cihazin uyudugunu hangisinin
            # koptugunu SAYIDAN ayirt edemeyiz (kod haritasi yok, zaten bu
            # dalin varlik sebebi o).
            #
            # Emin olmadigimizda dokunmuyoruz: uyuyan cihazi OFFLINE yapmak,
            # gece boyunca tum sahayi yanlislikla kirmiziya boyar. Kismi
            # kopmada karar zaten telemetri comm_lost olaylarinda.
            akilli = smart_counts(getattr(satir, "raw_json", None))
            if kayip > 0 and online == 0 and akilli["smart_idle"] == 0:
                sonuc = db.execute(
                    update(Device)
                    .where(
                        Device.gateway_code == satir.gateway_code,
                        Device.communication_status == CommunicationStatus.ONLINE,
                    )
                    .values(communication_status=CommunicationStatus.OFFLINE)
                )
                n = int(getattr(sonuc, "rowcount", 0) or 0)
                if n:
                    degisen += n
                    logger.warning(
                        "gateway_tum_cihazlar_koptu gateway=%s offline_yapilan=%d "
                        "(saglik kanali sayilarindan — telemetri kuyrugundan bagimsiz)",
                        satir.gateway_code, n,
                    )
            continue
        for kod, durum in durumlar.items():
            hedef = (
                CommunicationStatus.ONLINE if durum == "online"
                else CommunicationStatus.OFFLINE
            )
            sonuc = db.execute(
                update(Device)
                .where(
                    Device.code == kod,
                    Device.gateway_code == satir.gateway_code,
                    Device.communication_status != hedef,
                )
                .values(communication_status=hedef)
            )
            n = int(getattr(sonuc, "rowcount", 0) or 0)
            if n:
                degisen += n
                logger.info(
                    "cihaz_link_durumu device=%s gateway=%s -> %s",
                    kod, satir.gateway_code, hedef.value,
                )
    if degisen:
        db.commit()
    return degisen


def sweep_once(db) -> int:
    """Susmuş gateway'lerin cihazlarını UNKNOWN yapar; cihaz sayısını döner."""
    simdi = datetime.now(timezone.utc)
    sinir = simdi - timedelta(seconds=stale_after_sec())

    bayat = db.scalars(
        select(Gateway).where(
            Gateway.is_active.is_(True),
            Gateway.last_seen_at.is_not(None),
            Gateway.last_seen_at < sinir,
        )
    ).all()
    if not bayat:
        return 0

    toplam = 0
    for gateway in bayat:
        son = gateway.last_seen_at
        if son is not None and son.tzinfo is None:
            son = son.replace(tzinfo=timezone.utc)
        gecen = (simdi - son).total_seconds() if son else 0.0

        # Yalnizca ONLINE olanlar degistiriliyor. Zaten OFFLINE olan bir
        # cihazi UNKNOWN yapmak bilgi KAYBI olurdu: "kopuk oldugunu
        # biliyoruz" demekten "bilmiyoruz" demeye gerilerdik.
        sonuc = db.execute(
            update(Device)
            .where(
                Device.gateway_code == gateway.code,
                Device.communication_status == CommunicationStatus.ONLINE,
            )
            .values(communication_status=CommunicationStatus.UNKNOWN)
        )
        n = int(getattr(sonuc, "rowcount", 0) or 0)
        if n == 0:
            continue
        toplam += n
        logger.warning(
            "gateway_bayat code=%s gecen=%.0fs — %d cihaz UNKNOWN isaretlendi",
            gateway.code, gecen, n,
        )
        record_event(
            db,
            category="gateway",
            event_type="gateway_stale_devices_unknown",
            severity="warning",
            message=(
                f"{gateway.code} {int(gecen)} saniyedir gorulmedi; "
                f"{n} cihazin durumu bilinmiyor"
            ),
            metadata={
                "gateway_code": gateway.code,
                "elapsed_sec": int(gecen),
                "affected_devices": n,
            },
            i18n_key="gateway_stale_devices_unknown",
            i18n_params={"code": gateway.code, "count": n},
        )

    if toplam:
        db.commit()
    return toplam


class GatewayStalenessWatchdog:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="gateway-staleness", daemon=True
        )
        self._thread.start()
        logger.info(
            "gateway_staleness_watchdog_started interval=%.0fs stale_after=%.0fs",
            _interval_sec(), stale_after_sec(),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        ardisik_hata = 0
        while not self._stop.is_set():
            try:
                db = SessionLocal()
                try:
                    # ONCE kesin bilgi (gateway'in bildirdigi link durumu),
                    # SONRA bayatlik tahmini. Ters sirada olsaydi bayatlik
                    # kontrolu, gateway'in az once dogruladigi bir cihazi
                    # UNKNOWN yapabilirdi.
                    apply_link_states(db)
                    sweep_once(db)
                    # Filo seviyesi uyari AYNI THREAD'de: saha kutusu 2
                    # cekirdekli, ayri bir thread + ayri DB oturumu acmanin
                    # karsiligi yok — bu dongu zaten 60 saniyede bir kosuyor
                    # ve `gateway_health` satirlarini okuyor.
                    #
                    # AYRI try/except: uyari kurali CIHAZ DURUMUNU asla
                    # etkilememeli. Buradaki bir hata yukaridaki iki cagriyi
                    # da atlatirdi (ayni tur icinde), yani bir bildirim hatasi
                    # yuzunden cihazlar yesil kalirdi.
                    try:
                        from app.services import gateway_fleet_alarm

                        gateway_fleet_alarm.check_once(db)
                    except Exception:  # noqa: BLE001
                        logger.warning("gateway_fleet_alarm_failed", exc_info=True)
                finally:
                    db.close()
                ardisik_hata = 0
            except Exception:  # noqa: BLE001
                ardisik_hata += 1
                if ardisik_hata in (1, 10) or ardisik_hata % 60 == 0:
                    logger.exception(
                        "gateway_staleness_sweep_failed ardisik=%d", ardisik_hata
                    )
            self._stop.wait(_interval_sec())


_watchdog = GatewayStalenessWatchdog()


def start() -> None:
    _watchdog.start()


def stop() -> None:
    _watchdog.stop()
