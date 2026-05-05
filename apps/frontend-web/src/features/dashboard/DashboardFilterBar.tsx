import type { Line, Region, ResponsibilityAreaRow } from "../../shared/types";

export type StatusFilter = "all" | "online" | "offline" | "alarm";

type Props = {
  /** Arama metni (cihaz adı / kodu). Tablo + harita aynı stringi kullanır. */
  search: string;
  onSearchChange: (value: string) => void;
  statusFilter: StatusFilter;
  onStatusFilterChange: (value: StatusFilter) => void;
  areaId: number | "all";
  onAreaIdChange: (value: number | "all") => void;
  responsibilityAreas?: ResponsibilityAreaRow[];
  /** Şebeke topolojisi filtreleri */
  regionId: number | "all";
  onRegionIdChange: (value: number | "all") => void;
  lineId: number | "all";
  onLineIdChange: (value: number | "all") => void;
  regions?: Region[];
  lines?: Line[];
  /** Sayım rozetleri için pre-computed değerler (filtreden geçmemiş ham toplam). */
  counts: {
    total: number;
    online: number;
    offline: number;
    alarm: number;
  };
  visibleCount: number;
  areaLoading?: boolean;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  activeTab: "map" | "values";
  onActiveTabChange: (value: "map" | "values") => void;
};

export function DashboardFilterBar({
  search,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
  areaId,
  onAreaIdChange,
  responsibilityAreas,
  regionId,
  onRegionIdChange,
  lineId,
  onLineIdChange,
  regions,
  lines,
  counts,
  visibleCount,
  areaLoading,
  sidebarCollapsed,
  onToggleSidebar,
  activeTab,
  onActiveTabChange
}: Props) {
  const showActiveFilter =
    statusFilter !== "all" ||
    areaId !== "all" ||
    regionId !== "all" ||
    lineId !== "all" ||
    search.trim().length > 0;

  // Bölge seçimine göre hat dropdown filtrele
  const visibleLines = (lines ?? []).filter(
    (l) => regionId === "all" || l.region_id === regionId
  );

  return (
    <div className="dashboard-filter-bar">
      <button
        type="button"
        className="dashboard-filter-toggle"
        onClick={onToggleSidebar}
        title={sidebarCollapsed ? "Cihaz listesini göster" : "Cihaz listesini gizle"}
        aria-label={sidebarCollapsed ? "Cihaz listesini göster" : "Cihaz listesini gizle"}
      >
        <span className="material-symbols-outlined">
          {sidebarCollapsed ? "menu_open" : "menu"}
        </span>
      </button>

      <input
        type="search"
        className="dashboard-filter-search"
        placeholder="Cihaz ara (ad, kod)..."
        value={search}
        onChange={(event) => onSearchChange(event.target.value)}
      />

      <div className="map-filter-chips">
        <button
          type="button"
          className={`map-filter-chip ${statusFilter === "all" ? "active" : ""}`}
          onClick={() => onStatusFilterChange("all")}
        >
          Tümü <span className="map-filter-chip-count">{counts.total}</span>
        </button>
        <button
          type="button"
          className={`map-filter-chip map-filter-chip--online ${statusFilter === "online" ? "active" : ""}`}
          onClick={() => onStatusFilterChange("online")}
        >
          Çevrimiçi <span className="map-filter-chip-count">{counts.online}</span>
        </button>
        <button
          type="button"
          className={`map-filter-chip map-filter-chip--offline ${statusFilter === "offline" ? "active" : ""}`}
          onClick={() => onStatusFilterChange("offline")}
        >
          Çevrimdışı <span className="map-filter-chip-count">{counts.offline}</span>
        </button>
        <button
          type="button"
          className={`map-filter-chip map-filter-chip--alarm ${statusFilter === "alarm" ? "active" : ""}`}
          onClick={() => onStatusFilterChange("alarm")}
        >
          Alarmlı <span className="map-filter-chip-count">{counts.alarm}</span>
        </button>
      </div>

      <div className="map-filter-divider" />

      <label className="map-filter-area">
        <span>Sorumluluk alanı</span>
        <select
          value={areaId === "all" ? "all" : String(areaId)}
          onChange={(event) => {
            const v = event.target.value;
            onAreaIdChange(v === "all" ? "all" : Number(v));
          }}
          disabled={!responsibilityAreas || responsibilityAreas.length === 0}
        >
          <option value="all">Tüm alanlar</option>
          {(responsibilityAreas ?? [])
            .filter((a) => a.is_active)
            .map((area) => (
              <option key={area.id} value={area.id}>
                {area.name}
              </option>
            ))}
        </select>
      </label>

      <label className="map-filter-area">
        <span>Bölge</span>
        <select
          value={regionId === "all" ? "all" : String(regionId)}
          onChange={(event) => {
            const v = event.target.value;
            const next = v === "all" ? "all" : Number(v);
            onRegionIdChange(next);
            // Bölge değişirse hat filtresini sıfırla (uyumsuz olabilir)
            if (lineId !== "all") onLineIdChange("all");
          }}
          disabled={!regions || regions.length === 0}
        >
          <option value="all">Tüm bölgeler</option>
          {(regions ?? [])
            .filter((r) => r.is_active)
            .map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
        </select>
      </label>

      <label className="map-filter-area">
        <span>Hat</span>
        <select
          value={lineId === "all" ? "all" : String(lineId)}
          onChange={(event) => {
            const v = event.target.value;
            onLineIdChange(v === "all" ? "all" : Number(v));
          }}
          disabled={visibleLines.length === 0}
        >
          <option value="all">Tüm hatlar</option>
          {visibleLines
            .filter((l) => l.is_active)
            .map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
        </select>
      </label>

      <span className="map-filter-summary">
        {areaLoading
          ? "Yükleniyor…"
          : `${visibleCount} / ${counts.total} cihaz`}
      </span>

      {showActiveFilter ? (
        <button
          type="button"
          className="secondary-btn map-filter-clear"
          onClick={() => {
            onSearchChange("");
            onStatusFilterChange("all");
            onAreaIdChange("all");
            onRegionIdChange("all");
            onLineIdChange("all");
          }}
        >
          Temizle
        </button>
      ) : null}

      <div className="dashboard-filter-tabs">
        <button
          type="button"
          className={`dashboard-filter-tab ${activeTab === "map" ? "active" : ""}`}
          onClick={() => onActiveTabChange("map")}
        >
          Harita
        </button>
        <button
          type="button"
          className={`dashboard-filter-tab ${activeTab === "values" ? "active" : ""}`}
          onClick={() => onActiveTabChange("values")}
        >
          Tablo
        </button>
      </div>
    </div>
  );
}
