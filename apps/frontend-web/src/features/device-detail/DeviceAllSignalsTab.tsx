/**
 * DeviceAllSignalsTab — "Tumu" sekmesi.
 *
 * Master + Satellite 01 + Satellite 02 uc kaynagin TUM sinyallerini tek
 * gorunumde gosterir (kaynak alt-basliklari + tip gruplari). Kanal secmeye
 * gerek kalmadan hepsi bir bakista.
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { SignalCatalogRow, SignalDataType, SignalLiveRow, SignalSource } from "../../shared/types";

type Props = {
  device: { id: number; code: string };
  values: SignalLiveRow[];
  signals: SignalCatalogRow[];
  gwOnline: boolean;
};

const SOURCES: { key: SignalSource; label: string; tone: string }[] = [
  { key: "master", label: "Master", tone: "master" },
  { key: "sat01", label: "Satellite 01", tone: "green" },
  { key: "sat02", label: "Satellite 02", tone: "amber" },
];

const NUMBER_FORMATTER = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 6,
  useGrouping: false,
});

function fmtVal(row: SignalLiveRow, dataType: string | undefined): string {
  if (dataType === "string") {
    const t = (row.value_string ?? "").trim();
    return t.length > 0 ? t : "—";
  }
  if (row.value == null) return "—";
  if (dataType === "binary" || dataType === "binary_output") return row.value ? "AKTİF" : "PASİF";
  if (!Number.isFinite(row.value)) return String(row.value);
  const txt = dataType === "counter" ? Math.round(row.value).toString() : NUMBER_FORMATTER.format(row.value);
  return row.unit ? `${txt} ${row.unit}` : txt;
}

export function DeviceAllSignalsTab({ device, values, signals, gwOnline }: Props) {
  const { t } = useTranslation();

  const typeByKey = useMemo(() => {
    const m = new Map<string, SignalDataType>();
    for (const s of signals) m.set(s.key, s.data_type);
    return m;
  }, [signals]);

  // kaynak -> satirlar (label'a gore sirali)
  const bySource = useMemo(() => {
    const m = new Map<SignalSource, SignalLiveRow[]>();
    for (const src of SOURCES) m.set(src.key, []);
    for (const r of values) {
      if (r.device_id !== device.id) continue;
      const arr = m.get(r.source as SignalSource);
      if (arr) arr.push(r);
    }
    for (const arr of m.values()) {
      arr.sort((a, b) => (a.signal_label || a.signal_key).localeCompare(b.signal_label || b.signal_key));
    }
    return m;
  }, [values, device.id]);

  return (
    <div className="device-all">
      {SOURCES.map((src) => {
        const rows = bySource.get(src.key) ?? [];
        if (rows.length === 0) return null;
        return (
          <section key={src.key} className="device-all-source">
            <h3 className={`device-all-source-title tone-${src.tone}`}>
              <span className="device-all-source-badge">{src.label}</span>
              <span className="device-all-source-count">{rows.length}</span>
            </h3>
            <div className="device-all-grid">
              {rows.map((r) => {
                const dt = (r.data_type as string | undefined) ?? typeByKey.get(r.signal_key);
                const q = gwOnline ? r.quality : "bad";
                const bad = q === "bad" || q === "offline" || q === "comm_lost";
                return (
                  <div key={r.signal_key} className={`device-all-item${bad ? " is-stale" : ""}`}>
                    <span className="device-all-item-label" title={r.signal_key}>
                      {r.signal_label || r.signal_key}
                    </span>
                    <span className="device-all-item-value">{fmtVal(r, dt)}</span>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
      {[...bySource.values()].every((a) => a.length === 0) ? (
        <div className="device-events-empty">
          <span className="material-symbols-outlined">sensors_off</span>
          <p>{t("deviceDetail.noSignals", { source: "" })}</p>
        </div>
      ) : null}
    </div>
  );
}
