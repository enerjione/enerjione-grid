import { useEffect } from "react";
import { MapContainer, Marker, TileLayer, Tooltip, useMap } from "react-leaflet";
import L from "leaflet";

import type { DeviceRow } from "../../shared/types";

type Props = {
  devices: DeviceRow[];
  selectedDevice?: DeviceRow;
  onSelectDevice: (deviceId: number) => void;
};

function FlyToSelected({ selectedDevice }: { selectedDevice?: DeviceRow }) {
  const map = useMap();
  useEffect(() => {
    const timer = window.setTimeout(() => {
      map.invalidateSize();
    }, 120);
    return () => window.clearTimeout(timer);
  }, [map, selectedDevice]);

  if (selectedDevice) {
    map.flyTo([selectedDevice.latitude, selectedDevice.longitude], 7, { duration: 0.8 });
  }
  return null;
}

function MapInvalidator({ deps }: { deps: unknown[] }) {
  const map = useMap();
  useEffect(() => {
    const timer = window.setTimeout(() => map.invalidateSize(), 120);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return null;
}

function markerIcon(status: DeviceRow["communicationStatus"]) {
  const color = status === "online" ? "#10b981" : "#ef4444";
  return L.divIcon({
    className: "device-pin-wrapper",
    html: `<span class="device-pin" style="background:${color}"></span>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10]
  });
}

export function DeviceMapTab({ devices, selectedDevice, onSelectDevice }: Props) {
  return (
    <section className="map-full">
      <div className="world-map-shell">
        <MapContainer className="world-map" center={[39.0, 35.0]} zoom={5} scrollWheelZoom>
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          <FlyToSelected selectedDevice={selectedDevice} />
          <MapInvalidator deps={[devices.length]} />
          {devices.map((device) => (
            <Marker
              key={device.id}
              position={[device.latitude, device.longitude]}
              icon={markerIcon(device.communicationStatus)}
              eventHandlers={{
                click: () => onSelectDevice(device.id)
              }}
            >
              <Tooltip>{device.name}</Tooltip>
            </Marker>
          ))}
        </MapContainer>

        {selectedDevice ? (
          <div className="device-popup-card">
            <button className="close-popup" onClick={() => onSelectDevice(0)}>
              x
            </button>
            <h4>Cihaz Detayları</h4>
            <p>
              <strong>Cihaz:</strong> {selectedDevice.name}
            </p>
            <p>
              <strong>Durum:</strong> {selectedDevice.communicationStatus}
            </p>
            <p>
              <strong>Batarya:</strong> %{selectedDevice.batteryPercent}
            </p>
            <p>
              <strong>Alarm:</strong> {selectedDevice.alarmActive ? "Uyarı" : "Normal"}
            </p>
            <p>
              <strong>Konum:</strong> {selectedDevice.latitude.toFixed(4)}, {selectedDevice.longitude.toFixed(4)}
            </p>
            {selectedDevice.lastUpdateAt ? (
              <p>
                <strong>Son Güncelleme:</strong> {selectedDevice.lastUpdateAt}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
