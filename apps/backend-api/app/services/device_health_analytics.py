"""Cihaz sagligi analizi — batarya tukenmesi, sinyal kalitesi, ariza yogunlugu.

NEDEN AYRI MODUL: `fault_analytics_service` ARIZA olaylarini sayiyor
(fault_events / alarm_events). Burasi OLCUM zaman serisinden turetiyor
(`telemetry_history_1h`) ve tamamen farkli bir sorgu profili var — saatlik
kovalar uzerinde pencere fonksiyonlari. Ikisini tek dosyada toplamak, iki
ayri performans karakteristigini ayni yerde gizlemek olurdu.

NEDEN HAM TABLO DEGIL OZET
--------------------------
`telemetry_history` 90 gunde dusuyor ve 600 cihaz x 193 sinyal olceginde
gunde ~26M satir. Batarya egilimi aylar/yillar olceginde bir soru; ham
tabloyu taramak hem imkansiz (veri yok) hem gereksiz (dakikalik cozunurluk
bu soruya bir sey katmaz). `telemetry_history_1h` 2 YIL saklaniyor ve
avg/min/max tasiyor — tam da gereken sey.

TIMESCALE YOKSA
---------------
Dev ortaminda (vanilla postgres) ya da SQLite testlerde ozet tablo
OLMAYABILIR. Sorgular bu durumda bos doner ve arayuz "veri yok" gosterir;
patlamaz. Ozetin varligi calisma aninda kontrol edilir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Batarya gerilimi sinyali (kaynak oneki dahil). Cihazin batarya yuzdesi
#: bundan turetilir (bkz. ProjectSettings.battery_voltage_low/full).
BATTERY_SIGNAL = "master.battery_voltage_satellite"
#: Modem sinyal seviyesi (dBm). Negatif; 0'a yakin = guclu.
RSSI_SIGNAL = "master.modem_rssi"

#: Egilim hesabi icin gereken EN AZ gozlem araligi (gun). Daha kisa bir
#: pencereden "gunde 0.3 V dusuyor" gibi bir sonuc cikarmak, olcum
#: gurultusunu egilim diye sunmak olurdu.
MIN_TREND_DAYS = 3.0

#: Varsayilan batarya esikleri (V) — ProjectSettings bos ise.
#: Bkz. app/models/project_settings.py.
DEFAULT_BATTERY_LOW = 3.40
DEFAULT_BATTERY_FULL = 3.71


def _ozet_var_mi(db: Session) -> bool:
    """`telemetry_history_1h` bu kurulumda mevcut mu?

    Timescale extension olmayan bir kurulumda (dev / SQLite test) ozet
    olusturulmaz. Sorguyu kosturup hata yakalamak yerine ONCEDEN bakiyoruz:
    yakalanan bir hata, gercek bir sorgu hatasini da yutardi.

    SAVEPOINT ICINDE: eskiden basarisizlikta `db.rollback()` cagriliyordu ve
    bu, CAGIRANIN oturumundaki bekleyen isi de siliyordu. Bu bir olasilik
    degil olculmus bir davranis: ayni oturumda flush edilmis kayitlar,
    yalnizca "ozet tablosu var mi" diye bakildigi icin yok oluyordu. Bir
    VARLIK YOKLAMASI cagiranin islemini bozmamali; savepoint geri alinir,
    dis islem ayakta kalir.
    """
    try:
        with db.begin_nested():
            db.execute(text("SELECT 1 FROM telemetry_history_1h LIMIT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def _pencere(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, days))


def _cihaz_filtresi(visible_device_ids: set[int] | None) -> tuple[str, dict]:
    """Kapsam SQL parcasi. None = sinir yok; bos kume = HICBIR satir."""
    if visible_device_ids is None:
        return "", {}
    if not visible_device_ids:
        return " AND 1 = 0", {}
    return " AND t.device_id = ANY(:dev_ids)", {"dev_ids": list(visible_device_ids)}


def batarya_tukenme(
    db: Session,
    *,
    days: int,
    visible_device_ids: set[int] | None,
    limit: int = 10,
    battery_low: float | None = None,
) -> list[dict]:
    """Bataryasi EN HIZLI tukenen cihazlar.

    Egilim, penceredeki ILK ve SON saatlik ortalamanin farkindan hesaplanir
    (V/gun). Dogrusal regresyon yerine uc noktalarin secilmesi bilincli:
    batarya deşarj egrisi zaten yaklasik dogrusal ve regresyon, tek bir
    bozuk kovaya (sarj/degisim ani) regresyondan daha duyarli olurdu.

    `days_to_low`: mevcut hizla dusuk esige kac gun kaldigi. Egilim POZITIF
    (batarya yukseliyor — sarj ya da degisim) ya da ihmal edilebilirse NULL
    doner; "sonsuz gun" yazmak tabloyu anlamsiz kilardi.
    """
    if not _ozet_var_mi(db):
        return []
    esik = battery_low if battery_low is not None else DEFAULT_BATTERY_LOW
    kapsam, params = _cihaz_filtresi(visible_device_ids)
    sql = text(
        f"""
        WITH kova AS (
            SELECT t.device_id, t.bucket, t.avg_value
              FROM telemetry_history_1h t
             WHERE t.signal_key = :sig
               AND t.bucket >= :baslangic
               AND t.avg_value IS NOT NULL
               {kapsam}
        ), uclar AS (
            SELECT device_id,
                   MIN(bucket) AS ilk_an,
                   MAX(bucket) AS son_an,
                   COUNT(*)    AS kova_sayisi
              FROM kova
             GROUP BY device_id
        )
        SELECT u.device_id,
               d.code,
               d.name,
               ilk.avg_value  AS ilk_v,
               son.avg_value  AS son_v,
               EXTRACT(EPOCH FROM (u.son_an - u.ilk_an)) / 86400.0 AS gun,
               u.kova_sayisi
          FROM uclar u
          JOIN devices d ON d.id = u.device_id
          JOIN kova ilk ON ilk.device_id = u.device_id AND ilk.bucket = u.ilk_an
          JOIN kova son ON son.device_id = u.device_id AND son.bucket = u.son_an
         WHERE EXTRACT(EPOCH FROM (u.son_an - u.ilk_an)) / 86400.0 >= :min_gun
        """
    )
    try:
        rows = db.execute(
            sql,
            {
                "sig": BATTERY_SIGNAL,
                "baslangic": _pencere(days),
                "min_gun": MIN_TREND_DAYS,
                **params,
            },
        ).all()
    except Exception:  # noqa: BLE001
        logger.exception("batarya_tukenme_sorgusu_basarisiz")
        db.rollback()
        return []

    out: list[dict] = []
    for r in rows:
        ilk_v, son_v, gun = float(r[3]), float(r[4]), float(r[5])
        if gun <= 0:
            continue
        dusus_gunluk = (ilk_v - son_v) / gun  # pozitif = tukeniyor
        kalan_gun = None
        # Yalnizca GERCEKTEN dusuyorsa tahmin uret. 1 mV/gun'un altindaki
        # bir egim olcum gurultusudur; ondan "487 gun kaldi" uretmek
        # uydurma bir kesinlik olurdu.
        if dusus_gunluk > 0.001 and son_v > esik:
            kalan_gun = round((son_v - esik) / dusus_gunluk, 1)
        out.append(
            {
                "device_id": r[0],
                "code": r[1],
                "name": r[2],
                "first_v": round(ilk_v, 3),
                "last_v": round(son_v, 3),
                "drop_per_day_v": round(dusus_gunluk, 4),
                "days_to_low": kalan_gun,
                "observed_days": round(gun, 1),
                "samples": int(r[6]),
            }
        )
    # En hizli tukenen basta. `days_to_low` yerine egime gore siralanir:
    # esigin ALTINDA olan cihazlarda tahmin NULL'dur ama onlar en acil
    # olanlardir; egim onlari da dogru siralar.
    out.sort(key=lambda x: x["drop_per_day_v"], reverse=True)
    return out[:limit]


def sinyal_kalitesi(
    db: Session, *, days: int, visible_device_ids: set[int] | None, limit: int = 10
) -> list[dict]:
    """Sinyal kalitesi EN DUSUK cihazlar (modem RSSI, dBm).

    RSSI negatiftir ve 0'a yakin olan gucludur; yani "en kotu" = en KUCUK
    deger. Ortalamanin yaninda MINIMUM da doner: ortalamasi iyi ama zaman
    zaman dibe vuran bir cihaz, surekli orta seviyede olandan daha
    sorunludur (kopmalar o dip anlarinda olur).
    """
    if not _ozet_var_mi(db):
        return []
    kapsam, params = _cihaz_filtresi(visible_device_ids)
    sql = text(
        f"""
        SELECT t.device_id, d.code, d.name,
               AVG(t.avg_value) AS ort,
               MIN(t.min_value) AS dip,
               COUNT(*)         AS kova
          FROM telemetry_history_1h t
          JOIN devices d ON d.id = t.device_id
         WHERE t.signal_key = :sig
           AND t.bucket >= :baslangic
           AND t.avg_value IS NOT NULL
           {kapsam}
         GROUP BY t.device_id, d.code, d.name
         ORDER BY AVG(t.avg_value) ASC
         LIMIT :lim
        """
    )
    try:
        rows = db.execute(
            sql,
            {"sig": RSSI_SIGNAL, "baslangic": _pencere(days), "lim": limit, **params},
        ).all()
    except Exception:  # noqa: BLE001
        logger.exception("sinyal_kalitesi_sorgusu_basarisiz")
        db.rollback()
        return []
    return [
        {
            "device_id": r[0],
            "code": r[1],
            "name": r[2],
            "avg_dbm": round(float(r[3]), 1),
            "worst_dbm": round(float(r[4]), 1) if r[4] is not None else None,
            "samples": int(r[5]),
        }
        for r in rows
    ]


def sinyal_saat_profili(
    db: Session, *, days: int, visible_device_ids: set[int] | None
) -> list[dict]:
    """Gunun hangi saatinde sinyal kalitesi dusuyor (0-23, tum cihazlar).

    NEDEN DEGERLI: sinyal her gun ayni saatlerde dusuyorsa sebep cihaz degil
    CEVRESEL/SEBEKESEL bir dongudur (baz istasyonu yogunlugu, gunes paneli
    besleme dongusu). Cihaz bazli listeye bakan biri bu deseni goremez.

    Saat UTC'dir; arayuz yerel saate cevirir (bkz. local_time).
    """
    if not _ozet_var_mi(db):
        return []
    kapsam, params = _cihaz_filtresi(visible_device_ids)
    sql = text(
        f"""
        SELECT EXTRACT(HOUR FROM t.bucket)::int AS saat,
               AVG(t.avg_value) AS ort,
               MIN(t.min_value) AS dip,
               COUNT(*)         AS kova
          FROM telemetry_history_1h t
         WHERE t.signal_key = :sig
           AND t.bucket >= :baslangic
           AND t.avg_value IS NOT NULL
           {kapsam}
         GROUP BY saat
         ORDER BY saat
        """
    )
    try:
        rows = db.execute(
            sql, {"sig": RSSI_SIGNAL, "baslangic": _pencere(days), **params}
        ).all()
    except Exception:  # noqa: BLE001
        logger.exception("sinyal_saat_profili_sorgusu_basarisiz")
        db.rollback()
        return []
    return [
        {
            "hour_utc": int(r[0]),
            "avg_dbm": round(float(r[1]), 1),
            "worst_dbm": round(float(r[2]), 1) if r[2] is not None else None,
            "samples": int(r[3]),
        }
        for r in rows
    ]


#: Karsilastirma tablosunda dondurulen EN FAZLA cihaz. 600 cihazlik hedef
#: filonun tamami sigar (bkz. kapasite varsayimlari); tavan yalnizca kazara
#: buyuyen bir kurulumda yaniti sinirsiz buyutmemek icin var.
COMPARE_MAX_DEVICES = 600


def cihaz_karsilastirmasi(
    db: Session,
    *,
    days: int,
    visible_device_ids: set[int] | None,
    alarm_sayilari: dict[int, dict],
    ariza_sayilari: dict[int, int],
    battery_low: float | None = None,
) -> list[dict]:
    """Cihaz basina TEK SATIR: haberlesme, alarm, ariza, sinyal, batarya.

    NEDEN TEK TABLO
    ---------------
    Ekranda su an dort ayri "en kotu 10" listesi var ve her biri kendi
    olcusunde en kotuleri gosteriyor. Bu listelerden CAPRAZ bir soru
    sorulamiyor: "sinyali zayif olan cihazlar ayni zamanda cok mu alarm
    uretiyor?" Cevap evetse sorun esikte degil ANTENDE; hayirsa iki ayri
    is emri gerekir. Tek satirda birlesince bu sacilim grafiginde bakista
    okunur.

    HIC SORUN CIKARMAMIS CIHAZ DA GELIR. Satirlar `devices` tablosundan
    uretilir, alarm ya da olcum kayitlarindan degil. Aksi halde dagilim
    yalnizca sorunlu cihazlardan olusur ve "filo nasil" sorusu
    sistematik olarak kotu tarafa kayardi.

    EKSIK OLCU UYDURULMAZ: telemetri ozeti olmayan (ya da Timescale'siz)
    kurulumda `avg_dbm` / `drop_per_day_v` None doner — 0 degil. 0 dBm
    "mukemmel sinyal" demektir ve tam ters okunurdu.
    """
    from sqlalchemy import select

    from app.models.device import Device

    stmt = select(
        Device.id, Device.code, Device.name, Device.communication_status
    ).where(Device.parent_device_id.is_(None))
    if visible_device_ids is not None:
        if not visible_device_ids:
            return []
        stmt = stmt.where(Device.id.in_(visible_device_ids))
    cihazlar = db.execute(stmt.order_by(Device.code).limit(COMPARE_MAX_DEVICES)).all()
    if not cihazlar:
        return []

    # Mevcut "en kotu N" fonksiyonlari TUM filo icin cagrilir ve device_id ile
    # indekslenir. Ayri bir sorgu yazmak, ayni olcunun iki farkli yerde iki
    # farkli sekilde hesaplanmasi riskini dogururdu.
    sinyal = {
        int(r["device_id"]): r
        for r in sinyal_kalitesi(
            db, days=days, visible_device_ids=visible_device_ids,
            limit=COMPARE_MAX_DEVICES,
        )
    }
    batarya = {
        int(r["device_id"]): r
        for r in batarya_tukenme(
            db, days=days, visible_device_ids=visible_device_ids,
            limit=COMPARE_MAX_DEVICES, battery_low=battery_low,
        )
    }

    out: list[dict] = []
    for dev_id, kod, ad, durum in cihazlar:
        alarm = alarm_sayilari.get(int(dev_id), {})
        s = sinyal.get(int(dev_id))
        b = batarya.get(int(dev_id))
        out.append(
            {
                "device_id": int(dev_id),
                "code": kod,
                "name": ad,
                "comm_status": str(getattr(durum, "value", durum) or "unknown"),
                "alarms": int(alarm.get("alarms", 0)),
                "outages": int(alarm.get("outages", 0)),
                "faults": int(ariza_sayilari.get(int(dev_id), 0)),
                "avg_dbm": s["avg_dbm"] if s else None,
                "worst_dbm": s["worst_dbm"] if s else None,
                "drop_per_day_v": b["drop_per_day_v"] if b else None,
                "days_to_low": b["days_to_low"] if b else None,
            }
        )
    return out


def ariza_yogunlugu(
    db: Session, *, days: int, visible_line_ids: set[int] | None
) -> list[dict]:
    """Harita ISI KATMANI icin: koordinat + agirlik.

    Agirlik = o noktada acilan ariza sayisi. Nokta olarak arizanin BASLANGIC
    diregi kullanilir; ariza bir ARALIKTIR ama isi haritasinda araligi
    yaymak yogunlugu suni olarak seyreltirdi — operatorun aradigi sey
    "nereye gitmeliyim"in yogunlastigi yer.
    """
    from sqlalchemy import func, select

    from app.models.fault import FaultEvent
    from app.models.grid_topology import Pole

    stmt = (
        select(
            Pole.latitude,
            Pole.longitude,
            func.count().label("adet"),
        )
        .select_from(FaultEvent)
        .join(Pole, Pole.id == FaultEvent.from_pole_id)
        .where(FaultEvent.opened_at >= _pencere(days))
        .group_by(Pole.latitude, Pole.longitude)
    )
    if visible_line_ids is not None:
        if not visible_line_ids:
            return []
        stmt = stmt.where(FaultEvent.line_id.in_(visible_line_ids))
    rows = db.execute(stmt).all()
    return [
        {"latitude": float(r[0]), "longitude": float(r[1]), "weight": int(r[2])}
        for r in rows
        if r[0] is not None and r[1] is not None
    ]
