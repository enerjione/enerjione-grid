/** Mühendislik > Proje Ayarlari (sadece INSTALLER).
 *
 * Müsteri adi, proje adi, login logosu, header logosu (light) burada
 * yonetilir. Logolar base64 data URL olarak DB'ye kaydedilir; UI'in heryerinde
 * (login + header) ProjectSettingsProvider uzerinden yansir.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { DeviceProfilesPanel } from "./DeviceProfilesPanel";

import { useProjectSettings } from "../../components/ProjectSettingsProvider";
import { DEFAULT_TOAST_POSITION, toastPosition, useToast } from "../../components/ToastProvider";
import type {
  PhaseCode,
  ProjectSettings,
  ProjectSettingsSave,
  ToastPosition
} from "../../shared/types";
import { fetchPhaseMap } from "../../shared/api";

const MAX_FILE_SIZE = 1_000_000; // 1 MB (logo, favicon)
const MAX_LOGIN_IMAGE_SIZE = 2_500_000; // 2.5 MB (login dekoratif gorsel daha buyuk olabilir)
const ACCEPT = "image/png,image/jpeg,image/svg+xml,image/webp";
const ACCEPT_FAVICON = "image/x-icon,image/png,image/svg+xml,image/vnd.microsoft.icon";

type Props = {
  onSave: (payload: ProjectSettingsSave) => Promise<void>;
  /** Faz eslemesi HALKA ACIK ayarlardan ayri, kimlik dogrulamali bir uctan
   *  gelir — bu yuzden panelin token'a ihtiyaci var. */
  accessToken: string;
};

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function ProjectSettingsPanel({ onSave, accessToken }: Props) {
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
  // UYDU hucreleri master ile ayni aralikta calismaz; bos birakilirsa
  // uydular master esigini kullanmaya devam eder (mevcut davranis).
  const [batteryLowSat, setBatteryLowSat] = useState<string>("");
  const [batteryFullSat, setBatteryFullSat] = useState<string>("");
  const [siteTitle, setSiteTitle] = useState("");
  const [favicon, setFavicon] = useState<string | null>(null);
  const [loginImage, setLoginImage] = useState<string | null>(null);
  // Toast tercihleri KURULUM GENELI (kullanici basina degil): burada
  // degistirilen deger herkes icin gecerlidir.
  const [toastPos, setToastPos] = useState<ToastPosition>(DEFAULT_TOAST_POSITION);
  const [toastMuted, setToastMuted] = useState(false);
  // Unite -> faz eslemesi: kurulumun GENEL konvansiyonu. Bos = kod
  // varsayilani. Istisna cihazlar Cihaz Yonetimi'nden ayrica ezilir.
  const [phaseMaster, setPhaseMaster] = useState<"" | PhaseCode>("");
  const [phaseSat01, setPhaseSat01] = useState<"" | PhaseCode>("");
  const [phaseSat02, setPhaseSat02] = useState<"" | PhaseCode>("");
  // Faz eslemesi public ayarlarda DEGIL (bkz. PhaseMap tipi); ayri cekilir.
  useEffect(() => {
    let iptal = false;
    fetchPhaseMap(accessToken)
      .then((m) => {
        if (iptal) return;
        setPhaseMaster(m.phase_master ?? "");
        setPhaseSat01(m.phase_sat01 ?? "");
        setPhaseSat02(m.phase_sat02 ?? "");
      })
      .catch(() => {
        // Alinamazsa alanlar bos kalir = "varsayilani kullan"; formun geri
        // kalani etkilenmez.
      });
    return () => {
      iptal = true;
    };
  }, [accessToken]);
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
    setBatteryLowSat(
      settings.battery_voltage_low_sat !== null &&
        settings.battery_voltage_low_sat !== undefined
        ? String(settings.battery_voltage_low_sat)
        : ""
    );
    setBatteryFullSat(
      settings.battery_voltage_full_sat !== null &&
        settings.battery_voltage_full_sat !== undefined
        ? String(settings.battery_voltage_full_sat)
        : ""
    );
    setSiteTitle(settings.site_title ?? "");
    setFavicon(settings.favicon ?? null);
    setLoginImage(settings.login_image ?? null);
    // Normalizasyon ToastProvider ile AYNI fonksiyondan gecer; boylece panel
    // ile gercek davranis ayrisamaz (null/bozuk deger -> sag-alt).
    setToastPos(toastPosition(settings.toast_position));
    setToastMuted(settings.toast_muted === true);

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
    const lowSatNum = batteryLowSat.trim() === "" ? null : Number(batteryLowSat);
    const fullSatNum = batteryFullSat.trim() === "" ? null : Number(batteryFullSat);
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
    if (
      lowSatNum !== null &&
      (!Number.isFinite(lowSatNum) || lowSatNum < 0 || lowSatNum > 10)
    ) {
      toast.error(t("engineering.projectSettings.batteryLowInvalid"));
      setSaving(false);
      return;
    }
    if (
      fullSatNum !== null &&
      (!Number.isFinite(fullSatNum) || fullSatNum < 0 || fullSatNum > 10)
    ) {
      toast.error(t("engineering.projectSettings.batteryFullInvalid"));
      setSaving(false);
      return;
    }
    if (lowSatNum !== null && fullSatNum !== null && fullSatNum <= lowSatNum) {
      toast.error(t("engineering.projectSettings.batteryOrderInvalid"));
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
        battery_voltage_low_sat: lowSatNum,
        battery_voltage_full_sat: fullSatNum,
        site_title: siteTitle.trim() || null,
        favicon: favicon,
        login_image: loginImage,
        toast_position: toastPos,
        toast_muted: toastMuted,
        // Bos = "varsayilani kullan"; deger yazmak kurulumcunun onaylamadigi
        // bir eslemeyi "secilmis" gostermek olurdu.
        phase_master: phaseMaster || null,
        phase_sat01: phaseSat01 || null,
        phase_sat02: phaseSat02 || null
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

        {/* Unite -> faz eslemesi: kurulumun GENEL konvansiyonu.
            SN2'nin uc unitesi hattin uc ayri fazina kelepcelenir ve bu
            ayrim ariza sebebi cikariminin belirleyici girdisi (tek
            faz-toprak cogunlukla dis etken, uc faz ekipman/asiri yuk).
            Istisna cihazlar Cihaz Yonetimi'nden ayrica ezilir. */}
        <div className="project-settings-battery-box">
          <div className="project-settings-battery-head">
            <span
              className="project-settings-battery-icon material-symbols-outlined"
              aria-hidden="true"
            >
              electric_meter
            </span>
            <h4>{t("engineering.projectSettings.phaseTitle")}</h4>
          </div>
          <p className="helper-text">{t("engineering.projectSettings.phaseHint")}</p>
          <div className="project-settings-battery-grid">
            {(
              [
                ["master", phaseMaster, setPhaseMaster],
                ["sat01", phaseSat01, setPhaseSat01],
                ["sat02", phaseSat02, setPhaseSat02]
              ] as const
            ).map(([unite, deger, setter]) => (
              <label className="project-settings-battery-field" key={unite}>
                {t(`engineering.projectSettings.phaseUnit.${unite}`)}
                <select
                  value={deger}
                  onChange={(event) => setter((event.target.value || "") as "" | PhaseCode)}
                >
                  <option value="">
                    {t("engineering.projectSettings.phaseDefault", {
                      phase: unite === "master" ? "A" : unite === "sat01" ? "B" : "C"
                    })}
                  </option>
                  <option value="a">A (L1)</option>
                  <option value="b">B (L2)</option>
                  <option value="c">C (L3)</option>
                </select>
              </label>
            ))}
          </div>
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

          {/* UYDU HUCRELERI AYRI OLCULUR: uydunun bataryasi RTU'yu besleyen
              master hucresiyle ayni voltaj araliginda calismaz. Tek cift
              esikle olculunce uydular sahada saglamken ekranda surekli %0
              gorunuyordu (olculen ~3,05 V, master esigi 3,40 V) — sessiz bir
              yanlislik: gercekten biten bir hucreyi de gizler.
              BOS BIRAKMAK GECERLI: uydular master esigini kullanmaya devam
              eder, guncelleyen kurulumda hicbir sey degismez. */}
          <p className="helper-text">{t("engineering.projectSettings.batterySatHint")}</p>
          <div className="project-settings-battery-grid">
            <label className="project-settings-battery-field">
              <span className="project-settings-battery-label">
                {t("engineering.projectSettings.batteryLowSat")}
              </span>
              <div className="project-settings-battery-input-wrap">
                <input
                  type="number"
                  step="0.01"
                  min={0}
                  max={10}
                  placeholder={t("engineering.projectSettings.batterySatPlaceholder")}
                  value={batteryLowSat}
                  onChange={(event) => setBatteryLowSat(event.target.value)}
                />
                <span className="project-settings-battery-unit">V</span>
              </div>
            </label>
            <label className="project-settings-battery-field">
              <span className="project-settings-battery-label">
                {t("engineering.projectSettings.batteryFullSat")}
              </span>
              <div className="project-settings-battery-input-wrap">
                <input
                  type="number"
                  step="0.01"
                  min={0}
                  max={10}
                  placeholder={t("engineering.projectSettings.batterySatPlaceholder")}
                  value={batteryFullSat}
                  onChange={(event) => setBatteryFullSat(event.target.value)}
                />
                <span className="project-settings-battery-unit">V</span>
              </div>
            </label>
          </div>
          </div>

        {/* Model bazli ayarlar: ustteki batarya kutusu proje GENELI
            varsayilanidir, burasi modele ozel istisnadir. */}
        <DeviceProfilesPanel token={accessToken} canEdit />

        <div className="project-settings-toast-box">
          <div className="project-settings-toast-head">
            <span className="project-settings-toast-icon material-symbols-outlined" aria-hidden="true">
              notifications
            </span>
            <div>
              <h4>{t("engineering.projectSettings.toastTitle")}</h4>
              <p className="helper-text">{t("engineering.projectSettings.toastHint")}</p>
            </div>
          </div>
          <div className="project-settings-toast-grid">
            <label className="project-settings-toast-field">
              <span className="project-settings-toast-label">
                {t("engineering.projectSettings.toastPosition")}
              </span>
              <select
                value={toastPos}
                onChange={(event) => setToastPos(event.target.value as ToastPosition)}
              >
                <option value="bottom-right">
                  {t("engineering.projectSettings.toastPositionBottomRight")}
                </option>
                <option value="bottom-left">
                  {t("engineering.projectSettings.toastPositionBottomLeft")}
                </option>
                <option value="top-right">
                  {t("engineering.projectSettings.toastPositionTopRight")}
                </option>
                <option value="top-left">
                  {t("engineering.projectSettings.toastPositionTopLeft")}
                </option>
              </select>
            </label>
            <label className="project-settings-toast-check">
              <input
                type="checkbox"
                checked={toastMuted}
                onChange={(event) => setToastMuted(event.target.checked)}
              />
              <span>
                <strong>{t("engineering.projectSettings.toastMute")}</strong>
                <small>{t("engineering.projectSettings.toastMuteHint")}</small>
              </span>
            </label>
          </div>
          <p className="project-settings-toast-note">
            <span className="material-symbols-outlined" aria-hidden="true">info</span>
            {t("engineering.projectSettings.toastScopeNote")}
          </p>
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
