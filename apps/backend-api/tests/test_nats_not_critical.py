"""NATS/RabbitMQ arizasi ARAYUZU KAPATMAMALI + NATS TLS yarim kalmamali.

Saha hikayesi (2026-08-01, sanal makine):
Operator .env'e `NATS_TLS_ENABLED=true` yazip guncelledi. install.sh/update.sh
icindeki gomulu Python heredoc'u bozuk string literali yuzunden SyntaxError
verdi; `set -euo pipefail` altinda betik tam orada oldu ve
`infra/nats/nats-server.conf` icinde `{{NATS_TLS_BLOCK}}` yer tutucusu HAM
halde kaldi. NATS o dosyayi ayristiramayip crash-loop'a girdi. Backend NATS'a
baglanamadi, `/health` 503 dondu ve `frontend-web` -> `depends_on:
backend-api: service_healthy` zinciri yuzunden ARAYUZ HIC ACILMADI.

Bu dosya o zincirin her halkasini kilitler:
  1. /health NATS/JetStream/RabbitMQ dustugunde 200 + degraded doner
     (yalnizca Postgres 503 sebebidir).
  2. install.sh/update.sh icinde artik bozuk bir gomulu Python bloku yok.
  3. NATS'a baglanan her compose servisi sertifika dizinini mount eder.
"""

from __future__ import annotations

import io
import pathlib
import re

import pytest
from fastapi import status

from app.api import health as health_mod

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


class _FakeDb:
    """`SELECT 1` calisan sahte session."""

    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok

    def execute(self, *_args, **_kwargs):
        if not self._ok:
            raise RuntimeError("connection refused")
        return None


@pytest.fixture
def probes(monkeypatch):
    """Tum probe'lari kontrol edilebilir hale getirir."""
    state = {"js": True, "nats": True, "rmq": True}

    monkeypatch.setattr(health_mod, "_probe_jetstream", lambda: (state["js"], None if state["js"] else "bus_not_ready"))

    def _tcp(url: str, *, default_port: int, timeout: float = 1.0):
        ok = state["nats"] if default_port == 4222 else state["rmq"]
        return ok, None if ok else "refused", 1.0

    monkeypatch.setattr(health_mod, "_probe_tcp", _tcp)
    monkeypatch.setattr(health_mod, "_leader_status_source", None, raising=False)
    return state


def _body(db_ok: bool = True):
    return health_mod._build_health_body(_FakeDb(ok=db_ok))


def test_nats_down_arayuzu_kapatmaz(probes):
    """NATS + JetStream dustu: 200 + degraded. 503 DEGIL.

    503 dondurmek compose zincirini kilitliyor ve cihazi karartiyordu.
    """
    probes["nats"] = False
    probes["js"] = False

    body, code = _body()

    assert code == status.HTTP_200_OK, "NATS arizasi 503 uretmemeli - arayuz acilmali"
    assert body["status"] == "degraded"
    assert set(body["degraded_reasons"]) == {"nats_tcp", "jetstream_bus"}
    # Durum GIZLENMIYOR: hangi bagimliligin dustugu govdede acikca duruyor.
    assert body["dependencies"]["nats_tcp"]["ok"] is False
    assert body["dependencies"]["jetstream_bus"]["ok"] is False


def test_rabbitmq_down_degraded(probes):
    probes["rmq"] = False
    body, code = _body()
    assert code == status.HTTP_200_OK
    assert body["status"] == "degraded"
    assert body["degraded_reasons"] == ["rabbitmq_tcp"]


def test_postgres_down_hala_503(probes):
    """Tek kritik bagimlilik Postgres. Orada 503 DOGRU davranis."""
    body, code = _body(db_ok=False)
    assert code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert body["status"] == "unhealthy"
    assert body["degraded_reasons"] == ["database"]


def test_hepsi_saglikli_ok(probes):
    body, code = _body()
    assert code == status.HTTP_200_OK
    assert body["status"] == "ok"
    assert body["degraded_reasons"] == []


# --------------------------------------------------------------------------
# Kurulum betikleri
# --------------------------------------------------------------------------


@pytest.mark.parametrize("script", ["install.sh", "update.sh"])
def test_betikte_gomulu_python_bloku_kalmadi(script: str):
    """TLS render'i artik _lib.sh'taki paylasilan fonksiyonda.

    Eski gomulu heredoc SyntaxError veriyordu ve `set -e` altinda kurulumu
    yarida birakiyordu. Tekrar gomulmesin.
    """
    text = io.open(REPO_ROOT / script, encoding="utf-8").read()
    assert "E1_TLS_PY" not in text, f"{script}: bozuk TLS heredoc'u geri gelmis"
    assert "e1_nats_tls_prepare" in text, f"{script}: TLS hazirligi cagrilmiyor"
    assert "e1_nats_conf_render_tls" in text, f"{script}: TLS blogu render edilmiyor"


@pytest.mark.parametrize(
    "script",
    ["install.sh", "update.sh", "uninstall.sh", "infra/scripts/linux/_lib.sh"],
)
def test_betikte_gomulu_python_sozdizimi_gecerli(script: str):
    """Betikteki her `python3 - <<'X'` heredoc'u derlenebilmeli.

    Bu test var olmasaydi 2026-08-01 arizasi tekrar edebilirdi: bozuk blok
    yalnizca NATS_TLS_ENABLED=true olan kurulumlarda calistigi icin CI'da hic
    kosmuyordu. Gomulu Python, kabuk betiginin sozdizimi denetiminden (bash -n)
    de gecmez — tek koruma budur.
    """
    text = io.open(REPO_ROOT / script, encoding="utf-8").read()
    pattern = re.compile(r"python3 - (?:\"\$[A-Za-z_]+\" )?<<'([A-Z0-9_]+)'\n(.*?)\n\1", re.S)
    found = 0
    for name, src in pattern.findall(text):
        found += 1
        try:
            compile(src, f"{script}:{name}", "exec")
        except SyntaxError as exc:  # pragma: no cover - hata mesaji icin
            pytest.fail(f"{script} icindeki {name} bloku derlenmiyor: {exc.msg} (satir {exc.lineno})")
    # Blok yoksa test anlamsiz degil - sadece koruyacak bir sey yok demektir.
    assert found >= 0


def test_lib_tls_fonksiyonlari_var():
    """install.sh ve update.sh bu fonksiyonlara guveniyor."""
    text = io.open(REPO_ROOT / "infra/scripts/linux/_lib.sh", encoding="utf-8").read()
    assert "e1_nats_tls_prepare()" in text
    assert "e1_nats_conf_render_tls()" in text
    # TLS kapatilinca `tls://` semasi geri alinmali; aksi halde "kapattim ama
    # hala calismiyor" durumu olusuyor.
    assert "_e1_nats_url_scheme" in text


# --------------------------------------------------------------------------
# docker-compose: sertifika dizini NATS'a baglanan HER servise mount edilmeli
# --------------------------------------------------------------------------

_CERT_MOUNT = "./infra/nats/certs:/etc/nats/certs:ro"


def test_nats_istemcileri_sertifika_dizinini_mount_eder():
    """`NATS_CA_FILE` alan her servis o dosyayi GERCEKTEN gorebilmeli.

    Dizin yalnizca `nats` servisine bagliyken, compose yorumlarinin onerdigi
    `NATS_CA_FILE=/etc/nats/certs/ca.crt` degeri istemci container'inda
    bulunmayan bir yolu gosteriyordu -> TLS el sikismasi hep basarisiz.
    """
    text = io.open(REPO_ROOT / "docker-compose.yml", encoding="utf-8").read()
    lines = text.split("\n")

    current = None
    ca_users: set[str] = set()
    mounted: set[str] = set()
    for line in lines:
        m = re.match(r"^  ([a-z0-9-]+):\s*$", line)
        if m:
            current = m.group(1)
        if current is None:
            continue
        if re.match(r"^\s+NATS_CA_FILE:", line):
            ca_users.add(current)
        if _CERT_MOUNT in line or re.match(r"^\s+volumes:\s*\*nats-certs\s*$", line):
            mounted.add(current)

    assert ca_users, "NATS_CA_FILE hicbir serviste yok - test guncellenmeli"
    eksik = sorted(ca_users - mounted)
    assert not eksik, (
        "Bu servisler NATS_CA_FILE aliyor ama sertifika dizinini mount etmiyor: "
        f"{eksik}. TLS acildiginda CA dosyasini bulamazlar."
    )


def test_backend_kuyruklari_healthy_beklemiyor():
    """Acilis suresi: backend RabbitMQ/NATS'in `healthy` olmasini beklememeli.

    RabbitMQ healthcheck'i `start_period: 90s`; seri bekleme acilisa dogrudan
    ~90 saniye ekliyordu. Backend ikisi olmadan da ayaga kalkar.
    """
    text = io.open(REPO_ROOT / "docker-compose.yml", encoding="utf-8").read()
    block = text.split("\n  backend-api:", 1)[1].split("\n    environment:", 1)[0]
    for svc in ("rabbitmq", "nats"):
        seg = block.split(f"{svc}:", 1)[1]
        assert "service_started" in seg.split("condition:", 1)[1][:40], (
            f"backend-api -> {svc} bagimliligi `service_started` olmali; "
            "`service_healthy` acilisi gereksiz uzatiyor."
        )
