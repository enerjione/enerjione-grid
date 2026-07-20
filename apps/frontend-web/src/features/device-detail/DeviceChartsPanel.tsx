/**
 * DeviceChartsPanel — historian (telemetry_history) zaman serisi grafikleri.
 *
 * Secili kaynak (master/sat01/sat02) icin numeric sinyalleri listeler; kullanici
 * bir sinyal + zaman araligi secer, GET /devices/{code}/history'den seri cekilir.
 * Aralik/bucket otomatik: kisa aralik ham (raw), gunluk 1dk, haftalik 1saat
 * aggregate — ham veri patlamasini onler. String sinyaller (value NULL) haric.
 */

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  TimeScale,
  Tooltip,
  type ChartOptions,
} from "chart.js";
import "chartjs-adapter-date-fns";
import { Line } from "react-chartjs-2";

import { fetchDeviceHistory } from "../../shared/api";
import type {
  SignalCatalogRow,
  SignalSource,
  TelemetryAggregatePoint,
  TelemetryHistoryPoint,
} from "../../shared/types";

ChartJS.register(
  CategoryScale,
  LinearScale,
  TimeScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
  Legend
);

// Zaman araliklari — bucket otomatik secilir (ham veri patlamasini onlemek icin).
const RANGES: { key: string; hours: number; bucket: "raw" | "1m" | "1h" }[] = [
  { key: "1h", hours: 1, bucket: "raw" },
  { key: "6h", hours: 6, bucket: "raw" },
  { key: "24h", hours: 24, bucket: "1m" },
  { key: "7d", hours: 168, bucket: "1h" },
];

// Grafik cizgi rengi — tema-notr, erisilebilir (emerald, proje aksani).
const LINE = "#0ea5e9";
const LINE_FILL = "rgba(14, 165, 233, 0.12)";
const BAND = "rgba(14, 165, 233, 0.10)"; // min-max band (aggregate)

type Props = {
  deviceCode: string;
  activeSource: SignalSource;
  signals: SignalCatalogRow[];
  token: string;
};

type Point = { x: number; y: number | null };

function isAggregate(
  rows: TelemetryHistoryPoint[] | TelemetryAggregatePoint[]
): rows is TelemetryAggregatePoint[] {
  return rows.length > 0 && "avg_value" in rows[0];
}

export function DeviceChartsPanel({ deviceCode, activeSource, signals, token }: Props) {
  const { t } = useTranslation();
  const [rangeKey, setRangeKey] = useState<string>("6h");
  const [signalKey, setSignalKey] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [series, setSeries] = useState<{ avg: Point[]; min: Point[]; max: Point[] }>({
    avg: [], min: [], max: [],
  });

  // Bu kaynagin grafiklenebilir (numeric) sinyalleri: analog/counter/analog_output.
  const numericSignals = useMemo(
    () =>
      signals
        .filter(
          (s) =>
            s.source === activeSource &&
            (s.data_type === "analog" ||
              s.data_type === "counter" ||
              s.data_type === "analog_output")
        )
        .sort((a, b) => a.display_order - b.display_order),
    [signals, activeSource]
  );

  // Kaynak degisince gecerli sinyal secimi kaynakta yoksa ilkine dus.
  useEffect(() => {
    if (numericSignals.length === 0) {
      setSignalKey("");
      return;
    }
    if (!numericSignals.some((s) => s.key === signalKey)) {
      setSignalKey(numericSignals[0].key);
    }
  }, [numericSignals, signalKey]);

  const range = RANGES.find((r) => r.key === rangeKey) ?? RANGES[1];
  const unit = useMemo(
    () => signals.find((s) => s.key === signalKey)?.unit ?? "",
    [signals, signalKey]
  );

  useEffect(() => {
    if (!signalKey || !token) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    const since = new Date(Date.now() - range.hours * 3600_000).toISOString();
    fetchDeviceHistory(token, deviceCode, signalKey, {
      bucket: range.bucket,
      since,
      limit: 10000,
    })
      .then((rows) => {
        if (cancelled) return;
        if (isAggregate(rows)) {
          setSeries({
            avg: rows.map((r) => ({ x: new Date(r.bucket).getTime(), y: r.avg_value })),
            min: rows.map((r) => ({ x: new Date(r.bucket).getTime(), y: r.min_value })),
            max: rows.map((r) => ({ x: new Date(r.bucket).getTime(), y: r.max_value })),
          });
        } else {
          setSeries({
            avg: rows.map((r) => ({ x: new Date(r.source_timestamp).getTime(), y: r.value })),
            min: [],
            max: [],
          });
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [signalKey, token, deviceCode, range.hours, range.bucket]);

  const hasAgg = series.min.length > 0;
  const data = useMemo(
    () => ({
      datasets: [
        ...(hasAgg
          ? [
              {
                label: t("deviceDetail.charts.max"),
                data: series.max,
                borderColor: "transparent",
                backgroundColor: BAND,
                pointRadius: 0,
                fill: "+1" as const, // min'e kadar doldur (min-max band)
                tension: 0.25,
              },
              {
                label: t("deviceDetail.charts.min"),
                data: series.min,
                borderColor: "transparent",
                pointRadius: 0,
                fill: false as const,
                tension: 0.25,
              },
            ]
          : []),
        {
          label: hasAgg ? t("deviceDetail.charts.avg") : t("deviceDetail.charts.value"),
          data: series.avg,
          borderColor: LINE,
          backgroundColor: LINE_FILL,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          fill: !hasAgg,
          tension: 0.25,
        },
      ],
    }),
    [series, hasAgg, t]
  );

  const options = useMemo<ChartOptions<"line">>(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          type: "time",
          time: { tooltipFormat: "dd.MM HH:mm", displayFormats: { hour: "HH:mm", day: "dd.MM" } },
          grid: { color: "rgba(148, 163, 184, 0.12)" },
          ticks: { color: "#64748b", maxRotation: 0, autoSkipPadding: 24 },
        },
        y: {
          grid: { color: "rgba(148, 163, 184, 0.12)" },
          ticks: {
            color: "#64748b",
            callback: (v) => (unit ? `${v} ${unit}` : `${v}`),
          },
        },
      },
      plugins: {
        legend: {
          display: hasAgg,
          labels: { color: "#475569", filter: (l) => l.text !== t("deviceDetail.charts.min") },
        },
        tooltip: {
          callbacks: {
            label: (ctx) =>
              `${ctx.dataset.label}: ${ctx.parsed.y ?? "—"}${unit ? ` ${unit}` : ""}`,
          },
        },
      },
    }),
    [unit, hasAgg, t]
  );

  if (numericSignals.length === 0) {
    return (
      <div className="device-detail-empty">
        <span className="material-symbols-outlined">show_chart</span>
        <p className="helper-text">{t("deviceDetail.charts.noNumeric")}</p>
      </div>
    );
  }

  const empty = !loading && series.avg.length === 0;

  return (
    <div className="device-chart-panel">
      <div className="device-chart-controls">
        <label className="device-chart-select">
          <span>{t("deviceDetail.charts.signal")}</span>
          <select value={signalKey} onChange={(e) => setSignalKey(e.target.value)}>
            {numericSignals.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
                {s.unit ? ` (${s.unit})` : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="device-chart-ranges">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              className={`device-chart-range${rangeKey === r.key ? " active" : ""}`}
              onClick={() => setRangeKey(r.key)}
            >
              {t(`deviceDetail.charts.range.${r.key}`)}
            </button>
          ))}
        </div>
      </div>

      <div className="device-chart-canvas">
        {loading ? (
          <div className="device-chart-status">
            <span className="btn-spinner" aria-hidden="true" />
            {t("deviceDetail.charts.loading")}
          </div>
        ) : error ? (
          <div className="device-chart-status is-error">
            <span className="material-symbols-outlined">error</span>
            {error}
          </div>
        ) : empty ? (
          <div className="device-chart-status">
            <span className="material-symbols-outlined">data_usage</span>
            {t("deviceDetail.charts.empty")}
          </div>
        ) : (
          <Line data={data} options={options} />
        )}
      </div>
    </div>
  );
}
