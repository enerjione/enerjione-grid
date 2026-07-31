from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GatewayHealth(Base):
    """Gateway'in kendi bildirdigi saglik durumu (heartbeat).

    NEDEN AYRI TABLO — `gateways`'e kolon EKLEMEDIK:
      `/gateways/{code}/pending` ucu saniyede bir cagriliyor ve zaten her
      cagrida `gateways.last_seen_at` icin bir UPDATE + COMMIT atiyor. Saglik
      JSON'unu da o satira yazmak, saniyede bir guncellenen SICAK bir satiri
      daha da sisirir: her UPDATE yeni bir satir surumu uretir (Postgres MVCC),
      autovacuum'un ana musterisi olur ve `gateways` uzerindeki index'ler
      surekli yeniden yazilir.
      Ayri tabloda gateway basina TEK satir tutulur ve upsert edilir; sicak
      yol ayrisir.

    NEDEN GATEWAY BILDIRIYOR — backend sormuyor:
      Saha gateway'i NAT arkasinda; `WORKER_HEALTH_HOST` varsayilani
      127.0.0.1 ve compose portu localhost'a bagliyor. Backend gateway'in
      `/health` ucuna ULASAMAZ. Bu yuzden bilgi, gateway'in ZATEN attigi
      komut-poll istegine binerek gelir (ek istek maliyeti yok).

    `raw_json`: gateway'in gonderdigi govdenin tamami. Sema burada bilerek
    GEVSEK: gateway surumleri farkli alanlar gonderebilir ve backend'in yeni
    bir alan yuzunden veri kaybetmesini istemiyoruz. Sorgulanan alanlar ayrica
    kolon olarak acildi.
    """

    __tablename__ = "gateway_health"

    # Gateway kodu birincil anahtar: gateway basina TEK satir, upsert edilir.
    gateway_code: Mapped[str] = mapped_column(String(50), primary_key=True)

    # Gateway'in kendi degerlendirmesi: starting | ok | degraded | unhealthy
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    # Virgulle ayrilmis sorun kodlari (orn. "outbox_near_capacity,poll_loop_stalled").
    # Liste yerine duz metin: sorgulanmiyor, yalnizca gosteriliyor.
    issues: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    outbox_pending: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outbox_dead_letter: Mapped[int | None] = mapped_column(Integer, nullable=True)

    devices_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    devices_online: Mapped[int | None] = mapped_column(Integer, nullable=True)
    devices_recovering: Mapped[int | None] = mapped_column(Integer, nullable=True)
    devices_lost: Mapped[int | None] = mapped_column(Integer, nullable=True)

    uptime_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gateway_version: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Ham govde — ileride eklenen alanlar kaybolmasin.
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # BACKEND saati. Gateway saatine GUVENILMEZ: saha cihazlarinda RTC pili
    # bitince saat 2000-01-01'e donebiliyor (ayni kok neden B2'de de var).
    # "Ne zaman haber aldik" sorusunun cevabi burasi olmali.
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
