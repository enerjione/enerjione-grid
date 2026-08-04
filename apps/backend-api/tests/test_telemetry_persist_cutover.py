"""Kalicilastirmanin RAW -> NORMALIZED gecisi — DAVRANIS testleri.

NE KORUNUYOR
------------
Kalicilastirma tuketicisi HTTP surecinden cikarilip tag-engine cikisina
baglandi. Bu gecisin uc kirilma noktasi var ve ucu de SESSIZ arizalar:

  1. VERI KAYBI   — eski RAW durable'inda yazilmamis olcumler birikmisti
                    (olcum: 6.575.319). Yeni bir durable "akisin basindan"
                    degil "simdiden" baslarsa o olcumler HIC yazilmaz ve
                    kimse fark etmez: ekranda hata yok, sadece gecmiste
                    delik var.
  2. CIFT KAYIT   — gecis aninda hem eski hem yeni tuketici calisirsa ya da
                    geri sarma penceresi idempotency defterinden uzun olursa
                    ayni olcum iki kez yazilir. `telemetry` tablosunda dogal
                    anahtar YOK; ON CONFLICT bunu yakalamaz.
  3. ALAN KAYBI   — normalize edilmis payload `dnp3_flags`,
                    `device_event_at`, `timestamp_quality` tasimazsa
                    kalicilastirma bu akistan beslendigi anda: her nokta
                    seviyesi `invalid` TUM CIHAZI offline gosterir (harita
                    kirmizi) ve SOE/ariza-suresi kolonlari sessizce NULL
                    kalir.

Testler KAYNAK METNI ARAMAZ; gercek `_consume_loop`u sahte bir JetStream
uzerinde kostururlar. Bu depoda kaynak-grep eden testler davranis bozukken
yesil kalmisti.
"""

from __future__ import annotations

import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import nats
import pytest
from nats.js.api import DeliverPolicy
from nats.js.errors import NotFoundError

from app.core.config import settings
from app.schemas.telemetry import TelemetryIn
from app.services import telemetry_consumer as tc
from app.services.tag_engine_service import map_quality_to_status_scoped

REPO = Path(__file__).resolve().parents[3]

# tag-engine ayri bir servis (kendi requirements'i var, DB bagimliligi yok).
# Normalize edilmis payload'in SOZLESMESI iki servis arasinda paylasildigi
# icin testi burada tutuyoruz: alan dusurulurse kiran taraf BURASI.
_TAG_ENGINE = REPO / "apps" / "tag-engine"
if str(_TAG_ENGINE) not in sys.path:
    sys.path.insert(0, str(_TAG_ENGINE))
from tag_engine.main import _build_processed_payload  # noqa: E402


# ==========================================================================
# Sahte JetStream — gercek `_consume_loop` bunun uzerinde kosar.
# ==========================================================================


class _SahtePsub:
    """Pull subscription. `fetch` senaryodan beslenir."""

    def __init__(self, ad: str, gunluk: list, senaryo: list) -> None:
        self.ad = ad
        self._gunluk = gunluk
        self._senaryo = senaryo
        self.abonelik_acik = True

    async def fetch(self, batch: int = 1, timeout: float = 5):
        self._gunluk.append(("fetch", self.ad))
        if not self._senaryo:
            tc._stop_event.set()
            raise nats.errors.TimeoutError
        adim = self._senaryo.pop(0)
        if adim == "dur":
            # Bos fetch + dongu sonu. `bos` gibi bu da DRENAJ SINYALIDIR.
            tc._stop_event.set()
            raise nats.errors.TimeoutError
        if adim == "bos":
            raise nats.errors.TimeoutError
        if adim == "mesaj_dur":
            # Mesaj DONER ve dongu biter — drenaj sinyali URETMEDEN. "birikim
            # suruyor" senaryolarini kurmanin tek dogru yolu budur.
            tc._stop_event.set()
            return [_SahteMsg()]
        return adim  # mesaj listesi

    async def unsubscribe(self) -> None:
        self.abonelik_acik = False
        self._gunluk.append(("unsubscribe", self.ad))


class _SahteConsumerInfo:
    """Sunucunun gecis kararini verdigi iki sayac."""

    def __init__(self, num_pending: int = 0, num_ack_pending: int = 0) -> None:
        self.num_pending = num_pending
        self.num_ack_pending = num_ack_pending


class _SahteConsumerInfoAzalan:
    """Ilk sorguda DOLU, sonrakilerde bos — drenajin bitisini taklit eder."""

    def __init__(self, ilk_pending: int) -> None:
        self._kalan = [ilk_pending]
        self.num_ack_pending = 0

    @property
    def num_pending(self) -> int:
        return self._kalan.pop(0) if self._kalan else 0


class _SahteJS:
    def __init__(
        self,
        mevcut: set[tuple[str, str]],
        senaryolar: dict,
        *,
        sayaclar: dict | None = None,
    ) -> None:
        self.mevcut = set(mevcut)          # (stream, durable)
        self.senaryolar = senaryolar       # durable -> fetch adimlari
        # durable -> _SahteConsumerInfo. Verilmeyen durable "bos" sayilir.
        self.sayaclar: dict = sayaclar or {}
        self.gunluk: list = []
        self.cfg: dict = {}
        self.psub: dict[str, _SahtePsub] = {}

    async def consumer_info(self, stream: str, consumer: str, timeout=None):  # noqa: ANN001
        if (stream, consumer) not in self.mevcut:
            raise NotFoundError()
        self.gunluk.append(("consumer_info", stream, consumer))
        return self.sayaclar.get(consumer) or _SahteConsumerInfo()

    async def add_consumer(self, stream: str, config=None, timeout=None, **kw):  # noqa: ANN001
        self.gunluk.append(("add_consumer", stream, config.durable_name))
        self.cfg[(stream, config.durable_name)] = config
        self.mevcut.add((stream, config.durable_name))
        return object()

    async def pull_subscribe_bind(self, consumer=None, stream=None, **kw):  # noqa: ANN001
        self.gunluk.append(("bind", stream, consumer))
        p = _SahtePsub(consumer, self.gunluk, list(self.senaryolar.get(consumer, [])))
        self.psub[consumer] = p
        return p

    async def delete_consumer(self, stream: str, consumer: str) -> bool:
        # Gercek sunucu gibi: OLMAYAN consumer'i silmek HATA verir. Sahte
        # sessizce basarili olsaydi "silme cagrildi mi" olcen testler
        # anlamsizlasirdi.
        if (stream, consumer) not in self.mevcut:
            raise NotFoundError()
        self.gunluk.append(("delete_consumer", stream, consumer))
        self.mevcut.discard((stream, consumer))
        return True


class _SahteNC:
    def __init__(self, js: _SahteJS) -> None:
        self._js = js

    def jetstream(self) -> _SahteJS:
        return self._js

    async def drain(self) -> None:
        return None


class _SahteMsg:
    """Metadata'siz mesaj: backlog okunamaz (None) — testte DB'ye gidilmez."""

    data = b"{}"
    subject = "e1.telemetry.raw.GW-001"
    metadata = None


class _SahteMsgYetisen:
    """`num_pending=0` tasiyan mesaj — "bu batch'in ardinda bekleyen yok".

    Kesintisiz yuk altinda gercek hayatta gorulen sey budur: fetch HER ZAMAN
    mesaj dondurur (dolayisiyla TimeoutError HIC olusmaz) ama tuketici
    yetistigi icin son mesajin metadata'si sifir bekleyen bildirir.
    """

    data = b"{}"
    subject = "e1.telemetry.raw.GW-001"

    class metadata:  # noqa: N801
        num_pending = 0


RAW_STREAM = settings.nats_stream_telemetry_raw
NORM_STREAM = settings.nats_stream_telemetry_normalized
RAW_DURABLE = settings.nats_consumer_telemetry_persist
NORM_DURABLE = settings.nats_consumer_telemetry_persist_normalized


def _kostur(monkeypatch, js: _SahteJS, *, kaynak_tercihi: str = "auto") -> None:
    """Gercek `_consume_loop`u sahte JetStream uzerinde tek tur kosturur."""
    monkeypatch.setattr(settings, "telemetry_persist_source", kaynak_tercihi)

    async def _sahte_connect(**kw):  # noqa: ANN001
        return _SahteNC(js)

    monkeypatch.setattr(nats, "connect", _sahte_connect)
    # DB'ye HIC gidilmesin: bu dosya gecis mekanigini olcuyor, persist'i degil.
    monkeypatch.setattr(tc, "_persist_batch", lambda msgs: ([], [], [], []))

    tc._stop_event.clear()
    try:
        tc._consume_loop()
    finally:
        tc._stop_event.set()


@pytest.fixture(autouse=True)
def _temiz_durum():
    with tc._stats_lock:
        tc._stats.update(running=False, connected=False, source=None, backlog=None)
    yield
    tc._stop_event.clear()


def _sira(gunluk: list, *parca: str) -> int:
    """Gunlukte verilen parcalari ICEREN ilk kaydin indeksi."""
    for i, kayit in enumerate(gunluk):
        if all(p in [str(x) for x in kayit] for p in parca):
            return i
    raise AssertionError(f"gunlukte yok: {parca} — gunluk={gunluk}")


def _var_mi(gunluk: list, *parca: str) -> bool:
    try:
        _sira(gunluk, *parca)
        return True
    except AssertionError:
        return False


# ==========================================================================
# 1) IDEMPOTENCY DEFTERI — tasima sirasinda SIFIRLANAMAZ
# ==========================================================================


def test_dedup_defteri_anahtari_DEGISMEDI():
    """`processed_messages.consumer_name` gecisin ikinci savunma hattidir.

    Geri sarma penceresinde tekrar gelen olcumleri ELEYEN sey bu defter.
    Isim degisirse defter bir anda BOS gorunur ve pencere boyunca gelen HER
    olcum ikinci kez yazilir — hem de tam gecis aninda.
    """
    assert tc.CONSUMER_NAME == "backend-api.telemetry-persister"


def test_geri_sarma_dedup_defterinin_OMRUNU_ASAMAZ(monkeypatch):
    """Defterden uzun geri sarmak = dedup kaydi SILINMIS olcumu tekrar getirmek."""
    monkeypatch.setattr(settings, "processed_messages_retention_hours", 2)
    monkeypatch.setattr(settings, "telemetry_persist_cutover_overlap_sec", 999_999)
    assert tc._cutover_geri_sarma_sec() == 2 * 3600

    # Tavanin altindaki deger AYNEN gecer (gereksiz kirpma yok).
    monkeypatch.setattr(settings, "telemetry_persist_cutover_overlap_sec", 900)
    assert tc._cutover_geri_sarma_sec() == 900


# ==========================================================================
# 2) FAZ 1 — eski durable AYNEN devralinir (6.5M birikim kaybolmaz)
# ==========================================================================


def test_faz1_eski_durable_AYNEN_devralinir(monkeypatch):
    """Guncelleme sonrasi ilk baglanti: eski RAW durable'i YENIDEN YARATILMAZ.

    `add_consumer` cagrilsaydi (ya da `pull_subscribe(config=...)` ile config
    gonderilseydi) sunucu ya reddederdi ya da konumu ezilirdi; her iki halde
    de birikmis 6.5M olcum yazilmadan kaybolurdu.
    """
    js = _SahteJS({(RAW_STREAM, RAW_DURABLE)}, {RAW_DURABLE: ["dur"]})
    _kostur(monkeypatch, js, kaynak_tercihi="raw")

    assert _var_mi(js.gunluk, "bind", RAW_STREAM, RAW_DURABLE)
    assert not _var_mi(js.gunluk, "add_consumer"), (
        "mevcut durable yeniden yaratildi — konumu ezilir, birikim kaybolur"
    )
    assert tc.get_stats()["source"] == tc.KAYNAK_RAW


def test_birikim_BITMEDEN_gecis_YAPILMAZ(monkeypatch):
    """Fetch mesaj donduruyorsa (birikim suruyor) NORMALIZED'e GECILMEZ.

    Gecis erken yapilirsa RAW'da bekleyen olcumler hicbir zaman yazilmaz.
    Bu, "hicbir olcum kaybolmayacak" sartinin tek kilidi.
    """
    js = _SahteJS(
        {(RAW_STREAM, RAW_DURABLE)},
        {RAW_DURABLE: [[_SahteMsg()], [_SahteMsg()], "mesaj_dur"]},
    )
    _kostur(monkeypatch, js, kaynak_tercihi="auto")

    assert not _var_mi(js.gunluk, "add_consumer", NORM_STREAM, NORM_DURABLE), (
        "birikim varken NORMALIZED durable'i acildi — bekleyen olcumler yazilmadan atlanir"
    )
    assert not _var_mi(js.gunluk, "delete_consumer"), (
        "birikim varken eski durable silindi — bekleyen olcumler KAYBOLUR"
    )
    assert tc.get_stats()["source"] == tc.KAYNAK_RAW


# ==========================================================================
# 3) FAZ 2 — drenaj bitince gecis; SIRA ve TEKILLIK
# ==========================================================================


def test_drenaj_bitince_NORMALIZED_e_gecilir_ve_SIRA_dogru(monkeypatch):
    """Bos fetch = drenaj bitti. Gecis sirasi: yeni durable -> abonelik kes -> eskisini sil."""
    js = _SahteJS(
        {(RAW_STREAM, RAW_DURABLE)},
        {RAW_DURABLE: ["bos"], NORM_DURABLE: ["dur"]},
    )
    _kostur(monkeypatch, js, kaynak_tercihi="auto")

    i_yeni = _sira(js.gunluk, "add_consumer", NORM_STREAM, NORM_DURABLE)
    i_bind = _sira(js.gunluk, "bind", NORM_STREAM, NORM_DURABLE)
    i_kes = _sira(js.gunluk, "unsubscribe", RAW_DURABLE)
    i_sil = _sira(js.gunluk, "delete_consumer", RAW_STREAM, RAW_DURABLE)

    assert i_yeni < i_bind < i_kes < i_sil, (
        "gecis sirasi bozuk: eski durable yeni durable HAZIR OLMADAN kapatilirsa "
        f"tuketicisiz bir pencere olusur ve o penceredeki olcumler kaybolur. gunluk={js.gunluk}"
    )
    assert tc.get_stats()["source"] == tc.KAYNAK_NORMALIZED


def test_gecis_sonrasi_ESKI_abonelikten_okuma_YOK(monkeypatch):
    """Iki abonelik ayni anda beslenirse ayni olcum iki kez yazilir."""
    js = _SahteJS(
        {(RAW_STREAM, RAW_DURABLE)},
        {RAW_DURABLE: ["bos", [_SahteMsg()]], NORM_DURABLE: ["dur"]},
    )
    _kostur(monkeypatch, js, kaynak_tercihi="auto")

    i_kes = _sira(js.gunluk, "unsubscribe", RAW_DURABLE)
    sonraki_raw_fetch = [
        i for i, k in enumerate(js.gunluk)
        if k[0] == "fetch" and k[1] == RAW_DURABLE and i > i_kes
    ]
    assert not sonraki_raw_fetch, (
        f"gecis sonrasi eski abonelikten okunmaya devam edildi -> CIFT KAYIT. gunluk={js.gunluk}"
    )
    assert js.psub[RAW_DURABLE].abonelik_acik is False


def test_yeni_durable_GERI_SARILARAK_acilir(monkeypatch):
    """Geri sarilmazsa tag-engine gecikmesi kadarlik pencere HIC yazilmaz."""
    monkeypatch.setattr(settings, "processed_messages_retention_hours", 2)
    monkeypatch.setattr(settings, "telemetry_persist_cutover_overlap_sec", 900)
    js = _SahteJS(
        {(RAW_STREAM, RAW_DURABLE)},
        {RAW_DURABLE: ["bos"], NORM_DURABLE: ["dur"]},
    )
    simdi = datetime.now(timezone.utc)
    _kostur(monkeypatch, js, kaynak_tercihi="auto")

    cfg = js.cfg[(NORM_STREAM, NORM_DURABLE)]
    assert cfg.deliver_policy == DeliverPolicy.BY_START_TIME, (
        "yeni durable 'simdiden' baslatilirsa drenaj ile gecis arasindaki "
        "olcumler yazilmadan atlanir"
    )
    assert cfg.opt_start_time, "opt_start_time bos — geri sarma yapilmamis"
    baslangic = datetime.fromisoformat(cfg.opt_start_time)
    beklenen = simdi - timedelta(seconds=900)
    assert abs((baslangic - beklenen).total_seconds()) < 30, (
        f"geri sarma penceresi yanlis: {baslangic} vs {beklenen}"
    )


def test_gecis_TAMAMLANMISSA_tekrar_edilmez(monkeypatch):
    """Surec yeniden basladiginda gecis bastan yapilmaz; kaldigi yerden devam.

    Yeniden yapilsaydi durable her acilista geri sarilir ve her restart bir
    tekrar-isleme dalgasi uretirdi.
    """
    js = _SahteJS(
        {(RAW_STREAM, RAW_DURABLE), (NORM_STREAM, NORM_DURABLE)},
        {NORM_DURABLE: ["dur"]},
    )
    _kostur(monkeypatch, js, kaynak_tercihi="auto")

    assert not _var_mi(js.gunluk, "add_consumer"), (
        "mevcut NORMALIZED durable'i yeniden yaratildi — konumu geri sarilir"
    )
    assert _var_mi(js.gunluk, "bind", NORM_STREAM, NORM_DURABLE)
    # Gecis aninda silme adimina yetisemeden crash olmus olabilir: artik bos
    # olan RAW durable'i burada temizlenir. Kalirsa bir surum geri donusunde
    # eski kod onu kaldigi yerden okur ve ZATEN yazilmis olcumleri tekrarlar.
    assert _var_mi(js.gunluk, "delete_consumer", RAW_STREAM, RAW_DURABLE)
    assert tc.get_stats()["source"] == tc.KAYNAK_NORMALIZED


def test_kaynak_raw_secilirse_gecis_ERTELENIR(monkeypatch):
    """Teshis kacisi: `raw` iken drenaj bitse bile gecis yapilmaz."""
    js = _SahteJS({(RAW_STREAM, RAW_DURABLE)}, {RAW_DURABLE: ["bos", "dur"]})
    _kostur(monkeypatch, js, kaynak_tercihi="raw")

    assert not _var_mi(js.gunluk, "add_consumer", NORM_STREAM, NORM_DURABLE)
    assert not _var_mi(js.gunluk, "delete_consumer")
    assert tc.get_stats()["source"] == tc.KAYNAK_RAW


def test_ack_BEKLEYEN_varken_gecis_YAPILMAZ(monkeypatch):
    """Teslim edilmis ama ack'lenmemis mesaj varken gecis OLCUM KAYBEDER.

    Bos bir fetch penceresi "is bitti" demek DEGIL: bir batch DB hatasiyla
    dustuyse o mesajlar sunucuda `num_ack_pending` icinde durur ve ancak
    `ack_wait` (60 sn) dolunca yeniden teslim edilir. O aralikta gecis
    yapilirsa eski durable SILINIR ve o mesajlar yazilmadan, yeniden
    teslim de edilemeden yok olur.
    """
    js = _SahteJS(
        {(RAW_STREAM, RAW_DURABLE)},
        {RAW_DURABLE: ["bos", "dur"]},
        sayaclar={RAW_DURABLE: _SahteConsumerInfo(num_pending=0, num_ack_pending=7)},
    )
    _kostur(monkeypatch, js, kaynak_tercihi="auto")

    assert not _var_mi(js.gunluk, "add_consumer", NORM_STREAM, NORM_DURABLE)
    assert not _var_mi(js.gunluk, "delete_consumer", RAW_STREAM, RAW_DURABLE), (
        "ack bekleyen mesajlar varken eski durable silindi — yeniden teslim "
        "edilecek olcumler KAYBOLUR"
    )
    assert tc.get_stats()["source"] == tc.KAYNAK_RAW


def test_KESINTISIZ_YUKTE_de_gecis_yapilir(monkeypatch):
    """Gecis fetch TIMEOUT'una bagli olamaz — uretimde timeout hic olusmaz.

    `fetch(batch, timeout)` yalnizca pencerede TEK MESAJ BILE gelmediyse
    TimeoutError atar; kismi batch donduren cagri istisna atmaz. Sahada
    ~1000 msg/sn akarken 5 saniyelik bos bir pencere pratikte olusmaz, yani
    yalnizca timeout'a bagli bir gecis kosulu HIC saglanmaz ve
    kalicilastirma sonsuza kadar RAW'da kalirdi (sessiz basarisizlik: ekran
    "calisiyor" der, mimari degisikligi hic gerceklesmez).
    """
    js = _SahteJS(
        {(RAW_STREAM, RAW_DURABLE)},
        {
            # Fetch HER TURDA mesaj donduruyor: TimeoutError yolu HIC calismaz.
            RAW_DURABLE: [
                [_SahteMsgYetisen()],
                [_SahteMsgYetisen()],
                [_SahteMsgYetisen()],
            ],
            NORM_DURABLE: ["dur"],
        },
    )
    _kostur(monkeypatch, js, kaynak_tercihi="auto")

    assert _var_mi(js.gunluk, "add_consumer", NORM_STREAM, NORM_DURABLE), (
        f"timeout olusmadan gecis yapilmadi — uretimde gecis HIC olmazdi. "
        f"gunluk={js.gunluk}"
    )
    raw_fetch = [k for k in js.gunluk if k[0] == "fetch" and k[1] == RAW_DURABLE]
    assert len(raw_fetch) == 1, (
        f"birikim bittigi ILK batch'te gecilmedi (raw fetch={len(raw_fetch)})"
    )
    assert tc.get_stats()["source"] == tc.KAYNAK_NORMALIZED


def test_RAW_a_DONULUNCE_normalized_durable_silinir(monkeypatch):
    """`raw`a donus, tuketilmeyen bir NORMALIZED durable BIRAKMAMALI.

    Birakilsaydi: kimse tuketmedigi icin `num_pending` saatlerce buyur;
    tercih tekrar `auto` olunca (ki `auto` compose varsayilanidir —
    container'in bir sonraki yeniden yaratilmasi bunu kendiliginden yapar)
    o birikimin TAMAMI yeniden islenir. Ayni olcumler bu arada RAW
    uzerinden zaten yazilmistir ve dedup defteri 2 saatlik oldugu icin
    tekrari yutamaz -> `telemetry` tablosunda CIFT KAYIT.
    """
    js = _SahteJS(
        {(RAW_STREAM, RAW_DURABLE), (NORM_STREAM, NORM_DURABLE)},
        {RAW_DURABLE: ["dur"]},
    )
    _kostur(monkeypatch, js, kaynak_tercihi="raw")

    assert _var_mi(js.gunluk, "delete_consumer", NORM_STREAM, NORM_DURABLE), (
        f"NORMALIZED durable ortada birakildi — `auto`ya donuste cift kayit "
        f"uretir. gunluk={js.gunluk}"
    )
    assert _var_mi(js.gunluk, "bind", RAW_STREAM, RAW_DURABLE)
    assert tc.get_stats()["source"] == tc.KAYNAK_RAW


def test_gecis_SONRASI_raw_a_donus_TUKETICISIZ_PENCERE_BIRAKMAZ(monkeypatch):
    """`raw` geri donusu: once RAW aboneligi acilir, SONRA NORMALIZED silinir.

    Ters sirada iki islem arasinda hicbir tuketicisi olmayan bir pencere
    kalirdi: NORMALIZED durable'i (ve icindeki bekleyen olcumler) silinmis,
    RAW durable'i henuz yaratilmamis. O penceredeki olcumler HIC yazilmazdi.

    Ayrica yeni RAW durable'i `NEW` ile DEGIL, cutover ile ayni pencere kadar
    GERI SARILARAK yaratilmali: cutover sirasinda RAW durable'i silinmisti,
    yani surecin kapali oldugu sure boyunca RAW'a dusen olcumleri yalnizca
    geri sarma kurtarir. Tekrar gelenleri `processed_messages` defteri eler.
    """
    monkeypatch.setattr(settings, "processed_messages_retention_hours", 2)
    monkeypatch.setattr(settings, "telemetry_persist_cutover_overlap_sec", 900)
    js = _SahteJS(
        # RAW durable'i cutover'da SILINMISTI — geri donuste yeniden yaratilir.
        {(NORM_STREAM, NORM_DURABLE)},
        {RAW_DURABLE: ["dur"]},
    )
    simdi = datetime.now(timezone.utc)
    _kostur(monkeypatch, js, kaynak_tercihi="raw")

    assert tc.get_stats()["source"] == tc.KAYNAK_RAW
    i_ac = _sira(js.gunluk, "bind", RAW_STREAM, RAW_DURABLE)
    i_sil = _sira(js.gunluk, "delete_consumer", NORM_STREAM, NORM_DURABLE)
    assert i_ac < i_sil, (
        "NORMALIZED durable'i RAW aboneligi acilmadan silindi -> aradaki "
        f"pencerede hicbir tuketici yok, olcum kaybolur. gunluk={js.gunluk}"
    )

    cfg = js.cfg[(RAW_STREAM, RAW_DURABLE)]
    assert cfg.deliver_policy == DeliverPolicy.BY_START_TIME, (
        "geri donuste RAW durable'i `NEW` ile yaratildi — surecin kapali "
        "oldugu penceredeki olcumler HIC yazilmaz"
    )
    baslangic = datetime.fromisoformat(cfg.opt_start_time)
    assert abs((baslangic - (simdi - timedelta(seconds=900))).total_seconds()) < 30


def test_geri_donus_NORMALIZED_BOSALMADAN_yapilmaz(monkeypatch):
    """Geri donus, NORMALIZED durable'ini SILER — once icinde is kalmamali.

    Geri donusun istendigi tipik durum surecin bir sure duruk kalmasidir;
    tam da o durumda NORMALIZED'de yazilmamis olcumler birikmis olur ve
    bunlar RAW geri sarma penceresinin (varsayilan 15 dk) DISINDA kalir.
    Durable dolu iken silinseydi o olcumler ne yazilmis ne de geri
    getirilebilir olurdu.
    """
    js = _SahteJS(
        {(NORM_STREAM, NORM_DURABLE)},
        {NORM_DURABLE: ["dur"]},
        sayaclar={NORM_DURABLE: _SahteConsumerInfo(num_pending=4_200)},
    )
    _kostur(monkeypatch, js, kaynak_tercihi="raw")

    assert tc.get_stats()["source"] == tc.KAYNAK_NORMALIZED, (
        "NORMALIZED'de is varken RAW'a donuldu"
    )
    assert not _var_mi(js.gunluk, "delete_consumer", NORM_STREAM, NORM_DURABLE), (
        f"dolu NORMALIZED durable'i silindi -> bekleyen olcumler KAYBOLUR. "
        f"gunluk={js.gunluk}"
    )
    assert not _var_mi(js.gunluk, "add_consumer", RAW_STREAM, RAW_DURABLE)


def test_geri_donus_NORMALIZED_BOSALINCA_TAMAMLANIR(monkeypatch):
    """Erteleme kalici olmamali: birikim erirse geri donus kendiliginden biter."""
    js = _SahteJS(
        {(NORM_STREAM, NORM_DURABLE)},
        {NORM_DURABLE: ["bos", "dur"], RAW_DURABLE: ["dur"]},
        sayaclar={NORM_DURABLE: _SahteConsumerInfoAzalan(4_200)},
    )
    _kostur(monkeypatch, js, kaynak_tercihi="raw")

    assert tc.get_stats()["source"] == tc.KAYNAK_RAW, (
        f"birikim bittigi halde geri donus tamamlanmadi. gunluk={js.gunluk}"
    )
    assert _var_mi(js.gunluk, "delete_consumer", NORM_STREAM, NORM_DURABLE)
    assert js.psub[NORM_DURABLE].abonelik_acik is False


def test_gecis_YAPILMAMISKEN_raw_MEVCUT_durable_i_EZMEZ(monkeypatch):
    """Duz erteleme: NORMALIZED durable'i yokken `raw` hicbir seyi degistirmez.

    Geri sarma YALNIZCA geri donuse ozgudur; erteleme yolunda mevcut RAW
    durable'inin konumu AYNEN korunmali, yoksa birikim tekrar islenirdi.
    """
    js = _SahteJS({(RAW_STREAM, RAW_DURABLE)}, {RAW_DURABLE: ["dur"]})
    _kostur(monkeypatch, js, kaynak_tercihi="raw")

    assert tc.get_stats()["source"] == tc.KAYNAK_RAW
    assert not _var_mi(js.gunluk, "add_consumer"), "mevcut durable yeniden yaratildi"
    assert not _var_mi(js.gunluk, "delete_consumer"), (
        "silinecek bir sey yokken durable silme denendi"
    )


# ==========================================================================
# 4) SOZLESME — normalize edilmis payload persist icin YETERLI mi
# ==========================================================================


def _gateway_payload(**kw) -> dict:
    p = {
        "message_id": "m-1",
        "correlation_id": "c-1",
        "source_gateway": "GW-001",
        "device_code": "DEV-001",
        "signal_key": "master.actual_voltage",
        "signal_data_type": "analog",
        "value": 230.5,
        "value_string": None,
        "quality": "invalid",
        "dnp3_flags": 0x41,
        "source_timestamp": "2026-08-04T10:00:00+00:00",
        "device_event_at": "2026-08-04T09:59:58+00:00",
        "timestamp_quality": "synchronized",
    }
    p.update(kw)
    return p


def test_normalized_payload_persist_ALANLARINI_tasiyor():
    """Kalicilastirma bu akistan beslendigi icin uc alan DUSURULEMEZ."""
    normalized = _build_processed_payload(_gateway_payload())
    okuma = TelemetryIn(**normalized)

    assert okuma.dnp3_flags == 0x41, (
        "dnp3_flags dustu -> kapsam bilgisi kaybolur, tek noktanin hatasi "
        "TUM CIHAZI offline gosterir"
    )
    assert okuma.device_event_at is not None, (
        "device_event_at dustu -> telemetry/history/latest kolonlari NULL kalir"
    )
    assert okuma.timestamp_quality == "synchronized"
    # Zaten tasinan alanlar da regresyona karsi kilitli.
    assert okuma.device_code == "DEV-001"
    assert okuma.signal_key == "master.actual_voltage"
    assert okuma.value == 230.5
    assert okuma.quality == "invalid"


def test_normalized_payload_eski_gateway_de_BOZULMAZ():
    """0.4.x gateway bu alanlari GONDERMEZ; alan yoklugu da bir bilgidir."""
    ham = _gateway_payload()
    for alan in ("dnp3_flags", "device_event_at", "timestamp_quality"):
        ham.pop(alan)
    normalized = _build_processed_payload(ham)
    okuma = TelemetryIn(**normalized)

    assert okuma.dnp3_flags is None
    assert okuma.device_event_at is None
    assert okuma.timestamp_quality is None
    # `dnp3_flags is None` = CIHAZ seviyesi kalite -> `invalid` OFFLINE demek.
    assert normalized["status"] == "offline"


@pytest.mark.parametrize(
    "quality,flags",
    [
        ("invalid", 0x41), ("invalid", None),
        ("restart", 0x08), ("restart", None),
        ("forced", 0x20), ("forced", None),
        ("comm_lost", 0x41), ("comm_lost", None),
        ("good", 0x01), ("good", None),
        ("bad", None), ("offline", None),
    ],
)
def test_normalized_status_backend_karariyla_AYNI(quality, flags):
    """tag-engine'in `status`u backend'in kapsam farkindalikli karariyla ayni olmali.

    Ayrisirsa arsivdeki durum ile ekrandaki durum celisir: ayni olcum icin
    SCADA "online", harita "offline" gosterir.
    """
    normalized = _build_processed_payload(
        _gateway_payload(quality=quality, dnp3_flags=flags)
    )
    beklenen = map_quality_to_status_scoped(quality, dnp3_flags=flags).value
    assert normalized["status"] == beklenen, (
        f"quality={quality} flags={flags}: tag-engine={normalized['status']} "
        f"backend={beklenen}"
    )


# ==========================================================================
# 5) TOPOLOJI — HTTP sureci akis TUKETMEZ
# ==========================================================================


def test_backend_api_HTTP_sureci_arka_plan_ISI_ACMAZ():
    """`backend-api` container'i `api` rolunde: hicbir JetStream tuketicisi acmaz.

    Rol `api` iken `wants_background()` False'dur ve `leader.try_start()`
    kayitli isleri (telemetri tuketicisi dahil) HIC baslatmaz.
    """
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"^\s*SERVICE_ROLE:\s*\$\{E1_BACKEND_ROLE:-(\w+)\}", compose, re.M)
    assert m and m.group(1) == "api", (
        "backend-api varsayilan rolu `api` degil — HTTP sureci telemetri "
        "tuketicisini kendi GIL'inde kosardi"
    )
    assert re.search(r"^\s*SERVICE_ROLE:\s*worker\s*$", compose, re.M), (
        "backend-worker servisi yok — kalicilastirmayi kosacak surec kalmaz"
    )

    from app.core import service_role

    onceki = settings.service_role
    try:
        settings.service_role = "api"
        assert service_role.wants_background() is False
        assert service_role.serves_http() is True
    finally:
        settings.service_role = onceki


def test_yeni_ayarlar_COMPOSE_dan_geciyor():
    """config.py'de tanimli ama compose gecirmiyorsa container'a ULASMAZ."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    for env, varsayilan in (
        ("TELEMETRY_PERSIST_SOURCE", "auto"),
        ("TELEMETRY_PERSIST_CUTOVER_OVERLAP_SEC", "900"),
    ):
        m = re.search(rf"^\s*{env}:\s*\$\{{{env}:-([\w]+)\}}", compose, re.M)
        assert m, f"{env} compose'da yok"
        assert m.group(1) == varsayilan, f"{env} compose varsayilani {m.group(1)}"

    ornek = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "TELEMETRY_PERSIST_SOURCE" in ornek
    assert "TELEMETRY_PERSIST_CUTOVER_OVERLAP_SEC" in ornek


def test_update_sh_backend_ile_TAG_ENGINE_i_birlikte_gunceller():
    """`update.sh backend` tag-engine'i geride birakirsa arsiv SESSIZCE bozulur.

    Kalicilastirma artik tag-engine cikisindan besleniyor; eski tag-engine
    `dnp3_flags`i dusurur ve o an her nokta-seviyesi `invalid` TUM CIHAZI
    offline gosterir (harita kirmizi, "son veri" donar). Belirti hicbir hata
    uretmez ve biri `update.sh tag` kosana kadar KALICIDIR.
    """
    betik = (REPO / "update.sh").read_text(encoding="utf-8")
    blok = betik.split('e1_step "Servis \'$SVC\' guncelleniyor', 1)
    assert len(blok) == 2, "update.sh tek-servis blogu bulunamadi"
    tek_servis = blok[1]

    assert re.search(r'RECREATE\+=\("tag-engine"\)', tek_servis), (
        "backend-api guncellenirken tag-engine yeniden yaratilmiyor"
    )
    # Imaj HAZIRLIGI da kapsamali: yalnizca recreate etmek eski imaji tekrar
    # ayaga kaldirirdi.
    i_ekle = tek_servis.index('RECREATE+=("tag-engine")')
    i_hazirla = tek_servis.index("_e1_prepare_images")
    assert i_ekle < i_hazirla, (
        "tag-engine imaji indirilmeden recreate ediliyor — eski imaj geri gelir"
    )
    # backend-worker AYNI imaji kullanir; onun hazirliktan SONRA eklenmesi
    # dogrudur ve boyle kalmali (gereksiz ikinci pull olmasin).
    assert tek_servis.index('RECREATE+=("backend-worker")') > i_hazirla


def test_sistem_durumu_hangi_akistan_beslendigini_GOSTERIR():
    """Tuketici baska surece tasindi; hangi fazda oldugu ekranda gorunmeli."""
    from app.api.system_status import TelemetryPipelineReport

    assert "source" in TelemetryPipelineReport.model_fields
    tipler = (REPO / "apps" / "frontend-web" / "src" / "shared" / "types.ts").read_text(
        encoding="utf-8"
    )
    assert re.search(r"source\?:\s*\"raw\"\s*\|\s*\"normalized\"", tipler), (
        "frontend TelemetryPipelineStatus tipi backend semasiyla senkron degil"
    )


def test_tek_tuketici_ipligi(monkeypatch):
    """`start()` iki kez cagrilirsa ikinci iplik ACILMAZ (cift kayit kaynagi)."""
    monkeypatch.setattr(tc, "_consume_loop", lambda: threading.Event().wait(0.2))
    tc._thread = None
    try:
        tc.start()
        birinci = tc._thread
        tc.start()
        assert tc._thread is birinci
    finally:
        tc.stop()
        if tc._thread is not None:
            tc._thread.join(timeout=2)
        tc._thread = None
