/**
 * Trend grafiklerinin ORTAK cekirdegi (ECharts, acik tema).
 *
 * NEDEN AYRI DOSYA
 * ----------------
 * Iki ekran ayni grafigi ciziyor:
 *   * Cihaz Detayi > Trendler  — TEK cihaz, sinyal x kaynak karsilastirmasi
 *   * Analiz > Trendler        — TUM cihazlar, cihaz x sinyal karsilastirmasi
 * Seri TANIMI farkli (birinde cihaz sabit), ama cizim, tema, tooltip,
 * dataZoom, cift eksen ve ayarlar AYNI. Bunlari iki yerde tutmak, birinde
 * yapilan iyilestirmenin digerinde sessizce eksik kalmasi demekti.
 *
 * Buradaki her sey SAF: React yok, veri cekme yok. Sadece "seri listesi ->
 * ECharts option". Boylece iki ekran da kendi veri modelini koruyup ayni
 * gorunumu paylasiyor.
 */

import * as echarts from "echarts/core";

import type {
  HistoryBucket,
  TelemetryAggregatePoint,
  TelemetryHistoryPoint,
} from "../../shared/types";

export type ChartType = "line" | "area" | "bar";

export type ChartSettings = {
  smooth: boolean; // cizgi/alan: yumusak
  showSymbol: boolean; // cizgi/alan: nokta goster
  lineWidth: number; // cizgi/alan kalinlik
  barStack: boolean; // bar: ust uste (stack)
  barWidth: number; // bar: genislik (px, 0=otomatik)
  barRadius: number; // bar: ust kose yuvarlaklik (px)
};

export const DEFAULT_SETTINGS: ChartSettings = {
  smooth: true,
  showSymbol: false,
  lineWidth: 2,
  barStack: false,
  barWidth: 0,
  barRadius: 3,
};

/** Zaman araliklari — bucket araliga gore otomatik secilir. */
export const RANGES: { key: string; minutes: number; bucket: HistoryBucket }[] = [
  { key: "5m", minutes: 5, bucket: "10s" },
  { key: "15m", minutes: 15, bucket: "10s" },
  { key: "1h", minutes: 60, bucket: "raw" },
  { key: "6h", minutes: 360, bucket: "raw" },
  { key: "24h", minutes: 1440, bucket: "1m" },
  { key: "7d", minutes: 10080, bucket: "1h" },
  { key: "30d", minutes: 43200, bucket: "1h" },
];

/** Seri renkleri — hem varsayilan atama hem kullanici secimi icin. */
export const PALETTE = [
  "#38bdf8", "#f59e0b", "#34d399", "#f87171", "#a78bfa",
  "#f472b6", "#2dd4bf", "#fb923c", "#60a5fa", "#a3e635",
];

/**
 * Grafige uygun OLMAYAN sinyaller (sabit/konum/kimlik) — listelerde gizli.
 * Bunlar zaman icinde degismez; trend ekranini doldurup asil olcumleri
 * bulunmaz hale getiriyorlardi.
 */
export const NON_TREND_RE =
  /(firmware|fw_version|hardware_revision|serial|part_no|latitude|longitude|gps|test_point|modem_model|imei|sim_serial|ipv4|ip_address|dial_in|comm_library|network_operator|network_type|network_registration|rtu_status|device_position|last_configuration|nominal_voltage|pitch_angle)/;

export type Point = [number, number | null]; // [timestamp, value]

export function isAggregate(
  rows: TelemetryHistoryPoint[] | TelemetryAggregatePoint[]
): rows is TelemetryAggregatePoint[] {
  return rows.length > 0 && "avg_value" in rows[0];
}

/** "master.actual_current" -> "actual_current" */
export function suffixOf(key: string): string {
  const i = key.indexOf(".");
  return i >= 0 ? key.slice(i + 1) : key;
}

/** Ham/aggregate satirlari tek bir nokta dizisine cevirir. */
export function toPoints(
  rows: TelemetryHistoryPoint[] | TelemetryAggregatePoint[]
): Point[] {
  return isAggregate(rows)
    ? rows.map((r) => [new Date(r.bucket).getTime(), r.avg_value] as Point)
    : (rows as TelemetryHistoryPoint[]).map(
        (r) => [new Date(r.source_timestamp).getTime(), r.value] as Point
      );
}

/**
 * Aralik genisligine gore historian kovasi. Ozel tarih araliginda kullanilir;
 * hazir araliklarda `RANGES[].bucket` gecerlidir.
 */
export function bucketForSpanHours(spanHours: number): HistoryBucket {
  if (spanHours <= 1) return "raw";
  if (spanHours <= 12) return "1m";
  if (spanHours <= 72) return "5m";
  return "1h";
}

export type ChartSeries = {
  id: string;
  label: string;
  points: Point[];
  color: string;
  unit: string | null;
};

/**
 * Seri listesindeki AYRIK birimler. Iki farkli birim varsa ikinci eksen
 * sagda acilir — akim ile gerilimi ayni eksende gostermek grafigi
 * okunamaz hale getiriyordu.
 */
export function unitsOf(series: ChartSeries[]): string[] {
  const u: string[] = [];
  for (const s of series) {
    const un = s.unit ?? "";
    if (!u.includes(un)) u.push(un);
  }
  return u;
}

/**
 * line/area/bar icin ECharts option'i uretir (acik tema).
 *
 * `heatmap` BURADA YOK: o gorunum cihaz ekranina ozel (kaynak x zaman) ve
 * kendi veri hazirligini gerektiriyor; ortak cekirdege tasimak iki ekranin
 * veri modelini birbirine baglardi.
 */
export function buildTrendOption(args: {
  series: ChartSeries[];
  chartType: ChartType;
  settings: ChartSettings;
}) {
  const { series, chartType, settings } = args;
  const units = unitsOf(series);

  const mkAxis = (name: string, position: "left" | "right", showSplit: boolean) => ({
    type: "value" as const,
    name,
    position,
    splitLine: {
      lineStyle: {
        color: showSplit ? "rgba(148,163,184,0.22)" : "rgba(148,163,184,0)",
      },
    },
    axisLabel: { color: "#64748b" },
    nameTextStyle: { color: "#94a3b8" },
  });
  const yAxes = [mkAxis(units[0] ?? "", "left", true)];
  if (units[1] != null) yAxes.push(mkAxis(units[1], "right", false));

  return {
    backgroundColor: "transparent",
    animationDuration: 300,
    grid: {
      left: 8,
      right: units[1] != null ? 8 : 16,
      top: 16,
      bottom: 44,
      containLabel: true,
    },
    // Alt slider: zaman penceresini kaydir/yakinlastir.
    dataZoom: [
      { type: "inside", filterMode: "none" as const },
      {
        type: "slider" as const,
        height: 22,
        bottom: 6,
        filterMode: "none" as const,
        borderColor: "rgba(148,163,184,0.35)",
        fillerColor: "rgba(14,165,233,0.12)",
        handleStyle: { color: "#0ea5e9" },
        moveHandleStyle: { color: "#0ea5e9" },
        textStyle: { color: "#64748b", fontSize: 10 },
        dataBackground: {
          lineStyle: { color: "#cbd5e1" },
          areaStyle: { color: "rgba(148,163,184,0.15)" },
        },
      },
    ],
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(255,255,255,0.98)",
      borderColor: "#e2e8f0",
      borderWidth: 1,
      textStyle: { color: "#0f172a", fontSize: 12 },
      extraCssText:
        "box-shadow: 0 6px 20px rgba(15,23,42,0.12); border-radius: 10px; padding: 8px 10px;",
      axisPointer: { type: "cross", lineStyle: { color: "rgba(148,163,184,0.5)" } },
      // Birim seri sirasindan okunur (series[].unit); ECharts seriesIndex verir.
      formatter: (
        params: {
          axisValueLabel?: string;
          axisValue?: number;
          seriesIndex: number;
          value: [number, number | null];
          marker: string;
          seriesName: string;
        }[]
      ) => {
        if (!params.length) return "";
        const ts = params[0].axisValue;
        const head =
          ts != null
            ? new Date(ts).toLocaleString(undefined, {
                day: "2-digit",
                month: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
              })
            : (params[0].axisValueLabel ?? "");
        const rows = params
          .map((pt) => {
            const v = Array.isArray(pt.value) ? pt.value[1] : null;
            const unit = series[pt.seriesIndex]?.unit ?? "";
            const valStr =
              v == null
                ? "—"
                : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
            return (
              `<div style="display:flex;align-items:center;gap:6px;margin-top:3px">${pt.marker}` +
              `<span style="flex:1;color:#475569">${pt.seriesName}</span>` +
              `<span style="font-weight:800;color:#0f172a">${valStr}</span>` +
              (unit ? `<span style="color:#94a3b8;font-size:11px">${unit}</span>` : "") +
              `</div>`
            );
          })
          .join("");
        return `<div style="font-size:11px;color:#94a3b8;margin-bottom:2px">${head}</div>${rows}`;
      },
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: "rgba(148,163,184,0.35)" } },
      axisLabel: { color: "#64748b", hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: yAxes,
    series: series.map((s) => ({
      name: s.label,
      type: chartType === "bar" ? ("bar" as const) : ("line" as const),
      data: s.points,
      showSymbol: chartType !== "bar" && settings.showSymbol,
      symbolSize: 5,
      smooth: chartType !== "bar" && settings.smooth ? 0.3 : false,
      yAxisIndex: units[1] != null && (s.unit ?? "") === units[1] ? 1 : 0,
      stack: chartType === "bar" && settings.barStack ? "total" : undefined,
      barWidth: chartType === "bar" && settings.barWidth > 0 ? settings.barWidth : undefined,
      barMaxWidth: chartType === "bar" && settings.barWidth === 0 ? 40 : undefined,
      lineStyle:
        chartType !== "bar" ? { color: s.color, width: settings.lineWidth } : undefined,
      itemStyle: {
        color: s.color,
        borderRadius: chartType === "bar" ? [settings.barRadius, settings.barRadius, 0, 0] : 0,
      },
      areaStyle:
        chartType === "area"
          ? {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: s.color + "33" },
                { offset: 1, color: s.color + "05" },
              ]),
            }
          : undefined,
      connectNulls: true,
    })),
  };
}
