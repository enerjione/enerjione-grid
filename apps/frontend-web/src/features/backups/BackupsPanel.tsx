import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  createManualBackup,
  deleteBackup,
  downloadBackupFile,
  fetchBackups,
  fetchBackupSchedule,
  restoreBackup,
  updateBackupSchedule,
  uploadBackupFile
} from "../../shared/api";
import type { BackupJob, BackupSchedule } from "../../shared/types";
import { useToast } from "../../components/ToastProvider";

type Props = {
  accessToken: string;
};

function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

function fmtDate(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(localeTag);
}

const STATUS_COLOR: Record<string, string> = {
  running: "#3b82f6",
  success: "#10b981",
  failed: "#ef4444"
};

export function BackupsPanel({ accessToken }: Props) {
  const toast = useToast();
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [backups, setBackups] = useState<BackupJob[]>([]);
  const [schedule, setSchedule] = useState<BackupSchedule | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [restoringId, setRestoringId] = useState<number | null>(null);
  const [confirmRestoreId, setConfirmRestoreId] = useState<number | null>(null);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const reload = async () => {
    setLoading(true);
    try {
      const [b, s] = await Promise.all([
        fetchBackups(accessToken),
        fetchBackupSchedule(accessToken)
      ]);
      setBackups(b);
      setSchedule(s);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("backups.fetchFail"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    const id = window.setInterval(() => {
      void reload();
    }, 15000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  const handleCreate = async () => {
    setCreating(true);
    try {
      await createManualBackup(accessToken);
      toast.success(t("backups.createSuccess"));
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("backups.createFail"));
    } finally {
      setCreating(false);
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChosen = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".dump")) {
      toast.error(t("backups.uploadOnlyDump"));
      return;
    }
    setUploading(true);
    try {
      await uploadBackupFile(accessToken, file);
      toast.success(t("backups.uploadSuccess"));
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("backups.uploadFail"));
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm(t("backups.deleteConfirm"))) {
      return;
    }
    try {
      await deleteBackup(accessToken, id);
      toast.success(t("backups.deleteSuccess"));
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("backups.deleteFail"));
    }
  };

  const handleDownload = async (job: BackupJob) => {
    try {
      const filename = job.filename || `hsl-backup-${job.id}.dump`;
      await downloadBackupFile(accessToken, job.id, filename);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("backups.downloadFail"));
    }
  };

  const handleRestoreConfirmed = async () => {
    if (confirmRestoreId == null) return;
    const id = confirmRestoreId;
    setConfirmRestoreId(null);
    setRestoringId(id);
    try {
      await restoreBackup(accessToken, id);
      toast.success(t("backups.restore.success"));
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("backups.restore.fail"));
    } finally {
      setRestoringId(null);
    }
  };

  const handleScheduleChange = async (
    payload: Partial<BackupSchedule>
  ): Promise<void> => {
    setSavingSchedule(true);
    try {
      const s = await updateBackupSchedule(accessToken, payload);
      setSchedule(s);
      toast.success(t("backups.schedule.updated"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("backups.scheduleFail"));
    } finally {
      setSavingSchedule(false);
    }
  };

  const stats = useMemo(() => {
    const total = backups.length;
    const success = backups.filter((b) => b.status === "success").length;
    const failed = backups.filter((b) => b.status === "failed").length;
    const totalSize = backups.reduce((s, b) => s + (b.size_bytes || 0), 0);
    return { total, success, failed, totalSize };
  }, [backups]);

  const confirmJob = useMemo(
    () => backups.find((b) => b.id === confirmRestoreId) ?? null,
    [confirmRestoreId, backups]
  );

  return (
    <section className="tab-panel backups-panel">
      {/* TOP BAR — stats + actions tek satirda */}
      <div className="backups-topbar">
        <div className="backups-stats">
          <div className="backups-stat-chip">
            <span className="backups-stat-num">{stats.total}</span>
            <span className="backups-stat-label">{t("backups.stats.total")}</span>
          </div>
          <div className="backups-stat-chip is-ok">
            <span className="backups-stat-num">{stats.success}</span>
            <span className="backups-stat-label">{t("backups.stats.success")}</span>
          </div>
          <div className="backups-stat-chip is-fail">
            <span className="backups-stat-num">{stats.failed}</span>
            <span className="backups-stat-label">{t("backups.stats.failed")}</span>
          </div>
          <div className="backups-stat-chip is-size">
            <span className="backups-stat-num">{fmtBytes(stats.totalSize)}</span>
            <span className="backups-stat-label">{t("backups.stats.totalSize")}</span>
          </div>
        </div>
        <div className="backups-topbar-actions">
          <input
            ref={fileInputRef}
            type="file"
            accept=".dump"
            style={{ display: "none" }}
            onChange={(e) => void handleFileChosen(e)}
          />
          <button
            type="button"
            className="secondary-btn backups-action-btn"
            onClick={() => setScheduleModalOpen(true)}
            title={t("backups.schedule.title")}
          >
            <span className="material-symbols-outlined">schedule</span>
            {t("backups.schedule.openBtn")}
          </button>
          <button
            type="button"
            className="secondary-btn backups-action-btn backups-upload-btn"
            onClick={handleUploadClick}
            disabled={uploading}
            title={t("backups.uploadHint")}
          >
            <span className="material-symbols-outlined">upload_file</span>
            {uploading ? t("backups.uploading") : t("backups.uploadBackup")}
          </button>
          <button
            type="button"
            className="primary-btn backups-action-btn backups-create-btn"
            onClick={() => void handleCreate()}
            disabled={creating}
          >
            <span className="material-symbols-outlined">backup</span>
            {creating ? t("backups.creatingBackup") : t("backups.manualBackup")}
          </button>
        </div>
      </div>

      {/* Liste */}
      <div className="backups-list">
        <div className="backups-list-head">
          <h4>{t("backups.history")}</h4>
          <button
            type="button"
            className="secondary-btn"
            onClick={() => void reload()}
            disabled={loading}
          >
            <span className="material-symbols-outlined">refresh</span>
            {t("backups.refresh")}
          </button>
        </div>
        {loading && backups.length === 0 ? (
          <div className="backups-empty">{t("common.loading")}</div>
        ) : backups.length === 0 ? (
          <div className="backups-empty">{t("backups.empty")}</div>
        ) : (
          <div className="backups-list-table-wrap">
          <table className="backups-table">
            <thead>
              <tr>
                <th>{t("backups.table.createdAt")}</th>
                <th>{t("backups.table.type")}</th>
                <th>{t("backups.table.status")}</th>
                <th>{t("backups.table.size")}</th>
                <th>{t("backups.table.createdBy")}</th>
                <th>{t("backups.table.filename")}</th>
                <th>{t("backups.table.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((b) => {
                const sc = STATUS_COLOR[b.status] ?? "#64748b";
                return (
                  <tr key={b.id}>
                    <td>{fmtDate(b.created_at, localeTag)}</td>
                    <td>
                      <span
                        className={`backups-type-pill is-${b.job_type}`}
                        title={b.job_type}
                      >
                        {t(`backups.type.${b.job_type}`, { defaultValue: b.job_type })}
                      </span>
                    </td>
                    <td>
                      <span
                        className="backups-status-pill"
                        style={{ background: `${sc}22`, color: sc }}
                        title={b.error_message ?? undefined}
                      >
                        {t(`backups.status.${b.status}`, { defaultValue: b.status })}
                      </span>
                    </td>
                    <td className="backups-cell-mono">{fmtBytes(b.size_bytes)}</td>
                    <td>{b.created_by_username ?? "—"}</td>
                    <td className="backups-cell-mono backups-cell-filename">
                      {b.filename ?? "—"}
                    </td>
                    <td>
                      <div className="backups-actions">
                        <button
                          type="button"
                          className="icon-btn"
                          title={t("backups.actions.download")}
                          aria-label={t("backups.actions.download")}
                          disabled={b.status !== "success"}
                          onClick={() => void handleDownload(b)}
                        >
                          <span className="material-symbols-outlined">download</span>
                        </button>
                        <button
                          type="button"
                          className="icon-btn icon-btn-warn"
                          title={t("backups.actions.restore")}
                          aria-label={t("backups.actions.restore")}
                          disabled={b.status !== "success" || restoringId === b.id}
                          onClick={() => setConfirmRestoreId(b.id)}
                        >
                          {restoringId === b.id ? (
                            <span className="material-symbols-outlined">hourglass_top</span>
                          ) : (
                            <span className="material-symbols-outlined">restart_alt</span>
                          )}
                        </button>
                        <button
                          type="button"
                          className="icon-btn icon-btn-danger"
                          title={t("backups.actions.delete")}
                          aria-label={t("backups.actions.delete")}
                          onClick={() => {
                            if (b.status === "failed" && b.error_message) {
                              toast.error(b.error_message);
                            }
                            void handleDelete(b.id);
                          }}
                        >
                          <span className="material-symbols-outlined">delete</span>
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {/* Periyodik program modali — ayri popup */}
      {scheduleModalOpen && schedule ? (
        <div
          className="backups-confirm-backdrop"
          onClick={() => setScheduleModalOpen(false)}
        >
          <div
            className="backups-confirm-modal backups-schedule-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="backups-confirm-icon">
              <span className="material-symbols-outlined">schedule</span>
            </div>
            <h3>{t("backups.schedule.title")}</h3>
            <p className="helper-text">{t("backups.schedule.description")}</p>
            <div className="backups-schedule-fields backups-schedule-fields--modal">
              <label className="backups-schedule-toggle">
                <input
                  type="checkbox"
                  checked={schedule.enabled}
                  onChange={(e) =>
                    void handleScheduleChange({ enabled: e.target.checked })
                  }
                  disabled={savingSchedule}
                />
                <span>
                  {schedule.enabled
                    ? t("backups.schedule.enabled")
                    : t("backups.schedule.disabled")}
                </span>
              </label>
              <label className="backups-schedule-field">
                <span>{t("backups.schedule.intervalHours")}</span>
                <input
                  type="number"
                  min={1}
                  max={720}
                  value={schedule.interval_hours}
                  onChange={(e) =>
                    setSchedule((prev) =>
                      prev ? { ...prev, interval_hours: Number(e.target.value) } : prev
                    )
                  }
                  onBlur={() =>
                    void handleScheduleChange({
                      interval_hours: schedule.interval_hours
                    })
                  }
                  disabled={savingSchedule || !schedule.enabled}
                />
              </label>
              <label className="backups-schedule-field">
                <span>{t("backups.schedule.retentionCount")}</span>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={schedule.retention_count}
                  onChange={(e) =>
                    setSchedule((prev) =>
                      prev
                        ? { ...prev, retention_count: Number(e.target.value) }
                        : prev
                    )
                  }
                  onBlur={() =>
                    void handleScheduleChange({
                      retention_count: schedule.retention_count
                    })
                  }
                  disabled={savingSchedule}
                />
              </label>
            </div>
            {schedule.last_run_at ? (
              <div className="backups-schedule-last">
                <span>{t("backups.schedule.lastRun")}</span>
                <strong>{fmtDate(schedule.last_run_at, localeTag)}</strong>
              </div>
            ) : null}
            <div className="backups-confirm-actions">
              <button
                type="button"
                className="primary-btn"
                onClick={() => setScheduleModalOpen(false)}
              >
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Geri yukleme onay modali */}
      {confirmJob ? (
        <div
          className="backups-confirm-backdrop"
          onClick={() => setConfirmRestoreId(null)}
        >
          <div
            className="backups-confirm-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="backups-confirm-icon">
              <span className="material-symbols-outlined">warning</span>
            </div>
            <h3>{t("backups.restore.title")}</h3>
            <p>
              <strong>{confirmJob.filename ?? `Backup #${confirmJob.id}`}</strong>{" "}
              {t("backups.restore.warningPrefix")}
            </p>
            <p className="backups-confirm-warn">
              {t("backups.restore.warningBody")}{" "}
              <strong>{t("backups.restore.warningOverwrite")}</strong>
              {t("backups.restore.warningEnd")}
            </p>
            <div className="backups-confirm-actions">
              <button
                type="button"
                className="secondary-btn"
                onClick={() => setConfirmRestoreId(null)}
              >
                {t("backups.restore.cancel")}
              </button>
              <button
                type="button"
                className="primary-btn backups-confirm-restore"
                onClick={() => void handleRestoreConfirmed()}
              >
                {t("backups.restore.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
