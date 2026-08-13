"""Gunluk alarm sayacinin OLAY KAYDINDAN onarilmasi.

NEDEN BU DOSYA VAR
------------------
Ariza Analizi'ndeki "Alarm Sikligi — Takvim" kesiti `alarm_daily_counts`
sayacini okuyor. Sayac ileriye dogru dogru calisiyor (her yeni alarm satiri
bir ORM olayiyla gunu artiriyor, bkz. `models/alarm.py`), ama GECMISI YOKTU:

  * Sayac tablosu 0061 numarali migration ile geldi ve TEK SEFERLIK,
    `alarm_events` uzerinden geri dolduruldu.
  * `alarm_events` ise o gune kadar bir DURUM tablosuydu: tekrar tetikleyen
    alarm oncekinin satirini SILIYORDU, onaylanmis alarm normale donunce de
    satir yok oluyordu (bkz. `models/alarm.py::AlarmEvent.superseded_at`
    aciklamasi — "18 ariza kaydina karsilik 6 alarm satiri kaliyordu").

Yani geri doldurma, elinde zaten silinmis bir gecmis buldu. Sonuc: takvim
365 gunun tamamini "hic alarm gelmemis" gibi cizdi. Ekranda okunan sey
sahanin gecmisi degil, o tablonun bugunku sekliydi.

GERCEK GECMIS NEREDE
--------------------
`system_events` (olay kaydi) tablosunda. Alarm ureten HER IKI yol da satir
eklerken bir olay yaziyor:

  * ingest ucu          -> `alarm_triggered` (app/api/internal.py)
  * haberlesme motoru   -> `alarm_created`   (app/services/alarm_engine_service.py)

Olay kaydi hicbir zaman "durum" tutmadi; satirlari silinmedi, yalnizca
retention penceresi (varsayilan 730 gun) kadar tutuluyor. Yani alarm
satirlari silinirken bile "o gun bir alarm tetiklendi" bilgisi orada kaldi.

ONARIM KURALI: `max(sayac, olay_kaydi)`
---------------------------------------
Sayac ILERI yonde daha dogrudur — satir silinse de eksilmez. Olay kaydi ise
GERI yonde daha dogrudur — sayac tablosu yokken de yaziliyordu. Ikisinden
BUYUGU alinir; boylece:

  * sayacin dogru saydigi bir gun olay kaydi budanmis olsa bile bozulmaz,
  * sayacin hic gormedigi bir gun olay kaydindan gerceklesir.

Bu yuzden onarim TEKRAR TEKRAR calistirilabilir (idempotent): ayni veriyle
ikinci kez kosmak hicbir sayiyi degistirmez.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Alarm tetiklenmesini temsil eden olay tipleri. Iki alarm yolu iki ayri
#: tip yaziyor; biri unutulursa o yolun gecmisi sessizce eksik kalirdi.
ALARM_OLAY_TIPLERI = ("alarm_triggered", "alarm_created")


def _gun_ifadesi(lehce: str) -> str:
    """`created_at` -> UTC gun. Lehceye gore SQL ifadesi.

    Sayacin tanesi UTC gundur (bkz. `AlarmDailyCount`); olay kaydini yerel
    gune cevirmek raporlari sunucunun saat dilimine gore kaydirirdi.
    """
    if lehce == "postgresql":
        return "(e.created_at AT TIME ZONE 'UTC')::date"
    return "date(e.created_at)"


def arsivden_onar(db: Session, *, gun_sayisi: int) -> int:
    """Olay kaydindaki alarm gecmisini gunluk sayaca isler.

    Doner: dokunulan (gun, cihaz) satiri sayisi. Idempotent — bkz. modul
    aciklamasindaki `max(sayac, olay_kaydi)` kurali.
    """
    lehce = db.bind.dialect.name if db.bind is not None else "postgresql"
    gun = _gun_ifadesi(lehce)
    esik = datetime.now(timezone.utc) - timedelta(days=max(1, gun_sayisi))

    # Olay kaydi cihazi KODLA tutuyor (`device_code`), sayac ise id ile.
    # Eslesmeyen kod (silinmis cihaz) sessizce dusuyor: o alarmi bir cihaza
    # yazamayiz ve sahipsiz satir matriste bos bir satir acardi.
    if lehce == "postgresql":
        birlestir = "GREATEST(alarm_daily_counts.count, EXCLUDED.count)"
    else:
        birlestir = "MAX(alarm_daily_counts.count, excluded.count)"

    sonuc = db.execute(
        text(
            f"""
            INSERT INTO alarm_daily_counts (day, device_id, count)
            SELECT {gun} AS day, d.id, COUNT(*)
            FROM system_events AS e
            JOIN devices AS d ON d.code = e.device_code
            WHERE e.category = 'alarm'
              AND e.event_type IN :tipler
              AND e.created_at >= :esik
            GROUP BY {gun}, d.id
            ON CONFLICT (day, device_id)
            DO UPDATE SET count = {birlestir}
            """
        )
        # `expanding`: IN listesi tek bir bind degil, eleman basina bir bind
        # olarak acilir — aksi halde surucu diziyi tek deger sanip patlar.
        .bindparams(bindparam("tipler", expanding=True), bindparam("esik"))
        .params(tipler=list(ALARM_OLAY_TIPLERI), esik=esik)
    )
    db.commit()
    dokunulan = sonuc.rowcount or 0
    if dokunulan:
        logger.info(
            "alarm_daily_counts_onarildi satir=%d pencere_gun=%d",
            dokunulan,
            gun_sayisi,
        )
    return dokunulan
