"""remaining_deadbands

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-01

KALAN OLU BANTLAR — BIRIM BAZINDA YAPILAMAZ
--------------------------------------------
0033 akim/gerilim/sicakligi birim bazinda ayarlamisti. Kalanlarda bu yol
YANLIS olurdu, cunku ayni birim altinda cok farkli anlamlar var:

    °  ->  latitude_degrees, longitude_degrees   (KONUM — sabit)
           phase_angle, pitch_angle              (OLCUM — dinamik)

Birim bazinda tek bir esik verseydik ya konumu gereksiz sik kaydederdik ya
da acidaki anlamli degisimi kacirirdik. Bu yuzden SINYAL BAZINDA.

DEGERLER VE GEREKCELERI
-----------------------
GPS bilesenleri — cihaz direge monte, YERINDEN OYNAMAZ. Ama "ne zaman
oynadi" sorusu degerli (hirsizlik, direk hasari, yanlis montaj). Bu yuzden
arsivden CIKARMIYORUZ; hareket buyuklugunde bir esik koyuyoruz. Sonuc:
sabit durdugu surece tek satir, gercekten oynarsa kayit.

    latitude/longitude_degrees   0.0001   (~11 m)
    latitude/longitude_minutes   0.01     (~18 m)
    latitude/longitude_seconds   0.5      (~15 m)

RSSI — telsiz sinyal seviyesi dogasi geregi +/-1-2 dBm oynar. O bandin
altindaki degisim GURULTU; kaydetmek arsivi sisirir, hicbir soruya cevap
vermez.

    modem_rssi, rssi_satellite   2.0 dBm

Aci olcumleri — pitch (egim) hirsizlik/direk hasarinda anlamli, phase
elektriksel. Ikisinde de derece altindaki oynama operasyonel karsiligi
olmayan sensor gurultusu.

    pitch_angle, phase_angle     1.0

SIFIR BIRAKILANLAR — BILEREK
----------------------------
`fault_duration` (ms): OLAY VERISI. Her deger ayri bir arizanin suresi;
olu bant koymak ariza KAYBETMEK olurdu. Ardisik iki ariza benzer surede
olabilir ve ikincisi silinirdi.

`test_point_level`: anlami belirsiz. Tahmin edip veri cozunurlugu dusurmek
yerine dokunulmuyor.

`serial_number`, `firmware_version`, `hardware_revision`: 0034 zaten
`historize=false` yapti (statik metadata).

GERI ALINABILIR: downgrade bu sinyallerin olu bandini 0 yapar.
KAPATMAK: tek sinyal icin `historize_deadband = 0`.
"""

from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

#: sinyal son eki -> olu bant (sinyalin kendi biriminde)
_DEADBANDS = {
    # GPS bilesenleri — hareket buyuklugunde esik.
    "latitude_degrees": 0.0001,
    "longitude_degrees": 0.0001,
    "latitude_minutes": 0.01,
    "longitude_minutes": 0.01,
    "latitude_seconds": 0.5,
    "longitude_seconds": 0.5,
    # Telsiz sinyal seviyesi — gurultu bandi.
    "modem_rssi": 2.0,
    "rssi_satellite": 2.0,
    # Aci olcumleri — derece alti sensor gurultusu.
    "pitch_angle": 1.0,
    "phase_angle": 1.0,
}


def upgrade() -> None:
    for sonek, deger in _DEADBANDS.items():
        op.execute(
            sa.text(
                "UPDATE signal_catalog SET historize_deadband = :deger "
                "WHERE key LIKE :desen AND data_type = 'analog' "
                "AND historize IS TRUE AND historize_deadband = 0"
            ).bindparams(deger=deger, desen=f"%.{sonek}")
        )


def downgrade() -> None:
    for sonek in _DEADBANDS:
        op.execute(
            sa.text(
                "UPDATE signal_catalog SET historize_deadband = 0 "
                "WHERE key LIKE :desen"
            ).bindparams(desen=f"%.{sonek}")
        )
