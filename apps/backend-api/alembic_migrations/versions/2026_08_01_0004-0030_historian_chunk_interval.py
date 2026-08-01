"""historian_chunk_interval

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-01 00:00:04.000000

`telemetry_history` chunk araligini 1 GUN -> 1 SAAT yapar.

NEDEN
-----
Chunk araligi 0007'de 1 gun olarak secilmisti. 600 cihaz olceginde
(103,68M satir/gun) tek bir chunk:

    103,68M / 1 gun x ~120 bayt  ~=  12 GB veri + index  ~=  17 GB/chunk

Postgres container'i 3 GB ve `shared_buffers` 768 MB. Yani TEK chunk
shared_buffers'in ~22 KATI. TimescaleDB'nin boyutlandirma kurali, YAZILAN
(sicak) chunk'larin bellege sigmasidir: sigmadiginda her ekleme ve her
sorgu diske iner, index sayfalari surekli tahliye edilir.

1 saatlik chunk ayni yukte ~700 MB eder — shared_buffers ile ayni mertebede
ve `effective_cache_size` (2 GB) icinde.

CHUNK SAYISI SORUN DEGIL
------------------------
90 gunluk retention x 24 = ~2160 chunk. TimescaleDB bu sayiyi rahat
tasir (onerilen ust sinir on binler mertebesinde) ve chunk dislama sayesinde
zaman filtreli sorgular yalnizca ilgili chunk'lara dokunur. Ustelik 7 gunden
eski chunk'lar sikistirildigi icin sicak (sikistirilmamis) pencere
7 x 24 = 168 chunk ile sinirli.

MEVCUT CHUNK'LAR DEGISMEZ
-------------------------
`set_chunk_time_interval` YALNIZCA bundan sonra olusturulacak chunk'lari
etkiler; mevcut chunk'lar oldugu gibi kalir ve retention onlari zamaninda
duser. Yani bu migration veri TASIMAZ, kilit TUTMAZ ve aninda biter —
buyuk bir tabloda bile guvenlidir.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "telemetry_history"
# 1 saat = 3_600_000_000 mikrosaniye (TimescaleDB integer/interval kabul eder).
CHUNK_INTERVAL = "1 hour"


def _timescaledb_installed(bind) -> bool:
    return (
        bind.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname='timescaledb'")
        ).first()
        is not None
    )


def _is_hypertable(bind) -> bool:
    try:
        return (
            bind.execute(
                sa.text(
                    "SELECT 1 FROM timescaledb_information.hypertables"
                    " WHERE hypertable_name = :t"
                ),
                {"t": TABLE},
            ).first()
            is not None
        )
    except Exception:  # noqa: BLE001
        return False


def _uygula(bind, interval: str) -> None:
    """Adimi SAVEPOINT icinde kosturur; hata yutulur ama transaction BOZULMAZ.

    Bkz. 0019/0023: yakalanan bir hata savepoint'siz transaction'i "aborted"
    birakir ve sonraki HER ifade (alembic'in `UPDATE alembic_version`'i dahil)
    duser — backend crash-loop'a girer. Chunk araligi bir PERFORMANS ayaridir;
    uygulanamamasi boot'u durdurmayi haketmez.
    """
    try:
        with bind.begin_nested():
            op.execute(
                f"SELECT set_chunk_time_interval('{TABLE}', INTERVAL '{interval}')"
            )
        logger.warning("0030: %s chunk araligi -> %s", TABLE, interval)
    except Exception as exc:  # noqa: BLE001
        logger.warning("0030: chunk araligi ayarlanamadi (atlandi): %s", exc)


def upgrade() -> None:
    bind = op.get_bind()
    if not _timescaledb_installed(bind):
        logger.info("0030: timescaledb yok — atlandi")
        return
    if not _is_hypertable(bind):
        logger.warning("0030: %s hypertable degil — atlandi", TABLE)
        return
    _uygula(bind, CHUNK_INTERVAL)


def downgrade() -> None:
    """Eski 1 gunluk araliga doner.

    Veri kaybi yok (yalnizca YENI chunk'lari etkiler), ama 600 cihaz
    olceginde chunk'lar yeniden shared_buffers'in ~22 katina cikar.
    """
    bind = op.get_bind()
    if not _timescaledb_installed(bind) or not _is_hypertable(bind):
        return
    _uygula(bind, "1 day")
