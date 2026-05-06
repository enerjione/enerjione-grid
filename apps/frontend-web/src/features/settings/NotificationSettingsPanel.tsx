import { useEffect, useState, type FormEvent } from "react";

import type { NotificationSettings } from "../../shared/types";

type Props = {
  initialSettings: NotificationSettings | null;
  loading: boolean;
  saving: boolean;
  error: string;
  onSave: (payload: NotificationSettings) => Promise<void>;
  onTestSmtp: (payload: { recipient_email: string; subject?: string; message?: string }) => Promise<{ ok: boolean; detail: string }>;
  onTestSms: (payload: { recipient_phone: string; message?: string }) => Promise<{ ok: boolean; detail: string }>;
  /** Telegram bot test gonderimi. Alarm akisindaki bot tokeni ve verilen
   *  chat_id ile sade bir test mesaji yollar. */
  onTestTelegram?: (payload: { chat_id: string; message?: string }) => Promise<{ ok: boolean; detail: string }>;
};

const EMPTY_SETTINGS: NotificationSettings = {
  smtp_enabled: false,
  smtp_host: "",
  smtp_port: 587,
  smtp_username: "",
  smtp_password: "",
  smtp_from_email: "",
  sms_enabled: false,
  sms_provider: "mock",
  sms_api_url: "",
  sms_api_key: "",
  telegram_enabled: false,
  telegram_bot_token: "",
  telegram_chat_ids: ""
};

export function NotificationSettingsPanel({
  initialSettings,
  loading,
  saving,
  error,
  onSave,
  onTestSmtp,
  onTestSms,
  onTestTelegram
}: Props) {
  const [form, setForm] = useState<NotificationSettings>(EMPTY_SETTINGS);
  const [submitError, setSubmitError] = useState("");
  const [smtpTestEmail, setSmtpTestEmail] = useState("");
  const [smsTestPhone, setSmsTestPhone] = useState("");
  const [telegramTestChat, setTelegramTestChat] = useState("");
  const [testInfo, setTestInfo] = useState("");
  const [testingSmtp, setTestingSmtp] = useState(false);
  const [testingSms, setTestingSms] = useState(false);
  const [testingTelegram, setTestingTelegram] = useState(false);

  useEffect(() => {
    if (initialSettings) {
      // Backend bos donduyse defaultlari koru (telegram alanlari Optional).
      setForm({
        ...EMPTY_SETTINGS,
        ...initialSettings,
        telegram_enabled: initialSettings.telegram_enabled ?? false,
        telegram_bot_token: initialSettings.telegram_bot_token ?? "",
        telegram_chat_ids: initialSettings.telegram_chat_ids ?? ""
      });
    }
  }, [initialSettings]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitError("");
    try {
      await onSave(form);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Bildirim ayarları kaydedilemedi.");
    }
  };

  const handleSmtpTest = async () => {
    if (!smtpTestEmail.trim()) {
      setSubmitError("SMTP test için alıcı e-posta giriniz.");
      return;
    }
    setSubmitError("");
    setTestInfo("");
    setTestingSmtp(true);
    try {
      const result = await onTestSmtp({ recipient_email: smtpTestEmail.trim() });
      setTestInfo(result.detail);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "SMTP test başarısız.");
    } finally {
      setTestingSmtp(false);
    }
  };

  const handleSmsTest = async () => {
    if (!smsTestPhone.trim()) {
      setSubmitError("SMS test için telefon numarası giriniz.");
      return;
    }
    setSubmitError("");
    setTestInfo("");
    setTestingSms(true);
    try {
      const result = await onTestSms({ recipient_phone: smsTestPhone.trim() });
      setTestInfo(result.detail);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "SMS test başarısız.");
    } finally {
      setTestingSms(false);
    }
  };

  const handleTelegramTest = async () => {
    if (!onTestTelegram) return;
    const chat = telegramTestChat.trim();
    if (!chat) {
      setSubmitError("Telegram test için chat ID giriniz.");
      return;
    }
    setSubmitError("");
    setTestInfo("");
    setTestingTelegram(true);
    try {
      const result = await onTestTelegram({ chat_id: chat });
      setTestInfo(result.detail);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Telegram test başarısız.");
    } finally {
      setTestingTelegram(false);
    }
  };

  return (
    <section className="tab-panel notification-tab-panel">
      <div className="panel-head">
        <h3>Bildirim Ayarları</h3>
      </div>
      <form className="settings-modal notification-settings-panel" onSubmit={handleSubmit}>
        <div className="notification-columns">
          <div className="notification-card">
            <h4>E-posta (SMTP)</h4>
            <label className="notify-option">
              <input
                type="checkbox"
                checked={form.smtp_enabled}
                onChange={(event) => setForm((prev) => ({ ...prev, smtp_enabled: event.target.checked }))}
              />
              SMTP Aktif
            </label>
            <label>
              SMTP Sunucu
              <input
                value={form.smtp_host}
                onChange={(event) => setForm((prev) => ({ ...prev, smtp_host: event.target.value }))}
                placeholder="smtp.ornek.com"
              />
            </label>
            <label>
              SMTP Port
              <input
                type="number"
                min={1}
                max={65535}
                value={form.smtp_port}
                onChange={(event) => setForm((prev) => ({ ...prev, smtp_port: Number(event.target.value) || 0 }))}
              />
            </label>
            <label>
              SMTP Kullanıcı Adı
              <input
                value={form.smtp_username}
                onChange={(event) => setForm((prev) => ({ ...prev, smtp_username: event.target.value }))}
              />
            </label>
            <label>
              SMTP Şifre
              <input
                type="password"
                value={form.smtp_password}
                onChange={(event) => setForm((prev) => ({ ...prev, smtp_password: event.target.value }))}
              />
            </label>
            <label>
              Gönderen E-posta
              <input
                type="email"
                value={form.smtp_from_email}
                onChange={(event) => setForm((prev) => ({ ...prev, smtp_from_email: event.target.value }))}
                placeholder="alarm@firma.com"
              />
            </label>
            <label>
              Test E-posta Adresi
              <input
                type="email"
                value={smtpTestEmail}
                onChange={(event) => setSmtpTestEmail(event.target.value)}
                placeholder="test@firma.com"
              />
            </label>
            <button type="button" className="secondary-btn" onClick={handleSmtpTest} disabled={testingSmtp}>
              {testingSmtp ? "SMTP Test Ediliyor..." : "SMTP Test Gönder"}
            </button>
          </div>

          <div className="notification-card">
            <h4>SMS</h4>
            <label className="notify-option">
              <input
                type="checkbox"
                checked={form.sms_enabled}
                onChange={(event) => setForm((prev) => ({ ...prev, sms_enabled: event.target.checked }))}
              />
              SMS Aktif
            </label>
            <label>
              SMS Sağlayıcı
              <input
                value={form.sms_provider}
                onChange={(event) => setForm((prev) => ({ ...prev, sms_provider: event.target.value }))}
                placeholder="mock / netgsm / twilio"
              />
            </label>
            <label>
              SMS API URL
              <input
                value={form.sms_api_url}
                onChange={(event) => setForm((prev) => ({ ...prev, sms_api_url: event.target.value }))}
                placeholder="https://..."
              />
            </label>
            <label>
              SMS API Key
              <input
                value={form.sms_api_key}
                onChange={(event) => setForm((prev) => ({ ...prev, sms_api_key: event.target.value }))}
              />
            </label>
            <label>
              Test Telefon Numarası
              <input
                value={smsTestPhone}
                onChange={(event) => setSmsTestPhone(event.target.value)}
                placeholder="+90 5xx xxx xx xx"
              />
            </label>
            <button type="button" className="secondary-btn" onClick={handleSmsTest} disabled={testingSms}>
              {testingSms ? "SMS Test Ediliyor..." : "SMS Test Gönder"}
            </button>
          </div>

          <div className="notification-card">
            <h4>Telegram Bot</h4>
            <p className="helper-text" style={{ marginTop: 0 }}>
              Alarm kuralında "Telegram" işaretliyse, kural tetiklendiğinde
              aşağıdaki bot kanal/grup chat ID'lerine HTML mesaj gönderilir.
              Bot tokenini <code>@BotFather</code>'dan alabilirsiniz.
            </p>
            <label className="notify-option">
              <input
                type="checkbox"
                checked={form.telegram_enabled === true}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, telegram_enabled: event.target.checked }))
                }
              />
              Telegram Aktif
            </label>
            <label>
              Bot Token
              <input
                type="password"
                value={form.telegram_bot_token ?? ""}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, telegram_bot_token: event.target.value }))
                }
                placeholder="123456:ABC-DEF1234ghIkl..."
                autoComplete="off"
              />
            </label>
            <label>
              Chat ID Listesi
              <input
                value={form.telegram_chat_ids ?? ""}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, telegram_chat_ids: event.target.value }))
                }
                placeholder="-1001234567890, 123456789"
              />
              <small className="helper-text">
                Virgülle ayırın. Grup chat ID'leri "-100" ile başlar; kişisel
                chat ID'lerini <code>@userinfobot</code>'tan öğrenebilirsiniz.
              </small>
            </label>
            <label>
              Test Chat ID
              <input
                value={telegramTestChat}
                onChange={(event) => setTelegramTestChat(event.target.value)}
                placeholder="-1001234567890"
              />
            </label>
            <button
              type="button"
              className="secondary-btn"
              onClick={handleTelegramTest}
              disabled={testingTelegram || !onTestTelegram}
            >
              {testingTelegram ? "Telegram Test Ediliyor..." : "Telegram Test Gönder"}
            </button>
          </div>
        </div>

        {loading ? <p className="helper-text">Ayarlar yükleniyor...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {submitError ? <p className="error-text">{submitError}</p> : null}
        {testInfo ? <p className="helper-text">{testInfo}</p> : null}
        <div className="settings-actions">
          <button type="submit" disabled={saving}>
            {saving ? "Kaydediliyor..." : "Kaydet"}
          </button>
        </div>
      </form>
    </section>
  );
}
