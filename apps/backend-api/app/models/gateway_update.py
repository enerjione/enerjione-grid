from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GatewayUpdate(Base):
    """Gateway yazilim guncellemesinin BACKEND tarafindaki durumu.

    NEDEN AYRI TABLO — `gateways`'e kolon EKLENMEDI:
      `/gateways/{code}/pending` ucu saniyede bir cagriliyor ve her cagrida
      `gateways.last_seen_at` icin UPDATE atiyor. O satir Postgres MVCC
      acisindan SICAK; her UPDATE satirin TAMAMINI yeniden yaziyor.
      Guncelleme durumu nadiren degisen ama genis (iki imaj referansi + hata
      metni) bir veri — sicak satiri genisletmek her saniye daha fazla byte
      yazmak demekti. `gateway_health` ayni gerekceyle ayrilmisti; ayni
      deseni surduruyoruz.

    NEDEN `system_events` YETMEDI:
      Olay kaydi DENETIM icindir ve orada kaliyor (bkz. `gateway_update_*`
      olaylari). Ama "bu gateway SU AN hangi durumda" sorusu, gateway
      listesindeki her satir icin cevaplanmali. Olay kaydindan turetmek her
      liste isteginde gateway basina bir "son olayi bul + durumu yeniden
      insa et" taramasi demekti; ustelik olay kaydi retention'a tabi
      (2 yil FIFO) ve budandiginda durum SESSIZCE kaybolurdu.

    NEDEN `gateway_health`e EKLENMEDI:
      O satirin sahibi GATEWAY'dir (kendi bildirdigi olculer, upsert ile
      eziliyor). Buradaki veri BACKEND'in kendi kaydidir — kim, ne zaman,
      hangi surumden hangisine gecmeyi istedi. Iki farkli sahipligi tek
      satirda toplamak, gateway'in bir heartbeat'iyle denetim izini
      ezebilirdi.

    Gateway basina TEK satir tutulur ve upsert edilir: "son guncelleme"
    sorusunun cevabi budur. Gecmisin tamami olay kaydinda.
    """

    __tablename__ = "gateway_updates"

    # Gateway kodu birincil anahtar: gateway basina TEK satir.
    #
    # FK YOK — bilincli. Gateway silinip ayni kodla yeniden olusturulabilir;
    # o durumda eski guncelleme kaydinin da gitmesi DOGRU olurdu ama FK
    # cascade'i, gateway silme yolunu (zaten karmasik: cihazlar, setler,
    # telemetri) bir tablo daha ile baglar. `gateway_health` de ayni sebeple
    # FK'siz; temizlik ayni yerde yapilir.
    gateway_code: Mapped[str] = mapped_column(String(50), primary_key=True)

    #: idle | preparing | requested | running | succeeded | failed | rolled_back
    #:
    #: Durum makinesi BILEREK duz: ajan zaten kendi asamalarini
    #: (`pull`/`up`) `status.json` icinde bildiriyor ve arayuz onu canli
    #: gosteriyor. Buradaki durum "backend ne istedi ve sonuc ne oldu"
    #: sorusunu cevaplar; ajanin asamalarini KOPYALAMAZ.
    status: Mapped[str] = mapped_column(String(20), default="idle", index=True)

    #: Guncelleme oncesi calisan surum ve TAM imaj referansi.
    #: `from_image` GERI ALMANIN TEK KAYNAGIDIR: yalnizca bu sistemin
    #: gercekten calistirdigi bir imaja donulebilir (bkz. update service).
    from_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    from_image: Mapped[str | None] = mapped_column(String(400), nullable=True)

    #: Hedef surum ve referans. `to_image` HAZIRLIK adiminda digest'e
    #: sabitlenir (`repo@sha256:...`); boylece apply aninda etiketin baska
    #: bir imaja kaymis olmasi mumkun degildir.
    to_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_image: Mapped[str | None] = mapped_column(String(400), nullable=True)

    #: Hazirlikta cozulen manifest digest'i (`sha256:...`).
    #: Dogrulamayi BIZ yapmiyoruz: digest'e sabitlenmis referansta uyusmazlik
    #: olursa `docker pull` zaten reddeder. Alan, operatorun ekranda NEYIN
    #: kuruldugunu gormesi ve denetim izi icin saklanir.
    expected_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)

    #: Ajana yazilan istegin kimligi — `status.json` ile korelasyon.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Basarisizlik sebebi (ajan ciktisindan, maskeli ve kirpilmis).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Bu islem bir geri alma miydi? Denetimde ve arayuzde ayri gosterilir:
    #: "1.13.0 -> 1.12.0" bir yukseltme degil, bilincli bir geri donustur.
    is_rollback: Mapped[bool] = mapped_column(Boolean, default=False)
