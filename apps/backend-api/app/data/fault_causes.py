"""Hat arizasi SEBEP KATALOGU — analiz katmaninin etiket kumesi.

NEDEN SABIT LISTE
-----------------
Sebep serbest metin olarak toplanirsa istatistik cikmaz: ayni olay "agac
degdi", "dal temasi", "agactan kaynakli" diye on farkli yazilir ve
"en sik sebep hangisi" sorusu cevapsiz kalir. Bu yuzden saha ekibi
KATALOGDAN secer; ayrintiyi `cause_detail` serbest alanina yazar.

LISTE NEDEN BU KADAR KISA
-------------------------
Uzun liste doldurulmaz. Saha ekibi telefonda, arizanin basinda, acele
ediyor; 30 secenekli bir menu ya bos gecilir ya da ilk siradaki secilir —
ikisi de veriyi bozar. Buradaki kume orta gerilim havai hat dagitiminda
gercekten sik gorulen sebeplerdir; yetmedigi anlasilirsa `other` +
`cause_detail` ciftinden yeni kod TURETILIR (once veriye bakilir, sonra
liste buyur — tersi degil).

KOD DEGISTIRILEMEZ
------------------
`code` degerleri veritabanina yazilir ve gecmis kayitlarla karsilastirilir.
Bir kodu yeniden adlandirmak gecmisi bozar; etiket metni (`label_tr`)
serbestce degistirilebilir, kod DEGISMEZ.
"""

from __future__ import annotations

from typing import TypedDict


class FaultCause(TypedDict):
    code: str
    label_tr: str
    label_en: str
    #: Kabaca hangi ailedendir — dagilim grafiginde gruplamak icin.
    group: str


#: Sebep ailesi kodlari (grafik gruplamasi).
CAUSE_GROUPS = ("external", "equipment", "weather", "operational", "unknown")

FAULT_CAUSES: tuple[FaultCause, ...] = (
    # --- Dis etken ---
    {"code": "tree_contact", "label_tr": "Ağaç / dal teması", "label_en": "Tree or branch contact", "group": "external"},
    {"code": "animal", "label_tr": "Hayvan teması (kuş, kedi vb.)", "label_en": "Animal contact", "group": "external"},
    {"code": "third_party", "label_tr": "Üçüncü şahıs teması (iş makinesi, araç)", "label_en": "Third-party contact", "group": "external"},
    {"code": "foreign_object", "label_tr": "Yabancı cisim (balon, branda, tel)", "label_en": "Foreign object", "group": "external"},
    # --- Ekipman ---
    {"code": "insulator", "label_tr": "İzolatör arızası / kırık", "label_en": "Insulator failure", "group": "equipment"},
    {"code": "conductor_break", "label_tr": "İletken kopması", "label_en": "Conductor break", "group": "equipment"},
    {"code": "joint_connector", "label_tr": "Ek / klemens gevşemesi", "label_en": "Joint or connector failure", "group": "equipment"},
    {"code": "pole_damage", "label_tr": "Direk hasarı / devrilme", "label_en": "Pole damage", "group": "equipment"},
    {"code": "cable_termination", "label_tr": "Kablo başlığı / yeraltı geçişi", "label_en": "Cable termination", "group": "equipment"},
    {"code": "transformer", "label_tr": "Trafo kaynaklı", "label_en": "Transformer related", "group": "equipment"},
    # --- Hava ---
    {"code": "lightning", "label_tr": "Yıldırım", "label_en": "Lightning", "group": "weather"},
    {"code": "wind_storm", "label_tr": "Fırtına / şiddetli rüzgar", "label_en": "Wind or storm", "group": "weather"},
    {"code": "ice_snow", "label_tr": "Buzlanma / kar yükü", "label_en": "Ice or snow load", "group": "weather"},
    {"code": "flood_landslide", "label_tr": "Sel / heyelan", "label_en": "Flood or landslide", "group": "weather"},
    # --- Isletme ---
    {"code": "overload", "label_tr": "Aşırı yük", "label_en": "Overload", "group": "operational"},
    {"code": "switching", "label_tr": "Manevra / şalt işlemi", "label_en": "Switching operation", "group": "operational"},
    {"code": "planned_work", "label_tr": "Planlı çalışma", "label_en": "Planned work", "group": "operational"},
    # --- Bilinmiyor ---
    # NEDEN GEREKLI: sahada sebep bulunamayan ariza GERCEKTIR ve bu bilgi
    # degerlidir ("bu hatta arizalarin %40'inin sebebi bulunamiyor" bulgusu
    # basli basina bir sinyaldir). Bos birakmakla ayni sey DEGIL: bos =
    # "kimse doldurmadi", bu = "bakildi, bulunamadi".
    {"code": "not_found", "label_tr": "Sebep bulunamadı (arandı)", "label_en": "Cause not found (searched)", "group": "unknown"},
    {"code": "other", "label_tr": "Diğer (açıklama girin)", "label_en": "Other (add detail)", "group": "unknown"},
)

CAUSE_CODES: frozenset[str] = frozenset(c["code"] for c in FAULT_CAUSES)

#: Ariza kalicilik ekseni — sebepten BAGIMSIZ. Ayni sebep (orn. agac temasi)
#: ruzgarda gecici, dal kirilinca kalici olur; ikisini tek alanda toplamak
#: iki farkli soruyu birbirine karistirirdi.
FAULT_KINDS: tuple[tuple[str, str, str], ...] = (
    ("transient", "Geçici (kendiliğinden düzeldi)", "Transient (self-cleared)"),
    ("permanent", "Kalıcı (müdahale gerekti)", "Permanent (intervention needed)"),
    ("unknown", "Belirsiz", "Unknown"),
)
FAULT_KIND_CODES: frozenset[str] = frozenset(k[0] for k in FAULT_KINDS)

#: Etkilenen faz. Tek faz / iki faz / uc faz ayrimi sebep cikariminda
#: belirleyicidir: tek faz-toprak cogunlukla dis etken, uc faz cogunlukla
#: ekipman ya da asiri yuk isaretidir.
PHASES: frozenset[str] = frozenset({"a", "b", "c", "ab", "ac", "bc", "abc", "unknown"})
