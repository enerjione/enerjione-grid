"""tier_historian_aggregates

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-31 00:00:05.000000

Historian ozet katmanlarina RETENTION + SIKISTIRMA ekler.

SORUN
-----
0007 continuous aggregate'leri "sonsuza kadar saklanir" gerekcesiyle
retention'siz birakti (0007 docstring'i bunu acikca yaziyor). Ama:

  * `telemetry_history_1m` bir OZET DEGIL, pratikte ham verinin KOPYASIDIR.
    1 dakikalik kova = GROUP BY (device_id, signal_key, dakika). Cihaz basina
    dakikada ~30 FARKLI sinyal degistigi icin (kaynak: telemetry_retention.py
    dosya basligi) hemen her okuma KENDI dakika kovasina duser:
        600 cihaz x 30 sinyal/dk x 1440 dk = ~25,9M satir/GUN
    Yani ham veriyle 1:1. Ustelik CAGG'lerde sikistirma da acik degildi.
  * Ham tablo 90 gunde budanirken, yanindaki bu kopya SINIRSIZ buyuyordu.
    Diski asil dolduran kalem buydu ve hicbir yerde gorunmuyordu.

KATMANLI TASARIM (operator karari)
-----------------------------------
    ham `telemetry_history`   : 90 gun   (0007/0019'da tanimli, DEGISMIYOR)
    `telemetry_history_1m`    : 1 YIL    <- bu migration
    `telemetry_history_1h`    : 2 YIL    <- bu migration

Mantik: yakin gecmiste dakika cozunurlugu gerekir (ariza sonrasi inceleme);
uzak gecmiste saatlik egri yeterlidir ve cok daha ucuzdur. 2 yillik saatlik
seri `system_events` denetim ufkuyla hizali.

Ham veri silinse bile ozetler kaldigi icin gecmis analizlere dahil edilebilir
— urun gereksinimi buydu.

RETENTION ILE REFRESH PENCERESI CAKISMAZ
-----------------------------------------
Kaynak chunk'lari dusuren bir retention, CAGG'in refresh penceresi o bolgeye
uzaniyorsa sorun cikarir. Burada cakisma YOK:
    1m refresh start_offset = 3 saat   << 365 gun
    1h refresh start_offset = 3 gun    << 730 gun

HATA POLITIKASI — 0019 ILE AYNI
--------------------------------
Her adim try/except icinde; hata YUTULUR ve uyari loglanir. Sebep: bu
migration container CMD'sinde uvicorn'dan ONCE kosuyor; burada exception
firlatmak backend'in HIC ayaga kalkmamasi ve appliance'in crash-loop'a
girmesi demek. Gorunurluk `historian_service` uzerinden saglaniyor.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (view adi, retention gun, sikistirma sonrasi gun, orderby kolonu)
_TIERS = (
    ("telemetry_history_1m", 365, 7),
    ("telemetry_history_1h", 730, 30),
)


def _try(label: str, fn) -> bool:
    """Adimi SAVEPOINT icinde kosturur; hata yutulur ama transaction BOZULMAZ.

    Bu dosyada savepoint OZELLIKLE kritik: kod, "tam form" ALTER'in bazi
    TimescaleDB surumlerinde reddedilecegini BEKLEYIP `if not ok:` ile "sade
    form"a dusuyor. Savepoint olmadan ilk hata transaction'i abort ettigi icin
    yedek yol da, sonraki add_compression_policy/add_retention_policy de,
    alembic'in `UPDATE alembic_version` ifadesi de patliyordu — yani planlanan
    geri dusme yolu HIC calisamiyordu ve backend crash-loop'a giriyordu.
    """
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            fn()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("0023 adim basarisiz (atlaniyor): %s -> %s", label, exc)
        return False


def _timescaledb_installed(bind) -> bool:
    return (
        bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname='timescaledb'")).first()
        is not None
    )


def _cagg_exists(bind, view: str) -> bool:
    try:
        return (
            bind.execute(
                sa.text(
                    "SELECT 1 FROM timescaledb_information.continuous_aggregates"
                    " WHERE view_name = :v"
                ),
                {"v": view},
            ).first()
            is not None
        )
    except Exception:  # noqa: BLE001
        return False


def _has_policy(bind, view: str, proc_name: str) -> bool:
    """Bu CAGG icin verilen tipte bir arka plan job'u var mi?

    CAGG'lerde `jobs.hypertable_name` materialization hypertable'ini
    (`_materialized_hypertable_N`) gosterir, view adini DEGIL. Bu yuzden
    eslestirmeyi continuous_aggregates uzerinden yapiyoruz.
    """
    try:
        return (
            bind.execute(
                sa.text(
                    "SELECT 1 FROM timescaledb_information.jobs j"
                    " JOIN timescaledb_information.continuous_aggregates c"
                    "   ON j.hypertable_name = c.materialization_hypertable_name"
                    " WHERE c.view_name = :v AND j.proc_name = :p"
                ),
                {"v": view, "p": proc_name},
            ).first()
            is not None
        )
    except Exception:  # noqa: BLE001
        return False


def upgrade() -> None:
    bind = op.get_bind()

    if not _timescaledb_installed(bind):
        logger.info("0023: timescaledb yok (vanilla postgres) — ozet katmanlari atlandi")
        return

    for view, retention_days, compress_after_days in _TIERS:
        if not _cagg_exists(bind, view):
            logger.warning("0023: %s continuous aggregate'i yok — atlandi", view)
            continue

        # 1) SIKISTIRMA. Once tablo ayari, sonra policy. CAGG'lerde
        # compress_segmentby/orderby bazi TimescaleDB surumlerinde kabul
        # edilmiyor; once tam formu dener, olmazsa sade forma duseriz.
        if not _has_policy(bind, view, "policy_compression"):
            ok = _try(
                f"ALTER MATERIALIZED VIEW {view} SET compress (tam form)",
                lambda v=view: op.execute(
                    f"ALTER MATERIALIZED VIEW {v} SET ("
                    " timescaledb.compress = true,"
                    " timescaledb.compress_segmentby = 'device_id, signal_key',"
                    " timescaledb.compress_orderby = 'bucket DESC')"
                ),
            )
            if not ok:
                _try(
                    f"ALTER MATERIALIZED VIEW {view} SET compress (sade form)",
                    lambda v=view: op.execute(
                        f"ALTER MATERIALIZED VIEW {v} SET (timescaledb.compress = true)"
                    ),
                )
            _try(
                f"add_compression_policy {view}",
                lambda v=view, d=compress_after_days: op.execute(
                    f"SELECT add_compression_policy('{v}',"
                    f" compress_after => INTERVAL '{d} days', if_not_exists => TRUE)"
                ),
            )
        else:
            logger.info("0023: %s sikistirma politikasi mevcut", view)

        # 2) RETENTION — asil mesele. Yoksa bu tablolar sinirsiz buyur.
        if not _has_policy(bind, view, "policy_retention"):
            logger.warning(
                "0023: %s retention politikasi YOK — %d gun ekleniyor",
                view,
                retention_days,
            )
            _try(
                f"add_retention_policy {view}",
                lambda v=view, d=retention_days: op.execute(
                    f"SELECT add_retention_policy('{v}',"
                    f" drop_after => INTERVAL '{d} days', if_not_exists => TRUE)"
                ),
            )
        else:
            logger.info("0023: %s retention politikasi mevcut", view)


def downgrade() -> None:
    """Politikalari kaldirir.

    DIKKAT: retention politikasini kaldirmak veri KAYBETTIRMEZ, tam tersine
    tablolarin yeniden sinirsiz buyumesine izin verir. Yalnizca bu migration'in
    yanlis oldugu anlasilirsa kullanilmali.
    """
    bind = op.get_bind()
    if not _timescaledb_installed(bind):
        return
    for view, _r, _c in _TIERS:
        if not _cagg_exists(bind, view):
            continue
        _try(
            f"remove_retention_policy {view}",
            lambda v=view: op.execute(
                f"SELECT remove_retention_policy('{v}', if_exists => TRUE)"
            ),
        )
        _try(
            f"remove_compression_policy {view}",
            lambda v=view: op.execute(
                f"SELECT remove_compression_policy('{v}', if_exists => TRUE)"
            ),
        )
