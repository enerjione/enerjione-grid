import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { TablePagination } from "../../components/TablePagination";
import type { DeviceRow, SystemEvent } from "../../shared/types";
import {
  categoryFilterLabel,
  categoryLabelTr,
  categoryPillClass,
  severityLabelTr,
  severityPillClass
} from "./eventDisplayLabels";

type Props = {
  events: SystemEvent[];
  loading?: boolean;
  /** Cihaz kodu → ad çözümleme + kaynak (Master/Sat 01/Sat 02) için */
  devices?: DeviceRow[];
};

const SOURCE_LABEL_FROM_PREFIX: Record<string, { label: string; klass: string }> = {
  master: { label: "Master", klass: "master" },
  sat01: { label: "Sat 01", klass: "sat01" },
  sat02: { label: "Sat 02", klass: "sat02" }
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

export function EventsPage({ events, loading, devices }: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportFormat, setExportFormat] = useState<"csv" | "json">("csv");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const categories = useMemo(() => Array.from(new Set(events.map((item) => item.category))).sort(), [events]);

  // Cihaz kodu → ad lookup
  const deviceNameByCode = useMemo(() => {
    const map = new Map<string, string>();
    for (const d of devices ?? []) map.set(d.code, d.name);
    return map;
  }, [devices]);

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

  const filteredEvents = useMemo(() => {
    return events.filter((item) => {
      const categoryOk = categoryFilter === "all" ? true : item.category === categoryFilter;
      const severityOk = severityFilter === "all" ? true : item.severity === severityFilter;
      const text = `${item.message} ${item.actor_username ?? ""} ${item.device_code ?? ""}`.toLowerCase();
      const searchOk = search.trim() ? text.includes(search.trim().toLowerCase()) : true;
      return categoryOk && severityOk && searchOk;
    });
  }, [events, categoryFilter, severityFilter, search]);

  // Filtre/arama degisince ilk sayfaya don
  useEffect(() => {
    setPage(1);
  }, [search, categoryFilter, severityFilter, pageSize]);

  const pagedEvents = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredEvents.slice(start, start + pageSize);
  }, [filteredEvents, page, pageSize]);

  const exportRows = filteredEvents.map((item) => ({
    oncelik: severityLabelTr(item.severity),
    kategori: categoryLabelTr(item.category),
    mesaj: item.message,
    kullanici: item.actor_username ?? "-",
    cihaz: item.device_code ?? "-",
    tarih: new Date(item.created_at).toLocaleString(localeTag)
  }));

  const handleExport = () => {
    const now = new Date().toISOString().replace(/[:.]/g, "-");
    if (exportFormat === "json") {
      const blob = new Blob([JSON.stringify(exportRows, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      const baseJson = i18n.language?.startsWith("tr") ? "olaylar" : "events";
      anchor.download = `${baseJson}-${now}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setShowExportModal(false);
      return;
    }

    const headers = [
      t("events.table.priority"),
      t("events.table.category"),
      t("events.table.message"),
      t("events.table.user"),
      t("events.table.device"),
      t("events.table.date"),
    ];
    const rows = exportRows.map((item) =>
      [item.oncelik, item.kategori, item.mesaj, item.kullanici, item.cihaz, item.tarih]
        .map((cell) => `"${String(cell).replace(/"/g, '""')}"`)
        .join(",")
    );
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    const base = i18n.language?.startsWith("tr") ? "olaylar" : "events";
    anchor.download = `${base}-${now}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
    setShowExportModal(false);
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
            <select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
              <option value="all">{t("events.filterAllCategories")}</option>
              {categories.map((category) => (
                <option key={category} value={category}>
                  {categoryFilterLabel(category)}
                </option>
              ))}
            </select>
            <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}>
              <option value="all">{t("events.filterAllSeverities")}</option>
              <option value="info">{severityLabelTr("info")}</option>
              <option value="warning">{severityLabelTr("warning")}</option>
              <option value="error">{severityLabelTr("error")}</option>
              <option value="critical">{severityLabelTr("critical")}</option>
            </select>
            <button className="secondary-btn action-btn" type="button" onClick={() => setShowExportModal(true)}>
              {t("common.export")}
            </button>
          </div>
        </div>

        <div className="alarms-table-wrap events-table-wrap">
          <table className="values-table events-table">
            <thead>
              <tr>
                <th className="event-col-date">{t("events.table.date")}</th>
                <th>{t("events.table.priority")}</th>
                <th>{t("events.table.category")}</th>
                <th>{t("events.table.message")}</th>
                <th className="event-col-user">{t("events.table.user")}</th>
                <th className="event-col-device">{t("events.table.device")}</th>
                <th className="event-col-source">{t("events.table.source")}</th>
              </tr>
            </thead>
            <tbody>
              {pagedEvents.map((item) => (
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
                  <td>{item.message}</td>
                  <td className="event-col-user">{item.actor_username ?? "-"}</td>
                  <td className="event-col-device">{renderDeviceCell(item)}</td>
                  <td className="event-col-source">{renderSourceCell(item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && filteredEvents.length === 0 ? <p className="helper-text">{t("events.noResults")}</p> : null}
        {filteredEvents.length > 0 ? (
          <TablePagination
            totalItems={filteredEvents.length}
            page={page}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
            itemLabel={t("events.itemLabel")}
          />
        ) : null}
      </div>
      {showExportModal ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal export-modal">
            <h3>{t("events.export.title")}</h3>
            <p className="helper-text">{t("events.export.hint")}</p>
            <label>
              {t("events.export.format")}
              <select value={exportFormat} onChange={(event) => setExportFormat(event.target.value as "csv" | "json")}>
                <option value="csv">CSV</option>
                <option value="json">JSON</option>
              </select>
            </label>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowExportModal(false)}>
                {t("common.cancel")}
              </button>
              <button type="button" className="primary-btn" onClick={handleExport}>
                {t("events.export.download")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
