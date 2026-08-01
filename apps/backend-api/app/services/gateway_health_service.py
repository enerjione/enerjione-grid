"""Gateway saglik heartbeat'i — ayristirma ve kayit.

AKIS
  Gateway zaten saniyede bir `GET /gateways/{code}/pending` cagiriyor.
  Saglik ozeti bu istege `X-E1-Gateway-Health` basligiyla biniyor:
  base64url(compact JSON). Ek istek maliyeti YOK.

TASARIMIN EN ONEMLI KURALI — KOMUT KANALI KUTSAL
  `/pending` SCADA komut kanalidir. Buradaki hicbir sey — bozuk base64,
  gecersiz JSON, devasa baslik, beklenmedik tip, DB hatasi — o kanali
  DUSURMEMELIDIR. Aksi halde saglik raporlamak icin eklenen bir ozellik,
  kesici acma/kapama komutlarinin iletilmesini engelleyebilir. Bu yuzden
  ayristirma tamamen savunmacidir ve HER hata sessizce yutulup None doner.

BOYUT SINIRI
  Baslik `_MAX_HEADER_BYTES` uzerindeyse HIC ayristirilmaz. Gerekce iki
  yonlu: (1) kotu niyetli ya da hatali bir gateway backend'i mesgul
  edemesin, (2) nginx varsayilan baslik tavanina (genelde 8 KB) yaklasan
  bir baslik istegin TAMAMINI 431/400 ile reddettirir — yani komutlar da
  gitmez. 2 KB gercek govdenin (yaklasik 300 bayt) cok uzerinde.

YAZMA SIKLIGI
  Gateway her saniye saglik gondermez; backend `heartbeat_interval_sec`
  (varsayilan 30) ile sikligi SOYLER. Yine de erken gelen tekrarlar
  `_MIN_WRITE_INTERVAL_SEC` ile suzuluyor: 1 Hz'de DB'ye yazmak gateway
  basina gunde 86.400 UPDATE demekti.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.gateway_health import GatewayHealth

logger = logging.getLogger(__name__)

HEALTH_HEADER = "X-E1-Gateway-Health"

# Gercek govde ~300 bayt; 2 KB bol marj birakir ve nginx baslik tavaninin
# cok altinda kalir.
_MAX_HEADER_BYTES = 2048

# Ayni gateway'den bu sure icinde gelen tekrarlar DB'ye yazilmaz.
# `heartbeat_interval_sec` 30 sn; 25 sn esik jitter'a tolerans birakir.
_MIN_WRITE_INTERVAL_SEC = 25.0

# gateway_code -> son yazim (monotonic)
_last_write: dict[str, float] = {}

# Gateway'in gonderebilecegi issue kodlari uzun olabilir; kolon 1000 karakter.
_MAX_ISSUES_CHARS = 1000
_MAX_RAW_CHARS = 4000


def parse_health_header(raw: str | None) -> dict[str, Any] | None:
    """Basligi coz. BASARISIZLIK HER ZAMAN None — asla istisna firlatmaz.

    Cagiran taraf `/pending` ucudur ve orada bir istisna SCADA komut
    kanalini dusururdu.
    """
    if not raw:
        return None
    try:
        if len(raw) > _MAX_HEADER_BYTES:
            logger.warning(
                "gateway_health_header_too_large bytes=%d limit=%d — yok sayildi",
                len(raw),
                _MAX_HEADER_BYTES,
            )
            return None
        # base64url; gateway padding atlamis olabilir, tamamliyoruz.
        pad = "=" * (-len(raw) % 4)
        data = base64.urlsafe_b64decode(raw + pad)
        parsed = json.loads(data.decode("utf-8"))
        if not isinstance(parsed, dict):
            return None
        return parsed
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        logger.debug("gateway_health_header_unparsable error=%s", exc)
        return None
    except Exception:  # noqa: BLE001
        # Beklenmedik hicbir sey komut kanalini dusurmemeli.
        logger.debug("gateway_health_header_failed", exc_info=True)
        return None


def _as_int(value: Any) -> int | None:
    """Tip zorlamasi — gateway yanlis tip gonderirse satir yine yazilsin."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_str(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


def record_health(db: Session, gateway_code: str, payload: dict[str, Any]) -> bool:
    """Saglik ozetini upsert eder. True = yazildi, False = atlandi/basarisiz.

    Hata DURUMUNDA DA False doner ve istisna SIZDIRMAZ: bu fonksiyon komut
    kanalinin icinden cagriliyor.
    """
    now_mono = time.monotonic()
    last = _last_write.get(gateway_code)
    if last is not None and (now_mono - last) < _MIN_WRITE_INTERVAL_SEC:
        return False  # cok sik — sicak yolda gereksiz yazma yapma

    devices = payload.get("devices")
    if not isinstance(devices, dict):
        devices = {}
    issues = payload.get("issues")
    if isinstance(issues, (list, tuple)):
        issues_text = ",".join(str(i) for i in issues)[:_MAX_ISSUES_CHARS]
    else:
        issues_text = _as_str(issues, _MAX_ISSUES_CHARS)

    try:
        raw_text = json.dumps(payload, ensure_ascii=False)[:_MAX_RAW_CHARS]
    except (TypeError, ValueError):
        raw_text = None

    values = {
        "gateway_code": gateway_code,
        "status": _as_str(payload.get("status"), 20) or "unknown",
        "issues": issues_text,
        "outbox_pending": _as_int(payload.get("outbox_pending")),
        "outbox_dead_letter": _as_int(payload.get("outbox_dead_letter")),
        "devices_total": _as_int(devices.get("total")),
        "devices_online": _as_int(devices.get("online")),
        "devices_recovering": _as_int(devices.get("recovering")),
        "devices_lost": _as_int(devices.get("lost")),
        "uptime_sec": _as_int(payload.get("uptime_sec")),
        "gateway_version": _as_str(payload.get("version"), 40),
        "raw_json": raw_text,
        # BACKEND saati — gateway saatine guvenilmez.
        "reported_at": datetime.now(timezone.utc),
    }

    try:
        stmt = pg_insert(GatewayHealth).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["gateway_code"],
            set_={k: v for k, v in values.items() if k != "gateway_code"},
        )
        db.execute(stmt)
        _last_write[gateway_code] = now_mono
        return True
    except Exception:  # noqa: BLE001
        # Saglik kaydi BASARISIZ olsa bile komut kanali calismaya devam
        # etmeli. Caller commit'i kendi yapar; burada rollback ETMIYORUZ
        # cunku ayni transaction'da komut durum gecisi de var.
        logger.warning("gateway_health_record_failed gateway=%s", gateway_code, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Cihaz bazinda link durumu
# ---------------------------------------------------------------------------
#
# NEDEN GEREKLI
#   Backend `communication_status`i yalnizca TELEMETRI GELDIGINDE
#   guncelleyebiliyor. Ariza bekleyen bir gosterge saatlerce sessiz kalabilir
#   — deger degismezse gateway hicbir sey yayinlamaz — ve o sure boyunca
#   cihazin canli mi olu mu oldugunu BILEMIYORUZ.
#
#   "Veri gelmiyor" ile "haberlesme yok" ayni sey degil. Bu ayrimi yapabilecek
#   tek katman gateway: DNP3 baglantisi onda ve link durumunu zaten takip
#   ediyor (`state=lost`, `recovering`, `recovered`).
#
# NEDEN AKTIF YOKLAMA DEGIL
#   Backend'den cihazin IP:port'una TCP baglanmak ZARARLI olurdu: Horstmann
#   outstation'i `CloseExisting` modunda calisiyor, yeni baglanti gelince
#   mevcut olani kapatiyor. Yani "kontrol edeyim" diye atilan her yoklama
#   gateway'in oturumunu dusururdu — sahada 2.172 baglanti kopmasina yol acan
#   dongunun ta kendisi.
#
# BEKLENEN BICIM (gateway tarafi henuz gondermiyor)
#   payload["devices"]["states"] = {"d6": "lost", "d11": "connected", ...}
#
#   Ayri bir kolon ACILMADI: uretici taraf hazir olmadan sema degistirmek
#   erken. Durum zaten `raw_json` icinde saklaniyor ve tek satir okunuyor.

#: Gateway'in bildirdigi durum -> bizim haberlesme durumumuz.
#: `recovering` BILEREK ESLENMEDI: gecis durumu, iki katman arasinda
#: cihazin gidip gelmesine (flapping) yol acardi.
_LINK_STATE_MAP = {
    "connected": "online",
    "online": "online",
    "up": "online",
    "lost": "offline",
    "down": "offline",
    "disconnected": "offline",
}


def device_link_states(raw_json: str | None) -> dict[str, str]:
    """`raw_json` icinden cihaz bazinda haberlesme durumu cikarir.

    Donen sozluk: {device_code: "online"|"offline"}. Taninmayan durum ve
    `recovering` DISARIDA birakilir — emin olmadigimiz bir seyi yazmak,
    hicbir sey yazmamaktan kotudur.

    Ayristirma tamamen savunmaci: bu veri gateway'den geliyor ve bozuk bir
    govde cihaz durumlarini bozmamali.
    """
    if not raw_json:
        return {}
    try:
        payload = json.loads(raw_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    devices = payload.get("devices")
    if not isinstance(devices, dict):
        return {}
    states = devices.get("states")
    if not isinstance(states, dict):
        return {}

    sonuc: dict[str, str] = {}
    for kod, durum in states.items():
        if not isinstance(kod, str) or not kod:
            continue
        esleme = _LINK_STATE_MAP.get(str(durum).strip().lower())
        if esleme is not None:
            sonuc[kod[:50]] = esleme
    return sonuc
