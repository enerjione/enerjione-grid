import { useMemo, useState } from "react";

import type { SystemEvent } from "../../shared/types";
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
};

export function EventsPage({ events, loading }: Props) {
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportFormat, setExportFormat] = useState<"csv" | "json">("csv");

  const categories = useMemo(() => Array.from(new Set(events.map((item) => item.category))).sort(), [events]);

  const filteredEvents = useMemo(() => {
    return events.filter((item) => {
      const categoryOk = categoryFilter === "all" ? true : item.category === categoryFilter;
      const severityOk = severityFilter === "all" ? true : item.severity === severityFilter;
      const text = `${item.message} ${item.actor_username ?? ""} ${item.device_code ?? ""}`.toLowerCase();
      const searchOk = search.trim() ? text.includes(search.trim().toLowerCase()) : true;
      return categoryOk && severityOk && searchOk;
    });
  }, [events, categoryFilter, severityFilter, search]);

  const exportRows = filteredEvents.map((item) => ({
    oncelik: severityLabelTr(item.severity),
    kategori: categoryLabelTr(item.category),
    mesaj: item.message,
    kullanici: item.actor_username ?? "-",
    cihaz: item.device_code ?? "-",
    tarih: new Date(item.created_at).toLocaleString("tr-TR")
  }));

  const handleExport = () => {
    const now = new Date().toISOString().replace(/[:.]/g, "-");
    if (exportFormat === "json") {
      const blob = new Blob([JSON.stringify(exportRows, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `olaylar-${now}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setShowExportModal(false);
      return;
    }

    const headers = ["Öncelik", "Kategori", "Mesaj", "Kullanıcı", "Cihaz", "Tarih"];
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
    anchor.download = `olaylar-${now}.csv`;
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
            placeholder="Olay ara..."
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="alarms-filter-row">
            <select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
              <option value="all">Tüm kategoriler</option>
              {categories.map((category) => (
                <option key={category} value={category}>
                  {categoryFilterLabel(category)}
                </option>
              ))}
            </select>
            <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}>
              <option value="all">Tüm öncelikler</option>
              <option value="info">{severityLabelTr("info")}</option>
              <option value="warning">{severityLabelTr("warning")}</option>
              <option value="error">{severityLabelTr("error")}</option>
              <option value="critical">{severityLabelTr("critical")}</option>
            </select>
            <button className="secondary-btn action-btn" type="button" onClick={() => setShowExportModal(true)}>
              Export
            </button>
          </div>
        </div>

        <div className="alarms-table-wrap events-table-wrap">
          <table className="values-table events-table">
            <thead>
              <tr>
                <th>Öncelik</th>
                <th>Kategori</th>
                <th>Mesaj</th>
                <th>Kullanıcı</th>
                <th>Cihaz</th>
                <th>Tarih</th>
              </tr>
            </thead>
            <tbody>
              {filteredEvents.map((item) => (
                <tr key={item.id}>
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
                  <td>{item.actor_username ?? "-"}</td>
                  <td>{item.device_code ?? "-"}</td>
                  <td>{new Date(item.created_at).toLocaleString("tr-TR")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && filteredEvents.length === 0 ? <p className="helper-text">Filtreye uygun olay bulunamadı.</p> : null}
      </div>
      {showExportModal ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal export-modal">
            <h3>Olayları Dışa Aktar</h3>
            <p className="helper-text">Filtrelenmiş olaylar seçtiğiniz formatta indirilecektir.</p>
            <label>
              Format
              <select value={exportFormat} onChange={(event) => setExportFormat(event.target.value as "csv" | "json")}>
                <option value="csv">CSV</option>
                <option value="json">JSON</option>
              </select>
            </label>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowExportModal(false)}>
                İptal
              </button>
              <button type="button" className="primary-btn" onClick={handleExport}>
                İndir
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
