/**
 * Olaylar sayfasi — SUNUCU TARAFLI filtre + sayfalama.
 *
 * Eski surum App'ten gelen ilk 1000 olayi client-side filtreliyordu; artik
 * sayfa kendi verisini `/events?limit&offset&...` ile ceker, toplam sayi
 * X-Total-Count header'indan gelir — 1000 siniri kalkti, 2 yillik gecmis
 * sayfalanarak gezilebilir.
 *
 * Filtreler ayri bir MODAL'da toplanir. Modal TASLAK (draft) uzerinde
 * calisir; "Uygula" denene kadar sunucuya istek gitmez — aksi halde her
 * secim degisikliginde arkadaki tablo yenilenir ve kullanici gormedigi bir
 * listeyi filtrelemis olur. Uygulanan filtreler cip olarak toolbar'in
 * altinda gorunur (modal kapaliyken de ne suzuldugu bellidir).
 *
 * Gorunum: Durum sutunu olay tipinden turetilen rozet (Tetiklendi /
 * Normale dondu / Eklendi / Silindi...), Mesaj sutunu yalnizca OZNE
 * ("Test alarmi") — tam metin satirin tooltip'inde.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { TablePagination } from "../../components/TablePagination";
import { buildEventFilterParams, fetchSystemEventsPaged } from "../../shared/api";
import type { DeviceRow, SystemEvent } from "../../shared/types";
import {
  categoryFilterLabel,
  categoryLabelTr,
  categoryPillClass,
  severityLabelTr,
  severityPillClass
} from "./eventDisplayLabels";
import {
  STATUS_FILTERS,
  eventStatusClass,
  eventStatusLabel,
  eventSubject
} from "./eventStatus";
import { formatEventMessage } from "./formatEventMessage";

type Props = {
  accessToken: string;
  /** Cihaz kodu → ad çözümleme + kaynak (Master/Sat 01/Sat 02) için */
  devices?: DeviceRow[];
};

// Filtre secenekleri sunucudan gelen sayfayla SINIRLI olmamali (eski kod
// kategori listesini yuklu olaylardan turetiyordu) — sabit liste.
const CATEGORY_OPTIONS = [
  "alarm",
  "alarm-rule",
  "auth",
  "device",
  "fault",
  "gateway",
  "grid",
  "notification",
  "outbound",
  "project-settings",
  "responsibility-area",
  "security",
  "settings",
  "signal",
  "system",
  "telemetry",
  "user",
];

const SEVERITY_OPTIONS = ["info", "warning", "error", "critical"];

// Olay kayitlari 2 yil saklaniyor (FIFO) — zaman araligi secenekleri o
// pencerenin tamamini kapsar.
type TimeRange = "all" | "1h" | "24h" | "7d" | "30d" | "90d" | "1y" | "2y" | "custom";
const TIME_RANGES: TimeRange[] = [
  "all",
  "1h",
  "24h",
  "7d",
  "30d",
  "90d",
  "1y",
  "2y",
  "custom",
];
const DAY_MS = 86_400_000;
const RANGE_MS: Record<string, number> = {
  "1h": 3600_000,
  "24h": DAY_MS,
  "7d": 7 * DAY_MS,
  "30d": 30 * DAY_MS,
  "90d": 90 * DAY_MS,
  "1y": 365 * DAY_MS,
  "2y": 730 * DAY_MS,
};

const SOURCE_LABEL_FROM_PREFIX: Record<string, { label: string; klass: string }> = {
  master: { label: "Master", klass: "master" },
  sat01: { label: "Sat 01", klass: "sat01" },
  sat02: { label: "Sat 02", klass: "sat02" }
};

/** Modal'da duzenlenen, "Uygula" ile yururluge giren filtre kumesi. */
type FilterState = {
  category: string;
  severity: string;
  status: string;
  device: string;
  actor: string;
  timeRange: TimeRange;
  customFrom: string;
  customTo: string;
};

const EMPTY_FILTERS: FilterState = {
  category: "all",
  severity: "all",
  status: "all",
  device: "all",
  actor: "",
  timeRange: "all",
  customFrom: "",
  customTo: "",
};

/** Event.metadata_json'dan signal_key çıkarır (varsa). */
function extractSignalKey(metadataJson: string | null | undefined): string | null {
  if (!metadataJson) return null;
  try {
    const parsed = JSON.parse(metadataJson);
    const sk = parsed?.signal_key;
    return typeof sk === "string" && sk ? sk : null;
  } catch {
    return null;
  }
}

/** datetime-local input degeri -> ISO (UTC). Bos/bozuksa undefined. */
function localInputToIso(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

export function EventsPage({ accessToken, devices }: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  // `filters` YURURLUKTEKI kume, `draft` modal icinde duzenlenen kopya.
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [draft, setDraft] = useState<FilterState>(EMPTY_FILTERS);
  const [filterModalOpen, setFilterModalOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const [items, setItems] = useState<SystemEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const [showExportModal, setShowExportModal] = useState(false);
  const [exportFormat, setExportFormat] = useState<"csv" | "json" | "xlsx" | "pdf">("csv");
  const [exportBusy, setExportBusy] = useState(false);

  // Arama debounce: her tus vurusunda sunucuya gitme.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 400);
    return () => window.clearTimeout(timer);
  }, [search]);

  // Filtre degisince ilk sayfaya don.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, filters, pageSize]);

  // Modal Esc ile kapansin (proje genelindeki modal davranisi).
  useEffect(() => {
    if (!filterModalOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFilterModalOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [filterModalOpen]);

  /** Aktif zaman araligi -> ISO from/to. Preset araliklar "simdiden geriye". */
  const dateWindow = useMemo((): { from?: string; to?: string } => {
    if (filters.timeRange === "all") return {};
    if (filters.timeRange === "custom") {
      return {
        from: localInputToIso(filters.customFrom),
        to: localInputToIso(filters.customTo),
      };
    }
    // refreshTick bagimliligi: yenilemede pencere "simdi"ye gore kaysin.
    void refreshTick;
    return { from: new Date(Date.now() - RANGE_MS[filters.timeRange]).toISOString() };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.timeRange, filters.customFrom, filters.customTo, refreshTick]);

  /** Sunucuya gidecek filtre kumesi — liste ve export ORTAK kullanir. */
  const activeFilters = useMemo(
    () => ({
      category: filters.category === "all" ? undefined : filters.category,
      severity: filters.severity === "all" ? undefined : filters.severity,
      q: debouncedSearch,
      actorUsername: filters.actor,
      deviceCode: filters.device === "all" ? undefined : filters.device,
      eventTypeLike:
        filters.status === "all"
          ? undefined
          : STATUS_FILTERS.find((item) => item.key === filters.status)?.patterns,
      dateFrom: dateWindow.from,
      dateTo: dateWindow.to,
    }),
    [filters, debouncedSearch, dateWindow]
  );

  const deviceNameByCode = useMemo(() => {
    const map = new Map<string, string>();
    for (const d of devices ?? []) map.set(d.code, d.name);
    return map;
  }, [devices]);

  /** Uygulanan filtrelerin cip listesi — modal kapaliyken de gorunur. */
  const filterChips = useMemo(() => {
    const chips: Array<{ key: keyof FilterState | "range"; label: string }> = [];
    if (filters.category !== "all") {
      chips.push({ key: "category", label: categoryFilterLabel(filters.category) });
    }
    if (filters.severity !== "all") {
      chips.push({ key: "severity", label: severityLabelTr(filters.severity) });
    }
    if (filters.status !== "all") {
      chips.push({ key: "status", label: t(`events.status.${filters.status}`) });
    }
    if (filters.device !== "all") {
      chips.push({
        key: "device",
        label: deviceNameByCode.get(filters.device) ?? filters.device,
      });
    }
    if (filters.actor.trim()) {
      chips.push({ key: "actor", label: filters.actor.trim() });
    }
    if (filters.timeRange !== "all") {
      chips.push({ key: "range", label: t(`events.timeRange.${filters.timeRange}`) });
    }
    return chips;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, deviceNameByCode, i18n.language]);

  const activeFilterCount = filterChips.length;

  /** Tek cipi kaldir (zaman araligi ozel tarihleri de sifirlar). */
  const removeChip = (key: keyof FilterState | "range") => {
    setFilters((prev) =>
      key === "range"
        ? { ...prev, timeRange: "all", customFrom: "", customTo: "" }
        : { ...prev, [key]: key === "actor" ? "" : "all" }
    );
  };

  const openFilterModal = () => {
    setDraft(filters);
    setFilterModalOpen(true);
  };

  const applyDraft = () => {
    setFilters(draft);
    setFilterModalOpen(false);
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSystemEventsPaged(accessToken, {
      ...activeFilters,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    })
      .then((result) => {
        if (cancelled) return;
        setItems(result.items);
        setTotal(result.total);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : t("common.errorOccurred"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, activeFilters, page, pageSize, refreshTick]);

  /** Cihaz hücresi: sadece "ad" (kaynak bilgisi ayrı sütunda). */
  const renderDeviceCell = (item: SystemEvent) => {
    const code = item.device_code;
    if (!code) return <span className="event-cell-empty">—</span>;
    const name = deviceNameByCode.get(code) ?? code;
    return <span className="event-device-name">{name}</span>;
  };

  /** Kaynak hücresi: signal_key prefix'inden Master/Sat 01/Sat 02 rozeti. */
  const renderSourceCell = (item: SystemEvent) => {
    const signalKey = extractSignalKey(item.metadata_json);
    if (!signalKey) return <span className="event-cell-empty">—</span>;
    const prefix = signalKey.split(".", 1)[0]?.toLowerCase() ?? "";
    const source = SOURCE_LABEL_FROM_PREFIX[prefix];
    if (!source) return <span className="event-cell-empty">{prefix || "—"}</span>;
    return (
      <span className={`badge badge-source badge-source-${source.klass} event-source-badge`}>
        {source.label}
      </span>
    );
  };

  const handleExport = async () => {
    // xlsx ve pdf backend'de uretiliyor (openpyxl + reportlab); csv/json da
    // ayni endpoint'ten iner. Ekrandaki filtrelerin AYNISI kullanilir —
    // buildEventFilterParams ortak kaynak.
    setExportBusy(true);
    try {
      const params = buildEventFilterParams(activeFilters);
      params.set("fmt", exportFormat);
      // PDF = ekranin OKUNABILIR raporu: yalnizca GORUNEN SAYFA iner
      // (1.8M kayitta "tum olaylar" PDF'i anlamsiz ve cok agir). CSV/XLSX
      // veri dokumu olarak filtre kapsaminda kalir (backend tavani 20k).
      if (exportFormat === "pdf") {
        params.set("offset", String((page - 1) * pageSize));
        params.set("limit", String(pageSize));
      }

      const response = await fetch(`/api/v1/events/export?${params.toString()}`, {
        credentials: "include",
      });
      if (!response.ok) {
        const txt = await response.text();
        throw new Error(`Export failed: HTTP ${response.status} — ${txt.slice(0, 200)}`);
      }

      const blob = await response.blob();
      // Dosya adi: backend Content-Disposition header'inda gonderiyor
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      const now = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const fallbackBase = i18n.language?.startsWith("tr") ? "olaylar" : "events";
      const filename = match ? match[1] : `${fallbackBase}-${now}.${exportFormat}`;

      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setShowExportModal(false);
    } catch (err) {
      console.error("Event export failed", err);
      window.alert(err instanceof Error ? err.message : "Export failed.");
    } finally {
      setExportBusy(false);
    }
  };

  return (
    <section className="alarms-layout events-layout">
      <div className="alarms-list-card events-list-card">
        <div className="alarms-toolbar events-toolbar">
          <input
            className="device-search-input"
            placeholder={t("events.search")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="alarms-filter-row">
            <button
              type="button"
              className={`secondary-btn action-btn events-filter-toggle${
                activeFilterCount > 0 ? " is-open" : ""
              }`}
              onClick={openFilterModal}
            >
              {t("events.filters.toggle")}
              {activeFilterCount > 0 ? (
                <span className="events-filter-count">{activeFilterCount}</span>
              ) : null}
            </button>
            <button
              className="secondary-btn action-btn"
              type="button"
              onClick={() => setRefreshTick((tick) => tick + 1)}
              disabled={loading}
            >
              {t("common.refresh")}
            </button>
            <button
              className="secondary-btn action-btn"
              type="button"
              onClick={() => setShowExportModal(true)}
            >
              {t("common.export")}
            </button>
          </div>
        </div>

        {filterChips.length > 0 ? (
          <div className="events-chip-row">
            {filterChips.map((chip) => (
              <button
                key={chip.key}
                type="button"
                className="events-chip"
                onClick={() => removeChip(chip.key)}
                title={t("events.filters.removeChip")}
              >
                {chip.label}
                <span aria-hidden="true">×</span>
              </button>
            ))}
            <button
              type="button"
              className="events-chip-clear"
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              {t("events.filters.clear")}
            </button>
          </div>
        ) : null}

        {error ? <p className="error-text">{error}</p> : null}

        <div className="alarms-table-wrap events-table-wrap">
          <table className="values-table events-table">
            <thead>
              <tr>
                <th scope="col" className="event-col-date">{t("events.table.date")}</th>
                <th scope="col" className="event-col-priority">{t("events.table.priority")}</th>
                <th scope="col" className="event-col-category">{t("events.table.category")}</th>
                <th scope="col" className="event-col-message">{t("events.table.message")}</th>
                <th scope="col" className="event-col-status">{t("events.table.status")}</th>
                <th scope="col" className="event-col-user">{t("events.table.user")}</th>
                <th scope="col" className="event-col-device">{t("events.table.device")}</th>
                <th scope="col" className="event-col-source">{t("events.table.source")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td className="event-col-date">{new Date(item.created_at).toLocaleString(localeTag)}</td>
                  <td className="event-col-priority">
                    <span className={severityPillClass(item.severity)} title={item.severity}>
                      {severityLabelTr(item.severity)}
                    </span>
                  </td>
                  <td className="event-col-category">
                    <span className={categoryPillClass(item.category)} title={item.category}>
                      {categoryLabelTr(item.category)}
                    </span>
                  </td>
                  {/* Ozne sade; tam metin tooltip'te. */}
                  <td className="event-col-message" title={formatEventMessage(item)}>
                    {eventSubject(item)}
                  </td>
                  <td className="event-col-status">
                    <span className={eventStatusClass(item.event_type)} title={item.event_type}>
                      {eventStatusLabel(item.event_type)}
                    </span>
                  </td>
                  <td className="event-col-user">{item.actor_username ?? "-"}</td>
                  <td className="event-col-device">{renderDeviceCell(item)}</td>
                  <td className="event-col-source">{renderSourceCell(item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && items.length === 0 ? <p className="helper-text">{t("events.noResults")}</p> : null}
        {total > 0 ? (
          <TablePagination
            totalItems={total}
            page={page}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
            itemLabel={t("events.itemLabel")}
          />
        ) : null}
      </div>

      {filterModalOpen ? (
        <div className="settings-modal-backdrop" onClick={() => setFilterModalOpen(false)}>
          <div
            className="settings-modal events-filter-modal"
            role="dialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <h3>{t("events.filters.title")}</h3>
            <p className="helper-text">{t("events.filters.hint")}</p>

            <div className="events-filter-grid">
              <label className="events-filter-field">
                <span>{t("events.table.category")}</span>
                <select
                  value={draft.category}
                  onChange={(event) => setDraft({ ...draft, category: event.target.value })}
                >
                  <option value="all">{t("events.filterAllCategories")}</option>
                  {CATEGORY_OPTIONS.map((category) => (
                    <option key={category} value={category}>
                      {categoryFilterLabel(category)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="events-filter-field">
                <span>{t("events.table.priority")}</span>
                <select
                  value={draft.severity}
                  onChange={(event) => setDraft({ ...draft, severity: event.target.value })}
                >
                  <option value="all">{t("events.filterAllSeverities")}</option>
                  {SEVERITY_OPTIONS.map((severity) => (
                    <option key={severity} value={severity}>
                      {severityLabelTr(severity)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="events-filter-field">
                <span>{t("events.table.status")}</span>
                <select
                  value={draft.status}
                  onChange={(event) => setDraft({ ...draft, status: event.target.value })}
                >
                  <option value="all">{t("events.filters.allStatuses")}</option>
                  {STATUS_FILTERS.map((item) => (
                    <option key={item.key} value={item.key}>
                      {t(`events.status.${item.key}`)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="events-filter-field">
                <span>{t("events.table.device")}</span>
                <select
                  value={draft.device}
                  onChange={(event) => setDraft({ ...draft, device: event.target.value })}
                >
                  <option value="all">{t("events.filters.allDevices")}</option>
                  {(devices ?? []).map((device) => (
                    <option key={device.id} value={device.code}>
                      {device.name} ({device.code})
                    </option>
                  ))}
                </select>
              </label>

              <label className="events-filter-field">
                <span>{t("events.table.user")}</span>
                <input
                  type="text"
                  value={draft.actor}
                  placeholder={t("events.filters.userPlaceholder")}
                  onChange={(event) => setDraft({ ...draft, actor: event.target.value })}
                />
              </label>

              <label className="events-filter-field">
                <span>{t("events.filters.timeRangeLabel")}</span>
                <select
                  value={draft.timeRange}
                  onChange={(event) =>
                    setDraft({ ...draft, timeRange: event.target.value as TimeRange })
                  }
                >
                  {TIME_RANGES.map((range) => (
                    <option key={range} value={range}>
                      {t(`events.timeRange.${range}`)}
                    </option>
                  ))}
                </select>
              </label>

              {draft.timeRange === "custom" ? (
                <>
                  <label className="events-filter-field">
                    <span>{t("events.timeRange.from")}</span>
                    <input
                      type="datetime-local"
                      value={draft.customFrom}
                      onChange={(event) => setDraft({ ...draft, customFrom: event.target.value })}
                    />
                  </label>
                  <label className="events-filter-field">
                    <span>{t("events.timeRange.to")}</span>
                    <input
                      type="datetime-local"
                      value={draft.customTo}
                      onChange={(event) => setDraft({ ...draft, customTo: event.target.value })}
                    />
                  </label>
                </>
              ) : null}
            </div>

            <div className="modal-actions events-filter-actions">
              <button
                type="button"
                className="secondary-btn events-filter-reset"
                onClick={() => setDraft(EMPTY_FILTERS)}
              >
                {t("events.filters.clear")}
              </button>
              <button
                type="button"
                className="secondary-btn"
                onClick={() => setFilterModalOpen(false)}
              >
                {t("common.cancel")}
              </button>
              <button type="button" className="primary-btn" onClick={applyDraft}>
                {t("events.filters.apply")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {showExportModal ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal export-modal">
            <h3>{t("events.export.title")}</h3>
            <p className="helper-text">{t("events.export.hint")}</p>
            <label>
              {t("events.export.format")}
              <select
                value={exportFormat}
                onChange={(event) => setExportFormat(event.target.value as "csv" | "json" | "xlsx" | "pdf")}
                disabled={exportBusy}
              >
                <option value="csv">CSV (Excel-compatible)</option>
                <option value="xlsx">Excel Workbook (.xlsx)</option>
                <option value="pdf">PDF Report (.pdf)</option>
                <option value="json">JSON (raw data)</option>
              </select>
            </label>
            {exportFormat === "pdf" ? (
              <p className="helper-text">{t("events.export.pdfPageOnly")}</p>
            ) : null}
            <div className="modal-actions">
              <button
                type="button"
                className="secondary-btn"
                onClick={() => setShowExportModal(false)}
                disabled={exportBusy}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="primary-btn"
                onClick={() => void handleExport()}
                disabled={exportBusy}
              >
                {exportBusy ? "..." : t("events.export.download")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
