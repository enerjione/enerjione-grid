import type { ResponsibilityAreaRow } from "../../shared/types";

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
  /** Konum (il) filtresi — "all", "Yurt dışı" veya il adı. */
  locationFilter: string;
  onLocationFilterChange: (value: string) => void;
  /** Mevcut cihazlardan derlenmiş benzersiz konum etiketleri. */
  locationOptions: string[];
  /** Sayım rozetleri için pre-computed değerler (filtreden geçmemiş ham toplam). */
  counts: {
    total: number;
    online: number;
    offline: number;
    alarm: number;
  };
  /** Aktif filtre sonrası gösterilen cihaz sayısı (örn. "3 / 12 cihaz"). */
  visibleCount: number;
  areaLoading?: boolean;
  /** Sol cihaz listesi (sidebar) gizli mi — toggle butonunun durumu. */
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  /** Aktif sekme: harita ya da tablo. */
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
  locationFilter,
  onLocationFilterChange,
  locationOptions,
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
    locationFilter !== "all" ||
    search.trim().length > 0;

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
        <span>Konum</span>
        <select
          value={locationFilter}
          onChange={(event) => onLocationFilterChange(event.target.value)}
          disabled={locationOptions.length === 0}
        >
          <option value="all">Tüm konumlar</option>
          {locationOptions.map((loc) => (
            <option key={loc} value={loc}>
              {loc}
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
            onLocationFilterChange("all");
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
