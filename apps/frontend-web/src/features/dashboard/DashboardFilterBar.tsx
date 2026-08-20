import { useTranslation } from "react-i18next";

import type { Line, Region, ResponsibilityAreaRow } from "../../shared/types";

/**
 * Durum suzgeci — CALISMA-ZAMANI kovalarina gore.
 *
 * `smartIdle` ve `late` gateway 1.15.0 ile gelen `device_health_v1`
 * kanalindan dogar. Eski kurulumda ikisinin de sayisi 0'dir ve rozetleri
 * cizilmez; o zaman `online` + `offline` yine TOPLAMI verir, yani eski
 * davranis aynen korunur.
 *
 * Dortlu kume BIR BOLUNTUDUR: her cihaz tam olarak bir rozete duser
 * (bkz. App.tsx `dashboardCounts`). Cift sayim yapisal olarak imkansiz —
 * gecikmis bir Smart cihaz "Gecikmis"tedir ve "Smart Bekleme"de AYRICA
 * gorunmez.
 */
export type StatusFilter = "all" | "online" | "smartIdle" | "late" | "offline" | "alarm";

type Props = {
  /** Arama metni (cihaz adı / kodu). Tablo + harita aynı stringi kullanır. */
  search: string;
  onSearchChange: (value: string) => void;
  statusFilter: StatusFilter;
  onStatusFilterChange: (value: StatusFilter) => void;
  areaId: number | "all";
  onAreaIdChange: (value: number | "all") => void;
  responsibilityAreas?: ResponsibilityAreaRow[];
  /** Şebeke topolojisi filtreleri. "unassigned" = hat/bölge atanmamış cihazlar. */
  regionId: number | "all" | "unassigned";
  onRegionIdChange: (value: number | "all" | "unassigned") => void;
  lineId: number | "all" | "unassigned";
  onLineIdChange: (value: number | "all" | "unassigned") => void;
  regions?: Region[];
  lines?: Line[];
  /** Sayım rozetleri için pre-computed değerler (filtreden geçmemiş ham toplam). */
  counts: {
    total: number;
    /** Gateway ile konusan cihazlar (`ONLINE`). */
    online: number;
    /** Modemi kapali, SAGLIKLI uyuyan cihazlar (`SMART_IDLE`). */
    smartIdle: number;
    /** Rapor gecikmis ama haberlesme kaybi DEGIL (`LATE`). */
    late: number;
    /** Kalanlar: toparlanan, kopmus, dinleyici hatasi, bilinmeyen.
     *  `RECOVERING` burada cunku cihaz o an cevrimici DEGILDIR; "Gecikmis"
     *  kovasina koymak, farkli bir sorunu gecikme gibi gosterirdi. */
    offline: number;
    alarm: number;
  };
  visibleCount: number;
  areaLoading?: boolean;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
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
  onToggleSidebar
}: Props) {
  const { t } = useTranslation();
  const showActiveFilter =
    statusFilter !== "all" ||
    areaId !== "all" ||
    regionId !== "all" ||
    lineId !== "all" ||
    search.trim().length > 0;

  // Bölge seçimine göre hat dropdown filtrele.
  // "unassigned" bölge seçildiyse hat dropdown'unda anlamlı seçim kalmaz
  // (atanmamış cihazlar zaten hiçbir hat'a bağlı değildir) — boş bırakılır.
  const visibleLines =
    regionId === "unassigned"
      ? []
      : (lines ?? []).filter((l) => regionId === "all" || l.region_id === regionId);

  return (
    <div className="dashboard-filter-bar">
      <button
        type="button"
        className="dashboard-filter-toggle"
        onClick={onToggleSidebar}
        title={sidebarCollapsed ? t("dashboard.filter.showSidebar") : t("dashboard.filter.hideSidebar")}
        aria-label={sidebarCollapsed ? t("dashboard.filter.showSidebar") : t("dashboard.filter.hideSidebar")}
      >
        <span className="material-symbols-outlined">
          {sidebarCollapsed ? "menu_open" : "menu"}
        </span>
      </button>

      <input
        type="search"
        className="dashboard-filter-search"
        placeholder={t("dashboard.filter.searchPlaceholder")}
        value={search}
        onChange={(event) => onSearchChange(event.target.value)}
      />

      <div className="map-filter-chips">
        <button
          type="button"
          className={`map-filter-chip ${statusFilter === "all" ? "active" : ""}`}
          onClick={() => onStatusFilterChange("all")}
        >
          {t("dashboard.filter.all")} <span className="map-filter-chip-count">{counts.total}</span>
        </button>
        <button
          type="button"
          className={`map-filter-chip map-filter-chip--online ${statusFilter === "online" ? "active" : ""}`}
          onClick={() => onStatusFilterChange("online")}
        >
          {t("dashboard.filter.online")} <span className="map-filter-chip-count">{counts.online}</span>
        </button>
        {/* SMART BEKLEME — MAVI, saglikli. Eski gateway'de (sayi 0) hic
            cizilmez ki mevcut kurulumlarda bos bir rozet gurultu yapmasin. */}
        {counts.smartIdle > 0 || counts.late > 0 ? (
          <button
            type="button"
            className={`map-filter-chip map-filter-chip--smartidle ${statusFilter === "smartIdle" ? "active" : ""}`}
            onClick={() => onStatusFilterChange("smartIdle")}
            title={t("deviceRuntime.stateHint.smartIdle")}
          >
            {t("deviceRuntime.kpi.smartIdle")}{" "}
            <span className="map-filter-chip-count">{counts.smartIdle}</span>
          </button>
        ) : null}
        {counts.smartIdle > 0 || counts.late > 0 ? (
          <button
            type="button"
            className={`map-filter-chip map-filter-chip--late ${statusFilter === "late" ? "active" : ""}`}
            onClick={() => onStatusFilterChange("late")}
            title={t("deviceRuntime.stateHint.late")}
          >
            {t("deviceRuntime.kpi.late")}{" "}
            <span className="map-filter-chip-count">{counts.late}</span>
          </button>
        ) : null}
        <button
          type="button"
          className={`map-filter-chip map-filter-chip--offline ${statusFilter === "offline" ? "active" : ""}`}
          onClick={() => onStatusFilterChange("offline")}
        >
          {t("dashboard.filter.offline")} <span className="map-filter-chip-count">{counts.offline}</span>
        </button>
        <button
          type="button"
          className={`map-filter-chip map-filter-chip--alarm ${statusFilter === "alarm" ? "active" : ""}`}
          onClick={() => onStatusFilterChange("alarm")}
        >
          {t("dashboard.filter.withAlarm")} <span className="map-filter-chip-count">{counts.alarm}</span>
        </button>
      </div>

      <div className="map-filter-divider" />

      <label className="map-filter-area">
        <span>{t("dashboard.filter.responsibilityArea")}</span>
        <select
          value={areaId === "all" ? "all" : String(areaId)}
          onChange={(event) => {
            const v = event.target.value;
            onAreaIdChange(v === "all" ? "all" : Number(v));
          }}
          disabled={!responsibilityAreas || responsibilityAreas.length === 0}
        >
          <option value="all">{t("dashboard.filter.allAreas")}</option>
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
        <span>{t("dashboard.filter.region")}</span>
        <select
          value={regionId === "all" ? "all" : regionId === "unassigned" ? "unassigned" : String(regionId)}
          onChange={(event) => {
            const v = event.target.value;
            const next: number | "all" | "unassigned" =
              v === "all" ? "all" : v === "unassigned" ? "unassigned" : Number(v);
            onRegionIdChange(next);
            // Bölge değişirse hat filtresini sıfırla (uyumsuz olabilir)
            if (lineId !== "all") onLineIdChange("all");
          }}
          disabled={!regions || regions.length === 0}
        >
          <option value="all">{t("dashboard.filter.allRegions")}</option>
          <option value="unassigned">{t("dashboard.filter.unassigned")}</option>
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
        <span>{t("dashboard.filter.line")}</span>
        <select
          value={lineId === "all" ? "all" : lineId === "unassigned" ? "unassigned" : String(lineId)}
          onChange={(event) => {
            const v = event.target.value;
            const next: number | "all" | "unassigned" =
              v === "all" ? "all" : v === "unassigned" ? "unassigned" : Number(v);
            onLineIdChange(next);
          }}
          disabled={regionId === "unassigned"}
        >
          <option value="all">{t("dashboard.filter.allLines")}</option>
          <option value="unassigned">{t("dashboard.filter.unassigned")}</option>
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
          ? t("dashboard.filter.loading")
          : t("dashboard.filter.deviceCount", { visible: visibleCount, total: counts.total })}
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
          {t("dashboard.filter.clear")}
        </button>
      ) : null}

    </div>
  );
}
