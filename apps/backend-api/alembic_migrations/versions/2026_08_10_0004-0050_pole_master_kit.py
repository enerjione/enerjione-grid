"""pole_master_kit

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-10 00:00:04.000000

Horstmann Pole Master Kit destegi: tek fiziksel cihaz, uc sanal set.

NE DEGISIYOR
------------
1. `devices.parent_device_id` + `devices.subunit_index`
   Kitin her seti (Satellite 01-03 / 04-06 / 07-09) AYRI bir `devices`
   satiridir. Neden ayri satir: `line_segments.device_id` TEKILDIR — bir
   Device satiri hattin yalnizca TEK noktasina oturabilir. Uc seti tek
   satirda tutup uc ayri direk araligina koymak sema acisindan imkansiz.
   Ustelik ariza motoru, IEC 104 Common Address'i, telemetri anahtari ve
   operator kapsam filtresi bastan sona `device_id` uzerinden calisir; ayri
   satir olunca bu zincirin tamami degismeden dogru calisir.

2. `devices.phase_sat03` ve `project_settings.phase_sat03`
   SN2'de olcum yapan uc uniteden biri ANA unitedir (`master`); Pole Master
   Kit setinde ucu de uydudur (`sat01/sat02/sat03`) — kitin master'i ortak
   RTU'dur, bir faza kelepcelenmez. Ucuncu alan olmasaydi setin ucuncu
   uydusunun gordugu ariza HICBIR faz uretmez, `fault_events.phase` sessizce
   NULL kalir ve tek-faz/uc-faz ayrimi ile faz dagilimi raporu o arizalari
   hic saymazdi. Hicbir hata da olusmazdi.

3. `signal_catalog` anahtar tekilligi GLOBAL -> (model, key)
   `key` global tekildi. Tek modelli bir sistemde zararsiz gorunuyordu ama
   coklu modelde YANLIS bir kisit: her Horstmann modelinin bir `master`
   unitesi ve `master.actual_current` gibi ayni ADI tasiyan ama BASKA DNP3
   adresine oturan bir sinyali var. Global tekillik ikinci modelin bu
   sinyali tanimlamasini imkansiz kiliyor, tek cikis yolu olarak
   `pmk_master.actual_current` gibi uydurma onekler dayatiyordu — ki bu da
   sistemin her yerinde `signal_key.split(".", 1)[0]` ile KAYNAK cikaran
   mantigi (ariza fazi, alarm etiketi, MQTT topic'i, IEC104 kaynak alani)
   sessizce bozardi.

4. `alarm_rules.device_model_filter`
   Sinyaller modeller arasinda ORTAK DEGIL: Pole Master Kit'te
   `master.solar_power` / `master.ac_power` var ama SN2'de yok; SN2'de
   `master.nominal_voltage` ve GPS alanlari var ama kitte yok. Ortak
   adlarda ise esikler modele gore farklilasabilir. Bos = tum modeller
   (mevcut kurallarin davranisi AYNEN korunur).

GERIYE DONUK VERI
-----------------
Tum yeni kolonlar NULLABLE ve varsayilansiz; mevcut cihazlar fiziksel
(`parent_device_id IS NULL`) kalir ve hicbir davranisi degismez.
Tekillik degisimi mevcut satirlari etkilemez: bugun katalogtaki her `key`
zaten tekil oldugu icin `(model, key)` de tekildir.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


#: SQLAlchemy `unique=True, index=True` ciftinden uretilen indeks adi.
_ESKI_KEY_INDEX = "ix_signal_catalog_key"
_YENI_KEY_INDEX = "ix_signal_catalog_key"
_MODEL_KEY_UNIQUE = "uq_signal_catalog_model_key"


def _kolonlar(bind, tablo: str) -> set[str]:  # noqa: ANN001
    return {c["name"] for c in sa.inspect(bind).get_columns(tablo)}


def _indeksler(bind, tablo: str) -> dict[str, bool]:  # noqa: ANN001
    return {i["name"]: bool(i.get("unique")) for i in sa.inspect(bind).get_indexes(tablo)}


def _kisitlar(bind, tablo: str) -> set[str]:  # noqa: ANN001
    insp = sa.inspect(bind)
    adlar = {c["name"] for c in insp.get_unique_constraints(tablo) if c.get("name")}
    return adlar


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Kit / set bagi ------------------------------------------------
    mevcut = _kolonlar(bind, "devices")
    if "parent_device_id" not in mevcut:
        op.add_column("devices", sa.Column("parent_device_id", sa.Integer(), nullable=True))
        op.create_index("ix_devices_parent_device_id", "devices", ["parent_device_id"])
        op.create_foreign_key(
            "fk_devices_parent_device_id",
            "devices",
            "devices",
            ["parent_device_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if "subunit_index" not in mevcut:
        op.add_column("devices", sa.Column("subunit_index", sa.Integer(), nullable=True))

    # --- 2. Ucuncu uydunun faz alani -------------------------------------
    for tablo in ("devices", "project_settings"):
        if "phase_sat03" not in _kolonlar(bind, tablo):
            op.add_column(tablo, sa.Column("phase_sat03", sa.String(length=4), nullable=True))

    # --- 3. Sinyal anahtari tekilligi -> (model, key) ---------------------
    idx = _indeksler(bind, "signal_catalog")
    # Once YENI kisiti kur, sonra eskiyi dusur: ters sirada, aradaki pencerede
    # hicbir tekillik garantisi kalmazdi.
    if _MODEL_KEY_UNIQUE not in _kisitlar(bind, "signal_catalog") and _MODEL_KEY_UNIQUE not in idx:
        op.create_unique_constraint(_MODEL_KEY_UNIQUE, "signal_catalog", ["model", "key"])
    if idx.get(_ESKI_KEY_INDEX) is True:
        # Eski indeks UNIQUE idi; arama icin gereken indeksi kaybetmemek adina
        # yerine tekil OLMAYAN ayni adli bir indeks konur.
        op.drop_index(_ESKI_KEY_INDEX, table_name="signal_catalog")
    if _YENI_KEY_INDEX not in _indeksler(bind, "signal_catalog"):
        op.create_index(_YENI_KEY_INDEX, "signal_catalog", ["key"])

    # --- 4. Alarm kurali model kapsami ------------------------------------
    if "device_model_filter" not in _kolonlar(bind, "alarm_rules"):
        op.add_column(
            "alarm_rules",
            sa.Column("device_model_filter", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if "device_model_filter" in _kolonlar(bind, "alarm_rules"):
        op.drop_column("alarm_rules", "device_model_filter")

    idx = _indeksler(bind, "signal_catalog")
    if _YENI_KEY_INDEX in idx and idx.get(_YENI_KEY_INDEX) is False:
        op.drop_index(_YENI_KEY_INDEX, table_name="signal_catalog")
    if _MODEL_KEY_UNIQUE in _kisitlar(bind, "signal_catalog"):
        op.drop_constraint(_MODEL_KEY_UNIQUE, "signal_catalog", type_="unique")
    if _ESKI_KEY_INDEX not in _indeksler(bind, "signal_catalog"):
        # NOT: ikinci bir modelin sinyalleri girilmisse bu indeks KURULAMAZ
        # (ayni `key` iki modelde de var). Geri alma o durumda once yeni
        # modelin satirlarinin silinmesini gerektirir — bilincli olarak
        # sessizce yutmuyoruz.
        op.create_index(_ESKI_KEY_INDEX, "signal_catalog", ["key"], unique=True)

    for tablo in ("devices", "project_settings"):
        if "phase_sat03" in _kolonlar(bind, tablo):
            op.drop_column(tablo, "phase_sat03")

    mevcut = _kolonlar(bind, "devices")
    if "subunit_index" in mevcut:
        op.drop_column("devices", "subunit_index")
    if "parent_device_id" in mevcut:
        op.drop_constraint("fk_devices_parent_device_id", "devices", type_="foreignkey")
        op.drop_index("ix_devices_parent_device_id", table_name="devices")
        op.drop_column("devices", "parent_device_id")
