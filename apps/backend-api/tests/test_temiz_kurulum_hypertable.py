"""Temiz kurulum saklama politikalarini da KURMALI.

YASANAN ARIZA
-------------
Temiz kurulumda sema `Base.metadata.create_all` ile modellerden kuruluyor ve
alembic dogrudan `head` damgalaniyor. Bu, 30 migration'in bos veritabanina
tekrar uygulanmasindan dogan cokmeyi cozdu — ama sessiz bir yan etkisi vardi:

    `create_all` YALNIZCA DUZ TABLOLARI kurar.

Hypertable'a cevirme ve 90 gunluk saklama politikasi SQLAlchemy modelinde
tarif EDILEMEZ; yalnizca migration govdesinde vardi. Hicbir migration
kosmadigi icin bunlar atlandi ve Sistem Durumu sayfasi soyle dedi:

    "Arsiv tablosu hypertable'a cevrilmemis — saklama suresi politikasi
     calismiyor, tablo sinirsiz buyuyor."

600 cihaz x ~193 sinyal olceginde tablo gunde ~26M satir buyur; belirti disk
dolana kadar ortaya cikmaz.

TESTIN ASIL DEGERI
------------------
"Sema kuruldu mu" diye bakan bir test bu arizada YESIL KALIRDI: tablolarin
hepsi vardi, eksik olan sey tablonun TURU ve arka plan politikalariydi.
Burada sinanan sey, kurulum akisinin depolama kurulumunu GERCEKTEN
cagirdigi ve cagrinin dogru SQL'i urettigidir.

TimescaleDB gerektiren ifadeler yerel vanilla postgres'te kosturulamaz; bu
yuzden baglanti taklit ediliyor ve URETILEN SQL sinaniyor. Boylece test
gelistirici makinesinde de CI'da da anlamli kalir.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db.timescale_setup import (
    RETENTION_DAYS,
    TABLE,
    ensure_historian_storage,
)


class _SahteSonuc:
    def __init__(self, var: bool) -> None:
        self._var = var

    def first(self):
        return (1,) if self._var else None


class _SahteBaglanti:
    """Katalog sorgularina senaryoya gore cevap veren taklit baglanti.

    `executed` listesi URETILEN her SQL'i saklar; iddialar onun uzerinde.
    """

    def __init__(self, *, hypertable: bool, jobs: set[str] | None = None) -> None:
        self.hypertable = hypertable
        self.jobs = jobs or set()
        self.executed: list[str] = []

    # -- sorgu yonlendirme ---------------------------------------------------
    def execute(self, clause, params=None):  # noqa: ANN001
        sql = str(clause)
        self.executed.append(sql)
        p = params or {}

        if "information_schema.tables" in sql:
            return _SahteSonuc(True)
        if "pg_available_extensions" in sql:
            return _SahteSonuc(True)
        if "pg_extension" in sql:
            return _SahteSonuc(True)
        if "timescaledb_information.hypertables" in sql:
            return _SahteSonuc(self.hypertable)
        if "continuous_aggregates" in sql and "jobs" in sql:
            return _SahteSonuc(f"{p.get('v')}:{p.get('p')}" in self.jobs)
        if "timescaledb_information.jobs" in sql:
            return _SahteSonuc(p.get("p", "") in self.jobs)
        if "continuous_aggregates" in sql:
            return _SahteSonuc(True)
        return _SahteSonuc(False)

    # -- SAVEPOINT taklidi ---------------------------------------------------
    def begin_nested(self):  # noqa: ANN201
        baglanti = self

        class _Kapsam:
            def __enter__(self):
                return baglanti

            def __exit__(self, *a):
                return False

        return _Kapsam()

    def _yazilanlar(self) -> str:
        return "\n".join(self.executed)


def test_duz_tablo_hypertable_a_cevrilir() -> None:
    """ASIL VAKA: temiz kurulum sonrasi tablo duz — cevrilmeli."""
    bind = _SahteBaglanti(hypertable=False)
    rapor = ensure_historian_storage(bind)

    assert "create_hypertable" in rapor["actions"], (
        "duz tablo hypertable'a cevrilmedi — saklama politikasi hic kurulamaz"
    )
    sql = bind._yazilanlar()
    assert f"create_hypertable('{TABLE}', 'source_timestamp'" in sql
    # Duz tabloda birikmis satirlar KAYBOLMAMALI.
    assert "migrate_data => TRUE" in sql


def test_saklama_politikasi_kurulur() -> None:
    """Asil mesele bu: yoksa tablo sinirsiz buyur ve disk dolar."""
    bind = _SahteBaglanti(hypertable=False)
    rapor = ensure_historian_storage(bind)

    assert "add_retention_policy" in rapor["actions"]
    assert (
        f"add_retention_policy('{TABLE}', INTERVAL '{RETENTION_DAYS} days'"
        in bind._yazilanlar().replace("\n", " ")
    )


def test_zaten_kuruluysa_tekrar_kurmaz() -> None:
    """Idempotent olmali: her acilista kosuyor, her seferinde ALTER etmemeli."""
    bind = _SahteBaglanti(
        hypertable=True,
        jobs={"policy_retention", "policy_compression"},
    )
    rapor = ensure_historian_storage(bind)

    sql = bind._yazilanlar()
    assert "create_hypertable" not in sql, "zaten hypertable iken tekrar cevirdi"
    assert "add_retention_policy('telemetry_history'" not in sql
    assert "add_compression_policy('telemetry_history'" not in sql
    assert "create_hypertable" not in rapor["actions"]


def test_timescaledb_yoksa_sessizce_atlar() -> None:
    """Vanilla postgres (gelistirici makinesi) kurulumu DUSURMEMELI."""

    class _Yok(_SahteBaglanti):
        def execute(self, clause, params=None):  # noqa: ANN001
            sql = str(clause)
            self.executed.append(sql)
            if "information_schema.tables" in sql:
                return _SahteSonuc(True)
            if "pg_available_extensions" in sql:
                return _SahteSonuc(False)
            return _SahteSonuc(False)

    bind = _Yok(hypertable=False)
    rapor = ensure_historian_storage(bind)
    assert rapor["skipped"] == "timescaledb_unavailable"
    assert rapor["actions"] == []


def test_hypertable_kurulamazsa_politika_denenmez() -> None:
    """Hypertable olmadan politika eklenemez; bos yere denemek log'u kirletir
    ve gercek sebebi gizler."""

    class _Patlayan(_SahteBaglanti):
        def execute(self, clause, params=None):  # noqa: ANN001
            sql = str(clause)
            if "create_hypertable" in sql:
                raise sa.exc.SQLAlchemyError("cevrilemedi")
            return super().execute(clause, params)

    bind = _Patlayan(hypertable=False)
    rapor = ensure_historian_storage(bind)

    assert rapor["skipped"] == "hypertable_failed"
    assert "add_retention_policy" not in bind._yazilanlar()


def test_kurulum_akisi_depolama_kurulumunu_cagirir(monkeypatch) -> None:
    """`migrate_db` bu adimi GERCEKTEN cagirmali.

    Modul dogru olsa bile kurulum akisi onu cagirmiyorsa sahada hicbir sey
    degismez — arizanin tam olarak bu bicimi yasandi.

    KAYNAK METNI DEGIL DAVRANIS olculuyor. Bu test eskiden
    `inspect.getsource(migrate)` icinde string ariyordu; govde bir yardimci
    fonksiyona tasinir tasinmaz davranis DOGRUYKEN kirmizi oldu — ve cagri
    gercekten silinseydi de ayni sekilde kirmizi olacakti. Yani iki durumu
    birbirinden ayirt edemiyordu. Artik `migrate()` gercekten kosturuluyor.
    """
    from app.db import timescale_setup
    from scripts import migrate_db as md

    sira: list[str] = []

    class _SahteDialect:
        # Advisory lock yolunun disinda kal: burada olculen sey kilit degil,
        # depolama kurulumunun CAGRILMASI.
        name = "sqlite"

    class _SahteBind:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class _SahteEngine:
        dialect = _SahteDialect()

        def begin(self):
            return _SahteBind()

    class _SahteInspector:
        def has_table(self, _ad):
            return False  # TEMIZ KURULUM dali

    class _SahteCommand:
        @staticmethod
        def stamp(*_a, **_kw):
            sira.append("stamp")

        @staticmethod
        def upgrade(*_a, **_kw):  # pragma: no cover — temiz kurulumda cagrilmaz
            sira.append("upgrade")

    monkeypatch.setattr(md, "engine", _SahteEngine())
    monkeypatch.setattr(md, "inspect", lambda _e: _SahteInspector())
    monkeypatch.setattr(md, "command", _SahteCommand)
    monkeypatch.setattr(
        md.Base.metadata, "create_all", lambda **_kw: sira.append("create_all")
    )
    monkeypatch.setattr(
        timescale_setup,
        "ensure_historian_storage",
        lambda _bind: (sira.append("historian"), {"actions": [], "skipped": None})[1],
    )

    md.migrate()

    assert "historian" in sira, "migrate_db depolama kurulumunu cagirmiyor"
    # Sema kurulduktan SONRA cagrilmali; once cagrilirsa tablo henuz yok.
    assert sira.index("create_all") < sira.index("historian")
