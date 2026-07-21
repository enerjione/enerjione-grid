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
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import { fetchDeviceHistory } from "../../shared/api";
import type {
  HistoryBucket,
  SignalCatalogRow,
  SignalSource,
  TelemetryAggregatePoint,
  TelemetryHistoryPoint,
} from "../../shared/types";

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent, CanvasRenderer]);

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
type SavedView = { defs: SeriesDef[]; rangeKey: string; customFrom?: string; customTo?: string };

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
    return m;
  }, [signals]);
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

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [series, setSeries] = useState<
    { id: string; label: string; points: Point[]; color: string; unit: string | null }[]
  >([]);

  useEffect(() => {
    saveView(deviceCode, { defs, rangeKey: customOn ? "custom" : rangeKey, customFrom, customTo });
  }, [deviceCode, defs, rangeKey, customOn, customFrom, customTo]);

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
      grid: { left: 8, right: units[1] != null ? 8 : 16, top: 16, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(255,255,255,0.98)",
        borderColor: "#e2e8f0",
        borderWidth: 1,
        textStyle: { color: "#0f172a", fontSize: 12 },
        extraCssText: "box-shadow: 0 6px 20px rgba(15,23,42,0.12); border-radius: 10px;",
        axisPointer: { type: "cross", lineStyle: { color: "rgba(148,163,184,0.5)" } },
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
        type: "line" as const,
        data: s.points,
        showSymbol: false,
        smooth: 0.25,
        yAxisIndex: units[1] != null && (s.unit ?? "") === units[1] ? 1 : 0,
        lineStyle: { color: s.color, width: 2 },
        itemStyle: { color: s.color },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: s.color + "33" }, { offset: 1, color: s.color + "05" }]) },
        connectNulls: true,
      })),
    };
  }, [series, units]);

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
        {customOn ? (
          <div className="device-trend-custom">
            <input type="datetime-local" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} aria-label={t("deviceDetail.charts.from")} />
            <span className="device-trend-custom-sep">→</span>
            <input type="datetime-local" value={customTo} onChange={(e) => setCustomTo(e.target.value)} aria-label={t("deviceDetail.charts.to")} />
          </div>
        ) : null}
      </div>

      {/* ---- Ana govde: sol panel | grafik ---- */}
      <div className="device-trend-body">
        {/* Sol sinyal paneli */}
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

        {/* Grafik */}
        <div className="device-trend-chart">
          {seriesKeys.length === 0 ? (
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
