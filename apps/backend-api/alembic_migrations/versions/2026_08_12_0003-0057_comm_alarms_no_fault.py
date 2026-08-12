"""comm_alarms_no_fault

Revision ID: 0057
Revises: 0056
Create Date: 2026-08-12 00:00:03.000000

HABERLESME ALARMI HAT ARIZASI URETMEZ — mevcut kayitlarin duzeltilmesi.

NE OLUYORDU
-----------
`produces_fault` bayragi "bu alarm gercek bir hat arizasi uretir mi" sorusunu
cevaplar; `fault_recompute_service` yalnizca True olanlari "arizayi gordum"
diyen cihaz sayar. Alarm kurallarinda bu secenek arayuzden yonetiliyor
("Hat Arızası" kutusu), ama HABERLESME alarminin kurali yok ve iki ureticisi
de bu alani hic gondermiyordu:

  * alarm-service `_build_quality_alarm` (comm_lost/offline/invalid kalitesi)
  * backend `alarm_engine_service.handle_telemetry_alarm_event`

Ikisinde de varsayilan True devreye giriyordu. Sonucu: haberlesmesi kopan
cihaz, kendisi ile bir sonraki cihaz arasindaki aralikta HAT ARIZASI
aciyordu — haritada kirmizi kesim, operatore bildirim, ekibe bosuna saha
cikisi. Oysa sessiz kalan cihaz ariza akimi GORMUS DEGILDIR; sadece
bilmiyoruzdur ve "bilmiyorum"u "ariza var" diye okumak yanlistir.

BU MIGRATION NEYI DUZELTIR
--------------------------
Ureticiler duzeltildi ama ACIK kayitlar veritabaninda True olarak duruyor ve
cihaz sessiz kaldigi surece alarm yeniden gonderilmiyor (alarm-service cihaz
basina dedup yapar) — yani bayrak kendiliginden duzelmez. Burada gecmis ve
acik tum haberlesme alarmlari False'a cekilir.

Eslesme uc yoldan yapilir; ucu de ayni seyi anlatir ama farkli surumlerde
yazilmis kayitlar farkli izler birakti:
  kind = 'comm_loss'                  -> backend uretimi (dogru etiketli)
  title = 'Haberleşme arızası'        -> alarm-service uretimi
  title LIKE '% haberleşme alarmı'    -> backend'in eski baslik bicimi

Ayrica alarm-service'in urettigi kayitlar backend'de KOSULSUZ kind='rule'
olarak yazilmisti; onlarin kind'i da 'comm_loss' olarak duzeltilir, yoksa
"hangi cihazin haberlesmesi sik kopuyor" analizi bu alarmlari kural alarmi
sayarak eksik cevap verir.

ARIZA KAYITLARINA DOKUNULMAZ
----------------------------
Bu alarmlar yuzunden acilmis `fault_events` satirlari SILINMEZ. Bir sonraki
`recompute_faults` turunda karsiliksiz kalip "resolved" olurlar — yani ekran
kendiliginden temizlenir. Gecmis kayitlari silmek, o gun operatorun gercekten
gordugu ekrani sonradan degistirmek olurdu; hatali kayitlarin temizligi
(varsa) ayri ve bilincli bir istir.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_TABLO = "alarm_events"

# HAM SQL DEGIL CORE IFADESI: kosulda `LIKE '% haberleşme alarmı'` var ve ham
# metinde `%` psycopg2'nin bicim karakteri. Metni elle kacirmak ("%%")
# lehceye gore ya deseni bozar ya da patlar; Core'da desen BIND PARAMETRESI
# olarak gider ve kacis sorunu hic dogmaz.
_alarm_events = sa.table(
    _TABLO,
    sa.column("produces_fault", sa.Boolean),
    sa.column("kind", sa.String),
    sa.column("title", sa.String),
)

#: Eski baslik bicimi: "<cihaz adi> haberleşme alarmı".
_ESKI_BASLIK = "% haberleşme alarmı"
_YENI_BASLIK = "Haberleşme arızası"


def _kolonlar(bind) -> set[str]:  # noqa: ANN001
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLO)}


def upgrade() -> None:
    bind = op.get_bind()
    kolonlar = _kolonlar(bind)
    if "produces_fault" not in kolonlar:
        # Kolon yoksa yapacak bir sey de yok (cok eski sema).
        return

    c = _alarm_events.c
    baslik_izi = sa.or_(c.title == _YENI_BASLIK, c.title.like(_ESKI_BASLIK))
    haberlesme = sa.or_(baslik_izi, c.kind == "comm_loss") if "kind" in kolonlar else baslik_izi

    op.execute(
        _alarm_events.update()
        .where(sa.and_(c.produces_fault.is_(True), haberlesme))
        .values(produces_fault=False)
    )
    if "kind" in kolonlar:
        # alarm-service'in urettigi kayitlar backend'de KOSULSUZ 'rule'
        # yaziliyordu; etiketi duzeltmezsek haberlesme kararsizligi analizi
        # bu alarmlari kural alarmi sayar.
        op.execute(
            _alarm_events.update()
            .where(sa.and_(baslik_izi, sa.or_(c.kind.is_(None), c.kind != "comm_loss")))
            .values(kind="comm_loss")
        )


def downgrade() -> None:
    # GERI ALINMAZ (bilincli): "haberlesme alarmi hat arizasi uretir" hali bir
    # hataydi; geri yazmak sahada yanlis ariza kayitlari uretmeye devam
    # ederdi. Sema degismedigi icin geri alinacak yapi da yok.
    pass
