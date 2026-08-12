"""fault_assign_to_team

Revision ID: 0059
Revises: 0058
Create Date: 2026-08-12 00:00:05.000000

`fault_events.assigned_to_area_id` — ariza bir KISIYE ya da bir EKIBE atanabilir.

NE EKSIKTI
----------
Atama yalnizca kullaniciyaydi (`assigned_to_username`). Gece vardiyasinda ya da
nobet devrinde isi USTLENECEK kisi belli degildir; operator "birine" atiyor,
o kisi izinliyse kayit sessizce bekliyordu. Ekip (sorumluluk alani) zaten
sistemde var ve "bakim/operasyon ekibini temsil eder" — cihazlar ve
kullanicilar ona bagli. Ariza da ona baglanabilmeli.

KISI ILE EKIP AYNI ANDA DOLU OLMAZ
----------------------------------
Kural API katmaninda: bir tarafa atama digerini temizler. Ikisi de doluyken
"sorumlu kim" sorusunun iki cevabi olurdu; kolon duzeyinde CHECK koymak yerine
tek giris noktasinda (assign ucu) tutuluyor — mevcut kayitlarin hicbiri bu
durumda degil ve kisit, ileride devralma akisi eklenirse migration
gerektirirdi.

SILINME DAVRANISI
-----------------
`ON DELETE SET NULL`: ekip kaldirilirsa atama duser. Kaskat silme ariza
kaydini yok ederdi; kisitlamak ise ekip silmeyi imkansiz kilardi.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

_TABLO = "fault_events"
_KOLON = "assigned_to_area_id"
_INDEKS = "ix_fault_events_assigned_to_area_id"
_FK = "fk_fault_events_assigned_to_area_id"


def _kolonlar(bind) -> set[str]:  # noqa: ANN001
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLO)}


def _indeksler(bind) -> set[str]:  # noqa: ANN001
    return {i["name"] for i in sa.inspect(bind).get_indexes(_TABLO)}


def upgrade() -> None:
    bind = op.get_bind()
    if _KOLON not in _kolonlar(bind):
        op.add_column(_TABLO, sa.Column(_KOLON, sa.Integer(), nullable=True))
        # FK ayri adimda: SQLite ALTER ile FK ekleyemez (batch gerekir) ve
        # gelistirici makinesinde migration'in patlamasi, uretimde calisan bir
        # degisikligi test edilemez kilardi. Kolonun kendisi her iki lehcede de
        # eklenir; FK yalnizca destekleyen lehcede.
        if bind.dialect.name != "sqlite":
            op.create_foreign_key(
                _FK, _TABLO, "responsibility_areas", [_KOLON], ["id"], ondelete="SET NULL"
            )
    if _INDEKS not in _indeksler(bind):
        op.create_index(_INDEKS, _TABLO, [_KOLON])


def downgrade() -> None:
    bind = op.get_bind()
    if _INDEKS in _indeksler(bind):
        op.drop_index(_INDEKS, table_name=_TABLO)
    if _KOLON in _kolonlar(bind):
        if bind.dialect.name != "sqlite":
            op.drop_constraint(_FK, _TABLO, type_="foreignkey")
        op.drop_column(_TABLO, _KOLON)
