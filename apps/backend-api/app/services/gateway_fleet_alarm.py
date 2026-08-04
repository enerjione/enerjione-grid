"""Bir gateway'in cihazlarının büyük kısmı koptuğunda mühendisi uyar.

NEDEN AYRI BİR KURAL
--------------------
Tek tek cihaz kopmaları normaldir: batarya biter, direk bakıma girer, 4G
çeker çekmez. Bunların her biri için uyarı üretmek bildirim merkezini
kullanılamaz hale getirir.

Ama bir gateway'in cihazlarının **yarısından fazlası** aynı anda kopuksa
sorun cihazlarda değildir — anten, saha switch'i, besleme ya da gateway'in
kendi ağıdır. Bu tek bir olaydır ve tek bir uyarı hak eder.

Alarm motoru bu işi yapamaz: `Alarm.device_id` zorunlu, alarmlar cihaz
bazlı. "Cihazların %70'i kopuk" hiçbir cihaza ait değildir — filo
seviyesinde bir gözlemdir.

NEDEN SÜRE ŞARTI VAR
--------------------
Gateway yeniden başladığında ya da toplu bir config yenilemesinde cihazlar
kısa süre `lost` görünür. Eşiği anlık geçen her durumda uyarı göndermek,
her gateway güncellemesinde yanlış alarm demekti. Kesinti **kalıcı**
olduğunda uyarılıyor.

NEDEN "BİR KEZ"
---------------
Tarama periyodik. Her turda göndermek, saha ekibi sorunu çözene kadar
dakikada bir bildirim demekti — gerçek uyarılar bu yığında kaybolur.
Bir bozulma dönemi (episode) boyunca tek bildirim gider; düzelip yeniden
bozulursa yeni bir dönem başlar ve yeniden gider.

BİLİNEN SINIR
-------------
"Ne zamandan beri bozuk" bilgisi süreç içinde tutuluyor; `gateway_health`
tek satır upsert olduğu için geçmişi yok. Backend yeniden başlarsa sayaç
sıfırlanır ve uyarı en fazla bir eşik süresi gecikir. Kalıcı bir kesintide
bu gecikme önemsiz; alternatifi her sweep'te olay tablosuna yazmaktı ve
sahadaki disk bütçesi buna değmez.
"""

from __future__ import annotations

import logging
import os
import time

from sqlalchemy import select

from app.models.enums import UserRole
from app.models.gateway_health import GatewayHealth
from app.models.system_event import SystemEvent
from app.models.user import User
from app.services.event_service import record_event
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)

#: Kopuk cihaz oranı bu değeri AŞARSA bozulma sayılır (0.5 = yarısından fazlası).
DEFAULT_LOST_RATIO = 0.5

#: Bozulmanın uyarı üretmesi için kesintisiz sürmesi gereken süre.
DEFAULT_SUSTAIN_SEC = 300.0

#: Bu sayının altındaki filoda oran anlamsız. 2 cihazlı bir gateway'de tek
#: cihazın kopması %50 eder ve "filo çöktü" uyarısı üretirdi.
MIN_FLEET_SIZE = 4

EVENT_TYPE = "gateway_fleet_degraded_notified"

#: Uyarı bu rollere gider. `ops_manager` ve `operator` dışarıda: sahaya
#: müdahale edecek ya da ağ/donanım kararı verecek roller bunlar.
TARGET_ROLES = (UserRole.ENGINEER, UserRole.INSTALLER)

#: gateway_code -> bozulmanın başladığı monotonic saat.
#: Süreç içi; bkz. modül başlığındaki "BİLİNEN SINIR".
_degraded_since: dict[str, float] = {}


def _lost_ratio() -> float:
    try:
        deger = float(os.getenv("GATEWAY_FLEET_LOST_RATIO", str(DEFAULT_LOST_RATIO)))
    except ValueError:
        return DEFAULT_LOST_RATIO
    # 0 veya 1'in disi anlamsiz: 0 her zaman tetikler, >1 hic tetiklemez.
    if not 0.0 < deger <= 1.0:
        return DEFAULT_LOST_RATIO
    return deger


def _sustain_sec() -> float:
    try:
        return max(60.0, float(os.getenv("GATEWAY_FLEET_SUSTAIN_SEC", str(DEFAULT_SUSTAIN_SEC))))
    except ValueError:
        return DEFAULT_SUSTAIN_SEC


def degraded(total: int | None, lost: int | None, esik: float) -> bool:
    """Bu filo bozulmuş sayılır mı?

    Küçük filolar bilerek dışarıda: 2 cihazlı bir gateway'de tek cihazın
    kopması %50 eder ve "filo çöktü" uyarısı üretirdi.
    """
    if not total or total < MIN_FLEET_SIZE:
        return False
    kayip = int(lost or 0)
    if kayip <= 0:
        return False
    return (kayip / float(total)) > esik


def _already_notified(db, gateway_code: str) -> bool:
    """Bu bozulma dönemi için uyarı gitti mi?

    İşaret süreç içi değil `system_events`'te: backend her yeniden
    başladığında aynı uyarı tekrar giderdi.
    """
    return (
        db.scalar(
            select(SystemEvent.id)
            .where(
                SystemEvent.event_type == EVENT_TYPE,
                SystemEvent.message.like(f"%{gateway_code}%"),
            )
            .order_by(SystemEvent.id.desc())
            .limit(1)
        )
        is not None
    )


def _clear_marker(db, gateway_code: str) -> None:
    """Filo düzeldi: bir sonraki bozulmanın yeniden uyarabilmesi için işareti sil."""
    kayitlar = (
        db.scalars(
            select(SystemEvent).where(
                SystemEvent.event_type == EVENT_TYPE,
                SystemEvent.message.like(f"%{gateway_code}%"),
            )
        )
        .unique()
        .all()
    )
    for k in kayitlar:
        db.delete(k)


def _hedef_kullanicilar(db) -> list[str]:
    # NOT: User modelinde is_active KOLONU YOK — onceki surum var olmayan
    # alana bakip her turda AttributeError firlatiyordu ve filo alarmi HIC
    # calismadi (sahada watchdog dongusunun loglari boyle doluyordu).
    # Kullanici pasiflestirme kavrami gelirse filtre o zaman eklenir.
    roller = [r.value for r in TARGET_ROLES]
    return list(
        db.scalars(select(User.username).where(User.role.in_(roller))).all()
    )


def check_once(db) -> int:
    """Tüm gateway'leri tarar; gönderilen bildirim sayısını döner."""
    esik = _lost_ratio()
    sure = _sustain_sec()
    simdi = time.monotonic()
    gonderilen = 0

    satirlar = db.scalars(select(GatewayHealth)).all()
    gorulen: set[str] = set()

    for satir in satirlar:
        kod = satir.gateway_code
        gorulen.add(kod)
        toplam = satir.devices_total
        kayip = satir.devices_lost

        if not degraded(toplam, kayip, esik):
            # Düzeldi: sayacı ve "gönderildi" işaretini temizle ki bir
            # sonraki bozulma yeniden uyarabilsin.
            if _degraded_since.pop(kod, None) is not None:
                logger.info("gateway_fleet_recovered code=%s", kod)
            _clear_marker(db, kod)
            continue

        baslangic = _degraded_since.setdefault(kod, simdi)
        gecen = simdi - baslangic
        if gecen < sure:
            continue
        if _already_notified(db, kod):
            continue

        oran = int(round(100 * int(kayip or 0) / float(toplam or 1)))
        create_notification_hedefli(
            db,
            gateway_code=kod,
            toplam=int(toplam or 0),
            kayip=int(kayip or 0),
            oran=oran,
            gecen_sn=int(gecen),
        )
        record_event(
            db,
            category="gateway",
            event_type=EVENT_TYPE,
            severity="warning",
            message=(
                f"{kod} filosunun %{oran}'i kopuk ({kayip}/{toplam}), "
                f"{int(gecen)} saniyedir suruyor — uyari gonderildi"
            ),
            metadata={"gateway_code": kod, "lost": int(kayip or 0), "total": int(toplam or 0)},
        )
        gonderilen += 1
        logger.warning(
            "gateway_fleet_degraded code=%s lost=%s/%s gecen=%.0fs",
            kod, kayip, toplam, gecen,
        )

    # Sağlık satırı silinmiş gateway'lerin sayacı süreç içinde kalmasın.
    for kod in list(_degraded_since):
        if kod not in gorulen:
            _degraded_since.pop(kod, None)

    if gonderilen:
        db.commit()
    return gonderilen


def create_notification_hedefli(
    db, *, gateway_code: str, toplam: int, kayip: int, oran: int, gecen_sn: int
) -> None:
    """Uyarıyı hedef rollerdeki kullanıcılara tek tek yazar.

    Yayın (broadcast) YAPILMIYOR: operatörün bu uyarıyla yapabileceği bir şey
    yok, haritada zaten kırmızıyı görüyor. Sahaya müdahale edecek roller
    uyarılıyor.
    """
    baslik = f"Gateway {gateway_code}: cihazların %{oran}'i kopuk"
    govde = (
        f"{gateway_code} gateway'ine bağlı {toplam} cihazın {kayip} tanesiyle "
        f"haberleşme yok ve bu durum {gecen_sn // 60} dakikadır sürüyor.\n\n"
        "Bu kadar cihazın aynı anda kopması genellikle tek tek cihazlardan "
        "değil ortak bir sebepten olur: anten/saha switch'i, besleme ya da "
        "gateway'in ağ bağlantısı.\n\n"
        "Mühendislik > Gateway'ler ekranından gateway sağlığını inceleyin."
    )
    hedefler = _hedef_kullanicilar(db)
    if not hedefler:
        # Hedef rolde aktif kullanıcı yoksa uyarı sessizce kaybolmasın.
        logger.warning(
            "gateway_fleet_degraded_no_recipient code=%s — engineer/installer yok, "
            "yayina dusuluyor", gateway_code,
        )
        create_notification(
            db,
            recipient_username=None,
            title=baslik,
            body=govde,
            category="system",
            severity="warning",
            link="/engineering/gateways",
            metadata={"gateway_code": gateway_code, "lost": kayip, "total": toplam},
        )
        return

    for kullanici in hedefler:
        create_notification(
            db,
            recipient_username=kullanici,
            title=baslik,
            body=govde,
            category="system",
            severity="warning",
            link="/engineering/gateways",
            metadata={"gateway_code": gateway_code, "lost": kayip, "total": toplam},
        )
