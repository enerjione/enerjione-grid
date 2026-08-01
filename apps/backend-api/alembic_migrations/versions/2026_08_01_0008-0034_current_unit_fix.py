"""current_unit_fix

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-01

AKIM SINYALLERININ BIRIM/OLCEK TUTARSIZLIGI
--------------------------------------------
Katalogda AYNI fiziksel buyukluk IKI FARKLI sekilde tanimliydi:

    actual_current            unit=A   scale=0.001   (index 4)
    trip_level                unit=mA  scale=1.0     (index 16)
    minimum_current           unit=mA  scale=1.0     (index 20)
    maximum_current           unit=mA  scale=1.0     (index 23)
    average_current           unit=mA  scale=1.0     (index 26)
    fault_current             unit=mA  scale=1.0     (index 29)
    last_good_known_current   unit=mA  scale=1.0     (index 35)

Cihaz ham veriyi mA olarak gonderiyor (operator dogruladi); `actual_current`
bunu 0.001 ile ampere ceviriyor, DIGER ALTISINDA donusum unutulmus. Katalog
uc kaynak icin de ayni oldugundan toplam 18 sinyal etkileniyor.

SONUCLARI (duzeltilmeden once)
  * Ayni cihazda `actual_current` 0.37 A, `average_current` 608 gorunuyordu —
    ayni buyukluk, 1000 kat farkli sunum.
  * Bu sinyallere kurulan alarm esikleri `actual_current`e kurulanlarla
    KIYASLANAMAZ durumdaydi.
  * IEC 104 / Modbus cikislarina da tutarsiz olcekle gidiyordu.

NE YAPIYOR
----------
18 sinyalin `unit`ini 'A', `scale`ini 0.001 yapiyor. Olcek gateway'e config
ile gidiyor (bkz. api/gateways.py), yani BUNDAN SONRAKI okumalar ampere
cevrilmis gelir.

ARSIV SUREKSIZLIGI — BILINCLI OLARAK KABUL EDILDI
--------------------------------------------------
`telemetry_history` icindeki ESKI satirlar eski olcekte (mA) kaliyor; yeni
satirlar A cinsinden gelecek. Yani bu alti sinyalin grafiginde migration
anina denk gelen bir BASAMAK gorunur.

Eski satirlari 0.001 ile carpmak da MUMKUNDU ama YAPILMADI:
  * Hangi satirin hangi olcekle yazildigini ayirt eden bir isaret yok;
    migration iki kez calisirsa (ya da kismen uygulanmis bir kurulumda)
    veriyi ikinci kez bolerdi ve bu GERI DONULEMEZ olurdu.
  * Sistem su anda saha oncesi test asamasinda ve arsivdeki veri
    simulatorden geliyor; korunacak gercek olcum yok.

Gercek saha verisi biriktikten SONRA ayni duzeltme gerekseydi, dogru yol
`telemetry_history`ye bir olcek-surumu kolonu eklemek olurdu.

GERI ALINABILIR: downgrade eski birim/olcegi geri koyar.
"""

from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

#: Etkilenen sinyal son ekleri (master/sat01/sat02 icin ayni).
_AKIM_SONEKLERI = (
    "trip_level",
    "minimum_current",
    "maximum_current",
    "average_current",
    "fault_current",
    "last_good_known_current",
)


def upgrade() -> None:
    for sonek in _AKIM_SONEKLERI:
        op.execute(
            sa.text(
                "UPDATE signal_catalog SET unit = 'A', scale = 0.001 "
                "WHERE key LIKE :desen AND unit = 'mA'"
            ).bindparams(desen=f"%.{sonek}")
        )

    # Artik ampere cinsinden olduklari icin 0033'un akim olu bandi (0.5 A)
    # bunlara da uygulanabilir. 0033 yalnizca `unit='A'` olanlara vermisti;
    # bu sinyaller o an 'mA' oldugu icin disarida kalmislardi.
    for sonek in _AKIM_SONEKLERI:
        op.execute(
            sa.text(
                "UPDATE signal_catalog SET historize_deadband = 0.5 "
                "WHERE key LIKE :desen AND unit = 'A' AND historize IS TRUE"
            ).bindparams(desen=f"%.{sonek}")
        )


def downgrade() -> None:
    for sonek in _AKIM_SONEKLERI:
        op.execute(
            sa.text(
                "UPDATE signal_catalog SET unit = 'mA', scale = 1.0, "
                "historize_deadband = 0 WHERE key LIKE :desen AND unit = 'A'"
            ).bindparams(desen=f"%.{sonek}")
        )
