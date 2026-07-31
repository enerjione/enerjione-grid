"""add_device_clock_to_telemetry

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-31 00:00:08.000000

Cihaz saatinin durumunu CANLI tabloya da tasir, ki "sinyal statusu" ekraninda
gorunebilsin.

SORUN
-----
0025 cihazin kendi olay damgasini (`device_event_at`) ve o damganin
guvenilirligini (`timestamp_quality`) YALNIZCA `telemetry_history`'ye yazdi.
Ama canli deger ekrani (`/signals/live`) ve WS yayini `telemetry` tablosundan
okunuyor. Sonuc: saati bozuk bir cihaz arsivde isaretli ama EKRANDA normal
gorunuyordu. Operator, SOE analizi bozuk cikana kadar bunu fark etmiyordu.

`telemetry_history`'yi canli ekran icin sorgulamak alternatif degil: o tablo
90 gunluk arsiv, canli ekran her yenilemede 600 cihaz x 193 sinyal icin son
degeri istiyor. `telemetry` tam da bunun icin var (30 dakikalik pencere).

NEDEN UCUZ
----------
`telemetry` hypertable DEGIL (yalnizca `telemetry_history` hypertable'a
cevrilir, bkz. 0007) ve 30 dakikalik retention penceresinde tutuluyor: 600
cihaz olceginde ~540k satir. Iki nullable kolonun default'u NULL oldugu icin
PostgreSQL 11+ ADD COLUMN'u sadece katalog degisikligi olarak yapar; tablo
yeniden yazilmaz, satir basi ek maliyet yok.

INDEX EKLENMEDI — bilincli. Bu kolonlar filtre degil GORUNTU alanidir: canli
deger sorgusu zaten (device_id, signal_key) uzerinden DISTINCT ON ile geliyor
ve bu iki kolon yalnizca secilen satirla birlikte donuyor. 0022'nin temizledigi
yazma amplifikasyonunu geri getirmenin anlami yok.

ALARM ZAMANINA ETKISI: YOK
--------------------------
Bu kolonlar tesyhis/goruntu icindir. Alarm ve ariza zaman damgalari HER ZAMAN
backend'in olayi algiladigi ana gore uretilir (`datetime.now(timezone.utc)`);
cihazin kendi saati oraya HICBIR kosulda karismaz. Bkz.
tests/test_alarm_time_authority.py — bu kural testle kilitlidir.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Kilit bekleme tavani: ADD COLUMN kisa sureli ACCESS EXCLUSIVE alir.
    # Telemetri akisi bu tabloya surekli INSERT ettigi icin cakisma anlik da
    # olsa mumkun; sonsuza kadar beklemek yerine hizli basarisiz ol.
    op.execute("SET lock_timeout = '30s'")
    op.add_column(
        "telemetry",
        sa.Column("device_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telemetry",
        sa.Column("timestamp_quality", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telemetry", "timestamp_quality")
    op.drop_column("telemetry", "device_event_at")
