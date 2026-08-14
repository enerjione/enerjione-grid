"""infra_notification_state — kritik altyapi bildirimlerinin soguma defteri

Kritik altyapi olaylari (`system_events`) eskiden YALNIZCA tabloya
yaziliyordu; bildirim zinciri sadece cihaz alarmlari icin isliyordu. Yani
"telemetri tamponu tasti, olcum kayboldu" ya da "disk kritik" gibi olaylar,
birinin arayuze girip Olaylar ekranina bakmasina bagliydi. Basinda kimsenin
olmadigi bir saha IPC'sinde bu, en pahali ariza sinifinin (ayakta ama veri
yazmiyor) gunlerce fark edilmemesi demektir.

BU TABLO NE ISE YARAR
Bildirimi acmak tek basina yetmiyordu: disk_guard 5 dakikada bir, telemetri
tasmasi 15 saniyede bir olay uretebiliyor. Her biri icin SMS gitmesi gercek
uyarilari kullanilamaz hale getirirdi. Tablo "(olay tipi + kaynak) icin en
son ne zaman bildirim gitti" sorusunu KALICI olarak cevaplar.

NEDEN BELLEKTE DEGIL
Surec icinde tutulsaydi backend her yeniden baslatildiginda soguma
sifirlanirdi. Kalici bir disk arizasinda backend'in birkac kez yeniden
baslamasi olagandir; her acilista yeni bir SMS tam da onlemek istedigimiz
sey.

`resource_key` NULL DEGIL BOS STRING: Postgres'te NULL'lar birbirine esit
sayilmaz, dolayisiyla UNIQUE kisiti NULL'li satirlarda sessizce islevini
kaybeder ve ayni olay icin sinirsiz satir olusurdu.

DENETIM KAYDI ETKILENMEZ: bastirilan yalnizca GONDERIM'dir; `system_events`
satirlarinin hicbiri atlanmaz. `suppressed_count` o pencerede kac olayin tek
bildirime katlandigini sayar ve operatore metinde gosterilir.

Revision ID: 0068
Revises: 0067
"""

from alembic import op
import sqlalchemy as sa

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # VARLIK KONTROLU — savunma derinligi.
    #
    # Senaryo: eski bir yedek (alembic_version=0067) yeni bir kuruluma
    # yuklenirse `pg_restore --clean` YALNIZCA arsivdeki objeleri dusurur;
    # bu tablo arsivde olmadigi icin diskte KALIR ama surum 0067'ye doner.
    # Sonraki acilista bu migration yeniden oynatilir ve kosulsuz bir
    # `create_table` "DuplicateTable" ile duser. Migration konteyner
    # CMD'sinde uvicorn'dan ONCE kostugu icin sonuc KALICI CRASH-LOOP olur.
    #
    # Guvenli restore akisi (services/safe_restore.py) bu durumu yapisal
    # olarak imkansiz kilar: staging DB `TEMPLATE template0` ile BOS
    # yaratilir, dolayisiyla artik tablo hic olusmaz. Buradaki kontrol yine
    # de duruyor cunku migration'lar restore disi yollardan da oynatilabilir
    # ve bir crash-loop'un bedeli bu uc satirdan cok daha yuksektir.
    if sa.inspect(op.get_bind()).has_table("infra_notification_state"):
        return
    op.create_table(
        "infra_notification_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column(
            "resource_key",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "last_notified_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("last_event_id", sa.Integer(), nullable=True),
        sa.Column(
            "suppressed_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_type", "resource_key", name="uq_infra_notification_key"
        ),
    )
    op.create_index(
        "ix_infra_notification_state_event_type",
        "infra_notification_state",
        ["event_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_infra_notification_state_event_type",
        table_name="infra_notification_state",
    )
    op.drop_table("infra_notification_state")
