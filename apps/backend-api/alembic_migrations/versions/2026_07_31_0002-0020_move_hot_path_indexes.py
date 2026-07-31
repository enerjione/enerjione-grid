"""move_hot_path_indexes

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-31 00:00:02.000000

Uc KRITIK index'in sahipligini `main.py` startup blogundan Alembic'e tasir.

SORUN
-----
Bu uc index yalnizca `app/main.py` icindeki `create_tables()` startup
blogunda tanimliydi ve hicbir migration'da yoktu:

    idx_telemetry_device_signal_ts     telemetry(device_id, signal_key,
                                                source_timestamp DESC)
    idx_telemetry_source_timestamp     telemetry(source_timestamp)
    uq_processed_message_consumer_msg  processed_messages(consumer_name,
                                                          message_id) UNIQUE

O blok dosyada "LEGACY / FREEZED — yeni satir eklemeyin" diye isaretli ve
"legacy bootstrap" olarak tarif ediliyor; yani bir gun temizlenmesi BEKLENEN
bir kod. Temizlenirse:

  * `uq_processed_message_consumer_msg` gider -> idempotency'nin TEK
    veritabani garantisi kalkar. telemetry_consumer.py duplicate mesaji
    `IN` pre-check ile eleyip gecer ama es zamanli iki consumer ayni mesaji
    ayni anda gorurse cift yazim SESSIZCE olur.
  * `idx_telemetry_device_signal_ts` gider -> canli deger sorgusu ve alarm
    "son durum" sorgusu sequential scan'e duser.
  * `idx_telemetry_source_timestamp` gider -> retention DELETE'i her turda
    tabloyu bastan tarar.

Ucu de SESSIZ bozulmalar: sistem calismaya devam eder, sadece yavaslar veya
duplicate yazar. Disk/CPU dolana kadar kimse fark etmez.

NEDEN `create_all` BUNLARI KURMUYOR
------------------------------------
Uc index de ORM metadata'sinda DEGIL, ham SQL ile yaziliydi. Model
tanimlarindaki `index=True` kolonlari (telemetry.device_id,
telemetry.signal_key, processed_messages.processed_at, ...) `create_all` ile
gelir; bu composite/UNIQUE ucluler gelmez.

NEDEN CONCURRENTLY GEREKMIYOR
------------------------------
Bu index'ler MEVCUT kurulumlarda ZATEN VAR (startup blogu her boot'ta
`CREATE INDEX IF NOT EXISTS` ile kuruyor). Yani bu migration:
  * mevcut sahalarda   -> no-op (IF NOT EXISTS),
  * yeni kurulumlarda  -> tablolar BOS iken kosar.
Iki durumda da uzun sureli lock olusmaz, dolayisiyla CONCURRENTLY (ve onun
gerektirdigi transaction-disi calisma hilesi) gereksiz.

BU MIGRATION'DAN SONRA
----------------------
`main.py` icindeki karsilik gelen uc `CREATE INDEX` satiri artik yedek
(idempotent no-op) durumdadir ve blok temizlenirken guvenle silinebilir.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Retention DELETE'i (telemetry_retention._purge_telemetry) bu index
    # uzerinde `source_timestamp < cutoff` araligini tarar.
    op.create_index(
        "idx_telemetry_source_timestamp",
        "telemetry",
        ["source_timestamp"],
        if_not_exists=True,
    )

    # "Bu cihaz+sinyalin son degeri" sorgusu. device_id ve signal_key ayri
    # ayri index'li ama PostgreSQL bu kombinasyonda bitmap scan yapip
    # suboptimal kaliyor; composite + DESC ile dogrudan index seek olur.
    # DESC siralama icin ham SQL: op.create_index sutun bazinda DESC almiyor.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_telemetry_device_signal_ts "
        "ON telemetry (device_id, signal_key, source_timestamp DESC)"
    )

    # Idempotency'nin veritabani garantisi. telemetry_consumer.py her batch'te
    # (consumer_name, message_id) ikilisiyle sorgular; UNIQUE olmasi es zamanli
    # cift yazimi DB seviyesinde de engeller.
    op.create_index(
        "uq_processed_message_consumer_msg",
        "processed_messages",
        ["consumer_name", "message_id"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    """Index'leri dusur.

    NOT: `main.py` startup blogu hala ayakta oldugu surece bir sonraki boot
    bunlari yeniden kurar — downgrade kalici olmaz. Bu bilincli: index'siz
    calismak istenen bir durum degil, downgrade yalnizca zincir butunlugu
    icin var.
    """
    op.drop_index(
        "uq_processed_message_consumer_msg",
        table_name="processed_messages",
        if_exists=True,
    )
    op.execute("DROP INDEX IF EXISTS idx_telemetry_device_signal_ts")
    op.drop_index(
        "idx_telemetry_source_timestamp", table_name="telemetry", if_exists=True
    )
