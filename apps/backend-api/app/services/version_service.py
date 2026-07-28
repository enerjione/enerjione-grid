"""Surum bilgisi ve (SADECE BILGI AMACLI) guncelleme kontrolu.

Neden burada:
  Lisans sayfasi calisan surumu, Sistem Durumu sayfasi ise "yeni surum var mi"
  ibaresini gosteriyor. Ikisi de ayni kaynaktan beslenmeli, aksi halde iki
  sayfada farkli surum yazabilir.

ONEMLI — panelden guncelleme YAPILMAZ:
  Bu modul yalnizca UZAKTAKI surumu OKUR ve karsilastirir. Indirme/kurma/
  restart gibi hicbir islem yoktur ve bilincli olarak eklenmemistir:
  guncelleme `update.sh` ile, operatorun kontrolunde yapilir. Arayuzde
  "guncelle" butonu olmasi, calisan bir SCADA sisteminin operator onayi
  olmadan yeniden baslamasi riskini getirir.

Guncelleme kaynagi:
  `settings.update_check_url` bos ise kontrol KAPALIDIR (varsayilan).
  Doluysa o adresten JSON beklenir; surum su anahtarlardan ilk bulunanda
  aranir: "version" | "latest" | "tag_name". Boylece hem kendi yazdiginiz
  basit bir manifest (`{"version": "2.25.0"}`) hem de GitHub Releases API
  (`{"tag_name": "v2.25.0", ...}`) dogrudan calisir.

Sonuc `update_check_interval_sec` boyunca cache'lenir; Sistem Durumu sayfasi
10 saniyede bir sorgu attigi icin cache olmadan her istekte dis agri cikardi.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request

from app.core.config import settings

logger = logging.getLogger(__name__)

# Surumun icinden sayisal parcalari cikar: "v2.25.0-rc1" -> (2, 25, 0)
_NUM_RE = re.compile(r"\d+")
# Uzak JSON'da surumun aranacagi anahtarlar (sirayla).
_VERSION_KEYS = ("version", "latest", "tag_name", "latest_version")

_lock = threading.Lock()
# (fetched_at_monotonic, latest_version veya None, hata_metni veya None)
_cache: tuple[float, str | None, str | None] | None = None


def parse_version(raw: str | None) -> tuple[int, ...]:
    """Surum metnini karsilastirilabilir sayi demetine cevir.

    "v2.25.0" -> (2, 25, 0);  "2.24" -> (2, 24);  bos/bozuk -> ()
    Sadece sayilara bakar; "-rc1" gibi ekler yok sayilir (rc'yi stable'dan
    ayirmak icin ek kural gerekir, simdilik gerekmiyor).
    """
    if not raw:
        return ()
    return tuple(int(p) for p in _NUM_RE.findall(str(raw))[:4])


def is_newer(latest: str | None, current: str | None) -> bool:
    """latest > current mi? Ikisi de ayristirilamazsa False."""
    lv, cv = parse_version(latest), parse_version(current)
    if not lv or not cv:
        return False
    # Uzunluklari esitle: (2,25) vs (2,24,4) -> (2,25,0) vs (2,24,4)
    size = max(len(lv), len(cv))
    lv += (0,) * (size - len(lv))
    cv += (0,) * (size - len(cv))
    return lv > cv


def _extract_version(payload: object) -> str | None:
    """JSON govdesinden surum alanini bul (dict veya liste kabul edilir)."""
    if isinstance(payload, list):
        # GitHub /releases -> liste; ilk kayit en yenisi.
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return None
    for key in _VERSION_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _fetch_latest() -> tuple[str | None, str | None]:
    """Uzak surumu cek. Returns (latest_version, error)."""
    url = (settings.update_check_url or "").strip()
    if not url:
        return None, None
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": f"EnerjiOneGrid/{settings.app_version}",
            },
        )
        # Kisa timeout: bu cagri Sistem Durumu isteginin icinde; yavas bir
        # sunucu sayfayi bekletmesin.
        with urllib.request.urlopen(req, timeout=4.0) as resp:  # noqa: S310
            body = resp.read(64_000).decode("utf-8", errors="replace")
        latest = _extract_version(json.loads(body))
        if latest is None:
            return None, "Uzak yanitta surum alani bulunamadi"
        return latest, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_check_failed url=%s error=%s", url, exc)
        return None, f"Kontrol basarisiz: {type(exc).__name__}"


def get_version_info(force: bool = False) -> dict:
    """Surum + guncelleme durumu. Sonuc cache'lenir.

    Donus:
      current           : calisan surum
      check_enabled     : update_check_url tanimli mi
      latest            : uzak surum (yoksa None)
      update_available  : latest > current mi
      error             : kontrol hatasi (varsa)
      checked_at        : son basarili/denenmis kontrol (epoch, yoksa None)
    """
    global _cache
    current = settings.app_version
    url = (settings.update_check_url or "").strip()

    if not url:
        return {
            "current": current,
            "check_enabled": False,
            "latest": None,
            "update_available": False,
            "error": None,
            "checked_at": None,
        }

    ttl = max(60, int(settings.update_check_interval_sec))
    now = time.monotonic()
    with _lock:
        cached = _cache
        fresh = cached is not None and (now - cached[0]) < ttl and not force
        if not fresh:
            latest, error = _fetch_latest()
            _cache = (now, latest, error)
        else:
            _, latest, error = cached  # type: ignore[misc]

    return {
        "current": current,
        "check_enabled": True,
        "latest": latest,
        "update_available": is_newer(latest, current),
        "error": error,
        "checked_at": time.time(),
    }
