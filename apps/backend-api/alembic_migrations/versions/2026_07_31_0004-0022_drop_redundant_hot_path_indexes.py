"""drop_redundant_hot_path_indexes

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-31 00:00:04.000000

Sicak yazma yollarindaki KARSILIKSIZ index'leri dusurur.

Bes index de mevcut bir bilesik index (veya PK) tarafindan zaten karsilaniyor;
hicbir sorguya hizmet etmiyorlar ama her INSERT'te yeniden yaziliyorlar.
Bu tablolara gunde ~26M satir girdigi icin maliyet hem DISK hem WAL hem
rastgele I/O olarak surekli odeniyor.

DUSURULENLER VE NEDEN KARSILIKSIZ OLDUKLARI
-------------------------------------------
1. `ix_telemetry_history_device_signal_ts`
   (device_id, signal_key, source_timestamp)
   -> PRIMARY KEY BIREBIR AYNI kolonlari AYNI SIRADA tasiyor. Tam kopya.
      Historian disk kullaniminin kabaca dortte birini tek basina yiyordu.

2. `ix_processed_messages_consumer_name`
3. `ix_processed_messages_message_id`
   -> Bu tablo kodda HER ZAMAN ikisi BIRLIKTE sorgulanir:
        idempotency_service.py:13-16   (consumer_name == .. AND message_id == ..)
        telemetry_consumer.py:110-113  (consumer_name == .. AND message_id IN ..)
      Tek basina sorgulayan cagri YOK. Bilesik UNIQUE index
      `uq_processed_message_consumer_msg` ikisini de karsiliyor.

4. `ix_telemetry_device_id`
5. `ix_telemetry_signal_key`
   -> `idx_telemetry_device_signal_ts` (device_id, signal_key,
      source_timestamp DESC) bilesik index'i mevcut tum sorgulari karsiliyor.
      Kodda signal_key TEK BASINA filtrelenen sorgu yok; public.py:233-236,
      signals.py:252 ve alarm_reconciliation.py:306-313 hepsi device_id ile
      birlikte filtreliyor.

MODEL TARAFI
------------
Ilgili modellerden `index=True` / `__table_args__` kaldirildi, yani yeni
kurulumlarda (create_all yolu) bu index'ler hic olusmaz. Bu migration
yalnizca MEVCUT kurulumlari hizalar.

GERI ALMA
---------
downgrade() index'leri geri kurar. Performans acisindan gerek YOK (hicbiri
kullanilmiyor) ama zincir butunlugu ve "yanlis teshis ettiysek geri donebilelim"
icin duruyor.

NOT: `telemetry_history` bir hypertable; ustundeki index'i dusurmek chunk'lardaki
karsiliklarini da dusurur (TimescaleDB 2.x davranisi).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REDUNDANT = (
    "ix_telemetry_history_device_signal_ts",
    "ix_processed_messages_consumer_name",
    "ix_processed_messages_message_id",
    "ix_telemetry_device_id",
    "ix_telemetry_signal_key",
)


def upgrade() -> None:
    # Kilit bekleme tavani: DROP INDEX kisa suren ACCESS EXCLUSIVE alir, ama
    # cakisan uzun bir sorgu varsa sonsuza kadar beklemesin.
    op.execute("SET lock_timeout = '30s'")
    for name in _REDUNDANT:
        op.execute(f"DROP INDEX IF EXISTS {name}")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_telemetry_history_device_signal_ts "
        "ON telemetry_history (device_id, signal_key, source_timestamp)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processed_messages_consumer_name "
        "ON processed_messages (consumer_name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processed_messages_message_id "
        "ON processed_messages (message_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_telemetry_device_id ON telemetry (device_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_telemetry_signal_key ON telemetry (signal_key)"
    )
