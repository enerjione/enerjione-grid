import { useEffect, useMemo, useState } from "react";

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

  // Onay bekleyen normale donenler: reset = true ama acknowledged = false.
  // Onaylanmis ve resetli alarmlar tamamen gizlenir.
  const pendingResetAlarms = useMemo(
    () => alarms.filter((alarm) => alarm.reset && !alarm.acknowledged && filterPredicate(alarm)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [alarms, search, levelFilter, assignmentFilter, deviceLabelById]
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
        setError(err instanceof Error ? err.message : "Yorumlar yüklenemedi.");
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
      setError(err instanceof Error ? err.message : "Alarm ataması yapılamadı.");
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
      setError(err instanceof Error ? err.message : "Yorum kaydedilemedi.");
    } finally {
      setSaving(false);
    }
  };

  const handleAcknowledge = async (alarmId: number) => {
    setSaving(true);
    setError("");
    try {
      await onAcknowledge(alarmId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Alarm onaylanamadı.");
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
      setError(err instanceof Error ? err.message : "Alarm resetlenemedi.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (alarmId: number) => {
    if (!window.confirm("Bu alarm kalici olarak silinsin mi?")) return;
    setSaving(true);
    setError("");
    try {
      await onDelete(alarmId);
      if (selectedAlarmId === alarmId) {
        setSelectedAlarmId(null);
        setDetailModalOpen(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Alarm silinemedi.");
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
    setSaving(true);
    setError("");
    try {
      await onAcknowledgeAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Tüm alarmlar onaylanamadı.");
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
      setError(err instanceof Error ? err.message : "Tüm alarmlar resetlenemedi.");
    } finally {
      setSaving(false);
    }
  };

  const renderAlarmDetail = (mode: "panel" | "modal") => (
    <div className={mode === "modal" ? "alarm-detail-modal-body" : undefined}>
      <h3>Alarm Detayı</h3>
      {selectedAlarm ? (
        <>
          <p>
            <strong>{selectedAlarm.title}</strong>
          </p>
          <p className="helper-text">{selectedAlarm.description}</p>

          <label>
            Alarm Atama
            <select
              disabled={saving}
              value={selectedAlarm.assigned_to ?? ""}
              onChange={(event) => void handleAssign(selectedAlarm.id, event.target.value)}
            >
              <option value="">Atanmamış</option>
              {users.map((user) => (
                <option key={user.id} value={user.username}>
                  {user.full_name}
                </option>
              ))}
            </select>
          </label>

          <div className="alarm-comments">
            <h4>Yorumlar</h4>
            <div className="alarm-comment-list">
              {(commentsByAlarm[selectedAlarm.id] ?? []).map((comment) => (
                <div key={comment.id} className="alarm-comment-item">
                  <div className="alarm-comment-meta">
                    <strong>{comment.author_username}</strong>
                    <span>{new Date(comment.created_at).toLocaleString("tr-TR")}</span>
                  </div>
                  <p>{comment.comment}</p>
                </div>
              ))}
              {(commentsByAlarm[selectedAlarm.id] ?? []).length === 0 ? <p className="helper-text">Henüz yorum yok.</p> : null}
            </div>
            <textarea
              placeholder="Alarma yorum yaz..."
              value={commentDraft}
              onChange={(event) => setCommentDraft(event.target.value)}
            />
            <div className="settings-actions">
              {mode === "modal" ? (
                <button type="button" onClick={() => setDetailModalOpen(false)}>
                  Kapat
                </button>
              ) : null}
              <button type="button" disabled={saving || !commentDraft.trim()} onClick={() => void handleAddComment()}>
                Yorumu Kaydet
              </button>
            </div>
          </div>
        </>
      ) : (
        <p className="helper-text">Detay için soldan bir alarm seçin.</p>
      )}
      {error ? <p className="error-text">{error}</p> : null}
    </div>
  );

  return (
    <section className="alarms-layout">
      <div className="alarms-list-card alarms-page-list-card">
        <div className="alarms-toolbar alarms-page-toolbar">
          <input
            className="device-search-input"
            placeholder="Alarm ara..."
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="alarms-filter-row">
            <select value={levelFilter} onChange={(event) => setLevelFilter(event.target.value as typeof levelFilter)}>
              <option value="all">Tüm Seviyeler</option>
              <option value="critical">Kritik</option>
              <option value="warning">Uyarı</option>
              <option value="info">Bilgi</option>
            </select>
            <select
              value={assignmentFilter}
              onChange={(event) => setAssignmentFilter(event.target.value as typeof assignmentFilter)}
            >
              <option value="all">Tüm Atamalar</option>
              <option value="assigned">Atanmış</option>
              <option value="unassigned">Atanmamış</option>
            </select>
            <button
              type="button"
              className="secondary-btn action-btn"
              disabled={saving || activeAlarms.length === 0}
              onClick={() => void handleAcknowledgeAll()}
              title="Aktif alarmların tümünü onayla"
            >
              Tümünü Onayla
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
              <h3>Aktif Alarmlar</h3>
              <span className="alarms-section-count alarms-section-count-active">{activeAlarms.length}</span>
            </div>
          </div>
          <div className="alarms-table-wrap alarms-page-table-wrap">
            <table className="values-table alarms-page-table">
              <thead>
                <tr>
                  <th>Tarih</th>
                  <th>Seviye</th>
                  <th>Alarm</th>
                  <th>Cihaz</th>
                  <th>Durum</th>
                  <th>Atanan</th>
                  <th className="alarm-actions-th">İşlem</th>
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
                      <div className="alarm-date">{created.toLocaleDateString("tr-TR")}</div>
                      <div className="alarm-time">{created.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
                    </td>
                    <td className="alarm-cell-level">
                      <span className={`alarm-pill level-${alarm.level.toLowerCase()}`}>{alarm.level}</span>
                    </td>
                    <td className="alarm-cell-title">
                      <div className="alarm-title-text">{alarm.title}</div>
                      {alarm.description ? <div className="alarm-desc-text">{alarm.description}</div> : null}
                    </td>
                    <td className="alarm-cell-device">{renderDeviceCell(alarm.device_id)}</td>
                    <td className="alarm-cell-state">
                      <span className={`alarm-state ${alarm.acknowledged ? "state-ack" : "state-open"}`}>
                        {alarm.acknowledged ? "Onaylandı" : "Açık"}
                      </span>
                    </td>
                    <td className="alarm-cell-assignee">{alarm.assigned_to ?? <span className="alarm-cell-empty">—</span>}</td>
                    <td className="actions-cell alarm-actions-cell">
                      <button
                        type="button"
                        className="icon-btn icon-btn-ack"
                        title="Onayla"
                        aria-label="Onayla"
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
                        title="Atama"
                        aria-label="Atama"
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
                        title="Yorum"
                        aria-label="Yorum"
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
                    <td colSpan={7} className="alarms-empty-cell">
                      Aktif alarm yok.
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
              itemLabel="aktif alarm"
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
              <h3>Normale Dönen — Onay Bekliyor</h3>
              <span className="alarms-section-count alarms-section-count-resolved">{pendingResetAlarms.length}</span>
            </div>
          </div>
          <div className="alarms-table-wrap alarms-page-table-wrap">
            <table className="values-table alarms-page-table">
              <thead>
                <tr>
                  <th>Tarih</th>
                  <th>Seviye</th>
                  <th>Alarm</th>
                  <th>Cihaz</th>
                  <th>Atanan</th>
                  <th className="alarm-actions-th">İşlem</th>
                </tr>
              </thead>
              <tbody>
                {pagedPendingResetAlarms.map((alarm) => (
                  <tr
                    key={alarm.id}
                    className={`alarm-row-resolved ${selectedAlarmId === alarm.id ? "alarm-row-active" : ""}`}
                    onClick={() => setSelectedAlarmId(alarm.id)}
                    onDoubleClick={() => openDetail(alarm.id, "comments")}
                  >
                    <td>{new Date(alarm.created_at).toLocaleString("tr-TR")}</td>
                    <td>
                      <span className={`alarm-pill level-${alarm.level.toLowerCase()}`}>{alarm.level}</span>
                    </td>
                    <td>{alarm.title}</td>
                    <td className="alarm-cell-device">{renderDeviceCell(alarm.device_id)}</td>
                    <td>{alarm.assigned_to ?? "-"}</td>
                    <td className="actions-cell alarm-actions-cell">
                      <button
                        type="button"
                        className="icon-btn icon-btn-ack"
                        title="Onayla ve listeden çıkar"
                        aria-label="Onayla"
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
                        title="Yorum"
                        aria-label="Yorum"
                        disabled={saving}
                        onClick={(event) => {
                          event.stopPropagation();
                          openDetail(alarm.id, "comments");
                        }}
                      >
                        <span className="material-symbols-outlined">chat</span>
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn-delete"
                        title="Sil"
                        aria-label="Sil"
                        disabled={saving}
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleDelete(alarm.id);
                        }}
                      >
                        <span className="material-symbols-outlined">delete</span>
                      </button>
                    </td>
                  </tr>
                ))}
                {pendingResetAlarms.length === 0 && !loading ? (
                  <tr>
                    <td colSpan={6} className="alarms-empty-cell">
                      Onay bekleyen normale dönmüş alarm yok.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          {pendingResetAlarms.length > 100 ? (
            <p className="helper-text alarms-section-overflow-hint">
              {pendingResetAlarms.length - 100} kayıt daha var — onayladıkça listeden çıkacak.
            </p>
          ) : null}
        </div>
      </div>

      {isDetailModalOpen ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal alarm-detail-modal">{renderAlarmDetail("modal")}</div>
        </div>
      ) : null}
    </section>
  );
}
