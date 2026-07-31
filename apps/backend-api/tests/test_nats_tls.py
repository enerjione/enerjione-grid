"""NATS TLS — varsayilan KAPALI, acildiginda HICBIR baglanti disarida kalmamali.

NEDEN TLS
---------
NATS istemci portu (4222) tum arayuzlere aciktir ve gateway'ler ona
`nats://gateway:<parola>@<host>:4222` ile baglanir. TLS olmadan HEM gateway
parolasi HEM tum telemetri DUZ METIN gider. Parolayi yakalayan biri sahte
telemetri enjekte edebilir: uydurma kritik ariza uretmek ya da
`fault_indicator`i normal gonderip GERCEK arizayi maskelemek.

NEDEN VARSAYILAN KAPALI
-----------------------
TLS acildiginda TUM istemciler (backend'in 3 baglantisi, 4 worker, gateway'ler)
CA'yi tanimak zorunda. Biri eksik kalirsa telemetri akisi DURUR. Bu yuzden
acilis iki bilincli adimdir: once sertifika uret, sonra bayragi ac.

EN KRITIK TEST
--------------
`test_tum_baglanti_noktalari_TLS_e_bagli`: bayragin guvenle acilabilmesi,
hicbir `nats.connect` cagrisinin disarida kalmamasina bagli. Biri yeni bir
baglanti eklerse ve TLS'i baglamayi unutursa, bayrak acildigi anda o servis
sessizce baglanamaz.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.nats_tls import nats_tls_context

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _ayari_geri_al():
    onceki = settings.nats_ca_file
    import app.core.nats_tls as m

    m._cached = None
    m._cached_for = None
    yield
    settings.nats_ca_file = onceki
    m._cached = None
    m._cached_for = None


# ------------------------------------------------------------- varsayilan


def test_varsayilan_TLS_kapali():
    """Alan bos = bugunku davranis. Aksi her kurulumu kirardi."""
    assert settings.__class__.model_fields["nats_ca_file"].default == ""


def test_ca_dosyasi_yoksa_None_doner():
    settings.nats_ca_file = ""
    assert nats_tls_context() is None


def test_bosluk_da_KAPALI_sayilir():
    settings.nats_ca_file = "   "
    assert nats_tls_context() is None


# ------------------------------------------------------------ acik durum


def test_gecerli_CA_ile_baglam_kurulur(tmp_path):
    """Gercek bir CA sertifikasi ile SSL baglami olusmali."""
    import ssl
    import subprocess

    ca = tmp_path / "ca.crt"
    key = tmp_path / "ca.key"
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
                "-nodes", "-days", "1",
                "-keyout", str(key), "-out", str(ca),
                "-subj", "/CN=test",
            ],
            check=True, capture_output=True, timeout=60,
            env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"},
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("openssl yok")

    settings.nats_ca_file = str(ca)
    ctx = nats_tls_context()
    assert isinstance(ctx, ssl.SSLContext)
    # Sunucu adi dogrulamasi ACIK kalmali: kapatmak, dogru CA'dan alinmis ama
    # BASKA makineye ait bir sertifikayi kabul etmek olurdu.
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_okunamayan_CA_SESSIZCE_duz_metne_DUSMEZ(tmp_path):
    """En onemli guvenlik davranisi.

    Dosya yoksa/bozuksa TLS'siz devam etmek, operatorun "TLS acik" sandigi bir
    kurulumda parolayi acikta gondermek olurdu. Hata YUKSELMELI.
    """
    settings.nats_ca_file = str(tmp_path / "yok.crt")
    with pytest.raises((OSError, Exception)):
        nats_tls_context()


# --------------------------------------------- TUM baglanti noktalari bagli


def test_tum_baglanti_noktalari_TLS_e_bagli():
    """Bayragin guvenle acilabilmesi buna bagli.

    Bir `nats.connect` cagrisi TLS baglami almazsa, TLS acildigi anda o servis
    sessizce baglanamaz ve telemetri/alarm akisi durur.
    """
    hedefler = [
        "apps/backend-api/app/services/jetstream_bus.py",
        "apps/backend-api/app/services/telemetry_consumer.py",
        "apps/backend-api/app/services/ws_broadcaster.py",
        "apps/tag-engine/tag_engine/main.py",
        "apps/alarm-service/alarm_service/main.py",
        "apps/iec104-outbound/iec104_outbound/consumer.py",
        "apps/modbus-outbound/modbus_outbound/consumer.py",
    ]
    eksikler = []
    for rel in hedefler:
        yol = REPO / rel
        if not yol.is_file():
            eksikler.append(f"{rel} (dosya yok)")
            continue
        kaynak = yol.read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Call):
                continue
            fn = dugum.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "connect"):
                continue
            # `nats.connect(...)` / `_nats.connect(...)`
            kok = fn.value
            if not (isinstance(kok, ast.Name) and kok.id in ("nats", "_nats")):
                continue
            anahtarlar = {kw.arg for kw in dugum.keywords}
            if "tls" not in anahtarlar:
                eksikler.append(f"{rel}:{dugum.lineno}")

    assert not eksikler, (
        "TLS baglami gecirmeyen nats.connect cagrilari:\n  "
        + "\n  ".join(eksikler)
        + "\n\nTLS acildiginda bu baglantilar SESSIZCE kurulamaz."
    )


def test_sunucu_sablonunda_TLS_isareti_var():
    """Kurulum betigi bu isareti gercek bir `tls { ... }` blogu ile degistirir."""
    sablon = (REPO / "infra" / "nats" / "nats-server.conf.template").read_text(
        encoding="utf-8"
    )
    assert "{{NATS_TLS_BLOCK}}" in sablon


def test_sertifikalar_GITIGNORE_da():
    """`ca.key` repoya girerse o CA'ya guvenen HER kurulum icin sahte sunucu
    sertifikasi uretilebilirdi — TLS tamamen anlamsizlasirdi."""
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "infra/nats/certs/" in ignore
