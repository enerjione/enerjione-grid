import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { TablePagination } from "../../components/TablePagination";
import type { AlarmComment, AlarmEvent, DeviceRow, UserRead } from "../../shared/types";

type Props = {
  alarms: AlarmEvent[];
  users: UserRead[];
  devices: DeviceRow[];
  loading?: boolean;
  onAssign: (alarmId: number, assignedTo: string | null) => Promise<void>;
  onLoadComments: (alarmId: number) => Promise<AlarmComment[]>;
  onAddComment: (alarmId: number, comment: string) => Promise<void>;
  onAcknowledge: (alarmId: number) => Promise<void>;
  onReset: (alarmId: number) => Promise<void>;
  onDelete: (alarmId: number) => Promise<void>;
  onAcknowledgeAll: () => Promise<void>;
  onResetAll: () => Promise<void>;
};

type DetailFocus = "assign" | "comments" | null;

export function AlarmsPage({
  alarms,
  users,
  devices,
  loading,
  onAssign,
  onLoadComments,
  onAddComment,
  onAcknowledge,
  onReset,
  onDelete,
  onAcknowledgeAll,
  onResetAll
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [search, setSearch] = useState("");
  const [levelFilter, setLevelFilter] = useState<"all" | "critical" | "warning" | "info">("all");
  const [assignmentFilter, setAssignmentFilter] = useState<"all" | "assigned" | "unassigned">("all");
  const [selectedAlarmId, setSelectedAlarmId] = useState<number | null>(null);
  const [isDetailModalOpen, setDetailModalOpen] = useState(false);
  const [detailFocus, setDetailFocus] = useState<DetailFocus>(null);
  const [commentDraft, setCommentDraft] = useState("");
  const [commentsByAlarm, setCommentsByAlarm] = useState<Record<number, AlarmComment[]>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const selectedAlarm = useMemo(
    () => alarms.find((item) => item.id === selectedAlarmId) ?? null,
    [alarms, selectedAlarmId]
  );

  // Cihaz id -> ad lookup'ı: alarm satırlarında "#3" yerine "TEST" gibi anlamlı bir
  // etiket göstermek için. Bilinmeyen cihaz id'leri "#id" fallback'i ile gösterilir.
  const deviceLabelById = useMemo(() => {
    const map = new Map<number, { name: string; code: string }>();
    for (const dev of devices) {
      map.set(dev.id, { name: dev.name, code: dev.code });
    }
    return map;
  }, [devices]);

  const renderDeviceCell = (deviceId: number) => {
    const info = deviceLabelById.get(deviceId);
    if (!info) {
      return <span className="alarm-device-fallback">#{deviceId}</span>;
    }
    return (
      <div className="alarm-device-cell">
        <span className="alarm-device-name">{info.name}</span>
        <span className="alarm-device-code">{info.code}</span>
      </div>
    );
  };

  /** Seviye kodundan i18n etiketi. Bilinmeyen seviye için ham değer döner. */
  const levelLabelTr = (level: string): string => {
    const k = level.toLowerCase();
    if (k === "info" || k === "warning" || k === "critical" || k === "error" || k === "debug") {
      return t(`alarms.level.${k}`);
    }
    return level;
  };

  /** Sinyal anahtarının prefix'inden Master / Sat 01 / Sat 02 rozeti üretir. */
  const renderSourceCell = (signalKey: string | null | undefined) => {
    if (!signalKey) {
      return <span className="alarm-cell-empty">—</span>;
    }
    const prefix = signalKey.split(".", 1)[0]?.toLowerCase() ?? "";
    const sourceMap: Record<string, { label: string; klass: string }> = {
      master: { label: "Master", klass: "master" },
      sat01: { label: "Sat 01", klass: "sat01" },
      sat02: { label: "Sat 02", klass: "sat02" }
    };
    const entry = sourceMap[prefix];
    if (!entry) {
      return <span className="alarm-cell-empty">{prefix || "—"}</span>;
    }
    return (
      <span className={`badge badge-source badge-source-${entry.klass}`}>{entry.label}</span>
    );
  };

  // Genel filtre (arama / seviye / atama). Onaylanmis+resetli alarmlar ekrandan gizli.
  const filterPredicate = (alarm: AlarmEvent): boolean => {
    const level = alarm.level.toLowerCase();
    const levelOk = levelFilter === "all" ? true : level === levelFilter;
    const assignmentOk =
      assignmentFilter === "all"
        ? true
        : assignmentFilter === "assigned"
          ? Boolean(alarm.assigned_to)
          : !alarm.assigned_to;
    const dev = deviceLabelById.get(alarm.device_id);
    const text = `${alarm.title} ${alarm.description} ${alarm.device_id} ${dev?.name ?? ""} ${dev?.code ?? ""}`.toLowerCase();
    const searchOk = search.trim() ? text.includes(search.trim().toLowerCase()) : true;
    return levelOk && assignmentOk && searchOk;
  };

  // Aktif alarmlar: henuz normale donmemis (reset = false). Onaylanmis veya degil farketmez.
  const activeAlarms = useMemo(
    () => alarms.filter((alarm) => !alarm.reset && filterPredicate(alarm)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [alarms, search, levelFilter, assignmentFilter, deviceLabelById]
  );

  // Aktif alarmda olan (device_id + signal_key/title) kombinasyonlarinin set'i.
  // Backend bir sebeple pending kaydi silmemis olsa bile UI'da ayni alarm hem
  // ust hem alt panelde gozukmesin diye defansif filtre.
  const activeKeySet = useMemo(() => {
    const set = new Set<string>();
    for (const alarm of activeAlarms) {
      const key = alarm.signal_key
        ? `${alarm.device_id}|sk:${alarm.signal_key}|${alarm.title}`
        : `${alarm.device_id}|t:${alarm.title}|${alarm.level}`;
      set.add(key);
    }
    return set;
  }, [activeAlarms]);

  // Onay bekleyen normale donenler: reset = true ama acknowledged = false.
  // Ek: ayni device + signal/title aktif alarmda varsa burada gizle.
  const pendingResetAlarms = useMemo(
    () =>
      alarms.filter((alarm) => {
        if (!alarm.reset || alarm.acknowledged) return false;
        if (!filterPredicate(alarm)) return false;
        const key = alarm.signal_key
          ? `${alarm.device_id}|sk:${alarm.signal_key}|${alarm.title}`
          : `${alarm.device_id}|t:${alarm.title}|${alarm.level}`;
        if (activeKeySet.has(key)) return false;
        return true;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [alarms, search, levelFilter, assignmentFilter, deviceLabelById, activeKeySet]
  );

  // Birlesik liste — secili alarm dogrulamasi icin
  const visibleAlarms = useMemo(
    () => [...activeAlarms, ...pendingResetAlarms],
    [activeAlarms, pendingResetAlarms]
  );

  useEffect(() => {
    setPage(1);
  }, [search, levelFilter, assignmentFilter, pageSize]);

  const pagedActiveAlarms = useMemo(() => {
    const start = (page - 1) * pageSize;
    return activeAlarms.slice(start, start + pageSize);
  }, [activeAlarms, page, pageSize]);

  // Alt panel kucuk bir liste, ayri sayfalama gerekmez (her zaman ilk N gosterilsin)
  const pagedPendingResetAlarms = useMemo(
    () => pendingResetAlarms.slice(0, 100),
    [pendingResetAlarms]
  );

  useEffect(() => {
    if (selectedAlarmId !== null) return;
    if (visibleAlarms.length === 0) return;
    setSelectedAlarmId(visibleAlarms[0].id);
  }, [visibleAlarms, selectedAlarmId]);

  useEffect(() => {
    const load = async () => {
      if (!selectedAlarmId) return;
      if (commentsByAlarm[selectedAlarmId]) return;
      try {
        const comments = await onLoadComments(selectedAlarmId);
        setCommentsByAlarm((prev) => ({ ...prev, [selectedAlarmId]: comments }));
      } catch (err) {
        setError(err instanceof Error ? err.message : t("alarms.errors.loadComments"));
      }
    };
    void load();
  }, [commentsByAlarm, onLoadComments, selectedAlarmId]);

  const handleAssign = async (alarmId: number, assignedTo: string) => {
    setSaving(true);
    setError("");
    try {
      await onAssign(alarmId, assignedTo || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.assignFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleAddComment = async () => {
    if (!selectedAlarmId) return;
    const value = commentDraft.trim();
    if (!value) return;
    setSaving(true);
    setError("");
    try {
      await onAddComment(selectedAlarmId, value);
      const refreshed = await onLoadComments(selectedAlarmId);
      setCommentsByAlarm((prev) => ({ ...prev, [selectedAlarmId]: refreshed }));
      setCommentDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.commentFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleAcknowledge = async (alarmId: number) => {
    const alarm = alarms.find((a) => a.id === alarmId);
    const label = alarm ? `"${alarm.title}"` : t("alarms.confirmAckThis");
    if (!window.confirm(t("alarms.confirmAck", { label }))) return;
    setSaving(true);
    setError("");
    try {
      await onAcknowledge(alarmId);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.ackFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async (alarmId: number) => {
    setSaving(true);
    setError("");
    try {
      await onReset(alarmId);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.resetFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (alarmId: number) => {
    if (!window.confirm(t("alarms.confirmDelete"))) return;
    setSaving(true);
    setError("");
    try {
      await onDelete(alarmId);
      if (selectedAlarmId === alarmId) {
        setSelectedAlarmId(null);
        setDetailModalOpen(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.deleteFailed"));
    } finally {
      setSaving(false);
    }
  };

  const openDetail = (alarmId: number, focus: DetailFocus) => {
    setSelectedAlarmId(alarmId);
    setDetailFocus(focus);
    setDetailModalOpen(true);
  };

  const handleAcknowledgeAll = async () => {
    const activeCount = activeAlarms.length;
    const pendingCount = pendingResetAlarms.length;
    if (activeCount === 0 && pendingCount === 0) return;
    const message =
      pendingCount > 0
        ? t("alarms.confirmAckAllPending", { active: activeCount, pending: pendingCount })
        : t("alarms.confirmAckAllActive", { active: activeCount });
    if (!window.confirm(message)) return;
    setSaving(true);
    setError("");
    try {
      if (activeCount > 0) {
        await onAcknowledgeAll();
      }
      // Normale donmus + onay bekleyen kayitlari da temizle.
      for (const alarm of pendingResetAlarms) {
        try {
          await onDelete(alarm.id);
        } catch {
          // Tek bir kayit silinemezse digerlerine devam et
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.ackAllFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleResetAll = async () => {
    setSaving(true);
    setError("");
    try {
      await onResetAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.resetAllFailed"));
    } finally {
      setSaving(false);
    }
  };

  const renderAlarmDetail = (mode: "panel" | "modal") => {
    if (!selectedAlarm) {
      return (
        <div className={mode === "modal" ? "alarm-detail-modal-body" : undefined}>
          <h3>{t("alarms.detail.title")}</h3>
          <p className="helper-text">{t("alarms.detail.selectHint")}</p>
          {error ? <p className="error-text">{error}</p> : null}
        </div>
      );
    }
    const created = new Date(selectedAlarm.created_at);
    const deviceInfo = deviceLabelById.get(selectedAlarm.device_id);
    const comments = commentsByAlarm[selectedAlarm.id] ?? [];
    const stateLabel = selectedAlarm.reset
      ? t("alarms.stateReset")
      : selectedAlarm.acknowledged
        ? t("alarms.stateAck")
        : t("alarms.stateOpen");
    const stateClass = selectedAlarm.reset ? "state-reset" : selectedAlarm.acknowledged ? "state-ack" : "state-open";
    return (
      <div className={mode === "modal" ? "alarm-detail-modal-body alarm-detail-2col" : "alarm-detail-2col"}>
        <header className="alarm-detail-header">
          <div className="alarm-detail-titlebar">
            <span className={`alarm-pill level-${selectedAlarm.level.toLowerCase()}`}>{levelLabelTr(selectedAlarm.level)}</span>
            <h3>{selectedAlarm.title}</h3>
            <span className={`alarm-state ${stateClass}`}>{stateLabel}</span>
          </div>
          {selectedAlarm.description ? (
            <p className="alarm-detail-description">{selectedAlarm.description}</p>
          ) : null}
        </header>

        <div className="alarm-detail-grid">
          {/* SOL: Detaylar + atama */}
          <section className="alarm-detail-info">
            <h4 className="alarm-detail-section-title">{t("alarms.detail.sectionDetails")}</h4>
            <dl className="alarm-detail-dl">
              <div className="alarm-detail-dl-row">
                <dt>{t("alarms.detail.fieldDevice")}</dt>
                <dd>
                  {deviceInfo ? (
                    <>
                      <span className="alarm-detail-strong">{deviceInfo.name}</span>
                      <span className="alarm-detail-mono"> {deviceInfo.code}</span>
                    </>
                  ) : (
                    <span className="alarm-detail-mono">#{selectedAlarm.device_id}</span>
                  )}
                </dd>
              </div>
              <div className="alarm-detail-dl-row">
                <dt>{t("alarms.detail.fieldLevel")}</dt>
                <dd>
                  <span className={`alarm-pill level-${selectedAlarm.level.toLowerCase()}`}>{levelLabelTr(selectedAlarm.level)}</span>
                </dd>
              </div>
              <div className="alarm-detail-dl-row">
                <dt>{t("alarms.detail.fieldStatus")}</dt>
                <dd>
                  <span className={`alarm-state ${stateClass}`}>{stateLabel}</span>
                </dd>
              </div>
              <div className="alarm-detail-dl-row">
                <dt>{t("alarms.detail.fieldOpened")}</dt>
                <dd>
                  <span className="alarm-detail-strong">{created.toLocaleDateString(localeTag)}</span>
                  <span className="alarm-detail-mono"> {created.toLocaleTimeString(localeTag)}</span>
                </dd>
              </div>
              {selectedAlarm.reset && selectedAlarm.reset_at ? (
                <div className="alarm-detail-dl-row">
                  <dt>{t("alarms.detail.fieldReset")}</dt>
                  <dd>
                    <span className="alarm-detail-strong">{new Date(selectedAlarm.reset_at).toLocaleDateString(localeTag)}</span>
                    <span className="alarm-detail-mono"> {new Date(selectedAlarm.reset_at).toLocaleTimeString(localeTag)}</span>
                  </dd>
                </div>
              ) : null}
              {selectedAlarm.acknowledged && selectedAlarm.acknowledged_at ? (
                <div className="alarm-detail-dl-row">
                  <dt>{t("alarms.detail.fieldAck")}</dt>
                  <dd>
                    <span className="alarm-detail-strong">{new Date(selectedAlarm.acknowledged_at).toLocaleDateString(localeTag)}</span>
                    <span className="alarm-detail-mono"> {new Date(selectedAlarm.acknowledged_at).toLocaleTimeString(localeTag)}</span>
                  </dd>
                </div>
              ) : null}
            </dl>

            <div className="alarm-detail-assign-block">
              <label className="alarm-detail-assign-label">
                <span>{t("alarms.detail.assignTo")}</span>
                <select
                  className="alarm-detail-select"
                  disabled={saving}
                  value={selectedAlarm.assigned_to ?? ""}
                  onChange={(event) => void handleAssign(selectedAlarm.id, event.target.value)}
                >
                  <option value="">{t("alarms.detail.assignNone")}</option>
                  {users.map((user) => (
                    <option key={user.id} value={user.username}>
                      {user.full_name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </section>

          {/* SAĞ: Yorumlar */}
          <section className="alarm-detail-comments">
            <div className="alarm-detail-comments-header">
              <h4 className="alarm-detail-section-title">{t("alarms.detail.sectionComments")}</h4>
              <span className="alarm-detail-comments-count">{comments.length}</span>
            </div>
            <div className="alarm-detail-comments-list">
              {comments.map((comment) => (
                <div key={comment.id} className="alarm-comment-card">
                  <div className="alarm-comment-card-meta">
                    <span className="alarm-comment-card-avatar">
                      {comment.author_username.slice(0, 1).toUpperCase()}
                    </span>
                    <div className="alarm-comment-card-meta-text">
                      <strong>{comment.author_username}</strong>
                      <span>{new Date(comment.created_at).toLocaleString(localeTag)}</span>
                    </div>
                  </div>
                  <p className="alarm-comment-card-body">{comment.comment}</p>
                </div>
              ))}
              {comments.length === 0 ? (
                <p className="alarm-detail-comments-empty">{t("alarms.detail.commentsEmpty")}</p>
              ) : null}
            </div>
            <div className="alarm-detail-comment-form">
              <textarea
                className="alarm-detail-comment-textarea"
                placeholder={t("alarms.detail.commentPlaceholder")}
                value={commentDraft}
                onChange={(event) => setCommentDraft(event.target.value)}
                rows={3}
              />
              <small className="alarm-detail-comment-hint">
                {t("alarms.detail.commentHint")}
              </small>
              <div className="alarm-detail-comment-actions">
                {mode === "modal" ? (
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={() => setDetailModalOpen(false)}
                  >
                    {t("common.close")}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="primary-btn"
                  disabled={saving || !commentDraft.trim()}
                  onClick={() => void handleAddComment()}
                >
                  {saving ? t("alarms.detail.savingComment") : t("alarms.detail.saveComment")}
                </button>
              </div>
            </div>
          </section>
        </div>

        {error ? <p className="error-text">{error}</p> : null}
      </div>
    );
  };

  return (
    <section className="alarms-layout alarms-layout-flat">
        <div className="alarms-toolbar alarms-page-toolbar">
          <input
            className="device-search-input"
            placeholder={t("alarms.search")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="alarms-filter-row">
            <select value={levelFilter} onChange={(event) => setLevelFilter(event.target.value as typeof levelFilter)}>
              <option value="all">{t("alarms.filterAllLevels")}</option>
              <option value="critical">{t("alarms.level.critical")}</option>
              <option value="warning">{t("alarms.level.warning")}</option>
              <option value="info">{t("alarms.level.info")}</option>
            </select>
            <select
              value={assignmentFilter}
              onChange={(event) => setAssignmentFilter(event.target.value as typeof assignmentFilter)}
            >
              <option value="all">{t("alarms.filterAllAssignments")}</option>
              <option value="assigned">{t("alarms.assigned")}</option>
              <option value="unassigned">{t("alarms.unassigned")}</option>
            </select>
            <button
              type="button"
              className="secondary-btn action-btn"
              disabled={saving || activeAlarms.length === 0}
              onClick={() => void handleAcknowledgeAll()}
              title={t("alarms.ackAllTooltip")}
            >
              {t("alarms.ackAll")}
            </button>
          </div>
        </div>
        {/* ÜST: Aktif Alarmlar */}
        <div className="alarms-section alarms-section-active">
          <div className="alarms-section-header">
            <div className="alarms-section-title">
              <span className="alarms-section-icon alarms-section-icon-active">
                <span className="material-symbols-outlined">notifications_active</span>
              </span>
              <h3>{t("alarms.active")}</h3>
              <span className="alarms-section-count alarms-section-count-active">{activeAlarms.length}</span>
            </div>
          </div>
          <div className="alarms-table-wrap alarms-page-table-wrap">
            <table className="values-table alarms-page-table">
              <thead>
                <tr>
                  <th>{t("alarms.table.date")}</th>
                  <th>{t("alarms.table.level")}</th>
                  <th>{t("alarms.table.device")}</th>
                  <th>{t("alarms.table.source")}</th>
                  <th>{t("alarms.table.alarm")}</th>
                  <th>{t("alarms.table.status")}</th>
                  <th>{t("alarms.table.assignee")}</th>
                  <th className="alarm-actions-th">{t("alarms.table.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {pagedActiveAlarms.map((alarm) => {
                  const levelClass = `alarm-row-level-${alarm.level.toLowerCase()}`;
                  const stateClass = alarm.acknowledged ? "alarm-row-acked" : "alarm-row-open";
                  const selectedClass = selectedAlarmId === alarm.id ? "alarm-row-active" : "";
                  const created = new Date(alarm.created_at);
                  return (
                  <tr
                    key={alarm.id}
                    className={`alarm-row ${levelClass} ${stateClass} ${selectedClass}`.trim()}
                    onClick={() => setSelectedAlarmId(alarm.id)}
                    onDoubleClick={() => openDetail(alarm.id, "comments")}
                  >
                    <td className="alarm-cell-date">
                      <div className="alarm-date">{created.toLocaleDateString(localeTag)}</div>
                      <div className="alarm-time">{created.toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
                    </td>
                    <td className="alarm-cell-level">
                      <span className={`alarm-pill level-${alarm.level.toLowerCase()}`}>{levelLabelTr(alarm.level)}</span>
                    </td>
                    <td className="alarm-cell-device">{renderDeviceCell(alarm.device_id)}</td>
                    <td className="alarm-cell-source">{renderSourceCell(alarm.signal_key)}</td>
                    <td className="alarm-cell-title">
                      <div className="alarm-title-text" title={alarm.description || alarm.title}>
                        {alarm.title}
                      </div>
                    </td>
                    <td className="alarm-cell-state">
                      <span className={`alarm-state ${alarm.acknowledged ? "state-ack" : "state-open"}`}>
                        {alarm.acknowledged ? t("alarms.stateAck") : t("alarms.stateOpen")}
                      </span>
                    </td>
                    <td className="alarm-cell-assignee">{alarm.assigned_to ?? <span className="alarm-cell-empty">—</span>}</td>
                    <td className="actions-cell alarm-actions-cell">
                      <button
                        type="button"
                        className="icon-btn icon-btn-ack"
                        title={t("alarms.actions.acknowledge")}
                        aria-label={t("alarms.actions.acknowledge")}
                        disabled={saving || Boolean(alarm.acknowledged)}
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleAcknowledge(alarm.id);
                        }}
                      >
                        <span className="material-symbols-outlined">check</span>
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn-assign"
                        title={t("alarms.actions.assign")}
                        aria-label={t("alarms.actions.assign")}
                        disabled={saving}
                        onClick={(event) => {
                          event.stopPropagation();
                          openDetail(alarm.id, "assign");
                        }}
                      >
                        <span className="material-symbols-outlined">person_add</span>
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn-comment"
                        title={t("alarms.actions.comment")}
                        aria-label={t("alarms.actions.comment")}
                        disabled={saving}
                        onClick={(event) => {
                          event.stopPropagation();
                          openDetail(alarm.id, "comments");
                        }}
                      >
                        <span className="material-symbols-outlined">chat</span>
                      </button>
                    </td>
                  </tr>
                  );
                })}
                {activeAlarms.length === 0 && !loading ? (
                  <tr>
                    <td colSpan={8} className="alarms-empty-cell">
                      {t("alarms.noActive")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          {activeAlarms.length > pageSize ? (
            <TablePagination
              totalItems={activeAlarms.length}
              page={page}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              itemLabel={t("alarms.itemLabel")}
            />
          ) : null}
        </div>

        {/* ALT: Normale Dönen — Onay Bekleyen */}
        <div className="alarms-section alarms-section-resolved">
          <div className="alarms-section-header">
            <div className="alarms-section-title">
              <span className="alarms-section-icon alarms-section-icon-resolved">
                <span className="material-symbols-outlined">history_toggle_off</span>
              </span>
              <h3>{t("alarms.pendingReset")}</h3>
              <span className="alarms-section-count alarms-section-count-resolved">{pendingResetAlarms.length}</span>
            </div>
          </div>
          <div className="alarms-table-wrap alarms-page-table-wrap">
            <table className="values-table alarms-page-table">
              <thead>
                <tr>
                  <th>{t("alarms.table.date")}</th>
                  <th>{t("alarms.table.level")}</th>
                  <th>{t("alarms.table.device")}</th>
                  <th>{t("alarms.table.source")}</th>
                  <th>{t("alarms.table.alarm")}</th>
                  <th>{t("alarms.table.assignee")}</th>
                  <th className="alarm-actions-th">{t("alarms.table.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {pagedPendingResetAlarms.map((alarm) => {
                  const created = new Date(alarm.created_at);
                  return (
                  <tr
                    key={alarm.id}
                    className={`alarm-row alarm-row-resolved ${selectedAlarmId === alarm.id ? "alarm-row-active" : ""}`}
                    onClick={() => setSelectedAlarmId(alarm.id)}
                    onDoubleClick={() => openDetail(alarm.id, "comments")}
                  >
                    <td className="alarm-cell-date">
                      <div className="alarm-date">{created.toLocaleDateString(localeTag)}</div>
                      <div className="alarm-time">{created.toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
                    </td>
                    <td className="alarm-cell-level">
                      <span className={`alarm-pill level-${alarm.level.toLowerCase()}`}>{levelLabelTr(alarm.level)}</span>
                    </td>
                    <td className="alarm-cell-device">{renderDeviceCell(alarm.device_id)}</td>
                    <td className="alarm-cell-source">{renderSourceCell(alarm.signal_key)}</td>
                    <td className="alarm-cell-title">
                      <div className="alarm-title-text" title={alarm.description || alarm.title}>
                        {alarm.title}
                      </div>
                    </td>
                    <td className="alarm-cell-assignee">{alarm.assigned_to ?? <span className="alarm-cell-empty">—</span>}</td>
                    <td className="actions-cell alarm-actions-cell">
                      <button
                        type="button"
                        className="icon-btn icon-btn-ack"
                        title={t("alarms.actions.ackAndRemove")}
                        aria-label={t("alarms.actions.acknowledge")}
                        disabled={saving}
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleAcknowledge(alarm.id);
                        }}
                      >
                        <span className="material-symbols-outlined">check</span>
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn-comment"
                        title={t("alarms.actions.comment")}
                        aria-label={t("alarms.actions.comment")}
                        disabled={saving}
                        onClick={(event) => {
                          event.stopPropagation();
                          openDetail(alarm.id, "comments");
                        }}
                      >
                        <span className="material-symbols-outlined">chat</span>
                      </button>
                    </td>
                  </tr>
                  );
                })}
                {pendingResetAlarms.length === 0 && !loading ? (
                  <tr>
                    <td colSpan={7} className="alarms-empty-cell">
                      {t("alarms.noPending")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          {pendingResetAlarms.length > 100 ? (
            <p className="helper-text alarms-section-overflow-hint">
              {t("alarms.moreOverflow", { count: pendingResetAlarms.length - 100 })}
            </p>
          ) : null}
        </div>

      {isDetailModalOpen ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal alarm-detail-modal">{renderAlarmDetail("modal")}</div>
        </div>
      ) : null}
    </section>
  );
}
