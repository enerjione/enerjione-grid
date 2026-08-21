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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.services import command_identity, device_kit_service
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

#: Bir dis protokolun (IEC 104) tetikleyebilecegi komutlar.
#: Genisletmek bilincli bir urun karari olmali — bkz. modul docstring'i.
PROTOCOL_ALLOWED_SLUGS = frozenset({"reset_all_fcis"})

# ---------------------------------------------------------------------------
# F6-B: KOMUT NIYETI SINIRI
#
# Bu sinir gateway'in FIZIKSEL dogrulayicisinin (F6-G, v1.11.1) kopyasi
# DEGILDIR ve olmamalidir. Gateway "cihaz bunu kabul eder mi" sorusunu
# cevaplar; backend "biz bunu ISTEDIK mi" sorusunu. Backend sinirlari
# gateway'inkinden DAR olabilir ve burada oyledir — asagidaki degerler
# uydurulmadi, `schemas/device.DeviceCommandRequest` icinde ZATEN yururlukte
# olan uretim sinirlaridir; tek fark artik TUM cagiranlar icin gecerli
# olmalari.
#
# NEDEN SERVISTE (router'da degil): REST semasi yalnizca arayuz kapisini
# korur. `queue_command` bugun uc yerden cagriliyor (arayuz, config uygulama,
# IEC 104) ve hicbir tip/aralik kontrolu YOKTU; sema disindan gelen bir
# cagiran istedigi degeri yazabilirdi. Sinir, satirin yazildigi yere en
# yakin ortak noktada durmali.
# ---------------------------------------------------------------------------

#: Kabul edilen operasyon turleri. Horstmann SN2 Device Profile PULSE
#: DESTEKLEMEZ; uretimde tek deger `latch_on`.
#:
#: ALIAS/NORMALIZASYON YOK: "LATCH_ON", "latch-on", "Latch_On" gibi
#: varyantlar SESSIZCE duzeltilmez. Cagiranin yazim hatasini "tahmin edip"
#: fiziksel komut uretmek, tam da kapatmaya calistigimiz sinifta bir hata.
OP_TYPE_LATCH_ON = "latch_on"
ALLOWED_OP_TYPES = frozenset({OP_TYPE_LATCH_ON})

#: CROB tekrar sayisi. Ust sinir gateway'in fiziksel tavani (uint8, 255)
#: DEGIL, backend'in mevcut uretim sozlesmesidir.
COUNT_MIN = 1
COUNT_MAX = 10

#: LATCH islemlerinde zamanlama alanlari ANLAMSIZDIR ve uretimde daima 0'dir.
#: Ust sinir yine mevcut sema sozlesmesinden gelir (gateway uint32 kabul eder).
TIME_MIN = 0
TIME_MAX = 60_000


def _tam_sayi(ad: str, deger: object, alt: int, ust: int) -> int:
    """Kesin tamsayi + aralik kontrolu. COERCION YOK.

    `bool` ACIKCA reddedilir. Python'da `True == 1` ve `isinstance(True, int)`
    dogrudur; kontrol `isinstance` ile yazilsaydi `count=True` sessizce
    `count=1` olur, yani cagiranin GONDERMEDIGI bir niyet uretilirdi. Ayni
    sebeple `"1"` (metin) ve `1.0` (float) de kabul edilmez: bunlar cagiranin
    tip hatasidir ve dogru tepki duzeltmek degil REDDETMEKTIR.
    """
    if isinstance(deger, bool) or type(deger) is not int:
        raise CommandRejected(
            "invalid_parameter_type",
            f"{ad} tam sayi olmali (bool/metin/ondalik kabul edilmez); "
            f"gelen: {type(deger).__name__}",
        )
    if not (alt <= deger <= ust):
        raise CommandRejected(
            "parameter_out_of_range",
            f"{ad} {alt}..{ust} araliginda olmali; gelen: {deger}",
        )
    return deger


def validate_command_intent(
    *,
    slug: str,
    op_type: object,
    count: object,
    on_time_ms: object,
    off_time_ms: object,
    model: str | None = None,
) -> None:
    """Komut NIYETINI satir yazilmadan ONCE dogrular.

    Basarisizlikta `CommandRejected` firlar; cagiran satiri HIC olusturmaz,
    dolayisiyla gecersiz niyet `/pending`e de ulasamaz.

    `slug`/`model` bugun karar degistirmiyor ama imzada duruyor: komut basina
    kural gerektiginde (or. yalnizca belirli bir komutta tekrar sayisi) sinir
    yine TEK yerde kalsin, cagiranlara dagilmasin.
    """
    if not isinstance(op_type, str) or op_type not in ALLOWED_OP_TYPES:
        raise CommandRejected(
            "invalid_op_type",
            f"Desteklenmeyen operasyon turu: {op_type!r} "
            f"(kabul edilen: {', '.join(sorted(ALLOWED_OP_TYPES))})",
        )

    sayi = _tam_sayi("count", count, COUNT_MIN, COUNT_MAX)
    acik = _tam_sayi("on_time_ms", on_time_ms, TIME_MIN, TIME_MAX)
    kapali = _tam_sayi("off_time_ms", off_time_ms, TIME_MIN, TIME_MAX)

    # DESTEKLENMEYEN KOMBINASYON: LATCH'te zamanlama anlamsizdir.
    #
    # Cihaz bu alanlari zaten yok sayar; sifirdan farkli bir deger kabul
    # etmek, operatore uygulanmayacak bir sure verdigimizi dusundururdu.
    # Uretimde her yol bugun 0 gonderiyor, yani bu kural mevcut davranisi
    # DEGISTIRMEZ.
    if op_type.startswith("latch") and (acik != 0 or kapali != 0):
        raise CommandRejected(
            "unsupported_parameter_combination",
            f"{op_type} islemi zamanlama kullanmaz; on_time_ms/off_time_ms 0 olmali "
            f"(gelen: {acik}/{kapali})",
        )

    del sayi  # aralik kontrolu icin cozuldu; deger cagiranda kalir



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

    # NIYET SINIRI — satir yazilmadan ONCE. Gecersizse `CommandRejected`
    # firlar, `DeviceCommand` HIC olusmaz ve `/pending`e hicbir sey ulasmaz.
    validate_command_intent(
        slug=slug,
        op_type=OP_TYPE_LATCH_ON,
        count=count,
        on_time_ms=on_time_ms,
        off_time_ms=off_time_ms,
        model=hedef.model,
    )

    cmd = DeviceCommand(
        # KIMLIK: model varsayilani uretir (`command_identity.yeni_kimlik`).
        # Sequence KULLANILMAZ — degeri veritabaninin ICINDE yasiyordu ve DB
        # daha eski bir ana alindiginda DAGITILMIS kimlikler yeniden
        # uretiliyordu. Gateway defteri baska bir makinede durur ve geri
        # gitmez; sahada tam olarak bu yasandi (GW-002, id 39-42).
        gateway_code=gateway.code,
        device_code=hedef.code,
        command=slug,
        dnp3_index=index,
        # Horstmann SN2 Device Profile PULSE DESTEKLEMEZ (yalniz LATCH_ON/OFF).
        # BACKEND SABITI: cagiran taraf op_type SECEMEZ; API semasinda da
        # boyle bir alan yok. Dogrulayici yine de kontrol ediyor ki sabit
        # ileride degistirilirse sessizce sozlesme disina cikilmasin.
        op_type=OP_TYPE_LATCH_ON,
        count=count,
        on_time_ms=on_time_ms,
        off_time_ms=off_time_ms,
        status="pending",
        actor_username=actor,
    )
    db.add(cmd)
    # CAKISMA SANSA BIRAKILMAZ. Ayni milisaniyede 1000 yuva var ve komut
    # hizi saniyede tek haneli, ama birincil anahtar son sozu soylemeli:
    # cakisirsa TAZE bir kimlikle bir kez daha denenir. Ikinci kez cakismak
    # icin iki surecin ayni milisaniyede ayni yuvayi UST USTE IKI KEZ
    # secmesi gerekir.
    try:
        with db.begin_nested():
            db.flush()  # id zaten verildi; burada satir yazilir
    except IntegrityError:
        cmd.id = command_identity.yeni_kimlik()
        with db.begin_nested():
            db.flush()

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
