"""comm_loss_standard_rule

Revision ID: 0058
Revises: 0057
Create Date: 2026-08-12 00:00:04.000000

HABERLESME ARIZASI ARTIK BIR ALARM KURALI.

NE EKSIKTI
----------
Haberlesme alarmi kodda GOMULUYDU: seviyesi (critical), basligi, kimlere
gidecegi ve hat arizasi uretip uretmeyecegi kaynak dosyalarda sabitti.
Operatorun gorebilecegi ya da degistirebilecegi hicbir yer yoktu — Alarm
Kurallari ekrani sahadaki en sik alarmlardan birini HIC gostermiyordu.
Kapatmak isteyen kisi icin de tek yol kod degisikligiydi.

Ayrica bildirim kanali secimi kural uzerinden yapiliyor
(`notification_dispatch_service._resolve_active_rule` alarmin BASLIGI ile
kuralin ADINI eslestirir); kural olmadigi icin haberlesme alarmi "kural yok"
dalina dusup TUM etkin kanallardan gidiyordu. Yani kanal secimi de yoktu.

BU KAYIT NE YAPAR
-----------------
`rule_kind='comm_loss'` olan TEK bir standart kural ekler. Alarm motoru
(alarm-service `_process_device_comm_alarm`) bu kaydi okur:

  is_active            -> haberlesme alarmi uretilsin mi
  name                 -> alarm basligi (clear eslesmesi de bu baslikla)
  level                -> alarmin seviyesi
  produces_fault       -> hat arizasi uretsin mi (VARSAYILAN FALSE, bkz. 0057)
  device_code_filter   -> yalnizca bu cihazlarda
  device_model_filter  -> yalnizca bu modellerde
  notify_*             -> hangi kanallardan bildirim

`signal_key` bir sinyale bagli DEGIL: kural bir sinyalin degerine degil,
cihazin KALITESINE (comm_lost/offline/invalid) bakar. Sema kolonu zorunlu
oldugu icin bir NISAN degeri yazilir ve alarm-service comm_loss kurallarini
katalog kontrolunden muaf tutar.

VARSAYILANLAR NEDEN BOYLE
-------------------------
  produces_fault = False : sessiz kalan cihaz ariza akimi GORMUS DEGILDIR.
  notify_* = True        : bugunku davranis zaten "tum kanallar"di (kural
                           bulunamadigi icin fail-open); kural eklenince
                           kanallar KAPANMAMALI, aksi halde bu migration
                           sessizce bildirim kesintisi olurdu.
  level = critical       : mevcut davranis.

IDEMPOTENT: kayit zaten varsa hicbir sey yapmaz (elle duzenlenmis bir kural
migration tekrarinda EZILMEZ).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

_TABLO = "alarm_rules"

#: Kurali sinyale baglamayan NISAN deger. Katalogda boyle bir sinyal yok;
#: alarm-service comm_loss kurallarini katalog kontrolunden muaf tutar.
NISAN_SIGNAL_KEY = "__comm_loss__"

#: Alarmin basligi ile kuralin adi AYNI olmali — bildirim kanali secimi
#: (`_resolve_active_rule`) ikisini eslestirerek calisiyor.
KURAL_ADI = "Haberleşme arızası"


def upgrade() -> None:
    bind = op.get_bind()
    kolonlar = {c["name"] for c in sa.inspect(bind).get_columns(_TABLO)}
    if "rule_kind" not in kolonlar:
        return
    tablo = sa.table(
        _TABLO,
        *[sa.column(ad) for ad in kolonlar],
    )
    var = bind.execute(
        sa.select(sa.func.count())
        .select_from(tablo)
        .where(tablo.c.rule_kind == "comm_loss")
    ).scalar()
    if var:
        return

    degerler = {
        "signal_key": NISAN_SIGNAL_KEY,
        "name": KURAL_ADI,
        "description": (
            "Cihazla haberleşme koptuğunda açılır (comm_lost / offline / "
            "invalid kalite). Sinyal eşiğine bakmaz."
        ),
        "level": "critical",
        "rule_kind": "comm_loss",
        "comparator": "eq",
        "threshold": 0.0,
        "hysteresis": 0.0,
        "debounce_sec": 0,
        "is_active": True,
    }
    # Kolon adlari surumler arasinda degisebildigi icin YALNIZCA var olanlari
    # yaz; eksik olani varsayilaniyla birakmak, migration'i sema surumune
    # dayanikli kilar.
    for ad, deger in (
        ("produces_fault", False),
        ("notify_email", True),
        ("notify_sms", True),
        ("notify_telegram", True),
        ("notify_whatsapp_web", True),
    ):
        if ad in kolonlar:
            degerler[ad] = deger
    op.execute(tablo.insert().values(**{k: v for k, v in degerler.items() if k in kolonlar}))


def downgrade() -> None:
    bind = op.get_bind()
    kolonlar = {c["name"] for c in sa.inspect(bind).get_columns(_TABLO)}
    if "rule_kind" not in kolonlar:
        return
    tablo = sa.table(_TABLO, sa.column("rule_kind"), sa.column("signal_key"))
    op.execute(
        tablo.delete().where(
            sa.and_(
                tablo.c.rule_kind == "comm_loss",
                tablo.c.signal_key == NISAN_SIGNAL_KEY,
            )
        )
    )
