"""Ariza aninda cihazin durumunu ARIZA KAYDINA yazar.

NEDEN ANLIK GORUNTU
-------------------
Ham telemetri 90 gunde dusuyor (saatlik ozet 2 yil kaliyor ama ondan BELIRLI
bir arizanin tepe akimini geri cikarmak kayipli — kova tepe degeri o arizaya
ait olmayabilir). Ariza analizi yillar boyunca anlamli olmali, bu yuzden
arizayi TANIMLAYAN birkac deger kaydin kendisine kopyalanir. Bilincli
denormalizasyon.

NE OKUNUR
---------
`telemetry_latest` — arizayi tespit eden cihazin O ANDAKI degerleri.
Yalnizca `fault_inference.RELEVANT_SIGNALS` + birkac analog okunur; cihazin
193 sinyalinin tamami degil.

UC UNITE = UC FAZ
-----------------
`master` / `sat01` / `sat02` ayri fazlara kelepcelenir. Analog degerlerde
faz basina ayri okuma vardir; ariza akimi olarak UC FAZIN EN BUYUGU alinir
(arizali faz en yuksek akimi gorur). Ortalama almak arizali fazi saglam
fazlarla seyreltir ve tepe degeri gizlerdi.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.telemetry_latest import TelemetryLatest
from app.services.fault_inference import (
    RELEVANT_SIGNALS,
    infer,
    source_of,
    strip_source,
)

logger = logging.getLogger(__name__)

#: Anlik goruntuye alinan analog sinyaller (kaynak oneki soyulmus).
A_FAULT_CURRENT = "fault_current"
A_ACTUAL_CURRENT = "actual_current"
A_LAST_GOOD_CURRENT = "last_good_known_current"
A_CONDUCTOR_TEMP = "conductor_temperature"
C_MOMENTARY = "momentary_fault_counter"
C_PERMANENT = "permanent_fault_counter"

_ANALOG_KEYS = frozenset(
    {A_FAULT_CURRENT, A_ACTUAL_CURRENT, A_LAST_GOOD_CURRENT, A_CONDUCTOR_TEMP}
)
_COUNTER_KEYS = frozenset({C_MOMENTARY, C_PERMANENT})


#: (unite, kolon adi) — uc katmanda da ayni alan adlari kullanilir.
_PHASE_FIELDS = (
    ("master", "phase_master"),
    ("sat01", "phase_sat01"),
    ("sat02", "phase_sat02"),
)


def _katmani_uygula(esleme: dict[str, str], row) -> bool:  # noqa: ANN001
    """Bir kaynagin (proje ya da cihaz) doldurdugu alanlari eslemeye isler.

    Donus: bu katman bir sey degistirdi mi. KISMI doldurma desteklenir —
    yalnizca `sat01` farkliysa yalnizca o yazilir, digerleri alttaki
    katmandan gelmeye devam eder. Tumunu zorunlu kilmak, tek bir kelepce
    degisikligi icin ucunu birden girmeyi gerektirirdi.
    """
    if row is None:
        return False
    degisti = False
    for unite, alan in _PHASE_FIELDS:
        deger = (getattr(row, alan, None) or "").strip().lower()
        if deger:
            esleme[unite] = deger
            degisti = True
    return degisti


def resolve_source_phase(
    db: Session, *, device_id: int | None = None
) -> dict[str, str] | None:
    """Unite -> faz eslemesini COZ: cihaz -> proje -> kod varsayilani.

    Kelepceyi hangi faza takacagina sahadaki kisi karar verir ve bu
    CIHAZDAN CIHAZA degisebilir. Ama 600 cihazlik bir kurulumda her cihaz
    icin uc alan doldurma zorunlulugu pratikte "hicbiri doldurulmaz"
    demektir; bu yuzden kurulumun genel konvansiyonu Proje Ayarlari'nda
    bir kez girilir, cihaz alani yalnizca ISTISNALAR icindir.

    Hicbir katman bir sey soylemediyse None doner ve cagiran taraf kod
    varsayilanini (`DEFAULT_SOURCE_PHASE`) kullanir.
    """
    from app.models.device import Device
    from app.models.project_settings import ProjectSettings
    from app.services.fault_inference import DEFAULT_SOURCE_PHASE

    esleme = dict(DEFAULT_SOURCE_PHASE)
    # Sira onemli: once proje (genel konvansiyon), sonra cihaz (istisna)
    # — cihaz katmani projeyi EZER.
    degisti = _katmani_uygula(esleme, db.get(ProjectSettings, 1))
    if device_id is not None:
        degisti = _katmani_uygula(esleme, db.get(Device, device_id)) or degisti
    return esleme if degisti else None


def _truthy(value: float | None, value_string: str | None) -> bool:
    """Bir ikili sinyal AKTIF mi?

    Deger yoksa metne bakilir: DNP3 tarafi bazi noktalarda sayisal alani
    doldurmaz (bkz. modbus-outbound'daki ayni ikilik). Yalnizca `value`
    okumak o sinyalleri sessizce "pasif" gosterirdi.
    """
    if value is not None:
        return value != 0
    text = (value_string or "").strip().lower()
    return text in ("1", "true", "on", "yes", "active", "set", "closed")


def _en_buyuk(degerler: list[float]) -> float | None:
    return max(degerler) if degerler else None


def build_snapshot(
    db: Session,
    *,
    device_id: int,
    source_phase: dict[str, str] | None = None,
) -> dict:
    """Cihazin su anki durumundan ariza alanlarini uretir.

    Donus: dogrudan `FaultEvent` alanlarina atanabilecek sozluk. Hicbir
    alan UYDURULMAZ — okunamayan deger sozlukte yer almaz, boylece cagiran
    taraf mevcut degeri EZMEZ.
    """
    rows = list(
        db.scalars(select(TelemetryLatest).where(TelemetryLatest.device_id == device_id)).all()
    )
    if not rows:
        return {}

    aktif_sinyaller: list[str] = []
    ariza_akimlari: list[float] = []
    yuk_akimlari: list[float] = []
    sicakliklar: list[float] = []
    momentary: list[float] = []
    permanent: list[float] = []

    for row in rows:
        ad = strip_source(row.signal_key)
        if ad in RELEVANT_SIGNALS:
            if _truthy(row.value, row.value_string):
                # TAM anahtar saklanir (onek dahil) — faz cikarimi onu ister.
                aktif_sinyaller.append(row.signal_key)
            continue
        if row.value is None:
            continue
        if ad == A_FAULT_CURRENT:
            ariza_akimlari.append(row.value)
        elif ad in (A_ACTUAL_CURRENT, A_LAST_GOOD_CURRENT):
            yuk_akimlari.append(row.value)
        elif ad == A_CONDUCTOR_TEMP:
            sicakliklar.append(row.value)
        elif ad == C_MOMENTARY:
            momentary.append(row.value)
        elif ad == C_PERMANENT:
            permanent.append(row.value)

    sonuc = infer(
        active_signals=aktif_sinyaller,
        fault_current_a=_en_buyuk(ariza_akimlari),
        load_current_before_a=_en_buyuk(yuk_akimlari),
        source_phase=source_phase,
    )

    alanlar: dict = {
        # Imza HER ZAMAN yazilir (bos liste de bilgidir: "cihaz hicbir
        # ariza bayragi kaldirmamis"). Kararli sirali — ayni ariza her
        # okumada ayni imzayi versin.
        "trigger_signals": sorted(aktif_sinyaller),
        "measured_at": datetime.now(timezone.utc),
    }
    # Cikarim alanlari: yalnizca URETILDIYSE yazilir.
    if sonuc.auto_cause_code is not None:
        alanlar["auto_cause_code"] = sonuc.auto_cause_code
    if sonuc.fault_kind is not None:
        alanlar["fault_kind"] = sonuc.fault_kind
    if sonuc.phase is not None:
        alanlar["phase"] = sonuc.phase
    if sonuc.fault_direction is not None:
        alanlar["fault_direction"] = sonuc.fault_direction

    # Olcumler.
    ariza_akimi = _en_buyuk(ariza_akimlari)
    if ariza_akimi is not None:
        alanlar["fault_current_a"] = ariza_akimi
    yuk = _en_buyuk(yuk_akimlari)
    if yuk is not None:
        alanlar["load_current_before_a"] = yuk
    sicaklik = _en_buyuk(sicakliklar)
    if sicaklik is not None:
        alanlar["conductor_temp_c"] = sicaklik
    if momentary:
        alanlar["momentary_fault_count"] = int(max(momentary))
    if permanent:
        alanlar["permanent_fault_count"] = int(max(permanent))

    logger.info(
        "fault_snapshot device_id=%d imza=%d auto_cause=%s faz=%s goren=%s",
        device_id,
        len(aktif_sinyaller),
        sonuc.auto_cause_code,
        sonuc.phase,
        ",".join(sonuc.faulted_sources) or "-",
    )
    return alanlar


def apply_snapshot(
    db: Session, fault, *, source_phase: dict[str, str] | None = None
) -> None:  # noqa: ANN001
    """Anlik goruntuyu ariza kaydina uygular.

    HATA YUTULUR: bu cagri ariza motorunun icinde kosuyor. Anlik goruntu
    alinamazsa ariza kaydinin kendisi ACILMAYA devam etmeli — analiz alani
    bos kalir, ariza tespiti etkilenmez. Tersi (ariza kaydini dusurmek) cok
    daha agir bir hata olurdu.
    """
    try:
        # Esleme verilmediyse ZINCIRDEN cozulur (cihaz -> proje -> kod).
        # Cagiranin bunu hatirlamasini beklemek, ayarin sessizce etkisiz
        # kalmasi demekti. Cihaz kimligi ariza kaydindan gelir: faz, arizayi
        # TESPIT EDEN cihazin kelepce duzenine gore yorumlanmali.
        if source_phase is None:
            source_phase = resolve_source_phase(
                db, device_id=fault.last_red_device_id
            )
        alanlar = build_snapshot(
            db, device_id=fault.last_red_device_id, source_phase=source_phase
        )
    except Exception:  # noqa: BLE001
        logger.exception("fault_snapshot_failed device_id=%s", fault.last_red_device_id)
        return
    for ad, deger in alanlar.items():
        setattr(fault, ad, deger)
