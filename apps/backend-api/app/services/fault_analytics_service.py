"""Ariza analizi — hangi hat, hangi bolge, hangi sebep, ne kadar surede.

TASARIM: BU KATMAN DIL MODELI ICERMEZ
-------------------------------------
"En cok ariza cikaran hat hangisi" bir GROUP BY'dir. Bunu bir dil modeline
sormak daha yavas, daha pahali ve daha az guvenilir olurdu. Dil modelinin
gercekten katki verecegi yer ayri: serbest metni etikete cevirmek ve
veriyle konusmak. Bu modul o katman HIC KURULMASA DA tam calisir.

KAPSAM (SCOPE) HER SORGUDA UYGULANIR
------------------------------------
Operator yalnizca sorumluluk alanindaki hatlari gorur. Analiz ekrani
"tum sahanin ozeti" gibi durdugu icin burada kapsami unutmak, operatore
gormemesi gereken hatlarin arizalarini sizdirmak olurdu — ustelik toplam
sayilar icinde gizlenmis halde.

VERI KALITESI GORUNUR OLMALI
----------------------------
`labeled_ratio` her yanitta doner. Sebep dagilimini, kayitlarin yalnizca
%5'i etiketliyken "en sik sebep agac temasi" diye okumak yanlis karar
urettirir. Oran dusukse arayuz bunu SOYLEMELI; sayiyi gizlemek, olmayan
bir kesinlik hissi verirdi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.fault import FaultEvent
from app.models.grid_topology import Line, Pole, Region

#: Varsayilan pencere. Bir yil, mevsimselligi (yaz firtinasi / kis
#: buzlanmasi) tam bir dongu olarak icerir; daha kisa pencere mevsimsel
#: sebepleri sistematik olarak eksik gosterir.
DEFAULT_WINDOW_DAYS = 365

#: Siralamalarda dondurulen satir sayisi. Amac "nereye bakayim" sorusuna
#: cevap vermek; 200 satirlik bir liste o soruyu cevaplamaz.
TOP_N = 10


def _window_start(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, days))


# --- LEHCE FARKI -----------------------------------------------------------
#
# Uretim Postgres, testler SQLite. Tarih aritmetigi ve ay gruplama iki
# lehcede FARKLI yazilir; birini secip digerini unutmak, testlerde YESIL
# gorunup sahada patlayan bir sorgu birakir. En sinsi hali de bu: analiz
# ekrani kimsenin izlemedigi bir yerde 500 doner.


def _epoch_farki_sn(db: Session, bitis, baslangic):  # noqa: ANN001
    """Iki damga arasindaki saniye farki — lehceye gore."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.extract("epoch", bitis) - func.extract("epoch", baslangic)
    # SQLite: julianday gun cinsindendir.
    return (func.julianday(bitis) - func.julianday(baslangic)) * 86400.0


def _ay_kovasi(db: Session, damga):  # noqa: ANN001
    """`YYYY-MM` etiketi — lehceye gore."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.to_char(damga, "YYYY-MM")
    return func.strftime("%Y-%m", damga)


@dataclass
class SayiliSatir:
    key: str
    label: str
    count: int
    #: Ikincil olcut — siralamanin "neden" oldugunu gosterir.
    extra: dict = field(default_factory=dict)


def _scope_uygula(stmt, visible_line_ids: set[int] | None):
    """Operator kapsami. None = sinir yok (engineer/installer)."""
    if visible_line_ids is None:
        return stmt
    if not visible_line_ids:
        # Bos kume = hicbir hat gorunmuyor. `IN ()` yerine acikca hicbir
        # satir dondurmek, bos listeyi "tum hatlar" gibi yorumlamayi onler.
        return stmt.where(False)
    return stmt.where(FaultEvent.line_id.in_(visible_line_ids))


def _temel_sorgu(days: int, visible_line_ids: set[int] | None):
    stmt = select(FaultEvent).where(FaultEvent.opened_at >= _window_start(days))
    return _scope_uygula(stmt, visible_line_ids)


def ozet(db: Session, *, days: int, visible_line_ids: set[int] | None) -> dict:
    """Ust serit: toplam, cozulen, ortalama sure, etiketlenme orani."""
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c

    toplam = db.scalar(select(func.count()).select_from(base)) or 0
    if toplam == 0:
        return {
            "total": 0,
            "resolved": 0,
            "open": 0,
            "mttr_hours": None,
            "labeled": 0,
            "labeled_ratio": 0.0,
            "auto_suggested": 0,
        }

    bitis = func.coalesce(f.closed_at, f.resolved_at)
    cozulen = db.scalar(
        select(func.count()).select_from(base).where(bitis.is_not(None))
    ) or 0

    # MTTR yalnizca KAPANMIS arizalar uzerinden. Devam edeni "0 surdu"
    # saymak tabloyu oldugundan iyi gosterirdi.
    mttr_saat = None
    if cozulen:
        saniye = db.scalar(
            select(func.avg(_epoch_farki_sn(db, bitis, f.opened_at)))
            .select_from(base)
            .where(bitis.is_not(None))
        )
        if saniye is not None:
            mttr_saat = round(float(saniye) / 3600.0, 2)

    etiketli = db.scalar(
        select(func.count()).select_from(base).where(f.cause_code.is_not(None))
    ) or 0
    onerili = db.scalar(
        select(func.count()).select_from(base).where(f.auto_cause_code.is_not(None))
    ) or 0

    return {
        "total": int(toplam),
        "resolved": int(cozulen),
        "open": int(toplam - cozulen),
        "mttr_hours": mttr_saat,
        "labeled": int(etiketli),
        # Sebep dagilimini yorumlamadan ONCE bakilmasi gereken sayi.
        "labeled_ratio": round(etiketli / toplam, 4) if toplam else 0.0,
        "auto_suggested": int(onerili),
    }


def hat_siralamasi(
    db: Session, *, days: int, visible_line_ids: set[int] | None, limit: int = TOP_N
) -> list[dict]:
    """En cok ariza cikaran hatlar."""
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    rows = db.execute(
        select(f.line_id, Line.name, Line.code, func.count().label("adet"))
        .select_from(base)
        .join(Line, Line.id == f.line_id)
        .group_by(f.line_id, Line.name, Line.code)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {"line_id": r[0], "name": r[1], "code": r[2], "count": int(r[3])} for r in rows
    ]


def bolge_siralamasi(
    db: Session, *, days: int, visible_line_ids: set[int] | None, limit: int = TOP_N
) -> list[dict]:
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    rows = db.execute(
        select(f.region_id, Region.name, func.count().label("adet"))
        .select_from(base)
        .join(Region, Region.id == f.region_id)
        .group_by(f.region_id, Region.name)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [{"region_id": r[0], "name": r[1], "count": int(r[2])} for r in rows]


def tekrarlayan_acikliklar(
    db: Session, *, days: int, visible_line_ids: set[int] | None, limit: int = TOP_N
) -> list[dict]:
    """AYNI iki direk arasinda tekrar tekrar cikan arizalar.

    Bakim onceliklendirmesinin en dogrudan girdisi: bir aciklik yilda bes kez
    ariza yapiyorsa oradaki agac/izolator sorunu kalicidir ve tek tek
    mudahale etmek yerine o acikligi elden gecirmek gerekir.

    Anahtar DIREK ID'leridir, `sequence_no` DEGIL: sira numaralari topoloji
    duzenlenince yeniden atanabilir, ID'ler kalicidir.
    """
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    p_from = Pole.__table__.alias("p_from")
    p_to = Pole.__table__.alias("p_to")
    rows = db.execute(
        select(
            f.from_pole_id,
            f.to_pole_id,
            f.line_id,
            Line.name,
            p_from.c.sequence_no,
            p_to.c.sequence_no,
            func.count().label("adet"),
            func.max(f.opened_at).label("son"),
        )
        .select_from(base)
        .join(Line, Line.id == f.line_id)
        .join(p_from, p_from.c.id == f.from_pole_id)
        .join(p_to, p_to.c.id == f.to_pole_id)
        .group_by(
            f.from_pole_id, f.to_pole_id, f.line_id, Line.name,
            p_from.c.sequence_no, p_to.c.sequence_no,
        )
        # Tek seferlik arizalar "tekrarlayan" degildir; listeyi doldurup
        # gercek tekrarlari gizlerlerdi.
        .having(func.count() > 1)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {
            "from_pole_id": r[0],
            "to_pole_id": r[1],
            "line_id": r[2],
            "line_name": r[3],
            "from_pole_seq": r[4],
            "to_pole_seq": r[5],
            "count": int(r[6]),
            "last_opened_at": r[7],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# BOLGE RISK PUANI
# ---------------------------------------------------------------------------
#
# NEDEN SAYMAK YETMIYOR
# ---------------------
# "Bu aralik 4 kez arizalandi" tek basina siralama yapmaz: dort arizasi da
# 11 ay once olmus bir aralik ile ucu son iki haftada olan bir aralik ayni
# sayiyi verir, ama ikincisi SU AN sorunludur. Ayni sekilde kendiliginden
# duzelen (gecici) bir ariza ile ekip cikaran kalici bir ariza ayni agirlikta
# degildir.
#
# PUAN = agirlikli tekrar. Her ariza iki carpanla katkida bulunur:
#
#   tur agirligi     kalici 1.0 | gecici 0.5 | bilinmiyor 0.75
#   tazelik          0.5 ** (yas_gun / YARI_OMUR_GUN)
#
# Toplam ham agirlik `hw`, doyuma giden bir egriyle 0-100'e tasinir:
#
#   puan = 100 * (1 - 0.5 ** hw)     -> 1 taze kalici ariza = 50
#                                       2 = 75, 3 = 87.5 ...
#
# Doyum BILINCLI: 8 ile 12 arizanin farki bakim kararini degistirmez, "bu
# aralik elden gecmeli" der; oysa 0 ile 1'in farki her seyi degistirir.
# Egri de bu yuzden basta dik, sonda yatiktir.
#
# ANOMALI TESPITI ICIN: puan MUTLAKTIR (kume icinde normalize EDILMEZ).
# Normalize edilseydi tek bir aralik iyilesince digerlerinin puani kendi
# kendine yukselir ve "esik asildi" alarmi anlamsizlasirdi. Ayni aralik icin
# zaman serisi olarak saklanabilir ve esik/sapma bunun uzerine kurulur.
#: Puanin yari omru (gun): bu kadar eskiyen bir ariza yariya duser.
PUAN_YARI_OMUR_GUN = 90.0

#: Tur agirliklari — kalici ariza ekip cikartir, gecici ariza uyaridir.
PUAN_TUR_AGIRLIK: dict[str | None, float] = {
    "permanent": 1.0,
    "transient": 0.5,
    None: 0.75,
}


def bolge_puani(
    olaylar: list[tuple[datetime, str | None]], simdi: datetime | None = None
) -> float:
    """(acilis, tur) listesinden 0-100 arasi risk puani.

    Saf fonksiyon — testten ve ileride yazilacak anomali katmanindan DB'siz
    cagrilabilsin diye ayri duruyor.
    """
    if not olaylar:
        return 0.0
    an = simdi or datetime.now(timezone.utc)
    ham = 0.0
    for acilis, tur in olaylar:
        if acilis is None:
            continue
        if acilis.tzinfo is None:
            acilis = acilis.replace(tzinfo=timezone.utc)
        yas_gun = max(0.0, (an - acilis).total_seconds() / 86400.0)
        tazelik = 0.5 ** (yas_gun / PUAN_YARI_OMUR_GUN)
        ham += PUAN_TUR_AGIRLIK.get(tur, PUAN_TUR_AGIRLIK[None]) * tazelik
    return round(100.0 * (1.0 - 0.5**ham), 1)


def bolge_puanlari(
    db: Session, *, days: int, visible_line_ids: set[int] | None, limit: int = TOP_N
) -> list[dict]:
    """ARALIK bazli risk siralamasi — "hangi aralik elden gecmeli".

    Gruplama anahtari `zone_code`: hat degil, IKI CIHAZ ARASI aralik. Ayni
    hattin iki ucu birbirinden bagimsiz sorunlar olabilir ve bakim ekibi
    hatta degil araliga gider.

    Kod topolojiyle birlikte degisir (araya cihaz girerse yeni aralik, yeni
    kod). Eski kayitlar eski kodda kalir; bu yuzden liste "su anki
    topolojideki araliklar" degil, "arizalanan araliklar" listesidir —
    silinmis bir araligin gecmisi kaybolmaz.
    """
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    rows = db.execute(
        select(
            f.zone_code,
            f.line_id,
            f.last_red_device_id,
            f.first_green_device_id,
            f.from_pole_seq,
            f.to_pole_seq,
            f.opened_at,
            f.fault_kind,
        )
        .select_from(base)
        .where(f.zone_code.is_not(None))
    ).all()
    if not rows:
        return []

    an = datetime.now(timezone.utc)
    kovalar: dict[str, dict] = {}
    for kod, line_id, son_red, ilk_green, from_seq, to_seq, acilis, tur in rows:
        kova = kovalar.setdefault(
            kod,
            {
                "zone_code": kod,
                "line_id": line_id,
                "last_red_device_id": son_red,
                "first_green_device_id": ilk_green,
                "from_pole_seq": from_seq,
                "to_pole_seq": to_seq,
                "count": 0,
                "permanent_count": 0,
                "last_opened_at": None,
                "_olaylar": [],
            },
        )
        kova["count"] += 1
        if tur == "permanent":
            kova["permanent_count"] += 1
        if kova["last_opened_at"] is None or (acilis and acilis > kova["last_opened_at"]):
            kova["last_opened_at"] = acilis
        kova["_olaylar"].append((acilis, tur))

    # Ad/kod alanlari: tek seferde cek, satir basina sorgu atma.
    line_ids = {k["line_id"] for k in kovalar.values() if k["line_id"]}
    hatlar = {
        r[0]: r[1]
        for r in db.execute(select(Line.id, Line.name).where(Line.id.in_(line_ids))).all()
    } if line_ids else {}
    device_ids = {
        did
        for k in kovalar.values()
        for did in (k["last_red_device_id"], k["first_green_device_id"])
        if did
    }
    cihazlar = {
        r[0]: (r[1], r[2])
        for r in db.execute(
            select(Device.id, Device.code, Device.name).where(Device.id.in_(device_ids))
        ).all()
    } if device_ids else {}

    sonuc: list[dict] = []
    for kova in kovalar.values():
        olaylar = kova.pop("_olaylar")
        son_red = cihazlar.get(kova["last_red_device_id"]) if kova["last_red_device_id"] else None
        ilk_green = (
            cihazlar.get(kova["first_green_device_id"])
            if kova["first_green_device_id"]
            else None
        )
        kova["line_name"] = hatlar.get(kova["line_id"])
        kova["last_red_device_code"] = son_red[0] if son_red else None
        kova["last_red_device_name"] = son_red[1] if son_red else None
        kova["first_green_device_code"] = ilk_green[0] if ilk_green else None
        kova["first_green_device_name"] = ilk_green[1] if ilk_green else None
        kova["score"] = bolge_puani(olaylar, an)
        sonuc.append(kova)

    # Puan esitse cok arizalanan one gelsin — sonra en taze.
    sonuc.sort(
        key=lambda k: (k["score"], k["count"], k["last_opened_at"] or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    return sonuc[:limit]


def sebep_dagilimi(
    db: Session, *, days: int, visible_line_ids: set[int] | None
) -> list[dict]:
    """Insanin girdigi sebeplerin dagilimi.

    YALNIZCA ETIKETLI kayitlar. Etiketsizleri "bilinmiyor" diye bir dilim
    yapmak, veri eksikligini bir BULGU gibi gosterirdi ("arizalarin %80'i
    bilinmeyen sebepten"). Etiketlenme orani `ozet.labeled_ratio` ile ayrica
    bildiriliyor; arayuz onu yaninda gostermeli.
    """
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    rows = db.execute(
        select(f.cause_code, func.count().label("adet"))
        .select_from(base)
        .where(f.cause_code.is_not(None))
        .group_by(f.cause_code)
        .order_by(func.count().desc())
    ).all()
    return [{"cause_code": r[0], "count": int(r[1])} for r in rows]


def kural_isabeti(
    db: Session, *, days: int, visible_line_ids: set[int] | None
) -> dict:
    """Kural onerisi ile insan etiketi ne kadar ortusuyor?

    BU SAYI NEDEN VAR: bir ogrenme/cikarim katmani eklemeden once
    bilinmesi gereken tek sey budur. Isabet yuksekse kurallar zaten ise
    yariyor demektir; dusukse once KURALLARI duzeltmek gerekir, model
    eklemek isabetsizligi gizlemekten baska bir sey yapmaz.

    Yalnizca HER IKISI de dolu olan kayitlar sayilir — kuralin oneri
    uretmedigi ya da insanin etiketlemedigi kayit "yanlis" degildir.
    """
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    karsilastirilabilir = db.scalar(
        select(func.count())
        .select_from(base)
        .where(f.cause_code.is_not(None))
        .where(f.auto_cause_code.is_not(None))
    ) or 0
    if karsilastirilabilir == 0:
        return {"comparable": 0, "agreed": 0, "accuracy": None, "top_mismatches": []}

    ortusen = db.scalar(
        select(func.count())
        .select_from(base)
        .where(f.cause_code.is_not(None))
        .where(f.auto_cause_code.is_not(None))
        .where(f.cause_code == f.auto_cause_code)
    ) or 0

    # En sik yanilma ciftleri — kurali nerede duzeltecegini soyler.
    yanlislar = db.execute(
        select(f.auto_cause_code, f.cause_code, func.count().label("adet"))
        .select_from(base)
        .where(f.cause_code.is_not(None))
        .where(f.auto_cause_code.is_not(None))
        .where(f.cause_code != f.auto_cause_code)
        .group_by(f.auto_cause_code, f.cause_code)
        .order_by(func.count().desc())
        .limit(5)
    ).all()

    return {
        "comparable": int(karsilastirilabilir),
        "agreed": int(ortusen),
        "accuracy": round(ortusen / karsilastirilabilir, 4),
        "top_mismatches": [
            {"suggested": r[0], "actual": r[1], "count": int(r[2])} for r in yanlislar
        ],
    }


def faz_dagilimi(
    db: Session, *, days: int, visible_line_ids: set[int] | None
) -> list[dict]:
    """Tek faz mi uc faz mi — sebep cikariminin ayirt edici ekseni."""
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    rows = db.execute(
        select(f.phase, func.count().label("adet"))
        .select_from(base)
        .where(f.phase.is_not(None))
        .group_by(f.phase)
        .order_by(func.count().desc())
    ).all()
    return [{"phase": r[0], "count": int(r[1])} for r in rows]


def aylik_egilim(
    db: Session, *, days: int, visible_line_ids: set[int] | None
) -> list[dict]:
    """Ay bazinda ariza sayisi — mevsimsellik (firtina, buzlanma) icin."""
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    ay = _ay_kovasi(db, f.opened_at)
    rows = db.execute(
        select(ay.label("ay"), func.count().label("adet"))
        .select_from(base)
        .group_by(ay)
        .order_by(ay)
    ).all()
    return [{"month": r[0], "count": int(r[1])} for r in rows]


def tum_analiz(
    db: Session, *, days: int = DEFAULT_WINDOW_DAYS, visible_line_ids: set[int] | None
) -> dict:
    """Analiz ekraninin tek cagrida ihtiyaci olan her sey.

    TEK UC: ekran alti ayri istek atsaydi hepsi ayni pencereyi ve ayni
    kapsami tekrar tekrar hesaplardi; ustelik biri hata verince ekranin bir
    parcasi sessizce bos kalirdi.
    """
    return {
        "window_days": days,
        "summary": ozet(db, days=days, visible_line_ids=visible_line_ids),
        "top_lines": hat_siralamasi(db, days=days, visible_line_ids=visible_line_ids),
        "top_regions": bolge_siralamasi(db, days=days, visible_line_ids=visible_line_ids),
        "repeat_spans": tekrarlayan_acikliklar(
            db, days=days, visible_line_ids=visible_line_ids
        ),
        # ARALIK RISK PUANI — bakim onceliginin asil girdisi. `repeat_spans`
        # direk ciftine bakar ve yalnizca SAYAR; bu liste cihaz araligina
        # bakar, tazelik ve ariza turuyle agirliklandirir.
        "zone_scores": bolge_puanlari(db, days=days, visible_line_ids=visible_line_ids),
        "cause_distribution": sebep_dagilimi(
            db, days=days, visible_line_ids=visible_line_ids
        ),
        "rule_accuracy": kural_isabeti(db, days=days, visible_line_ids=visible_line_ids),
        "phase_distribution": faz_dagilimi(
            db, days=days, visible_line_ids=visible_line_ids
        ),
        "monthly_trend": aylik_egilim(db, days=days, visible_line_ids=visible_line_ids),
        # Bolge -> Hat -> Faz akisi. Uc ayri cubuk grafiginin gostermedigi
        # sey: arizalarin NEREDE toplandigi.
        "sankey": sankey_akisi(db, days=days, visible_line_ids=visible_line_ids),
        # Cografi isi katmani. CIHAZ SAGLIGINDAN BURAYA TASINDI: bu bir ariza
        # cografyasi (FaultEvent + Pole), cihaz olcumu degil. Yanlis uctayken
        # harita sekmesi yalnizca bu alan icin cihaz sagligini cagiriyor ve
        # 600 cihazlik karsilastirma tablosunu, iki agir telemetri sorgusunu
        # bosuna odetiyordu. Islev kendi modulunde kaldi; yalnizca baglanti
        # tasindi.
        "fault_heatmap": _ariza_cografyasi(db, days=days, visible_line_ids=visible_line_ids),
    }


def _ariza_cografyasi(
    db: Session, *, days: int, visible_line_ids: set[int] | None
) -> list[dict]:
    """`device_health_analytics.ariza_yogunlugu` sarmalayicisi.

    Modul-seviyesi import DONGU yaratirdi (o modul de bu moduldeki alarm
    sayimlarini cagiriyor); bu yuzden cagri anindadir.
    """
    from app.services.device_health_analytics import ariza_yogunlugu

    return ariza_yogunlugu(db, days=days, visible_line_ids=visible_line_ids)


# ===========================================================================
# SISTEM SAGLIGI — alarm sikligi ve haberlesme kararliligi
# ===========================================================================
#
# Ariza analizi "sahada ne oldu" sorusunu cevapliyor; bu bolum "SISTEM
# kendisi nasil davraniyor" sorusunu. Ikisi ayri sorular ve ayri kararlar
# urettirir:
#
#   * Bir alarm kurali cok sik tetikliyor ama hicbir zaman arizaya
#     donusmuyorsa esik yanlistir — kural "kurt geldi" diye bagiriyor ve
#     operator bir sure sonra hepsini gormezden gelmeye baslar. Bu, gercek
#     alarmin kacirilmasinin en yaygin sebebidir.
#   * Bir cihazin haberlesmesi gunde onlarca kez kopup geliyorsa sorun
#     arizada degil o cihazda/modemde/anten hattindadir. Tek tek alarmlara
#     bakan biri bunu FARK EDEMEZ; ancak sayilinca gorunur.

from app.models.alarm import AlarmDailyCount, AlarmEvent  # noqa: E402
from app.models.device import Device  # noqa: E402


def _alarm_temel(days: int, visible_device_ids: set[int] | None):
    stmt = select(AlarmEvent).where(AlarmEvent.created_at >= _window_start(days))
    if visible_device_ids is None:
        return stmt
    if not visible_device_ids:
        return stmt.where(False)
    return stmt.where(AlarmEvent.device_id.in_(visible_device_ids))


def _gun_metni(gun) -> str:  # noqa: ANN001
    """`YYYY-MM-DD`. SQLite `Date` kolonunu bazen metin olarak geri verir."""
    return gun.isoformat() if hasattr(gun, "isoformat") else str(gun)[:10]


def _sayac_temel(days: int, visible_device_ids: set[int] | None):
    """Gunluk alarm sayacinin pencere + kapsam suzulmus temeli.

    Pencere GUN sinirina yuvarlanir: sayacin tanesi zaten gundur, saat
    hassasiyetli bir esik gunun bir kismini disarida birakip o gunu eksik
    gosterirdi (`_surekli_gunler` ile de tutmazdi).
    """
    ilk = (
        datetime.now(timezone.utc).date()
        - timedelta(days=max(1, min(days, CALENDAR_MAX_DAYS)) - 1)
    )
    stmt = select(AlarmDailyCount).where(AlarmDailyCount.day >= ilk)
    if visible_device_ids is None:
        return stmt
    if not visible_device_ids:
        return stmt.where(False)
    return stmt.where(AlarmDailyCount.device_id.in_(visible_device_ids))


def alarm_sikligi(
    db: Session, *, days: int, visible_device_ids: set[int] | None, limit: int = TOP_N
) -> list[dict]:
    """Hangi alarm kurali kac kez tetikledi — ve kaci ONAYLANDI.

    `title` alarm kuralinin adidir (bkz. `_resolve_active_rule`). Onay orani
    ikinci sutun olarak doner: cok tetikleyip HIC onaylanmayan bir kural,
    operatorun gormezden geldigi bir kuraldir. Bu, esigi gozden gecirmek
    icin sayidan daha guclu bir sinyaldir.
    """
    base = _alarm_temel(days, visible_device_ids).subquery()
    a = base.c
    rows = db.execute(
        select(
            a.title,
            a.level,
            func.count().label("adet"),
            func.sum(case((a.acknowledged.is_(True), 1), else_=0)).label("onayli"),
            func.max(a.created_at).label("son"),
        )
        .select_from(base)
        # Haberlesme alarmlari ayri bir olcut (asagida); kural siralamasina
        # karistirmak "en sik alarm" listesini cihaz kopmalariyla doldururdu.
        .where(func.coalesce(a.kind, "rule") != "comm_loss")
        .group_by(a.title, a.level)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {
            "rule_name": r[0],
            "level": r[1],
            "count": int(r[2]),
            "acknowledged": int(r[3] or 0),
            "last_at": r[4],
        }
        for r in rows
    ]


#: Isi haritasinda gosterilecek EN COK alarm ureten cihaz sayisi.
#: 600 cihazli bir sahada her cihaza bir satir vermek okunmaz bir duvar
#: uretir; ustelik satirlarin cogu bos olur. Kesilen kisim sessizce
#: atilmaz — yanit `truncated` ve `device_total` ile bunu SOYLER.
HEATMAP_TOP_DEVICES = 25

#: Sutun tavani. Gunluk kovada 3 yillik pencere 1095 sutun demek; ekranda
#: her sutun bir piksel altina duser ve grafik anlamsizlasir.
HEATMAP_MAX_COLS = 120


def _surekli_gunler(days: int) -> list[str]:
    """Bugunden geriye `days` gunun KESINTISIZ `YYYY-MM-DD` listesi.

    Hem takvim hem cihaz x zaman matrisi bunu kullanir: sutunlar VERIDEN
    degil takvimden gelsin, sessiz gunler de yer kaplasin. Iki yerde ayri
    ayri hesaplansaydi biri gun sinirina yuvarlarken digeri yuvarlamayabilir
    ve ayni ekrandaki iki grafik farkli gunlerde biterdi.
    """
    bugun = datetime.now(timezone.utc).date()
    uzunluk = max(1, days)
    ilk = bugun - timedelta(days=uzunluk - 1)
    return [(ilk + timedelta(days=i)).isoformat() for i in range(uzunluk)]


#: Cihaz x zaman matrisinin SABIT penceresi. Sayfanin pencere secimini
#: (30/90/365/1095) IZLEMEZ ve bu bilinclidir: 25 satirlik matriste 365
#: sutun, sutun basina bir pikselin altina duser ve desen okunmaz olur.
#: Matrisin cevapladigi soru zaten "SON DONEMDE hangi cihaz gurultuluydu";
#: uzun donem sorusunu takvim cevapliyor. Yanit bu pencereyi `window_days`
#: ile BILDIRIR — arayuz "hep son 30 gun" diyebilsin, kullanici pencereyi
#: degistirip matris neden ayni kaldi diye dusunmesin.
HEATMAP_WINDOW_DAYS = 30


def alarm_isi_haritasi(
    db: Session,
    *,
    days: int,
    visible_device_ids: set[int] | None,
    limit: int = HEATMAP_TOP_DEVICES,
    surekli: bool = False,
) -> dict:
    """Cihaz x zaman alarm yogunlugu — TUM cihazlar tek ekranda karsilastirilir.

    NEDEN LISTE YETMIYOR
    --------------------
    "En cok alarm ureten cihazlar" listesi tek bir sayi verir ve ZAMANI
    duzler. Oysa operatorun ayirt etmesi gereken iki durum bu sayida
    ayni gorunur:

      * Bir cihaz uc ay boyunca her gun 2 alarm uretiyor  -> kronik, esik
        yanlis kurulmus ya da montaj sorunlu.
      * Ayni cihaz tek bir gunde 180 alarm uretmis        -> o gun sahada
        bir olay olmus; cihazin kendisiyle ilgisi olmayabilir.

    Ikisi de "180 alarm" der. Isi haritasi bunlari BAKISTA ayirir; ustelik
    ayni gun sutununda birden cok cihaz kararmissa sorun cihazlarda degil
    o gun yasanan ortak olaydadir (besleme, sebeke, gateway).

    KOVA COZUNURLUGU pencereye gore: 2 gune kadar saatlik, ustu gunluk.
    Kisa pencerede gunluk kova tek sutuna duser; uzun pencerede saatlik kova
    binlerce sutun uretir.

    KESILEN VERI SOYLENIR. Yalnizca en cok alarm ureten `limit` cihaz
    cizilir ve yanit bunu `truncated` / `device_total` ile bildirir —
    "listede yok" ile "alarm uretmemis" karistirilmasin.

    `surekli=True`: sutunlar VERIDEN degil TAKVIMDEN uretilir; alarm
    gorulmeyen gunler de sutun acar. Sessiz gecen bir hafta matriste
    gercekten bir hafta genisligindedir. Yalnizca SINIRLI pencerede
    guvenli — 365 gunluk pencerede 365 sutun, sutun basina bir pikselin
    altina duser ve matris okunmaz olur. Bu yuzden varsayilan KAPALI ve
    yalnizca 30 gune sabitlenmis matris (bkz. HEATMAP_WINDOW_DAYS) aciyor.
    """
    # KAYNAK: GUNLUK SAYAC — takvimle AYNI tablo (bkz. `alarm_takvimi`).
    # Iki kesit ayni soruyu farkli kesiyor; farkli kaynaklardan okusalardi
    # ayni ekranda birbirini tutmayan iki sayi cikardi.
    #
    # KOVA HEP GUNLUK: sayacin tanesi gun. Onceden 2 gunden kisa pencerede
    # saatlik kova aciliyordu; matris zaten 30 gune sabit (bkz.
    # HEATMAP_WINDOW_DAYS), yani bu dal pratikte hic calismiyordu.
    temel = _sayac_temel(days, visible_device_ids).subquery()
    a = temel.c

    # 1) Satirlar: en cok alarm ureten cihazlar. Hic alarm uretmemis cihaz
    #    ZATEN gelmez — bos bir satir cizmenin bilgi degeri yok.
    sayimlar = (
        select(a.device_id, func.sum(a.count).label("adet"))
        .select_from(temel)
        .group_by(a.device_id)
        .having(func.sum(a.count) > 0)
        .subquery()
    )
    toplam_cihaz = db.scalar(select(func.count()).select_from(sayimlar)) or 0

    satirlar = db.execute(
        select(sayimlar.c.device_id, sayimlar.c.adet, Device.code, Device.name)
        .join(Device, Device.id == sayimlar.c.device_id)
        .order_by(sayimlar.c.adet.desc(), Device.code)
        .limit(limit)
    ).all()
    if not satirlar:
        return {
            # Matrisin KENDI penceresi. Sayfanin pencere secimiyle ayni
            # olmak ZORUNDA degil (bkz. HEATMAP_WINDOW_DAYS); arayuz
            # "son 30 gun" diyebilsin diye yanitta tasiniyor.
            "window_days": days,
            "bucket": "day",
            "buckets": _surekli_gunler(days) if surekli else [],
            "devices": [],
            "cells": [],
            "max": 0,
            "device_total": 0,
            "truncated": False,
        }

    id_sira = {int(r[0]): i for i, r in enumerate(satirlar)}

    hucreler_ham = db.execute(
        select(a.device_id, a.day.label("kova"), func.sum(a.count).label("adet"))
        .select_from(temel)
        .where(a.device_id.in_(list(id_sira)))
        .group_by(a.device_id, a.day)
    ).all()

    # 2) Sutunlar.
    #    `surekli`: takvimden uretilir, sessiz gunler de sutun acar.
    #    Aksi halde veride GORULEN kovalar — 365 gunluk pencerede sahanin
    #    sessiz gecen aylari grafigi okunmaz genislige tasirdi.
    if surekli:
        kovalar = _surekli_gunler(days)
    else:
        kovalar = sorted({_gun_metni(r[1]) for r in hucreler_ham})
    if len(kovalar) > HEATMAP_MAX_COLS:
        kovalar = kovalar[-HEATMAP_MAX_COLS:]
    kova_sira = {k: i for i, k in enumerate(kovalar)}

    hucreler: list[list[int]] = []
    en_cok = 0
    for dev_id, k, adet in hucreler_ham:
        sutun = kova_sira.get(_gun_metni(k))
        if sutun is None:
            continue  # tavan disinda kalan eski kova
        adet = int(adet or 0)
        en_cok = max(en_cok, adet)
        hucreler.append([sutun, id_sira[int(dev_id)], adet])

    return {
        "window_days": days,
        "bucket": "day",
        "buckets": kovalar,
        "devices": [
            {
                "device_id": int(r[0]),
                "code": r[2],
                "name": r[3],
                "total": int(r[1]),
            }
            for r in satirlar
        ],
        "cells": hucreler,
        "max": en_cok,
        "device_total": int(toplam_cihaz),
        "truncated": toplam_cihaz > len(satirlar),
    }


def haberlesme_kararsizligi(
    db: Session, *, days: int, visible_device_ids: set[int] | None, limit: int = TOP_N
) -> list[dict]:
    """Haberlesmesi en sik kopup gelen cihazlar.

    Haberlesme alarmi kesinti BASINA bir kez acilir (motor ayni cihaz icin
    acik alarm varken ikincisini yaratmaz), yani bu sayi dogrudan KESINTI
    SAYISIDIR — mesaj sayisi degil.

    `kind` alani eklenmeden onceki kayitlar NULL'dur ve buraya GIRMEZ; onlari
    "kural alarmi" sayip disarida birakmak, "haberlesme kopmasi" sayip iceri
    almaktan daha guvenli. Sayi bu yuzden alanin eklendigi surumden itibaren
    anlamlidir.
    """
    base = _alarm_temel(days, visible_device_ids).subquery()
    a = base.c
    rows = db.execute(
        select(
            a.device_id,
            Device.code,
            Device.name,
            func.count().label("kesinti"),
            func.max(a.created_at).label("son"),
        )
        .select_from(base)
        .join(Device, Device.id == a.device_id)
        .where(a.kind == "comm_loss")
        .group_by(a.device_id, Device.code, Device.name)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {
            "device_id": r[0],
            "code": r[1],
            "name": r[2],
            "outages": int(r[3]),
            "last_at": r[4],
        }
        for r in rows
    ]


def haberlesme_durumu_dagilimi(
    db: Session, *, visible_device_ids: set[int] | None
) -> list[dict]:
    """Filonun ANLIK haberlesme durumu dagilimi (online / offline / unknown).

    Pencereden BAGIMSIZ: bu bir gecmis sayimi degil, su ANDAKI durum.
    "Son 365 gunde 12 kesinti oldu" ile "su an 12 cihaz kopuk" bambaska iki
    sey; ikincisi vardiya baslangicinda bakilan sayidir.

    Alarm uretmemis cihazlar da SAYILIR — sayim `devices` tablosundan gelir,
    alarm kayitlarindan degil. Alarmdan turetilseydi hic sorun cikarmamis
    (yani en saglikli) cihazlar dagilimda hic gorunmezdi.
    """
    stmt = select(Device.communication_status, func.count()).group_by(
        Device.communication_status
    )
    if visible_device_ids is not None:
        if not visible_device_ids:
            return []
        stmt = stmt.where(Device.id.in_(visible_device_ids))
    # Sanal set kayitlari FIZIKSEL cihaz degildir (bkz. device_kit_service);
    # filo sayimina girerlerse ayni donanim iki kez sayilir.
    stmt = stmt.where(Device.parent_device_id.is_(None))
    rows = db.execute(stmt).all()
    return [
        {"status": str(getattr(r[0], "value", r[0]) or "unknown"), "count": int(r[1])}
        for r in rows
    ]


def cihaz_alarm_sayilari(
    db: Session, *, days: int, visible_device_ids: set[int] | None
) -> dict[int, dict]:
    """Cihaz basina alarm ve kesinti sayisi — karsilastirma tablosunun girdisi.

    Kural alarmi ile haberlesme kesintisi AYRI sayilir: ikisini toplamak
    "cok alarm ureten cihaz" ile "cok kopan cihaz"i tek sayida eritirdi ve
    bunlar farkli mudahale gerektirir (esik ayari vs. anten/modem).
    """
    base = _alarm_temel(days, visible_device_ids).subquery()
    a = base.c
    rows = db.execute(
        select(
            a.device_id,
            func.sum(case((func.coalesce(a.kind, "rule") != "comm_loss", 1), else_=0)),
            func.sum(case((a.kind == "comm_loss", 1), else_=0)),
            func.max(a.created_at),
        )
        .select_from(base)
        .where(a.device_id.is_not(None))
        .group_by(a.device_id)
    ).all()
    return {
        int(r[0]): {
            "alarms": int(r[1] or 0),
            "outages": int(r[2] or 0),
            "last_alarm_at": r[3],
        }
        for r in rows
    }


def cihaz_ariza_sayilari(
    db: Session, *, days: int, visible_line_ids: set[int] | None
) -> dict[int, int]:
    """Cihaz basina ariza sayisi — `last_red_device_id` uzerinden.

    NEDEN "SON KIRMIZI": bir ariza bir ARALIKTIR ve iki cihaz arasinda
    kalir. Aralikin baslangicini belirleyen cihaz son kirmizi goren
    cihazdir; ariza o cihazin ASAGISINDADIR. Iki uca birden yazmak her
    arizayi iki kez saydirirdi.
    """
    stmt = (
        select(FaultEvent.last_red_device_id, func.count())
        .where(FaultEvent.opened_at >= _window_start(days))
        .where(FaultEvent.last_red_device_id.is_not(None))
        .group_by(FaultEvent.last_red_device_id)
    )
    if visible_line_ids is not None:
        if not visible_line_ids:
            return {}
        stmt = stmt.where(FaultEvent.line_id.in_(visible_line_ids))
    return {int(r[0]): int(r[1]) for r in db.execute(stmt).all()}


def alarm_ozeti(
    db: Session, *, days: int, visible_device_ids: set[int] | None
) -> dict:
    """Ust serit: toplam alarm, onaylanan, haberlesme kesintisi, siniflanmamis.

    TOPLAM SAYACTAN GELIR (`alarm_daily_counts`), takvimle AYNI kaynak.
    Onceden alarm SATIRLARI sayiliyordu ve serit takvimle celisiyordu:
    baslikta "6 alarm" yazarken takvim baska bir sey gosteriyordu — cunku
    satirlar eksilirken tetiklenme sayisi eksilmiyor.

    Onay/haberlesme/siniflanmamis sayilari ise DURUM sorulari; onlarin
    cevabi alarm satirlarindadir. `ack_ratio` de bu yuzden satir toplamina
    gore hesaplanir — "elimizdeki kayitlarin yuzde kaci onaylandi".
    """
    base = _alarm_temel(days, visible_device_ids).subquery()
    a = base.c
    tetiklenme = db.scalar(
        _sayac_temel(days, visible_device_ids).with_only_columns(
            func.coalesce(func.sum(AlarmDailyCount.count), 0)
        )
    ) or 0
    toplam = db.scalar(select(func.count()).select_from(base)) or 0
    if toplam == 0:
        return {
            "total": int(tetiklenme),
            "acknowledged": 0,
            "comm_outages": 0,
            "unclassified": 0,
            "ack_ratio": 0.0,
        }
    onayli = db.scalar(
        select(func.count()).select_from(base).where(a.acknowledged.is_(True))
    ) or 0
    kesinti = db.scalar(
        select(func.count()).select_from(base).where(a.kind == "comm_loss")
    ) or 0
    # `kind` eklenmeden onceki kayitlar. GORUNUR OLMALI: haberlesme
    # sayisinin neden dusuk oldugunu aciklayan tek sey bu.
    siniflanmamis = db.scalar(
        select(func.count()).select_from(base).where(a.kind.is_(None))
    ) or 0
    return {
        "total": int(tetiklenme),
        "acknowledged": int(onayli),
        "comm_outages": int(kesinti),
        "unclassified": int(siniflanmamis),
        "ack_ratio": round(onayli / toplam, 4),
    }


#: Takvimde cizilecek EN FAZLA gun. Uc yillik pencere 1095 kare demek;
#: GitHub'in yillik gorunumu 53 sutundur ve okunabilirligin siniri oradadir.
#: Tavan asilirsa pencerenin SON gunleri gosterilir (eskisi degil) —
#: operatorun sordugu sey her zaman "son donemde ne oldu".
CALENDAR_MAX_DAYS = 371


def alarm_takvimi(
    db: Session, *, days: int, visible_device_ids: set[int] | None
) -> dict:
    """Gun gun alarm sikligi — GitHub katki takvimi bicimi.

    NEDEN CIHAZ x ZAMAN MATRISI DEGIL
    ---------------------------------
    Matris "hangi cihaz" ve "ne zaman" sorularini AYNI ANDA cevaplar ve
    ikisini de yariya kirpar: 25 satirlik tavan yuzunden filonun geri kalani
    gorunmez, sutunlar da yalnizca VERI OLAN gunlerde acildigi icin iki
    gunluk bir veri sonsuza kadar "iki sutunluk" bir grafik uretir. Ekranda
    sahanin ritmi degil, veri tabaninin sekli gorunur.

    Takvim tek bir soruyu tam cevaplar: SAHA NE ZAMAN GURULTULUYDU. Bos gun
    de kare acar — sessiz gecen bir hafta, grafikte gercekten bir hafta
    genisligindedir. Yogunluk koyulukla okunur, tarih hizasi haftalarla.

    BOS GUN ILE VERI OLMAYAN GUN AYNI DEGIL: pencere basi kurulumun
    oncesine dusuyorsa o gunler de 0 gorunur. Bunu ayirmak icin yanit
    `first_alarm_at` tasir; arayuz oncesini soluk cizer.
    """
    # KAYNAK: GUNLUK SAYAC (`alarm_daily_counts`), alarm satirlari DEGIL.
    #
    # Eskiden `alarm_events` gun gun gruplaniyordu ve takvim bos gorunuyordu:
    # o tablo bir DURUM tablosu ve satirlari eksiliyor (tekrar tetikleyen
    # alarm oncekinin yerine geciyor, kapanan kayit arsive dusuyor, gunu
    # gelen retention'a takiliyor). Grafik "gecmiste ne oldu" diye soruyor;
    # cevabi durum tablosundan okumak, cevabi o tablonun bugunku sekline
    # bagimli kiliyordu. Sayac tetiklenmeyi OLAY olarak tutar (bkz.
    # `alarm_counter_service`), boylece gecmis degismez.
    satirlar = db.execute(
        _sayac_temel(days, visible_device_ids)
        .with_only_columns(
            AlarmDailyCount.day, func.sum(AlarmDailyCount.count).label("adet")
        )
        .group_by(AlarmDailyCount.day)
    ).all()
    sayim = {_gun_metni(r[0]): int(r[1] or 0) for r in satirlar}

    # Kovalar VERIDEN degil TAKVIMDEN uretilir — bos gunler de sutun acsin.
    # Gun sinirina yuvarlanir ki "bugun" her zaman son kare olsun.
    takvim = _surekli_gunler(min(max(1, days), CALENDAR_MAX_DAYS))

    gunler: list[dict] = []
    en_cok = 0
    toplam = 0
    for g in takvim:
        adet = sayim.get(g, 0)
        en_cok = max(en_cok, adet)
        toplam += adet
        gunler.append({"date": g, "count": adet})

    # ILK ALARM — pencere basi kurulumun oncesine dusuyorsa arayuz oncesini
    # soluk cizsin diye. Sayacin en eski GUNU; pencereyle sinirli degil ki
    # "bu saha ne zamandir izleniyor" sorusu dogru cevaplansin.
    ilk_gun = db.scalar(
        select(func.min(AlarmDailyCount.day)).where(AlarmDailyCount.count > 0)
    )
    ilk_alarm = (
        datetime.combine(ilk_gun, time.min, tzinfo=timezone.utc)
        if isinstance(ilk_gun, date_type)
        else None
    )
    return {
        "start": takvim[0],
        "end": takvim[-1],
        "days": gunler,
        "max": en_cok,
        "total": toplam,
        # Pencere kurulumun oncesine uzaniyorsa "0 alarm" ile "veri yok"
        # ayrilabilsin diye. None = pencerede hic alarm yok.
        "first_alarm_at": ilk_alarm,
        # Pencere tavana takildiysa arayuz bunu SOYLEMELI; aksi halde
        # "365 gun sectim ama 371 kare var" gibi sessiz bir sapma olurdu.
        "truncated": days > CALENDAR_MAX_DAYS,
    }


def sistem_sagligi(
    db: Session, *, days: int = DEFAULT_WINDOW_DAYS, visible_device_ids: set[int] | None
) -> dict:
    """Alarm yogunlugunun IKI KESITI + ozet.

    Arayuzde "Hat Ariza Yogunlugu" sekmesini besler; sekmedeki anahtar
    kesitler arasinda gecer:
      * `alarm_calendar` -> NE ZAMAN (gun gun, takvim)
      * `alarm_heatmap`  -> HANGI CIHAZ, ne zaman (cihaz x zaman matrisi)

    IKISI AYNI YANITTA: ayni pencere ve ayni kapsam uzerinde hesaplaniyorlar
    ve arayuzde anahtarla degisiyorlar. Ayri uclar olsaydi her gecis yeni
    bir istek atar, ustelik iki kesit farkli donemleri gosterebilirdi.

    KURAL SIRALAMASI VE KOPAN CIHAZLAR BURADA DEGIL: ikisi de CIHAZ
    duzeyinde sorular ve cihaz sagligi ucundan doner
    (`/faults/device-health`).
    """
    return {
        "window_days": days,
        "alarm_summary": alarm_ozeti(db, days=days, visible_device_ids=visible_device_ids),
        "alarm_calendar": alarm_takvimi(
            db, days=days, visible_device_ids=visible_device_ids
        ),
        # Matris sayfanin penceresini IZLEMEZ — hep son 30 gun, kesintisiz
        # gunluk sutunlarla (bkz. HEATMAP_WINDOW_DAYS). 365 gunluk pencerede
        # 25 satirlik matris sutun basina bir pikselin altina duserdi.
        "alarm_heatmap": alarm_isi_haritasi(
            db,
            days=HEATMAP_WINDOW_DAYS,
            visible_device_ids=visible_device_ids,
            surekli=True,
        ),
    }


def sankey_akisi(
    db: Session, *, days: int, visible_line_ids: set[int] | None
) -> dict:
    """Bolge -> Hat -> Faz akisi (Sankey diyagrami icin).

    NEDEN UC KADEME: "hangi hatta cok ariza var" tek basina bir sayidir;
    Sankey'in kattigi sey AKISIN NEREYE GITTIGI. Bolgeden hatta, hattan faza
    inen kalinliklar "su bolgedeki arizalarin cogu tek bir hatta ve o hattin
    da A fazinda toplaniyor" gibi bir deseni tek bakista gosterir — uc ayri
    cubuk grafigi bunu gostermez.

    FAZI OLMAYAN kayitlar akisa GIRMEZ. "Bilinmiyor" diye bir dugum eklemek,
    olcum eksikligini akisin bir kolu gibi gosterirdi; Sankey'de kalinlik
    "gercekten oraya giden miktar" demektir.

    HIYERARSI GERCEK TOPOLOJIDIR: bolge -> ANA HAT -> (kol -> kolun kolu ...)
    -> faz. Bransman kolu ayri bir `Line` kaydidir ama hattin KARDESI degil,
    COCUGUDUR (`Line.branched_from_pole_id` ana hattin bir diregini gosterir).
    Onceki surum tum hatlari bolgenin altina duz diziyordu: "BR-4" ile "ANA
    HAT" ayni kademede duruyor, kolun hangi hattan ciktigi kayboluyordu —
    oysa sahada BR-4'e giden ekip once ANA HAT'tan geciyor.

    KALINLIK GECISLIDIR: bir hattin bolgeden aldigi akis, KENDI arizalari +
    TUM alt kollarininkidir. Aksi halde ana hattin girisi cikisindan kucuk
    kalir ve Sankey'in temel okumasi ("giren = cikan") bozulur. Arizasi
    olmayan bir ana hat da, altindaki kolun arizasi varsa GECIS dugumu olarak
    gorunur; zincir kopmaz.

    KAPSAM SIZINTISI YOK: operatorun goremedigi bir ust hat zincire
    EKLENMEZ; kol o durumda dogrudan bolgeye baglanir. Aksi halde hiyerarsi,
    kapsam disindaki bir hattin ADINI ekrana tasirdi.

    Dugum adlari benzersiz olmali (echarts dugumleri ADA gore eslestirir).
    Onek + KIMLIK tasirlar ("H12:ANA HAT"): iki bolgede ayni adli iki hat
    varsa isimden eslesip tek dugume cokerlerdi. Arayuz ilk ":" oncesini
    kirpar, yani ekranda yalnizca ad gorunur.
    """
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    rows = db.execute(
        select(f.line_id, f.phase, func.count().label("adet"))
        .select_from(base)
        .where(f.phase.is_not(None))
        .group_by(f.line_id, f.phase)
    ).all()
    if not rows:
        return {"nodes": [], "links": []}

    # --- Hat hiyerarsisi: kol -> ust hat ---------------------------------
    # `branched_from_pole_id` UST HATTIN diregidir; ustun kimligi o direkten
    # okunur. Tek sorgu: hat sayisi zaten kucuk (yuzler), ariza sayisi degil.
    hatlar = {
        satir.id: satir
        for satir in db.scalars(select(Line)).all()
    }
    direk_hatti = {
        pid: lid
        for pid, lid in db.execute(select(Pole.id, Pole.line_id)).all()
    }
    bolge_adi = {
        rid: ad for rid, ad in db.execute(select(Region.id, Region.name)).all()
    }

    def ust_hat(line_id: int) -> int | None:
        hat = hatlar.get(line_id)
        if hat is None or hat.branched_from_pole_id is None:
            return None
        ust = direk_hatti.get(hat.branched_from_pole_id)
        if ust is None or ust == line_id:
            return None
        # KAPSAM: gorunmeyen ust hat zincire girmez (adi bile sizmamali).
        if visible_line_ids is not None and ust not in visible_line_ids:
            return None
        return ust

    # --- Kendi arizalari + alt kollarin toplami ---------------------------
    kendi: dict[int, int] = {}
    faz_dagilimi: dict[tuple[int, str], int] = {}
    for line_id, faz, adet in rows:
        kendi[line_id] = kendi.get(line_id, 0) + int(adet)
        anahtar = (line_id, str(faz).upper())
        faz_dagilimi[anahtar] = faz_dagilimi.get(anahtar, 0) + int(adet)

    # Zincirdeki TUM hatlar (arizasi olmayan gecis hatlari dahil).
    zincir: dict[int, int | None] = {}
    for line_id in list(kendi):
        gecerli = line_id
        # DONGU KORUMASI: bozuk topolojide (A'nin kolu B, B'nin kolu A) sonsuz
        # donguye girmek analiz ekranini tamamen dusururdu.
        gorulen: set[int] = set()
        while gecerli is not None and gecerli not in gorulen:
            gorulen.add(gecerli)
            ust = ust_hat(gecerli)
            zincir[gecerli] = ust
            gecerli = ust

    def toplam(line_id: int, gorulen: frozenset[int] = frozenset()) -> int:
        """Hattin KENDI + tum alt kollarinin ariza sayisi."""
        if line_id in gorulen:
            return 0
        alt = frozenset({*gorulen, line_id})
        return kendi.get(line_id, 0) + sum(
            toplam(c, alt) for c, u in zincir.items() if u == line_id
        )

    dugumler: dict[str, str] = {}   # ad -> kademe
    baglar: dict[tuple[str, str], int] = {}

    def ekle(kaynak: str, hedef: str, adet: int) -> None:
        if adet <= 0:
            return
        baglar[(kaynak, hedef)] = baglar.get((kaynak, hedef), 0) + adet

    def hat_dugumu(line_id: int) -> str:
        hat = hatlar.get(line_id)
        ad = f"H{line_id}:{hat.name if hat else f'#{line_id}'}"
        dugumler[ad] = "line"
        return ad

    for line_id, ust in zincir.items():
        dugum = hat_dugumu(line_id)
        # Hattin KENDI arizalari faza akar; kollarinkiler kolun kendi
        # dugumunden akar (yoksa ayni ariza iki kez sayilirdi).
        for (lid, faz), adet in faz_dagilimi.items():
            if lid != line_id:
                continue
            faz_dugum = f"F:{faz}"
            dugumler[faz_dugum] = "phase"
            ekle(dugum, faz_dugum, adet)
        if ust is not None:
            ekle(hat_dugumu(ust), dugum, toplam(line_id))
            continue
        # KOK HAT: bolgeye baglanir ve tum alt kollarini da tasir.
        hat = hatlar.get(line_id)
        if hat is None:
            continue
        bolge = f"B{hat.region_id}:{bolge_adi.get(hat.region_id, '—')}"
        dugumler[bolge] = "region"
        ekle(bolge, dugum, toplam(line_id))

    return {
        "nodes": [{"name": ad, "tier": kademe} for ad, kademe in dugumler.items()],
        "links": [
            {"source": k, "target": h, "value": v}
            for (k, h), v in sorted(baglar.items(), key=lambda x: -x[1])
        ],
    }
