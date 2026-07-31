"""Historian saglik raporu — karar mantigi + tam-tarama korumasi.

Bu testler TimescaleDB GEREKTIRMEZ: introspection sorgulari sahte bir
Session ile beslenir, boylece sahada olusabilecek her kombinasyon (hypertable
yok, retention yok, job basarisiz...) CI'da kosturulabilir.

Ayrica en kritik performans invariant'i burada kilitleniyor: duz tabloda ve
tablo buyukken `min/max(source_timestamp)` SORULMAMALI — indeks
(device_id, signal_key, source_timestamp) oldugu icin tam tarama olurdu ve
bu tam olarak arizanin en agir oldugu durum.
"""

from __future__ import annotations

import pytest

from app.services import historian_service as hs


class FakeSession:
    """`_scalar`/`execute` cagrilarini desen eslestirerek yanitlar.

    `answers`: SQL parcasi -> donecek deger. Ilk eslesen kazanir.
    Sorulan tum SQL'ler `asked` listesinde birikir (hangi sorgunun
    calistirildigini test edebilmek icin).
    """

    def __init__(self, answers: dict[str, object], rows: list | None = None) -> None:
        self.answers = answers
        self.rows = rows or []
        self.asked: list[str] = []

    def execute(self, stmt, params=None):  # noqa: ANN001
        sql = str(stmt)
        self.asked.append(sql)
        session = self

        class _Result:
            def scalar(self):
                for needle, value in session.answers.items():
                    if needle in sql:
                        return value
                return None

            def all(self):
                return session.rows

        return _Result()

    def rollback(self):  # noqa: D102
        pass


def _healthy_answers(**over):
    base = {
        "pg_extension": 1,                       # timescaledb kurulu
        "reltuples": 5_000_000,
        "timescaledb_information.hypertables": 1,
        "hypertable_size": 12_345_678,
        "config->>'drop_after'": "90 days",
        "last_run_status": "Success",
        "policy_compression": 1,
        "min(source_timestamp)": "2026-05-01T00:00:00+00:00",
        "max(source_timestamp)": "2026-07-31T00:00:00+00:00",
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clear_cache():
    hs.invalidate_cache()
    yield
    hs.invalidate_cache()


# --- Saglikli durum ---------------------------------------------------------
def test_healthy_historian_reports_ok():
    db = FakeSession(_healthy_answers(), rows=[("telemetry_history_1m",), ("telemetry_history_1h",)])
    st = hs._collect(db)
    assert st.severity == "ok"
    assert st.problems == []
    assert st.is_hypertable is True
    assert st.retention_days == 90
    assert st.compression_enabled is True
    assert st.continuous_aggregates == ["telemetry_history_1m", "telemetry_history_1h"]
    assert st.total_bytes == 12_345_678
    assert st.row_estimate == 5_000_000


# --- ASIL ARIZA: 0007 timescaledb'siz kosmus ------------------------------
def test_extension_installed_but_table_not_hypertable_is_critical():
    """Extension var ama tablo cevrilmemis -> retention YOK -> disk dolar."""
    db = FakeSession(_healthy_answers(**{"timescaledb_information.hypertables": None}))
    st = hs._collect(db)
    assert st.severity == "critical"
    assert hs.PROBLEM_NOT_HYPERTABLE in st.problems


def test_hypertable_without_retention_is_critical():
    db = FakeSession(_healthy_answers(**{"config->>'drop_after'": None}))
    st = hs._collect(db)
    assert st.severity == "critical"
    assert hs.PROBLEM_NO_RETENTION in st.problems


def test_retention_job_failing_is_critical():
    """Politika VAR ama kosmuyor — disk yine dolar, 'var' demek yetmez."""
    db = FakeSession(_healthy_answers(**{"last_run_status": "Failed"}))
    st = hs._collect(db)
    assert st.severity == "critical"
    assert hs.PROBLEM_RETENTION_FAILING in st.problems


def test_timescaledb_missing_is_critical():
    db = FakeSession({"pg_extension": None, "pg_available_extensions": None, "reltuples": 10})
    st = hs._collect(db)
    assert st.timescaledb == "unavailable"
    assert st.severity == "critical"
    assert hs.PROBLEM_TIMESCALE_MISSING in st.problems


def test_timescaledb_available_but_not_installed():
    db = FakeSession({"pg_extension": None, "pg_available_extensions": 1, "reltuples": 10})
    st = hs._collect(db)
    assert st.timescaledb == "available_not_installed"
    assert hs.PROBLEM_TIMESCALE_MISSING in st.problems


# --- Daha hafif sorunlar ----------------------------------------------------
def test_missing_compression_is_only_warning():
    db = FakeSession(_healthy_answers(**{"policy_compression": None}))
    st = hs._collect(db)
    assert st.severity == "warning"
    assert st.problems == [hs.PROBLEM_NO_COMPRESSION]


def test_retention_mismatch_is_warning_not_critical():
    """Operator bilincli degistirmis olabilir; bildir ama kritik sayma."""
    db = FakeSession(_healthy_answers(**{"config->>'drop_after'": "30 days"}))
    st = hs._collect(db)
    assert st.retention_days == 30
    assert st.severity == "warning"
    assert hs.PROBLEM_RETENTION_MISMATCH in st.problems


@pytest.mark.parametrize(
    "raw,expected",
    [("90 days", 90), ("30 days 00:00:00", 30), ("7 days", 7), ("", None), ("abc", None)],
)
def test_retention_interval_parsing(raw, expected):
    db = FakeSession(_healthy_answers(**{"config->>'drop_after'": raw or None}))
    st = hs._collect(db)
    assert st.retention_days == expected


# --- TAM TARAMA KORUMASI (performans invariant'i) --------------------------
def _asked_time_range(db: FakeSession) -> bool:
    return any("min(source_timestamp)" in s for s in db.asked)


def test_no_full_scan_on_large_plain_table():
    """Duz + buyuk tablo: zaman araligi SORULMAMALI (tam tarama olurdu).

    Bu, arizanin en agir oldugu durum — tam da burada 100 GB taramak
    'sorun var mi' sorusunu felakete cevirirdi.
    """
    db = FakeSession({
        "pg_extension": None, "pg_available_extensions": 1,
        "reltuples": 50_000_000,
        "min(source_timestamp)": "OLMAMALI",
    })
    st = hs._collect(db)
    assert not _asked_time_range(db), "buyuk duz tabloda min/max sorulmus (tam tarama)"
    assert st.oldest_sample_at is None


def test_time_range_asked_on_small_plain_table():
    """Kucuk tablo (dev): tarama ucuz, bilgi degerli."""
    db = FakeSession({
        "pg_extension": None, "pg_available_extensions": None,
        "reltuples": 1000,
        "min(source_timestamp)": "2026-01-01",
        "max(source_timestamp)": "2026-01-02",
    })
    st = hs._collect(db)
    assert _asked_time_range(db)
    assert st.oldest_sample_at == "2026-01-01"


def test_time_range_asked_on_hypertable_even_when_huge():
    """Hypertable'da partition kolonu source_timestamp; chunk dislama ile ucuz."""
    db = FakeSession(_healthy_answers(**{"reltuples": 500_000_000}))
    st = hs._collect(db)
    assert _asked_time_range(db)
    assert st.newest_sample_at == "2026-07-31T00:00:00+00:00"


def test_unknown_row_estimate_skips_scan():
    """reltuples okunamadiysa (hic ANALYZE edilmemis) temkinli davran."""
    db = FakeSession({
        "pg_extension": None, "pg_available_extensions": None,
        "reltuples": None, "min(source_timestamp)": "OLMAMALI",
    })
    hs._collect(db)
    assert not _asked_time_range(db)


def test_count_star_is_never_used():
    """Satir sayisi TAHMIN ile okunmali; COUNT(*) 26M+ satirda tam tarama."""
    db = FakeSession(_healthy_answers())
    hs._collect(db)
    assert not any("count(*)" in s.lower() for s in db.asked)


# --- Dayaniklilik -----------------------------------------------------------
def test_introspection_error_degrades_gracefully():
    """Bir sorgu patlarsa Sistem Durumu sayfasi DUSMEMELI."""

    class ExplodingSession(FakeSession):
        def execute(self, stmt, params=None):  # noqa: ANN001
            if "hypertable_size" in str(stmt):
                raise RuntimeError("timescaledb surum farki")
            return super().execute(stmt, params)

    db = ExplodingSession(_healthy_answers())
    st = hs._collect(db)  # patlamamali
    assert st.is_hypertable is True
    assert st.severity in ("ok", "warning", "critical")


# --- Cache ------------------------------------------------------------------
def test_cache_avoids_repeat_queries():
    db = FakeSession(_healthy_answers())
    hs.get_historian_status(db)
    first = len(db.asked)
    hs.get_historian_status(db)
    assert len(db.asked) == first, "cache calismadi, sorgular tekrarlandi"


def test_refresh_bypasses_cache():
    db = FakeSession(_healthy_answers())
    hs.get_historian_status(db)
    first = len(db.asked)
    hs.get_historian_status(db, refresh=True)
    assert len(db.asked) > first


def test_to_dict_has_all_report_fields():
    """Pydantic HistorianReport(**to_dict()) calismali — alan adlari eslesmeli."""
    from app.api.system_status import HistorianReport

    db = FakeSession(_healthy_answers())
    report = HistorianReport(**hs._collect(db).to_dict())
    assert report.severity == "ok"
    assert report.table == "telemetry_history"
