"""docker-compose.yml'in BIND MOUNT ettigi her repo yolu .deb'e giriyor mu?

NEDEN BU TEST VAR
-----------------
`docker-compose.yml` pakete giriyor ama `infra/` altindan yalnizca SECILI
dizinler kopyalaniyor (bkz. `packaging/build-deb.sh`). Compose'a yeni bir
`./...` bind mount eklenip o yol build-deb.sh'a EKLENMEZSE saha cihazinda
mount kaynagi bulunmaz: Docker eksik yolu sessizce DIZIN olarak yaratir ve
servis, beklenen dosya yerine bos bir dizinle acilir.

2026-08-20'de tam bu yasandi: Disk Guardian isi compose'a
`./infra/rabbitmq/rabbitmq.conf -> conf.d/20-e1-disk.conf` mount'unu ekledi,
`infra/rabbitmq` pakete girmiyordu. RabbitMQ'nun urun farkindali disk esigi
sahada hic devreye girmeyecekti ve bunu gosteren hicbir hata olmayacakti.

MEVCUT CI NEDEN YAKALAMIYOR
---------------------------
* "compose config" adimi REPO checkout'unda kosuyor — orada dosya var.
* "Kurulum provasi" .deb'i kuruyor ama stack'i AYAGA KALDIRMIYOR.

Yani bu ayrisma ancak paketin ICERIGI ile compose'un BEKLENTISI dogrudan
karsilastirilarak gorulur. Test onu yapar; docker ya da .deb uretimi
gerektirmez, saf metin analizidir.
"""

from __future__ import annotations

import re
from pathlib import Path

KOK = Path(__file__).resolve().parents[3]
COMPOSE = KOK / "docker-compose.yml"
BUILD_DEB = KOK / "packaging" / "build-deb.sh"

#: `- ./yol/dosya:/kapsayici/yol[:ro]` bicimindeki bind mount satirlari.
#: Yalnizca `./` ile baslayanlar ilgilendiriyor: adlandirilmis volume'ler
#: (`rabbitmq-data:`) ve mutlak yollar paketle gelmez.
_MOUNT_RE = re.compile(r"^\s*-\s+(\./[^:\s]+):", re.MULTILINE)

#: Pakete BILEREK girmeyen mount kaynaklari — kurulum aninda uretilirler.
#: Her biri bir SIR ya da kuruluma ozel artefakt; repo'ya da pakete de
#: girmemeleri DOGRU davranistir (bkz. `.gitignore`).
#:
#: Buraya bir satir eklemek bilincli bir karardir: "bu yolun pakette
#: olmamasi normal" demektir. Unutulmus bir mount'u susturmak icin
#: kullanilirsa test amacini kaybeder.
RUNTIME_URETILEN = {
    # install.sh yer tutucu olarak olusturur (bkz. install.sh, "fcm-service-
    # account.json bir DIZIN" temizligi). Icerigi musterinin FCM sirri.
    "./fcm-service-account.json",
    # Kuruluma ozel TLS materyali; `infra/nats/certs/` gitignore'da.
    "./infra/nats/certs",
    # Render edilmis NATS conf'u: `infra/nats` pakete KOPYALANIR ama bu dosya
    # hemen ardindan SILINIR — build-deb.sh'taki `rm -f` satiri, "icinde o
    # kurulumun bcrypt hash'leri var" gerekcesiyle. Her kurulum kendi
    # sifreleriyle kendi conf'unu uretir.
    "./infra/nats/nats-server.conf",
}


def _compose_bind_kaynaklari() -> set[str]:
    return {m.group(1) for m in _MOUNT_RE.finditer(COMPOSE.read_text(encoding="utf-8"))}


def _build_deb_kodu() -> str:
    """build-deb.sh — YORUMLAR ATILMIS hali.

    Yorumlara bakmak testi sahte-yesil yapar: bu dosyadaki aciklama
    satirlari da `infra/rabbitmq` yazisini iceriyor, yani `cp` komutu
    silinse bile duz metin aramasi eslesirdi. (Muhafiz yazilirken tam
    olarak bu yasandi.) Yalnizca CALISAN kod sayilir.
    """
    satirlar = [
        s for s in BUILD_DEB.read_text(encoding="utf-8").splitlines()
        if not s.lstrip().startswith("#")
    ]
    return "\n".join(satirlar)


def test_compose_bind_mount_kaynaklari_pakete_giriyor():
    """Compose'un bekledigi her `./` yolu build-deb.sh tarafindan kopyalaniyor."""
    betik = _build_deb_kodu()
    eksik = []

    for kaynak in sorted(_compose_bind_kaynaklari()):
        if kaynak in RUNTIME_URETILEN:
            continue

        # CALISMA DIZINININ VARLIGINA BAKMIYORUZ — bilincli.
        #
        # Ilk hali `(KOK / yol).exists()` diyordu ve testi MAKINEYE BAGIMLI
        # yapiyordu: gelistirici makinesinde render edilmis
        # `infra/nats/nats-server.conf` duruyor, temiz CI checkout'unda
        # durmuyor. Sonuc, yerelde yesil / CI'da kirmizi bir testti (tam da
        # kapatmaya calistigimiz sinif). Tek anlamli soru sudur: build-deb.sh
        # bu yolu pakete KOPYALIYOR MU?

        # build-deb.sh yolun KENDISINI ya da onu kapsayan bir dizini
        # kopyaliyor mu? `cp -r infra/rabbitmq` , `./infra/rabbitmq/x.conf`i
        # kapsar; parca parca degil, yol bileseni bazinda bakiyoruz ki
        # `infra/nats` yanlislikla `infra/nats-tls`i kapsamasin.
        parcalar = kaynak.removeprefix("./").split("/")
        kapsandi = any(
            re.search(rf"(?<![\w/-])/?{re.escape('/'.join(parcalar[:n]))}(?![\w/-])", betik)
            for n in range(1, len(parcalar) + 1)
        )
        if not kapsandi:
            eksik.append(kaynak)

    assert not eksik, (
        "docker-compose.yml su yollari bind mount ediyor ama packaging/build-deb.sh "
        f"onlari pakete KOPYALAMIYOR: {eksik}. Saha cihazinda mount kaynagi "
        "bulunmaz; Docker eksik yolu dizin olarak yaratir ve servis sessizce "
        "yanlis yapilandirmayla acilir. build-deb.sh'a `cp -r <yol>` ekleyin."
    )


def test_rabbitmq_disk_konfu_pakete_giriyor():
    """Regresyon kilidi — 2026-08-20'de sizan somut vaka.

    Genel test yeterli gorunuyor ama bu mount'un kaybi SESSIZ ve sonucu
    agir (disk dolunca koruma yok). Adiyla sanryla kilitliyoruz ki compose
    yeniden duzenlense bile kimse farkinda olmadan dusuremesin.
    """
    assert (KOK / "infra" / "rabbitmq" / "rabbitmq.conf").exists(), (
        "infra/rabbitmq/rabbitmq.conf repo'dan kaybolmus"
    )
    betik = _build_deb_kodu()
    assert "infra/rabbitmq" in betik, (
        "build-deb.sh infra/rabbitmq'yu pakete kopyalamiyor — RabbitMQ'nun "
        "urun farkindali disk esigi sahada devreye girmez"
    )
