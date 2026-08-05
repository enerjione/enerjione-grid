"""devices.serial_number — seri numarasinin kalici ve birincil kaynagi.

Config dosya adi (`<seri>_Configuration.csv`) simdiye dek YALNIZCA
telemetriden okunuyordu; cihaz bir an `master.serial_number = 0` gonderince
sistem `0_Configuration.csv` uretti (sahada yasandi). Seri artik cihaz
kaydinda durur: kurulumda girilir, cihaz baglaninca telemetriden otomatik
guncellenir.

Backfill iki adim:
  1. Telemetride GECERLI (sifir olmayan) seri olan cihazlara o deger yazilir.
  2. Hala bos kalan ve kodu SALT RAKAM olan cihazlara kod yazilir — sahada
     operatorler cihaz kodunu seri numarasiyla aciyor (orn. kod "50984").
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices", sa.Column("serial_number", sa.String(length=20), nullable=True)
    )
    op.create_index(
        op.f("ix_devices_serial_number"), "devices", ["serial_number"]
    )

    # 1) Telemetriden gecerli seri (metin ya da sifir olmayan sayi).
    op.execute(
        sa.text(
            """
            UPDATE devices d SET serial_number = sub.seri
            FROM (
                SELECT device_id,
                       COALESCE(
                           NULLIF(TRIM(value_string), ''),
                           CASE WHEN value IS NOT NULL AND value <> 0
                                THEN CAST(CAST(value AS BIGINT) AS TEXT) END
                       ) AS seri
                FROM telemetry_latest
                WHERE signal_key = 'master.serial_number'
            ) sub
            WHERE sub.device_id = d.id
              AND sub.seri IS NOT NULL
              AND TRIM(sub.seri, '0') <> ''
            """
        )
    )
    # 2) Kalanlar: kod salt rakamsa (ve sifir degilse) kod = seri.
    op.execute(
        sa.text(
            r"""
            UPDATE devices SET serial_number = code
            WHERE serial_number IS NULL
              AND code ~ '^[0-9]{1,20}$'
              AND TRIM(code, '0') <> ''
            """
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_devices_serial_number"), table_name="devices")
    op.drop_column("devices", "serial_number")
