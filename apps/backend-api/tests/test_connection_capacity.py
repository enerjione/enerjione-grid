"""Belgelenen olcekleme recetesi Postgres baglanti tavanina SIGMALI (Faz 2-16).

YASANAN CELISKI
---------------
`docker-compose.yml` 600 cihaz icin su receteyi belgeliyor:

    E1_BACKEND_ROLE=api
    E1_API_WORKERS=4
    docker compose --profile scale up -d

Baglanti talebi:

    backend-api, 4 uvicorn worker'i -> her worker KENDI engine'ini kurar
        4 x (db_pool_size 30 + db_max_overflow 20) = 200
    backend-worker (scale profili, tek surec)      =  50
                                              TOPLAM = 250

PostgreSQL varsayilani ise 100 ve hicbir yerde yukseltilmemisti. Yani
receteyi uygulayan operator, yuk arttiginda "remaining connection slots are
reserved" hatalarina giriyordu — ustelik bu tam da OLCEKLEMEK icin yapilan
degisiklikten SONRA olacagi icin sebep-sonuc iliskisi yaniltici.

Bu test hesabi KAYNAKTAN okur: havuz boyutu, worker sayisi ya da
`max_connections` degisirse ayrisma PR aninda yakalanir.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import settings

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "docker-compose.yml"

# Recetede belgelenen worker sayisi (compose yorumundaki "E1_API_WORKERS=4").
BELGELENEN_API_WORKERS = 4
# scale profilindeki ayri tuketici container'i.
WORKER_CONTAINER_SURECI = 1


def _compose_metni() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _max_connections() -> int:
    m = re.search(r'max_connections=(\d+)', _compose_metni())
    assert m, (
        "docker-compose.yml'de max_connections ayarlanmamis — PostgreSQL "
        "varsayilani 100 ve belgelenen olcekleme recetesi bunu asiyor"
    )
    return int(m.group(1))


def _surec_basina_baglanti() -> int:
    return settings.db_pool_size + settings.db_max_overflow


def test_compose_BULUNDU():
    """Yol yanlissa asagidaki testler sessizce yesil kalirdi."""
    assert COMPOSE.is_file(), COMPOSE


def test_recete_max_connections_a_SIGIYOR():
    """Asil celiski buydu: 250 talep, 100 tavan."""
    talep = (BELGELENEN_API_WORKERS + WORKER_CONTAINER_SURECI) * _surec_basina_baglanti()
    tavan = _max_connections()
    assert talep <= tavan, (
        f"belgelenen olcekleme recetesi {talep} baglanti istiyor ama "
        f"max_connections={tavan}. Operator receteyi uygulayinca 'remaining "
        "connection slots are reserved' hatalarina girer — ve bu, olceklemek "
        "icin yapilan degisiklikten SONRA olur."
    )


def test_superuser_ve_bakim_payi_VAR():
    """Tavan tam talebe esit olmamali.

    `superuser_reserved_connections` (varsayilan 3) + operator psql'i +
    yedek/geri yukleme oturumu icin bosluk kalmali; aksi halde sistem
    "calisiyor ama mudahale edilemez" duruma duser.
    """
    talep = (BELGELENEN_API_WORKERS + WORKER_CONTAINER_SURECI) * _surec_basina_baglanti()
    assert _max_connections() >= talep + 20, (
        "baglanti tavani talebe cok yakin — operator psql ile baglanamaz"
    )


def test_recete_compose_ta_HALA_belgeli():
    """Hesap belgelenen receteye dayaniyor; rece degisirse test de degismeli."""
    metin = _compose_metni()
    assert f"E1_API_WORKERS={BELGELENEN_API_WORKERS}" in metin, (
        "compose'daki olcekleme recetesi degismis — baglanti hesabi da "
        "guncellenmeli"
    )
    assert "--profile scale" in metin


@pytest.mark.parametrize("alan", ["db_pool_size", "db_max_overflow"])
def test_havuz_ayarlari_MAKUL(alan: str):
    """Havuz surec BASINA; tek bir surec tavanin tamamini yutmamali."""
    deger = getattr(settings, alan)
    assert 1 <= deger <= 100, f"{alan}={deger} makul araligin disinda"


def test_tek_surec_kurulumu_da_SIGIYOR():
    """Varsayilan (tek surec) kurulum her zaman rahat sigmali."""
    assert _surec_basina_baglanti() * 2 <= _max_connections()
