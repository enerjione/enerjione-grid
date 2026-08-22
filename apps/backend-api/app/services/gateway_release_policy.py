"""ONAYLI GATEWAY SURUMU — dagitim kararinin TEK KAYNAGI.

NE COZUYOR
----------
Uretim dagitiminda gateway imaji `:latest` etiketiyle veriliyordu ve compose
sablonu `pull_policy: always` tasiyor. Ikisi birlikte sunu uretiyor: Grid
AYNI SURUMDE KALSA BILE, kayit defterinde `latest` baska bir release'e
tasindiginda container'in yeniden olusturuldugu HER an (yeniden kurulum,
`docker compose up`, cihaz degisimi) operator ONAYI OLMADAN farkli bir
gateway kodu calismaya baslar.

Bu bir kuram degil: 2026-08-11'de sahada `:latest` 1.6.2'ye tasinmisti ve
ekran hala "Surum bilinmiyor" diyordu (bkz. `gateway_release_service` modul
basligi). Yani etiketin nereye baktigini kimse bilmeden calisiyorduk.

NE DEGISMEDI — VE BU ONEMLI
---------------------------
`:latest`i kaldirmak gateway'i 1.15.1'e KILITLEMEK DEGILDIR. Yeni surumler
ciktiginda Grid onlari GORUR (`gateway_release_service` kayit defterini
okur) ve operator guncelleyebilir. Degisen tek sey: dagitim kararinin
hicbir noktasinda "o an latest ne ise o" ifadesi kalmamasi.

    onayli surum
        v
    uyumluluk (min Grid surumu)
        v
    OPERATOR ONAYI          <-- otomatik guncelleme YOK
        v
    tam semver + degismez digest
        v
    dagit -> dogrula -> basari ya da TAM digest ile geri al

NEDEN AYRI MODUL
----------------
Ayni bilgi UC yerde kopyalanmisti: `gateway_compose.DEFAULT_GATEWAY_IMAGE`,
`api/gateways._DEFAULT_GATEWAY_IMAGE` ve ajan istegindeki varsayilan. Uc
kopya, birinde yapilan degisikligin otekilerde unutulmasi demekti — ve
uretim dagitiminda "hangisi gecerli" sorusunun cevabi belirsizlesirdi.

NEDEN SURUM BURADA SABIT
------------------------
Onayli surum bir URUN KARARIDIR: Grid'in bu surumu ile birlikte test
edilmis gateway. Kayit defterinden "en yeni ne varsa" almak, tam da
kaldirmaya calistigimiz nondeterminizmin baska bir bicimi olurdu.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Gateway imaj deposu — etiketsiz.
GATEWAY_IMAGE_REPO = "ghcr.io/enerjione/enerjione-grid-dnp3-gateway"

#: Grid'in bu surumuyle birlikte DOGRULANMIS gateway surumu.
#:
#: Yeni bir gateway release'i ciktiginda burasi elle yukseltilir — otomatik
#: DEGIL. "Kayit defterinde en yeni ne varsa" demek, `:latest`in adini
#: degistirmekten ibaret olurdu.
APPROVED_GATEWAY_VERSION = "1.15.1"

#: Gateway surumu -> CALISMASI ICIN GEREKEN EN DUSUK GRID SURUMU.
#:
#: Bos birakilan bir surum "kisitsiz" DEMEK DEGILDIR; `min_grid_for()` uc
#: durumlu doner ve bilinmeyen surumde uretim guncellemesi ACILMAZ
#: (fail-closed). Yeni gateway release'i eklenirken bu tabloya da yazilir.
#: ONAYLI SURUMUN KAYNAK COMMIT'I (gateway repo'sunun release commit'i).
#:
#: NEDEN BURADA
#: ------------
#: Ayni release'in dort ayri temsili var ve DORDU DE bagimsiz kayabilir:
#:
#:     surum      -> APPROVED_GATEWAY_VERSION
#:     imaj       -> APPROVED_GATEWAY_DIGESTS
#:     kaynak     -> APPROVED_GATEWAY_SOURCE_SHA (burasi)
#:     sozlesme   -> infra/gateway-contract/v<surum>.json
#:
#: Bu sabit dorduncuyu birinciye baglar ve zinciri kapatir: sozlesme
#: artifact'indaki `gateway_source_sha` ile ESIT olmali (testle kilitli),
#: imajin `org.opencontainers.image.revision` etiketi de ayni degeri tasir.
#: Yani "hangi kod calisiyor" sorusunun cevabi tek bir commit'e iner.
#:
#: DOGRULAMA (2026-08-21, ag ile):
#:   GitHub tag v1.15.1  -> commit ae9f00df10edd36b9106ba1c0efaa6b8871c801e
#:   release workflow    -> run 32476830826, headSha ae9f00df10ed
#:   imaj etiketi        -> org.opencontainers.image.revision = ae9f00df...
#:   generated artifact  -> gateway_source_sha = ae9f00df...
#:
#: Yeni surumde: sozlesme artifact'ini vendor edin, digest'i yazin, bu
#: SHA'yi artifact'takiyle ayni yapin. Uctan biri unutulursa test kirilir.
APPROVED_GATEWAY_SOURCE_SHA = "ae9f00df10edd36b9106ba1c0efaa6b8871c801e"

#: ONAYLI SURUMLERIN DEGISMEZ DIGEST'LERI (surum -> manifest digest).
#:
#: NEDEN KODDA SABIT
#: -----------------
#: Kurulum aninda kayit defterine erisim GEREKMESIN diye. Digest yalnizca
#: cevrimici cozulebiliyor olsaydi, GHCR'in erisilemez oldugu bir anda
#: kurulum ya bloke olur ya da (eski davranis) DEGISEBILIR bir etikete
#: duserdi. Ikisi de kabul edilebilir degil: birincisi sahayi kurtarmayi
#: engeller, ikincisi "hangi kod calisiyor" sorusunun cevabini kaybeder.
#:
#: DEGER NASIL URETILIR (yeni surumde ya da etiket tasinirsa):
#:   ghcr.io token al -> /v2/<repo>/manifests/<surum> HEAD ->
#:   `docker-content-digest` basligi. Accept basligi OCI image index
#:   olmali; aksi halde tek mimarinin digest'i doner ve cok mimarili
#:   pull calismaz.
#:
#: Yeni surum eklerken buraya digest'i de yazin: `APPROVED_GATEWAY_VERSION`
#: burada karsiligi olmadan yukseltilirse kurulum FAIL-CLOSED olur (bkz.
#: `production_image_ref`) — sessizce etikete dusmez.
APPROVED_GATEWAY_DIGESTS: dict[str, str] = {
    "1.15.1": "sha256:494b38bc2f9e40d634cf384547563ced321f99d3688279d45aaa69107bdade22",
}


class DigestCozulemedi(RuntimeError):
    """Uretim kurulumu icin degismez referans uretilemedi.

    FAIL-CLOSED: cagiran bunu yakalayip kullaniciya anlatmali; DEGISEBILIR
    etikete dusmek COZUM DEGIL, sorunu gorunmez kilmaktir.
    """


GATEWAY_MIN_GRID: dict[str, str] = {
    # 1.15.0: cihaz basina calisma-zamani sagligi tasiyicisi (`device_health_v1`).
    "1.15.0": "2.107.0",
    # 1.15.1: saat/oturum gozlem alanlari + DNP3_TIME_SYNC=nonlan.
    #         Grid tarafi 2.109.0 ile bu alanlari saklamaya basladi.
    "1.15.1": "2.109.0",
}

#: GELISTIRME etiketleri — yayinlanmamis dal/commit imajlari.
#:
#: `latest` BU LISTEDE DEGIL ve bu ONEMLI: sahadaki mevcut kurulumlarin
#: HEPSI `:latest` izliyor (2026-08-21'e kadar uretim hedefiydi). Onlari
#: "gelistirme kanali" saymak, arayuzde surum bilgisini ve Guncelle
#: butonunu KAPATIRDI — yani bu isin duzeltmeye calistigi 2026-08-11 saha
#: sorununun (ekranda "Surum bilinmiyor") aynisini uretirdik.
DEV_TAGS = frozenset({"main", "master", "edge", "nightly", "dev"})

#: URETIM GUNCELLEME HEDEFI OLAMAYACAK etiketler.
#:
#: `latest` burada: hareketli bir etiket, onaylanan ile kurulanin
#: ayrismasina kapi acar. Ama yukaridaki ayrim korunur — mevcut kurulumun
#: `:latest` IZLEMESI bir sorun degil, onu HEDEF olarak SECMEK sorundur.
NON_PRODUCTION_TARGET_TAGS = DEV_TAGS | {"latest"}


def approved_image_tag() -> str:
    """Onayli surumun ETIKETLI referansi (`repo:1.15.1`).

    Digest cozulemedigi durumlarda kullanilan YEDEK. `:latest` DEGIL:
    semver etiketi yayinlandiktan sonra tasinmaz, dolayisiyla "o an ne
    varsa" belirsizligini tasimaz.
    """
    return f"{GATEWAY_IMAGE_REPO}:{APPROVED_GATEWAY_VERSION}"


def pin(image_ref: str, digest: str | None) -> str:
    """`repo:tag` + digest -> `repo:tag@sha256:...`.

    Digest yoksa referans OLDUGU GIBI doner; cagiran taraf bunun bir
    yedek oldugunu bilir ve loglar.
    """
    taban = (image_ref or "").split("@", 1)[0]
    d = (digest or "").strip()
    return f"{taban}@{d}" if d else taban


def is_production_ref(image_ref: str | None) -> bool:
    """Bu referans URETIM dagitiminda kullanilabilir mi?

    Kural: ya digest'e sabitlenmis olacak, ya da gelistirme etiketi
    OLMAYAN bir semver etiketi tasiyacak. `:latest` her iki testten de
    duser.
    """
    ref = (image_ref or "").strip()
    if not ref:
        return False
    if "@sha256:" in ref:
        return True
    taban = ref.split("@", 1)[0]
    egik = taban.rfind("/")
    iki_nokta = taban.rfind(":")
    if iki_nokta <= egik:
        # Etiket YOK -> docker `latest` varsayar. Uretimde kabul edilmez.
        return False
    return taban[iki_nokta + 1 :].strip().lower() not in NON_PRODUCTION_TARGET_TAGS


def min_grid_for(gateway_version: str | None) -> str | None:
    """Bu gateway surumu icin gereken en dusuk Grid surumu.

    `None` = BILINMIYOR. `"0.0.0"` gibi bir varsayilan DONDURULMEZ:
    bilinmeyen bir surumu "her Grid ile calisir" saymak fail-open olurdu ve
    uyumsuz bir gateway sahaya sessizce inebilirdi.
    """
    s = (gateway_version or "").strip().lstrip("vV")
    if not s:
        return None
    return GATEWAY_MIN_GRID.get(s)


def en_yuksek_bilinen() -> tuple[int, ...]:
    """Tabloda kayitli EN YUKSEK gateway surumu."""
    from app.services.gateway_compatibility import parse_version

    surumler = [parse_version(v) for v in GATEWAY_MIN_GRID]
    return max((s for s in surumler if s), default=(0,))


def uyumlu_mu(gateway_version: str | None, grid_version: str | None) -> tuple[bool, str]:
    """(uygun_mu, gerekce) — uretim guncellemesi icin uyumluluk kapisi.

    UC DURUM DEGIL IKI: bu bir KARAR noktasidir ve karar noktalarinda
    "bilmiyorum" HAYIR demektir. Ama FAIL-CLOSED, RISKIN GERCEKTEN OLDUGU
    YERE daraltilir:

      * TABLODA VARSA          -> Grid surumu karsilastirilir.
      * TABLODAKI EN YUKSEKTEN YENIYSE -> BLOKE.
        Asil risk budur: yeni bir gateway, Grid'in henuz saglamadigi bir
        sozlesme bekleyebilir (1.15.1'in saat/oturum alanlari Grid 2.109.0
        ile geldi). Gateway imaji kendi Grid gereksinimini ILAN ETMIYOR
        (Dockerfile'da `min-grid-version` etiketi yok), dolayisiyla
        bilmedigimiz bir ust surum icin "muhtemelen uyumludur" diyemeyiz.
      * TABLODAKI EN YUKSEKTEN ESKIYSE -> GECER.
        Eski bir gateway YENI Grid gerektirmez; Grid eski gateway'leri
        bilerek destekliyor (bkz. `device_session_readiness` eski uyumluluk
        yolu). Bunlari bloke etmek, sahada geri alma ve eski kurulum
        yollarini kapatirdi — koruma degil, engel olurdu.

    Grid surumu okunamiyorsa hicbir karsilastirma yapilamaz: BLOKE.
    """
    from app.services.gateway_compatibility import parse_version

    mevcut = parse_version(grid_version)
    if not mevcut:
        return (False, "Grid surumu okunamadi; guncelleme baslatilmadi.")

    def _en_az(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
        boy = max(len(a), len(b))
        return a + (0,) * (boy - len(a)) >= b + (0,) * (boy - len(b))

    gereken = min_grid_for(gateway_version)
    if gereken is not None:
        if _en_az(mevcut, parse_version(gereken)):
            return (True, "")
        return (
            False,
            f"Gateway {gateway_version} surumu Grid {gereken} veya ustunu "
            f"gerektiriyor; bu sistem {grid_version}. Once Grid'i guncelleyin.",
        )

    hedef = parse_version(gateway_version)
    if not hedef:
        return (
            False,
            f"Gateway surumu okunamadi ({gateway_version!r}); guncelleme "
            "baslatilmadi.",
        )

    if _en_az(en_yuksek_bilinen(), hedef):
        # Tabloda yok ama bilinen en yuksekten ESKI: Grid onu destekliyor.
        return (True, "")

    return (
        False,
        f"Gateway {gateway_version} bu Grid surumunun tanidigi en yeni "
        f"gateway surumunden ({'.'.join(str(x) for x in en_yuksek_bilinen())}) "
        "daha yeni. Uyumlulugu dogrulanmadigi icin guncelleme baslatilmadi; "
        "once Grid'i guncelleyin.",
    )


def approved_digest(version: str | None = None) -> str | None:
    """Surumun kodda sabitlenmis digest'i (yoksa None)."""
    return APPROVED_GATEWAY_DIGESTS.get(version or APPROVED_GATEWAY_VERSION)


def production_image_ref() -> tuple[str, str]:
    """Yeni kurulumda kullanilacak DEGISMEZ imaj referansi + digest.

    HER ZAMAN `repo:surum@sha256:...` doner. Digest uretilemezse
    `DigestCozulemedi` FIRLATIR — etiketli referansa DUSMEZ.

    NEDEN FAIL-CLOSED
    -----------------
    Onceki surum, kayit defterine ulasilamadiginda etiketli `:1.15.1`e
    duser ve uyari loglardi. Gerekce "yeni kurulumda korunacak bir sey
    yok" idi; ama korunmasi gereken sey CALISAN CONTAINER degil, HANGI
    KODUN KURULDUGU BILGISI. Etiket kayit defterinde tasinabilir bir
    isaretci: ayni etiketle iki farkli sahaya iki farkli gateway kurulmus
    olabilir ve loglarda bunu ayirt edecek hicbir sey kalmaz. Bir
    kurulumun BASARISIZ olmasi, sessizce YANLIS OLMASINDAN iyidir.

    NEDEN ONCE SABIT PIN, SONRA KAYIT DEFTERI
    -----------------------------------------
    Sabit pin `APPROVED_GATEWAY_DIGESTS`ten gelir ve AG GEREKTIRMEZ —
    kurulum GHCR erisimine bagli kalmaz. Kayit defteri yalnizca pin
    bulunmadiginda (henuz yazilmamis bir surum) devreye girer.

    Kayit defteri sabit pinden FARKLI bir digest bildirirse ONAYLI OLAN
    KAZANIR ve durum loglanir: bu, etiketin release sonrasi tasindigi
    anlamina gelir ve kurulumun sessizce baska bir artefakta kaymasi
    tam olarak engellemek istedigimiz sey.
    """
    etiketli = approved_image_tag()
    sabit = approved_digest()

    if sabit:
        _kayit_defteri_dogrula(etiketli, sabit)
        return (pin(etiketli, sabit), sabit)

    # Sabit pin yok (surum listeye eklenirken digest yazilmamis). Son care
    # olarak kayit defterinden cozulur.
    logger.warning(
        "gateway_release_policy %s icin sabit digest yok; kayit defterinden "
        "cozulmeye calisiliyor (APPROVED_GATEWAY_DIGESTS guncellenmeli)",
        APPROVED_GATEWAY_VERSION,
    )
    from app.services import gateway_release_service

    try:
        uzak = gateway_release_service.fetch(etiketli)
    except Exception as exc:  # noqa: BLE001 - ag hatasi anlamli mesaja cevrilir
        raise DigestCozulemedi(
            f"{etiketli} icin degismez digest uretilemedi: kayit defterine "
            f"ulasilamadi ({exc}). Uretim kurulumu degisebilir etikete "
            f"dusurulmez; APPROVED_GATEWAY_DIGESTS icine digest yazin."
        ) from exc

    if uzak.error or not uzak.digest:
        raise DigestCozulemedi(
            f"{etiketli} icin degismez digest uretilemedi: "
            f"{uzak.error or 'digest okunamadi'}. Uretim kurulumu degisebilir "
            f"etikete dusurulmez; APPROVED_GATEWAY_DIGESTS icine digest yazin."
        )
    return (pin(etiketli, uzak.digest), uzak.digest)


def _kayit_defteri_dogrula(etiketli: str, sabit: str) -> None:
    """Etiket tasinmis mi diye bakar; kurulumu ENGELLEMEZ.

    Kayit defterine ulasilamamasi bir sorun DEGIL: sabit pin zaten
    elimizde ve kurulum ona gore yapilacak. Yalnizca etiketin bizden
    habersiz baska bir artefakta tasinmis olmasi loglanir.
    """
    from app.services import gateway_release_service

    try:
        uzak = gateway_release_service.fetch(etiketli)
    except Exception:  # noqa: BLE001 - dogrulama iyimserdir
        return
    if uzak.error or not uzak.digest:
        return
    if uzak.digest != sabit:
        logger.warning(
            "gateway_release_policy ETIKET TASINMIS: %s kayit defterinde %s "
            "gosteriyor, onayli digest %s. Kurulum ONAYLI digest ile yapilir.",
            etiketli, uzak.digest, sabit,
        )


__all__ = [
    "APPROVED_GATEWAY_DIGESTS",
    "APPROVED_GATEWAY_SOURCE_SHA",
    "APPROVED_GATEWAY_VERSION",
    "DigestCozulemedi",
    "approved_digest",
    "DEV_TAGS",
    "NON_PRODUCTION_TARGET_TAGS",
    "GATEWAY_IMAGE_REPO",
    "GATEWAY_MIN_GRID",
    "approved_image_tag",
    "en_yuksek_bilinen",
    "is_production_ref",
    "min_grid_for",
    "pin",
    "production_image_ref",
    "uyumlu_mu",
]
