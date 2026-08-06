"""Uzaktan log — ajan tarafi (`logs` eylemi).

NEDEN BU TESTLER
----------------
Backend Docker daemon'a BILEREK erisemez; sahadaki "gateway ne diyor"
sorusu ancak bu ajan koprüsüyle cevaplanabiliyor. Kopru root'ta docker
calistirdigi icin iki sey kilitlenmeli:

  1) SINIR: `tail` docker'a ARGUMAN olarak gidiyor. Metin ya da sinirsiz
     sayi gecerse (a) komut enjeksiyonu, (b) diski dolduran dev bir cikti
     riski dogar. Dogrulama tam sayiya cevirip kelepceler.
  2) DAVRANIS: cikti backend'in OKUYABILECEGI dosyaya yazilir; container
     DURMUS olsa bile log alinabilir — zaten en cok o durumda lazim.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_AJAN_YOL = Path(__file__).resolve().parents[1] / "e1-gwd.py"
_spec = importlib.util.spec_from_file_location("e1_gwd_log", _AJAN_YOL)
assert _spec is not None and _spec.loader is not None
gwd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gwd)

KOD = "GW-7"


@pytest.fixture
def ajan(tmp_path, monkeypatch):
    kok = tmp_path / "gateways"
    durum = tmp_path / "state"
    kok.mkdir()
    durum.mkdir()
    (kok / KOD).mkdir()
    (kok / KOD / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(gwd, "GATEWAY_ROOT", str(kok))
    monkeypatch.setattr(gwd, "STATE_DIR", str(durum))
    monkeypatch.setattr(gwd, "STATUS_PATH", str(durum / "status.json"))

    kayit: dict = {"komutlar": [], "cikti": "2026-08-06T10:00:00Z gw | hazir\n"}

    def _sahte_run(cmd: list[str], timeout: float) -> tuple[int, str]:
        kayit["komutlar"].append(list(cmd))
        return kayit.get("rc", 0), kayit["cikti"]

    monkeypatch.setattr(gwd, "_run", _sahte_run)
    monkeypatch.setattr(gwd, "_write_status", lambda payload: None)
    monkeypatch.setattr(
        gwd,
        "_container_info",
        lambda code: {"container": "e1-gw-gw-7", "state": "running", "status": "Up"},
    )
    kayit["durum"] = durum
    return kayit


def _istek(**ek) -> dict:
    return {"id": "abc123", "action": "logs", "code": KOD, "requested_by": "installer", **ek}


# --- Dogrulama --------------------------------------------------------------


def test_logs_eylemi_taniniyor():
    assert "logs" in gwd.ALLOWED_ACTIONS


def test_tail_sinirlara_kelepcelenir():
    assert gwd._validate(_istek(tail=5))["tail"] == gwd.LOGS_TAIL_MIN
    assert gwd._validate(_istek(tail=999_999))["tail"] == gwd.LOGS_TAIL_MAX
    assert gwd._validate(_istek(tail=300))["tail"] == 300
    # tail verilmezse varsayilan.
    assert gwd._validate(_istek())["tail"] == gwd.LOGS_TAIL_DEFAULT


@pytest.mark.parametrize("kotu", ["500; rm -rf /", "--follow", "", None, [300], {"a": 1}])
def test_tail_METIN_kabul_etmez(kotu):
    with pytest.raises(ValueError):
        gwd._validate(_istek(tail=kotu))


def test_gecersiz_kod_reddediliyor():
    for kotu in ("../../etc", "gw 7", "gw;rm"):
        with pytest.raises(ValueError):
            gwd._validate({"action": "logs", "code": kotu})


# --- Davranis ---------------------------------------------------------------


def test_log_dosyaya_yazilir_ve_komut_dogru(ajan):
    sonuc = gwd._do_logs(_istek(tail=250), ["docker", "compose"])
    assert sonuc["ok"] is True

    komut = ajan["komutlar"][0]
    assert komut[:2] == ["docker", "compose"]
    assert "logs" in komut
    # tail SAYI olarak, kendi bayraginin hemen ardindan.
    assert komut[komut.index("--tail") + 1] == "250"
    # `-f` compose DOSYA bayragi (meşru); akis (`--follow`) YOK — istek
    # sonlanmali, ajan bir sonraki istegi bekliyor.
    assert "--follow" not in komut
    assert komut[komut.index("-f") + 1].endswith("docker-compose.yml")

    yazilan = json.loads((ajan["durum"] / f"logs-{KOD}.json").read_text(encoding="utf-8"))
    assert yazilan["code"] == KOD
    assert yazilan["tail"] == 250
    assert yazilan["truncated"] is False
    assert "hazir" in yazilan["output"]
    assert yazilan["generated_at"]


def test_uzun_cikti_BASTAN_kirpilir(ajan):
    # Yeni satirlar SONDA; kirpma bastan olmali ki en guncel kisim kalsin.
    ajan["cikti"] = ("eski satir\n" * 50_000) + "EN_YENI_SATIR\n"
    gwd._do_logs(_istek(), ["docker", "compose"])
    yazilan = json.loads((ajan["durum"] / f"logs-{KOD}.json").read_text(encoding="utf-8"))
    assert yazilan["truncated"] is True
    assert len(yazilan["output"]) <= gwd.LOGS_MAX_CHARS
    assert yazilan["output"].rstrip().endswith("EN_YENI_SATIR")


def test_DURMUS_container_icin_de_log_alinir(ajan, monkeypatch):
    monkeypatch.setattr(
        gwd,
        "_container_info",
        lambda code: {"container": "e1-gw-gw-7", "state": "exited", "status": "Exited (0)"},
    )
    sonuc = gwd._do_logs(_istek(), ["docker", "compose"])
    assert sonuc["ok"] is True
    assert (ajan["durum"] / f"logs-{KOD}.json").exists()


def test_kurulu_OLMAYAN_gateway_reddedilir(ajan, monkeypatch):
    monkeypatch.setattr(gwd, "GATEWAY_ROOT", str(ajan["durum"] / "yok"))
    sonuc = gwd._do_logs(_istek(), ["docker", "compose"])
    assert sonuc["ok"] is False
    assert "kurulu degil" in sonuc["message"]


def test_docker_hatasi_dosya_YAZMAZ(ajan):
    ajan["rc"] = 1
    sonuc = gwd._do_logs(_istek(), ["docker", "compose"])
    assert sonuc["ok"] is False
    assert not (ajan["durum"] / f"logs-{KOD}.json").exists()
