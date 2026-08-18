"""Migration/integration testleri icin GUVENLI hedef veritabani secimi.

NEDEN VAR — YASANMIS OLAY
-------------------------
0071 testleri gelistirilirken bir kez `alembic upgrade head` GELISTIRICININ
KENDI veritabanina (`localhost:5432/enerjione_grid`) kostu ve onu 0063'ten
0071'e tasidi. Sebep: `alembic_migrations/env.py` `sqlalchemy.url`'i
KOSULSUZ olarak `settings.database_url` ile ezer. Testte
`cfg.set_main_option("sqlalchemy.url", ...)` demek YETMEZ — sessizce yok
sayilir ve migration yanlis hedefte calisir.

Bu modul o hatayi bir daha MUMKUN KILMAZ. Migration kostur(ma)dan once
hedefin gercekten gecici bir test veritabani oldugu FAIL-CLOSED dogrulanir.

TEK KAYNAK: test PG adresi yalnizca `E1_TEST_PG_URL` ortam degiskeninden
gelir. Ortulu varsayilan, `settings.database_url` yedegi ve `localhost`
tahmini YOKTUR.

GUNCELLEME (Alembic Schema Authority): `env.py` artik hedefi ACIK bir
precedence ile secer ve acikca verilen URL'i EZMEZ. Bu modulun guard'i yine de
durur — precedence dogru hedefi SECER, guard ise hedefin gercekten gecici bir
test DB'si oldugunu DOGRULAR. Ikisi farkli isler yapar.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

#: Test veritabani adlarinin ZORUNLU oneki. Guard'in ilk katmani.
DB_ONEK = "e1_test_"

#: Ortam degiskeni — test PG adresinin TEK kaynagi.
ENV_ADI = "E1_TEST_PG_URL"

#: Hicbir kosulda migration hedefi olamayacak adlar.
YASAK_ADLAR = frozenset({
    "enerjione_grid",   # gelistirici / uretim veritabani
    "postgres",
    "template0",
    "template1",
})

#: BU KOSUDA bu modul tarafindan olusturulmus veritabanlari. Guard'in en
#: guclu katmani: adi uydurulmus ama bizim yaratmadigimiz bir hedefe
#: migration kosturulamaz.
_OLUSTURULANLAR: set[str] = set()


class UnsafeMigrationTarget(RuntimeError):
    """Hedef veritabani gecici bir test DB'si DEGIL — migration KOSMADI."""


def pg_url() -> str:
    """Test PG adresi. Tanimli degilse bos string (cagiran skip eder)."""
    return os.getenv(ENV_ADI, "")


def _db_adi(url: str) -> str:
    yol = urlparse(url).path or ""
    return yol.lstrip("/").split("?", 1)[0]


def url_for(db_adi: str) -> str:
    """Temel URL'in veritabani bolumunu `db_adi` ile degistirir."""
    temel = pg_url()
    if not temel:
        raise UnsafeMigrationTarget(f"{ENV_ADI} tanimli degil")
    return re.sub(r"/[^/?]+(\?|$)", f"/{db_adi}\\1", temel, count=1)


def yeni_db_adi(etiket: str) -> str:
    """Zorunlu oneki tasiyan, bu kosuya ozgu bir test DB adi uretir."""
    temiz = re.sub(r"[^a-z0-9_]", "_", etiket.lower())
    return f"{DB_ONEK}{temiz}_{os.getpid()}"


def kaydet_olusturuldu(db_adi: str) -> None:
    """Bu modul disinda olusturulan test DB'sini kayit defterine ekler."""
    _OLUSTURULANLAR.add(db_adi)


def unut(db_adi: str) -> None:
    _OLUSTURULANLAR.discard(db_adi)


def hedefi_dogrula(url: str) -> None:
    """FAIL-CLOSED guard. Hedef guvenli DEGILSE istisna firlatir.

    Katmanlar — TEK BASINA isim kontrolu yeterli degildir, hepsi birlikte:

    1. `E1_TEST_PG_URL` tanimli olmali (acik opt-in).
    2. Hedef DB adi `e1_test_` onekini tasimali.
    3. Ad yasak listede olmamali.
    4. Hedef, bu modulun BU KOSUDA olusturdugu bir DB olmali. En guclu
       katman: dogru desende bir ad uydurmak yetmez.
    5. Hedef (host, port, db) uclusu `settings.database_url` ile AYNI
       OLMAMALI — gelistirici DB'si tesadufen desene uysa bile reddedilir.
    """
    temel = pg_url()
    if not temel:
        raise UnsafeMigrationTarget(
            f"{ENV_ADI} tanimli degil — migration testi ortulu hedefe KOSAMAZ"
        )

    ad = _db_adi(url)
    if not ad:
        raise UnsafeMigrationTarget(f"hedef URL'de veritabani adi yok: {url!r}")
    if ad in YASAK_ADLAR:
        raise UnsafeMigrationTarget(f"yasak migration hedefi: {ad!r}")
    if not ad.startswith(DB_ONEK):
        raise UnsafeMigrationTarget(
            f"test veritabani adi {DB_ONEK!r} ile baslamali, gelen: {ad!r}"
        )
    if ad not in _OLUSTURULANLAR:
        raise UnsafeMigrationTarget(
            f"{ad!r} bu kosuda test altyapisi tarafindan OLUSTURULMADI — "
            "migration yalnizca kendi urettigimiz gecici DB'de kosabilir"
        )

    from app.core.config import settings

    if _ayni_hedef(url, settings.database_url):
        raise UnsafeMigrationTarget(
            "hedef, uygulamanin yapilandirilmis veritabani ile AYNI — "
            "migration testi gelistirici/uretim semasina DOKUNAMAZ"
        )


def _ayni_hedef(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (
        (pa.hostname or "") == (pb.hostname or "")
        and (pa.port or 5432) == (pb.port or 5432)
        and _db_adi(a) == _db_adi(b)
    )


#: `settings.database_url` icin ZEHIRLI yedek. Bilerek baglanilamaz: port 1'de
#: dinleyen yok, ad da neyin yanlis gittigini soyluyor.
ZEHIRLI_YEDEK = "postgresql+psycopg2://e1_guard@127.0.0.1:1/e1_guard_KULLANILMAMALI"


def alembic_config(url: str, monkeypatch):  # noqa: ANN001
    """Dogrulanmis hedefe bagli Alembic yapilandirmasi uretir.

    IKI YAN ETKI ACIKCA ELE ALINIR:

    1. HEDEF SECIMI. `env.py` artik ACIK precedence uygular: acikca verilen
       `sqlalchemy.url` > `E1_MIGRATION_DATABASE_URL` > `settings.database_url`.
       Burada hedef 1. siradan verilir.

       `settings.database_url` HEDEFE CEKILMEZ — bilerek ZEHIRLENIR. Eskiden
       hedefe cekiliyordu (env.py kosulsuz ezdigi icin mecburdu) ama bu,
       regresyonu MASKELERDI: env.py yeniden `settings` ile ezmeye baslasa
       bile iki deger ayni oldugu icin test yine gecerdi.

       Zehirli yedek ile davranis gozlemlenebilir olur:
         * env.py DOGRU  -> acik URL kullanilir, test GECER.
         * env.py REGRESE -> yedege duser, baglanti REDDEDILIR, test PATLAR
           ve gelistirici/uretim veritabanina ASLA dokunulmaz.

    2. LOGGING. `env.py` `fileConfig(config.config_file_name)` cagirir; bu,
       `disable_existing_loggers=True` varsayilaniyla UYGULAMANIN TUM
       LOGGER'LARINI SUSTURUR ve ayni pytest surecinde sonradan kosan
       `caplog` testleri hicbir kayit goremez (ALAKASIZ testler duser).
       `config_file_name = None` birakilarak o dal DETERMINISTIK olarak
       atlanir; `script_location` elle veriliyor.

    Guard once kosar: dogrulama basarisizsa Config bile kurulmaz.
    """
    hedefi_dogrula(url)

    from alembic.config import Config
    from app.core.config import settings as _s

    monkeypatch.setattr(_s, "database_url", ZEHIRLI_YEDEK, raising=False)
    # Ortam degiskeni yedegi de kapatilir: precedence'in 2. basamagi testin
    # acik hedefini golgelememeli.
    monkeypatch.delenv("E1_MIGRATION_DATABASE_URL", raising=False)

    ham = Config("alembic.ini")
    script_location = ham.get_main_option("script_location")

    cfg = Config()  # dosyaya BAGLI DEGIL -> fileConfig cagrilmaz
    cfg.set_main_option("script_location", script_location)
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg
