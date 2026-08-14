"""ws_tickets — WS bileti surecler arasinda paylasilir

Bilet `/auth/ws-ticket` ile uretilip AYRI bir TCP baglantisi olan WS upgrade
istegiyle tuketiliyor. `E1_API_WORKERS>1` iken bu iki istek farkli uvicorn
sureclerine dusebiliyordu; bilet surec-ici bir dict'te tutuldugu icin ikinci
surec onu bulamiyor ve baglanti `1008 invalid_credentials` ile kapaniyordu.

Istemcideki token fallback'i yalnizca bilet ALINAMAZSA devreye giriyor
(`useLiveValuesSocket.ts`), 1008 kapanisi o dala girmiyor. Sonuc backoff'lu
yeniden baglanma dongusuydu: her denemede yeni bilet, yine rastgele surec —
4 surecte deneme basina ~%25 tutma olasiligi, yani canli degerler ve harita
kalici olarak kararsiz.

`E1_API_WORKERS` varsayilani 1 oldugu icin bugun sahada gorunmuyor; ama
`.env.example` olceklenme icin acikca 4 oneriyor.

TEK KULLANIM KORUNUYOR: tuketim `DELETE ... rowcount` ile yapiliyor, ayni
bileti es zamanli tuketmeye calisan iki surecten yalnizca biri kazaniyor.

Tablo kendi kendini temizler (bilet uretiminde suresi dolmuslar silinir);
omur 30 sn oldugu icin ayri bir retention isi gerekmez.

Revision ID: 0067
Revises: 0066
"""

from alembic import op
import sqlalchemy as sa

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ws_tickets",
        sa.Column("ticket", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=150), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ticket"),
    )
    op.create_index("ix_ws_tickets_expires_at", "ws_tickets", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_ws_tickets_expires_at", table_name="ws_tickets")
    op.drop_table("ws_tickets")
