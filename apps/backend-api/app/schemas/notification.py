from pydantic import BaseModel, EmailStr


class NotificationSettingsRead(BaseModel):
    smtp_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_email: str
    sms_enabled: bool
    sms_provider: str
    sms_api_url: str
    sms_api_key: str
    # Twilio'ya ozel (diger sagayicilarda bos kalir).
    sms_account_sid: str = ""
    sms_from_number: str = ""
    sms_twilio_use_whatsapp: bool = False
    sms_twilio_content_sid: str = ""
    sms_twilio_content_vars: str = ""
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""

    class Config:
        from_attributes = True


class NotificationSettingsUpdate(BaseModel):
    smtp_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_email: EmailStr
    sms_enabled: bool
    sms_provider: str
    sms_api_url: str
    sms_api_key: str
    sms_account_sid: str = ""
    sms_from_number: str = ""
    sms_twilio_use_whatsapp: bool = False
    sms_twilio_content_sid: str = ""
    sms_twilio_content_vars: str = ""
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""


class NotificationSmtpTestRequest(BaseModel):
    recipient_email: EmailStr
    subject: str | None = None
    message: str | None = None


class NotificationSmsTestRequest(BaseModel):
    recipient_phone: str
    message: str | None = None


class NotificationTelegramTestRequest(BaseModel):
    chat_id: str
    message: str | None = None


class TelegramDiscoveredChat(BaseModel):
    id: str
    type: str
    title: str


class TelegramDiscoverChatsRequest(BaseModel):
    """Bot token kayitli degilse veya gecici bir token denemek icin
    payload'da override edilebilir. Bos gelirse kayitli token kullanilir."""

    bot_token: str | None = None


class TelegramDiscoverChatsResult(BaseModel):
    ok: bool
    detail: str = ""
    chats: list[TelegramDiscoveredChat] = []


class NotificationTestResult(BaseModel):
    ok: bool
    detail: str
