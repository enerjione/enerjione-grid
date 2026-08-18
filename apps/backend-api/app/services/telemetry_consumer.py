"""Telemetri kalicilastirma tuketicisi — NATS JetStream uzerinden.

NEREDE KOSAR
------------
HTTP surecinde DEGIL. `leader` bu isi yalnizca arka plan rolundeki surecte
acar (bkz. core/service_role.py) ve varsayilan topolojide o surec
`e1-grid-backend-worker` container'idir; `e1-grid-backend-api` `SERVICE_ROLE=api`
ile kosar ve HICBIR JetStream tuketicisi acmaz — yalnizca HTTP alir ve
gateway'den geleni `outbox -> jetstream_bus` uzerinden akisa YAYINLAR.

HANGI AKISTAN OKUR
------------------
Hedef: tag-engine CIKISI (`e1.telemetry.normalized.>`). Boylece arsivdeki
deger ile alarm-service / iec104-outbound / modbus-outbound'un gordugu deger
AYNI normalizasyondan gecer. Onceden bu tuketici ham akisi (`e1.telemetry.raw.>`)
okuyordu ve iki farkli karar zinciri olusuyordu.

Gecis TEK ADIMDA YAPILMAZ — eski RAW durable'inda HENUZ YAZILMAMIS olcumler
birikmis olabilir (olcum: 6.5M). Bu yuzden iki fazli:

  FAZ 1 (drenaj)  : eski durable AYNEN devralinir (ayni isim, ayni stream,
                    ayni konum). JetStream mesajlari kaldigi yerden verir —
                    tek mesaj atlanmaz, tek mesaj tekrarlanmaz.
  FAZ 2 (gecis)   : RAW durable'inda ISLENMEMIS TEK MESAJ KALMAYINCA
                    (`_drenaj_bitti_mi` — sunucunun `num_pending` VE
                    `num_ack_pending` sayaclari) NORMALIZED durable'i
                    olusturulur, biraz geri sarilarak (bkz.
                    telemetry_persist_cutover_overlap_sec); sonra eski
                    durable SILINIR.

Faz gecisi kendiliginden olur (`TELEMETRY_PERSIST_SOURCE=auto`, varsayilan);
operator adimina bagli DEGILDIR.

CIFT KAYIT NEDEN OLMAZ
----------------------
  * Tek surec, tek fetch dongusu: iki abonelik AYNI ANDA acik kalmaz.
  * Kumede tek lider (Postgres advisory lock) — guncelleme sirasinda eski
    surec kilidi birakmadan yeni surec HIC tuketmez.
  * Geri sarilan pencerede tekrar gelen olcumler `processed_messages`
    defteriyle elenir; `CONSUMER_NAME` bilerek DEGISTIRILMEDI, aksi halde
    defter sifirlanir ve gecis aninda her olcum ikinci kez yazilirdi.

IKI HAT — ONCELIKLI VE TOPLU
----------------------------
NORMALIZED akisi TEK bir durable ile cekildigi surece, yuz binlerce ANALOG
olcumu ile bir ARIZA BAYRAGI ayni sirada bekler. Ariza gostergesinde gecikme
konfor degil DOGRULUK meselesidir: 10 dakika gec gorulen ariza
operasyonel olarak KACIRILMISTIR. Ustelik ekranin gordugu her sey
(`telemetry_latest` + WS yayini) bu dongunun ARKASINDADIR.

tag-engine artik konuya bir sinif token'i ekliyor
(`e1.telemetry.normalized.<gw>.<prio|bulk>`) ve burada her sinif KENDI
durable'i ile, KENDI fetch dongusunde cekilir:

  ONCELIKLI (`prio`)  dijital/durum sinyalleri, ariza bayraklari/yonu,
                      sayaclar, Class 1 analoglar (`fault_current`,
                      `fault_duration`), katalogda olmayan her sey.
                      Filtresi SINIFSIZ eski konuyu da tasir — guncelleme
                      sirasinda henuz yenilenmemis bir tag-engine'in bastigi
                      4 token'li mesajlar sessizce kaybolmasin.
  TOPLU (`bulk`)      yalnizca surekli taranan Class 2 analog olcumler.

IZOLASYON NEREDEN GELIYOR: her hat AYRI bir JetStream durable'idir; teslim
sirasi, `num_pending` ve ack muhasebesi hat basinadir. Toplu hattaki
birikim oncelikli hattin ONUNE GECEMEZ. DB isi de hat basina
`asyncio.to_thread` ile ayri calisir; toplu hattin commit'i saniyelerce
assa bile oncelikli hat ayni anda fetch/commit/ack yapmaya devam eder.

CIFT KAYIT: iki hat AYRIK konu kumeleri tasir, ayni mesaj ikisine birden
DUSMEZ. Ustelik `processed_messages` defteri iki hatta da AYNI
`CONSUMER_NAME` ile tutulur ve `uq_processed_message_consumer_msg` bilesik
UNIQUE index'i son savunmadir: bir yaris olsa bile DB ikinci yazimi
reddeder, batch geri alinir ve yeniden teslimde dedup yutar.

Asyncio loop ayri bir thread'de calisir; NATS reconnect ve durable consumer
sayesinde process restart'inda kaldigi yerden devam eder.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.device import Device
from app.models.processed_message import ProcessedMessage
from app.schemas.telemetry import TelemetryIn
from app.services import unknown_device_quarantine as quarantine
from app.services.ws_broadcaster import broadcaster as ws_broadcaster

logger = logging.getLogger(__name__)

# IDEMPOTENCY DEFTERININ ANAHTARI — `processed_messages.consumer_name`.
#
# BU DEGER DEGISTIRILEMEZ. NATS durable adiyla ILGISI YOK; kalicilastirma
# hangi akistan (raw/normalized) beslenirse beslensin, hangi container'da
# kosarsa kossun ayni kalmak ZORUNDA. Degistirilirse defter bir anda BOS
# gorunur ve gecis penceresinde geri sarilan her olcum IKINCI KEZ yazilir.
CONSUMER_NAME = "backend-api.telemetry-persister"

# Kalicilastirmanin okudugu akis.
KAYNAK_RAW = "raw"
KAYNAK_NORMALIZED = "normalized"
# `telemetry_persist_source` icin gecerli degerler ("auto" = otomatik gecis).
KAYNAK_AUTO = "auto"

# ----- Hat siniflari --------------------------------------------------------
# Bu degerler OLAY KAYDINA yazilir (`sinif` alani) ve tag-engine'in konuya
# koydugu token ile AYNI olmak zorunda (bkz. tag_engine/katalog.py).
SINIF_ONCELIKLI = "prio"
SINIF_TOPLU = "bulk"
#: Tek hat donemi — ayni durable her iki sinifi da tasir (RAW fazi ve
#: hat ayrimindan onceki NORMALIZED fazi). Olay kaydinda "hangi sinif dustu"
#: sorusunun durusu bilinmedigi icin bu deger yazilir.
SINIF_KARMA = "karma"

_stop_event = threading.Event()
_thread: threading.Thread | None = None


class Hat:
    """Tek bir tuketim hatti: sinif + stream + durable + acik abonelik."""

    __slots__ = ("sinif", "stream", "durable", "psub")

    def __init__(self, *, sinif: str, stream: str, durable: str, psub: Any) -> None:
        self.sinif = sinif
        self.stream = stream
        self.durable = durable
        self.psub = psub

    async def kapat(self) -> None:
        try:
            await self.psub.unsubscribe()
        except Exception:  # noqa: BLE001
            logger.debug("telemetry_persist_unsubscribe_failed hat=%s", self.sinif)

    def __repr__(self) -> str:  # pragma: no cover - teshis
        return f"Hat({self.sinif}, {self.durable})"


def _kaynak_tercihi() -> str:
    """`TELEMETRY_PERSIST_SOURCE` — taninmayan deger `auto`ya duser."""
    ham = (settings.telemetry_persist_source or "").strip().lower()
    if ham in (KAYNAK_AUTO, KAYNAK_RAW, KAYNAK_NORMALIZED):
        return ham
    if ham:
        logger.warning(
            "telemetry_persist_source_unknown deger=%r — `auto` varsayildi "
            "(gecerli: auto, raw, normalized)",
            ham,
        )
    return KAYNAK_AUTO


def _cutover_geri_sarma_sec() -> int:
    """Gecis aninda NORMALIZED akisinda ne kadar geri sarilacak (saniye).

    TAVAN `processed_messages` defterinin OMRUDUR ve burada ZORLANIR.
    Defterden daha uzun geri sarmak, dedup kaydi ZATEN SILINMIS olcumleri
    tekrar getirmek demektir — yani tam da onlemeye calistigimiz cift kayit.
    Bu yuzden yapilandirma degeri sessizce kirpilmaz, kirpilir VE loglanir.
    """
    tavan = max(0, int(settings.processed_messages_retention_hours) * 3600)
    istenen = max(0, int(settings.telemetry_persist_cutover_overlap_sec))
    if istenen > tavan:
        logger.warning(
            "telemetry_persist_cutover_overlap_clamped istenen=%ds tavan=%ds — "
            "geri sarma penceresi dedup defterinin (processed_messages) omrunu "
            "asamaz, yoksa ayni olcum IKINCI KEZ yazilirdi",
            istenen,
            tavan,
        )
        return tavan
    return istenen


# --------------------------------------------------------------------------
# ISLEM TELEMETRISI — "tuketici yetisiyor mu?"
#
# NEDEN GEREKLI: telemetri akisi NATS stream'inde tamponlanir ve stream
# `discard=old` ile calisir. Yani tuketici gelis hizinin GERISINE duserse
# tampon dolar ve EN ESKI mesajlar SESSIZCE dusurulur. Ekranda hata yok,
# alarm yok; sadece bazi okumalar hic gelmemis olur.
#
# Bu sessizlik bilincli bir takas (sistem durmasin diye) ama gorunurluk
# olmadan tehlikeli. Asagidaki sayaclar "yetisiyor muyuz" sorusunun tek
# cevabidir.
#
# BACKLOG BEDAVA OLCULUR: JetStream her mesajin metadata'sinda `num_pending`
# tasir — tuketicinin ONUNDE bekleyen mesaj sayisi. Ayrica bir consumer_info
# cagrisi yapmaya gerek yok.
# --------------------------------------------------------------------------

_stats_lock = threading.Lock()
_stats: dict[str, Any] = {
    "running": False,
    "connected": False,
    # Hangi akistan besleniyor: "raw" (gecis oncesi drenaj) | "normalized"
    # (hedef mimari) | None (henuz baglanmadi). Sistem Durumu bunu gosterir;
    # aksi halde gecisin tamamlanip tamamlanmadigi ancak NATS CLI ile
    # anlasilirdi.
    "source": None,
    "last_fetch_at": None,        # ISO string
    "last_batch_size": 0,
    "last_batch_duration_sec": 0.0,
    "backlog": None,              # num_pending — tuketicinin onunde bekleyen
    "processed_total": 0,
    "bad_total": 0,
    "reconnects": 0,
    "last_error": None,
}
# Throughput icin kayan pencere: (zaman, islenen_adet) ciftleri.
_throughput_window: list[tuple[float, int]] = []
_THROUGHPUT_WINDOW_SEC = 60.0

# HAT BASINA olcum. Ust seviye `_stats` sozlugu (yukarida) GERIYE UYUMLU
# kalir — `system_status.py` onu okuyor — ama artik TOPLAM/EN KOTU degerdir.
# Hangi hattin geride oldugunu ayirt etmek icin burasi kullanilir; sistemin
# tamami iyi gorunurken TEK BIR hattin bogulmasi tam da yakalanmasi gereken
# durum.
_hat_stats: dict[str, dict[str, Any]] = {}


def _hat_kaydi(sinif: str) -> dict[str, Any]:
    kayit = _hat_stats.get(sinif)
    if kayit is None:
        kayit = {
            "backlog": None,
            "processed_total": 0,
            "bad_total": 0,
            "dropped_total": 0,
            "last_batch_duration_sec": 0.0,
            "last_fetch_at": None,
        }
        _hat_stats[sinif] = kayit
    return kayit


def _stats_update(**kwargs: Any) -> None:
    with _stats_lock:
        _stats.update(kwargs)


def _hat_backlog_yaz(sinif: str, backlog: int | None) -> None:
    with _stats_lock:
        _hat_kaydi(sinif)["backlog"] = backlog
        _stats["backlog"] = _toplam_backlog_kilitsiz()


def _toplam_backlog_kilitsiz() -> int | None:
    """Ust seviye `backlog` = hatlarin EN BUYUGU.

    Toplam DEGIL, EN KOTU: iki hattin toplami "ortalama iyi" gorunen bir sayi
    uretebilirdi; oysa esik uyarisinin sormasi gereken soru "herhangi bir hat
    bogulyor mu".
    """
    degerler = [k["backlog"] for k in _hat_stats.values() if k["backlog"] is not None]
    return max(degerler) if degerler else None


def _stats_record_batch(
    *, sinif: str, size: int, duration: float, backlog: int | None, bad: int
) -> None:
    now = _time.monotonic()
    with _stats_lock:
        kayit = _hat_kaydi(sinif)
        simdi_iso = datetime.now(timezone.utc).isoformat()
        kayit["last_fetch_at"] = simdi_iso
        kayit["last_batch_duration_sec"] = round(duration, 4)
        kayit["processed_total"] += size
        kayit["bad_total"] += bad
        if backlog is not None:
            kayit["backlog"] = backlog
        _stats["last_fetch_at"] = simdi_iso
        _stats["last_batch_size"] = size
        _stats["last_batch_duration_sec"] = round(duration, 4)
        _stats["processed_total"] += size
        _stats["bad_total"] += bad
        _stats["backlog"] = _toplam_backlog_kilitsiz()
        _throughput_window.append((now, size))
        cutoff = now - _THROUGHPUT_WINDOW_SEC
        while _throughput_window and _throughput_window[0][0] < cutoff:
            _throughput_window.pop(0)


# "Henuz hic uyarilmadi" = None. 0.0 DEGIL — bilincli.
#
# `time.monotonic()` sabit bir baslangic noktasi vermez; Linux'ta makine
# acilisindan beri gecen suredir. Baslangic degeri 0.0 olsaydi acilistan
# sonraki ILK `telemetry_backlog_warn_interval_sec` saniye boyunca
# `now - 0.0 < interval` cikar ve ILK uyari bastirilirdi — hem de tam
# backlog'un en yuksek oldugu an, yeniden baslatmanin hemen ardindan.
# (Windows'ta uptime buyuk oldugu icin bu davranis gorunmuyordu; hatayi
# Linux CI ortaya cikardi.)
_last_backlog_warn_at: dict[str, float] = {}


def _olay_yaz(
    *, event_type: str, severity: str, message: str, metadata: dict[str, Any]
) -> None:
    """Kisa omurlu bir session ile denetim kaydi yazar; hata akisi bozmaz."""
    try:
        from app.services.event_service import record_event

        db = SessionLocal()
        try:
            record_event(
                db,
                category="telemetry",
                event_type=event_type,
                severity=severity,
                message=message,
                metadata=metadata,
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.debug("telemetry_olay_yazilamadi type=%s", event_type, exc_info=True)


def _warn_if_backlog_high(sinif: str, backlog: int | None) -> None:
    """Backlog esigi asilirsa loglar ve denetim kaydina yazar — HAT BASINA.

    Bu, `discard=old` tercihinin karsiligidir: tampon tasarsa mesajlar
    SESSIZCE dusuruluyor, dolayisiyla tasmaya YAKLASILDIGINI haber verecek
    bir sinyal sart. Esik, stream tavaninin cok altinda tutulur ki operatorun
    mudahale etmesi icin zaman kalsin.

    HAT BASINA olmasi sart: toplu hattin geride kalmasi beklenen ve kabul
    edilebilir bir durumdur, oncelikli hattin geride kalmasi DEGILDIR. Tek
    bir birlesik sayac ikisini ayirt edemez; oncelikli hat bogulurken toplu
    hattin gurultusune karisir.

    Olay kaydi rate-limit'lidir; backlog saatlerce yuksek kalsa bile
    `system_events` tablosunu doldurmaz.
    """
    if backlog is None or backlog < settings.telemetry_backlog_warn_threshold:
        return
    now = _time.monotonic()
    # ILK uyari her zaman gecer; rate-limit yalnizca IKINCIDEN itibaren isler.
    # Mutlak degeri karsilastirmak yerine "daha once uyardik mi" sorusunu
    # sormak, monotonic()'in baslangic noktasindan bagimsiz kilar.
    onceki = _last_backlog_warn_at.get(sinif)
    if onceki is not None and now - onceki < settings.telemetry_backlog_warn_interval_sec:
        return
    _last_backlog_warn_at[sinif] = now
    snapshot = get_stats()
    hat_ozeti = snapshot.get("hatlar", {}).get(sinif, {})
    logger.warning(
        "telemetry_backlog_high sinif=%s backlog=%d threshold=%d throughput=%.1f msg/s "
        "(tuketici gelis hizinin gerisinde — tampon tasarsa VERI KAYBI baslar)",
        sinif,
        backlog,
        settings.telemetry_backlog_warn_threshold,
        snapshot.get("throughput_msgs_per_sec", 0.0),
    )
    _olay_yaz(
        event_type="telemetry_backlog_high",
        # Oncelikli hattin geride kalmasi ariza gorunurlugunu dogrudan
        # tehdit eder; toplu hat icin ayni birikim yalnizca arsiv gecikmesi.
        severity="critical" if sinif == SINIF_ONCELIKLI else "warning",
        message=(
            f"Telemetri tuketicisi geride ({_HAT_ADI.get(sinif, sinif)}): "
            f"{backlog} mesaj bekliyor "
            f"({snapshot.get('throughput_msgs_per_sec', 0.0)} msg/sn isleniyor)"
        ),
        metadata={
            "sinif": sinif,
            "backlog": backlog,
            "threshold": settings.telemetry_backlog_warn_threshold,
            "throughput_msgs_per_sec": snapshot.get("throughput_msgs_per_sec"),
            "last_batch_duration_sec": hat_ozeti.get("last_batch_duration_sec"),
        },
    )


#: Olay kaydinda operatorun gordugu Turkce hat adi.
_HAT_ADI = {
    SINIF_ONCELIKLI: "oncelikli hat / dijital-durum",
    SINIF_TOPLU: "toplu hat / analog",
    SINIF_KARMA: "tek hat (ayrim yapilmamis)",
}

#: Hat basina "buraya kadarki kayip ZATEN bildirildi" isareti (stream sirasi).
_kayip_isareti: dict[str, int] = {}
_son_kayip_denetimi: dict[str, float] = {}
#: Kayip denetimi araligi. Ucuz bir cagri (`stream_info` + `consumer_info`)
#: ama saniyede binlerce batch'te her turda yapmak gereksiz yuk.
_KAYIP_DENETIM_ARALIK_SEC = 15.0


async def _dusen_mesaj_denetimi(js, hat: Hat) -> None:  # noqa: ANN001
    """Bu hattin mesajlari, hat onlari GORMEDEN dusuruldu mu?

    NEDEN VAR — "tampon tasmasi SESSIZ OLMAYACAK" sartinin karsiligi.
    Stream `discard=OLD` ile calisir: tavana carpinca EN ESKI mesajlari atar
    ve bunu JetStream tuketiciye HABER VERMEZ. Bugune kadar yalnizca backlog
    ESIGI izleniyordu; yani "tasmaya yaklasiyoruz" goruluyor ama "TASTI ve su
    kadar mesaj gitti" HIC gorulmuyordu. Ariza sinifi dustuyse bu asla sessiz
    kalmamali.

    TESPIT — iki sayacin karsilastirilmasi:
        stream.state.first_seq          stream'de duran EN ESKI mesajin sirasi
        consumer.ack_floor.stream_seq   bu hattin ACK ettigi EN SON sira
    Hat yetisiyorsa ack_floor daima first_seq'in ONUNDEDIR. Arkasina duserse
    aradaki pencere, hat onlari HIC GORMEDEN silinmis mesajlardir.

    SAYININ ANLAMI BIR UST SINIRDIR: stream sirasi tum hatlarin mesajlarini
    birlikte numaralandirir, dolayisiyla pencere digger hattin mesajlarini da
    icerir. Ama "0" TAM VE KESINDIR — hicbir sey kaybolmadi demektir, ki
    oncelikli hat icin kanitlanmasi gereken tam olarak budur. Sifirdan buyuk
    her deger operatore soylenmeli; hangi hat oldugu olay kaydinda yazar.
    """
    simdi = _time.monotonic()
    onceki = _son_kayip_denetimi.get(hat.sinif)
    if onceki is not None and simdi - onceki < _KAYIP_DENETIM_ARALIK_SEC:
        return
    _son_kayip_denetimi[hat.sinif] = simdi
    try:
        stream_bilgi = await js.stream_info(hat.stream)
        tuketici_bilgi = await js.consumer_info(hat.stream, hat.durable)
    except Exception:  # noqa: BLE001
        # Bilgi okunamadi: KARAR VERME. Yanlis pozitif bir "veri kaybi"
        # olayi, gercek olani da inandiriciliktan dusururdu.
        logger.debug("telemetry_kayip_denetimi_basarisiz hat=%s", hat.sinif, exc_info=True)
        return

    ilk_sira = int(getattr(getattr(stream_bilgi, "state", None), "first_seq", 0) or 0)
    ack_tabani = int(
        getattr(getattr(tuketici_bilgi, "ack_floor", None), "stream_seq", 0) or 0
    )
    if ilk_sira <= 0:
        return
    ust = ilk_sira - 1
    alt = max(ack_tabani, _kayip_isareti.get(hat.sinif, 0))
    yeni_kayip = ust - alt
    if yeni_kayip <= 0:
        return
    _kayip_isareti[hat.sinif] = ust
    with _stats_lock:
        _hat_kaydi(hat.sinif)["dropped_total"] += yeni_kayip
    logger.error(
        "telemetry_tampon_tasti sinif=%s dusen<=%d stream=%s durable=%s "
        "ilk_sira=%d ack_tabani=%d — mesajlar bu hat ISLEMEDEN silindi",
        hat.sinif,
        yeni_kayip,
        hat.stream,
        hat.durable,
        ilk_sira,
        ack_tabani,
    )
    ariza_hatti = hat.sinif in (SINIF_ONCELIKLI, SINIF_KARMA)
    await asyncio.to_thread(
        _olay_yaz,
        event_type="telemetry_tampon_tasmasi",
        # Ariza belirleyen sinif dustuyse bu bir VERI KAYBI olayidir, uyari
        # degil: kacan bir ariza gecisi geri getirilemez.
        severity="critical" if ariza_hatti else "warning",
        message=(
            f"Telemetri tamponu tasti — {_HAT_ADI.get(hat.sinif, hat.sinif)}: "
            f"en fazla {yeni_kayip} mesaj islenmeden dusuruldu"
            + (
                " (ARIZA BELIRLEYEN SINIF — kacan ariza gecisi olabilir)"
                if ariza_hatti
                else ""
            )
        ),
        metadata={
            "sinif": hat.sinif,
            "dusen_ust_sinir": yeni_kayip,
            "stream": hat.stream,
            "durable": hat.durable,
            "stream_ilk_sira": ilk_sira,
            "ack_tabani": ack_tabani,
        },
    )


def get_stats() -> dict[str, Any]:
    """Tuketicinin anlik durumu. Sistem Durumu sayfasi bunu okur.

    `throughput_msgs_per_sec` son 60 saniyelik kayan ortalamadir; anlik
    dalgalanmalari yumusatir. `backlog` en son fetch anindaki num_pending —
    surekli 0 civarinda olmasi beklenir; kalici olarak buyuyorsa tuketici
    gelis hizinin gerisindedir ve tampon tasarsa VERI KAYBI baslar.
    """
    with _stats_lock:
        snapshot = dict(_stats)
        # Hat basina ozet: hangi hattin geride oldugunu ayirt etmenin tek yolu.
        # Ust seviye alanlar (backlog/processed_total/...) GERIYE UYUMLU kalir.
        snapshot["hatlar"] = {sinif: dict(k) for sinif, k in _hat_stats.items()}
        if _throughput_window:
            span = max(1e-6, _throughput_window[-1][0] - _throughput_window[0][0])
            total = sum(n for _t, n in _throughput_window)
            # Tek ornek varsa span ~0 olur; bolmeyi anlamli tutmak icin
            # en az 1 saniye kabul ediyoruz.
            snapshot["throughput_msgs_per_sec"] = round(total / max(1.0, span), 2)
        else:
            snapshot["throughput_msgs_per_sec"] = 0.0
    return snapshot


# --------------------------------------------------------------------------
# TOPLU YAZIM — COPY + execute_values
#
# NEDEN: MIKRO-OLCUM (600 cihaz, 500 mesajlik parti, yerel Postgres 18.3)
# darbogazin DB OLMADIGINI gosterdi. Bir parti 142,7 ms suruyordu ve bunun
# yalnizca 42,0 ms'i sunucuda gecen SQL suresiydi; kalan ~100 ms Python
# tarafinda, SQLAlchemy'nin her partide YENIDEN yaptigi is:
#
#   * ORM unit-of-work (telemetry + processed_messages flush)   ~%39
#   * `insert().values([...500 sozluk...])` derlemesi            ~%29
#     (cok degerli INSERT'ler SQLAlchemy'de ONBELLEGE ALINMAZ —
#      her partide sifirdan derlenir)
#   * asil SQL calismasi (sunucu)                                ~%29
#
# JSON cozumu 1,8 us/mesaj (parti basina 0,91 ms, yani %0,6) — bu yuzden
# `orjson` gibi YENI BIR BAGIMLILIK ALINMADI: en iyi ihtimalle 0,5 ms
# kazandirirdi.
#
# COZUM: en buyuk iki tabloya (`telemetry`, `processed_messages`) psycopg2
# COPY ile yazmak — SQL derlemesi de ORM turu de YOK; arsiv/canli tablolarina
# ise `execute_values` ile TEK ifade. Sonuc olculdu, bkz. modul sonundaki
# olcum notu.
# --------------------------------------------------------------------------

#: COPY TEXT formatinin kacis tablosu. Bu dort karakter kacirilmazsa satir
#: sinirlari kayar ve BASKA BIR SATIRIN alanina veri tasar — sessiz bozulma.
#: `\` ONCE gelmek zorunda degil: `str.translate` her karakteri TEK GECISTE
#: cevirir, yani uretilen `\\` yeniden taranmaz.
_COPY_KACIS = str.maketrans(
    {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
)

#: COPY hedef kolonlari. `telemetry.id` BILEREK YOK — BIGSERIAL varsayilani
#: sunucuda uretilir, boylece istemcinin sira uretmesi gerekmez.
_TELEMETRI_KOLONLARI = (
    "device_id", "signal_key", "value", "value_string", "quality",
    "source_timestamp", "device_event_at", "timestamp_quality",
)
_DEDUP_KOLONLARI = ("consumer_name", "message_id", "processed_at")
_ARSIV_KOLONLARI = (
    "device_id", "signal_key", "value", "value_string", "quality",
    "source_timestamp", "device_event_at", "timestamp_quality",
)
_CANLI_KOLONLARI = _ARSIV_KOLONLARI + ("updated_at",)


def _copy_alan(deger: Any) -> str:
    """Tek bir alani COPY TEXT gosterimine cevirir.

    `bool` kontrolu `int`ten ONCE: Python'da `isinstance(True, int)` DOGRU'dur
    ve bool once yakalanmazsa `True` alana `1` olarak yazilirdi.
    """
    if deger is None:
        return "\\N"
    if deger is True:
        return "t"
    if deger is False:
        return "f"
    if isinstance(deger, (int, float)):
        return repr(deger)
    if isinstance(deger, datetime):
        return deger.isoformat()
    return str(deger).translate(_COPY_KACIS)


def _copy_govde(satirlar: list[tuple]) -> str:
    return "".join(
        "\t".join(_copy_alan(a) for a in satir) + "\n" for satir in satirlar
    )


def _parcala(satirlar: list, boyut: int):
    """Satirlari en fazla `boyut`luk dilimlere ayirir.

    TEK IFADEDE SINIRSIZ SATIR GONDERILEMEZ: `execute_values` bir INSERT
    ifadesine tum satirlari bind eder ve PostgreSQL'in ifade basina 65535
    parametre tavani vardir (9 kolonlu `telemetry_latest` icin ~7280 satir).
    Parti boyutu ayarla buyutulebildigi icin bu tavan ELDE tutulmali.
    """
    if boyut <= 0:
        boyut = 1
    for i in range(0, len(satirlar), boyut):
        yield satirlar[i:i + boyut]


def _copy_yaz(db, tablo: str, kolonlar: tuple[str, ...], satirlar: list[tuple]) -> None:  # noqa: ANN001
    """`COPY <tablo> (...) FROM STDIN` — SQL derlemesi ve ORM turu YOK.

    Ayni session'in ACIK transaction'i uzerinden gider (SQLAlchemy `BEGIN`i
    zaten actigi baglantinin ham kursoru kullanilir), yani atomikligi ve
    savepoint davranisini AYNEN korur.
    """
    ham = db.connection().connection
    sql = f"COPY {tablo} ({', '.join(kolonlar)}) FROM STDIN"
    cur = ham.cursor()
    try:
        for dilim in _parcala(satirlar, settings.telemetry_write_chunk_size):
            cur.copy_expert(sql, io.StringIO(_copy_govde(dilim)))
    finally:
        cur.close()


def _degerlerle_yaz(db, sql: str, satirlar: list[tuple]) -> None:  # noqa: ANN001
    """`INSERT ... VALUES %s ...` — psycopg2 `execute_values` ile TEK ifade.

    SQLAlchemy'nin `insert().values([...])` yolu yerine bu kullaniliyor cunku
    cok degerli INSERT'ler SQLAlchemy'de ONBELLEGE ALINMAZ: her partide
    yeniden derlenir ve olcumde parti suresinin ~%29'unu yiyordu. Uretilen
    SQL sabit oldugu icin burada derleme YOK.
    """
    from psycopg2.extras import execute_values

    ham = db.connection().connection
    cur = ham.cursor()
    try:
        for dilim in _parcala(satirlar, settings.telemetry_write_chunk_size):
            execute_values(cur, sql, dilim, page_size=len(dilim))
    finally:
        cur.close()


# Arsiv (historian) toplu INSERT'i. `ON CONFLICT DO NOTHING` idempotency
# saglar: ayni okuma yeniden teslim edilirse ikinci satir YAZILMAZ.
_ARSIV_SQL = (
    "INSERT INTO telemetry_history "
    "(device_id, signal_key, value, value_string, quality, "
    "source_timestamp, device_event_at, timestamp_quality) VALUES %s "
    "ON CONFLICT (device_id, signal_key, source_timestamp) DO NOTHING"
)

# Canli deger tablosu — ESKI DEGER YENIYI EZMEZ.
#
# `WHERE excluded.source_timestamp >= telemetry_latest.source_timestamp`
# kosulu PAZARLIK EDILEMEZ. NATS en-az-bir-kez teslim eder ve mesajlar sira
# DISINDA gelebilir (redelivery, gateway yeniden baglanmasi). Kosulsuz bir
# `DO UPDATE` bayat bir okumayi "son deger" yapardi; bu tablo canli ekranin
# ve WS yayininin kaynagi oldugu icin sonuc dogrudan operatore yansir: deger
# geri sicrar, ariza gecisi yanlis gorunur.
#
# TUM alanlar birlikte guncellenir. Eksik birakilan alan "yari guncellenmis"
# bir satir birakirdi — yeni deger ama eski kalite gibi, teshiste en
# yaniltici hal.
_CANLI_SQL = (
    "INSERT INTO telemetry_latest "
    "(device_id, signal_key, value, value_string, quality, "
    "source_timestamp, device_event_at, timestamp_quality, updated_at) "
    "VALUES %s "
    "ON CONFLICT (device_id, signal_key) DO UPDATE SET "
    "value = EXCLUDED.value, "
    "value_string = EXCLUDED.value_string, "
    "quality = EXCLUDED.quality, "
    "source_timestamp = EXCLUDED.source_timestamp, "
    "timestamp_quality = EXCLUDED.timestamp_quality, "
    "device_event_at = EXCLUDED.device_event_at, "
    "updated_at = EXCLUDED.updated_at "
    "WHERE EXCLUDED.source_timestamp >= telemetry_latest.source_timestamp"
)


class _Satir:
    """Tek bir olcumun DB'ye gidecek TUM satirlari — bir arada.

    Bolerek yeniden deneme (bkz. `_satirlari_yaz`) ancak boyle mumkun:
    bir olcum dusuruldugunde onun arsiv satiri, canli satiri VE dedup
    kaydi da birlikte duser. Aksi halde `processed_messages`'a yazilmis
    ama `telemetry`'ye yazilmamis bir olcum olusur ve yeniden teslimde
    dedup onu YUTAR — olcum kalici olarak kaybolurdu.
    """

    __slots__ = ("msg", "message_id", "telemetri", "arsiv", "canli", "ws", "outbound")

    def __init__(self, *, msg, message_id, telemetri, arsiv, canli, ws, outbound):  # noqa: ANN001
        self.msg = msg
        self.message_id = message_id
        self.telemetri = telemetri
        self.arsiv = arsiv
        self.canli = canli
        self.ws = ws
        self.outbound = outbound


def _tek_gecis_yaz(db, satirlar: list[_Satir], islenme_zamani: datetime) -> None:  # noqa: ANN001
    """Verilen satirlari DORT tabloya yazar. Hata YUKARI FIRLAR."""
    if not satirlar:
        return
    _copy_yaz(db, "telemetry", _TELEMETRI_KOLONLARI, [s.telemetri for s in satirlar])
    _copy_yaz(
        db,
        "processed_messages",
        _DEDUP_KOLONLARI,
        [(CONSUMER_NAME, s.message_id, islenme_zamani) for s in satirlar],
    )
    arsiv = [s.arsiv for s in satirlar if s.arsiv is not None]
    if arsiv:
        _degerlerle_yaz(db, _ARSIV_SQL, arsiv)
    # Ayni (cihaz, sinyal) cifti bu dilimde birden fazla kez gelebilir; tek
    # bir INSERT icinde `ON CONFLICT DO UPDATE` ayni satiri IKI KEZ
    # guncelleyemez ("cannot affect row a second time"). Bu yuzden burada
    # tekillestirilip EN YENISI birakilir. Dilimler arasi sira onemsizdir:
    # yukaridaki `WHERE` kosulu bayat degeri zaten reddeder.
    canli_satirlar: dict[tuple[int, str], tuple] = {}
    for s in satirlar:
        if s.canli is None:
            continue
        anahtar = (s.canli[0], s.canli[1])
        onceki = canli_satirlar.get(anahtar)
        if onceki is None or s.canli[5] >= onceki[5]:  # source_timestamp
            canli_satirlar[anahtar] = s.canli
    if canli_satirlar:
        _degerlerle_yaz(db, _CANLI_SQL, list(canli_satirlar.values()))


def _satirlari_yaz(
    db, satirlar: list[_Satir], islenme_zamani: datetime  # noqa: ANN001
) -> tuple[list[_Satir], list[_Satir]]:
    """Satirlari yazar; BIR SATIRIN hatasi TUM PARTIYI DUSURMEZ.

    Donus: (yazilan, dusen).

    HIZLI YOL tek gecistir. `DataError` (kolon genisligini asan bir deger,
    sayiya cevrilemeyen bir alan...) gelirse parti IKIYE BOLUNUP yeniden
    denenir ve bu ozyinelemeli olarak TEK SATIRA kadar iner. Boylece yalnizca
    gercekten bozuk satir(lar) dusurulur; yanindaki 499 saglam olcum yazilir.
    ~log2(N) fazladan gidis-donus, ve YALNIZCA hata halinde.

    `IntegrityError` BILEREK yakalanmaz. O, `processed_messages` bilesik
    UNIQUE index'inin "bu olcum ZATEN islenmis" demesidir; satir bozuk
    degildir, yalnizca tekrardir. Bolup DLQ'ya atmak saglam bir olcumu
    karantinaya gondermek olurdu. Yukari birakilir, cagiran TUM partiyi
    yeniden teslime birakir ve bir sonraki turda `IN` on kontrolu onu eler.

    SAVEPOINT sart: `DataError` transaction'i ABORTED yapar; savepoint
    olmadan geri sarma cihaz mutasyonlarini (communication_status,
    last_update_at) da gotururdu.
    """
    if not satirlar:
        return [], []
    try:
        with db.begin_nested():
            _tek_gecis_yaz(db, satirlar, islenme_zamani)
        return satirlar, []
    except DataError as exc:
        if len(satirlar) == 1:
            logger.error(
                "telemetry_satir_karantinada msg=%s — bu TEK satir yazilamadi, "
                "partinin geri kalani yazildi. Sebep: %s",
                satirlar[0].message_id,
                str(exc)[:300],
            )
            return [], satirlar
        orta = len(satirlar) // 2
        sol_ok, sol_bad = _satirlari_yaz(db, satirlar[:orta], islenme_zamani)
        sag_ok, sag_bad = _satirlari_yaz(db, satirlar[orta:], islenme_zamani)
        return sol_ok + sag_ok, sol_bad + sag_bad


def process_valid_telemetry(  # noqa: ANN001
    db,
    *,
    device,
    reading: TelemetryIn,
    payload: dict,
    message_id: str,
    msg=None,
) -> _Satir:
    """Cihazi BILINEN gecerli bir okumayi DB satirlarina donusturur.

    TEK KAYNAK: canli tuketici yolu ve karantina replay yolu bu fonksiyonu
    kullanir. Ayni is mantiginin iki kopyasi olsaydi biri sessizce
    otekinden ayrilirdi — replay edilen olcum canli olcumden farkli kalite,
    farkli arsiv karari ya da farkli saat degerlendirmesi alirdi.

    Yan etkiler `db` uzerinde ORM mutasyonu olarak birikir (cihaz durumu,
    last_update_at); satirlar caller tarafindan `_tek_gecis_yaz` ile
    yazilir. Burada commit YOK.
    """
    from app.models.telemetry import Telemetry
    from app.services.device_clock_service import assess_device_timestamp
    from app.services import historian_policy
    from app.services.tag_engine_service import (
        map_quality_to_status,
        normalize_quality,
        process_telemetry_reading,
        should_write_last_update,
    )

    # process_telemetry_reading: Telemetry obj + device mutasyonu (ayni
    # db, commit yok). Patlarsa TUM batch'i rollback ETME -> o mesaji
    # bad_msgs'e ayir, minimal fallback ile devam.
    telemetry = None
    try:
        telemetry, _event = process_telemetry_reading(device, reading, db=db)
    except SQLAlchemyError:
        # DB/transaction hatasinda fallback ile devam edilmez: session
        # aborted olabilir. Batch exception ile cikar, hicbir msg ack
        # edilmez; JetStream redeliver eder (veri kaybi yok).
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "telemetry-consumer-process-error msg=%s error=%s", message_id, exc
        )
        # Saf is-mantigi hatasinda minimal telemetry + status fallback.
        nq = normalize_quality(reading.quality)
        device.communication_status = map_quality_to_status(nq)
        if device.communication_status.value == "online":
            # Karar ana yolla AYNI fonksiyondan geliyor; kosulu
            # kopyalamak, birinin sessizce eski davranisa donmesi
            # demekti.
            _simdi = datetime.now(timezone.utc)
            if should_write_last_update(device.last_update_at, _simdi):
                device.last_update_at = _simdi
        _fb_at, _fb_quality = assess_device_timestamp(
            getattr(reading, "device_event_at", None),
            reported_quality=getattr(reading, "timestamp_quality", None),
        )
        telemetry = Telemetry(
            device_id=device.id,
            signal_key=reading.signal_key,
            value=reading.value,
            value_string=reading.value_string,
            quality=nq,
            source_timestamp=reading.source_timestamp,
            # Fallback yolunda da damgalanir: aksi halde is-mantigi
            # hatasi alan mesajlarda saat durumu SESSIZCE kaybolurdu.
            device_event_at=_fb_at,
            timestamp_quality=_fb_quality,
        )

    # `telemetry` ORM'e EKLENMEZ: satiri COPY yazar (bkz.
    # `_tek_gecis_yaz`). ORM nesnesi yalnizca deger tasiyicisi —
    # process_telemetry_reading'in damgaladigi alanlar tuple'a
    # buradan okunur. `processed_messages` dedup satiri da ayni
    # COPY gecisinde gider.
    #
    # Cihazin kendi olay zamani (varsa) + makullugu. `source_timestamp`
    # AYNEN kaliyor (PK/partition kolonu); bu ikisi yalnizca analiz
    # icin ayri kolonlarda duruyor. Bkz. device_clock_service.
    #
    # Degerlendirme TEKRAR yapilmaz: canli satiri kuran
    # `process_telemetry_reading` zaten damgaladi. Ikinci kez cagirmak
    # 7 gunluk pencerenin tam sinirinda iki satirin FARKLI kalite
    # almasina yol acabilirdi (araya gecen mikrosaniyeler yuzunden).
    _dev_at = telemetry.device_event_at
    _ts_quality = telemetry.timestamp_quality
    # Kalite BIR KEZ normalize edilir ve hem arsiv KARARINA hem
    # yazilan satirlara ayni deger gider. Karara gecirilmesi sart:
    # olu bant yalnizca sayiya bakarsa, esik icinde donmus bir
    # olcumde good->invalid/comm_lost gecisi arsive HIC girmez.
    _kalite = normalize_quality(reading.quality)
    # ARSIV POLITIKASI — her okuma arsive yazilmaz.
    #
    # Gercek SCADA pratigi: anlik deger her zaman guncel tutulur
    # (`telemetry_latest`, hemen asagida) ama arsive yalnizca
    # isaretlenen tag'ler, olu bant suzgecinden gecerek yazilir.
    # Alarm dogrulugu ETKILENMEZ: alarm-service akis tabanli
    # calisiyor, gecmis sorgusu yapmiyor.
    arsiv_satiri = None
    if historian_policy.should_archive(
        db,
        device_id=device.id,
        signal_key=reading.signal_key,
        value=reading.value,
        quality=_kalite,
        # Arsiv politikasi MODELE aittir: ayni ada sahip sinyal iki
        # modelde farkli olu banda/arsiv ayarina sahip olabilir.
        device_model=device.model,
    ):
        arsiv_satiri = (
            device.id, reading.signal_key, reading.value,
            reading.value_string, _kalite, reading.source_timestamp,
            _dev_at, _ts_quality,
        )
    # `telemetry_latest` satiri — batch-ici tekillestirme burada DEGIL,
    # `_tek_gecis_yaz` icinde yapilir (dilimlere bolunmus yeniden
    # denemede de dogru kalsin diye tek yerde).
    canli_satiri = (
        device.id, reading.signal_key, reading.value,
        reading.value_string, _kalite, reading.source_timestamp,
        _dev_at, _ts_quality, datetime.now(timezone.utc),
    )
    # WS yayini ham gateway payload'unu tasir; saat degerlendirmesini
    # UZERINE YAZIYORUZ. Gateway'in ham bildirimi degil BIZIM
    # degerlendirmemiz otoriter: gateway hic bir sey demese bile
    # 2000-01-01 damgasini "invalid" olarak isaretleyen biziz. Aksi
    # halde canli ekran (WS) ile yenilenmis snapshot (`/signals/live`)
    # ayni satir icin farkli sey gosterirdi.
    payload["device_event_at"] = _dev_at.isoformat() if _dev_at else None
    payload["timestamp_quality"] = _ts_quality

    # Outbound dispatch payload'u — status commit ONCESI yakalanir
    # (commit sonrasi device expire olabilir). Dispatch commit SONRASI.
    status_val = (
        device.communication_status.value
        if hasattr(device.communication_status, "value")
        else str(device.communication_status)
    )
    sig_source = None
    if reading.signal_key and "." in reading.signal_key:
        sig_source = reading.signal_key.split(".", 1)[0].lower()
    outbound_satiri = {
        "message_id": message_id,
        "correlation_id": reading.correlation_id or message_id,
        "event_kind": "telemetry",
        "device_code": reading.device_code,
        "signal_key": reading.signal_key,
        "signal_source": sig_source,
        "source_gateway": reading.source_gateway,
        "value": reading.value,
        "value_string": reading.value_string,
        "quality": reading.quality,
        "status": status_val,
        "source_timestamp": reading.source_timestamp.isoformat() if reading.source_timestamp else None,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    return _Satir(
        msg=msg,
        message_id=message_id,
        # `telemetry` tablosu ORM nesnesinin damgaladigi alanlardan
        # yazilir (process_telemetry_reading value/quality'yi
        # donusturmus olabilir); kolon sirasi _TELEMETRI_KOLONLARI.
        telemetri=(
            device.id, telemetry.signal_key, telemetry.value,
            telemetry.value_string, telemetry.quality,
            telemetry.source_timestamp, telemetry.device_event_at,
            telemetry.timestamp_quality,
        ),
        arsiv=arsiv_satiri,
        canli=canli_satiri,
        ws=payload,
        outbound=outbound_satiri,
    )


def _persist_batch(msgs: list) -> tuple[list, list, list, list]:  # noqa: ANN001
    """Bir fetch batch'ini TEK session + TEK commit ile isler.

    Onceki tasarim her mesaji ayri SessionLocal + ayri commit ile isliyordu;
    200 cihaz yukunde DB round-trip gelis hizindan yavas kalip 1.4M mesajlik
    backlog + ack_pending tikanmasi (consumer DONMASI) yaratti. Burada 256-500
    mesaj tek transaction'da yazilir -> DB round-trip ~batch kadar azalir,
    throughput gelis hizini gecer, backlog erir.

    Idempotency: batch'teki message_id'ler TEK `IN` sorgusuyla kontrol edilir
    (256 ayri SELECT yerine). Device lookup batch-ici cache ile tekrar sorgu
    yapmaz. Historian ayni commit'e dahil (ayri session/commit kaldirildi).

    Donus: (ok_msgs, bad_msgs, ok_payloads, outbound_payloads)
      - ok_msgs: commit BASARILI olduktan sonra ack edilecek NATS mesajlari
        (yeni islenmis + zaten islenmis skip'ler).
      - bad_msgs: parse/validation hatasi olan mesajlar -> caller DLQ/nak yapar.
      - ok_payloads: commit sonrasi WS broadcast icin ham payload'lar.
      - outbound_payloads: commit sonrasi IEC104/REST/MQTT dispatch payload'lari.

    Kismi hata: bir mesajin process'i patlarsa TUM batch nak EDILMEZ; o mesaj
    bad_msgs'e ayrilir, kalan batch commit'e girer. Tek poison mesaj 255
    saglikli mesaji redeliver dongusune sokmaz. Ayni koruma YAZIM asamasinda
    da var: bkz. `_satirlari_yaz` (bozuk satir bolerek ayiklanir).
    """
    from app.models.telemetry import Telemetry
    from app.services.device_clock_service import assess_device_timestamp
    from app.services import historian_policy
    from app.services.tag_engine_service import (
        map_quality_to_status,
        normalize_quality,
        process_telemetry_reading,
        should_write_last_update,
    )

    ok_msgs: list = []
    bad_msgs: list = []
    ok_payloads: list = []
    outbound_payloads: list = []  # commit sonrasi IEC104/REST/MQTT dispatch

    # 1) Parse + message_id'leri topla. Parse hatasi -> bad_msgs (DLQ/nak).
    #
    # 4. eleman `mid_vardi`: message_id payload'da GERCEKTEN var miydi.
    # Bilinmeyen cihaz karantinasi bunu bilmek ZORUNDA — asagida uretilen
    # uuid4 her teslimde DEGISIR ve tekillestirme anahtari olarak kullanilirsa
    # ayni fiziksel mesaj her yeniden teslimde ikinci bir karantina satiri
    # acardi (bkz. unknown_device_quarantine.dedup_key_for).
    parsed: list[tuple[Any, dict, str, bool]] = []
    for msg in msgs:
        try:
            payload = json.loads(msg.data.decode("utf-8"))
        except Exception:  # noqa: BLE001
            bad_msgs.append(msg)
            continue
        # JSON gecerli ama nesne DEGIL (dizi/skaler) olabilir. Asagidaki
        # `payload.get` o durumda AttributeError firlatir ve istisna
        # `_persist_batch`ten disari cikip TUM batch'i ack'siz birakirdi —
        # ayni bozuk mesaj sonsuza kadar yeniden teslim edilirdi. Bu bir
        # BICIM hatasi, bilinmeyen cihaz degil: DLQ yoluna gider.
        if not isinstance(payload, dict):
            bad_msgs.append(msg)
            continue
        message_id = str(payload.get("message_id") or "")
        mid_vardi = bool(message_id)
        if not message_id:
            message_id = str(uuid4())
            payload["message_id"] = message_id
        parsed.append((msg, payload, message_id, mid_vardi))

    if not parsed:
        return ok_msgs, bad_msgs, ok_payloads, outbound_payloads

    db = SessionLocal()
    try:
        # 2) Idempotency + device lookup: batch basina IKISER sorgu. Onceki
        # davranis mesaj basina device SELECT yapiyordu (500 mesaj -> yuzlerce
        # DB round-trip). Tum code'lari tek IN sorgusuyla cache'le.
        ids = [mid for (_m, _p, mid, _v) in parsed]
        seen: set[str] = set(
            db.scalars(
                select(ProcessedMessage.message_id).where(
                    ProcessedMessage.consumer_name == CONSUMER_NAME,
                    ProcessedMessage.message_id.in_(ids),
                )
            ).all()
        )
        device_codes = {
            str(payload.get("device_code") or "") for (_m, payload, _mid, _v) in parsed
        }
        devices = db.scalars(
            select(Device).where(Device.code.in_(device_codes))
        ).all()
        device_cache: dict[str, Device] = {device.code: device for device in devices}
        # Her olcumun DB'ye gidecek TUM satirlari bir arada biriktirilir ve
        # dongu SONUNDA `_satirlari_yaz` ile COPY + tek-ifade upsert olarak
        # gider (bkz. modul basindaki TOPLU YAZIM olcumu). ack/ws/outbound
        # kararlari da yazim SONUCUNA baglanir: dusen satirin mesaji ack
        # EDILMEZ, WS/outbound yayini yapilmaz.
        satirlar: list[_Satir] = []
        # Bilinmeyen cihaz dali — bilinen cihaz HIZLI YOLU bu listelere hic
        # dokunmaz, ek sorgu/kilit gormez.
        karantina_sonucu: quarantine.QuarantineOutcome | None = None
        bilinmeyen_kayitlar: list[quarantine.QuarantineEntry] = []
        bilinmeyen_msgs: list = []
        bilinmeyen_bildirim: dict[str, str | None] = {}

        for msg, payload, message_id, mid_vardi in parsed:
            if message_id in seen:
                ok_msgs.append(msg)  # zaten islenmis -> sadece ack
                continue
            try:
                reading = TelemetryIn(**payload)
            except ValidationError as exc:
                logger.warning(
                    "telemetry-consumer-invalid-payload msg=%s error=%s", message_id, exc
                )
                bad_msgs.append(msg)
                continue

            dcode = reading.device_code
            device = device_cache.get(dcode)

            if device is None:
                # BILINMEYEN CIHAZ — PAYLOAD ARTIK ATILMIYOR.
                #
                # ESKI DAVRANIS VE NEDEN YANLISTI: burada yalnizca uyari
                # loglaniyor, `processed_messages`'a dedup satiri yazilip
                # mesaj ack ediliyordu. Payload KALICI OLARAK kayboluyordu:
                # ack'lenen mesaj bir daha teslim edilmiyor, dedup satiri da
                # olasi bir yeniden teslimi yutuyordu. Cihaz iki dakika sonra
                # tanimlansa bile aradaki olcumler geri getirilemezdi.
                #
                # `ProcessedMessage` BILEREK YAZILMIYOR: onu yazmak mesaji
                # "islenmis" saymak demek ve replay'i kalici olarak bloke
                # ederdi (hem yeniden teslim yolunu hem de karantinadan
                # replay'i). Tekillestirme gorevini karantina tablosunun
                # (consumer_name, dedup_key) UNIQUE kisiti ustleniyor.
                #
                # Mesaj ack listesine HENUZ girmiyor: karantina yazimi
                # basarili olmadan ack YOK (bkz. asagidaki yazim blogu).
                bilinmeyen_kayitlar.append(
                    quarantine.entry_from_message(
                        msg,
                        payload,
                        consumer_name=CONSUMER_NAME,
                        message_id=message_id,
                        device_code=dcode,
                        payload_had_message_id=mid_vardi,
                    )
                )
                bilinmeyen_msgs.append(msg)
                bilinmeyen_bildirim.setdefault(
                    dcode, str(payload.get("source_gateway") or "") or None
                )
                continue

            # Cihaz BILINIYOR: olcumu ortak is mantigiyla satirlara cevir.
            # Ayni fonksiyonu karantina replay yolu da cagirir (bkz.
            # `process_valid_telemetry`) — iki yolun ayrisamamasi icin.
            satir = process_valid_telemetry(
                db,
                device=device,
                reading=reading,
                payload=payload,
                message_id=message_id,
                msg=msg,
            )
            seen.add(message_id)  # ayni batch'te duplicate message_id'ye karsi
            satirlar.append(satir)

        # BILINMEYEN CIHAZ KARANTINASI — ack'ten ONCE, ayni transaction'da.
        #
        # ACK SOZLESMESI: bu mesajlar `ok_msgs`e ANCAK yazim basarili olursa
        # girer, ve `ok_msgs` yalnizca asagidaki `db.commit()` gectikten
        # sonra ack edilir. Yani "karantina persist edilmeden ack" durumu
        # olusamaz. Yazim istisna firlatirsa (DB erisilemez, kisit ihlali)
        # istisna disari cikar, hicbir mesaj ack EDILMEZ ve JetStream
        # yeniden teslim eder.
        if bilinmeyen_kayitlar:
            # SAVEPOINT SART — yer acma geri sarilabilmeli.
            #
            # `ensure_capacity` yer acmak icin ESKI SATIRLARI SILER. Yer yine
            # de yetmezse `QuarantineCapacityError` firlar; savepoint olmadan
            # o silmeler asagidaki `db.commit()` ile BILINEN CIHAZ yazimlarina
            # takilip KALICI olurdu. Sonuc: yeni payload yazilamamis ama eski
            # payload'lar silinmis — saf veri kaybi.
            #
            # Savepoint ile karantina denemesi TEK PARCA halinde geri sarilir;
            # ayni parti icindeki bilinen cihaz telemetrisi commit edilmeye
            # devam eder (onu da kaybetmek gereksiz olurdu).
            #
            # `flush` savepoint'ten ONCE: bu noktaya kadar biriken cihaz
            # mutasyonlari (communication_status, last_update_at) savepoint'in
            # DISINDA kalsin; aksi halde autoflush onlari savepoint icinde
            # yazar ve geri sarma onlari da gotururdu.
            db.flush()
            try:
                with db.begin_nested():
                    karantina_sonucu = quarantine.quarantine_batch(
                        db, bilinmeyen_kayitlar
                    )
                    for _dcode, _gcode in bilinmeyen_bildirim.items():
                        quarantine.notify(db, _dcode, _gcode)
            except quarantine.QuarantineCapacityError:
                # YER ACILAMADI — ARTIK OLAGANDISI BIR DURUM.
                #
                # Normal kapasite baskisinda `ensure_capacity` kontrollu yer
                # acar (suresi dolmus kayitlar, gerekirse acil veri dusurme)
                # ve bu dala HIC girilmez. Buraya dusuluyorsa yapilandirma
                # hatasi vardir: parti tavandan buyuk, ya da silinebilecek
                # pending kayit kalmamis.
                #
                # O halde mesajlari ack ETMEMEK dogru davranis: veri kaybini
                # sessizlestirmek yerine gorunur birakiyoruz. Batch'in BILINEN
                # cihaz olcumleri etkilenmez ve normal yazilir — kapasite
                # arizasi saglam telemetriyi de durdurmamali.
                # Savepoint geri sarildi: yer acma silmeleri de geri geldi.
                logger.error(
                    "unknown_device_quarantine_capacity_block mesaj=%d — ack edilmedi, "
                    "yer acma geri sarildi "
                    "(UNKNOWN_TELEMETRY_MAX_ROWS yapilandirmasini gozden gecirin)",
                    len(bilinmeyen_msgs),
                )
            else:
                ok_msgs.extend(bilinmeyen_msgs)

        # TOPLU YAZIM: dort tablo tek geciste (COPY + tek-ifade upsert).
        # Bozuk satir partiyi dusurmez — `_satirlari_yaz` ikiye bolerek
        # yalnizca gercekten bozuk satiri karantinaya alir. ack/ws/outbound
        # YALNIZCA yazilan satirlar icin yapilir; dusen satirin mesaji
        # DLQ/nak yoluna gider (veri kaybi sessiz olmaz).
        yazilan, dusen = _satirlari_yaz(db, satirlar, datetime.now(timezone.utc))
        for s in yazilan:
            ok_msgs.append(s.msg)
            ok_payloads.append(s.ws)
            outbound_payloads.append(s.outbound)
        for s in dusen:
            bad_msgs.append(s.msg)

        # 3) TEK commit. Basarisizsa (nadir: paralel consumer carpismasi) tum
        # batch redeliver edilir (ack YAPILMADI) -> at-least-once korunur.
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.warning("telemetry-consumer-batch-commit-conflict — redeliver bekleniyor")
            # Commit patladi: hicbir sey ack'leme, hepsi redeliver olsun.
            # (IN pre-check duplicate'leri eledigi icin bu neredeyse hic tetiklenmez.)
            return [], bad_msgs, [], []
        except DataError as exc:
            # VERI BOZUK — redeliver ETMEK ISE YARAMAZ.
            #
            # `DataError` IntegrityError'in KARDESIDIR (ikisi de
            # DatabaseError'dan turer), yani yukaridaki yakalayici onu hic
            # gormuyordu. Istisna disari cikiyor ve cagirandaki genel
            # `except Exception` bunu BAGLANTI HATASI sanip
            # `telemetry_consumer_reconnect` logluyordu — operator sebebi
            # agda ariyordu.
            #
            # Tipik sebep kolon genisligini asan bir deger. Artik `TelemetryIn`
            # uzunluklari dogruladigi icin bu yola normalde HIC girilmez; buraya
            # dusuluyorsa dogrulanmayan yeni bir alan var demektir. Redeliver
            # ayni hatayi 10 kez tekrarlar ve batch'teki SAGLAM olcumleri de
            # goturur; bu yuzden batch bad_msgs'e alinip karantinaya gonderilir.
            db.rollback()
            logger.error(
                "telemetry_batch_data_error mesaj=%d — batch karantinaya alindi "
                "(redeliver ayni hatayi tekrarlardi). Sebep: %s",
                len(ok_msgs),
                str(exc)[:500],
            )
            return [], bad_msgs + ok_msgs, [], []

        # COMMIT BASARILI — KARANTINA METRIKLERI ANCAK SIMDI ISLENIR.
        #
        # Commit'ten once islenselerdi, geri sarilan bir transaction'in
        # ardindan `data_shed_total > 0` gorunur ve operator HIC OLMAMIS bir
        # veri kaybini kovalardi. Sayaclar yalnizca KALICILASMIS gercegi
        # anlatir.
        if karantina_sonucu is not None:
            karantina_sonucu.apply_metrics()
    finally:
        db.close()

    return ok_msgs, bad_msgs, ok_payloads, outbound_payloads


def _dispatch_outbound(outbound_payloads: list) -> None:  # noqa: ANN001
    """Commit sonrasi IEC104/REST/MQTT dispatch. Kendi kisa session'i ile;
    hata telemetri akisini bozmaz (mevcut _persist_message davranisi)."""
    if not outbound_payloads:
        return
    from app.models.outbound_target import OutboundTarget
    from app.services.outbound_dispatch_service import dispatch_event
    from app.services.outbound_telemetry_batcher import submit as rest_batch_submit
    from app.services.mqtt_publisher_service import submit_telemetry as mqtt_submit

    db = SessionLocal()
    try:
        targets = list(
            db.scalars(select(OutboundTarget).where(OutboundTarget.is_active.is_(True))).all()
        )
        for op in outbound_payloads:
            try:
                dispatch_event(
                    db, event_kind="telemetry", payload=op, targets=targets
                )  # IEC104 anlik
                rest_batch_submit(op)  # REST 5sn batch
                mqtt_submit(op)  # MQTT per-target
            except Exception:  # noqa: BLE001
                logger.debug("outbound_dispatch_failed_telemetry msg=%s",
                             op.get("message_id"), exc_info=True)
    finally:
        db.close()


# --------------------------------------------------------------------------
# KAYNAK SECIMI VE GECIS (raw -> normalized)
#
# Gecisin DURUM DEFTERI JetStream'in kendisidir: "yeni durable var mi?".
# Ayri bir bayrak (DB satiri, dosya) BILEREK tutulmuyor — bayrak ile gercek
# durum ayrisabilirdi ("gectim" diyen bir bayrak ama silinmis bir durable,
# ya da tersi) ve ayrisma ancak veri kaybi/tekrari olarak fark edilirdi.
# --------------------------------------------------------------------------


async def _consumer_var_mi(js, stream: str, durable: str) -> bool:  # noqa: ANN001
    try:
        await js.consumer_info(stream, durable)
        return True
    except Exception:  # noqa: BLE001  (nats.js.errors.NotFoundError dahil)
        return False


def _consumer_cfg(  # noqa: ANN001
    *,
    durable: str,
    deliver_policy,
    opt_start_time: str | None = None,
    filtreler: list[str] | None = None,
):
    """Pull consumer konfigurasyonu — tum hatlar icin AYNI parametreler.

    `filtreler` verilirse durable YALNIZCA o konulari cekmesi icin kurulur;
    hat ayriminin temeli budur. Tek eleman varsa `filter_subject`, birden
    fazlaysa `filter_subjects` (NATS 2.10+) kullanilir — ikisi AYNI ANDA
    gonderilemez, sunucu reddeder.
    """
    from nats.js.api import ConsumerConfig

    ek: dict[str, Any] = {}
    if filtreler:
        if len(filtreler) == 1:
            ek["filter_subject"] = filtreler[0]
        else:
            ek["filter_subjects"] = list(filtreler)
    return ConsumerConfig(
        durable_name=durable,
        deliver_policy=deliver_policy,
        opt_start_time=opt_start_time,
        # persist + WS tipik <100ms; 60sn defansif cap.
        ack_wait=60,
        # Batch-commit consumer: fetch inflight'i sinirlar; batch boyutunun
        # kati olmali. Backlog'u TEK BASINA cozmez, sadece tikanma tavani.
        max_ack_pending=settings.nats_pull_max_ack_pending,
        # Poison message sonsuz redeliver edilmez -> DLQ.
        max_deliver=settings.nats_worker_max_deliver,
        **ek,
    )


async def _bagla(  # noqa: ANN001
    js,
    *,
    stream: str,
    durable: str,
    deliver_policy,
    opt_start_time: str | None = None,
    filtreler: list[str] | None = None,
):
    """Durable YOKSA olustur, sonra ONA BAGLAN (config gondermeden).

    NEDEN `pull_subscribe(config=...)` DEGIL: mevcut bir durable'a her
    baglanista config gondermek, config surustugu anda sunucunun reddetmesi
    demek. Gecisin yaptigi tam olarak budur (`deliver_policy` degisir).
    `add_consumer` + `pull_subscribe_bind` ayrimi, "olusturma parametreleri"
    ile "baglanma" islerini birbirinden ayirir: mevcut durable'in konumu ve
    ayarlari ASLA ezilmez — devralinan 6.5M birikimin kaybolmamasi buna
    bagli.
    """
    if not await _consumer_var_mi(js, stream, durable):
        await js.add_consumer(
            stream,
            config=_consumer_cfg(
                durable=durable,
                deliver_policy=deliver_policy,
                opt_start_time=opt_start_time,
                filtreler=filtreler,
            ),
        )
    return await js.pull_subscribe_bind(consumer=durable, stream=stream)


def _oncelikli_filtreler() -> list[str]:
    """Oncelikli hattin konu filtresi — SINIFSIZ eski konu DAHIL.

    Ikinci filtre (`e1.telemetry.normalized.*`, 4 token) bir gecis onlemi
    DEGIL, KALICI bir guvenlik agidir: bir sekilde siniflandirilmamis bir
    mesaj akisa girerse (eski surum tag-engine, elle publish, gelecekte
    eklenen baska bir uretici) hicbir sinifli filtreye uymaz ve SESSIZCE
    kaybolurdu. "Bilinmeyen -> oncelikli hat" kurali konu seviyesinde de
    gecerli.

    Iki desen CAKISMAZ: biri 5, digeri 4 token. Cakisan filtreler NATS
    tarafindan reddedilir ve ayni mesaj iki kez islenmis olurdu.
    """
    return [
        settings.nats_subject_telemetry_normalized_prio,
        settings.nats_subject_telemetry_normalized_legacy,
    ]


async def _eski_raw_durable_temizle(js) -> None:  # noqa: ANN001
    """Bosalmis RAW durable'ini siler (yoksa sessizce gecer).

    NEDEN SILINIYOR: birakilirsa bir SURUM GERI DONUSUNDE (rollback) eski
    kod onu kaldigi yerden okumaya devam eder ve NORMALIZED uzerinden ZATEN
    yazilmis olcumleri IKINCI KEZ yazar — `processed_messages` defteri 2
    saatlik oldugu icin o tekrari da yutamaz. Silinmis olursa eski kod onu
    `DeliverPolicy.NEW` ile YENIDEN yaratir, yani akisin basindan degil o
    andan baslar: ne tekrar olur ne de yeni gelen olcum kaybolur.
    """
    try:
        await js.delete_consumer(
            settings.nats_stream_telemetry_raw,
            settings.nats_consumer_telemetry_persist,
        )
        logger.info(
            "telemetry_persist_raw_durable_silindi durable=%s — drenaj bitti",
            settings.nats_consumer_telemetry_persist,
        )
    except Exception:  # noqa: BLE001
        logger.debug("telemetry_persist_raw_durable_delete_skipped", exc_info=True)


async def _ayrik_durable_temizle(js) -> None:  # noqa: ANN001
    """Oncelikli + toplu durable'larini siler (yoksa sessizce gecer).

    TOPLU ONCE: silme sirasinda surec olurse geride ONCELIKLI hattin
    durable'i kalsin — ariza sinifinin tuketicisiz kalmasi, analog
    arsivinin gecikmesinden agirdir.
    """
    norm_stream = settings.nats_stream_telemetry_normalized
    for durable in (
        settings.nats_consumer_telemetry_persist_bulk,
        settings.nats_consumer_telemetry_persist_prio,
    ):
        try:
            await js.delete_consumer(norm_stream, durable)
            logger.info("telemetry_persist_ayrik_durable_silindi durable=%s", durable)
        except Exception:  # noqa: BLE001  (yoksa NotFoundError — normal)
            logger.debug(
                "telemetry_persist_ayrik_durable_delete_skipped durable=%s", durable
            )


async def _normalized_durable_temizle(js, *, yalnizca_tek_hat: bool = False) -> None:  # noqa: ANN001
    """Kaynak `raw`a alindiysa NORMALIZED durable'ini siler.

    `yalnizca_tek_hat=True` ise SADECE tek hat donemi durable'i (v3) silinir;
    hat ayrimina gecerken kullanilir. Varsayilan (False) iki hattin
    durable'larini da siler — geri donus hepsini terk ediyor.

    NEDEN: `raw` gecisi ERTELEME/GERI ALMA kolu. Gecis daha once tamamlanmis
    ve sonra bu kola basilmissa, NORMALIZED durable'i ORTADA KALIR ve onu
    kimse tuketmez — `num_pending` saatlerce buyur. Tercih tekrar `auto`
    yapildiginda (ki `auto` compose varsayilanidir, yani container'in bir
    sonraki yeniden yaratilmasi bunu KENDILIGINDEN yapar) o birikimin
    TAMAMI yeniden islenir. Oysa ayni olcumler bu arada RAW uzerinden ZATEN
    yazilmistir ve `processed_messages` defteri 2 saatlik oldugu icin
    tekrari yutamaz -> `telemetry` tablosunda CIFT KAYIT (bu tabloda dogal
    anahtar yok, ON CONFLICT koruyamaz).

    Silinince `auto`ya donus durable'i sinirli geri sarmayla (bkz.
    `_cutover`) YENIDEN yaratir: ne tekrar olur ne kayip.
    """
    try:
        await js.delete_consumer(
            settings.nats_stream_telemetry_normalized,
            settings.nats_consumer_telemetry_persist_normalized,
        )
        logger.warning(
            "telemetry_persist_normalized_durable_silindi durable=%s — "
            "tuketilmeyen durable birikip `auto`ya donuste CIFT KAYIT uretirdi",
            settings.nats_consumer_telemetry_persist_normalized,
        )
    except Exception:  # noqa: BLE001  (yoksa NotFoundError — normal)
        logger.debug("telemetry_persist_normalized_durable_delete_skipped", exc_info=True)
    if not yalnizca_tek_hat:
        await _ayrik_durable_temizle(js)


async def _drenaj_bitti_mi(js, *, stream: str, durable: str) -> bool:  # noqa: ANN001
    """Verilen durable'da ISLENMEMIS TEK MESAJ kaldi mi? Gecisin on kosulu.

    HER IKI YON de bunu kullanir: ileri gecis (RAW -> NORMALIZED) ve geri
    donus (NORMALIZED -> RAW). Terk edilecek durable ONCE bosalmali, cunku
    her iki yol da sonunda o durable'i SILER.

    KARAR NEDEN SUNUCUDAN SORULUYOR — fetch timeout'u IKI YONDEN de yanlis:

    1) YANLIS NEGATIF, yani gecis HIC OLMAZ. `fetch(batch, timeout)` sadece
       o pencerede TEK MESAJ BILE gelmediyse TimeoutError atar; kismi batch
       donduren cagri istisna ATMAZ (bkz. nats-py `_fetch_n`: NO_MESSAGES
       gelince eldekini dondurur). Sahada ~1000 msg/sn akarken 5 saniyelik
       BOS bir pencere pratikte hic olusmaz — yani drenaj bitse bile gecis
       tetiklenmez ve kalicilastirma sonsuza kadar RAW'da kalirdi. Bu
       yuzden gecis artik `backlog == 0` olcumunde de deneniyor.

    2) YANLIS POZITIF, yani OLCUM KAYBI. Timeout "teslim edilebilir mesaj
       yok" demek; "is bitti" demek DEGIL. Teslim edilmis ama ack'lenmemis
       mesajlar sunucuda `num_ack_pending` icinde durur ve ancak `ack_wait`
       (60 sn) dolunca yeniden teslim edilir. Somut yol: drenajin sonunda
       bir batch `_persist_batch` icinde `SQLAlchemyError` ile duser ->
       hicbiri ack'lenmez -> dis `except` yeniden baglanir -> ilk fetch 5
       sn'de bos doner (num_pending = 0) -> eski kod GECIS yapip
       `_eski_raw_durable_temizle` ile durable'i SILERDI. O 500 olcum
       yazilmadan ve YENIDEN TESLIM EDILEMEDEN yok olurdu.

    Bilgi okunamazsa (ag/sunucu) `False` doner: karar VERILMEZ, bir sonraki
    turda tekrar sorulur. Guvenli taraf gecisi ertelemektir.
    """
    from nats.js.errors import NotFoundError

    try:
        bilgi = await js.consumer_info(stream, durable)
    except NotFoundError:
        # Durable YOK — devralinacak birikim de yok (temiz kurulum ya da
        # yarim kalmis bir gecisin ardindan). Drenaj tanim geregi bitti.
        return True
    except Exception:  # noqa: BLE001
        logger.debug("telemetry_persist_drenaj_sorgusu_basarisiz", exc_info=True)
        return False

    bekleyen = int(getattr(bilgi, "num_pending", 0) or 0)
    ack_bekleyen = int(getattr(bilgi, "num_ack_pending", 0) or 0)
    if bekleyen or ack_bekleyen:
        logger.debug(
            "telemetry_persist_drenaj_suruyor durable=%s bekleyen=%d ack_bekleyen=%d",
            durable,
            bekleyen,
            ack_bekleyen,
        )
        return False
    return True


async def _raw_drenaj_bitti_mi(js) -> bool:  # noqa: ANN001
    return await _drenaj_bitti_mi(
        js,
        stream=settings.nats_stream_telemetry_raw,
        durable=settings.nats_consumer_telemetry_persist,
    )


async def _normalized_drenaj_bitti_mi(js) -> bool:  # noqa: ANN001
    return await _drenaj_bitti_mi(
        js,
        stream=settings.nats_stream_telemetry_normalized,
        durable=settings.nats_consumer_telemetry_persist_normalized,
    )


async def _ayrik_hatlar_drenaj_bitti_mi(js) -> bool:  # noqa: ANN001
    """Oncelikli VE toplu durable'larin IKISI de bosaldi mi?"""
    norm_stream = settings.nats_stream_telemetry_normalized
    for durable in (
        settings.nats_consumer_telemetry_persist_prio,
        settings.nats_consumer_telemetry_persist_bulk,
    ):
        if not await _drenaj_bitti_mi(js, stream=norm_stream, durable=durable):
            return False
    return True


async def _normalized_tum_drenaj_bitti_mi(js) -> bool:  # noqa: ANN001
    """NORMALIZED tarafindaki TUM durable'lar (tek hat + iki hat) bosaldi mi?

    Geri donus (`raw`) hepsini SILIYOR; hangisinin var oldugu topolojiye
    gore degistigi icin uc durable da tek tek sorulur. Biri bile doluysa
    geri donus ertelenir — silinen bir durable'daki olcumler geri getirilemez.
    """
    return await _normalized_drenaj_bitti_mi(js) and await _ayrik_hatlar_drenaj_bitti_mi(js)


# Gecis adlari — `_gecis_gerekli_mi` bunlardan birini doner, `_gecisi_uygula`
# uygular. Tespit ile UYGULAMA bilerek AYRIK: uygulama yalnizca TUM
# abonelikler kapatildiktan sonra calisir, boylece "iki abonelik ayni anda
# acik kalmasin" kurali yapisal olarak garanti edilir.
GECIS_CUTOVER = "cutover"
GECIS_GERI_DONUS = "geri_donus"
GECIS_HAT_AYRIMI = "hat_ayrimi"
GECIS_TEK_HAT = "tek_hat"


async def _gecis_gerekli_mi(js, kaynak: str, hatlar: list[Hat]) -> str | None:  # noqa: ANN001
    """Bir gecisin vakti geldiyse ADINI doner, degilse None.

    HER GECISIN ON KOSULU AYNI: terk edilecek durable(lar) SILINECEGI icin
    once BOSALMALI. `_drenaj_bitti_mi` bunu sunucunun `num_pending` VE
    `num_ack_pending` sayaclarindan sorar (fetch timeout'u iki yonden de
    yanilticidir — bkz. o fonksiyonun docstring'i).
    """
    tercih = _kaynak_tercihi()
    if kaynak == KAYNAK_RAW:
        if tercih == KAYNAK_AUTO and await _raw_drenaj_bitti_mi(js):
            return GECIS_CUTOVER
        return None

    # kaynak == NORMALIZED
    if tercih == KAYNAK_RAW:
        if await _normalized_tum_drenaj_bitti_mi(js):
            return GECIS_GERI_DONUS
        return None

    siniflar = {h.sinif for h in hatlar}
    if settings.telemetry_persist_split_enabled and siniflar == {SINIF_KARMA}:
        if await _normalized_drenaj_bitti_mi(js):
            return GECIS_HAT_AYRIMI
        return None
    if not settings.telemetry_persist_split_enabled and SINIF_KARMA not in siniflar:
        if await _ayrik_hatlar_drenaj_bitti_mi(js):
            return GECIS_TEK_HAT
        return None
    return None


async def _gecisi_uygula(js, gecis: str) -> None:  # noqa: ANN001
    """Durable cerrahisi. CAGIRAN TUM ABONELIKLERI KAPATMIS OLMALIDIR."""
    if gecis == GECIS_CUTOVER:
        await _cutover(js)
    elif gecis == GECIS_GERI_DONUS:
        await _geri_donus(js)
    elif gecis == GECIS_HAT_AYRIMI:
        await _hat_ayrimina_gec(js)
    elif gecis == GECIS_TEK_HAT:
        await _tek_hatta_don(js)


async def _durable_olustur(  # noqa: ANN001
    js, *, stream: str, durable: str, deliver_policy, opt_start_time=None, filtreler=None
) -> None:
    """Durable YOKSA olusturur; VARSA DOKUNMAZ (konumu ezilmez)."""
    if await _consumer_var_mi(js, stream, durable):
        return
    await js.add_consumer(
        stream,
        config=_consumer_cfg(
            durable=durable,
            deliver_policy=deliver_policy,
            opt_start_time=opt_start_time,
            filtreler=filtreler,
        ),
    )


async def _ayrik_durablelari_olustur(js, *, deliver_policy, opt_start_time=None) -> None:  # noqa: ANN001
    """Oncelikli + toplu durable'larini olusturur.

    ONCELIKLI ONCE: iki cagri arasinda surec olurse (yeniden baslatma,
    OOM) yalnizca biri kurulmus olur. Yarim kalan tarafin ONCELIKLI hat
    olmasi, ariza sinifinin tuketicisiz kalmasi demekti.
    """
    norm_stream = settings.nats_stream_telemetry_normalized
    await _durable_olustur(
        js,
        stream=norm_stream,
        durable=settings.nats_consumer_telemetry_persist_prio,
        deliver_policy=deliver_policy,
        opt_start_time=opt_start_time,
        filtreler=_oncelikli_filtreler(),
    )
    await _durable_olustur(
        js,
        stream=norm_stream,
        durable=settings.nats_consumer_telemetry_persist_bulk,
        deliver_policy=deliver_policy,
        opt_start_time=opt_start_time,
        filtreler=[settings.nats_subject_telemetry_normalized_bulk],
    )


async def _normalized_durable_kur(js, *, deliver_policy, opt_start_time=None) -> None:  # noqa: ANN001
    """Yapilandirmanin ISTEDIGI NORMALIZED durable'larini olusturur.

    Hat ayrimi acikken TEK HAT durable'i (v3) HIC yaratilmaz: yaratmak,
    hemen ardindan bosaltilip silinecek gereksiz bir duragi akisa sokmak
    olurdu.
    """
    if settings.telemetry_persist_split_enabled:
        await _ayrik_durablelari_olustur(
            js, deliver_policy=deliver_policy, opt_start_time=opt_start_time
        )
        return
    await _durable_olustur(
        js,
        stream=settings.nats_stream_telemetry_normalized,
        durable=settings.nats_consumer_telemetry_persist_normalized,
        deliver_policy=deliver_policy,
        opt_start_time=opt_start_time,
    )


async def _hatlari_bagla(js, durable_siniflari: list[tuple[str, str]]) -> list[Hat]:  # noqa: ANN001
    """Var olan durable'lara baglanir; Hat listesi doner."""
    norm_stream = settings.nats_stream_telemetry_normalized
    hatlar: list[Hat] = []
    for durable, sinif in durable_siniflari:
        psub = await js.pull_subscribe_bind(consumer=durable, stream=norm_stream)
        hatlar.append(Hat(sinif=sinif, stream=norm_stream, durable=durable, psub=psub))
    return hatlar


async def _normalized_hatlarina_bagla(js) -> list[Hat]:  # noqa: ANN001
    """NORMALIZED tarafinda VAR OLAN topolojiye baglanir.

    Karar defteri yine JetStream'in kendisidir: "hangi durable var?". Ayri
    bir bayrak tutulmuyor — bayrak ile gercek durum ayrisabilir ve ayrisma
    ancak veri kaybi/tekrari olarak fark edilirdi.
    """
    from nats.js.api import DeliverPolicy

    norm_stream = settings.nats_stream_telemetry_normalized
    prio_durable = settings.nats_consumer_telemetry_persist_prio
    bulk_durable = settings.nats_consumer_telemetry_persist_bulk
    v3_durable = settings.nats_consumer_telemetry_persist_normalized

    ayrik_var = await _consumer_var_mi(js, norm_stream, prio_durable) or (
        await _consumer_var_mi(js, norm_stream, bulk_durable)
    )
    tek_hat_var = await _consumer_var_mi(js, norm_stream, v3_durable)
    if not ayrik_var and not tek_hat_var:
        # Hicbiri yok — temiz kurulum. Yapilandirmanin istedigi topolojiyi
        # kur ve ASAGIDAKI dallara birak (ozyineleme yok: kurulum
        # basarisizsa sonsuz dongu degil, acik bir hata istiyoruz).
        await _normalized_durable_kur(js, deliver_policy=DeliverPolicy.NEW)
        ayrik_var = settings.telemetry_persist_split_enabled
        tek_hat_var = not ayrik_var

    if ayrik_var:
        # Yarim kalmis kurulum (bir onceki surecin arasinda olmesi) burada
        # TAMAMLANIR; eksik olan taraf `NEW` ile degil ayni geri sarmayla
        # yaratilir ki ara pencere kaybolmasin.
        geri_sarma = _cutover_geri_sarma_sec()
        await _ayrik_durablelari_olustur(
            js,
            deliver_policy=DeliverPolicy.BY_START_TIME,
            opt_start_time=(
                datetime.now(timezone.utc) - timedelta(seconds=geri_sarma)
            ).isoformat(),
        )
        return await _hatlari_bagla(
            js, [(prio_durable, SINIF_ONCELIKLI), (bulk_durable, SINIF_TOPLU)]
        )

    # TEK HAT donemi. Ayrim acik olsa bile v3 BOSALMADAN bolunmez;
    # bolunme `_gecis_gerekli_mi` -> `_hat_ayrimina_gec` ile olur.
    return await _hatlari_bagla(js, [(v3_durable, SINIF_KARMA)])


async def _kaynaga_bagla(js):  # noqa: ANN001
    """Acilista hangi akistan okunacagini belirler. Donus: (hatlar, kaynak)."""
    from nats.js.api import DeliverPolicy

    tercih = _kaynak_tercihi()
    norm_stream = settings.nats_stream_telemetry_normalized

    # Gecis DAHA ONCE tamamlanmis mi? `raw` tercihi bunu GERI ALMA anlamina
    # gelir ve asagida ayrica ele alinir (bkz. `geri_donus`).
    gecis_yapilmis = False
    for durable in (
        settings.nats_consumer_telemetry_persist_normalized,
        settings.nats_consumer_telemetry_persist_prio,
        settings.nats_consumer_telemetry_persist_bulk,
    ):
        if await _consumer_var_mi(js, norm_stream, durable):
            gecis_yapilmis = True
            break

    if tercih != KAYNAK_RAW and gecis_yapilmis:
        hatlar = await _normalized_hatlarina_bagla(js)
        # Gecis aninda silme adimina yetisemeden crash olmus olabilir; kalmis
        # olan bos RAW durable'i burada temizlenir (rollback tekrar riski).
        await _eski_raw_durable_temizle(js)
        return hatlar, KAYNAK_NORMALIZED

    if tercih == KAYNAK_NORMALIZED:
        logger.warning(
            "telemetry_persist_source=normalized — RAW DRENAJI ATLANIYOR. "
            "Eski durable'da (%s) bekleyen olcumler varsa HIC YAZILMAYACAK. "
            "Bu ayar yalnizca temiz kurulum icindir; mevcut sahada `auto` kullanin.",
            settings.nats_consumer_telemetry_persist,
        )
        await _normalized_durable_kur(js, deliver_policy=DeliverPolicy.NEW)
        return await _normalized_hatlarina_bagla(js), KAYNAK_NORMALIZED

    # GERI DONUS MU, ERTELEME MI?
    #
    # `raw` iki farkli anlama gelir ve ikisi ayni sekilde ele ALINAMAZ:
    #   * gecis HIC yapilmamis  -> sadece ERTELEME. RAW durable'i yerinde,
    #     konumu korunur, yapacak baska bir sey yok (asagidaki FAZ 1).
    #   * gecis yapilmis        -> GERI DONUS. Ama ONCE NORMALIZED durable'lari
    #     BOSALMALI: geri donus onlari siliyor ve icinde yazilmamis olcum
    #     kalabilir. Surec bir sureligine duruk kaldiysa — yani tam da geri
    #     donusun istendigi durumda — o birikim RAW geri sarma penceresinin
    #     (varsayilan 15 dk) DISINDA kalir ve silinmesiyle birlikte
    #     KAYBOLURDU. Dolu ise NORMALIZED'de kaliriz; bosalinca gecisi
    #     `_gecis_gerekli_mi` tamamlar.
    if tercih == KAYNAK_RAW and gecis_yapilmis:
        if await _normalized_tum_drenaj_bitti_mi(js):
            await _geri_donus(js)
            return await _raw_hattina_bagla(js), KAYNAK_RAW
        logger.warning(
            "telemetry_persist_geri_donus_erteleniyor — NORMALIZED akisinda "
            "yazilmamis olcum var; once o bosaltilacak, sonra RAW'a donulecek "
            "(silinmis durable'daki olcumler geri getirilemezdi)",
        )
        return await _normalized_hatlarina_bagla(js), KAYNAK_NORMALIZED

    # FAZ 1 — eski RAW durable'i AYNEN devralinir: ayni isim, ayni stream,
    # ayni konum. JetStream mesajlari kaldigi yerden verir; birikim erir.
    return await _raw_hattina_bagla(js), KAYNAK_RAW


async def _raw_hattina_bagla(js) -> list[Hat]:  # noqa: ANN001
    """RAW akisi TEK HATTIR: ham akista sinif token'i yoktur.

    Ham konu (`e1.telemetry.raw.<gw>`) siniflandirilmamis; ayrimi tag-engine
    yapiyor. Bu yuzden RAW fazinda hat sinifi `karma`dir ve olay kaydinda
    oyle gorunur — "hangi sinif dustu" sorusuna burada durust cevap
    "bilinmiyor, ikisi de olabilir"dir.
    """
    from nats.js.api import DeliverPolicy

    stream = settings.nats_stream_telemetry_raw
    durable = settings.nats_consumer_telemetry_persist
    psub = await _bagla(
        js,
        stream=stream,
        durable=durable,
        # Yalnizca durable HIC YOKSA (temiz kurulum) gecerli: 2 gunluk
        # history'yi replay etmeyip guncelden baslar.
        deliver_policy=DeliverPolicy.NEW,
    )
    return [Hat(sinif=SINIF_KARMA, stream=stream, durable=durable, psub=psub)]


def _geri_sarma_baslangici() -> tuple[int, str]:
    geri_sarma = _cutover_geri_sarma_sec()
    baslangic = datetime.now(timezone.utc) - timedelta(seconds=geri_sarma)
    return geri_sarma, baslangic.isoformat()


async def _geri_donus(js) -> None:  # noqa: ANN001
    """GERI DONUS — NORMALIZED bosaldi, RAW'a don.

    `_cutover`un AYNASI; adim sirasi ve gerekceler birebir ayni:
      1) RAW durable'i olusturulur. Cutover sirasinda SILINMISTI, yani
         yeniden yaratiliyor. `DeliverPolicy.NEW` ile yaratmak, NORMALIZED
         drenaji ile bu an arasindaki olcumleri atlamak olurdu; bu yuzden
         cutover ile AYNI pencere kadar geri sarilir. Tekrar gelenleri
         `processed_messages` defteri eler — pencere zaten (bkz.
         `_cutover_geri_sarma_sec`) defterin omruyle sinirli.
      2) NORMALIZED durable'lari silinir. Birakilsaydi kimse tuketmedigi icin
         birikir ve `auto`ya donuste (ki `auto` compose varsayilanidir)
         birikimin tamami yeniden islenerek CIFT KAYIT uretirdi.

    Abonelikler cagiran tarafindan ZATEN kapatilmistir.
    """
    from nats.js.api import DeliverPolicy

    geri_sarma, baslangic = _geri_sarma_baslangici()
    await _durable_olustur(
        js,
        stream=settings.nats_stream_telemetry_raw,
        durable=settings.nats_consumer_telemetry_persist,
        deliver_policy=DeliverPolicy.BY_START_TIME,
        opt_start_time=baslangic,
    )
    await _normalized_durable_temizle(js)
    logger.warning(
        "telemetry_persist_geri_donus kaynak=normalized->raw durable=%s "
        "geri_sarma=%ds — kalicilastirma yeniden HAM akistan besleniyor",
        settings.nats_consumer_telemetry_persist,
        geri_sarma,
    )


async def _cutover(js) -> None:  # noqa: ANN001
    """FAZ 2 — RAW bosaldi, NORMALIZED'e gec.

    ADIM SIRASI HAYATIDIR:
      1) YENI durable(lar) olusturulur (geri sarilmis baslangicla). Once
         eskisini silmek, aradaki bir hatada hicbir tuketicisi olmayan bir
         pencere birakirdi — o penceredeki olcumler kaybolurdu.
      2) Eski durable silinir (rollback tekrarini onler).

    Iki abonelik ASLA ayni anda acik kalmaz — cift kaydin en olasi kaynagi
    budur ve artik yapisal olarak imkansiz: bu fonksiyon yalnizca TUM
    abonelikler kapatildiktan sonra cagrilir.
    """
    from nats.js.api import DeliverPolicy

    geri_sarma, baslangic = _geri_sarma_baslangici()
    await _normalized_durable_kur(
        js, deliver_policy=DeliverPolicy.BY_START_TIME, opt_start_time=baslangic
    )
    await _eski_raw_durable_temizle(js)
    logger.info(
        "telemetry_persist_cutover kaynak=raw->normalized ayrik_hat=%s "
        "geri_sarma=%ds (tekrar gelen olcumler processed_messages ile elenir)",
        settings.telemetry_persist_split_enabled,
        geri_sarma,
    )


async def _hat_ayrimina_gec(js) -> None:  # noqa: ANN001
    """TEK HAT -> IKI HAT. `_cutover` ile AYNI disiplin: once kur, sonra sil.

    Geri sarma neden yine gerekli: v3 drenaji ile yeni durable'larin
    olusturuldugu an arasinda tag-engine yayin yapmaya devam eder. Geri
    sarmazsak o penceredeki olcumler HIC yazilmaz. Tekrar gelenleri
    `processed_messages` defteri eler.
    """
    from nats.js.api import DeliverPolicy

    geri_sarma, baslangic = _geri_sarma_baslangici()
    await _ayrik_durablelari_olustur(
        js, deliver_policy=DeliverPolicy.BY_START_TIME, opt_start_time=baslangic
    )
    await _normalized_durable_temizle(js, yalnizca_tek_hat=True)
    logger.info(
        "telemetry_persist_hat_ayrimi oncelikli=%s toplu=%s geri_sarma=%ds — "
        "dijital/durum sinyalleri artik analog selinin arkasinda beklemiyor",
        settings.nats_consumer_telemetry_persist_prio,
        settings.nats_consumer_telemetry_persist_bulk,
        geri_sarma,
    )


async def _tek_hatta_don(js) -> None:  # noqa: ANN001
    """IKI HAT -> TEK HAT (`TELEMETRY_PERSIST_SPLIT_ENABLED=false`).

    `_hat_ayrimina_gec`in aynasi. Bu kolun gercekten calisir olmasi sart:
    hicbir sey yapmayan bir geri alma kolu, operatorun "kapattim" sanip
    kapanmamis bir davranisla yasamasi demekti.
    """
    from nats.js.api import DeliverPolicy

    geri_sarma, baslangic = _geri_sarma_baslangici()
    await _durable_olustur(
        js,
        stream=settings.nats_stream_telemetry_normalized,
        durable=settings.nats_consumer_telemetry_persist_normalized,
        deliver_policy=DeliverPolicy.BY_START_TIME,
        opt_start_time=baslangic,
    )
    await _ayrik_durable_temizle(js)
    logger.warning(
        "telemetry_persist_tek_hat durable=%s geri_sarma=%ds — hat ayrimi "
        "KAPATILDI; analog seli ile ariza bayragi yeniden ayni sirada bekliyor",
        settings.nats_consumer_telemetry_persist_normalized,
        geri_sarma,
    )


#: Gecis kosullarinin sunucuya sorulma araligi. Eski kod bunu yalnizca fetch
#: timeout'unda veya `backlog == 0` aninda soruyordu; iki hatta bu kosullar
#: farkli zamanlarda olustugu icin karar artik ZAMANA bagli ve hatlardan
#: bagimsiz. Maliyeti 5 saniyede bir `consumer_info` — ihmal edilebilir.
_GECIS_KONTROL_ARALIK_SEC = 5.0


async def _hat_dongusu(  # noqa: ANN001
    js, hat: Hat, yeniden_kur, handle_bad, timeout_hatasi, bosalma_isareti=None
) -> None:
    """TEK bir hattin fetch/isle/ack dongusu.

    Her hat icin AYRI bir asyncio gorevi olarak calisir. DB isi
    `asyncio.to_thread` ile olay dongusunun disina cikar; boylece toplu
    hattin yavas bir commit'i oncelikli hattin fetch'ini BEKLETMEZ. Bu
    fonksiyonun hat sinifindan haberdar olmasinin tek sebebi OLCUM ve OLAY
    KAYDI — is mantigi iki hatta da birebir aynidir.

    `bosalma_isareti`: hat "onumde bekleyen kalmadi" dedigi anda kaldirilir.
    Gecis gozetmeni bunu bekler; boylece drenajin bitisi bir ZAMANLAYICIYA
    degil hattin kendi olcumune bagli kalir ve gecis ANINDA denenir.
    """
    def _bosaldi() -> None:
        _hat_backlog_yaz(hat.sinif, 0)
        if bosalma_isareti is not None:
            bosalma_isareti.set()

    try:
        await _hat_dongusu_govde(
            js, hat, yeniden_kur, handle_bad, timeout_hatasi, _bosaldi
        )
    finally:
        # Dongu hangi sebeple biterse bitsin gozetmeni UYANDIR: aksi halde
        # kapanista veya bir hatada gozetmen bos yere `_GECIS_KONTROL_ARALIK_SEC`
        # kadar bekler ve kapanis o kadar gecikirdi.
        if bosalma_isareti is not None:
            bosalma_isareti.set()


async def _hat_dongusu_govde(  # noqa: ANN001
    js, hat: Hat, yeniden_kur, handle_bad, timeout_hatasi, _bosaldi
) -> None:
    while not _stop_event.is_set() and not yeniden_kur.is_set():
        try:
            msgs = await hat.psub.fetch(batch=settings.nats_pull_batch_size, timeout=5)
        except (asyncio.TimeoutError, timeout_hatasi):
            # Bu pencerede yeni mesaj yok — NORMAL ve ayni zamanda "backlog
            # bos" demektir; olcume yansitiyoruz ki ekranda bayat bir backlog
            # degeri asili kalmasin.
            _bosaldi()
            # Gozetmene SIRA VER: drenaj isareti daha yeni kalkti, gecis
            # karari bir sonraki fetch'i beklemeden verilebilsin.
            await asyncio.sleep(0)
            await _dusen_mesaj_denetimi(js, hat)
            continue
        if not msgs:
            _bosaldi()
            await asyncio.sleep(0)
            continue
        _batch_started = _time.monotonic()
        # DB isi ayri thread'de (senkron SQLAlchemy event loop'u bloke
        # etmesin). _persist_batch TEK commit yapar.
        ok_msgs, bad_msgs, ok_payloads, outbound_payloads = await asyncio.to_thread(
            _persist_batch, msgs
        )
        # DB commit SONRASI ama NATS ack ONCESI yan etkiler. Process burada
        # crash ederse mesaj redeliver olur; DB idempotency duplicate'i yutar
        # ve dis akis tekrar denenir (veri kaybi yok).
        for payload in ok_payloads:
            try:
                ws_broadcaster.broadcast(payload)
            except Exception:  # noqa: BLE001
                logger.debug("ws_broadcast_failed", exc_info=True)
        if outbound_payloads:
            await asyncio.to_thread(_dispatch_outbound, outbound_payloads)
        # DB + yan etkiler tamam -> iyi/skip mesajlari ack.
        for m in ok_msgs:
            try:
                await m.ack()
            except Exception:  # noqa: BLE001
                logger.debug("js_ack_failed", exc_info=True)
        # Parse/validation hatali mesajlar: DLQ/nak.
        for m in bad_msgs:
            await handle_bad(m)

        # --- Olcum: yetisiyor muyuz? ------------------------------------
        # Backlog'u BEDAVA aliyoruz: JetStream her mesajin metadata'sinda
        # `num_pending` tasir (bu mesajdan SONRA tuketicinin onunde bekleyen
        # adet). Ayrica consumer_info cagrisi yapmaya gerek yok. Son mesaji
        # baz aliyoruz; batch'in en guncel noktasi orasi.
        backlog: int | None = None
        try:
            meta = getattr(msgs[-1], "metadata", None)
            if meta is not None and getattr(meta, "num_pending", None) is not None:
                backlog = int(meta.num_pending)
        except Exception:  # noqa: BLE001
            backlog = None
        _stats_record_batch(
            sinif=hat.sinif,
            size=len(ok_msgs),
            duration=_time.monotonic() - _batch_started,
            backlog=backlog,
            bad=len(bad_msgs),
        )
        _warn_if_backlog_high(hat.sinif, backlog)
        # `backlog == 0` = "bu batch'in ardinda bekleyen yok". Kesintisiz
        # yukte fetch NEREDEYSE HIC timeout vermez (kismi batch de mesaj
        # sayilir), yani yalnizca timeout'a bagli bir drenaj sinyali
        # uretimde HIC olusmazdi.
        if backlog == 0:
            _bosaldi()
            await asyncio.sleep(0)  # gozetmene sira ver (yukaridaki gerekce)
        await _dusen_mesaj_denetimi(js, hat)


async def _gecis_gozetmeni(  # noqa: ANN001
    js, kaynak, hatlar, yeniden_kur, gecis_istegi, bosalma_isareti=None
) -> None:
    """Gecis kosullarini SUNUCUYA sorar; kosul olusunca teardown ister.

    NE ZAMAN SORAR: bir hat "onumde bekleyen kalmadi" dedigi anda (bkz.
    `bosalma_isareti`), ayrica en gec `_GECIS_KONTROL_ARALIK_SEC` saniyede
    bir. Isaret hizli tepki, periyot ise emniyet agidir — isaretin hic
    gelmedigi bir durumda gecis sonsuza kadar ertelenmesin.

    Kosul olustugunda gecisi KENDISI UYGULAMAZ; yalnizca adini yazip
    `yeniden_kur` bayragini kaldirir. Cerrahi (durable olustur/sil) tum
    abonelikler kapandiktan sonra `_consume_loop` icinde yapilir. Boylece
    "iki abonelik ayni anda acik kalmasin" kurali bir DIKKAT meselesi
    olmaktan cikip yapisal hale gelir — cift kaydin en olasi kaynagi buydu.
    """
    while not _stop_event.is_set() and not yeniden_kur.is_set():
        # ONCE SOR, SONRA BEKLE. Ters sirasi iki sorun uretirdi:
        #   * Baglanma aninda ZATEN vadesi gelmis bir gecis, ilk bekleme
        #     penceresi kadar (ya da bir hattin isaret gondermesine kadar)
        #     gereksiz ertelenirdi.
        #   * Karar, gozetmenin hat donguleriyle giristigi ZAMANLAMA YARISINI
        #     kazanmasina baglanirdi — kapanis sirasinda kaybedilen bir yaris
        #     gecisi tamamen atlatabilirdi.
        try:
            gecis = await _gecis_gerekli_mi(js, kaynak, hatlar)
        except Exception:  # noqa: BLE001
            logger.debug("telemetry_persist_gecis_kontrolu_basarisiz", exc_info=True)
            gecis = None
        if gecis:
            gecis_istegi["ad"] = gecis
            logger.info(
                "telemetry_persist_gecis_istegi gecis=%s kaynak=%s hatlar=%s",
                gecis,
                kaynak,
                [h.sinif for h in hatlar],
            )
            yeniden_kur.set()
            return
        # Bir hat "onumde bekleyen kalmadi" der demez yeniden sor; en gec
        # `_GECIS_KONTROL_ARALIK_SEC` sonra periyodik olarak da sorulur
        # (isaretin hic gelmedigi durumlar icin emniyet agi).
        isaret = bosalma_isareti if bosalma_isareti is not None else yeniden_kur
        try:
            await asyncio.wait_for(isaret.wait(), timeout=_GECIS_KONTROL_ARALIK_SEC)
        except asyncio.TimeoutError:
            pass
        if bosalma_isareti is not None:
            bosalma_isareti.clear()


def _consume_loop() -> None:
    """JetStream'den telemetri akisini dinler (bkz. modul docstring'i).

    Durable consumer ile process restart'inda kaldigi yerden devam eder.
    NATS gelmediyse veya bagklanti koparsa kendi icinde exponential backoff
    ile yeniden baglanir.
    """
    try:
        import nats as _nats  # type: ignore[import-not-found]
        from nats.errors import TimeoutError as _NatsTimeoutError  # type: ignore[import-not-found]
    except ImportError:
        logger.error(
            "telemetry_consumer_jetstream_disabled reason=nats_py_missing "
            "(pip install nats-py>=2.6). Telemetri DB'ye YAZILMIYOR!"
        )
        return

    async def _run() -> None:
        backoff = 2
        while not _stop_event.is_set():
            nc = None
            try:
                from app.core.nats_tls import nats_tls_context

                nc = await _nats.connect(
                    servers=[settings.nats_url],
                    connect_timeout=settings.nats_connect_timeout_sec,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=2,
                    name="e1-backend-api-consumer",
                    tls=nats_tls_context(),  # None = TLS kapali (varsayilan)
                )
                js = nc.jetstream()

                async def _handle_bad(msg) -> None:  # noqa: ANN001
                    """Parse/validation hatasi olan TEK mesaj: max_deliver'a gore
                    DLQ'ya tasi + ack, degilse nak ile redeliver."""
                    num_delivered = int(
                        getattr(getattr(msg, "metadata", None), "num_delivered", 1) or 1
                    )
                    is_terminal = num_delivered >= settings.nats_worker_max_deliver
                    logger.warning(
                        "telemetry-consumer-bad-msg subject=%s delivery=%d/%d terminal=%s",
                        getattr(msg, "subject", "?"),
                        num_delivered,
                        settings.nats_worker_max_deliver,
                        is_terminal,
                    )
                    if is_terminal:
                        try:
                            orig_subject = getattr(msg, "subject", "unknown")
                            dlq_subject = f"e1.dlq.backend-api.{orig_subject}"
                            dlq_headers = {
                                "X-DLQ-Reason": "max_deliver_exceeded",
                                "X-DLQ-Service": "backend-api",
                                "X-DLQ-Original-Subject": orig_subject,
                                "X-DLQ-Delivery-Count": str(num_delivered),
                            }
                            await js.publish(dlq_subject, msg.data, headers=dlq_headers)
                            logger.error("telemetry-consumer-dlq subject=%s", dlq_subject)
                            await msg.ack()
                        except Exception:  # noqa: BLE001
                            logger.exception("dlq_publish_failed")
                            try:
                                await msg.nak()
                            except Exception:  # noqa: BLE001
                                logger.debug("js_nak_failed", exc_info=True)
                    else:
                        try:
                            await msg.nak()
                        except Exception:  # noqa: BLE001
                            logger.debug("js_nak_failed", exc_info=True)

                # Durable PULL consumer.
                #
                # NEDEN pull (push degil): push consumer'da NATS mesajlari sunucu
                # hizinda client socket'ine iter; backend her mesajda senkron DB
                # yazimi yaptigi icin socket write buffer'i dolar ve NATS
                # "Slow Consumer Detected" ile BAGLANTIYI DUSURUR. 200 cihaz
                # yukunde bu dongu cihaz durumunu (communication_status) kesik
                # kesik gunceller -> yeni cihazlar UNKNOWN kalir. Pull'da backend
                # kendi hizinda fetch(batch) yapar; slow-consumer imkansiz.
                #
                # Hangi akisa baglanacagi ve gecisin hangi fazda oldugu
                # `_kaynaga_bagla` icinde belirlenir (bkz. modul docstring'i).
                #
                # HER HAT KENDI DONGUSUNDE. `asyncio.gather` ile paralel
                # calisirlar ve DB isi `asyncio.to_thread` ile olay
                # dongusunun DISINDA yapilir: toplu hattin commit'i
                # saniyelerce assa bile oncelikli hat ayni anda fetch eder,
                # yazar, ack'ler ve WS'e yayar. Izolasyonun somut yeri burasi.
                while not _stop_event.is_set():
                    hatlar, kaynak = await _kaynaga_bagla(js)
                    logger.info(
                        "telemetry_consumer_running mode=pull kaynak=%s hatlar=%s url=%s",
                        kaynak,
                        [(h.sinif, h.durable) for h in hatlar],
                        settings.nats_url,
                    )
                    _stats_update(source=kaynak, connected=True, last_error=None)
                    backoff = 2  # connect basarili — backoff sifirla
                    # Gecis istegi: gozetmen bunu set eder, TUM hat donguleri
                    # cikar, abonelikler kapanir ve durable cerrahisi ANCAK
                    # ondan sonra yapilir. "Iki abonelik ayni anda acik
                    # kalmasin" kurali boylece yapisal olarak garanti edilir.
                    gecis_istegi: dict[str, str | None] = {"ad": None}
                    yeniden_kur = asyncio.Event()
                    # Hatlar "onumde bekleyen kalmadi" dedigi anda kaldirilir;
                    # gozetmen gecisi ZAMANLAYICI beklemeden dener.
                    bosalma_isareti = asyncio.Event()
                    gorevler = [
                        asyncio.create_task(
                            _hat_dongusu(
                                js,
                                hat,
                                yeniden_kur,
                                _handle_bad,
                                _NatsTimeoutError,
                                bosalma_isareti,
                            )
                        )
                        for hat in hatlar
                    ]
                    gorevler.append(
                        asyncio.create_task(
                            _gecis_gozetmeni(
                                js,
                                kaynak,
                                hatlar,
                                yeniden_kur,
                                gecis_istegi,
                                bosalma_isareti,
                            )
                        )
                    )
                    try:
                        await asyncio.gather(*gorevler)
                    finally:
                        yeniden_kur.set()
                        for gorev in gorevler:
                            gorev.cancel()
                        for hat in hatlar:
                            await hat.kapat()
                    if _stop_event.is_set():
                        break
                    if gecis_istegi["ad"]:
                        await _gecisi_uygula(js, gecis_istegi["ad"])
                    # Dongu basa doner: `_kaynaga_bagla` yeni topolojiyi bulur.
            except Exception as exc:  # noqa: BLE001
                if _stop_event.is_set():
                    break
                logger.warning(
                    "telemetry_consumer_reconnect error=%s backoff=%ds url=%s",
                    exc,
                    backoff,
                    settings.nats_url,
                )
                with _stats_lock:
                    _stats["connected"] = False
                    _stats["last_error"] = str(exc)[:300]
                    _stats["reconnects"] += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if nc is not None:
                    try:
                        await nc.drain()
                    except Exception:  # noqa: BLE001
                        logger.debug("js_drain_error", exc_info=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _stats_update(running=True, connected=False, last_error=None)
    _thread = threading.Thread(
        target=_consume_loop, name="telemetry-consumer-jetstream", daemon=True
    )
    _thread.start()


def stop() -> None:
    _stop_event.set()
