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
  values: SignalLiveRow[];
  signals: SignalCatalogRow[];
  devices: DeviceRow[];
  gateways: Gateway[];
  loading: boolean;
  error?: string;
  onRefresh: () => Promise<void>;
};

const GATEWAY_LIVE_SEC = 60;

function isGatewayOnline(gw: Gateway | undefined): boolean {
  if (!gw || !gw.is_active) return false;
  if (!gw.last_seen_at) return false;
  const sec = (Date.now() - new Date(gw.last_seen_at).getTime()) / 1000;
  return sec < GATEWAY_LIVE_SEC;
}

type TabKey = "all" | SignalDataType;

const DATA_TYPES: SignalDataType[] = [
  "analog",
  "binary",
  "counter"
];

const DATA_TYPE_LABEL: Record<SignalDataType, string> = {
  analog: "Analog Input",
  binary: "Binary Input",
  counter: "Counter",
  // 'string' tipi sistemde gosterilmiyor (cihaz konfigurasyonunda Class 0
  // disinda atanmis, okunamiyor). Tipte yine taniml ki SignalCatalog seed'i
  // bozulmasin, ama UI'de filtre/tab listesinden kaldirildi.
  string: "String"
};

const SOURCE_LABEL: Record<string, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
};

const AUTO_REFRESH_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: "Kapalı" },
  { value: 1, label: "1 sn" },
  { value: 2, label: "2 sn" },
  { value: 5, label: "5 sn" },
  { value: 10, label: "10 sn" },
  { value: 30, label: "30 sn" },
  { value: 60, label: "1 dk" }
];

const AUTO_REFRESH_STORAGE_KEY = "hsl.live-values.auto-refresh-sec";
// Default 10sn -> 2sn: kullanici cihaz degerini neredeyse anlik gorur.
// Backend her live-values cagrisinda 600 cihaz × 175 sinyal SELECT yapar;
// composite index (idx_telemetry_device_signal_ts) ile <50ms tamamlanir.
const AUTO_REFRESH_DEFAULT_SEC = 2;

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

// Sayisal degeri tr-TR locale ile (virgullu), max 6 ondalik basamak ve
// gereksiz trailing-zero olmadan formatlar. Ornek: 216.87 -> "216,87",
// 216.000 -> "216", 216.870000 -> "216,87" (anlamli digit korunur).
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
) {
  // String tipli sinyaller (DNP3 Group 110 / Octet String) — gateway numeric
  // value=null yollar; gercek metin value_string'tedir.
  if (dataType === "string") {
    const txt = (valueString ?? "").trim();
    return txt.length > 0 ? txt : "—";
  }
  if (value === null || value === undefined) {
    return "—";
  }
  if (dataType === "binary") {
    return formatBinaryValue(value);
  }
  const text = Number.isFinite(value)
    ? dataType === "counter"
      ? Math.round(value).toString()
      : NUMBER_FORMATTER.format(value)
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

export function LiveValuesPage({ values, signals, devices, gateways, loading, error, onRefresh }: Props) {
  // device_code -> gateway_code mapping (cihazdan gateway'e gitmek icin)
  const deviceGwMap = useMemo(() => {
    const m = new Map<string, string | undefined>();
    for (const d of devices) m.set(d.code, d.gatewayCode);
    return m;
  }, [devices]);
  const gwOnlineMap = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const g of gateways) m.set(g.code, isGatewayOnline(g));
    return m;
  }, [gateways]);

  const effectiveQuality = (row: SignalLiveRow): string | null => {
    const gwCode = deviceGwMap.get(row.device_code);
    if (gwCode && gwOnlineMap.get(gwCode) === false) {
      // Gateway offline -> bagli cihazin sinyali "bad" (kalite kotu) gozukur.
      return "bad";
    }
    return row.quality;
  };
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [search, setSearch] = useState("");
  const [autoRefreshSec, setAutoRefreshSec] = useState<number>(() => readStoredAutoRefresh());
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  // Yeni filtreler — cihaz, kaynak (Master/Sat01/Sat02), kalite
  const [deviceFilter, setDeviceFilter] = useState<string>("all"); // device_code veya "all"
  const [sourceFilter, setSourceFilter] = useState<string>("all"); // master/sat01/sat02 veya "all"
  const [qualityFilter, setQualityFilter] = useState<string>("all"); // good/bad/comm_lost/pending/all

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

  // Filtre dropdown'ları için mevcut canlı değerlerden çıkarılan benzersiz listeler
  const deviceOptions = useMemo(() => {
    const seen = new Map<string, string>(); // code -> name
    for (const row of values) {
      if (row.device_code && !seen.has(row.device_code)) {
        seen.set(row.device_code, row.device_name || row.device_code);
      }
    }
    return Array.from(seen, ([code, name]) => ({ code, name })).sort((a, b) =>
      a.name.localeCompare(b.name, "tr")
    );
  }, [values]);

  const sourceOptions = useMemo(() => {
    const set = new Set<string>();
    for (const row of values) {
      if (row.source) set.add(row.source);
    }
    // Tutarlı sıra: master, sat01, sat02, sonra diğerleri
    const ordered: string[] = [];
    ["master", "sat01", "sat02"].forEach((s) => {
      if (set.has(s)) {
        ordered.push(s);
        set.delete(s);
      }
    });
    Array.from(set).sort().forEach((s) => ordered.push(s));
    return ordered;
  }, [values]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return values.filter((row) => {
      const sig = signalByKey.get(row.signal_key);
      // String tipli sinyaller hic gosterilmiyor — cihaz tarafinda 'Not Class 0'
      // ile isaretli oldugu icin okunamiyor.
      if (sig?.data_type === "string") return false;
      if (activeTab !== "all") {
        if (!sig || sig.data_type !== activeTab) return false;
      }
      if (deviceFilter !== "all" && row.device_code !== deviceFilter) return false;
      if (sourceFilter !== "all" && row.source !== sourceFilter) return false;
      if (qualityFilter !== "all") {
        const eq = effectiveQuality(row);
        if (qualityFilter === "pending") {
          if (eq) return false;
        } else if (eq !== qualityFilter) {
          return false;
        }
      }
      if (!q) return true;
      return (
        row.signal_label.toLowerCase().includes(q) ||
        row.signal_key.toLowerCase().includes(q) ||
        row.device_code.toLowerCase().includes(q) ||
        row.device_name.toLowerCase().includes(q)
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values, signalByKey, activeTab, search, deviceFilter, sourceFilter, qualityFilter]);

  // Filtre/tab/sayfa boyutu degisince ilk sayfaya don
  useEffect(() => {
    setPage(1);
  }, [activeTab, search, pageSize, deviceFilter, sourceFilter, qualityFilter]);

  const hasActiveFilter =
    deviceFilter !== "all" ||
    sourceFilter !== "all" ||
    qualityFilter !== "all" ||
    activeTab !== "all" ||
    search.trim().length > 0;

  const handleClearFilters = () => {
    setDeviceFilter("all");
    setSourceFilter("all");
    setQualityFilter("all");
    setActiveTab("all");
    setSearch("");
  };

  const pagedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

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

      <div className="signals-toolbar live-values-toolbar">
        <input
          className="signals-search"
          type="search"
          placeholder="Ara (cihaz, etiket, key)..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <div className="live-filter-group">
          <select
            className="live-filter-select"
            value={deviceFilter}
            onChange={(event) => setDeviceFilter(event.target.value)}
            title="Cihaza göre filtrele"
          >
            <option value="all">Tüm cihazlar</option>
            {deviceOptions.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.name} · {opt.code}
              </option>
            ))}
          </select>
          <select
            className="live-filter-select"
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            title="Kaynağa göre filtrele (Master / Satellite)"
          >
            <option value="all">Tüm kaynaklar</option>
            {sourceOptions.map((src) => (
              <option key={src} value={src}>
                {SOURCE_LABEL[src] ?? src}
              </option>
            ))}
          </select>
          <select
            className="live-filter-select"
            value={qualityFilter}
            onChange={(event) => setQualityFilter(event.target.value)}
            title="Kaliteye göre filtrele"
          >
            <option value="all">Tüm kaliteler</option>
            <option value="good">İyi (good)</option>
            <option value="bad">Kötü (bad)</option>
            <option value="comm_lost">Haberleşme kayıp</option>
            <option value="pending">Henüz veri yok</option>
          </select>
          {hasActiveFilter ? (
            <button
              type="button"
              className="secondary-btn live-filter-clear"
              onClick={handleClearFilters}
              title="Tüm filtreleri temizle"
            >
              Temizle
            </button>
          ) : null}
        </div>
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
            {pagedRows.map((row) => {
              const sig = signalByKey.get(row.signal_key);
              // Once row.data_type (backend live endpoint'inden gelir; en doğru kaynak),
              // sonra catalog. Catalog yuklenmemis olsa bile string sinyaller dogru
              // gosterilsin diye row.data_type one alindi.
              const dataType = ((row.data_type as SignalDataType | undefined) ?? sig?.data_type) as SignalDataType | undefined;
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
                      formatValue(row.value, dataType, row.unit, row.value_string)
                    )}
                  </td>
                  <td className="cell-center">
                    {(() => {
                      const q = effectiveQuality(row);
                      return q ? (
                        <span className={`quality quality-${q}`}>{q}</span>
                      ) : (
                        <span className="quality quality-pending">—</span>
                      );
                    })()}
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
