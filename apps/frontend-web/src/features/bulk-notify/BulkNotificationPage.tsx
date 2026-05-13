/**
 * Toplu Bildirim sayfasi — ops_manager / installer / engineer kullanır.
 *
 * Hedef secimi: kullanici/ekip/herkes (kombine olabilir).
 * Kanallar: web push (NotificationBell), email, sms (kullanici telefon/email
 * tanimliysa).
 *
 * Backend filter: ops_manager hedeflerden operator-DISI rolu otomatik kirpar.
 */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  fetchResponsibilityAreas,
  fetchUsers,
  sendBulkNotification,
  type BulkNotifyChannel,
  type BulkNotifyResult,
} from "../../shared/api";
import type { ResponsibilityAreaRow, UserRead, UserRole } from "../../shared/types";

type Props = {
  accessToken: string;
  currentRole: UserRole;
};

const ALL_CHANNELS: BulkNotifyChannel[] = ["web", "email", "sms"];

export function BulkNotificationPage({ accessToken, currentRole }: Props) {
  const { t } = useTranslation();

  const [users, setUsers] = useState<UserRead[]>([]);
  const [areas, setAreas] = useState<ResponsibilityAreaRow[]>([]);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);

  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [channels, setChannels] = useState<Set<BulkNotifyChannel>>(new Set<BulkNotifyChannel>(["web"]));
  const [selectedUserIds, setSelectedUserIds] = useState<Set<number>>(new Set<number>());
  const [selectedAreaIds, setSelectedAreaIds] = useState<Set<number>>(new Set<number>());
  const [sendToAll, setSendToAll] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [result, setResult] = useState<BulkNotifyResult | null>(null);

  const [userSearch, setUserSearch] = useState("");
  const [areaSearch, setAreaSearch] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([fetchUsers(accessToken), fetchResponsibilityAreas(accessToken)])
      .then(([u, a]) => {
        if (cancelled) return;
        setUsers(u);
        setAreas(a);
        setLoadError("");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : t("common.errorOccurred"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken, t]);

  const toggleChannel = (ch: BulkNotifyChannel) => {
    setChannels((prev) => {
      const next = new Set(prev);
      if (next.has(ch)) next.delete(ch);
      else next.add(ch);
      return next;
    });
  };

  const toggleUser = (id: number) => {
    setSelectedUserIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleArea = (id: number) => {
    setSelectedAreaIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filteredUsers = useMemo(() => {
    const q = userSearch.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.username.toLowerCase().includes(q) ||
        u.full_name.toLowerCase().includes(q) ||
        (u.email || "").toLowerCase().includes(q)
    );
  }, [users, userSearch]);

  const filteredAreas = useMemo(() => {
    const q = areaSearch.trim().toLowerCase();
    if (!q) return areas;
    return areas.filter(
      (a) => a.name.toLowerCase().includes(q) || a.code.toLowerCase().includes(q)
    );
  }, [areas, areaSearch]);

  const targetCount = sendToAll
    ? "*"
    : `${selectedUserIds.size + selectedAreaIds.size}`;

  const canSubmit =
    !submitting &&
    subject.trim().length > 0 &&
    message.trim().length > 0 &&
    channels.size > 0 &&
    (sendToAll || selectedUserIds.size > 0 || selectedAreaIds.size > 0);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitError("");
    setResult(null);
    setSubmitting(true);
    try {
      const res = await sendBulkNotification(accessToken, {
        subject: subject.trim(),
        message: message.trim(),
        channels: Array.from(channels),
        user_ids: Array.from(selectedUserIds),
        team_ids: Array.from(selectedAreaIds),
        send_to_all: sendToAll,
      });
      setResult(res);
      // Basari sonrasi formu temizlemiyoruz; kullanici tekrar gondermek
      // isteyebilir. Hedefleri sifirlamak yeterli — guvenli default.
      setSelectedUserIds(new Set<number>());
      setSelectedAreaIds(new Set<number>());
      setSendToAll(false);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="tab-panel bulk-notify-panel">
      <div className="panel-head">
        <div>
          <h3>
            <span className="material-symbols-outlined">campaign</span>
            {t("bulkNotify.title")}
          </h3>
          <p className="helper-text">
            {currentRole === "ops_manager"
              ? t("bulkNotify.subtitleOpsManager")
              : t("bulkNotify.subtitle")}
          </p>
        </div>
      </div>

      {loadError ? <p className="error-text">{loadError}</p> : null}

      <form className="bulk-notify-form" onSubmit={handleSubmit}>
        {/* Mesaj kismi */}
        <div className="bulk-notify-card">
          <div className="bulk-notify-card-head">
            <span className="material-symbols-outlined">edit_note</span>
            <strong>{t("bulkNotify.messageSection")}</strong>
          </div>
          <label className="bulk-notify-field">
            <span>{t("bulkNotify.subject")}</span>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              maxLength={200}
              placeholder={t("bulkNotify.subjectPlaceholder")}
            />
          </label>
          <label className="bulk-notify-field">
            <span>{t("bulkNotify.message")}</span>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              maxLength={2000}
              rows={5}
              placeholder={t("bulkNotify.messagePlaceholder")}
            />
          </label>
        </div>

        {/* Kanallar */}
        <div className="bulk-notify-card">
          <div className="bulk-notify-card-head">
            <span className="material-symbols-outlined">send</span>
            <strong>{t("bulkNotify.channelsSection")}</strong>
          </div>
          <div className="bulk-notify-channels">
            {ALL_CHANNELS.map((ch) => (
              <label
                key={ch}
                className={`bulk-notify-channel ${channels.has(ch) ? "is-on" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={channels.has(ch)}
                  onChange={() => toggleChannel(ch)}
                />
                <span className="material-symbols-outlined">
                  {ch === "web" ? "notifications" : ch === "email" ? "mail" : "sms"}
                </span>
                <span className="bulk-notify-channel-label">
                  {t(`bulkNotify.channel.${ch}`)}
                </span>
              </label>
            ))}
          </div>
        </div>

        {/* Hedef secimi */}
        <div className="bulk-notify-card">
          <div className="bulk-notify-card-head">
            <span className="material-symbols-outlined">group</span>
            <strong>{t("bulkNotify.recipientsSection")}</strong>
            <span className="bulk-notify-count-badge">{targetCount}</span>
          </div>

          <label className="bulk-notify-all-toggle">
            <input
              type="checkbox"
              checked={sendToAll}
              onChange={(e) => setSendToAll(e.target.checked)}
            />
            <span>
              {currentRole === "ops_manager"
                ? t("bulkNotify.sendToAllOperators")
                : t("bulkNotify.sendToAll")}
            </span>
          </label>

          {!sendToAll ? (
            <div className="bulk-notify-targets-grid">
              {/* Ekipler */}
              <div className="bulk-notify-targets-col">
                <div className="bulk-notify-targets-head">
                  <strong>{t("bulkNotify.teams")}</strong>
                  <small>{selectedAreaIds.size}</small>
                </div>
                <input
                  type="search"
                  className="bulk-notify-search"
                  placeholder={t("bulkNotify.searchTeams")}
                  value={areaSearch}
                  onChange={(e) => setAreaSearch(e.target.value)}
                />
                <div className="bulk-notify-list">
                  {loading ? (
                    <p className="helper-text">{t("common.loading")}</p>
                  ) : filteredAreas.length === 0 ? (
                    <p className="helper-text">{t("bulkNotify.noTeams")}</p>
                  ) : (
                    filteredAreas.map((a) => (
                      <label
                        key={a.id}
                        className={`bulk-notify-list-item ${selectedAreaIds.has(a.id) ? "is-on" : ""}`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedAreaIds.has(a.id)}
                          onChange={() => toggleArea(a.id)}
                        />
                        <span className="bulk-notify-list-item-main">
                          <strong>{a.name}</strong>
                          <small>{a.code}</small>
                        </span>
                      </label>
                    ))
                  )}
                </div>
              </div>

              {/* Kullanicilar */}
              <div className="bulk-notify-targets-col">
                <div className="bulk-notify-targets-head">
                  <strong>{t("bulkNotify.users")}</strong>
                  <small>{selectedUserIds.size}</small>
                </div>
                <input
                  type="search"
                  className="bulk-notify-search"
                  placeholder={t("bulkNotify.searchUsers")}
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                />
                <div className="bulk-notify-list">
                  {loading ? (
                    <p className="helper-text">{t("common.loading")}</p>
                  ) : filteredUsers.length === 0 ? (
                    <p className="helper-text">{t("bulkNotify.noUsers")}</p>
                  ) : (
                    filteredUsers.map((u) => (
                      <label
                        key={u.id}
                        className={`bulk-notify-list-item ${selectedUserIds.has(u.id) ? "is-on" : ""}`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedUserIds.has(u.id)}
                          onChange={() => toggleUser(u.id)}
                        />
                        <span className="bulk-notify-list-item-main">
                          <strong>{u.full_name || u.username}</strong>
                          <small>
                            {u.username}
                            {u.email ? ` · ${u.email}` : ""}
                            {u.role ? ` · ${u.role}` : ""}
                          </small>
                        </span>
                      </label>
                    ))
                  )}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        {/* Submit */}
        {submitError ? <p className="error-text">{submitError}</p> : null}
        {result ? (
          <div className="bulk-notify-result">
            <span className="material-symbols-outlined">check_circle</span>
            <div>
              <strong>
                {t("bulkNotify.resultTitle", { count: result.recipients_count })}
              </strong>
              <small>
                {t("bulkNotify.resultDetail", {
                  web: result.web_sent,
                  email: result.email_sent,
                  sms: result.sms_sent,
                })}
                {result.email_failed || result.sms_failed
                  ? ` · ${t("bulkNotify.resultFail", {
                      emailFail: result.email_failed,
                      smsFail: result.sms_failed,
                    })}`
                  : ""}
              </small>
            </div>
          </div>
        ) : null}

        <div className="bulk-notify-actions">
          <button
            type="submit"
            className="primary-btn"
            disabled={!canSubmit}
            title={!canSubmit ? t("bulkNotify.completeForm") : undefined}
          >
            <span className="material-symbols-outlined">send</span>
            {submitting ? t("bulkNotify.sending") : t("bulkNotify.send")}
          </button>
        </div>
      </form>
    </section>
  );
}
