import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { DeviceRow, NotificationSettings, OutboundTarget, WhatsappWebGroup } from "../../shared/types";
import {
  OutboundTargetsPanel,
  type OutboundTargetCreatePayload,
  type OutboundTargetUpdatePayload
} from "../outbound/OutboundTargetsPanel";

export type DiscoveredChat = { id: string; type: string; title: string };

type Props = {
  initialSettings: NotificationSettings | null;
  loading: boolean;
  saving: boolean;
  error: string;
  outboundTargets?: OutboundTarget[];
  devices?: DeviceRow[];
  accessToken?: string;
  onCreateWebhook?: (payload: OutboundTargetCreatePayload) => Promise<OutboundTarget | undefined>;
  onUpdateWebhook?: (targetId: number, payload: OutboundTargetUpdatePayload) => Promise<void>;
  onDeleteWebhook?: (targetId: number) => Promise<void>;
  onSave: (payload: NotificationSettings) => Promise<void>;
  onTestSmtp: (payload: { recipient_email: string; subject?: string; message?: string }) => Promise<{ ok: boolean; detail: string }>;
  onTestSms: (payload: { recipient_phone: string; message?: string }) => Promise<{ ok: boolean; detail: string }>;
  /** Telegram bot test gonderimi. Alarm akisindaki bot tokeni ve verilen
   *  chat_id ile sade bir test mesaji yollar. */
  onTestTelegram?: (payload: { chat_id: string; message?: string }) => Promise<{ ok: boolean; detail: string }>;
  /** Telegram bot'a yazmis chat'leri otomatik tespit eder. Opsiyonel
   *  bot_token verilirse o kullanilir, aksi halde kayitli token. */
  onDiscoverTelegramChats?: (
    payload?: { bot_token?: string }
  ) => Promise<{ ok: boolean; detail: string; chats: DiscoveredChat[] }>;
  /** WhatsApp Web (Baileys sidecar) baglanti durumunu getirir — polling icin. */
  onFetchWhatsappWebStatus?: () => Promise<{ status: string; phone_number: string | null }>;
  onFetchWhatsappWebQr?: () => Promise<{ qr: string | null }>;
  onFetchWhatsappWebGroups?: () => Promise<{ groups: WhatsappWebGroup[] }>;
  onTestWhatsappWeb?: (payload: { recipient_phone: string; message?: string }) => Promise<{ ok: boolean; detail: string }>;
  onLogoutWhatsappWeb?: () => Promise<{ ok: boolean; detail: string }>;
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
  whatsapp_web_enabled: false,
  whatsapp_web_group_jids: "",
  whatsapp_web_group_mode: false,
  telegram_enabled: false,
  telegram_bot_token: "",
  telegram_chat_ids: ""
};

type ChannelKey = "smtp" | "sms" | "telegram" | "webhook" | "whatsapp";
type TestChannel = "smtp" | "sms" | "telegram" | "whatsapp";
type ChannelDef = {
  key: ChannelKey;
  label: string;
  subtitle: string;
  icon: string;
  enabled: boolean;
  passive?: boolean;
};

export function NotificationSettingsPanel({
  initialSettings,
  loading,
  saving,
  error,
  outboundTargets = [],
  devices = [],
  accessToken,
  onCreateWebhook,
  onUpdateWebhook,
  onDeleteWebhook,
  onSave,
  onTestSmtp,
  onTestSms,
  onTestTelegram,
  onDiscoverTelegramChats,
  onFetchWhatsappWebStatus,
  onFetchWhatsappWebQr,
  onFetchWhatsappWebGroups,
  onTestWhatsappWeb,
  onLogoutWhatsappWeb
}: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<NotificationSettings>(EMPTY_SETTINGS);
  const [activeChannel, setActiveChannel] = useState<ChannelKey>("smtp");
  const [submitError, setSubmitError] = useState("");
  const [smtpTestEmail, setSmtpTestEmail] = useState("");
  const [smsTestPhone, setSmsTestPhone] = useState("");
  const [whatsappTestPhone, setWhatsappTestPhone] = useState("");
  const [telegramTestChat, setTelegramTestChat] = useState("");
  const [discoveringChats, setDiscoveringChats] = useState(false);
  const [discoveredChats, setDiscoveredChats] = useState<DiscoveredChat[] | null>(null);
  const [discoverDetail, setDiscoverDetail] = useState("");
  const [discoverError, setDiscoverError] = useState("");
  const [testResult, setTestResult] = useState<
    { channel: TestChannel; ok: boolean; detail: string } | null
  >(null);
  const [testingSmtp, setTestingSmtp] = useState(false);
  const [testingSms, setTestingSms] = useState(false);
  const [testingWhatsapp, setTestingWhatsapp] = useState(false);
  const [testingTelegram, setTestingTelegram] = useState(false);
  const [whatsappStatus, setWhatsappStatus] = useState<{ status: string; phone_number: string | null } | null>(null);
  const [whatsappQr, setWhatsappQr] = useState<string | null>(null);
  const [whatsappGroups, setWhatsappGroups] = useState<WhatsappWebGroup[]>([]);
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [loggingOutWhatsapp, setLoggingOutWhatsapp] = useState(false);

  useEffect(() => {
    if (initialSettings) {
      setForm({
        ...EMPTY_SETTINGS,
        ...initialSettings,
        telegram_enabled: initialSettings.telegram_enabled ?? false,
        telegram_bot_token: initialSettings.telegram_bot_token ?? "",
        telegram_chat_ids: initialSettings.telegram_chat_ids ?? "",
        whatsapp_web_group_jids: initialSettings.whatsapp_web_group_jids ?? "",
        whatsapp_web_group_mode: initialSettings.whatsapp_web_group_mode ?? false
      });
    }
  }, [initialSettings]);

  useEffect(() => {
    if (activeChannel !== "whatsapp" || !onFetchWhatsappWebStatus) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await onFetchWhatsappWebStatus();
        if (cancelled) return;
        setWhatsappStatus(status);
        if (status.status === "qr_pending" && onFetchWhatsappWebQr) {
          const qr = await onFetchWhatsappWebQr();
          if (!cancelled) setWhatsappQr(qr.qr);
        } else if (!cancelled) {
          setWhatsappQr(null);
        }
      } catch {
        if (!cancelled) setWhatsappStatus({ status: "disconnected", phone_number: null });
      }
    };
    void poll();
    const interval = setInterval(() => void poll(), 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [activeChannel, onFetchWhatsappWebStatus, onFetchWhatsappWebQr]);

  const refreshWhatsappGroups = useCallback(async () => {
    if (!onFetchWhatsappWebGroups) return;
    setLoadingGroups(true);
    try {
      const result = await onFetchWhatsappWebGroups();
      setWhatsappGroups(result.groups);
    } catch {
      setWhatsappGroups([]);
    } finally {
      setLoadingGroups(false);
    }
  }, [onFetchWhatsappWebGroups]);

  useEffect(() => {
    if (activeChannel !== "whatsapp" || whatsappStatus?.status !== "connected") return;
    void refreshWhatsappGroups();
  }, [activeChannel, whatsappStatus?.status, refreshWhatsappGroups]);

  const channels = useMemo<ChannelDef[]>(
    () => [
      {
        key: "smtp",
        label: t("notifications.settings.channelEmail"),
        subtitle: t("notifications.settings.channelEmailSub"),
        icon: "mail",
        enabled: form.smtp_enabled
      },
      {
        key: "sms",
        label: t("notifications.settings.channelSms"),
        subtitle: t("notifications.settings.channelSmsSub"),
        icon: "sms",
        enabled: form.sms_enabled
      },
      {
        key: "telegram",
        label: t("notifications.settings.channelTelegram"),
        subtitle: t("notifications.settings.channelTelegramSub"),
        icon: "send",
        enabled: form.telegram_enabled === true
      },
      {
        key: "webhook",
        label: t("notifications.settings.channelWebhook"),
        subtitle: t("notifications.settings.channelWebhookSub"),
        icon: "hub",
        enabled: outboundTargets.some((target) => target.protocol === "rest" && target.is_active)
      },
      {
        key: "whatsapp",
        label: t("notifications.settings.channelWhatsapp"),
        subtitle: t("notifications.settings.channelWhatsappSub"),
        icon: "chat",
        enabled: !!form.whatsapp_web_enabled
      }
    ],
    [
      form.sms_enabled,
      form.whatsapp_web_enabled,
      form.smtp_enabled,
      form.telegram_enabled,
      outboundTargets,
      t
    ]
  );

  const activeDef = channels.find((channel) => channel.key === activeChannel) ?? channels[0];

  const handleSave = async () => {
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

  const handleWhatsappTest = async () => {
    if (!onTestWhatsappWeb) return;
    if (!whatsappTestPhone.trim()) {
      setTestResult({ channel: "whatsapp", ok: false, detail: "Test için telefon numarası giriniz." });
      return;
    }
    setSubmitError("");
    setTestResult(null);
    setTestingWhatsapp(true);
    try {
      const result = await onTestWhatsappWeb({ recipient_phone: whatsappTestPhone.trim() });
      setTestResult({ channel: "whatsapp", ...result });
    } catch (err) {
      setTestResult({
        channel: "whatsapp",
        ok: false,
        detail: err instanceof Error ? err.message : "WhatsApp test başarısız."
      });
    } finally {
      setTestingWhatsapp(false);
    }
  };

  const handleWhatsappLogout = async () => {
    if (!onLogoutWhatsappWeb) return;
    setLoggingOutWhatsapp(true);
    try {
      await onLogoutWhatsappWeb();
      setWhatsappStatus({ status: "disconnected", phone_number: null });
      setWhatsappQr(null);
    } catch (err) {
      setTestResult({
        channel: "whatsapp",
        ok: false,
        detail: err instanceof Error ? err.message : "Bağlantı kesilemedi."
      });
    } finally {
      setLoggingOutWhatsapp(false);
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

  const handleDiscoverChats = async () => {
    if (!onDiscoverTelegramChats) return;
    setDiscoveringChats(true);
    setDiscoverError("");
    setDiscoverDetail("");
    try {
      const result = await onDiscoverTelegramChats({
        bot_token: (form.telegram_bot_token ?? "").trim() || undefined
      });
      if (!result.ok) {
        setDiscoverError(result.detail);
        setDiscoveredChats([]);
      } else {
        setDiscoveredChats(result.chats);
        setDiscoverDetail(result.detail);
      }
    } catch (err) {
      setDiscoverError(
        err instanceof Error ? err.message : t("notifications.settings.fields.telegramDiscoverFail")
      );
    } finally {
      setDiscoveringChats(false);
    }
  };

  const addChatIdToList = (chatId: string) => {
    const current = (form.telegram_chat_ids ?? "").trim();
    const existing = new Set(
      current
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    );
    if (existing.has(chatId)) return;
    existing.add(chatId);
    setForm((prev) => ({ ...prev, telegram_chat_ids: Array.from(existing).join(", ") }));
  };

  const whatsappJidList = (form.whatsapp_web_group_jids ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const toggleGroupSelection = (jid: string) => {
    const next = whatsappJidList.includes(jid)
      ? whatsappJidList.filter((item) => item !== jid)
      : [...whatsappJidList, jid];
    setForm((prev) => ({ ...prev, whatsapp_web_group_jids: next.join(", ") }));
  };

  const renderTestResult = (channel: TestChannel) =>
    testResult && testResult.channel === channel ? (
      <div className={`notif-test-result ${testResult.ok ? "is-ok" : "is-fail"}`} role="status">
        <span className="material-symbols-outlined">
          {testResult.ok ? "check_circle" : "error"}
        </span>
        {testResult.detail}
      </div>
    ) : null;

  const renderSmtpPanel = () => (
    <>
      <div className="notification-detail-section">
        <div className="notification-content-card">
          <div className="notification-content-head">
            <span className="material-symbols-outlined">dns</span>
            <div>
              <h4>{t("notifications.settings.smtp.title")}</h4>
              <p>{t("notifications.settings.content.smtpDesc")}</p>
            </div>
          </div>
          <div className="notif-field-grid notification-detail-grid">
          <label className="notif-field">
            <span>{t("notifications.settings.fields.smtpHost")}</span>
            <input
              value={form.smtp_host}
              onChange={(event) => setForm((prev) => ({ ...prev, smtp_host: event.target.value }))}
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
                setForm((prev) => ({ ...prev, smtp_port: Number(event.target.value) || 0 }))
              }
            />
          </label>
          <label className="notif-field notif-field--full">
            <span>{t("notifications.settings.fields.smtpUser")}</span>
            <input
              value={form.smtp_username}
              onChange={(event) => setForm((prev) => ({ ...prev, smtp_username: event.target.value }))}
              autoComplete="off"
              placeholder={t("notifications.settings.fields.smtpUserPlaceholder")}
            />
          </label>
          <label className="notif-field notif-field--full">
            <span>{t("notifications.settings.fields.smtpPassword")}</span>
            <input
              type="password"
              value={form.smtp_password}
              onChange={(event) => setForm((prev) => ({ ...prev, smtp_password: event.target.value }))}
              autoComplete="new-password"
              placeholder="••••••••"
            />
          </label>
          <label className="notif-field notif-field--full">
            <span>{t("notifications.settings.fields.smtpFrom")}</span>
            <input
              type="email"
              value={form.smtp_from_email}
              onChange={(event) => setForm((prev) => ({ ...prev, smtp_from_email: event.target.value }))}
              placeholder={t("notifications.settings.fields.smtpFromPlaceholder")}
            />
          </label>
          </div>
        </div>
      </div>
      <div className="notification-card-test notification-detail-test">
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
          <button type="button" className="secondary-btn" onClick={handleSmtpTest} disabled={testingSmtp}>
            {testingSmtp ? t("notifications.settings.testSending") : t("notifications.settings.testBtn")}
          </button>
        </div>
        {renderTestResult("smtp")}
      </div>
    </>
  );

  const renderSmsPanel = () => (
    <>
      <div className="notification-detail-section">
        <div className="notification-content-card">
          <div className="notification-content-head">
            <span className="material-symbols-outlined">sms</span>
            <div>
              <h4>{t("notifications.settings.sms.title")}</h4>
              <p>{t("notifications.settings.content.smsDesc")}</p>
            </div>
          </div>
          <div className="notif-field-grid notification-detail-grid">
          <label className="notif-field notif-field--full">
            <span>{t("notifications.settings.fields.smsProvider")}</span>
            <select
              value={(form.sms_provider || "mock").toLowerCase()}
              onChange={(event) => setForm((prev) => ({ ...prev, sms_provider: event.target.value }))}
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
                  onChange={(event) => setForm((prev) => ({ ...prev, sms_account_sid: event.target.value }))}
                  placeholder={t("notifications.settings.fields.twilioAccountSidPlaceholder")}
                  spellCheck={false}
                />
              </label>
              <label className="notif-field notif-field--full">
                <span>{t("notifications.settings.fields.twilioAuthToken")}</span>
                <input
                  type="password"
                  value={form.sms_api_key}
                  onChange={(event) => setForm((prev) => ({ ...prev, sms_api_key: event.target.value }))}
                  placeholder={t("notifications.settings.fields.twilioAuthTokenPlaceholder")}
                  autoComplete="new-password"
                />
              </label>
              <label className="notif-field notif-field--full">
                <span>{t("notifications.settings.fields.twilioFromNumber")}</span>
                <input
                  value={form.sms_from_number ?? ""}
                  onChange={(event) => setForm((prev) => ({ ...prev, sms_from_number: event.target.value }))}
                  placeholder={t("notifications.settings.fields.twilioFromNumberPlaceholder")}
                />
              </label>
              <p className="helper-text notif-field--full">{t("notifications.settings.fields.twilioHint")}</p>
            </>
          ) : (form.sms_provider || "").toLowerCase() !== "mock" ? (
            <>
              <label className="notif-field notif-field--full">
                <span>{t("notifications.settings.fields.smsApiUrl")}</span>
                <input
                  value={form.sms_api_url}
                  onChange={(event) => setForm((prev) => ({ ...prev, sms_api_url: event.target.value }))}
                  placeholder={t("notifications.settings.fields.smsApiUrlPlaceholder")}
                />
              </label>
              <label className="notif-field notif-field--full">
                <span>{t("notifications.settings.fields.smsApiKey")}</span>
                <input
                  type="password"
                  value={form.sms_api_key}
                  onChange={(event) => setForm((prev) => ({ ...prev, sms_api_key: event.target.value }))}
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
      </div>
      <div className="notification-card-test notification-detail-test">
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
          <button type="button" className="secondary-btn" onClick={handleSmsTest} disabled={testingSms}>
            {testingSms ? t("notifications.settings.testSending") : t("notifications.settings.testBtn")}
          </button>
        </div>
        {renderTestResult("sms")}
      </div>
    </>
  );

  const renderWhatsappPanel = () => {
    const status = whatsappStatus?.status ?? "disconnected";
    return (
      <>
        <div className="notification-detail-section">
          <p className="helper-text whatsapp-panel-desc">{t("notifications.settings.content.whatsappDesc")}</p>
          <div className="whatsapp-qr-panel">
            {status === "connected" ? (
              <div className="whatsapp-qr-connected">
                <span className="material-symbols-outlined">check_circle</span>
                <div>
                  <strong>{t("notifications.settings.fields.whatsappConnected")}</strong>
                  <small>{whatsappStatus?.phone_number ?? ""}</small>
                </div>
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={() => void handleWhatsappLogout()}
                  disabled={loggingOutWhatsapp || !onLogoutWhatsappWeb}
                >
                  {loggingOutWhatsapp
                    ? t("notifications.settings.fields.whatsappLoggingOut")
                    : t("notifications.settings.fields.whatsappLogoutBtn")}
                </button>
              </div>
            ) : status === "qr_pending" && whatsappQr ? (
              <div className="whatsapp-qr-pending">
                <p>{t("notifications.settings.fields.whatsappQrHint")}</p>
                <img src={whatsappQr} alt={t("notifications.settings.fields.whatsappQrTitle")} />
              </div>
            ) : (
              <div className="whatsapp-qr-waiting">
                <span className="material-symbols-outlined">hourglass_top</span>
                <p>{t("notifications.settings.fields.whatsappDisconnected")}</p>
              </div>
            )}
          </div>
        </div>
        <div className="notification-detail-section">
          <div className="notification-content-card">
            <div className="notification-content-head">
              <span className="material-symbols-outlined">forum</span>
              <div>
                <h4>{t("notifications.settings.fields.whatsappGroupModeLabel")}</h4>
                <p>
                  {form.whatsapp_web_group_mode
                    ? t("notifications.settings.fields.whatsappGroupModeHint")
                    : t("notifications.settings.fields.whatsappPersonalModeHint")}
                </p>
              </div>
              <label
                className="notif-toggle"
                style={{ marginLeft: "auto" }}
                title={t("notifications.settings.fields.whatsappGroupModeLabel")}
              >
                <input
                  type="checkbox"
                  checked={form.whatsapp_web_group_mode}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, whatsapp_web_group_mode: event.target.checked }))
                  }
                />
                <span className="notif-toggle-slider" />
              </label>
            </div>

            {form.whatsapp_web_group_mode && status === "connected" ? (
              <div className="telegram-discover-card">
                <div className="telegram-discover-head">
                  <span className="material-symbols-outlined">groups</span>
                  <div className="telegram-discover-head-text">
                    <strong>{t("notifications.settings.fields.whatsappGroupListTitle")}</strong>
                    <small>
                      {t("notifications.settings.fields.whatsappGroupSelectedCount", {
                        count: whatsappJidList.length
                      })}
                    </small>
                  </div>
                  <button
                    type="button"
                    className="primary-btn telegram-discover-btn"
                    onClick={() => void refreshWhatsappGroups()}
                    disabled={loadingGroups}
                  >
                    <span className="material-symbols-outlined">
                      {loadingGroups ? "hourglass_top" : "refresh"}
                    </span>
                    {t("notifications.settings.fields.whatsappGroupListRefresh")}
                  </button>
                </div>
                {whatsappGroups.length === 0 ? (
                  <div className="telegram-discover-empty">
                    <span className="material-symbols-outlined">info</span>
                    {t("notifications.settings.fields.whatsappGroupListEmpty")}
                  </div>
                ) : (
                  <ul className="telegram-discover-list">
                    {whatsappGroups.map((group) => {
                      const isSelected = whatsappJidList.includes(group.jid);
                      return (
                        <li key={group.jid} className="telegram-discover-item">
                          <span className="telegram-discover-type" title="group">
                            👥
                          </span>
                          <div className="telegram-discover-info">
                            <strong>{group.name}</strong>
                            <code>{group.participants} üye</code>
                          </div>
                          <button
                            type="button"
                            className={`telegram-discover-add ${isSelected ? "is-added" : ""}`}
                            onClick={() => toggleGroupSelection(group.jid)}
                          >
                            <span className="material-symbols-outlined">
                              {isSelected ? "check" : "add"}
                            </span>
                            {isSelected
                              ? t("notifications.settings.fields.telegramDiscoverAdded")
                              : t("notifications.settings.fields.telegramDiscoverAdd")}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            ) : null}
          </div>
        </div>
        <div className="notification-card-test notification-detail-test">
          <div className="notif-test-title">
            <span className="material-symbols-outlined">science</span>
            <span>{t("notifications.settings.testGroup")}</span>
          </div>
          <div className="notif-test-row">
            <input
              value={whatsappTestPhone}
              onChange={(event) => setWhatsappTestPhone(event.target.value)}
              placeholder={t("notifications.settings.fields.smsTestPlaceholder")}
            />
            <button
              type="button"
              className="secondary-btn"
              onClick={() => void handleWhatsappTest()}
              disabled={testingWhatsapp || status !== "connected"}
            >
              {testingWhatsapp ? t("notifications.settings.testSending") : t("notifications.settings.testBtn")}
            </button>
          </div>
          {renderTestResult("whatsapp")}
        </div>
      </>
    );
  };

  const renderTelegramPanel = () => (
    <>
      <div className="notification-detail-section">
        <div className="notification-content-card">
          <div className="notification-content-head">
            <span className="material-symbols-outlined">send</span>
            <div>
              <h4>{t("notifications.settings.telegram.title")}</h4>
              <p>{t("notifications.settings.content.telegramDesc")}</p>
            </div>
          </div>
          <div className="notif-field-grid notification-detail-grid">
          <label className="notif-field notif-field--full">
            <span>{t("notifications.settings.fields.telegramBotToken")}</span>
            <input
              type="password"
              value={form.telegram_bot_token ?? ""}
              onChange={(event) => setForm((prev) => ({ ...prev, telegram_bot_token: event.target.value }))}
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
              onChange={(event) => setForm((prev) => ({ ...prev, telegram_chat_ids: event.target.value }))}
              placeholder={t("notifications.settings.fields.telegramChatIdsPlaceholder")}
            />
            <small className="helper-text">{t("notifications.settings.fields.telegramChatIdsHint")}</small>
          </label>
          {onDiscoverTelegramChats ? (
            <div className="notif-field notif-field--full telegram-discover-card">
              <div className="telegram-discover-head">
                <span className="material-symbols-outlined">auto_fix_high</span>
                <div className="telegram-discover-head-text">
                  <strong>{t("notifications.settings.fields.telegramDiscoverTitle")}</strong>
                  <small>{t("notifications.settings.fields.telegramDiscoverDesc")}</small>
                </div>
                <button
                  type="button"
                  className="primary-btn telegram-discover-btn"
                  onClick={() => void handleDiscoverChats()}
                  disabled={discoveringChats || !(form.telegram_bot_token ?? "").trim()}
                >
                  <span className="material-symbols-outlined">
                    {discoveringChats ? "hourglass_top" : "search"}
                  </span>
                  {discoveringChats
                    ? t("notifications.settings.fields.telegramDiscovering")
                    : t("notifications.settings.fields.telegramDiscoverBtn")}
                </button>
              </div>
              {discoverError ? (
                <div className="telegram-discover-error" role="alert">
                  <span className="material-symbols-outlined">error</span>
                  {discoverError}
                </div>
              ) : null}
              {discoveredChats !== null && discoveredChats.length === 0 && !discoverError ? (
                <div className="telegram-discover-empty">
                  <span className="material-symbols-outlined">info</span>
                  {discoverDetail || t("notifications.settings.fields.telegramDiscoverEmpty")}
                </div>
              ) : null}
              {discoveredChats && discoveredChats.length > 0 ? (
                <>
                  <div className="telegram-discover-detail">{discoverDetail}</div>
                  <ul className="telegram-discover-list">
                    {discoveredChats.map((chat) => {
                      const alreadyAdded = (form.telegram_chat_ids ?? "")
                        .split(",")
                        .map((s) => s.trim())
                        .includes(chat.id);
                      return (
                        <li key={chat.id} className="telegram-discover-item">
                          <span
                            className={`telegram-discover-type telegram-discover-type--${chat.type}`}
                            title={chat.type}
                          >
                            {chat.type === "group" || chat.type === "supergroup"
                              ? "👥"
                              : chat.type === "channel"
                              ? "📢"
                              : "👤"}
                          </span>
                          <div className="telegram-discover-info">
                            <strong>{chat.title}</strong>
                            <code>{chat.id}</code>
                          </div>
                          <button
                            type="button"
                            className={`telegram-discover-add ${alreadyAdded ? "is-added" : ""}`}
                            onClick={() => addChatIdToList(chat.id)}
                            disabled={alreadyAdded}
                          >
                            <span className="material-symbols-outlined">
                              {alreadyAdded ? "check" : "add"}
                            </span>
                            {alreadyAdded
                              ? t("notifications.settings.fields.telegramDiscoverAdded")
                              : t("notifications.settings.fields.telegramDiscoverAdd")}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
        </div>
      </div>
      <div className="notification-card-test notification-detail-test">
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
        {renderTestResult("telegram")}
      </div>
    </>
  );

  const renderComingSoonPanel = () => (
    <div className="notification-coming-soon">
      <span className="material-symbols-outlined">construction</span>
      <h4>{t("notifications.settings.channelComingSoonTitle")}</h4>
      <p>{t("notifications.settings.channelComingSoonDesc")}</p>
    </div>
  );

  const renderWebhookPanel = () =>
    accessToken && onCreateWebhook && onUpdateWebhook && onDeleteWebhook ? (
      <div className="notification-webhook-panel">
        <OutboundTargetsPanel
          targets={outboundTargets}
          devices={devices}
          accessToken={accessToken}
          allowedProtocols={["rest"]}
          titleKey="notifications.settings.webhook.title"
          newTargetKey="notifications.settings.webhook.newTarget"
          onCreate={onCreateWebhook}
          onUpdate={onUpdateWebhook}
          onDelete={onDeleteWebhook}
        />
      </div>
    ) : (
      <div className="notification-coming-soon">
        <span className="material-symbols-outlined">hub</span>
        <h4>{t("notifications.settings.webhook.title")}</h4>
        <p>{t("notifications.settings.webhook.missing")}</p>
      </div>
    );

  const renderActivePanel = () => {
    if (activeChannel === "smtp") return renderSmtpPanel();
    if (activeChannel === "sms") return renderSmsPanel();
    if (activeChannel === "telegram") return renderTelegramPanel();
    if (activeChannel === "webhook") return renderWebhookPanel();
    if (activeChannel === "whatsapp") return renderWhatsappPanel();
    return renderComingSoonPanel();
  };

  const setActiveEnabled = (checked: boolean) => {
    if (activeChannel === "smtp") setForm((prev) => ({ ...prev, smtp_enabled: checked }));
    if (activeChannel === "sms") setForm((prev) => ({ ...prev, sms_enabled: checked }));
    if (activeChannel === "telegram") setForm((prev) => ({ ...prev, telegram_enabled: checked }));
    if (activeChannel === "whatsapp") setForm((prev) => ({ ...prev, whatsapp_web_enabled: checked }));
  };

  return (
    <section className="tab-panel notification-tab-panel">
      <div className="notification-settings-panel notification-form-v2">
        <div className="notification-layout">
          <aside className="notification-channel-list" aria-label={t("notifications.settings.channelListTitle")}>
            <div className="notification-channel-stack">
              {channels.map((channel) => (
                <button
                  key={channel.key}
                  type="button"
                  className={`notification-channel-card ${activeChannel === channel.key ? "is-selected" : ""} ${channel.passive ? "is-passive" : ""}`}
                  aria-pressed={activeChannel === channel.key}
                  onClick={() => setActiveChannel(channel.key)}
                >
                  <span className="notification-channel-icon material-symbols-outlined">{channel.icon}</span>
                  <span className="notification-channel-text">
                    <strong>{channel.label}</strong>
                    <small>{channel.subtitle}</small>
                  </span>
                  <span className={`notification-channel-status ${channel.enabled ? "is-on" : "is-off"}`}>
                    {channel.passive
                      ? t("notifications.settings.channelFuture")
                      : channel.enabled
                      ? t("notifications.settings.channelActive")
                      : t("notifications.settings.channelPassive")}
                  </span>
                  <span className="material-symbols-outlined notification-channel-chevron">chevron_right</span>
                </button>
              ))}
            </div>
          </aside>

          <div className="notification-detail-column">
            <article className={`notification-detail-card ${activeDef.enabled ? "is-active" : ""}`}>
              <header className="notification-detail-head">
                <div className="notification-card-icon">
                  <span className="material-symbols-outlined">{activeDef.icon}</span>
                </div>
                <div className="notification-card-titles">
                  <h4>{activeDef.label}</h4>
                  <small>{activeDef.subtitle}</small>
                </div>
                {!activeDef.passive && activeDef.key !== "webhook" ? (
                  <label className="notif-toggle" title={t("notifications.settings.channelActive")}>
                    <input
                      type="checkbox"
                      checked={activeDef.enabled}
                      onChange={(event) => setActiveEnabled(event.target.checked)}
                    />
                    <span className="notif-toggle-slider" />
                  </label>
                ) : null}
              </header>
              {renderActivePanel()}
            </article>
          </div>
        </div>

        {loading ? <p className="helper-text notification-inline-state">{t("notifications.settings.loading")}</p> : null}
        {error ? <p className="error-text notification-inline-state">{error}</p> : null}
        {submitError ? <p className="error-text notification-inline-state">{submitError}</p> : null}
        <div className="notification-bottom-actions">
          <button type="button" className="primary-btn notification-save-top" disabled={saving} onClick={() => void handleSave()}>
            <span className="material-symbols-outlined">save</span>
            {saving ? t("notifications.settings.saving") : t("notifications.settings.saveAll")}
          </button>
        </div>
      </div>
    </section>
  );
}
