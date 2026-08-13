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

type BolumKey = "kimlik" | "olcum" | "bildirim";

const BOLUMLER: { key: BolumKey; icon: string }[] = [
  { key: "kimlik", icon: "badge" },
  { key: "olcum", icon: "battery_charging_full" },
  { key: "bildirim", icon: "notifications" }
];

/** Baloncuk koseleri — ekrandaki YERLESIMLE ayni sirada (ust satir, alt
 *  satir). Acilir listede sira alfabetikti ve "sag alt" ile "sol ust"
 *  arasindaki iliski okunmuyordu. */
const TOAST_KOSELERI: { value: ToastPosition; labelKey: string }[] = [
  { value: "top-left", labelKey: "toastPositionTopLeft" },
  { value: "top-right", labelKey: "toastPositionTopRight" },
  { value: "bottom-left", labelKey: "toastPositionBottomLeft" },
  { value: "bottom-right", labelKey: "toastPositionBottomRight" }
];

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

  /** Alt sekmeler — uc bagimsiz soru, uc ayri ekran (bkz. render). */
  const [bolum, setBolum] = useState<BolumKey>("kimlik");

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
      {/* ALT SEKMELER — sayfa tek bir yigin halindeydi.
          Kurulumcu "logo" ile "batarya esigi"ni ayni kaydirmada ariyordu ve
          uzun formda hangi bolumun nerede bittigi belirsizdi. Uc grup birbirinden
          BAGIMSIZ sorular soruyor:
            Kimlik   -> bu kurulum kime ait, nasil gorunuyor
            Olcum    -> cihazdan gelen sayi neye gore yorumlanacak
            Bildirim -> uyari nerede ve nasil cikacak
          KAYDET TEK: form tek bir istek gonderir, sekme degistirmek girdiyi
          KAYBETTIRMEZ. Aksiyon cubugunda bunu soyleyen bir not var. */}
      <div className="ps-tabs" role="tablist" aria-label={t("engineering.projectSettings.sectionsLabel")}>
        {BOLUMLER.map((b) => (
          <button
            key={b.key}
            type="button"
            role="tab"
            aria-selected={bolum === b.key}
            className={`ps-tab${bolum === b.key ? " is-active" : ""}`}
            onClick={() => setBolum(b.key)}
          >
            <span className="material-symbols-outlined" aria-hidden="true">
              {b.icon}
            </span>
            {t(`engineering.projectSettings.section.${b.key}`)}
          </button>
        ))}
      </div>

      <div className="project-settings-body ps-body">
        {bolum === "kimlik" ? (
          <>
            {/* Uc kisa metin alani dar sutunda, dort gorsel onizleme genis
                sutunda: yan yana durduklarinda ikisi de kendi olcusunde
                kalir, satir sonunda bos alan birakmaz. */}
            <section className="ps-card ps-card--w4 ps-card--xl3">
              <header className="ps-card-head">
                <span className="ps-card-icon material-symbols-outlined" aria-hidden="true">
                  badge
                </span>
                <div>
                  <h4>{t("engineering.projectSettings.identityTitle")}</h4>
                  <p className="ps-hint">{t("engineering.projectSettings.identityHint")}</p>
                </div>
              </header>
              <div className="ps-field-row">
                <label className="ps-field">
                  <span className="ps-label">{t("engineering.projectSettings.projectName")}</span>
                  <input
                    type="text"
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                    placeholder={t("engineering.projectSettings.projectNamePlaceholder")}
                  />
                </label>
                <label className="ps-field">
                  <span className="ps-label">{t("engineering.projectSettings.customerName")}</span>
                  <input
                    type="text"
                    value={customerName}
                    onChange={(event) => setCustomerName(event.target.value)}
                    placeholder={t("engineering.projectSettings.customerNamePlaceholder")}
                  />
                </label>
                <label className="ps-field">
                  <span className="ps-label">{t("engineering.projectSettings.siteTitle")}</span>
                  <input
                    type="text"
                    value={siteTitle}
                    onChange={(event) => setSiteTitle(event.target.value)}
                    placeholder={t("engineering.projectSettings.siteTitlePlaceholder")}
                    maxLength={200}
                  />
                </label>
              </div>
            </section>

            <section className="ps-card ps-card--w8 ps-card--xl9">
              <header className="ps-card-head">
                <span className="ps-card-icon material-symbols-outlined" aria-hidden="true">
                  image
                </span>
                <div>
                  <h4>{t("engineering.projectSettings.brandTitle")}</h4>
                  <p className="ps-hint">{t("engineering.projectSettings.brandHint")}</p>
                </div>
              </header>
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
            </section>
          </>
        ) : null}

        {bolum === "olcum" ? (
          <>
            {/* Unite -> faz eslemesi: kurulumun GENEL konvansiyonu. SN2'nin uc
                unitesi hattin uc ayri fazina kelepcelenir ve bu ayrim ariza
                sebebi cikariminin belirleyici girdisi (tek faz-toprak cogunlukla
                dis etken, uc faz ekipman/asiri yuk). Istisna cihazlar Cihaz
                Yonetimi'nden ayrica ezilir. */}
            <section className="ps-card ps-card--w5 ps-card--xl4">
              <header className="ps-card-head">
                <span className="ps-card-icon material-symbols-outlined" aria-hidden="true">
                  electric_meter
                </span>
                <div>
                  <h4>{t("engineering.projectSettings.phaseTitle")}</h4>
                  <p className="ps-hint">{t("engineering.projectSettings.phaseHint")}</p>
                </div>
              </header>
              <div className="ps-unit-grid">
                {(
                  [
                    ["master", "a", phaseMaster, setPhaseMaster],
                    ["sat01", "b", phaseSat01, setPhaseSat01],
                    ["sat02", "c", phaseSat02, setPhaseSat02]
                  ] as const
                ).map(([unite, varsayilan, deger, setter]) => (
                  <label className="ps-unit" key={unite}>
                    <span className="ps-unit-head">
                      {/* Rozet SECILI fazi gosterir; secim bos ise varsayilani.
                          Eskiden hangi unitenin hangi fazda oldugu yalnizca
                          acilir listenin icinde yaziyordu, kapaliyken kurulum
                          bir bakista okunamiyordu. */}
                      <span className={`ps-phase ps-phase--${deger || varsayilan}`}>
                        {(deger || varsayilan).toUpperCase()}
                      </span>
                      <span className="ps-unit-name">
                        {t(`engineering.projectSettings.phaseUnit.${unite}`)}
                      </span>
                    </span>
                    <span className="ps-select">
                      <select
                        value={deger}
                        onChange={(event) => setter((event.target.value || "") as "" | PhaseCode)}
                      >
                        <option value="">
                          {t("engineering.projectSettings.phaseDefault", {
                            phase: varsayilan.toUpperCase()
                          })}
                        </option>
                        <option value="a">A (L1)</option>
                        <option value="b">B (L2)</option>
                        <option value="c">C (L3)</option>
                      </select>
                      <span className="material-symbols-outlined" aria-hidden="true">
                        expand_more
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </section>

            <section className="ps-card ps-card--w7 ps-card--xl4">
              <header className="ps-card-head">
                <span className="ps-card-icon material-symbols-outlined" aria-hidden="true">
                  battery_charging_full
                </span>
                <div>
                  <h4>{t("engineering.projectSettings.batteryTitle")}</h4>
                  <p className="ps-hint">{t("engineering.projectSettings.batteryHint")}</p>
                </div>
              </header>

              <div className="ps-cells">
                <BatteryRange
                  title={t("engineering.projectSettings.batteryCellMaster")}
                  note={t("engineering.projectSettings.batteryCellMasterNote")}
                  lowLabel={t("engineering.projectSettings.batteryLow")}
                  fullLabel={t("engineering.projectSettings.batteryFull")}
                  low={batteryLow}
                  full={batteryFull}
                  onLow={setBatteryLow}
                  onFull={setBatteryFull}
                  lowPlaceholder="3.40"
                  fullPlaceholder="3.71"
                  fallback={t("engineering.projectSettings.batteryFallbackCode", {
                    low: "3.40",
                    full: "3.71"
                  })}
                />
                {/* UYDU HUCRELERI AYRI OLCULUR: uydunun bataryasi RTU'yu besleyen
                    master hucresiyle ayni voltaj araliginda calismaz. Tek cift
                    esikle olculunce uydular sahada saglamken ekranda surekli %0
                    gorunuyordu (olculen ~3,05 V, master esigi 3,40 V) — sessiz
                    bir yanlislik: gercekten biten bir hucreyi de gizler.
                    BOS BIRAKMAK GECERLI: uydular master esigini kullanir. */}
                <BatteryRange
                  title={t("engineering.projectSettings.batteryCellSat")}
                  note={t("engineering.projectSettings.batterySatHint")}
                  lowLabel={t("engineering.projectSettings.batteryLow")}
                  fullLabel={t("engineering.projectSettings.batteryFull")}
                  low={batteryLowSat}
                  full={batteryFullSat}
                  onLow={setBatteryLowSat}
                  onFull={setBatteryFullSat}
                  lowPlaceholder="3.00"
                  fullPlaceholder="3.30"
                  fallback={t("engineering.projectSettings.batteryFallbackMaster")}
                />
              </div>
            </section>

            {/* Model bazli ayarlar: ustteki batarya kutusu proje GENELI
                varsayilanidir, burasi modele ozel istisnadir. Genis ekranda
                ucu bir satirda; dar ekranda faz+batarya ikilisinin altina
                tam genislikte gecer. */}
            <DeviceProfilesPanel
              token={accessToken}
              canEdit
              className="ps-card--xl4"
            />
          </>
        ) : null}

        {bolum === "bildirim" ? (
          <section className="ps-card">
            <header className="ps-card-head">
              <span className="ps-card-icon material-symbols-outlined" aria-hidden="true">
                notifications
              </span>
              <div>
                <h4>{t("engineering.projectSettings.toastTitle")}</h4>
                <p className="ps-hint">{t("engineering.projectSettings.toastHint")}</p>
              </div>
            </header>
            {/* Iki ayri soru YAN YANA: "nerede cikacak" ve "hic cikacak mi".
                Alt alta olduklarinda ikisi de satirin solunda kalip sagda
                genis bir bosluk birakiyordu. */}
            <div className="ps-toast-layout">
              {/* Kose secimi SOYUT bir tercih: acilir listedeki "sag alt"
                  yazisini okuyup zihinde canlandirmak gerekiyordu. Dort kucuk
                  ekran maketi hem secim hem onizleme — secili olan zaten
                  sonucu gosteriyor. */}
              <fieldset className="ps-pos-group">
                <legend className="ps-label">
                  {t("engineering.projectSettings.toastPosition")}
                </legend>
                <div className="ps-pos-grid">
                  {TOAST_KOSELERI.map((kose) => (
                    <label
                      key={kose.value}
                      className={`ps-pos${toastPos === kose.value ? " is-active" : ""}`}
                    >
                      <input
                        type="radio"
                        name="ps-toast-position"
                        value={kose.value}
                        checked={toastPos === kose.value}
                        onChange={() => setToastPos(kose.value)}
                      />
                      <span className={`ps-corner ps-corner--${kose.value}`} aria-hidden="true">
                        <span className="ps-corner-dot" />
                      </span>
                      <span className="ps-pos-name">
                        {t(`engineering.projectSettings.${kose.labelKey}`)}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="ps-toast-side">
                <label className="ps-switch">
                  <input
                    type="checkbox"
                    checked={toastMuted}
                    onChange={(event) => setToastMuted(event.target.checked)}
                  />
                  <span className="ps-switch-track" aria-hidden="true">
                    <span className="ps-switch-knob" />
                  </span>
                  <span className="ps-switch-text">
                    <strong>{t("engineering.projectSettings.toastMute")}</strong>
                    <small>{t("engineering.projectSettings.toastMuteHint")}</small>
                  </span>
                </label>
                <p className="ps-note">
                  <span className="material-symbols-outlined" aria-hidden="true">
                    info
                  </span>
                  {t("engineering.projectSettings.toastScopeNote")}
                </p>
              </div>
            </div>
          </section>
        ) : null}
      </div>

      <div className="project-settings-actions">
        {/* Kaydet TUM sekmeleri birden yazar: form tek bir PUT gonderir.
            Sekme degistirince girdinin kaybolmadigini soylemek gerekiyor,
            aksi halde kullanici her sekmede ayri ayri kaydetmeye calisir. */}
        <span className="ps-save-note">{t("engineering.projectSettings.saveScopeNote")}</span>
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

/** Bir HUCRE TIPININ bos/dolu voltaj penceresi.
 *
 *  Iki sayi tek basina "bu deger makul mu" sorusunu cevaplamiyordu. Kucuk
 *  gradyan serit pencereyi GORSEL olarak gosterir: sol uc bos, sag uc dolu.
 *  Boylece ters girilmis (dolu < bos) ya da cok dar bir aralik goze carpar —
 *  kaydetmeyi denemeden once. */
function BatteryRange({
  title,
  note,
  lowLabel,
  fullLabel,
  low,
  full,
  onLow,
  onFull,
  lowPlaceholder,
  fullPlaceholder,
  fallback
}: {
  title: string;
  note: string;
  lowLabel: string;
  fullLabel: string;
  low: string;
  full: string;
  onLow: (v: string) => void;
  onFull: (v: string) => void;
  lowPlaceholder: string;
  fullPlaceholder: string;
  fallback: string;
}) {
  const lowNum = Number(low);
  const fullNum = Number(full);
  const dolu = low.trim() !== "" && full.trim() !== "";
  const gecerli = dolu && Number.isFinite(lowNum) && Number.isFinite(fullNum) && fullNum > lowNum;
  return (
    <div className={`ps-cell${dolu && !gecerli ? " is-invalid" : ""}`}>
      <div className="ps-cell-head">
        <h5>{title}</h5>
        {!dolu ? <span className="ps-cell-tag">{fallback}</span> : null}
      </div>
      <div className="ps-cell-fields">
        <label className="ps-field ps-field--num">
          <span className="ps-label">{lowLabel}</span>
          <span className="ps-num">
            <input
              type="number"
              step="0.01"
              min={0}
              max={10}
              placeholder={lowPlaceholder}
              value={low}
              onChange={(event) => onLow(event.target.value)}
            />
            <span className="ps-num-unit">V</span>
          </span>
        </label>
        <span className="ps-cell-bar" aria-hidden="true">
          <span className="ps-cell-bar-fill" />
        </span>
        <label className="ps-field ps-field--num">
          <span className="ps-label">{fullLabel}</span>
          <span className="ps-num">
            <input
              type="number"
              step="0.01"
              min={0}
              max={10}
              placeholder={fullPlaceholder}
              value={full}
              onChange={(event) => onFull(event.target.value)}
            />
            <span className="ps-num-unit">V</span>
          </span>
        </label>
      </div>
      <p className="ps-hint ps-cell-note">{note}</p>
    </div>
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
