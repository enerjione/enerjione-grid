from typing import Literal

from pydantic import BaseModel, Field

IpEndpointType = Literal["initiating", "listening"]

#: Gateway v1.12.0 oturum politikasi.
#:
#: `continuous` — gateway periyodik DNP3 taramasi yapar (bugune kadarki tek
#: davranis, varsayilan).
#: `smart` — cihaz raporunu gonderip baglantiyi kapatinca bu NORMAL UYKU
#: sayilir; gateway aktif yoklamaya girmez. YALNIZCA `initiating` uc nokta
#: tipiyle anlamlidir: uykudaki cihaza gateway BAGLANAMAZ, baglantiyi cihaz
#: kurar.
SessionPolicy = Literal["continuous", "smart"]

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

    # ----- B5 / gateway v1.12.0: akilli oturum ---------------------------
    #
    # `smart` YALNIZCA `initiating` ile birlikte gecerlidir; kombinasyon
    # dogrulamasi burada DEGIL, `validate_session_policy` ile EFEKTIF
    # (birlestirilmis) durum uzerinde yapilir. Gerekce: PATCH kismi gelir ve
    # model icindeki `ip_endpoint_type` o istekte gonderilmemis olabilir —
    # model uzerinde dogrulamak, yalnizca `session_policy` gonderen gecerli
    # bir istegi VARSAYILAN "listening" yuzunden reddederdi.
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
    """`smart` + `listening` kombinasyonunu reddeder. Hata: ValueError.

    NEDEN BACKEND'DE (gateway zaten guvenli davraniyorken): gateway v1.12.0
    bu kombinasyonda `continuous`a DUSER, yani saha kirilmaz. Ama o zaman
    arayuzde "Akilli" yazarken cihaz surekli modda calisir — operatorun
    GORDUGU ile sahada OLAN ayrisir. Sessiz ayrisma, gorunur bir hatadan
    daha pahalidir; kombinasyon uretildigi yerde durdurulur.

    Cagiran taraf HTTP 422'ye cevirir (bkz. `api/devices.py`).
    """
    if settings.session_policy != "smart":
        return
    if settings.ip_endpoint_type != "initiating":
        raise ValueError(
            "Akilli oturum (smart) yalnizca 'initiating' uc nokta tipiyle "
            "kullanilabilir: uykudaki cihaza gateway baglanamaz, baglantiyi "
            "cihaz kurar. Uc nokta tipini 'initiating' yapin ya da oturum "
            "politikasini 'continuous' olarak birakin."
        )
