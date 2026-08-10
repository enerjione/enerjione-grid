"""Cihaz silme iki faza ayrildi: kuyruk tablosu + telemetry_history FK'si duser.

SORUN
-----
500 cihazlik kurulumda bir cihazi silmek dakikalarca suruyor, arayuz zaman
asimina ugruyordu — cihaz pratikte SILINEMIYORDU. Sebep, tek transaction
icinde `telemetry_history`'den o cihaza ait tum satirlarin silinmesi: 90
gunluk pencerede cihaz basina ~4M satir, ~90 hypertable chunk'ina yayilmis.

COZUM
-----
1. `device_purge_jobs`: cihaz satiri silindikten SONRA arka planda
   temizlenecek arsiv verisinin kuyrugu. Retention worker partili siler.
2. `telemetry_history.device_id` uzerindeki FOREIGN KEY DUSER. Cihaz satiri
   arsiv satirlari dururken silinebilsin diye:
     * FK dursaydi silme bloke olurdu,
     * ON DELETE CASCADE yapmak ayni yavas silmeyi Postgres'e yaptirirdi
       (tek transaction, ayni kilit ve WAL maliyeti).
   Sahipsiz kalan satirlar zararsizdir: tum okuma yollari `devices`
   uzerinden gider, bu satirlar hicbir ekranda gorunmez ve kuyruk neyin
   silinecegini acikca tutar.

`telemetry` (30 dk pencere) ve `telemetry_latest` / `device_config`
(ON DELETE CASCADE, cihaz basina kucuk) DOKUNULMADI — onlar senkron
silinmeye devam eder.

Revision ID: 0046
Revises: 0045
"""

from alembic import op
import sqlalchemy as sa

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

_TABLO = "telemetry_history"
_KOLON = "device_id"


def _fk_adlari(baglanti, tablo: str, kolon: str) -> list[str]:
    """`tablo.kolon` uzerindeki FK kisitlarinin adlari.

    Ad SABIT DEGIL: kisit farkli surumlerde farkli adlarla (autogenerate ya
    da elle) olusmus olabilir. Sabit bir ad varsaymak yerine katalogdan
    okuyoruz — yanlis ada `drop_constraint` demek migration'i patlatirdi.
    """
    inspector = sa.inspect(baglanti)
    adlar = []
    for fk in inspector.get_foreign_keys(tablo):
        if kolon in (fk.get("constrained_columns") or []) and fk.get("name"):
            adlar.append(fk["name"])
    return adlar


def upgrade() -> None:
    op.create_table(
        "device_purge_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        # FK YOK: is olusturuldugunda cihaz satiri zaten silinmis oluyor.
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("device_code", sa.String(50), nullable=False),
        sa.Column("device_name", sa.String(255), nullable=True),
        sa.Column("requested_by", sa.String(120), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("rows_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_purge_jobs_device_id", "device_purge_jobs", ["device_id"])
    op.create_index("ix_device_purge_jobs_state", "device_purge_jobs", ["state"])
    op.create_index(
        "ix_device_purge_jobs_requested_at", "device_purge_jobs", ["requested_at"]
    )

    baglanti = op.get_bind()
    # SQLite (testler) ALTER ile FK dusurmeyi desteklemez; zaten orada FK
    # zorlamasi varsayilan KAPALI, dolayisiyla yapacak bir sey yok.
    if baglanti.dialect.name == "sqlite":
        return
    for ad in _fk_adlari(baglanti, _TABLO, _KOLON):
        op.drop_constraint(ad, _TABLO, type_="foreignkey")


def downgrade() -> None:
    baglanti = op.get_bind()
    if baglanti.dialect.name != "sqlite":
        # Geri alirken FK'yi yeniden kur. ONCE sahipsiz satirlari temizle;
        # aksi halde kisit dogrulamasi patlar.
        baglanti.execute(
            sa.text(
                "DELETE FROM telemetry_history th "
                "WHERE NOT EXISTS (SELECT 1 FROM devices d WHERE d.id = th.device_id)"
            )
        )
        op.create_foreign_key(
            "fk_telemetry_history_device_id_devices",
            _TABLO,
            "devices",
            [_KOLON],
            ["id"],
        )
    op.drop_index("ix_device_purge_jobs_requested_at", table_name="device_purge_jobs")
    op.drop_index("ix_device_purge_jobs_state", table_name="device_purge_jobs")
    op.drop_index("ix_device_purge_jobs_device_id", table_name="device_purge_jobs")
    op.drop_table("device_purge_jobs")
