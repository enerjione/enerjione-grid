"""add_device_event_time

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-31 00:00:07.000000

Cihazin kendi DNP3 olay zaman damgasi (B2) — SOE / ariza suresi analizi icin.

SORUN
-----
Gateway `source_timestamp` olarak KENDI saatini basiyor ve bir cihazin TUM
sinyallerine ayni degeri veriyor (poller cycle basinda tek damga uretiyor).
Cihazin kendi DNP3 olay damgasi atiliyor.

Somut senaryo: 4G link 10 dakika kopar. Outstation event buffer'inda
`08:00:03` (ariza gecti), `08:00:04` (ariza kalkti), `08:07:12` (yeniden
kapama) olaylarini KENDI damgalariyla biriktirir. Link gelince hepsi tek
fragment'ta bosalir ve gateway hepsini `08:10:00` ile ayni saniyeye
damgalar. Ariza SURESI ve olay SIRASI kalici olarak kaybolur.

NEDEN YENI KOLON — `source_timestamp`'IN ANLAMI DEGISTIRILMEDI
--------------------------------------------------------------
Gateway ekibi `source_timestamp`'i "artik cihaz zamani" yapmayi onerdi.
REDDEDILDI. O alan ayni anda:
  * telemetry_history BIRINCIL ANAHTARININ parcasi (models: uc kolon da PK)
  * TimescaleDB hypertable PARTITION kolonu (0007)
  * retention/disk-guard silme kriteri (telemetry_retention, disk_guard)
  * continuous aggregate ekseni (0007/0023)

Anlamini degistirmenin somut sonuclari:
  * gecmise damgali satir historian INSERT'ini patlatir -> TUM telemetri
    akisi durur
  * ileriye damgali satir `telemetry` tablosunda OLUMSUZ olur; retention ve
    disk guard onu HIC goremez
  * ayni saniyeye dusen "ariza gecti / ariza kalkti" cifti AYNI PK'ya duser,
    ikincisi on_conflict_do_nothing ile SESSIZCE kaybolur — yani B2'nin
    korumak istedigi veri tam da bu yuzden yok olur
  * RTC pili biten cihaz 2000-01-01 damgalar; olcum kabul edilir ve retention
    tarafindan bir gun icinde SESSIZCE silinir

Ayri nullable kolon bunlarin HICBIRINE dokunmaz. Kotu bir cihaz saati
yalnizca analiz verisini bozar; bozuklugu `timestamp_quality` soyler.

DEPLOY SIRASINDAN BAGIMSIZ
--------------------------
`TelemetryIn`'de `extra="forbid"` YOK. Yani yeni gateway ONCE cikarsa bile
eski backend yeni alanlari sessizce yok sayar; eski gateway yeni backend'e
alan gondermez ve kolonlar NULL kalir. Iki yon de guvenli — "once backend"
kuralina bagimli degiliz.

MALIYET
-------
Iki nullable kolon. `timestamp_quality` yalnizca gateway bildirdiginde dolar.
Kolonlar hypertable'a eklenir; TimescaleDB ADD COLUMN'u chunk'lara yayar,
varsayilan NULL oldugu icin tablo yeniden yazilmaz.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Kilit bekleme tavani: hypertable'a ADD COLUMN kisa sureli ACCESS
    # EXCLUSIVE alir; cakisan bir islem varsa sonsuza kadar beklemesin.
    op.execute("SET lock_timeout = '30s'")
    # KOLON ZATEN VARSA ATLA — kurulum bu yuzden kilitleniyordu.
    #
    # Backend acilista once `Base.metadata.create_all()` cagiriyor: tablolar
    # GUNCEL modellerden olusuyor ve bu kolonlar zaten iceride. Ardindan
    # alembic gecmisi bastan oynatiliyor ve 0025 var olan kolonu eklemeye
    # calisip `DuplicateColumn` ile oluyor:
    #
    #     psycopg2.errors.DuplicateColumn: column "device_event_at"
    #     of relation "telemetry_history" already exists
    #
    # Backend acilamiyor, healthcheck dusuyor, kurulum "backend-api is
    # unhealthy" diyerek duruyor. Cihaz KALICI olarak kilitleniyor: her
    # yeniden deneme ayni noktada patliyor.
    #
    # Temiz bir veritabaninda gorulmez (create_all ile alembic ayni sirayi
    # uretir); yalnizca onceki bir denemeden veri hacmi kalmis cihazlarda
    # cikar. "Bir sunucuda oluyor digerinde olmuyor"un sebebi tam olarak bu.
    #
    # Migration'in gorevi SONUCU garanti etmek: kolon varsa is zaten yapilmis.
    mevcut = {
        s["name"]
        for s in sa.inspect(op.get_bind()).get_columns("telemetry_history")
    }
    if "device_event_at" not in mevcut:
        op.add_column(
            "telemetry_history",
            sa.Column("device_event_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "timestamp_quality" not in mevcut:
        op.add_column(
            "telemetry_history",
            sa.Column("timestamp_quality", sa.String(length=20), nullable=True),
        )
    # INDEX EKLENMEDI — bilincli. 0022 tam da gereksiz index'leri dusurmustu;
    # gunde ~26M satir alan bir tabloya "belki lazim olur" index'i eklemek
    # yazma amplifikasyonunu geri getirir. SOE sorgulari zaten
    # (device_id, signal_key, source_timestamp) PK'si uzerinden araliga
    # inip sonra device_event_at'e gore siralayabilir.


def downgrade() -> None:
    # Geri alma da dayanikli: kolon yoksa hata vermek yerine atla.
    mevcut = {
        s["name"]
        for s in sa.inspect(op.get_bind()).get_columns("telemetry_history")
    }
    if "timestamp_quality" in mevcut:
        op.drop_column("telemetry_history", "timestamp_quality")
    if "device_event_at" in mevcut:
        op.drop_column("telemetry_history", "device_event_at")
