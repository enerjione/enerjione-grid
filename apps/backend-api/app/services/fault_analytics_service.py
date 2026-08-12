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
from datetime import datetime, timedelta, timezone

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
    }


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

from app.models.alarm import AlarmEvent  # noqa: E402
from app.models.device import Device  # noqa: E402


def _alarm_temel(days: int, visible_device_ids: set[int] | None):
    stmt = select(AlarmEvent).where(AlarmEvent.created_at >= _window_start(days))
    if visible_device_ids is None:
        return stmt
    if not visible_device_ids:
        return stmt.where(False)
    return stmt.where(AlarmEvent.device_id.in_(visible_device_ids))


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


def _kova_ifadesi(db: Session, damga, gunluk: bool):  # noqa: ANN001
    """Zaman kovasi etiketi — lehceye gore. Gunluk `YYYY-MM-DD`, saatlik
    `YYYY-MM-DD HH`."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.to_char(damga, "YYYY-MM-DD" if gunluk else "YYYY-MM-DD HH24")
    return func.strftime("%Y-%m-%d" if gunluk else "%Y-%m-%d %H", damga)


def alarm_isi_haritasi(
    db: Session,
    *,
    days: int,
    visible_device_ids: set[int] | None,
    limit: int = HEATMAP_TOP_DEVICES,
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
    """
    base = _alarm_temel(days, visible_device_ids).subquery()
    a = base.c

    # 1) Satirlar: en cok alarm ureten cihazlar. Hic alarm uretmemis cihaz
    #    ZATEN gelmez — bos bir satir cizmenin bilgi degeri yok.
    sayimlar = (
        select(a.device_id, func.count().label("adet"))
        .select_from(base)
        .where(a.device_id.is_not(None))
        .group_by(a.device_id)
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
            # `window_days` UST duzeyde (`sistem_sagligi`) veriliyor; burada
            # tekrar etmek ayrisabilen ikinci bir kaynak yaratirdi.
            "bucket": "day",
            "buckets": [],
            "devices": [],
            "cells": [],
            "max": 0,
            "device_total": 0,
            "truncated": False,
        }

    gunluk = days > 2
    kova = _kova_ifadesi(db, a.created_at, gunluk)
    id_sira = {int(r[0]): i for i, r in enumerate(satirlar)}

    hucreler_ham = db.execute(
        select(a.device_id, kova.label("kova"), func.count().label("adet"))
        .select_from(base)
        .where(a.device_id.in_(list(id_sira)))
        .group_by(a.device_id, kova)
    ).all()

    # 2) Sutunlar: veride GORULEN kovalar, kronolojik. Bos gunler icin sutun
    #    acilmaz — 365 gunluk pencerede sahanin sessiz gecen aylari grafigi
    #    okunmaz genislige tasirdi.
    kovalar = sorted({str(r[1]) for r in hucreler_ham})
    if len(kovalar) > HEATMAP_MAX_COLS:
        kovalar = kovalar[-HEATMAP_MAX_COLS:]
    kova_sira = {k: i for i, k in enumerate(kovalar)}

    hucreler: list[list[int]] = []
    en_cok = 0
    for dev_id, k, adet in hucreler_ham:
        sutun = kova_sira.get(str(k))
        if sutun is None:
            continue  # tavan disinda kalan eski kova
        adet = int(adet)
        en_cok = max(en_cok, adet)
        hucreler.append([sutun, id_sira[int(dev_id)], adet])

    return {
        "bucket": "day" if gunluk else "hour",
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


def alarm_ozeti(
    db: Session, *, days: int, visible_device_ids: set[int] | None
) -> dict:
    """Ust serit: toplam alarm, onaylanan, haberlesme kesintisi, siniflanmamis."""
    base = _alarm_temel(days, visible_device_ids).subquery()
    a = base.c
    toplam = db.scalar(select(func.count()).select_from(base)) or 0
    if toplam == 0:
        return {
            "total": 0,
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
        "total": int(toplam),
        "acknowledged": int(onayli),
        "comm_outages": int(kesinti),
        "unclassified": int(siniflanmamis),
        "ack_ratio": round(onayli / toplam, 4),
    }


def sistem_sagligi(
    db: Session, *, days: int = DEFAULT_WINDOW_DAYS, visible_device_ids: set[int] | None
) -> dict:
    """Alarm sikligi + haberlesme kararliligi + cihaz x zaman yogunlugu."""
    return {
        "window_days": days,
        "alarm_summary": alarm_ozeti(db, days=days, visible_device_ids=visible_device_ids),
        "top_rules": alarm_sikligi(db, days=days, visible_device_ids=visible_device_ids),
        "flapping_devices": haberlesme_kararsizligi(
            db, days=days, visible_device_ids=visible_device_ids
        ),
        # Ayni uctan doner: ucu de AYNI pencere ve AYNI kapsam uzerinde
        # hesaplaniyor; ayri istek olsaydi ekranin bir parcasi digerinden
        # farkli bir donemi gosterebilirdi.
        "alarm_heatmap": alarm_isi_haritasi(
            db, days=days, visible_device_ids=visible_device_ids
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

    Dugum adlari benzersiz olmali (echarts dugumleri ADA gore eslestirir);
    bu yuzden kademe oneki tasirlar: "B:Merkez", "H:ANA HAT", "F:A".
    """
    base = _temel_sorgu(days, visible_line_ids).subquery()
    f = base.c
    rows = db.execute(
        select(
            Region.name.label("bolge"),
            Line.name.label("hat"),
            f.phase,
            func.count().label("adet"),
        )
        .select_from(base)
        .join(Line, Line.id == f.line_id)
        .join(Region, Region.id == f.region_id)
        .where(f.phase.is_not(None))
        .group_by(Region.name, Line.name, f.phase)
    ).all()

    dugumler: dict[str, str] = {}   # ad -> kademe
    baglar: dict[tuple[str, str], int] = {}

    def ekle(kaynak: str, hedef: str, adet: int) -> None:
        baglar[(kaynak, hedef)] = baglar.get((kaynak, hedef), 0) + adet

    for bolge, hat, faz, adet in rows:
        b, h, fz = f"B:{bolge}", f"H:{hat}", f"F:{str(faz).upper()}"
        dugumler[b] = "region"
        dugumler[h] = "line"
        dugumler[fz] = "phase"
        ekle(b, h, int(adet))
        ekle(h, fz, int(adet))

    return {
        "nodes": [{"name": ad, "tier": kademe} for ad, kademe in dugumler.items()],
        "links": [
            {"source": k, "target": h, "value": v}
            for (k, h), v in sorted(baglar.items(), key=lambda x: -x[1])
        ],
    }
