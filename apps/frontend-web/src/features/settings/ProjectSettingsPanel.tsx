/** Mühendislik > Proje Ayarlari (sadece INSTALLER).
 *
 * Müsteri adi, proje adi, login logosu, header logosu (light) burada
 * yonetilir. Logolar base64 data URL olarak DB'ye kaydedilir; UI'in heryerinde
 * (login + header) ProjectSettingsProvider uzerinden yansir.
 */
import { useEffect, useState } from "react";

import { useProjectSettings } from "../../components/ProjectSettingsProvider";
import type { ProjectSettings } from "../../shared/types";

const MAX_FILE_SIZE = 1_000_000; // 1 MB
const ACCEPT = "image/png,image/jpeg,image/svg+xml,image/webp";

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
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    setProjectName(settings.project_name ?? "");
    setCustomerName(settings.customer_name ?? "");
    setCustomerLogo(settings.customer_logo ?? null);
    setCustomerLogoLight(settings.customer_logo_light ?? null);
  }, [settings]);

  const handlePickLogo = async (
    file: File | undefined,
    setter: (val: string | null) => void
  ) => {
    setError("");
    setSuccess("");
    if (!file) return;
    if (file.size > MAX_FILE_SIZE) {
      setError(`Dosya çok büyük (max ${Math.round(MAX_FILE_SIZE / 1024)} KB).`);
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
    try {
      await onSave({
        project_name: projectName.trim() || null,
        customer_name: customerName.trim() || null,
        customer_logo: customerLogo,
        customer_logo_light: customerLogoLight
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
};

function LogoBox({ title, description, value, onPick, onClear, previewClass }: LogoBoxProps) {
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
          <span className="project-settings-logo-empty">Henüz logo yüklenmedi</span>
        )}
      </div>
      <label className="project-settings-logo-upload">
        <input
          type="file"
          accept={ACCEPT}
          onChange={(event) => onPick(event.target.files?.[0])}
        />
        <span>Logo Seç</span>
      </label>
    </div>
  );
}
