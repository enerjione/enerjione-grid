"""Gercek PostgreSQL gerektiren testlerin ortak kurulumu.

KABUL ORTAMI: PostgreSQL 16 + TimescaleDB 2.17.2 — yani uretimde kullanilan
imajin (`timescale/timescaledb:2.17.2-pg16`) BIREBIR ayni surumu. Safe
restore'un kabulu bu ortamda verilir; baska surumler yalnizca ek uyumluluk
bilgisidir.

`E1_TEST_PG_URL` tanimli degilse bu dizindeki testler ATLANIR.

YEREL KOSUM (Windows, PG18 istemcisi kurulu):
    docker run -d --name e1-restore-it \\
      -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres \\
      -p 5433:5432 -v C:/e1-restore-it-share:/shared \\
      timescale/timescaledb:2.17.2-pg16

    E1_TEST_PG_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/postgres \\
        pytest tests/integration -q

CI: runner'a `postgresql-client-16` kurulur, sunucu ayni imajdan servis
olarak gelir; sarmalayiciya gerek kalmaz.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

#: Yedek dosyalarinin host tarafindaki yeri. Konteyner bunu `/shared`
#: altinda gorur; PG16 sarmalayicisi yol cevirisini yapar.
SHARE = Path(os.getenv("E1_IT_SHARE", r"C:/e1-restore-it-share"))


def pytest_configure(config):  # noqa: ANN001
    config.addinivalue_line(
        "markers",
        "integration: gercek PostgreSQL/TimescaleDB gerektiren uctan uca test",
    )


def _sunucu_major(url: str) -> int | None:
    try:
        from sqlalchemy import create_engine, text

        eng = create_engine(url)
        try:
            with eng.connect() as c:
                return int(c.execute(text("SHOW server_version_num")).scalar()) // 10000
        finally:
            eng.dispose()
    except Exception:  # noqa: BLE001
        return None


def _istemci_major(ikili: str) -> int | None:
    yol = shutil.which(ikili)
    if not yol:
        return None
    try:
        p = subprocess.run([yol, "--version"], capture_output=True, text=True, timeout=20)
        m = re.search(r"(\d+)\.", (p.stdout or "") + (p.stderr or ""))
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _konteyner_var(ad: str) -> bool:
    try:
        p = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", ad],
            capture_output=True, text=True, timeout=30,
        )
        return p.returncode == 0 and p.stdout.strip() == "true"
    except Exception:  # noqa: BLE001
        return False


def _sarmalayici_uret(arac: str) -> Path:
    """PG16 sarmalayicisini KOSUM ANINDA uret.

    Depoya commit EDILMEZ: icinde bu makineye ozgu mutlak Python yolu var.
    Uretilen dosyalar `.gitignore` ile disarida tutulur.
    """
    import sys

    dizin = Path(__file__).parent
    cmd = dizin / f"{arac}.cmd"
    satirlar = [
        "@echo off",
        f'"{sys.executable}" "{dizin / "pg16_client.py"}" {arac} %*',
    ]
    cmd.write_text("\r\n".join(satirlar) + "\r\n", encoding="utf-8")
    return cmd


@pytest.fixture(scope="session", autouse=True)
def pg_ortami():
    """Kabul ortamini dogrular ve gerekiyorsa PG16 sarmalayicisini devreye alir."""
    url = os.getenv("E1_TEST_PG_URL")
    if not url:
        pytest.skip("E1_TEST_PG_URL yok — gercek PostgreSQL testleri atlandi")

    sunucu = _sunucu_major(url)
    if sunucu is None:
        pytest.skip(f"PostgreSQL'e baglanilamadi: {url}")

    # UYUMSUZ ISTEMCI OTOMATIK DUZELTILIR (yalnizca testlerde).
    #
    # Olculdu: pg_restore 18 -> PG16 sunucu, "unrecognized configuration
    # parameter transaction_timeout" ile duser. Uretimde istemci imaja
    # gomulu (16) oldugu icin bu durum olusmaz; gelistirici makinesinde
    # olusur. Testler uretimdekiyle AYNI istemciyi kullanmali, aksi halde
    # kabul kanitlanmis sayilmaz.
    istemci = _istemci_major("pg_restore")
    if istemci is not None and istemci > sunucu:
        konteyner = os.getenv("E1_IT_CONTAINER", "e1-restore-it")
        if not _konteyner_var(konteyner):
            pytest.skip(
                f"istemci (pg{istemci}) sunucudan (pg{sunucu}) yeni; uretimle "
                f"ayni surumu kullanmak icin '{konteyner}' konteyneri gerekli. "
                f"Bkz. bu dosyanin basindaki YEREL KOSUM notu."
            )
        for arac in ("pg_dump", "pg_restore", "psql"):
            os.environ[arac.upper()] = str(_sarmalayici_uret(arac))
        SHARE.mkdir(parents=True, exist_ok=True)

    yield {"url": url, "sunucu_major": sunucu, "istemci_major": istemci}


@pytest.fixture()
def paylasim(tmp_path) -> Path:
    """Yedek dosyalarinin yazilacagi dizin.

    Sarmalayici kullaniliyorsa dosya konteynerden gorunebilmeli, bu yuzden
    paylasilan dizine yazilir. Aksi halde normal `tmp_path` yeterlidir.
    """
    if os.environ.get("PG_RESTORE", "").endswith(".cmd"):
        SHARE.mkdir(parents=True, exist_ok=True)
        d = SHARE / tmp_path.name
        d.mkdir(parents=True, exist_ok=True)
        return d
    return tmp_path
