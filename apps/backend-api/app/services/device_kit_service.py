"""Cok uniteli kit cihazlari (Horstmann Pole Master Kit) — TEK KAYNAK.

NE COZUYOR
----------
Pole Master Kit tek bir DNP3 outstation'dir ama uzerindeki 9 uydu ucerli
setler halinde sahada BIRBIRINDEN BAGIMSIZ noktalara kelepcelenir. Kullanici
her seti ayri bir cihaz gibi gormeli: hatta ayri yerlestirmeli, ayri detay
sayfasi acmali, arizasi dogru sete dusmeli.

Bu yuzden her set icin AYRI bir `devices` satiri acilir:

    PMK-001        model=horstmann_pole_master_kit   parent=NULL   (FIZIKSEL)
    PMK-001-S1     model=horstmann_pmk_set           parent=PMK-001, set 1
    PMK-001-S2     model=horstmann_pmk_set           parent=PMK-001, set 2
    PMK-001-S3     model=horstmann_pmk_set           parent=PMK-001, set 3

NEDEN AYRI SATIR, NEDEN TEK SATIRDA UC SET DEGIL
------------------------------------------------
`line_segments.device_id` TEKILDIR: bir Device satiri hattin yalnizca TEK
noktasina oturabilir. Ustelik ariza motoru, IEC 104 Common Address'i,
telemetri anahtari (`device_id`, `signal_key`) ve operator kapsam filtresi
bastan sona `device_id` uzerinden calisir. Her set ayri satir olunca bu
zincirin TAMAMI hicbir degisiklik olmadan dogru calisir; tek satirda tutmak
ise once topolojide imkansiz, sonra ariza konumunda yanlis olurdu.

FIZIKSEL SATIR NE ISE YARAR
---------------------------
DNP3 baglantisini (IP, port, master adresi), gateway bagini, seri numarasini,
yapilandirma dosyasini ve KIT SEVIYESINDEKI telemetriyi (modem, GPS, sebeke,
solar/AC besleme, cihaz sicakligi, komutlar) O tasir. Kullanici listesinde
GORUNMEZ — arayuz onu setlerin altinda toplayan bir baslik olarak kullanir ve
kit seviyesindeki degerleri her setin "Pole Master" sekmesinde gosterir.

MASTER VERISI NEDEN COGALTILMIYOR
---------------------------------
Kit seviyesindeki olcumler uc setin ORTAK varligidir. Bunlari uc sanal cihaza
da yazmak "bir giren = bir cikan" kuralini bozardi: telemetri tuketicisi
`processed_messages` uzerinde (consumer, message_id) TEKIL kisiti tutuyor,
ikinci satir IntegrityError verir, batch geri alinir ve mesaj SONSUZA KADAR
yeniden teslim edilir. Bu yuzden master telemetrisi fiziksel satirda kalir ve
setler onu OKUMA TARAFINDA devralir (`master_source_device`).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.device_models import (
    SATELLITE_COUNT,
    SATELLITES_PER_SET,
    is_kit_model,
    max_sets_for,
    resolve_subunit_satellites,
    subunit_model_for,
)
from app.models.alarm import AlarmEvent
from app.models.device import Device
from app.services import device_runtime_health_service

log = logging.getLogger(__name__)

#: Sanal set kodunun uretimi: `<kit kodu>-S<sira>`.
#:
#: Kod `devices.code` uzerinde TEKIL ve `String(50)`; kit kodu 47 karakteri
#: asarsa uretilen kod sinira takilir — bu yuzden uretim once dogrulanir.
SUBUNIT_CODE_SUFFIX = "-S{index}"
_MAX_CODE_LEN = 50


def subunit_code(parent_code: str, set_index: int) -> str:
    return f"{parent_code}{SUBUNIT_CODE_SUFFIX.format(index=set_index)}"


def subunit_name(parent_name: str, set_index: int) -> str:
    return f"{parent_name} / Set {set_index}"


def is_kit(device: Device | None) -> bool:
    return device is not None and is_kit_model(device.model)


def is_subunit(device: Device | None) -> bool:
    return device is not None and device.parent_device_id is not None


def list_subunits(db: Session, parent_id: int) -> list[Device]:
    return list(
        db.scalars(
            select(Device)
            .where(Device.parent_device_id == parent_id)
            .order_by(Device.subunit_index.asc())
        ).all()
    )


def master_source_device(db: Session, device: Device) -> Device | None:
    """Bu cihazin KIT SEVIYESINDEKI verisini hangi satir tasiyor?

    Sanal set icin fiziksel kit, digerleri icin cihazin kendisi. Arayuzun
    "Pole Master" sekmesi ve komut ucu bunu kullanir — boylece uc set de ayni
    modem/GPS/besleme degerlerini ve ayni komut kumesini gorur, ama veri tek
    yerde tutulur.
    """
    if device.parent_device_id is None:
        return device
    return db.get(Device, device.parent_device_id)


def command_target(db: Session, device: Device) -> Device:
    """Komut hangi FIZIKSEL cihaza gonderilecek?

    Sanal setin kendi DNP3 oturumu yoktur; "Yapilandirmayi guncelle" ya da
    "Tum FCI'lari sifirla" komutu kitin outstation'ina gider. Bunu yapmazsak
    komut ya `unknown_device` ile reddedilir ya da hicbir yere ulasmadan
    kuyrukta kalir.

    DIKKAT: komut KIT GENELINDE etkilidir. "Tum FCI'lari sifirla" dokuz
    uydunun hepsini sifirlar, yalnizca komutu veren seti degil — arayuz bunu
    acikca yazmali.
    """
    if device.parent_device_id is None:
        return device
    return db.get(Device, device.parent_device_id) or device


# --------------------------------------------------------------------------
# Set sayisi yonetimi
# --------------------------------------------------------------------------


def normalize_satellites(
    db: Session, child: Device, istenen: list | None
) -> list[int] | None:
    """Setin uydu atamasini dogrular ve normalize eder.

    KURALLAR
      1. Tam olarak uc numara, hepsi 1..9 araliginda.
      2. Set ICINDE tekrar YOK.
      3. AYNI KITTEKI diger setlerle CAKISMA YOK.

    (3) en kritigi ve sessiz olani: ayni uydu iki sete atanirsa bolme haritasi
    bijektif olmaktan cikar; tag-engine ilk eslemeyi korur (ve hata loglar) ama
    ikinci setin o unitesi HIC veri almaz. Arayuzde set saglikli gorunur,
    yalnizca "bir faz hic olcum vermiyor" diye fark edilir.
    """
    if istenen is None:
        return None
    try:
        sayilar = [int(n) for n in istenen]
    except (TypeError, ValueError) as exc:
        raise ValueError("Uydu numaralari sayi olmali.") from exc

    if len(sayilar) != SATELLITES_PER_SET:
        raise ValueError(f"Her set icin {SATELLITES_PER_SET} uydu secilmeli.")
    if any(not 1 <= n <= SATELLITE_COUNT for n in sayilar):
        raise ValueError(f"Uydu numarasi 1 ile {SATELLITE_COUNT} arasinda olmali.")
    if len(set(sayilar)) != len(sayilar):
        raise ValueError("Ayni uydu bir sette iki kez kullanilamaz.")

    if child.parent_device_id is not None:
        for kardes in list_subunits(db, child.parent_device_id):
            if kardes.id == child.id:
                continue
            kardes_uydular = set(
                resolve_subunit_satellites(kardes.subunit_index, kardes.subunit_satellites)
            )
            cakisan = sorted(kardes_uydular.intersection(sayilar))
            if cakisan:
                adlar = ", ".join(f"Satellite {n:02d}" for n in cakisan)
                raise ValueError(
                    f"{adlar} zaten '{kardes.name}' setine atanmis. "
                    "Bir uydu yalnizca TEK bir sete baglanabilir."
                )
    return sayilar


def normalize_set_count(model: str | None, istenen: int | None) -> int | None:
    """Modelin kabul ettigi set sayisini dondur; kit degilse None.

    Kit modelinde deger ZORUNLUDUR: kac set takildigi sahada belli olur,
    varsayilan uydurmak (orn. "hep 3") kullanilmayan iki set kaydi acar ve o
    setler hattta "veri gelmiyor" diye gorunur.
    """
    if not is_kit_model(model):
        return None
    tavan = max_sets_for(model)
    if istenen is None:
        raise ValueError(
            f"Bu model icin bagli set sayisi zorunlu (1..{tavan})."
        )
    if not 1 <= istenen <= tavan:
        raise ValueError(f"Set sayisi 1 ile {tavan} arasinda olmali.")
    return istenen


def validate_kit_codes(parent_code: str, set_count: int) -> None:
    """Uretilecek set kodlari `devices.code` sinirina sigiyor mu?

    Sigmiyorsa kit HIC olusturulmamali: yarim kalmis bir kit (fiziksel satir
    var, setleri yok) telemetriyi hicbir yere yazamaz ve arayuzde bos gorunur.
    """
    for i in range(1, set_count + 1):
        kod = subunit_code(parent_code, i)
        if len(kod) > _MAX_CODE_LEN:
            raise ValueError(
                f"Cihaz kodu cok uzun: '{kod}' ({len(kod)} karakter, en fazla "
                f"{_MAX_CODE_LEN}). Kit kodunu kisaltin."
            )


def _bos_uydular(index: int, kullanilan: set[int]) -> list[int]:
    """Yeni set icin uydu atamasi: once KONUM VARSAYILANI, cakisirsa ilk bos.

    NEDEN GEREKLI
    -------------
    Varsayilan yerlesim (set 1 -> 1/2/3, set 2 -> 4/5/6, ...) korlemesine
    yazildiginda, atamasi ELLE DEGISTIRILMIS bir kardesle cakisabiliyordu.
    Ornek: Set 1 [7,8,9]'a alinip set sayisi 3'e cikarilinca Set 3 de
    varsayilanla [7,8,9] yaziliyordu.

    Sonuc SESSIZDI ve agirdi: bolme haritasinda ayni fiziksel uydu icin ILK
    esleme kazanir, gec kalan setin o unitesine ait 48 sinyalin TAMAMI hic
    gelmez. Arayuzde set saglikli gorunur; tek iz tag-engine konteynerindeki
    bir ERROR satiridir. Ustelik cakisan uydular hicbir sete gitmedigi icin
    fiziksel kayitta yetim kalir.

    Bijeksiyon muhafizi (`normalize_satellites`) yalnizca PATCH yolunda
    kosuyordu; uretim yolu korumasizdi.
    """
    varsayilan = list(resolve_subunit_satellites(index))
    if not kullanilan.intersection(varsayilan):
        return varsayilan
    # Cakisma var: bos uydulardan sirayla doldur. Hata firlatmak yerine
    # DOGRU bir atama uretmek dogru: kurulumcu zaten "bir set daha ekle"
    # demis, ve hangi uydunun bos oldugu tamamen turetilebilir bir bilgi.
    bos = [n for n in range(1, SATELLITE_COUNT + 1) if n not in kullanilan]
    if len(bos) < SATELLITES_PER_SET:
        raise ValueError(
            "Yeni set icin yeterli bos uydu yok; once mevcut setlerin uydu "
            "atamasini duzenleyin."
        )
    return bos[:SATELLITES_PER_SET]


def create_subunits(
    db: Session, parent: Device, set_count: int, *, mevcut_kodlar: set[str] | None = None
) -> list[Device]:
    """Kit icin eksik set kayitlarini uretir ve doner (var olanlara dokunmaz).

    Setler fiziksel kayitla AYNI konumdan baslar; kurulumcu her setini hat
    uzerine yerlestirdiginde koordinat topolojiden yeniden turetilir
    (`grid_topology._resync_slot`).
    """
    from app.repositories.device_repository import DeviceRepository

    repository = DeviceRepository(db)
    varolan = {d.subunit_index: d for d in list_subunits(db, parent.id)}
    kullanilan = mevcut_kodlar if mevcut_kodlar is not None else set()
    child_model = subunit_model_for(parent.model)
    uretilen: list[Device] = []
    # MEVCUT kardeslerin uyduları — yeni setler bunlarla CAKISAMAZ.
    kullanilan_uydular: set[int] = set()
    for kardes in varolan.values():
        kullanilan_uydular.update(
            resolve_subunit_satellites(kardes.subunit_index, kardes.subunit_satellites)
        )

    for index in range(1, set_count + 1):
        if index in varolan:
            continue
        kod = subunit_code(parent.code, index)
        if kod in kullanilan or repository.get_by_code(kod) is not None:
            raise ValueError(
                f"Set kodu zaten kullanimda: {kod}. Kit kodunu degistirin."
            )
        child = Device(
            code=kod,
            name=subunit_name(parent.name, index),
            description=parent.description,
            model=child_model,
            installation_date=parent.installation_date,
            # BAGLANTI ALANLARI FIZIKSEL KAYITTAN KOPYALANIR ama sanal set
            # gateway'e POLL HEDEFI OLARAK VERILMEZ (bkz. api/gateways.py).
            # Kopyalanmalarinin tek nedeni sema zorunlulugu ve arayuzde
            # "hangi outstation'dan geliyor" sorusunun cevaplanabilmesidir.
            gateway_code=parent.gateway_code,
            ip_address=parent.ip_address,
            dnp3_outstation_port=parent.dnp3_outstation_port,
            dnp3_address=parent.dnp3_address,
            poll_interval_sec=parent.poll_interval_sec,
            timeout_ms=parent.timeout_ms,
            retry_count=parent.retry_count,
            signal_profile=parent.signal_profile,
            latitude=parent.latitude,
            longitude=parent.longitude,
            parent_device_id=parent.id,
            subunit_index=index,
            # Varsayilan yerlesim ACIKCA yazilir (turetmeye birakilmaz):
            # kurulumcu ekranda ne gorduyse veritabaninda da o durur ve
            # ileride varsayilan degisirse mevcut setler kaymaz.
            #
            # AMA once KARDESLERE bakilir: konum varsayilanini korlemesine
            # yazmak, atamasi degistirilmis bir kardesle CAKISMA uretiyordu
            # (bkz. _bos_uydular).
            subunit_satellites=_bos_uydular(index, kullanilan_uydular),
        )
        kullanilan_uydular.update(child.subunit_satellites)
        # Her setin KENDI Common Address'i olur: SCADA uc seti ayri cihaz
        # olarak gorur. Ortak CA verilseydi uc setin ayni IOA'lari birbirini
        # ezerdi ve carpisma hicbir yerde loglanmazdi.
        child.iec104_common_address = repository.next_free_iec104_ca()
        db.add(child)
        db.flush()
        uretilen.append(child)

    return uretilen


#: Sanal setin FIZIKSEL kayittan devraldigi alanlar.
#:
#: Setin kendi DNP3 oturumu yoktur; bu alanlar yalnizca "hangi outstation'dan
#: geliyor" sorusunun cevabini tasir. Ama TASIYORLARSA GUNCEL OLMAK ZORUNDA:
#: `delete_all_for_gateway` silinecek cihazlari SADECE `gateway_code` ile
#: seciyor. Kit baska bir gateway'e tasindiginda setler eski kodda kalirsa,
#: eski gateway silindiginde SETLER GIDER, KIT KALIR — hat yerlesimi, ariza
#: gecmisi ve alarmlar geri alinamaz sekilde silinir.
#:
#: DISARIDA BIRAKILANLAR ve nedeni:
#:   code / name          setin kendi kimligi; kullanici degistirmis olabilir
#:   latitude / longitude her set hat uzerinde AYRI noktada; topolojiden
#:                        turetilir (`grid_topology._resync_slot`)
#:   iec104_common_address her setin KENDI adresi olmali (SCADA ayri cihaz gorur)
#:   phase_*              setin kendi kelepce duzeni
#:   dnp3_extended        yalnizca fiziksel oturumun ozelligi
_INHERITED_FIELDS = (
    "gateway_code",
    "ip_address",
    "dnp3_outstation_port",
    "dnp3_address",
    "poll_interval_sec",
    "timeout_ms",
    "retry_count",
    "signal_profile",
)


def propagate_to_subunits(db: Session, parent: Device) -> list[str]:
    """Fiziksel kayittan devralinan alanlari setlere yansitir.

    Donus: degisen set kodlari. Cagiran taraf gateway config nonce'unu buna
    gore artirmali.
    """
    degisen: list[str] = []
    for child in list_subunits(db, parent.id):
        fark = False
        for alan in _INHERITED_FIELDS:
            yeni = getattr(parent, alan)
            if getattr(child, alan) != yeni:
                setattr(child, alan, yeni)
                fark = True
        if fark:
            degisen.append(child.code)
    if degisen:
        db.flush()
    return degisen


def sync_subunits(db: Session, parent: Device, set_count: int) -> dict[str, list[str]]:
    """Set sayisini istenen degere getirir.

    Donus: {"created": [kod...], "deleted": [kod...]}

    AZALTMA VERI SILER: kaldirilan setin telemetrisi, alarmlari, ariza
    gecmisi ve hat yerlesimi de gider (FK'lar CASCADE). Bu geri alinamaz;
    cagiran taraf kullaniciya ACIKCA sormali. Burada sessizce yapilmasinin
    nedeni, yarim kalmis bir kitin (fazladan set kaydi acikta) daha kotu
    olmasi: o setler telemetri almaz ama haritada saglikli gorunur.
    """
    from app.repositories.device_repository import DeviceRepository

    repository = DeviceRepository(db)
    validate_kit_codes(parent.code, set_count)

    silinen: list[str] = []
    for child in list_subunits(db, parent.id):
        if (child.subunit_index or 0) > set_count:
            silinen.append(child.code)
            repository.delete(child)

    uretilen = [d.code for d in create_subunits(db, parent, set_count)]
    return {"created": uretilen, "deleted": silinen}


# --------------------------------------------------------------------------
# Okuma tarafi zenginlestirme
# --------------------------------------------------------------------------


def annotate(db: Session, devices: list[Device]) -> list[Device]:
    """`parent_device_code`, `satellite_set_count`, `alarm_active` ve
    `runtime_health` doldurur.

    Hicbiri gercek anlamda KOLON DEGIL, turetilmis alandir. Iliski
    (relationship) ile cozmek cazip ama 600+ cihazli listede satir basina bir
    sorgu demekti; burada TOPLAM birkac sorgu ile hallediliyor.

    `DeviceRead` doduren HER uc buradan gecer (liste, olusturma, guncelleme,
    `/public` liste ve detay); zenginlestirmenin tek yeri burasi oldugu icin
    liste ile detay YAPISAL OLARAK ayrisamaz. TEK ISTISNA
    `/internal/devices`: o uc ham ORM satirlarini doner ve zenginlestirme
    ISTEMEZ (tuketicisi iec104-outbound'un point registry'si; ne canli
    alarma ne calisma-zamani sagligina bakar).
    """
    if not devices:
        return devices

    parent_ids = {d.parent_device_id for d in devices if d.parent_device_id}
    kodlar: dict[int, str] = {}
    if parent_ids:
        kodlar = dict(
            db.execute(
                select(Device.id, Device.code).where(Device.id.in_(parent_ids))
            ).all()
        )

    kit_ids = [d.id for d in devices if is_kit_model(d.model)]
    sayilar: dict[int, int] = {}
    if kit_ids:
        sayilar = dict(
            db.execute(
                select(Device.parent_device_id, func.count(Device.id))
                .where(Device.parent_device_id.in_(kit_ids))
                .group_by(Device.parent_device_id)
            ).all()
        )

    # HABERLESME DURUMU KITTEN DEVRALINIR.
    #
    # Setin kendi DNP3 oturumu yok; hepsi TEK fiziksel baglantidan besleniyor.
    # Her set kendi satirinda durum tutsaydi, link koptugunda yalnizca
    # telemetriyi EN SON alan set offline gorunur, digerleri saatlerce
    # "online" kalirdi — ariza motoru da hattin saglikli oldugunu sanardi.
    # Tek gercek var: kitin durumu.
    kit_durumu: dict[int, tuple] = {}
    if parent_ids:
        kit_durumu = {
            r[0]: (r[1], r[2])
            for r in db.execute(
                select(Device.id, Device.communication_status, Device.last_update_at).where(
                    Device.id.in_(parent_ids)
                )
            ).all()
        }

    # ALARM DURUMU CANLI ALARMDAN OKUNUR, KOLONDAN DEGIL.
    #
    # `devices.alarm_active` bir kolon olarak duruyordu ama HICBIR YERDE
    # yazilmiyordu: alarm acan yollarin hicbiri onu True yapmiyor, kapanan
    # alarm da False'a cekmiyordu. Yani her cihaz sonsuza kadar "alarm yok"
    # diyordu. Bunu okuyan her ekran yaniliyordu — haritadaki cihaz karti
    # ("Normal / Aktif alarm yok"), anasayfanin "alarmli" filtresi ve
    # sayaci, ust arama, sebeke yonetimi cipleri, cihaz ozeti KPI'i.
    # Haritanin PIN rengi dogruydu cunku o, alarm listesinden kendi
    # kumesini cikariyordu; ayni ekranda pin kirmizi, kart yesil oluyordu.
    #
    # Kolonu canli tutmaya calismak (her alarm yolunda yaz, her kapanista
    # sil, bir de kacanlar icin mutabakat) ayni gercegi iki yerde tutmak
    # olurdu. Cevap zaten alarm tablosunda; tek DISTINCT sorgu ile okunuyor.
    #
    # OLCUT: canli (`superseded_at IS NULL`) ve normale donmemis
    # (`reset = false`) alarm. Kartin metni de bunu soyluyor ("aktif alarm").
    # Haritanin kirmizi pini bundan DAHA DAR bir kume kullanir
    # (`produces_fault != false`) — o soru "hat arizasi var mi", bu soru
    # "cihazda aktif alarm var mi".
    alarmli: set[int] = set(
        db.scalars(
            select(AlarmEvent.device_id)
            .where(AlarmEvent.reset.is_(False))
            .where(AlarmEvent.superseded_at.is_(None))
            .distinct()
        ).all()
    )

    # CALISMA-ZAMANI SAGLIGI (`device_health_v1`) — AYRI TABLO, TEK SORGU.
    #
    # Cihaz sorgusuna JOIN EDILMEZ: o kume kapsam filtresinden gecmis
    # haldedir ve bir join onu cogaltabilir ya da dusurebilirdi (sayfalama,
    # toplam sayi ve yetki sinirini bozardi). Ayri select yalnizca ELIMIZDEKI
    # kodlari sorar; kapsam disi bir cihazin sagligi HIC OKUNMAZ.
    #
    # Kit kodlari da sorulur: sanal setin kendi satiri YOKTUR (gateway
    # config'inde yalnizca fiziksel outstation vardir) ve durumu kitten
    # devralir — tipki hemen yukaridaki `communication_status` gibi.
    saglik = device_runtime_health_service.saglik_haritasi(
        db, {d.code for d in devices} | set(kodlar.values())
    )

    for d in devices:
        d.alarm_active = d.id in alarmli
        d.parent_device_code = kodlar.get(d.parent_device_id) if d.parent_device_id else None
        # SETIN KENDI OTURUMU YOK — saglik da kitten devralinir.
        #
        # Devralmasaydi ayni donanim ayni ekranda IKI RENK gosterirdi: uyuyan
        # bir Smart kit gateway'e gore `smart_idle` (mavi, saglikli), setleri
        # ise saglik satiri olmadigi icin eski davranisa duser ve telemetri
        # sustugu icin KIRMIZI (`offline`) gorunurdu. Bu kanal tam da o
        # yanlisi onlemek icin var.
        d.runtime_health = saglik.get(d.code)
        if d.parent_device_id is not None:
            kit_saglik = saglik.get(kodlar.get(d.parent_device_id))
            if kit_saglik is not None:
                d.runtime_health = kit_saglik
        if d.parent_device_id in kit_durumu:
            durum, son_guncelleme = kit_durumu[d.parent_device_id]
            d.communication_status = durum
            d.last_update_at = son_guncelleme
        d.satellite_set_count = sayilar.get(d.id) if is_kit_model(d.model) else None
        # COZULMUS atama: kolon NULL olsa bile arayuz gercek uydu numaralarini
        # gorur. Bos birakip "varsayilani sen turet" demek, ayni kurali iki
        # dilde iki kez yazmak olurdu.
        if d.parent_device_id is not None:
            d.subunit_satellites = list(
                resolve_subunit_satellites(d.subunit_index, d.subunit_satellites)
            )
    return devices


def annotate_one(db: Session, device: Device) -> Device:
    return annotate(db, [device])[0]


def signal_model(device: Device | None) -> str | None:
    """Cihazin sinyal katalogunu hangi model kodu tasiyor?

    Su an her model kendi katalogunu tasiyor, yani bu cihazin modelidir.
    Ayri bir fonksiyon olmasinin nedeni, katalogtan sinyal cozen cagrilarin
    (komut cozumu, outbound kayit defteri) `device.model`'i DOGRUDAN okumak
    yerine tek bir yerden gecmesi — ileride bir modelin baska bir modelin
    katalogunu paylasmasi gerekirse tek satir degisir.
    """
    return getattr(device, "model", None)


__all__ = [
    "annotate",
    "annotate_one",
    "command_target",
    "create_subunits",
    "is_kit",
    "is_subunit",
    "list_subunits",
    "master_source_device",
    "normalize_satellites",
    "normalize_set_count",
    "propagate_to_subunits",
    "signal_model",
    "subunit_code",
    "subunit_name",
    "sync_subunits",
    "validate_kit_codes",
]
