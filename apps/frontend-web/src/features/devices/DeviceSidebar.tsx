import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AlarmEvent, DeviceRow, SignalLiveRow } from "../../shared/types";
import type { GridSnapshot } from "../../shared/api";
import { useProjectSettings } from "../../components/ProjectSettingsProvider";
import { TablePagination } from "../../components/TablePagination";
import { DeviceRowButton, type DeviceAlarmState } from "./DeviceRowButton";
import { DeviceLineTree } from "./DeviceLineTree";

export type DeviceTopologyLabel = {
  regionName: string;
  lineName: string;
};

type Props = {
  devices: DeviceRow[];
  selectedId: number;
  onSelect: (id: number) => void;
  /** Açık alarmları cihaz id'sine göre çözmek için. */
  alarms?: AlarmEvent[];
  /** Master batarya voltajını canlı okumak için (3.40V=0%, 3.71V=100% lineer). */
  liveValues?: SignalLiveRow[];
  /** Cihaz id -> {region,line} etiketi. Sebeke topolojisinden turetilir. */
  deviceTopology?: Map<number, DeviceTopologyLabel>;
  /** Hat Agaci gorunumu icin ham topoloji (bolge/hat/direk/segment). */
  gridSnapshot?: GridSnapshot | null;
  /** Haritada grilestirilecek hatlar. */
  hiddenLineIds?: Set<number>;
  onToggleLineHidden?: (lineId: number) => void;
};

// Master batarya voltaj-yüzde haritası — Proje Ayarları'ndan override edilebilir.
const DEFAULT_BATTERY_VOLTAGE_FULL = 3.71;
const DEFAULT_BATTERY_VOLTAGE_LOW = 3.4;

// 500 cihazlik kurulumda tum satirlari DOM'a basmak sayfayi kilitliyordu.
// Duz liste artik sayfalanir; agac gorunumu ise hatlari kapali baslatarak
// ayni sorunu bastan onler.
const DEFAULT_PAGE_SIZE = 20;
const PAGE_SIZE_OPTIONS = [20, 50, 100, 200];

type ViewMode = "list" | "tree";
const VIEW_STORAGE_KEY = "e1.dashboard.sidebar-view";

function makeVoltageToPercent(low: number, full: number) {
  const span = full - low;
  return (v: number | null): number | null => {
    if (v === null || Number.isNaN(v)) return null;
    if (v <= low) return 0;
    if (v >= full) return 100;
    if (span <= 0) return null;
    return Math.round(((v - low) / span) * 100);
  };
}

function readStoredView(): ViewMode {
  if (typeof window === "undefined") return "list";
  return window.localStorage.getItem(VIEW_STORAGE_KEY) === "tree" ? "tree" : "list";
}

export function DeviceSidebar({
  devices,
  selectedId,
  onSelect,
  alarms,
  liveValues,
  deviceTopology,
  gridSnapshot,
  hiddenLineIds,
  onToggleLineHidden
}: Props) {
  const { settings } = useProjectSettings();
  const { t } = useTranslation();
  const [view, setView] = useState<ViewMode>(readStoredView);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  useEffect(() => {
    window.localStorage.setItem(VIEW_STORAGE_KEY, view);
  }, [view]);

  const battLow = typeof settings.battery_voltage_low === "number" ? settings.battery_voltage_low : DEFAULT_BATTERY_VOLTAGE_LOW;
  const battFull = typeof settings.battery_voltage_full === "number" ? settings.battery_voltage_full : DEFAULT_BATTERY_VOLTAGE_FULL;
  const voltageToPercent = useMemo(() => makeVoltageToPercent(battLow, battFull), [battLow, battFull]);

  // Cihaz id → alarm durumu: "open" (onaylanmamış aktif), "ack" (onaylanmış aktif), null
  const deviceAlarmState = useMemo(() => {
    const map = new Map<number, "open" | "ack">();
    if (!alarms) return map;
    for (const a of alarms) {
      if (a.reset) continue;
      const prev = map.get(a.device_id);
      if (!a.acknowledged) {
        // Onaylanmamış varsa daima öncelikli
        map.set(a.device_id, "open");
      } else if (!prev) {
        map.set(a.device_id, "ack");
      }
    }
    return map;
  }, [alarms]);

  // Cihaz id → master.battery_voltage_satellite canlı yüzdesi.
  // Popup ile aynı kaynaktan beslenir; DB'deki device.battery_percent (default 100)
  // henüz hiç batarya telemetrisi gelmediyse yanıltıcı %100 gösterirdi.
  const masterBatteryByDevice = useMemo(() => {
    const map = new Map<number, number | null>();
    if (!liveValues) return map;
    for (const row of liveValues) {
      if (row.signal_key !== "master.battery_voltage_satellite") continue;
      const v = typeof row.value === "number" ? row.value : null;
      map.set(row.device_id, voltageToPercent(v));
    }
    return map;
  }, [liveValues, voltageToPercent]);

  const alarmStateOf = useCallback(
    (deviceId: number): DeviceAlarmState => deviceAlarmState.get(deviceId) ?? null,
    [deviceAlarmState]
  );

  // ARIZA ureten aktif alarmlar. Haritadaki `alarmActiveDeviceIds` ile AYNI
  // kural: produces_fault === false olan (gecici/gurultulu) alarmlar hat
  // arizasi saymaz, sadece Alarmlar ekraninda durur. Agac ile harita ayni
  // seyi kirmizi gostersin diye kriter birebir ayni tutuldu.
  const faultDeviceIds = useMemo(() => {
    const s = new Set<number>();
    for (const a of alarms ?? []) {
      if (!a.reset && a.produces_fault !== false) s.add(a.device_id);
    }
    return s;
  }, [alarms]);

  const batteryOf = useCallback(
    (device: DeviceRow): number | null => {
      // Once canli master batarya voltajini dene; yoksa eski DB alanina dus.
      // Eski default %100 fallback'ine guvenmemek icin liveValues set ise
      // ondan gelen sonuc (null da olabilir) tercih edilir.
      const live = liveValues ? masterBatteryByDevice.get(device.id) ?? null : undefined;
      const battery = live !== undefined ? live : device.batteryPercent ?? null;
      return typeof battery === "number" ? Math.max(0, Math.min(100, battery)) : null;
    },
    [liveValues, masterBatteryByDevice]
  );

  // Filtre degisip liste kisalinca son sayfada asili kalmayalim.
  const totalPages = Math.max(1, Math.ceil(devices.length / pageSize));
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  // Secili cihaz baska bir sayfadaysa o sayfaya atla — haritadan cihaz
  // secildiginde sidebar'in bos gorunmesini onler.
  useEffect(() => {
    if (view !== "list" || !selectedId) return;
    const index = devices.findIndex((d) => d.id === selectedId);
    if (index < 0) return;
    const target = Math.floor(index / pageSize) + 1;
    setPage((prev) => (prev === target ? prev : target));
  }, [selectedId, devices, pageSize, view]);

  const pagedDevices = useMemo(() => {
    const start = (Math.min(page, totalPages) - 1) * pageSize;
    return devices.slice(start, start + pageSize);
  }, [devices, page, pageSize, totalPages]);

  const hiddenLines = hiddenLineIds ?? new Set<number>();

  return (
    <aside className="sidebar device-sidebar-modern">
      {/* Gorunum secici — duz liste (sayfali) / hat agaci. */}
      <div className="device-sidebar-viewbar">
        <button
          type="button"
          className={`device-view-tab ${view === "list" ? "active" : ""}`}
          onClick={() => setView("list")}
        >
          <span className="material-symbols-outlined">list</span>
          {t("dashboard.sidebar.viewList")}
        </button>
        <button
          type="button"
          className={`device-view-tab ${view === "tree" ? "active" : ""}`}
          onClick={() => setView("tree")}
        >
          <span className="material-symbols-outlined">account_tree</span>
          {t("dashboard.sidebar.viewTree")}
        </button>
      </div>

      {view === "tree" ? (
        <div className="device-list device-list--tree">
          <DeviceLineTree
            devices={devices}
            selectedId={selectedId}
            onSelect={onSelect}
            gridSnapshot={gridSnapshot}
            alarmStateOf={alarmStateOf}
            batteryOf={batteryOf}
            faultDeviceIds={faultDeviceIds}
            hiddenLineIds={hiddenLines}
            onToggleLineHidden={onToggleLineHidden ?? (() => {})}
          />
        </div>
      ) : (
        <>
          <div className="device-list">
            {devices.length === 0 ? (
              <p className="device-list-empty">{t("dashboard.sidebar.noDevices")}</p>
            ) : null}
            {pagedDevices.map((device) => {
              const topo = deviceTopology?.get(device.id);
              const isUnassigned = !topo;
              return (
                <DeviceRowButton
                  key={device.id}
                  device={device}
                  selected={selectedId === device.id}
                  onSelect={onSelect}
                  alarmState={alarmStateOf(device.id)}
                  batteryPercent={batteryOf(device)}
                  subtitle={
                    topo
                      ? `${topo.regionName ? topo.regionName + " · " : ""}${topo.lineName}`
                      : t("dashboard.sidebar.unassigned")
                  }
                  unassigned={isUnassigned}
                />
              );
            })}
          </div>
          {devices.length > PAGE_SIZE_OPTIONS[0] ? (
            <div className="device-sidebar-pagination">
              <TablePagination
                totalItems={devices.length}
                page={Math.min(page, totalPages)}
                pageSize={pageSize}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
                pageSizeOptions={PAGE_SIZE_OPTIONS}
                itemLabel={t("dashboard.sidebar.deviceItem")}
              />
            </div>
          ) : null}
        </>
      )}
    </aside>
  );
}
