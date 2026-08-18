"""Startup dayanikliligi — appliance'in KARARMAMASI garantisi.

NEDEN BU TEST VAR
-----------------
Startup event'inden cikan HER exception uvicorn'u "Application startup
failed." ile sonlandirir:

    -> Dockerfile CMD `migrate_db && exec uvicorn` zinciri oldugu icin
       container cikar
    -> compose `restart: unless-stopped` ile SONSUZ CRASH-LOOP
    -> appliance tamamen karanlik; operator arayuze ulasip teshis bile
       yapamaz.

DEGISEN NE
----------
Eskiden bu dosya `create_tables` / `_legacy_bootstrap_ddl` ikilisini
koruyordu: acilista ~124 DDL ifadesi kosuyor ve hatasi yutuluyordu. O blok
0072 ile Alembic'e devredildi ve KALDIRILDI.

Korunan RISK aynen duruyor, yalnizca sahibi degisti. Bugun acilista kosan
iki kanca var ve ikisi de appliance'i karartmamali:

  * `dogrula_sema_uyumlulugu` — SALT OKUNUR sema kontrolu
  * `seed_fabrika_verisi`     — sinyal katalogu + fabrika config sablonu

Gercek bir sema sorunu zaten `scripts/migrate_db` tarafindan uvicorn
BASLAMADAN once, gurultulu bicimde yakalanir.
"""

from __future__ import annotations

import app.main as main_module


# --------------------------------------------------------------------------
# Sema kontrolu — okur, karartmaz
# --------------------------------------------------------------------------
def test_sema_kontrolu_startupi_DURDURMAZ(monkeypatch):
    """Kontrol patlasa bile acilis SURMELI."""
    from app.db import schema_guard

    def _boom(_engine):
        raise RuntimeError("simulasyon: sema kontrolu patladi")

    monkeypatch.setattr(schema_guard, "dogrula_ve_isaretle", _boom)

    try:
        main_module.dogrula_sema_uyumlulugu()
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"sema kontrolu startup'i durdurdu ({exc!r}) — crash-loop riski"
        ) from exc


def test_sema_kontrolu_DB_hatasinda_NOT_READY_yapmaz(monkeypatch):
    """DB'ye ulasilamamasi bir SEMA karari degildir.

    Baglanti hatasinda uygulamayi NOT READY'ye cakmak teshisi zorlastirirdi;
    `/health/ready` zaten ayri bir DB probe'u yapiyor.
    """
    from sqlalchemy.exc import OperationalError

    from app.db import schema_guard

    class _Kirik:
        def connect(self):
            raise OperationalError("SELECT 1", {}, Exception("baglanti koptu"))

    schema_guard.DURUM.update(uyumlu=True, sebep=None)
    schema_guard.dogrula_ve_isaretle(_Kirik())

    hazir, _ = schema_guard.hazir_mi()
    assert hazir is True, "baglanti hatasi sema uyumsuzlugu gibi raporlandi"


def test_sema_kontrolu_SEMA_DEGISTIRMEZ():
    """Kanca yalnizca DOGRULAR — `upgrade`/`stamp`/`create_all` YOK."""
    import inspect

    src = inspect.getsource(main_module.dogrula_sema_uyumlulugu)
    kod = "\n".join(
        satir for satir in src.splitlines() if not satir.strip().startswith("#")
    )
    # Docstring bilerek eski davranisi anlatiyor; govdeyi ayikla.
    govde = kod.split('"""')[-1]
    for yasak in ("create_all", "upgrade", "stamp", "ALTER TABLE", "CREATE TABLE"):
        assert yasak not in govde, f"startup sema kontrolu mutasyon iceriyor: {yasak}"


# --------------------------------------------------------------------------
# Tohumlama — eksik kalabilir, karartmamali
# --------------------------------------------------------------------------
def test_seed_hatasi_startupi_DURDURMAZ(monkeypatch, caplog):
    """Tohumlama patlarsa acilis DEVAM ETMELI ve hata LOGLANMALI.

    Bu koruma eskiden `create_tables`'in try/except'inden geliyordu; DDL
    blogu kaldirilinca `seed_fabrika_verisi` icinde ACIKCA yeniden kuruldu.
    Bu test o kaymanin sessizce geri gelmesini engeller.
    """
    import app.main as m

    def _boom(*_a, **_k):
        raise RuntimeError("simulasyon: seed patladi")

    monkeypatch.setattr(m, "SessionLocal", _boom)

    m.seed_fabrika_verisi()  # exception DISARI SIZMAMALI

    assert any(
        "fabrika_verisi_seed_basarisiz" in r.getMessage() for r in caplog.records
    ), "hata yutuldu ama LOGLANMADI — sessiz yutma kabul edilemez"


# --------------------------------------------------------------------------
# Kaldirilan legacy blok geri gelmemeli
# --------------------------------------------------------------------------
def test_legacy_bootstrap_DDL_geri_gelmedi():
    """`_legacy_bootstrap_ddl` / `create_tables` yeniden eklenmemeli.

    Sema otoritesi Alembic'tir (0072). Acilista DDL kosturan bir blok geri
    gelirse bu test duser.
    """
    assert not hasattr(main_module, "_legacy_bootstrap_ddl")
    assert not hasattr(main_module, "create_tables")
