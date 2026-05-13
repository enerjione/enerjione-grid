/**
 * Toplu Bildirim sayfasi — WIZARD modunda (4 adim):
 *   1) Mesaj      — baslik + govde
 *   2) Kanallar   — web / email / sms
 *   3) Hedef      — tum kullanicilara / ekipler / kullanicilar
 *   4) Onay       — secimleri ozetler, "Gonder" butonu
 *
 * Sonuc bilgilendirmesi sayfa altinda banner olarak DEGIL, toast olarak verilir
 * (kullanici talebi). Hatalar da toast.
 *
 * Backend filter: ops_manager hedeflerden operator-DISI rolu otomatik kirpar
 * (defense-in-depth — UI'da zaten secemiyor).
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useToast } from "../../components/ToastProvider";
import { asyncConfirm } from "../../components/ConfirmDialog";
import {
  createBulkNotifyTemplate,
  deleteBulkNotifyTemplate,
  fetchResponsibilityAreas,
  fetchUsers,
  listBulkNotifyTemplates,
  sendBulkNotification,
  type BulkNotifyChannel,
  type BulkNotifyTemplate,
} from "../../shared/api";
import type { ResponsibilityAreaRow, UserRead, UserRole } from "../../shared/types";

type Props = {
  accessToken: string;
  currentRole: UserRole;
};

const ALL_CHANNELS: BulkNotifyChannel[] = ["web", "email", "sms"];

type WizardStep = 1 | 2 | 3 | 4;

export function BulkNotificationPage({ accessToken, currentRole }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [users, setUsers] = useState<UserRead[]>([]);
  const [areas, setAreas] = useState<ResponsibilityAreaRow[]>([]);
  const [loading, setLoading] = useState(true);

  const [step, setStep] = useState<WizardStep>(1);

  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [channels, setChannels] = useState<Set<BulkNotifyChannel>>(new Set<BulkNotifyChannel>(["web"]));
  const [selectedUserIds, setSelectedUserIds] = useState<Set<number>>(new Set<number>());
  const [selectedAreaIds, setSelectedAreaIds] = useState<Set<number>>(new Set<number>());
  const [sendToAll, setSendToAll] = useState(false);

  const [submitting, setSubmitting] = useState(false);

  const [userSearch, setUserSearch] = useState("");
  const [areaSearch, setAreaSearch] = useState("");

  // Sablonlar
  const [templates, setTemplates] = useState<BulkNotifyTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | "">("");
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false);
  const [templateNameInput, setTemplateNameInput] = useState("");
  const [templateSaving, setTemplateSaving] = useState(false);
  // Hedef de sablonla kaydedilsin mi (opsiyonel)
  const [saveWithTarget, setSaveWithTarget] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetchUsers(accessToken),
      fetchResponsibilityAreas(accessToken),
      listBulkNotifyTemplates(accessToken).catch(() => [] as BulkNotifyTemplate[]),
    ])
      .then(([u, a, tpl]) => {
        if (cancelled) return;
        setUsers(u);
        setAreas(a);
        setTemplates(tpl);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        toast.error(err instanceof Error ? err.message : t("common.errorOccurred"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken, t, toast]);

  const applyTemplate = (id: number | "") => {
    setSelectedTemplateId(id);
    if (id === "") return;
    const tpl = templates.find((x) => x.id === id);
    if (!tpl) return;
    setSubject(tpl.subject);
    setMessage(tpl.message);
    setChannels(new Set(tpl.channels));
    if (tpl.target) {
      setSelectedUserIds(new Set(tpl.target.user_ids));
      setSelectedAreaIds(new Set(tpl.target.team_ids));
      setSendToAll(Boolean(tpl.target.send_to_all));
    }
  };

  const handleSaveTemplate = async () => {
    const name = templateNameInput.trim();
    if (!name) {
      toast.error(t("bulkNotify.template.nameRequired"));
      return;
    }
    if (!subject.trim() || !message.trim()) {
      toast.error(t("bulkNotify.template.subjectMessageRequired"));
      return;
    }
    setTemplateSaving(true);
    try {
      const target = saveWithTarget
        ? {
            user_ids: Array.from(selectedUserIds),
            team_ids: Array.from(selectedAreaIds),
            send_to_all: sendToAll,
          }
        : null;
      const created = await createBulkNotifyTemplate(accessToken, {
        name,
        subject: subject.trim(),
        message: message.trim(),
        channels: Array.from(channels),
        target,
      });
      setTemplates((prev) => [created, ...prev]);
      setSelectedTemplateId(created.id);
      setTemplateNameInput("");
      setSaveTemplateOpen(false);
      toast.success(t("bulkNotify.template.saved", { name: created.name }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setTemplateSaving(false);
    }
  };

  const handleDeleteTemplate = async (id: number) => {
    const tpl = templates.find((x) => x.id === id);
    if (!tpl) return;
    if (!(await asyncConfirm(t("bulkNotify.template.deleteConfirm", { name: tpl.name })))) return;
    try {
      await deleteBulkNotifyTemplate(accessToken, id);
      setTemplates((prev) => prev.filter((x) => x.id !== id));
      if (selectedTemplateId === id) setSelectedTemplateId("");
      toast.success(t("bulkNotify.template.deleted"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

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

  // Adim gecerlilik kurallari — "ileri" butonunu disable etmek icin
  const step1Valid = subject.trim().length > 0 && message.trim().length > 0;
  const step2Valid = channels.size > 0;
  const step3Valid = sendToAll || selectedUserIds.size > 0 || selectedAreaIds.size > 0;
  const canSubmit = step1Valid && step2Valid && step3Valid && !submitting;

  // Onay adiminda gosterilecek hedef ozeti
  const selectedAreasList = useMemo(
    () => areas.filter((a) => selectedAreaIds.has(a.id)),
    [areas, selectedAreaIds]
  );
  const selectedUsersList = useMemo(
    () => users.filter((u) => selectedUserIds.has(u.id)),
    [users, selectedUserIds]
  );

  const goNext = () => {
    if (step === 1 && !step1Valid) return;
    if (step === 2 && !step2Valid) return;
    if (step === 3 && !step3Valid) return;
    setStep((s) => (Math.min(4, s + 1) as WizardStep));
  };
  const goBack = () => setStep((s) => (Math.max(1, s - 1) as WizardStep));

  const resetWizard = () => {
    setStep(1);
    setSubject("");
    setMessage("");
    setChannels(new Set<BulkNotifyChannel>(["web"]));
    setSelectedUserIds(new Set<number>());
    setSelectedAreaIds(new Set<number>());
    setSendToAll(false);
    setUserSearch("");
    setAreaSearch("");
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
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
      // Sonucu TOAST olarak goster
      const okMsg = t("bulkNotify.resultTitle", { count: res.recipients_count });
      const detail = t("bulkNotify.resultDetail", {
        web: res.web_sent,
        email: res.email_sent,
        sms: res.sms_sent,
      });
      toast.success(`${okMsg} — ${detail}`);
      if (res.email_failed || res.sms_failed) {
        toast.error(
          t("bulkNotify.resultFail", {
            emailFail: res.email_failed,
            smsFail: res.sms_failed,
          })
        );
      }
      resetWizard();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setSubmitting(false);
    }
  };

  // Toplam hedef sayisi (onay icin)
  const targetSummaryCount = sendToAll
    ? "*"
    : `${selectedAreaIds.size + selectedUserIds.size}`;

  return (
    <section className="tab-panel bulk-notify-panel bulk-notify-wizard">
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

      {/* Stepper */}
      <ol className="bulk-notify-stepper">
        {[1, 2, 3, 4].map((n) => {
          const labels: Record<number, string> = {
            1: t("bulkNotify.steps.message"),
            2: t("bulkNotify.steps.channels"),
            3: t("bulkNotify.steps.recipients"),
            4: t("bulkNotify.steps.review"),
          };
          const state = n === step ? "current" : n < step ? "done" : "todo";
          return (
            <li key={n} className={`bulk-notify-step bulk-notify-step--${state}`}>
              <span className="bulk-notify-step-num">{n < step ? "✓" : n}</span>
              <span className="bulk-notify-step-label">{labels[n]}</span>
            </li>
          );
        })}
      </ol>

      <div className="bulk-notify-form">
        {/* --- ADIM 1: MESAJ --- */}
        {step === 1 ? (
          <div className="bulk-notify-card">
            <div className="bulk-notify-card-head">
              <span className="material-symbols-outlined">edit_note</span>
              <strong>{t("bulkNotify.messageSection")}</strong>
              <div className="bulk-notify-template-controls">
                <label className="bulk-notify-template-pick">
                  <span>{t("bulkNotify.template.pickLabel")}</span>
                  <select
                    value={selectedTemplateId}
                    onChange={(e) => {
                      const v = e.target.value;
                      applyTemplate(v === "" ? "" : Number(v));
                    }}
                  >
                    <option value="">{t("bulkNotify.template.none")}</option>
                    {templates.map((tpl) => (
                      <option key={tpl.id} value={tpl.id}>
                        {tpl.name}
                      </option>
                    ))}
                  </select>
                </label>
                {selectedTemplateId !== "" ? (
                  <button
                    type="button"
                    className="secondary-btn"
                    title={t("bulkNotify.template.deleteBtn")}
                    onClick={() => void handleDeleteTemplate(selectedTemplateId as number)}
                  >
                    <span className="material-symbols-outlined">delete</span>
                  </button>
                ) : null}
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={() => {
                    setTemplateNameInput(subject.trim().slice(0, 80) || "");
                    setSaveTemplateOpen(true);
                  }}
                  disabled={!subject.trim() || !message.trim()}
                  title={t("bulkNotify.template.saveAs")}
                >
                  <span className="material-symbols-outlined">bookmark_add</span>
                  {t("bulkNotify.template.saveAs")}
                </button>
              </div>
            </div>
            <label className="bulk-notify-field">
              <span>{t("bulkNotify.subject")}</span>
              <input
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                maxLength={200}
                placeholder={t("bulkNotify.subjectPlaceholder")}
                autoFocus
              />
            </label>
            <label className="bulk-notify-field">
              <span>{t("bulkNotify.message")}</span>
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                maxLength={2000}
                rows={7}
                placeholder={t("bulkNotify.messagePlaceholder")}
              />
            </label>
          </div>
        ) : null}

        {/* Sablon kaydet modal */}
        {saveTemplateOpen ? (
          <div className="settings-modal-backdrop" onClick={() => setSaveTemplateOpen(false)}>
            <div
              className="settings-modal"
              style={{ width: "min(440px, 92vw)" }}
              onClick={(e) => e.stopPropagation()}
            >
              <h3>{t("bulkNotify.template.saveModalTitle")}</h3>
              <p className="helper-text">{t("bulkNotify.template.saveModalHint")}</p>
              <label className="bulk-notify-field">
                <span>{t("bulkNotify.template.nameLabel")}</span>
                <input
                  type="text"
                  value={templateNameInput}
                  onChange={(e) => setTemplateNameInput(e.target.value)}
                  maxLength={120}
                  autoFocus
                  placeholder={t("bulkNotify.template.namePlaceholder")}
                />
              </label>
              <label
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 13,
                  color: "#475569",
                  marginTop: 8,
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={saveWithTarget}
                  onChange={(e) => setSaveWithTarget(e.target.checked)}
                />
                <span>{t("bulkNotify.template.saveWithTarget")}</span>
              </label>
              <div className="settings-actions">
                <button
                  type="button"
                  onClick={() => setSaveTemplateOpen(false)}
                  disabled={templateSaving}
                >
                  {t("common.cancel")}
                </button>
                <button
                  type="button"
                  className="primary-btn"
                  onClick={() => void handleSaveTemplate()}
                  disabled={templateSaving || !templateNameInput.trim()}
                >
                  {templateSaving ? t("common.saving") : t("common.save")}
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {/* --- ADIM 2: KANALLAR --- */}
        {step === 2 ? (
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
        ) : null}

        {/* --- ADIM 3: HEDEF --- */}
        {step === 3 ? (
          <div className="bulk-notify-card">
            <div className="bulk-notify-card-head">
              <span className="material-symbols-outlined">group</span>
              <strong>{t("bulkNotify.recipientsSection")}</strong>
              <span className="bulk-notify-count-badge">
                {sendToAll ? "*" : selectedAreaIds.size + selectedUserIds.size}
              </span>
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
        ) : null}

        {/* --- ADIM 4: ONAY --- */}
        {step === 4 ? (
          <div className="bulk-notify-card">
            <div className="bulk-notify-card-head">
              <span className="material-symbols-outlined">checklist</span>
              <strong>{t("bulkNotify.reviewSection")}</strong>
            </div>
            <div className="bulk-notify-review">
              <div className="bulk-notify-review-row">
                <span className="bulk-notify-review-label">
                  {t("bulkNotify.subject")}
                </span>
                <span className="bulk-notify-review-value">{subject}</span>
              </div>
              <div className="bulk-notify-review-row">
                <span className="bulk-notify-review-label">
                  {t("bulkNotify.message")}
                </span>
                <span className="bulk-notify-review-value bulk-notify-review-value--multiline">
                  {message}
                </span>
              </div>
              <div className="bulk-notify-review-row">
                <span className="bulk-notify-review-label">
                  {t("bulkNotify.channelsSection")}
                </span>
                <span className="bulk-notify-review-value">
                  {Array.from(channels)
                    .map((c) => t(`bulkNotify.channel.${c}`))
                    .join(", ")}
                </span>
              </div>
              <div className="bulk-notify-review-row">
                <span className="bulk-notify-review-label">
                  {t("bulkNotify.recipientsSection")}
                </span>
                <span className="bulk-notify-review-value">
                  {sendToAll ? (
                    currentRole === "ops_manager"
                      ? t("bulkNotify.sendToAllOperators")
                      : t("bulkNotify.sendToAll")
                  ) : (
                    <span className="bulk-notify-review-targets">
                      {selectedAreasList.length > 0 ? (
                        <span>
                          <strong>{t("bulkNotify.teams")}: </strong>
                          {selectedAreasList.map((a) => a.name).join(", ")}
                        </span>
                      ) : null}
                      {selectedUsersList.length > 0 ? (
                        <span>
                          <strong>{t("bulkNotify.users")}: </strong>
                          {selectedUsersList
                            .map((u) => u.full_name || u.username)
                            .join(", ")}
                        </span>
                      ) : null}
                    </span>
                  )}
                </span>
              </div>
              <div className="bulk-notify-review-row">
                <span className="bulk-notify-review-label">
                  {t("bulkNotify.reviewTargetCount")}
                </span>
                <span className="bulk-notify-review-value">{targetSummaryCount}</span>
              </div>
            </div>
          </div>
        ) : null}

        {/* Nav butonlari */}
        <div className="bulk-notify-actions">
          {step > 1 ? (
            <button type="button" className="secondary-btn" onClick={goBack} disabled={submitting}>
              <span className="material-symbols-outlined">chevron_left</span>
              {t("bulkNotify.back")}
            </button>
          ) : (
            <span />
          )}
          {step < 4 ? (
            <button
              type="button"
              className="primary-btn"
              onClick={goNext}
              disabled={
                (step === 1 && !step1Valid) ||
                (step === 2 && !step2Valid) ||
                (step === 3 && !step3Valid)
              }
            >
              {t("bulkNotify.next")}
              <span className="material-symbols-outlined">chevron_right</span>
            </button>
          ) : (
            <button
              type="button"
              className="primary-btn"
              onClick={() => void handleSubmit()}
              disabled={!canSubmit}
            >
              <span className="material-symbols-outlined">send</span>
              {submitting ? t("bulkNotify.sending") : t("bulkNotify.send")}
            </button>
          )}
        </div>
      </div>

      {/* ALT BOLUM: Kaydedilmis Sablonlar — onizleme + yukle butonu */}
      <div className="bulk-notify-templates-section">
        <div className="bulk-notify-card-head">
          <span className="material-symbols-outlined">bookmark</span>
          <strong>{t("bulkNotify.template.listTitle")}</strong>
          <span className="bulk-notify-count-badge">{templates.length}</span>
        </div>
        {templates.length === 0 ? (
          <p className="helper-text" style={{ padding: "24px 0", textAlign: "center" }}>
            {t("bulkNotify.template.emptyHint")}
          </p>
        ) : (
          <ul className="bulk-notify-template-grid">
            {templates.map((tpl) => {
              const targetSummary = tpl.target
                ? tpl.target.send_to_all
                  ? t("bulkNotify.sendToAll")
                  : `${tpl.target.team_ids.length} ${t("bulkNotify.teams").toLowerCase()} · ${tpl.target.user_ids.length} ${t("bulkNotify.users").toLowerCase()}`
                : t("bulkNotify.template.noTargetSaved");
              return (
                <li key={tpl.id} className="bulk-notify-template-card">
                  <div className="bulk-notify-template-card-head">
                    <strong className="bulk-notify-template-card-name">{tpl.name}</strong>
                    <div className="bulk-notify-template-card-actions">
                      <button
                        type="button"
                        className="primary-btn bulk-notify-template-load"
                        onClick={() => {
                          applyTemplate(tpl.id);
                          setStep(1);
                        }}
                        title={t("bulkNotify.template.loadBtnTitle")}
                      >
                        <span className="material-symbols-outlined">file_open</span>
                        {t("bulkNotify.template.loadBtn")}
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn-danger"
                        onClick={() => void handleDeleteTemplate(tpl.id)}
                        title={t("bulkNotify.template.deleteBtn")}
                      >
                        <span className="material-symbols-outlined">delete</span>
                      </button>
                    </div>
                  </div>
                  <div className="bulk-notify-template-card-meta">
                    {tpl.channels.map((c) => (
                      <span key={c} className={`bulk-notify-template-chip bulk-notify-template-chip--${c}`}>
                        <span className="material-symbols-outlined">
                          {c === "web" ? "notifications" : c === "email" ? "mail" : "sms"}
                        </span>
                        {t(`bulkNotify.channel.${c}`)}
                      </span>
                    ))}
                    <span className="bulk-notify-template-target">
                      <span className="material-symbols-outlined">group</span>
                      {targetSummary}
                    </span>
                  </div>
                  <div className="bulk-notify-template-preview">
                    <strong>{tpl.subject}</strong>
                    <p>{tpl.message.length > 200 ? tpl.message.slice(0, 200) + "…" : tpl.message}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
