"""update.sh imaj temizligi — KAYNAK OKUYARAK DEGIL, CALISTIRARAK sinanir.

YASANAN SORUN
-------------
update.sh her surumde yeni bir ETIKETLI imaj birakiyor, hicbirini
silmiyordu. `docker image prune -f` (update.ps1 / uninstall.sh) yalnizca
dangling `<none>` imajlari alir; `.../backend-api:2.37.4` gibi etiketli eski
surumleri ASLA geri kazanmaz. 100 cihazlik test sunucusunda birikim 15,8 GB,
atilabilir kismi 9,8 GB idi — ve bu alan postgres-data / nats-data ile AYNI
dosya sisteminde.

BU TESTIN BICIMI NEDEN BOYLE
----------------------------
"update.sh icinde 'docker rmi' geciyor mu" diye bakan bir test, filtre
regex'i yanlis oldugu halde YESIL kalirdi — ve buradaki tum risk filtrededir:
yanlis filtre ya hicbir sey silmez (disk dolmaya devam eder) ya da CALISAN
surumu / yan yana kosan Solar kurulumunu siler.

Bu yuzden blok update.sh'ten ayiklanip, PATH'e konan SAHTE bir `docker` ile
gercekten kosturulur ve hangi imajlarin silindigi olculur.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
UPDATE_SH = REPO / "update.sh"

BASLANGIC = "E1_IMAGE_PRUNE_BEGIN"
BITIS = "E1_IMAGE_PRUNE_END"

#: `docker images --format '{{.Repository}}:{{.Tag}}'` ciktisi taklidi.
IMAJ_LISTESI = """\
ghcr.io/enerjione/enerjione-grid/backend-api:2.38.4
ghcr.io/enerjione/enerjione-grid/backend-api:2.37.4
ghcr.io/enerjione/enerjione-grid/backend-api:2.36.0
ghcr.io/enerjione/enerjione-grid/frontend-web:2.30.1
ghcr.io/enerjione/enerjione-grid/tag-engine:latest
ghcr.io/enerjione/enerjione-grid/iec104-outbound:<none>
ghcr.io/enerjione/enerjione-solar/backend-api:2.36.0
e1-solar/frontend-web:1.2.3
e1-grid/backend-api:2.10.0
e1/alarm-service:1.0.0
postgres:16
timescale/timescaledb:2.17.2-pg16
nats:2.10-alpine
"""

DOCKER_STUB = """\
#!/usr/bin/env bash
# Sahte docker: 'images' listeyi basar, 'rmi' ve 'image prune' cagrilari
# kaydedilir. Gercek bir docker daemon'a ihtiyac yok.
case "$1" in
  images) cat "$E1_TEST_IMAGE_LIST" ;;
  rmi)    echo "RMI $2" >> "$E1_TEST_LOG" ;;
  image)  echo "PRUNE $2" >> "$E1_TEST_LOG" ;;
  *)      exit 1 ;;
esac
"""


def _blogu_ayikla() -> str:
    """update.sh'teki temizlik blogunu isaretciler arasindan al."""
    satirlar = UPDATE_SH.read_text(encoding="utf-8").splitlines()
    bas = next((i for i, s in enumerate(satirlar) if BASLANGIC in s), None)
    son = next((i for i, s in enumerate(satirlar) if BITIS in s), None)
    assert bas is not None and son is not None and son > bas, (
        f"update.sh icinde {BASLANGIC}/{BITIS} isaretcileri bulunamadi — "
        "blok tasindiysa bu test de guncellenmeli"
    )
    return "\n".join(satirlar[bas + 1 : son])


def _bash_bul() -> str | None:
    """Windows YOLLARINI anlayan bir bash bul.

    `shutil.which("bash")` Windows'ta cogu zaman
    `%LOCALAPPDATA%\\Microsoft\\WindowsApps\\bash.EXE` dondururu — bu WSL
    saplamasidir ve kendisine verilen `C:/...` yolunu goremez; harness
    "No such file or directory" ile 127 doner. Yani test, update.sh'ta
    HICBIR sorun yokken kirmizi olur.

    Bu, hangi kabuktan calistirildigina gore degisen sinsi bir farkti:
    Git Bash icinden pytest yesil, PowerShell icinden (ve dolayisiyla
    `tools/oturum-teslim.ps1` icinden) 5 test kirmizi. Teslim scripti tam
    bu yuzden duruyordu.

    Sira: Git Bash -> PATH'teki bash (WSL saplamasi degilse) -> yok.
    """
    adaylar = [
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
    ]
    for aday in adaylar:
        if aday.is_file():
            return str(aday)

    yol = shutil.which("bash")
    if yol and "windowsapps" not in yol.replace("\\", "/").lower():
        return yol
    return None


def _calistir(tmp_path: Path, *, registry: str, yeni: str, onceki: str) -> list[str]:
    """Blogu sahte docker ile kostur, SILINEN imajlarin listesini dondur."""
    bash = _bash_bul()
    if not bash:
        pytest.skip(
            "Windows yollarini anlayan bash yok (bu blok yalnizca Linux saha "
            "cihazinda ya da Git Bash kurulu makinede kosar)"
        )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_STUB, encoding="utf-8", newline="\n")
    docker.chmod(0o755)

    liste = tmp_path / "images.txt"
    liste.write_text(IMAJ_LISTESI, encoding="utf-8", newline="\n")
    log = tmp_path / "log.txt"
    log.write_text("", encoding="utf-8")

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        # update.sh'in log yardimcilari; bu testin konusu degil.
        "e1_info() { :; }\n"
        "e1_ok() { :; }\n"
        f"{_blogu_ayikla()}\n"
        f"e1_prune_old_images '{yeni}' '{onceki}' '{registry}'\n",
        encoding="utf-8",
        newline="\n",
    )

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir.as_posix()}{os.pathsep}{env.get('PATH', '')}"
    env["E1_TEST_IMAGE_LIST"] = liste.as_posix()
    env["E1_TEST_LOG"] = log.as_posix()

    sonuc = subprocess.run(
        [bash, harness.as_posix()],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert sonuc.returncode == 0, f"blok hata verdi:\n{sonuc.stdout}\n{sonuc.stderr}"

    return [
        s.split(" ", 1)[1]
        for s in log.read_text(encoding="utf-8").splitlines()
        if s.startswith("RMI ")
    ]


def test_eski_etiketli_surumler_GERCEKTEN_siliniyor(tmp_path: Path) -> None:
    """Asil kusur buydu: etiketli eski surumler hic silinmiyordu."""
    silinen = _calistir(
        tmp_path,
        registry="ghcr.io/enerjione/enerjione-grid",
        yeni="2.38.4",
        onceki="2.37.4",
    )
    assert set(silinen) == {
        "ghcr.io/enerjione/enerjione-grid/backend-api:2.36.0",
        "ghcr.io/enerjione/enerjione-grid/frontend-web:2.30.1",
        "e1-grid/backend-api:2.10.0",
        "e1/alarm-service:1.0.0",
    }, f"beklenmeyen silme kumesi: {silinen}"


def test_calisan_ve_onceki_surum_ile_latest_KORUNUYOR(tmp_path: Path) -> None:
    """Hedef surum silinirse sistem ayaga kalkmaz; onceki surum silinirse
    `update.sh --version <eski>` ile geri donus internet ister."""
    silinen = _calistir(
        tmp_path,
        registry="ghcr.io/enerjione/enerjione-grid",
        yeni="2.38.4",
        onceki="2.37.4",
    )
    for korunmali in (
        "ghcr.io/enerjione/enerjione-grid/backend-api:2.38.4",
        "ghcr.io/enerjione/enerjione-grid/backend-api:2.37.4",
        "ghcr.io/enerjione/enerjione-grid/tag-engine:latest",
    ):
        assert korunmali not in silinen, f"{korunmali} silindi — geri donus kirilir"


def test_ucuncu_taraf_imajlarina_DOKUNULMUYOR(tmp_path: Path) -> None:
    """postgres/nats/timescaledb silinirse bir sonraki acilis internet ister;
    sahada internet olmayabilir."""
    silinen = _calistir(
        tmp_path,
        registry="ghcr.io/enerjione/enerjione-grid",
        yeni="2.38.4",
        onceki="2.37.4",
    )
    for korunmali in ("postgres:16", "timescale/timescaledb:2.17.2-pg16", "nats:2.10-alpine"):
        assert korunmali not in silinen, f"{korunmali} silindi — altyapi imaji"


def test_dangling_etiket_rmi_ile_DEGIL_prune_ile_alinir(tmp_path: Path) -> None:
    """`<none>` etiketi `docker rmi` argumani olarak gecerli degildir; onu
    `docker image prune` toplar. Listeye karisirsa her turda sessiz hata."""
    silinen = _calistir(
        tmp_path,
        registry="ghcr.io/enerjione/enerjione-grid",
        yeni="2.38.4",
        onceki="2.37.4",
    )
    assert not any("<none>" in s for s in silinen), (
        "dangling etiket rmi'ye gecirildi"
    )


def test_SOLAR_kurulumu_ayni_HOSTTA_hayatta_kaliyor(tmp_path: Path) -> None:
    """Solar yan yana kosabiliyor ve onun imajlarini silmek BASKA bir urunu
    bozar.

    Senaryo bilincli olarak en zor hali: operator E1_REGISTRY'yi ust dizine
    (`ghcr.io/enerjione`) ayarlamis. O zaman registry oneki Solar imajlarini
    da KAPSAR ve tek koruma, ismen Solar dislama satiridir.
    """
    silinen = _calistir(
        tmp_path,
        registry="ghcr.io/enerjione",
        yeni="2.38.4",
        onceki="2.37.4",
    )
    assert "ghcr.io/enerjione/enerjione-solar/backend-api:2.36.0" not in silinen, (
        "Solar imaji silindi — yan yana kosan urun bozulur"
    )
    assert "e1-solar/frontend-web:1.2.3" not in silinen, "eski Solar namespace'i silindi"
    # Yine de Grid'in eski surumleri temizlenmis olmali; koruma "hicbir sey
    # silme"ye donusmemeli.
    assert "ghcr.io/enerjione/enerjione-grid/backend-api:2.36.0" in silinen
