import { useEffect, useMemo, useRef, useState } from "react";
import type { SignalCatalogRow, SignalDataType, SignalLiveRow } from "../../shared/types";

type Props = {
  values: SignalLiveRow[];
  signals: SignalCatalogRow[];
  loading: boolean;
  error?: string;
  onRefresh: () => Promise<void>;
};

type TabKey = "all" | SignalDataType;

const DATA_TYPES: SignalDataType[] = [
  "analog",
  "binary",
  "counter",
  "string"
];

const DATA_TYPE_LABEL: Record<SignalDataType, string> = {
  analog: "Analog Input",
  binary: "Binary Input",
  counter: "Counter",
  string: "String"
};

const SOURCE_LABEL: Record<string, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
};

const AUTO_REFRESH_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: "Kapalı" },
  { value: 5, label: "5 sn" },
  { value: 10, label: "10 sn" },
  { value: 30, label: "30 sn" },
  { value: 60, label: "1 dk" },
  { value: 300, label: "5 dk" }
];

const AUTO_REFRESH_STORAGE_KEY = "hsl.live-values.auto-refresh-sec";
const AUTO_REFRESH_DEFAULT_SEC = 10;

function readStoredAutoRefresh(): number {
  if (typeof window === "undefined") return AUTO_REFRESH_DEFAULT_SEC;
  const raw = window.localStorage.getItem(AUTO_REFRESH_STORAGE_KEY);
  if (raw === null) return AUTO_REFRESH_DEFAULT_SEC;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return AUTO_REFRESH_DEFAULT_SEC;
  return AUTO_REFRESH_OPTIONS.some((opt) => opt.value === parsed) ? parsed : AUTO_REFRESH_DEFAULT_SEC;
}

function formatBinaryValue(value: number): string {
  return value ? "AKTİF (1)" : "PASİF (0)";
}

function formatValue(value: number | null, dataType: SignalDataType | undefined, unit?: string | null) {
  if (value === null || value === undefined) {
    return "—";
  }
  if (dataType === "binary") {
    return formatBinaryValue(value);
  }
  const text = Number.isFinite(value)
    ? dataType === "counter"
      ? Math.round(value).toString()
      : value.toFixed(3)
    : String(value);
  return unit ? `${text} ${unit}` : text;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) {
    return "—";
  }
  try {
    return new Date(ts).toLocaleString("tr-TR");
  } catch {
    return ts;
  }
}

export function LiveValuesPage({ values, signals, loading, error, onRefresh }: Props) {
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [search, setSearch] = useState("");
  const [autoRefreshSec, setAutoRefreshSec] = useState<number>(() => readStoredAutoRefresh());

  const onRefreshRef = useRef(onRefresh);
  const loadingRef = useRef(loading);
  useEffect(() => {
    onRefreshRef.current = onRefresh;
  }, [onRefresh]);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);

  useEffect(() => {
    window.localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, String(autoRefreshSec));
  }, [autoRefreshSec]);

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
    for (const s of signals) {
      map.set(s.key, s);
    }
    return map;
  }, [signals]);

  const countsByType = useMemo(() => {
    const map = new Map<SignalDataType, number>();
    DATA_TYPES.forEach((t) => map.set(t, 0));
    for (const row of values) {
      const type = signalByKey.get(row.signal_key)?.data_type;
      if (type) map.set(type, (map.get(type) ?? 0) + 1);
    }
    return map;
  }, [values, signalByKey]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return values.filter((row) => {
      const sig = signalByKey.get(row.signal_key);
      if (activeTab !== "all") {
        if (!sig || sig.data_type !== activeTab) return false;
      }
      if (!q) return true;
      return (
        row.signal_label.toLowerCase().includes(q) ||
        row.signal_key.toLowerCase().includes(q) ||
        row.device_code.toLowerCase().includes(q) ||
        row.device_name.toLowerCase().includes(q)
      );
    });
  }, [values, signalByKey, activeTab, search]);

  const totalCount = values.length;

  return (
    <section className="tab-panel live-values-page">
      <div className="signals-type-tabs">
        <button
          className={`signals-type-tab ${activeTab === "all" ? "active" : ""}`}
          onClick={() => setActiveTab("all")}
        >
          <span className="stt-label">Tümü</span>
          <span className="stt-count">{totalCount}</span>
        </button>
        {DATA_TYPES.map((type) => (
          <button
            key={type}
            className={`signals-type-tab stt-${type} ${activeTab === type ? "active" : ""}`}
            onClick={() => setActiveTab(type)}
          >
            <span className="stt-label">{DATA_TYPE_LABEL[type]}</span>
            <span className="stt-count">{countsByType.get(type) ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="signals-toolbar">
        <input
          className="signals-search"
          type="search"
          placeholder="Ara (cihaz, etiket, key)..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <span className="signals-count-pill">
          {filtered.length} / {totalCount}
        </span>
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

      <div className="live-values-table-wrap">
        <table className="values-table">
          <thead>
            <tr>
              <th>Cihaz</th>
              <th className="cell-center">Kaynak</th>
              <th>Sinyal</th>
              <th className="cell-center">Tip</th>
              <th>Değer</th>
              <th className="cell-center">Kalite</th>
              <th>Zaman</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => {
              const sig = signalByKey.get(row.signal_key);
              const dataType = sig?.data_type;
              return (
                <tr key={`${row.device_id}-${row.signal_key}`}>
                  <td>
                    <div className="cell-strong">{row.device_name}</div>
                    <div className="cell-helper">{row.device_code}</div>
                  </td>
                  <td className="cell-center">
                    <span className={`badge badge-source badge-source-${row.source}`}>
                      {SOURCE_LABEL[row.source] ?? row.source}
                    </span>
                  </td>
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
                    {formatValue(row.value, dataType, row.unit)}
                  </td>
                  <td className="cell-center">
                    {row.quality ? (
                      <span className={`quality quality-${row.quality}`}>{row.quality}</span>
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
                <td colSpan={7} className="helper-text" style={{ textAlign: "center" }}>
                  {totalCount === 0
                    ? "Gösterilecek satır yok: en az bir cihaz ve aktif sinyal kataloğu gerekir. Cihaz eklediğinizde tüm sinyal satırları burada listelenir; değerler telemetri geldikçe dolacaktır."
                    : "Filtreye uygun satır bulunamadı."}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}
