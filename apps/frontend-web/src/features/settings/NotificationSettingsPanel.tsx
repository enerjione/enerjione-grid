import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

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
  sms_account_sid: "",
  sms_from_number: "",
  sms_twilio_use_whatsapp: false,
  sms_twilio_content_sid: "",
  sms_twilio_content_vars: "",
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
  const { t } = useTranslation();
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
    { key: "smtp", label: t("notifications.settings.channelEmail"), icon: "mail", enabled: form.smtp_enabled },
    { key: "sms", label: t("notifications.settings.channelSms"), icon: "sms", enabled: form.sms_enabled },
    {
      key: "telegram",
      label: t("notifications.settings.channelTelegram"),
      icon: "send",
      enabled: form.telegram_enabled === true
    }
  ];

  return (
    <section className="tab-panel notification-tab-panel">
      <div className="panel-head notification-panel-head">
        <div>
          <h3>{t("notifications.settings.title")}</h3>
          <p className="helper-text" style={{ margin: "4px 0 0 0" }}>
            {t("notifications.settings.subtitle")}
          </p>
        </div>
        <button
          type="button"
          className="primary-btn notification-save-top"
          disabled={saving}
          onClick={() => {
            void handleSubmit({
              preventDefault: () => undefined
            } as unknown as FormEvent<HTMLFormElement>);
          }}
        >
          <span className="material-symbols-outlined">save</span>
          {saving ? t("notifications.settings.saving") : t("notifications.settings.saveAll")}
        </button>
      </div>

      <div className="notif-status-row">
        {channels.map((ch) => (
          <div
            key={ch.key}
            className={`notif-status-pill ${ch.enabled ? "is-on" : "is-off"}`}
          >
            <span className="material-symbols-outlined">{ch.icon}</span>
            <span className="notif-status-label">{ch.label}</span>
            <span className="notif-status-state">{ch.enabled ? t("notifications.settings.channelActive") : t("notifications.settings.channelPassive")}</span>
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
                <h4>{t("notifications.settings.channelEmail")}</h4>
                <small>{t("notifications.settings.channelEmailSub")}</small>
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
                  <span>{t("notifications.settings.fields.smtpHost")}</span>
                  <input
                    value={form.smtp_host}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, smtp_host: event.target.value }))
                    }
                    placeholder={t("notifications.settings.fields.smtpHostPlaceholder")}
                  />
                </label>
                <label className="notif-field notif-field--narrow">
                  <span>{t("notifications.settings.fields.smtpPort")}</span>
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
                  <span>{t("notifications.settings.fields.smtpUser")}</span>
                  <input
                    value={form.smtp_username}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, smtp_username: event.target.value }))
                    }
                    autoComplete="off"
                    placeholder={t("notifications.settings.fields.smtpUserPlaceholder")}
                  />
                </label>
                <label className="notif-field notif-field--full">
                  <span>{t("notifications.settings.fields.smtpPassword")}</span>
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
                  <span>{t("notifications.settings.fields.smtpFrom")}</span>
                  <input
                    type="email"
                    value={form.smtp_from_email}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, smtp_from_email: event.target.value }))
                    }
                    placeholder={t("notifications.settings.fields.smtpFromPlaceholder")}
                  />
                </label>
              </div>
            </div>
            <div className="notification-card-test">
              <div className="notif-test-title">
                <span className="material-symbols-outlined">science</span>
                <span>{t("notifications.settings.testGroup")}</span>
              </div>
              <div className="notif-test-row">
                <input
                  type="email"
                  value={smtpTestEmail}
                  onChange={(event) => setSmtpTestEmail(event.target.value)}
                  placeholder={t("notifications.settings.fields.smtpTestPlaceholder")}
                />
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={handleSmtpTest}
                  disabled={testingSmtp}
                >
                  {testingSmtp ? t("notifications.settings.testSending") : t("notifications.settings.testBtn")}
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
                <h4>{t("notifications.settings.channelSms")}</h4>
                <small>{t("notifications.settings.channelSmsSub")}</small>
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
                  <span>{t("notifications.settings.fields.smsProvider")}</span>
                  <select
                    value={(form.sms_provider || "mock").toLowerCase()}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, sms_provider: event.target.value }))
                    }
                  >
                    <option value="mock">{t("notifications.settings.fields.smsProviderMock")}</option>
                    <option value="twilio">{t("notifications.settings.fields.smsProviderTwilio")}</option>
                    <option value="netgsm">{t("notifications.settings.fields.smsProviderNetgsm")}</option>
                    <option value="generic">{t("notifications.settings.fields.smsProviderGeneric")}</option>
                  </select>
                </label>
                {(form.sms_provider || "").toLowerCase() === "twilio" ? (
                  <>
                    <label className="notif-field notif-field--full">
                      <span>{t("notifications.settings.fields.twilioAccountSid")}</span>
                      <input
                        value={form.sms_account_sid ?? ""}
                        onChange={(event) =>
                          setForm((prev) => ({ ...prev, sms_account_sid: event.target.value }))
                        }
                        placeholder={t("notifications.settings.fields.twilioAccountSidPlaceholder")}
                        spellCheck={false}
                      />
                    </label>
                    <label className="notif-field notif-field--full">
                      <span>{t("notifications.settings.fields.twilioAuthToken")}</span>
                      <input
                        type="password"
                        value={form.sms_api_key}
                        onChange={(event) =>
                          setForm((prev) => ({ ...prev, sms_api_key: event.target.value }))
                        }
                        placeholder={t("notifications.settings.fields.twilioAuthTokenPlaceholder")}
                        autoComplete="new-password"
                      />
                    </label>
                    <label className="notif-field notif-field--full">
                      <span>{t("notifications.settings.fields.twilioFromNumber")}</span>
                      <input
                        value={form.sms_from_number ?? ""}
                        onChange={(event) =>
                          setForm((prev) => ({ ...prev, sms_from_number: event.target.value }))
                        }
                        placeholder={
                          form.sms_twilio_use_whatsapp
                            ? t("notifications.settings.fields.twilioWhatsappFromPlaceholder")
                            : t("notifications.settings.fields.twilioFromNumberPlaceholder")
                        }
                      />
                    </label>

                    {/* WhatsApp toggle + opsiyonel ContentSid */}
                    <div className="notif-field notif-field--full twilio-whatsapp-card">
                      <label className="twilio-whatsapp-toggle">
                        <input
                          type="checkbox"
                          checked={!!form.sms_twilio_use_whatsapp}
                          onChange={(event) =>
                            setForm((prev) => ({
                              ...prev,
                              sms_twilio_use_whatsapp: event.target.checked
                            }))
                          }
                        />
                        <span className="twilio-whatsapp-toggle-text">
                          <span className="material-symbols-outlined twilio-whatsapp-icon">
                            chat
                          </span>
                          <span>
                            <strong>{t("notifications.settings.fields.twilioUseWhatsapp")}</strong>
                            <small>{t("notifications.settings.fields.twilioUseWhatsappHint")}</small>
                          </span>
                        </span>
                      </label>
                      {form.sms_twilio_use_whatsapp ? (
                        <div className="twilio-whatsapp-body">
                          <label className="notif-field notif-field--full">
                            <span>{t("notifications.settings.fields.twilioContentSid")}</span>
                            <input
                              value={form.sms_twilio_content_sid ?? ""}
                              onChange={(event) =>
                                setForm((prev) => ({
                                  ...prev,
                                  sms_twilio_content_sid: event.target.value
                                }))
                              }
                              placeholder={t("notifications.settings.fields.twilioContentSidPlaceholder")}
                              spellCheck={false}
                            />
                            <small className="helper-text">
                              {t("notifications.settings.fields.twilioContentSidHint")}
                            </small>
                          </label>
                          {(form.sms_twilio_content_sid ?? "").trim() ? (
                            <label className="notif-field notif-field--full">
                              <span>{t("notifications.settings.fields.twilioContentVars")}</span>
                              <input
                                value={form.sms_twilio_content_vars ?? ""}
                                onChange={(event) =>
                                  setForm((prev) => ({
                                    ...prev,
                                    sms_twilio_content_vars: event.target.value
                                  }))
                                }
                                placeholder={t("notifications.settings.fields.twilioContentVarsPlaceholder")}
                                spellCheck={false}
                                style={{ fontFamily: "ui-monospace, monospace" }}
                              />
                              <small className="helper-text">
                                {t("notifications.settings.fields.twilioContentVarsHint")}
                              </small>
                            </label>
                          ) : null}
                        </div>
                      ) : null}
                    </div>

                    <p className="helper-text notif-field--full">
                      {form.sms_twilio_use_whatsapp
                        ? t("notifications.settings.fields.twilioWhatsappHint")
                        : t("notifications.settings.fields.twilioHint")}
                    </p>
                  </>
                ) : (form.sms_provider || "").toLowerCase() !== "mock" ? (
                  <>
                    <label className="notif-field notif-field--full">
                      <span>{t("notifications.settings.fields.smsApiUrl")}</span>
                      <input
                        value={form.sms_api_url}
                        onChange={(event) =>
                          setForm((prev) => ({ ...prev, sms_api_url: event.target.value }))
                        }
                        placeholder={t("notifications.settings.fields.smsApiUrlPlaceholder")}
                      />
                    </label>
                    <label className="notif-field notif-field--full">
                      <span>{t("notifications.settings.fields.smsApiKey")}</span>
                      <input
                        type="password"
                        value={form.sms_api_key}
                        onChange={(event) =>
                          setForm((prev) => ({ ...prev, sms_api_key: event.target.value }))
                        }
                        autoComplete="new-password"
                      />
                    </label>
                  </>
                ) : (
                  <p className="helper-text notif-field--full">
                    {t("notifications.settings.fields.smsProviderMockHint")}
                  </p>
                )}
              </div>
            </div>
            <div className="notification-card-test">
              <div className="notif-test-title">
                <span className="material-symbols-outlined">science</span>
                <span>{t("notifications.settings.testGroup")}</span>
              </div>
              <div className="notif-test-row">
                <input
                  value={smsTestPhone}
                  onChange={(event) => setSmsTestPhone(event.target.value)}
                  placeholder={t("notifications.settings.fields.smsTestPlaceholder")}
                />
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={handleSmsTest}
                  disabled={testingSms}
                >
                  {testingSms ? t("notifications.settings.testSending") : t("notifications.settings.testBtn")}
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
                <h4>{t("notifications.settings.channelTelegram")}</h4>
                <small>{t("notifications.settings.channelTelegramSub")}</small>
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
                  <span>{t("notifications.settings.fields.telegramBotToken")}</span>
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
                    {t("notifications.settings.fields.telegramBotTokenHint")}
                  </small>
                </label>
                <label className="notif-field notif-field--full">
                  <span>{t("notifications.settings.fields.telegramChatIds")}</span>
                  <input
                    value={form.telegram_chat_ids ?? ""}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, telegram_chat_ids: event.target.value }))
                    }
                    placeholder={t("notifications.settings.fields.telegramChatIdsPlaceholder")}
                  />
                  <small className="helper-text">
                    {t("notifications.settings.fields.telegramChatIdsHint")}
                  </small>
                </label>
              </div>
            </div>
            <div className="notification-card-test">
              <div className="notif-test-title">
                <span className="material-symbols-outlined">science</span>
                <span>{t("notifications.settings.testGroup")}</span>
              </div>
              <div className="notif-test-row">
                <input
                  value={telegramTestChat}
                  onChange={(event) => setTelegramTestChat(event.target.value)}
                  placeholder={t("notifications.settings.fields.telegramTestPlaceholder")}
                />
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={handleTelegramTest}
                  disabled={testingTelegram || !onTestTelegram}
                >
                  {testingTelegram ? t("notifications.settings.testSending") : t("notifications.settings.testBtn")}
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
            {t("notifications.settings.loading")}
          </p>
        ) : null}
        {error ? <p className="error-text">{error}</p> : null}
        {submitError ? <p className="error-text">{submitError}</p> : null}
      </form>
    </section>
  );
}
