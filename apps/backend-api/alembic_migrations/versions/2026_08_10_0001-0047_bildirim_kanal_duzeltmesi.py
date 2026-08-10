"""Bildirim kanallari: ariza bildirim damgasi + kullanici tercihi opt-out'a cevrildi

Iki ayri sessiz kayip kapatiliyor:

1) `fault_events.notified_at` (YENI KOLON)
   Ariza bildirimi production varsayilaninda HIC gonderilmiyordu. Ariza
   kaydini `fault_recompute_service` acar; SMTP/HTTP cagrisi ariza motorunun
   icinde kostugu ve yanit vermeyen bir relay arizanin commit edilmemesine
   yol actigi icin satir ici dispatch `notification_inline_dispatch_enabled`
   bayragina baglanmisti. Bayrak production'da False, notification-worker ise
   yalnizca `alarm.created` tuketip ALARM dispatch'i tetikliyordu — ariza
   icin hicbir yol yoktu. Artik ariza `notified_at=NULL` ("bildirim
   bekliyor") olarak acilir, gonderimi worker'in tetikledigi dispatch ucu
   yapar ve bu alani damgalar (idempotency).

   Mevcut ACIK arizalar `notified_at = opened_at` ile isaretlenir: gecmiste
   acilmis (ve bildirimi kacirilmis) yuzlerce ariza icin migration sonrasi
   toplu e-posta/SMS/WhatsApp firtinasi cikmasin.

2) `user_notification_preferences` — sms/telegram/whatsapp opt-out'a cevrildi
   Dispatcher "kuralda secili VE kullanici tercihinde acik" seklinde
   AND'liyordu. Tercih satiri, kullanici bildirim ekranini SADECE ACTIGINDA
   bile (GET yan etkisi) sms=False/telegram=False/whatsapp=False ile
   yaziliyordu. Sonuc: kurulumcu alarm kuralinda "SMS + WhatsApp" isaretliyor,
   alarm gercek olusuyor, hicbir sey gitmiyordu.

   Bu False'lar bilincli bir tercih DEGIL, varsayilanin yan etkisiydi; bu
   yuzden True'ya cekiliyor. Kanal secimi bundan sonra alarm kuralinda,
   alici kumesi ise sorumluluk alaninda (scope_service) belirlenir; bu alan
   yalnizca kullanicinin BILINCLI opt-out'u icin kalir.
"""

from alembic import op
import sqlalchemy as sa

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fault_events",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_fault_events_notified_at", "fault_events", ["notified_at"], unique=False
    )
    # Gecmis arizalari "bildirimi yapilmis" say — migration sonrasi toplu
    # bildirim firtinasini onler.
    op.execute(
        "UPDATE fault_events SET notified_at = opened_at WHERE notified_at IS NULL"
    )

    # Sessizce kapali kalmis kanallari ac (bkz. modul docstring'i).
    op.execute(
        "UPDATE user_notification_preferences "
        "SET sms_enabled = true, telegram_enabled = true, whatsapp_web_enabled = true"
    )


def downgrade() -> None:
    # Kanal tercihleri geri alinmaz: hangi satirin bilincli kapatildigi
    # bilgisi zaten kaybolmustu, eski haline "hepsini kapat" diye donmek
    # bildirimleri yeniden sessizlestirir.
    op.drop_index("ix_fault_events_notified_at", table_name="fault_events")
    op.drop_column("fault_events", "notified_at")
