"""WebSocket el sikisma biletleri — SURECLER ARASI PAYLASILIR.

NEDEN TABLO, NEDEN BELLEK DEGIL
-------------------------------
Bilet `/auth/ws-ticket` ile uretilir, hemen ardindan WS upgrade istegiyle
tuketilir. Bu IKI AYRI TCP BAGLANTISIDIR: uvicorn birden fazla surecle
kosarken (`E1_API_WORKERS>1`) istekler surecler arasinda dagitilir ve bilet
A surecinde uretilip B surecine dusebilir.

Bilet surec-ici bir dict'te tutuldugunda B surecinde BULUNAMIYOR ve baglanti
`1008 invalid_credentials` ile kapaniyordu. Istemci tarafindaki token
fallback'i yalnizca bilet ALINAMAZSA devreye giriyor (bkz.
`useLiveValuesSocket.ts`), 1008 kapanisi o dala girmedigi icin sonuc
backoff'lu yeniden baglanma dongusuydu: her denemede yeni bilet, yine
rastgele surec. 4 surecte her denemenin tutma olasiligi ~%25 — canli
degerler ve harita kalici olarak kararsiz hale geliyordu.

`E1_API_WORKERS` varsayilani 1 ama `.env.example` olceklenme icin acikca 4
oneriyor; yani bu, ayar buyutuldugu anda patlayan bir mayindi.

TEK KULLANIM KORUNUYOR
----------------------
Bilet URL query'sinde tasindigi icin (nginx access log, Referer, tarayici
gecmisi) tekrar oynatmaya karsi tek-kullanim sarti GERCEK bir korumadir ve
burada da korunur: tuketim `DELETE ... rowcount` ile yapilir, yarisi yalnizca
BIR surec kazanir. Durumsuz imzali bir bilete gecmek bu ozelligi sessizce
kaybettirirdi.

Schema:
  - ticket: PK, tek kullanimlik rastgele deger (uuid4 hex)
  - username: bileti alan kullanici
  - jti: bileti ureten oturumun JWT kimligi. WS endpoint'i baglanti boyunca
    `UserSession.revoked_at` kontrolunu bunun uzerinden yapar. NULL = eski
    cagri sekli, iptal kontrolu yapilamaz.
  - expires_at: 30 sn TTL. Gecmis satirlar tuketimde reddedilir; temizlik
    bilet uretiminde firsatci olarak yapilir (bkz. auth_service).
"""
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WsTicket(Base):
    __tablename__ = "ws_tickets"

    ticket: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(150), nullable=False)
    jti: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Suresi dolmus satirlarin toplu silinmesi bu indeks uzerinden gider.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
