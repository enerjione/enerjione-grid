"""Komut teslim kirasi ve dayanikli kabul (F3C).

KAPATILAN ARIZA
---------------
Backend `/pending` yanitini uretirken komutu `sent` isaretliyordu. Iki pencere
acikti:

  A) backend `sent` COMMIT etti, HTTP yaniti gateway'e ULASMADI (ag/timeout)
  B) gateway yaniti bellege aldi, dayanikli deftere yazmadan oldu

Her ikisinde de backend `sent`, gateway defteri bos, cihaz komutu hic almadi.
Komut sonsuza kadar `sent` kaliyor; operator "gonderildi" goruyor ama kesici
surulmemis. Kayip SESSIZ.

Artik komut once KIRALANIR (`status` `pending` KALIR) ve gateway defterine
yazdigini ACK ile bildirince `sent` olur.

UC AYRI YENIDEN DENEME - KARISTIRMA
-----------------------------------
  Backend -> Gateway TESLIM     : yeniden denenebilir  (bu modul)
  Gateway -> Cihaz   CALISTIRMA : ASLA otomatik yeniden denenmez
  Gateway -> Backend SONUC      : yeniden denenebilir  (command-results)

Teslimi yeniden denemek GUVENLIDIR cunku gateway defteri (`command_id`
PRIMARY KEY + INSERT OR IGNORE) ayni komutun ikinci kez CROB uretmesini
imkansiz kilar. Yeniden sunulan komut gateway'de yalnizca ACK/sonuc kurtarmasi
tetikler. Bu modul o garantiye DAYANIR; onu uretmez.

Tel sozlesmesi: docs/f3c-command-delivery-protocol.md
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.device_command import DeviceCommand

logger = logging.getLogger(__name__)

#: Gateway'in her `/pending` isteginde gonderdigi teslim protokolu basligi.
DELIVERY_HEADER = "X-E1-Delivery"

#: Gateway'in bildirdigi yetenek adi.
CAPABILITY_ACK_V1 = "command_delivery_ack_v1"

#: `X-E1-Gateway-Health` ile ayni tavan. Gercek govde ~60 bayt; bu sinir hem
#: kotu niyetli bir gateway'in backend'i mesgul etmesini onler hem de nginx
#: baslik tavanina yaklasip istegin TAMAMINI (yani komutlari da) reddettirecek
#: bir buyumeyi engeller.
_MAX_HEADER_BYTES = 2048

#: Jeton uzunlugu. `token_urlsafe(32)` ~43 karakter uretir; kolon 64.
_TOKEN_BYTES = 32

#: Tek istekte kabul edilecek azami ACK sayisi.
MAX_ACK_BATCH = 500

#: Eski protokol uyarisinin gateway basina hiz siniri (saniye). Komut-poll
#: 1 Hz; her poll'de uyari basmak gunde gateway basina 86.400 satir demekti.
_LEGACY_WARN_INTERVAL_SEC = 900.0

#: gateway_code -> son uyari ani (monotonic).
_legacy_last_warn: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Terminal sonuc kodlari
# ---------------------------------------------------------------------------
#
# YENI BIR `status` DEGERI URETILMEZ. Durum sozlesmesi
# (pending/sent/ok/failed/cancelled) korunuyor; ayrim `result_status` ile
# yapiliyor. Yeni bir lifecycle durumu eklemek arayuzu, IEC 104 esleme
# tablosunu (TERMINAL_OK/TERMINAL_FAIL) ve `command-results` idempotency
# kontrollerini birden etkilerdi.

#: TTL doldu, komut gateway'e HIC sunulmadi. Fiziksel komut KESINLIKLE
#: uygulanmadi.
RESULT_EXPIRED = "expired"

#: Gateway kabul etti (ya da etmis OLABILIR) ama cihaz sonucu alinamadi.
#: Fiziksel komut uygulanmis olabilir. Gec gelen gercek sonuc bu durumu
#: duzeltebilir (bkz. reconciliation).
RESULT_UNKNOWN = "result_unknown"

#: Gateway defteri sifirlandi; tekrar-onleme kaniti yok. Otomatik teslim
#: durduruldu, operator incelemesi gerekiyor.
RESULT_DELIVERY_STATE_LOST = "delivery_state_lost"

#: Kira defalarca denendi, gateway hicbir zaman kabul ettigini bildirmedi.
RESULT_DELIVERY_FAILED = "delivery_failed"


@dataclass(frozen=True)
class DeliveryCapability:
    """Gateway'in `X-E1-Delivery` basligindan cozulen teslim yetenegi."""

    version: int
    epoch: str

    @property
    def ack_v1(self) -> bool:
        return self.version >= 1 and bool(self.epoch)


def parse_delivery_header(raw: str | None) -> DeliveryCapability | None:
    """Teslim basligini cozer. BASARISIZLIK HER ZAMAN None — istisna ATMAZ.

    Cagiran taraf `/pending` ucudur ve orada bir istisna SCADA komut kanalini
    dusururdu. `X-E1-Gateway-Health` ile ayni savunmaci sozlesme.

    None donmesi "gateway eski" anlamina gelir ve fail-closed yola girer; yani
    bozuk bir baslik sessizce ESKI protokole dusurmez, gorunur bir sonuc verir.
    """
    if not raw:
        return None
    try:
        if len(raw) > _MAX_HEADER_BYTES:
            logger.warning(
                "gateway_delivery_header_too_large bytes=%d limit=%d — yok sayildi",
                len(raw),
                _MAX_HEADER_BYTES,
            )
            return None
        pad = "=" * (-len(raw) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(raw + pad).decode("utf-8"))
        if not isinstance(parsed, dict):
            return None
        surum = parsed.get("v")
        epoch = parsed.get("epoch")
        if not isinstance(surum, int) or isinstance(surum, bool):
            return None
        if not isinstance(epoch, str) or not epoch.strip():
            return None
        # Epoch bir DB kolonuna (String(64)) yazilacak; gateway'in gonderdigi
        # uzunluga guvenmiyoruz.
        return DeliveryCapability(version=surum, epoch=epoch.strip()[:64])
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        logger.debug("gateway_delivery_header_unparsable error=%s", exc)
        return None
    except Exception:  # noqa: BLE001
        logger.debug("gateway_delivery_header_failed", exc_info=True)
        return None


def utc(deger: datetime | None) -> datetime | None:
    """Naive damgayi UTC-aware yap.

    SOZLESMEYI SURUCUYE BIRAKMIYORUZ: kolon `DateTime(timezone=True)` olsa da
    bazi surucler (or. SQLite) tzinfo'yu KAYBEDEREK dondurur.
    """
    if deger is None:
        return None
    return deger if deger.tzinfo is not None else deger.replace(tzinfo=timezone.utc)


def son_kullanma(cmd: DeviceCommand, ttl_sec: int) -> datetime | None:
    """Komutun MUTLAK son kullanma ani: `created_at + COMMAND_MAX_AGE_SEC`.

    Kaynak dogruluk `created_at`tir; DB'ye `expires_at` kolonu EKLENMEDI.
    Turetilmis deger gateway'e `delivery_not_after` olarak gonderilir ve orada
    da uygulanir (savunma derinligi): backend kirayi yenilerken bir hata yapsa
    bile gateway suresi gecmis bir komutu FIZIKSEL olarak calistirmaz.
    """
    olusturma = utc(cmd.created_at)
    if olusturma is None:
        return None
    return olusturma + timedelta(seconds=int(ttl_sec))


def _sonlandir(
    cmd: DeviceCommand,
    *,
    now: datetime,
    result_status: str,
    hata: str,
) -> None:
    """Komutu terminal duruma alir. `sent_at` DOKUNULMAZ.

    `sent_at`i temizlemek ya da uydurmak denetim izini bozardi: gateway'in
    kabul ettigi bir komutta damga KALMALI, hic teslim edilmemis komutta ise
    zaten None'dir.
    """
    cmd.status = "failed"
    cmd.result_status = result_status
    cmd.result_error = hata[:500]
    cmd.completed_at = now


def legacy_uyarisi_gerekli(gateway_code: str) -> bool:
    """Eski protokol uyarisi icin hiz siniri. True = simdi logla.

    Komut-poll 1 Hz; her poll'de uyari basmak gunde gateway basina 86.400
    satir demekti ve gercek uyarilari gorunmez kilardi.
    """
    simdi = time.monotonic()
    son = _legacy_last_warn.get(gateway_code)
    if son is not None and (simdi - son) < _LEGACY_WARN_INTERVAL_SEC:
        return False
    _legacy_last_warn[gateway_code] = simdi
    return True


@dataclass
class TeslimKarari:
    """Bir poll'un teslim sonucu."""

    #: Gateway'e sunulacak komutlar (kiralanmis).
    teslim: list[DeviceCommand]
    #: Terminal duruma alinan komutlar — (komut, result_status) ciftleri.
    sonlandirilan: list[tuple[DeviceCommand, str]]
    #: Aktif kirasi oldugu icin bu turda atlanan komut sayisi.
    kirali: int


def kirala(
    db: Session,
    *,
    gateway_code: str,
    yetenek: DeliveryCapability,
    now: datetime,
) -> TeslimKarari:
    """Bekleyen komutlari degerlendirir ve teslim edilecekleri KIRALAR.

    `status` `pending` KALIR ve `sent_at` YAZILMAZ — teslim ancak gateway ACK
    gonderince tamamlanir.

    Cagiran taraf satirlari `with_for_update()` ile kilitlenmis olarak
    vermelidir; es zamanli iki poll ayni komutu iki kez kiralayamaz.
    """
    ttl_sec = int(settings.command_max_age_sec)
    kira_sn = int(settings.command_delivery_lease_sec)
    azami_deneme = int(settings.command_delivery_max_attempts)

    pending_cmds = list(
        db.scalars(
            select(DeviceCommand)
            .where(
                DeviceCommand.gateway_code == gateway_code,
                DeviceCommand.status == "pending",
            )
            .order_by(DeviceCommand.id.asc())
            .with_for_update()
        ).all()
    )

    karar = TeslimKarari(teslim=[], sonlandirilan=[], kirali=0)

    for cmd in pending_cmds:
        bitis = son_kullanma(cmd, ttl_sec)

        # --- 1) MUTLAK TTL — kiralanmis olsun olmasin HER ZAMAN -----------
        #
        # "TTL yalnizca hic kiralanmamis komutta uygulanir" kurali TEHLIKELI
        # olurdu:
        #   10:00 komut olusturuldu, kiralandi, yanit gateway'e ulasmadi
        #   14:00 gateway geri geldi, kira suresi coktan doldu
        #   -> 4 saatlik komut fiziksel olarak uygulanirdi.
        # Son kullanma her zaman `created_at`ten turetilir ve kiralama onu
        # OTELEMEZ.
        #
        # `created_at` yoksa (teorik) komut BAYAT SAYILMAZ: yasini
        # bilemedigimiz bir kesici komutunu sessizce dusurmek, operatorun
        # verdigi komutu kaybetmek olurdu.
        if bitis is not None and now > bitis:
            # AYRIM ONEMLI: hic kiralanmamis komut KESINLIKLE cihaza gitmedi
            # (`expired`). Kiralanmis komut ise gateway tarafindan kabul
            # edilmis OLABILIR — ACK yolda kaybolmus olabilir — bu yuzden
            # belirsizligi durust temsil eden `result_unknown` kullanilir ve
            # gec gelen gercek sonucun onu duzeltmesine izin verilir.
            if int(cmd.delivery_attempt or 0) > 0:
                _sonlandir(
                    cmd,
                    now=now,
                    result_status=RESULT_UNKNOWN,
                    hata=(
                        "Komut gateway'e sunuldu ancak kabul edildigi dogrulanamadan "
                        "azami yasa ulasti. Fiziksel komut uygulanmis olabilir."
                    ),
                )
                karar.sonlandirilan.append((cmd, RESULT_UNKNOWN))
                logger.warning(
                    "event=command_expired_after_lease gateway_code=%s command_id=%s "
                    "device_code=%s command=%s attempt=%s ttl_sec=%s",
                    gateway_code, cmd.id, cmd.device_code, cmd.command,
                    cmd.delivery_attempt, ttl_sec,
                )
            else:
                _sonlandir(
                    cmd,
                    now=now,
                    result_status=RESULT_EXPIRED,
                    hata="Komut gateway'e teslim edilmeden once zaman asimina ugradi",
                )
                cmd.sent_at = None  # gateway'e HIC gonderilmedi
                karar.sonlandirilan.append((cmd, RESULT_EXPIRED))
                logger.warning(
                    "event=command_expired_backend gateway_code=%s command_id=%s "
                    "device_code=%s command=%s dnp3_index=%s ttl_sec=%s",
                    gateway_code, cmd.id, cmd.device_code, cmd.command,
                    cmd.dnp3_index, ttl_sec,
                )
            continue

        # --- 2) Aktif kira: komut zaten baska bir poll'a sunuldu ----------
        kira_bitis = utc(cmd.delivery_lease_until)
        if kira_bitis is not None and kira_bitis > now:
            karar.kirali += 1
            continue

        # --- 3) DEFTER KIMLIGI SUREKLILIGI (fail-closed) ------------------
        #
        # Gateway defteri sifirlandiysa tekrar-onleme kaniti kayboldu: bu
        # komutu yeniden sunmak, gateway'in onu YENI sanip CROB'u TEKRARLAMASI
        # demek olabilir. Otomatik teslim DURDURULUR.
        eski_epoch = (cmd.delivery_gateway_epoch or "").strip()
        if eski_epoch and eski_epoch != yetenek.epoch:
            _sonlandir(
                cmd,
                now=now,
                result_status=RESULT_DELIVERY_STATE_LOST,
                hata=(
                    "Gateway komut defteri sifirlandi; komutun daha once uygulanip "
                    "uygulanmadigi belirlenemiyor. Otomatik teslim durduruldu — "
                    "durumu sahadan dogrulayin."
                ),
            )
            karar.sonlandirilan.append((cmd, RESULT_DELIVERY_STATE_LOST))
            logger.error(
                "event=command_delivery_state_lost gateway_code=%s command_id=%s "
                "device_code=%s command=%s attempt=%s — gateway defteri sifirlandi, "
                "TEKRAR-ONLEME GARANTISI YOK; komut operator incelemesine birakildi",
                gateway_code, cmd.id, cmd.device_code, cmd.command, cmd.delivery_attempt,
            )
            continue

        # --- 4) Deneme siniri --------------------------------------------
        if int(cmd.delivery_attempt or 0) >= azami_deneme:
            _sonlandir(
                cmd,
                now=now,
                result_status=RESULT_DELIVERY_FAILED,
                hata=(
                    f"Komut {cmd.delivery_attempt} kez gateway'e sunuldu ancak gateway "
                    "kabul ettigini hicbir zaman bildirmedi."
                ),
            )
            karar.sonlandirilan.append((cmd, RESULT_DELIVERY_FAILED))
            logger.error(
                "event=command_delivery_exhausted gateway_code=%s command_id=%s "
                "device_code=%s command=%s attempt=%s",
                gateway_code, cmd.id, cmd.device_code, cmd.command, cmd.delivery_attempt,
            )
            continue

        # --- 5) KIRALA ----------------------------------------------------
        #
        # JETON DONDURULMEZ. Ilk kiralamada uretilir ve ACK gelene kadar ayni
        # kalir; yalnizca kira suresi ve deneme sayaci ilerler. Sabit teslim
        # kimligi, gateway defterindeki jetonu her zaman gecerli tutar ve
        # mukerrer ACK'i sadelestirir.
        if not cmd.delivery_token:
            cmd.delivery_token = secrets.token_urlsafe(_TOKEN_BYTES)
            cmd.delivery_gateway_epoch = yetenek.epoch
        cmd.delivery_lease_until = now + timedelta(seconds=kira_sn)
        cmd.delivery_attempt = int(cmd.delivery_attempt or 0) + 1
        karar.teslim.append(cmd)

    return karar


def bayatlari_sonlandir(
    db: Session,
    *,
    gateway_code: str,
    now: datetime,
) -> list[tuple[DeviceCommand, str]]:
    """Bekleyen komutlarda YALNIZCA mutlak TTL'yi uygular; teslim ETMEZ.

    NEDEN AYRI BIR FONKSIYON
    ------------------------
    Eski protokol gateway'ine fail-closed davranildiginda komut teslim
    edilmiyor — ama TTL de uygulanmiyordu ve komut SONSUZA KADAR `pending`
    kaliyordu. Ne `kirala` (yalnizca yetenek bildiren gateway'de kosuyor) ne de
    sonuc supurucusu (`status='sent'` arar) o satirlara bakiyordu. Yani
    "gateway yukseltilmezse komut TTL dolunca `expired` olur" sozu
    tutulmuyordu: operator panelinde komut sonsuza kadar "bekliyor" gorunurdu.

    Teslim yapilmadigi icin `delivery_attempt` artmaz ve komut cihaza HIC
    gitmemistir; sonlandirma her zaman `expired`dir.
    """
    ttl_sec = int(settings.command_max_age_sec)
    sonlandirilan: list[tuple[DeviceCommand, str]] = []

    for cmd in db.scalars(
        select(DeviceCommand)
        .where(
            DeviceCommand.gateway_code == gateway_code,
            DeviceCommand.status == "pending",
        )
        .order_by(DeviceCommand.id.asc())
        .with_for_update()
    ).all():
        bitis = son_kullanma(cmd, ttl_sec)
        if bitis is None or now <= bitis:
            continue
        # Bu dala DUSEN komut hic teslim edilmedi. Kiralanmis bir komut bu
        # fonksiyona gelemez (asagidaki iddia), dolayisiyla `expired` dogru.
        if int(cmd.delivery_attempt or 0) > 0:
            # Savunma: kiralanmis komut yanlislikla bu yola dusmus.
            _sonlandir(
                cmd, now=now, result_status=RESULT_UNKNOWN,
                hata=(
                    "Komut gateway'e sunuldu ancak kabul edildigi dogrulanamadan "
                    "azami yasa ulasti. Fiziksel komut uygulanmis olabilir."
                ),
            )
            sonlandirilan.append((cmd, RESULT_UNKNOWN))
            continue
        _sonlandir(
            cmd, now=now, result_status=RESULT_EXPIRED,
            hata="Komut gateway'e teslim edilmeden once zaman asimina ugradi",
        )
        cmd.sent_at = None
        sonlandirilan.append((cmd, RESULT_EXPIRED))
        logger.warning(
            "event=command_expired_backend gateway_code=%s command_id=%s "
            "device_code=%s command=%s dnp3_index=%s ttl_sec=%s",
            gateway_code, cmd.id, cmd.device_code, cmd.command,
            cmd.dnp3_index, ttl_sec,
        )
    return sonlandirilan


def ack_uygula(
    db: Session,
    *,
    gateway_code: str,
    ackler: list[tuple[int, str]],
    now: datetime,
) -> tuple[int, int]:
    """Gateway'in dayanikli kabul bildirimlerini uygular.

    Doner: (kabul_edilen, reddedilen).

    Dogrulama zinciri:
      * komut GERCEKTEN bu gateway'e ait mi (IDOR koruma)
      * jeton sabit-zamanli karsilastirma ile esiyor mu
      * komut ACK kabul edecek durumda mi

    Mukerrer ACK idempotent NO-OP'tur ve KABUL sayilir: gateway kuyrugunun
    ilerleyebilmesi icin ACK'in "islendi" cevabini almasi gerekir; reddetmek
    onu sonsuz yeniden denemeye sokardi.

    JETON HICBIR LOG SATIRINDA YER ALMAZ.
    """
    if not ackler:
        return (0, 0)

    ids = {cid for cid, _ in ackler}
    # `ORDER BY id` — KILIT SIRASI `/pending` ILE AYNI OLMALI.
    #
    # Olculdu: sirasiz bir index taramasi satirlari fiziksel dizilime gore
    # kilitliyor; `kirala` ise `id ASC` ile kilitliyor. Iki uc ayni iki komuta
    # ayni anda dokundugunda Postgres DEADLOCK uretti ve kurban `/pending`
    # oldu — yani SCADA komut poll'u 500 dondu ve o turda HICBIR komut teslim
    # edilmedi. Ayni siralama iki ucu da tek bir kilit sirasina sokar.
    rows = {
        row.id: row
        for row in db.scalars(
            select(DeviceCommand)
            .where(
                DeviceCommand.id.in_(ids),
                DeviceCommand.gateway_code == gateway_code,
            )
            .order_by(DeviceCommand.id.asc())
            .with_for_update()
        ).all()
    }

    kabul = 0
    ret = 0
    for command_id, jeton in ackler:
        cmd = rows.get(command_id)
        if cmd is None:
            # Baska gateway'e ait ya da silinmis.
            ret += 1
            logger.warning(
                "event=command_ack_rejected reason=unknown_command gateway_code=%s command_id=%s",
                gateway_code, command_id,
            )
            continue

        beklenen = cmd.delivery_token or ""
        # Sabit-zamanli karsilastirma: jeton bir yetki kanitidir.
        #
        # BAYTA CEVIRILIYOR: `compare_digest` ASCII DISI str ile `TypeError`
        # atar. Bozuk bir gateway defteri satiri tek bir ASCII disi jeton
        # gonderdiginde bu istisna TUM PARTIYI dusururdu — ayni partideki
        # GECERLI ACK'ler de kaybolur, uc 500 doner ve gateway (5xx'i gecici
        # sayacagi icin) ayni partiyi sonsuza kadar yeniden denerdi.
        if not beklenen or not secrets.compare_digest(
            beklenen.encode("utf-8"), str(jeton).encode("utf-8")
        ):
            ret += 1
            logger.warning(
                "event=command_ack_rejected reason=token_mismatch gateway_code=%s command_id=%s",
                gateway_code, command_id,
            )
            continue

        if cmd.status == "sent":
            # Mukerrer ACK — idempotent no-op.
            kabul += 1
            continue

        if cmd.status != "pending":
            # Terminal ya da iptal edilmis komut icin ACK gec kalmis.
            # Durumu DEGISTIRMIYORUZ: `failed`/`cancelled` bir komutu `sent`e
            # geri almak lifecycle'i tersine cevirirdi.
            ret += 1
            logger.warning(
                "event=command_ack_rejected reason=not_pending gateway_code=%s "
                "command_id=%s status=%s",
                gateway_code, command_id, cmd.status,
            )
            continue

        # KABUL: `sent` artik "gateway dayanikli olarak kabul etti" demek.
        cmd.status = "sent"
        cmd.sent_at = now
        kabul += 1
        logger.info(
            "event=command_delivery_acked gateway_code=%s command_id=%s "
            "device_code=%s command=%s attempt=%s",
            gateway_code, cmd.id, cmd.device_code, cmd.command, cmd.delivery_attempt,
        )

    return (kabul, ret)
