/**
 * DeviceChartsPanel — historian (telemetry_history) COKLU sinyal trend analizi.
 *
 * Kullanici birden fazla sinyal (suffix bazli) + kaynak (master/sat01/sat02)
 * secer; her (sinyal x kaynak) kombinasyonu ayri seri olarak TEK grafikte
 * cizilir. Renk = sinyal, cizgi stili (duz/kesikli/noktali) = kaynak.
 *
 * Backend history TEK sinyal doner; coklu icin her seri AYRI fetch (Promise.all,
 * cancellation flag). Aralik/bucket otomatik (raw/1m/1h). Ozel tarih araligi da
 * secilebilir. Secim localStorage'a kaydedilir (cihaza donunce ayni gorunum).
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

ChartJS.register(CategoryScale, LinearScale, TimeScale, PointElement, LineElement, Filler, Tooltip, Legend);

const RANGES: { key: string; hours: number; bucket: "raw" | "1m" | "1h" }[] = [
  { key: "1h", hours: 1, bucket: "raw" },
  { key: "6h", hours: 6, bucket: "raw" },
  { key: "24h", hours: 24, bucket: "1m" },
  { key: "7d", hours: 168, bucket: "1h" },
];

// Sinyal renk paleti (indekse gore deterministik, ayirt edilebilir).
const PALETTE = [
  "#0ea5e9", "#f59e0b", "#22c55e", "#ef4444", "#8b5cf6",
  "#ec4899", "#14b8a6", "#f97316", "#3b82f6", "#84cc16",
];
// Kaynak -> cizgi stili (borderDash) + isim.
const SOURCE_META: Record<SignalSource, { label: string; dash: number[] }> = {
  master: { label: "Master", dash: [] },
  sat01: { label: "Sat 01", dash: [6, 3] },
  sat02: { label: "Sat 02", dash: [2, 3] },
};
const ALL_SOURCES: SignalSource[] = ["master", "sat01", "sat02"];

type Props = {
  deviceCode: string;
  activeSource: SignalSource;
  signals: SignalCatalogRow[];
  token: string;
};

type Point = { x: number; y: number | null };
type Bucket = "raw" | "1m" | "1h";

type SavedView = {
  suffixes: string[];
  sources: SignalSource[];
  rangeKey: string;
  customFrom?: string;
  customTo?: string;
};

function isAggregate(
  rows: TelemetryHistoryPoint[] | TelemetryAggregatePoint[]
): rows is TelemetryAggregatePoint[] {
  return rows.length > 0 && "avg_value" in rows[0];
}

function suffixOf(key: string): string {
  const i = key.indexOf(".");
  return i >= 0 ? key.slice(i + 1) : key;
}

// localStorage yukle/kaydet (quota/devre-disi sessiz yut).
function loadView(deviceCode: string): SavedView | null {
  try {
    const raw = window.localStorage.getItem(`hsl.device-trends.${deviceCode}`);
    return raw ? (JSON.parse(raw) as SavedView) : null;
  } catch {
    return null;
  }
}
function saveView(deviceCode: string, v: SavedView): void {
  try {
    window.localStorage.setItem(`hsl.device-trends.${deviceCode}`, JSON.stringify(v));
  } catch {
    /* sessiz */
  }
}

export function DeviceChartsPanel({ deviceCode, activeSource, signals, token }: Props) {
  const { t } = useTranslation();

  // Trend'e uygun sinyaller (analog/counter), suffix bazinda (kaynak-bagimsiz).
  // Ayni suffix birden cok kaynakta olabilir; label/unit ilk gorulenden.
  const suffixCatalog = useMemo(() => {
    const m = new Map<string, { label: string; unit: string | null; sources: Set<SignalSource> }>();
    for (const s of signals) {
      if (s.data_type !== "analog" && s.data_type !== "counter" && s.data_type !== "analog_output") continue;
      if (!s.is_active) continue;
      const suf = suffixOf(s.key);
      const entry = m.get(suf) ?? { label: s.label, unit: s.unit ?? null, sources: new Set<SignalSource>() };
      entry.sources.add(s.source);
      m.set(suf, entry);
    }
    return m;
  }, [signals]);

  const suffixList = useMemo(
    () => [...suffixCatalog.entries()].map(([suffix, v]) => ({ suffix, ...v })).sort((a, b) => a.label.localeCompare(b.label)),
    [suffixCatalog]
  );

  // ---- State (localStorage'dan lazy init) ----
  const saved = useMemo(() => loadView(deviceCode), [deviceCode]);
  const [suffixes, setSuffixes] = useState<string[]>(() => {
    if (saved?.suffixes?.length) return saved.suffixes;
    // Varsayilan: akim (yoksa ilk sinyal).
    const def = suffixCatalog.has("actual_current") ? "actual_current" : suffixList[0]?.suffix;
    return def ? [def] : [];
  });
  const [sources, setSources] = useState<SignalSource[]>(() => saved?.sources?.length ? saved.sources : [activeSource]);
  const [rangeKey, setRangeKey] = useState<string>(() => saved?.rangeKey ?? "6h");
  const [customOn, setCustomOn] = useState<boolean>(() => rangeKey === "custom");
  const [customFrom, setCustomFrom] = useState<string>(() => saved?.customFrom ?? "");
  const [customTo, setCustomTo] = useState<string>(() => saved?.customTo ?? "");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // seriKey -> {label, points, color, dash, unit}
  const [series, setSeries] = useState<
    { key: string; label: string; points: Point[]; color: string; dash: number[]; unit: string | null }[]
  >([]);

  // Secimi localStorage'a kaydet.
  useEffect(() => {
    saveView(deviceCode, { suffixes, sources, rangeKey, customFrom, customTo });
  }, [deviceCode, suffixes, sources, rangeKey, customFrom, customTo]);

  // Aktif seri anahtarlari: secili suffix x secili kaynak (katalogda varsa).
  const seriesKeys = useMemo(() => {
    const out: { seriesKey: string; suffix: string; source: SignalSource; label: string; unit: string | null; color: string; dash: number[] }[] = [];
    suffixes.forEach((suf, si) => {
      const cat = suffixCatalog.get(suf);
      if (!cat) return;
      for (const src of sources) {
        if (!cat.sources.has(src)) continue; // o kaynakta bu sinyal yok
        out.push({
          seriesKey: `${src}.${suf}`,
          suffix: suf,
          source: src,
          label: cat.label,
          unit: cat.unit,
          color: PALETTE[si % PALETTE.length],
          dash: SOURCE_META[src].dash,
        });
      }
    });
    return out;
  }, [suffixes, sources, suffixCatalog]);

  // ---- Veri cek (her seri ayri fetch, Promise.all) ----
  useEffect(() => {
    if (!token || seriesKeys.length === 0) {
      setSeries([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Zaman penceresi: ozel varsa custom, yoksa hazir aralik.
    let sinceISO: string;
    let untilISO: string | undefined;
    let bucket: Bucket;
    if (customOn && customFrom && customTo) {
      sinceISO = new Date(customFrom).toISOString();
      untilISO = new Date(customTo).toISOString();
      const spanH = (new Date(customTo).getTime() - new Date(customFrom).getTime()) / 3600_000;
      bucket = spanH <= 6 ? "raw" : spanH <= 48 ? "1m" : "1h";
    } else {
      const range = RANGES.find((r) => r.key === rangeKey) ?? RANGES[1];
      sinceISO = new Date(Date.now() - range.hours * 3600_000).toISOString();
      bucket = range.bucket;
    }

    Promise.all(
      seriesKeys.map((sk) =>
        fetchDeviceHistory(token, deviceCode, sk.seriesKey, {
          bucket,
          since: sinceISO,
          until: untilISO,
          limit: 10000,
        })
          .then((rows) => {
            const points: Point[] = isAggregate(rows)
              ? rows.map((r) => ({ x: new Date(r.bucket).getTime(), y: r.avg_value }))
              : (rows as TelemetryHistoryPoint[]).map((r) => ({ x: new Date(r.source_timestamp).getTime(), y: r.value }));
            return { ...sk, points };
          })
          .catch(() => ({ ...sk, points: [] as Point[] }))
      )
    )
      .then((results) => {
        if (cancelled) return;
        setSeries(
          results.map((r) => ({
            key: r.seriesKey,
            label: `${r.label} · ${SOURCE_META[r.source].label}`,
            points: r.points,
            color: r.color,
            dash: r.dash,
            unit: r.unit,
          }))
        );
      })
      .catch(() => {
        if (!cancelled) setError(t("deviceDetail.charts.loadError"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [token, deviceCode, seriesKeys, rangeKey, customOn, customFrom, customTo, t]);

  // ---- Cift Y-ekseni: ilk 2 farkli birim ----
  const unitAxes = useMemo(() => {
    const units: string[] = [];
    for (const s of series) {
      const u = s.unit ?? "";
      if (!units.includes(u)) units.push(u);
    }
    return { left: units[0] ?? "", right: units[1] }; // right undefined ise tek eksen
  }, [series]);

  const chartData = useMemo(
    () => ({
      datasets: series.map((s) => ({
        label: s.label,
        data: s.points,
        borderColor: s.color,
        backgroundColor: s.color,
        borderWidth: 2,
        borderDash: s.dash,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.25,
        yAxisID: unitAxes.right != null && (s.unit ?? "") === unitAxes.right ? "yR" : "yL",
        spanGaps: true,
      })),
    }),
    [series, unitAxes]
  );

  const chartOptions = useMemo<ChartOptions<"line">>(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          type: "time",
          time: { tooltipFormat: "dd.MM HH:mm", displayFormats: { hour: "HH:mm", day: "dd.MM", minute: "HH:mm" } },
          grid: { color: "rgba(148,163,184,0.12)" },
          ticks: { color: "#94a3b8", maxRotation: 0, autoSkipPadding: 20 },
        },
        yL: {
          position: "left",
          title: { display: !!unitAxes.left, text: unitAxes.left, color: "#94a3b8" },
          grid: { color: "rgba(148,163,184,0.12)" },
          ticks: { color: "#94a3b8" },
        },
        yR: {
          position: "right",
          display: unitAxes.right != null,
          title: { display: !!unitAxes.right, text: unitAxes.right ?? "", color: "#94a3b8" },
          grid: { drawOnChartArea: false },
          ticks: { color: "#94a3b8" },
        },
      },
      plugins: {
        legend: { display: false }, // kendi legend chip'lerimiz
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const s = series[ctx.datasetIndex];
              const y = ctx.parsed.y;
              const u = s?.unit ? ` ${s.unit}` : "";
              return `${ctx.dataset.label}: ${y == null ? "—" : y}${u}`;
            },
          },
        },
      },
    }),
    [series, unitAxes]
  );

  const toggleSuffix = (suf: string) =>
    setSuffixes((prev) => (prev.includes(suf) ? prev.filter((s) => s !== suf) : [...prev, suf]));
  const toggleSource = (src: SignalSource) =>
    setSources((prev) => (prev.includes(src) ? prev.filter((s) => s !== src) : [...prev, src]));

  const hasData = series.some((s) => s.points.length > 0);

  return (
    <div className="device-trend">
      {/* ---- Kontrol paneli ---- */}
      <div className="device-trend-controls">
        {/* Sinyal coklu-sec */}
        <div className="device-trend-signals">
          <span className="device-trend-ctrl-label">{t("deviceDetail.charts.signals")}</span>
          <div className="device-trend-chips">
            {suffixList.map((s, i) => {
              const active = suffixes.includes(s.suffix);
              const color = PALETTE[suffixes.indexOf(s.suffix) % PALETTE.length];
              return (
                <button
                  key={s.suffix}
                  type="button"
                  className={`device-trend-chip${active ? " active" : ""}`}
                  style={active ? { borderColor: color, color } : undefined}
                  onClick={() => toggleSuffix(s.suffix)}
                  title={s.unit ? `${s.label} (${s.unit})` : s.label}
                >
                  {active ? <span className="device-trend-chip-dot" style={{ background: color }} /> : null}
                  {s.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="device-trend-controls-row">
          {/* Kaynak sec */}
          <div className="device-trend-sources">
            <span className="device-trend-ctrl-label">{t("deviceDetail.charts.sources")}</span>
            {ALL_SOURCES.map((src) => (
              <label key={src} className={`device-trend-src${sources.includes(src) ? " active" : ""}`}>
                <input type="checkbox" checked={sources.includes(src)} onChange={() => toggleSource(src)} />
                <span className="device-trend-src-dash" data-src={src} aria-hidden="true" />
                {SOURCE_META[src].label}
              </label>
            ))}
          </div>

          {/* Zaman araligi */}
          <div className="device-trend-ranges">
            {RANGES.map((r) => (
              <button
                key={r.key}
                type="button"
                className={`device-trend-range${!customOn && rangeKey === r.key ? " active" : ""}`}
                onClick={() => {
                  setCustomOn(false);
                  setRangeKey(r.key);
                }}
              >
                {t(`deviceDetail.charts.range.${r.key}`)}
              </button>
            ))}
            <button
              type="button"
              className={`device-trend-range${customOn ? " active" : ""}`}
              onClick={() => {
                setCustomOn(true);
                setRangeKey("custom");
              }}
            >
              {t("deviceDetail.charts.custom")}
            </button>
          </div>
        </div>

        {/* Ozel tarih araligi */}
        {customOn ? (
          <div className="device-trend-custom">
            <label>
              {t("deviceDetail.charts.from")}
              <input type="datetime-local" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} />
            </label>
            <label>
              {t("deviceDetail.charts.to")}
              <input type="datetime-local" value={customTo} onChange={(e) => setCustomTo(e.target.value)} />
            </label>
          </div>
        ) : null}
      </div>

      {/* ---- Grafik ---- */}
      <div className="device-trend-canvas">
        {seriesKeys.length === 0 ? (
          <div className="device-trend-empty">
            <span className="material-symbols-outlined">show_chart</span>
            <p>{t("deviceDetail.charts.selectSignal")}</p>
          </div>
        ) : loading && !hasData ? (
          <div className="device-trend-empty">
            <span className="btn-spinner" aria-hidden="true" />
          </div>
        ) : error ? (
          <div className="device-trend-empty is-error">
            <span className="material-symbols-outlined">error</span>
            <p>{error}</p>
          </div>
        ) : !hasData ? (
          <div className="device-trend-empty">
            <span className="material-symbols-outlined">timeline</span>
            <p>{t("deviceDetail.charts.noData")}</p>
          </div>
        ) : (
          <Line data={chartData} options={chartOptions} />
        )}
      </div>

      {/* ---- Legend (seri chip'leri) ---- */}
      {series.length > 0 ? (
        <div className="device-trend-legend">
          {series.map((s) => (
            <span key={s.key} className="device-trend-legend-item">
              <span
                className="device-trend-legend-line"
                style={{
                  background: s.color,
                  ...(s.dash.length ? { backgroundImage: `repeating-linear-gradient(90deg, ${s.color} 0 6px, transparent 6px 10px)`, background: "transparent" } : {}),
                }}
              />
              {s.label}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
