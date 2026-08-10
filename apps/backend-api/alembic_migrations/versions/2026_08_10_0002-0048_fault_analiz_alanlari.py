"""fault_analiz_alanlari

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-10 00:00:02.000000

Ariza ANALIZ katmaninin veri temeli.

NE EKSIKTI
----------
"Hangi hat en cok ariza cikariyor" sorusu bugun de cevaplanabiliyordu
(`line_id` + `opened_at` yeter). Cevaplanamayan soru "NEDEN" idi: sebebin tek
kaynagi serbest metin `note` ve yorumlardi. Serbest metinden istatistik
cikmaz — ayni olay "agac degdi", "dal temasi", "agactan kaynakli" diye on
farkli yazilir.

BU MIGRATION NE GETIRIYOR
-------------------------
1. Yapilandirilmis sebep (`cause_code` + `cause_detail`), kalicilik ekseni
   (`fault_kind`) ve etkilenen faz (`phase`). Katalog:
   `app/data/fault_causes.py`.

2. Olcum ANLIK GORUNTUSU (`fault_current_a`, `load_current_before_a`,
   `measured_at`). Bilincli denormalizasyon: ham telemetri 90 gunde dusuyor,
   saatlik ozet 2 yil kaliyor ama ondan BELIRLI bir arizanin tepe akimini
   geri cikarmak kayipli (kova tepe degeri o arizaya ait olmayabilir). Ariza
   analizi yillar boyunca anlamli olmali, bu yuzden arizayi tanimlayan birkac
   sayi kaydin KENDISINE yaziliyor.

3. Analiz indeksleri: hat/bolge sikligi, TEKRARLAYAN ACIKLIK
   (`from_pole_id`, `to_pole_id` — direk ID'leri kalicidir, `sequence_no`
   yeniden siralanabilir) ve sebep dagilimi.

GERIYE DONUK VERI
-----------------
Tum kolonlar NULLABLE ve VARSAYILANSIZ. Gecmis arizalar `cause_code IS NULL`
kalir — yani "bilinmiyor". Onlara toplu bir "other" atamak analiz katmanina
UYDURMA etiket beslemek olurdu; bos ile "bakildi, bulunamadi" (`not_found`)
birbirinden ayri kalmali.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("cause_code", sa.String(length=40)),
    ("cause_detail", sa.String(length=500)),
    ("fault_kind", sa.String(length=20)),
    ("phase", sa.String(length=10)),
    # Cihazin alarm imzasi — sebep cikariminin ASIL kaynagi.
    ("trigger_signals", sa.JSON()),
    ("auto_cause_code", sa.String(length=40)),
    ("fault_direction", sa.String(length=20)),
    # Olcum anlik goruntusu.
    ("fault_current_a", sa.Float()),
    ("load_current_before_a", sa.Float()),
    ("conductor_temp_c", sa.Float()),
    ("momentary_fault_count", sa.Integer()),
    ("permanent_fault_count", sa.Integer()),
    ("measured_at", sa.DateTime(timezone=True)),
)

_INDEXES = (
    ("ix_fault_line_opened", ["line_id", "opened_at"]),
    ("ix_fault_region_opened", ["region_id", "opened_at"]),
    ("ix_fault_span", ["from_pole_id", "to_pole_id"]),
    ("ix_fault_cause_opened", ["cause_code", "opened_at"]),
    # Kural ciktisinin isabetini olcmek icin: auto vs insan etiketi.
    ("ix_fault_autocause_opened", ["auto_cause_code", "opened_at"]),
)


#: Unite -> faz eslemesi. IKI KATMAN:
#:   `project_settings` = kurulumun genel konvansiyonu (bir kez girilir)
#:   `devices`          = ISTISNA cihazlar (kelepce farkli takilmissa)
#: Cozum zinciri: cihaz -> proje -> kod varsayilani (a/b/c).
#: Yalnizca cihaz katmani olsaydi 600 cihazlik kurulumda hicbiri
#: doldurulmaz ve veri varsayilana guvenmekten daha kotu olurdu.
_PHASE_COLUMNS = (
    ("phase_master", sa.String(length=4)),
    ("phase_sat01", sa.String(length=4)),
    ("phase_sat02", sa.String(length=4)),
)
_PHASE_TABLES = ("project_settings", "devices")


def _mevcut_kolonlar(bind, tablo: str = "fault_events") -> set[str]:  # noqa: ANN001
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(tablo)}


def _mevcut_indeksler(bind) -> set[str]:  # noqa: ANN001
    insp = sa.inspect(bind)
    return {i["name"] for i in insp.get_indexes("fault_events")}


def upgrade() -> None:
    bind = op.get_bind()
    # Idempotent: bu migration appliance'ta uvicorn'dan ONCE kosuyor ve
    # yarim kalmis bir yukseltmenin ardindan yeniden calisabilir.
    var = _mevcut_kolonlar(bind)
    for ad, tip in _COLUMNS:
        if ad not in var:
            op.add_column("fault_events", sa.Column(ad, tip, nullable=True))

    var_idx = _mevcut_indeksler(bind)
    for ad, kolonlar in _INDEXES:
        if ad not in var_idx:
            op.create_index(ad, "fault_events", kolonlar)

    # Unite -> faz eslemesi. NULL birakilir: varsayilan
    # (master=a, sat01=b, sat02=c) kodda tanimli. Buraya deger YAZMAK,
    # kurulumcunun onaylamadigi bir esmelemeyi "secilmis" gostermek olurdu.
    for tablo in _PHASE_TABLES:
        mevcut = _mevcut_kolonlar(bind, tablo)
        for ad, tip in _PHASE_COLUMNS:
            if ad not in mevcut:
                op.add_column(tablo, sa.Column(ad, tip, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    var_idx = _mevcut_indeksler(bind)
    for ad, _kolonlar in _INDEXES:
        if ad in var_idx:
            op.drop_index(ad, table_name="fault_events")

    for tablo in _PHASE_TABLES:
        mevcut = _mevcut_kolonlar(bind, tablo)
        for ad, _tip in _PHASE_COLUMNS:
            if ad in mevcut:
                op.drop_column(tablo, ad)

    var = _mevcut_kolonlar(bind)
    for ad, _tip in _COLUMNS:
        if ad in var:
            op.drop_column("fault_events", ad)
