"""Grid servis imajlari URETIMDE `latest` ile calismaz.

YASANAN CELISKI
---------------
`.env.example` acikca "Production'da HER ZAMAN explicit semver kullanin
(rollback icin sart)" diyordu; ayni dosyanin bir alt satirinda
`E1_VERSION=latest` yaziyordu. Yonerge ile varsayilan birbirinin tersiydi.

Celiski kozmetik degildi. Uc ayri yerde uretime siziyordu:

  1. `.env.example` -> install.sh bu dosyayi `.env` olarak KOPYALIYOR.
  2. `docker-compose.yml` -> `${E1_VERSION:-latest}`: `.env` icinde anahtar
     yoksa TUM servisler sessizce `latest`e duser.
  3. `install.sh` -> VERSION dosyasi okunamazsa `latest` yazip UYARI basiyor,
     kurulum yesil bitiyordu.

`latest` ile calisan bir saha cihazinda `docker compose up -d` her
cagrildiginda kayit defterindeki etiket neye isaret ediyorsa O gelir: surum
operator hicbir sey yapmadan degisebilir ve geri donulecek somut bir etiket
kalmaz.

BU DOSYANIN KAPSAMI
-------------------
Grid'in KENDI servis imajlari. Gateway imaji AYRI bir mesele ve AYRI
dosyada korunuyor (`test_gateway_kurulum_degismez_imaj.py`) — orada kural
daha da sıkı: etiket degil, digest.
"""

from __future__ import annotations

import re
from pathlib import Path

KOK = Path(__file__).resolve().parents[3]


def _oku(gorece: str) -> str:
    return (KOK / gorece).read_text(encoding="utf-8")


def _env_ornegi_degeri(anahtar: str) -> str | None:
    for satir in _oku(".env.example").splitlines():
        satir = satir.strip()
        if satir.startswith(f"{anahtar}="):
            return satir.split("=", 1)[1].strip()
    return None


# ===========================================================================
# A — URETIM VARSAYILANI
# ===========================================================================


def test_A_env_orneginde_E1_VERSION_latest_DEGIL():
    """install.sh bu dosyayi `.env` olarak kopyalar: varsayilan uretim degeridir."""
    deger = _env_ornegi_degeri("E1_VERSION")
    assert deger is not None, ".env.example icinde E1_VERSION yok"
    assert deger != "latest", (
        "`.env.example` hala `latest` diyor — ayni dosya bir ust satirda "
        "'production'da HER ZAMAN explicit semver' diyor. Celiski geri geldi."
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", deger), (
        f"E1_VERSION semver degil: {deger!r}. Yer tutucu birakmak da olmaz — "
        "`docker compose` 'invalid reference format' ile duser."
    )


def test_A_env_ornegi_VERSION_dosyasiyla_SENKRON():
    """Surum cikarma akisinin guncelledigi dosyalardan biri artik bu.

    Ayrisirsa elle kurulum yapan biri ESKI bir surumu sabitler ve bunu
    fark etmez; imaj mevcut oldugu icin hicbir hata da almaz.
    """
    beklenen = _oku("VERSION").strip()
    assert _env_ornegi_degeri("E1_VERSION") == beklenen, (
        f"`.env.example` E1_VERSION ile VERSION ({beklenen}) ayrismis"
    )


# ===========================================================================
# B — COMPOSE FAIL-CLOSED
# ===========================================================================


def test_B_compose_SESSIZ_latest_yedegi_TASIMAZ():
    """`${E1_VERSION:-latest}` en derindeki sessiz varsayilandi.

    `.env` icinde anahtar hic yoksa 10 servisin HEPSI `latest`e duser ve
    hicbir yerde uyari cikmaz.
    """
    compose = _oku("docker-compose.yml")
    assert "${E1_VERSION:-latest}" not in compose, (
        "compose hala sessiz `latest` yedegi tasiyor"
    )
    assert "${E1_VERSION:-" not in compose, (
        "compose'da E1_VERSION icin varsayilan deger var — tanimsizsa "
        "kurulum DURMALI, sessizce bir etikete dusmemeli"
    )


def test_B_compose_TANIMSIZSA_hata_verir():
    """Zorunluluk `:?` ile ifade edilmeli ve mesaji anlamli olmali."""
    compose = _oku("docker-compose.yml")
    zorunlu = re.findall(r"\$\{E1_VERSION:\?([^}]*)\}", compose)
    assert zorunlu, "E1_VERSION zorunlu kilinmamis (`${E1_VERSION:?...}` yok)"
    for mesaj in zorunlu:
        assert len(mesaj) > 20, f"fail-closed mesaji cok kisa: {mesaj!r}"
        assert "semver" in mesaj.lower()


def test_B_TUM_servisler_ayni_kurala_tabi():
    """Bir servis atlanirsa o servis tek basina `latest` calisirdi —
    ayni stack icinde iki farkli surum, en zor teshis edilen hallerden biri.
    """
    compose = _oku("docker-compose.yml")
    imaj_satirlari = [
        s for s in compose.splitlines()
        if re.match(r"\s*image:\s*\$\{E1_REGISTRY", s)
    ]
    # Esik regex'in sessizce hicbir sey eslememesine karsi; asil iddia
    # asagidaki dongudur. (9 servis imaji + `environment:` altindaki bir
    # E1_VERSION girdisi = toplam 10 kullanim.)
    assert len(imaj_satirlari) >= 9, (
        f"beklenenden az servis imaji bulundu ({len(imaj_satirlari)}) — "
        "regex kaymis olabilir"
    )
    for satir in imaj_satirlari:
        assert "${E1_VERSION:?" in satir, f"servis kural disi: {satir.strip()}"


# ===========================================================================
# C — INSTALL: TAM SEMVER
# ===========================================================================


def test_C_install_sh_checkout_edilen_surumu_yazar():
    kaynak = _oku("install.sh")
    assert '_set_env_var "E1_VERSION" "$E1_VERSION_LABEL"' in kaynak, (
        "install.sh checkout edilen surumu .env'e yazmiyor"
    )


def test_C_install_sh_latest_YAZMAZ():
    """Eski dal 'latest' yazip UYARI basiyor, kurulum yesil bitiyordu."""
    kaynak = _oku("install.sh")
    assert '_set_env_var "E1_VERSION" "latest"' not in kaynak, (
        "install.sh hala `latest` yaziyor — uyari yeterli degil, kurulum "
        "durmali"
    )


def test_D_install_sh_surum_okunamazsa_DURUR():
    """Fail-closed: yanlis surumle acilmis bir sahayi sonradan teshis etmek,
    kurulumu bastan reddetmekten pahalidir."""
    kaynak = _oku("install.sh")
    dal = kaynak[kaynak.index('if [[ "$E1_VERSION_LABEL" =~'):]
    dal = dal[: dal.index("\nfi\n")]
    assert "exit 1" in dal, "surum okunamadiginda kurulum devam ediyor"


def test_D_install_ps1_varsayilani_latest_DEGIL():
    kaynak = _oku("install.ps1")
    assert '[string]$Version = "latest"' not in kaynak, (
        "Windows installer hala varsayilan olarak `latest` kuruyor"
    )
    assert "VERSION" in kaynak, "install.ps1 surumu VERSION dosyasindan cozmuyor"


# ===========================================================================
# E — UPDATE / ROLLBACK KORUNUYOR
# ===========================================================================


def test_E_update_sh_hedef_surumu_yazar():
    kaynak = _oku("update.sh")
    assert re.search(r"sed -i \"s\|\^E1_VERSION=\.\*\|E1_VERSION=\$\{yeni\}\|\"", kaynak), (
        "update.sh hedef surumu .env'e yazmiyor"
    )


def test_E_update_sh_ONCEKI_surumu_geri_alabiliyor():
    """Rollback modeli bu isin kapsaminda DEGISMEDI; bozulmadigini dogrular."""
    kaynak = _oku("update.sh")
    assert "E1_ENV_VERSION_PENDING" in kaynak, (
        "guncelleme oncesi surum saklanmiyor — geri alma imkansizlasir"
    )
    assert re.search(r"E1_VERSION=\$\{eski\}", kaynak), (
        "geri alma yolu onceki surumu yazmiyor"
    )


# ===========================================================================
# F/G — GELISTIRME KACISI VE KALAN KULLANIMLAR
# ===========================================================================


def test_F_gelistirme_kacisi_ACIK_kaliyor():
    """Developer bilincli olarak `E1_VERSION=latest` yazabilmeli.

    `:?` yalnizca TANIMSIZ degeri reddeder; acikca yazilan `latest`
    calismaya devam eder. Kural "latest yasak" degil, "sessiz varsayilan
    yasak".
    """
    compose = _oku("docker-compose.yml")
    # `:?` YALNIZCA tanimsiz/bos degeri reddeder. Compose tarafinda DEGER
    # dogrulamasi (orn. "semver olmayan etiketi reddet") bilincli olarak YOK:
    # olsaydi gelistiricinin `latest` ya da yerel bir build etiketi kullanmasi
    # da engellenirdi ve kural "sessiz varsayilan yasak"tan "latest yasak"a
    # kayardi.
    assert "${E1_VERSION:?" in compose
    assert "${E1_VERSION}" not in compose, (
        "korumasiz bir E1_VERSION kullanimi var — o servis tanimsiz degerde "
        "bos etiketle acilirdi"
    )

    # DAVRANIS OLCULDU (2026-08-21, docker compose v29.6.2 — `compose config`):
    #   E1_VERSION=2.109.1 -> image ...backend-api:2.109.1
    #   E1_VERSION yok     -> "required variable E1_VERSION is missing a value"
    #   E1_VERSION=latest  -> image ...backend-api:latest   (kacis calisiyor)


def test_G_uretim_yolunda_E1_VERSION_latest_KALMADI():
    """Repo taramasi: `E1_VERSION=latest` yalnizca dev/dokuman baglaminda
    kalabilir, uretim yolunda kalamaz.

    Uretim yolu = `.env.example`, `docker-compose.yml`, `install.sh`,
    `install.ps1`, `update.sh`.
    """
    uretim_yollari = [
        ".env.example",
        "docker-compose.yml",
        "install.sh",
        "update.sh",
    ]
    # ARANAN SEY: gercek ATAMA. Bir hata mesajinin icinde gecen
    # "E1_VERSION=latest yazin" metni bir atama DEGILDIR — nitekim
    # install.sh, kurulumu durdururken gelistiriciye tam da bunu onerir.
    # Metni de yakalayan bir tarama, dogru davranisi hata sanardi.
    atama = re.compile(
        r"""(?mx)
        ^\s*E1_VERSION=latest\s*$          # .env satiri
        | _set_env_var\s+"E1_VERSION"\s+"latest"   # install.sh yazimi
        | \$\{E1_VERSION:-latest\}          # compose sessiz yedegi
        """
    )
    kalanlar = {yol: atama.findall(_oku(yol)) for yol in uretim_yollari}
    kalanlar = {k: v for k, v in kalanlar.items() if v}
    assert not kalanlar, f"uretim yolunda `E1_VERSION=latest` ATAMASI kaldi: {kalanlar}"
