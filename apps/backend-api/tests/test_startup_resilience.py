"""Startup dayanikliligi — appliance'in KARARMAMASI garantisi.

NEDEN BU TEST VAR
-----------------
`create_tables` startup event'i, ~160 DDL ifadesini tek transaction'da
kosturan legacy bootstrap blogunu cagirir. Oradan bir exception CIKARSA:

    uvicorn "Application startup failed." der ve process'i sonlandirir
      -> Dockerfile CMD `migrate_db && exec uvicorn` zinciri oldugu icin
         container cikar
      -> compose `restart: unless-stopped` ile SONSUZ CRASH-LOOP
      -> appliance tamamen karanlik; operator arayuze ulasip teshis bile
         yapamaz.

Yani tek bir kilit cakismasi ya da beklenmedik bir satir, sahayi komple
durdurabilir. Bu testler o yolun kapali kaldigini kilitler.

Bu blok LEGACY'dir: gercek sema otoritesi Alembic'tir ve `scripts/migrate_db`
uvicorn'dan ONCE kosup gercek bir sema sorununda zaten gurultulu sekilde
patlar. Dolayisiyla buradaki basarisizligi yutup acilmaya devam etmek
dogru takastir.
"""

from __future__ import annotations

import app.main as main_module


def test_create_tables_swallows_bootstrap_failure(monkeypatch, caplog):
    """Bootstrap patlarsa startup DEVAM ETMELI (crash-loop olmamali)."""

    def _boom() -> None:
        raise RuntimeError("simulasyon: DDL patladi")

    monkeypatch.setattr(main_module, "_legacy_bootstrap_ddl", _boom)

    # Exception DISARI SIZMAMALI.
    main_module.create_tables()

    assert any(
        "legacy_bootstrap_ddl_failed" in r.message or "legacy_bootstrap_ddl_failed" in r.getMessage()
        for r in caplog.records
    ), "hata yutuldu ama LOGLANMADI — sessiz yutma kabul edilemez"


def test_create_tables_propagates_nothing_even_on_db_error(monkeypatch):
    """DB baglanti hatasi da startup'i durdurmamali.

    Saha senaryosu: Postgres healthcheck'i gecti ama backend DDL'i kosarken
    kisa bir kesinti oldu. Bu, backend'in HIC acilmamasi icin sebep degil.
    """
    from sqlalchemy.exc import OperationalError

    def _boom() -> None:
        raise OperationalError("SELECT 1", {}, Exception("baglanti koptu"))

    monkeypatch.setattr(main_module, "_legacy_bootstrap_ddl", _boom)
    main_module.create_tables()  # patlamamali


def test_bootstrap_sets_lock_timeout():
    """Legacy bootstrap KILIT BEKLEME TAVANI koymali.

    Tavan olmadan `ALTER TABLE ... IF NOT EXISTS` no-op olsa bile ACCESS
    EXCLUSIVE kilidi ister ve cakisan bir kilit varsa (pg_dump, restore,
    operator psql'i, idle-in-transaction baglanti) SONSUZA KADAR bekler.
    Bu, restart ile COZULMEYEN bir kilitlenmedir: her deneme ayni kilide
    girer.

    Kaynak metnini kontrol ediyoruz cunku davranisi calistirarak dogrulamak
    canli bir PostgreSQL + cakisan kilit gerektirir (o dogrulama ayrica
    elle yapildi: kilit altinda blok 5.0 sn sonra hata verdi).
    """
    import inspect

    src = inspect.getsource(main_module._legacy_bootstrap_ddl)
    assert "SET LOCAL lock_timeout" in src, (
        "engine.begin() blogunda lock_timeout yok — cakisan kilitte boot "
        "sonsuza kadar bekler"
    )
    assert "SET LOCAL statement_timeout" in src, (
        "statement_timeout yok — tek bir agir ifade boot'u kilitleyebilir"
    )
    assert "SET lock_timeout" in src, (
        "autocommit (ALTER TYPE) blogunda lock_timeout yok"
    )
