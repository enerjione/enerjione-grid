/**
 * DeviceSidebar — cihaz detay sol sabit panel.
 *
 * Cihaz kimlik (kod solunda online/offline nokta) + birlesik BILGILER (bolge/
 * hat/IP/pil/RSSI/kalite/seri no'lar) + mini harita (topoloji konumu) + kanal
 * secimi (seri no'lu). activeSource sidebar'dan kontrol edilir.
 */

import { useTranslation } from "react-i18next";
import { MapContainer, Marker, TileLayer } from "react-leaflet";
import L from "leaflet";

import { formatRelative } from "../../shared/format";
import type { DeviceRow, SignalSource } from "../../shared/types";

// Cihaz pin ikonu (Leaflet divIcon).
const DEVICE_PIN = L.divIcon({
  className: "device-map-pin",
  html: '<span class="device-map-pin-dot"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

type TopologyInfo =
  | { regionName: string; lineName: string; latitude?: number; longitude?: number }
  | undefined;

type Props = {
  device: DeviceRow;
  topologyInfo?: TopologyInfo;
  /** RSSI (master.modem_rssi). */
  rssi?: number;
  /** Master IP (master.ipv4_address). */
  ip?: string;
  /** Kanal seri no'lari (master/sat01/sat02 serial_number). */
  channelSerials?: Partial<Record<SignalSource, string>>;
  activeSource: SignalSource;
  onSourceChange: (s: SignalSource) => void;
  /** Her kaynaktaki sinyal sayisi (0 ise kanal disabled). */
  sourceCounts: Record<SignalSource, number>;
};

const CHANNELS: { key: SignalSource; label: string; tone: string }[] = [
  { key: "master", label: "Master", tone: "master" },
  { key: "sat01", label: "Satellite 01", tone: "green" },
  { key: "sat02", label: "Satellite 02", tone: "amber" },
];

// RSSI -> sinyal kalitesi (dBm). -70 ust iyi, -85 ust orta, alti zayif.
function rssiQuality(rssi: number | undefined): { key: "good" | "fair" | "poor" | "none"; dbm: string } {
  if (rssi == null) return { key: "none", dbm: "—" };
  const dbm = `${Math.round(rssi)} dBm`;
  if (rssi >= -70) return { key: "good", dbm };
  if (rssi >= -85) return { key: "fair", dbm };
  return { key: "poor", dbm };
}

export function DeviceSidebar({
  device,
  topologyInfo,
  rssi,
  ip,
  channelSerials,
  activeSource,
  onSourceChange,
  sourceCounts,
}: Props) {
  const { t } = useTranslation();
  const online = device.communicationStatus === "online";
  const quality = rssiQuality(rssi);
  // Konum: cihazin kendi lat/lon'u yoksa topoloji (hat/segment) konumu.
  const validSelf =
    Number.isFinite(device.latitude) &&
    Number.isFinite(device.longitude) &&
    !(device.latitude === 0 && device.longitude === 0);
  const lat = validSelf ? device.latitude : topologyInfo?.latitude;
  const lon = validSelf ? device.longitude : topologyInfo?.longitude;
  const hasGeo =
    lat != null && lon != null && Number.isFinite(lat) && Number.isFinite(lon) && !(lat === 0 && lon === 0);

  return (
    <aside className="device-sidebar">
      {/* ---- Cihaz kimlik (kod solunda durum noktasi) ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.device")}</span>
        <div className="device-sidebar-idrow">
          <span
            className={`device-sidebar-statusdot ${online ? "is-online" : "is-offline"}`}
            title={online ? t("deviceDetail.online") : t("deviceDetail.offline")}
            aria-label={online ? t("deviceDetail.online") : t("deviceDetail.offline")}
          />
          <h2 className="device-sidebar-code">{device.code}</h2>
        </div>
        <div className="device-sidebar-name">{device.name}</div>
        <div className="device-sidebar-lastcomm">
          {t("deviceDetail.sidebar.lastComm")}{" "}
          <strong>{device.lastUpdateAt ? formatRelative(device.lastUpdateAt) : "—"}</strong>
        </div>
      </section>

      {/* ---- Birlesik BILGILER (durum ozeti + bilgiler tek yerde) ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.info")}</span>
        <ul className="device-sidebar-info">
          <InfoRow
            icon="wifi"
            label={t("deviceDetail.sidebar.deviceStatus")}
            value={online ? t("deviceDetail.online") : t("deviceDetail.offline")}
            tone={online ? "green" : "slate"}
          />
          {topologyInfo?.regionName ? (
            <InfoRow icon="map" label={t("deviceDetail.meta.region")} value={topologyInfo.regionName} />
          ) : null}
          {topologyInfo?.lineName ? (
            <InfoRow icon="timeline" label={t("deviceDetail.meta.line")} value={topologyInfo.lineName} />
          ) : null}
          {ip ? <InfoRow icon="router" label="IP" value={ip} /> : null}
          <InfoRow
            icon="battery_full"
            label={t("deviceDetail.meta.battery")}
            value={`%${Math.round(device.batteryPercent)}`}
            tone={device.batteryPercent < 20 ? "amber" : "green"}
          />
          <InfoRow
            icon="cell_tower"
            label={t("deviceDetail.sidebar.signalQuality")}
            value={quality.key === "none" ? "—" : `${t(`deviceDetail.signalQuality.${quality.key}`)} · ${quality.dbm}`}
            tone={quality.key === "good" ? "green" : quality.key === "fair" ? "amber" : "slate"}
          />
        </ul>

        {/* Seri no'lar (master + satellite) */}
        {channelSerials && (channelSerials.master || channelSerials.sat01 || channelSerials.sat02) ? (
          <ul className="device-sidebar-serials">
            {CHANNELS.map((ch) =>
              channelSerials[ch.key] ? (
                <li key={ch.key} className="device-sidebar-serial-row">
                  <span className={`device-sidebar-serial-tag tone-${ch.tone}`}>{ch.label}</span>
                  <span className="device-sidebar-serial-val">{channelSerials[ch.key]}</span>
                </li>
              ) : null
            )}
          </ul>
        ) : null}

        {/* Mini harita (topoloji/cihaz konumu) */}
        {hasGeo ? (
          <div className="device-sidebar-map">
            <MapContainer
              center={[lat as number, lon as number]}
              zoom={14}
              zoomControl={false}
              dragging={false}
              scrollWheelZoom={false}
              doubleClickZoom={false}
              attributionControl={false}
              style={{ height: "150px", width: "100%" }}
            >
              <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
              <Marker position={[lat as number, lon as number]} icon={DEVICE_PIN} />
            </MapContainer>
          </div>
        ) : (
          <div className="device-sidebar-nomap">
            <span className="material-symbols-outlined">location_off</span>
            {t("deviceDetail.sidebar.noLocation")}
          </div>
        )}
      </section>

      {/* ---- Kanal secimi (seri no'lu) ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.channel")}</span>
        <ul className="device-sidebar-channels">
          {CHANNELS.map((ch) => {
            const n = sourceCounts[ch.key] ?? 0;
            const active = activeSource === ch.key;
            const sn = channelSerials?.[ch.key];
            return (
              <li key={ch.key}>
                <button
                  type="button"
                  className={`device-channel tone-${ch.tone}${active ? " active" : ""}`}
                  onClick={() => onSourceChange(ch.key)}
                  disabled={n === 0}
                >
                  <span className="device-channel-label">{ch.label}</span>
                  <span className="device-channel-serial">{sn ?? (n === 0 ? "—" : "")}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>
    </aside>
  );
}

function InfoRow({
  icon,
  label,
  value,
  tone,
}: {
  icon: string;
  label: string;
  value: string;
  tone?: "green" | "amber" | "slate";
}) {
  return (
    <li className="device-sidebar-info-row">
      <span className="material-symbols-outlined">{icon}</span>
      <span className="device-sidebar-info-label">{label}</span>
      <span className={`device-sidebar-info-value${tone ? ` tone-${tone}` : ""}`} title={value}>
        {tone ? <span className={`device-sidebar-info-dot dot-${tone}`} aria-hidden="true" /> : null}
        {value}
      </span>
    </li>
  );
}
