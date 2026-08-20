import { useEffect, useMemo, useRef, useState } from "react";
import { sourceLabel as ortakKaynakEtiketi } from "../signals/signalCatalogConstants";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import { RuntimeStateChip } from "../../components/RuntimeStateChip";
import { TablePagination } from "../../components/TablePagination";
import { deviceRuntimeStateOf } from "../../shared/deviceRuntimeState";
import { usePolling } from "../../shared/usePolling";
import type {
  DeviceRow,
  Gateway,
  SignalCatalogRow,
  SignalDataType,
  SignalLiveRow
} from "../../shared/types";

type Props = {
  selectedDevice?: DeviceRow;
  values: SignalLiveRow[];
  signals: SignalCatalogRow[];
  gateways: Gateway[];
  loading: boolean;
  error?: string;
  onRefresh: () => Promise<void>;
};

type TabKey = "all" | "master" | "sat01" | "sat02";

// Master/Satellite urun terimi — cevrilmiyor; yalnizca "Genel" yerellesiyor.
const TABS: { key: TabKey; label: string | null }[] = [
  { key: "all", label: null },
  { key: "master", label: "Master" },
  { key: "sat01", label: "Satellite 01" },
  { key: "sat02", label: "Satellite 02" }
];

const DATA_TYPE_KEY: Record<SignalDataType, string> = {
  analog: "analog",
  binary: "binary",
  counter: "counter",
  string: "string",
  binary_output: "binaryOutput",
  analog_output: "analogOutput"
};

// Kaynak etiketi TEK KAYNAKTAN. Elle yazilan sozluk `sat04`+ icin ham
// deger donduruyordu ve `Record<string, ...>` oldugu icin TypeScript bunu
// YAKALAMIYORDU.
const SOURCE_LABEL = new Proxy({} as Record<string, string>, {
  get: (_t, k: string) => ortakKaynakEtiketi(k)
});

const GATEWAY_LIVE_SEC = 60;

function isGatewayOnline(gw: Gateway | undefined): boolean {
  if (!gw || !gw.is_active) return false;
  if (!gw.last_seen_at) return false;
  const sec = (Date.now() - new Date(gw.last_seen_at).getTime()) / 1000;
  return sec < GATEWAY_LIVE_SEC;
}

const NUMBER_FORMATTER = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 6,
  useGrouping: false
});

function formatValue(
  t: TFunction,
  value: number | null,
  dataType: SignalDataType | undefined,
  unit?: string | null,
  valueString?: string | null
): string {
  // String tipli sinyaller (DNP3 Group 110 / Octet String) — gateway numeric
  // value=null yollar; gercek metin value_string'tedir.
  if (dataType === "string") {
    const txt = (valueString ?? "").trim();
    return txt.length > 0 ? txt : "—";
  }
  if (value === null || value === undefined) return "—";
  if (dataType === "binary")
    return value ? t("deviceSummary.binaryOn") : t("deviceSummary.binaryOff");
  if (!Number.isFinite(value)) return String(value);
  const text = dataType === "counter" ? Math.round(value).toString() : NUMBER_FORMATTER.format(value);
  return unit ? `${text} ${unit}` : text;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString(undefined);
  } catch {
    return ts;
  }
}

const AUTO_REFRESH_VALUES = [0, 1, 2, 5, 10, 30, 60];

/** 0 -> "Kapalı", 60 -> "1 dk", digerleri -> "{n} sn". */
function autoRefreshLabel(t: TFunction, value: number): string {
  if (value === 0) return t("deviceSummary.autoRefresh.off");
  if (value >= 60) return t("deviceSummary.autoRefresh.min", { count: Math.round(value / 60) });
  return t("deviceSummary.autoRefresh.sec", { count: value });
}

const AUTO_REFRESH_KEY = "hsl.device-summary.auto-refresh-sec";
// Default 10sn -> 2sn: cihaz detay sayfasinda anlik takip.
const AUTO_REFRESH_DEFAULT_SEC = 2;

export function DeviceSummaryPage({
  selectedDevice,
  values,
  signals,
  gateways,
  loading,
  error,
  onRefresh
}: Props) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [autoRefreshSec, setAutoRefreshSec] = useState<number>(() => {
    if (typeof window === "undefined") return AUTO_REFRESH_DEFAULT_SEC;
    const raw = window.localStorage.getItem(AUTO_REFRESH_KEY);
    const parsed = raw ? Number.parseInt(raw, 10) : AUTO_REFRESH_DEFAULT_SEC;
    return Number.isFinite(parsed) ? parsed : AUTO_REFRESH_DEFAULT_SEC;
  });

  useEffect(() => {
    window.localStorage.setItem(AUTO_REFRESH_KEY, String(autoRefreshSec));
  }, [autoRefreshSec]);

  // Otomatik yenile
  const onRefreshRef = useRef(onRefresh);
  const loadingRef = useRef(loading);
  useEffect(() => {
    onRefreshRef.current = onRefresh;
  }, [onRefresh]);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);
  usePolling({
    enabled: autoRefreshSec > 0,
    intervalMs: autoRefreshSec * 1000,
    fn: () => {
      if (loadingRef.current) return;
      void onRefreshRef.current();
    },
    // Periyot secimi degistirilirken her adimda istek atmasin.
    immediate: false
  });

  const signalByKey = useMemo(() => {
    const map = new Map<string, SignalCatalogRow>();
    for (const s of signals) map.set(s.key, s);
    return map;
  }, [signals]);

  const gwOnline = useMemo(() => {
    if (!selectedDevice?.gatewayCode) return true;
    const gw = gateways.find((g) => g.code === selectedDevice.gatewayCode);
    return isGatewayOnline(gw);
  }, [gateways, selectedDevice]);

  // Bu cihaza ait satırlar — id eşleşmesi
  const deviceValues = useMemo(() => {
    if (!selectedDevice) return [];
    return values.filter((row) => row.device_id === selectedDevice.id);
  }, [values, selectedDevice]);

  const counts = useMemo(() => {
    const c = { all: 0, master: 0, sat01: 0, sat02: 0 };
    for (const row of deviceValues) {
      c.all += 1;
      if (row.source === "master") c.master += 1;
      else if (row.source === "sat01") c.sat01 += 1;
      else if (row.source === "sat02") c.sat02 += 1;
    }
    return c;
  }, [deviceValues]);

  // KPI hesabı (sadece "Genel" sekmesi için)
  const kpis = useMemo(() => {
    let goodCount = 0;
    let badCount = 0;
    let pendingCount = 0;
    for (const row of deviceValues) {
      const q = (gwOnline ? row.quality : "bad") || null;
      if (q === "good") goodCount += 1;
      else if (q) badCount += 1;
      else pendingCount += 1;
    }
    return { goodCount, badCount, pendingCount };
  }, [deviceValues, gwOnline]);

  // Filtrelenmiş satırlar
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return deviceValues.filter((row) => {
      // String tipli sinyaller hic gosterilmiyor (cihaz tarafinda 'Not Class 0')
      const sig = signalByKey.get(row.signal_key);
      const dt = (row.data_type as string | undefined) ?? sig?.data_type;
      if (dt === "string") return false;
      if (activeTab !== "all" && row.source !== activeTab) return false;
      if (!q) return true;
      return (
        row.signal_label.toLowerCase().includes(q) ||
        row.signal_key.toLowerCase().includes(q)
      );
    });
  }, [deviceValues, signalByKey, activeTab, search]);

  useEffect(() => {
    setPage(1);
  }, [activeTab, search, pageSize, selectedDevice?.id]);

  const pagedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

  // Cihaz seçilmemişse boş state
  if (!selectedDevice) {
    return (
      <section className="device-summary-empty">
        <div className="device-summary-empty-card">
          <span className="material-symbols-outlined device-summary-empty-icon">router</span>
          <h3>{t("deviceSummary.empty.title")}</h3>
          <p className="helper-text">{t("deviceSummary.empty.body")}</p>
        </div>
      </section>
    );
  }

  const battPct =
    typeof selectedDevice.batteryPercent === "number"
      ? Math.max(0, Math.min(100, selectedDevice.batteryPercent))
      : null;

  return (
    <section className="device-summary-page">
      {/* Üst KPI başlık şeridi */}
      <header className="device-summary-header">
        <div className="device-summary-title">
          <h2>{selectedDevice.name}</h2>
          <span className="device-summary-code">{selectedDevice.code}</span>
          {/* CALISMA-ZAMANI durumu (alti durum) — ikili online/offline DEGIL.
              `gwOnline` kapisi KALDIRILDI: gateway'in kendi tazeligi bu
              cihazin durumunu belirlemez, gateway'in BILDIRDIGI durum
              belirler. Gateway sussa bile ekran uydurma yapmaz; kayit
              bayatlayinca normalizer kendisi "Bayat"a duser. */}
          <RuntimeStateChip state={deviceRuntimeStateOf(selectedDevice)} withIcon={false} />
        </div>
        <div className="device-summary-kpis">
          <div className="device-summary-kpi">
            <span className="device-summary-kpi-label">{t("deviceSummary.kpi.totalSignals")}</span>
            <span className="device-summary-kpi-value">{counts.all}</span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--good">
            <span className="device-summary-kpi-label">{t("deviceSummary.kpi.good")}</span>
            <span className="device-summary-kpi-value">{kpis.goodCount}</span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--bad">
            <span className="device-summary-kpi-label">{t("deviceSummary.kpi.bad")}</span>
            <span className="device-summary-kpi-value">{kpis.badCount}</span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--pending">
            <span className="device-summary-kpi-label">{t("deviceSummary.kpi.pending")}</span>
            <span className="device-summary-kpi-value">{kpis.pendingCount}</span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--battery">
            <span className="device-summary-kpi-label">{t("deviceSummary.kpi.battery")}</span>
            <span className="device-summary-kpi-value">
              {battPct !== null ? `%${Math.round(battPct)}` : "—"}
            </span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--alarm">
            <span className="device-summary-kpi-label">{t("deviceSummary.kpi.alarm")}</span>
            <span className="device-summary-kpi-value">
              {selectedDevice.alarmActive
                ? t("deviceSummary.kpi.alarmActive")
                : t("deviceSummary.kpi.alarmNone")}
            </span>
          </div>
        </div>
      </header>

      {/* Sekmeler + arama + yenile */}
      <div className="device-summary-toolbar">
        <div className="device-summary-tabs">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`device-summary-tab ${activeTab === tab.key ? "active" : ""}`}
              onClick={() => setActiveTab(tab.key)}
            >
              <span>{tab.label ?? t("deviceSummary.tabs.all")}</span>
              <span className="device-summary-tab-count">{counts[tab.key]}</span>
            </button>
          ))}
        </div>
        <input
          type="search"
          className="device-summary-search"
          placeholder={t("deviceSummary.searchPlaceholder")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <label className="auto-refresh-control">
          <span className="auto-refresh-label">{t("deviceSummary.autoRefresh.label")}</span>
          <select
            className="auto-refresh-select"
            value={autoRefreshSec}
            onChange={(event) => setAutoRefreshSec(Number.parseInt(event.target.value, 10) || 0)}
          >
            {AUTO_REFRESH_VALUES.map((value) => (
              <option key={value} value={value}>
                {autoRefreshLabel(t, value)}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="primary-btn live-values-refresh"
          onClick={() => void onRefresh()}
          disabled={loading}
        >
          {loading ? "Yenileniyor..." : "Yenile"}
        </button>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {/* Sinyal tablosu */}
      <div className="device-summary-table-wrap">
        <table className="values-table device-summary-table">
          <thead>
            <tr>
              {activeTab === "all" ? <th scope="col" className="cell-center">{t("deviceSummary.table.source")}</th> : null}
              <th scope="col">{t("deviceSummary.table.signal")}</th>
              <th scope="col" className="cell-center">{t("deviceSummary.table.type")}</th>
              <th scope="col">{t("deviceSummary.table.value")}</th>
              <th scope="col" className="cell-center">{t("deviceSummary.table.quality")}</th>
              <th scope="col">{t("deviceSummary.table.time")}</th>
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((row) => {
              const sig = signalByKey.get(row.signal_key);
              // row.data_type (backend live endpoint'i) > catalog data_type
              const dataType = ((row.data_type as SignalDataType | undefined) ?? sig?.data_type) as SignalDataType | undefined;
              const effectiveQuality = gwOnline ? row.quality : "bad";
              return (
                <tr key={`${row.device_id}-${row.signal_key}`}>
                  {activeTab === "all" ? (
                    <td className="cell-center">
                      <span className={`badge badge-source badge-source-${row.source}`}>
                        {SOURCE_LABEL[row.source] ?? row.source}
                      </span>
                    </td>
                  ) : null}
                  <td>
                    <div className="cell-strong">{row.signal_label}</div>
                    <div className="cell-helper">{row.signal_key}</div>
                  </td>
                  <td className="cell-center">
                    {dataType ? (
                      <span className={`badge badge-${dataType}`}>
                        {t(`deviceSummary.dataType.${DATA_TYPE_KEY[dataType]}`)}
                      </span>
                    ) : (
                      <span className="helper-text">-</span>
                    )}
                  </td>
                  <td className={`cell-value ${row.value === null ? "cell-value-pending" : ""}`}>
                    {dataType === "binary" && row.value !== null && row.value !== undefined ? (
                      <span
                        className={`live-binary-pill ${row.value ? "live-binary-pill--true" : "live-binary-pill--false"}`}
                      >
                        {row.value ? "TRUE" : "FALSE"}
                      </span>
                    ) : dataType === "string" ? (
                      (() => {
                        const txt = (row.value_string ?? "").trim();
                        if (!txt) return <span className="cell-value-empty">—</span>;
                        return <span className="live-string-chip" title={txt}>{txt}</span>;
                      })()
                    ) : (
                      formatValue(t, row.value, dataType, row.unit, row.value_string)
                    )}
                  </td>
                  <td className="cell-center">
                    {effectiveQuality ? (
                      <span className={`quality quality-${effectiveQuality}`}>{effectiveQuality}</span>
                    ) : (
                      <span className="quality quality-pending">—</span>
                    )}
                  </td>
                  <td>{formatTimestamp(row.source_timestamp)}</td>
                </tr>
              );
            })}
            {filtered.length === 0 && !loading ? (
              <tr>
                <td colSpan={activeTab === "all" ? 6 : 5} className="device-summary-empty-row">
                  {deviceValues.length === 0
                    ? t("deviceSummary.noStream")
                    : t("deviceSummary.noMatch")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {filtered.length > 0 ? (
        <TablePagination
          totalItems={filtered.length}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
          itemLabel="sinyal"
        />
      ) : null}
    </section>
  );
}
