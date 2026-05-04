import { useMemo } from "react";

import type { AlarmEvent, DeviceRow } from "../../shared/types";
import { locateDevice } from "../../shared/geoLookup";

type Props = {
  devices: DeviceRow[];
  selectedId: number;
  onSelect: (id: number) => void;
  /** Açık alarmları cihaz id'sine göre çözmek için. */
  alarms?: AlarmEvent[];
};

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 5) return "şimdi";
  if (sec < 60) return `${sec} sn önce`;
  if (sec < 3600) return `${Math.round(sec / 60)} dk önce`;
  if (sec < 86400) return `${Math.round(sec / 3600)} sa önce`;
  return d.toLocaleString("tr-TR");
}

function batteryClass(percent: number | null | undefined): string {
  if (percent === null || percent === undefined) return "device-battery--unknown";
  if (percent <= 20) return "device-battery--critical";
  if (percent <= 50) return "device-battery--low";
  return "device-battery--ok";
}

export function DeviceSidebar({ devices, selectedId, onSelect, alarms }: Props) {
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

  return (
    <aside className="sidebar device-sidebar-modern">
      <div className="device-list">
        {devices.length === 0 ? (
          <p className="device-list-empty">Cihaz bulunamadı.</p>
        ) : null}
        {devices.map((device) => {
          const isOnline = device.communicationStatus === "online";
          const battery = device.batteryPercent;
          const battPct = typeof battery === "number" ? Math.max(0, Math.min(100, battery)) : null;
          const location = locateDevice(device.latitude, device.longitude);
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
                  title="Onaylanmamış aktif alarm"
                  aria-label="Onaylanmamış aktif alarm"
                >
                  <span className="material-symbols-outlined">warning</span>
                </span>
              ) : alarmState === "ack" ? (
                <span
                  className="device-row-alarm-acked device-row-alarm-pulse--corner"
                  title="Aktif alarm onaylandı, sürdürülüyor"
                  aria-label="Aktif alarm onaylandı"
                >
                  <span className="material-symbols-outlined">verified</span>
                </span>
              ) : null}

              {/* Üst satır: durum noktası + cihaz adı */}
              <div className="device-row-top">
                <span
                  className={`device-status-dot ${isOnline ? "online" : "offline"}`}
                  title={isOnline ? "Çevrimiçi" : "Çevrimdışı"}
                />
                <div className="device-row-name">
                  <strong>{device.name}</strong>
                  <span className="device-row-code">{device.code}</span>
                </div>
              </div>

              {/* Orta satır: konum + batarya yan yana (çerçevesiz) */}
              <div className="device-row-meta-row device-row-meta-row--bare">
                <span className="device-row-location" title={location.label}>
                  <span className="material-symbols-outlined">place</span>
                  {location.label}
                </span>
                <span
                  className={`device-battery-chip device-battery-chip--bare ${batteryClass(battPct)}`}
                  title={battPct !== null ? `Batarya: %${Math.round(battPct)}` : "Batarya bilgisi yok"}
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
                    ? new Date(device.lastUpdateAt).toLocaleString("tr-TR", {
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
