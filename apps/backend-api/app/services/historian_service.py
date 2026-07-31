"""Historian (`telemetry_history`) saglik raporu.

NEDEN VAR
---------
Historian, TimescaleDB hypertable'i olarak 90 gun retention + 7 gun sonrasi
compression ile calismak UZERE tasarlandi (migration 0007). Ama 0007'nin
`timescaledb yoksa atla` dali alindiginda alembic revision'i yine DAMGALANIR
ve tablo sonsuza kadar DUZ TABLO olarak kalir — retention hic kurulmaz.
600 cihaz x ~193 sinyal olceginde bu gunde ~26M satir demek: disk dolana
kadar hicbir belirti vermez.

Migration 0019 bu durumu onarmaya CALISIR (en-iyi-caba; bir migration'in
hata firlatmasi sahada backend'in hic ayaga kalkmamasi demek). Bu modul ise
GARANTILI olan tarafi saglar: durumu gorunur kilar. Operator Sistem Durumu
sayfasinda "retention yok" uyarisini disk dolmadan gorur.

MALIYET
-------
Sistem Durumu sayfasi bu bilgiyi 10 saniyede bir sorguluyor, o yuzden:
  * `COUNT(*)` ASLA KULLANILMAZ. 26M+ satirli bir tabloda tam tarama olurdu.
    Satir sayisi `pg_class.reltuples` TAHMINI ile okunur (planlayici
    istatistigi, katalog okumasi — sabit maliyet).
  * Sonuc `_CACHE_TTL_SEC` boyunca cache'lenir. Disk boyutu / politika varligi
    saniyeler icinde degismez.
  * Her sorgu ayri try/except: TimescaleDB goruntuleri surumler arasi
    degisebilir, bir introspection hatasi Sistem Durumu sayfasini
    DUSURMEMELI — bilinmeyen alan None doner.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TABLE = "telemetry_history"

# Beklenen politika degerleri — migration 0007/0019 ile AYNI olmali.
EXPECTED_RETENTION_DAYS = 90

# Disk boyutu/politika varligi hizli degismez; 10sn'lik polling'i bogmayalim.
_CACHE_TTL_SEC = 60.0
_cache: tuple[float, "HistorianStatus"] | None = None
_cache_lock = threading.Lock()


# Sorun kodlari — frontend i18n anahtari olarak kullanilir, DEGISTIRMEYIN.
PROBLEM_TIMESCALE_MISSING = "timescaledb_missing"
PROBLEM_NOT_HYPERTABLE = "not_hypertable"
PROBLEM_NO_RETENTION = "no_retention"
PROBLEM_NO_COMPRESSION = "no_compression"
PROBLEM_RETENTION_FAILING = "retention_failing"
PROBLEM_RETENTION_MISMATCH = "retention_mismatch"


@dataclass
class HistorianStatus:
    """Historian'in yapisal sagligi.

    `severity`:
      "ok"       — hypertable + retention yerinde
      "warning"  — calisir ama ideal degil (compression yok, dev ortami)
      "critical" — retention YOK: tablo sinirsiz buyuyor, disk dolacak
    """

    table: str = TABLE
    # "installed" | "available_not_installed" | "unavailable"
    timescaledb: str = "unavailable"
    is_hypertable: bool = False
    retention_days: int | None = None
    compression_enabled: bool = False
    continuous_aggregates: list[str] = field(default_factory=list)
    # Tahmin (reltuples) — tam sayim DEGIL. Bilincli: COUNT(*) cok pahali.
    row_estimate: int | None = None
    total_bytes: int | None = None
    oldest_sample_at: str | None = None
    newest_sample_at: str | None = None
    retention_last_run_status: str | None = None
    severity: str = "warning"
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "timescaledb": self.timescaledb,
            "is_hypertable": self.is_hypertable,
            "retention_days": self.retention_days,
            "compression_enabled": self.compression_enabled,
            "continuous_aggregates": self.continuous_aggregates,
            "row_estimate": self.row_estimate,
            "total_bytes": self.total_bytes,
            "oldest_sample_at": self.oldest_sample_at,
            "newest_sample_at": self.newest_sample_at,
            "retention_last_run_status": self.retention_last_run_status,
            "severity": self.severity,
            "problems": self.problems,
        }


def _scalar(db: Session, sql: str, params: dict | None = None) -> Any:
    """Tek deger dondur; her turlu hatada None.

    Introspection sorgulari surum/uzanti farklarina duyarli. Bir tanesinin
    patlamasi tum raporu (ve Sistem Durumu sayfasini) dusurmemeli.
    """
    try:
        return db.execute(text(sql), params or {}).scalar()
    except Exception:  # noqa: BLE001
        logger.debug("historian_introspection_failed sql=%s", sql[:80], exc_info=True)
        try:
            # Basarisiz sorgu transaction'i abort etmis olabilir; sonraki
            # sorgular calisabilsin diye geri al.
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


# Duz tabloda min/max taramasina izin verilen ust satir siniri.
# Bunun ustunde veri araligini HIC sorgulamayiz (bkz. _collect_time_range).
_TIME_RANGE_SCAN_MAX_ROWS = 1_000_000


def _collect_time_range(db: Session, st: "HistorianStatus") -> None:
    """En eski/en yeni ornek zamanini doldurur — GUVENLI oldugunda.

    DIKKAT, buradaki kosul performans icin ZORUNLU:
      Tablonun tek indeksi `(device_id, signal_key, source_timestamp)`.
      `min(source_timestamp)` bu indeksin UCUNCU kolonu oldugu icin indeksten
      okunamaz -> DUZ TABLODA TAM TARAMA olur.

      Ve bu tam olarak ARIZA durumudur: hypertable'a cevrilmemis, retention
      calismamis, dolayisiyla tablo devasa. Yani "sorun var mi?" diye soran
      sorgu, sorunun en agir oldugu yerde 100 GB tarardi (60sn'de bir).

      Hypertable'da durum farkli: partition kolonu `source_timestamp` ve
      Timescale chunk dislama ile min/max'i ucuz cozer.

    Bu yuzden yalnizca (a) hypertable ise veya (b) tablo kucukse (dev) sorulur.
    """
    if not st.is_hypertable and (
        st.row_estimate is None or st.row_estimate > _TIME_RANGE_SCAN_MAX_ROWS
    ):
        return
    st.oldest_sample_at = _scalar(db, f"SELECT min(source_timestamp)::text FROM {TABLE}")
    st.newest_sample_at = _scalar(db, f"SELECT max(source_timestamp)::text FROM {TABLE}")


def _collect(db: Session) -> HistorianStatus:
    st = HistorianStatus()

    # --- TimescaleDB durumu -------------------------------------------------
    installed = _scalar(db, "SELECT 1 FROM pg_extension WHERE extname='timescaledb'")
    if installed:
        st.timescaledb = "installed"
    else:
        available = _scalar(
            db, "SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'"
        )
        st.timescaledb = "available_not_installed" if available else "unavailable"

    # --- Satir tahmini (her durumda anlamli, sabit maliyet) -----------------
    # reltuples: planlayici tahmini. -1 => hic ANALYZE edilmemis.
    reltuples = _scalar(
        db, "SELECT reltuples::bigint FROM pg_class WHERE relname = :t", {"t": TABLE}
    )
    if reltuples is not None and reltuples >= 0:
        st.row_estimate = int(reltuples)

    if st.timescaledb != "installed":
        # Vanilla postgres: duz tablo, politika kavrami yok.
        st.total_bytes = _scalar(
            db, "SELECT pg_total_relation_size(:t)", {"t": TABLE}
        )
        _collect_time_range(db, st)
        st.problems.append(PROBLEM_TIMESCALE_MISSING)
        st.severity = "critical"
        return st

    # --- Hypertable mi? -----------------------------------------------------
    st.is_hypertable = bool(
        _scalar(
            db,
            "SELECT 1 FROM timescaledb_information.hypertables"
            " WHERE hypertable_name = :t",
            {"t": TABLE},
        )
    )

    if st.is_hypertable:
        # hypertable_size chunk'lari toplar; duz pg_total_relation_size
        # hypertable'in BOS ebeveynini olcer ve 0 gibi gorunur.
        st.total_bytes = _scalar(db, "SELECT hypertable_size(:t::regclass)", {"t": TABLE})
    if st.total_bytes is None:
        st.total_bytes = _scalar(db, "SELECT pg_total_relation_size(:t)", {"t": TABLE})

    # Veri araligi — is_hypertable belirlendikten SONRA (tam tarama korumasi).
    _collect_time_range(db, st)

    # --- Retention politikasi ----------------------------------------------
    # jobs.config bir jsonb; retention icin {"drop_after": "90 days"} tasir.
    retention_cfg = _scalar(
        db,
        "SELECT config->>'drop_after' FROM timescaledb_information.jobs"
        " WHERE proc_name='policy_retention' AND hypertable_name = :t LIMIT 1",
        {"t": TABLE},
    )
    if retention_cfg:
        # "90 days" / "90 days 00:00:00" -> 90
        digits = "".join(ch for ch in str(retention_cfg).split()[0] if ch.isdigit())
        st.retention_days = int(digits) if digits else None

    st.retention_last_run_status = _scalar(
        db,
        "SELECT last_run_status FROM timescaledb_information.job_stats"
        " WHERE hypertable_name = :t AND job_id IN ("
        "   SELECT job_id FROM timescaledb_information.jobs"
        "   WHERE proc_name='policy_retention' AND hypertable_name = :t)"
        " LIMIT 1",
        {"t": TABLE},
    )

    st.compression_enabled = bool(
        _scalar(
            db,
            "SELECT 1 FROM timescaledb_information.jobs"
            " WHERE proc_name='policy_compression' AND hypertable_name = :t",
            {"t": TABLE},
        )
    )

    # --- Continuous aggregate'ler ------------------------------------------
    try:
        rows = db.execute(
            text(
                "SELECT view_name FROM timescaledb_information.continuous_aggregates"
                " WHERE view_name LIKE :p ORDER BY view_name"
            ),
            {"p": f"{TABLE}%"},
        ).all()
        st.continuous_aggregates = [r[0] for r in rows]
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

    # --- Karar --------------------------------------------------------------
    if not st.is_hypertable:
        # En kotu durum: extension var ama tablo cevrilmemis -> retention YOK.
        st.problems.append(PROBLEM_NOT_HYPERTABLE)
        st.severity = "critical"
        return st

    if st.retention_days is None:
        st.problems.append(PROBLEM_NO_RETENTION)
        st.severity = "critical"
    elif st.retention_days != EXPECTED_RETENTION_DAYS:
        # Operator bilincli degistirmis olabilir; engellemiyoruz, bildiriyoruz.
        st.problems.append(PROBLEM_RETENTION_MISMATCH)
        st.severity = "warning"

    if st.retention_last_run_status not in (None, "Success"):
        # Politika VAR ama kosmuyor — disk yine dolar.
        st.problems.append(PROBLEM_RETENTION_FAILING)
        st.severity = "critical"

    if not st.compression_enabled:
        st.problems.append(PROBLEM_NO_COMPRESSION)
        if st.severity == "ok":
            st.severity = "warning"

    if not st.problems:
        st.severity = "ok"
    return st


def get_historian_status(db: Session, *, refresh: bool = False) -> HistorianStatus:
    """Historian saglik raporu (TTL cache'li).

    `refresh=True` cache'i atlar — operator sayfada "yenile"ye bastiginda.
    """
    global _cache
    if not refresh:
        with _cache_lock:
            cached = _cache
        if cached is not None and (time.monotonic() - cached[0]) < _CACHE_TTL_SEC:
            return cached[1]

    status = _collect(db)
    with _cache_lock:
        _cache = (time.monotonic(), status)
    return status


def invalidate_cache() -> None:
    """Migration/politika degisiminden sonra cache'i dusur."""
    global _cache
    with _cache_lock:
        _cache = None
