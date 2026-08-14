"""Gateway kaydi silinince host'taki container da kaldirilmali.

SAHADA GORULEN (2026-08-13)
---------------------------
Bir test kurulumunda DORT gateway container'i calisiyordu ama veritabaninda
YALNIZCA IKI kayit vardi. Operator arayuzden iki gateway silmisti; DB satiri
gitti, container'lar kaldi. Yetimler backend'e sormaya devam ediyordu —
biri 696 kez ust uste 404 aldi.

BUNLAR ZARARSIZ DEGIL
---------------------
Kaldirilan container'in state VOLUME'u geride kaliyor ve icindeki config
onbellegi CIHAZ IP'LERINI tasiyor. Yetim container o adreslere baglanmayi
denerse — Horstmann outstation `CloseExisting` modunda oldugu icin — her
yeni baglanti CALISAN oturumu dusurur. Belirti tam olarak sahada sikayet
edilen sey olur: "haberlesme gidip geliyor".

Bu dosya iki seyi kilitler:
  * kayit silinince bu makinedeki container icin kaldirma istegi yazilir
    ve istek `purge` (volume de silinsin) tasir,
  * kaldirilamazsa operator OLAY KAYDINDAN ogrenir — sessiz kalinmaz.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.api import gateways as gateways_api
from app.services import gateway_agent_service
from app.services.gateway_agent_service import GatewayAgentError


# ---------------------------------------------------------------- servis katmani


def test_request_remove_purge_bayragini_isteğe_yazar(monkeypatch: pytest.MonkeyPatch) -> None:
    yazilan: dict = {}

    def sahte_yaz(body: dict) -> str:
        yazilan.update(body)
        return "req-1"

    monkeypatch.setattr(gateway_agent_service, "_write_request", sahte_yaz)

    gateway_agent_service.request_remove("GW-009", "kurulumcu", purge=True)
    assert yazilan["action"] == "remove"
    assert yazilan["code"] == "GW-009"
    assert yazilan["purge"] is True


def test_request_remove_varsayilani_purge_DEGIL(monkeypatch: pytest.MonkeyPatch) -> None:
    """"Yalnizca bu makineden kaldir" akisinda volume KORUNMALI.

    Orada gonderilmemis telemetri (outbox) ve komut defteri olabilir; kayit
    duruyorsa veriyi silmek veri kaybidir.
    """
    yazilan: dict = {}
    monkeypatch.setattr(
        gateway_agent_service, "_write_request", lambda b: (yazilan.update(b), "req-2")[1]
    )

    gateway_agent_service.request_remove("GW-009", "kurulumcu")
    assert yazilan["purge"] is False


# ---------------------------------------------------------------- arka plan gorevi


def test_uzak_gateway_icin_istek_YAZILMAZ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bu makinede kurulu olmayan gateway'e dokunulmamali."""
    cagrildi: list = []
    monkeypatch.setattr(gateway_agent_service, "is_installed_locally", lambda code: False)
    monkeypatch.setattr(
        gateway_agent_service,
        "request_remove",
        lambda *a, **k: cagrildi.append(a) or "req",
    )

    gateways_api._remove_local_container("GW-UZAK", "Uzak GW", "kurulumcu")
    assert cagrildi == [], "uzak gateway icin ajana istek gonderildi"


def test_yerel_gateway_purge_ile_kaldirilir(monkeypatch: pytest.MonkeyPatch) -> None:
    kayit: dict = {}
    monkeypatch.setattr(gateway_agent_service, "is_installed_locally", lambda code: True)

    def sahte_remove(code, actor, *, purge=False):
        kayit.update({"code": code, "actor": actor, "purge": purge})
        return "req-3"

    monkeypatch.setattr(gateway_agent_service, "request_remove", sahte_remove)
    monkeypatch.setattr(gateways_api, "record_event", lambda *a, **k: None)

    gateways_api._remove_local_container("GW-003", "Eski GW", "kurulumcu")

    assert kayit["code"] == "GW-003"
    assert kayit["purge"] is True, (
        "kayit silindiginde volume de gitmeli; yoksa cihaz IP'leri onbellekte kalir"
    )


def test_ajan_hatasi_SESSIZ_kalmaz(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kaldirma basarisizsa operator olay kaydindan ogrenmeli.

    Sessiz kalinsaydi yetim container aylar sonra "haberlesme gidip geliyor"
    olarak geri donerdi ve kimse sebebini bilemezdi.
    """
    olaylar: list[dict] = []
    monkeypatch.setattr(gateway_agent_service, "is_installed_locally", lambda code: True)

    def patla(*a, **k):
        raise GatewayAgentError("request_pending")

    monkeypatch.setattr(gateway_agent_service, "request_remove", patla)
    monkeypatch.setattr(
        gateways_api, "record_event", lambda db, **kw: olaylar.append(kw)
    )

    # Istisna DISARI SIZMAMALI: silme zaten commit edildi.
    gateways_api._remove_local_container("GW-004", "Yetim GW", "kurulumcu")

    assert olaylar, "hata olay kaydina yazilmadi"
    olay = olaylar[-1]
    assert olay["event_type"] == "gateway_local_remove_failed"
    assert olay["severity"] == "warning"
    assert "request_pending" in olay["message"]


# ---------------------------------------------------------------- host ajani


_AGENT_PATH = Path(__file__).resolve().parents[3] / "infra" / "appliance" / "e1-gwd.py"


@pytest.fixture(scope="module")
def ajan():
    spec = importlib.util.spec_from_file_location("e1_gwd_yetim", _AGENT_PATH)
    assert spec and spec.loader, f"e1-gwd.py yuklenemedi: {_AGENT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ajan_purge_alanini_dogrular(ajan) -> None:
    temiz = ajan._validate({"action": "remove", "code": "GW-003", "name": "x", "purge": True})
    assert temiz["purge"] is True

    temiz = ajan._validate({"action": "remove", "code": "GW-003", "name": "x"})
    assert temiz["purge"] is False, "purge gonderilmezse ESKI davranis (volume korunur)"


def test_ajan_purge_ile_volume_siler(ajan, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """`docker compose down` volume'u BIRAKIR; purge'de `--volumes` eklenmeli."""
    komutlar: list[list[str]] = []

    monkeypatch.setattr(ajan, "GATEWAY_ROOT", str(tmp_path))
    hedef = tmp_path / "GW-003"
    hedef.mkdir()
    (hedef / "compose.yml").write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(ajan, "_compose_path", lambda code: str(hedef / "compose.yml"))
    monkeypatch.setattr(ajan, "_project_name", lambda code: f"e1-gw-{code.lower()}")
    monkeypatch.setattr(ajan, "_run", lambda cmd, timeout: (komutlar.append(cmd), (0, ""))[1])

    sonuc = ajan._do_remove({"code": "GW-003", "purge": True}, ["docker", "compose"])
    assert sonuc["ok"] is True
    assert "--volumes" in komutlar[0], f"purge'de volume silinmiyor: {komutlar[0]}"


def test_ajan_purgesiz_volume_KORUR(ajan, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    komutlar: list[list[str]] = []

    monkeypatch.setattr(ajan, "GATEWAY_ROOT", str(tmp_path))
    hedef = tmp_path / "GW-005"
    hedef.mkdir()
    (hedef / "compose.yml").write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(ajan, "_compose_path", lambda code: str(hedef / "compose.yml"))
    monkeypatch.setattr(ajan, "_project_name", lambda code: f"e1-gw-{code.lower()}")
    monkeypatch.setattr(ajan, "_run", lambda cmd, timeout: (komutlar.append(cmd), (0, ""))[1])

    ajan._do_remove({"code": "GW-005"}, ["docker", "compose"])
    assert "--volumes" not in komutlar[0], (
        "kayit dururken volume silinirse gonderilmemis telemetri kaybolur"
    )
