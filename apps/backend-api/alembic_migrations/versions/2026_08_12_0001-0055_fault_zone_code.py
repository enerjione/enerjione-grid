"""fault_zone_code

Revision ID: 0055
Revises: 0054
Create Date: 2026-08-12 00:00:01.000000

`fault_events.zone_code` — arizanin kimligi HAT degil, IKI CIHAZ ARASI ARALIK.

NE EKSIKTI
----------
Kayit "su hattin arizasi" olarak tutuluyordu; aralik bilgisi uc ayri kolonda
(line_id + last_red_device_id + first_green_device_id) dagilmisti. Iki somut
sonucu vardi:

  1. AYNI HATTA BASKA BIR ARALIKTA olusan ariza, mevcut kaydin uzerine
     yaziliyordu (eslestirme direk araligi KESISIMINE bakiyordu ve komsu
     araliklar bir direk paylasir). Operator icin ariza "yeni bir ariza"
     olarak degil, eskisinin yer degistirmesi olarak gorunuyordu.
  2. "En cok ariza cikaran ARALIK hangisi" sorusu tek bir GROUP BY ile
     sorulamiyordu; oysa bakim onceliklendirmesi tam olarak bu duzeyde
     yapilir.

KOD BICIMI
----------
    L{line_id}/D{last_red_device_id}>D{first_green_device_id}
    L{line_id}/D{last_red_device_id}>END        (ilerisinde cihaz yok)

ARAYA CIHAZ GIRERSE KOD DEGISIR — ve bu dogrudur: yeni cihaz araligin bir ucu
olur, yani artik BASKA bir araliktan bahsediyoruzdur. Gecmis kayitlar ESKI
kodda kalir; gecmisi yeni topolojiye gore yeniden yazmak, o gun sahada
gidilen yeri degistirmek olurdu.

GERIYE DONUK VERI
-----------------
Mevcut kayitlar icin kod BURADA doldurulur (uc kolondan turetilebiliyor,
uydurma yok). Kolon yine de NULLABLE: cihaz kaydi silinmis eski arizalarda
(`first_green_device_id` SET NULL) kod "END" ile dolar, `last_red_device_id`
NULL olamaz — yani pratikte tum satirlar dolar.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

_TABLO = "fault_events"
_KOLON = "zone_code"
_INDEKS = "ix_fault_events_zone_code"


def _kolonlar(bind) -> set[str]:  # noqa: ANN001
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLO)}


def _indeksler(bind) -> set[str]:  # noqa: ANN001
    return {i["name"] for i in sa.inspect(bind).get_indexes(_TABLO)}


def upgrade() -> None:
    bind = op.get_bind()
    if _KOLON not in _kolonlar(bind):
        op.add_column(_TABLO, sa.Column(_KOLON, sa.String(length=64), nullable=True))
    if _INDEKS not in _indeksler(bind):
        op.create_index(_INDEKS, _TABLO, [_KOLON])
    # Mevcut kayitlari doldur — `fault_recompute_service.zone_code` ile AYNI
    # bicim. Ifade iki lehcede de ayni: string birlestirme + NULL kolu.
    op.execute(
        sa.text(
            f"""
            UPDATE {_TABLO}
               SET {_KOLON} = 'L' || line_id || '/D' || last_red_device_id || '>' ||
                   CASE WHEN first_green_device_id IS NULL
                        THEN 'END'
                        ELSE 'D' || first_green_device_id
                   END
             WHERE {_KOLON} IS NULL
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _INDEKS in _indeksler(bind):
        op.drop_index(_INDEKS, table_name=_TABLO)
    if _KOLON in _kolonlar(bind):
        op.drop_column(_TABLO, _KOLON)
