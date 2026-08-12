"""alarm_events.superseded_at — alarm tarihcesi artik SILINMIYOR

Eskiden iki yerde satir siliniyordu:
  1) Ayni sinyal tekrar tetikleyince "Onay Bekliyor"daki onceki kayit,
  2) ONAYLANMIS bir alarm normale donunce kaydin kendisi.

Ikisi de bir GORUNUM sorununu veri silerek cozuyordu; "gecen ay hangi gun
kac alarm geldi" sorusunun cevabi kalmiyordu. Artik satir duruyor, yalnizca
`superseded_at` damgasi yiyor: canli paneller bunu suzer, analiz katmani
(alarm takvimi, cihaz x zaman matrisi) HIC bakmaz.

Revision ID: 0059
Revises: 0058
"""

from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alarm_events",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Canli panel sorgusu `superseded_at IS NULL` suzuyor; tablo buyudukce
    # (artik tarihce birikiyor) bu suzgec indekssiz kalmamali.
    op.create_index(
        "ix_alarm_events_superseded_at", "alarm_events", ["superseded_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_alarm_events_superseded_at", table_name="alarm_events")
    op.drop_column("alarm_events", "superseded_at")
