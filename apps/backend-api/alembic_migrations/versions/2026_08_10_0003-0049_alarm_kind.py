"""alarm_kind

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-10 00:00:03.000000

`alarm_events.kind` — alarm NEREDEN dogdu: "rule" | "comm_loss".

NEDEN GEREKLI
-------------
Analiz katmani "hangi cihazin haberlesmesi sik kopup geliyor" sorusunu
cevapliyor ve bunun icin haberlesme alarmlarini kural alarmlarindan
ayirmasi gerek. Yapisal bir ayirt edici YOKTU:

  * `signal_key` haberlesme alarminda NULL, ama sema kural alarmlarinda da
    NULL'a izin veriyor (`AlarmCreate.signal_key: str | None`).
  * Basliga bakmak cihaz adina ve arayuz diline bagimli olurdu.

Sessizce yanlis kovaya atan bir metrik, metrik olmamasindan KOTUDUR:
"su cihaz gunde 40 kez kopuyor" diyen bir satira bakip sahaya teknisyen
gonderilecek.

GERIYE DONUK DOLDURMA — TAHMIN DEGIL, BICIM KURTARMA
-----------------------------------------------------
Haberlesme alarmi TEK bir kod yolundan uretiliyor ve basligi deterministik:

    f"{device_name} haberleşme alarmı"

Bu son ekle eslesen ve `signal_key` NULL olan kayitlar `comm_loss` olarak
isaretlenir. Bu bir tahmin degil, sabit bir bicimden olguyu geri okumaktir.

ESLESMEYENLER NULL BIRAKILIR — "rule" VARSAYILMAZ. Eski kayitlarin hepsini
kural sayip analize katmak, uydurma veriyi olcum gibi gostermek olurdu;
analiz katmani NULL'lari "bilinmiyor" olarak DISARIDA tutar.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_INDEX = "ix_alarm_events_kind"


def _kolonlar(bind) -> set[str]:  # noqa: ANN001
    return {c["name"] for c in sa.inspect(bind).get_columns("alarm_events")}


def _indeksler(bind) -> set[str]:  # noqa: ANN001
    return {i["name"] for i in sa.inspect(bind).get_indexes("alarm_events")}


def upgrade() -> None:
    bind = op.get_bind()
    if "kind" not in _kolonlar(bind):
        op.add_column("alarm_events", sa.Column("kind", sa.String(length=20), nullable=True))

    # Yeni kayitlar zaten dolu geliyor; burasi GECMISI kurtariyor.
    # Yalnizca deterministik bicime uyanlar isaretlenir.
    op.execute(
        sa.text(
            """
            UPDATE alarm_events
               SET kind = 'comm_loss'
             WHERE kind IS NULL
               AND signal_key IS NULL
               AND title LIKE '%haberleşme alarmı'
            """
        )
    )

    if _INDEX not in _indeksler(bind):
        op.create_index(_INDEX, "alarm_events", ["kind"])


def downgrade() -> None:
    bind = op.get_bind()
    if _INDEX in _indeksler(bind):
        op.drop_index(_INDEX, table_name="alarm_events")
    if "kind" in _kolonlar(bind):
        op.drop_column("alarm_events", "kind")
