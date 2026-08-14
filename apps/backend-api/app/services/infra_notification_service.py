"""Kritik ALTYAPI olaylarini operatore bildirir.

KAPATILAN ACIK
--------------
Sistem cesitli altyapi arizalarini `record_event(...)` ile `system_events`
tablosuna yaziyordu — ve orada BIRAKIYORDU. Bildirim zinciri yalnizca CIHAZ
ALARMLARI icin isliyordu:

    alarm -> RabbitMQ -> notification-worker -> kanallar

`record_event` hicbir gonderim tetiklemiyordu. Sonuc: saha IPC'sinde

    sistem ayakta  ama  telemetri kayboluyor

durumu, birinin arayuze girip Olaylar ekranina bakmasina bagliydi. Basinda
kimsenin olmadigi bir cihazda bu, en pahali ariza sinifinin (ayakta ama veri
yazmiyor) gunlerce fark edilmemesi demektir.

TASARIM: YENI BIR BILDIRIM SISTEMI DEGIL, YENI BIR TETIK
--------------------------------------------------------
Kanallarin kendisi (SMTP / SMS / Telegram / WhatsApp / uygulama ici zil)
AYNEN mevcut altyapidan kullanilir; ayarlar ayni `NotificationSettings`
satirindan okunur. Eklenen tek sey, `system_events` tablosunu okuyup ayni
kanallari besleyen bir TARAYICI'dir. Cihaz alarm akisina HIC dokunulmaz ve
altyapi olaylari icin ALARM SATIRI URETILMEZ — `Alarm.device_id` zorunludur
ve "disk doldu" hicbir cihaza ait degildir.

NEDEN HOT PATH'TE DEGIL
-----------------------
`record_event` disk_guard, telemetri tuketicisi ve yedek zamanlayicisi gibi
sicak yollardan cagriliyor. Oraya SMTP/HTTP koymak, yanit vermeyen bir
relay'in telemetri tuketicisini bloke etmesi demekti (bu depoda ayni hata
`fault_recompute_service`de bir kez yasandi ve satir ici gonderim bilerek
kapatildi). Bu yuzden gonderim AYRI bir surecte, ayri bir oturumla, olay
yazildiktan SONRA yapilir. `record_event` DEGISMEDI.

DORT KORUMA
-----------
1. ACIK LISTE  : her `record_event` bildirim uretmez. Yalnizca asagidaki
                 `ACIK_LISTE`de yer alan tipler. Seviye tek basina YETMEZ.
2. SOGUMA      : (olay tipi + kaynak) basina en fazla bir bildirim /
                 `cooldown`. 100 olay -> 1 bildirim; denetim kaydi yine 100.
3. OZYINELEME  : bildirim altyapisinin KENDI hata olaylari
                 (`notification_*`, `alarm_notification_*`,
                 `infra_notification_*`) acikca YASAKLI. Aksi halde
                 "bildirim gonderilemedi" olayi yeni bir bildirim denemesi
                 dogurur ve zincir kendini besler.
4. YALITIM     : her kanal hatasi tek tek yutulur; tarayici turu hicbir
                 kosulda cagirani (ve dolayisiyla arka plan surecini)
                 dusurmez.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import UserRole
from app.models.infra_notification import InfraNotificationState
from app.models.notification_settings import NotificationSettings
from app.models.system_event import SystemEvent
from app.models.user import User
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# OZYINELEME KALKANI
#
# Bu tipler HICBIR KOSULDA bildirim uretmez. Liste `ACIK_LISTE`den ONCE
# uygulanir, yani biri yanlislikla iki listeye birden yazilsa bile yasak
# kazanir (asagidaki `_ozyineleme_kalkani_tutarli` bunu ayrica dogrular).
#
# NEDEN: bildirim gonderimi basarisiz oldugunda sistem bunu bir olay olarak
# kaydeder (`notification_smtp_test_failed`, `alarm_notification_failed`...).
# Bu olaylar `severity="error"`dur. Seviyeye bakan bir tetik su zinciri
# kurardi:
#     gonderim basarisiz -> olay -> bildirim denemesi -> yine basarisiz -> ...
# Kalici bir SMTP arizasinda bu, kendini besleyen sonsuz bir dongudur.
# --------------------------------------------------------------------------
YASAKLI_ONEKLER: tuple[str, ...] = (
    "notification_",          # kanal test/gonderim hatalari
    "alarm_notification_",    # alarm bildirim zinciri
    "infra_notification_",    # BU modulun kendi hatalari
)


def ozyineleme_riski_var_mi(event_type: str) -> bool:
    """Bu olay tipi bildirim altyapisinin KENDISI hakkinda mi?"""
    return any(event_type.startswith(onek) for onek in YASAKLI_ONEKLER)


# --------------------------------------------------------------------------
# ACIK LISTE
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Kural:
    """Bir olay tipinin bildirim davranisi."""

    #: Bu seviyenin ALTINDAKI ayni tipteki olaylar bildirim uretmez.
    #: Ornegin `telemetry_backlog_high` toplu hatta `warning`, oncelikli
    #: hatta `critical` uretir; yalnizca ikincisi operatoru uyandirmali.
    asgari_seviye: str

    #: Sogumanin kaynak ayrimi. Olayin `metadata`sindan okunur; ilk dolu
    #: alan kullanilir. Bos tuple = ayrim yok (tek kova).
    kaynak_alanlari: tuple[str, ...] = ()

    #: Operatorun ne yapacagini soyleyen kisa yonlendirme.
    yonlendirme: str = ""

    #: Uygulama ici bildirimin tiklandiginda gidecegi ekran.
    link: str = "/system-status"


#: HANGI OLAYLAR OPERATORU RAHATSIZ ETMEYE DEGER
#
#: Olcut tek: "bu olay GERCEKTEN veri kaybi ya da kullanilabilirlik kaybi
#: mi, yoksa yalnizca bir uyari mi". Liste BILEREK KISA. Genisletmenin
#: bedeli asagida "RISK" basliginda yazili.
ACIK_LISTE: dict[str, Kural] = {
    # --- Telemetri: GERCEKLESMIS veri kaybi ------------------------------
    # Tampon tasti ve mesajlar tuketici GORMEDEN silindi. Geri getirilemez;
    # icinde bir ariza gecisi olabilir. Bu urunde en agir olay budur.
    "telemetry_tampon_tasmasi": Kural(
        asgari_seviye="critical",
        kaynak_alanlari=("sinif",),
        yonlendirme=(
            "Telemetri tamponu tasti ve olcumler islenmeden silindi. "
            "Sistem Durumu > Telemetri Boru Hatti'ndan tuketicinin geride "
            "kalma sebebini inceleyin."
        ),
    ),
    # Heniz kayip YOK ama tuketici gelis hizinin gerisinde; tasmaya
    # yaklasiliyor. Yalnizca ONCELIKLI hat (critical) uyandirir — toplu
    # hattin gecici birikimi beklenen bir durumdur.
    "telemetry_backlog_high": Kural(
        asgari_seviye="critical",
        kaynak_alanlari=("sinif",),
        yonlendirme=(
            "Telemetri tuketicisi gelis hizinin gerisinde. Mudahale "
            "edilmezse tampon tasar ve olcum kaybi baslar."
        ),
    ),
    # --- Disk: kullanilabilirlik kaybina en yakin esik --------------------
    # disk_guard'in son seviyesi. Bu noktada yeniden uretilebilir veriler
    # zaten siliniyor demektir.
    "disk_guard_emergency": Kural(
        asgari_seviye="critical",
        kaynak_alanlari=("path",),
        yonlendirme=(
            "Disk kritik seviyede. Sistem yeniden uretilebilir verileri "
            "silmeye basladi; yer acilmazsa yazma islemleri durur."
        ),
    ),
    # --- Yedekleme: veri dayanikliligi ------------------------------------
    # Yedek dizini doldu ve zamanli yedek ATLANDI.
    "backup_disk_critical": Kural(
        asgari_seviye="critical",
        yonlendirme=(
            "Yedek dizini doldugu icin zamanli yedek alinamadi. Yer "
            "acilmadan yedekleme devam etmeyecek."
        ),
        link="/admin/backups",
    ),
    # `error` seviyesinde ama ACIK OLARAK listede: yedek alinamiyorsa
    # sistem calisir gorunur ve ariza ancak yedege ihtiyac duyuldugu anda —
    # yani en gec anda — anlasilir.
    "backup_scheduled_failed": Kural(
        asgari_seviye="error",
        yonlendirme=(
            "Zamanli veritabani yedegi alinamadi. Bir sonraki denemeye "
            "kadar sistemde guncel yedek olmayabilir."
        ),
        link="/admin/backups",
    ),
}

#: Bildirim uretebilecek seviyeler. `warning` ve `info` HICBIR KOSULDA
#: uretmez — acik listede olsalar bile. Politika:
#:   critical -> bildirilir (acik listedeyse)
#:   error    -> yalnizca acik listede ACIKCA yer aliyorsa
#:   warning  -> hayir
#:   info     -> hayir
BILDIRILEBILIR_SEVIYELER = frozenset({"critical", "error"})

_SEVIYE_SIRASI = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def _seviye_yeterli(seviye: str, asgari: str) -> bool:
    return _SEVIYE_SIRASI.get(seviye, 0) >= _SEVIYE_SIRASI.get(asgari, 3)


def _ozyineleme_kalkani_tutarli() -> None:
    """Acik liste ile yasak liste CAKISAMAZ (import aninda dogrulanir)."""
    cakisan = [t for t in ACIK_LISTE if ozyineleme_riski_var_mi(t)]
    if cakisan:  # pragma: no cover - yapilandirma hatasi
        raise RuntimeError(
            f"Bildirim altyapisinin kendi olaylari acik listeye girmis: "
            f"{cakisan}. Bu ozyinelemeli bildirim dongusune yol acar."
        )


_ozyineleme_kalkani_tutarli()


# --------------------------------------------------------------------------
# Yapilandirma
# --------------------------------------------------------------------------


def _cooldown() -> timedelta:
    return timedelta(minutes=max(1, int(settings.infra_notify_cooldown_minutes)))


def _geriye_bakis() -> timedelta:
    """Taramanin kapsadigi pencere.

    Tarama araligindan BUYUK olmali, yoksa iki tur arasina dusen bir olay
    hic gorulmez. Soguma penceresinden kucuk tutmanin da anlami yok: ayni
    kova zaten bastirilacaktir.
    """
    return timedelta(minutes=max(1, int(settings.infra_notify_lookback_minutes)))


# --------------------------------------------------------------------------
# Olay -> bildirim
# --------------------------------------------------------------------------


def _metadata(olay: SystemEvent) -> dict[str, Any]:
    ham = getattr(olay, "metadata_json", None)
    if not ham:
        return {}
    try:
        veri = json.loads(ham)
    except (ValueError, TypeError):
        return {}
    return veri if isinstance(veri, dict) else {}


def _kaynak_anahtari(olay: SystemEvent, kural: Kural) -> str:
    """Sogumanin kova anahtari — ayni tipin bagimsiz ornekleri ayrilir.

    Ornegin oncelikli hattin tasmasi, toplu hattin sogumasi yuzunden
    bastirilmamali; ikisi ayri kovadir.
    """
    if not kural.kaynak_alanlari:
        return ""
    md = _metadata(olay)
    for alan in kural.kaynak_alanlari:
        deger = md.get(alan)
        if deger not in (None, ""):
            return str(deger)[:200]
    return ""


def bildirim_gerekir_mi(event_type: str, severity: str) -> bool:
    """Saf karar fonksiyonu — testlerin ve cagiranlarin ortak girisi."""
    if ozyineleme_riski_var_mi(event_type):
        return False
    if severity not in BILDIRILEBILIR_SEVIYELER:
        return False
    kural = ACIK_LISTE.get(event_type)
    if kural is None:
        return False
    return _seviye_yeterli(severity, kural.asgari_seviye)


def _hedef_kullanicilar(db: Session) -> list[User]:
    """Sahaya mudahale edebilecek roller.

    Operatore GONDERILMEZ: altyapi arizasinda yapabilecegi bir sey yok ve
    bu bildirimler onun icin gurultu olur. (`gateway_fleet_alarm` ile ayni
    tercih.)
    """
    roller = [UserRole.ENGINEER.value, UserRole.INSTALLER.value]
    return list(db.scalars(select(User).where(User.role.in_(roller))).all())


def _ayarlar(db: Session) -> NotificationSettings | None:
    return db.get(NotificationSettings, 1)


#: `Notification.title` kolonunun genisligi (models/notification.py String(200)).
#:
#: BU KIRPMA SART. `SystemEvent.message` String(500); ondan turetilen baslik
#: 200'u kolayca asar (ornek: pg_dump ikilisi bulunamadiginda uretilen 217
#: karakterlik mesaj -> 258 karakterlik baslik). Postgres uzunlugu ZORLAR ve
#: INSERT `StringDataRightTruncation` ile duser.
#:
#: SONUC NEDEN AGIR: hata commit aninda olusur, yani kanallara ZATEN
#: gonderilmistir; commit dustugu icin soguma damgasi (`last_notified_at`)
#: HIC YAZILMAZ ve bir sonraki turda ayni olay "bildirilmemis" gorunur.
#: 60 saniyede bir tekrarlanan e-posta/SMS/Telegram — yani bu modulun
#: onlemek icin var oldugu firtinanin ta kendisi, ve kendiliginden durmaz.
#:
#: SQLite uzunlugu ZORLAMADIGI icin testler bunu yakalamaz; bu yuzden
#: `_metin` uzunlugu dogrudan test ediliyor.
_BASLIK_MAX = 200


def _metin(olay: SystemEvent, kural: Kural, bastirilan: int) -> tuple[str, str]:
    """(baslik, govde) — tum kanallarda ayni icerik.

    Baslik kolon genisligine kirpilir; TAM mesaj govdede durdugu icin bilgi
    kaybi olmaz.
    """
    ham = f"Sistem uyarisi: {olay.message or olay.event_type}"
    baslik = ham if len(ham) <= _BASLIK_MAX else ham[: _BASLIK_MAX - 1] + "…"
    satirlar = [olay.message or olay.event_type]
    if kural.yonlendirme:
        satirlar += ["", kural.yonlendirme]
    if bastirilan > 0:
        # Operator "tek seferlik miydi, suruyor mu" ayrimini yapabilmeli.
        satirlar += [
            "",
            f"Not: Bu uyari son {settings.infra_notify_cooldown_minutes} dakikada "
            f"{bastirilan + 1} kez olustu; tekrarlari bastirildi.",
        ]
    satirlar += ["", f"Olay tipi: {olay.event_type}"]
    return baslik, "\n".join(satirlar)


def _kanallara_gonder(
    db: Session, olay: SystemEvent, kural: Kural, bastirilan: int
) -> int:
    """Mevcut kanallari kullanarak gonderir. Kanal hatalari YUTULUR.

    Donus: basarili gonderim sayisi (uygulama ici bildirimler dahil).
    Tek bir kanalin arizasi digerlerini engellemez — bu, "bildirim sistemi
    cokerse ana sistem etkilenmez" sartinin kanal seviyesindeki karsiligi.

    KULLANICI BAZLI KANAL TERCIHI (`UserNotificationPreference`) BILEREK
    OKUNMUYOR — ve bu bir gozden kacirma DEGIL, acik bir karardir:

      * O tercih CIHAZ ALARMLARI icin tasarlandi ("her ariza icin SMS
        istemiyorum"). Altyapi uyarilari gunde birkac taneden fazla
        olmamali; olduysa zaten mudahale gerektiren bir durum vardir.
      * "Diskin dolmasi" ya da "telemetri kayboluyor" uyarisini kacirmanin
        bedeli, gereksiz bir SMS'in bedelinden cok daha buyuk.
      * Yine de bu bir URUN karari: musteri altyapi uyarilarinin da kisisel
        tercihe uymasini isterse, `dispatch_alarm_notifications` icindeki
        `_get_pref` deseni buraya birebir tasinabilir.

    Sistem capindaki kanal anahtarlarina (`NotificationSettings`) HER ZAMAN
    uyulur: SMS kapaliysa SMS gitmez.
    """
    from app.services import whatsapp_web_client_service
    from app.services.notification_test_service import (
        send_sms_test,
        send_smtp_test,
        send_telegram_test,
    )

    baslik, govde = _metin(olay, kural, bastirilan)
    ayar = _ayarlar(db)
    hedefler = _hedef_kullanicilar(db)
    gonderilen = 0

    # 1) Uygulama ici zil — kanal yapilandirmasi olmasa BILE calisir.
    #    Hicbir kanal acik degilse uyarinin tek gorunur oldugu yer burasi.
    if hedefler:
        for kullanici in hedefler:
            try:
                create_notification(
                    db,
                    recipient_username=kullanici.username,
                    title=baslik,
                    body=govde,
                    category="system",
                    severity=olay.severity,
                    link=kural.link,
                    metadata={
                        "event_type": olay.event_type,
                        "system_event_id": olay.id,
                        "suppressed_count": bastirilan,
                    },
                )
                gonderilen += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "infra_notify_web_failed user=%s type=%s",
                    kullanici.username, olay.event_type,
                )
    else:
        # Hedef rolde kullanici yoksa uyari sessizce kaybolmasin.
        logger.warning(
            "infra_notify_no_recipient type=%s — engineer/installer yok, "
            "yayina dusuluyor", olay.event_type,
        )
        try:
            create_notification(
                db,
                recipient_username=None,
                title=baslik,
                body=govde,
                category="system",
                severity=olay.severity,
                link=kural.link,
                metadata={"event_type": olay.event_type, "system_event_id": olay.id},
            )
            gonderilen += 1
        except Exception:  # noqa: BLE001
            logger.exception("infra_notify_broadcast_failed type=%s", olay.event_type)

    if ayar is None:
        return gonderilen

    # 2) E-posta
    if getattr(ayar, "smtp_enabled", False):
        for kullanici in hedefler:
            if not kullanici.email:
                continue
            try:
                send_smtp_test(
                    ayar, recipient_email=kullanici.email,
                    subject=baslik, message=govde,
                )
                gonderilen += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "infra_notify_email_failed user=%s type=%s error=%s",
                    kullanici.username, olay.event_type, exc,
                )

    # 3) SMS
    if getattr(ayar, "sms_enabled", False):
        for kullanici in hedefler:
            if not kullanici.phone_number:
                continue
            try:
                send_sms_test(
                    ayar, recipient_phone=kullanici.phone_number, message=govde,
                )
                gonderilen += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "infra_notify_sms_failed user=%s type=%s error=%s",
                    kullanici.username, olay.event_type, exc,
                )

    # 4) Telegram — kullanici bazli degil, yayin (alarm akisiyla ayni desen).
    if getattr(ayar, "telegram_enabled", False):
        ham = getattr(ayar, "telegram_chat_ids", "") or ""
        for chat_id in [c.strip() for c in ham.split(",") if c.strip()]:
            try:
                send_telegram_test(
                    ayar, chat_id=chat_id, message=f"<b>{baslik}</b>\n\n{govde}",
                )
                gonderilen += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "infra_notify_telegram_failed chat=%s type=%s error=%s",
                    chat_id, olay.event_type, exc,
                )

    # 5) WhatsApp — yalnizca GRUP modunda. Kisisel mod bilerek atlanir:
    #    altyapi uyarisi bir ekip bilgisidir, kisiye ozel degil.
    if getattr(ayar, "whatsapp_web_enabled", False) and getattr(
        ayar, "whatsapp_web_group_mode", False
    ):
        ham = getattr(ayar, "whatsapp_web_group_jids", "") or ""
        for jid in [j.strip() for j in ham.split(",") if j.strip()]:
            try:
                whatsapp_web_client_service.send_test_message(
                    jid, f"{baslik}\n\n{govde}"
                )
                gonderilen += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "infra_notify_whatsapp_failed jid=%s type=%s error=%s",
                    jid, olay.event_type, exc,
                )

    return gonderilen


# --------------------------------------------------------------------------
# Tarama
# --------------------------------------------------------------------------


def dispatch_pending_infra_notifications(
    db: Session, *, simdi: datetime | None = None
) -> int:
    """Bekleyen kritik altyapi olaylarini bildirir; gonderilen KOVA sayisini doner.

    "Kova" = (olay tipi + kaynak). Bir kovadaki 100 olay TEK bildirim uretir;
    `system_events`teki 100 satirin hicbirine dokunulmaz.

    Cagiran commit eder. Istisna FIRLATMAZ — tarayicinin turu her kosulda
    temiz bitmeli.
    """
    simdi = simdi or datetime.now(timezone.utc)
    esik = simdi - _geriye_bakis()

    olaylar = list(
        db.scalars(
            select(SystemEvent)
            .where(
                SystemEvent.created_at >= esik,
                SystemEvent.event_type.in_(list(ACIK_LISTE.keys())),
                SystemEvent.severity.in_(list(BILDIRILEBILIR_SEVIYELER)),
            )
            .order_by(SystemEvent.created_at.asc(), SystemEvent.id.asc())
        ).all()
    )
    if not olaylar:
        return 0

    # Kovalara ayir: her kovanin EN YENI olayi temsilci, geri kalani sayilir.
    kovalar: dict[tuple[str, str], list[SystemEvent]] = {}
    for olay in olaylar:
        if not bildirim_gerekir_mi(olay.event_type, olay.severity):
            continue
        kural = ACIK_LISTE[olay.event_type]
        anahtar = (olay.event_type, _kaynak_anahtari(olay, kural))
        kovalar.setdefault(anahtar, []).append(olay)

    if not kovalar:
        return 0

    cooldown = _cooldown()
    gonderilen_kova = 0

    for (event_type, kaynak), grup in kovalar.items():
        kural = ACIK_LISTE[event_type]
        durum = db.scalar(
            select(InfraNotificationState).where(
                InfraNotificationState.event_type == event_type,
                InfraNotificationState.resource_key == kaynak,
            )
        )

        # FILIGRAN (watermark): bu kovada DAHA ONCE HESABA KATILMIS en yuksek
        # olay kimligi. Yalnizca ondan YENI olaylar dikkate alinir.
        #
        # NEDEN SART: geriye bakis penceresi (60 dk) tarama araligindan (60 sn)
        # cok daha genis. Filigran olmasaydi ayni 100 olay HER TURDA yeniden
        # sayilir ve `suppressed_count` 15 dakikalik soguma boyunca ~1500'e
        # cikardi — operatore gosterilen "bu uyari N kez olustu" sayisi
        # tamamen yanlis olurdu. Ayrica soguma dolduktan sonra HIC YENI OLAY
        # OLMASA BILE eski bir olay yeniden bildirim uretirdi.
        filigran = durum.last_event_id if durum is not None else None
        yeni = [o for o in grup if filigran is None or o.id > filigran]
        if not yeni:
            # Bu kovada hesaba katilmamis olay yok: ne gonderilecek bir sey
            # var ne de sayilacak.
            continue

        if durum is not None:
            son = durum.last_notified_at
            if son.tzinfo is None:
                son = son.replace(tzinfo=timezone.utc)
            if simdi - son < cooldown:
                # SOGUMADA: gonderme, ama denetim icin say ve filigrani
                # ilerlet. `system_events` kayitlarina DOKUNULMAZ —
                # bastirilan yalnizca GONDERIM'dir.
                durum.suppressed_count += len(yeni)
                durum.last_event_id = yeni[-1].id
                continue

        temsilci = yeni[-1]
        bastirilan = (durum.suppressed_count if durum is not None else 0) + (
            len(yeni) - 1
        )
        try:
            # KOVA BASINA SAVEPOINT — TEK ZEHIRLI KOVA TURU DUSURMESIN.
            #
            # Gonderim ve soguma damgasi AYNI savepoint icinde: biri
            # yazilamazsa digeri de yazilmaz, yani "gonderildi ama
            # damgalanmadi" ara durumu olusamaz. O ara durum tam olarak
            # bildirim firtinasini uretirdi (damga yok -> bir sonraki turda
            # yeniden gonder).
            #
            # Savepoint olmadan bir kovadaki DB hatasi TUM transaction'i
            # abort ederdi ve ayni taramadaki disk/telemetri kovalarinin
            # damgalari da yazilamazdi — hepsi her turda yeniden gonderilir.
            #
            # `flush()` ACIK: hatanin cagirandaki `commit()`te degil BURADA
            # yuzeye cikmasini istiyoruz ki yalnizca bu kova geri sarilsin.
            with db.begin_nested():
                _kanallara_gonder(db, temsilci, kural, bastirilan)
                if durum is None:
                    durum = InfraNotificationState(
                        event_type=event_type,
                        resource_key=kaynak,
                        last_notified_at=simdi,
                        last_event_id=temsilci.id,
                        suppressed_count=0,
                    )
                    db.add(durum)
                else:
                    durum.last_notified_at = simdi
                    durum.last_event_id = temsilci.id
                    durum.suppressed_count = 0
                db.flush()
        except Exception:  # noqa: BLE001
            # Kanal hatalari zaten iceride yutuluyor; buraya dusen sey bir DB
            # sorunudur. Tur DUSMEZ: kalan kovalar islenmeye devam eder.
            logger.exception(
                "infra_notify_dispatch_failed type=%s kaynak=%s", event_type, kaynak
            )
            continue

        gonderilen_kova += 1
        logger.info(
            "infra_notify_sent type=%s kaynak=%s bastirilan=%d",
            event_type, kaynak or "-", bastirilan,
        )

    return gonderilen_kova
