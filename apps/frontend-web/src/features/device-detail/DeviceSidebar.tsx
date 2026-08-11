/**
 * DeviceSidebar — cihaz detay sol sabit panel.
 *
 * Cihaz kimlik (kod solunda online/offline nokta) + birlesik BILGILER (bolge/
 * hat/IP/pil/RSSI/kalite/seri no'lar) + mini harita (topoloji konumu) + kanal
 * secimi (seri no'lu). activeSource sidebar'dan kontrol edilir.
 */

import { useTranslation } from "react-i18next";
import { MapContainer, Marker, Tooltip } from "react-leaflet";
import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import { MAP_LAYERS } from "../../shared/mapTiles";
import L from "leaflet";

import { formatRelative } from "../../shared/format";
import { sourceLabel, sourceTone } from "../signals/signalCatalogConstants";
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
  /** FIZIKSEL kit kaydi — cihaz bir Pole Master Kit SETI ise dolu.
   *
   *  Setin kendi haberlesmesi, modemi ve RTU pili YOKTUR: uc set tek bir
   *  DNP3 outstation'in (kitin) arkasindadir. Bu yuzden durum/son iletisim/
   *  pil/sebeke sinyali KIT kaydindan okunur; setin kendi kaydindaki
   *  degerler kitten kopyalanmis ya da hic dolmamis alanlardi ve sahada
   *  "set cevrimici ama kit degil" gibi imkansiz durumlar gosteriyordu. */
  parentDevice?: DeviceRow;
  topologyInfo?: TopologyInfo;
  /** RSSI (master.modem_rssi). */
  rssi?: number;
  /** Master IP (master.ipv4_address). */
  ip?: string;
  /** Part No (master.info_part_no). */
  partNo?: string;
  /** Firmware surumu (master.firmware_version / info_fw_version). */
  firmware?: string;
  /** Gercek alarm listesinden: aktif (giderilmemis) alarm var mi. */
  hasAlarm?: boolean;
  /** Kanal seri no'lari (master/sat01/sat02 serial_number). */
  channelSerials?: Partial<Record<SignalSource, string>>;
  /** Kanal pil yuzdeleri (0..100) — her cihazin ayri pil seviyesi. */
  channelBattery?: Partial<Record<SignalSource, number>>;
  activeSource: SignalSource;
  onSourceChange: (s: SignalSource) => void;
  /** Her kaynaktaki sinyal sayisi (0 ise kanal disabled). */
  sourceCounts: Record<SignalSource, number>;
};

/** Cihazin OLCUM YAPAN unite kanallari — MODELE gore.
 *
 * Horstmann SN 2.0'da ucuncu unite ANA unitedir (`master` olcum yapar).
 * Pole Master Kit'in bir SETINDE ise ucu de uydudur: kitin `master`i ortak
 * RTU'dur, bir faza kelepcelenmez ve setin telemetrisinde `master.*` HIC
 * YOKTUR. Sabit liste kullansaydik set acildiginda hep bos bir "Master"
 * kanali gorunur, gercek ucuncu unite (`sat03`) ise hic gorunmezdi.
 * Kit seviyesindeki degerler ayri bir "Pole Master" sekmesinde.
 */
function channelsFor(device: DeviceRow): { key: SignalSource; label: string; tone: string }[] {
  const isSet = (device.parentDeviceId ?? null) !== null;
  const keys: SignalSource[] = isSet
    ? ["sat01", "sat02", "sat03"]
    : ["master", "sat01", "sat02"];
  return keys.map((key) => ({ key, label: sourceLabel(key), tone: sourceTone(key) }));
}

// RSSI -> sebeke sinyali (dBm). -70 ust iyi, -85 ust orta, alti zayif.
// bars: 4 kademeli sinyal cubugu (0..4).
function rssiQuality(rssi: number | undefined): {
  key: "good" | "fair" | "poor" | "none";
  dbm: string;
  bars: number;
} {
  if (rssi == null) return { key: "none", dbm: "—", bars: 0 };
  const dbm = `${Math.round(rssi)} dBm`;
  if (rssi >= -70) return { key: "good", dbm, bars: 4 };
  if (rssi >= -85) return { key: "fair", dbm, bars: 3 };
  if (rssi >= -100) return { key: "poor", dbm, bars: 2 };
  return { key: "poor", dbm, bars: 1 };
}

// Pil % -> renk sinifi (ana sayfa ile ayni esikler).
function batteryClass(pct: number): string {
  if (pct <= 20) return "device-battery--critical";
  if (pct <= 50) return "device-battery--low";
  return "device-battery--ok";
}

export function DeviceSidebar({
  device,
  parentDevice,
  topologyInfo,
  rssi,
  ip,
  partNo,
  firmware,
  hasAlarm = false,
  channelSerials,
  channelBattery,
  activeSource,
  onSourceChange,
  sourceCounts,
}: Props) {
  const { t } = useTranslation();
  // Haberlesme/pil/sinyal SAHIBI cihaz: sette kit, sade cihazda kendisi.
  const health = parentDevice ?? device;
  const online = health.communicationStatus === "online";
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
        <div className="device-sidebar-idrow">
          <span
            className={`device-sidebar-statusdot ${online ? "is-online" : "is-offline"}`}
            title={online ? t("deviceDetail.online") : t("deviceDetail.offline")}
            aria-label={online ? t("deviceDetail.online") : t("deviceDetail.offline")}
          />
          <h2 className="device-sidebar-code">{device.name}</h2>
        </div>
        <div className="device-sidebar-name">{device.code}</div>

        {/* Genel alarm durum karti — alarm varsa yanip sonen, yoksa yesil */}
        <div className={`device-sidebar-alarmcard ${hasAlarm ? "is-alarm" : "is-ok"}`}>
          <span className="device-sidebar-alarmcard-icon">
            <span className="material-symbols-outlined">
              {hasAlarm ? "notification_important" : "check_circle"}
            </span>
          </span>
          <div className="device-sidebar-alarmcard-body">
            <span className="device-sidebar-alarmcard-title">
              {hasAlarm ? t("deviceDetail.sidebar.alarmActive") : t("deviceDetail.sidebar.alarmClear")}
            </span>
            <span className="device-sidebar-alarmcard-sub">
              {hasAlarm ? t("deviceDetail.sidebar.alarmActiveSub") : t("deviceDetail.sidebar.alarmClearSub")}
            </span>
          </div>
          {hasAlarm ? <span className="device-sidebar-alarmcard-pulse" aria-hidden="true" /> : null}
        </div>
      </section>

      {/* ---- Birlesik BILGILER (durum ozeti + bilgiler tek yerde) ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">
          {t("deviceDetail.sidebar.info")}
          {/* Degerlerin KIME ait oldugu yazmazsa "set cevrimici" sanilir;
              oysa haberlesen taraf kitin RTU'sudur. */}
          {parentDevice ? (
            <span className="device-sidebar-kicker-tag">
              {t("deviceDetail.sidebar.fromKit", { name: parentDevice.name })}
            </span>
          ) : null}
        </span>
        <ul className="device-sidebar-info">
          {/* UST CIHAZ — set tek basina bir sey ifade etmiyor; hangi kitin
              parcasi oldugu buraya, diger bilgilerle ayni bicimde yazilir.
              BAGLANTI DEGIL: kitin kendi detay sayfasi yok, kit seviyesindeki
              her sey zaten "Pole Master" sekmesinde. */}
          {parentDevice ? (
            <InfoRow
              icon="dns"
              label={t("deviceDetail.sidebar.parentDevice")}
              value={parentDevice.name}
            />
          ) : null}
          <InfoRow
            icon="wifi"
            label={t("deviceDetail.sidebar.deviceStatus")}
            value={online ? t("deviceDetail.online") : t("deviceDetail.offline")}
            tone={online ? "green" : "slate"}
          />
          <InfoRow
            icon="schedule"
            label={t("deviceDetail.sidebar.lastCommShort")}
            value={health.lastUpdateAt ? formatRelative(health.lastUpdateAt) : "—"}
          />
          {topologyInfo?.regionName ? (
            <InfoRow icon="map" label={t("deviceDetail.meta.region")} value={topologyInfo.regionName} />
          ) : null}
          {topologyInfo?.lineName ? (
            <InfoRow icon="timeline" label={t("deviceDetail.meta.line")} value={topologyInfo.lineName} />
          ) : null}
          {ip ? <InfoRow icon="router" label="IP" value={ip} /> : null}
          {partNo ? <InfoRow icon="qr_code_2" label={t("deviceDetail.sidebar.partNo")} value={partNo} /> : null}
          {firmware ? <InfoRow icon="memory" label={t("deviceDetail.sidebar.firmware")} value={firmware} /> : null}

          {/* Pil — ana sayfa batarya sembolu (% ye gore renkli) */}
          <li className="device-sidebar-info-row">
            <span className="material-symbols-outlined">battery_full</span>
            <span className="device-sidebar-info-label">{t("deviceDetail.meta.battery")}</span>
            <span className={`device-sidebar-battery ${batteryClass(health.batteryPercent)}`}>
              <span className="device-battery-icon" aria-hidden="true">
                <span
                  className="device-battery-fill"
                  style={{ width: `${Math.max(0, Math.min(100, health.batteryPercent))}%` }}
                />
              </span>
              <span className="device-sidebar-battery-text">%{Math.round(health.batteryPercent)}</span>
            </span>
          </li>

          {/* Sebeke sinyali — renkli sinyal cubugu */}
          <li className="device-sidebar-info-row">
            <span className="material-symbols-outlined">signal_cellular_alt</span>
            <span className="device-sidebar-info-label">{t("deviceDetail.sidebar.networkSignal")}</span>
            <span className={`device-sidebar-signal sig-${quality.key}`}>
              <span className="device-sidebar-signal-bars" aria-hidden="true">
                {[1, 2, 3, 4].map((b) => (
                  <span key={b} className={`bar${b <= quality.bars ? " on" : ""}`} />
                ))}
              </span>
              <span className="device-sidebar-signal-text">{quality.dbm}</span>
            </span>
          </li>
        </ul>
      </section>

      {/* ---- Kanal secimi (seri no'lu) ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.channel")}</span>
        <ul className="device-sidebar-channels">
          {channelsFor(device).map((ch) => {
            const n = sourceCounts[ch.key] ?? 0;
            const active = activeSource === ch.key;
            const sn = channelSerials?.[ch.key];
            const batt = channelBattery?.[ch.key];
            return (
              <li key={ch.key}>
                <button
                  type="button"
                  className={`device-channel tone-${ch.tone}${active ? " active" : ""}`}
                  onClick={() => onSourceChange(ch.key)}
                  disabled={n === 0}
                >
                  {batt != null ? (
                    <span className={`device-channel-batt ${batteryClass(batt)}`} title={`%${Math.round(batt)}`}>
                      <span className="device-battery-icon" aria-hidden="true">
                        <span
                          className="device-battery-fill"
                          style={{ width: `${Math.max(0, Math.min(100, batt))}%` }}
                        />
                      </span>
                      <span className="device-channel-batt-text">%{Math.round(batt)}</span>
                    </span>
                  ) : null}
                  <span className="device-channel-label">{ch.label}</span>
                  <span className="device-channel-serial">{sn ?? (n === 0 ? "—" : "")}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {/* ---- Konum + mini harita (en alta yapisik) ---- */}
      <section className="device-sidebar-mapsection">
        <div className="device-sidebar-maphead">
          <span className="device-sidebar-kicker">{t("deviceDetail.meta.location")}</span>
          {hasGeo ? (
            <span className="device-sidebar-coords">
              <span className="material-symbols-outlined">my_location</span>
              {(lat as number).toFixed(5)}, {(lon as number).toFixed(5)}
            </span>
          ) : null}
        </div>
        {hasGeo ? (
          <div className="device-sidebar-map">
            <MapContainer
              center={[lat as number, lon as number]}
              zoom={14}
              zoomControl={true}
              dragging={true}
              scrollWheelZoom={true}
              doubleClickZoom={true}
              attributionControl={false}
              style={{ height: "100%", width: "100%" }}
            >
              <ResilientTileLayer layer="osm" maxZoom={MAP_LAYERS[0].maxZoom} />
              <Marker position={[lat as number, lon as number]} icon={DEVICE_PIN}>
                <Tooltip permanent direction="top" offset={[0, -10]} className="device-map-label">
                  {device.code}
                </Tooltip>
              </Marker>
            </MapContainer>
          </div>
        ) : (
          <div className="device-sidebar-nomap">
            <span className="material-symbols-outlined">location_off</span>
            {t("deviceDetail.sidebar.noLocation")}
          </div>
        )}
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
