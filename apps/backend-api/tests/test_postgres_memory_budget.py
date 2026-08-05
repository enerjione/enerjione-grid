"""Postgres bellek/WAL ayarlari ile cgroup tavani BIRLIKTE tutarli olmali.

YASANAN SORUN
-------------
Container tavani 3 GiB, gercek kullanim 2,33 GiB idi (%78) — headroom yok.
Tavani buyutmek TEK BASINA ise yaramaz: Postgres tampon boyutunu cgroup
tavanindan DEGIL postgresql.conf'tan okur. Yani "6 GiB'a cikardik" denip
shared_buffers 768 MB birakilirsa Postgres yine 768 MB ile calisir; degisen
tek sey OOM-kill'in gec gelmesidir.

Ters yon daha tehlikeli: shared_buffers buyutulup cgroup tavani unutulursa
Postgres acilista bellegi ister ve container OOM-kill yer — veritabani
crash-loop'a girer, HTTP katmani hala 200 doner.

Bu testler iki tarafi birbirine BAGLAR: hangisi degisirse digeri de
degismeden CI gecmez.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "docker-compose.yml"

MB = 1024**2
GB = 1024**3


def _postgres() -> dict:
    # PyYAML `<<` merge anahtarini kendisi cozer; anchor'daki `deploy` blogu
    # servis mapping'ine duz olarak gelir.
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return data["services"]["postgres"]


def _boyut_bayt(deger: str) -> int:
    """'1536MB' / '6G' / '2GB' -> bayt. Postgres ve compose birimleri."""
    m = re.fullmatch(r"(\d+)\s*([KMGT]?)i?B?", deger.strip(), re.IGNORECASE)
    assert m, f"boyut ayristirilamadi: {deger!r}"
    carpan = {"": 1, "K": 1024, "M": MB, "G": GB, "T": 1024**4}[m.group(2).upper()]
    return int(m.group(1)) * carpan


def _pg_ayarlari() -> dict[str, str]:
    """postgres `command` listesindeki `-c anahtar=deger` ciftleri."""
    ciftler = {}
    for parca in _postgres()["command"]:
        if isinstance(parca, str) and "=" in parca and not parca.startswith("-"):
            anahtar, _, deger = parca.partition("=")
            ciftler[anahtar] = deger
    return ciftler


def _tavan_bayt() -> int:
    return _boyut_bayt(_postgres()["deploy"]["resources"]["limits"]["memory"])


def test_shared_buffers_TAVANLA_BIRLIKTE_buyuyor():
    """Tavani buyutup shared_buffers'i unutmak "bellek verdik" yanilgisidir.

    PostgreSQL onerisi tavanin ~%25'i. Alt sinir %20 secildi: %25'ten kucuk
    sapmalara izin var ama "tavan 2 katina cikti, tampon ayni kaldi" hatasi
    yakalanir.
    """
    tavan = _tavan_bayt()
    shared = _boyut_bayt(_pg_ayarlari()["shared_buffers"])
    oran = shared / tavan
    assert 0.20 <= oran <= 0.35, (
        f"shared_buffers tavanin %{oran * 100:.0f}'i ({shared / MB:.0f}MB / "
        f"{tavan / GB:.1f}GiB) — %20-35 araligi disinda. Tavan degistiyse "
        "tampon da degismeli; yoksa Postgres eski tamponla kosar."
    )


def test_EN_KOTU_DURUM_bakim_bellegi_tavanin_ALTINDA():
    """Autovacuum worker'lari maintenance_work_mem'i AYRI AYRI ayirir.

    Gozden kacan carpim tam olarak budur: `autovacuum_max_workers` x
    `maintenance_work_mem` es zamanli talep edilebilir ve shared_buffers'in
    USTUNE biner. 5 x 512MB + 1536MB = 4 GiB; 6 GiB tavan altinda.
    Worker sayisini ya da bakim bellegini buyutmek bu carpimi patlatir.
    """
    ayar = _pg_ayarlari()
    tavan = _tavan_bayt()
    shared = _boyut_bayt(ayar["shared_buffers"])
    bakim = _boyut_bayt(ayar["maintenance_work_mem"])
    worker = int(ayar["autovacuum_max_workers"])

    en_kotu = shared + bakim * worker
    assert en_kotu < tavan, (
        f"shared_buffers({shared / MB:.0f}MB) + "
        f"{worker} x maintenance_work_mem({bakim / MB:.0f}MB) = "
        f"{en_kotu / GB:.2f}GiB >= tavan {tavan / GB:.2f}GiB — "
        "autovacuum firtinasinda container OOM-kill yer"
    )


def test_effective_cache_size_PLANLAYICIYI_yaniltmiyor():
    """Yalnizca bir ipucudur, bellek ayirmaz — ama tavandan buyuk olursa
    planlayici var olmayan bir OS cache'i varsayip index taramasini gercekci
    olmayan bicimde ucuz sanir."""
    ayar = _pg_ayarlari()
    assert _boyut_bayt(ayar["effective_cache_size"]) <= _tavan_bayt()


def test_work_mem_baglanti_tavaniyla_BIRLIKTE_degerlendirilmis():
    """work_mem BAGLANTI BASINA DEGIL sorgudaki her sort/hash dugumu
    basinadir; max_connections ile carpimi en kotu durumdur.

    Bu carpim tavanin altinda OLAMAZ (300 x 16MB = 4,7 GiB, tavan 6 GiB'a
    yakin) — amac onu tavanin altina cekmek degil, work_mem'in sessizce
    buyutulmesini engellemek. 300 baglantida 32MB = 9,4 GiB olurdu.
    """
    ayar = _pg_ayarlari()
    work = _boyut_bayt(ayar["work_mem"])
    baglanti = int(ayar["max_connections"])
    assert work * baglanti <= _tavan_bayt() * 1.0, (
        f"max_connections({baglanti}) x work_mem({work / MB:.0f}MB) = "
        f"{work * baglanti / GB:.1f}GiB — tavanin ({_tavan_bayt() / GB:.1f}GiB) "
        "uzerinde; tek bir sorgu firtinasi container'i dusurur"
    )


def test_WAL_tavani_DISK_BUTCESINE_gore_ve_URETIM_dusurulmus():
    """WAL tavani, checkpoint'lerin ZAMAN tetiklemesine yetecek kadar buyuk
    ama disk butcesinin kucuk bir parcasi olmali.

    ONCEKI KARAR VE NEDEN DEGISTI
    -----------------------------
    Bu test once `max_wal_size <= 2 GiB` diye kilitliyordu. Gerekce: "diskte
    yer yok, geriye tek kaldirac WAL uretimini kucultmek kalir". O gerekce
    O ANDAKI olcumle dogruydu.

    2026-08-05, 500 cihaz yuku, `pg_stat_bgwriter`:
        zamanlanmis checkpoint :  34
        ZORLANMIS checkpoint   : 654         <- %95
        buffers_checkpoint     :  15.105.908
        buffers_backend        : 367.505.585 <- 24 KAT fazla
    2 GiB tavan o kadar hizli doluyordu ki Postgres surekli ACIL checkpoint
    yapiyordu. Zorlanmis checkpoint YAYILAMAZ: bir anda buyuk bir yazma
    dalgasi gelir ve o sirada gelen her INSERT bekler. Yazma hizi
    3.355 <-> 1.666 satir/sn arasinda testere disi dalgalaniyordu.
    `buffers_backend`in 24 kat baskin olmasi ikinci kanit: kirli sayfalari
    checkpoint degil, VERI YAZAN SUREC diske indiriyordu.

    "Disk dar" varsayimi da artik gecerli degil: arsiv 60/193 sinyale
    indirildikten sonra disk %23-32'de, 300+ GB bos.

    KURAL ARTIK SAYISAL, YASAK DEGIL
    Yasak koymak, kosullar degistiginde yanlis tarafta kalir. Onun yerine
    iliskiyi kilitliyoruz: pg_wal gecici olarak ~2 x max_wal_size'a
    cikabilir; bu, tipik saha diskinin (456 GB) %10'unu ASMAMALI.
    """
    ayar = _pg_ayarlari()
    wal = _boyut_bayt(ayar["max_wal_size"])

    # Alt sinir: 2 GiB'de zorlanmis checkpoint OLCULDU; altina donmek
    # olculmus bir arizaya geri donmektir.
    assert wal > 2 * GB, (
        f"max_wal_size {wal / GB:.0f}GiB — 2 GiB'de checkpoint'lerin %95'i "
        "ZORLANMIS olculdu ve yazma hizi yariya dusuyordu"
    )

    # Ust sinir: disk butcesi. pg_wal ~2x tavana cikabilir.
    SAHA_DISK_GB = 456
    assert wal * 2 <= SAHA_DISK_GB * GB * 0.10, (
        f"max_wal_size {wal / GB:.0f}GiB — pg_wal ~{wal * 2 / GB:.0f}GiB'a "
        f"cikabilir, {SAHA_DISK_GB}GB diskin %10'unu asiyor"
    )

    # WAL uretimini dusurmek HALA gecerli; tavan buyudu diye birakilmamali.
    assert ayar.get("wal_compression", "off") != "off", (
        "wal_compression kapatilmis — tavan buyutuldu diye WAL uretimini "
        "dusurmekten vazgecilmemeli"
    )


@pytest.mark.parametrize("anahtar", ["min_wal_size", "checkpoint_timeout"])
def test_checkpoint_ayarlari_ACIKCA_verilmis(anahtar: str):
    """Varsayilanlar (80MB / 5min) bu is yuku icin secilmis degil, sadece
    dokunulmamis degerler. Acik yazmak sonraki okuyana bunun bir KARAR
    oldugunu gosterir."""
    assert anahtar in _pg_ayarlari()


def test_wal_compression_DERLEME_BAGIMLI_deger_kullanmiyor():
    """`lz4`/`zstd` imajin derleme secenegine baglidir; desteklenmiyorsa
    Postgres HIC BASLAMAZ ve tum stack ayaga kalkmaz. `on` (pglz) her
    build'de gecerlidir."""
    assert _pg_ayarlari().get("wal_compression") == "on", (
        "wal_compression yalnizca 'on' olmali — lz4/zstd desteklenmezse "
        "Postgres baslamaz ve ariza kurulum aninda degil, ilk yeniden "
        "baslatmada ortaya cikar"
    )
