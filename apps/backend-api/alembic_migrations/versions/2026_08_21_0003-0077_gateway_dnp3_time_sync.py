"""gateways.dnp3_time_sync — Horstmann icin `nonlan`.

NEDEN
-----
DNP3 outstation saat senkronizasyon proseduru Grid'de UC RENDER NOKTASINDA
sabit `"lan"` yaziliyordu (backend compose sablonu, backend .env sablonu,
appliance compose sablonu). Gateway 1.15.1 lab olcumu (yadnp3 3.2.1.1,
gercek outstation) sunu gosterdi:

    lan     -> FC=24 RECORD_CURRENT_TIME  -> WRITE G50V3
    nonlan  -> FC=23 DELAY_MEASUREMENT    -> WRITE G50V1

Horstmann SN 2.0 / Pole Master profili **FC=23 ve G50V1'i ILAN EDER**;
FC=24 ve G50V3'u **ETMEZ**. Yani `lan` seciliyken gateway, cihazin ilan
ETMEDIGI bir nesneyi yaziyordu: NEED_TIME asserted olsa BILE senkronizasyon
basarisiz oluyor ve saat yanlis kaliyordu. Sahada bir cihazin RTC'si **2066**
yilindaydi.

Gerekce (vendor edilmis): `docs/gateway-contract/horstmann-time-sync-1.15.1.md`

NEDEN VARSAYILAN `nonlan`
-------------------------
Gateway'in KENDI varsayilani `lan` olarak kaldi ve bu dogru: orasi Horstmann
olmayan kurulumlara da hizmet ediyor. Grid ise bir HORSTMANN PLATFORMUDUR —
kayitli cihaz modellerinin hepsi Horstmann (`app/data/device_models.py`:
`horstmann_sn_2_0`, `horstmann_pole_master_kit`, `horstmann_pmk_set`).
Bu yuzden dogru varsayilan BURADA `nonlan`.

MEVCUT SATIRLAR DA `nonlan` OLUR ve bu bilincli: o gateway'ler bugun
hardcoded `lan` aliyor — yani duzeltilmek istenen hatanin ta kendisini
yasiyorlar. `server_default` ile hepsi dogru degere gecer.

DEGER ACIK VE DEGISTIRILEBILIR: Horstmann olmayan bir outstation'a hizmet
eden bir kurulum `lan` (ya da `none`) secebilir. Gecersiz deger uygulama
katmaninda REDDEDILIR (fail-closed, bkz. `gateway_compose._validate`);
gateway tarafi da gecersiz degerde ACILMAZ.

Revision ID: 0077
Revises: 0076
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0077"
down_revision: Union[str, None] = "0076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Model tarafindaki `Gateway.dnp3_time_sync` ile BIREBIR ayni olmali.
VARSAYILAN = "nonlan"


def upgrade() -> None:
    op.add_column(
        "gateways",
        sa.Column(
            "dnp3_time_sync",
            sa.String(length=12),
            nullable=False,
            server_default=VARSAYILAN,
        ),
    )


def downgrade() -> None:
    op.drop_column("gateways", "dnp3_time_sync")
