"""Gateway surumu <-> Grid ozelligi uyumluluk matrisi — TEK KAYNAK.

NEDEN AYRI BIR MODUL
--------------------
"Bu ozellik hangi gateway surumunden itibaren calisir" sorusunun cevabi
BIRDEN COK yerde lazim: cihaz kaydederken uyari uretmek, gateway listesinde
rozet gostermek, guncelleme ekraninda "guncelleyince ne kazanirsin" demek.
Ucune ayri ayri yazilirsa uc kopya sessizce ayrisir ve en kotu bicimde
ayrisir: arayuz "destekleniyor" derken saha sessizce baska bir rejimde
calisir.

SURUM == OZELLIK VARSAYIMI YAPILMAZ
-----------------------------------
Matris ozellik ADIYLA anahtarlanir, surum numarasiyla degil. Gateway 1.14
cikmasi `smart_session`in minimumunu 1.14 YAPMAZ — minimum, ozelligin
GERCEKTEN calismaya basladigi surumdur ve bir kez saptandiktan sonra
ileri kaymaz. Yeni bir ozellik gelirse matrise YENI BIR SATIR eklenir.

UYARIR, REDDETMEZ
-----------------
Uyumsuzluk cihaz yapilandirmasini 422 ile reddetmez (urun karari). Sebep:
mesru akis "once cihazi yapilandir, sonra gateway'i guncelle"dir; reddetmek
bunu imkansiz kilar ve cihazi gateway surumune rehin eder (cihaz baska bir
gateway'e tasinabilir). Ama gorunmez de birakilmaz — sessiz ayrisma, B5'in
kapatmak icin var oldugu hata sinifinin ta kendisidir.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.dnp3_extended import SMART_CAPABLE_POLICIES
from app.services.version_service import parse_version

#: Ozellik -> gerektirdigi EN DUSUK gateway surumu.
#:
#: `smart_session` (B5 / G-SMART-01): cihaz basina `session_policy` +
#: `smart_max_silence_sec`. Gateway 1.12.0'da uygulandi; alanlari
#: gondermeyen backend'lerde davranis degismedigi icin daha eski
#: gateway'ler bu alanlari YOK SAYAR — yani saha kirilmaz, yalnizca
#: arayuzdeki "Akilli" iddiasi karsiliksiz kalir.
FEATURE_MIN_VERSION: dict[str, str] = {
    # Akilli oturum kavraminin KENDISI (initiating uctaki Smart). 1.12.0'da
    # calisiyordu ve minimumu ILERI KAYMAZ (bkz. modul basligi).
    "smart_session": "1.12.0",
    # `session_policy=auto` DEGERI. 1.13.0 ve oncesi bu degeri TANIMAZ ve
    # sozlesme geregi tanimsiz deger TUM config'i reddeder
    # (`session_policy_invalid_behavior: reject_config`) — yani eski bir
    # gateway'e `auto` gondermek yalnizca o cihazi degil O GATEWAY'DEKI TUM
    # CIHAZLARI dondurur.
    "smart_auto": "1.14.0",
    # `smart`/`auto` + `listening` kombinasyonu. 1.13.0 bunu reject_config ile
    # reddediyordu; 1.14.0 uc tipi kisitini KALDIRDI.
    "smart_listening": "1.14.0",
    # Dial-In farkindali gecikme takibi (`dial_in_interval_min`).
    "dial_in_health": "1.14.0",
    # Listening kanal yeniden baglanma tavani.
    "smart_listen_reconnect": "1.14.0",
    # Cihaz basina calisma-zamani sagliginin AYRI BIR UCA POST edilmesi
    # (`POST /gateways/{code}/device-health`, sema `device_health_v1`).
    #
    # `smart_session` ile ORTAK KULLANILMAZ ve onun uzerine de kurulmaz:
    # 1.14.0 gateway'inde Smart Listening CALISIR ama bu tasiyici YOKTUR.
    # Ayni satiri paylassalardi, 1.14.0'daki calisan bir Smart kurulumu bu
    # uc yuzunden "uyumsuz" gorunur; ya da tersine, tasiyicisi olmayan bir
    # gateway "saglik gonderiyor" sanilirdi. Yetenek ADIYLA anahtarlanir
    # (bkz. modul basligi) — surumle degil.
    "device_runtime_health_transport": "1.15.0",
}


def gerekli_yetenekler(session_policy: str, ip_endpoint_type: str) -> tuple[str, ...]:
    """Bu kombinasyonun gateway'den istedigi YENI yetenekler.

    TEK KAYNAK: hem "render edilebilir mi" karari hem operatore gosterilen
    uyari buradan turer. Iki yerde ayri kural yazmak, arayuz "destekleniyor"
    derken payload'in sessizce dusurulmesine yol acardi.

    `smart_session` BILEREK LISTEDE YOK: o yetenek 1.12.0'dan beri var ve
    `initiating` + `smart` bugune kadar HICBIR surum kapisindan gecmeden
    render ediliyordu. Simdi kapiya sokmak, surumunu bildirmemis (cok yaygin)
    her gateway'de SAHADA CALISAN Smart kurulumlarini sessizce `continuous`a
    dusururdu. Kapi yalnizca v1.14.0 ile GELEN yetenekler icindir;
    `smart_session` icin uyari yolu ayrica duruyor (`smart_session_warning`)
    ve o yol UYARIR, payload'i degistirmez.
    """
    politika = (session_policy or "continuous").strip().lower()
    uc = (ip_endpoint_type or "listening").strip().lower()
    if politika not in SMART_CAPABLE_POLICIES:
        return ()
    gerekli: list[str] = []
    if politika == "auto":
        gerekli.append("smart_auto")
    if uc == "listening":
        gerekli.append("smart_listening")
    return tuple(gerekli)


def eksik_yetenekler(
    session_policy: str, ip_endpoint_type: str, gateway_version: str | None
) -> tuple[str, ...]:
    """Gateway'in KARSILAYAMADIGI yetenekler. Bos demet = oldugu gibi render.

    BILINMEYEN SURUM (None) EKSIK SAYILIR — bilincli. Surumunu bildirmemis
    bir gateway'e `auto` gondermek TUM config'i reddettirebilir; "bilmiyorum"
    durumunda guvenli taraf ozelligi GONDERMEMEK ve operatore acikca
    soylemektir. Ters yon tek bir cihaz ayari yuzunden butun sahayi susturur.
    """
    return tuple(
        ozellik
        for ozellik in gerekli_yetenekler(session_policy, ip_endpoint_type)
        if supports(ozellik, gateway_version) is not True
    )


@dataclass(frozen=True)
class CompatibilityWarning:
    """Tek bir uyumsuzluk bulgusu."""

    feature: str
    required_version: str
    #: Gateway'in bildirdigi surum; bilinmiyorsa None.
    current_version: str | None
    #: Bu uyumsuzluktan etkilenen cihaz sayisi (0 ise ozellik kullanilmiyor).
    affected_devices: int
    message: str


def supports(feature: str, gateway_version: str | None) -> bool | None:
    """Bu gateway surumu ozelligi destekliyor mu?

    UC DURUMLU ve bu bilincli: `None` = BILINMIYOR (gateway surumunu henuz
    bildirmedi). `False` ile ayni saymak "desteklemiyor" iddiasinda bulunmak
    olurdu; bildirmemis bir gateway pekala guncel olabilir. Ayni gerekce
    `LocalGateway.update_available` uc durumlulugunda da gecerli.
    """
    gerekli = FEATURE_MIN_VERSION.get(feature)
    if gerekli is None:
        # Matriste olmayan ozellik kisitsizdir: bilmedigimiz bir sey icin
        # "desteklenmiyor" demeyiz.
        return True
    mevcut = parse_version(gateway_version)
    if not mevcut:
        return None
    return _en_az(mevcut, parse_version(gerekli))


def _en_az(mevcut: tuple[int, ...], gerekli: tuple[int, ...]) -> bool:
    boy = max(len(mevcut), len(gerekli))
    mevcut += (0,) * (boy - len(mevcut))
    gerekli += (0,) * (boy - len(gerekli))
    return mevcut >= gerekli


def smart_session_warning(
    gateway_version: str | None, smart_device_count: int
) -> CompatibilityWarning | None:
    """`smart_session` icin uyari uret (gerekmiyorsa None).

    Cihaz sayisi 0 ise uyari YOK: eski bir gateway, uzerinde akilli cihaz
    olmadigi surece uyumsuz DEGILDIR. Her eski gateway'i uyarmak, gercekten
    etkilenen gateway'i gurultude bogardi.
    """
    if smart_device_count <= 0:
        return None
    destek = supports("smart_session", gateway_version)
    if destek is True:
        return None

    gerekli = FEATURE_MIN_VERSION["smart_session"]
    if destek is None:
        mesaj = (
            f"Bu gateway surumunu henuz bildirmedi; akilli oturum {gerekli}+ "
            f"gerektirir. {smart_device_count} cihaz akilli moda ayarli — "
            "surum dogrulanana kadar gercekten akilli calistiklari "
            "VARSAYILAMAZ."
        )
    else:
        mesaj = (
            f"Akilli oturum gateway {gerekli}+ gerektirir; bu gateway "
            f"{gateway_version}. {smart_device_count} cihaz akilli moda "
            "ayarli ama sahada SUREKLI modda calisiyor — gateway eski "
            "surumde bu alanlari yok sayar. Gateway'i guncelleyin."
        )
    return CompatibilityWarning(
        feature="smart_session",
        required_version=gerekli,
        current_version=gateway_version,
        affected_devices=smart_device_count,
        message=mesaj,
    )


# ---------------------------------------------------------------------------
# SURUM SAPMASI — Grid'in vendor ettigi sozlesme vs. gerektirdigi ozellikler
# ---------------------------------------------------------------------------
#
# Grid, gateway deployment sozlesmesinin bir kopyasini `infra/gateway-contract/`
# altinda tutar ve CI onu yukari akistaki TAG ile karsilastirir. O dosya
# Grid'in "hangi gateway surumunu resmen destekliyorum" beyanidir.
#
# SESSIZ SAPMA RISKI: bir ozellik, vendor edilen surumden DAHA YENI bir
# gateway gerektirebilir. O zaman Grid, resmen desteklemedigi bir surume
# bagimli olur ve bunu kimse fark etmez — ta ki sahada "ayar goruyorum ama
# calismiyor" denene kadar.
#
# Cozum: sapmayi YASAKLAMIYORUZ (mesru olabilir; ozellik once gateway'de
# cikar, Grid sonra vendor eder), ama BEYAN ETTIRIYORUZ. Beyansiz her sapma
# testte kirmizi olur (bkz. GU-20).

#: Bilinen ve KABUL EDILMIS sapmalar: ozellik -> gerekce.
#: Buraya bir satir eklemek bilincli bir karardir; silmek, sapmanin
#: kapandigini (vendor guncellendigini) soyler.
#: v1.14.0 YETENEKLERININ ORTAK GEREKCESI.
#:
#: Dordu de ayni sebeple sapiyor, o yuzden metin tek yerde tutulur: dort ayri
#: kopya zamanla ayrisir ve hangisinin guncel oldugu belirsizlesir.
_V114_SAPMA_GEREKCESI = (
    "Gateway v1.14.0 uc tipi kisitini kaldirdi ve `auto` politikasi + Dial-In "
    "farkindali gecikme takibini getirdi; Grid bu yetenekleri destekliyor. "
    "Vendor edilen sozlesme ise hala v1.11.4 — cunku v1.14.0 icin GitHub "
    "Release ve release ARTIFACT'i henuz uretilmedi ve Grid, tag'deki repo "
    "dosyasini canonical artifact gibi vendor ETMEZ (provenance karari, "
    "2026-08-20). SAHA KIRILMAZ: bu yetenekleri desteklemeyen gateway'e ilgili "
    "payload GONDERILMEZ (bkz. `eksik_yetenekler`), guvenli tarafa `continuous` "
    "dusurulur ve operatore uyari gosterilir. Sapma, v1.14.0 release "
    "artifact'i vendor edildiginde kapanir."
)

#: `device_runtime_health_transport` KENDI gerekcesini tasir — v1.14.0
#: metnini paylasmaz. Sebep farkli: v1.14.0 YAYINLANDI (yalnizca release
#: artifact'i uretilmedi), bu yetenek ise HENUZ BIRLESMEMIS BIR PR'da.
_PR33_SAPMA_GEREKCESI = (
    "Cihaz basina saglik tasiyicisi gateway PR #33'te (commit bd502c49) "
    "tanimli ve HENUZ ACIK; gateway 1.15.0 YAYINLANMADI. Grid ucu once "
    "hazir olmali (sozlesme bolum 9: 'once backend ucu yayina alin, sonra "
    "gateway'lerde bayragi acin'), bu yuzden yetenek matrise sozlesme "
    "vendor edilmeden giriyor. Vendor edilen sozlesme hala v1.11.4. SAHA "
    "KIRILMAZ: gateway tarafinda kanal varsayilan olarak KAPALI "
    "(`DEVICE_HEALTH_PUBLISH_ENABLED=false`) ve tasiyicisi olmayan bir "
    "gateway bu uca hic istek atmaz — Grid de karsiliginda hicbir cihaz "
    "yapilandirmasini degistirmez, yalnizca gozlem tablosu bos kalir. "
    "Sozlesme PR birlesene kadar DEGISEBILIR; wire<->model eslemesi bu "
    "yuzden tek adaptorde toplandi "
    "(`app/services/device_runtime_health_service.py`). Sapma, gateway "
    "1.15.0 release artifact'i vendor edildiginde kapanir."
)

KNOWN_VERSION_DRIFT: dict[str, str] = {
    "device_runtime_health_transport": _PR33_SAPMA_GEREKCESI,
    "smart_auto": _V114_SAPMA_GEREKCESI,
    "smart_listening": _V114_SAPMA_GEREKCESI,
    "dial_in_health": _V114_SAPMA_GEREKCESI,
    "smart_listen_reconnect": _V114_SAPMA_GEREKCESI,
    "smart_session": (
        "B5 (2026-08-20) gateway 1.12.0'in Smart Mode sozlesmesini uyguladi; "
        "Grid o tarihte hala v1.11.4 sozlesmesini vendor ediyordu. Saha "
        "kirilmaz — 1.12.0 oncesi gateway yeni alanlari yok sayar ve "
        "`continuous` calisir — ama arayuzdeki 'Akilli' iddiasi karsiliksiz "
        "kalir. Uyumluluk uyarisi tam da bunu gorunur kilar. Sapma, vendor "
        "edilen sozlesme 1.12.0+ surumune tasindiginda kapanir."
    ),
}


def vendored_contract_version(contract_dir) -> str | None:
    """`infra/gateway-contract/` altindaki EN YUKSEK vendor edilmis surum.

    Dosya adi `v<semver>.json`. Dizin yoksa ya da bosssa None — cagiran
    taraf bunu "beyan yok" olarak degerlendirir; "sorun yok" olarak DEGIL.
    """
    from pathlib import Path

    yol = Path(contract_dir)
    if not yol.is_dir():
        return None
    surumler = []
    for dosya in yol.glob("v*.json"):
        ham = dosya.stem.lstrip("v")
        ayrisan = parse_version(ham)
        if ayrisan:
            surumler.append((ayrisan, ham))
    if not surumler:
        return None
    return max(surumler)[1]


def undeclared_drift(vendored: str | None) -> dict[str, str]:
    """Vendor edilen surumu ASAN ama beyan EDILMEMIS ozellikler.

    Bos sozluk = sapma yok ya da hepsi beyanli. Dolu sozluk = birileri
    Grid'i, resmen desteklemedigi bir gateway surumune sessizce bagladi.
    """
    if not vendored:
        return {}
    taban = parse_version(vendored)
    if not taban:
        return {}
    sapan: dict[str, str] = {}
    for ozellik, gerekli in FEATURE_MIN_VERSION.items():
        if ozellik in KNOWN_VERSION_DRIFT:
            continue
        if not _en_az(taban, parse_version(gerekli)):
            sapan[ozellik] = gerekli
    return sapan
