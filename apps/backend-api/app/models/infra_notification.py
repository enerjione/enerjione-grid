"""Altyapi bildirimlerinin soguma (cooldown) defteri.

NE ISE YARAR
------------
`system_events` tablosuna yazilan KRITIK altyapi olaylari operatore
bildirilir (bkz. services/infra_notification_service.py). Ama olay uretimi
ile bildirim gonderimi AYRI SIKLIKLARDA olmali:

    disk_guard her 5 dakikada bir `disk_guard_emergency` yazabilir
    telemetri tamponu tasmasi 15 saniyede bir yeni kayit uretebilir

Bunlarin her biri icin SMS gondermek, gercek uyarilari kullanilamaz hale
getirir. Bu tablo "bu (olay tipi + kaynak) icin en son ne zaman bildirim
gitti" sorusunu KALICI olarak cevaplar.

NEDEN SUREC ICINDE DEGIL DB'DE
------------------------------
Bellekte tutulsaydi backend her yeniden baslatildiginda soguma sifirlanirdi.
Kalici bir disk arizasinda backend'in birkac kez yeniden baslamasi olagan;
her acilista yeni bir SMS gitmesi tam da onlemek istedigimiz sey.

DENETIM KAYDI ILE KARISTIRILMAMALI
----------------------------------
`system_events` HICBIR SEKILDE bastirilmaz — 100 olay olduysa 100 satir
yazilir ve denetim izi eksiksiz kalir. Bastirilan yalnizca GONDERIM'dir.
`suppressed_count` o pencerede kac olayin tek bir bildirime katlandigini
sayar; bildirim metninde operatore bu sayi gosterilir.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InfraNotificationState(Base):
    __tablename__ = "infra_notification_state"

    __table_args__ = (
        # Sogumanin anahtari. `resource_key` bos string olabilir (kaynak
        # ayrimi olmayan olaylar) ama NULL OLAMAZ: Postgres'te NULL'lar
        # birbirine esit sayilmaz ve UNIQUE kisiti sessizce islevini
        # kaybederdi — ayni olay icin sonsuz sayida satir olusurdu.
        UniqueConstraint(
            "event_type", "resource_key", name="uq_infra_notification_key"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: `system_events.event_type` ile AYNI deger.
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    #: Ayni olay tipinin birbirinden BAGIMSIZ ornekleri. Ornegin telemetri
    #: hatti (`prio` / `bulk`) ya da disk yolu. Bos string = ayrim yok.
    #: Boylece oncelikli hattin tasmasi, toplu hattin sogumasi yuzunden
    #: bastirilmaz.
    resource_key: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", server_default=""
    )

    last_notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    #: FILIGRAN (watermark): bu kovada HESABA KATILMIS en yuksek
    #: `system_events.id`. Yalnizca bundan yeni olaylar degerlendirilir.
    #:
    #: NEDEN FILIGRAN: geriye bakis penceresi (60 dk) tarama araligindan
    #: (60 sn) cok genis; ayni olay dizisi her turda yeniden gorulur.
    #: Filigran olmadan `suppressed_count` her turda yeniden sayardi ve
    #: operatore gosterilen "bu uyari N kez olustu" degeri anlamsizlasirdi.
    #: Ayrica soguma dolduktan sonra YENI OLAY OLMADAN eski bir olay
    #: yeniden bildirim uretirdi.
    last_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Soguma penceresinde bastirilan (gonderilmeyen) olay sayisi. Bir
    #: sonraki bildirim gittiginde sifirlanir ve metinde raporlanir.
    suppressed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
