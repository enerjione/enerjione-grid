/** Mühendislik > Proje Ayarlari (sadece INSTALLER).
 *
 * Müsteri adi, proje adi, login logosu, header logosu (light) burada
 * yonetilir. Logolar base64 data URL olarak DB'ye kaydedilir; UI'in heryerinde
 * (login + header) ProjectSettingsProvider uzerinden yansir.
 */
import { useEffect, useState } from "react";

import { useProjectSettings } from "../../components/ProjectSettingsProvider";
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
  const { settings, refresh } = useProjectSettings();

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
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

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
    setError("");
    setSuccess("");
    if (!file) return;
    if (file.size > maxSize) {
      setError(`Dosya çok büyük (max ${Math.round(maxSize / 1024)} KB).`);
      return;
    }
    try {
      const url = await readFileAsDataUrl(file);
      setter(url);
    } catch {
      setError("Dosya okunamadı.");
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    setSuccess("");
    const lowNum = batteryLow.trim() === "" ? null : Number(batteryLow);
    const fullNum = batteryFull.trim() === "" ? null : Number(batteryFull);
    if (lowNum !== null && (!Number.isFinite(lowNum) || lowNum < 0 || lowNum > 10)) {
      setError("Düşük voltaj geçersiz (0-10 V).");
      setSaving(false);
      return;
    }
    if (fullNum !== null && (!Number.isFinite(fullNum) || fullNum < 0 || fullNum > 10)) {
      setError("Tam voltaj geçersiz (0-10 V).");
      setSaving(false);
      return;
    }
    if (lowNum !== null && fullNum !== null && fullNum <= lowNum) {
      setError("Tam voltaj, düşük voltajdan büyük olmalı.");
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
      setSuccess("Proje ayarları kaydedildi.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Proje ayarları kaydedilemedi.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="tab-panel project-settings-panel">
      <div className="panel-head">
        <h3>Proje Ayarları</h3>
      </div>
      <p className="helper-text">
        Müşteri logosu burada güncellendiğinde hem login ekranı hem de üst
        navigasyondaki logo otomatik değişir. Logolar PNG/JPG/SVG/WebP olabilir,
        max 1 MB.
      </p>

      <div className="project-settings-grid">
        <div className="project-settings-field">
          <label>
            Proje Adı
            <input
              type="text"
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="Örn: Aras EDAŞ Smart Logger"
            />
          </label>
          <label>
            Müşteri Adı
            <input
              type="text"
              value={customerName}
              onChange={(event) => setCustomerName(event.target.value)}
              placeholder="Örn: Aras EDAŞ"
            />
          </label>
          <label>
            Tarayıcı Sekme Başlığı
            <input
              type="text"
              value={siteTitle}
              onChange={(event) => setSiteTitle(event.target.value)}
              placeholder="Örn: Aras EDAŞ Smart Logger"
              maxLength={200}
            />
            <small className="helper-text">
              Tarayıcı sekmesinde ve yer imlerinde gözükür. Boş bırakılırsa
              varsayılan "Horstmann Smart Logger" kullanılır.
            </small>
          </label>
        </div>

        <div className="project-settings-logo-grid">
          <LogoBox
            title="Login Logosu (Büyük)"
            description="Giriş ekranında merkezde gösterilir. Açık zeminli versiyon."
            value={customerLogo}
            onPick={(file) => void handlePickLogo(file, setCustomerLogo)}
            onClear={() => setCustomerLogo(null)}
            previewClass="project-settings-logo-preview project-settings-logo-preview--light"
          />
          <LogoBox
            title="Header Logosu (Koyu Zemin)"
            description="Üst navigasyon koyu arka plana uyumlu sürümü. Boş bırakılırsa login logosu kullanılır."
            value={customerLogoLight}
            onPick={(file) => void handlePickLogo(file, setCustomerLogoLight)}
            onClear={() => setCustomerLogoLight(null)}
            previewClass="project-settings-logo-preview project-settings-logo-preview--dark"
          />
          <LogoBox
            title="Tarayıcı İkonu (Favicon)"
            description="Tarayıcı sekmesinde başlığın yanında gözükür. ICO, PNG veya SVG. Kare format ve 16-128 px arası önerilir."
            value={favicon}
            onPick={(file) => void handlePickLogo(file, setFavicon, MAX_FILE_SIZE)}
            onClear={() => setFavicon(null)}
            previewClass="project-settings-logo-preview project-settings-favicon-preview"
            accept={ACCEPT_FAVICON}
            buttonLabel="Favicon Seç"
            emptyLabel="Varsayılan favicon kullanılıyor"
          />
          <LogoBox
            title="Giriş Ekranı Görseli"
            description="Giriş ekranının sağ tarafındaki dekoratif görsel. Kaldırılırsa varsayılan görsel kullanılır. Geniş ekran (örn. 1200×800) önerilir."
            value={loginImage}
            onPick={(file) => void handlePickLogo(file, setLoginImage, MAX_LOGIN_IMAGE_SIZE)}
            onClear={() => setLoginImage(null)}
            previewClass="project-settings-logo-preview project-settings-login-image-preview"
            buttonLabel="Görsel Seç"
            emptyLabel="Henüz görsel yüklenmedi"
          />
        </div>

        <div className="project-settings-battery-box">
          <h4>Batarya Voltaj Eşikleri</h4>
          <p className="helper-text">
            Cihaz batarya yüzdesi <code>master.battery_voltage_satellite</code>{" "}
            sinyalinden hesaplanır. Bu eşikler dışındaki değerlerde sırasıyla %0
            ve %100 atanır; arada lineer interpolasyon. Boş bırakılırsa varsayılan
            (3.40 V / 3.71 V) kullanılır.
          </p>
          <div className="project-settings-battery-grid">
            <label>
              Düşük Voltaj (%0) — V
              <input
                type="number"
                step="0.01"
                min={0}
                max={10}
                placeholder="3.40"
                value={batteryLow}
                onChange={(event) => setBatteryLow(event.target.value)}
              />
            </label>
            <label>
              Tam Voltaj (%100) — V
              <input
                type="number"
                step="0.01"
                min={0}
                max={10}
                placeholder="3.71"
                value={batteryFull}
                onChange={(event) => setBatteryFull(event.target.value)}
              />
            </label>
          </div>
        </div>
      </div>

      {error ? <p className="error-text">{error}</p> : null}
      {success ? <p className="success-text">{success}</p> : null}

      <div className="settings-actions">
        <button
          type="button"
          className="primary-btn"
          disabled={saving}
          onClick={() => void handleSave()}
        >
          {saving ? "Kaydediliyor..." : "Kaydet"}
        </button>
      </div>
    </section>
  );
}

type LogoBoxProps = {
  title: string;
  description: string;
  value: string | null;
  onPick: (file: File | undefined) => void;
  onClear: () => void;
  previewClass: string;
  accept?: string;
  buttonLabel?: string;
  emptyLabel?: string;
};

function LogoBox({
  title,
  description,
  value,
  onPick,
  onClear,
  previewClass,
  accept = ACCEPT,
  buttonLabel = "Logo Seç",
  emptyLabel = "Henüz logo yüklenmedi"
}: LogoBoxProps) {
  return (
    <div className="project-settings-logo-box">
      <div className="project-settings-logo-head">
        <h4>{title}</h4>
        {value ? (
          <button type="button" className="text-btn" onClick={onClear}>
            Kaldır
          </button>
        ) : null}
      </div>
      <p className="helper-text">{description}</p>
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
