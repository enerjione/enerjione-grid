/**
 * DeviceAllSignalsTab — "Tumu" sekmesi: SET OZETI.
 *
 * Master + Satellite 01 + Satellite 02 uc cihazi YAN YANA kartlarda gosterir
 * (scroll yok). Her kart: kimlik + kilit KPI (akim/gerilim/sicaklik/pil) +
 * ariza durumu (rozet) + sayaclar. Bagli olmayan uydu -> soluk kart + "Veri yok".
 * Tum sinyaller degil, set'in genel durumu tek bakista.
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { DeviceRow, SignalLiveRow, SignalSource } from "../../shared/types";

type Props = {
  device: DeviceRow;
  values: SignalLiveRow[];
  gwOnline: boolean;
  /** Her kaynaktaki sinyal sayisi — 0 ise cihaz bagli degil (soluk kart). */
  sourceCounts: Record<SignalSource, number>;
};

const SOURCES: { key: SignalSource; label: string; tone: string; icon: string }[] = [
  { key: "master", label: "Master", tone: "master", icon: "dns" },
  { key: "sat01", label: "Satellite 01", tone: "green", icon: "settings_input_antenna" },
  { key: "sat02", label: "Satellite 02", tone: "amber", icon: "settings_input_antenna" },
];

const NUMBER_FORMATTER = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 3,
  useGrouping: false,
});

// Kilit durum sinyalleri (binary) — rozet.
const STATUS_DEFS: { suffix: string; labelKey: string; icon: string }[] = [
  { suffix: "overcurrent_tripped", labelKey: "overcurrent", icon: "bolt" },
  { suffix: "voltage_loss", labelKey: "voltageLoss", icon: "power_off" },
  { suffix: "current_loss", labelKey: "currentLoss", icon: "flash_off" },
  { suffix: "delta_i_delta_t_tripped", labelKey: "delta", icon: "show_chart" },
  { suffix: "permanent_fault", labelKey: "permanent", icon: "warning" },
  { suffix: "momentary_fault", labelKey: "momentary", icon: "error_outline" },
];

function fmtNum(v: number | undefined, unit: string | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const txt = NUMBER_FORMATTER.format(v);
  return unit ? `${txt} ${unit}` : txt;
}

// Satellite pil: battery_voltage_satellite voltajindan basit Li-ion oran.
function voltToPct(v: number | undefined): number | undefined {
  if (v == null || !Number.isFinite(v)) return undefined;
  return Math.max(0, Math.min(100, Math.round(((v - 3.2) / (4.2 - 3.2)) * 100)));
}

function batteryClass(pct: number): string {
  if (pct <= 20) return "critical";
  if (pct <= 50) return "low";
  return "ok";
}

export function DeviceAllSignalsTab({ device, values, gwOnline, sourceCounts }: Props) {
  const { t } = useTranslation();

  // kaynak -> (suffix -> row). Bu cihazin tum satirlari.
  const bySource = useMemo(() => {
    const m = new Map<SignalSource, Map<string, SignalLiveRow>>();
    for (const src of SOURCES) m.set(src.key, new Map());
    for (const r of values) {
      if (r.device_id !== device.id) continue;
      const inner = m.get(r.source as SignalSource);
      if (!inner) continue;
      const i = r.signal_key.indexOf(".");
      inner.set(i >= 0 ? r.signal_key.slice(i + 1) : r.signal_key, r);
    }
    return m;
  }, [values, device.id]);

  return (
    <div className="device-set">
      {SOURCES.map((src) => {
        const rows = bySource.get(src.key) ?? new Map();
        const online = (sourceCounts[src.key] ?? 0) > 0 && gwOnline;
        const get = (suffix: string): SignalLiveRow | undefined => rows.get(suffix);
        const numOf = (suffix: string): number | undefined => {
          const v = get(suffix)?.value;
          return v == null ? undefined : v;
        };

        // KPI degerleri (birim sinyalin kendi unit'i).
        const cur = get("actual_current");
        const volt = get("actual_voltage");
        const temp = get("device_temperature") ?? get("conductor_temperature");
        const battPct =
          src.key === "master"
            ? Number.isFinite(device.batteryPercent)
              ? device.batteryPercent
              : undefined
            : voltToPct(numOf("battery_voltage_satellite"));

        const permCount = numOf("permanent_fault_counter");
        const momCount = numOf("momentary_fault_counter");

        // Uydu bagli degil (sinyal yok).
        const noData = (sourceCounts[src.key] ?? 0) === 0;

        return (
          <section
            key={src.key}
            className={`device-set-card tone-${src.tone}${noData ? " is-offline" : ""}`}
          >
            {/* Baslik */}
            <header className="device-set-head">
              <span className="device-set-icon material-symbols-outlined">{src.icon}</span>
              <div className="device-set-title">
                <span className="device-set-name">{src.label}</span>
                {rows.get("serial_number")?.value != null ? (
                  <span className="device-set-serial">
                    {Math.round(rows.get("serial_number")!.value as number)}
                  </span>
                ) : null}
              </div>
              <span className={`device-set-status ${online ? "is-online" : "is-off"}`}>
                <span className="device-set-status-dot" aria-hidden="true" />
                {online ? t("deviceDetail.online") : noData ? t("deviceDetail.set.noData") : t("deviceDetail.offline")}
              </span>
            </header>

            {noData ? (
              <div className="device-set-nodata">
                <span className="material-symbols-outlined">sensors_off</span>
                <p>{t("deviceDetail.set.noDataLong")}</p>
              </div>
            ) : (
              <>
                {/* KPI mini grid (2x2) */}
                <div className="device-set-kpis">
                  <SetKpi icon="bolt" tone="amber" label={t("deviceDetail.kpi.current")} value={fmtNum(cur?.value ?? undefined, cur?.unit)} />
                  <SetKpi icon="electric_bolt" tone="blue" label={t("deviceDetail.kpi.voltage")} value={fmtNum(volt?.value ?? undefined, volt?.unit)} />
                  <SetKpi icon="device_thermostat" tone="rose" label={t("deviceDetail.kpi.temperature")} value={fmtNum(temp?.value ?? undefined, temp?.unit)} />
                  <SetKpi
                    icon="battery_full"
                    tone={battPct == null ? "slate" : (batteryClass(battPct) === "ok" ? "green" : batteryClass(battPct) === "low" ? "amber" : "red")}
                    label={t("deviceDetail.meta.battery")}
                    value={battPct == null ? "—" : `%${Math.round(battPct)}`}
                  />
                </div>

                {/* Ariza durumu */}
                <div className="device-set-block">
                  <span className="device-set-block-title">{t("deviceDetail.set.faultStatus")}</span>
                  <ul className="device-set-status-list">
                    {STATUS_DEFS.map((d) => {
                      const active = get(d.suffix)?.value === 1;
                      return (
                        <li key={d.suffix} className="device-set-status-row">
                          <span className="device-set-status-name">
                            <span className="material-symbols-outlined">{d.icon}</span>
                            {t(`deviceDetail.set.signal.${d.labelKey}`)}
                          </span>
                          <span className={`device-set-badge ${active ? "is-active" : "is-normal"}`}>
                            <span className="material-symbols-outlined">{active ? "warning" : "check_circle"}</span>
                            {active ? t("deviceDetail.status.active") : t("deviceDetail.status.normal")}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>

                {/* Sayaclar */}
                <div className="device-set-counters">
                  <div className="device-set-counter tone-red">
                    <span className="material-symbols-outlined">report</span>
                    <div>
                      <span className="device-set-counter-label">{t("deviceDetail.permanentFaults")}</span>
                      <strong>{permCount != null ? Math.round(permCount) : "—"}</strong>
                    </div>
                  </div>
                  <div className="device-set-counter tone-orange">
                    <span className="material-symbols-outlined">flash_on</span>
                    <div>
                      <span className="device-set-counter-label">{t("deviceDetail.momentaryFaults")}</span>
                      <strong>{momCount != null ? Math.round(momCount) : "—"}</strong>
                    </div>
                  </div>
                </div>
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}

function SetKpi({ icon, tone, label, value }: { icon: string; tone: string; label: string; value: string }) {
  return (
    <div className={`device-set-kpi tone-${tone}`}>
      <span className="device-set-kpi-icon material-symbols-outlined">{icon}</span>
      <div className="device-set-kpi-body">
        <span className="device-set-kpi-value">{value}</span>
        <span className="device-set-kpi-label">{label}</span>
      </div>
    </div>
  );
}
