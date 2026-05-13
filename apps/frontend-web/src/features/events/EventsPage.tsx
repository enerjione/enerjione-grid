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
import { formatEventMessage } from "./formatEventMessage";

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
  const [exportFormat, setExportFormat] = useState<"csv" | "json" | "xlsx" | "pdf">("csv");
  const [exportBusy, setExportBusy] = useState(false);
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
      // Arama, ham mesaj + cevrilmis mesaj uzerinden gecsin (kullanici hangi
      // dilde aradigi bilinmiyor)
      const translated = formatEventMessage(item);
      const text = `${item.message} ${translated} ${item.actor_username ?? ""} ${item.device_code ?? ""}`.toLowerCase();
      const searchOk = search.trim() ? text.includes(search.trim().toLowerCase()) : true;
      return categoryOk && severityOk && searchOk;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, categoryFilter, severityFilter, search, i18n.language]);

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
    mesaj: formatEventMessage(item),
    kullanici: item.actor_username ?? "-",
    cihaz: item.device_code ?? "-",
    tarih: new Date(item.created_at).toLocaleString(localeTag)
  }));

  const handleExport = async () => {
    // xlsx ve pdf SADECE backend'de uretiliyor (openpyxl + reportlab).
    // csv ve json icin de backend kullaniyoruz \u2014 filtreleri server-side ayni
    // mantikta uyguladigi icin daha tutarli (UI'da page-limited degil tum
    // filtreli set indirilebilir).
    setExportBusy(true);
    try {
      const params = new URLSearchParams({ fmt: exportFormat });
      if (categoryFilter !== "all") params.append("category", categoryFilter);
      if (severityFilter !== "all") params.append("severity", severityFilter);
      // search filtresi backend'de yok \u2014 client-side; xlsx/pdf'de search
      // uygulanmaz (sadece kategori+severity backend filtresi). Kullanici
      // search yapmissa CSV/JSON istemesi gerek (UI uyarisi gelecek turde).

      const response = await fetch(`/api/v1/events/export?${params.toString()}`, {
        credentials: "include",
      });
      if (!response.ok) {
        const txt = await response.text();
        throw new Error(`Export failed: HTTP ${response.status} \u2014 ${txt.slice(0, 200)}`);
      }

      const blob = await response.blob();
      // Dosya adi: backend Content-Disposition header'inda gonderiyor
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      const now = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const fallbackBase = i18n.language?.startsWith("tr") ? "olaylar" : "events";
      const filename = match
        ? match[1]
        : `${fallbackBase}-${now}.${exportFormat}`;

      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setShowExportModal(false);
    } catch (err) {
      // Kullaniciya alert + console \u2014 toast yoksa minimum geri bildirim
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
                <th scope="col" className="event-col-date">{t("events.table.date")}</th>
                <th scope="col" className="event-col-priority">{t("events.table.priority")}</th>
                <th scope="col" className="event-col-category">{t("events.table.category")}</th>
                <th scope="col">{t("events.table.message")}</th>
                <th scope="col" className="event-col-user">{t("events.table.user")}</th>
                <th scope="col" className="event-col-device">{t("events.table.device")}</th>
                <th scope="col" className="event-col-source">{t("events.table.source")}</th>
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
                  <td>{formatEventMessage(item)}</td>
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
            {(exportFormat === "xlsx" || exportFormat === "pdf") && search.trim() ? (
              <p className="helper-text" style={{ color: "#a85800" }}>
                Note: text search filter is applied client-side; {exportFormat.toUpperCase()} export uses
                category + severity filters only. To export search-filtered rows, use CSV or JSON.
              </p>
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
