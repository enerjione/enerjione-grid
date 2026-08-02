"""Kodda okunan her ayara operator GERCEKTEN ulasabiliyor mu?

TEKRAR EDEN HATA SINIFI
-----------------------
Bir ayar `os.getenv("X", varsayilan)` ile okunur, ama `docker-compose.yml`
backend servisine `X`i GECIRMEZ. Sonuc: ayar KODDA vardir, dokumanda vardir,
`.env`e yazilir — ama container'in ortaminda hic olusmaz. Operator degeri
degistirir, hicbir sey olmaz. Hata vermez, log basmaz; sessizce varsayilan
kullanilir.

Bu sinif bu depoda BES KEZ ayri ayri bulundu:
    BACKUP_OFFSITE_DIR
    ACCESS_TOKEN_MINUTES
    OUTBOX_* (5 degisken)
    NATS_STREAM_*_MAX_AGE_DAYS (3 degisken)
    GATEWAY_STALE_* / GATEWAY_UPDATE_CHECK_* / GATEWAY_FLEET_*

Her seferinde elle bulundu. Bu test altincisini otomatik yakalar.

BILINEN BORC
------------
Asagidaki `BILINEN_BOSLUKLAR` listesi "bunlar sorunsuz" DEMEZ — "bunlar
henuz incelenmedi" der. Liste yalnizca KUCULMELI. Yeni bir ayar eklenip
compose'a gecirilmezse test duser ve gelistirici bilincli bir karar vermek
zorunda kalir: ya compose'a ekler, ya da gerekcesiyle bu listeye yazar.
"""

from __future__ import annotations

import re
from pathlib import Path

_KOK = Path(__file__).resolve().parents[3]

#: Kodda okunan ama compose'un gecirmedigi, HENUZ INCELENMEMIS ayarlar.
#: Bu liste bir BORC KAYDIDIR; buyumemeli.
BILINEN_BOSLUKLAR = {
    # Alarm motoru tunable'lari — gercekten ayarlanabilir olmali mi,
    # yoksa sabit mi kalmali, karar verilmedi.
    "ALARM_RATE_WINDOW_MIN",
    "ALARM_RECONCILE_INTERVAL_SEC",
    "ALARM_RECONCILE_LOOKBACK_MIN",
    "BACKUP_SCHEDULER_TICK_SEC",
    "FAULT_RECOMPUTE_MIN_INTERVAL_SEC",
    "LICENSE_GATE_CACHE_TTL_SEC",
    # Ic servis adresi / yol varsayilanlari — calisan varsayilanlari var.
    "IEC104_OUTBOUND_HEALTH_URL",
    "MQTT_CERT_DIR",
    # Geri yukleme araclari: backend surecinde DEGIL, bakim komutlarinda
    # kullaniliyor. Compose'dan gecmemeleri beklenen davranis olabilir.
    "POSTGRES_RESTORE_USER",
    "POSTGRES_RESTORE_PASSWORD",
    "PSQL",
}


def _kodda_okunan_ayarlar() -> set[str]:
    bulunan: set[str] = set()
    desen = re.compile(r'os\.getenv\(\s*["\']([A-Z0-9_]+)["\']')
    for yol in (_KOK / "apps" / "backend-api" / "app").rglob("*.py"):
        bulunan |= set(desen.findall(yol.read_text(encoding="utf-8", errors="ignore")))
    return bulunan


def _composeun_gecirdikleri() -> set[str]:
    metin = (_KOK / "docker-compose.yml").read_text(encoding="utf-8", errors="ignore")
    # backend-api servisinin blogu: bir sonraki ust-seviye servise kadar.
    blok = re.search(r"\n  backend-api:.*?(?=\n  [a-z0-9-]+:\n)", metin, re.S)
    assert blok is not None, "docker-compose.yml icinde backend-api servisi bulunamadi"
    return set(re.findall(r"^\s{6}([A-Z0-9_]+):", blok.group(0), re.M))


def test_kodda_okunan_ayar_composeda_da_geciyor() -> None:
    kodda = _kodda_okunan_ayarlar()
    assert kodda, "hic os.getenv bulunamadi — desen ya da yol bozulmus olmali"

    ulasilamayan = kodda - _composeun_gecirdikleri() - BILINEN_BOSLUKLAR
    assert not ulasilamayan, (
        "Bu ayarlar kodda okunuyor ama docker-compose.yml backend-api'ye "
        "GECIRMIYOR — operator degeri degistirse bile hicbir sey olmaz:\n  "
        + "\n  ".join(sorted(ulasilamayan))
        + "\n\ncompose'a ekleyin ya da gerekcesiyle BILINEN_BOSLUKLAR'a yazin."
    )


def test_borc_listesi_bayatlamasin() -> None:
    """Borc listesindeki bir ayar artik kodda YOKSA ya da compose'a EKLENDIYSE
    listeden de cikarilmali.

    Aksi halde liste zamanla anlamsiz isimlerle sisip "incelendi mi?"
    sorusunu cevaplanamaz hale getirir.
    """
    kodda = _kodda_okunan_ayarlar()
    gecen = _composeun_gecirdikleri()

    artik_yok = {a for a in BILINEN_BOSLUKLAR if a not in kodda}
    assert not artik_yok, (
        "Bu ayarlar kodda artik okunmuyor; BILINEN_BOSLUKLAR'dan silin:\n  "
        + "\n  ".join(sorted(artik_yok))
    )

    cozulmus = BILINEN_BOSLUKLAR & gecen
    assert not cozulmus, (
        "Bu ayarlar artik compose'dan geciyor; BILINEN_BOSLUKLAR'dan silin:\n  "
        + "\n  ".join(sorted(cozulmus))
    )
