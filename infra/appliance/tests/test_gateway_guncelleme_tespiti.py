""""Guncel" diyen ama guncel OLMAYAN gateway — surum tespiti.

SAHADA YASANAN
--------------
Kayit defterinde `:latest` 1.6.1'e tasindi; cihaz 1.5.0 kosuyordu ve arayuz
"Guncel" diyordu. Yani YENI SURUM YAYINLANDIGI HALDE guncelleme secenegi hic
gorunmuyordu — operator eski surumde kaldigini fark edemezdi.

SEBEP
-----
Uzak digest, `docker ps --format {{.Image}}` ciktisiyla soruluyordu. Bu deger
container'in YARATILDIGI andaki cozulmus referanstir ve
  * digest'e sabitlenmis olabilir  (`repo:tag@sha256:...`)
  * etiket baska imaja kayinca ham imaj ID'sine donebilir
Her iki halde de "uzak" sorgu KENDI digest'ini geri dondurur; karsilastirma
her zaman esit cikar ve sonuc kalici olarak "guncel" olur.

DOGRUSU
-------
Guncelleme kontrolu operatorun IZLEDIGI etikete gore yapilmali; o da compose
dosyasindaki `image:` satiridir. Yerel taraf calisan container'in imaji
olarak kalir — "ne kosuyor" ile "ne izleniyor" farkli sorulardir.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_AJAN_YOL = Path(__file__).resolve().parents[1] / "e1-gwd.py"
_spec = importlib.util.spec_from_file_location("e1_gwd_surum", _AJAN_YOL)
assert _spec is not None and _spec.loader is not None
gwd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gwd)

KOD = "GW-001"
IMAJ = "ghcr.io/enerjione/enerjione-grid-dnp3-gateway"
# Gercek digest'ler (2026-08-06): latest == 1.6.1, 1.5.0 farkli.
D_YENI = "sha256:0e2784be3e8ce6d1e1806e7ec88f0e0ee50da3eb389014dcce1362c58fe65021"
D_ESKI = "sha256:b5c3bd0b60ec7684a0cd65d0bc56952452f16810302e76c7dbc3e86899ae2d56"


@pytest.fixture
def ajan(tmp_path, monkeypatch):
    kok = tmp_path / "gateways"
    (kok / KOD).mkdir(parents=True)
    monkeypatch.setattr(gwd, "GATEWAY_ROOT", str(kok))
    monkeypatch.setattr(gwd, "STATE_DIR", str(tmp_path))
    gwd._remote_digest_cache.clear()
    gwd._remote_version_cache.clear()
    return kok


def _compose_yaz(kok, image_satiri: str) -> None:
    (kok / KOD / "docker-compose.yml").write_text(
        f"services:\n  gateway:\n    image: {image_satiri}\n    restart: unless-stopped\n",
        encoding="utf-8",
    )


# --- compose'daki etiketin okunmasi ----------------------------------------


def test_compose_etiketi_okunur(ajan):
    _compose_yaz(ajan, f"{IMAJ}:latest")
    assert gwd._compose_image(KOD) == f"{IMAJ}:latest"


def test_digeste_sabitlenmis_referansta_digest_ATILIR(ajan):
    """Takip edilen sey ETIKET; o anki digest degil."""
    _compose_yaz(ajan, f"{IMAJ}:latest@{D_ESKI}")
    assert gwd._compose_image(KOD) == f"{IMAJ}:latest"


def test_tirnakli_ve_yorumlu_satir(ajan):
    _compose_yaz(ajan, f'"{IMAJ}:1.6.1"   # sabitlenmis')
    assert gwd._compose_image(KOD) == f"{IMAJ}:1.6.1"


def test_compose_yoksa_bos(ajan):
    assert gwd._compose_image("YOK-BOYLE") == ""


# --- ASIL ARIZA: yeni surum gorunmeli --------------------------------------


def test_YENI_SURUM_gorunur(ajan, monkeypatch):
    """Kayit defterinde latest 1.6.1; cihazda 1.5.0 -> guncelleme VAR."""
    _compose_yaz(ajan, f"{IMAJ}:latest")
    # `docker ps` container'in cozulmus (digest'e sabitli) referansini verir —
    # eski kodun uzak sorguyu bununla yaptigi ve hep "guncel" dedigi yer.
    cozulmus = f"{IMAJ}:latest@{D_ESKI}"

    monkeypatch.setattr(gwd, "_compose_cmd", lambda: ["docker", "compose"])
    monkeypatch.setattr(gwd, "_installed_codes", lambda: [KOD])
    monkeypatch.setattr(
        gwd, "_container_info",
        lambda code: {"container": "e1-gw-gw-001", "state": "running",
                      "status": "Up 26 hours", "image": cozulmus, "ports": ""},
    )
    monkeypatch.setattr(gwd, "_local_digest", lambda image: D_ESKI)
    monkeypatch.setattr(gwd, "_local_version", lambda image: "1.5.0")
    # Uzak sorgu HANGI referansla yapiliyor: kaydet.
    sorulan: list[str] = []

    def _uzak_digest(image: str) -> str:
        sorulan.append(image)
        # Digest'e sabitli referans kendi digest'ini dondurur (eski hata).
        return D_ESKI if "@" in image else D_YENI

    monkeypatch.setattr(gwd, "_remote_digest", _uzak_digest)
    monkeypatch.setattr(gwd, "_remote_version", lambda image: "1.6.1")
    monkeypatch.setattr(gwd, "_buildx_var", lambda: True)
    monkeypatch.setattr(gwd, "_read_json", lambda path: {})

    state = gwd.build_state()
    gw = state["gateways"][0]

    # Uzak sorgu COMPOSE etiketiyle yapilmali; sabitlenmis referansla DEGIL.
    assert sorulan == [f"{IMAJ}:latest"], f"yanlis referansla soruldu: {sorulan}"
    assert gw["update_available"] is True, "yeni surum yayinlandi ama guncelleme gorunmuyor"
    assert gw["local_version"] == "1.5.0"
    assert gw["remote_version"] == "1.6.1"
    assert gw["tracked_image"] == f"{IMAJ}:latest"


def test_gercekten_guncelse_guncel_der(ajan, monkeypatch):
    """Ters yon: cihaz zaten en yeni imajdaysa "guncelleme var" DEMEZ."""
    _compose_yaz(ajan, f"{IMAJ}:latest")
    monkeypatch.setattr(gwd, "_compose_cmd", lambda: ["docker", "compose"])
    monkeypatch.setattr(gwd, "_installed_codes", lambda: [KOD])
    monkeypatch.setattr(
        gwd, "_container_info",
        lambda code: {"container": "e1-gw-gw-001", "state": "running",
                      "status": "Up 1 hour", "image": f"{IMAJ}:latest", "ports": ""},
    )
    monkeypatch.setattr(gwd, "_local_digest", lambda image: D_YENI)
    monkeypatch.setattr(gwd, "_remote_digest", lambda image: D_YENI)
    monkeypatch.setattr(gwd, "_local_version", lambda image: "1.6.1")
    monkeypatch.setattr(gwd, "_remote_version", lambda image: "1.6.1")
    monkeypatch.setattr(gwd, "_buildx_var", lambda: True)
    monkeypatch.setattr(gwd, "_read_json", lambda path: {})

    gw = gwd.build_state()["gateways"][0]
    assert gw["update_available"] is False


def test_kayit_defteri_okunamazsa_BILINMIYOR(ajan, monkeypatch):
    """Bos uzak digest -> None ("guncel" DEMEK degil)."""
    _compose_yaz(ajan, f"{IMAJ}:latest")
    monkeypatch.setattr(gwd, "_compose_cmd", lambda: ["docker", "compose"])
    monkeypatch.setattr(gwd, "_installed_codes", lambda: [KOD])
    monkeypatch.setattr(
        gwd, "_container_info",
        lambda code: {"container": "c", "state": "running", "status": "Up",
                      "image": f"{IMAJ}:latest", "ports": ""},
    )
    monkeypatch.setattr(gwd, "_local_digest", lambda image: D_ESKI)
    monkeypatch.setattr(gwd, "_remote_digest", lambda image: "")
    monkeypatch.setattr(gwd, "_local_version", lambda image: "1.5.0")
    monkeypatch.setattr(gwd, "_remote_version", lambda image: "")
    monkeypatch.setattr(gwd, "_buildx_var", lambda: False)
    monkeypatch.setattr(gwd, "_read_json", lambda path: {})

    state = gwd.build_state()
    assert state["gateways"][0]["update_available"] is None
    assert state["buildx_available"] is False
