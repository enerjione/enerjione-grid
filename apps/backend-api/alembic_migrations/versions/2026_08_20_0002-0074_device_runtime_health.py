"""device_runtime_health — gateway'in bildirdigi cihaz basina calisma-zamani sagligi.

Sozlesme: `device_health_v1`. (Bu migration yazildiginda sozlesme gateway
PR #33'te acikti; 1.15.0 ile YAYINLANDI ve kanonik artifact
`infra/gateway-contract/v1.15.1.json` olarak vendor edildi.) Vendor kopyasi:
`docs/gateway-contract/device-health-api-pr33.md`.

NEDEN YENI TABLO (alternatifler elendi)
---------------------------------------
* `devices`e kolon: o satir cihaz KAYDIDIR (operatorun girdigi kimlik,
  konum, profil) ve nadiren degisir. Saniyeler icinde degisen bir gozlemi
  oraya koymak, operator verisini tasiyan satiri her saglik partisinde
  yeniden yazmak (Postgres MVCC) demekti.
* `telemetry_latest`: sahibi tag-engine ve icerigi SINYAL degerleri. Bu
  kanal DNP3 sinyali tasimaz; `devices.communication_status` da telemetri
  hattindan turer ve BU KANAL ONU EZMEZ — `smart_idle` SAGLIKLI bir uyku
  halidir, "haberlesme yok" ile ayni kovaya konursa uyuyan filo SCADA'da
  arizali gorunur.
* `gateway_health`: orada gateway BASINA tek satir var; buradaki veri CIHAZ
  basinadir (200+ cihaz) ve zaten ayri bir uca tasindi cunku baslik
  butcesine sigmiyordu.

Cihaz basina TEK satir, upsert. FK YOK — gateway backend'in tanimadigi bir
cihaz kodu bildirebilir ve FK partinin TAMAMINI dusururdu; ortada kalan
satir kalici degildir, sonraki tam snapshot'ta uzlastirilir.

Revision ID: 0074
Revises: 0073
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: Union[str, None] = "0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZATEN VARSA ATLA — 0073 ile ayni gerekce: temiz kurulum ile
    # yukseltilen kurulum ayni yerden gecmiyor (bkz. 0072 legacy sema
    # materyalizasyonu) ve tablo baska bir yoldan olusmus olabilir. Var
    # olani yeniden yaratmaya calismak yukseltmeyi kirardi.
    bind = op.get_bind()
    if sa.inspect(bind).has_table("device_runtime_health"):
        return

    op.create_table(
        "device_runtime_health",
        sa.Column("device_code", sa.String(length=50), nullable=False),
        sa.Column("gateway_code", sa.String(length=50), nullable=False),
        # --- sozlesme bolum 4: cihaz kaydi ---
        # NOT NULL + server_default: kolon modelde de NOT NULL. Iki kurulum
        # yolu oldugu icin varsayilan DB SEVIYESINDE de bulunmali; yalnizca
        # Python `default`u birakmak, ORM disindan gelen bir INSERT'in
        # (restore, elle SQL) bir kurulumda patlayip digerinde calismasi
        # demekti (ayni gerekce 0073'te `gateway_updates.status` icin yazili).
        sa.Column(
            "connection_state",
            sa.String(length=24),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "connected", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "reachable", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("configured_session_policy", sa.String(length=24), nullable=True),
        sa.Column("effective_session_policy", sa.String(length=24), nullable=True),
        sa.Column("operation_mode", sa.String(length=24), nullable=True),
        sa.Column("dial_in_interval_min", sa.Integer(), nullable=True),
        # Epoch alanlari FLOAT ve NULL edilebilir: `null` = "HIC OLMADI".
        # `0` gonderilmez ve 0'a cevrilmez (panelde 1970 tarihi cikmasin).
        sa.Column("next_expected_report_epoch", sa.Float(), nullable=True),
        sa.Column("report_overdue_sec", sa.Float(), nullable=True),
        sa.Column(
            "report_late", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("last_valid_contact_epoch", sa.Float(), nullable=True),
        sa.Column("last_frame_epoch", sa.Float(), nullable=True),
        # Sonda alanlari SALT TESHIS — durum belirlemez.
        sa.Column("ip_probe_status", sa.String(length=24), nullable=True),
        sa.Column("tcp_probe_status", sa.String(length=24), nullable=True),
        sa.Column("last_probe_epoch", sa.Float(), nullable=True),
        sa.Column("ip_endpoint_type", sa.String(length=24), nullable=True),
        # --- sozlesme bolum 6: siralama ve snapshot ---
        # `gateway_instance_id` KALICIDIR (restart'ta degismez) ve
        # SIRALAMAYA GIRMEZ; yalnizca teshis icin saklanir.
        sa.Column("gateway_instance_id", sa.String(length=80), nullable=True),
        sa.Column("boot_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("snapshot_batch_index", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("device_code"),
    )
    op.create_index(
        "ix_device_runtime_health_gateway_code",
        "device_runtime_health",
        ["gateway_code"],
        unique=False,
    )
    # Bayat yazma korumasi her istekte "bu gateway'in en yuksek
    # (boot_id, sequence) ikilisi" sorusunu sorar; bu index onu tek satir
    # okumaya indirir.
    op.create_index(
        "ix_device_runtime_health_kursor",
        "device_runtime_health",
        ["gateway_code", "boot_id", "sequence"],
        unique=False,
    )
    # Uzlastirma: "bu gateway'de su snapshot'i tasimayan satirlar".
    op.create_index(
        "ix_device_runtime_health_snapshot",
        "device_runtime_health",
        ["gateway_code", "snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("device_runtime_health"):
        return
    op.drop_index(
        "ix_device_runtime_health_snapshot", table_name="device_runtime_health"
    )
    op.drop_index(
        "ix_device_runtime_health_kursor", table_name="device_runtime_health"
    )
    op.drop_index(
        "ix_device_runtime_health_gateway_code", table_name="device_runtime_health"
    )
    op.drop_table("device_runtime_health")
