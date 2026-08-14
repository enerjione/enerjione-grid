"""Temiz kurulumda kacan/kalan KISITLARI onarir (denetim 2026-08-13).

SORUN
-----
Sema iki ayri yoldan kuruluyor ve ikisi ayni sonucu vermiyordu:

  * TEMIZ kurulum: `Base.metadata.create_all()` + `alembic stamp head`
    -> migration'lar HIC kosmaz.
  * MEVCUT kurulum: `alembic upgrade head`.

Bu yuzden migration govdesinde yasayan her KISIT degisikligi sifirdan
kurulan sahalara ulasmiyordu. Bu migration iki tanesini onarir; ikisi de
katalogdan okunarak idempotent uygulanir (kisit adi surumden surume
degisebilir, sabit ad varsaymak migration'i patlatirdi).

1) `telemetry_history.device_id` -> `devices.id` FK'si DUSMELI.
   Migration 0046 cihaz silmeyi iki faza ayirirken bu FK'yi bilerek
   dusurmustu (FK dururken cihaz silme bloke olur; CASCADE ise dakikalarca
   suren silmeyi Postgres'e yaptirir). Ama MODEL FK'yi tarif etmeye devam
   ettigi icin `create_all` onu her temiz kurulumda geri kuruyordu:
   yukseltilen sahada calisan cihaz silme, temiz kurulanda FK ihlaliyle
   500 donuyordu. Model tarafi da bu commit'te duzeltildi
   (`app/models/telemetry_history.py`).

2) `lines.branched_from_pole_id` -> `poles.id` FK'si EKLENMELI.
   `poles.line_id -> lines.id` ile karsilikli bagimli oldugu icin
   SQLAlchemy donguyu cozemiyor, "Cannot correctly sort tables ...
   constraints involving these tables will not be considered" uyarisi
   verip IKI FK'yi de atliyordu. Alembic baseline'i BOS oldugundan
   (`0001` yalnizca damga) her kurulum semayi `create_all` ile kurmustu —
   yani bu kisit TUM kurulumlarda eksik. Model tarafinda `use_alter=True`
   ile cozuldu; burada mevcut veritabanlari onarilir.

   Kisit eklenmeden once YETIM degerler temizlenir, aksi halde ALTER
   patlar ve backend acilmaz.

Revision ID: 0064
Revises: 0063
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def _fk_adlari(baglanti, tablo: str, kolon: str, hedef: str | None = None) -> list[str]:
    """`tablo.kolon` uzerindeki FK kisitlarinin adlari (0046 ile ayni yaklasim).

    Ad SABIT DEGIL: kisit farkli surumlerde autogenerate ya da elle farkli
    adlarla olusmus olabilir. Katalogdan okuyoruz.
    """
    inspector = sa.inspect(baglanti)
    if not inspector.has_table(tablo):
        return []
    adlar = []
    for fk in inspector.get_foreign_keys(tablo):
        if kolon not in (fk.get("constrained_columns") or []):
            continue
        if hedef is not None and fk.get("referred_table") != hedef:
            continue
        if fk.get("name"):
            adlar.append(fk["name"])
    return adlar


def upgrade() -> None:
    baglanti = op.get_bind()
    inspector = sa.inspect(baglanti)

    # --- 1) telemetry_history.device_id FK'si duser -----------------------
    for ad in _fk_adlari(baglanti, "telemetry_history", "device_id", "devices"):
        op.drop_constraint(ad, "telemetry_history", type_="foreignkey")

    # --- 2) lines.branched_from_pole_id FK'si eklenir ---------------------
    if inspector.has_table("lines") and inspector.has_table("poles"):
        if not _fk_adlari(baglanti, "lines", "branched_from_pole_id", "poles"):
            # Yetim degerleri once temizle: kisit eklenirken var olan bir
            # ihlal ALTER'i patlatir ve backend hic acilmaz. Bu satirlar
            # zaten "silinmis bir direge bagli bransman" demek; dogru deger
            # NULL (ondelete=SET NULL ile ayni sonuc).
            op.execute(
                sa.text(
                    "UPDATE lines SET branched_from_pole_id = NULL "
                    "WHERE branched_from_pole_id IS NOT NULL "
                    "  AND branched_from_pole_id NOT IN (SELECT id FROM poles)"
                )
            )
            op.create_foreign_key(
                "fk_lines_branched_from_pole_id",
                "lines",
                "poles",
                ["branched_from_pole_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    baglanti = op.get_bind()
    inspector = sa.inspect(baglanti)

    if inspector.has_table("lines"):
        for ad in _fk_adlari(baglanti, "lines", "branched_from_pole_id", "poles"):
            op.drop_constraint(ad, "lines", type_="foreignkey")

    # telemetry_history FK'si BILEREK geri konmaz: 0046'nin dusurdugu kisiti
    # geri koymak, cihaz silmeyi yeniden bloke ederdi. Downgrade semayi
    # 0063'e tasir, hatayi geri getirmez.
