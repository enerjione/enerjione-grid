"""Gateway compose sablonlari — SAHADA DOGRULANMIS uc regresyon (2026-08-07).

Sablon IKI YERDE duruyor: `app/services/gateway_compose.py` (indirilen dosya)
ve `infra/appliance/e1-gwd.py` (cihaza yerel kurulum). Biri guncellenip
digeri unutuldugu icin asagidaki ayarlar sahada SESSIZCE kayboldu; bu testler
ikisini birden kilitler.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.gateway_compose import (
    DEFAULT_GATEWAY_IMAGE,
    _COMPOSE_TEMPLATE,
)

_KOK = Path(__file__).resolve().parents[3]
_AJAN = _KOK / "infra" / "appliance" / "e1-gwd.py"


def _ajan_kaynak() -> str:
    return _AJAN.read_text(encoding="utf-8", errors="ignore")


# --- 1. Guncellemede silinen ayarlar ---------------------------------------
#
# `DNP3_EVENT_SCAN_INTERVAL_SEC` yoksa scan araligi poll araligina (1 sn)
# duser: her cihaz icin saniyede bir DNP3 istegi. 401 cihazli olcumde CPU
# %108. Ajan guncellemede compose'u KENDI SABLONUNDAN yeniden urettigi icin,
# sablonda olmayan ayar her guncellemede SILINIR — cozulmus bir performans
# sorunu geri gelir.
@pytest.mark.parametrize("anahtar", ["DNP3_EVENT_SCAN_INTERVAL_SEC", "INSTALL_MODE"])
def test_indirilen_compose_sablonunda_ayar_var(anahtar: str):
    assert anahtar in _COMPOSE_TEMPLATE, (
        f"{anahtar} indirilen compose sablonundan dusmus — guncellemede silinir"
    )


@pytest.mark.parametrize("anahtar", ["DNP3_EVENT_SCAN_INTERVAL_SEC", "INSTALL_MODE"])
def test_ajan_compose_sablonunda_ayar_var(anahtar: str):
    assert anahtar in _ajan_kaynak(), (
        f"{anahtar} e1-gwd.py sablonundan dusmus — guncellemede silinir"
    )


def test_install_mode_degerleri_dogru_yerde():
    """Indirilen dosya uzak makinede kosar (remote); ajan cihaza yerel kurar."""
    assert re.search(r'INSTALL_MODE:\s*"remote"', _COMPOSE_TEMPLATE)
    assert re.search(r'INSTALL_MODE:\s*"local"', _ajan_kaynak())


# --- 2. Imaj etiketi kilidi -------------------------------------------------
#
# `image` UPDATE_PARAM_KEYS'te yoksa ajan compose'daki mevcut etiketi geri
# kazanip aynen yazar: compose'a bir kez `:1.5.0` yazilirsa "Guncelle" onu
# BIR DAHA ASLA degistiremez. Sahada GW-001 boyle kilitlendi ve ekran kalici
# olarak "Guncel" dedi; 1.6.0/1.6.1 hic gorunmedi.
def test_ajan_guncellemede_image_parametresini_kabul_eder():
    kaynak = _ajan_kaynak()
    eslesme = re.search(r"UPDATE_PARAM_KEYS\s*=\s*frozenset\(\{([^}]*)\}\)", kaynak)
    assert eslesme is not None, "UPDATE_PARAM_KEYS bulunamadi"
    assert '"image"' in eslesme.group(1), (
        "UPDATE_PARAM_KEYS'te `image` yok — sabit etikete kilitlenmis kurulum "
        "guncellemeyle DUZELMEZ"
    )


def test_guncelleme_istegi_latest_etiketi_gonderir():
    """Sahada sabitlenmis kurulum ilk guncellemede normallesmeli."""
    from app.services import gateway_agent_service as svc

    yazilan: dict = {}

    def sahte_write(body: dict) -> str:
        yazilan.update(body)
        return "istek-1"

    orijinal = svc._write_request
    svc._write_request = sahte_write  # type: ignore[assignment]
    try:
        svc.request_update("GW-001", "tester")
    finally:
        svc._write_request = orijinal  # type: ignore[assignment]

    assert yazilan["params"]["image"] == DEFAULT_GATEWAY_IMAGE
    assert DEFAULT_GATEWAY_IMAGE.endswith(":latest"), (
        "takip edilen etiket sabit bir surume baglanmis — guncelleme kilitlenir"
    )


def test_compose_her_kalkista_imaji_yeniden_ceker():
    """`:latest` TEK BASINA YETMEZ: `restart: unless-stopped` ile container
    yeniden baslarken imaj tekrar CEKILMEZ ve "latest kullaniyoruz"
    yanilsamasi olusur."""
    assert "pull_policy: always" in _COMPOSE_TEMPLATE
    assert "pull_policy: always" in _ajan_kaynak()


# --- 3. Poll havuzu olceklenmesi -------------------------------------------
#
# Sabit 500 idi: cihaz sayisi bu degere ESITSE hic pay kalmaz ve yavas cevap
# veren birkac cihaz slotu tutunca digerlerine o turda hic istek gonderilemez
# (sahada: poll_pool_starved baslamayan=6 due=500 workers=500).
def test_poll_havuzu_cihaz_sayisina_gore_olceklenir():
    import importlib.util

    spec = importlib.util.spec_from_file_location("e1gwd_test", _AJAN)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)

    hesapla = modul._max_parallel_devices
    assert hesapla(100) == 120, "cihaz sayisinin uzerine %20 pay birakilmali"
    assert hesapla(500) == 600, "500 cihazda havuz 500'de kalirsa pay kalmaz"
    assert hesapla(1) == 50, "taban 50 olmali"
    assert hesapla(5000) == 1000, "tavan 1000 olmali"
    assert hesapla(None) == 500, "bilinmiyorsa guvenli varsayilan"
