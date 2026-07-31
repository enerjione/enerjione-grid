"""0021 BIGINT gecisi saha cihazini boot edemez hale getirmemeli (denetim A16).

YASANAN SORUN
-------------
`processed_messages.id` int4 -> int8 cevrimi tabloyu ve index'lerini YENIDEN
YAZAR ve bu sirada ACCESS EXCLUSIVE kilidi tutar. Migration'in ilk hali
ALTER oncesi budama yapiyordu ama budama 10M satirla (200 tur x 50.000)
TAVANLIYDI. Eski 7 gunluk TTL ile ~180M satira ulasmis bir tabloda ~170M
satir kaliyordu ve iki yoldan da cihaz ACILMIYORDU:

  (a) Budamanin urettigi olu satirlar autovacuum'u tetikler; autovacuum'un
      ShareUpdateExclusiveLock'i ALTER'in talebiyle catisir -> 30 sn'de
      lock_timeout -> exception -> `migrate_db` patlar -> backend
      CRASH-LOOP. Her yeniden baslatma ayni 10M'lik budamayi bastan yapar.
  (b) Kilit alinsa bile ~170M satirlik tablo+index yeniden yazimi diskte
      tablonun ~2 kati yer ister; bu dalin varlik sebebi zaten diskin
      dolmasiysa ENOSPC ile patlar.

Her iki durumda da uzaktan erisimin zor oldugu bir saha cihazi acilmaz.

COZUM: DELETE degil TRUNCATE. Tablo bosaltilir, ALTER BOS tabloda kosar,
sonra son 1 saat geri yazilir. Bu, (b)'yi ortadan kaldirir ve (a)'nin
sebebi olan olu satir yigini hic olusmaz.

BU TESTLERIN KORUDUGU SEY
-------------------------
Asagidaki ozelliklerden biri kaybolursa arizanin TAMAMI geri gelir; ustelik
sonucu ancak buyuk bir saha cihazinda gorulur, gelistirici makinesinde asla.
Bu yuzden davranisi kaynak duzeyinde sabitliyoruz.

Migration'in gercek PostgreSQL uzerindeki ucdan uca dogrulamasi ayrica
yapildi (620 -> 120 satir, kolon VE sequence bigint, index'ler saglam,
int4 tavani ustunde id uretimi).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic_migrations"
    / "versions"
    / "2026_07_31_0003-0021_widen_hot_table_pk_to_bigint.py"
)


@pytest.fixture(scope="module")
def kaynak() -> str:
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def agac(kaynak: str) -> ast.Module:
    return ast.parse(kaynak)


@pytest.fixture(scope="module")
def modul():
    """Migration modulunu import eder (alembic context'i olmadan).

    Modul govdesinde yalnizca sabitler ve fonksiyon tanimlari var; `op`
    cagrilari fonksiyon icinde oldugu icin import yan etkisiz.
    """
    spec = importlib.util.spec_from_file_location("m0021", MIGRATION)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fn(agac: ast.Module, ad: str) -> ast.FunctionDef:
    for d in ast.walk(agac):
        if isinstance(d, ast.FunctionDef) and d.name == ad:
            return d
    raise AssertionError(f"{ad} fonksiyonu bulunamadi")


def test_migration_dosyasi_BULUNDU():
    """Yol yanlissa diger testler sessizce yesil kalirdi."""
    assert MIGRATION.is_file(), f"bulunamadi: {MIGRATION}"


def test_tavanli_budama_KALDIRILDI(kaynak: str):
    """Asil ariza buydu: budama 10M satirda duruyordu.

    Tavan geri gelirse ~170M satir ALTER'a kalir ve cihaz acilmaz.
    """
    assert "_TRIM_MAX_BATCHES" not in kaynak, (
        "tavanli budama geri gelmis — buyuk tabloda ALTER yeniden yazacak "
        "veriyle bas basa kalir ve boot bloklanir"
    )


def test_buyuk_tablo_TRUNCATE_ile_bosaltiliyor(agac: ast.Module):
    """DELETE dongusu yerine TRUNCATE.

    Batch'li DELETE 170M satirda saatler surer, WAL sisirir ve olu satirlar
    VACUUM'a kadar DISKTE KALIR — yani ENOSPC riskini azaltmaz, artirir.
    TRUNCATE O(1)'dir ve alani aninda geri verir.
    """
    fn = _fn(agac, "_shrink_and_widen_processed_messages")
    govde = ast.dump(fn)
    assert "TRUNCATE" in govde, "buyuk tablo TRUNCATE ile bosaltilmiyor"
    assert "DELETE FROM processed_messages" not in govde, (
        "DELETE dongusu geri gelmis"
    )


def test_ALTER_bos_tabloda_kosuyor(agac: ast.Module):
    """Sira kritik: once TRUNCATE, SONRA genisletme, en son geri yazma.

    Sira bozulursa (or. once geri yazma) ALTER yine dolu tabloda kosar ve
    kazanimin tamami kaybolur — ustelik kod "duzeltilmis" gorunmeye devam
    eder.
    """
    fn = _fn(agac, "_shrink_and_widen_processed_messages")
    step = next(
        (d for d in ast.walk(fn) if isinstance(d, ast.FunctionDef) and d.name == "_step"),
        None,
    )
    assert step is not None, "_step bulunamadi"

    sira: list[str] = []
    for node in ast.walk(step):
        if not isinstance(node, ast.Call):
            continue
        adi = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if adi == "_widen_sql":
            sira.append("WIDEN")
        elif adi == "execute" and node.args:
            arg = node.args[0]
            metin = arg.value if isinstance(arg, ast.Constant) else ast.dump(arg)
            metin = str(metin)
            if "TRUNCATE" in metin:
                sira.append("TRUNCATE")
            elif "CREATE TEMP TABLE" in metin:
                sira.append("KEEP")
            elif "INSERT INTO processed_messages" in metin:
                sira.append("RESTORE")

    assert sira == ["KEEP", "TRUNCATE", "WIDEN", "RESTORE"], (
        f"adim sirasi beklenenden farkli: {sira}. Dogru sira: gecici tabloya "
        "al -> TRUNCATE -> BOS tabloda ALTER -> geri yaz."
    )


def test_kilit_tekrar_denemesi_SAVEPOINT_kullanir(agac: ast.Module):
    """Savepoint olmadan "tekrar deneme" fikri ilk denemede olur.

    PostgreSQL'de basarisiz bir ifade transaction'i "aborted" birakir;
    sonraki HER ifade `InFailedSqlTransaction` ile duser — alembic'in kendi
    `UPDATE alembic_version`'i dahil (0019/0023 ile ayni tuzak).
    """
    fn = _fn(agac, "_with_lock_retry")
    assert "begin_nested" in ast.dump(fn), (
        "_with_lock_retry SAVEPOINT kullanmiyor — ilk hata transaction'i "
        "abort eder ve tekrar deneme calisamaz"
    )


def test_tukenen_denemede_hata_FIRLATILIR(agac: ast.Module):
    """Hata YUTULMAMALI — 0019/0023'un aksine.

    Alembic basarili bir upgrade'i DAMGALAR ve bir daha kosmaz. Yutsaydik
    sayac genisletme HIC yapilmayacak, cihaz ~83 gun sorunsuz calisip sonra
    telemetri alimini sessizce durduracakti. Tablo artik TRUNCATE sayesinde
    bos oldugu icin yeniden deneme ucuz ve kendi kendini cozer.
    """
    fn = _fn(agac, "_with_lock_retry")
    handlers = [d for d in ast.walk(fn) if isinstance(d, ast.ExceptHandler)]
    assert handlers, "_with_lock_retry hic hata yakalamiyor"
    assert any(
        isinstance(alt, ast.Raise) for h in handlers for alt in ast.walk(h)
    ), "_with_lock_retry hatayi yutuyor — sayac genisletme sessizce atlanir"


def test_yalnizca_KILIT_hatasi_tekrar_denenir(modul):
    """Bozuk bir ALTER'i alti kez denemek sadece boot'u geciktirir.

    Davranis testi: gercek psycopg2 hata nesnesi olmadan da sinifllandirma
    dogru calismali (pgcode ve mesaj yolu).
    """

    class _Orig:
        def __init__(self, pgcode: str) -> None:
            self.pgcode = pgcode

    class _Exc(Exception):
        def __init__(self, mesaj: str, pgcode: str | None = None) -> None:
            super().__init__(mesaj)
            self.orig = _Orig(pgcode) if pgcode else None

    # 55P03 = lock_not_available -> tekrar denenmeli
    assert modul._is_lock_timeout(_Exc("bir sey", pgcode="55P03"))
    # mesaj yolu (surucu pgcode vermezse)
    assert modul._is_lock_timeout(
        _Exc("canceling statement due to lock timeout")
    )
    # 57014 = query_canceled (statement_timeout) -> tekrar DENENMEMELI
    assert not modul._is_lock_timeout(_Exc("iptal", pgcode="57014"))
    # gercek sema hatasi -> tekrar DENENMEMELI
    assert not modul._is_lock_timeout(_Exc('column "id" does not exist'))


def test_korunan_pencere_redelivery_penceresinden_GENIS(modul):
    """Geri yazilan pencere, gercek redelivery penceresini kapsamali.

    ack_wait(60sn) x max_deliver(10) = 10 dakika. Bundan dar bir pencere
    idempotency defterini islevsiz birakir ve mukerrer islemeye yol acar.
    """
    assert modul._KEEP_INTERVAL == "1 hour", (
        f"korunan pencere degismis: {modul._KEEP_INTERVAL!r} — 10 dakikalik "
        "redelivery penceresinden belirgin olcude genis kalmali"
    )


def test_disk_kontrolu_icin_shutil_KULLANILMIYOR(agac: ast.Module):
    """`shutil.disk_usage` bu kodda YANILTICI olurdu.

    Migration backend container'inda kosuyor, veritabani AYRI container'da.
    Backend'in gordugu dosya sistemi postgres-data volume'u DEGILDIR; oradan
    okunan "bos alan" ilgisiz bir sayidir ve ona gore alinan karar yanlistir.
    Dogru koruma yeniden yazilacak veriyi kucultmektir (TRUNCATE).

    Kontrol AST uzerinden: metin aramasi bu dosyanin KENDI aciklamasina
    takiliyor ve testi anlamsizca kirmizi yapiyordu.
    """
    for node in ast.walk(agac):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "shutil" for a in node.names), (
                "migration `shutil` import ediyor — bos alan olcumu backend "
                "container'inin dosya sisteminden okunur, DB'ninkinden DEGIL"
            )
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "shutil", (
                "migration `shutil`'den import ediyor"
            )
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert node.value.id != "shutil", "migration `shutil.*` cagiriyor"
