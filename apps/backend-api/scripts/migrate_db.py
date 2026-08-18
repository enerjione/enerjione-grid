"""DB semasini legacy kurulumlardan Alembic head'e guvenle tasir."""

import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.session import engine

# Model'lerin import edilmesi Alembic autogenerate/`env.py` ile ayni tek
# kaynaktan (`app/models/__init__.py`) gelir. Sema ARTIK modellerden
# uretilmiyor (bkz. `_migrate_locked`), ama `alembic` yapilandirmasi ayni
# surecte yuklendigi icin kayitlarin eksiksiz olmasi teshis/autogenerate
# tarafinda onemini koruyor.
import app.models  # noqa: F401

log = logging.getLogger(__name__)

# Migration icin AYRI bir advisory lock anahtari.
# `service_role._BACKGROUND_LOCK_KEY` ile ayni OLMAMALI: o kilit surec omru
# boyunca tutulur, bu ise yalnizca migration suresince. Ayni anahtari
# paylassalardi migration biter bitmez liderlik de dusmus olurdu.
# Temiz kurulumda zincirin "uygulanmis" sayildigi taban revizyon. 0072
# guncel semanin tamamini kurdugu icin ondan ONCEKI zincir bos DB'de
# oynatilmaz (oynatilamaz da: 52 revizyon "UndefinedTable" ile kirilir).
# YENI MIGRATION EKLERKEN BU DEGERI ILERLETME — 0072 sonrasi her revizyon
# temiz kurulumda da GERCEKTEN kosmalidir.
_TEMIZ_KURULUM_TABANI = "0071"

_MIGRATION_LOCK_KEY = 0x0E1_6D_1167

# Kilidi beklerken ust sinir. Sinirsiz beklemek container'i "starting"
# durumunda SESSIZCE asardi: healthcheck henuz baslamadigi icin dusmez,
# hicbir hata gorunmez, kimse sebebini bilmez.
_MIGRATION_LOCK_WAIT_SEC = 600


def migrate() -> None:
    """Semayi head'e tasir — kumede TEK SUREC, digerleri bekler.

    NEDEN KILIT: `backend-api` ve `backend-worker` AYNI imaji ve AYNI
    komutu (`python -m scripts.migrate_db && exec uvicorn ...`) kullanir ve
    ikisi de `postgres: service_healthy` kosuluyla ES ZAMANLI acilir. Iki
    surec ayni anda `upgrade head` kosarsa ayni revizyon iki kez oynatilir:

        psycopg2.errors.DuplicateColumn: column ... already exists

    Container cikar, `restart: unless-stopped` ile donguye girer. Genelde
    kendini toparlar ama uzun bir migration'da gorunur kesinti ve teshisi
    cok zor bir hata olur.

    `pg_try_advisory_lock` DEGIL `pg_advisory_lock`: ikinci surec ATLAMAMALI,
    BEKLEMELI. Atlarsa uvicorn'u eski semayla acar ve ilk sorguda patlar.

    KILIT ALINAMAZSA PATLAR (yutulmaz): tek sebebi onceki migration'in
    asili kalmasidir ve o durumda kilitsiz devam etmek tam da onlemek
    istedigimiz es zamanli upgrade'i yapardi. Container cikar, sebep log'da
    goruntur — sessizce yanlis semayla acilmaktan iyidir.
    """
    if engine.dialect.name != "postgresql":
        # SQLite (testler/gelistirme): advisory lock yok, es zamanli ikinci
        # surec de yok. Kilitsiz kosmak dogru davranis.
        log.debug("advisory lock desteklenmeyen backend — migration kilitsiz kosuyor")
        _migrate_locked()
        return

    # AUTOCOMMIT: kilidi tutan baglanti migration boyunca "idle in
    # transaction" kalmamali — o durumda VACUUM ilerleyemez ve uzun bir
    # migration sirasinda tablo sismesi hizlanir.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_conn:
        # lock_timeout SADECE bu baglantiyi baglar; alembic kendi
        # baglantilarinda kosar ve bundan etkilenmez.
        lock_conn.execute(text(f"SET lock_timeout = '{_MIGRATION_LOCK_WAIT_SEC}s'"))
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY})
        try:
            _migrate_locked()
        finally:
            try:
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY}
                )
            except Exception:  # noqa: BLE001
                # Baglanti nasil olsa kapaniyor; session-level kilit onunla duser.
                log.debug("migration advisory unlock basarisiz", exc_info=True)


class SemaIleridedeHatasi(RuntimeError):
    """Veritabani semasi, bu imajin tanidigi migration'lardan ILERIDE."""


def _sema_ileride_mi_kontrol(config: Config) -> bool:
    """Sema kodun ilerisindeyse anlasilir hata verir; crash-loop uretmez.

    Donus: `True` ise migration ATLANMALI (operator devam etmeyi secti),
    `False` ise normal `upgrade` yolu surdurulur.

    SORUN
    -----
    Belgelenen geri alma yolu (`update.sh --version <eski>`) arada yeni bir
    migration yayinlanmissa backend'i KALICI crash-loop'a sokuyordu:
    `alembic_version` tablosunda YENI revizyon yazili, eski imajin
    `versions/` dizininde o dosya YOK, `command.upgrade` soyle patliyordu:

        alembic.util.exc.CommandError: Can't locate revision identified by '0064'

    Traceback opaktir: operator "geri aldim, sistem acilmiyor" der ve
    sebebini goremez. Container healthcheck'i dusurup surekli yeniden
    baslar.

    DAVRANIS
    --------
    Durum ONCEDEN tespit edilir ve NE YAPILACAGINI soyleyen bir hata
    uretilir. Islem yine durur — durmak DOGRU: sema kodun tanimadigi bir
    noktada ve otomatik "duzeltmek" (stamp/downgrade) veri kaybettirebilir.
    Degisen sey, hatanin ANLASILIR olmasi.

    `E1_ALLOW_SCHEMA_AHEAD=1` ile devam edilebilir: yalnizca EKLEMELI
    migration'lardan sonra (yeni kolon/tablo) guvenlidir — eski kod fazladan
    kolonu gormezden gelir. Kolon dusuren/yeniden adlandiran bir migration
    varsa bu bayrak veri hatasi uretir.
    """
    from alembic.script import ScriptDirectory

    with engine.connect() as conn:
        mevcut = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all()
    if not mevcut:
        return False

    script = ScriptDirectory.from_config(config)
    bilinen = {rev.revision for rev in script.walk_revisions()}
    yabanci = [r for r in mevcut if r not in bilinen]
    if not yabanci:
        return False

    mesaj = (
        "Veritabani semasi bu surumden ILERIDE: alembic_version "
        f"{yabanci} revizyon(lar)ini tasiyor ama bu imajda o migration "
        "dosyalari YOK. Buyuk olasilikla daha yeni bir surumden GERI "
        "DONULDU.\n"
        "Yapilacaklar (birini secin):\n"
        "  1) Yeniden ileri surume gecin (onerilen): "
        "sudo bash update.sh --version <yeni-surum>\n"
        "  2) Yukseltmeden ONCE alinan yedegi geri yukleyin, sonra bu "
        "surume donun.\n"
        "  3) Migration'lar yalnizca EKLEMELI ise (yeni kolon/tablo) "
        "E1_ALLOW_SCHEMA_AHEAD=1 ile devam edin — kolon dusuren bir "
        "migration varsa bu SECENEK VERI HATASI URETIR."
    )
    if os.getenv("E1_ALLOW_SCHEMA_AHEAD", "").strip() in {"1", "true", "yes"}:
        log.warning(
            "%s\nE1_ALLOW_SCHEMA_AHEAD ayarli — migration ATLANIYOR, "
            "uygulama mevcut sema ile aciliyor.",
            mesaj,
        )
        return True
    raise SemaIleridedeHatasi(mesaj)


def _migrate_locked() -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic_migrations"))
    if not inspect(engine).has_table("alembic_version"):
        # TEMIZ KURULUM — sema ALEMBIC'ten gelir, modelden DEGIL.
        #
        # ESKIDEN burada `Base.metadata.create_all()` + `stamp head` vardi:
        # sema SQLAlchemy modellerinden uretiliyor, Alembic yalnizca damga
        # atiyordu. Sema otoritesi fiilen modeldi ve migration gecmisi
        # semayi TARIF ETMIYORDU.
        #
        # NEDEN `stamp 0071` + `upgrade`, duz `upgrade head` DEGIL:
        # 0001 baseline'i BOS ve 39 tabloyu hicbir migration kurmuyor. Bos
        # bir DB'de zincir olculdu: 71 revizyonun 52'si "UndefinedTable" ile
        # kiriliyor ve geriye 8 tablo kaliyor. Yani 0001-0071 bos DB'de
        # GECILEMEZ. O gecmis DEGISTIRILMEDI; bunun yerine 0072 guncel
        # semanin tamamini explicit operasyonlarla kuruyor.
        #
        # Sonuc: 0071'e kadar olan zincir "zaten uygulanmis" sayilir (bos
        # DB icin dogru: o revizyonlarin tarif ettigi sema 0072 icinde),
        # ardindan 0072 semayi bastan kurar.
        command.stamp(config, _TEMIZ_KURULUM_TABANI)
        command.upgrade(config, "head")
    else:
        # MEVCUT kurulum: gercek gecmisi oynat.
        # Once sema bu imajin ILERISINDE mi bak (surum geri alindi mi):
        # oyleyse `upgrade` "Can't locate revision" ile patlar ve container
        # kalici crash-loop'a girer. Kontrol, opak traceback yerine ne
        # yapilacagini soyleyen bir hata uretir.
        if _sema_ileride_mi_kontrol(config):
            return
        command.upgrade(config, "head")

    # ---- Historian depolamasi: hypertable + saklama politikalari ----------
    #
    # SEMADAN AYRI BIR ADIM, cunku bunlar SQLAlchemy modelinde tarif
    # EDILEMEZ: Alembic operasyonlari (0072 dahil) YALNIZCA DUZ TABLOLARI
    # kurar; hypertable'a cevirme ve saklama politikasi ayri bir adimdir.
    # Hypertable'a cevirme ve 90 gunluk saklama politikasi migration
    # govdesinde yasadigi icin, hicbir migration kosmayan temiz kurulumda
    # sessizce ATLANIYORDU:
    #
    #     "Arsiv tablosu hypertable'a cevrilmemis — saklama suresi politikasi
    #      calismiyor, tablo sinirsiz buyuyor."
    #
    # Belirti disk dolana kadar ortaya cikmaz (600 cihazda gunde ~26M satir).
    #
    # HER ACILISTA kosuyor, yalnizca temiz kurulumda degil: extension ilk
    # boot'ta hazir olmayabilir ve sonradan dogru hale gelebilir. Adim
    # idempotent; zaten kuruluysa birkac katalog sorgusu maliyeti var.
    #
    # Hata YUTULUR: burada patlamak backend'in hic ayaga kalkmamasi demek.
    # Tespit garantisi ayri bir yerde — Sistem Durumu sayfasi eksikligi
    # raporluyor (`app/services/historian_service.py`).
    try:
        from app.db.timescale_setup import ensure_historian_storage

        with engine.begin() as bind:
            rapor = ensure_historian_storage(bind)
        if rapor.get("actions"):
            log.info("historian depolama kurulumu: %s", ", ".join(rapor["actions"]))
        elif rapor.get("skipped"):
            log.info("historian depolama kurulumu atlandi: %s", rapor["skipped"])
    except Exception as exc:  # noqa: BLE001
        log.warning("historian depolama kurulumu basarisiz (atlaniyor): %s", exc)


if __name__ == "__main__":
    migrate()
