/**
 * CIHAZ PROFILLERI — model bazli ayarlar.
 *
 * NEDEN VAR: batarya esikleri proje genelinde TEK bir cifttti ve tum
 * cihazlara uygulaniyordu. Horstmann SN 2.0'in lityum hucresi ile Pole
 * Master Kit'in bataryasi ayni voltaj araliginda calismaz; tek esikle
 * olculunce bir model surekli "dolu", digeri surekli "bitmek uzere"
 * gorunur. Bu sessiz bir yanlisliktir — ekip ya bosuna direge cikar ya da
 * gercekten biten bataryayi kacirir.
 *
 * BOS BIRAKMAK GECERLI BIR SECIMDIR: alan bos ise o model proje ayarindan
 * (o da yoksa kod varsayilanindan) devam eder. Bu yuzden her alanin altinda
 * "su an sunu kullaniyor" bilgisi gosteriliyor — kullanici kendi girdigi
 * degerle miras alinan degeri ayirt edebilmeli.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  fetchDeviceModelSettings,
  saveDeviceModelSettings,
  type DeviceModelSettingsRow
} from "../../shared/api";
import { useToast } from "../../components/ToastProvider";

type Props = {
  token: string;
  canEdit: boolean;
};

/** Bos string -> null; "3,40" gibi virgullu girisi de kabul et. */
function sayiya(value: string): number | null {
  const temiz = value.trim().replace(",", ".");
  if (temiz === "") return null;
  const n = Number(temiz);
  return Number.isFinite(n) ? n : Number.NaN;
}

export function DeviceProfilesPanel({ token, canEdit }: Props) {
  const { t } = useTranslation();
  const toast = useToast();
  const [rows, setRows] = useState<DeviceModelSettingsRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<Record<string, { low: string; full: string }>>({});
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    let iptal = false;
    setLoading(true);
    fetchDeviceModelSettings(token)
      .then((data) => {
        if (iptal) return;
        setRows(data);
        const yeni: Record<string, { low: string; full: string }> = {};
        for (const r of data) {
          yeni[r.model] = {
            low: r.battery_voltage_low === null ? "" : String(r.battery_voltage_low),
            full: r.battery_voltage_full === null ? "" : String(r.battery_voltage_full)
          };
        }
        setDraft(yeni);
      })
      .catch((err: unknown) => {
        if (!iptal) toast.error(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!iptal) setLoading(false);
      });
    return () => {
      iptal = true;
    };
  }, [token, toast]);

  const kaydet = async (row: DeviceModelSettingsRow) => {
    const d = draft[row.model] ?? { low: "", full: "" };
    const low = sayiya(d.low);
    const full = sayiya(d.full);
    if (Number.isNaN(low) || Number.isNaN(full)) {
      toast.error(t("engineering.deviceProfiles.invalidNumber"));
      return;
    }
    // Ikisi de girildiyse sira onemli: ters aralik yuzdeyi anlamsiz kilar.
    // Backend de reddeder ama kullaniciya burada aninda soylemek daha iyi.
    if (low !== null && full !== null && full <= low) {
      toast.error(t("engineering.deviceProfiles.rangeInvalid"));
      return;
    }
    setSaving(row.model);
    try {
      const guncel = await saveDeviceModelSettings(token, row.model, {
        battery_voltage_low: low,
        battery_voltage_full: full
      });
      setRows((onceki) => onceki.map((r) => (r.model === guncel.model ? guncel : r)));
      toast.success(t("engineering.deviceProfiles.saved", { model: row.label ?? row.model }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return (
      <section className="ps-card">
        <p className="device-profiles-empty">{t("common.loading")}</p>
      </section>
    );
  }

  return (
    <section className="ps-card device-profiles-box">
      <header className="ps-card-head">
        <span className="ps-card-icon material-symbols-outlined" aria-hidden="true">
          tune
        </span>
        <div>
          <h4>{t("engineering.deviceProfiles.title")}</h4>
          <p className="ps-hint">{t("engineering.deviceProfiles.hint")}</p>
        </div>
      </header>

      <div className="device-profiles-list">
        {rows.map((row) => {
          const d = draft[row.model] ?? { low: "", full: "" };
          const ozel = row.battery_voltage_low !== null || row.battery_voltage_full !== null;
          return (
            <div className="device-profile-row" key={row.model}>
              <div className="device-profile-name">
                <strong>{row.label ?? row.model}</strong>
                <small>
                  {t("engineering.deviceProfiles.units", {
                    units: row.battery_units.join(" · ").toUpperCase()
                  })}
                </small>
              </div>
              <div className="device-profile-fields">
                <label className="ps-field ps-field--num">
                  <span className="ps-label">
                    {t("engineering.projectSettings.batteryLow")}
                  </span>
                  <span className="ps-num">
                    <input
                      type="number"
                      step="0.01"
                      min={0}
                      max={10}
                      disabled={!canEdit}
                      placeholder={String(row.resolved_battery_voltage_low)}
                      value={d.low}
                      onChange={(e) =>
                        setDraft((o) => ({
                          ...o,
                          [row.model]: { ...d, low: e.target.value }
                        }))
                      }
                    />
                    <span className="ps-num-unit">V</span>
                  </span>
                </label>
                <label className="ps-field ps-field--num">
                  <span className="ps-label">
                    {t("engineering.projectSettings.batteryFull")}
                  </span>
                  <span className="ps-num">
                    <input
                      type="number"
                      step="0.01"
                      min={0}
                      max={10}
                      disabled={!canEdit}
                      placeholder={String(row.resolved_battery_voltage_full)}
                      value={d.full}
                      onChange={(e) =>
                        setDraft((o) => ({
                          ...o,
                          [row.model]: { ...d, full: e.target.value }
                        }))
                      }
                    />
                    <span className="ps-num-unit">V</span>
                  </span>
                </label>
                {canEdit ? (
                  <button
                    type="button"
                    className="primary-btn device-profile-save"
                    disabled={saving === row.model}
                    onClick={() => void kaydet(row)}
                  >
                    {saving === row.model ? t("common.saving") : t("common.save")}
                  </button>
                ) : null}
              </div>
              <small className="device-profile-resolved">
                {ozel
                  ? t("engineering.deviceProfiles.custom")
                  : t("engineering.deviceProfiles.inherited", {
                      low: row.resolved_battery_voltage_low,
                      full: row.resolved_battery_voltage_full
                    })}
              </small>
            </div>
          );
        })}
      </div>
    </section>
  );
}
