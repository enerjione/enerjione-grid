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

type ChannelDef = {
  key: "smtp" | "sms" | "telegram";
  label: string;
  icon: string;
  enabled: boolean;
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
  // Test sonucu kanal-bazli ayri tutuluyor; her kart kendi banner'inda
  // basari/hata gosterir (tek "testInfo" string'i karistirici idi).
  const [testResult, setTestResult] = useState<
    { channel: "smtp" | "sms" | "telegram"; ok: boolean; detail: string } | null
  >(null);
  const [testingSmtp, setTestingSmtp] = useState(false);
  const [testingSms, setTestingSms] = useState(false);
  const [testingTelegram, setTestingTelegram] = useState(false);

  useEffect(() => {
    if (initialSettings) {
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
      setTestResult({ channel: "smtp", ok: false, detail: "Test için alıcı e-posta giriniz." });
      return;
    }
    setSubmitError("");
    setTestResult(null);
    setTestingSmtp(true);
    try {
      const result = await onTestSmtp({ recipient_email: smtpTestEmail.trim() });
      setTestResult({ channel: "smtp", ...result });
    } catch (err) {
      setTestResult({
        channel: "smtp",
        ok: false,
        detail: err instanceof Error ? err.message : "SMTP test başarısız."
      });
    } finally {
      setTestingSmtp(false);
    }
  };

  const handleSmsTest = async () => {
    if (!smsTestPhone.trim()) {
      setTestResult({ channel: "sms", ok: false, detail: "Test için telefon numarası giriniz." });
      return;
    }
    setSubmitError("");
    setTestResult(null);
    setTestingSms(true);
    try {
      const result = await onTestSms({ recipient_phone: smsTestPhone.trim() });
      setTestResult({ channel: "sms", ...result });
    } catch (err) {
      setTestResult({
        channel: "sms",
        ok: false,
        detail: err instanceof Error ? err.message : "SMS test başarısız."
      });
    } finally {
      setTestingSms(false);
    }
  };

  const handleTelegramTest = async () => {
    if (!onTestTelegram) return;
    const chat = telegramTestChat.trim();
    if (!chat) {
      setTestResult({ channel: "telegram", ok: false, detail: "Test için chat ID giriniz." });
      return;
    }
    setSubmitError("");
    setTestResult(null);
    setTestingTelegram(true);
    try {
      const result = await onTestTelegram({ chat_id: chat });
      setTestResult({ channel: "telegram", ...result });
    } catch (err) {
      setTestResult({
        channel: "telegram",
        ok: false,
        detail: err instanceof Error ? err.message : "Telegram test başarısız."
      });
    } finally {
      setTestingTelegram(false);
    }
  };

  const channels: ChannelDef[] = [
    { key: "smtp", label: "E-posta (SMTP)", icon: "mail", enabled: form.smtp_enabled },
    { key: "sms", label: "SMS", icon: "sms", enabled: form.sms_enabled },
    {
      key: "telegram",
      label: "Telegram Bot",
      icon: "send",
      enabled: form.telegram_enabled === true
    }
  ];

  return (
    <section className="tab-panel notification-tab-panel">
      <div className="panel-head notification-panel-head">
        <div>
          <h3>Bildirim Ayarları</h3>
          <p className="helper-text" style={{ margin: "4px 0 0 0" }}>
            Sistem geneli bildirim kanalları. Bir alarm kuralı tetiklendiğinde
            kuraldaki "E-posta / SMS / Telegram" seçenekleri bu sayfada
            <strong> aktif</strong> olan kanallardan gönderilir.
          </p>
        </div>
        <button
          type="button"
          className="primary-btn notification-save-top"
          disabled={saving}
          onClick={() => {
            // Form submit'i tetikle (form ref yerine event dispatch)
            void handleSubmit({
              preventDefault: () => undefined
            } as unknown as FormEvent<HTMLFormElement>);
          }}
        >
          <span className="material-symbols-outlined">save</span>
          {saving ? "Kaydediliyor..." : "Tüm Ayarları Kaydet"}
        </button>
      </div>

      {/* Kanal durum ozeti — kullanici tek bakista hangi kanal acik gorur */}
      <div className="notif-status-row">
        {channels.map((ch) => (
          <div
            key={ch.key}
            className={`notif-status-pill ${ch.enabled ? "is-on" : "is-off"}`}
          >
            <span className="material-symbols-outlined">{ch.icon}</span>
            <span className="notif-status-label">{ch.label}</span>
            <span className="notif-status-state">{ch.enabled ? "Aktif" : "Pasif"}</span>
          </div>
        ))}
      </div>

      <form className="notification-settings-panel notification-form-v2" onSubmit={handleSubmit}>
        <div className="notification-cards-grid">
          {/* ============ SMTP / E-posta ============ */}
          <article className={`notification-card-v2 ${form.smtp_enabled ? "is-active" : ""}`}>
            <header className="notification-card-head">
              <div className="notification-card-icon">
                <span className="material-symbols-outlined">mail</span>
              </div>
              <div className="notification-card-titles">
                <h4>E-posta (SMTP)</h4>
                <small>HTML şablonlu alarm bildirim e-postaları.</small>
              </div>
              <label className="notif-toggle">
                <input
                  type="checkbox"
                  checked={form.smtp_enabled}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, smtp_enabled: event.target.checked }))
                  }
                />
                <span className="notif-toggle-slider" />
              </label>
            </header>
            <div className="notification-card-body">
              <div className="notif-field-grid">
                <label className="notif-field">
                  <span>SMTP Sunucu</span>
                  <input
                    value={form.smtp_host}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, smtp_host: event.target.value }))
                    }
                    placeholder="smtp.ornek.com"
                  />
                </label>
                <label className="notif-field notif-field--narrow">
                  <span>Port</span>
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={form.smtp_port}
                    onChange={(event) =>
                      setForm((prev) => ({
                        ...prev,
                        smtp_port: Number(event.target.value) || 0
                      }))
                    }
                  />
                </label>
                <label className="notif-field notif-field--full">
                  <span>Kullanıcı Adı</span>
                  <input
                    value={form.smtp_username}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, smtp_username: event.target.value }))
                    }
                    autoComplete="off"
                    placeholder="kullanici@firma.com"
                  />
                </label>
                <label className="notif-field notif-field--full">
                  <span>Şifre</span>
                  <input
                    type="password"
                    value={form.smtp_password}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, smtp_password: event.target.value }))
                    }
                    autoComplete="new-password"
                    placeholder="••••••••"
                  />
                </label>
                <label className="notif-field notif-field--full">
                  <span>Gönderen E-posta</span>
                  <input
                    type="email"
                    value={form.smtp_from_email}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, smtp_from_email: event.target.value }))
                    }
                    placeholder="alarm@firma.com"
                  />
                </label>
              </div>
            </div>
            <div className="notification-card-test">
              <div className="notif-test-title">
                <span className="material-symbols-outlined">science</span>
                <span>Test Gönder</span>
              </div>
              <div className="notif-test-row">
                <input
                  type="email"
                  value={smtpTestEmail}
                  onChange={(event) => setSmtpTestEmail(event.target.value)}
                  placeholder="test@firma.com"
                />
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={handleSmtpTest}
                  disabled={testingSmtp}
                >
                  {testingSmtp ? "Gönderiliyor..." : "Test"}
                </button>
              </div>
              {testResult && testResult.channel === "smtp" ? (
                <div
                  className={`notif-test-result ${testResult.ok ? "is-ok" : "is-fail"}`}
                  role="status"
                >
                  <span className="material-symbols-outlined">
                    {testResult.ok ? "check_circle" : "error"}
                  </span>
                  {testResult.detail}
                </div>
              ) : null}
            </div>
          </article>

          {/* ============ SMS ============ */}
          <article className={`notification-card-v2 ${form.sms_enabled ? "is-active" : ""}`}>
            <header className="notification-card-head">
              <div className="notification-card-icon">
                <span className="material-symbols-outlined">sms</span>
              </div>
              <div className="notification-card-titles">
                <h4>SMS</h4>
                <small>Telefon numarası kayıtlı kullanıcılara kısa mesaj.</small>
              </div>
              <label className="notif-toggle">
                <input
                  type="checkbox"
                  checked={form.sms_enabled}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, sms_enabled: event.target.checked }))
                  }
                />
                <span className="notif-toggle-slider" />
              </label>
            </header>
            <div className="notification-card-body">
              <div className="notif-field-grid">
                <label className="notif-field notif-field--full">
                  <span>SMS Sağlayıcı</span>
                  <input
                    value={form.sms_provider}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, sms_provider: event.target.value }))
                    }
                    placeholder="mock / netgsm / twilio"
                  />
                </label>
                <label className="notif-field notif-field--full">
                  <span>API URL</span>
                  <input
                    value={form.sms_api_url}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, sms_api_url: event.target.value }))
                    }
                    placeholder="https://api.netgsm.com.tr/sms/send"
                  />
                </label>
                <label className="notif-field notif-field--full">
                  <span>API Key</span>
                  <input
                    type="password"
                    value={form.sms_api_key}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, sms_api_key: event.target.value }))
                    }
                    autoComplete="new-password"
                  />
                </label>
              </div>
            </div>
            <div className="notification-card-test">
              <div className="notif-test-title">
                <span className="material-symbols-outlined">science</span>
                <span>Test Gönder</span>
              </div>
              <div className="notif-test-row">
                <input
                  value={smsTestPhone}
                  onChange={(event) => setSmsTestPhone(event.target.value)}
                  placeholder="+90 5xx xxx xx xx"
                />
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={handleSmsTest}
                  disabled={testingSms}
                >
                  {testingSms ? "Gönderiliyor..." : "Test"}
                </button>
              </div>
              {testResult && testResult.channel === "sms" ? (
                <div
                  className={`notif-test-result ${testResult.ok ? "is-ok" : "is-fail"}`}
                  role="status"
                >
                  <span className="material-symbols-outlined">
                    {testResult.ok ? "check_circle" : "error"}
                  </span>
                  {testResult.detail}
                </div>
              ) : null}
            </div>
          </article>

          {/* ============ Telegram ============ */}
          <article
            className={`notification-card-v2 ${form.telegram_enabled === true ? "is-active" : ""}`}
          >
            <header className="notification-card-head">
              <div className="notification-card-icon">
                <span className="material-symbols-outlined">send</span>
              </div>
              <div className="notification-card-titles">
                <h4>Telegram Bot</h4>
                <small>Bot ile kanal/grup chat'lerine HTML mesaj.</small>
              </div>
              <label className="notif-toggle">
                <input
                  type="checkbox"
                  checked={form.telegram_enabled === true}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, telegram_enabled: event.target.checked }))
                  }
                />
                <span className="notif-toggle-slider" />
              </label>
            </header>
            <div className="notification-card-body">
              <div className="notif-field-grid">
                <label className="notif-field notif-field--full">
                  <span>Bot Token</span>
                  <input
                    type="password"
                    value={form.telegram_bot_token ?? ""}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, telegram_bot_token: event.target.value }))
                    }
                    placeholder="123456:ABC-DEF1234ghIkl..."
                    autoComplete="off"
                  />
                  <small className="helper-text">
                    Token <code>@BotFather</code>'dan alınır. Boş bırakılırsa Telegram
                    bildirimi devre dışıdır.
                  </small>
                </label>
                <label className="notif-field notif-field--full">
                  <span>Chat ID Listesi</span>
                  <input
                    value={form.telegram_chat_ids ?? ""}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, telegram_chat_ids: event.target.value }))
                    }
                    placeholder="-1001234567890, 123456789"
                  />
                  <small className="helper-text">
                    Virgülle ayırın. Grup ID'leri <code>-100</code> ile başlar; kişisel
                    chat ID'leri <code>@userinfobot</code>'tan öğrenilir.
                  </small>
                </label>
              </div>
            </div>
            <div className="notification-card-test">
              <div className="notif-test-title">
                <span className="material-symbols-outlined">science</span>
                <span>Test Gönder</span>
              </div>
              <div className="notif-test-row">
                <input
                  value={telegramTestChat}
                  onChange={(event) => setTelegramTestChat(event.target.value)}
                  placeholder="-1001234567890"
                />
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={handleTelegramTest}
                  disabled={testingTelegram || !onTestTelegram}
                >
                  {testingTelegram ? "Gönderiliyor..." : "Test"}
                </button>
              </div>
              {testResult && testResult.channel === "telegram" ? (
                <div
                  className={`notif-test-result ${testResult.ok ? "is-ok" : "is-fail"}`}
                  role="status"
                >
                  <span className="material-symbols-outlined">
                    {testResult.ok ? "check_circle" : "error"}
                  </span>
                  {testResult.detail}
                </div>
              ) : null}
            </div>
          </article>
        </div>

        {loading ? (
          <p className="helper-text" style={{ textAlign: "center" }}>
            Ayarlar yükleniyor...
          </p>
        ) : null}
        {error ? <p className="error-text">{error}</p> : null}
        {submitError ? <p className="error-text">{submitError}</p> : null}
      </form>
    </section>
  );
}
