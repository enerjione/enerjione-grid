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
// Arizali bir hatta arizadan ONCEKI (besleme tarafi) kismi belirgin yesil
// renkle gosterilir — operator hangi bolum saglikli/enerjili oldugunu anlik
// gorebilsin diye. Hattin kendi rengi (mavi/turuncu) yerine bu kullanilir.
const HEALTHY_FAULT_LINE_COLOR = "#16a34a";

// Ariza noktasi marker'i — segment ortasinda buyuk yanip sonen kirmizi simsek.
const faultPin = () =>
  L.divIcon({
    className: "grid-fault-leaflet-wrap",
    html: `<div class="grid-fault-pin"><span class="material-symbols-outlined">bolt</span></div>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17]
  });

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

  // Cihaz id -> slot uzerindeki konum.
  // Coklu cihaz icin: ayni (from, to) ciftine sahip segmentler grupla, sira ile
  // (idx+1)/(n+1) oraninda hat boyunca dagit. Backend ile ayni formul.
  const deviceLocationOverride = useMemo<Map<number, [number, number]>>(() => {
    const m = new Map<number, [number, number]>();
    if (!gridSnapshot) return m;
    const polesById = new Map(gridSnapshot.poles.map((p) => [p.id, p]));
    // Slot anahtari -> bu slot'taki cihazli segmentler (sirali).
    const bySlot = new Map<string, typeof gridSnapshot.segments>();
    for (const seg of gridSnapshot.segments) {
      if (!seg.device_id) continue;
      const key = `${seg.from_pole_id}|${seg.to_pole_id}`;
      const list = bySlot.get(key) ?? [];
      list.push(seg);
      bySlot.set(key, list);
    }
    for (const [key, segs] of bySlot.entries()) {
      const [fromIdStr, toIdStr] = key.split("|");
      const fp = polesById.get(Number(fromIdStr));
      const tp = polesById.get(Number(toIdStr));
      if (!fp || !tp) continue;
      // created_at + id ile sirala (backend ile ayni siralama).
      const sorted = [...segs].sort((a, b) => {
        const ad = new Date(a.created_at).getTime();
        const bd = new Date(b.created_at).getTime();
        if (ad !== bd) return ad - bd;
        return a.id - b.id;
      });
      const total = sorted.length;
      sorted.forEach((seg, idx) => {
        if (!seg.device_id) return;
        const t = (idx + 1) / (total + 1);
        const lat = fp.latitude + (tp.latitude - fp.latitude) * t;
        const lon = fp.longitude + (tp.longitude - fp.longitude) * t;
        m.set(seg.device_id, [lat, lon]);
      });
    }
    return m;
  }, [gridSnapshot]);

  const topology = useMemo(() => {
    if (!gridSnapshot) return null;
    const polesById = new Map(gridSnapshot.poles.map((p) => [p.id, p]));
    const linesById = new Map(gridSnapshot.lines.map((l) => [l.id, l]));
    const regionsById = new Map(gridSnapshot.regions.map((r) => [r.id, r]));

    // Hat bazli polyline: line.id -> [[lat,lon], ...] sequence_no sirali.
    // NOT: Burada hattin ham polyline'ini hesapliyoruz; ariza lokalizasyonu
    // asagida hesaplanip linePolylines uc parcaya ayrilacak (pre/fault/post).
    const polesByLine = new Map<number, typeof gridSnapshot.poles>();
    for (const p of gridSnapshot.poles) {
      const arr = polesByLine.get(p.line_id) ?? [];
      arr.push(p);
      polesByLine.set(p.line_id, arr);
    }
    const sortedPolesByLine = new Map<number, typeof gridSnapshot.poles>();
    for (const [lineId, poles] of polesByLine) {
      const sorted = [...poles].sort((a, b) => a.sequence_no - b.sequence_no);
      sortedPolesByLine.set(lineId, sorted);
    }

    type LinePart = {
      id: string;            // benzersiz key
      lineId: number;
      positions: [number, number][];
      color: string;
      kind: "healthy" | "fault" | "post";  // saglikli / ariza / ariza sonrasi
      name: string;
      regionName: string;
    };
    const linePolylines: LinePart[] = [];
    // Saglikli (alarm yok) hatlar her zaman yesil — kullanici hat rengi
    // secimi sistemden kaldirildi; renk standartlasti.
    const HEALTHY_DEFAULT = HEALTHY_FAULT_LINE_COLOR;

    // ===== Ariza lokalizasyon mantigi =====
    // Cihazlar hat boyunca sirali (slot from_pole_seq, ardindan slot ici sira).
    // Besleme yonu hat baslangicindan (#1) sonuna dogru.
    // Bir cihaz alarm verirse: ondan oncesi enerjili (alarm akimi cihazdan
    // gectiyse demek besleme tarafi saglikli), ariza ise ondan SONRADAKI
    // segmentte gerceklesti.
    // Lokalizasyon: hat bazinda alarm veren CIHAZLARI sirayla bul; "son alarmli
    // cihaz ile bir sonraki segment" arizali olarak isaretlenir.
    //   - Sonraki segment varsa o; yoksa (son cihaz hat ucundaysa) cihazin
    //     kendi segmenti vurgulanir.
    //
    // Bu yaklasim "cihazlar hem kendinden oncekini hem sonrakini kontrol
    // ederek nokta atisi" davranisini saglar:
    //   #1 alarm, #2 alarm, #3 NORMAL  =>  arizali segment = #2-#3 arasi
    //   tum cihazlar alarm             =>  arizali segment = son cihazin kendi konumu
    //   hicbiri alarm degil            =>  hat tamamen yesil

    // Slot icindeki cihaz sirasi icin segmentleri grupla.
    type AnnotatedSeg = {
      seg: typeof gridSnapshot.segments[number];
      orderInSlot: number;  // slot icindeki sira (created_at, id'ye gore)
      fromSeq: number;      // slot fromPole sequence_no
      toSeq: number;        // slot toPole sequence_no
    };
    const lineDevices = new Map<number, AnnotatedSeg[]>();
    {
      // Slot anahtariyla grupla, her slotta sirala.
      const bySlot = new Map<string, typeof gridSnapshot.segments>();
      for (const seg of gridSnapshot.segments) {
        if (!seg.device_id) continue;
        const k = `${seg.line_id}|${seg.from_pole_id}|${seg.to_pole_id}`;
        const arr = bySlot.get(k) ?? [];
        arr.push(seg);
        bySlot.set(k, arr);
      }
      for (const [, segs] of bySlot) {
        const sortedSlot = [...segs].sort((a, b) => {
          const ad = new Date(a.created_at).getTime();
          const bd = new Date(b.created_at).getTime();
          if (ad !== bd) return ad - bd;
          return a.id - b.id;
        });
        sortedSlot.forEach((seg, idx) => {
          const fp = polesById.get(seg.from_pole_id);
          const tp = polesById.get(seg.to_pole_id);
          if (!fp || !tp) return;
          const list = lineDevices.get(seg.line_id) ?? [];
          list.push({
            seg,
            orderInSlot: idx,
            fromSeq: fp.sequence_no,
            toSeq: tp.sequence_no
          });
          lineDevices.set(seg.line_id, list);
        });
      }
      // Hat icinde tum cihazlari, slotlar arasinda fromSeq sirasiyla, slot
      // icinde orderInSlot sirasiyla dizimle.
      for (const [lid, list] of lineDevices) {
        list.sort((a, b) => {
          if (a.fromSeq !== b.fromSeq) return a.fromSeq - b.fromSeq;
          if (a.toSeq !== b.toSeq) return a.toSeq - b.toSeq;
          return a.orderInSlot - b.orderInSlot;
        });
        lineDevices.set(lid, list);
      }
    }

    // Iki nokta arasi oklid uzakligi (lat/lng kucuk olcekte yeterli yaklasim).
    const dist2 = (a: [number, number], b: [number, number]) => {
      const dx = a[0] - b[0];
      const dy = a[1] - b[1];
      return dx * dx + dy * dy;
    };
    // Bir polyline'i belirli bir nokta cevresinde ikiye ayir.
    // Once nokta hangi segmentte (k. ile k+1.) en yakin: o segmenti ikiye keser.
    const splitPolyline = (
      polyline: [number, number][],
      splitAt: [number, number]
    ): { pre: [number, number][]; post: [number, number][] } => {
      if (polyline.length < 2) {
        return { pre: polyline, post: [] };
      }
      let bestK = 0;
      let bestD = Infinity;
      for (let k = 0; k < polyline.length - 1; k += 1) {
        const a = polyline[k];
        const b = polyline[k + 1];
        const mid: [number, number] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        const d = dist2(mid, splitAt);
        if (d < bestD) {
          bestD = d;
          bestK = k;
        }
      }
      const pre: [number, number][] = [...polyline.slice(0, bestK + 1), splitAt];
      const post: [number, number][] = [splitAt, ...polyline.slice(bestK + 1)];
      return { pre, post };
    };

    // Hangi hatta nerede ariza var? (lineId -> { faultMid, ... })
    type FaultInfo = {
      midpoint: [number, number];
      device: DeviceRow | undefined;
      fromSeq: number | null;
      toSeq: number | null;
      // Hat polyline'ini ikiye boldugumuz direk sequence_no'su:
      // bu sequence'a kadar (dahil) PRE (yesil), sonrasi POST (kirmizi).
      // splitSeq = lastAlarmed.toSeq -> son alarmli cihazin bitis diregi.
      // Cihazdan sonraki tum hat ucu etkilenen bolumdur.
      splitSeq: number | null;
    };
    const faultByLine = new Map<number, FaultInfo>();

    // Her hat icin lokalize edilmis tek (veya yok) ariza segmenti.
    const alarmedSegments: {
      id: string; // "fault-<lineId>"
      positions: [number, number][];
      midpoint: [number, number];
      device: DeviceRow | undefined;
      lineName: string;
      regionName: string;
      fromSeq: number | null;
      toSeq: number | null;
    }[] = [];
    for (const [lineId, list] of lineDevices) {
      // Alarmli cihazlarin sira indekslerini bul.
      const alarmedIdx: number[] = [];
      list.forEach((it, i) => {
        if (it.seg.device_id && alarmActiveDeviceIds.has(it.seg.device_id)) {
          alarmedIdx.push(i);
        }
      });
      if (alarmedIdx.length === 0) continue;
      const lastAlarmedIdx = alarmedIdx[alarmedIdx.length - 1];
      const lastAlarmed = list[lastAlarmedIdx];
      const next = list[lastAlarmedIdx + 1]; // sonraki cihaz (varsa)

      const line = linesById.get(lineId);
      const region = line ? regionsById.get(line.region_id) : undefined;

      // Ariza pozisyonu:
      //   next varsa: lastAlarmed cihazi ile next cihazi arasi
      //   next yoksa: lastAlarmed cihazinin kendi konumu (hat ucu)
      const lastDeviceId = lastAlarmed.seg.device_id ?? undefined;
      const lastDevPos = lastDeviceId ? deviceLocationOverride.get(lastDeviceId) : undefined;
      const nextDevPos = next?.seg.device_id ? deviceLocationOverride.get(next.seg.device_id) : undefined;

      let positions: [number, number][];
      let midpoint: [number, number];
      let fromSeqShow: number | null = lastAlarmed.fromSeq;
      let toSeqShow: number | null = lastAlarmed.toSeq;

      if (lastDevPos && nextDevPos) {
        positions = [lastDevPos, nextDevPos];
        midpoint = [
          (lastDevPos[0] + nextDevPos[0]) / 2,
          (lastDevPos[1] + nextDevPos[1]) / 2
        ];
        fromSeqShow = lastAlarmed.fromSeq;
        toSeqShow = next?.toSeq ?? null;
      } else if (lastDevPos) {
        // Hat ucunda kalan alarm — cihazin kendi slot segmenti vurgulanir.
        const fp = polesById.get(lastAlarmed.seg.from_pole_id);
        const tp = polesById.get(lastAlarmed.seg.to_pole_id);
        if (!fp || !tp) continue;
        positions = [
          [fp.latitude, fp.longitude],
          [tp.latitude, tp.longitude]
        ];
        midpoint = lastDevPos;
      } else {
        continue;
      }

      const dev = lastDeviceId ? devices.find((d) => d.id === lastDeviceId) : undefined;
      alarmedSegments.push({
        id: `fault-${lineId}`,
        positions,
        midpoint,
        device: dev,
        lineName: line?.name ?? "",
        regionName: region?.name ?? "",
        fromSeq: fromSeqShow,
        toSeq: toSeqShow
      });
      faultByLine.set(lineId, {
        midpoint,
        device: dev,
        fromSeq: fromSeqShow,
        toSeq: toSeqShow,
        // Son alarmli cihazin baglanti slot'unun BITIS diregi -> kesim noktasi.
        // Bu direge kadar (dahil) yesil; bu direkten sonra (sonraki polyline
        // edge'inden itibaren) kirmizi. Boylece cihazin bagli oldugu segment
        // YESILE dahil kalir; sonraki direk araliklari KIRMIZI olur.
        splitSeq: lastAlarmed.toSeq
      });
    }

    // Hat polyline'larini olustur. Arizali hatlar 3 parcaya ayrilir
    // (pre saglikli yesil + fault kirmizi pulse + post kirmizi sabit);
    // arizasiz hatlar tek parca cizilir (kendi rengiyle).
    for (const [lineId, sortedPoles] of sortedPolesByLine) {
      const line = linesById.get(lineId);
      if (!line) continue;
      const region = regionsById.get(line.region_id);
      const positionsAll: [number, number][] = sortedPoles.map((p) => [p.latitude, p.longitude]);
      if (positionsAll.length < 2) continue;
      const fault = faultByLine.get(lineId);
      if (!fault) {
        linePolylines.push({
          id: `line-${lineId}`,
          lineId,
          positions: positionsAll,
          color: HEALTHY_DEFAULT,
          kind: "healthy",
          name: line.name,
          regionName: region?.name ?? ""
        });
        continue;
      }
      // Arizali: hat'i splitSeq direginde kes.
      //   splitSeq = son alarmli cihazin segment bitis diregi (toSeq) =
      //   arizanin tahmini noktasi.
      //   Hat baslangicindan bu direge kadar olan kisim ALARM VEREN
      //   cihazlari icerir -> KIRMIZI (arizali bolum).
      //   Bu direkten hat ucuna kadar olan kisim alarm vermeyen cihazlar
      //   (akim gelmemis) -> YESIL (saglikli/etkilenmemis bolum).
      const splitSeq = fault.splitSeq;
      let splitIdx = -1;
      if (splitSeq !== null) {
        splitIdx = sortedPoles.findIndex((p) => p.sequence_no === splitSeq);
      }
      // splitSeq bulunamazsa veya hat ucundaysa, midpoint'e gore eski yontem
      // yedek olarak kullanilir.
      let preFault: [number, number][];
      let postFault: [number, number][];
      if (splitIdx >= 0 && splitIdx < positionsAll.length - 1) {
        // preFault (besleme + alarmli cihazlar) = 0..splitIdx dahil
        preFault = positionsAll.slice(0, splitIdx + 1);
        // postFault (alarm vermemis cihazlar) = splitIdx..end
        postFault = positionsAll.slice(splitIdx);
      } else {
        const split = splitPolyline(positionsAll, fault.midpoint);
        preFault = split.pre;
        postFault = split.post;
      }

      // Pre-fault (alarmli bolum) -> KIRMIZI
      if (preFault.length >= 2) {
        linePolylines.push({
          id: `line-${lineId}-fault-side`,
          lineId,
          positions: preFault,
          color: FAULT_COLOR,
          kind: "post",
          name: line.name,
          regionName: region?.name ?? ""
        });
      }
      // Post-fault (alarm vermemis bolum) -> YESIL
      if (postFault.length >= 2) {
        linePolylines.push({
          id: `line-${lineId}-healthy-side`,
          lineId,
          positions: postFault,
          color: HEALTHY_FAULT_LINE_COLOR,
          kind: "healthy",
          name: line.name,
          regionName: region?.name ?? ""
        });
      }
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

          {/* Hat polylineları:
              - healthy/pre kismi: hattin kendi rengi
              - post-fault kismi: kirmizi (ariza sonrasi enerjisiz/etkilenen bolum) */}
          {topology?.linePolylines.map((line) => {
            const isHealthy = line.kind === "healthy";
            return (
            <Polyline
              key={line.id}
              positions={line.positions}
              pathOptions={{
                color: line.color,
                weight: line.kind === "post" ? 5 : isHealthy ? 5 : 3,
                opacity: line.kind === "post" ? 0.85 : isHealthy ? 0.9 : 0.7,
                dashArray: line.kind === "post" ? "10 6" : undefined
              }}
            >
              <Tooltip sticky>
                <strong>{line.name}</strong>
                {line.regionName ? <><br />{line.regionName}</> : null}
                {line.kind === "post" ? (
                  <><br /><em style={{ color: FAULT_COLOR }}>Arıza sonrası — etkilenen bölüm</em></>
                ) : isHealthy ? (
                  <><br /><em style={{ color: HEALTHY_FAULT_LINE_COLOR }}>Sağlıklı / enerjili</em></>
                ) : null}
              </Tooltip>
            </Polyline>
            );
          })}

          {/* Ariza noktasi ikonu (lokalize edilmis nokta — son alarmli cihaz
              ile sonraki cihaz arasinin orta noktasinda).
              NOT: Onceden burada arizali iki cihaz arasini birbirine baglayan
              KIRMIZI POLYLINE da ciziliyordu; kullanici istegine gore o cizgi
              kaldirildi. Hat segmentleri kendisi pre/post olarak renklenir;
              sadece nokta atisi marker'i kalir. */}
          {topology?.alarmedSegments.map((seg) => (
            <Marker
              key={`alarm-pin-${seg.id}`}
              position={seg.midpoint}
              icon={faultPin()}
              eventHandlers={{
                click: () => seg.device && onSelectDevice(seg.device.id)
              }}
            >
              <Tooltip>
                <strong style={{ color: FAULT_COLOR }}>⚠ ARIZA NOKTASI</strong>
                <br />
                {seg.regionName ? `${seg.regionName} · ` : ""}{seg.lineName}
                {seg.fromSeq !== null && seg.toSeq !== null ? (
                  <><br />Direk #{seg.fromSeq} → #{seg.toSeq}</>
                ) : null}
              </Tooltip>
            </Marker>
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
// Durum + ariza yonu sinyalleri tek listede; UI bunlari grupluyor (durum ve yon).
const PER_SOURCE_BINARY: { suffix: string; label: string; group?: "state" | "direction" }[] = [
  { suffix: "overcurrent_tripped", label: "Aşırı akım", group: "state" },
  { suffix: "delta_i_delta_t_tripped", label: "ΔI/Δt", group: "state" },
  { suffix: "voltage_loss", label: "Gerilim kaybı", group: "state" },
  { suffix: "current_loss", label: "Akım kaybı", group: "state" },
  { suffix: "battery_status", label: "Pil durumu", group: "state" },
  { suffix: "communication_status", label: "Haberleşme", group: "state" },
  { suffix: "permanent_fault", label: "Kalıcı arıza", group: "state" },
  { suffix: "momentary_fault", label: "Geçici arıza", group: "state" },
  // Ariza yonu / akis yonu sinyalleri (A=Green, B=Red)
  { suffix: "load_flow_direction_green_a", label: "Akış yönü A (yeşil)", group: "direction" },
  { suffix: "load_flow_direction_red_b", label: "Akış yönü B (kırmızı)", group: "direction" },
  { suffix: "overcurrent_fault_direction_green_a", label: "Aşırı akım arıza yönü A", group: "direction" },
  { suffix: "overcurrent_fault_direction_red_b", label: "Aşırı akım arıza yönü B", group: "direction" },
  { suffix: "delta_i_delta_t_fault_direction_green_a", label: "ΔI/Δt arıza yönü A", group: "direction" },
  { suffix: "delta_i_delta_t_fault_direction_red_b", label: "ΔI/Δt arıza yönü B", group: "direction" }
];

const PER_SOURCE_ANALOG: { suffix: string; label: string; unit: string; group?: "live" | "fault" }[] = [
  { suffix: "actual_current", label: "Akım", unit: "mA", group: "live" },
  { suffix: "actual_voltage", label: "Gerilim", unit: "V", group: "live" },
  { suffix: "average_current", label: "Ort. akım", unit: "mA", group: "live" },
  { suffix: "maximum_current", label: "Max. akım", unit: "mA", group: "live" },
  { suffix: "conductor_temperature", label: "İletken sıc.", unit: "°C", group: "live" },
  { suffix: "device_temperature", label: "Cihaz sıc.", unit: "°C", group: "live" },
  // Ariza ile ilgili degerler — son arizada kaydedilen
  { suffix: "fault_current", label: "Arıza akımı", unit: "mA", group: "fault" },
  { suffix: "fault_duration", label: "Arıza süresi", unit: "ms", group: "fault" },
  { suffix: "last_good_known_current", label: "Son iyi akım", unit: "mA", group: "fault" },
  { suffix: "minimum_current", label: "Min. akım", unit: "mA", group: "fault" }
];

const SOURCES: SourceKey[] = ["master", "sat01", "sat02"];

function DeviceDetailModal({
  device,
  liveValues,
  sourceBatteries,
  gridSnapshot,
  onClose
}: {
  device: DeviceRow;
  liveValues: SignalLiveRow[];
  sourceBatteries: Record<
    SourceKey,
    { voltage: number | null; percent: number | null } | null
  >;
  gridSnapshot: GridSnapshot | null;
  onClose: () => void;
}) {
  const deviceRows = liveValues.filter((r) => r.device_id === device.id);
  const valueByKey = new Map(deviceRows.map((r) => [r.signal_key, r]));

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

    // Batarya seviyesi — sidebar'daki batteryClass mantigi ile ayni:
    // null=unknown, <=20 critical, <=50 low, otherwise ok.
    const battLevelCls =
      battP === null
        ? "device-battery--unknown"
        : battP <= 20
          ? "device-battery--critical"
          : battP <= 50
            ? "device-battery--low"
            : "device-battery--ok";

    const renderAnalogRow = ({
      suffix,
      label,
      unit
    }: { suffix: string; label: string; unit: string }) => {
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
    };

    const renderBinaryRow = ({ suffix, label }: { suffix: string; label: string }) => {
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
    };

    const stateBinary = PER_SOURCE_BINARY.filter((b) => b.group !== "direction");
    const directionBinary = PER_SOURCE_BINARY.filter((b) => b.group === "direction");
    const liveAnalog = PER_SOURCE_ANALOG.filter((a) => a.group !== "fault");
    const faultAnalog = PER_SOURCE_ANALOG.filter((a) => a.group === "fault");

    return (
      <div key={src} className={`device-detail-col device-detail-col--${src}`}>
        <header className="device-detail-col-head">
          <span className={`device-detail-col-badge is-${src === "master" ? "master" : src === "sat01" ? "sat1" : "sat2"}`}>
            {SOURCE_LABEL[src]}
          </span>
          {/* Batarya — sag ust kosede pil ikonu (sidebar ile ayni stil) */}
          <div
            className={`device-battery device-battery-mini ${battLevelCls}`}
            title={typeof battV === "number" ? `${battV.toFixed(2)} V` : "Voltaj —"}
          >
            <span className="device-battery-icon">
              <span
                className="device-battery-fill"
                style={{ width: `${Math.max(0, Math.min(100, battP ?? 0))}%` }}
              />
            </span>
            <span className="device-battery-text">
              {typeof battP === "number" ? `%${battP}` : "—"}
            </span>
          </div>
        </header>

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

        {/* Olcumler — canli */}
        <div className="device-detail-col-section">
          <div className="device-detail-col-title">
            <span className="material-symbols-outlined">monitoring</span>
            Ölçümler
          </div>
          <div className="device-detail-col-analog">
            {liveAnalog.map(renderAnalogRow)}
          </div>
        </div>

        {/* Olcumler — ariza ile ilgili (sadece bu kaynak icin sinyal varsa goster) */}
        {faultAnalog.some(({ suffix }) => valueByKey.has(`${src}.${suffix}`)) ? (
          <div className="device-detail-col-section">
            <div className="device-detail-col-title">
              <span className="material-symbols-outlined">warning</span>
              Arıza Ölçümleri
            </div>
            <div className="device-detail-col-analog">
              {faultAnalog.map(renderAnalogRow)}
            </div>
          </div>
        ) : null}

        {/* Durum sinyalleri */}
        <div className="device-detail-col-section">
          <div className="device-detail-col-title">
            <span className="material-symbols-outlined">flag</span>
            Durum
          </div>
          <div className="device-detail-col-binary">
            {stateBinary.map(renderBinaryRow)}
          </div>
        </div>

        {/* Ariza yonu sinyalleri (sadece bu kaynak icin sinyal varsa goster) */}
        {directionBinary.some(({ suffix }) => valueByKey.has(`${src}.${suffix}`)) ? (
          <div className="device-detail-col-section">
            <div className="device-detail-col-title">
              <span className="material-symbols-outlined">explore</span>
              Arıza Yönü
            </div>
            <div className="device-detail-col-binary">
              {directionBinary.map(renderBinaryRow)}
            </div>
          </div>
        ) : null}

      </div>
    );
  };

  return (
    <div className="device-detail-modal-backdrop" onClick={onClose}>
      <div className="device-detail-modal device-detail-modal--wide" onClick={(e) => e.stopPropagation()}>
        <header className="device-detail-modal-head">
          <div className="device-detail-modal-head-left">
            {/* Tek satir: cihaz adi + kod + topology chip'leri + tum info chip'ler */}
            <div className="device-detail-modal-titlebar">
              <h3 className="device-detail-modal-title">{device.name}</h3>
              <span className="device-detail-modal-titlebar-code">{device.code}</span>
              {topoInfo ? (
                <>
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
                      #{topoInfo.fromSeq} → #{topoInfo.toSeq}
                    </span>
                  ) : null}
                </>
              ) : (
                <span className="device-detail-modal-meta-chip is-warn">
                  <span className="material-symbols-outlined">link_off</span>
                  Hat atanmamış
                </span>
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

        {/* 3 sutun: Master + Sat01 + Sat02 */}
        <div className="device-detail-modal-cols">
          {SOURCES.map((src) => renderColumn(src))}
        </div>
      </div>
    </div>
  );
}
