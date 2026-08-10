"""Desteklenen cihaz modelleri.

MODEL LISTESI IKI KAYNAKTAN GELIR
---------------------------------
1. `BUILTIN_MODELS` — kod icinde tanimli, INSANCA OKUNUR etiketi olanlar.
2. `signal_catalog.model` — katalogta sinyali tanimlanmis HER model.

Ikisinin BIRLESIMI kullanilir.

NEDEN KATALOGDAN DA TURETILIYOR
-------------------------------
Sinyal katalogu API'den duzenlenebiliyor (POST/PATCH/DELETE /signals), yani
yeni bir modelin adres haritasi surum cikarmadan girilebiliyordu. Ama model
listesi yalnizca bu dosyadaki sozlukten geliyordu; sonuc tutarsizdi:

  * yeni modelin sinyallerini girebiliyordunuz,
  * ama o modeli hicbir cihaza ATAYAMIYORDUNUZ (form dropdown'inda yok),
  * ustelik `GET /signals?model=<yeni>` "Unknown device model" ile 400
    donuyordu — kendi girdiginiz veriyi listeleyemiyordunuz.

Yeni bir Horstmann modeli (ya da ileride baska bir marka) eklemek icin
SINYALLERI tanimlamak yeterli olmali; kod degisikligi ve yeni imaj
gerekmemeli. Adres haritasi VERIDIR, kod degil.

`BUILTIN_MODELS` yine de duruyor: bilinen modellerin arayuzde "Horstmann
Smart Navigator 2.0" gibi duzgun bir adi olsun diye. Katalogdan gelen ama
burada karsiligi olmayan bir model KENDI KODUYLA gosterilir — cirkin ama
DOGRU; uydurma bir etiket uretmiyoruz.

`code` veritabaninda `devices.model` ve `signal_catalog.model` kolonlarinda
saklanan kanonik degerdir; degistirme.


POLE MASTER KIT — TEK FIZIKSEL CIHAZ, UC SANAL SET
--------------------------------------------------
Horstmann Pole Master Kit tek bir DNP3 outstation'dir ama uzerinde 9 uydu
(Satellite 01..09) tasir. Uyduler UCERLI SETLER halinde, birbirinden bagimsiz
noktalara kelepcelenir:

    Set 1 -> Satellite 01, 02, 03
    Set 2 -> Satellite 04, 05, 06
    Set 3 -> Satellite 07, 08, 09

Her set sahada AYRI bir ariza gostergesidir: kendi direk araliginda oturur,
kendi arizasini uretir, kendi detay sayfasi olur. Yani kullanici acisindan
uc ayri cihazdir; veri kaynagi acisindan tek cihazdir.

BU YUZDEN IKI MODEL VAR:

  `horstmann_pole_master_kit`  FIZIKSEL kayit. DNP3 baglantisini (IP, port,
                               master adresi), gateway bagini, seri numarasini
                               ve yapilandirma dosyasini O tasir. Sinyal
                               katalogu cihazin GERCEK DNP3 adres haritasidir
                               (master + sat01..sat09, 493 nokta).

  `horstmann_pmk_set`          SANAL set kaydi. Kullanicinin gordugu, hatta
                               yerlestirdigi, arizanin dustugu kayit budur.
                               Kendi DNP3 baglantisi YOKTUR — `parent_device_id`
                               ile fiziksel kayda baglidir. Sinyal katalogu
                               setin uc uydusudur (sat01/sat02/sat03).

BOLME NEREDE YAPILIR: tag-engine, normalize akisa yayinlamadan hemen once
`device_code` + `signal_key` ciftini YERINDE yeniden yazar (bkz.
`subunit_source_map`). Bir giren = bir cikan; cogaltma YOK (cogaltilirsa
`processed_messages` tekil kisiti ikinci satiri reddeder ve mesaj sonsuza
kadar yeniden teslim edilir).

MASTER SINYALLERI NEDEN COGALTILMAZ: modem, GPS, cihaz sicakligi, sebeke
bilgisi ve komutlar KIT SEVIYESINDEDIR — uc setin ortak varligidir. Bunlari
uc sanal cihaza da yazmak yukaridaki cogaltma yasagina takilir. Bu yuzden
master telemetrisi FIZIKSEL kayitta durur ve sanal setler onu OKUMA
TARAFINDA devralir (bkz. `app/services/device_kit_service.py`).
"""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL: str = "horstmann_sn_2_0"

#: Horstmann Pole Master Kit — fiziksel (ana) kayit.
POLE_MASTER_KIT_MODEL: str = "horstmann_pole_master_kit"

#: Pole Master Kit'in sanal seti — kullanicinin gordugu kayit.
PMK_SET_MODEL: str = "horstmann_pmk_set"

#: Insanca okunur etiketi olan, kod icinde tanimli modeller.
BUILTIN_MODELS: dict[str, str] = {
    "horstmann_sn_2_0": "Horstmann Smart Navigator 2.0",
    POLE_MASTER_KIT_MODEL: "Horstmann Pole Master Kit",
    PMK_SET_MODEL: "Pole Master Kit — Set",
}

#: Geriye donuk ad.
MODELS = BUILTIN_MODELS

#: Cihaz ekleme formunda SECILEMEYEN modeller.
#:
#: Sanal set kaydi kullanici tarafindan elle olusturulmaz — fiziksel kit
#: eklenirken set sayisi kadar OTOMATIK uretilir. Dropdown'da gorunmesi
#: "bagimsiz bir set cihazi" eklenebilecegi izlenimi verirdi; boyle bir sey
#: yok, her set bir kite baglidir.
NON_SELECTABLE_MODELS: frozenset[str] = frozenset({PMK_SET_MODEL})

#: Ana (fiziksel) model -> uretecegi sanal alt cihaz modeli.
SUBUNIT_MODELS: dict[str, str] = {POLE_MASTER_KIT_MODEL: PMK_SET_MODEL}

#: Bir sette kac uydu var.
SATELLITES_PER_SET: int = 3

#: Ana model -> baglanabilecek EN FAZLA set sayisi.
MAX_SETS: dict[str, int] = {POLE_MASTER_KIT_MODEL: 3}

#: Sanal setin unite (kaynak) adlari — sirasiyla 1., 2. ve 3. uydu.
#:
#: SN2 ile ayni isim uzayi BILEREK secildi: mevcut alarm kurallari
#: (`sat01.overcurrent_tripped` gibi), faz eslemesi, e-posta/WhatsApp
#: metinleri ve arayuz kanal listesi hicbir degisiklik olmadan setlerde de
#: calisir. Tek fark, SN2'de olcum yapan ucuncu unitenin `master` olmasi,
#: sette ise ucunun de uydu olmasidir — bu yuzden `sat03` eklendi.
SET_UNIT_SOURCES: tuple[str, ...] = ("sat01", "sat02", "sat03")


def _katalog_modelleri(db: Any) -> list[str]:
    """Katalogta sinyali olan model kodlari.

    Hata HICBIR kosulda listeyi dusurmemeli: veritabani okunamazsa yerlesik
    listeye duseriz. Model dropdown'inin bos gelmesi, cihaz eklemeyi tamamen
    engellerdi.
    """
    if db is None:
        return []
    try:
        from sqlalchemy import select

        from app.models.signal_catalog import SignalCatalog

        satirlar = db.scalars(
            select(SignalCatalog.model).where(SignalCatalog.model.is_not(None)).distinct()
        ).all()
        return [str(m).strip() for m in satirlar if m and str(m).strip()]
    except Exception:  # noqa: BLE001
        return []


def _birlesim(db: Any = None) -> dict[str, str]:
    """kod -> etiket. Yerlesik etiketler kazanir; katalogdan gelen yeni
    kodlar kendi kodlariyla listelenir."""
    sonuc = dict(BUILTIN_MODELS)
    for kod in _katalog_modelleri(db):
        sonuc.setdefault(kod, kod)
    return sonuc


def is_valid_model(code: str, db: Any = None) -> bool:
    """`db` verilirse katalogdaki modeller de gecerli sayilir.

    `db` opsiyoneldir: oturum verilmeyen cagrilarda davranis eskisiyle ayni
    kalir (yalnizca yerlesik modeller).

    Secilemeyen modeller de GECERLIDIR — sistem onlari kendisi uretir ve
    `GET /signals?model=...` ile katalogu goruntulenebilmelidir.
    """
    return code in _birlesim(db)


def model_label(code: str, db: Any = None) -> str:
    return _birlesim(db).get(code, code)


def list_models(db: Any = None) -> list[dict[str, str]]:
    """Arayuzun model dropdown'ini besleyen liste.

    Yerlesikler once, katalogdan gelenler alfabetik — operator once bildigi
    modeli gorsun. Secilemeyen modeller (sanal set) LISTEDE YER ALMAZ.
    """
    tum = _birlesim(db)
    yerlesik = [
        {"code": k, "label": tum[k]}
        for k in BUILTIN_MODELS
        if k in tum and k not in NON_SELECTABLE_MODELS
    ]
    ek = [
        {"code": k, "label": v}
        for k, v in sorted(tum.items())
        if k not in BUILTIN_MODELS and k not in NON_SELECTABLE_MODELS
    ]
    return yerlesik + ek


# --------------------------------------------------------------------------
# Kit / set yardimcilari
# --------------------------------------------------------------------------


def is_kit_model(code: str | None) -> bool:
    """Bu model sanal alt cihaz ureten bir ANA model mi?"""
    return bool(code) and code in SUBUNIT_MODELS


def is_subunit_model(code: str | None) -> bool:
    """Bu model bir ANA cihaza bagli SANAL set modeli mi?"""
    return bool(code) and code in set(SUBUNIT_MODELS.values())


def subunit_model_for(parent_model: str | None) -> str | None:
    return SUBUNIT_MODELS.get(parent_model or "")


def max_sets_for(parent_model: str | None) -> int:
    """Ana modelin destekledigi en fazla set sayisi (kit degilse 0)."""
    return MAX_SETS.get(parent_model or "", 0)


def set_satellite_numbers(set_index: int) -> tuple[int, ...]:
    """Set numarasindan (1 tabanli) fiziksel uydu numaralari.

        1 -> (1, 2, 3)
        2 -> (4, 5, 6)
        3 -> (7, 8, 9)
    """
    if set_index < 1:
        raise ValueError(f"set_index 1 veya buyuk olmali: {set_index}")
    ilk = (set_index - 1) * SATELLITES_PER_SET + 1
    return tuple(range(ilk, ilk + SATELLITES_PER_SET))


#: Kitteki toplam uydu sayisi.
SATELLITE_COUNT: int = 9


def resolve_subunit_satellites(
    set_index: int | None, stored: list | None = None
) -> tuple[int, ...]:
    """Setin uydu atamasi: KAYITLI deger varsa o, yoksa set sirasindan turetilir.

    NEDEN KAYITLI DEGER OLABILIYOR
    ------------------------------
    Varsayilan yerlesim (set 1 -> 1/2/3, set 2 -> 4/5/6, set 3 -> 7/8/9)
    sahadaki en yaygin kurulum; ama uyduları kelepceyi takan kisi baglar ve
    sirasi kite gore degil DIREGE gore olusur. Ikinci sete 4/5/6 yerine
    2/7/9 baglanmis bir kurulumda, atama sabit kalsaydi telemetri yanlis
    setlere yazilir ve bu HICBIR hata uretmezdi — yalnizca "bu setin akimi
    tuhaf" diye gorunurdu.

    Bu yuzden atama duzenlenebilir; varsayilan yalnizca BASLANGIC noktasidir.
    """
    if stored:
        temiz = [int(n) for n in stored if isinstance(n, (int, float, str)) and str(n).strip()]
        if len(temiz) == SATELLITES_PER_SET:
            return tuple(temiz)
    if not set_index:
        return ()
    return set_satellite_numbers(set_index)


def subunit_source_map(
    set_index: int | None, satellites: list | None = None
) -> dict[str, str]:
    """FIZIKSEL kaynak adi -> SANAL set icindeki kaynak adi.

    Varsayilan set 2 icin:  {"sat04": "sat01", "sat05": "sat02", "sat06": "sat03"}

    Set 1'de varsayilan esleme birim (kimlik) fonksiyonudur; yine de acikca
    uretilir ki bolme mantigi tum setlerde AYNI kod yolundan gecsin — "set 1
    ozel durum" kaciniyor, cunku o ozel durum ilk kirilan sey olurdu.

    `satellites` verilirse (kurulumcu atamayi degistirmisse) esleme ondan
    kurulur: orn. [2, 7, 9] -> {"sat02": "sat01", "sat07": "sat02", "sat09": "sat03"}.
    """
    return {
        f"sat{n:02d}": SET_UNIT_SOURCES[i]
        for i, n in enumerate(resolve_subunit_satellites(set_index, satellites))
    }


#: Modelin telemetrisi GERCEKTEN hangi kaynaklara yazilir.
#:
#: Bir modelin sinyal katalogu, cihazin DNP3 ADRES HARITASIDIR — gateway'in
#: neyi okuyacagini soyler. Ama okunan her nokta o cihaz kaydinda SAKLANMAZ:
#: Pole Master Kit'in 9 uydusunun tamami tag-engine tarafindan SETLERE
#: yonlendirilir (bkz. tag_engine/bolme.py) ve fiziksel kayitta yalnizca
#: `master.*` kalir.
#:
#: NEDEN ONEMLI: Modbus ve IEC 104 nokta tablolari cihaz x sinyal
#: kartezyeninden uretiliyor. Bu ayrim yapilmazsa kit kaydi icin, HICBIR ZAMAN
#: VERI GELMEYECEK ~300 adres SCADA'ya bildirilir. Ustelik Modbus'ta cihaz
#: blogu en buyuk modele gore boyutlandigi icin blok 232 register'a sisip
#: MEVCUT SN2 cihazlarinin tum adreslerini kaydirir — sessizce.
#:
#: Kayitli olmayan bir model icin None doner = "hepsi saklanir".
STORED_SOURCES: dict[str, frozenset[str]] = {
    POLE_MASTER_KIT_MODEL: frozenset({"master"}),
}


def stored_sources(model: str | None) -> frozenset[str] | None:
    return STORED_SOURCES.get(model or "")


def is_stored_signal(model: str | None, source: str | None) -> bool:
    """Bu (model, kaynak) ciftine gercekten telemetri yaziliyor mu?"""
    kaynaklar = stored_sources(model)
    return kaynaklar is None or (source or "") in kaynaklar


def satellite_source_to_set_index(source: str) -> int | None:
    """`sat07` -> 3. Uydu kaynagi degilse None.

    Kaynak adi `satNN` bicimli degilse ya da uydu numarasi gecerli bir sete
    dusmuyorsa None doner — cagiran taraf mesaji AYNEN gecirmelidir.
    """
    if not source.startswith("sat") or len(source) != 5:
        return None
    try:
        n = int(source[3:])
    except ValueError:
        return None
    if n < 1:
        return None
    return (n - 1) // SATELLITES_PER_SET + 1
