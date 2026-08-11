"""`sudo bash update.sh` `.git` icinde root'a ait nesne birakmamali.

YASANAN ARIZA
-------------
`install.sh` ve `update.sh` root olarak calisir (`e1_require_root`) ve git'i
de root olarak cagirir. Root her dosyaya yazabildigi icin bu komutlar HATA
VERMEZ — sessizce `.git` icinde root'a ait nesneler birakirlar.

Fatura sonra kesilir: depoyu normal kullanici olarak kullanmak isteyen
(operator, CI ya da bir ajan) `git fetch` dedigi anda

    error: insufficient permission for adding an object to repository
    database .git/objects
    fatal: failed to write object

ile duser. Bir saha kurulumunda olculdu: 834 root sahipli dosya birikmisti.
Kendiliginden duzelmez ve her `sudo bash update.sh` birikimi buyutur.

NEDEN TEST
----------
Bu, "bir kez elle chown yap" ile kapanacak bir sey degil: git cagrilari
betiklerin uc ayri yerinde (clone, fetch, checkout) ve yenisi eklendiginde
ariza sessizce geri gelir — betik yine hata vermez, yalnizca depo tekrar
bozulur. Bu yuzden kontrol iki katmanli:

  1. Yardimcinin DAVRANISI gercek dosya sisteminde, gercek `chown` ile
     surulur (yalnizca metin araması degil).
  2. Root olarak git calistiran betiklerin yardimciyi CAGIRDIGI dogrulanir.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parents[3]
LIB = KOK / "infra" / "scripts" / "linux" / "_lib.sh"

_BASH = shutil.which("bash")

# YALNIZCA DAVRANIS testleri POSIX ister (gercek `chown`). Betiklerin
# yardimciyi cagirdigini dogrulayan testler dosya okur ve HER YERDE kosar —
# aksi halde gelistirici makinesinde (Windows) tamami atlanir ve dosya
# "yesil" gorunurken hicbir sey dogrulamamis olur.
posix_gerekir = pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="Gercek chown yalnizca POSIX'te surulebilir",
)


def _calistir(betik: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_BASH, "-c", betik], capture_output=True, text=True, cwd=str(KOK)
    )


@posix_gerekir
def test_yardimci_git_sahipligini_CALISMA_AGACIYLA_hizalar(tmp_path: Path):
    """Davranis testi: `.git` icindeki sahiplik agacin sahibine cekilir.

    Root olmadan gercek bir sahiplik degisikligi yapilamaz; bu yuzden test
    ayni KULLANICI icinde GRUP degisimiyle surulur — `chown -R` dogru
    hedefe uygulaniyorsa grup da hizalanir, uygulanmiyorsa ayrik kalir.
    """
    depo = tmp_path / "depo"
    (depo / ".git" / "objects" / "ab").mkdir(parents=True)
    nesne = depo / ".git" / "objects" / "ab" / "cdef"
    nesne.write_text("x", encoding="utf-8")

    gruplar = os.getgroups()
    if len(gruplar) < 2:
        pytest.skip("Ikinci bir grup yok; sahiplik degisimi surulemez")
    agac_grubu, nesne_grubu = gruplar[0], gruplar[1]

    os.chown(depo, -1, agac_grubu)
    os.chown(nesne, -1, nesne_grubu)
    assert nesne.stat().st_gid != depo.stat().st_gid, "test kurulumu ayrisma uretemedi"

    r = _calistir(
        f'set -e; source "{LIB}" >/dev/null 2>&1 || true; '
        f'e1_git_sahipligini_hizala "{depo}"'
    )
    assert r.returncode == 0, r.stderr

    assert nesne.stat().st_gid == depo.stat().st_gid, (
        ".git icindeki nesne calisma agacinin sahipligine cekilmedi"
    )


@posix_gerekir
def test_yardimci_git_OLMAYAN_dizinde_sessizce_gecer(tmp_path: Path):
    """Paket kurulumunda (.deb) `.git` YOKTUR ve bu mesrudur; yardimci
    orada patlarsa `set -e` altindaki kurulum komple duser."""
    r = _calistir(
        f'set -e; source "{LIB}" >/dev/null 2>&1 || true; '
        f'e1_git_sahipligini_hizala "{tmp_path}"; echo TAMAM'
    )
    assert r.returncode == 0, r.stderr
    assert "TAMAM" in r.stdout


@posix_gerekir
def test_yardimci_IDEMPOTENT(tmp_path: Path):
    """Her kosuda cagriliyor; sahiplik zaten dogruysa bir sey degismemeli."""
    depo = tmp_path / "depo"
    (depo / ".git").mkdir(parents=True)
    (depo / ".git" / "config").write_text("x", encoding="utf-8")
    onceki = (depo / ".git" / "config").stat()

    for _ in range(2):
        r = _calistir(
            f'set -e; source "{LIB}" >/dev/null 2>&1 || true; '
            f'e1_git_sahipligini_hizala "{depo}"'
        )
        assert r.returncode == 0, r.stderr

    sonraki = (depo / ".git" / "config").stat()
    assert (onceki.st_uid, onceki.st_gid) == (sonraki.st_uid, sonraki.st_gid)


@pytest.mark.parametrize("betik", ["install.sh", "update.sh"])
def test_root_olarak_git_calistiran_betikler_yardimciyi_CAGIRIR(betik: str):
    """Yeni bir git cagrisi eklendiginde ariza sessizce geri gelir; betigin
    yardimciyi cagirdigini kilitliyoruz."""
    metin = (KOK / betik).read_text(encoding="utf-8")
    # Yorumlari eleyip GERCEK cagriyi ara: gerekce metni testi gecirmesin.
    kod = "\n".join(
        satir for satir in metin.splitlines() if not satir.lstrip().startswith("#")
    )
    assert "e1_git_sahipligini_hizala" in kod, (
        f"{betik} git'i root olarak calistiriyor ama `.git` sahipligini "
        "geri vermiyor — sonraki normal-kullanici `git fetch` duser."
    )


def test_update_sh_HEM_ONCE_HEM_SONRA_hizalar():
    """Bastaki cagri onceki kosulardan kalan birikimi onarir (erken cikis
    yollari dahil), sondaki bu kosunun urettigini. Biri eksikse acik kalir:
    yalnizca sondaki olsaydi `git diff`/`git status` gibi erken adimlarin
    yazdigi `.git/index` root'ta kalirdi."""
    kod = "\n".join(
        s for s in (KOK / "update.sh").read_text(encoding="utf-8").splitlines()
        if not s.lstrip().startswith("#")
    )
    assert kod.count("e1_git_sahipligini_hizala") >= 2, (
        "update.sh yardimciyi yalnizca bir kez cagiriyor"
    )
