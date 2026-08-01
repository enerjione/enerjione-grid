"""historian_policy

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-01

HISTORIAN SECICILIGI — gercek SCADA pratigi
-------------------------------------------
Bir SCADA'da her tag arsive yazilmaz. Anlik deger (RTDB) her zaman guncel
tutulur — alarmlar, ekranlar ve kontrol oradan okur — ama arsive yalnizca
isaretlenen tag'ler, ustelik olu bant suzgecinden gecerek yazilir.

Bu sistemde `telemetry_history` docstring'i acikca "okunan HER degeri
saklar" diyordu. Olcum (saha test cihazi, 15 cihaz): 296 satir/sn, hepsi
arsive giriyor.

ALARM DOGRULUGU ETKILENMIYOR: alarm-service JetStream akisini dinliyor
(gecmis sorgusu YAPMIYOR) ve canli deger `telemetry_latest` tablosunda.

BU MIGRATION NE YAPIYOR
-----------------------
1. `signal_catalog.historize` (bool, default true) ve
   `signal_catalog.historize_deadband` (float, default 0) kolonlari.

2. BACKFILL — yalnizca zaman serisi HICBIR SORUYA CEVAP VERMEYEN tipler
   kapatiliyor:
     * `string`        (30 sinyal) — seri no, firmware surumu, donanim
       revizyonu, parca no, SIM CCID, sebeke operatoru, GPS. Cihazin omru
       boyunca sabit.
     * `binary_output` (18 sinyal) — `config_update`, `firmware_update`,
       `reset_all_fcis`, `trigger_*`. Bunlar cihaza YAZILAN komut noktalari;
       cihazin kendi durumu degil.

   Binary input, counter ve analog tiplerine DOKUNULMUYOR (historize=true
   kaliyor): ariza bayraklari, sayaclar ve olcumler arsivin asil icerigi.

3. Olu bant HERKESTE 0 birakiliyor, yani suzgec KAPALI ve arsivleme
   davranisi bu migration'dan sonra analoglarda AYNEN devam ediyor.

   NEDEN VARSAYILAN 0: olu bant veri cozunurlugunu KALICI olarak dusurur.
   Uygun deger sahaya ve sinyalin birimine gore degisir (akim icin 0.5 A,
   gerilim icin 1 V, sicaklik icin 0.5 C gibi) ve bu bir URUN KARARIDIR.
   Migration'in bunu operator adina secmesi, gecmise donuk incelemede
   arizanin oncesindeki egriyi sessizce silikleştirebilirdi.

GERI ALINABILIR: downgrade kolonlari dusurur.
"""

from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

#: Zaman serisi anlamli olmayan veri tipleri.
_ARSIVLENMEYEN_TIPLER = ("string", "binary_output")


def upgrade() -> None:
    op.add_column(
        "signal_catalog",
        sa.Column("historize", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "signal_catalog",
        sa.Column(
            "historize_deadband",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # Backfill. Parametreli sorgu: tip listesi koddan geliyor ama yine de
    # string birlestirme ile SQL kurmuyoruz.
    op.execute(
        sa.text(
            "UPDATE signal_catalog SET historize = false "
            "WHERE data_type IN :tipler"
        ).bindparams(sa.bindparam("tipler", value=_ARSIVLENMEYEN_TIPLER, expanding=True))
    )

    # `server_default` YALNIZCA backfill icin gerekliydi; kalici birakmak
    # uygulama katmanindaki default ile ikinci bir dogruluk kaynagi yaratir.
    op.alter_column("signal_catalog", "historize", server_default=None)
    op.alter_column("signal_catalog", "historize_deadband", server_default=None)


def downgrade() -> None:
    op.drop_column("signal_catalog", "historize_deadband")
    op.drop_column("signal_catalog", "historize")
