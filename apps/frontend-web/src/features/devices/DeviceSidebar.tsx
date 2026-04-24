import { useMemo, useState } from "react";

import type { DeviceRow } from "../../shared/types";

type Props = {
  devices: DeviceRow[];
  selectedId: number;
  onSelect: (id: number) => void;
};

export function DeviceSidebar({ devices, selectedId, onSelect }: Props) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "online" | "offline" | "warning">("all");

  const filteredDevices = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return devices.filter((device) => {
      const matchSearch = keyword.length === 0 || device.name.toLowerCase().includes(keyword);
      const matchFilter =
        filter === "all" ||
        (filter === "online" && device.communicationStatus === "online") ||
        (filter === "offline" && device.communicationStatus !== "online") ||
        (filter === "warning" && device.alarmActive);
      return matchSearch && matchFilter;
    });
  }, [devices, filter, search]);

  const onlineCount = devices.filter((item) => item.communicationStatus === "online").length;
  const offlineCount = devices.filter((item) => item.communicationStatus !== "online").length;
  const warningCount = devices.filter((item) => item.alarmActive).length;

  return (
    <aside className="sidebar">
      <div className="device-search-wrap">
        <input
          className="device-search-input"
          placeholder="Cihaz ara..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      <div className="device-filter-row">
        <button className={`filter-chip ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>
          Tumu ({devices.length})
        </button>
        <button
          className={`filter-chip ${filter === "online" ? "active" : ""}`}
          onClick={() => setFilter("online")}
        >
          Cevrimici ({onlineCount})
        </button>
        <button
          className={`filter-chip ${filter === "offline" ? "active" : ""}`}
          onClick={() => setFilter("offline")}
        >
          Cevrimdisi ({offlineCount})
        </button>
        <button
          className={`filter-chip ${filter === "warning" ? "active" : ""}`}
          onClick={() => setFilter("warning")}
        >
          Uyari ({warningCount})
        </button>
      </div>
      <div className="device-list">
        {filteredDevices.length === 0 ? <p>Cihaz bulunamadi.</p> : null}
        {filteredDevices.map((device) => (
          <button
            key={device.id}
            className={`device-row ${selectedId === device.id ? "selected" : ""}`}
            onClick={() => onSelect(device.id)}
          >
            <div className="device-row-top">
              <strong>{device.name}</strong>
              <span className={`dot ${device.communicationStatus === "online" ? "online" : "offline"}`} />
            </div>
            <div className="device-row-meta">
              <span>Battery: {device.batteryPercent}%</span>
              <span>Alarm: {device.alarmActive ? "Active" : "Normal"}</span>
            </div>
            {device.lastUpdateAt ? <small>{device.lastUpdateAt}</small> : null}
          </button>
        ))}
      </div>
    </aside>
  );
}
