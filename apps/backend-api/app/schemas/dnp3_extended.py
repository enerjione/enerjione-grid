from typing import Literal

from pydantic import BaseModel, Field

IpEndpointType = Literal["initiating", "listening"]

#: Gateway v1.14.0 oturum politikasi.
#:
#: `continuous` — gateway periyodik DNP3 taramasi yapar (varsayilan; mevcut
#: kurulumlarin davranisi DEGISMEZ).
#: `smart` — cihaz raporunu gonderip baglantiyi kapatinca bu NORMAL UYKU
#: sayilir; gateway aktif yoklamaya girmez.
#: `auto` — rejim cihazin MASTER "Operation Mode" noktasindan CALISMA ANINDA
#: turetilir (Smart -> etkin smart, Boost -> etkin continuous).
#:
#: UC TIPI ILE MOD ORTOGONALDIR (gateway v1.14.0)
#: ----------------------------------------------
#: `ip_endpoint_type` TCP baglantisini KIM actigini soyler; `session_policy`
#: cihazin modemini kapatip kapatmadigini. v1.13.0 `smart`/`auto` + `listening`
#: kombinasyonunu reddediyordu ve bu, iki BAGIMSIZ kavrami birbirine
#: karistiran bir kisittir: sabit IP'li bir Horstmann'in Smart modda
#: calismasini imkansiz kiliyordu. v1.14.0 kisiti KALDIRDI, alti kombinasyon
#: da gecerlidir (bkz. sozlesme `smart_wrong_endpoint_behavior`).
#:
#: `master_ip_port` zorunlulugu YALNIZCA `initiating` icin gecerli olmaya
#: DEVAM EDER.
SessionPolicy = Literal["continuous", "smart", "auto"]

#: Politikanin gateway'in modemi uyutmasina IZIN verdigi degerler. `auto`
#: dahildir: calisma aninda `smart`a donebilir, dolayisiyla Dial-In gibi
#: Smart'a ozel ayarlar `auto` icin de ANLAMLIDIR ve gizlenmez/silinmez.
SMART_CAPABLE_POLICIES: frozenset[str] = frozenset({"smart", "auto"})

#: Dial-In araligi (dakika) — cihazin zamanlanmis raporlama sikligi.
#:
#: ARALIK GATEWAY'DEN DEGIL FIZIKSEL CIHAZDAN GELIR. Gateway sozlesmesi
#: "60..1440" der ve orada durur; Horstmann SN2.0 config katalogu (girdi
#: `2010C6`, `app/data/horstmann_sn2_config_catalog.json`) bir kural DAHA
#: koyar: "Deger 1440 dakikanin (24 saat) boleni olmalidir".
#:
#: Yalnizca araligi dogrulamak YETMEZ: 100 dk araliktadir ama 1440'in boleni
#: DEGILDIR. Cihaz boyle bir degeri kabul etmez; Grid kaydeder, config
#: dosyasina yazar ve operator ayarin uygulandigini SANIR. Bu yuzden bolen
#: sarti burada, uretildigi yerde dogrulanir.
DIAL_IN_INTERVAL_MIN = 60
DIAL_IN_INTERVAL_MAX = 1440
DIAL_IN_DAY_MINUTES = 1440

#: Ayni ayarin FIZIKSEL cihazdaki karsiligi (Horstmann CatIndex).
#:
#: IKI AYRI DIAL-IN VARDIR ve karistirilmamalidir:
#:   * `Dnp3ExtendedSettings.dial_in_interval_min` -> GATEWAY'e gider; gateway
#:     bununla "rapor gecikti mi" hesabini yapar.
#:   * `2010C6` -> CIHAZIN KENDI yapilandirma dosyasindaki (`<seri>_
#:     Configuration.csv`) deger; cihazin GERCEKTEN ne siklikta raporladigini
#:     belirleyen tek sey budur.
#:
#: Ikisi ayrisirsa gateway yanlis ana gore gecikme olcer: cihaz 240 dk'da bir
#: raporlarken gateway 60 dk bekler ve saglikli cihazi surekli "gecikmis"
#: gosterir. Bu sabit, iki yolu birbirine baglayan tek referanstir.
DIAL_IN_CAT_INDEX = "2010C6"


def dial_in_gecerli_degerler() -> tuple[int, ...]:
    """1440'in boleni olan ve araliga giren TUM Dial-In degerleri.

    Sabit liste yazmak yerine turetiyoruz: liste elle bakilirsa bir gun
    araligi degistirildiginde sessizce ayrisir.
    """
    return tuple(
        d
        for d in range(DIAL_IN_INTERVAL_MIN, DIAL_IN_INTERVAL_MAX + 1)
        if DIAL_IN_DAY_MINUTES % d == 0
    )


#: Haberlesme toleransi (dakika) — zamanlanmis rapor geciktiginde haberlesme
#: kaybi saymadan once beklenecek EK sure.
#:
#: YUZDE DEGIL SABIT PAYDIR (urun karari). "Dial-In * 1.5" gibi bir oran, 24
#: saatlik bir Dial-In'i 36 saatlik alarm esigine cevirir — yani gunde bir
#: raporlayan bir cihazin oldugu ertesi gun ogleden sonra anlasilir.
COMMUNICATION_GRACE_MIN_MIN = 5
COMMUNICATION_GRACE_MIN_MAX = 30
COMMUNICATION_GRACE_MIN_DEFAULT = 15

#: `smart_listen_reconnect_max_sec` araligi (gateway sozlesmesi 5..600).
#: Bu bir PING/PROBE araligi DEGILDIR: listening kanaldaki ChannelRetry
#: yeniden baglanma TAVANIDIR (taban 1 sn'de kalir). Eski
#: `smart_listen_probe_interval_sec` adi bu yuzden KULLANILMAZ.
SMART_LISTEN_RECONNECT_MIN_SEC = 5
SMART_LISTEN_RECONNECT_MAX_SEC = 600

#: `smart_max_silence_sec` icin CIHAZ SEVIYESI gecerli araligi (gateway
#: v1.12.0 sozlesmesi).
#:
#: 0 BILEREK DISARIDA: gateway tarafinda 0, ENV duzeyindeki
#: `DNP3_SMART_MAX_SILENCE_SEC` icin "devre disi" anlamina gelir. Cihaz
#: seviyesinde ayni degeri kabul etmek iki farkli anlami tek alana yuklerdi;
#: cihazda "esik yok" demenin yolu None'dir (bkz. asagidaki alan).
SMART_MAX_SILENCE_MIN_SEC = 60
SMART_MAX_SILENCE_MAX_SEC = 2_592_000  # 30 gun

#: Horstmann SN2 fabrika master (link layer) adresi. Frontend
#: `DEFAULT_DNP3_EXTENDED.master_address` ile AYNI olmali — ayrisirsa okuma
#: yolu `null` doner, formdaki 100 ezilir ve saha cihazi susar (v2.54.1-2.54.3).
DEFAULT_MASTER_ADDRESS = 100


class Dnp3ExtendedSettings(BaseModel):
    """Uç birimdeki (gateway/collector) DNP3 oturum parametreleri; merkez sadece saklar ve gösterir.

    MASTER_ADDRESS: VARSAYILAN 100, TIP Optional (2026-08-07 saha arizasi)
    ----------------------------------------------------------------------
    Bu iki sey AYRI konudur ve v2.54.1'de birbirine karistirildi:

    * VARSAYILAN DEGER (100) okuma/gosterim yolunu ilgilendirir. Horstmann SN2
      fabrika degeri 100'dur; cihazin kendi ekraninda da 100 yazar ve gateway
      log'undaki `local=100` bunu dogrular. Kullanicinin talebi cihazda HICBIR
      ayar yapmadan IP + port + Outstation ID girip cihaz ekleyebilmek.
    * TIPIN Optional olmasi YAZMA yolunu ilgilendirir: istemcinin hic
      gondermedigi alani kayit sirasinda uydurup diske sabitlememek icin
      (bkz. `dnp3_extended_to_store`, `exclude_unset`).

    `exclude_unset` alanin VARSAYILAN DEGERINE bakmaz, yalnizca istemcinin
    gercekten gonderip gondermedigine bakar. Yani varsayilani 100 yapmak
    sessiz-yazim korumasini BOZMAZ. v2.54.1 varsayilani None'a cekerek
    korumayi degil, yalnizca dogru degeri kaybetti.

    NEDEN None GORUNUMDE DE TEHLIKELI: gateway'in kendi varsayilani
    DNP3_LOCAL_ADDRESS=1'dir. DNP3 outstation'lari BEKLEMEDIKLERI master
    adresinden gelen istekleri SESSIZCE ATAR: TCP baglantisi kurulur, uygulama
    katmani hic cevap vermez (link_open -> 15sn fresh frame yok -> lost ->
    forced_relink dongusu); cihazin kendi ekraninda "DNP3 session var" yazar
    (dogru — TCP oturumu). Ayirt edici kanit: ayni gateway'deki SIMULATOR
    cihazlari sorunsuz calisir — simulator master adresini dogrulamaz, gercek
    outstation dogrular. Yani bu hata SIMULASYON TESTLERINDE GORUNMEZ.
    """

    ip_endpoint_type: IpEndpointType = "listening"
    master_ip_address: str = ""
    master_ip_port: int = Field(default=20002, ge=1, le=65535)
    #: Saha cihazinin BEKLEDIGI master (link layer) adresi. Horstmann SN2
    #: fabrika degeri 100. Tip Optional ama VARSAYILAN 100 — gerekce yukarida.
    master_address: int | None = Field(default=DEFAULT_MASTER_ADDRESS, ge=0, le=65535)
    unsolicited_reporting: bool = True
    unsolicited_on_startup: bool = True
    unsolicited_class_mask_id: int = Field(default=7, ge=0, le=255)
    link_status_period_min: int = Field(default=0, ge=0)
    enable_self_address: bool = False
    validate_source_address: bool = False
    session_timeout_listening_sec: int = Field(default=60, ge=1, le=86400)
    socket_listening_timeout_sec: int = Field(default=600, ge=1, le=86400)

    # ----- B5 / gateway v1.14.0: akilli oturum ---------------------------
    #
    # UC TIPI KISITI YOK (v1.14.0). Alti kombinasyon da gecerlidir; eskiden
    # burada duran "`smart` yalnizca `initiating` ile" kurali v1.13.0
    # davranisiydi ve KALDIRILDI (bkz. `SessionPolicy` ve
    # `validate_session_policy`).
    #
    # Alan seviyesinde capraz dogrulama yapilmamasinin gerekcesi DEGISMEDI:
    # PATCH kismi gelir ve `ip_endpoint_type` o istekte gonderilmemis
    # olabilir; model uzerinde dogrulamak, yalnizca `session_policy`
    # gonderen gecerli bir istegi VARSAYILAN "listening" yuzunden
    # reddederdi. Capraz kurallar EFEKTIF (birlestirilmis) durum uzerinde
    # `validate_session_policy` ile isletilir.
    session_policy: SessionPolicy = "continuous"

    #: Cihaz seviyesi sessizlik esigi. None/eksik = "bu cihaz icin OZEL esik
    #: YOK" — devre disi DEMEK DEGILDIR. Gateway cozum sirasi:
    #: 1) gecerli cihaz degeri, 2) `DNP3_SMART_MAX_SILENCE_SEC` env,
    #: 3) devre disi.
    smart_max_silence_sec: int | None = Field(
        default=None,
        ge=SMART_MAX_SILENCE_MIN_SEC,
        le=SMART_MAX_SILENCE_MAX_SEC,
    )

    # ----- Gateway v1.14.0: Dial-In farkindali Smart yasam dongusu -------
    #
    # Bu uc alan da None kalabilir ve None "YOK" demektir, "kapali" DEGIL —
    # `smart_max_silence_sec` ile ayni sozlesme. Eski cihaz kayitlari bu
    # alanlar olmadan calismaya devam eder (bkz. `smart_max_silence_sec`
    # legacy davranisi); hicbiri sessizce doldurulmaz.
    #
    #: Zamanlanmis rapor araligi. Gateway bunu `smart_max_silence_sec`in
    #: YERINE degil YANINDA kullanir: rapor gecti ama sessizlik esigi
    #: dolmadiysa `report_late` bayragi kalkar (DEGRADED), haberlesme kaybi
    #: SAYILMAZ.
    dial_in_interval_min: int | None = Field(
        default=None,
        ge=DIAL_IN_INTERVAL_MIN,
        le=DIAL_IN_INTERVAL_MAX,
    )

    #: Rapor gecikmesine taninan ek sure. `smart_max_silence_sec` bu ikisinden
    #: TURETILIR (bkz. `cozulmus_max_silence_sec`).
    communication_grace_min: int | None = Field(
        default=None,
        ge=COMMUNICATION_GRACE_MIN_MIN,
        le=COMMUNICATION_GRACE_MIN_MAX,
    )

    #: Listening kanalda yeniden baglanma TAVANI (saniye). None = kutuphane
    #: varsayilani (ChannelRetry.Default, ustel 1..60 sn) — gateway
    #: sozlesmesinde olculmus ve Smart icin yeterli bulunmustur.
    smart_listen_reconnect_max_sec: int | None = Field(
        default=None,
        ge=SMART_LISTEN_RECONNECT_MIN_SEC,
        le=SMART_LISTEN_RECONNECT_MAX_SEC,
    )


def merge_dnp3_extended(stored: dict | None) -> Dnp3ExtendedSettings:
    """Kayitli sozlugu GORUNTULEME icin tamamlar (eksik alanlara varsayilan).

    Yalnizca okuma/gosterim yolunda kullanilir. YAZMA yolunda
    `dnp3_extended_to_store` kullanilir — orada eksik alan TAMAMLANMAZ,
    yoksa her kayit islemi dokunulmamis alanlari diske sabitler.

    DISKTEKI `None` = "YOK" SAYILIR (v2.54.1-2.54.3 onarimi)
    -------------------------------------------------------
    v2.54.1'de form varsayilani da None'a cekilmisti; o pencerede cihaz
    kaydini ACIP KAYDEDEN operator diske acikca `master_address: null`
    yazdirdi. Bu deger kaldigi surece gateway config'i alani bos gonderir,
    gateway DNP3_LOCAL_ADDRESS=1 kullanir ve 100 bekleyen saha cihazi
    SESSIZCE susar — surum 2.54.3'e cikmak bunu TEK BASINA duzeltmez.

    Bu yuzden `None` degerler "hic yazilmamis" gibi elenir ve varsayilan
    devreye girer; kayitli cihaz elle mudahale olmadan iyilesir. Alanin
    Optional kalmasi yalnizca eski kayitlarin ve `exclude_unset` yazma
    yolunun dogrulanabilmesi icindir, "1 kullan" demenin yolu DEGILDIR.
    """
    base = Dnp3ExtendedSettings().model_dump()
    if not stored:
        return Dnp3ExtendedSettings.model_validate(base)
    if not isinstance(stored, dict):
        return Dnp3ExtendedSettings.model_validate(base)
    clean = {
        k: v for k, v in stored.items() if k not in ("tls_dnp3",) and v is not None
    }
    base.update({k: v for k, v in clean.items() if k in base})
    return Dnp3ExtendedSettings.model_validate(base)


def dnp3_extended_to_store(value: object) -> dict | None:
    """Diske YAZILACAK sozluk — istemcinin ACIKCA gonderdigi alanlar.

    NEDEN (2026-08-07): yazma yolunda tum alanlari somutlastirmak, operatorun
    hic dokunmadigi ayarlari merkezi varsayilanlarla SABITLIYOR. master_address
    ornegi haberlesmeyi tamamen kesti (bkz. Dnp3ExtendedSettings docstring'i);
    ayni risk unsolicited_*, validate_source_address, session_timeout_* icin de
    gecerli. Pydantic `model_fields_set` istemcinin gercekten gonderdigi
    anahtarlari tutar; yalnizca onlar yazilir.
    """
    if value is None:
        return None
    if isinstance(value, Dnp3ExtendedSettings):
        return value.model_dump(exclude_unset=True)
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k not in ("tls_dnp3",)}
    return None


def effective_dnp3_extended(stored: dict | None, incoming: object) -> Dnp3ExtendedSettings:
    """Yazma SONRASI olusacak ayari hesaplar — dogrulama bunun uzerinden yapilir.

    NEDEN AYRI BIR ADIM (B5): PATCH govdesi KISMI gelir ve depo katmani
    gonderilen anahtarlari mevcut sozlugun UZERINE bindirir (bkz.
    `DeviceRepository.update`). Dolayisiyla "gecerli mi" sorusunun cevabi ne
    yalnizca gelen govdeden ne de yalnizca diskteki kayittan okunabilir:

      * Yalnizca govdeye bakmak — diskte `initiating` olan bir cihaza
        `session_policy=smart` gonderen GECERLI istegi, modeldeki varsayilan
        `listening` yuzunden reddederdi.
      * Yalnizca diske bakmak — diskte `smart` olan bir cihazi
        `ip_endpoint_type=listening` ile guncelleyen GECERSIZ istegi kabul
        ederdi; yasak kombinasyon sessizce diske yazilirdi.

    Ikisinin BIRLESIMI tek dogru zemindir.

    `incoming is None` = istemci alani acikca `null` gonderdi: depo katmani
    sozlugun TAMAMINI siler ve varsayilanlar gecerli olur; burada da oyle
    modellenir.
    """
    birlesik: dict = dict(stored) if isinstance(stored, dict) else {}
    gelen = dnp3_extended_to_store(incoming)
    if gelen is None:
        birlesik = {}
    else:
        birlesik.update(gelen)
    return merge_dnp3_extended(birlesik)


def validate_session_policy(settings: Dnp3ExtendedSettings) -> None:
    """Oturum ayarlarinin KENDI ICINDE tutarli olmasini dogrular.

    NE DEGISTI (gateway v1.14.0)
    ----------------------------
    Eskiden burada `smart` + `listening` KOSULSUZ reddediliyordu (422).
    O kural v1.12/v1.13 gateway davranisini yansitiyordu ve v1.14.0 ile
    GECERSIZ: uc tipi ile mod ortogonal iki kavramdir ve alti kombinasyon da
    desteklenir. Kisiti burada tutmak, sabit IP'li bir Horstmann'i Smart
    modda calistirmayi Grid tarafinda imkansiz birakirdi.

    SURUM KAPISI BURADA DEGIL
    -------------------------
    "Bu gateway bu kombinasyonu destekliyor mu" sorusu ISTENEN yapilandirmayi
    reddetmez — cunku gateway yarin guncellenebilir ve istenen ayar dogru
    kalir. O soru RENDER anininda sorulur (bkz. `gateway_compatibility` +
    `device_gateway_config`): eski gateway'e v1.14-only payload GONDERILMEZ
    ve operatore ozelligin sahada HENUZ AKTIF OLMADIGI soylenir.

    Bu ayrim bilincli: DESIRED (kullanicinin istedigi) ile RENDERED (o
    gateway'e fiilen giden) ayni sey degildir.

    Cagiran taraf HTTP 422'ye cevirir (bkz. `api/devices.py`).
    """
    # Dial-In: fiziksel cihaz kisiti (1440'in boleni). Pydantic yalnizca
    # araligi dogruluyor; bolen sarti ancak burada yakalanir.
    aralik = settings.dial_in_interval_min
    if aralik is not None and DIAL_IN_DAY_MINUTES % aralik != 0:
        gecerli = dial_in_gecerli_degerler()
        raise ValueError(
            f"Dial-In araligi {aralik} dk gecersiz: deger 1440'in (24 saat) "
            "boleni olmalidir, yoksa Horstmann yapilandirmayi kabul etmez. "
            f"Gecerli degerler: {', '.join(str(d) for d in gecerli)}."
        )

    # Sessizlik esigi, BEKLENEN RAPORDAN once dolmamali. Aksi halde cihaz
    # zamaninda raporlasa bile "haberlesme kaybi" damgasini yer.
    esik = settings.smart_max_silence_sec
    if aralik is not None and esik is not None:
        tolerans = settings.communication_grace_min
        if tolerans is None:
            tolerans = COMMUNICATION_GRACE_MIN_DEFAULT
        beklenen = (aralik + tolerans) * 60
        if esik < aralik * 60:
            raise ValueError(
                f"Sessizlik esigi ({esik} sn) Dial-In araligindan "
                f"({aralik} dk = {aralik * 60} sn) KISA. Cihaz zamanlanmis "
                "raporunu gondermeden once haberlesme kaybi sayilirdi. "
                f"Onerilen deger: {beklenen} sn (Dial-In + tolerans)."
            )


def cozulmus_max_silence_sec(settings: Dnp3ExtendedSettings) -> int | None:
    """Bu cihaz icin gecerli SESSIZLIK ESIGI (saniye) — tek kaynak.

    KANONIK ILISKI (gateway v1.14.0):

        smart_max_silence_sec = (dial_in_interval_min + communication_grace_min) * 60

    Ornek: 60 dk Dial-In + 15 dk tolerans -> 4500 sn.

    COZUM SIRASI ve NEDENI
    ----------------------
    1. ACIKCA yazilmis `smart_max_silence_sec` KAZANIR. Eski cihazlarda bu
       alan Dial-In/tolerans olmadan doldurulmustur ve onlarin davranisini
       sessizce degistirmek, calisan bir sahayi bozmak olurdu (§15).
    2. Yoksa Dial-In + tolerans'tan TURETILIR.
    3. Ikisi de yoksa None — "cihaz seviyesinde ezme yok". Gateway kendi
       env yedegine duser; bu "denetim kapali" DEMEK DEGILDIR.

    Tolerans yazilmamissa varsayilan 15 dk kullanilir; DISKE yazilmaz
    (`dnp3_extended_to_store` dokunulmamis alani sabitlemez).
    """
    if settings.smart_max_silence_sec is not None:
        return settings.smart_max_silence_sec
    aralik = settings.dial_in_interval_min
    if aralik is None:
        return None
    tolerans = settings.communication_grace_min
    if tolerans is None:
        tolerans = COMMUNICATION_GRACE_MIN_DEFAULT
    return (aralik + tolerans) * 60
