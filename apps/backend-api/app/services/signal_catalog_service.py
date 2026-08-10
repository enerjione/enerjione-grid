"""Sinyal katalogu — historian (arsiv) politikasinin IS KURALLARI.

NEDEN AYRI BIR SERVIS
---------------------
Arayuz artik "bu sinyal arsivlenecek mi" ve "olu bandi kac" sorularini
kullaniciya aciyor. Bu iki ayar dogrudan VERI KAYBI uretebilir ve hatasi
SESSIZDIR: ekran calisir, alarm calisir, canli deger akar; eksik olan
yalnizca gecmise donuk inceleme yapabilme yetenegidir ve bu, ancak bir
ariza sonrasi "kayit yok" denildiginde fark edilir.

Bu yuzden kurallar router'a degil buraya yazildi: hem `PATCH /signals/{key}`
hem de toplu uc AYNI kapidan gecer. Kural yalnizca arayuzde dursaydi, elle
atilan bir istek ya da bir betik onu tamamen atlardi.

UC KURAL
--------
1. ARIZA SINYALININ ARSIVI SESSIZCE KAPATILAMAZ.
   `binary` / `binary_output` sinyallerinde 0 -> 1 bir OLAYDIR; arsivi
   kapatmak "ariza ne zaman gecti" sorusunu KALICI olarak cevapsiz birakir
   (ham kopya `telemetry` tablosunda retention ile silinir).

   ENGELLEME DEGIL ONAY: bu tiplerin arsivini kapatmak MESRU bir ihtiyactir
   ve zaten sahada uygulanmis durumdadir — migration 0032 tum
   `binary_output` (DNP3 CROB komut kanali: Trigger Download, Reset...)
   sinyallerini `historize=false` yapti. Sert engel koysaydik arayuz
   sahadaki MEVCUT durumu yonetemez, kullanici yanlislikla actigi bir komut
   noktasini geri kapatamazdi. Bu yuzden kapi acik ama KILITLI: cagiran
   `confirm_fault_signals` ile ne yaptigini bildigini soylemek zorunda.

   Arsivi ACMAK her zaman serbesttir (guvenli yon).

2. OLU BANT YALNIZCA ANALOGDA.
   Motor (`historian_policy`) esigi sadece `analog` tipte uyguluyor. Baska
   bir tipe esik yazmak kaydedilir ama HICBIR SEY YAPMAZ — kullaniciya
   olmayan bir ayari varmis gibi gostermek olurdu. Tekil guncellemede
   reddedilir; toplu guncellemede (karisik secim normaldir) ATLANIR ve
   atlananlar cagirana GERI BILDIRILIR.

3. OLU BANDIN UST SINIRI VAR.
   Sinirsiz esik, `historize=false`in SESSIZ halidir: sinyal arayuzde
   "Arsivleniyor" gorunur, denetim kaydinda "warning" yoktur, ama arsive
   hicbir sey yazilmaz. Niyet buysa arsiv acikca KAPATILMALI. Sinir
   `OLU_BANT_UST_SINIR` (bkz. `models/signal_catalog.py`); sema da ayni
   siniri uygular, burasi servisi dogrudan cagiran kod icin.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.signal_catalog import OLU_BANT_UST_SINIR, SignalCatalog
from app.services.historian_policy import OLU_BANT_TIPLERI

#: ARIZA-GECIS TASIYAN veri tipleri. Bu tiplerde her degisim bir olaydir;
#: arsivi kapatmak gecmis incelemesini imkansiz kilar.
ARIZA_TIPLERI = frozenset({"binary", "binary_output"})


class HistorianPolitikaHatasi(ValueError):
    """Historian kurali ihlali. Router bunu 400'e cevirir.

    `sinyaller` alani, arayuzun onay penceresinde HANGI sinyallerin riskli
    oldugunu gosterebilmesi icin doludur; "bir yerlerde bir sorun var"
    demek, kullaniciyi onaya zorlamanin amacini bosa cikarirdi.
    """

    def __init__(self, mesaj: str, *, kod: str, sinyaller: list[str] | None = None):
        super().__init__(mesaj)
        self.kod = kod
        self.sinyaller = sinyaller or []


def olu_bant_uygulanir(data_type: str | None) -> bool:
    """Bu veri tipinde olu bant GERCEKTEN calisir mi?"""
    return (data_type or "") in OLU_BANT_TIPLERI


def ariza_tasiyor(data_type: str | None) -> bool:
    """Bu veri tipi ariza gecisi tasiyor mu (arsivi kapatmak kayip mi)?"""
    return (data_type or "") in ARIZA_TIPLERI


def _olu_bant_sinirini_dogrula(deadband: float | None, sinyaller: list[str]) -> None:
    """Esik makul araligin disindaysa firlatir.

    `not (0 <= d <= sinir)` yazimi NaN'i da yakalar: NaN ile yapilan her
    karsilastirma False doner, yani `d > sinir` kontrolu NaN'i GECIRIRDI ve
    `abs(v - onceki) < nan` daima False olacagi icin sinyal her okumada
    arsivlenirdi — sessiz ama ters yonde bir bozukluk.
    """
    if deadband is None:
        return
    if not (0.0 <= deadband <= OLU_BANT_UST_SINIR):
        raise HistorianPolitikaHatasi(
            f"Olu bant 0 ile {OLU_BANT_UST_SINIR:.0f} arasinda olmali. Bu kadar "
            "buyuk bir esik suzgec degil, sessiz bir kapatmadir: sinyal "
            "'arsivleniyor' gorunur ama hicbir sey yazilmaz. Arsivi kapatmak "
            "istiyorsaniz historize=false gonderin.",
            kod="olu_bant_sinir_disi",
            sinyaller=sinyaller,
        )


def dogrula_tekil(
    *,
    signal_key: str,
    data_type: str | None,
    historize: bool | None,
    deadband: float | None,
    ariza_onayi: bool,
) -> None:
    """Tek sinyalin historian degisikligini dogrular; ihlalde firlatir.

    `historize` / `deadband` None ise o alan DEGISTIRILMIYOR demektir.
    """
    if historize is False and ariza_tasiyor(data_type) and not ariza_onayi:
        raise HistorianPolitikaHatasi(
            "Bu sinyal ariza gecisi tasiyor; arsivi kapatilirsa gecmise donuk "
            "inceleme yapilamaz. Islemi onaylamak icin confirm_fault_signals "
            "gonderin.",
            kod="ariza_onayi_gerekli",
            sinyaller=[signal_key],
        )
    _olu_bant_sinirini_dogrula(deadband, [signal_key])
    if deadband is not None and deadband > 0 and not olu_bant_uygulanir(data_type):
        # Sessizce 0'a cekmek ya da kaydedip yok saymak, kullaniciya
        # calismayan bir ayar satmak olurdu.
        raise HistorianPolitikaHatasi(
            f"Olu bant yalnizca analog sinyallerde uygulanir; '{data_type}' "
            "tipinde her degisim bir olaydir.",
            kod="olu_bant_uygulanmaz",
            sinyaller=[signal_key],
        )


def toplu_historian_guncelle(
    db: Session,
    *,
    signal_keys: list[str],
    historize: bool | None,
    deadband: float | None,
    ariza_onayi: bool,
    model: str | None = None,
) -> dict:
    """Cok sinyalin arsiv ayarini tek islemde gunceller.

    193 sinyali tek tek PATCH etmek 193 istek ve 193 denetim kaydi demekti;
    denetim kaydi o hacimde OKUNAMAZ hale gelir. Burada tek bir ozet
    dondurulur, cagiran (router) tek bir `record_event` yazar.

    Donus: {"updated", "unchanged", "skipped_deadband", "not_found",
            "fault_signals", "changed_signals"} — son ikisi denetim kaydina
    girer: `fault_signals` onaylanarak arsivden cikarilan ariza sinyalleri,
    `changed_signals` ise GERCEKTEN degisen tum anahtarlar. Yalnizca sayi
    yazsaydik "hangi sinyaller o gun kayit tutmayi birakti" sorusu
    cevapsiz kalirdi; katalogun BUGUNKU hali sonraki degisikliklerle
    ustuste bindigi icin geriye donuk cikarilamaz.
    """
    if historize is None and deadband is None:
        raise HistorianPolitikaHatasi(
            "Degistirilecek alan yok: historize ve/veya historize_deadband gonderin.",
            kod="bos_istek",
        )
    _olu_bant_sinirini_dogrula(deadband, [])

    istenen = list(dict.fromkeys(signal_keys))  # sira korunur, tekrar atilir
    # MODELE GORE DARALT.
    #
    # Tekillik `(model, key)` ciftinde ve 192 anahtar birden fazla modelde
    # var. `{row.key: row}` sozlugu SON satiri saklayip digerlerini sessizce
    # dusuruyordu: kullanici bir modelin sinyallerini secip arsivini
    # kapatiyor, islem "basarili" donuyor ve denetim kaydi yaziliyor — ama
    # degisen satir BASKA modelin satiri olabiliyordu.
    stmt = select(SignalCatalog).where(SignalCatalog.key.in_(istenen))
    if isinstance(model, str) and model.strip():
        stmt = stmt.where(SignalCatalog.model == model)
    rows = list(db.scalars(stmt).all())
    bulunan = {row.key: row for row in rows}
    not_found = [k for k in istenen if k not in bulunan]

    # ONCE TOPLU DOGRULAMA, SONRA YAZMA. Yariya kadar uygulanip hata veren
    # bir toplu islem, kullaniciyi neyin degistigini bilmedigi bir durumda
    # birakirdi.
    ariza_sinyalleri = [
        row.key for row in rows if historize is False and ariza_tasiyor(row.data_type)
    ]
    if ariza_sinyalleri and not ariza_onayi:
        raise HistorianPolitikaHatasi(
            "Secimde ariza gecisi tasiyan sinyaller var; arsivleri kapatilirsa "
            "gecmise donuk inceleme yapilamaz.",
            kod="ariza_onayi_gerekli",
            sinyaller=ariza_sinyalleri,
        )

    unchanged = 0
    skipped_deadband: list[str] = []
    changed: list[str] = []
    for key in istenen:
        row = bulunan.get(key)
        if row is None:
            continue
        degisti = False
        if historize is not None and row.historize != historize:
            row.historize = historize
            degisti = True
        if deadband is not None:
            if olu_bant_uygulanir(row.data_type):
                if row.historize_deadband != deadband:
                    row.historize_deadband = deadband
                    degisti = True
            else:
                # Karisik secim NORMALDIR ("tum kaynaklar" + esik ver). Tum
                # islemi reddetmek yerine atliyoruz ve atlananlari geri
                # bildiriyoruz; kullanici neyin uygulanmadigini GORUR.
                skipped_deadband.append(key)
        if degisti:
            changed.append(key)
        else:
            unchanged += 1

    return {
        "updated": len(changed),
        "changed_signals": changed,
        "unchanged": unchanged,
        "skipped_deadband": skipped_deadband,
        "not_found": not_found,
        "fault_signals": ariza_sinyalleri,
    }
