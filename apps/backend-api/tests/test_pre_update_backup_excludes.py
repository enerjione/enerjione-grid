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

  * backend  : `backup_service.EXCLUDED_DATA_TABLES` (+ `_EXCLUDED_DATA_PATTERNS`)
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
    _EXCLUDED_DATA_PATTERNS,
    EXCLUDED_DATA_TABLES,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
UPDATE_SH = REPO_ROOT / "update.sh"


def _update_sh_exclude_list() -> list[str]:
    """update.sh icindeki `E1_DUMP_EXCLUDE=( ... )` dizisini okur."""
    kaynak = UPDATE_SH.read_text(encoding="utf-8")
    m = re.search(r"^E1_DUMP_EXCLUDE=\((.*?)^\)", kaynak, re.MULTILINE | re.DOTALL)
    assert m, "update.sh icinde E1_DUMP_EXCLUDE dizisi bulunamadi"
    govde = m.group(1)
    ogeler: list[str] = []
    for satir in govde.splitlines():
        satir = satir.split("#", 1)[0].strip()
        if not satir:
            continue
        ogeler.append(satir.strip("'\""))
    return ogeler


def test_update_sh_BULUNDU():
    """Yol yanlissa asagidaki testler sessizce yesil kalirdi."""
    assert UPDATE_SH.is_file(), f"update.sh bulunamadi: {UPDATE_SH}"


def test_exclude_listesi_backend_ile_AYNI():
    """Iki liste birebir ayni olmali — eksik ya da fazla oge kabul edilmez.

    EKSIK oge: o tablonun verisi update dump'ina geri girer, dosya GB'lara
    cikar ve saha cihazinin diski dolar (A12'nin ta kendisi).

    FAZLA oge: backend'in yedekledigi bir tablo update dump'inda eksik olur;
    "guncelleme oncesi yedegim var" diyen operator geri yukledginde o verinin
    kaybini ancak o an fark eder.
    """
    beklenen = set(EXCLUDED_DATA_TABLES) | set(_EXCLUDED_DATA_PATTERNS)
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


def test_timescaledb_chunk_patternleri_DAHIL():
    """Hypertable satirlari asil tabloda DEGIL chunk'larda durur.

    `--exclude-table-data telemetry_history` bunlari KAPSAMAZ; pattern
    olmadan historian verisi tablo adiyla haric tutulmus GORUNUR ama dump'a
    yine de tamamen girer. A12'nin en kolay gozden kacan parcasi budur.
    """
    bulunan = _update_sh_exclude_list()
    for pattern in _EXCLUDED_DATA_PATTERNS:
        assert pattern in bulunan, (
            f"update.sh chunk pattern'ini icermiyor: {pattern} — historian "
            "verisi dump'a tamamen girer"
        )


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
    kaynak = UPDATE_SH.read_text(encoding="utf-8")
    assert beklenen_bayrak in kaynak, (
        f"update.sh pg_dump cagrisinda '{beklenen_bayrak}' yok"
    )


def test_rotasyon_cagriliyor():
    """Dump alinan yerde budama da cagrilmali.

    Rotasyon fonksiyonu `_lib.sh`'ta tanimli ve ayrica davranis testi var
    (infra/scripts/linux/tests/test-pre-update-backup-rotation.sh); burada
    dogrulanan sey update.sh'in onu GERCEKTEN cagirdigi. Cagri dusesse
    fonksiyon dogru calismaya devam eder ama dosyalar yine birikir.
    """
    kaynak = UPDATE_SH.read_text(encoding="utf-8")
    assert kaynak.count("e1_prune_pre_update_backups") >= 2, (
        "update.sh rotasyonu cagirmiyor — yedekler yine birikir"
    )
