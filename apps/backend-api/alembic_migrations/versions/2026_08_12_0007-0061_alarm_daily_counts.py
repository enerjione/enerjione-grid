"""alarm_daily_counts — gunluk alarm tetiklenme sayaci

"Bugun kac kez alarm tetiklendi" sorusunun cevabi bir SAYIDIR; alarm
satirlarini saymak degildir. Satir bir DURUM kaydi (acilir, onaylanir,
normale doner, arsivlenir); tetiklenme ise degismez bir OLAY. Analiz
ekranindaki takvim ve cihaz x zaman kesitleri artik bu sayaci okuyor.

GERI DOLDURMA: elde kalan `alarm_events` satirlarindan (gun, cihaz) bazinda
sayim uretilir. Gecmiste SILINMIS alarmlar geri gelmez — sayac o gunler icin
elde kalanla baslar, bu surumden sonra tam sayar.

Revision ID: 0061
Revises: 0060
"""

from alembic import op
import sqlalchemy as sa

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alarm_daily_counts",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("day", "device_id"),
    )
    # Takvim gune gore toplar (cihazdan bagimsiz); PK'nin ilk kolonu `day`
    # oldugu icin o sorgu zaten indeksli. Matris ise cihaz bazinda okur.
    op.create_index(
        "ix_alarm_daily_counts_device_id", "alarm_daily_counts", ["device_id"]
    )

    # --- GERI DOLDURMA ---------------------------------------------------
    # Lehceye gore gun ifadesi: Postgres `date()`, SQLite `date()` — ikisi de
    # ayni adi tasiyor, ama SQLite'ta `created_at` metin olarak durabilir.
    baglanti = op.get_bind()
    lehce = baglanti.dialect.name
    if lehce == "postgresql":
        gun = "(created_at AT TIME ZONE 'UTC')::date"
    else:
        gun = "date(created_at)"
    baglanti.exec_driver_sql(
        f"""
        INSERT INTO alarm_daily_counts (day, device_id, count)
        SELECT {gun} AS day, device_id, COUNT(*)
        FROM alarm_events
        WHERE device_id IS NOT NULL
        GROUP BY {gun}, device_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_alarm_daily_counts_device_id", table_name="alarm_daily_counts")
    op.drop_table("alarm_daily_counts")
