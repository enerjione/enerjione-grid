import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { useToast } from "../../components/ToastProvider";
import { downloadLicenseRequest, fetchVersionInfo, importLicense } from "../../shared/api";
import type { LicenseStatus, VersionInfo } from "../../shared/types";


type Props = {
  accessToken: string;
  status: LicenseStatus | null;
  loading: boolean;
  onStatusChange: (status: LicenseStatus) => void;
  onRefresh: () => Promise<void>;
};

// Donut cevre uzunlugu — r=54 icin 2*pi*r. arc dasharray'i bunun uzerinden.
const DONUT_R = 54;
const DONUT_C = 2 * Math.PI * DONUT_R;

export function LicenseManagementPanel({
  accessToken,
  status,
  loading,
  onStatusChange,
  onRefresh
}: Props) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"request" | "import" | null>(null);
  // Yazilim surumu — bir kez cekilir, degismez (backend restart edilirse
  // sayfa da yenilenir).
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);
  useEffect(() => {
    let cancelled = false;
    void fetchVersionInfo(accessToken)
      .then((info) => {
        if (!cancelled) setVersionInfo(info);
      })
      .catch(() => {
        /* surum gosterimi kritik degil, sessiz gec */
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  const handleRequest = async () => {
    setBusy("request");
    try {
      await downloadLicenseRequest(accessToken);
      toast.success(t("engineering.license.requestDownloaded"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("common.errorOccurred"));
    } finally {
      setBusy(null);
    }
  };

  const handleImport = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 50 * 1024) {
      toast.error(t("engineering.license.fileTooLarge"));
      return;
    }
    setBusy("import");
    try {
      const updated = await importLicense(accessToken, file);
      onStatusChange(updated);
      toast.success(t("engineering.license.imported"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("common.errorOccurred"));
    } finally {
      setBusy(null);
    }
  };

  if (loading && !status) {
    return <section className="license-panel"><p className="helper-text">{t("common.loading")}</p></section>;
  }
  if (!status) {
    return (
      <section className="license-panel">
        <p className="error-text">{t("engineering.license.loadFailed")}</p>
        <button type="button" className="secondary-btn" onClick={() => void onRefresh()}>
          {t("common.retry")}
        </button>
      </section>
    );
  }

  const stateKey = status.is_valid ? status.quota_state : status.state;
  const isBlocked = !status.can_add_device;
  const hasLimit = status.device_limit > 0;
  const used = status.device_count;
  const limit = status.device_limit;
  const remaining = status.remaining;
  // Durum karti: gecerli+eklenebilir -> "Aktif Lisans". Aksi mevcut state cevirisi.
  const statusLabel = status.is_valid && !isBlocked
    ? t("engineering.license.states.active")
    : t(`engineering.license.states.${stateKey}`);
  const statusHint = !status.is_valid
    ? t("engineering.license.statusUnlicensedHint")
    : isBlocked
      ? t("engineering.license.statusBlockedHint")
      : t("engineering.license.statusActiveHint");
  const statusIcon = status.is_valid ? (isBlocked ? "lock" : "verified_user") : "shield";

  const usedFrac = hasLimit ? Math.min(1, used / limit) : 0;
  const arcLen = DONUT_C * usedFrac;

  const issuedAtText = status.issued_at
    ? new Date(status.issued_at).toLocaleString(i18n.language === "en" ? "en-GB" : "tr-TR", {
        dateStyle: "short",
        timeStyle: "short"
      })
    : "—";

  return (
    <section className="license-panel" aria-label={t("engineering.license.title")}>
      {/* Yazilim surumu — lisans bilgisiyle birlikte, destek/kayit
          taleplerinde ilk sorulan bilgi. Guncelleme durumu Sistem Durumu
          sayfasinda; burada sadece calisan surum yazar.
          Surum gelmeden (veya eski backend'de uc yoksa) HIC gosterilmez —
          bos bir "—" seridi birakmak yerine tamamen gizli. */}
      {/* Ust: uc durum karti */}
      <div className="license-status-cards">
        <article className={`license-stat-card license-stat-card--${stateKey}`}>
          <span className="license-stat-icon" aria-hidden="true">
            <span className="material-symbols-outlined">{statusIcon}</span>
          </span>
          <div>
            <span className="license-stat-label">{t("engineering.license.statusCard")}</span>
            <strong className="license-stat-title">{statusLabel}</strong>
            <p>{statusHint}</p>
          </div>
        </article>

        <article className={`license-stat-card license-stat-card--${status.is_valid ? "type" : "unavailable"}`}>
          <span className="license-stat-icon" aria-hidden="true">
            <span className="material-symbols-outlined">sell</span>
          </span>
          <div>
            <span className="license-stat-label">{t("engineering.license.typeCard")}</span>
            <strong className="license-stat-title">
              {status.is_valid
                ? t("engineering.license.typeStandard")
                : t("engineering.license.states.unavailable")}
            </strong>
            <p>
              {status.is_valid && hasLimit
                ? t("engineering.license.typeDeviceSupport", { limit })
                : t("engineering.license.statusUnlicensedHint")}
              {status.is_valid && hasLimit ? (
                <span className="license-inline-badge">{t("engineering.license.limitBadge", { limit })}</span>
              ) : null}
            </p>
          </div>
        </article>

        <article className={`license-stat-card license-stat-card--${status.is_valid ? "validity" : "unavailable"}`}>
          <span className="license-stat-icon" aria-hidden="true">
            <span className="material-symbols-outlined">all_inclusive</span>
          </span>
          <div>
            <span className="license-stat-label">{t("engineering.license.validityCard")}</span>
            <strong className="license-stat-title">
              {status.is_valid
                ? t("engineering.license.validityUnlimited")
                : t("engineering.license.states.unavailable")}
            </strong>
            <p>
              {status.is_valid
                ? t("engineering.license.validityUnlimitedHint")
                : t("engineering.license.statusUnlicensedHint")}
            </p>
          </div>
        </article>
      </div>

      {/* Orta: kullanim (donut) + bilgiler */}
      <div className="license-dashboard">
        <article className="license-capacity-card">
          <h3>{t("engineering.license.usageTitle")}</h3>
          <div className="license-usage-body">
            <div
              className="license-donut"
              role="img"
              aria-label={t("engineering.license.donutAria", { used, limit, remaining })}
            >
              <svg viewBox="0 0 128 128" width="190" height="190">
                <circle className="license-donut-track" cx="64" cy="64" r={DONUT_R} />
                {hasLimit ? (
                  <circle
                    className="license-donut-arc"
                    cx="64"
                    cy="64"
                    r={DONUT_R}
                    strokeDasharray={`${arcLen} ${DONUT_C - arcLen}`}
                    transform="rotate(-90 64 64)"
                  />
                ) : null}
              </svg>
              <div className="license-donut-center">
                <strong>{hasLimit ? limit : "—"}</strong>
                <span>{hasLimit ? t("engineering.license.totalLabel") : t("engineering.license.noCapacity")}</span>
              </div>
            </div>

            <ul className="license-usage-legend">
              <li>
                <span className="license-legend-dot license-legend-dot--used" aria-hidden="true" />
                <span className="license-legend-name">{t("engineering.license.usedDevices")}</span>
                <strong>{used}</strong>
              </li>
              <li>
                <span className="license-legend-dot license-legend-dot--free" aria-hidden="true" />
                <span className="license-legend-name">{t("engineering.license.unusedDevices")}</span>
                <strong>{hasLimit ? remaining : "—"}</strong>
              </li>
              <li className="license-usage-legend-total">
                <span className="license-legend-name">{t("engineering.license.totalCapacity")}</span>
                <strong>{hasLimit ? limit : "—"}</strong>
              </li>
            </ul>
          </div>
          {isBlocked ? (
            <div className="license-notice" role="status">
              <span className="material-symbols-outlined" aria-hidden="true">info</span>
              <span>
                {status.quota_state === "over_limit"
                  ? t("engineering.license.overLimitHint")
                  : t("engineering.license.blockedHint")}
              </span>
            </div>
          ) : null}
        </article>

        <article className="license-identity-card">
          <h3>{t("engineering.license.infoTitle")}</h3>
          <dl>
            {/* Surum burada: tam genislikteki serit cok yer kapliyordu.
                Yeni surum varsa ayni satirda rozet olarak gorunur. */}
            {versionInfo ? (
              <div>
                <dt>{t("engineering.license.appVersionLabel")}</dt>
                <dd>
                  v{versionInfo.current}
                  {versionInfo.update_available && versionInfo.latest ? (
                    <span
                      className="license-inline-badge license-update-badge"
                      title={t("engineering.license.updateHint")}
                    >
                      {t("engineering.license.updateAvailable", { version: versionInfo.latest })}
                    </span>
                  ) : null}
                </dd>
              </div>
            ) : null}
            <div><dt>{t("engineering.license.customerCode")}</dt><dd>{status.customer_code || "—"}</dd></div>
            <div><dt>{t("engineering.license.customerName")}</dt><dd>{status.customer_name || "—"}</dd></div>
            <div><dt>{t("engineering.license.projectName")}</dt><dd>{status.project_name || "—"}</dd></div>
            <div><dt>{t("engineering.license.licenseId")}</dt><dd className="license-mono">{status.license_id || "—"}</dd></div>
            <div><dt>{t("engineering.license.issuedAt")}</dt><dd>{issuedAtText}</dd></div>
            <div>
              <dt>{t("engineering.license.validityField")}</dt>
              <dd>
                <span className="license-inline-badge">
                  {status.is_valid
                    ? t("engineering.license.unlimitedBadge")
                    : t("engineering.license.states.unavailable")}
                </span>
              </dd>
            </div>
          </dl>
          {status.note ? <p className="license-note">{status.note}</p> : null}
        </article>
      </div>

      {/* Alt: lisans islemleri */}
      <article className="license-ops-card">
        <h3>{t("engineering.license.opsTitle")}</h3>
        <div className="license-ops-grid">
          <button
            type="button"
            className="license-op-btn"
            disabled={busy !== null}
            onClick={() => void handleRequest()}
          >
            <span className="license-op-icon" aria-hidden="true">
              <span className="material-symbols-outlined">download</span>
            </span>
            <span>{busy === "request" ? t("common.loading") : t("engineering.license.downloadRequest")}</span>
          </button>

          <button
            type="button"
            className="license-op-btn"
            disabled={busy !== null}
            onClick={() => fileRef.current?.click()}
          >
            <span className="license-op-icon" aria-hidden="true">
              <span className="material-symbols-outlined">upload_file</span>
            </span>
            <span>{busy === "import" ? t("common.loading") : t("engineering.license.importLicense")}</span>
          </button>

          <input
            ref={fileRef}
            className="sr-only"
            type="file"
            accept=".lic,application/json"
            onChange={(event) => void handleImport(event)}
          />
        </div>
      </article>
    </section>
  );
}
