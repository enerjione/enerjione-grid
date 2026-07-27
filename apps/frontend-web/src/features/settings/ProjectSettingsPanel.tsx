/** Mühendislik > Proje Ayarlari (sadece INSTALLER).
 *
 * Müsteri adi, proje adi, login logosu, header logosu (light) burada
 * yonetilir. Logolar base64 data URL olarak DB'ye kaydedilir; UI'in heryerinde
 * (login + header) ProjectSettingsProvider uzerinden yansir.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useProjectSettings } from "../../components/ProjectSettingsProvider";
import { useToast } from "../../components/ToastProvider";
import type { ProjectSettings } from "../../shared/types";

const MAX_FILE_SIZE = 1_000_000; // 1 MB (logo, favicon)
const MAX_LOGIN_IMAGE_SIZE = 2_500_000; // 2.5 MB (login dekoratif gorsel daha buyuk olabilir)
const ACCEPT = "image/png,image/jpeg,image/svg+xml,image/webp";
const ACCEPT_FAVICON = "image/x-icon,image/png,image/svg+xml,image/vnd.microsoft.icon";

type Props = {
  onSave: (payload: ProjectSettings) => Promise<void>;
};

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function ProjectSettingsPanel({ onSave }: Props) {
  const { t } = useTranslation();
  const { settings, refresh } = useProjectSettings();
  // Hata/basari mesajlari sayfanin altinda satir olarak degil toast ile
  // gosterilir — uzun formda alta scroll etmeden geri bildirim alinir.
  const toast = useToast();

  const [projectName, setProjectName] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [customerLogo, setCustomerLogo] = useState<string | null>(null);
  const [customerLogoLight, setCustomerLogoLight] = useState<string | null>(null);
  const [batteryLow, setBatteryLow] = useState<string>("");
  const [batteryFull, setBatteryFull] = useState<string>("");
  const [siteTitle, setSiteTitle] = useState("");
  const [favicon, setFavicon] = useState<string | null>(null);
  const [loginImage, setLoginImage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setProjectName(settings.project_name ?? "");
    setCustomerName(settings.customer_name ?? "");
    setCustomerLogo(settings.customer_logo ?? null);
    setCustomerLogoLight(settings.customer_logo_light ?? null);
    setBatteryLow(
      settings.battery_voltage_low !== null && settings.battery_voltage_low !== undefined
        ? String(settings.battery_voltage_low)
        : ""
    );
    setBatteryFull(
      settings.battery_voltage_full !== null && settings.battery_voltage_full !== undefined
        ? String(settings.battery_voltage_full)
        : ""
    );
    setSiteTitle(settings.site_title ?? "");
    setFavicon(settings.favicon ?? null);
    setLoginImage(settings.login_image ?? null);
  }, [settings]);

  const handlePickLogo = async (
    file: File | undefined,
    setter: (val: string | null) => void,
    maxSize = MAX_FILE_SIZE
  ) => {
    if (!file) return;
    if (file.size > maxSize) {
      toast.error(t("engineering.projectSettings.fileTooLarge", { kb: Math.round(maxSize / 1024) }));
      return;
    }
    try {
      const url = await readFileAsDataUrl(file);
      setter(url);
    } catch {
      toast.error(t("engineering.projectSettings.fileReadFailed"));
    }
  };

  const handleSave = async () => {
    setSaving(true);
    const lowNum = batteryLow.trim() === "" ? null : Number(batteryLow);
    const fullNum = batteryFull.trim() === "" ? null : Number(batteryFull);
    if (lowNum !== null && (!Number.isFinite(lowNum) || lowNum < 0 || lowNum > 10)) {
      toast.error(t("engineering.projectSettings.batteryLowInvalid"));
      setSaving(false);
      return;
    }
    if (fullNum !== null && (!Number.isFinite(fullNum) || fullNum < 0 || fullNum > 10)) {
      toast.error(t("engineering.projectSettings.batteryFullInvalid"));
      setSaving(false);
      return;
    }
    if (lowNum !== null && fullNum !== null && fullNum <= lowNum) {
      toast.error(t("engineering.projectSettings.batteryOrderInvalid"));
      setSaving(false);
      return;
    }
    try {
      await onSave({
        project_name: projectName.trim() || null,
        customer_name: customerName.trim() || null,
        customer_logo: customerLogo,
        customer_logo_light: customerLogoLight,
        battery_voltage_low: lowNum,
        battery_voltage_full: fullNum,
        site_title: siteTitle.trim() || null,
        favicon: favicon,
        login_image: loginImage
      });
      await refresh();
      toast.success(t("engineering.projectSettings.saveSuccess"));
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("engineering.projectSettings.saveFailed")
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="tab-panel project-settings-panel project-settings-panel--wide">
      {/* Sayfa basligi kaldirildi — sekme cubugu zaten "Proje Ayarlari"
          diyor. Icerik kendi icinde kayar, Kaydet butonu altta sabit bir
          aksiyon cubugunda durur; boylece uzun formun neresinde olursaniz
          olun buton gorunur kalir. */}
      <div className="project-settings-body">
        <div className="project-settings-grid">
        <div className="project-settings-field">
          <label>
            {t("engineering.projectSettings.projectName")}
            <input
              type="text"
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder={t("engineering.projectSettings.projectNamePlaceholder")}
            />
          </label>
          <label>
            {t("engineering.projectSettings.customerName")}
            <input
              type="text"
              value={customerName}
              onChange={(event) => setCustomerName(event.target.value)}
              placeholder={t("engineering.projectSettings.customerNamePlaceholder")}
            />
          </label>
          <label>
            {t("engineering.projectSettings.siteTitle")}
            <input
              type="text"
              value={siteTitle}
              onChange={(event) => setSiteTitle(event.target.value)}
              placeholder={t("engineering.projectSettings.siteTitlePlaceholder")}
              maxLength={200}
            />
          </label>
        </div>

        <div className="project-settings-logo-grid">
          <LogoBox
            title={t("engineering.projectSettings.loginLogoTitle")}
            value={customerLogo}
            onPick={(file) => void handlePickLogo(file, setCustomerLogo)}
            onClear={() => setCustomerLogo(null)}
            previewClass="project-settings-logo-preview project-settings-logo-preview--light"
            buttonLabel={t("engineering.projectSettings.pickLogoBtn")}
            emptyLabel={t("engineering.projectSettings.emptyLogo")}
            removeLabel={t("engineering.projectSettings.remove")}
          />
          <LogoBox
            title={t("engineering.projectSettings.headerLogoTitle")}
            value={customerLogoLight}
            onPick={(file) => void handlePickLogo(file, setCustomerLogoLight)}
            onClear={() => setCustomerLogoLight(null)}
            previewClass="project-settings-logo-preview project-settings-logo-preview--dark"
            buttonLabel={t("engineering.projectSettings.pickLogoBtn")}
            emptyLabel={t("engineering.projectSettings.emptyLogo")}
            removeLabel={t("engineering.projectSettings.remove")}
          />
          <LogoBox
            title={t("engineering.projectSettings.faviconTitle")}
            value={favicon}
            onPick={(file) => void handlePickLogo(file, setFavicon, MAX_FILE_SIZE)}
            onClear={() => setFavicon(null)}
            previewClass="project-settings-logo-preview project-settings-favicon-preview"
            accept={ACCEPT_FAVICON}
            buttonLabel={t("engineering.projectSettings.pickFaviconBtn")}
            emptyLabel={t("engineering.projectSettings.emptyFavicon")}
            removeLabel={t("engineering.projectSettings.remove")}
          />
          <LogoBox
            title={t("engineering.projectSettings.loginImageTitle")}
            value={loginImage}
            onPick={(file) => void handlePickLogo(file, setLoginImage, MAX_LOGIN_IMAGE_SIZE)}
            onClear={() => setLoginImage(null)}
            previewClass="project-settings-logo-preview project-settings-login-image-preview"
            buttonLabel={t("engineering.projectSettings.pickImageBtn")}
            emptyLabel={t("engineering.projectSettings.emptyImage")}
            removeLabel={t("engineering.projectSettings.remove")}
          />
        </div>

        <div className="project-settings-battery-box">
          <div className="project-settings-battery-head">
            <span className="project-settings-battery-icon material-symbols-outlined" aria-hidden="true">
              battery_charging_full
            </span>
            <h4>{t("engineering.projectSettings.batteryTitle")}</h4>
          </div>
          <div className="project-settings-battery-grid">
            <label className="project-settings-battery-field">
              <span className="project-settings-battery-label">
                {t("engineering.projectSettings.batteryLow")}
              </span>
              <div className="project-settings-battery-input-wrap">
                <input
                  type="number"
                  step="0.01"
                  min={0}
                  max={10}
                  placeholder="3.40"
                  value={batteryLow}
                  onChange={(event) => setBatteryLow(event.target.value)}
                />
                <span className="project-settings-battery-unit">V</span>
              </div>
            </label>
            <label className="project-settings-battery-field">
              <span className="project-settings-battery-label">
                {t("engineering.projectSettings.batteryFull")}
              </span>
              <div className="project-settings-battery-input-wrap">
                <input
                  type="number"
                  step="0.01"
                  min={0}
                  max={10}
                  placeholder="3.71"
                  value={batteryFull}
                  onChange={(event) => setBatteryFull(event.target.value)}
                />
                <span className="project-settings-battery-unit">V</span>
              </div>
            </label>
          </div>
          </div>
        </div>
      </div>

      <div className="project-settings-actions">
        <button
          type="button"
          className="primary-btn project-settings-save"
          disabled={saving}
          onClick={() => void handleSave()}
        >
          <span className="material-symbols-outlined" aria-hidden="true">save</span>
          {saving ? t("engineering.projectSettings.saving") : t("engineering.projectSettings.save")}
        </button>
      </div>
    </section>
  );
}

type LogoBoxProps = {
  title: string;
  value: string | null;
  onPick: (file: File | undefined) => void;
  onClear: () => void;
  previewClass: string;
  accept?: string;
  buttonLabel: string;
  emptyLabel: string;
  removeLabel: string;
};

function LogoBox({
  title,
  value,
  onPick,
  onClear,
  previewClass,
  accept = ACCEPT,
  buttonLabel,
  emptyLabel,
  removeLabel
}: LogoBoxProps) {
  return (
    <div className="project-settings-logo-box">
      <div className="project-settings-logo-head">
        <h4>{title}</h4>
        {value ? (
          <button type="button" className="text-btn" onClick={onClear}>
            {removeLabel}
          </button>
        ) : null}
      </div>
      <div className={previewClass}>
        {value ? (
          <img src={value} alt={title} />
        ) : (
          <span className="project-settings-logo-empty">{emptyLabel}</span>
        )}
      </div>
      <label className="project-settings-logo-upload">
        <input
          type="file"
          accept={accept}
          onChange={(event) => onPick(event.target.files?.[0])}
        />
        <span>{buttonLabel}</span>
      </label>
    </div>
  );
}
