import { Fragment, useEffect, useMemo, useRef, useState } from "react";
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
      toast.error(err instanceof Error ? err.message : "Yedekler alınamadı.");
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
      toast.success("Yedek alındı.");
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Yedek alınamadı.");
    } finally {
      setCreating(false);
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChosen = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // input'u resetle ki ayni dosya tekrar secilebilsin
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
    if (!window.confirm("Bu yedek kaydını ve dosyasını silmek istediğinizden emin misiniz?")) {
      return;
    }
    try {
      await deleteBackup(accessToken, id);
      toast.success("Yedek silindi.");
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Silinemedi.");
    }
  };

  const handleDownload = async (job: BackupJob) => {
    try {
      const filename = job.filename || `hsl-backup-${job.id}.dump`;
      await downloadBackupFile(accessToken, job.id, filename);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "İndirilemedi.");
    }
  };

  const handleRestoreConfirmed = async () => {
    if (confirmRestoreId == null) return;
    const id = confirmRestoreId;
    setConfirmRestoreId(null);
    setRestoringId(id);
    try {
      await restoreBackup(accessToken, id);
      toast.success("Geri yükleme tamamlandı. Sistem verileri güncellendi.");
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Geri yükleme başarısız.");
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
      toast.success("Yedek programı güncellendi.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Güncellenemedi.");
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
      {/* Baslik kaldirildi — sekme adi zaten "Yedekler" gosteriyor.
          Sag ust: yeni yedek + indirilmis yedek dosyasi yukle butonlari. */}
      <div className="backups-head backups-head--actions-only">
        <input
          ref={fileInputRef}
          type="file"
          accept=".dump"
          style={{ display: "none" }}
          onChange={(e) => void handleFileChosen(e)}
        />
        <button
          type="button"
          className="secondary-btn backups-upload-btn"
          onClick={handleUploadClick}
          disabled={uploading}
          title={t("backups.uploadHint")}
        >
          <span className="material-symbols-outlined">upload_file</span>
          {uploading ? t("backups.uploading") : t("backups.uploadBackup")}
        </button>
        <button
          type="button"
          className="primary-btn backups-create-btn"
          onClick={() => void handleCreate()}
          disabled={creating}
        >
          <span className="material-symbols-outlined">backup</span>
          {creating ? t("backups.creatingBackup") : t("backups.manualBackup")}
        </button>
      </div>

      {/* Sayaç chip'leri */}
      <div className="backups-stats">
        <div className="backups-stat-chip">
          <span className="backups-stat-num">{stats.total}</span>
          <span className="backups-stat-label">Toplam</span>
        </div>
        <div className="backups-stat-chip is-ok">
          <span className="backups-stat-num">{stats.success}</span>
          <span className="backups-stat-label">Başarılı</span>
        </div>
        <div className="backups-stat-chip is-fail">
          <span className="backups-stat-num">{stats.failed}</span>
          <span className="backups-stat-label">Başarısız</span>
        </div>
        <div className="backups-stat-chip is-size">
          <span className="backups-stat-num">{fmtBytes(stats.totalSize)}</span>
          <span className="backups-stat-label">Toplam Boyut</span>
        </div>
      </div>

      {/* Periyodik program kartı */}
      {schedule ? (
        <div className="backups-schedule-card">
          <div className="backups-schedule-icon">
            <span className="material-symbols-outlined">schedule</span>
          </div>
          <div className="backups-schedule-body">
            <h4>Periyodik Yedek Programı</h4>
            <p className="helper-text">
              Açıkken belirtilen aralıkta otomatik yedek alınır; tutulacak
              yedek sayısını aştığında en eski "otomatik" yedekler silinir.
            </p>
            <div className="backups-schedule-fields">
              <label className="backups-schedule-toggle">
                <input
                  type="checkbox"
                  checked={schedule.enabled}
                  onChange={(e) =>
                    void handleScheduleChange({ enabled: e.target.checked })
                  }
                  disabled={savingSchedule}
                />
                <span>{schedule.enabled ? "Açık" : "Kapalı"}</span>
              </label>
              <label className="backups-schedule-field">
                <span>Aralık (saat)</span>
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
                <span>Tutulacak yedek sayısı</span>
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
              {schedule.last_run_at ? (
                <div className="backups-schedule-last">
                  <span>Son otomatik yedek:</span>
                  <strong>{fmtDate(schedule.last_run_at, localeTag)}</strong>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {/* Liste */}
      <div className="backups-list">
        <div className="backups-list-head">
          <h4>Yedek Geçmişi</h4>
          <button
            type="button"
            className="secondary-btn"
            onClick={() => void reload()}
            disabled={loading}
          >
            <span className="material-symbols-outlined">refresh</span>
            Yenile
          </button>
        </div>
        {loading && backups.length === 0 ? (
          <div className="backups-empty">Yükleniyor…</div>
        ) : backups.length === 0 ? (
          <div className="backups-empty">
            Henüz yedek yok. Üstten "Yeni Yedek Al" butonu ile başlayabilirsiniz.
          </div>
        ) : (
          <div className="backups-list-table-wrap">
          <table className="backups-table">
            <thead>
              <tr>
                <th>Tarih</th>
                <th>Tip</th>
                <th>Durum</th>
                <th>Boyut</th>
                <th>Oluşturan</th>
                <th>Dosya</th>
                <th>İşlem</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((b) => {
                const sc = STATUS_COLOR[b.status] ?? "#64748b";
                return (
                  <Fragment key={b.id}>
                    <tr>
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
                            title="İndir"
                            aria-label="İndir"
                            disabled={b.status !== "success"}
                            onClick={() => void handleDownload(b)}
                          >
                            <span className="material-symbols-outlined">download</span>
                          </button>
                          <button
                            type="button"
                            className="icon-btn icon-btn-warn"
                            title="Geri Yükle"
                            aria-label="Geri Yükle"
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
                            title="Sil"
                            aria-label="Sil"
                            onClick={() => void handleDelete(b.id)}
                          >
                            <span className="material-symbols-outlined">delete</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                    {b.status === "failed" && b.error_message ? (
                      <tr className="backups-error-row">
                        <td colSpan={7}>
                          <div className="backups-error-banner">
                            <span className="material-symbols-outlined">error</span>
                            <div>
                              <strong>Hata</strong>
                              <code>{b.error_message}</code>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

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
            <h3>Yedeği Geri Yükle</h3>
            <p>
              <strong>{confirmJob.filename ?? `Yedek #${confirmJob.id}`}</strong>{" "}
              dosyasındaki veritabanı durumunu geri yükleyeceksiniz.
            </p>
            <p className="backups-confirm-warn">
              Bu işlem mevcut veritabanı içeriğinin{" "}
              <strong>üzerine yazar</strong>. Geri alınamaz. Yedeklerden sonraki
              tüm değişiklikler silinir.
            </p>
            <div className="backups-confirm-actions">
              <button
                type="button"
                className="secondary-btn"
                onClick={() => setConfirmRestoreId(null)}
              >
                Vazgeç
              </button>
              <button
                type="button"
                className="primary-btn backups-confirm-restore"
                onClick={() => void handleRestoreConfirmed()}
              >
                Evet, Geri Yükle
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
