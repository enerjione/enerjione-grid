import { useEffect, useMemo, useRef, useState } from "react";

import { TablePagination } from "../../components/TablePagination";
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

const TABS: { key: TabKey; label: string }[] = [
  { key: "all", label: "Genel" },
  { key: "master", label: "Master" },
  { key: "sat01", label: "Satellite 01" },
  { key: "sat02", label: "Satellite 02" }
];

const DATA_TYPE_LABEL: Record<SignalDataType, string> = {
  analog: "Analog",
  binary: "Binary",
  counter: "Counter",
  string: "String"
};

const SOURCE_LABEL: Record<string, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
};

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
  if (dataType === "binary") return value ? "AKTİF (1)" : "PASİF (0)";
  if (!Number.isFinite(value)) return String(value);
  const text = dataType === "counter" ? Math.round(value).toString() : NUMBER_FORMATTER.format(value);
  return unit ? `${text} ${unit}` : text;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("tr-TR");
  } catch {
    return ts;
  }
}

const AUTO_REFRESH_OPTIONS = [
  { value: 0, label: "Kapalı" },
  { value: 5, label: "5 sn" },
  { value: 10, label: "10 sn" },
  { value: 30, label: "30 sn" },
  { value: 60, label: "1 dk" }
];

const AUTO_REFRESH_KEY = "hsl.device-summary.auto-refresh-sec";

export function DeviceSummaryPage({
  selectedDevice,
  values,
  signals,
  gateways,
  loading,
  error,
  onRefresh
}: Props) {
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [autoRefreshSec, setAutoRefreshSec] = useState<number>(() => {
    if (typeof window === "undefined") return 10;
    const raw = window.localStorage.getItem(AUTO_REFRESH_KEY);
    const parsed = raw ? Number.parseInt(raw, 10) : 10;
    return Number.isFinite(parsed) ? parsed : 10;
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
  useEffect(() => {
    if (autoRefreshSec <= 0) return;
    const id = window.setInterval(() => {
      if (loadingRef.current) return;
      void onRefreshRef.current();
    }, autoRefreshSec * 1000);
    return () => window.clearInterval(id);
  }, [autoRefreshSec]);

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
      if (activeTab !== "all" && row.source !== activeTab) return false;
      if (!q) return true;
      return (
        row.signal_label.toLowerCase().includes(q) ||
        row.signal_key.toLowerCase().includes(q)
      );
    });
  }, [deviceValues, activeTab, search]);

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
          <h3>Cihaz seçin</h3>
          <p className="helper-text">
            Soldaki listeden bir cihaz seçtiğinizde buraya o cihazın özet gösterimi gelir.
            Master ve uydu sinyallerini sekmeler arasında geçerek inceleyebilirsiniz.
          </p>
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
          <span
            className={`device-summary-status ${gwOnline && selectedDevice.communicationStatus === "online" ? "online" : "offline"}`}
          >
            {gwOnline && selectedDevice.communicationStatus === "online" ? "Çevrimiçi" : "Çevrimdışı"}
          </span>
        </div>
        <div className="device-summary-kpis">
          <div className="device-summary-kpi">
            <span className="device-summary-kpi-label">Toplam Sinyal</span>
            <span className="device-summary-kpi-value">{counts.all}</span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--good">
            <span className="device-summary-kpi-label">İyi</span>
            <span className="device-summary-kpi-value">{kpis.goodCount}</span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--bad">
            <span className="device-summary-kpi-label">Kötü</span>
            <span className="device-summary-kpi-value">{kpis.badCount}</span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--pending">
            <span className="device-summary-kpi-label">Bekleyen</span>
            <span className="device-summary-kpi-value">{kpis.pendingCount}</span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--battery">
            <span className="device-summary-kpi-label">Batarya</span>
            <span className="device-summary-kpi-value">
              {battPct !== null ? `%${Math.round(battPct)}` : "—"}
            </span>
          </div>
          <div className="device-summary-kpi device-summary-kpi--alarm">
            <span className="device-summary-kpi-label">Alarm</span>
            <span className="device-summary-kpi-value">
              {selectedDevice.alarmActive ? "Aktif" : "Yok"}
            </span>
          </div>
        </div>
      </header>

      {/* Sekmeler + arama + yenile */}
      <div className="device-summary-toolbar">
        <div className="device-summary-tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              className={`device-summary-tab ${activeTab === t.key ? "active" : ""}`}
              onClick={() => setActiveTab(t.key)}
            >
              <span>{t.label}</span>
              <span className="device-summary-tab-count">{counts[t.key]}</span>
            </button>
          ))}
        </div>
        <input
          type="search"
          className="device-summary-search"
          placeholder="Sinyal ara..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <label className="auto-refresh-control">
          <span className="auto-refresh-label">Otomatik yenile</span>
          <select
            className="auto-refresh-select"
            value={autoRefreshSec}
            onChange={(event) => setAutoRefreshSec(Number.parseInt(event.target.value, 10) || 0)}
          >
            {AUTO_REFRESH_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
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
              {activeTab === "all" ? <th className="cell-center">Kaynak</th> : null}
              <th>Sinyal</th>
              <th className="cell-center">Tip</th>
              <th>Değer</th>
              <th className="cell-center">Kalite</th>
              <th>Zaman</th>
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((row) => {
              const sig = signalByKey.get(row.signal_key);
              const dataType = sig?.data_type;
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
                      <span className={`badge badge-${dataType}`}>{DATA_TYPE_LABEL[dataType]}</span>
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
                    ) : (
                      formatValue(row.value, dataType, row.unit, row.value_string)
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
                    ? "Bu cihaz için henüz veri akışı başlamadı."
                    : "Aramaya uygun sinyal yok."}
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
