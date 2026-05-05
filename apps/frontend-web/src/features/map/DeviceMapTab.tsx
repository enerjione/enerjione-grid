import { useEffect, useMemo, useRef } from "react";
import { MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";
import L from "leaflet";

import type { AlarmEvent, DeviceRow, SignalLiveRow } from "../../shared/types";
import type { GridSnapshot } from "../../shared/api";
import { useProjectSettings } from "../../components/ProjectSettingsProvider";
import { locateDevice } from "../../shared/geoLookup";

type Props = {
  devices: DeviceRow[];
  selectedDevice?: DeviceRow;
  onSelectDevice: (deviceId: number) => void;
  /** Canlı sinyal değerleri — Master/Sat01/Sat02 batarya voltajları popup'ta. */
  liveValues?: SignalLiveRow[];
  /** Şebeke topolojisi — anasayfada bölge/hat/direk/segment görselleri için. */
  gridSnapshot?: GridSnapshot | null;
  /** Aktif alarmlar — segment cihazının alarm durumunu hesaplamak için. */
  alarms?: AlarmEvent[];
};

const DEFAULT_LINE_COLOR = "#2563eb";
const FAULT_COLOR = "#ef4444";

const polePin = (label: string, isStart: boolean, isEnd: boolean) => {
  const cls = isStart ? "is-start" : isEnd ? "is-end" : "";
  return L.divIcon({
    className: "grid-pole-leaflet-wrap",
    html: `<div class="grid-pole-pin grid-pole-pin--sm ${cls}"><span>${label}</span></div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10]
  });
};

function FlyToSelected({
  selectedDevice,
  override
}: {
  selectedDevice?: DeviceRow;
  override?: [number, number];
}) {
  const map = useMap();
  const lastFlownIdRef = useRef<number | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      map.invalidateSize();
    }, 120);
    return () => window.clearTimeout(timer);
  }, [map, selectedDevice]);

  useEffect(() => {
    if (!selectedDevice) {
      lastFlownIdRef.current = null;
      return;
    }
    if (lastFlownIdRef.current === selectedDevice.id) return;
    lastFlownIdRef.current = selectedDevice.id;
    const target: [number, number] = override
      ? override
      : [selectedDevice.latitude, selectedDevice.longitude];
    map.flyTo(target, 13, { duration: 0.8 });
  }, [map, selectedDevice, override]);

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

// Lithium pil voltaj-yüzde haritası — Proje Ayarları'ndan override edilebilir.
const DEFAULT_BATTERY_VOLTAGE_FULL = 3.71;
const DEFAULT_BATTERY_VOLTAGE_LOW = 3.4;

function makeVoltageToPercent(low: number, full: number) {
  const span = full - low;
  return (v: number | null | undefined): number | null => {
    if (v === null || v === undefined || !Number.isFinite(v)) return null;
    if (v <= low) return 0;
    if (v >= full) return 100;
    if (span <= 0) return null;
    return Math.round(((v - low) / span) * 100);
  };
}

function batteryClass(percent: number | null): string {
  if (percent === null) return "device-battery--unknown";
  if (percent <= 20) return "device-battery--critical";
  if (percent <= 50) return "device-battery--low";
  return "device-battery--ok";
}

type SourceKey = "master" | "sat01" | "sat02";

const SOURCE_LABEL: Record<SourceKey, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
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

export function DeviceMapTab({ devices, selectedDevice, onSelectDevice, liveValues, gridSnapshot, alarms }: Props) {
  const { settings } = useProjectSettings();
  const battLow = typeof settings.battery_voltage_low === "number" ? settings.battery_voltage_low : DEFAULT_BATTERY_VOLTAGE_LOW;
  const battFull = typeof settings.battery_voltage_full === "number" ? settings.battery_voltage_full : DEFAULT_BATTERY_VOLTAGE_FULL;
  const voltageToPercent = useMemo(() => makeVoltageToPercent(battLow, battFull), [battLow, battFull]);

  // Seçili cihaz için kaynak başına batarya voltajı/yüzdesi
  const sourceBatteries = useMemo(() => {
    if (!selectedDevice || !liveValues) {
      return { master: null, sat01: null, sat02: null } as Record<
        SourceKey,
        { voltage: number | null; percent: number | null } | null
      >;
    }
    const result: Record<SourceKey, { voltage: number | null; percent: number | null } | null> = {
      master: null,
      sat01: null,
      sat02: null
    };
    const targets: { key: SourceKey; signal: string }[] = [
      { key: "master", signal: "master.battery_voltage_satellite" },
      { key: "sat01", signal: "sat01.battery_voltage_satellite" },
      { key: "sat02", signal: "sat02.battery_voltage_satellite" }
    ];
    for (const t of targets) {
      const row = liveValues.find(
        (r) => r.device_id === selectedDevice.id && r.signal_key === t.signal
      );
      if (row) {
        const v = row.value;
        result[t.key] = {
          voltage: typeof v === "number" ? v : null,
          percent: voltageToPercent(typeof v === "number" ? v : null)
        };
      }
    }
    return result;
  }, [selectedDevice, liveValues, voltageToPercent]);

  // ===== Sebeke topolojisi: hatlar + direkler + cihaz segmentleri =====
  // Cihazda aktif (reset edilmemis) alarm var mi? Polyline rengi icin.
  const alarmActiveDeviceIds = useMemo<Set<number>>(() => {
    const s = new Set<number>();
    for (const a of alarms ?? []) {
      if (!a.reset) s.add(a.device_id);
    }
    return s;
  }, [alarms]);

  // Cihaz id -> segment orta noktasi. DB'de eski lat/lon kalsa bile bu override
  // edilir; cihaz dogru hat ustunde gozukur.
  const deviceLocationOverride = useMemo<Map<number, [number, number]>>(() => {
    const m = new Map<number, [number, number]>();
    if (!gridSnapshot) return m;
    const polesById = new Map(gridSnapshot.poles.map((p) => [p.id, p]));
    for (const seg of gridSnapshot.segments) {
      if (!seg.device_id) continue;
      const fp = polesById.get(seg.from_pole_id);
      const tp = polesById.get(seg.to_pole_id);
      if (!fp || !tp) continue;
      m.set(seg.device_id, [
        (fp.latitude + tp.latitude) / 2,
        (fp.longitude + tp.longitude) / 2
      ]);
    }
    return m;
  }, [gridSnapshot]);

  const topology = useMemo(() => {
    if (!gridSnapshot) return null;
    const polesById = new Map(gridSnapshot.poles.map((p) => [p.id, p]));
    const linesById = new Map(gridSnapshot.lines.map((l) => [l.id, l]));
    const regionsById = new Map(gridSnapshot.regions.map((r) => [r.id, r]));

    // Hat bazli polyline: line.id -> [[lat,lon], ...] sequence_no sirali
    const polesByLine = new Map<number, typeof gridSnapshot.poles>();
    for (const p of gridSnapshot.poles) {
      const arr = polesByLine.get(p.line_id) ?? [];
      arr.push(p);
      polesByLine.set(p.line_id, arr);
    }
    const linePolylines: { id: number; positions: [number, number][]; color: string; name: string; regionName: string }[] = [];
    for (const [lineId, poles] of polesByLine) {
      const line = linesById.get(lineId);
      if (!line) continue;
      const region = regionsById.get(line.region_id);
      const sorted = [...poles].sort((a, b) => a.sequence_no - b.sequence_no);
      linePolylines.push({
        id: lineId,
        positions: sorted.map((p) => [p.latitude, p.longitude]),
        color: line.color || region?.color || DEFAULT_LINE_COLOR,
        name: line.name,
        regionName: region?.name ?? ""
      });
    }

    // Cihaz segmentleri: from -> to direklerinden cizilen ekstra polyline.
    // Cihazda alarm varsa kirmizi pulse; sagliklı ise hattin kendi rengiyle ust uste cizilmemesi icin
    // sadece alarmda olan segmentleri vurgu olarak ekle.
    const alarmedSegments: {
      id: number;
      positions: [number, number][];
      midpoint: [number, number];
      device: DeviceRow | undefined;
      lineName: string;
      regionName: string;
      fromSeq: number | null;
      toSeq: number | null;
    }[] = [];
    for (const seg of gridSnapshot.segments) {
      if (!seg.device_id) continue;
      const dev = devices.find((d) => d.id === seg.device_id);
      const isAlarmed = dev ? alarmActiveDeviceIds.has(dev.id) : false;
      if (!isAlarmed) continue;
      const fromPole = polesById.get(seg.from_pole_id);
      const toPole = polesById.get(seg.to_pole_id);
      if (!fromPole || !toPole) continue;
      const line = linesById.get(seg.line_id);
      const region = line ? regionsById.get(line.region_id) : undefined;
      alarmedSegments.push({
        id: seg.id,
        positions: [
          [fromPole.latitude, fromPole.longitude],
          [toPole.latitude, toPole.longitude]
        ],
        midpoint: [
          (fromPole.latitude + toPole.latitude) / 2,
          (fromPole.longitude + toPole.longitude) / 2
        ],
        device: dev,
        lineName: line?.name ?? "",
        regionName: region?.name ?? "",
        fromSeq: seg.from_pole_seq ?? null,
        toSeq: seg.to_pole_seq ?? null
      });
    }

    // Direklerin baslangic/bitis bilgisi
    const polesWithRole: { p: typeof gridSnapshot.poles[number]; isStart: boolean; isEnd: boolean }[] = [];
    for (const [, poles] of polesByLine) {
      const sorted = [...poles].sort((a, b) => a.sequence_no - b.sequence_no);
      sorted.forEach((p, idx) => {
        polesWithRole.push({
          p,
          isStart: idx === 0,
          isEnd: idx === sorted.length - 1
        });
      });
    }

    return { linePolylines, alarmedSegments, polesWithRole };
  }, [gridSnapshot, devices, alarmActiveDeviceIds]);

  return (
    <section className="map-full">
      <div className="world-map-shell">
        <MapContainer className="world-map" center={[39.0, 35.0]} zoom={5} scrollWheelZoom>
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          <FlyToSelected
            selectedDevice={selectedDevice}
            override={selectedDevice ? deviceLocationOverride.get(selectedDevice.id) : undefined}
          />
          <MapInvalidator deps={[devices.length]} />

          {/* Hat polylineları (sağlıklı): bölge rengi ince çizgi */}
          {topology?.linePolylines.map((line) => (
            <Polyline
              key={`line-${line.id}`}
              positions={line.positions}
              pathOptions={{
                color: line.color,
                weight: 3,
                opacity: 0.7
              }}
            >
              <Tooltip sticky>
                <strong>{line.name}</strong>
                {line.regionName ? <><br />{line.regionName}</> : null}
              </Tooltip>
            </Polyline>
          ))}

          {/* Alarmlı segmentler: kırmızı kalın overlay */}
          {topology?.alarmedSegments.map((seg) => (
            <Polyline
              key={`alarm-seg-${seg.id}`}
              positions={seg.positions}
              pathOptions={{
                color: FAULT_COLOR,
                weight: 6,
                opacity: 0.9,
                className: "grid-segment-alarm-line"
              }}
              eventHandlers={{
                click: () => seg.device && onSelectDevice(seg.device.id)
              }}
            >
              <Tooltip sticky>
                <strong style={{ color: FAULT_COLOR }}>⚠ ARIZA</strong>
                <br />
                {seg.regionName ? `${seg.regionName} · ` : ""}{seg.lineName}
                {seg.fromSeq !== null && seg.toSeq !== null ? (
                  <><br />Direk #{seg.fromSeq} → #{seg.toSeq}</>
                ) : null}
                {seg.device ? <><br />Cihaz: <strong>{seg.device.name}</strong> ({seg.device.code})</> : null}
              </Tooltip>
            </Polyline>
          ))}

          {/* Direkler: küçük numara etiketli pin */}
          {topology?.polesWithRole.map(({ p, isStart, isEnd }) => (
            <Marker
              key={`pole-${p.id}`}
              position={[p.latitude, p.longitude]}
              icon={polePin(String(p.sequence_no), isStart, isEnd)}
              eventHandlers={{}}
            >
              <Tooltip>
                {p.name ?? `Direk #${p.sequence_no}`}
                {isStart ? " (BAŞ)" : isEnd ? " (SON)" : ""}
              </Tooltip>
            </Marker>
          ))}

          {devices.map((device) => {
            const override = deviceLocationOverride.get(device.id);
            const position: [number, number] = override
              ? override
              : [device.latitude, device.longitude];
            return (
              <Marker
                key={device.id}
                position={position}
                icon={markerIcon(device.communicationStatus)}
                eventHandlers={{
                  click: () => onSelectDevice(device.id)
                }}
              >
                <Tooltip>{device.name}</Tooltip>
              </Marker>
            );
          })}
        </MapContainer>

        {selectedDevice ? (
          <div className="device-popup-card device-popup-card--modern">
            <button
              type="button"
              className="device-popup-close"
              onClick={() => onSelectDevice(0)}
              aria-label="Kapat"
            >
              <span className="material-symbols-outlined">close</span>
            </button>

            {/* Üst başlık — alarm + durum + cihaz adı */}
            <header className="device-popup-header">
              <div className="device-popup-title">
                <h4>{selectedDevice.name}</h4>
                <span className="device-popup-code">{selectedDevice.code}</span>
              </div>
              <div className="device-popup-badges">
                {selectedDevice.alarmActive ? (
                  <span className="device-popup-alarm-badge" title="Aktif alarm var">
                    <span className="material-symbols-outlined">warning</span>
                    Alarm
                  </span>
                ) : null}
                <span
                  className={`device-popup-status ${
                    selectedDevice.communicationStatus === "online" ? "online" : "offline"
                  }`}
                  title={selectedDevice.communicationStatus === "online" ? "Çevrimiçi" : "Çevrimdışı"}
                >
                  <span className="device-popup-status-dot" />
                  {selectedDevice.communicationStatus === "online" ? "Çevrimiçi" : "Çevrimdışı"}
                </span>
              </div>
            </header>

            {/* Bilgi satırı: konum + son veri */}
            <div className="device-popup-info">
              <div className="device-popup-info-item">
                <span className="material-symbols-outlined">place</span>
                <div>
                  <span className="device-popup-info-label">Konum</span>
                  <span className="device-popup-info-value">
                    {locateDevice(selectedDevice.latitude, selectedDevice.longitude).label}
                  </span>
                </div>
              </div>
              <div className="device-popup-info-item">
                <span className="material-symbols-outlined">schedule</span>
                <div>
                  <span className="device-popup-info-label">Son veri</span>
                  <span className="device-popup-info-value">
                    {formatRelative(selectedDevice.lastUpdateAt)}
                  </span>
                </div>
              </div>
            </div>

            {/* Master / Sat01 / Sat02 batarya kartları */}
            <div className="device-popup-batteries">
              {(["master", "sat01", "sat02"] as SourceKey[]).map((src) => {
                const data = sourceBatteries[src];
                const pct = data?.percent ?? null;
                const voltage = data?.voltage ?? null;
                return (
                  <div
                    key={src}
                    className={`device-popup-battery-card ${batteryClass(pct)}`}
                    title={voltage !== null ? `${voltage.toFixed(2)} V` : "Veri yok"}
                  >
                    <div className="device-popup-battery-card-head">
                      <span className={`badge badge-source badge-source-${src}`}>
                        {SOURCE_LABEL[src]}
                      </span>
                      {voltage !== null ? (
                        <span className="device-popup-battery-voltage">
                          {voltage.toFixed(2)} V
                        </span>
                      ) : null}
                    </div>
                    <div className="device-popup-battery-bar">
                      <span
                        className="device-popup-battery-fill"
                        style={{ width: `${pct ?? 0}%` }}
                      />
                    </div>
                    <div className="device-popup-battery-percent">
                      {pct !== null ? `%${pct}` : "—"}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
