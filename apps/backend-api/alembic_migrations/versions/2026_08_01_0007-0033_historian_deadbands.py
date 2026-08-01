"""historian_deadbands

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-01

OLU BANT VARSAYILANLARI (operator onayi ile)
---------------------------------------------
0032 mekanizmayi kurdu ama olu bandi herkeste 0 (KAPALI) birakti, cunku
uygun deger sahaya ve birime gore degisir ve bu bir URUN KARARIDIR.

Onaylanan degerler:

    A    -> 0.5      akim
    V    -> 1.0      gerilim
    °C   -> 0.5      sicaklik

BILEREK DOKUNULMAYANLAR
-----------------------
`mA` (18 sinyal — trip_level, min/max/average/fault current,
last_good_known_current) 0 KALIYOR. Onay "akim icin 0.5 A" idi; bu birime
ceviri 500 mA eder ve o, bu sinyaller icin cok buyuk bir esik olabilir.
Yanlis bir olu bant, ariza oncesindeki akim egrisini KALICI olarak siler.
Bu grup ayrica ariza analizinin merkezinde. Deger operator tarafindan
ayrica belirlenmeli.

`°`, `dBm`, `ms`, `min`, `sec` ve birimsizler de 0 kaliyor — konusulmadi.

STATIK METADATA `analog` OLARAK TIPLENMIS
------------------------------------------
Katalogda `serial_number`, `firmware_version` ve `hardware_revision`
sinyalleri `data_type='analog'` (birimsiz). 0032 yalnizca `string` ve
`binary_output` tiplerini kapatmisti, dolayisiyla bunlar arsivlenmeye devam
ediyordu. Oysa 0032'nin gerekcesi aynen gecerli: bunlar cihazin omru boyunca
sabit ve zaman serileri hicbir soruya cevap vermiyor. `historize=false`
yapiliyor.

GERI ALINABILIR: downgrade tum degerleri 0 / true yapar.

KAPATMAK: tek bir sinyal icin `historize_deadband = 0`; tumu icin
`UPDATE signal_catalog SET historize_deadband = 0`.
"""

from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

#: birim -> olu bant (sinyalin kendi biriminde)
_DEADBANDS = {
    "A": 0.5,
    "V": 1.0,
    "°C": 0.5,
}

#: `analog` tiplenmis ama aslinda statik metadata olan sinyal son ekleri.
#: Anahtar "master."/"sat01."/"sat02." onekiyle geldigi icin son eke bakiyoruz.
_STATIK_ANALOG_SONEKLERI = (
    "serial_number",
    "firmware_version",
    "hardware_revision",
)


def upgrade() -> None:
    for birim, deger in _DEADBANDS.items():
        op.execute(
            sa.text(
                "UPDATE signal_catalog SET historize_deadband = :deger "
                "WHERE unit = :birim AND data_type = 'analog' AND historize IS TRUE"
            ).bindparams(deger=deger, birim=birim)
        )

    # Statik metadata — arsivden cikariliyor.
    for sonek in _STATIK_ANALOG_SONEKLERI:
        op.execute(
            sa.text(
                "UPDATE signal_catalog SET historize = false "
                "WHERE key LIKE :desen"
            ).bindparams(desen=f"%.{sonek}")
        )


def downgrade() -> None:
    op.execute("UPDATE signal_catalog SET historize_deadband = 0")
    for sonek in _STATIK_ANALOG_SONEKLERI:
        op.execute(
            sa.text(
                "UPDATE signal_catalog SET historize = true WHERE key LIKE :desen"
            ).bindparams(desen=f"%.{sonek}")
        )
