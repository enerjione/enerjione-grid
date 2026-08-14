"""alarm_events — cihaz silinince alarm gecmisi KALIR

Cihaz silindiginde alarm satirlari da siliniyordu
(`device_repository._delete_telemetry_and_alarms_for_device`). Sahada olculdu
(2026-08-12): demo cihazlari 17 kez silinip yeniden olusturulmus ve
`alarm_events` 2 satira dusmustu. Operator bunu "alarm olmamis" diye okuyordu.

Asimetri de buydu: `telemetry_history` silinen cihazin satirlarini KORUYOR
(ayni olcumde ~20 olu device_id duruyordu), alarm korumuyordu. Bir ariza
izleme urununde operasyonel kaydin donanim kaydiyla birlikte yok olmasi,
saklama politikasi degil VERI KAYBIDIR.

Bu migration satirin cihazdan BAGIMSIZ yasayabilmesini saglar:

  * `device_id` NULL olabilir hale gelir — cihaz silinince FK'yi ihlal
    etmeden bosaltilir, satir kalir.
  * `device_code` / `device_name` eklenir: silme ANINDAKI anlik goruntu.
    Cihaz artik yok, adi `devices` tablosundan okunamaz; okunamayan bir ad
    yerine gecmiste "#41" yazmak, kaydi kimin urettigini kaybetmek olurdu.

Ikisi de NULL kalabilir: mevcut satirlarin hepsi (cihazi duran alarmlar) bu
alanlari BOS tutar. Dolu olmasi "cihaz silinmis" demektir — okuma tarafinin
ayrimi budur, ayri bir bayrak eklemeye gerek yok.

GERI ALMA: `downgrade` device_id'yi NOT NULL'a dondururken cihazi silinmis
satirlari SILMEK zorunda — NULL bir degeri NOT NULL kolona sigdirmanin baska
yolu yok. Yani geri alma bu ozelligin korudugu gecmisi atar; bilerek ve
gorunur sekilde yapiliyor.

Revision ID: 0066
Revises: 0065
"""

from alembic import op
import sqlalchemy as sa

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alarm_events", sa.Column("device_code", sa.String(length=64), nullable=True))
    op.add_column("alarm_events", sa.Column("device_name", sa.String(length=120), nullable=True))
    op.alter_column(
        "alarm_events",
        "device_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    # Cihazi silinmis satirlar NOT NULL kolona sigmaz; geri alirken giderler.
    op.execute("DELETE FROM alarm_comments WHERE alarm_event_id IN (SELECT id FROM alarm_events WHERE device_id IS NULL)")
    op.execute("DELETE FROM alarm_events WHERE device_id IS NULL")
    op.alter_column(
        "alarm_events",
        "device_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("alarm_events", "device_name")
    op.drop_column("alarm_events", "device_code")
