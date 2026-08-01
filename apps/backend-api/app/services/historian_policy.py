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
"""

from __future__ import annotations

import logging
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


@dataclass(frozen=True)
class SignalPolicy:
    historize: bool
    deadband: float


#: signal_key -> SignalPolicy
_catalog_cache: dict[str, SignalPolicy] | None = None
_catalog_cached_at: float = 0.0

#: (device_id, signal_key) -> son ARŞİVLENEN sayısal değer
_last_archived: dict[tuple[int, str], float] = {}


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
        )
        for key, historize, deadband in rows
    }
    _catalog_cached_at = now
    return _catalog_cache


def should_archive(
    db: Session,
    *,
    device_id: int,
    signal_key: str,
    value: float | None,
) -> bool:
    """Bu okuma `telemetry_history`'ye yazılmalı mı?

    Bilinmeyen sinyal -> True. Katalogda olmayan bir anahtarı sessizce
    atmak, yeni eklenen bir sinyalin arşivinin hiç oluşmamasına yol açardı.
    """
    politika = _load_catalog(db).get(signal_key)
    if politika is None:
        return True
    if not politika.historize:
        return False

    deadband = politika.deadband
    if deadband <= 0.0 or value is None:
        # Ölü bant kapalı ya da sayısal olmayan değer (string/binary):
        # her okuma arşivlenir.
        return True

    anahtar = (device_id, signal_key)
    onceki = _last_archived.get(anahtar)
    if onceki is not None and abs(value - onceki) < deadband:
        return False

    if onceki is None and len(_last_archived) >= _LAST_CACHE_MAX:
        # Sinir asildi: en eski girisleri atmak yerine yeni kayit ACMIYORUZ.
        # Sonuc "bu sinyal her zaman arsivlenir" — yani GUVENLI yon.
        return True
    _last_archived[anahtar] = value
    return True


def cache_size() -> int:
    """Son arşivlenen değer önbelleğinin boyutu (izleme için)."""
    return len(_last_archived)
