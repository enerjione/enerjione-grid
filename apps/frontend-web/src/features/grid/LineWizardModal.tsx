/**
 * LineWizardModal — Hat Sihirbazı.
 *
 * Adım 0: yol seçimi — Excel ile toplu (aktif) / Soru-cevap ile tek hat (yakında).
 * Excel yolu: şablon (mevcut veri dolu, ağaç görünüm) indir → doldur → yükle →
 * önizleme (dry-run) → onay (commit). Panel yenilenir.
 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  commitGridImport,
  downloadGridImportTemplate,
  previewGridImport,
  type GridImportPreview,
} from "../../shared/api";
import { useToast } from "../../components/ToastProvider";
import { useModalDialog } from "../../shared/useModalDialog";

type Props = {
  accessToken: string;
  onClose: () => void;
  onImported: () => void;
};

type Method = null | "excel" | "qa";

export function LineWizardModal({ accessToken, onClose, onImported }: Props) {
  const { t } = useTranslation();
  // ESC ile kapanma + odak tuzagi (modal disina Tab ile cikilamasin).
  const dialogRef = useModalDialog<HTMLDivElement>(onClose);
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [method, setMethod] = useState<Method>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<GridImportPreview | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState("");

  const handleDownload = async () => {
    setDownloading(true);
    setError("");
    try {
      await downloadGridImportTemplate(accessToken);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("engineering.grid.import.downloadFail"));
    } finally {
      setDownloading(false);
    }
  };

  const handlePickFile = () => fileInputRef.current?.click();

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    e.target.value = "";
    if (!selected) return;
    setFile(selected);
    setPreview(null);
    setError("");
    setPreviewing(true);
    try {
      const result = await previewGridImport(accessToken, selected);
      setPreview(result);
    } catch (err) {
      setFile(null);
      setError(err instanceof Error ? err.message : t("engineering.grid.import.previewFail"));
    } finally {
      setPreviewing(false);
    }
  };

  const handleCommit = async () => {
    if (!file) return;
    setCommitting(true);
    setError("");
    try {
      const result = await commitGridImport(accessToken, file);
      toast.success(
        t("engineering.grid.import.successToast", {
          regions: result.regions_created,
          lines: result.lines_created,
          poles: result.poles_created,
          devices: result.segments_created,
        })
      );
      onImported();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("engineering.grid.import.commitFail"));
    } finally {
      setCommitting(false);
    }
  };

  const hasErrors = (preview?.errors.length ?? 0) > 0;
  const hasValidRows = (preview?.poles ?? 0) > 0;

  return (
    <div className="settings-modal-backdrop" onClick={onClose}>
      <div
        className="settings-modal grid-wizard-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        ref={dialogRef}
      >
        <div className="grid-wizard-head">
          <h3>{t("engineering.grid.wizard.title")}</h3>
          {method ? (
            <button className="grid-wizard-back" onClick={() => setMethod(null)}>
              <span className="material-symbols-outlined">arrow_back</span>
              {t("engineering.grid.wizard.back")}
            </button>
          ) : null}
        </div>

        {/* Adım 0 — Yol seçimi */}
        {method === null ? (
          <div className="grid-wizard-methods">
            <p className="helper-text">{t("engineering.grid.wizard.chooseMethod")}</p>
            <div className="grid-wizard-cards">
              <button className="grid-wizard-card" onClick={() => setMethod("excel")}>
                <span className="material-symbols-outlined grid-wizard-card-icon">table_view</span>
                <strong>{t("engineering.grid.wizard.excelMethod")}</strong>
                <span className="grid-wizard-card-desc">
                  {t("engineering.grid.wizard.excelMethodDesc")}
                </span>
              </button>
              <button
                className="grid-wizard-card grid-wizard-card--soon"
                onClick={() => setMethod("qa")}
              >
                <span className="grid-wizard-soon-badge">
                  {t("engineering.grid.wizard.comingSoon")}
                </span>
                <span className="material-symbols-outlined grid-wizard-card-icon">forum</span>
                <strong>{t("engineering.grid.wizard.qaMethod")}</strong>
                <span className="grid-wizard-card-desc">
                  {t("engineering.grid.wizard.qaMethodDesc")}
                </span>
              </button>
            </div>
          </div>
        ) : null}

        {/* Soru-cevap yolu — placeholder (2. faz) */}
        {method === "qa" ? (
          <div className="grid-wizard-qa-placeholder">
            <span className="material-symbols-outlined">construction</span>
            <h4>{t("engineering.grid.wizard.qaComingTitle")}</h4>
            <p className="helper-text">{t("engineering.grid.wizard.qaComingHint")}</p>
          </div>
        ) : null}

        {/* Excel yolu */}
        {method === "excel" ? (
          <>
            <div className="grid-import-step">
              <div className="grid-import-step-num">1</div>
              <div className="grid-import-step-body">
                <strong>{t("engineering.grid.import.step1Title")}</strong>
                <p className="helper-text">{t("engineering.grid.import.step1Hint")}</p>
                <button className="grid-import-btn-soft" onClick={handleDownload} disabled={downloading}>
                  <span className="material-symbols-outlined">download</span>
                  {downloading
                    ? t("engineering.grid.import.downloading")
                    : t("engineering.grid.import.downloadTemplate")}
                </button>
              </div>
            </div>

            <div className="grid-import-step">
              <div className="grid-import-step-num">2</div>
              <div className="grid-import-step-body">
                <strong>{t("engineering.grid.import.step2Title")}</strong>
                <p className="helper-text">{t("engineering.grid.import.step2Hint")}</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".xlsx"
                  style={{ display: "none" }}
                  onChange={handleFileChange}
                />
                <button
                  className={`grid-import-filepick ${file ? "has-file" : ""}`}
                  onClick={handlePickFile}
                  disabled={previewing}
                >
                  <span className="material-symbols-outlined">upload_file</span>
                  {file ? file.name : t("engineering.grid.import.selectFile")}
                </button>
                {previewing ? (
                  <p className="helper-text">{t("engineering.grid.import.analyzing")}</p>
                ) : null}
              </div>
            </div>

            {preview ? (
              <div className="grid-import-preview">
                <div className="grid-import-summary">
                  <SummaryCard value={preview.regions} label={t("engineering.grid.import.regions")} />
                  <SummaryCard value={preview.lines} label={t("engineering.grid.import.lines")} />
                  <SummaryCard value={preview.poles} label={t("engineering.grid.import.poles")} />
                  <SummaryCard value={preview.devices} label={t("engineering.grid.import.devices")} />
                </div>
                {hasErrors ? (
                  <div className="grid-import-errors">
                    <strong>
                      {t("engineering.grid.import.errorsTitle", { count: preview.errors.length })}
                    </strong>
                    <ul>
                      {preview.errors.slice(0, 50).map((e, i) => (
                        <li key={i}>
                          <span className="grid-import-error-row">
                            {t("engineering.grid.import.rowLabel", { row: e.row })}
                          </span>
                          {e.message}
                        </li>
                      ))}
                    </ul>
                    {preview.errors.length > 50 ? (
                      <p className="helper-text">
                        {t("engineering.grid.import.moreErrors", { count: preview.errors.length - 50 })}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="grid-import-ok">
                    <span className="material-symbols-outlined">check_circle</span>
                    {t("engineering.grid.import.noErrors")}
                  </p>
                )}
              </div>
            ) : null}

            {error ? <p className="error-text">{error}</p> : null}

            <div className="settings-actions grid-import-actions">
              <button className="grid-import-btn-cancel" onClick={onClose} disabled={committing}>
                {t("common.cancel")}
              </button>
              <button
                className="grid-import-btn-primary"
                onClick={handleCommit}
                disabled={!file || !hasValidRows || committing || previewing}
                title={hasErrors ? t("engineering.grid.import.commitWithErrorsHint") : undefined}
              >
                <span className="material-symbols-outlined">table_view</span>
                {committing
                  ? t("engineering.grid.import.importing")
                  : hasErrors
                    ? t("engineering.grid.import.importValidOnly")
                    : t("engineering.grid.import.import")}
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

function SummaryCard({ value, label }: { value: number; label: string }) {
  return (
    <div className="grid-import-summary-card">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
