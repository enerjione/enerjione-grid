"""unknown_device_telemetry — bilinmeyen cihaz telemetrisinin dayanikli karantinasi

NEDEN
-----
Consumer bilinmeyen cihaz gelince payload'i ATIYORDU: uyari loglayip
`processed_messages`'a dedup satiri yaziyor ve JetStream mesajini ack
ediyordu. Mesaj bir daha teslim edilmedigi ve dedup satiri olasi bir
yeniden teslimi de yuttugu icin, cihaz birkac dakika sonra sisteme
eklendiginde aradaki olcumler GERI GETIRILEMIYORDU.

Bu tablo o payload'u tutar; cihaz tanimlandiktan sonra replay ayni is
mantigiyla normal telemetri yoluna basar.

IDEMPOTENT OLMAK ZORUNDA
------------------------
Iki kurulum yolu ayni migration'i gorur (bkz. 0070'teki ayni gerekce):
  * yukseltme     -> tablo yok, alembic olusturur
  * temiz/restore -> `create_all` tabloyu MODELDEN zaten olusturur ve
    ardindan alembic zinciri kosar
Ikinci yolda korumasiz `create_table` "already exists" ile duser ve restore
TAMAMLANAMAZ. Bu yuzden tablo ve index'ler varlik kontrolu ile olusturulur.
"""

from alembic import op
import sqlalchemy as sa

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

_TABLO = "unknown_device_telemetry"


def _tablo_var_mi(bind) -> bool:
    return sa.inspect(bind).has_table(_TABLO)


def _index_adlari(bind) -> set[str]:
    if not _tablo_var_mi(bind):
        return set()
    return {i["name"] for i in sa.inspect(bind).get_indexes(_TABLO)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _tablo_var_mi(bind):
        op.create_table(
            _TABLO,
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("consumer_name", sa.String(length=80), nullable=False),
            sa.Column("dedup_key", sa.String(length=200), nullable=False),
            sa.Column("message_id", sa.String(length=120), nullable=False),
            sa.Column("gateway_code", sa.String(length=50), nullable=True),
            sa.Column("device_code", sa.String(length=50), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=True),
            sa.Column("stream_sequence", sa.BigInteger(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("signal_key", sa.String(length=120), nullable=True),
            sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reason", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("seen_count", sa.Integer(), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("replay_attempts", sa.Integer(), nullable=False),
            sa.Column("last_replay_error", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            # Idempotency + es zamanli iki consumer korumasi tek yerden gelir.
            sa.UniqueConstraint(
                "consumer_name",
                "dedup_key",
                name="uq_unknown_telemetry_consumer_dedup",
            ),
        )

    mevcut = _index_adlari(bind)
    if "ix_unknown_device_telemetry_device_code" not in mevcut:
        op.create_index(
            "ix_unknown_device_telemetry_device_code", _TABLO, ["device_code"]
        )
    if "ix_unknown_device_telemetry_status" not in mevcut:
        op.create_index("ix_unknown_device_telemetry_status", _TABLO, ["status"])
    if "ix_unknown_telemetry_replay" not in mevcut:
        op.create_index(
            "ix_unknown_telemetry_replay",
            _TABLO,
            ["status", "device_code", "gateway_code"],
        )
    if "ix_unknown_telemetry_status_first_seen" not in mevcut:
        op.create_index(
            "ix_unknown_telemetry_status_first_seen",
            _TABLO,
            ["status", "first_seen_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _tablo_var_mi(bind):
        return
    mevcut = _index_adlari(bind)
    for ad in (
        "ix_unknown_telemetry_status_first_seen",
        "ix_unknown_telemetry_replay",
        "ix_unknown_device_telemetry_status",
        "ix_unknown_device_telemetry_device_code",
    ):
        if ad in mevcut:
            op.drop_index(ad, table_name=_TABLO)
    op.drop_table(_TABLO)
