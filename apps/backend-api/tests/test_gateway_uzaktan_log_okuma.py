"""Uzaktan log okuma — backend tarafi.

Log ciktisi arayuze GIDIYOR. Gateway container'i baslangicta yapilandirma
ozeti basarsa token/parola oraya duser; maskeleme olmadan bu, denetim
kaydina bile girmeden ekrana yansiyan bir sir sizintisidir. Bu dosya
maskelemeyi ve tazelik (stale) isaretini kilitler.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from app.services import gateway_agent_service as gas


@pytest.fixture
def durum_dizini(tmp_path, monkeypatch):
    monkeypatch.setattr(gas, "state_dir", lambda: tmp_path)
    return tmp_path


def _log_yaz(dizin, output: str, **ek):
    govde = {"code": "GW-7", "tail": 300, "truncated": False,
             "generated_at": "2026-08-06T10:00:00+00:00", "output": output, **ek}
    (dizin / "logs-GW-7.json").write_text(json.dumps(govde), encoding="utf-8")


def test_log_yoksa_None(durum_dizini):
    assert gas.read_logs("GW-7") is None


def test_okunur_ve_alanlar_dolu(durum_dizini):
    _log_yaz(durum_dizini, "gateway hazir\n")
    data = gas.read_logs("GW-7")
    assert data is not None
    assert data["code"] == "GW-7"
    assert data["tail"] == 300
    assert "gateway hazir" in data["output"]
    assert data["stale"] is False
    assert data["age_seconds"] is not None


@pytest.mark.parametrize(
    "ham,gorunmemeli",
    [
        ('GATEWAY_TOKEN="s3cr3t-abc123xyz"', "s3cr3t-abc123xyz"),
        ("token: bearerlike-9f8e7d6c5b", "bearerlike-9f8e7d6c5b"),
        ("password=HunterTwo42", "HunterTwo42"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc", "eyJhbGciOiJIUzI1NiJ9.abc"),
        ("nats://kullanici:GizliParola@nats:4222", "GizliParola"),
    ],
)
def test_sirlar_maskelenir(durum_dizini, ham, gorunmemeli):
    _log_yaz(durum_dizini, f"2026-08-06 gw | {ham}\n")
    data = gas.read_logs("GW-7")
    assert data is not None
    assert gorunmemeli not in data["output"], "sir maskelenmeden arayuze gidiyor"
    assert "***" in data["output"]


def test_normal_metin_bozulmaz(durum_dizini):
    metin = "2026-08-06 gw | 12 cihaz baglandi, DNP3 outstation 20001 dinleniyor\n"
    _log_yaz(durum_dizini, metin)
    data = gas.read_logs("GW-7")
    assert data is not None
    assert "12 cihaz baglandi" in data["output"]
    assert "20001" in data["output"]


def test_eski_cikti_bayat_isaretlenir(durum_dizini):
    _log_yaz(durum_dizini, "eski\n")
    yol = durum_dizini / "logs-GW-7.json"
    eski = time.time() - (gas.LOGS_STALE_SECONDS + 60)
    os.utime(yol, (eski, eski))
    data = gas.read_logs("GW-7")
    assert data is not None
    assert data["stale"] is True


def test_bozuk_dosya_patlatmaz(durum_dizini):
    (durum_dizini / "logs-GW-7.json").write_text("{bozuk", encoding="utf-8")
    assert gas.read_logs("GW-7") is None


def test_tail_sinirlanir(durum_dizini, monkeypatch):
    yazilan: dict = {}

    def _sahte_yaz(body: dict) -> str:
        yazilan.update(body)
        return body["id"]

    monkeypatch.setattr(gas, "_write_request", _sahte_yaz)
    gas.request_logs("GW-7", "installer", tail=999_999)
    assert yazilan["tail"] == gas.LOGS_TAIL_MAX
    gas.request_logs("GW-7", "installer", tail=1)
    assert yazilan["tail"] == gas.LOGS_TAIL_MIN
    assert yazilan["action"] == "logs"
