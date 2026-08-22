"""Migration 0078 — GERCEK PostgreSQL uzerinde `int4 -> int8`.

NEDEN SQLITE YETMEZ
-------------------
`test_command_identity.py` ve `test_migration_idempotans.py` SQLite'ta
kosuyor ve 0078 orada ERKEN DONER: SQLite'ta `INTEGER PRIMARY KEY` zaten
64-bit'tir ve `ALTER COLUMN TYPE` desteklenmez. Yani migration'in ASIL
govdesi — `ALTER TABLE ... TYPE BIGINT` ve `ALTER SEQUENCE ... AS bigint` —
SQLite kosumlarinda HIC CALISMIYOR.

Sahadaki hata tam da o govdenin calismamasindan cikti: uretimdeki
2.109.1 imajinda 0078 YOKTU, `device_commands.id` int4 kaldi, restore
sonrasi sequence geri dondu ve 43/44 gibi DAGITILMIS kimlikler yeniden
verildi. Gateway defteri o kimlikleri completed biliyordu; fiziksel islemi
hakli olarak tekrarlamadi ve eski ACK'i dondurdu -> `token_mismatch`.

Bu dosya migration'in KENDI fonksiyonunu (kopyasini degil) gercek bir
PostgreSQL baglantisinda surer.

KURULUM
-------
`E1_TEST_PG_URL` tanimli degilse ATLANIR (bkz. conftest).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

KOK = Path(__file__).resolve().parents[2]
GOC_YOLU = (
    KOK / "alembic_migrations/versions/2026_08_21_0004-0078_command_id_bigint.py"
)


def _goc_modulu():
    """Migration dosyasini modul olarak yukler — govde KOPYALANMAZ."""
    spec = importlib.util.spec_from_file_location("goc_0078", GOC_YOLU)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def pg():
    url = os.getenv("E1_TEST_PG_URL")
    if not url:
        pytest.skip("E1_TEST_PG_URL tanimli degil")
    eng = create_engine(url, future=True)
    with eng.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS e1_0078 CASCADE"))
        conn.execute(text("CREATE SCHEMA e1_0078"))
        conn.execute(text("SET search_path TO e1_0078"))
        conn.commit()
        conn.execute(text("SET search_path TO e1_0078"))
        yield conn
        conn.rollback()
        conn.execute(text("DROP SCHEMA IF EXISTS e1_0078 CASCADE"))
        conn.commit()
    eng.dispose()


def _op(conn):
    """Migration'in bekledigi `op` nesnesi, verilen baglantiya bagli."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    return Operations(MigrationContext.configure(conn))


def _yukselt(conn, monkeypatch):
    mod = _goc_modulu()
    monkeypatch.setattr(mod, "op", _op(conn))
    mod.upgrade()
    return mod


def _dusur(conn, monkeypatch):
    mod = _goc_modulu()
    monkeypatch.setattr(mod, "op", _op(conn))
    mod.downgrade()


def _tip(conn, tablo: str, kolon: str) -> str:
    return conn.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='e1_0078' AND table_name=:t AND column_name=:k"
        ),
        {"t": tablo, "k": kolon},
    ).scalar()


def _kolon_default(conn, tablo: str = "device_commands", kolon: str = "id") -> str | None:
    return conn.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema='e1_0078' AND table_name=:t AND column_name=:k"
        ),
        {"t": tablo, "k": kolon},
    ).scalar()


def _sequence_tipi(conn, ad: str = "device_commands_id_seq") -> str | None:
    """Belirli bir sequence'in tipi.

    ADA GORE SORULUR: semada iki sequence var (`device_commands_id_seq` ve
    `device_config_applications_id_seq`) ve isimsiz bir sorgu hangisini
    dondurecegini SOYLEMEZ. Ilk yazimda oyleydi ve test, migration dogru
    calisirken bile rastgele kirmizi/yesil oluyordu.

    Yalnizca `device_commands_id_seq` genisletilir — otekinin kendi `id`si
    hala SERIAL'dir ve oyle kalmalidir.
    """
    return conn.execute(
        text(
            "SELECT data_type FROM information_schema.sequences "
            "WHERE sequence_schema='e1_0078' AND sequence_name=:ad"
        ),
        {"ad": ad},
    ).scalar()


def _yukseltilmis_kurulum(conn) -> None:
    """0078 ONCESI sema: SERIAL int4 + int4 FK + index.

    Sahadaki 2.109.1 kurulumunun aynisi. `id` SERIAL'dir, yani arkasinda
    int4 bir sequence vardir — 0021'in belgeledigi tuzak burada yasiyor.
    """
    conn.execute(text("SET search_path TO e1_0078"))
    conn.execute(
        text(
            """
            CREATE TABLE device_commands (
                id SERIAL PRIMARY KEY,
                gateway_code VARCHAR(50) NOT NULL,
                device_code VARCHAR(50) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending'
            )
            """
        )
    )
    conn.execute(text("CREATE INDEX ix_dc_gateway ON device_commands (gateway_code)"))
    conn.execute(
        text(
            """
            CREATE TABLE device_config_applications (
                id SERIAL PRIMARY KEY,
                device_id INTEGER NOT NULL,
                command_id INTEGER REFERENCES device_commands(id),
                state VARCHAR(32) NOT NULL
            )
            """
        )
    )
    # SAHADAKI GECMIS: 1..44. Gateway defteri de bu kimlikleri biliyor.
    for i in range(1, 45):
        conn.execute(
            text(
                "INSERT INTO device_commands (id, gateway_code, device_code, status) "
                "VALUES (:i, 'GW-002', 'SN2-001', 'completed')"
            ),
            {"i": i},
        )
    conn.execute(text("SELECT setval('device_commands_id_seq', 44, true)"))
    conn.execute(
        text(
            "INSERT INTO device_config_applications (device_id, command_id, state) "
            "VALUES (7, 44, 'dogrulandi')"
        )
    )
    conn.commit()
    conn.execute(text("SET search_path TO e1_0078"))


def _temiz_kurulum(conn) -> None:
    """`create_all` sonrasi sema: BIGINT, sequence YOK (autoincrement=False)."""
    conn.execute(text("SET search_path TO e1_0078"))
    conn.execute(
        text(
            """
            CREATE TABLE device_commands (
                id BIGINT PRIMARY KEY,
                gateway_code VARCHAR(50) NOT NULL,
                device_code VARCHAR(50) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending'
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE device_config_applications (
                id SERIAL PRIMARY KEY,
                device_id INTEGER NOT NULL,
                command_id BIGINT REFERENCES device_commands(id),
                state VARCHAR(32) NOT NULL
            )
            """
        )
    )
    conn.commit()
    conn.execute(text("SET search_path TO e1_0078"))


# ===========================================================================
# YUKSELTILMIS KURULUM — asil senaryo
# ===========================================================================


def test_yukseltilmis_kurulumda_KOLONLAR_bigint_olur(pg, monkeypatch):
    _yukseltilmis_kurulum(pg)
    assert _tip(pg, "device_commands", "id") == "integer"
    assert _tip(pg, "device_config_applications", "command_id") == "integer"

    _yukselt(pg, monkeypatch)

    assert _tip(pg, "device_commands", "id") == "bigint"
    assert _tip(pg, "device_config_applications", "command_id") == "bigint", (
        "FK genisletilmedi — yeni kimlik tasiyan komuta baglanan niyet kaydi "
        "'integer out of range' ile patlardi"
    )


def test_ESKI_KIMLIK_OTORITESI_SOKULUR(pg, monkeypatch):
    """Kolonu genisletmek YETMEZ — sequence sokulmeli.

    OLCULDU: sadece `ALTER COLUMN TYPE BIGINT` yapilan bir tabloda
    `DEFAULT nextval(...)` yerinde kalir ve kimliksiz bir INSERT `id=1`
    uretir. Yani sahadaki arizanin kaynagi (kucuk kimlik -> gateway
    defteriyle cakisma -> token_mismatch) genisletilmis semada da hayatta
    kalir.
    """
    _yukseltilmis_kurulum(pg)
    assert _sequence_tipi(pg) == "integer"

    _yukselt(pg, monkeypatch)

    assert _sequence_tipi(pg) is None, "eski sequence hala duruyor"
    assert _kolon_default(pg) is None, "kolonda hala nextval varsayilani var"


def test_KIMLIKSIZ_insert_ARTIK_REDDEDILIR(pg, monkeypatch):
    """Kimligi vermeyen bir yazma yolu sessizce kucuk kimlik URETEMEZ.

    Genisletmeden once bu INSERT `id=1` uretiyordu. Artik NOT NULL ihlali
    ile REDDEDILIR: gurultulu bir hata, sessiz bir cakismadan iyidir.
    """
    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)
    with pytest.raises(Exception) as hata:
        pg.execute(
            text(
                "INSERT INTO device_commands (gateway_code, device_code) "
                "VALUES ('GW-002', 'SN2-001')"
            )
        )
    assert "null value" in str(hata.value).lower() or "not-null" in str(hata.value).lower()
    pg.rollback()
    pg.execute(text("SET search_path TO e1_0078"))


def test_TEMIZ_kurulumla_SEMA_PARITESI(pg, monkeypatch):
    """Yukseltilmis ve temiz kurulum AYNI kolon tanimini uretmeli.

    Ayrisirlarsa iki farkli uretim semasi olusur ve A15 parite testi
    (`test_sema_parity_pg`) kirmizi olur.
    """
    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)
    yukseltilmis = (_tip(pg, "device_commands", "id"), _kolon_default(pg))

    pg.execute(text("DROP TABLE device_config_applications"))
    pg.execute(text("DROP TABLE device_commands"))
    pg.commit()
    pg.execute(text("SET search_path TO e1_0078"))
    _temiz_kurulum(pg)
    temiz = (_tip(pg, "device_commands", "id"), _kolon_default(pg))

    assert yukseltilmis == temiz, (
        f"yukseltilmis {yukseltilmis} != temiz {temiz}"
    )


def test_MEVCUT_kimlikler_KORUNUR(pg, monkeypatch):
    """Genisletme kayipsizdir; 1..44 aynen okunmaya devam eder."""
    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)
    satirlar = pg.execute(
        text("SELECT id FROM device_commands ORDER BY id")
    ).scalars().all()
    assert satirlar == list(range(1, 45))


def test_FK_ve_INDEX_korunur(pg, monkeypatch):
    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)

    fk = pg.execute(
        text(
            "SELECT count(*) FROM information_schema.table_constraints "
            "WHERE table_schema='e1_0078' AND table_name='device_config_applications' "
            "AND constraint_type='FOREIGN KEY'"
        )
    ).scalar()
    assert fk == 1, "FK kayboldu"

    idx = pg.execute(
        text("SELECT indexname FROM pg_indexes WHERE schemaname='e1_0078' "
             "AND tablename='device_commands'")
    ).scalars().all()
    assert "ix_dc_gateway" in idx, "index kayboldu"
    # Iliski hala zorlaniyor mu?
    with pytest.raises(Exception):
        pg.execute(
            text(
                "INSERT INTO device_config_applications (device_id, command_id, state) "
                "VALUES (9, 999999, 'kuyrukta')"
            )
        )
    pg.rollback()
    pg.execute(text("SET search_path TO e1_0078"))


def test_BUYUK_kimlik_genisletmeden_SONRA_yazilabilir(pg, monkeypatch):
    """Asil kabul olcutu: ~1.79e15 kimlik int4'e SIGMAZ."""
    from app.services import command_identity

    _yukseltilmis_kurulum(pg)

    kimlik = command_identity.yeni_kimlik()
    assert kimlik > 2_147_483_647, "uretici int4'e sigan bir deger verdi"

    with pytest.raises(Exception):
        pg.execute(
            text(
                "INSERT INTO device_commands (id, gateway_code, device_code) "
                "VALUES (:i, 'GW-002', 'SN2-001')"
            ),
            {"i": kimlik},
        )
    pg.rollback()
    pg.execute(text("SET search_path TO e1_0078"))

    _yukselt(pg, monkeypatch)

    pg.execute(
        text(
            "INSERT INTO device_commands (id, gateway_code, device_code) "
            "VALUES (:i, 'GW-002', 'SN2-001')"
        ),
        {"i": kimlik},
    )
    pg.execute(
        text(
            "INSERT INTO device_config_applications (device_id, command_id, state) "
            "VALUES (7, :i, 'kuyrukta')"
        ),
        {"i": kimlik},
    )
    okunan = pg.execute(
        text("SELECT command_id FROM device_config_applications WHERE command_id=:i"),
        {"i": kimlik},
    ).scalar()
    assert okunan == kimlik, "buyuk kimlik kirpildi"


# ===========================================================================
# IDEMPOTANS — temiz kurulum, tekrar kosum
# ===========================================================================


def test_TEMIZ_kurulumda_patlamaz(pg, monkeypatch):
    """`create_all` + `stamp head` semasinda kolonlar ZATEN bigint ve
    sequence YOK. Migration atlamali, hata vermemeli."""
    _temiz_kurulum(pg)
    _yukselt(pg, monkeypatch)
    assert _tip(pg, "device_commands", "id") == "bigint"
    # Temiz kurulumda `autoincrement=False` oldugu icin komut tablosunun
    # sequence'i HIC yoktur; migration'in atlamasi beklenir.
    assert _sequence_tipi(pg) is None


def test_TEKRAR_kosumda_patlamaz(pg, monkeypatch):
    """Migration retry: yarida kalan bir upgrade tekrar kosulabilmeli."""
    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)
    _yukselt(pg, monkeypatch)
    _yukselt(pg, monkeypatch)
    assert _tip(pg, "device_commands", "id") == "bigint"
    assert _sequence_tipi(pg) is None


def test_TABLO_YOKKEN_atlanir(pg, monkeypatch):
    """Kismi sema (config apply tablosu henuz yok) migration'i dusurmemeli."""
    pg.execute(text("SET search_path TO e1_0078"))
    pg.execute(
        text(
            "CREATE TABLE device_commands (id SERIAL PRIMARY KEY, "
            "gateway_code VARCHAR(50) NOT NULL, device_code VARCHAR(50) NOT NULL)"
        )
    )
    pg.commit()
    pg.execute(text("SET search_path TO e1_0078"))
    _yukselt(pg, monkeypatch)
    assert _tip(pg, "device_commands", "id") == "bigint"


# ===========================================================================
# DOWNGRADE — veri kaybi riski
# ===========================================================================


def test_DOWNGRADE_SERIAL_davranisini_GERI_KURAR(pg, monkeypatch):
    """Geri alinan kurulumda eski kod kimligi VERMEZ; sequence olmazsa
    tablo hic yazilamazdi."""
    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)
    _dusur(pg, monkeypatch)

    assert _tip(pg, "device_commands", "id") == "integer"
    assert _sequence_tipi(pg) == "integer"
    assert _kolon_default(pg) is not None, "nextval varsayilani geri gelmedi"

    # Eski kodun yazma yolu: kimliksiz INSERT calismali ve gecmisin USTUNDEN
    # devam etmeli (44 vardi -> 45).
    pg.execute(
        text(
            "INSERT INTO device_commands (gateway_code, device_code) "
            "VALUES ('GW-002', 'SN2-001')"
        )
    )
    yeni_id = pg.execute(text("SELECT max(id) FROM device_commands")).scalar()
    assert yeni_id == 45, f"sequence gecmisin ustunden devam etmedi: {yeni_id}"


def test_DOWNGRADE_buyuk_kimlik_VARKEN_REDDEDILIR(pg, monkeypatch):
    """Sessizce veri kirpmaktansa geri alma BASARISIZ olmali.

    Bu, downgrade'in bir kusuru DEGIL; dogru davranisi. Yeni uretici
    ~1.79e15 yaziyor ve o deger int4'e sigmaz.
    """
    from app.services import command_identity

    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)
    pg.execute(
        text(
            "INSERT INTO device_commands (id, gateway_code, device_code) "
            "VALUES (:i, 'GW-002', 'SN2-001')"
        ),
        {"i": command_identity.yeni_kimlik()},
    )
    with pytest.raises(Exception) as hata:
        _dusur(pg, monkeypatch)
    assert "out of range" in str(hata.value).lower()
    pg.rollback()
    pg.execute(text("SET search_path TO e1_0078"))


# ===========================================================================
# GERI ALMA GUVENLIGI — 0078 sonrasi ESKI backend (2.109.1) ne yapar?
# ===========================================================================
#
# "Container rollback mumkun" demek TEK BASINA yeterli degil. 0078 semayi
# ILERI tasiyor; sorulmasi gereken sey eski UYGULAMANIN o semayla ne
# yapacagi (schema-forward / application-backward uyumluluk).
#
# 2.109.1 modelinde `id` su sekildeydi:
#     id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
# yani `autoincrement` ACIK ve SQLAlchemy INSERT'e `id` KOYMAZ; degeri
# veritabanindan bekler.


def _eski_backend_insert(conn):
    """2.109.1'in yazma yolu: kimlik VERILMEZ, DB uretsin diye beklenir."""
    conn.execute(
        text(
            "INSERT INTO device_commands (gateway_code, device_code) "
            "VALUES ('GW-002', 'SN2-001')"
        )
    )


def test_GERI_ALMA_eski_backend_KOMUT_YAZAMAZ(pg, monkeypatch):
    """0078 sonrasi 2.109.1'e donulurse komut kuyruklama HEMEN duser.

    Bu bir kusur DEGIL, bilincli sonuc: sequence sokuldugu icin kimligi
    vermeyen bir INSERT NOT NULL ihlaliyle reddedilir. Alternatif —
    sequence'i birakmak — eski backend'in yeniden kucuk kimlik uretmesine
    ve sahadaki arizanin AYNEN geri gelmesine izin verirdi.

    GURULTULU BASARISIZLIK, SESSIZ CAKISMADAN IYIDIR.
    """
    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)

    with pytest.raises(Exception) as hata:
        _eski_backend_insert(pg)
    mesaj = str(hata.value).lower()
    assert "null value" in mesaj or "not-null" in mesaj
    pg.rollback()
    pg.execute(text("SET search_path TO e1_0078"))


def test_GERI_ALMA_eski_backend_OKUYABILIR(pg, monkeypatch):
    """Okuma yolu bozulmaz: bigint kolon eski kodda da okunur.

    Yani geri alinan bir kurulum ekranlari gosterir, gecmisi listeler;
    yalnizca YENI komut uretemez. Ariza teshisi icin bu ayrim onemli.
    """
    from app.services import command_identity

    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)
    buyuk = command_identity.yeni_kimlik()
    pg.execute(
        text(
            "INSERT INTO device_commands (id, gateway_code, device_code) "
            "VALUES (:i, 'GW-002', 'SN2-001')"
        ),
        {"i": buyuk},
    )
    hepsi = pg.execute(text("SELECT id FROM device_commands ORDER BY id")).scalars().all()
    assert hepsi[:3] == [1, 2, 3], "eski kucuk kimlikler okunamiyor"
    assert hepsi[-1] == buyuk, "buyuk kimlik okunamiyor"


def test_GERI_ALMA_downgrade_sonrasi_ESKI_backend_CALISIR(pg, monkeypatch):
    """Kontrollu geri alma yolu: 0078 downgrade + eski imaj.

    `downgrade()` sequence'i geri kurdugu icin eski backend yeniden yazabilir.
    ANCAK bu, kimlik otoritesini de sequence'e geri verir — yani arizanin
    kosullari yeniden olusur. Bu test o gercegi GORUNUR kilar.
    """
    _yukseltilmis_kurulum(pg)
    _yukselt(pg, monkeypatch)
    _dusur(pg, monkeypatch)

    _eski_backend_insert(pg)
    yeni_id = pg.execute(text("SELECT max(id) FROM device_commands")).scalar()
    assert yeni_id == 45, "downgrade sonrasi eski yazma yolu calismiyor"
    # Ve iste risk: kimlik yine KUCUK, yani gateway defteriyle ayni uzayda.
    assert yeni_id < 2_147_483_647
