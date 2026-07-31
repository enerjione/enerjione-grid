/**
 * DeviceChartsPanel — historian trend analiz araci (ECharts).
 *
 * Sol panel: eklenmis sinyaller (renk + kaynak + sil) + "Sinyal Ekle" popup
 * (sinyal sec + kaynak + renk). Sag: koyu temali ECharts grafik, ekrani doldurur.
 * Her (sinyal x kaynak) ayri seri; backend tek-sinyal doner -> Promise.all fetch.
 * Kisa araliklar (5dk..7g) + ozel tarih. Secim localStorage'a (cihaza donunce ayni).
 *
 * Asamali: bu surum line grafik + sol panel + kisa aralik. Bar/heatmap/dataZoom/
 * realtime sonraki asamalarda (chartType/series builder genisletilebilir yapida).
 */

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactECharts from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { LineChart, BarChart, HeatmapChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import { fetchDeviceHistory, API_BASE_URL } from "../../shared/api";
import { useLiveValuesSocket } from "../../shared/useLiveValuesSocket";
import type {
  HistoryBucket,
  SignalCatalogRow,
  SignalSource,
  TelemetryAggregatePoint,
  TelemetryHistoryPoint,
} from "../../shared/types";

echarts.use([LineChart, BarChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent, VisualMapComponent, CanvasRenderer]);

type ChartType = "line" | "area" | "bar" | "heatmap";
type HeatmapMode = "alarm" | "value"; // alarm yogunlugu / secili sinyal degeri
type ChartSettings = {
  smooth: boolean;       // cizgi/alan: yumusak
  showSymbol: boolean;   // cizgi/alan: nokta goster
  lineWidth: number;     // cizgi/alan kalinlik
  barStack: boolean;     // bar: ust uste (stack)
  barWidth: number;      // bar: genislik (px, 0=otomatik)
  barRadius: number;     // bar: ust kose yuvarlaklik (px)
};
const DEFAULT_SETTINGS: ChartSettings = { smooth: true, showSymbol: false, lineWidth: 2, barStack: false, barWidth: 0, barRadius: 3 };

// Zaman araliklari (kisa araliklar dahil) — bucket araliga gore otomatik.
const RANGES: { key: string; minutes: number; bucket: HistoryBucket }[] = [
  { key: "5m", minutes: 5, bucket: "10s" },
  { key: "15m", minutes: 15, bucket: "10s" },
  { key: "1h", minutes: 60, bucket: "raw" },
  { key: "6h", minutes: 360, bucket: "raw" },
  { key: "24h", minutes: 1440, bucket: "1m" },
  { key: "7d", minutes: 10080, bucket: "1h" },
];

// Renk paleti (sinyal ekleme popup'inda secilebilir + varsayilan atama).
const PALETTE = [
  "#38bdf8", "#f59e0b", "#34d399", "#f87171", "#a78bfa",
  "#f472b6", "#2dd4bf", "#fb923c", "#60a5fa", "#a3e635",
];
const SOURCE_META: Record<SignalSource, { label: string }> = {
  master: { label: "Master" },
  sat01: { label: "Satellite 01" },
  sat02: { label: "Satellite 02" },
};
const ALL_SOURCES: SignalSource[] = ["master", "sat01", "sat02"];

// Grafige uygun OLMAYAN sinyaller (sabit/konum/kimlik) — listede gizli.
const NON_TREND_RE =
  /(firmware|fw_version|hardware_revision|serial|part_no|latitude|longitude|gps|test_point|modem_model|imei|sim_serial|ipv4|ip_address|dial_in|comm_library|network_operator|network_type|network_registration|rtu_status|device_position|last_configuration|nominal_voltage|pitch_angle)/;

type Props = {
  deviceCode: string;
  activeSource: SignalSource;
  signals: SignalCatalogRow[];
  token: string;
};

type Point = [number, number | null]; // [timestamp, value]

// Eklenmis bir seri = tek sinyal + tek cihaz + renk (id ile benzersiz).
type SeriesDef = { id: string; suffix: string; source: SignalSource; color: string };
type SavedView = {
  defs: SeriesDef[];
  rangeKey: string;
  customFrom?: string;
  customTo?: string;
  chartType?: ChartType;
  settings?: ChartSettings;
  heatmapMode?: HeatmapMode;
};

let _idCounter = 0;
function newId(): string {
  _idCounter += 1;
  return `s${_idCounter}_${Math.floor(performance.now())}`;
}

function isAggregate(
  rows: TelemetryHistoryPoint[] | TelemetryAggregatePoint[]
): rows is TelemetryAggregatePoint[] {
  return rows.length > 0 && "avg_value" in rows[0];
}
function suffixOf(key: string): string {
  const i = key.indexOf(".");
  return i >= 0 ? key.slice(i + 1) : key;
}
function loadView(code: string): SavedView | null {
  try {
    const raw = window.localStorage.getItem(`hsl.device-trends.${code}`);
    return raw ? (JSON.parse(raw) as SavedView) : null;
  } catch {
    return null;
  }
}
function saveView(code: string, v: SavedView): void {
  try {
    window.localStorage.setItem(`hsl.device-trends.${code}`, JSON.stringify(v));
  } catch {
    /* sessiz */
  }
}

export function DeviceChartsPanel({ deviceCode, activeSource, signals, token }: Props) {
  const { t } = useTranslation();

  // Trend'e uygun sinyaller (analog/counter), suffix bazinda. Sabit/konum/kimlik
  // sinyalleri (firmware/hardware/latitude vb) HARIC — grafigi anlamsiz doldurur.
  const suffixCatalog = useMemo(() => {
    const m = new Map<string, { label: string; unit: string | null; sources: Set<SignalSource> }>();
    for (const s of signals) {
      if (s.data_type !== "analog" && s.data_type !== "counter" && s.data_type !== "analog_output") continue;
      if (!s.is_active) continue;
      const suf = suffixOf(s.key);
      if (NON_TREND_RE.test(suf)) continue; // grafige uygun degil
      const e = m.get(suf) ?? { label: s.label, unit: s.unit ?? null, sources: new Set<SignalSource>() };
      e.sources.add(s.source);
      m.set(suf, e);
    }
    // Sanal sinyal: alarm_rate (backend alarm_reconciliation worker'i
    // master.alarm_rate historian'a yazar; katalogda YOK ama historian sorgusu
    // katalogsuz calisir). Trendler'de/heatmap'te secilebilsin diye elle ekle.
    m.set("alarm_rate", { label: t("deviceDetail.charts.alarmRate"), unit: t("deviceDetail.charts.perMin"), sources: new Set<SignalSource>(["master"]) });
    return m;
  }, [signals, t]);
  const suffixList = useMemo(
    () => [...suffixCatalog.entries()].map(([suffix, v]) => ({ suffix, ...v })).sort((a, b) => a.label.localeCompare(b.label)),
    [suffixCatalog]
  );

  // ---- State (localStorage lazy init) ----
  const saved = useMemo(() => loadView(deviceCode), [deviceCode]);
  const [defs, setDefs] = useState<SeriesDef[]>(() => {
    if (saved?.defs?.length) return saved.defs.filter((d) => d.source); // eski cok-kaynak kayitlari ele
    const def = suffixCatalog.has("actual_current") ? "actual_current" : suffixList[0]?.suffix;
    return def ? [{ id: newId(), suffix: def, source: activeSource, color: PALETTE[0] }] : [];
  });
  const [rangeKey, setRangeKey] = useState<string>(() => saved?.rangeKey ?? "1h");
  const [customOn, setCustomOn] = useState<boolean>(() => (saved?.rangeKey ?? "1h") === "custom");
  const [customFrom, setCustomFrom] = useState<string>(() => saved?.customFrom ?? "");
  const [customTo, setCustomTo] = useState<string>(() => saved?.customTo ?? "");
  const [popupOpen, setPopupOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null); // duzenlenen seri (null=yeni)
  const [chartType, setChartType] = useState<ChartType>(() => saved?.chartType ?? "area");
  const [settings, setSettings] = useState<ChartSettings>(() => ({ ...DEFAULT_SETTINGS, ...(saved?.settings ?? {}) }));
  const [gearOpen, setGearOpen] = useState(false);
  const [heatmapMode, setHeatmapMode] = useState<HeatmapMode>(() => saved?.heatmapMode ?? "alarm");
  // heatmap deger modu icin secili sinyal (ilk def'in suffix'i varsayilan).
  const [heatmapSignal, setHeatmapSignal] = useState<string>("");
  // heatmap: {sources x zaman-kova -> deger} + zaman etiketleri.
  const [heatData, setHeatData] = useState<{ cols: string[]; cells: [number, number, number | null][]; max: number }>(
    { cols: [], cells: [], max: 0 }
  );

  const [live, setLive] = useState(false); // canli mod: WS ile anlik append
  const [liveTick, setLiveTick] = useState(0); // heatmap canli: periyodik refetch tetigi
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [series, setSeries] = useState<
    { id: string; label: string; points: Point[]; color: string; unit: string | null }[]
  >([]);

  useEffect(() => {
    saveView(deviceCode, { defs, rangeKey: customOn ? "custom" : rangeKey, customFrom, customTo, chartType, settings, heatmapMode });
  }, [deviceCode, defs, rangeKey, customOn, customFrom, customTo, chartType, settings, heatmapMode]);

  // Aktif seri anahtarlari: her def = tek sinyal + tek cihaz (katalogda varsa).
  const seriesKeys = useMemo(() => {
    const out: { id: string; seriesKey: string; source: SignalSource; label: string; unit: string | null; color: string }[] = [];
    for (const d of defs) {
      const cat = suffixCatalog.get(d.suffix);
      if (!cat || !cat.sources.has(d.source)) continue;
      out.push({ id: d.id, seriesKey: `${d.source}.${d.suffix}`, source: d.source, label: cat.label, unit: cat.unit, color: d.color });
    }
    return out;
  }, [defs, suffixCatalog]);

  // ---- Veri cek (Promise.all, cancellation flag) ----
  useEffect(() => {
    if (!token || seriesKeys.length === 0) {
      setSeries([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    let sinceISO: string;
    let untilISO: string | undefined;
    let bucket: HistoryBucket;
    if (customOn && customFrom && customTo) {
      sinceISO = new Date(customFrom).toISOString();
      untilISO = new Date(customTo).toISOString();
      const spanH = (new Date(customTo).getTime() - new Date(customFrom).getTime()) / 3600_000;
      bucket = spanH <= 1 ? "raw" : spanH <= 12 ? "1m" : spanH <= 72 ? "5m" : "1h";
    } else {
      const range = RANGES.find((r) => r.key === rangeKey) ?? RANGES[2];
      sinceISO = new Date(Date.now() - range.minutes * 60_000).toISOString();
      bucket = range.bucket;
    }

    Promise.all(
      seriesKeys.map((sk) =>
        fetchDeviceHistory(token, deviceCode, sk.seriesKey, { bucket, since: sinceISO, until: untilISO, limit: 10000 })
          .then((rows) => {
            const points: Point[] = isAggregate(rows)
              ? rows.map((r) => [new Date(r.bucket).getTime(), r.avg_value])
              : (rows as TelemetryHistoryPoint[]).map((r) => [new Date(r.source_timestamp).getTime(), r.value]);
            return { ...sk, points };
          })
          .catch(() => ({ ...sk, points: [] as Point[] }))
      )
    )
      .then((results) => {
        if (cancelled) return;
        setSeries(
          results.map((r) => ({
            id: r.id,
            label: `${r.label} · ${SOURCE_META[r.source].label}`,
            points: r.points,
            color: r.color,
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

  // ---- Heatmap veri (3 kaynak x zaman kova) ----
  useEffect(() => {
    if (chartType !== "heatmap" || !token) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Zaman penceresi + kova cozunurlugu (kisa aralik -> saat, uzun -> gun).
    let sinceMs: number;
    let untilMs: number;
    if (customOn && customFrom && customTo) {
      sinceMs = new Date(customFrom).getTime();
      untilMs = new Date(customTo).getTime();
    } else {
      const range = RANGES.find((r) => r.key === rangeKey) ?? RANGES[2];
      untilMs = Date.now();
      sinceMs = untilMs - range.minutes * 60_000;
    }
    const spanH = (untilMs - sinceMs) / 3600_000;
    // Kova cozunurlugunu aralik genisligine gore kademelendir (hedef ~10-40 kolon).
    // Cok kisa aralikta (5-15dk) saatlik kova tek sutuna dusuyor -> dakika kovasi.
    //   <=30dk -> 1dk | <=3s -> 5dk | <=48s -> saat | ustu -> gun
    type Grain = "min" | "min5" | "hour" | "day";
    const grain: Grain =
      spanH <= 0.5 ? "min" : spanH <= 3 ? "min5" : spanH <= 48 ? "hour" : "day";
    const bucketMs =
      grain === "min" ? 60_000 : grain === "min5" ? 300_000 : grain === "hour" ? 3600_000 : 86_400_000;
    // Historian bucket: kova cozunurlugune uygun (kovadan ince olmali).
    const histBucket: HistoryBucket =
      grain === "min" ? "10s" : grain === "min5" ? "1m" : grain === "hour" ? "5m" : "1h";

    // Kova sinirlarini olustur (kova basi timestamp -> kolon index).
    // Guvenlik: cok sayida kova (uzun custom + dakika) okunmaz + agir olur;
    // 400 kolonda kes (pratikte grain zaten kademeli, bu son emniyet).
    const MAX_COLS = 400;
    const colStarts: number[] = [];
    const first = Math.floor(sinceMs / bucketMs) * bucketMs;
    for (let ts = first; ts <= untilMs && colStarts.length < MAX_COLS; ts += bucketMs) colStarts.push(ts);
    const colFmt = (ts: number): string => {
      const d = new Date(ts);
      const hh = String(d.getHours()).padStart(2, "0");
      const mm = String(d.getMinutes()).padStart(2, "0");
      if (grain === "min" || grain === "min5") return `${hh}:${mm}`;
      if (grain === "hour") return `${hh}:00`;
      return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
    };
    const cols = colStarts.map(colFmt);
    const colIndex = (ts: number): number => {
      const i = Math.floor((ts - first) / bucketMs);
      return i >= 0 && i < colStarts.length ? i : -1;
    };

    // Deger modu icin sinyal (secili yoksa ilk def / actual_current).
    const valSuffix = heatmapSignal || defs[0]?.suffix || "actual_current";
    const sinceISO = new Date(sinceMs).toISOString();
    const untilISO = new Date(untilMs).toISOString();

    // Her kaynak icin fetch. Alarm: {src}.alarm_rate topla; Deger: {src}.{suffix} avg.
    const sigKey = (src: SignalSource) =>
      heatmapMode === "alarm" ? `${src}.alarm_rate` : `${src}.${valSuffix}`;

    Promise.all(
      ALL_SOURCES.map((src) =>
        fetchDeviceHistory(token, deviceCode, sigKey(src), { bucket: histBucket, since: sinceISO, until: untilISO, limit: 10000 })
          .then((rows) => {
            // Kova bazinda topla: alarm=sum, deger=avg (ortalama).
            const sums = new Array(colStarts.length).fill(0);
            const counts = new Array(colStarts.length).fill(0);
            const norm: { ts: number; v: number | null }[] = isAggregate(rows)
              ? rows.map((r) => ({ ts: new Date(r.bucket).getTime(), v: heatmapMode === "value" ? r.avg_value : r.avg_value }))
              : (rows as TelemetryHistoryPoint[]).map((r) => ({ ts: new Date(r.source_timestamp).getTime(), v: r.value }));
            for (const p of norm) {
              if (p.v == null) continue;
              const ci = colIndex(p.ts);
              if (ci < 0) continue;
              sums[ci] += p.v;
              counts[ci] += 1;
            }
            return { src, sums, counts };
          })
          .catch(() => ({ src, sums: new Array(colStarts.length).fill(0), counts: new Array(colStarts.length).fill(0) }))
      )
    )
      .then((results) => {
        if (cancelled) return;
        const cells: [number, number, number | null][] = [];
        let max = 0;
        results.forEach((res, rowIdx) => {
          for (let ci = 0; ci < colStarts.length; ci++) {
            let val: number | null;
            if (heatmapMode === "alarm") {
              val = res.sums[ci]; // toplam alarm sayisi
            } else {
              val = res.counts[ci] > 0 ? res.sums[ci] / res.counts[ci] : null; // ortalama
            }
            if (val != null && val > max) max = val;
            cells.push([ci, rowIdx, val]);
          }
        });
        setHeatData({ cols, cells, max: max || 1 });
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
  }, [chartType, heatmapMode, heatmapSignal, defs, token, deviceCode, rangeKey, customOn, customFrom, customTo, t, liveTick]);

  // ---- Canli mod: WS (line/area/bar append) + heatmap periyodik refetch ----
  const liveDevices = useMemo(() => new Set([deviceCode]), [deviceCode]);
  const { connectionState, registerHandler } = useLiveValuesSocket({
    token,
    apiBaseUrl: API_BASE_URL,
    deviceCodes: liveDevices,
    enabled: live && chartType !== "heatmap",
  });

  // WS mesaji -> ilgili seriye nokta ekle (kayan pencere degil; sadece append).
  // seriesKeys[].seriesKey ("master.actual_current") <-> msg.signal_key eslesir.
  //
  // Mesajlar hook icinde tamponlanip BATCH gelir. Batch once seriesKey bazinda
  // gruplanir, sonra seri dizisi TEK gecisde guncellenir: mesaj basina
  // setSeries + mesaj basina `seriesKeys.find` (O(mesaj x seri)) kalkiyor.
  useEffect(() => {
    if (!live || chartType === "heatmap") return;
    // id -> seriesKey haritasi; her batch'te yeniden aramaya gerek yok.
    const keyById = new Map(seriesKeys.map((k) => [k.id, k.seriesKey]));
    registerHandler((msgs) => {
      // seriesKey -> eklenecek noktalar (zaman sirasi korunur).
      const appends = new Map<string, Point[]>();
      for (const msg of msgs) {
        if (msg.device_code !== deviceCode || msg.value == null) continue;
        const ts = msg.source_timestamp ? new Date(msg.source_timestamp).getTime() : Date.now();
        const bucket = appends.get(msg.signal_key);
        const point: Point = [ts, msg.value ?? null];
        if (bucket) bucket.push(point);
        else appends.set(msg.signal_key, [point]);
      }
      if (appends.size === 0) return;
      setSeries((prev) => {
        let changed = false;
        const out = prev.map((s) => {
          const seriesKey = keyById.get(s.id);
          if (!seriesKey) return s;
          const incoming = appends.get(seriesKey);
          if (!incoming || incoming.length === 0) return s;
          // Ayni timestamp tekrari gelirse ekleme (dedup son nokta).
          let last = s.points.length > 0 ? s.points[s.points.length - 1] : undefined;
          const fresh: Point[] = [];
          for (const p of incoming) {
            if (last && last[0] === p[0]) continue;
            fresh.push(p);
            last = p;
          }
          if (fresh.length === 0) return s;
          changed = true;
          return { ...s, points: [...s.points, ...fresh] };
        });
        return changed ? out : prev;
      });
    });
  }, [live, chartType, deviceCode, seriesKeys, registerHandler]);

  // Heatmap canli: WS append anlamsiz (kova-bazli) -> 30sn'de bir refetch.
  useEffect(() => {
    if (!live || chartType !== "heatmap") return;
    const id = window.setInterval(() => setLiveTick((n) => n + 1), 30_000);
    return () => window.clearInterval(id);
  }, [live, chartType]);

  // Cift Y-ekseni: ilk 2 farkli birim.
  const units = useMemo(() => {
    const u: string[] = [];
    for (const s of series) {
      const un = s.unit ?? "";
      if (!u.includes(un)) u.push(un);
    }
    return u;
  }, [series]);

  const hasData = series.some((s) => s.points.length > 0);

  // ---- ECharts option (koyu tema) ----
  const chartOption = useMemo(() => {
    // ---- HEATMAP: 3 kaynak (satir) x zaman kova (sutun) ----
    if (chartType === "heatmap") {
      const rowLabels = ALL_SOURCES.map((s) => SOURCE_META[s].label);
      const isAlarm = heatmapMode === "alarm";
      // Baslik: hangi deger gosteriliyor. Alarm -> "Alarm yogunlugu";
      // Deger -> secili sinyalin etiketi (+ birim).
      const valSuffix = heatmapSignal || defs[0]?.suffix || "actual_current";
      const valCat = suffixCatalog.get(valSuffix);
      const heatTitle = isAlarm
        ? t("deviceDetail.charts.alarmDensity")
        : `${valCat?.label ?? valSuffix}${valCat?.unit ? ` (${valCat.unit})` : ""}`;
      return {
        backgroundColor: "transparent",
        animationDuration: 300,
        title: {
          text: heatTitle,
          left: "center",
          top: 6,
          textStyle: { color: "#0f172a", fontSize: 14, fontWeight: 700 },
        },
        tooltip: {
          position: "top",
          backgroundColor: "rgba(255,255,255,0.98)",
          borderColor: "#e2e8f0",
          borderWidth: 1,
          textStyle: { color: "#0f172a", fontSize: 12 },
          extraCssText: "box-shadow: 0 6px 20px rgba(15,23,42,0.12); border-radius: 10px;",
          formatter: (p: { data: [number, number, number | null]; marker: string }) => {
            const [ci, ri, v] = p.data;
            const label = rowLabels[ri];
            const time = heatData.cols[ci] ?? "";
            const val = v == null ? "—" : isAlarm ? String(Math.round(v)) : v.toFixed(1);
            // Birim: alarm -> "alarm", deger -> secili sinyalin birimi.
            const unit = isAlarm ? t("deviceDetail.charts.alarmCount") : (valCat?.unit ?? "");
            return (
              `<div style="min-width:120px">` +
              `<div style="font-size:11px;color:#94a3b8;margin-bottom:4px">${label} · ${time}</div>` +
              `<div style="display:flex;align-items:baseline;gap:5px">${p.marker}` +
              `<span style="font-size:17px;font-weight:800;color:#0f172a">${val}</span>` +
              (unit ? `<span style="font-size:12px;color:#64748b">${unit}</span>` : "") +
              `</div></div>`
            );
          },
        },
        grid: { left: 8, right: 8, top: 42, bottom: 60, containLabel: true },
        xAxis: {
          type: "category",
          data: heatData.cols,
          splitArea: { show: true },
          axisLabel: { color: "#64748b", hideOverlap: true },
          axisLine: { lineStyle: { color: "rgba(148,163,184,0.35)" } },
        },
        yAxis: {
          type: "category",
          data: rowLabels,
          splitArea: { show: true },
          axisLabel: { color: "#64748b" },
          axisLine: { lineStyle: { color: "rgba(148,163,184,0.35)" } },
        },
        visualMap: {
          min: 0,
          max: heatData.max,
          calculable: true,
          orient: "horizontal",
          left: "center",
          bottom: 8,
          textStyle: { color: "#64748b" },
          // alarm: sicak renkler (az->cok), deger: mavi->kirmizi
          inRange: {
            color: isAlarm
              ? ["#f1f5f9", "#fde68a", "#fb923c", "#ef4444", "#b91c1c"]
              : ["#dbeafe", "#93c5fd", "#facc15", "#fb923c", "#ef4444"],
          },
        },
        series: [
          {
            type: "heatmap" as const,
            data: heatData.cells,
            label: { show: false },
            itemStyle: { borderColor: "#fff", borderWidth: 1, borderRadius: 2 },
            emphasis: { itemStyle: { shadowBlur: 6, shadowColor: "rgba(0,0,0,0.2)" } },
          },
        ],
      };
    }

    // ---- LINE/AREA/BAR ----
    // Acik tema — okunur gri eksen + hafif grid.
    const mkAxis = (name: string, position: "left" | "right", showSplit: boolean) => ({
      type: "value" as const,
      name,
      position,
      splitLine: { lineStyle: { color: showSplit ? "rgba(148,163,184,0.22)" : "rgba(148,163,184,0)" } },
      axisLabel: { color: "#64748b" },
      nameTextStyle: { color: "#94a3b8" },
    });
    const yAxes = [mkAxis(units[0] ?? "", "left", true)];
    if (units[1] != null) {
      yAxes.push(mkAxis(units[1], "right", false));
    }
    return {
      backgroundColor: "transparent",
      animationDuration: 300,
      grid: { left: 8, right: units[1] != null ? 8 : 16, top: 16, bottom: 44, containLabel: true },
      // Alt slider: zaman penceresini kaydir/yakinlastir (line/area/bar).
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
          dataBackground: { lineStyle: { color: "#cbd5e1" }, areaStyle: { color: "rgba(148,163,184,0.15)" } },
        },
      ],
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(255,255,255,0.98)",
        borderColor: "#e2e8f0",
        borderWidth: 1,
        textStyle: { color: "#0f172a", fontSize: 12 },
        extraCssText: "box-shadow: 0 6px 20px rgba(15,23,42,0.12); border-radius: 10px; padding: 8px 10px;",
        axisPointer: { type: "cross", lineStyle: { color: "rgba(148,163,184,0.5)" } },
        // Her seri satiri: renk noktasi + etiket + deger + birim. Birim
        // seri sirasindan (series[].unit) alinir; ECharts param.seriesIndex.
        formatter: (params: { axisValueLabel?: string; axisValue?: number; seriesIndex: number; value: [number, number | null]; marker: string; seriesName: string }[]) => {
          if (!params.length) return "";
          const ts = params[0].axisValue;
          const head = ts != null
            ? new Date(ts).toLocaleString(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
            : (params[0].axisValueLabel ?? "");
          const rows = params.map((pt) => {
            const v = Array.isArray(pt.value) ? pt.value[1] : null;
            const unit = series[pt.seriesIndex]?.unit ?? "";
            const valStr = v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
            return (
              `<div style="display:flex;align-items:center;gap:6px;margin-top:3px">${pt.marker}` +
              `<span style="flex:1;color:#475569">${pt.seriesName}</span>` +
              `<span style="font-weight:800;color:#0f172a">${valStr}</span>` +
              (unit ? `<span style="color:#94a3b8;font-size:11px">${unit}</span>` : "") +
              `</div>`
            );
          }).join("");
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
        // bar: gruplu ya da stack + genislik/yuvarlaklik
        stack: chartType === "bar" && settings.barStack ? "total" : undefined,
        barWidth: chartType === "bar" && settings.barWidth > 0 ? settings.barWidth : undefined,
        barMaxWidth: chartType === "bar" && settings.barWidth === 0 ? 40 : undefined,
        lineStyle: chartType !== "bar" ? { color: s.color, width: settings.lineWidth } : undefined,
        itemStyle: {
          color: s.color,
          borderRadius: chartType === "bar" ? [settings.barRadius, settings.barRadius, 0, 0] : 0,
        },
        // alan dolgusu: sadece "area" tipinde
        areaStyle:
          chartType === "area"
            ? { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: s.color + "33" }, { offset: 1, color: s.color + "05" }]) }
            : undefined,
        connectNulls: true,
      })),
    };
  }, [series, units, chartType, settings, heatmapMode, heatData, t]);

  // Popup: sinyal ekle / duzenle (tek sinyal + tek cihaz).
  const [pSuffix, setPSuffix] = useState<string>("");
  const [pSource, setPSource] = useState<SignalSource>("master");
  const [pColor, setPColor] = useState<string>(PALETTE[0]);
  const openAdd = () => {
    setEditId(null);
    setPSuffix(suffixList[0]?.suffix ?? "");
    setPSource(activeSource);
    setPColor(PALETTE[defs.length % PALETTE.length]);
    setPopupOpen(true);
  };
  const openEdit = (d: SeriesDef) => {
    setEditId(d.id);
    setPSuffix(d.suffix);
    setPSource(d.source);
    setPColor(d.color);
    setPopupOpen(true);
  };
  const submitSeries = () => {
    if (!pSuffix) return;
    // Secili sinyal secili kaynakta yoksa (o cihazda yok) ilk mevcut kaynaga dus.
    const cat = suffixCatalog.get(pSuffix);
    const src = cat?.sources.has(pSource) ? pSource : (cat ? [...cat.sources][0] : pSource);
    setDefs((prev) => {
      if (editId) return prev.map((d) => (d.id === editId ? { ...d, suffix: pSuffix, source: src, color: pColor } : d));
      return [...prev, { id: newId(), suffix: pSuffix, source: src, color: pColor }];
    });
    setPopupOpen(false);
  };
  const removeSeries = (id: string) => setDefs((prev) => prev.filter((d) => d.id !== id));

  return (
    <div className="device-trend">
      {/* ---- Zaman araligi (ust) ---- */}
      <div className="device-trend-timebar">
        <div className="device-trend-ranges">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              className={`device-trend-range${!customOn && rangeKey === r.key ? " active" : ""}`}
              onClick={() => { setCustomOn(false); setRangeKey(r.key); }}
            >
              {t(`deviceDetail.charts.range.${r.key}`)}
            </button>
          ))}
          <button
            type="button"
            className={`device-trend-range${customOn ? " active" : ""}`}
            onClick={() => { setCustomOn(true); setRangeKey("custom"); }}
          >
            {t("deviceDetail.charts.custom")}
          </button>
        </div>
        {/* Grafik tipi + ayarlar */}
        <div className="device-trend-typebar">
          {/* Canli mod toggle — WS ile anlik guncelleme */}
          <button
            type="button"
            className={`device-trend-live${live ? " active" : ""}`}
            onClick={() => setLive((v) => !v)}
            title={t("deviceDetail.charts.liveHint")}
          >
            <span className={`device-trend-live-dot${live && connectionState === "open" ? " is-on" : ""}`} aria-hidden="true" />
            {t("deviceDetail.charts.live")}
          </button>
          <div className="device-trend-types">
            {([
              { key: "line", icon: "show_chart" },
              { key: "area", icon: "area_chart" },
              { key: "bar", icon: "bar_chart" },
              { key: "heatmap", icon: "grid_on" },
            ] as { key: ChartType; icon: string }[]).map((ct) => (
              <button
                key={ct.key}
                type="button"
                className={`device-trend-type${chartType === ct.key ? " active" : ""}`}
                onClick={() => setChartType(ct.key)}
                title={t(`deviceDetail.charts.type.${ct.key}`)}
              >
                <span className="material-symbols-outlined">{ct.icon}</span>
              </button>
            ))}
          </div>
          <div className="device-trend-gear-wrap">
            <button
              type="button"
              className={`device-trend-gear${gearOpen ? " active" : ""}`}
              onClick={() => setGearOpen((o) => !o)}
              title={t("deviceDetail.charts.settings")}
            >
              <span className="material-symbols-outlined">tune</span>
            </button>
            {gearOpen ? (
              <>
                <div className="device-trend-gear-backdrop" onClick={() => setGearOpen(false)} />
                <div className="device-trend-gear-panel">
                  <span className="device-trend-gear-title">{t("deviceDetail.charts.settings")}</span>
                  {chartType === "heatmap" ? (
                    <>
                      <div className="device-trend-heat-modes">
                        <button
                          type="button"
                          className={`device-trend-heat-mode${heatmapMode === "alarm" ? " active" : ""}`}
                          onClick={() => setHeatmapMode("alarm")}
                        >
                          {t("deviceDetail.charts.alarmDensity")}
                        </button>
                        <button
                          type="button"
                          className={`device-trend-heat-mode${heatmapMode === "value" ? " active" : ""}`}
                          onClick={() => setHeatmapMode("value")}
                        >
                          {t("deviceDetail.charts.valueDensity")}
                        </button>
                      </div>
                      {heatmapMode === "value" ? (
                        <label className="device-trend-gear-row device-trend-gear-slider">
                          <span>{t("deviceDetail.charts.signal")}</span>
                          <select
                            value={heatmapSignal || defs[0]?.suffix || ""}
                            onChange={(e) => setHeatmapSignal(e.target.value)}
                          >
                            {suffixList
                              .filter((s) => s.suffix !== "alarm_rate")
                              .map((s) => (
                                <option key={s.suffix} value={s.suffix}>
                                  {s.label}{s.unit ? ` (${s.unit})` : ""}
                                </option>
                              ))}
                          </select>
                        </label>
                      ) : null}
                      <span className="device-trend-gear-note">{t("deviceDetail.charts.heatmapNote")}</span>
                    </>
                  ) : chartType !== "bar" ? (
                    <>
                      <label className="device-trend-gear-row">
                        <input type="checkbox" checked={settings.smooth} onChange={(e) => setSettings((s) => ({ ...s, smooth: e.target.checked }))} />
                        {t("deviceDetail.charts.smooth")}
                      </label>
                      <label className="device-trend-gear-row">
                        <input type="checkbox" checked={settings.showSymbol} onChange={(e) => setSettings((s) => ({ ...s, showSymbol: e.target.checked }))} />
                        {t("deviceDetail.charts.showPoints")}
                      </label>
                      <label className="device-trend-gear-row device-trend-gear-slider">
                        <span>{t("deviceDetail.charts.lineWidth")}: {settings.lineWidth}px</span>
                        <input type="range" min={1} max={5} value={settings.lineWidth} onChange={(e) => setSettings((s) => ({ ...s, lineWidth: Number(e.target.value) }))} />
                      </label>
                    </>
                  ) : (
                    <>
                      <label className="device-trend-gear-row">
                        <input type="checkbox" checked={settings.barStack} onChange={(e) => setSettings((s) => ({ ...s, barStack: e.target.checked }))} />
                        {t("deviceDetail.charts.barStack")}
                      </label>
                      <label className="device-trend-gear-row device-trend-gear-slider">
                        <span>{t("deviceDetail.charts.barWidth")}: {settings.barWidth === 0 ? t("deviceDetail.charts.auto") : `${settings.barWidth}px`}</span>
                        <input type="range" min={0} max={60} step={2} value={settings.barWidth} onChange={(e) => setSettings((s) => ({ ...s, barWidth: Number(e.target.value) }))} />
                      </label>
                      <label className="device-trend-gear-row device-trend-gear-slider">
                        <span>{t("deviceDetail.charts.barRadius")}: {settings.barRadius}px</span>
                        <input type="range" min={0} max={12} value={settings.barRadius} onChange={(e) => setSettings((s) => ({ ...s, barRadius: Number(e.target.value) }))} />
                      </label>
                    </>
                  )}
                </div>
              </>
            ) : null}
          </div>
        </div>

        {customOn ? (
          <div className="device-trend-custom">
            <input type="datetime-local" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} aria-label={t("deviceDetail.charts.from")} />
            <span className="device-trend-custom-sep">→</span>
            <input type="datetime-local" value={customTo} onChange={(e) => setCustomTo(e.target.value)} aria-label={t("deviceDetail.charts.to")} />
          </div>
        ) : null}
      </div>

      {/* ---- Ana govde: sol panel | grafik ---- */}
      <div className={`device-trend-body${chartType === "heatmap" ? " is-heatmap" : ""}`}>
        {/* Sol sinyal paneli — heatmap 3 sabit kaynak gosterir, panel anlamsiz */}
        {chartType !== "heatmap" ? (
        <aside className="device-trend-side">
          <div className="device-trend-side-head">
            <span>{t("deviceDetail.charts.signals")}</span>
            <button type="button" className="device-trend-add" onClick={openAdd}>
              <span className="material-symbols-outlined">add</span>
            </button>
          </div>
          <ul className="device-trend-list">
            {defs.length === 0 ? (
              <li className="device-trend-list-empty">{t("deviceDetail.charts.noSignalsYet")}</li>
            ) : (
              defs.map((d) => {
                const cat = suffixCatalog.get(d.suffix);
                return (
                  <li key={d.id} className="device-trend-listitem" onClick={() => openEdit(d)} title={t("deviceDetail.charts.editHint")}>
                    <span className="device-trend-listcolor" style={{ background: d.color }} />
                    <div className="device-trend-listbody">
                      <span className="device-trend-listlabel">{cat?.label ?? d.suffix}</span>
                      <span className="device-trend-listsrc">
                        {SOURCE_META[d.source].label}
                        {cat?.unit ? ` · ${cat.unit}` : ""}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="device-trend-listdel"
                      onClick={(e) => { e.stopPropagation(); removeSeries(d.id); }}
                      aria-label="Sil"
                    >
                      <span className="material-symbols-outlined">close</span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>
        </aside>
        ) : null}

        {/* Grafik */}
        <div className="device-trend-chart">
          {chartType === "heatmap" ? (
            // Heatmap: sinyal secimi gerekmez (3 sabit kaynak). heatData.cells kontrolu.
            loading && heatData.cells.length === 0 ? (
              <div className="device-trend-empty"><span className="btn-spinner" aria-hidden="true" /></div>
            ) : error ? (
              <div className="device-trend-empty is-error"><span className="material-symbols-outlined">error</span><p>{error}</p></div>
            ) : heatData.cells.length === 0 ? (
              <div className="device-trend-empty"><span className="material-symbols-outlined">grid_on</span><p>{t("deviceDetail.charts.noData")}</p></div>
            ) : (
              <ReactECharts echarts={echarts} option={chartOption} style={{ height: "100%", width: "100%" }} notMerge lazyUpdate />
            )
          ) : seriesKeys.length === 0 ? (
            <div className="device-trend-empty">
              <span className="material-symbols-outlined">show_chart</span>
              <p>{t("deviceDetail.charts.selectSignal")}</p>
            </div>
          ) : loading && !hasData ? (
            <div className="device-trend-empty"><span className="btn-spinner" aria-hidden="true" /></div>
          ) : error ? (
            <div className="device-trend-empty is-error"><span className="material-symbols-outlined">error</span><p>{error}</p></div>
          ) : !hasData ? (
            <div className="device-trend-empty"><span className="material-symbols-outlined">timeline</span><p>{t("deviceDetail.charts.noData")}</p></div>
          ) : (
            <ReactECharts echarts={echarts} option={chartOption} style={{ height: "100%", width: "100%" }} notMerge lazyUpdate />
          )}
        </div>
      </div>

      {/* ---- Sinyal ekle popup ---- */}
      {popupOpen ? (
        <div className="device-trend-modal-overlay" onClick={() => setPopupOpen(false)}>
          <div className="device-trend-modal" onClick={(e) => e.stopPropagation()}>
            <div className="device-trend-modal-head">
              <h4>{editId ? t("deviceDetail.charts.editSignal") : t("deviceDetail.charts.addSignal")}</h4>
              <button type="button" onClick={() => setPopupOpen(false)}><span className="material-symbols-outlined">close</span></button>
            </div>
            <div className="device-trend-modal-body">
              <label className="device-trend-field">
                <span>{t("deviceDetail.charts.signal")}</span>
                <select value={pSuffix} onChange={(e) => setPSuffix(e.target.value)}>
                  {suffixList.map((s) => (
                    <option key={s.suffix} value={s.suffix}>{s.label}{s.unit ? ` (${s.unit})` : ""}</option>
                  ))}
                </select>
              </label>
              <div className="device-trend-field">
                <span>{t("deviceDetail.charts.device")}</span>
                <div className="device-trend-modal-sources">
                  {ALL_SOURCES.map((src) => {
                    const avail = suffixCatalog.get(pSuffix)?.sources.has(src) ?? false;
                    return (
                      <label key={src} className={`device-trend-radio${pSource === src ? " active" : ""}${avail ? "" : " is-disabled"}`}>
                        <input
                          type="radio"
                          name="trend-device"
                          checked={pSource === src}
                          disabled={!avail}
                          onChange={() => setPSource(src)}
                        />
                        {SOURCE_META[src].label}
                      </label>
                    );
                  })}
                </div>
              </div>
              <div className="device-trend-field">
                <span>{t("deviceDetail.charts.color")}</span>
                <div className="device-trend-colors">
                  {PALETTE.map((c) => (
                    <button
                      key={c}
                      type="button"
                      className={`device-trend-colorbtn${pColor === c ? " active" : ""}`}
                      style={{ background: c }}
                      onClick={() => setPColor(c)}
                      aria-label={c}
                    />
                  ))}
                </div>
              </div>
            </div>
            <div className="device-trend-modal-foot">
              <button type="button" className="device-trend-btn-secondary" onClick={() => setPopupOpen(false)}>{t("common.cancel")}</button>
              <button type="button" className="device-trend-btn-primary" onClick={submitSeries} disabled={!pSuffix}>
                {editId ? t("common.save") : t("deviceDetail.charts.add")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
