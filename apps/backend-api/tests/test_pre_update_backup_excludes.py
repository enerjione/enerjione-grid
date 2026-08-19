"""`update.sh` pre-update dump'i backend ile AYNI tablolari haric tutmali (A12).

YASANAN SORUN
-------------
`update.sh` her guncellemeden once bir pg_dump aliyordu ama
`--exclude-table-data` HIC kullanmiyordu. Yani backend'in "yedege girmesin,
dosyayi sisirir" diye ozenle disarida biraktigi 90 gunluk historian arsivi
(`telemetry_history` + 1m/1h ozetleri + TimescaleDB chunk'lari) her seferinde
TAMAMEN dump ediliyordu — dosya basina birkac GB. Ustelik bu dosyalar
rotasyona da tabi degildi.

NEDEN BU TEST VAR
-----------------
Liste iki yerde duruyor ve DURMAK ZORUNDA:

  * backend  : `backup_service.EXCLUDED_DATA_TABLES` (+ `HISTORIAN_EXCLUDE_SQL`)
  * update.sh: `E1_DUMP_EXCLUDE` dizisi

Paket (.deb) kurulumunda cihazda backend KAYNAGI YOKTUR — uygulama kodu
imajlarin icindedir. Yani update.sh listeyi Python'dan okuyamaz, elle
tasimak zorunda. Bu da klasik "iki kopya zamanla ayrisir" durumu: birine
yeni bir hypertable eklenip digerine eklenmezse, o tablonun verisi update
dump'ina SESSIZCE geri girer ve disk yeniden dolmaya baslar.

Bu test o ayrismayi PR aninda yakalar.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.backup_service import (
    HISTORIAN_EXCLUDE_SQL,
    EXCLUDED_DATA_TABLES,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
UPDATE_SH = REPO_ROOT / "update.sh"
LIB_SH = REPO_ROOT / "infra" / "scripts" / "linux" / "_lib.sh"
CLI = REPO_ROOT / "packaging" / "bin" / "enerjione-grid"


def _update_sh_exclude_list() -> list[str]:
    """`_lib.sh` icindeki `E1_DUMP_EXCLUDE=( ... )` dizisini okur.

    Liste once `update.sh` icindeydi; `enerjione-grid backup` ayni koruma
    olmadan yazilinca (ve GB'larca dump uretince) tek yere tasindi.
    """
    kaynak = LIB_SH.read_text(encoding="utf-8")
    m = re.search(r"^E1_DUMP_EXCLUDE=\((.*?)^\)", kaynak, re.MULTILINE | re.DOTALL)
    assert m, "_lib.sh icinde E1_DUMP_EXCLUDE dizisi bulunamadi"
    govde = m.group(1)
    ogeler: list[str] = []
    for satir in govde.splitlines():
        satir = satir.split("#", 1)[0].strip()
        if not satir:
            continue
        ogeler.append(satir.strip("'\""))
    return ogeler


def test_kaynak_dosyalar_BULUNDU():
    """Yol yanlissa asagidaki testler sessizce yesil kalirdi."""
    assert UPDATE_SH.is_file(), f"update.sh bulunamadi: {UPDATE_SH}"
    assert LIB_SH.is_file(), f"_lib.sh bulunamadi: {LIB_SH}"
    assert CLI.is_file(), f"enerjione-grid bulunamadi: {CLI}"


def test_elle_yedek_komutu_AYNI_listeyi_kullaniyor():
    """`enerjione-grid backup` de exclude uygulamali.

    Bu komut musteriye verilen TEK elle-yedek yoludur ve uzun sure
    `--exclude-table-data` OLMADAN calisiyordu: 90 gunluk historian'in tamami
    her yedege giriyor, dosya GB'lara cikiyordu. update.sh duzeltilirken bu
    yol atlanmisti; ayni hatanin ikinci kez olusmamasi icin liste tek yerde.
    """
    kaynak = CLI.read_text(encoding="utf-8")
    assert "e1_dump_exclude_into" in kaynak, (
        "elle yedek komutu ortak exclude listesini kullanmiyor"
    )
    assert "-F c" in kaynak, (
        "elle yedek duz SQL uretiyor — UI'dan geri YUKLENEMEZ "
        "(validate_dump_file PGDMP imzasi arar)"
    )
    # Cikti YOLU kontrol ediliyor, kaynakta gecen herhangi bir metin degil:
    # once ".sql.gz" arayan bir kontrol yazdim ve bu dosyanin KENDI aciklama
    # satirina takildi. Aciklamalar davranis kaniti degildir.
    cikti = re.findall(r'^\s*out="([^"]+)"', kaynak, re.MULTILINE)
    assert cikti, "elle yedegin cikti yolu bulunamadi"
    assert all(y.endswith(".dump") for y in cikti), (
        f"elle yedek hala duz-SQL uzantisi yaziyor: {cikti}"
    )


def test_exclude_listesi_backend_ile_AYNI():
    """Iki liste birebir ayni olmali — eksik ya da fazla oge kabul edilmez.

    EKSIK oge: o tablonun verisi update dump'ina geri girer, dosya GB'lara
    cikar ve saha cihazinin diski dolar (A12'nin ta kendisi).

    FAZLA oge: backend'in yedekledigi bir tablo update dump'inda eksik olur;
    "guncelleme oncesi yedegim var" diyen operator geri yukledginde o verinin
    kaybini ancak o an fark eder.
    """
    beklenen = set(EXCLUDED_DATA_TABLES)
    bulunan = set(_update_sh_exclude_list())

    eksik = sorted(beklenen - bulunan)
    fazla = sorted(bulunan - beklenen)

    assert not eksik, (
        "update.sh'taki E1_DUMP_EXCLUDE listesinde EKSIK oge var: "
        f"{eksik}. Bu tablolarin verisi her guncellemede tam olarak dump "
        "edilir ve cihazin diskini doldurur."
    )
    assert not fazla, (
        "update.sh'taki E1_DUMP_EXCLUDE listesinde FAZLA oge var: "
        f"{fazla}. Backend bu tablolari yedekliyor; update dump'inda "
        "olmamasi sessiz veri kaybidir."
    )


def _lib_historian_sql() -> str:
    metin = LIB_SH.read_text(encoding="utf-8")
    m = re.search('E1_HISTORIAN_EXCLUDE_SQL="(.*?)\n"', metin, re.S)
    assert m, "_lib.sh icinde E1_HISTORIAN_EXCLUDE_SQL bulunamadi"
    return m.group(1)


def _normalize(sql: str) -> str:
    """Bosluk/girinti farkini yok sayarak SQL karsilastir."""
    return re.sub(r"\s+", " ", sql).strip()


def test_historian_dislamasi_KATALOGDAN_turetiliyor():
    """Sabit chunk kalibi KULLANILMAMALI.

    YASANAN ARIZA: `_timescaledb_internal._hyper_*` kalibi sikistirilmis
    chunk'lara (`compress_hyper_<id>_<M>_chunk`) HIC degmiyordu ve historian
    her yedege giriyordu. Kume artik katalogdan turetiliyor.
    """
    for kaynak, ad in ((LIB_SH.read_text(encoding="utf-8"), "_lib.sh"),):
        m = re.search("E1_DUMP_EXCLUDE=\\((.*?)\n\\)", kaynak, re.S)
        assert m, f"{ad} icinde E1_DUMP_EXCLUDE bulunamadi"
        assert "_hyper_*" not in m.group(1), (
            f"{ad} hala sabit chunk kalibi tasiyor — sikistirilmis chunk'lari "
            "kacirir (bkz. saha olcumu 2026-08-19)"
        )


def test_historian_SQL_i_backend_ile_AYNI():
    """update.sh ile backend AYNI katalog sorgusunu kullanmali.

    Ayrisirlarsa iki yol farkli kumeleri dislar: biri historian'siz, digeri
    historian'li yedek uretir ve fark ancak diskin dolmasiyla anlasilir.

    NOT: bu yalnizca METIN paritesidir. Iki yolun GERCEKTEN historian'siz
    dump urettigini `tests/integration/test_historian_backup_exclusion_pg.py`
    kanitlar (HX02/HX03/HX04).
    """
    assert _normalize(_lib_historian_sql()) == _normalize(HISTORIAN_EXCLUDE_SQL)


@pytest.mark.parametrize(
    "beklenen_bayrak",
    [
        "-F c",  # custom format: UI'daki yukleme PGDMP imzasi arar
        "--exclude-table-data",
    ],
)
def test_dump_komutu_dogru_bayraklarla(beklenen_bayrak: str):
    """Duz SQL dump UI'dan geri YUKLENEMEZ.

    `validate_dump_file` PGDMP magic'i arar; `.sql.gz` dosyalar reddedilir.
    Yani eski hal yalnizca yer kaplayan, ise yaramaz dosyalar uretiyordu.
    """
    # KONUMDAN BAGIMSIZ: pre-update dump komutu artik `_lib.sh` icindeki
    # `e1_pre_update_backup_gate` fonksiyonunda (update.sh onu cagiriyor).
    # Iddia "hangi dosyada" degil, "guncelleme yolunda bu bayrakla dump
    # aliniyor" olmali; aksi halde kod tasindiginda test sozlesmeyi degil
    # yerlesimi korur.
    kaynak = (
        UPDATE_SH.read_text(encoding="utf-8")
        + "\n"
        + LIB_SH.read_text(encoding="utf-8")
    )
    assert beklenen_bayrak in kaynak, (
        f"guncelleme yolundaki pg_dump cagrisinda '{beklenen_bayrak}' yok"
    )


def test_rotasyon_cagriliyor():
    """Dump alinan yerde budama da cagrilmali.

    Rotasyon fonksiyonu `_lib.sh`'ta tanimli ve ayrica davranis testi var
    (infra/scripts/linux/tests/test-pre-update-backup-rotation.sh); burada
    dogrulanan sey update.sh'in onu GERCEKTEN cagirdigi. Cagri dusesse
    fonksiyon dogru calismaya devam eder ama dosyalar yine birikir.
    """
    # Cagri `_lib.sh` icindeki yedek kapisina tasindi; iddia yine "dump
    # alinan yerde budama da var" — yalnizca konum degisti.
    kaynak = (
        UPDATE_SH.read_text(encoding="utf-8")
        + "\n"
        + LIB_SH.read_text(encoding="utf-8")
    )
    assert kaynak.count("e1_prune_pre_update_backups(") >= 1, (
        "rotasyon fonksiyonu tanimli degil"
    )
    cagri = kaynak.count("e1_prune_pre_update_backups ")
    assert cagri >= 2, (
        f"guncelleme yolu rotasyonu {cagri} kez cagiriyor (>=2 bekleniyor) — "
        "yedekler yine birikir"
    )
