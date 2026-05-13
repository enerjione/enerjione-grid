import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { AlarmEvent, DeviceRow, SignalLiveRow } from "../../shared/types";
import { useProjectSettings } from "../../components/ProjectSettingsProvider";

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
};

// Master batarya voltaj-yüzde haritası — Proje Ayarları'ndan override edilebilir.
const DEFAULT_BATTERY_VOLTAGE_FULL = 3.71;
const DEFAULT_BATTERY_VOLTAGE_LOW = 3.4;

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

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 5) return "şimdi";
  if (sec < 60) return `${sec} sn önce`;
  if (sec < 3600) return `${Math.round(sec / 60)} dk önce`;
  if (sec < 86400) return `${Math.round(sec / 3600)} sa önce`;
  return d.toLocaleString(undefined);
}

function batteryClass(percent: number | null | undefined): string {
  if (percent === null || percent === undefined) return "device-battery--unknown";
  if (percent <= 20) return "device-battery--critical";
  if (percent <= 50) return "device-battery--low";
  return "device-battery--ok";
}

export function DeviceSidebar({ devices, selectedId, onSelect, alarms, liveValues, deviceTopology }: Props) {
  const { settings } = useProjectSettings();
  const { t } = useTranslation();
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

  return (
    <aside className="sidebar device-sidebar-modern">
      <div className="device-list">
        {devices.length === 0 ? (
          <p className="device-list-empty">{t("dashboard.sidebar.noDevices")}</p>
        ) : null}
        {devices.map((device) => {
          const isOnline = device.communicationStatus === "online";
          // Once canli master batarya voltajini dene; yoksa eski DB alanina dus.
          // Eski default %100 fallback'ine guvenmemek icin liveValues set ise
          // ondan gelen sonuc (null da olabilir) tercih edilir.
          const liveBatt = liveValues ? masterBatteryByDevice.get(device.id) ?? null : undefined;
          const battery =
            liveBatt !== undefined ? liveBatt : (device.batteryPercent ?? null);
          const battPct = typeof battery === "number" ? Math.max(0, Math.min(100, battery)) : null;
          const topo = deviceTopology?.get(device.id);
          const topoLabel = topo
            ? `${topo.regionName ? topo.regionName + " · " : ""}${topo.lineName}`
            : t("dashboard.sidebar.noLine");
          const alarmState = deviceAlarmState.get(device.id) ?? (device.alarmActive ? "open" : null);
          const hasAlarm = alarmState !== null;
          return (
            <button
              key={device.id}
              className={`device-row ${selectedId === device.id ? "selected" : ""} ${
                isOnline ? "device-row--online" : "device-row--offline"
              } ${hasAlarm ? "device-row--alarm" : ""}`}
              onClick={() => onSelect(device.id)}
            >
              {/* Sağ üst köşede alarm rozeti — onaylanmamışsa kırmızı yanıp söner,
                 onaylanmışsa sarı/sabit (kullanıcı görmüş, üzerinde çalışılıyor). */}
              {alarmState === "open" ? (
                <span
                  className="device-row-alarm-pulse device-row-alarm-pulse--corner"
                  title={t("dashboard.sidebar.alarmOpen")}
                  aria-label={t("dashboard.sidebar.alarmOpen")}
                >
                  <span className="material-symbols-outlined">warning</span>
                </span>
              ) : alarmState === "ack" ? (
                <span
                  className="device-row-alarm-acked device-row-alarm-pulse--corner"
                  title={t("dashboard.sidebar.alarmAcked")}
                  aria-label={t("dashboard.sidebar.alarmAcked")}
                >
                  <span className="material-symbols-outlined">verified</span>
                </span>
              ) : null}

              {/* Üst satır: durum noktası + cihaz adı */}
              <div className="device-row-top">
                <span
                  className={`device-status-dot ${isOnline ? "online" : "offline"}`}
                  title={isOnline ? t("dashboard.sidebar.online") : t("dashboard.sidebar.offline")}
                />
                <div className="device-row-name">
                  <strong>{device.name}</strong>
                  <span className="device-row-code">{device.code}</span>
                </div>
              </div>

              {/* Orta satır: bölge/hat + batarya yan yana (çerçevesiz) */}
              <div className="device-row-meta-row device-row-meta-row--bare">
                <span className="device-row-location" title={topoLabel}>
                  <span className="material-symbols-outlined">cable</span>
                  {topoLabel}
                </span>
                <span
                  className={`device-battery-chip device-battery-chip--bare ${batteryClass(battPct)}`}
                  title={battPct !== null ? t("dashboard.sidebar.battery", { value: Math.round(battPct) }) : t("dashboard.sidebar.noBattery")}
                >
                  <span className="device-battery-icon" aria-hidden="true">
                    <span className="device-battery-fill" style={{ width: `${battPct ?? 0}%` }} />
                  </span>
                  <span className="device-battery-text">
                    {battPct !== null ? `%${Math.round(battPct)}` : "—"}
                  </span>
                </span>
              </div>

              {/* Alt satır: tarih + saat (sade) */}
              <div className="device-row-last-line">
                <span className="material-symbols-outlined">schedule</span>
                <span className="device-row-last-text">
                  {device.lastUpdateAt
                    ? new Date(device.lastUpdateAt).toLocaleString(undefined, {
                        day: "2-digit",
                        month: "2-digit",
                        year: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit"
                      })
                    : "—"}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
