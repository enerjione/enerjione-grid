"""fault_resolution_note

Revision ID: 0054
Revises: 0053
Create Date: 2026-08-11 00:00:03.000000

`fault_events.resolution_note` — arizayi KAPATAN kisinin "ne yapildi" cevabi.

NE EKSIKTI
----------
Ariza kapatma serbestti: `PATCH /faults/{id}/status` herhangi bir durumdan
dogrudan `closed`a gecebiliyordu ve hicbir gerekce istemiyordu. Iki sonucu
vardi:

  1. SAHADA DEVAM EDEN bir ariza kapatilabiliyordu. Ekrandan dusuyor, kimse
     ilgilenmedigi halde kapali gorunuyordu. Cihaz hala alarm veriyor olsa
     bile listeye geri gelmiyordu.
  2. Kapanan arizanin NEDEN kapandigi hicbir yerde yazmiyordu. Gecmise
     bakildiginda "bu ariza nasil giderildi" sorusunun cevabi yoktu; ayni
     direkte tekrarlayan bir ariza icin onceki mudahalenin ne oldugu
     bilinemiyordu.

Bu kolon ikinci sorunu cozer; birincisi API katmanindaki gecis kuraliyla
(kapatma yalnizca `resolved_at` doluyken) kapanir.

NEDEN `note`DAN AYRI KOLON
--------------------------
`note` ariza acikken tutulan serbest calisma notudur ve degisir. Kapanis
gerekcesini oraya yazsaydik, sonraki bir not duzenlemesi kapanis kaydini
SESSIZCE silerdi. Bu alan bir kez yazilir ve arizanin kalici cevabidir.

GERIYE DONUK VERI
-----------------
Kolon NULLABLE. Mevcut kapali arizalarda bos kalir — gecmise gerekce
UYDURULMAZ. Kural yalnizca bundan sonraki kapatmalar icin isler.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_TABLO = "fault_events"
_KOLON = "resolution_note"


def _kolonlar(bind) -> set[str]:  # noqa: ANN001
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLO)}


def upgrade() -> None:
    bind = op.get_bind()
    if _KOLON not in _kolonlar(bind):
        op.add_column(_TABLO, sa.Column(_KOLON, sa.String(length=1000), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _KOLON in _kolonlar(bind):
        op.drop_column(_TABLO, _KOLON)
