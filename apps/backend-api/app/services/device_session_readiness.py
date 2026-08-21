"""Cihaz KOMUT ALMAYA HAZIR MI — tek karar noktasi.

NE COZUYOR
----------
Horstmann Smart modda modemini BILEREK kapatir. Uyuyan bir cihaza fiziksel
DNP3 islemi gonderilemez: komut kuyrukta bekler ve 120 saniyelik tazelik
suresi dolunca `expired` olur. Yapilandirma gonderimi tam da bu yuzden
sessizce basarisiz oluyordu — dosya FTP'ye yaziliyor, komut uretiliyor,
iki dakika sonra oluyor ve cihaz 24 saat sonra uyandiginda kimse ona
"yeni dosyani oku" demiyordu.

Cozum komut omrunu uzatmak DEGILDIR (bkz. `command_delivery_service`:
120 sn bir GUVENLIK invaryantidir, eski bir fiziksel komutun saatler sonra
calismasini onler). Cozum, KOMUTU degil NIYETI kalici yapmak ve cihaz
DOGAL OLARAK uyandiginda o an TAZE bir komut uretmektir. Bu modul o
kararin verildigi tek yerdir.

NEDEN AYRI MODUL
----------------
Ayni soru iki ayri yerden soruluyor:
  * `POST /devices/{id}/config/apply` — simdi komut uretilsin mi?
  * `POST /gateways/{code}/device-health` — cihaz uyandi, simdi uretilsin mi?
Iki yerde iki ayri kural, iki farkli "hazir" tanimi demekti. Tek fonksiyon
olmasi, birinde duzeltilen bir kenar durumunun otekinde acik kalmasini
yapisal olarak engeller.

KANIT SINIFLARI — ve NEDEN DUSMEYE IZIN YOK
-------------------------------------------
Iki kanit sinifi var ve aralarindaki oncelik KATIDIR:

  1. SOZLESME KANITI (`device_health_v1`, gateway >= 1.15.0)
     Gateway cihaz basina durum bildiriyor. Bu kanit varsa BASKA HICBIR
     SEYE BAKILMAZ.

  2. ESKI KANIT (`Device.communication_status`, telemetri turevli)
     Gateway 1.15.0 oncesi ya da saglik yayincisi kapaliyken TEK bilgi
     kaynagi budur. `tag_engine_service` ve `telemetry_consumer` yazar,
     `gateway_staleness_watchdog` bayatlayani dusurur.

KRITIK KURAL: saglik satiri VARSA eski kanita ASLA DUSULMEZ. Uyuyan bir
Horstmann'in `communication_status` degeri henuz `online` kalmis olabilir
(tag-engine bir sonraki degerlendirmeye kadar bekler); o degere bakip
"hazir" demek, uyuyan cihaza fiziksel islem gondermeye calismak olurdu —
tam da onlemeye calistigimiz sey. Saglik satiri "uykuda" diyorsa karar
UYKUDADIR, eski alan ne derse desin.

TAHMIN YOK
----------
`session_started_epoch` bu kod tabaninda YOKTUR (gateway sozlesmesi PR #33
/ 1.15.0 onu tanimlamaz; repo genelinde ve git gecmisinde hicbir izi yok).
Oturumun BASLANGICINI bilmedigimiz icin "son gecerli temas SU ANKI
oturuma ait" iddiasini kanitlayamayiz. Bu yuzden:

  * O terim YOK SAYILMAZ, YERINE UYDURULMAZ — sadece kurulamaz.
  * Yerine `connection_state == "online"` konur: sozlesme bolum 5 acikca
    "Baglanti karari YALNIZCA `connection_state`indir" der, yani oturumun
    SU AN canli oldugunun otoritesi odur.
  * Alan geldiginde predicate'e EK bir AND terimi olarak girer
    (`last_valid_contact_epoch >= session_started_epoch`); burasi o gun
    tek satirla guclendirilecek sekilde yazildi.

`last_valid_contact_epoch` uzerine TAZELIK PENCERESI KOYULMAZ. Bunu
denemek cazip ama YANLIS olurdu: yalnizca istenmeyen rapor gonderen
(unsolicited) bir cihaz 60 dakikada bir konusur ve arada `online` kalir;
60 dakikalik bir temas, 15 dakikalik bir pencereye takilip cihazi kalici
olarak "hazir degil" yapardi. Alanin rolu "bu cihazla HIC gercek DNP3
temasi oldu mu" sorusunu cevaplamaktir; canlilik sorusunun otoritesi
`connection_state`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.device_runtime_health import DeviceRuntimeHealth
from app.models.enums import CommunicationStatus

#: Gateway'in uzlastirma snapshot araligi (saniye) — sozlesme bolum 9
#: varsayilani. Grid bu env'i render ETMEZ (bkz. `gateway_compose`), yani
#: sahada gecerli olan deger budur.
SNAPSHOT_INTERVAL_SEC = 300

#: Gozlem bundan eskiyse saglik satirina GUVENILMEZ (saniye).
#:
#: Esik uzlastirma araliginin UC KATI: sozlesme bolum 7'deki yeniden deneme
#: geri cekilmesi 120 saniyeye kadar cikabildigi icin tek bir kacan snapshot
#: kaydi bayatlatmamali. Uc ust uste kacirilmis uzlastirma ise artik "gecici
#: gecikme" degildir.
#:
#: SAYI ELLE SECILMEDI, TURETILDI. Frontend'de ayni turetme zaten var
#: (`deviceRuntimeState.ts` -> `RUNTIME_STALE_AFTER_MS`); iki taraf
#: `tests/test_device_session_readiness.py` icindeki parite testiyle
#: birbirine bagli. Kor kopya degil, kanitli ayna.
RUNTIME_STALE_AFTER_SEC = 3 * SNAPSHOT_INTERVAL_SEC

#: Karar gerekceleri — denetim kaydina ve arayuze AYNEN yazilir.
#: Serbest metin DEGIL sabit: gerekce metnini degistirmek gecmis kayitlarin
#: anlamini degistirmemeli.
HAZIR = "hazir"
YOK_KANIT = "kanit_yok"
BAYAT_GOZLEM = "bayat_gozlem"
UYKUDA = "uykuda"
ERISILEMEZ = "erisilemez"
TEMAS_YOK = "temas_yok"
ESKI_KANIT_CEVRIMDISI = "eski_kanit_cevrimdisi"
#: Cihaz hazir ama son denemeden bu yana YENI bir gozlem gelmedi. Ayni
#: gozlemle ikinci komut uretmek, kor tekrar dongusu olurdu.
YENI_KANIT_BEKLENIYOR = "yeni_kanit_bekleniyor"

#: Kanit sinifi — hangi delile dayanildigi denetimde gorunur olmali.
KAYNAK_SOZLESME = "runtime_health"
KAYNAK_ESKI = "legacy"
KAYNAK_YOK = "yok"


@dataclass(frozen=True)
class Hazirlik:
    """Karar + gerekce + hangi kanita dayandigi.

    `hazir=False` bir ARIZA IDDIASI DEGILDIR: uyuyan bir Horstmann icin
    beklenen ve SAGLIKLI cevaptir.
    """

    hazir: bool
    sebep: str
    kaynak: str

    #: Karar aninda gorulen ham durum — denetim icin saklanir.
    connection_state: str | None = None


def utc(deger: datetime | None) -> datetime | None:
    """Naive datetime'i UTC kabul et.

    Bazi suruculer (SQLite) tzinfo'yu KAYBEDEREK dondurur; naive bir degeri
    yerel saat sanmak UTC+3'te her gozlemi 3 saat bayat gosterirdi.
    """
    if deger is None:
        return None
    return deger if deger.tzinfo is not None else deger.replace(tzinfo=timezone.utc)


def gozlem_bayat(saglik: DeviceRuntimeHealth, *, simdi: datetime) -> bool:
    """Saglik gozlemi guvenilemeyecek kadar eski mi?

    `updated_at` yoksa BAYATLIK IDDIA EDILMEZ: olcemedigim bir seyi "eski"
    ilan etmek de bir uydurmadir. Ama o durumda karar zaten `TEMAS_YOK` ya
    da durum kontrolunden dusecektir.
    """
    gozlem = utc(saglik.updated_at)
    if gozlem is None:
        return False
    return (simdi - gozlem).total_seconds() > RUNTIME_STALE_AFTER_SEC


def degerlendir(
    *,
    saglik: DeviceRuntimeHealth | None,
    legacy_status: CommunicationStatus | str | None,
    simdi: datetime,
) -> Hazirlik:
    """Cihaz SU AN taze bir DNP3 oturumuna sahip mi ve komut alabilir mi?

    Saf fonksiyon: DB'ye gitmez, zamani disaridan alir. Boylece her kenar
    durumu tek tek test edilebilir.
    """
    # --- 1) SOZLESME KANITI — varsa TEK belirleyici ------------------------
    if saglik is not None:
        durum = (saglik.connection_state or "").strip().lower() or None

        if gozlem_bayat(saglik, simdi=simdi):
            # Gateway susmus. Eski bir "online" gozlemine dayanip komut
            # uretmek, susmus bir sistemin son sozune guvenmek olurdu.
            return Hazirlik(False, BAYAT_GOZLEM, KAYNAK_SOZLESME, durum)

        if durum != "online":
            # `smart_idle` ve `recovering` DE BURAYA DUSER ve dusmelidir:
            # ikisi de "su an komut calistirilamaz" demektir. `smart_idle`
            # SAGLIKLI bir durumdur, ariza degil.
            return Hazirlik(False, UYKUDA, KAYNAK_SOZLESME, durum)

        if not bool(saglik.reachable):
            # Sozlesme bolum 4 `reachable`i birebir "Komut gonderilebilir
            # mi" diye tanimlar. Bu yuzden burasi turetilmis bir kural
            # degil, sozlesmenin kendi ifadesidir.
            return Hazirlik(False, ERISILEMEZ, KAYNAK_SOZLESME, durum)

        if saglik.last_valid_contact_epoch is None:
            # Bu cihazla HIC gercek DNP3 temasi olmamis. `online` gorunse
            # bile (or. TCP acik, DNP3 el sikismasi yok) komut calistirmak
            # icin kanit yok.
            return Hazirlik(False, TEMAS_YOK, KAYNAK_SOZLESME, durum)

        # NOT: `session_started_epoch` gelirse buraya TEK bir kontrol
        # eklenecek:
        #     if saglik.session_started_epoch is not None and (
        #         saglik.last_valid_contact_epoch < saglik.session_started_epoch
        #     ):
        #         return Hazirlik(False, TEMAS_ESKI_OTURUM, KAYNAK_SOZLESME, durum)
        # Alan YOKKEN bu kontrol kurulamaz; uydurulmaz da.
        return Hazirlik(True, HAZIR, KAYNAK_SOZLESME, durum)

    # --- 2) ESKI KANIT — yalnizca saglik satiri HIC YOKKEN -----------------
    #
    # Buraya yalnizca gateway 1.15.0 oncesi ya da saglik yayincisi kapaliyken
    # gelinir. O kurulumlarda mevcut davranis KORUNUR: cihaz `online` ise
    # yapilandirma eskisi gibi aninda gonderilir. Aksi halde bu is, saglik
    # kanali olmayan her sahada yapilandirma gonderimini kalici olarak
    # bozardi.
    if saglik is None:
        ham = (
            legacy_status.value
            if isinstance(legacy_status, CommunicationStatus)
            else (str(legacy_status).strip().lower() if legacy_status else None)
        )
        if ham == CommunicationStatus.ONLINE.value:
            return Hazirlik(True, HAZIR, KAYNAK_ESKI, None)
        return Hazirlik(False, ESKI_KANIT_CEVRIMDISI, KAYNAK_ESKI, None)

    return Hazirlik(False, YOK_KANIT, KAYNAK_YOK, None)


def cihaz_icin(db: Session, device: Device, *, simdi: datetime) -> Hazirlik:
    """`degerlendir` icin saglik satirini cekip karari doner.

    TEK CIHAZ icindir. Parti degerlendirmesinde cagirilmaz: 200 cihazlik bir
    saglik partisinde cihaz basina sorgu 200 sorgu demektir; orada satirlar
    zaten elde olur ve dogrudan `degerlendir` cagrilir.
    """
    saglik = db.get(DeviceRuntimeHealth, device.code)
    return degerlendir(
        saglik=saglik, legacy_status=device.communication_status, simdi=simdi
    )


__all__ = [
    "BAYAT_GOZLEM",
    "ERISILEMEZ",
    "ESKI_KANIT_CEVRIMDISI",
    "HAZIR",
    "Hazirlik",
    "KAYNAK_ESKI",
    "KAYNAK_SOZLESME",
    "KAYNAK_YOK",
    "RUNTIME_STALE_AFTER_SEC",
    "SNAPSHOT_INTERVAL_SEC",
    "TEMAS_YOK",
    "UYKUDA",
    "YENI_KANIT_BEKLENIYOR",
    "YOK_KANIT",
    "cihaz_icin",
    "degerlendir",
    "gozlem_bayat",
    "utc",
]
