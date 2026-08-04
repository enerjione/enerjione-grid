"""Historian (arşiv) politikası — hangi okuma arşive yazılır.

GERÇEK SCADA PRATİĞİ
--------------------
Her tag arşive yazılmaz. Anlık değer (RTDB) her zaman güncel tutulur —
alarmlar, ekranlar ve kontrol oradan okur — ama arşive yalnızca işaretlenen
tag'ler, üstelik ölü bant süzgecinden geçerek yazılır.

Bu sistemde iki koşul da zaten sağlanıyor:
  * alarm motoru akış tabanlı (`alarm-service` JetStream'i dinliyor, geçmiş
    sorgusu YAPMIYOR),
  * canlı değer `telemetry_latest` tablosunda.

Dolayısıyla arşivi kısmak **alarm doğruluğunu etkilemiyor**.

SON ARŞİVLENEN DEĞER NEREDE TUTULUYOR
-------------------------------------
Süreç içi önbellekte, DB'de değil. Gerekçe: ölü bant kararı için "son
arşivlenen değer" lazım ve bunu `telemetry_latest`e yazmak o sıcak tabloya
bir kolon daha eklemek demekti.

Önbelleğin boş olması **güvenli yönde** hata verir: bilinmeyen bir (cihaz,
sinyal) çifti için okuma ARŞİVLENİR. Yani yeniden başlatmada ya da birden
fazla tüketici örneğinde en kötü ihtimalle biraz FAZLA yazılır; veri
kaybolmaz.

OLU BANDIN IKI SINIRI — PAZARLIK EDILEMEZ
------------------------------------------
Bu bir ariza-gecis gostergesi sistemi; bir gecisi KACIRMAK kabul edilemez.
Olu bant bu yuzden iki yerde kilitli:

  1. YALNIZCA ANALOG. Ikili (binary), sayac ve metin sinyalinde "yakin
     deger" diye bir sey yoktur: 0 -> 1 bir OLAYDIR. Bu tiplerde esik ne
     olursa olsun suzgec DEVREYE GIRMEZ (bkz. `_OLU_BANT_TIPLERI`).
  2. KALITE DEGISIMI ESIGI ASAR. "Ayni deger + farkli kalite" gercek bir
     olaydir (good -> invalid / comm_lost / restart / forced). Karar
     yalnizca sayiya bakarsa, olu bandin icinde donmus bir olcumde
     haberlesmenin kopmasi arsive HIC girmez; ham kopya `telemetry`
     tablosunda ~30 dk sonra retention ile silindigi icin kayip KALICI olur.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.signal_catalog import SignalCatalog

logger = logging.getLogger(__name__)

#: Katalog önbelleği TTL'i. Operatör bir sinyalin politikasını değiştirdiğinde
#: en geç bu kadar sonra etkili olur.
_CATALOG_TTL_SEC = 60.0

#: Son arşivlenen değer önbelleğinin üst sınırı. 600 cihaz × 193 sinyal =
#: 115.800 giriş; sınır onun üstünde ama sınırsız da değil (sızıntı olmasın).
_LAST_CACHE_MAX = 200_000

#: Olu bandin uygulanabildigi TEK sinyal tipi. `binary`, `binary_output`,
#: `counter` ve `string` DISARIDA: bu tiplerde "yakin deger" yoktur, her
#: degisim bir olaydir. Bilinmeyen/bos tip de disarida kalir (guvenli yon).
#:
#: PUBLIC: arayuze olu bant alanini ACAN katman (signal_catalog_service) bunu
#: okur. Kendi listesini tutsaydi ikisi kacinilmaz olarak birbirinden ayrilir
#: ve arayuz, motorun UYGULAMADIGI bir ayari varmis gibi gosterirdi.
OLU_BANT_TIPLERI = frozenset({"analog"})

#: Dosya ici / geri uyumlu ad. TEK kaynak yukaridaki `OLU_BANT_TIPLERI`.
_OLU_BANT_TIPLERI = OLU_BANT_TIPLERI


@dataclass(frozen=True)
class SignalPolicy:
    historize: bool
    deadband: float
    data_type: str


#: signal_key -> SignalPolicy
_catalog_cache: dict[str, SignalPolicy] | None = None
_catalog_cached_at: float = 0.0

#: (device_id, signal_key) -> son ARSIVLENEN (sayisal deger, kalite)
#: Kalite de tutulur; yoksa "ayni deger + degisen kalite" okumasi yutulurdu.
_last_archived: dict[tuple[int, str], tuple[float, str]] = {}


def reset_caches() -> None:
    """Önbellekleri temizler (testler ve ayar değişikliği sonrası)."""
    global _catalog_cache, _catalog_cached_at
    _catalog_cache = None
    _catalog_cached_at = 0.0
    _last_archived.clear()


def _load_catalog(db: Session) -> dict[str, SignalPolicy]:
    global _catalog_cache, _catalog_cached_at
    now = time.monotonic()
    if _catalog_cache is not None and (now - _catalog_cached_at) < _CATALOG_TTL_SEC:
        return _catalog_cache
    try:
        rows = db.execute(
            select(
                SignalCatalog.key,
                SignalCatalog.historize,
                SignalCatalog.historize_deadband,
                SignalCatalog.data_type,
            )
        ).all()
    except Exception:  # noqa: BLE001
        # Katalog okunamadi (or. migration henuz uygulanmamis). ESKI
        # DAVRANISA don: her sey arsivlenir. Sessizce arsivlemeyi KESMEK
        # veri kaybi olurdu.
        logger.warning("historian_katalog_okunamadi — tum okumalar arsivlenecek",
                       exc_info=True)
        _catalog_cache = {}
        _catalog_cached_at = now
        return _catalog_cache
    _catalog_cache = {
        str(key): SignalPolicy(
            historize=bool(historize),
            deadband=float(deadband or 0.0),
            data_type=str(data_type or ""),
        )
        for key, historize, deadband, data_type in rows
    }
    _catalog_cached_at = now
    return _catalog_cache


def should_archive(
    db: Session,
    *,
    device_id: int,
    signal_key: str,
    value: float | None,
    quality: str = "good",
) -> bool:
    """Bu okuma `telemetry_history`'ye yazılmalı mı?

    Bilinmeyen sinyal -> True. Katalogda olmayan bir anahtarı sessizce
    atmak, yeni eklenen bir sinyalin arşivinin hiç oluşmamasına yol açardı.

    `quality` normalize edilmis kalite (`normalize_quality` ciktisi). Olu
    bant YALNIZCA deger de kalite de ayni kaldiginda eler; kalite
    degistiyse okuma her zaman arsivlenir.
    """
    politika = _load_catalog(db).get(signal_key)
    if politika is None:
        return True
    if not politika.historize:
        return False

    deadband = politika.deadband
    if (
        deadband <= 0.0
        or value is None
        or politika.data_type not in _OLU_BANT_TIPLERI
    ):
        # Olu bant kapali, sayisal olmayan deger, ya da analog OLMAYAN bir
        # sinyal (ikili/sayac/metin): her okuma arsivlenir. Ikili sinyalde
        # esik uygulamak ariza bayraginin gecisini YUTARDI.
        return True

    anahtar = (device_id, signal_key)
    onceki = _last_archived.get(anahtar)
    if (
        onceki is not None
        and onceki[1] == quality
        and abs(value - onceki[0]) < deadband
    ):
        return False

    if onceki is None and len(_last_archived) >= _LAST_CACHE_MAX:
        # Sinir asildi: en eski girisleri atmak yerine yeni kayit ACMIYORUZ.
        # Sonuc "bu sinyal her zaman arsivlenir" — yani GUVENLI yon.
        return True
    # `intern`: 200.000 girislik onbellekte ayni kalite metninin (normalize
    # her cagrida YENI bir str uretir) kopyalanmamasi icin.
    _last_archived[anahtar] = (value, sys.intern(quality))
    return True


def cache_size() -> int:
    """Son arşivlenen değer önbelleğinin boyutu (izleme için)."""
    return len(_last_archived)
