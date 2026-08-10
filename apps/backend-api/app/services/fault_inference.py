"""Cihazin alarm imzasindan ariza sebebi/turu cikarimi — KURAL TABANLI.

NEDEN LLM DEGIL
---------------
Buradaki cikarimlarin tamami olculmus bayraklarin mantiksal birlesimi.
"Akim kaybi var ama asiri akim yok" -> iletken kopmasi; bunu bir dil
modeline sormak hem daha yavas hem daha az guvenilir olurdu. Dil modelinin
gercekten katki verdigi yer AYRI: saha ekibinin serbest metnini etikete
cevirmek ve veriyle konusmak. Bu modul o katman HIC KURULMASA DA calisir.

CIKARIMIN SINIRI
----------------
Bu modul SEBEP ONERIR, karar VERMEZ. Ciktisi `auto_cause_code` alanina
yazilir; insanin girdigi `cause_code` ayri durur. Ikisini karsilastirmak
kurallarin isabetini olcer — bir ogrenme katmani eklemeden once bilinmesi
gereken sey tam olarak budur. Kural yanlissa insan etiketi kazanir.

SINYAL KAYNAGI VE FAZ
---------------------
Horstmann SN2 tek bir cihazdir ama UC AYRI unitesi vardir: `master`,
`sat01`, `sat02`. Bu uniteler hatta UC AYRI FAZA kelepcelenir. Yani kaynak
oneki yalnizca "hangi unite" demek degil, **HANGI FAZ** demektir:

    master -> L1 (A)      sat01 -> L2 (B)      sat02 -> L3 (C)

Bu yuzden kural eslestirmesinde onek soyulur (ariza mantigi uniteden
bagimsizdir) ama FAZ CIKARIMINDA onek ASIL BILGIDIR. Yalnizca `sat01`
asiri akim gorduyse bu TEK FAZLI bir arizadir ve tek faz-toprak arizasi
cogunlukla dis etken (agac, hayvan) isaretidir; uc unite birden gorduyse
uc fazli aridir ve ekipman/asiri yuk isaretidir. Onegi atmak bu ayrimi
tamamen kaybettirirdi.

HANGI UNITE HANGI FAZDA — KURULUM KARARI
-----------------------------------------
Kelepceyi hangi faza takacagina kurulumcu karar verir. Varsayilan
`master=a, sat01=b, sat02=c`; saha bunu farkli kurduysa `source_phase`
parametresiyle gecilir (Proje Ayarlari'ndan gelir). Varsayilani sabit
kabul etmek, faz etiketlerinin sessizce yanlis birikmesi demek olurdu.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Kaynak oneki soyulmus sinyal adlari.
S_OVERCURRENT = "overcurrent_tripped"
S_DIDT_TRIP = "delta_i_delta_t_tripped"
S_DIDT_PICKUP = "delta_i_delta_t_pick_up"
S_CURRENT_LOSS = "current_loss"
S_VOLTAGE_LOSS = "voltage_loss"
S_VOLTAGE_LOSS_ALL = "voltage_loss_all_units"
S_PERMANENT = "permanent_fault"
S_MOMENTARY = "momentary_fault"
S_TEMP_ALARM = "conductor_temperature_alarm"
S_TAMPER = "tamper_detection"
S_DIR_GREEN = "overcurrent_fault_direction_green_a"
S_DIR_RED = "overcurrent_fault_direction_red_b"
S_DIDT_DIR_GREEN = "delta_i_delta_t_fault_direction_green_a"
S_DIDT_DIR_RED = "delta_i_delta_t_fault_direction_red_b"


#: Unite -> faz varsayilani. Kurulum farkliysa `source_phase` ile gecilir.
DEFAULT_SOURCE_PHASE: dict[str, str] = {"master": "a", "sat01": "b", "sat02": "c"}

#: "Bu unite arizayi GORDU" anlamina gelen sinyaller. Faz cikarimi yalnizca
#: bunlara bakar; `battery_status` ya da `config_update` gibi isletme
#: bayraklari bir fazi arizali gostermemeli.
FAULT_INDICATORS: frozenset[str] = frozenset(
    {
        "overcurrent_tripped",
        "delta_i_delta_t_tripped",
        "delta_i_delta_t_pick_up",
        "current_loss",
        "permanent_fault",
        "momentary_fault",
    }
)


#: Kurallarin BAKTIGI tum bayraklar. Anlik goruntu alinirken cihazin
#: yuzlerce sinyali icinden yalnizca bunlar okunur — hem ucuz hem de
#: "imzaya ne girdi" sorusunun cevabi tek yerde tanimli kalir.
RELEVANT_SIGNALS: frozenset[str] = frozenset(
    {
        S_OVERCURRENT, S_DIDT_TRIP, S_DIDT_PICKUP, S_CURRENT_LOSS,
        S_VOLTAGE_LOSS, S_VOLTAGE_LOSS_ALL, S_PERMANENT, S_MOMENTARY,
        S_TEMP_ALARM, S_TAMPER,
        S_DIR_GREEN, S_DIR_RED, S_DIDT_DIR_GREEN, S_DIDT_DIR_RED,
    }
)


def strip_source(signal_key: str) -> str:
    """`master.overcurrent_tripped` -> `overcurrent_tripped`."""
    return signal_key.split(".", 1)[1] if "." in signal_key else signal_key


def source_of(signal_key: str) -> str:
    """`sat01.current_loss` -> `sat01`. Onek yoksa `master` varsayilir."""
    return signal_key.split(".", 1)[0] if "." in signal_key else "master"


def phase_from_sources(
    active_signals: list[str] | tuple[str, ...],
    source_phase: dict[str, str] | None = None,
) -> str | None:
    """Arizayi GOREN unitelerden etkilenen fazi turetir.

    Tek unite -> tek faz ("a"), iki unite -> "ab" gibi, uc unite -> "abc".
    Hicbir unite ariza sinyali kaldirmadiysa None (bos = cikarilamadi;
    "unknown" yazmak bilgi tasimaz).
    """
    esleme = source_phase or DEFAULT_SOURCE_PHASE
    fazlar: set[str] = set()
    for key in active_signals:
        if strip_source(key) in FAULT_INDICATORS:
            faz = esleme.get(source_of(key))
            if faz:
                fazlar.add(faz)
    if not fazlar:
        return None
    # Kararli siralama: "ac" her zaman "ac", asla "ca".
    return "".join(sorted(fazlar))


@dataclass(frozen=True)
class InferenceResult:
    """Kural ciktisi. Hicbir alani zorunlu degil — emin olunamayan seye
    deger UYDURULMAZ, NULL birakilir."""

    auto_cause_code: str | None = None
    fault_kind: str | None = None
    fault_direction: str | None = None
    #: Etkilenen faz — HANGI UNITELERIN arizayi gordugunden turetilir
    #: (master/sat01/sat02 ayri fazlara kelepcelenir). "a" tek faz,
    #: "abc" uc faz. Bu ayrim sebep cikariminda belirleyici.
    phase: str | None = None
    #: Arizayi goren unitelerin ham listesi — arayuzde "hangi cihaz ne dedi".
    faulted_sources: tuple[str, ...] = field(default_factory=tuple)
    #: Hangi kuralin eslestigi — arayuzde "neden boyle dedi" sorusunun cevabi.
    #: Aciklanamayan bir oneri, operatorun guvenmeyecegi bir oneridir.
    reason: str | None = None
    #: Cikarima katkida bulunan sinyaller (kaynak oneki SOYULMUS).
    matched: tuple[str, ...] = field(default_factory=tuple)


def _kind_from_signals(aktif: set[str]) -> str | None:
    """Kalicilik CIHAZDAN okunur — saha ekibine sorulmaz.

    Ikisi birden aktifse `permanent` kazanir: cihaz once gecici gorup tekrar
    kapama denemis, tutmayinca kalici isaretlemistir. Sirali bir olayin son
    hali baglayicidir.
    """
    if S_PERMANENT in aktif:
        return "permanent"
    if S_MOMENTARY in aktif:
        return "transient"
    return None


def _direction_from_signals(aktif: set[str]) -> str | None:
    green = S_DIR_GREEN in aktif or S_DIDT_DIR_GREEN in aktif
    red = S_DIR_RED in aktif or S_DIDT_DIR_RED in aktif
    if green and not red:
        return "green_a"
    if red and not green:
        return "red_b"
    # Ikisi birden ya da hicbiri: yon BELIRSIZ. "unknown" yazmak bilgi
    # tasimadigi icin NULL birakilir (bos = sorulmadi degil, cikmadi).
    return None


def infer(
    *,
    active_signals: list[str] | tuple[str, ...],
    fault_current_a: float | None = None,
    load_current_before_a: float | None = None,
    source_phase: dict[str, str] | None = None,
) -> InferenceResult:
    """Alarm imzasindan sebep onerisi uretir.

    SIRA ONEMLI — ilk eslesen kural kazanir. Siralama "ne kadar ayirt
    edici" oldugna gore: tamper tek basina kesin bir isaret, asiri akim ise
    onlarca sebebin ortak sonucu.
    """
    aktif = {strip_source(s) for s in active_signals}
    kind = _kind_from_signals(aktif)
    yon = _direction_from_signals(aktif)
    faz = phase_from_sources(active_signals, source_phase)
    gorenler = tuple(
        sorted({source_of(k) for k in active_signals if strip_source(k) in FAULT_INDICATORS})
    )

    def sonuc(code: str | None, reason: str, *matched: str) -> InferenceResult:
        return InferenceResult(
            auto_cause_code=code,
            fault_kind=kind,
            fault_direction=yon,
            phase=faz,
            faulted_sources=gorenler,
            reason=reason,
            matched=tuple(m for m in matched if m in aktif),
        )

    # 1) Kurcalama/darbe algilandi — ucuncu sahis mudahalesinin en net isareti.
    if S_TAMPER in aktif:
        return sonuc("third_party", "Cihazda kurcalama/darbe algilandi", S_TAMPER)

    # 2) Iletken kopmasi: akim KESILDI ama asiri akim YOK. Kisa devrede once
    #    akim FIRLAR; burada firlama olmadan kesilme var.
    if S_CURRENT_LOSS in aktif and S_OVERCURRENT not in aktif:
        return sonuc(
            "conductor_break",
            "Akim kaybi var, asiri akim yok — iletken kopmasi/acik devre",
            S_CURRENT_LOSS,
            S_VOLTAGE_LOSS,
        )

    # 3) Termal: sicaklik alarmi + asiri akim yok -> asiri yuk. Asiri akim
    #    VARSA bu bir kisa devredir ve sicaklik onun SONUCUDUR, sebebi degil.
    if S_TEMP_ALARM in aktif and S_OVERCURRENT not in aktif:
        return sonuc(
            "overload",
            "Iletken sicaklik alarmi, asiri akim yok — asiri yuk",
            S_TEMP_ALARM,
        )

    # 4) Yuksek empedansli ariza: dI/dt tetikledi ama asiri akim esigi
    #    asilmadi. Klasik ornegi agac/dal temasidir — akim yavas ve sinirli
    #    artar, koruma esigine ulasmaz. Bu YALNIZCA bir onerdir; dogrulamayi
    #    saha yapar.
    if (S_DIDT_TRIP in aktif or S_DIDT_PICKUP in aktif) and S_OVERCURRENT not in aktif:
        return sonuc(
            "tree_contact",
            "dI/dt tetiklendi, asiri akim yok — yuksek empedansli ariza "
            "(tipik olarak agac/dal temasi); sahada dogrulanmali",
            S_DIDT_TRIP,
            S_DIDT_PICKUP,
        )

    # 5) Asiri akim: kisa devre KESIN ama SEBEP degil. Onlarca sebep (yildirim,
    #    hayvan, izolator, ucuncu sahis) ayni sonucu verir. Bilerek sebep
    #    ONERILMEZ — uydurmak, analiz katmanini yanlis egitmek olurdu.
    #    Yine de tur ve yon doldurulur, olcum kaydedilir.
    if S_OVERCURRENT in aktif:
        # Faz SAYISI ayirt edici bir ipucudur: tek faz-toprak cogunlukla dis
        # etken (agac/hayvan), uc faz cogunlukla ekipman ya da asiri yuk.
        # Yine de SEBEP UYDURULMAZ — yalnizca gerekceye yazilir.
        if faz and len(faz) == 1:
            ek = f" Tek faz ({faz.upper()}) — dis etken olasiligi yuksek."
        elif faz and len(faz) == 3:
            ek = " Uc faz — ekipman ya da asiri yuk olasiligi yuksek."
        else:
            ek = ""
        return sonuc(
            None,
            "Asiri akim (kisa devre) — sebep cihaz verisinden ayirt edilemez, "
            "saha tespiti gerekli." + ek,
            S_OVERCURRENT,
        )

    # 6) Yalnizca gerilim kaybi: besleme kesintisi; hat arizasi olmayabilir
    #    (ust kademeden gelen kesinti). Sebep onerilmez.
    if S_VOLTAGE_LOSS_ALL in aktif or S_VOLTAGE_LOSS in aktif:
        return sonuc(
            None,
            "Gerilim kaybi — ust kademe kesintisi olabilir, hat arizasi "
            "olmayabilir",
            S_VOLTAGE_LOSS,
            S_VOLTAGE_LOSS_ALL,
        )

    return InferenceResult(
        fault_kind=kind, fault_direction=yon, phase=faz, faulted_sources=gorenler
    )
