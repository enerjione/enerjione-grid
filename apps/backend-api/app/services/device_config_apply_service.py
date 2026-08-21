"""Yapilandirma uygulama NIYETI — olusturma, uyanmada komut uretme, sonuc.

AKIS
----
    istenen surum
        |
    FTP'ye yazilir (dosya KALICI, cihazi bekler)
        |
    NIYET kaydi (device_config_applications)  <-- KALICI olan sey BU
        |
    cihaz DOGAL OLARAK uyanir  (uzaktan uyandirma YOK)
        |
    taze DNP3 oturum kaniti (device_session_readiness)
        |
    O AN uretilen YENI komut — normal 120 sn tazelik suresiyle
        |
    komut sonucu -> iletildi   ("cihaz yukledi" DEMEK DEGIL)
        |
    cihazin KENDI kaniti -> dogrulandi

NEDEN KOMUT DEGIL NIYET KALICI
------------------------------
`command_max_age_sec = 120` bir GUVENLIK INVARYANTIDIR: operatorun saatler
onceki karari sahanin su anki durumu icin gecerli olmayabilir. Uyuyan cihaz
icin o sureyi uzatmak, ayni kanaldan gecen KESICI komutlarini da kapsayan
o invaryanti kaldirmak olurdu. Bu modul TTL'e, `delivery_not_after`a,
kiralama/idempotency mantigina DOKUNMAZ; yalnizca "komut ne zaman
URETILECEK" sorusunu erteler.

EXACTLY-ONCE
------------
Ayni niyet icin iki komut uretilmemeli. Garanti UC KATMANLI:
  1. `device_config_applications` uzerinde KISMI UNIQUE INDEX — cihaz basina
     en fazla bir ACIK niyet (veritabani seviyesinde; birden fazla uvicorn
     worker'i ayri sureclerdir, uygulama ici kilit onlari baglamaz).
  2. Durum gecisi `SELECT ... FOR UPDATE` ile kilitli satirda yapilir ve
     `BEKLIYOR -> KUYRUKTA` tek yonlu; ikinci istek satiri artik `BEKLIYOR`
     gormez.
  3. Gecis, sagligi yazan transaction'in ICINDE olur. Backend yeniden
     baslarsa ya ikisi de yazilmistir ya hicbiri.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.device_config import DeviceConfigVersion
from app.models.device_config_application import (
    ACIK_DURUMLAR,
    BASARISIZ,
    BEKLIYOR,
    DOGRULANDI,
    GECERSIZ,
    ILETILDI,
    KUYRUKTA,
    DeviceConfigApplication,
)
from app.models.device_runtime_health import DeviceRuntimeHealth
from app.services import device_session_readiness as hazirlik_svc
from app.services.event_service import record_event

logger = logging.getLogger(__name__)

#: Uygulama komutunun slug'i. Cihaza "FTP'deki yeni dosyani oku" der.
CONFIG_SLUG = "config_update"

#: `BEKLIYOR` durumunu yeniden disari verir: cagiran katmanlar (or.
#: `/config/apply`) durum karsilastirmasi icin ayrica model modulunu
#: import etmek zorunda kalmasin.
BEKLIYOR_DURUMU = BEKLIYOR

#: Cihazin kendi bildirdigi son yapilandirma damgasi.
READBACK_SIGNAL = "master.info_last_configuration_update"

#: Kac kez komut uretilebilir. SONSUZ DENEME YOK: cihaz uyaniyor, komut
#: uretiliyor, teslim edilemeden tekrar uyuyor... Bu tavana carpinca niyet
#: `basarisiz` olur ve operator GORUR. Sessizce donmeye devam etmek, sahada
#: hicbir zaman fark edilmeyen bir dongu yaratirdi.
AZAMI_DENEME = 5

#: Dogrulama kanit siniflari.
KANIT_CIHAZ_DOSYASI = "cihaz_dosyasi"
KANIT_DAMGA = "damga_degisti"


def ozet(raw: bytes) -> str:
    """FTP'ye yazilan baytlarin sha256'si (hex)."""
    return hashlib.sha256(raw).hexdigest()


def _readback(db: Session, device_id: int) -> str | None:
    """Cihazin bildirdigi son yapilandirma damgasi (ham metin)."""
    from app.models.telemetry_latest import TelemetryLatest

    deger = db.execute(
        select(TelemetryLatest.value_string).where(
            TelemetryLatest.device_id == device_id,
            TelemetryLatest.signal_key == READBACK_SIGNAL,
        )
    ).scalar()
    kirpik = (deger or "").strip()
    return kirpik[:120] or None


def acik_niyet(db: Session, device_id: int, *, kilitle: bool = False):
    """Cihazin ACIK niyeti (varsa).

    `kilitle=True` -> `SELECT ... FOR UPDATE`. Gecis yapacak her cagiran
    bunu kullanmali; kilitsiz okuyup yazmak iki surecin ayni satiri
    `BEKLIYOR` gormesine izin verirdi.
    """
    sorgu = select(DeviceConfigApplication).where(
        DeviceConfigApplication.device_id == device_id,
        DeviceConfigApplication.state.in_(ACIK_DURUMLAR),
    )
    if kilitle:
        sorgu = sorgu.with_for_update()
    return db.scalars(sorgu).first()


def gecersiz_kil(
    db: Session, device: Device, *, simdi: datetime, sebep: str
) -> DeviceConfigApplication | None:
    """Varsa acik niyeti `gecersiz_kilindi` yapar ve doner.

    NEDEN GEREKLI: cihaz uyurken v10 bekliyorken kullanici v11 gonderirse,
    cihaz uyandiginda v10'un uygulanmasi YANLIS olurdu. Ustelik FTP'de
    cihaz basina TEK dosya vardir ve v11 v10'un uzerine yazmistir — v10
    niyeti uyansa v11'in dosyasini yukletir ve kayitta yanlis surum yazardi.

    Gecmis SILINMEZ: satir kalir, yalnizca durumu degisir.
    """
    onceki = acik_niyet(db, device.id, kilitle=True)
    if onceki is None:
        return None
    onceki.state = GECERSIZ
    onceki.closed_at = simdi
    onceki.failure_reason = sebep
    record_event(
        db,
        category="device",
        event_type="config_superseded",
        device_code=device.code,
        message=f"{device.name}: bekleyen yapilandirma istegi degistirildi — {sebep}",
        metadata={"application_id": onceki.id},
    )
    return onceki


def niyet_olustur(
    db: Session,
    *,
    device: Device,
    surum: DeviceConfigVersion,
    raw: bytes,
    ftp_path: str | None,
    actor: str | None,
    simdi: datetime,
) -> DeviceConfigApplication:
    """FTP'ye yazildiktan SONRA cagrilir; kalici niyeti olusturur.

    Commit ETMEZ — cagiran taraf kendi transaction'ini yonetir.
    """
    gecersiz_kil(
        db,
        device,
        simdi=simdi,
        sebep=f"Daha yeni yapilandirma istegi (v{surum.version}) ile degistirildi.",
    )
    # Ustteki gecersiz kilma AYNI transaction'da; kismi unique index yeni
    # satiri ancak eskisi kapandiktan sonra kabul eder, o yuzden flush sart.
    db.flush()

    niyet = DeviceConfigApplication(
        device_id=device.id,
        config_version_id=surum.id,
        state=BEKLIYOR,
        requested_by=actor,
        requested_at=simdi,
        ftp_staged_at=simdi,
        ftp_path=ftp_path,
        ftp_sha256=ozet(raw),
    )
    db.add(niyet)
    db.flush()
    return niyet


def _ftp_hala_bizim_mi(db: Session, device_id: int, niyet: DeviceConfigApplication) -> bool:
    """FTP'deki dosya HALA bu niyetin dosyasi mi?

    Dosya adi cihaz basina SABITTIR (`<seri>_Configuration.csv`) ve yeni
    surum eskisinin USTUNE yazar. Bu kontrol olmadan, arada baska bir yoldan
    (elle yukleme, cihazin kendi yazdigi dosya) degismis bir dosyayi
    yukletip kayda yanlis surumu isleyebilirdik.

    Kontrol EDILEMIYORSA (FTP'ye ulasilamiyor) `True` DONMEZ: kanitsiz
    devam etmek tam da kapatmaya calistigimiz sey.
    """
    from app.services import device_config_service as cfg_svc
    from app.services import ftp_client_service as ftp

    try:
        dosya_adi = cfg_svc.config_filename(db, device_id)
        mevcut = ftp.find_config_on_ftp(db, dosya_adi)
    except Exception as exc:  # noqa: BLE001 - her hata "kanit yok" demektir
        logger.warning(
            "config_apply FTP dogrulamasi yapilamadi device_id=%s: %s", device_id, exc
        )
        return False
    if mevcut is None:
        return False
    return ozet(mevcut) == niyet.ftp_sha256


def komut_uret(
    db: Session,
    *,
    niyet: DeviceConfigApplication,
    device: Device,
    simdi: datetime,
) -> DeviceCommand | None:
    """Niyet icin O AN taze bir `config_update` komutu uretir.

    Cagiran taraf niyeti KILITLI okumus ve hazirligi dogrulamis olmalidir.
    Basarisizlikta niyet `basarisiz` olur ve `None` doner.
    """
    from app.services import device_command_service as cmd_svc

    if niyet.attempt >= AZAMI_DENEME:
        niyet.state = BASARISIZ
        niyet.closed_at = simdi
        niyet.failure_reason = (
            f"Komut {AZAMI_DENEME} kez uretildi ama hicbiri sonuclanmadi; "
            "otomatik deneme durduruldu."
        )
        return None

    if not _ftp_hala_bizim_mi(db, device.id, niyet):
        niyet.state = BASARISIZ
        niyet.closed_at = simdi
        niyet.failure_reason = (
            "FTP'deki dosya bu surumun dosyasi degil (ya da okunamadi); "
            "cihaza yanlis dosya yukletmemek icin komut uretilmedi."
        )
        return None

    try:
        kuyruk = cmd_svc.queue_command(
            db,
            device=device,
            slug=CONFIG_SLUG,
            actor=niyet.requested_by or "sistem",
            origin="config_apply",
        )
    except cmd_svc.CommandRejected as exc:
        # Kalici bir engel (gateway yok) — bekletmenin anlami yok.
        niyet.state = BASARISIZ
        niyet.closed_at = simdi
        niyet.failure_reason = f"Komut kuyruga alinamadi: {exc.detail}"
        return None

    # `queue_command` hafif bir DTO doner (`QueuedCommand`), ORM nesnesi
    # DEGIL; satiri id ile geri okuyoruz.
    cmd = db.get(DeviceCommand, kuyruk.id)
    if cmd is None:  # pragma: no cover - flush edilmis satir kaybolamaz
        niyet.state = BASARISIZ
        niyet.closed_at = simdi
        niyet.failure_reason = "Komut satiri olusturulamadi."
        return None

    niyet.state = KUYRUKTA
    niyet.command_id = cmd.id
    niyet.queued_at = simdi
    niyet.attempt = int(niyet.attempt or 0) + 1
    # Dogrulama icin BASLANGIC damgasi: komut sonrasi degisirse cihaz bir
    # yapilandirma yuklemis demektir.
    niyet.readback_before = _readback(db, device.id)
    return cmd


def cihazi_ilerlet(
    db: Session,
    *,
    device: Device,
    saglik: DeviceRuntimeHealth | None,
    simdi: datetime,
) -> DeviceCommand | None:
    """Tek cihazin acik niyeti icin TAM dongu: senkronize -> ilerlet -> dogrula.

    Sirasi onemli:
      1. Once bagli komutun sonucu okunur (`kuyrukta` -> `iletildi` ya da
         geri `cihaz_bekleniyor`). Bu yapilmadan hazirliga bakmak, sonucu
         gelmis bir komutu hala bekliyor sanip ikinci bir komut uretirdi.
      2. Sonra `cihaz_bekleniyor` durumundaki niyet icin hazirliga bakilir.
      3. En son `iletildi` durumundaki niyet icin cihaz kaniti aranir.
    """
    niyet = acik_niyet(db, device.id, kilitle=True)
    if niyet is None:
        return None

    komut_durumunu_senkronize_et(db, niyet=niyet, simdi=simdi, device=device)

    cmd: DeviceCommand | None = None
    if niyet.state == BEKLIYOR:
        karar = hazirlik_svc.degerlendir(
            saglik=saglik, legacy_status=device.communication_status, simdi=simdi
        )
        niyet.last_readiness_reason = karar.sebep

        # --- KOR TEKRAR KAPISI ------------------------------------------
        #
        # Komut bayatlayip niyet beklemeye dondugunde cihaz HALA `online`
        # gorunuyor olabilir (gozlem ayni gozlemdir). O gozlemle hemen
        # ikinci bir komut uretmek, ayni fiziksel komutu kor bicimde
        # tekrar yollamak olurdu: cihaz teslimden once yeniden uyuduysa
        # ikinci komut da aynen bayatlar ve deneme sayaci bosuna tukenir.
        #
        # Kural: yeni komut ancak SON DENEMEDEN SONRA gelen bir gozlemle
        # uretilir. Gozlem yenilendiyse gateway cihazi TEKRAR gormus
        # demektir — bu, gercek bir uyanma kanitidir.
        if karar.hazir and niyet.queued_at is not None:
            gozlem = hazirlik_svc.utc(saglik.updated_at) if saglik else None
            onceki = hazirlik_svc.utc(niyet.queued_at)
            if gozlem is None or onceki is None or gozlem <= onceki:
                karar = hazirlik_svc.Hazirlik(
                    False,
                    hazirlik_svc.YENI_KANIT_BEKLENIYOR,
                    karar.kaynak,
                    karar.connection_state,
                )
                niyet.last_readiness_reason = karar.sebep

        if karar.hazir:
            cmd = komut_uret(db, niyet=niyet, device=device, simdi=simdi)
            if cmd is not None:
                record_event(
                    db,
                    category="device",
                    event_type="config_command_queued",
                    device_code=device.code,
                    message=(
                        f"{device.name}: cihaz komut alabilir durumda "
                        f"({karar.kaynak}); yapilandirma komutu kuyruga "
                        f"alindi (#{cmd.id})."
                    ),
                    metadata={
                        "application_id": niyet.id,
                        "command_id": cmd.id,
                        "readiness_source": karar.kaynak,
                        "connection_state": karar.connection_state,
                        "attempt": niyet.attempt,
                    },
                )
            elif niyet.state == BASARISIZ:
                record_event(
                    db,
                    category="device",
                    event_type="config_failed",
                    severity="warning",
                    device_code=device.code,
                    message=(
                        f"{device.name}: yapilandirma uygulanamadi — "
                        f"{niyet.failure_reason}"
                    ),
                    metadata={"application_id": niyet.id},
                )

    if niyet.state == ILETILDI:
        _dogrulamayi_dene(db, niyet=niyet, device=device, simdi=simdi)

    return cmd


def uyanma_degerlendir(
    db: Session,
    *,
    saglik_satirlari: dict[str, DeviceRuntimeHealth],
    simdi: datetime,
) -> int:
    """Saglik partisindeki cihazlarin acik niyetlerini ilerletir.

    HER SAGLIK POST'UNDA IS YAPMAZ: once TEK indeksli sorguyla "bu partide
    acik niyeti olan cihaz var mi" diye bakar. 600 cihazlik bir filoda
    bekleyen niyet genelde SIFIRDIR ve o durumda maliyet tek sorgudur.

    Doner: uretilen komut sayisi.
    """
    if not saglik_satirlari:
        return 0

    cihazlar = db.scalars(
        select(Device)
        .join(
            DeviceConfigApplication,
            DeviceConfigApplication.device_id == Device.id,
        )
        .where(
            Device.code.in_(list(saglik_satirlari.keys())),
            DeviceConfigApplication.state.in_(ACIK_DURUMLAR),
        )
    ).all()
    if not cihazlar:
        return 0

    uretilen = 0
    for device in cihazlar:
        if cihazi_ilerlet(
            db,
            device=device,
            saglik=saglik_satirlari.get(device.code),
            simdi=simdi,
        ):
            uretilen += 1
    return uretilen


def komut_durumunu_senkronize_et(
    db: Session,
    *,
    niyet: DeviceConfigApplication,
    simdi: datetime,
    device: Device | None = None,
) -> None:
    """Bagli komut sonuclandiysa niyeti ona gore gunceller.

    NEDEN ITME DEGIL CEKME
    ----------------------
    Komut bes ayri yerde terminal duruma gecebilir (cihaz sonucu, mutlak
    TTL, kira kaybi, teslim hatasi, sonuc supurucusu). Her birine bir kanca
    takmak, ileride eklenen ALTINCI cikis noktasinin sessizce atlanmasi
    demekti — ve atlanan niyet sonsuza kadar `kuyrukta` gorunurdu.

    Bunun yerine durum OKUNDUGU AN uzlastirilir: komut satiri zaten tek
    dogruluk kaynagi, biz ondan turetiriz. Komut boru hattina HICBIR kanca
    eklenmez.

    `iletildi` != `dogrulandi`: gateway yalnizca komutu cihaza ILETTIGINI
    bilir. Cihazin dosyayi gercekten yukledigi ancak cihazin KENDI kaniti
    ile anlasilir.
    """
    if niyet.state not in (KUYRUKTA, ILETILDI) or niyet.command_id is None:
        return
    cmd = db.get(DeviceCommand, niyet.command_id)
    if cmd is None:
        # Komut kaydi silinmis (retention). Kanit kayboldu; niyeti beklemeye
        # geri al ki cihaz uyandiginda yeniden denensin.
        niyet.state = BEKLIYOR
        niyet.command_id = None
        niyet.last_readiness_reason = "komut_kaydi_yok"
        return
    if cmd.status in ("pending", "sent"):
        return  # henuz sonuclanmadi

    if cmd.status == "ok":
        if niyet.state == KUYRUKTA:
            niyet.state = ILETILDI
            niyet.delivered_at = cmd.completed_at or simdi
            # UYGULAMA BASINA EN FAZLA BIR KEZ: gecis tek yonlu oldugu icin
            # bu dal ikinci kez calismaz. (Her saglik POST'unda olay
            # uretmek 2 yillik FIFO olay kaydini gurultuyle doldururdu.)
            record_event(
                db,
                category="device",
                event_type="config_command_delivered",
                device_code=device.code if device is not None else cmd.device_code,
                message=(
                    "Yapilandirma komutu cihaza iletildi "
                    f"(#{cmd.id}). Cihazin dosyayi yukledigi HENUZ dogrulanmadi."
                ),
                metadata={"application_id": niyet.id, "command_id": cmd.id},
            )
        return

    # --- BASARISIZLIK: iki tur, ayrimi kaybetmek pahaliya mal olur --------
    gecici = {
        "expired",                # cihaz teslimden once yeniden uyudu
        "delivery_state_lost",    # gateway kira defterini kaybetti
        "delivery_failed",        # ag/teslim hatasi
    }
    if cmd.result_status in gecici:
        # CIHAZ REDDI DEGIL. Niyet beklemeye doner ve bir sonraki DOGAL
        # uyanmada yeni bir komut uretilir. `attempt` zaten artmisti;
        # tavan bu dongunun sonsuz olmasini engeller.
        niyet.state = BEKLIYOR
        niyet.command_id = None
        # `queued_at` KORUNUR: "en son ne zaman denedik" bilgisi, ayni
        # gozlemle ikinci bir komut uretilmesini engelleyen kapinin girdisi.
        niyet.last_readiness_reason = f"komut_{cmd.result_status}"
        return

    # Cihaz komutu ACIKCA reddetti (DNP3 CommandStatus) ya da sonuc
    # bilinmiyor. Otomatik tekrar YOK: ayni fiziksel komutu kor bicimde
    # yeniden gondermek istemiyoruz; operator gorsun ve karar versin.
    niyet.state = BASARISIZ
    niyet.closed_at = simdi
    niyet.failure_reason = (
        f"Komut basarisiz ({cmd.result_status or cmd.status}): "
        f"{cmd.result_error or '-'}"
    )


def _dogrulamayi_dene(
    db: Session,
    *,
    niyet: DeviceConfigApplication,
    device: Device,
    simdi: datetime,
) -> None:
    """Cihazin KENDI kaniti geldiyse niyeti `dogrulandi` yapar.

    IKI KANIT SINIFI, farkli guclerde:

      1. `cihaz_dosyasi` (KESIN) — cihaz FTP'ye kendi yazdigi dosyada bizim
         gonderdigimiz baytlar var. O baytlari CIHAZ uretti, yani icerik
         cihazda GECERLI. Bu, `uygulanan_dial_in`in de tek gecerli kanit
         saydigi kaynagin ta kendisi (`source="cihazdan_cekildi"`).
      2. `damga_degisti` (ZAYIF) — `info_last_configuration_update` degisti.
         Bir yapilandirmanin yuklendigini soyler ama HANGISININ oldugunu
         SOYLEMEZ; ham metindir, surumle korele edilemez.

    Kanit yoksa HICBIR SEY yazilmaz: niyet `iletildi` olarak kalir ve arayuz
    "uygulandi" IDDIA ETMEZ. Kanitsiz basari iddiasi, bu isin duzeltmek icin
    var oldugu hatanin ta kendisi.
    """
    surum = db.get(DeviceConfigVersion, niyet.config_version_id)
    if surum is None:
        return

    # --- 1) KESIN kanit: cihazin FTP'ye kendi yazdigi dosya ---------------
    from app.services.device_config_service import _READBACK_SOURCE

    sorgu = select(DeviceConfigVersion).where(
        DeviceConfigVersion.device_id == device.id,
        DeviceConfigVersion.source == _READBACK_SOURCE,
    )
    if niyet.queued_at is not None:
        # Komuttan ONCEKI bir readback bu uygulamanin kaniti olamaz.
        sorgu = sorgu.where(DeviceConfigVersion.created_at >= niyet.queued_at)
    cihaz_dosyasi = db.scalars(
        sorgu.order_by(DeviceConfigVersion.version.desc()).limit(1)
    ).first()
    if cihaz_dosyasi is not None and bytes(cihaz_dosyasi.raw) == bytes(surum.raw):
        _dogrula(db, niyet, device, simdi, KANIT_CIHAZ_DOSYASI)
        return

    # --- 2) ZAYIF kanit: cihazin bildirdigi damga degisti -----------------
    simdiki = _readback(db, device.id)
    if simdiki is not None and simdiki != niyet.readback_before:
        _dogrula(db, niyet, device, simdi, KANIT_DAMGA)


def _dogrula(
    db: Session,
    niyet: DeviceConfigApplication,
    device: Device,
    simdi: datetime,
    kanit: str,
) -> None:
    niyet.state = DOGRULANDI
    niyet.verified_at = simdi
    niyet.verified_by = kanit
    niyet.closed_at = simdi

    # `applied_at` ARTIK YALNIZCA BURADA yazilir. Onceden komut kuyruga
    # alinir alinmaz doluyordu ve arayuz "Cihaza gonderildi" diyordu —
    # uyuyan cihazda bu duz bir yalandi.
    surum = db.get(DeviceConfigVersion, niyet.config_version_id)
    if surum is not None and surum.applied_at is None:
        surum.applied_at = simdi

    record_event(
        db,
        category="device",
        event_type="config_verified",
        message=(
            f"{device.name}: yapilandirma cihazda DOGRULANDI "
            f"({'cihazin kendi dosyasi' if kanit == KANIT_CIHAZ_DOSYASI else 'cihaz damgasi degisti'})."
        ),
        metadata={
            "device_code": device.code,
            "application_id": niyet.id,
            "evidence": kanit,
        },
    )


__all__ = [
    "AZAMI_DENEME",
    "BEKLIYOR_DURUMU",
    "CONFIG_SLUG",
    "KANIT_CIHAZ_DOSYASI",
    "KANIT_DAMGA",
    "acik_niyet",
    "cihazi_ilerlet",
    "gecersiz_kil",
    "komut_durumunu_senkronize_et",
    "komut_uret",
    "niyet_olustur",
    "ozet",
    "uyanma_degerlendir",
]
