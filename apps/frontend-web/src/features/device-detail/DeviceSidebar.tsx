/**
 * DeviceSidebar — cihaz detay sol sabit panel.
 *
 * Logo + cihaz kimlik + bilgiler + durum ozeti + kanal secimi + Alarm Reset.
 * Veri prop'lardan (device, topologyInfo, sinyaller). activeSource sidebar'dan
 * kontrol edilir (Master/Sat01/Sat02), overview+trends'i besler.
 */

import { useTranslation } from "react-i18next";
import { MapContainer, Marker, TileLayer } from "react-leaflet";
import L from "leaflet";

import { formatRelative } from "../../shared/format";
import { locateDevice } from "../../shared/geoLookup";
import type { DeviceRow, SignalSource } from "../../shared/types";

// Cihaz pin ikonu (Leaflet divIcon — material-symbols yerine basit daire).
const DEVICE_PIN = L.divIcon({
  className: "device-map-pin",
  html: '<span class="device-map-pin-dot"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

type TopologyInfo = { regionName: string; lineName: string } | undefined;

type Props = {
  device: DeviceRow;
  topologyInfo?: TopologyInfo;
  /** RSSI (master.modem_rssi) — string sinyal degeri getirici. */
  rssi?: number;
  /** Seri no (master.serial_number) — cihaz kimligi altinda. */
  serial?: string;
  /** Ek teknik bilgiler (IP/IMEI/firmware/modem) — string sinyallerden. */
  extraInfo?: { icon: string; label: string; value: string }[];
  activeSource: SignalSource;
  onSourceChange: (s: SignalSource) => void;
  /** Her kaynaktaki sinyal sayisi (0 ise kanal disabled). */
  sourceCounts: Record<SignalSource, number>;
  onOtherActions?: () => void;
};

const CHANNELS: { key: SignalSource; label: string; icon: string; tone: string }[] = [
  { key: "master", label: "Master", icon: "dns", tone: "master" },
  { key: "sat01", label: "Satellite 01", icon: "settings_input_antenna", tone: "green" },
  { key: "sat02", label: "Satellite 02", icon: "settings_input_antenna", tone: "amber" },
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
  serial,
  extraInfo,
  activeSource,
  onSourceChange,
  sourceCounts,
  onOtherActions,
}: Props) {
  const { t } = useTranslation();
  const online = device.communicationStatus === "online";
  const loc = locateDevice(device.latitude, device.longitude);
  const quality = rssiQuality(rssi);
  const hasGeo =
    Number.isFinite(device.latitude) &&
    Number.isFinite(device.longitude) &&
    !(device.latitude === 0 && device.longitude === 0);

  return (
    <aside className="device-sidebar">
      {/* ---- Cihaz kimlik ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.device")}</span>
        <h2 className="device-sidebar-code">{device.code}</h2>
        <div className="device-sidebar-name">{device.name}</div>
        {serial ? (
          <div className="device-sidebar-serial">
            <span className="material-symbols-outlined">tag</span>
            {t("deviceDetail.sidebar.serial")}: <strong>{serial}</strong>
          </div>
        ) : null}
        <div className={`device-sidebar-status ${online ? "is-online" : "is-offline"}`}>
          <span className="device-sidebar-dot" aria-hidden="true" />
          {online ? t("deviceDetail.online") : t("deviceDetail.offline")}
        </div>
        <div className="device-sidebar-lastcomm">
          {t("deviceDetail.sidebar.lastComm")}{" "}
          <strong>{device.lastUpdateAt ? formatRelative(device.lastUpdateAt) : "—"}</strong>
        </div>
      </section>

      {/* ---- Bilgiler ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.info")}</span>
        <ul className="device-sidebar-info">
          {topologyInfo?.regionName ? (
            <InfoRow icon="map" label={t("deviceDetail.meta.region")} value={topologyInfo.regionName} />
          ) : null}
          {topologyInfo?.lineName ? (
            <InfoRow icon="timeline" label={t("deviceDetail.meta.line")} value={topologyInfo.lineName} />
          ) : null}
          <InfoRow
            icon="battery_full"
            label={t("deviceDetail.meta.battery")}
            value={`%${Math.round(device.batteryPercent)}`}
          />
          <InfoRow icon="cell_tower" label="RSSI" value={quality.dbm} />
          <InfoRow icon="location_on" label={t("deviceDetail.meta.location")} value={loc.label} />
          {extraInfo?.map((it) => (
            <InfoRow key={it.label} icon={it.icon} label={it.label} value={it.value} />
          ))}
        </ul>
        {hasGeo ? (
          <div className="device-sidebar-map">
            <MapContainer
              center={[device.latitude, device.longitude]}
              zoom={13}
              zoomControl={false}
              dragging={false}
              scrollWheelZoom={false}
              doubleClickZoom={false}
              attributionControl={false}
              style={{ height: "140px", width: "100%" }}
            >
              <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
              <Marker position={[device.latitude, device.longitude]} icon={DEVICE_PIN} />
            </MapContainer>
          </div>
        ) : null}
      </section>

      {/* ---- Durum ozeti ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.statusSummary")}</span>
        <ul className="device-sidebar-summary">
          <SummaryRow
            dot={online ? "green" : "slate"}
            label={t("deviceDetail.sidebar.deviceStatus")}
            value={online ? t("deviceDetail.online") : t("deviceDetail.offline")}
          />
          <SummaryRow
            dot={device.batteryPercent < 20 ? "amber" : "green"}
            label={t("deviceDetail.sidebar.batteryStatus")}
            value={device.batteryPercent < 20 ? t("deviceDetail.sidebar.low") : t("deviceDetail.status.normal")}
          />
          <SummaryRow
            dot={quality.key === "good" ? "green" : quality.key === "fair" ? "amber" : "slate"}
            label={t("deviceDetail.sidebar.signalQuality")}
            value={
              quality.key === "none"
                ? "—"
                : `${t(`deviceDetail.signalQuality.${quality.key}`)} (${quality.dbm})`
            }
          />
        </ul>
      </section>

      {/* ---- Kanal secimi ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.channel")}</span>
        <ul className="device-sidebar-channels">
          {CHANNELS.map((ch) => {
            const n = sourceCounts[ch.key] ?? 0;
            const active = activeSource === ch.key;
            return (
              <li key={ch.key}>
                <button
                  type="button"
                  className={`device-channel tone-${ch.tone}${active ? " active" : ""}`}
                  onClick={() => onSourceChange(ch.key)}
                  disabled={n === 0}
                >
                  <span className="device-channel-label">{ch.label}</span>
                  <span className="material-symbols-outlined">{ch.icon}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {/* ---- Diger Islemler (Alarm Reset ust sekme cubugunda) ---- */}
      {onOtherActions ? (
        <button type="button" className="device-sidebar-more" onClick={onOtherActions}>
          <span className="material-symbols-outlined">more_horiz</span>
          {t("deviceDetail.sidebar.otherActions")}
          <span className="material-symbols-outlined device-sidebar-more-chevron">chevron_right</span>
        </button>
      ) : null}
    </aside>
  );
}

function InfoRow({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <li className="device-sidebar-info-row">
      <span className="material-symbols-outlined">{icon}</span>
      <span className="device-sidebar-info-label">{label}</span>
      <span className="device-sidebar-info-value" title={value}>{value}</span>
    </li>
  );
}

function SummaryRow({ dot, label, value }: { dot: string; label: string; value: string }) {
  return (
    <li className="device-sidebar-summary-row">
      <span className={`device-summary-dot dot-${dot}`} aria-hidden="true" />
      <span className="device-sidebar-summary-label">{label}</span>
      <span className="device-sidebar-summary-value">{value}</span>
    </li>
  );
}
