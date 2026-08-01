"""Outbox indeks/saklama rejimi — olcumle alinan kararlar geri kaymasin.

OLCUM (saha test cihazi, 2 cekirdek, 8 cihaz, ~90 ekleme/sn)
------------------------------------------------------------
`outbox_events` bir saatte 326.027 satira / 272 MB'a cikti ve veritabaninin
EN BUYUK tablosu oldu — telemetrinin kendisi 65 MB. Postgres bostayken bile
CPU'nun ~%25'ini yiyordu, disk 8,9 GB bosla 68% doluydu.

`pg_stat_user_indexes` iki indeksin 326 bin ekleme boyunca HIC taranmadigini
gosterdi (`created_at` 14 MB, `topic` 4 MB) ve `published` boolean indeksinin
secicilik degeri yoktu (satirlarin neredeyse tamami `true`).

BU TESTLER NEYI KORUR
---------------------
Indeks ve saklama ayarlari "her ihtimale karsi" geri eklenmeye cok musait
seylerdir; ikisi de sessizce maliyet uretir ve etkisi ancak aylar sonra dolu
bir diskte gorunur. Karar ile gerekcesini birlikte kilitliyoruz.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.outbox_event import OutboxEvent

VERSIONS = Path(__file__).resolve().parents[1] / "alembic_migrations" / "versions"
MIGRATION = VERSIONS / "2026_08_01_0005-0031_outbox_index_diet.py"


def _migration_kaynak() -> str:
    assert MIGRATION.is_file(), f"migration bulunamadi: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


def _kod(metin: str) -> str:
    """Yorum satirlarini eler — kontroller KOD'a bakmali.

    Bu depoda metin aramasi birkac kez kendi aciklamalarina takildi; burada
    ozellikle riskli, cunku migration'in docstring'i kaldirilan indekslerin
    ADLARINI aciklama amaciyla iceriyor."""
    metin = re.sub(r'""".*?"""', "", metin, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", metin, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sutun", ["topic", "created_at"])
def test_taranmayan_sutunlarda_INDEKS_YOK(sutun: str):
    """Olcumde 326 bin ekleme boyunca sifir tarama aldilar."""
    kolon = OutboxEvent.__table__.c[sutun]
    assert not kolon.index, (
        f"{sutun} uzerinde indeks geri eklenmis — her INSERT'te bedeli var, "
        "buna karsilik olcumde hic taranmadi"
    )
    tekil = [i for i in OutboxEvent.__table__.indexes if list(i.columns) == [kolon]]
    assert not tekil, f"{sutun} icin ayri Index() tanimi var"


def test_published_uzerinde_TAM_indeks_yok():
    """Boolean tam indeks: satirlarin neredeyse tamami `true`, secicilik yok."""
    kolon = OutboxEvent.__table__.c["published"]
    assert not kolon.index, "published uzerinde tam (kismi olmayan) indeks var"


def test_yayinlanmamislar_icin_KISMI_indeks_var():
    """Flush sorgusunun tek dayanagi. Kismi oldugu icin maliyeti tablonun
    toplam boyutundan bagimsiz kalir."""
    idx = {i.name: i for i in OutboxEvent.__table__.indexes}
    assert "ix_outbox_events_unpublished" in idx, "kismi indeks tanimli degil"
    kismi = idx["ix_outbox_events_unpublished"]
    kosul = kismi.dialect_options["postgresql"].get("where")
    assert kosul is not None, (
        "indeks KISMI degil — tum tabloyu kapsarsa eski boolean indeksten "
        "farki kalmaz"
    )
    assert "published" in str(kosul), f"kismi kosul published'a bakmiyor: {kosul}"


def test_dedup_ve_purge_indeksleri_KORUNDU():
    """Bunlar olcumde YOGUN kullanildi; yanlislikla silinmesinler."""
    t = OutboxEvent.__table__
    assert t.c["dedup_key"].unique, "dedup_key benzersizligi kalkmis — cift yayin riski"
    assert t.c["dedup_key"].index, "dedup_key indeksi kalkmis (330 bin tarama almisti)"
    assert t.c["published_at"].index, "published_at indeksi kalkmis — purge seq scan yapar"


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migration_zinciri_DOGRU():
    kod = _kod(_migration_kaynak())
    # Revision ID'leri bu depoda SADE sayi ("0030"), dosya adindaki uzun slug
    # DEGIL. Ilk yazimda slug kullandim ve zincir koptu; pytest 951 testle
    # yesil kaldi, yakalayan sey alembic'in kendi head kontrolu oldu.
    assert 'revision = "0031"' in kod, "revision id sade sayi bicimde degil"
    assert 'down_revision = "0030"' in kod, "down_revision zinciri kopuk"


def test_alembic_zinciri_TEK_HEAD():
    """Zincir koparsa migration'lar hic uygulanmaz — CI'da ayri bir is bunu
    kontrol ediyor, burada da erken yakalansin."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    kok = Path(__file__).resolve().parents[1]
    cfg = Config(str(kok / "alembic.ini"))
    cfg.set_main_option("script_location", str(kok / "alembic_migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"birden fazla alembic head: {heads}"


def test_YENI_indeks_ESKISINDEN_once_kuruluyor():
    """Sira onemli: once eskisi dusurulseydi, aradaki anda flush sorgusu
    destegini kaybedip 326 bin satirlik tabloda her 0.3 saniyede bir tam
    tarama yapardi."""
    kod = _kod(_migration_kaynak())
    i_yeni = kod.find("ix_outbox_events_unpublished")
    i_eski = kod.find("DROP INDEX IF EXISTS ix_outbox_events_published\"")
    assert i_yeni != -1, "yeni kismi indeks kurulmuyor"
    assert i_eski != -1, "eski boolean indeks dusurulmuyor"
    assert i_yeni < i_eski, "eski indeks YENISINDEN ONCE dusuruluyor"


def test_migration_GERI_ALINABILIR():
    kod = _kod(_migration_kaynak())
    govde = kod[kod.index("def downgrade"):]
    for ad in ("ix_outbox_events_topic", "ix_outbox_events_created_at",
               "ix_outbox_events_published"):
        assert ad in govde, f"downgrade {ad} indeksini geri kurmuyor"


def test_CONCURRENTLY_kullanilmiyor():
    """Alembic migration'lari tek transaction'da kosuyor; CONCURRENTLY
    transaction blogunda CALISMAZ ve migration'i patlatir."""
    kod = _kod(_migration_kaynak())
    assert "CONCURRENTLY" not in kod.upper(), (
        "CONCURRENTLY transaction icinde calismaz — migration patlar"
    )


# ---------------------------------------------------------------------------
# Saklama suresi
# ---------------------------------------------------------------------------

def test_varsayilan_saklama_1_SAAT(monkeypatch: pytest.MonkeyPatch):
    """24 saat, olculen 90,6 satir/sn oraninda 7,8 milyon satir / ~6,5 GB
    demekti; cihazda 8,9 GB bos disk vardi."""
    from app.services import outbox_flush_worker as w

    monkeypatch.delenv("OUTBOX_PUBLISHED_RETENTION_HOURS", raising=False)
    assert w._retention_hours() == 1, (
        "varsayilan saklama suresi geri buyumus — disk butcesi bununla carpiliyor"
    )


def test_saklama_suresi_env_ile_YUKSELTILEBILIR(monkeypatch: pytest.MonkeyPatch):
    """Adli inceleme isteyen kurulum kendi disk butcesiyle karar versin."""
    from app.services import outbox_flush_worker as w

    monkeypatch.setenv("OUTBOX_PUBLISHED_RETENTION_HOURS", "12")
    assert w._retention_hours() == 12


def test_bozuk_env_degeri_COKERTMEZ(monkeypatch: pytest.MonkeyPatch):
    from app.services import outbox_flush_worker as w

    monkeypatch.setenv("OUTBOX_PUBLISHED_RETENTION_HOURS", "yirmidort")
    assert w._retention_hours() == 1


def test_purge_YAYINLANMAMISLARA_dokunmuyor():
    """At-least-once'in dayanagi: published=False satir ASLA silinmemeli."""
    import inspect

    from app.services.outbox_service import purge_published_outbox

    kaynak = inspect.getsource(purge_published_outbox)
    assert "published.is_(True)" in kaynak, (
        "purge yalnizca yayinlanmislari hedeflemiyor — teslim edilmemis olay "
        "silinebilir ve KALICI VERI KAYBI olur"
    )


# ---------------------------------------------------------------------------
# Yuklem BICIMI — kismi indeksin en kolay kacirilan hatasi
#
# Kismi indeksin yuklemi, onu kullanacak sorgunun yuklemiyle AYNI BICIMDE
# yazilmali. `published IS false` ile `published = false` mantiken esdegerdir
# (sutun NOT NULL), ama planlayici bunu KANITLAYAMAZ ve indeksi kullanmaz.
#
# Bu tuzaga gercekten dusuldu. Cihazda 329.702 satirlik tabloda ayni sorgu:
#
#     boolean tam indeks (eski)              93 buffer    0,589 ms
#     kismi indeks, WHERE published = false  25.490 buffer 37,7 ms  <- seq scan
#     kismi indeks, WHERE published IS FALSE      1 buffer 0,012 ms <- dogru
#
# Yani yanlis bicim, DUZELTMEYE CALISTIGI sorguyu 64 kat yavaslatiyordu ve
# statik testlerin hepsi yesildi. Yakalayan sey olcumdu; bu testler o olcumun
# sonucunu kalici hale getiriyor.
# ---------------------------------------------------------------------------

def _model_indeks_ddl() -> str:
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    idx = {i.name: i for i in OutboxEvent.__table__.indexes}["ix_outbox_events_unpublished"]
    return str(CreateIndex(idx).compile(dialect=postgresql.dialect()))


def test_model_yuklemi_IS_bicimini_kullaniyor():
    ddl = _model_indeks_ddl().lower()
    assert "where published is false" in ddl, (
        f"kismi indeks yuklemi `IS FALSE` bicminde degil: {ddl!r} — planlayici "
        "`= false` ile yazilmis indeksi flush sorgusuyla eslestiremez ve "
        "SEQ SCAN'e duser"
    )


def test_flush_sorgusu_AYNI_bicimi_kullaniyor():
    """Sorgu tarafi `== False`e cevrilirse indeks yine devre disi kalir."""
    import inspect

    from app.services.outbox_service import flush_outbox

    kaynak = inspect.getsource(flush_outbox)
    assert "published.is_(False)" in kaynak, (
        "flush sorgusu `published.is_(False)` kullanmiyor — kismi indeksin "
        "yuklemiyle eslesmezse indeks kullanilmaz"
    )


def test_migration_DDL_i_model_ile_AYNI():
    """Ikisi ayrisirsa: migration bir indeks kurar, model baskasini bekler.

    Ilk yazimda tam bu oldu — model `IS false`, migration `= false` idi.
    """
    model_ddl = _model_indeks_ddl().lower()
    migration = _kod(_migration_kaynak()).lower()

    m = re.search(
        r"create index if not exists ix_outbox_events_unpublished\s*\"?\s*\"?\s*"
        r"(on outbox_events \(id\) where published is false)",
        migration,
    )
    assert m, (
        "migration'daki CREATE INDEX ifadesi model ile ayni yuklemi "
        f"kullanmiyor. Model: {model_ddl!r}"
    )
