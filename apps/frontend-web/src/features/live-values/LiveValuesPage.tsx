import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { WsStatusBadge } from "../../components/WsStatusBadge";
import type { WsConnectionState } from "../../shared/useLiveValuesSocket";
import { TablePagination } from "../../components/TablePagination";
import { SearchableSelect } from "../../components/SearchableSelect";
import { usePolling } from "../../shared/usePolling";
import { signalLabel } from "../../shared/signalLabel";
import { sourceLabel } from "../signals/signalCatalogConstants";
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
  /** Canli telemetri soketi durumu. Rozet BU sayfada duruyor cunku soket
   *  yalnizca burada (ve Anasayfa/cihaz detayda) ACIK — Sistem Durumu gibi
   *  soketin bilerek kapali oldugu bir sayfada rozet her zaman "Kopuk" der
   *  ve bu bir ARIZA sanilir. Bkz. App.tsx `liveValuesNeeded`. */
  wsState?: WsConnectionState;
  /** Son TELEMETRI mesajinin zamani (ms). */
  wsLastDataAt?: number | null;
};

const GATEWAY_LIVE_SEC = 60;

function isGatewayOnline(gw: Gateway | undefined): boolean {
  if (!gw || !gw.is_active) return false;
  if (!gw.last_seen_at) return false;
  const sec = (Date.now() - new Date(gw.last_seen_at).getTime()) / 1000;
  return sec < GATEWAY_LIVE_SEC;
}

type TabKey = "all" | SignalDataType;

// Yayinlanan (okunabilir) sinyal tipleri — sekme olarak gosterilir.
// binary_output (G10) haric: o DNP3 komut kanali, canli deger yayinlamaz.
const DATA_TYPES: SignalDataType[] = [
  "analog",
  "binary",
  "counter",
  "string",
  "analog_output"
];



// Auto refresh options — labels come from i18n at render time.
type RefreshOpt = { value: number; labelKey: string; labelArgs?: Record<string, unknown> };
const AUTO_REFRESH_OPTIONS: RefreshOpt[] = [
  { value: 0, labelKey: "engineering.liveValues.autoRefreshOff" },
  { value: 1, labelKey: "engineering.liveValues.secondsShort", labelArgs: { count: 1 } },
  { value: 2, labelKey: "engineering.liveValues.secondsShort", labelArgs: { count: 2 } },
  { value: 5, labelKey: "engineering.liveValues.secondsShort", labelArgs: { count: 5 } },
  { value: 10, labelKey: "engineering.liveValues.secondsShort", labelArgs: { count: 10 } },
  { value: 30, labelKey: "engineering.liveValues.secondsShort", labelArgs: { count: 30 } },
  { value: 60, labelKey: "engineering.liveValues.minutesShort", labelArgs: { count: 1 } }
];

const AUTO_REFRESH_STORAGE_KEY = "hsl.live-values.auto-refresh-sec";
// Default: KAPALI (manuel). Kullanici "Yenile" ile ceker; istemedikce otomatik
// yenileme backend'i bosuna yormaz (her cagride 600 cihaz × N sinyal SELECT).
// Isteyen asagidaki secimden 1/2/5... sn otomatik yenilemeyi acabilir.
const AUTO_REFRESH_DEFAULT_SEC = 0;

function readStoredAutoRefresh(): number {
  if (typeof window === "undefined") return AUTO_REFRESH_DEFAULT_SEC;
  const raw = window.localStorage.getItem(AUTO_REFRESH_STORAGE_KEY);
  if (raw === null) return AUTO_REFRESH_DEFAULT_SEC;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return AUTO_REFRESH_DEFAULT_SEC;
  return AUTO_REFRESH_OPTIONS.some((opt) => opt.value === parsed) ? parsed : AUTO_REFRESH_DEFAULT_SEC;
}

function makeNumberFormatter(localeTag: string): Intl.NumberFormat {
  return new Intl.NumberFormat(localeTag, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 6,
    useGrouping: false
  });
}

function formatValueWith(
  value: number | null,
  dataType: SignalDataType | undefined,
  unit: string | null | undefined,
  valueString: string | null | undefined,
  numberFmt: Intl.NumberFormat,
  binaryActive: string,
  binaryInactive: string,
) {
  if (dataType === "string") {
    const txt = (valueString ?? "").trim();
    return txt.length > 0 ? txt : "—";
  }
  if (value === null || value === undefined) {
    return "—";
  }
  if (dataType === "binary") {
    return value ? binaryActive : binaryInactive;
  }
  const text = Number.isFinite(value)
    ? dataType === "counter"
      ? Math.round(value).toString()
      : numberFmt.format(value)
    : String(value);
  return unit ? `${text} ${unit}` : text;
}

function formatTimestamp(ts: string | null, localeTag: string): string {
  if (!ts) {
    return "—";
  }
  try {
    return new Date(ts).toLocaleString(localeTag);
  } catch {
    return ts;
  }
}

/** Saat farki icin birim etiketleri (i18n'den render aninda doldurulur). */
type ClockUnits = { sec: string; min: string; hour: string; day: string };

/** Cihaz saati ile gateway saati arasindaki farki okunur hale getirir.
 *  Isaret KORUNUR: "+" cihaz ileride, "-" cihaz geride. */
function formatClockOffset(
  deviceIso: string,
  gatewayIso: string,
  units: ClockUnits
): string | null {
  const dev = new Date(deviceIso).getTime();
  const gw = new Date(gatewayIso).getTime();
  if (Number.isNaN(dev) || Number.isNaN(gw)) return null;
  const totalSec = Math.round((dev - gw) / 1000);
  const sign = totalSec >= 0 ? "+" : "-";
  const abs = Math.abs(totalSec);
  if (abs < 60) return `${sign}${abs} ${units.sec}`;
  if (abs < 3600) return `${sign}${Math.round(abs / 60)} ${units.min}`;
  if (abs < 86400) return `${sign}${Math.round(abs / 3600)} ${units.hour}`;
  return `${sign}${Math.round(abs / 86400)} ${units.day}`;
}

/**
 * Sinyal satirinda gosterilecek saat uyarisi.
 *
 * SADECE backend'in KANITLADIGI durumlar uyari uretir:
 *   * "invalid"        -> damga makul pencerenin disinda ya da cihaz kendi
 *                         saatinin bozuk oldugunu bildirdi
 *   * "unsynchronized" -> cihaz zaman senkronu yapilmadigini bildirdi
 *
 * `null`/undefined ve "synchronized" HICBIR uyari uretmez. Bu ayrim onemli:
 * eski gateway'ler bu alani hic gondermiyor ve "bilgi yok"u "saat bozuk" gibi
 * gostermek 175 sinyalin tamamini sahte uyariyla doldururdu.
 *
 * Fark hesabi da tek basina uyari SEBEBI DEGIL: 4G kopmasi sonrasi bosalan
 * event buffer'inda gunlerce eski ama TAMAMEN GECERLI damgalar bulunur.
 * Negatif farki "saat geri kalmis" saymak, tam da korumak istedigimiz
 * senaryoda yanlis alarm uretirdi.
 */
function clockWarningOf(row: SignalLiveRow): "invalid" | "unsynchronized" | null {
  const q = (row.timestamp_quality ?? "").toLowerCase();
  if (q === "invalid") return "invalid";
  if (q === "unsynchronized") return "unsynchronized";
  return null;
}

export function LiveValuesPage({ values, signals, devices, gateways, loading, error, onRefresh,
  wsState,
  wsLastDataAt
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const numberFmt = useMemo(() => makeNumberFormatter(localeTag), [localeTag]);
  const dataTypeLabel = (type: SignalDataType): string =>
    t(`engineering.liveValues.dataType.${type}`, { defaultValue: type });
  const binaryActive = t("engineering.liveValues.binaryActive");
  const binaryInactive = t("engineering.liveValues.binaryInactive");
  const clockUnits = useMemo<ClockUnits>(
    () => ({
      sec: t("engineering.liveValues.clock.unitSec"),
      min: t("engineering.liveValues.clock.unitMin"),
      hour: t("engineering.liveValues.clock.unitHour"),
      day: t("engineering.liveValues.clock.unitDay")
    }),
    [t]
  );
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

  usePolling({
    enabled: autoRefreshSec > 0,
    intervalMs: autoRefreshSec * 1000,
    fn: () => {
      if (loadingRef.current) return;
      void onRefreshRef.current();
    },
    // Kullanici periyodu degistirdiginde aninda istek atma — secim yaparken
    // (5s -> 10s -> 30s) her adimda bir cagri gitmesin.
    immediate: false
  });

  /** Cihaz kodu -> model. Katalog eslemesi buna gore yapilir. */
  const modelByDeviceCode = useMemo(() => {
    const map = new Map<string, string>();
    for (const d of devices) map.set(d.code, d.model);
    return map;
  }, [devices]);

  /** (model, key) -> katalog satiri.
   *
   *  Sinyal anahtari MODEL BAZINDA tekil: `master.actual_current` hem Smart
   *  Navigator 2.0'da hem Pole Master Kit'te var ve DNP3 adresleri farkli.
   *  Yalnizca `key` ile anahtarlamak son okunan modelin satirini kazandirir;
   *  tip sekmesi sayimlari ve satirin tipi yanlis modelden gelirdi.
   *
   *  Yalnizca-key haritasi YEDEK: cihaz listesi henuz yuklenmediyse satiri
   *  tamamen kaybetmek yerine eldeki bilgiyle gostermek dogru. */
  const signalByModelKey = useMemo(() => {
    const map = new Map<string, SignalCatalogRow>();
    for (const s of signals) map.set(`${s.model}|${s.key}`, s);
    return map;
  }, [signals]);

  const signalByAnyKey = useMemo(() => {
    const map = new Map<string, SignalCatalogRow>();
    for (const s of signals) if (!map.has(s.key)) map.set(s.key, s);
    return map;
  }, [signals]);

  const signalOf = (row: SignalLiveRow): SignalCatalogRow | undefined => {
    const model = modelByDeviceCode.get(row.device_code);
    return (
      (model ? signalByModelKey.get(`${model}|${row.signal_key}`) : undefined) ??
      signalByAnyKey.get(row.signal_key)
    );
  };

  const countsByType = useMemo(() => {
    const map = new Map<SignalDataType, number>();
    DATA_TYPES.forEach((t) => map.set(t, 0));
    for (const row of values) {
      const type = signalOf(row)?.data_type;
      if (type) map.set(type, (map.get(type) ?? 0) + 1);
    }
    return map;
  }, [values, signals, devices]);

  // Filtre dropdown'ları için mevcut canlı değerlerden çıkarılan benzersiz listeler
  const deviceOptions = useMemo(() => {
    const seen = new Map<string, string>(); // code -> name
    for (const row of values) {
      if (row.device_code && !seen.has(row.device_code)) {
        seen.set(row.device_code, row.device_name || row.device_code);
      }
    }
    return Array.from(seen, ([code, name]) => ({ code, name })).sort((a, b) =>
      a.name.localeCompare(b.name, localeTag)
    );
  }, [values, localeTag]);

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
      const sig = signalOf(row);
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
        // Cevrilmis ad da aranabilir olmali — kullanici ekranda gordugu
        // Turkce adi yazarak filtreleyebilsin.
        signalLabel(row.signal_key, row.signal_label).toLowerCase().includes(q) ||
        row.signal_key.toLowerCase().includes(q) ||
        row.device_code.toLowerCase().includes(q) ||
        row.device_name.toLowerCase().includes(q)
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values, signals, devices, activeTab, search, deviceFilter, sourceFilter, qualityFilter]);

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
      {/* Duzen: once arama/filtre cubugu, ALTINDA veri tipi filtresi.
          Alarm kurallari ve sinyaller sayfalariyla ayni sira. */}
      <div className="signals-toolbar live-values-toolbar">
        {/* Veri akiyor mu — bu sayfanin ilk sorusu. */}
        {wsState ? (
          <WsStatusBadge state={wsState} lastDataAt={wsLastDataAt} />
        ) : null}
        <input
          className="signals-search"
          type="search"
          placeholder={t("engineering.liveValues.search")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <div className="live-filter-group">
          <SearchableSelect
            className="live-filter-select live-filter-select--searchable"
            value={deviceFilter}
            onChange={setDeviceFilter}
            options={deviceOptions.map((opt) => ({
              value: opt.code,
              label: opt.name,
              secondary: opt.code,
            }))}
            allValue="all"
            allLabel={t("engineering.liveValues.filter.allDevices")}
            title={t("engineering.liveValues.filter.device")}
            searchPlaceholder={t("engineering.liveValues.filter.deviceSearchPlaceholder")}
            emptyText={t("engineering.liveValues.filter.deviceSearchEmpty")}
          />
          <select
            className="live-filter-select"
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            title={t("engineering.liveValues.filter.source")}
          >
            <option value="all">{t("engineering.liveValues.filter.allSources")}</option>
            {sourceOptions.map((src) => (
              <option key={src} value={src}>
                {sourceLabel(src)}
              </option>
            ))}
          </select>
          <select
            className="live-filter-select"
            value={qualityFilter}
            onChange={(event) => setQualityFilter(event.target.value)}
            title={t("engineering.liveValues.filter.quality")}
          >
            <option value="all">{t("engineering.liveValues.filter.allQualities")}</option>
            <option value="good">{t("engineering.liveValues.filter.qualityGood")}</option>
            <option value="bad">{t("engineering.liveValues.filter.qualityBad")}</option>
            <option value="comm_lost">{t("engineering.liveValues.filter.qualityCommLost")}</option>
            <option value="pending">{t("engineering.liveValues.filter.qualityPending")}</option>
          </select>
          {hasActiveFilter ? (
            <button
              type="button"
              className="secondary-btn live-filter-clear"
              onClick={handleClearFilters}
              title={t("engineering.liveValues.filter.clearAll")}
            >
              {t("engineering.liveValues.filter.clear")}
            </button>
          ) : null}
        </div>
        <span className="signals-count-pill">
          {filtered.length} / {totalCount}
        </span>
        <label className="auto-refresh-control">
          <span className="auto-refresh-label">{t("engineering.liveValues.autoRefresh")}</span>
          <select
            className="auto-refresh-select"
            value={autoRefreshSec}
            onChange={(event) => setAutoRefreshSec(Number.parseInt(event.target.value, 10) || 0)}
          >
            {AUTO_REFRESH_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {t(opt.labelKey, opt.labelArgs ?? {})}
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
          {loading ? t("engineering.liveValues.refreshing") : t("engineering.liveValues.refresh")}
        </button>
      </div>

      <div className="signals-type-tabs">
        <button
          className={`signals-type-tab ${activeTab === "all" ? "active" : ""}`}
          onClick={() => setActiveTab("all")}
        >
          <span className="stt-label">{t("engineering.liveValues.all")}</span>
          <span className="stt-count">{totalCount}</span>
        </button>
        {DATA_TYPES.map((type) => (
          <button
            key={type}
            className={`signals-type-tab stt-${type} ${activeTab === type ? "active" : ""}`}
            onClick={() => setActiveTab(type)}
          >
            <span className="stt-label">{dataTypeLabel(type)}</span>
            <span className="stt-count">{countsByType.get(type) ?? 0}</span>
          </button>
        ))}
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <div className="live-values-table-wrap">
        <table className="values-table">
          <thead>
            <tr>
              <th scope="col">{t("engineering.liveValues.table.device")}</th>
              <th scope="col" className="cell-center">{t("engineering.liveValues.table.source")}</th>
              <th scope="col">{t("engineering.liveValues.table.signal")}</th>
              <th scope="col" className="cell-center">{t("engineering.liveValues.table.type")}</th>
              <th scope="col">{t("engineering.liveValues.table.value")}</th>
              <th scope="col" className="cell-center">{t("engineering.liveValues.table.quality")}</th>
              <th scope="col">{t("engineering.liveValues.table.time")}</th>
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((row) => {
              const sig = signalOf(row);
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
                      {sourceLabel(row.source)}
                    </span>
                  </td>
                  <td>
                    <div className="cell-strong">{signalLabel(row.signal_key, row.signal_label)}</div>
                    <div className="cell-helper">{row.signal_key}</div>
                  </td>
                  <td className="cell-center">
                    {dataType ? (
                      <span className={`badge badge-${dataType}`}>{dataTypeLabel(dataType)}</span>
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
                      formatValueWith(row.value, dataType, row.unit, row.value_string, numberFmt, binaryActive, binaryInactive)
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
                  <td>
                    {formatTimestamp(row.source_timestamp, localeTag)}
                    {(() => {
                      // Saat uyarisi KALITE ROZETINDEN AYRI, zaman hucresinde
                      // duruyor: sorun degerin kendisinde degil damgasinda.
                      const warn = clockWarningOf(row);
                      if (!warn) return null;
                      const devAt = row.device_event_at ?? null;
                      const offset =
                        devAt && row.source_timestamp
                          ? formatClockOffset(devAt, row.source_timestamp, clockUnits)
                          : null;
                      const tooltip = [
                        t(`engineering.liveValues.clock.${warn}Hint`),
                        devAt
                          ? `${t("engineering.liveValues.clock.deviceTime")}: ${formatTimestamp(devAt, localeTag)}`
                          : null,
                        offset ? `${t("engineering.liveValues.clock.offset")}: ${offset}` : null
                      ]
                        .filter(Boolean)
                        .join("\n");
                      return (
                        <div className={`live-clock-chip live-clock-chip--${warn}`} title={tooltip}>
                          <span className="material-symbols-outlined">schedule</span>
                          <span>
                            {t(`engineering.liveValues.clock.${warn}`)}
                            {offset ? ` (${offset})` : ""}
                          </span>
                        </div>
                      );
                    })()}
                  </td>
                </tr>
              );
            })}
            {filtered.length === 0 && !loading ? (
              <tr>
                <td colSpan={7}>
                  <div className="empty-state">
                    <span className="material-symbols-outlined">monitoring</span>
                    <p>{totalCount === 0 ? t("engineering.liveValues.noRowsInitial") : t("engineering.liveValues.noRows")}</p>
                  </div>
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
          itemLabel={t("engineering.liveValues.itemLabel")}
        />
      ) : null}
    </section>
  );
}
