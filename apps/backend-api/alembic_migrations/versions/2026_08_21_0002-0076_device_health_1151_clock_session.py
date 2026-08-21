"""device_runtime_health — 1.15.1 saat sagligi + oturum kaniti alanlari.

Sozlesme: `device_health_v1` (gateway 1.15.1). Vendor kopyasi:
`docs/gateway-contract/device-health-api-1.15.1.md`.

SEMA ADI DEGISMEDI. Bes alan EKLENDI, hicbiri kaldirilmadi ve hepsi
OPSIYONEL:

  device_clock_status      enum|null   unknown|ok|invalid|need_time
  device_clock_offset_sec  float|null  cihaz_saati - gateway_saati (isaretli)
  last_device_time_epoch   float|null  cihazin KENDI bildirdigi damga
  need_time_iin            bool|null   null = HIC IIN gorulmedi
  session_started_epoch    float|null  oturum kapaliyken null

NEDEN HEPSI NULLABLE
--------------------
Sahada hala 1.15.0 kosan gateway'ler var ve onlar bu alanlari GONDERMEZ.
`NOT NULL + server_default` vermek, gondermeyenler icin UYDURMA bir deger
yazmak olurdu: `need_time_iin=False` "cihaz saat istemiyor" demektir, oysa
gercek "bilmiyoruz"dur. Bu ayrim onemli, cunku saati yanlis olup saat
ISTEMEYEN cihaz kendiliginden DUZELMEZ — sahada gorulen tam olarak buydu.

`device_clock_offset_sec` icin `0.0` GECERLI bir degerdir (tam senkron);
`NULL` ile karistirilmamalidir. Bu da ayni sebeple default alamaz.

NEDEN AYNI TABLOYA KOLON
------------------------
Bunlar cihaz basina TEK bir gozlemin parcalari ve ayni partide, ayni
`updated_at` ile gelirler. Ayri tabloya koymak her okumada bir JOIN ve
"hangi gozleme ait" sorusunu yaratirdi.

Revision ID: 0076
Revises: 0075
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: Union[str, None] = "0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLO = "device_runtime_health"

#: (kolon, tip) — model ile BIREBIR ayni olmak zorunda; parite testi kontrol eder.
YENI_KOLONLAR = (
    ("device_clock_status", sa.String(length=24)),
    ("device_clock_offset_sec", sa.Float()),
    ("last_device_time_epoch", sa.Float()),
    ("need_time_iin", sa.Boolean()),
    ("session_started_epoch", sa.Float()),
)


def _kolonlar() -> set[str]:
    return {k["name"] for k in sa.inspect(op.get_bind()).get_columns(TABLO)}


def upgrade() -> None:
    # ZATEN VARSA ATLA — temiz kurulum semayi `create_all` ile kuruyor ve
    # kolonlar bu migration hic kosmadan da var olabilir. Migration'in
    # gorevi SONUCU garanti etmek: kolon varsa is zaten yapilmis.
    mevcut = _kolonlar()
    for ad, tip in YENI_KOLONLAR:
        if ad in mevcut:
            continue
        # `server_default` YOK ve bu BILINCLI: eksik gozlemi uydurma bir
        # degere cevirmek, "bilmiyoruz"u "hayir" yapardi.
        op.add_column(TABLO, sa.Column(ad, tip, nullable=True))


def downgrade() -> None:
    mevcut = _kolonlar()
    for ad, _tip in reversed(YENI_KOLONLAR):
        if ad in mevcut:
            op.drop_column(TABLO, ad)
