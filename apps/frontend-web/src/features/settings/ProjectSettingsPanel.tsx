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
  const [batteryLow, setBatteryLow] = useState<string>("");
  const [batteryFull, setBatteryFull] = useState<string>("");
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
        battery_voltage_full: fullNum
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
