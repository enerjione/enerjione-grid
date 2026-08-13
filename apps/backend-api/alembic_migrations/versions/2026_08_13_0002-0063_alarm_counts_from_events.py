"""alarm_daily_counts — gunluk sayaci OLAY KAYDINDAN geri doldur

0061 sayaci kurup `alarm_events` uzerinden geri doldurmustu. Ama o tablo o
gune kadar bir DURUM tablosuydu: tekrar tetikleyen alarm oncekinin satirini
siliyor, onaylanmis alarm normale donunce satir tamamen yok oluyordu. Geri
doldurma, elinde ZATEN SILINMIS bir gecmis buldu — Ariza Analizi'ndeki
"Alarm Sikligi" takvimi butun donemi "hic alarm gelmemis" gibi cizdi.

Gercek gecmis `system_events` tablosunda duruyor: alarm ureten her iki yol da
satir eklerken bir olay yaziyor (`alarm_triggered` / `alarm_created`) ve olay
kaydi hicbir zaman durum tutmadi, retention penceresi (varsayilan 730 gun)
boyunca satirlari duruyor.

Bu migration o gecmisi sayaca isler. Kural `max(sayac, olay_kaydi)`:
sayacin dogru saydigi gun bozulmaz, sayacin hic gormedigi gun gerceklesir.
Tekrar calistirilabilir. Ayni onarim bundan sonra bakim dongusunde de
kosuyor (bkz. `alarm_counter_service.arsivden_onar`), boylece sayac arsiv
budanmadan once tamamlanir.

Revision ID: 0063
Revises: 0062
"""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    baglanti = op.get_bind()
    lehce = baglanti.dialect.name
    if lehce == "postgresql":
        gun = "(e.created_at AT TIME ZONE 'UTC')::date"
        birlestir = "GREATEST(alarm_daily_counts.count, EXCLUDED.count)"
    else:
        gun = "date(e.created_at)"
        birlestir = "MAX(alarm_daily_counts.count, excluded.count)"

    baglanti.exec_driver_sql(
        f"""
        INSERT INTO alarm_daily_counts (day, device_id, count)
        SELECT {gun} AS day, d.id, COUNT(*)
        FROM system_events AS e
        JOIN devices AS d ON d.code = e.device_code
        WHERE e.category = 'alarm'
          AND e.event_type IN ('alarm_triggered', 'alarm_created')
        GROUP BY {gun}, d.id
        ON CONFLICT (day, device_id)
        DO UPDATE SET count = {birlestir}
        """
    )


def downgrade() -> None:
    # GERI ALINAMAZ — ve alinmamali. Bu migration tablo/kolon yaratmiyor,
    # yalnizca eksik kalmis gecmisi dolduruyor. "Geri almak" ancak dogru
    # sayilari silmek olurdu; hangi satirin buradan geldigini de ayirt
    # edemeyiz (mevcut sayacla birlestiriliyorlar).
    pass
