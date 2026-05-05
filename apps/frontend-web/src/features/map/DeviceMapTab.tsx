import { useEffect, useMemo, useRef, useState } from "react";
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

const polePin = (
  label: string,
  isStart: boolean,
  isEnd: boolean,
  poleType?: string
) => {
  const typeCls =
    poleType === "transformer" ? "is-transformer" : "";
  const cls = [
    isStart ? "is-start" : isEnd ? "is-end" : "",
    typeCls
  ].filter(Boolean).join(" ");
  // Trafo direkleri biraz daha buyuk ve sembollu gosterilir.
  const isTrafo = poleType === "transformer";
  const inner = isTrafo
    ? `<span class="grid-pole-symbol" title="Trafo">⚡</span><span class="grid-pole-seq">${label}</span>`
    : `<span>${label}</span>`;
  const size: [number, number] = isTrafo ? [26, 26] : [20, 20];
  return L.divIcon({
    className: "grid-pole-leaflet-wrap",
    html: `<div class="grid-pole-pin grid-pole-pin--sm ${cls}">${inner}</div>`,
    iconSize: size,
    iconAnchor: [size[0] / 2, size[1] / 2]
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

function markerIcon(status: DeviceRow["communicationStatus"], alarmActive: boolean) {
  // Cihaz sembolu: dis halkali, ortada simsek (Horstmann Smart Navigator).
  // Direkten (gri pin) ve sade dot'tan ayirt edici.
  const color = alarmActive ? "#dc2626" : status === "online" ? "#10b981" : "#94a3b8";
  const cls = alarmActive
    ? "is-alarm"
    : status === "online"
      ? "is-online"
      : "is-offline";
  return L.divIcon({
    className: "device-marker-wrap",
    html: `
      <div class="device-marker ${cls}" style="--c:${color}">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path fill="#fff" d="M13 2 4 14h6l-1 8 9-12h-6z"/>
        </svg>
      </div>
    `,
    iconSize: [28, 28],
    iconAnchor: [14, 14]
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
  const [detailModalOpen, setDetailModalOpen] = useState(false);
  // Cihaz değişince modali kapat (yanlışlıkla başka cihazın detayını gösterme)
  useEffect(() => {
    setDetailModalOpen(false);
  }, [selectedDevice?.id]);
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

          {/* Direkler: küçük numara etiketli pin (trafo ise farkli sembol) */}
          {topology?.polesWithRole.map(({ p, isStart, isEnd }) => (
            <Marker
              key={`pole-${p.id}`}
              position={[p.latitude, p.longitude]}
              icon={polePin(String(p.sequence_no), isStart, isEnd, p.pole_type)}
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
            const isAlarmed = alarmActiveDeviceIds.has(device.id);
            return (
              <Marker
                key={device.id}
                position={position}
                icon={markerIcon(device.communicationStatus, isAlarmed)}
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

            <button
              type="button"
              className="device-popup-detail-btn"
              onClick={() => setDetailModalOpen(true)}
            >
              <span className="material-symbols-outlined">read_more</span>
              Tüm detayları göster
            </button>
          </div>
        ) : null}

        {/* Cihaz detay modali — onemli sinyaller */}
        {detailModalOpen && selectedDevice ? (
          <DeviceDetailModal
            device={selectedDevice}
            liveValues={liveValues ?? []}
            alarms={alarms ?? []}
            sourceBatteries={sourceBatteries}
            gridSnapshot={gridSnapshot ?? null}
            onClose={() => setDetailModalOpen(false)}
          />
        ) : null}
      </div>
    </section>
  );
}

// ===================================================================
// Cihaz detay modali — onemli sinyaller, alarmlar, batarya, baglanti
// ===================================================================

// Per-source sinyal seti — her kaynak (Master / Sat01 / Sat02) icin ayni anahtar.
const PER_SOURCE_BINARY: { suffix: string; label: string }[] = [
  { suffix: "overcurrent_tripped", label: "Aşırı akım" },
  { suffix: "delta_i_delta_t_tripped", label: "ΔI/Δt" },
  { suffix: "voltage_loss", label: "Gerilim kaybı" },
  { suffix: "current_loss", label: "Akım kaybı" },
  { suffix: "battery_status", label: "Pil durumu" },
  { suffix: "communication_status", label: "Haberleşme" },
  { suffix: "permanent_fault", label: "Kalıcı arıza" },
  { suffix: "momentary_fault", label: "Geçici arıza" }
];

const PER_SOURCE_ANALOG: { suffix: string; label: string; unit: string }[] = [
  { suffix: "actual_current", label: "Akım", unit: "mA" },
  { suffix: "actual_voltage", label: "Gerilim", unit: "V" },
  { suffix: "average_current", label: "Ort. akım", unit: "mA" },
  { suffix: "conductor_temperature", label: "Sıcaklık", unit: "°C" }
];

const SOURCES: SourceKey[] = ["master", "sat01", "sat02"];

function DeviceDetailModal({
  device,
  liveValues,
  alarms,
  sourceBatteries,
  gridSnapshot,
  onClose
}: {
  device: DeviceRow;
  liveValues: SignalLiveRow[];
  alarms: AlarmEvent[];
  sourceBatteries: Record<
    SourceKey,
    { voltage: number | null; percent: number | null } | null
  >;
  gridSnapshot: GridSnapshot | null;
  onClose: () => void;
}) {
  const deviceRows = liveValues.filter((r) => r.device_id === device.id);
  const valueByKey = new Map(deviceRows.map((r) => [r.signal_key, r]));
  const activeAlarms = alarms.filter((a) => a.device_id === device.id && !a.reset);

  // Topoloji bilgisi: bu cihaz hangi hat / bolge / segment ile bagli?
  const topoInfo = (() => {
    if (!gridSnapshot) return null;
    const seg = gridSnapshot.segments.find((s) => s.device_id === device.id);
    if (!seg) return null;
    const line = gridSnapshot.lines.find((l) => l.id === seg.line_id);
    if (!line) return null;
    const region = gridSnapshot.regions.find((r) => r.id === line.region_id);
    return { regionName: region?.name ?? "—", lineName: line.name, fromSeq: seg.from_pole_seq ?? null, toSeq: seg.to_pole_seq ?? null };
  })();

  const renderColumn = (src: SourceKey) => {
    const data = sourceBatteries[src];
    const battV = data?.voltage ?? null;
    const battP = data?.percent ?? null;
    const battBarCls = battP === null ? "" : battP <= 20 ? "is-low" : battP <= 50 ? "is-mid" : "";

    const permRow = valueByKey.get(`${src}.permanent_fault_counter`);
    const tempRow = valueByKey.get(`${src}.momentary_fault_counter`);
    const permVal = typeof permRow?.value === "number" ? Math.trunc(permRow.value as number) : null;
    const tempVal = typeof tempRow?.value === "number" ? Math.trunc(tempRow.value as number) : null;

    return (
      <div key={src} className={`device-detail-col device-detail-col--${src}`}>
        <header className="device-detail-col-head">
          <span className={`device-detail-col-badge is-${src === "master" ? "master" : src === "sat01" ? "sat1" : "sat2"}`}>
            {SOURCE_LABEL[src]}
          </span>
        </header>

        {/* Batarya — kompakt */}
        <div className="device-detail-col-batt">
          <div className={`device-detail-battery-bar ${battBarCls}`}>
            <span style={{ width: `${battP ?? 0}%` }} />
          </div>
          <div className="device-detail-col-batt-row">
            <span>{typeof battV === "number" ? `${battV.toFixed(2)} V` : "—"}</span>
            <strong>{typeof battP === "number" ? `%${battP}` : "—"}</strong>
          </div>
        </div>

        {/* Ariza sayaclari */}
        <div className="device-detail-col-counters">
          <div className="device-detail-mini-counter is-permanent">
            <span className="material-symbols-outlined">error</span>
            <div>
              <div className="lbl">Kalıcı</div>
              <div className="val">{permVal ?? "—"}</div>
            </div>
          </div>
          <div className="device-detail-mini-counter is-transient">
            <span className="material-symbols-outlined">flash_on</span>
            <div>
              <div className="lbl">Geçici</div>
              <div className="val">{tempVal ?? "—"}</div>
            </div>
          </div>
        </div>

        {/* Olcumler */}
        <div className="device-detail-col-section">
          <div className="device-detail-col-title">
            <span className="material-symbols-outlined">monitoring</span>
            Ölçümler
          </div>
          <div className="device-detail-col-analog">
            {PER_SOURCE_ANALOG.map(({ suffix, label, unit }) => {
              const row = valueByKey.get(`${src}.${suffix}`);
              const v = row?.value;
              const display =
                typeof v === "number" && Number.isFinite(v) ? v.toFixed(2) : "—";
              return (
                <div key={suffix} className="device-detail-col-analog-row">
                  <span className="lbl">{label}</span>
                  <span className="val">
                    {display}
                    <span className="unit"> {row?.unit ?? unit}</span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Durum sinyalleri */}
        <div className="device-detail-col-section">
          <div className="device-detail-col-title">
            <span className="material-symbols-outlined">flag</span>
            Durum
          </div>
          <div className="device-detail-col-binary">
            {PER_SOURCE_BINARY.map(({ suffix, label }) => {
              const row = valueByKey.get(`${src}.${suffix}`);
              if (!row) return null;
              const v = row.value;
              const active = typeof v === "number" ? v !== 0 : false;
              return (
                <div
                  key={suffix}
                  className={`device-detail-col-binary-row ${active ? "is-active" : ""}`}
                  title={`${src}.${suffix}`}
                >
                  <span className="dot" />
                  <span className="lbl">{label}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="device-detail-modal-backdrop" onClick={onClose}>
      <div className="device-detail-modal device-detail-modal--wide" onClick={(e) => e.stopPropagation()}>
        <header className="device-detail-modal-head">
          <div className="device-detail-modal-head-left">
            <span className="label">Cihaz Detayı</span>
            <h3>{device.name}</h3>
            <div className="device-detail-modal-meta">
              <span className="device-code">{device.code}</span>
              {topoInfo ? (
                <>
                  <span className="device-detail-modal-meta-sep">·</span>
                  <span className="device-detail-modal-meta-chip">
                    <span className="material-symbols-outlined">map</span>
                    {topoInfo.regionName}
                  </span>
                  <span className="device-detail-modal-meta-chip is-line">
                    <span className="material-symbols-outlined">cable</span>
                    {topoInfo.lineName}
                  </span>
                  {topoInfo.fromSeq !== null && topoInfo.toSeq !== null ? (
                    <span className="device-detail-modal-meta-chip is-seg">
                      Direk #{topoInfo.fromSeq} → #{topoInfo.toSeq}
                    </span>
                  ) : null}
                </>
              ) : (
                <>
                  <span className="device-detail-modal-meta-sep">·</span>
                  <span className="device-detail-modal-meta-chip is-warn">
                    <span className="material-symbols-outlined">link_off</span>
                    Hat atanmamış
                  </span>
                </>
              )}
            </div>
          </div>
          <button
            type="button"
            className="device-detail-modal-close"
            onClick={onClose}
            aria-label="Kapat"
          >
            ✕
          </button>
        </header>

        {/* Aktif alarmlar — varsa header'in altinda dar bir serit */}
        {activeAlarms.length > 0 ? (
          <div className="device-detail-alert-bar">
            <span className="material-symbols-outlined">warning</span>
            <strong>{activeAlarms.length} aktif alarm</strong>
            <span className="device-detail-alert-list">
              {activeAlarms.slice(0, 3).map((a, idx) => (
                <span key={a.id} className="device-detail-alert-chip">
                  {a.title}
                  {idx < Math.min(activeAlarms.length, 3) - 1 ? " · " : ""}
                </span>
              ))}
              {activeAlarms.length > 3 ? (
                <span className="device-detail-alert-more">+{activeAlarms.length - 3} daha</span>
              ) : null}
            </span>
          </div>
        ) : null}

        {/* 3 sutun: Master + Sat01 + Sat02 */}
        <div className="device-detail-modal-cols">
          {SOURCES.map((src) => renderColumn(src))}
        </div>

        <footer className="device-detail-modal-foot">
          <span className="helper-text">
            Tüm sinyaller için Mühendislik &gt; Canlı Değerler sayfasını kullanın.
          </span>
          <button type="button" className="primary-btn" onClick={onClose}>
            Kapat
          </button>
        </footer>
      </div>
    </div>
  );
}
