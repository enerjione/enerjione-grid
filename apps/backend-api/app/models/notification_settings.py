from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    smtp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=25)
    smtp_username: Mapped[str] = mapped_column(String(255), default="")
    smtp_password: Mapped[str] = mapped_column(String(255), default="")
    smtp_from_email: Mapped[str] = mapped_column(String(255), default="")
    # GONDEREN ADI — mektupta gorunen isim.
    #
    # Onceden bu ad Proje Ayarlari'ndan TURETILIYORDU
    # (site_title -> project_name -> customer_name). Alanin arayuzdeki adi
    # "Tarayici Sekme Basligi" oldugu icin kullanici oraya sekme basligi
    # yaziyor, o metin sessizce musteriye giden mektubun markasi oluyordu;
    # nereden geldigi hicbir ekranda yazmiyordu. Artik mail ayarlarinda
    # ACIK bir alan var ve doluysa HER ZAMAN o kazanir.
    #
    # Bos birakilirsa eski zincir yedek olarak calismaya devam eder —
    # mevcut kurulumlarda gonderen adi bu surumle DEGISMEZ.
    smtp_from_name: Mapped[str] = mapped_column(String(200), default="")
    sms_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 'mock' | 'netgsm' | 'twilio' | 'generic' — UI'da seclir.
    sms_provider: Mapped[str] = mapped_column(String(80), default="mock")
    # Generic JSON-POST sagayicilar (eski 'netgsm/jsonpost' davranisi) icin
    # endpoint URL; Twilio'da kullanilmaz (URL Twilio API'sinden hesaplanir).
    sms_api_url: Mapped[str] = mapped_column(String(500), default="")
    # Generic sagayicilarda API key; Twilio'da Auth Token olarak kullanilir.
    sms_api_key: Mapped[str] = mapped_column(String(255), default="")
    # Twilio'ya ozel: Account SID (AC.. ile baslayan) ve gonderen numara
    # (E.164 formatinda, orn. +14057769058). Diger sagayicilarda bos kalir.
    sms_account_sid: Mapped[str] = mapped_column(String(120), default="")
    sms_from_number: Mapped[str] = mapped_column(String(40), default="")
    # Telegram Bot API. Kanal/grup chat_id'leri virgulle ayrilmis liste.
    # Bot tokeni @BotFather'dan alinir; chat_id'ler @userinfobot veya
    # bot'un /getUpdates endpoint'inden ogrenilir.
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_bot_token: Mapped[str] = mapped_column(String(255), default="")
    telegram_chat_ids: Mapped[str] = mapped_column(String(2000), default="")
    # WhatsApp Web (Baileys self-hosted sidecar) — kullanicinin bu kanali
    # yapilandirip aktif ettigini isaretler. Baglanti durumu ve QR kodu
    # DB'de SAKLANMAZ; her zaman sidecar'dan canli cekilir.
    whatsapp_web_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Alarm broadcast icin secilen grup JID listesi (sidecar'in /groups
    # endpoint'inden kesfedilir, kullanici UI'da secer — elle JID girisi yok).
    # Virgulle ayrilmis, grup JID formati: <id>@g.us.
    whatsapp_web_group_jids: Mapped[str] = mapped_column(String(2000), default="")
    # Kapali: alarm/test mesajlari kullanicinin User.phone_number'ina gider
    # (kisisel mod). Acik: sadece whatsapp_web_group_jids'teki gruplara
    # broadcast yapilir — ikisi ayni anda calismaz.
    whatsapp_web_group_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
