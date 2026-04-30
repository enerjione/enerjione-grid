import type { DeviceRow } from "../../shared/types";
import { locateDevice } from "../../shared/geoLookup";

type Props = {
  devices: DeviceRow[];
  selectedId: number;
  onSelect: (id: number) => void;
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

export function DeviceSidebar({ devices, selectedId, onSelect }: Props) {
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
          return (
            <button
              key={device.id}
              className={`device-row ${selectedId === device.id ? "selected" : ""} ${
                isOnline ? "device-row--online" : "device-row--offline"
              } ${device.alarmActive ? "device-row--alarm" : ""}`}
              onClick={() => onSelect(device.id)}
            >
              <div className="device-row-top">
                <span
                  className={`device-status-dot ${isOnline ? "online" : "offline"}`}
                  title={isOnline ? "Çevrimiçi" : "Çevrimdışı"}
                />
                <div className="device-row-name">
                  <strong>{device.name}</strong>
                  <span className="device-row-code">{device.code}</span>
                </div>
                <div
                  className={`device-battery-mini ${batteryClass(battPct)}`}
                  title={battPct !== null ? `Batarya: %${Math.round(battPct)}` : "Batarya bilgisi yok"}
                >
                  <span className="device-battery-icon" aria-hidden="true">
                    <span className="device-battery-fill" style={{ width: `${battPct ?? 0}%` }} />
                  </span>
                  <span className="device-battery-text">
                    {battPct !== null ? `%${Math.round(battPct)}` : "—"}
                  </span>
                </div>
              </div>

              <div className="device-row-foot">
                <span className="device-row-location" title={location.label}>
                  <span className="material-symbols-outlined">place</span>
                  {location.label}
                </span>
                <span className="device-row-last">
                  {device.lastUpdateAt ? formatRelative(device.lastUpdateAt) : "—"}
                </span>
                {device.alarmActive ? (
                  <span className="device-row-alarm-badge" title="Aktif alarm var">
                    <span className="material-symbols-outlined">warning</span>
                    Alarm
                  </span>
                ) : null}
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
