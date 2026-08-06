import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { asyncConfirm } from "../../components/ConfirmDialog";
import { useTranslation } from "react-i18next";

import {
  createManualBackup,
  deleteBackup,
  downloadBackupFile,
  fetchBackups,
  fetchBackupSchedule,
  getRestoreStatus,
  restartBackend,
  restoreBackup,
  updateBackupSchedule,
  uploadBackupFile
} from "../../shared/api";
import type { RestoreStatus } from "../../shared/api";
import type { BackupJob, BackupSchedule } from "../../shared/types";
import { useToast } from "../../components/ToastProvider";
import { usePolling } from "../../shared/usePolling";

type Props = {
  accessToken: string;
  /** Mevcut kullanici rolu — engineer ise 'Geri Yukle' butonu disabled.
   *  Engineer yedek alabilir (POST /backups/create) ama restore yapamaz. */
  currentRole?: "operator" | "engineer" | "installer" | "ops_manager";
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

/** "2 saat once" tarzi goreceli sure — Intl ile, ekstra ceviri anahtari yok. */
function fmtRelative(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  const diffMin = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  const rtf = new Intl.RelativeTimeFormat(localeTag, { numeric: "auto" });
  if (Math.abs(diffMin) < 60) return rtf.format(-diffMin, "minute");
  const diffHour = Math.round(diffMin / 60);
  if (Math.abs(diffHour) < 24) return rtf.format(-diffHour, "hour");
  return rtf.format(-Math.round(diffHour / 24), "day");
}

/** Yedek tipine gore satir ikonu. */
const TYPE_ICON: Record<string, string> = {
  manual: "person",
  scheduled: "schedule",
  uploaded: "upload_file"
};

/** Panel ust bandinin sagligi — ikon + renk buna gore secilir. */
type BackupHealth = "ok" | "stale" | "fail" | "empty";

const HEALTH_ICON: Record<BackupHealth, string> = {
  ok: "verified_user",
  stale: "gpp_maybe",
  fail: "gpp_bad",
  empty: "cloud_off"
};

/** Durum seridi tonu — uygulama genelindeki `net-banner` paleti. */
const HEALTH_TONE: Record<BackupHealth, string> = {
  ok: "net-banner--ok",
  stale: "net-banner--warn",
  fail: "net-banner--bad",
  empty: "net-banner--info"
};

/** Otomatik program kapaliysa bu suredan eski yedek "eski" sayilir. */
const STALE_FALLBACK_HOURS = 24 * 7;

export function BackupsPanel({ accessToken, currentRole }: Props) {
  const canRestore = currentRole !== "engineer";
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
  const [confirmRestart, setConfirmRestart] = useState(false);
  const [restarting, setRestarting] = useState(false);
  // Restore progress modal — restore'a basildiginda acilir, polling ile
  // backend'den her 1.5sn'de bir status alir. status 'done'/'failed' olunca
  // polling durur, "Kapat" butonu aktif olur.
  const [restoreStatus, setRestoreStatus] = useState<RestoreStatus | null>(null);
  const [restoreModalOpen, setRestoreModalOpen] = useState(false);
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

  usePolling({ enabled: Boolean(accessToken), intervalMs: 15000, fn: reload });

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
    if (!await asyncConfirm(t("backups.deleteConfirm"))) {
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
    // Progress modal'i hemen ac — backend POST'un cevabini beklemeden bile
    // kullaniciya "baslatiyoruz" hissi ver.
    setRestoreModalOpen(true);
    setRestoreStatus({
      backup_id: id,
      filename: null,
      status: "queued",
      current_step: "queued",
      step_index: 0,
      total_steps: 5,
      progress_percent: 0,
      message: t("backups.restore.progress.starting"),
      error: null,
      started_by: null,
      started_at: null,
      finished_at: null,
      elapsed_sec: null,
      steps: ["queued", "validating", "preparing", "restoring", "finalizing", "done"],
      logs: [],
    });
    try {
      await restoreBackup(accessToken, id);
      // POST geri donduyse backend background thread baslatti; polling devreye girer.
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("backups.restore.fail");
      toast.error(msg);
      setRestoringId(null);
      setRestoreStatus((prev) =>
        prev ? { ...prev, status: "failed", error: msg, message: msg } : prev
      );
    }
  };

  // Restore polling — restoreModalOpen true iken her 1.5sn'de status cek.
  // status 'done' veya 'failed' olunca polling durur.
  useEffect(() => {
    if (!restoreModalOpen) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await getRestoreStatus(accessToken);
        if (cancelled) return;
        setRestoreStatus(status);
        if (status.status === "done") {
          setRestoringId(null);
          toast.success(t("backups.restore.success"));
          await reload();
        } else if (status.status === "failed") {
          setRestoringId(null);
          toast.error(status.error || t("backups.restore.fail"));
        }
      } catch (err) {
        // Backend gecici hata — polling'i durdurmayalim, bir sonraki tick yine deniyor.
        if (cancelled) return;
        // Restore sirasinda backend DB pool reset oluyor; gecici hata kabul.
        // eslint-disable-next-line no-console
        console.debug("restore_status_poll_error", err);
      }
    };
    // Hemen bir kez cek + interval kur
    void poll();
    const interval = window.setInterval(() => {
      // 'done' veya 'failed' olduktan sonra ekstra polling yapma — interval'i sil
      const s = restoreStatus?.status;
      if (s === "done" || s === "failed") {
        window.clearInterval(interval);
        return;
      }
      void poll();
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restoreModalOpen, accessToken]);

  const handleRestart = async () => {
    setRestarting(true);
    try {
      await restartBackend(accessToken);
      toast.success(t("backups.restart.success"));
      // Backend ~5sn icinde geri kalkar; otomatik bir yenileme kullaniciyi
      // hizla geri getirir. 7sn sonra reload dene; basarisiz olursa kullanici
      // F5 yapar.
      window.setTimeout(() => {
        try {
          window.location.reload();
        } catch {
          // ignore
        }
      }, 7000);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("backups.restart.fail"));
      setRestarting(false);
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

  // Backend siralamasina guvenmeyelim — en yeni ustte olsun.
  const sortedBackups = useMemo(
    () =>
      [...backups].sort((a, b) =>
        (b.created_at ?? "").localeCompare(a.created_at ?? "")
      ),
    [backups]
  );

  /** Ust bant: en son BASARILI yedek tazelik olcusu, en yeni kayit ise
   *  "son deneme patladi mi?" sinyali. */
  const { health, lastSuccess } = useMemo(() => {
    const newest = sortedBackups[0] ?? null;
    const ok = sortedBackups.find((b) => b.status === "success") ?? null;
    let h: BackupHealth;
    if (!newest) {
      h = "empty";
    } else if (newest.status === "failed" || !ok) {
      h = "fail";
    } else {
      const staleAfter =
        schedule?.enabled && schedule.interval_hours > 0
          ? schedule.interval_hours * 2
          : STALE_FALLBACK_HOURS;
      const ageHours = (Date.now() - new Date(ok.created_at).getTime()) / 3600000;
      h = ageHours > staleAfter ? "stale" : "ok";
    }
    return { health: h, lastSuccess: ok };
  }, [sortedBackups, schedule]);

  const confirmJob = useMemo(
    () => backups.find((b) => b.id === confirmRestoreId) ?? null,
    [confirmRestoreId, backups]
  );

  return (
    <section className="tab-panel backups-panel">
      <input
        ref={fileInputRef}
        type="file"
        accept=".dump"
        style={{ display: "none" }}
        onChange={(e) => void handleFileChosen(e)}
      />

      {/* DURUM SERIDI — eski "hero" blogunun yerine tek satir.
          Buyuk rozet + degrade + dort sayac karti sayfayi uygulamanin geri
          kalanindan bambaska gosteriyordu (kullanici istegi, 2026-08-06).
          Ayni bilgi burada: saglik + son yedek kunyesi; sayilar liste
          basligindaki sayac ve asagidaki satirlarda zaten var. */}
      <p className={`net-banner ${HEALTH_TONE[health]} bk-status`}>
        <span className="material-symbols-outlined">{HEALTH_ICON[health]}</span>
        <strong>{t(`backups.hero.title.${health}`)}</strong>
        {lastSuccess ? (
          <span className="bk-status-meta">
            {fmtDate(lastSuccess.created_at, localeTag)}
            <i>·</i>
            {fmtRelative(lastSuccess.created_at, localeTag)}
            <i>·</i>
            {fmtBytes(lastSuccess.size_bytes)}
            {lastSuccess.created_by_username ? (
              <>
                <i>·</i>
                {lastSuccess.created_by_username}
              </>
            ) : null}
          </span>
        ) : (
          <span className="bk-status-meta">{t("backups.hero.noneYet")}</span>
        )}
      </p>

      {/* Basarisiz yedek varsa sessiz gecmiyoruz — eski "0 BASARISIZ"
          sayac karti her zaman yer kapliyordu, bu serit yalnizca sorun
          varken cikar. */}
      {stats.failed > 0 ? (
        <p className="net-banner net-banner--bad bk-status">
          <span className="material-symbols-outlined">error</span>
          {t("backups.failedBanner", { count: stats.failed })}
        </p>
      ) : null}

      {/* Liste — tek kart; aksiyonlar basligin saginda (Guvenlik Duvari ve
          Ag Ayarlari sayfalariyla ayni gorsel dil). */}
      <section className="rad-card bk-card">
        <header className="rad-card-head bk-card-head">
          <h3>
            <span className="material-symbols-outlined">history</span>
            {t("backups.history")}
            <span className="bk-count">{backups.length}</span>
            {stats.totalSize > 0 ? (
              <span className="bk-count-size">{fmtBytes(stats.totalSize)}</span>
            ) : null}
          </h3>
          <div className="bk-head-actions">
            {/* Periyodik program: DURUM gostergesi + ayar girisi. */}
            <button
              type="button"
              className={`bk-schedule-chip ${schedule?.enabled ? "is-on" : "is-off"}`}
              onClick={() => setScheduleModalOpen(true)}
              title={`${t("backups.schedule.title")} — ${
                schedule?.enabled
                  ? t("backups.hero.retention", { count: schedule.retention_count })
                  : t("backups.hero.autoOffHint")
              }`}
            >
              <span className="material-symbols-outlined">
                {schedule?.enabled ? "autorenew" : "timer_off"}
              </span>
              {schedule?.enabled
                ? t("backups.hero.autoOn", { hours: schedule.interval_hours })
                : t("backups.hero.autoOff")}
            </button>
            <button
              type="button"
              className="secondary-btn bk-btn"
              onClick={() => void reload()}
              disabled={loading}
              title={t("backups.refresh")}
            >
              <span
                className={`material-symbols-outlined ${loading ? "bk-spin" : ""}`}
              >
                refresh
              </span>
              {t("backups.refresh")}
            </button>
            <button
              type="button"
              className="secondary-btn bk-btn"
              onClick={handleUploadClick}
              disabled={uploading}
              title={t("backups.uploadHint")}
            >
              <span className="material-symbols-outlined">upload_file</span>
              {uploading ? t("backups.uploading") : t("backups.uploadBackup")}
            </button>
            <button
              type="button"
              className="primary-btn bk-btn"
              onClick={() => void handleCreate()}
              disabled={creating}
            >
              <span className={`material-symbols-outlined ${creating ? "bk-spin" : ""}`}>
                {creating ? "progress_activity" : "backup"}
              </span>
              {creating ? t("backups.creatingBackup") : t("backups.manualBackup")}
            </button>
          </div>
        </header>
        {loading && backups.length === 0 ? (
          <p className="net-empty">{t("common.loading")}</p>
        ) : backups.length === 0 ? (
          <p className="net-empty">{t("backups.empty")}</p>
        ) : (
          <div className="bk-table-wrap">
          <table className="backups-table">
            <thead>
              <tr>
                <th scope="col">{t("backups.table.createdAt")}</th>
                <th scope="col">{t("backups.table.type")}</th>
                <th scope="col">{t("backups.table.status")}</th>
                <th scope="col">{t("backups.table.size")}</th>
                <th scope="col">{t("backups.table.createdBy")}</th>
                <th scope="col">{t("backups.table.filename")}</th>
                <th scope="col">{t("backups.table.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {sortedBackups.map((b) => {
                const showError = b.status === "failed" && !!b.error_message;
                return (
                  <Fragment key={b.id}>
                  <tr className={`backups-row is-${b.status}`}>
                    <td>
                      <div className="backups-date-cell">
                        <strong>{fmtDate(b.created_at, localeTag)}</strong>
                        <small>{fmtRelative(b.created_at, localeTag)}</small>
                      </div>
                    </td>
                    <td>
                      <span
                        className={`backups-type-pill is-${b.job_type}`}
                        title={b.job_type}
                      >
                        <span className="material-symbols-outlined">
                          {TYPE_ICON[b.job_type] ?? "backup"}
                        </span>
                        {t(`backups.type.${b.job_type}`, { defaultValue: b.job_type })}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`backups-status-pill is-${b.status}`}
                        title={b.error_message ?? undefined}
                      >
                        <span className="backups-status-dot" />
                        {t(`backups.status.${b.status}`, { defaultValue: b.status })}
                      </span>
                    </td>
                    <td className="backups-cell-mono">{fmtBytes(b.size_bytes)}</td>
                    <td>
                      {b.created_by_username ? (
                        b.created_by_username
                      ) : (
                        <span className="backups-muted">—</span>
                      )}
                    </td>
                    <td className="backups-cell-filename" title={b.filename ?? undefined}>
                      {b.filename ? (
                        <code>{b.filename}</code>
                      ) : (
                        <span className="backups-muted">—</span>
                      )}
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
                          title={
                            canRestore
                              ? t("backups.actions.restore")
                              : t("backups.actions.restoreEngineerForbidden")
                          }
                          aria-label={t("backups.actions.restore")}
                          disabled={!canRestore || b.status !== "success" || restoringId === b.id}
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
                          onClick={() => void handleDelete(b.id)}
                        >
                          <span className="material-symbols-outlined">delete</span>
                        </button>
                      </div>
                    </td>
                  </tr>
                  {/* Basarisiz yedegin hata detayi — tooltip'te saklamak yerine
                      satirin altinda acikca gosteriliyor. */}
                  {showError ? (
                    <tr className="backups-error-row">
                      <td colSpan={7}>
                        <div className="backups-error-banner">
                          <span className="material-symbols-outlined">error</span>
                          <div>
                            <strong>{t("backups.errorBanner")}</strong>
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

        {/* Sistemi yeniden baslatma bu sayfanin ANA isi degil (yedek/geri
            yukleme sonrasi bakim aksiyonu). Ust satirdaki turuncu vurgulu
            buton yerine kart altinda sakin bir satir — Guvenlik Duvari
            sayfasindaki kilitlenme korumasi notuyla ayni yerlesim. */}
        <p className="bk-foot">
          <span className="material-symbols-outlined">power_settings_new</span>
          <span>{t("backups.restart.title")}</span>
          <button
            type="button"
            className="bk-foot-btn"
            onClick={() => setConfirmRestart(true)}
            disabled={restarting}
          >
            {t("backups.restart.btn")}
          </button>
        </p>
      </section>

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

      {/* Restore progress modali — restore'a basildiginda acilir, polling
          ile backend'den adim adim ilerleme bilgisi alir. */}
      {restoreModalOpen && restoreStatus ? (
        <div className="backups-confirm-backdrop">
          <div
            className="backups-confirm-modal backups-restore-progress-modal"
            role="dialog"
            aria-modal="true"
          >
            <div className={`backups-confirm-icon restore-status-${restoreStatus.status}`}>
              <span className="material-symbols-outlined">
                {restoreStatus.status === "done"
                  ? "check_circle"
                  : restoreStatus.status === "failed"
                  ? "error"
                  : "progress_activity"}
              </span>
            </div>
            <h3>
              {restoreStatus.status === "done"
                ? t("backups.restore.progress.titleDone")
                : restoreStatus.status === "failed"
                ? t("backups.restore.progress.titleFailed")
                : t("backups.restore.progress.titleRunning")}
            </h3>
            {restoreStatus.filename ? (
              <p className="restore-progress-filename">
                <span className="material-symbols-outlined">folder_zip</span>
                {restoreStatus.filename}
              </p>
            ) : null}

            {/* Progress bar */}
            <div className="restore-progress-bar-wrap">
              <div className="restore-progress-bar-track">
                <div
                  className={`restore-progress-bar-fill restore-progress-bar-fill--${restoreStatus.status}`}
                  style={{ width: `${restoreStatus.progress_percent}%` }}
                />
              </div>
              <div className="restore-progress-bar-label">
                %{restoreStatus.progress_percent}
                {restoreStatus.elapsed_sec != null ? (
                  <span className="restore-progress-elapsed">
                    {" "}· {restoreStatus.elapsed_sec.toFixed(1)}s
                  </span>
                ) : null}
              </div>
            </div>

            {/* Adim listesi — her adim icin tik / spinner / hata */}
            <ul className="restore-progress-steps">
              {restoreStatus.steps.filter((s) => s !== "queued").map((stepName, idx) => {
                const stepIdx = restoreStatus.steps.indexOf(stepName);
                const isCurrent = restoreStatus.current_step === stepName;
                const isDone = stepIdx < restoreStatus.step_index || restoreStatus.status === "done";
                const isFailed = restoreStatus.status === "failed" && isCurrent;
                let iconName = "radio_button_unchecked";
                let cls = "step-pending";
                if (isFailed) {
                  iconName = "cancel";
                  cls = "step-failed";
                } else if (isDone) {
                  iconName = "check_circle";
                  cls = "step-done";
                } else if (isCurrent) {
                  iconName = "progress_activity";
                  cls = "step-active";
                }
                return (
                  <li key={stepName} className={`restore-step ${cls}`}>
                    <span className="material-symbols-outlined">{iconName}</span>
                    <span>{t(`backups.restore.progress.steps.${stepName}`, stepName)}</span>
                  </li>
                );
              })}
            </ul>

            {/* Mesaj / hata */}
            <p className="restore-progress-message">{restoreStatus.message}</p>
            {restoreStatus.error ? (
              <p className="restore-progress-error">{restoreStatus.error}</p>
            ) : null}

            {/* Aksiyon butonlari — sadece done/failed iken aktif */}
            <div className="backups-confirm-actions">
              {restoreStatus.status === "done" ? (
                <>
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={() => {
                      setRestoreModalOpen(false);
                      setRestoreStatus(null);
                    }}
                  >
                    {t("backups.restore.progress.close")}
                  </button>
                  <button
                    type="button"
                    className="primary-btn"
                    onClick={() => {
                      setRestoreModalOpen(false);
                      setConfirmRestart(true);
                    }}
                  >
                    {t("backups.restart.confirm")}
                  </button>
                </>
              ) : restoreStatus.status === "failed" ? (
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={() => {
                    setRestoreModalOpen(false);
                    setRestoreStatus(null);
                  }}
                >
                  {t("backups.restore.progress.close")}
                </button>
              ) : (
                <button
                  type="button"
                  className="secondary-btn"
                  disabled
                  title={t("backups.restore.progress.runningDisabled")}
                >
                  {t("backups.restore.progress.running")}
                </button>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {/* Sistem yeniden baslatma onay modali */}
      {confirmRestart ? (
        <div
          className="backups-confirm-backdrop"
          onClick={() => !restarting && setConfirmRestart(false)}
        >
          <div
            className="backups-confirm-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="backups-confirm-icon">
              <span className="material-symbols-outlined">power_settings_new</span>
            </div>
            <h3>{t("backups.restart.title")}</h3>
            <p>{t("backups.restart.warning")}</p>
            <p className="backups-confirm-warn">{t("backups.restart.downtime")}</p>
            <div className="backups-confirm-actions">
              <button
                type="button"
                className="secondary-btn"
                onClick={() => setConfirmRestart(false)}
                disabled={restarting}
              >
                {t("backups.restore.cancel")}
              </button>
              <button
                type="button"
                className="primary-btn backups-confirm-restore"
                onClick={() => void handleRestart()}
                disabled={restarting}
              >
                {restarting
                  ? t("backups.restart.inProgress")
                  : t("backups.restart.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
