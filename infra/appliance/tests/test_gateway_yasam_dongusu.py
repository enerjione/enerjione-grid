"""Gateway'i konsoldan DURDUR / BASLAT / YENIDEN BASLAT — ajan tarafi.

NEDEN BU TESTLER
----------------
Ajan cihazda ROOT kosuyor ve backend'in yazdigi dosyayi okuyup docker
calistiriyor. Yanlis giden her sey ya (a) sahada veri akisini keser ya da
(b) root'ta keyfi komut demektir. Bu dosya iki seyi kilitler:

  1) DAVRANIS: "durdur" container'i SILMEZ (`stop`, `down` degil). Silinirse
     durum `absent` olur ve "operator durdurdu" ile "hic kurulu degil" ayirt
     edilemez hale gelir — arayuzun durduruldugunu soyleyebilmesi tam olarak
     bu ayrima dayaniyor.
  2) SINIR: eylem kumesi SABIT, hedef container adi ISTEKTEN DEGIL koddan
     turetilip dogrulaniyor, istekteki fazla alanlar docker komutuna
     tasinmiyor.

Testler docker CALISTIRMAZ: `_run` ve `_container_info` sahte. Olculen sey
ajanin kurdugu KOMUT ve verdigi karar.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

_AJAN_YOL = Path(__file__).resolve().parents[1] / "e1-gwd.py"
_spec = importlib.util.spec_from_file_location("e1_gwd_yd", _AJAN_YOL)
assert _spec is not None and _spec.loader is not None
gwd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gwd)

KOD = "GW-7"


class SahteAjan:
    """Calistirilan komutlari ve bildirilen asamalari SIRAYLA toplar."""

    def __init__(self, kok: Path, durum: Path):
        self.kok = kok
        self.durum = durum
        self.izler: list[str] = []
        self.komutlar: list[list[str]] = []
        self.container = {
            "container": "e1-gw-gw-7",
            "state": "running",
            "status": "Up 3 minutes",
            "image": "ghcr.io/enerjione/dnp3-gateway:latest",
            "ports": "",
        }

    def istek_yaz(self, govde: dict) -> None:
        (self.durum / "request.json").write_text(
            json.dumps(govde), encoding="utf-8"
        )

    def sonuc(self) -> dict:
        return json.loads((self.durum / "status.json").read_text(encoding="utf-8"))

    @property
    def docker_komutlari(self) -> list[str]:
        """Her komutun SON argumani (compose alt komutu)."""
        return [k[-1] for k in self.komutlar]


@pytest.fixture
def ajan(tmp_path, monkeypatch) -> SahteAjan:
    kok = tmp_path / "gateways"
    durum = tmp_path / "state"
    kok.mkdir()
    durum.mkdir()
    # Kurulu bir gateway: compose dosyasi var.
    (kok / KOD).mkdir()
    (kok / KOD / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(gwd, "GATEWAY_ROOT", str(kok))
    monkeypatch.setattr(gwd, "STATE_DIR", str(durum))
    monkeypatch.setattr(gwd, "REQUEST_PATH", str(durum / "request.json"))
    monkeypatch.setattr(gwd, "STATE_PATH", str(durum / "state.json"))
    monkeypatch.setattr(gwd, "STATUS_PATH", str(durum / "status.json"))
    monkeypatch.setattr(gwd, "ARCHIVE_DIR", str(durum / "archive"))

    sahte = SahteAjan(kok, durum)

    def _sahte_run(cmd: list[str], timeout: float) -> tuple[int, str]:
        sahte.komutlar.append(list(cmd))
        sahte.izler.append(f"run:{cmd[-1]}")
        return 0, ""

    def _sahte_write_status(payload: dict) -> None:
        sahte.izler.append(f"status:{payload.get('stage')}")
        gwd._write_json(str(durum / "status.json"), {**payload, "at": "test"})

    monkeypatch.setattr(gwd, "_run", _sahte_run)
    monkeypatch.setattr(gwd, "_write_status", _sahte_write_status)
    monkeypatch.setattr(gwd, "_compose_cmd", lambda: ["docker", "compose"])
    monkeypatch.setattr(gwd, "_container_info", lambda code: dict(sahte.container))
    # build_state kayit defterine gitmesin.
    monkeypatch.setattr(gwd, "_local_digest", lambda image: "")
    monkeypatch.setattr(gwd, "_remote_digest", lambda image: "")
    monkeypatch.setattr(gwd, "_local_version", lambda image: "")
    monkeypatch.setattr(gwd, "_remote_version", lambda image: "")
    return sahte


def _istek(action: str, **ek) -> dict:
    return {"id": "abc123", "action": action, "code": KOD,
            "requested_by": "installer", **ek}


# ---------------------------------------------------------------------------
# Eylem kumesi SABIT
# ---------------------------------------------------------------------------

def test_yasam_dongusu_eylemleri_taniniyor():
    for eylem in ("start", "stop", "restart"):
        assert eylem in gwd.ALLOWED_ACTIONS


def test_kume_DISINDAKI_aksiyon_reddediliyor():
    """Ajan bir komut METNI degil, sabit kumeden bir AD kabul eder."""
    for kotu in ("exec", "sh", "stop; rm -rf /", "down", "", "STOP"):
        with pytest.raises(ValueError):
            gwd._validate({"action": kotu, "code": KOD})


def test_gecersiz_kod_reddediliyor():
    """Kod compose yolu ve proje adi olarak kullaniliyor."""
    for kotu in ("../../etc", "gw 7", "gw;rm", "-flag"):
        with pytest.raises(ValueError):
            gwd._validate({"action": "stop", "code": kotu})


def test_istekteki_FAZLA_alanlar_tasinmiyor():
    """Dogrulama bir SUZGEC degil, yeniden insa: bilinmeyen alan gecemez."""
    temiz = gwd._validate(
        {
            "action": "stop",
            "code": KOD,
            "compose": "services:\n  x:\n    privileged: true\n",
            "cmd": "rm -rf /",
            "args": ["--privileged"],
        }
    )
    assert set(temiz) <= {"action", "code", "name", "params"}
    assert "compose" not in temiz and "cmd" not in temiz and "args" not in temiz


# ---------------------------------------------------------------------------
# DURDUR: `stop` — `down` DEGIL
# ---------------------------------------------------------------------------

def test_durdurma_container_i_SILMIYOR(ajan):
    """`down` container'i siler; ardindan durum `absent` olur ve arayuz
    'durduruldu' ile 'kurulu degil'i ayirt edemez."""
    sonuc = gwd._do_stop(_istek("stop"), ["docker", "compose"])

    assert sonuc["ok"] is True
    assert ajan.docker_komutlari == ["stop"]
    tum = [arg for komut in ajan.komutlar for arg in komut]
    assert "down" not in tum, "durdurma container'i siliyor"
    assert "rm" not in tum


def test_baslatma_IMAJ_CEKMIYOR(ajan):
    """`start` mevcut imajla baslatir; surum degistirmek `update`in isi."""
    ajan.container["state"] = "exited"
    sonuc = gwd._do_start(_istek("start"), ["docker", "compose"])

    assert sonuc["ok"] is True
    assert ajan.docker_komutlari == ["start"]
    assert "pull" not in [arg for komut in ajan.komutlar for arg in komut]


def test_yeniden_baslatma_tek_komut(ajan):
    sonuc = gwd._do_restart(_istek("restart"), ["docker", "compose"])
    assert sonuc["ok"] is True
    assert ajan.docker_komutlari == ["restart"]


# ---------------------------------------------------------------------------
# HEDEF DOGRULAMA
# ---------------------------------------------------------------------------

def test_hedef_container_ADI_dogrulaniyor(ajan):
    """docker baska adda bir container bildirirse islem YAPILMAZ.

    Ajan root kosuyor; "hangi container" sorusunun cevabi her zaman kendi
    hesabi olmali, docker'in ya da istegin sundugu bir ad degil.
    """
    ajan.container["container"] = "musterinin-veritabani"
    sonuc = gwd._do_stop(_istek("stop"), ["docker", "compose"])

    assert sonuc["ok"] is False
    assert ajan.komutlar == [], "yanlis container'a docker komutu calisti"


def test_container_yoksa_islem_yapilmiyor(ajan):
    ajan.container = {"state": "absent"}
    for fn in (gwd._do_stop, gwd._do_start, gwd._do_restart):
        sonuc = fn(_istek("stop"), ["docker", "compose"])
        assert sonuc["ok"] is False
    assert ajan.komutlar == []


def test_kurulu_DEGILSE_islem_yapilmiyor(ajan):
    """compose dosyasi yoksa bu gateway bu cihaza ait degil."""
    os.remove(ajan.kok / KOD / "docker-compose.yml")
    sonuc = gwd._do_stop(_istek("stop"), ["docker", "compose"])
    assert sonuc["ok"] is False
    assert "kurulu degil" in sonuc["message"]
    assert ajan.komutlar == []


def test_hedef_container_adi_KODDAN_turetiliyor():
    """Istek bir container adi tasisa bile okunmaz."""
    assert gwd._hedef_container("GW-7") == "e1-gw-gw-7"
    assert gwd._hedef_container("gw-7") == "e1-gw-gw-7"


# ---------------------------------------------------------------------------
# ILERLEME: docker cagrisindan ONCE asama bildirilmeli
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fn,asama",
    [("_do_stop", "stop"), ("_do_start", "start"), ("_do_restart", "restart")],
)
def test_asama_docker_CAGRISINDAN_ONCE_bildiriliyor(ajan, fn, asama):
    """Arayuz islem boyunca sessiz kalmasin: operator bastigini goremezse
    tekrar basar, ikinci istek reddedilir."""
    ajan.container["state"] = "exited" if asama == "start" else "running"
    getattr(gwd, fn)(_istek(asama), ["docker", "compose"])

    assert ajan.izler.index(f"status:{asama}") < ajan.izler.index(f"run:{asama}")
    kayit = ajan.sonuc()
    assert kayit["running"] is True, "asama bildirimi 'suruyor' demiyor"


# ---------------------------------------------------------------------------
# UCTAN UCA: request.json -> cmd_apply -> status.json + arsiv
# ---------------------------------------------------------------------------

def test_uctan_uca_durdurma(ajan):
    ajan.istek_yaz(_istek("stop"))
    rc = gwd.cmd_apply()

    assert rc == 0
    kayit = ajan.sonuc()
    assert kayit["ok"] is True
    assert kayit["action"] == "stop"
    assert kayit["code"] == KOD
    assert kayit["running"] is False, "arayuz islemi bitmis sayamaz"
    # Islenmis istek SILINIR; aksi halde path unit ayni istegi tekrar tetikler.
    assert not (ajan.durum / "request.json").exists()


@pytest.mark.skipif(
    not hasattr(os, "O_DIRECTORY"),
    reason="arsiv dizini O_NOFOLLOW|O_DIRECTORY ile aciliyor — yalnizca POSIX",
)
def test_durdurma_istegi_ARSIVLENIYOR(ajan):
    """Denetim izi: hangi istek ne zaman uygulandi, dosyada kalsin."""
    ajan.istek_yaz(_istek("stop"))
    gwd.cmd_apply()
    arsiv = list((ajan.durum / "archive").glob("*.json"))
    assert arsiv, "islenmis istek arsivlenmedi"
    kayit = json.loads(arsiv[0].read_text(encoding="utf-8"))
    assert kayit["request"]["action"] == "stop"
    assert kayit["result"]["ok"] is True


def test_uctan_uca_bilinmeyen_aksiyon_docker_CALISTIRMIYOR(ajan):
    ajan.istek_yaz(_istek("kill-everything"))
    rc = gwd.cmd_apply()

    assert rc == 1
    assert ajan.komutlar == []
    assert ajan.sonuc()["ok"] is False
