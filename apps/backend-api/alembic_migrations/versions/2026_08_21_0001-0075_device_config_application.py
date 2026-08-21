"""device_config_applications — yapilandirma uygulama NIYETI ve durumu.

NEDEN VAR
---------
Horstmann Smart modda modemini BILEREK kapatir; Dial-In araligi 24 saate
kadar cikar. Yapilandirma gonderimi ise komut kuyruguna dayaniyordu ve
komutun tazelik suresi 120 SANIYEDIR. Yani uyuyan bir cihaza yapilan her
yapilandirma gonderimi, cihaz uyanmadan cok once oluyordu; kimse cihaz
uyandiginda ona "yeni dosyani oku" demiyordu.

NEDEN KOMUT TTL'I UZATILMADI
----------------------------
120 saniye bir GUVENLIK INVARYANTIDIR: operatorun saatler onceki karari
sahanin su anki durumu icin gecerli olmayabilir, o yuzden bayat komut
fiziksel sisteme ULASMAZ. Uyuyan cihaz icin o sureyi uzatmak, kesici
komutlarini da kapsayan o invaryanti kaldirmak olurdu.

Bu yuzden KALICI olan sey komut degil NIYETTIR. Cihaz DOGAL OLARAK
uyandiginda backend O AN yeni ve taze (yine 120 sn'lik) bir komut uretir.

NEDEN `device_config_versions`E KOLON DEGIL
-------------------------------------------
O tablo bir BELGEDIR ve append-only'dir: baytlar, kim uretti, ne zaman.
Uygulama ise bir SUREC'tir, saatlerce surer ve DEGISIR. Ayrica ayni surum
birden fazla kez uygulanabilir (basarisiz oldu, cihaz uyandi, tekrar
denendi) — tek bir `applied_at` bunu anlatamaz.

Revision ID: 0075
Revises: 0074
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075"
down_revision: Union[str, None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Kismi unique index'in WHERE kosulu. Model tarafindaki `_ACIK_WHERE` ile
#: BIREBIR AYNI olmali; `tests/test_config_apply_uyanma.py` icindeki parite
#: testi ikisini karsilastirir. Ayrisirsa index yanlis satirlari kapsar ve
#: "cihaz basina tek acik niyet" garantisi sessizce kaybolur.
ACIK_WHERE = "state IN ('cihaz_bekleniyor', 'kuyrukta', 'iletildi')"


def upgrade() -> None:
    op.create_table(
        "device_config_applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("config_version_id", sa.Integer(), nullable=False),
        # `server_default` model ile AYNI olmak ZORUNDA: varsayilan yalnizca
        # Python tarafinda kalirsa ORM disindan gelen bir INSERT (restore,
        # elle SQL) bir kurulumda patlar digerinde calisir.
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
            server_default="cihaz_bekleniyor",
        ),
        sa.Column("requested_by", sa.String(length=150), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ftp_staged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ftp_path", sa.String(length=500), nullable=True),
        sa.Column("ftp_sha256", sa.String(length=64), nullable=False),
        sa.Column("command_id", sa.Integer(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("readback_before", sa.String(length=120), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(length=24), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("last_readiness_reason", sa.String(length=32), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["config_version_id"], ["device_config_versions.id"], ondelete="CASCADE"
        ),
        # Komut silinirse niyet KALIR, yalnizca baglanti kopar: "hangi komut
        # uretilmisti" bilgisi kaybolur ama niyetin kendisi denetim kaydidir.
        sa.ForeignKeyConstraint(
            ["command_id"], ["device_commands.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_device_config_applications_id"),
        "device_config_applications",
        ["id"],
    )
    op.create_index(
        op.f("ix_device_config_applications_device_id"),
        "device_config_applications",
        ["device_id"],
    )
    op.create_index(
        op.f("ix_device_config_applications_config_version_id"),
        "device_config_applications",
        ["config_version_id"],
    )
    op.create_index(
        op.f("ix_device_config_applications_state"),
        "device_config_applications",
        ["state"],
    )
    op.create_index(
        op.f("ix_device_config_applications_requested_at"),
        "device_config_applications",
        ["requested_at"],
    )
    # CIHAZ BASINA EN FAZLA BIR ACIK NIYET.
    #
    # Kismi unique index: yalnizca acik durumlari kapsar, gecmis serbesttir.
    # Bu, exactly-once garantisinin VERITABANI seviyesindeki ayagidir —
    # birden fazla uvicorn worker'i ayri sureclerdir ve uygulama icindeki
    # bir kilit onlari baglamaz.
    op.create_index(
        "uq_device_config_app_acik",
        "device_config_applications",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text(ACIK_WHERE),
        sqlite_where=sa.text(ACIK_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_device_config_app_acik", table_name="device_config_applications")
    op.drop_index(
        op.f("ix_device_config_applications_requested_at"),
        table_name="device_config_applications",
    )
    op.drop_index(
        op.f("ix_device_config_applications_state"),
        table_name="device_config_applications",
    )
    op.drop_index(
        op.f("ix_device_config_applications_config_version_id"),
        table_name="device_config_applications",
    )
    op.drop_index(
        op.f("ix_device_config_applications_device_id"),
        table_name="device_config_applications",
    )
    op.drop_index(
        op.f("ix_device_config_applications_id"),
        table_name="device_config_applications",
    )
    op.drop_table("device_config_applications")
