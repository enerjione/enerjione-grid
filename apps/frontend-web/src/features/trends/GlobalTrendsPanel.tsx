/**
 * GlobalTrendsPanel — TUM cihazlar uzerinde trend karsilastirmasi.
 *
 * NEDEN VAR
 * ---------
 * Cihaz Detayi > Trendler yalnizca TEK cihazin sinyallerini karsilastirabiliyor.
 * "Su fiderdeki akim ile oteki fiderdeki akimi yan yana gor" sorusu — saha
 * ekibinin en sik sordugu sey — o ekranda cevaplanamiyordu; iki sekme acip
 * goz karari kiyaslamak gerekiyordu.
 *
 * Burada bir seri = CIHAZ x SINYAL x KAYNAK. Cihaz artik sabit degil, serinin
 * parcasi. Cizim/tema/tooltip/dataZoom ortak cekirdekten (`trendChartCore`)
 * geliyor; iki ekran ayni gorunumu paylasiyor.
 *
 * YETKI: ayri bir rol kapisi YOK — backend `/devices/{code}/history` zaten tum
 * rollere acik ve `_ensure_device_visible` ile operatorun kapsamini koruyor.
 * Cihaz listesi de kapsam suzgecinden geciyor, yani operator yalnizca kendi
 * hatlarindaki cihazlari secebiliyor.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import ReactECharts from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import { fetchDeviceHistory, fetchDevices, fetchSignals } from "../../shared/api";
import { signalLabel } from "../../shared/signalLabel";
import { SOURCES, sourceLabel } from "../signals/signalCatalogConstants";
import type {
  DeviceRow,
  HistoryBucket,
  SignalCatalogRow,
  SignalSource,
} from "../../shared/types";
import {
  DEFAULT_SETTINGS,
  NON_TREND_RE,
  PALETTE,
  RANGES,
  bucketForSpanHours,
  buildTrendOption,
  suffixOf,
  toPoints,
  type ChartSeries,
  type ChartSettings,
  type ChartType,
  type Point,
} from "./trendChartCore";

echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

/** Bir seri: hangi cihazin, hangi sinyali, hangi kaynaktan. */
type SeriesDef = {
  id: string;
  deviceCode: string;
  suffix: string;
  source: SignalSource;
  color: string;
};

type SavedView = {
  defs: SeriesDef[];
  rangeKey: string;
  customFrom?: string;
  customTo?: string;
  chartType?: ChartType;
  settings?: ChartSettings;
};

const STORE_KEY = "hsl.global-trends";

let _idCounter = 0;
function newId(): string {
  _idCounter += 1;
  return `g${_idCounter}_${Math.floor(performance.now())}`;
}

function loadView(): SavedView | null {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    return raw ? (JSON.parse(raw) as SavedView) : null;
  } catch {
    return null;
  }
}
function saveView(v: SavedView): void {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(v));
  } catch {
    /* kota dolu olabilir — gorunum kaydi kritik degil */
  }
}

type Props = { accessToken: string };

export function GlobalTrendsPanel({ accessToken }: Props) {
  const { t } = useTranslation();

  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [signals, setSignals] = useState<SignalCatalogRow[]>([]);
  const [metaError, setMetaError] = useState<string | null>(null);

  // ---- Cihaz + sinyal katalogu ----
  useEffect(() => {
    let iptal = false;
    Promise.all([fetchDevices(accessToken), fetchSignals(accessToken)])
      .then(([d, s]) => {
        if (iptal) return;
        setDevices(d);
        setSignals(s);
      })
      .catch(() => {
        if (!iptal) setMetaError(t("common.errorOccurred"));
      });
    return () => {
      iptal = true;
    };
  }, [accessToken, t]);

  /** model -> (suffix -> {label, unit, sources}) — cihazin modeline gore secenek. */
  const modelCatalog = useMemo(() => {
    const m = new Map<
      string,
      Map<string, { label: string; unit: string | null; sources: Set<SignalSource> }>
    >();
    for (const s of signals) {
      if (s.data_type !== "analog" && s.data_type !== "counter" && s.data_type !== "analog_output")
        continue;
      if (!s.is_active) continue;
      const suf = suffixOf(s.key);
      if (NON_TREND_RE.test(suf)) continue;
      const perModel = m.get(s.model) ?? new Map();
      const e =
        perModel.get(suf) ?? {
          label: signalLabel(s.key, s.label),
          unit: s.unit ?? null,
          sources: new Set<SignalSource>(),
        };
      e.sources.add(s.source);
      perModel.set(suf, e);
      m.set(s.model, perModel);
    }
    return m;
  }, [signals]);

  const deviceByCode = useMemo(() => {
    const m = new Map<string, DeviceRow>();
    for (const d of devices) m.set(d.code, d);
    return m;
  }, [devices]);

  const suffixesFor = useCallback(
    (deviceCode: string) => {
      const dev = deviceByCode.get(deviceCode);
      const per = dev ? modelCatalog.get(dev.model) : undefined;
      if (!per) return [];
      return [...per.entries()]
        .map(([suffix, v]) => ({ suffix, ...v }))
        .sort((a, b) => a.label.localeCompare(b.label));
    },
    [deviceByCode, modelCatalog]
  );

  // ---- Gorunum durumu ----
  const saved = useMemo(() => loadView(), []);
  const [defs, setDefs] = useState<SeriesDef[]>(() => saved?.defs ?? []);
  const [rangeKey, setRangeKey] = useState<string>(() => saved?.rangeKey ?? "24h");
  const [customOn, setCustomOn] = useState<boolean>(() => (saved?.rangeKey ?? "24h") === "custom");
  const [customFrom, setCustomFrom] = useState<string>(() => saved?.customFrom ?? "");
  const [customTo, setCustomTo] = useState<string>(() => saved?.customTo ?? "");
  const [chartType, setChartType] = useState<ChartType>(() => saved?.chartType ?? "line");
  const [settings, setSettings] = useState<ChartSettings>(() => ({
    ...DEFAULT_SETTINGS,
    ...(saved?.settings ?? {}),
  }));
  const [gearOpen, setGearOpen] = useState(false);
  const [popupOpen, setPopupOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [series, setSeries] = useState<ChartSeries[]>([]);

  useEffect(() => {
    saveView({
      defs,
      rangeKey: customOn ? "custom" : rangeKey,
      customFrom,
      customTo,
      chartType,
      settings,
    });
  }, [defs, rangeKey, customOn, customFrom, customTo, chartType, settings]);

  /** Cizilebilir seriler: katalogda karsiligi olan tanimlar. */
  const seriesKeys = useMemo(() => {
    const out: {
      id: string;
      deviceCode: string;
      deviceName: string;
      seriesKey: string;
      label: string;
      unit: string | null;
      color: string;
    }[] = [];
    for (const d of defs) {
      const dev = deviceByCode.get(d.deviceCode);
      if (!dev) continue; // cihaz silinmis / kapsam disi
      const per = modelCatalog.get(dev.model);
      const cat = per?.get(d.suffix);
      if (!cat || !cat.sources.has(d.source)) continue;
      out.push({
        id: d.id,
        deviceCode: d.deviceCode,
        deviceName: dev.name || dev.code,
        seriesKey: `${d.source}.${d.suffix}`,
        label: `${dev.name || dev.code} · ${cat.label} · ${sourceLabel(d.source)}`,
        unit: cat.unit,
        color: d.color,
      });
    }
    return out;
  }, [defs, deviceByCode, modelCatalog]);

  // ---- Veri cek: her seri KENDI cihazindan ----
  const [reloadTick, setReloadTick] = useState(0);
  useEffect(() => {
    if (!accessToken || seriesKeys.length === 0) {
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
      const spanH =
        (new Date(customTo).getTime() - new Date(customFrom).getTime()) / 3600_000;
      bucket = bucketForSpanHours(spanH);
    } else {
      const range = RANGES.find((r) => r.key === rangeKey) ?? RANGES[4];
      sinceISO = new Date(Date.now() - range.minutes * 60_000).toISOString();
      bucket = range.bucket;
    }

    Promise.all(
      seriesKeys.map((sk) =>
        fetchDeviceHistory(accessToken, sk.deviceCode, sk.seriesKey, {
          bucket,
          since: sinceISO,
          until: untilISO,
          limit: 10000,
        })
          .then((rows) => ({ ...sk, points: toPoints(rows) }))
          // Tek cihazin hatasi TUM grafigi dusurmemeli: o seri bos kalir,
          // digerleri cizilir. (Cihaz kapsam disi/silinmis olabilir.)
          .catch(() => ({ ...sk, points: [] as Point[] }))
      )
    )
      .then((results) => {
        if (cancelled) return;
        setSeries(
          results.map((r) => ({
            id: r.id,
            label: r.label,
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
  }, [accessToken, seriesKeys, rangeKey, customOn, customFrom, customTo, reloadTick, t]);

  const chartOption = useMemo(
    () => buildTrendOption({ series, chartType, settings }),
    [series, chartType, settings]
  );

  const hasData = series.some((s) => s.points.length > 0);

  // ---- Seri ekle/duzenle popup ----
  const [pDevice, setPDevice] = useState<string>("");
  const [pSuffix, setPSuffix] = useState<string>("");
  const [pSource, setPSource] = useState<SignalSource>("master");
  const [pColor, setPColor] = useState<string>(PALETTE[0]);
  const [pFilter, setPFilter] = useState("");

  const popupSuffixes = useMemo(() => suffixesFor(pDevice), [pDevice, suffixesFor]);
  const popupSources = useMemo(() => {
    const cat = popupSuffixes.find((s) => s.suffix === pSuffix);
    return cat ? SOURCES.filter((s) => cat.sources.has(s)) : SOURCES;
  }, [popupSuffixes, pSuffix]);

  // Cihaz degisince sinyal/kaynak secimi gecersizlesebilir — ilk gecerliye cek.
  useEffect(() => {
    if (!popupOpen) return;
    if (popupSuffixes.length === 0) return;
    if (!popupSuffixes.some((s) => s.suffix === pSuffix)) {
      setPSuffix(popupSuffixes[0].suffix);
    }
  }, [popupOpen, popupSuffixes, pSuffix]);
  useEffect(() => {
    if (!popupOpen) return;
    if (popupSources.length > 0 && !popupSources.includes(pSource)) {
      setPSource(popupSources[0]);
    }
  }, [popupOpen, popupSources, pSource]);

  const filteredDevices = useMemo(() => {
    const q = pFilter.trim().toLocaleLowerCase();
    const list = [...devices].sort((a, b) =>
      (a.name || a.code).localeCompare(b.name || b.code)
    );
    if (!q) return list;
    return list.filter(
      (d) =>
        (d.name || "").toLocaleLowerCase().includes(q) ||
        d.code.toLocaleLowerCase().includes(q)
    );
  }, [devices, pFilter]);

  const openAdd = () => {
    setEditId(null);
    setPFilter("");
    const ilk = filteredDevices[0]?.code ?? devices[0]?.code ?? "";
    setPDevice(ilk);
    const sufs = suffixesFor(ilk);
    setPSuffix(sufs[0]?.suffix ?? "");
    setPSource("master");
    setPColor(PALETTE[defs.length % PALETTE.length]);
    setPopupOpen(true);
  };
  const openEdit = (d: SeriesDef) => {
    setEditId(d.id);
    setPFilter("");
    setPDevice(d.deviceCode);
    setPSuffix(d.suffix);
    setPSource(d.source);
    setPColor(d.color);
    setPopupOpen(true);
  };
  const applyPopup = () => {
    if (!pDevice || !pSuffix) return;
    if (editId) {
      setDefs((prev) =>
        prev.map((x) =>
          x.id === editId
            ? { ...x, deviceCode: pDevice, suffix: pSuffix, source: pSource, color: pColor }
            : x
        )
      );
    } else {
      setDefs((prev) => [
        ...prev,
        { id: newId(), deviceCode: pDevice, suffix: pSuffix, source: pSource, color: pColor },
      ]);
    }
    setPopupOpen(false);
  };

  const gearRef = useRef<HTMLDivElement | null>(null);

  return (
    <div className="device-trend global-trend">
      {/* ---- Zaman araligi + grafik tipi (UST SERIT, tam genislik) ----
           Cihaz Detayi > Trendler ile AYNI iskelet: serit ustte, altta
           "sol panel | grafik" izgarasi. Onceden serit sag kolonun ICINE
           konulmustu ve iki kolonu kuran `.device-trend-body` sarmalayicisi
           hic yoktu; `.device-trend` bir SUTUN flex'i oldugu icin seri
           paneli grafigin USTUNDE tam genislikte duruyor, sayfanin yarisi
           bos kaliyordu. */}
      <div className="device-trend-timebar">
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
              {t(`deviceDetail.charts.range.${r.key}`, { defaultValue: r.key })}
            </button>
          ))}
          <button
            type="button"
            className={`device-trend-range${customOn ? " active" : ""}`}
            onClick={() => setCustomOn(true)}
          >
            {t("deviceDetail.charts.custom")}
          </button>
        </div>

        {customOn ? (
          <div className="device-trend-custom">
            <input
              type="datetime-local"
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
            />
            <span className="device-trend-custom-sep">→</span>
            <input
              type="datetime-local"
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
            />
          </div>
        ) : null}

        <div className="device-trend-typebar">
          <div className="device-trend-types">
            {([
              { key: "line", icon: "show_chart" },
              { key: "area", icon: "area_chart" },
              { key: "bar", icon: "bar_chart" },
            ] as { key: ChartType; icon: string }[]).map((ct) => (
              <button
                key={ct.key}
                type="button"
                className={`device-trend-type${chartType === ct.key ? " active" : ""}`}
                onClick={() => setChartType(ct.key)}
                title={t(`deviceDetail.charts.type.${ct.key}`, { defaultValue: ct.key })}
              >
                <span className="material-symbols-outlined">{ct.icon}</span>
              </button>
            ))}
          </div>
          <div className="device-trend-gear-wrap" ref={gearRef}>
            <button
              type="button"
              className={`device-trend-gear${gearOpen ? " active" : ""}`}
              onClick={() => setGearOpen((v) => !v)}
              title={t("trends.chartSettings")}
              aria-label={t("trends.chartSettings")}
            >
              <span className="material-symbols-outlined">tune</span>
            </button>
              {gearOpen ? (
                <>
                  <div
                    className="device-trend-gear-backdrop"
                    onClick={() => setGearOpen(false)}
                  />
                  <div className="device-trend-gear-panel">
                    <p className="device-trend-gear-title">{t("trends.chartSettings")}</p>
                    {chartType !== "bar" ? (
                      <>
                        <label className="device-trend-gear-row">
                          <input
                            type="checkbox"
                            checked={settings.smooth}
                            onChange={(e) =>
                              setSettings((s) => ({ ...s, smooth: e.target.checked }))
                            }
                          />
                          {t("deviceDetail.charts.smooth")}
                        </label>
                        <label className="device-trend-gear-row">
                          <input
                            type="checkbox"
                            checked={settings.showSymbol}
                            onChange={(e) =>
                              setSettings((s) => ({ ...s, showSymbol: e.target.checked }))
                            }
                          />
                          {t("deviceDetail.charts.showSymbol")}
                        </label>
                        <label className="device-trend-gear-row device-trend-gear-slider">
                          {t("deviceDetail.charts.lineWidth")}
                          <input
                            type="range"
                            min={1}
                            max={5}
                            value={settings.lineWidth}
                            onChange={(e) =>
                              setSettings((s) => ({ ...s, lineWidth: Number(e.target.value) }))
                            }
                          />
                        </label>
                      </>
                    ) : (
                      <>
                        <label className="device-trend-gear-row">
                          <input
                            type="checkbox"
                            checked={settings.barStack}
                            onChange={(e) =>
                              setSettings((s) => ({ ...s, barStack: e.target.checked }))
                            }
                          />
                          {t("deviceDetail.charts.barStack")}
                        </label>
                        <label className="device-trend-gear-row device-trend-gear-slider">
                          {t("deviceDetail.charts.barRadius")}
                          <input
                            type="range"
                            min={0}
                            max={8}
                            value={settings.barRadius}
                            onChange={(e) =>
                              setSettings((s) => ({ ...s, barRadius: Number(e.target.value) }))
                            }
                          />
                        </label>
                      </>
                    )}
                    <p className="device-trend-gear-note">{t("trends.settingsSaved")}</p>
                  </div>
                </>
              ) : null}
            </div>
          <button
            type="button"
            className="device-trend-gear"
            onClick={() => setReloadTick((v) => v + 1)}
            disabled={loading}
            title={t("common.refresh")}
            aria-label={t("common.refresh")}
          >
            {loading ? (
              <span className="btn-spinner" aria-hidden="true" />
            ) : (
              <span className="material-symbols-outlined">refresh</span>
            )}
          </button>
        </div>
      </div>

      {/* ---- Ana govde: sol seri paneli | grafik ---- */}
      <div className="device-trend-body">
        <aside className="device-trend-side">
          <div className="device-trend-side-head">
            <span>{t("trends.seriesTitle")}</span>
            <button
              type="button"
              className="device-trend-add"
              onClick={openAdd}
              disabled={devices.length === 0}
              title={t("trends.addSeries")}
              aria-label={t("trends.addSeries")}
            >
              <span className="material-symbols-outlined">add</span>
            </button>
          </div>
          <ul className="device-trend-list">
            {defs.length === 0 ? (
              <li className="device-trend-list-empty">{t("trends.noSeries")}</li>
            ) : (
              defs.map((d) => {
                const dev = deviceByCode.get(d.deviceCode);
                const cat = dev ? modelCatalog.get(dev.model)?.get(d.suffix) : undefined;
                return (
                  <li
                    key={d.id}
                    className="device-trend-listitem"
                    onClick={() => openEdit(d)}
                    title={t("trends.editSeries")}
                  >
                    <span className="device-trend-listcolor" style={{ background: d.color }} />
                    <div className="device-trend-listbody">
                      {/* CIHAZ ADI USTTE: bu ekranda seriler FARKLI cihazlardan
                          geliyor, ayirt edici olan sinyal degil cihaz. */}
                      <span className="device-trend-listlabel">{dev?.name || d.deviceCode}</span>
                      <span className="device-trend-listsrc">
                        {cat?.label ?? d.suffix} · {sourceLabel(d.source)}
                        {cat?.unit ? ` · ${cat.unit}` : ""}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="device-trend-listdel"
                      onClick={(e) => {
                        e.stopPropagation();
                        setDefs((prev) => prev.filter((x) => x.id !== d.id));
                      }}
                      aria-label={t("common.delete")}
                    >
                      <span className="material-symbols-outlined">close</span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>
        </aside>

        <div className="device-trend-chart">
          {metaError ? (
            <div className="device-trend-empty is-error">
              <span className="material-symbols-outlined">error</span>
              <p>{metaError}</p>
            </div>
          ) : error ? (
            <div className="device-trend-empty is-error">
              <span className="material-symbols-outlined">error</span>
              <p>{error}</p>
            </div>
          ) : defs.length === 0 ? (
            // Bos durumda EYLEM de duruyor: grafik alani sayfanin en buyuk
            // parcasi ve tek satirlik bir aciklamayla bos birakmak, kullaniciyi
            // sol panelin kosesindeki kucuk "+" dugmesini aramaya birakiyordu.
            <div className="device-trend-empty">
              <span className="material-symbols-outlined">timeline</span>
              <p>{t("trends.emptyHint")}</p>
              <button
                type="button"
                className="device-trend-btn-primary"
                onClick={openAdd}
                disabled={devices.length === 0}
              >
                {t("trends.addSeries")}
              </button>
            </div>
          ) : !hasData && !loading ? (
            <div className="device-trend-empty">
              <span className="material-symbols-outlined">timeline</span>
              <p>{t("deviceDetail.charts.noData")}</p>
            </div>
          ) : (
            <ReactECharts
              echarts={echarts}
              option={chartOption}
              notMerge
              style={{ width: "100%", height: "100%" }}
            />
          )}
        </div>
      </div>

      {/* ---- Seri ekle/duzenle ---- */}
      {popupOpen ? (
        <div className="device-trend-modal-overlay" onClick={() => setPopupOpen(false)}>
          <div className="device-trend-modal" onClick={(e) => e.stopPropagation()}>
            <div className="device-trend-modal-head">
              {editId ? t("trends.editSeries") : t("trends.addSeries")}
            </div>
            <div className="device-trend-modal-body">
              <label className="device-trend-field">
                {t("trends.device")}
                <input
                  type="search"
                  placeholder={t("trends.searchDevice")}
                  value={pFilter}
                  onChange={(e) => setPFilter(e.target.value)}
                />
                <select
                  value={pDevice}
                  size={6}
                  onChange={(e) => setPDevice(e.target.value)}
                >
                  {filteredDevices.map((d) => (
                    <option key={d.code} value={d.code}>
                      {d.name || d.code}
                      {d.name && d.name !== d.code ? ` (${d.code})` : ""}
                    </option>
                  ))}
                </select>
              </label>

              <label className="device-trend-field">
                {t("trends.signal")}
                <select value={pSuffix} onChange={(e) => setPSuffix(e.target.value)}>
                  {popupSuffixes.map((s) => (
                    <option key={s.suffix} value={s.suffix}>
                      {s.label}
                      {s.unit ? ` (${s.unit})` : ""}
                    </option>
                  ))}
                </select>
              </label>

              <div className="device-trend-modal-sources">
                {popupSources.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={pSource === s ? "is-active" : ""}
                    onClick={() => setPSource(s)}
                  >
                    {sourceLabel(s)}
                  </button>
                ))}
              </div>

              <div className="device-trend-colors">
                {PALETTE.map((c) => (
                  <button
                    key={c}
                    type="button"
                    style={{ background: c }}
                    className={pColor === c ? "is-active" : ""}
                    onClick={() => setPColor(c)}
                    aria-label={c}
                  />
                ))}
              </div>
            </div>
            <div className="device-trend-modal-foot">
              <button
                type="button"
                className="device-trend-btn-secondary"
                onClick={() => setPopupOpen(false)}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="device-trend-btn-primary"
                onClick={applyPopup}
                disabled={!pDevice || !pSuffix}
              >
                {editId ? t("common.save") : t("common.add")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
