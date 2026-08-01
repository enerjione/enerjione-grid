"""device_iec104_ca_backfill

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-01 00:00:03.000000

`devices.iec104_common_address` NULL olan cihazlara BENZERSIZ adres atar.

NEDEN
-----
Kolon nullable ve varsayilansizdi. IEC104 kayit defteri
(`registry._resolve_device_ca`) deger yoksa target'in varsayilan CA'sina
duser; IOA ise SINYALDEN gelir ve TUM CIHAZLARDA AYNIDIR. Yani her cihaza
elle ayri CA atanmadikca:

    600 cihazin TAMAMI ayni (CA, IOA) ciftlerine biner.

SCADA tarafinda 600 cihaz TEK sanal cihaza cokuyor: hangi fiderin
arizalandigi anlasilamiyor ve son yazan deger digerlerini eziyor. Ustelik
carpisma uyarisi da hicbir yerde basilmiyordu — yani ariza tamamen sessizdi.

ATAMA KURALI
------------
Mevcut en yuksek adresten devam eden ardisik tamsayilar (1'den baslar).
0 KULLANILMAZ: bazi master'larda "tanimsiz/ilgisiz" anlamina gelir.
65535 broadcast oldugu icin tavan 65534 — 600 cihaz rahat sigar.

ELLE ATANMIS ADRESLERE DOKUNULMAZ: operator bir SCADA adres planina uymus
olabilir; onlari yeniden numaralandirmak calisan bir kurulumu bozardi.

Yeni cihazlarda ayni atama `DeviceRepository.create` icinde yapiliyor.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BROADCAST = 0xFFFF


def upgrade() -> None:
    bind = op.get_bind()

    var = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_schema = current_schema()"
            "   AND table_name = 'devices' AND column_name = 'iec104_common_address'"
        )
    ).first()
    if var is None:
        logger.warning("0029: devices.iec104_common_address kolonu yok — atlandi")
        return

    eksik = bind.execute(
        sa.text("SELECT count(*) FROM devices WHERE iec104_common_address IS NULL")
    ).scalar_one()
    if not eksik:
        logger.info("0029: adressiz cihaz yok — atlandi")
        return

    baslangic = int(
        bind.execute(
            sa.text("SELECT COALESCE(MAX(iec104_common_address), 0) FROM devices")
        ).scalar_one()
    )
    if baslangic + eksik >= BROADCAST:
        # Pratikte ulasilmaz; sessizce sarmalamak yerine acikca dur.
        raise RuntimeError(
            f"IEC 104 Common Address alani yetmiyor: {eksik} cihaz icin "
            f"{baslangic} sonrasi bos adres kalmadi (tavan {BROADCAST - 1})."
        )

    # Deterministik sira: cihaz id'si. Ayni veritabaninda tekrar kosulsa
    # (ki `IS NULL` filtresi yuzunden kosmaz) ayni sonucu verir.
    bind.execute(
        sa.text(
            """
            WITH numaralandirma AS (
                SELECT id,
                       :baslangic + ROW_NUMBER() OVER (ORDER BY id) AS yeni_ca
                  FROM devices
                 WHERE iec104_common_address IS NULL
            )
            UPDATE devices d
               SET iec104_common_address = n.yeni_ca
              FROM numaralandirma n
             WHERE d.id = n.id
            """
        ),
        {"baslangic": baslangic},
    )
    logger.warning(
        "0029: %d cihaza IEC 104 Common Address atandi (%d..%d) — onceden "
        "hepsi ayni (CA, IOA) ciftine biniyordu",
        eksik,
        baslangic + 1,
        baslangic + eksik,
    )


def downgrade() -> None:
    """Atanan adresler GERI ALINMAZ.

    Geri almak (NULL'a cevirmek) tam da duzeltilen arizayi geri getirir:
    tum cihazlar yeniden ayni CA'ya duser ve SCADA'da tek cihaza cokerler.
    Ayrica hangi adresin bu migration tarafindan atandigini, hangisinin
    operator tarafindan girildigini ayirt etmenin yolu yok.
    """
    pass
