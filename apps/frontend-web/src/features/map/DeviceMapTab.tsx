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

// Pole icon CACHE — markerIcon ile ayni nedenle: polling her tick'te
// ayni kombinasyon icin yeni divIcon yaratmasin (DOM re-render +
// olasi flicker).
const _polePinCache = new Map<string, L.DivIcon>();
const polePin = (
  label: string,
  isStart: boolean,
  isEnd: boolean,
  poleType?: string,
  isBranchPoint?: boolean,
  isBranchEntry?: boolean
) => {
  const key = `${label}|${isStart ? 1 : 0}|${isEnd ? 1 : 0}|${poleType ?? ""}|${isBranchPoint ? 1 : 0}|${isBranchEntry ? 1 : 0}`;
  const cached = _polePinCache.get(key);
  if (cached) return cached;
  const typeCls =
    poleType === "transformer" ? "is-transformer" : "";
  const cls = [
    isStart ? "is-start" : isEnd ? "is-end" : "",
    typeCls,
    // Branşman pole iki türlü olabilir:
    //   isBranchPoint = bu direk bir veya birden fazla dalin "kaynagi"dir
    //   isBranchEntry = bu direk bir dalin ilk diregidir (parent'a bagli)
    // Iki sinif birden olabilir (zincir bransman); CSS bunu ele alir.
    isBranchPoint ? "is-branch-point" : "",
    isBranchEntry ? "is-branch-entry" : ""
  ].filter(Boolean).join(" ");
  // Trafo: ic ice cift halka — fiziksel sembol cagrisimi.
  // Numara halka altinda kucuk badge olarak gosterilir.
  const isTrafo = poleType === "transformer";
  const inner = isTrafo
    ? `<span class="grid-trafo-rings" aria-label="Trafo">
         <span class="grid-trafo-ring grid-trafo-ring--outer"></span>
         <span class="grid-trafo-ring grid-trafo-ring--inner"></span>
       </span>
       <span class="grid-pole-seq">${label}</span>`
    : `<span>${label}</span>`;
  // Bransman noktasi ise pin'in ust kosesine kucuk Y-catalli rozet.
  const branchBadge = (isBranchPoint || isBranchEntry)
    ? `<span class="grid-pole-branch-badge" title="Branşman noktası">⑂</span>`
    : "";
  const size: [number, number] = isTrafo ? [36, 36] : [20, 20];
  const icon = L.divIcon({
    className: "grid-pole-leaflet-wrap",
    html: `<div class="grid-pole-pin grid-pole-pin--sm ${cls}">${inner}${branchBadge}</div>`,
    iconSize: size,
    iconAnchor: [size[0] / 2, size[1] / 2]
  });
  _polePinCache.set(key, icon);
  return icon;
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

// Icon CACHE: ayni (status, alarmActive) icin AYNI L.divIcon instance'ini
// dondur. Aksi takdirde polling her 5sn'de yeni icon yarattigindan, marker
// DOM'u re-render olur ve CSS alarm-pulse animasyonu surekli %0'dan
// baslayarak titrer (kullanici sikayeti).
const _markerIconCache = new Map<string, L.DivIcon>();
function markerIcon(status: DeviceRow["communicationStatus"], alarmActive: boolean) {
  const key = `${status}|${alarmActive ? 1 : 0}`;
  const cached = _markerIconCache.get(key);
  if (cached) return cached;
  const color = alarmActive ? "#dc2626" : status === "online" ? "#10b981" : "#94a3b8";
  const cls = alarmActive
    ? "is-alarm"
    : status === "online"
      ? "is-online"
      : "is-offline";
  const icon = L.divIcon({
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
  _markerIconCache.set(key, icon);
  return icon;
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

// Harita uzerinde tiklanan hat / direk icin sag-popup kart bilgisi.
type PoleInfoCard = {
  pole: NonNullable<GridSnapshot["poles"]>[number];
  lineName: string;
  isStart: boolean;
  isEnd: boolean;
  isBranchPoint: boolean;
  childLineNames: string[];
  isBranchEntry: boolean;
  branchParentLineName: string;
};
type LineInfoCard = {
  lineId: number | null;
  name: string;
  regionName: string;
  isFault: boolean;
};

export function DeviceMapTab({ devices, selectedDevice, onSelectDevice, liveValues, gridSnapshot, alarms }: Props) {
  const [detailModalOpen, setDetailModalOpen] = useState(false);
  const [poleInfo, setPoleInfo] = useState<PoleInfoCard | null>(null);
  const [lineInfo, setLineInfo] = useState<LineInfoCard | null>(null);
  // Cihaz değişince modali kapat (yanlışlıkla başka cihazın detayını gösterme)
  useEffect(() => {
    setDetailModalOpen(false);
  }, [selectedDevice?.id]);
  // Cihaz secildiginde diger pop-up kartlarini kapat (UI ust uste binmesin).
  useEffect(() => {
    if (selectedDevice) {
      setPoleInfo(null);
      setLineInfo(null);
    }
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
        // Manuel device_position_t varsa onu kullan; yoksa otomatik dagit.
        const tManual = seg.device_position_t;
        const t = (tManual !== null && tManual !== undefined && tManual >= 0 && tManual <= 1)
          ? tManual
          : (idx + 1) / (total + 1);
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
      lineId: number | null; // null -> bransman baglanti edge'i
      positions: [number, number][];
      color: string;
      kind: "healthy" | "fault";   // saglikli yesil / arizali kirmizi kesik
      name: string;
      regionName: string;
    };
    const linePolylines: LinePart[] = [];
    const HEALTHY_DEFAULT = HEALTHY_FAULT_LINE_COLOR;

    // ============================================================
    //   ARIZA YERI KESTIRIM ALGORITMASI (Graf bazli)
    // ============================================================
    //
    // Horstmann Smart Navigator cihazlari hat boyunca yerlestirilmis
    // FAULT INDICATOR'lerdir. Her cihaz:
    //   RED  (alarm aktif)  : ariza akimini ALGILADI
    //   GREEN (alarm yok)   : ariza akimini ALGILAMADI
    //
    // Ariza yeri = SON RED ile ILK GREEN cihaz arasindaki edge'dir.
    // Sadece bu edge KIRMIZI KESIK cizilir; geri kalan tum hat YESIL.
    //
    // Topoloji bir GRAF'tir:
    //   * node = pole (direk)
    //   * edge = iki ardisik pole arasindaki kablo (hat segmenti)
    //   * edge = bransman baglantisi (parent_pole -> branch_first_pole)
    //
    // Bransman, ana hattin dogal devamidir: parent hattin son alarmli
    // cihazi bransman pole'undan onceyse, bransmana akim hic gelmemis;
    // ama yine de "son RED -> ilk GREEN" geçişi tek bir edge oldugundan
    // sadece o edge kirmizi gosterilir (tum dal degil).
    //
    // ============================================================

    // 1) NODE TIPLERI
    //
    // Cihaz bazli renklendirme icin graf node'larini SADECE
    // direklerle sinirlandirmak yetmez; cihazlar da node olmali ki
    // edge'ler "cihazlar arasi" parcalara bolunsun.
    //
    // Node turleri:
    //   - pole node id  : `p-<poleId>`
    //   - device node id: `d-<deviceId>`
    //
    // Bir slot (pole_a -> pole_b) icinde N cihaz varsa, slot N+1
    // mikro-edge'e bolunur:
    //   pole_a -> dev1 -> dev2 -> ... -> devN -> pole_b
    // Eger N=0 ise tek edge: pole_a -> pole_b.
    //
    // Branshman: parent_pole -> branch_first_pole tek edge (genellikle
    // bu segmentte cihaz yoktur).
    type NodeKind = "pole" | "device";
    type GraphNode = {
      id: string;
      kind: NodeKind;
      pos: [number, number];
      // device node ise: cihazin alarm durumu (RED ise true)
      isRed?: boolean;
      poleId?: number;
      deviceId?: number;
    };
    const nodes = new Map<string, GraphNode>();
    const poleNodeId = (poleId: number) => `p-${poleId}`;
    const deviceNodeId = (deviceId: number) => `d-${deviceId}`;

    // Tum direkleri node olarak kaydet.
    for (const p of gridSnapshot.poles) {
      nodes.set(poleNodeId(p.id), {
        id: poleNodeId(p.id),
        kind: "pole",
        pos: [p.latitude, p.longitude],
        poleId: p.id
      });
    }
    // Tum cihazlari (bir segmente atanmis olanlar) node olarak kaydet.
    // Pozisyon: deviceLocationOverride'tan; yoksa device.lat/lon'undan.
    for (const seg of gridSnapshot.segments) {
      if (!seg.device_id) continue;
      const dev = devices.find((d) => d.id === seg.device_id);
      const pos: [number, number] = deviceLocationOverride.get(seg.device_id)
        ?? (dev ? [dev.latitude, dev.longitude] : [0, 0]);
      nodes.set(deviceNodeId(seg.device_id), {
        id: deviceNodeId(seg.device_id),
        kind: "device",
        pos,
        isRed: alarmActiveDeviceIds.has(seg.device_id),
        deviceId: seg.device_id
      });
    }

    // 2) EDGE'LERI KUR (CIHAZLARI ARALARA YERLESTIREREK)
    //
    // Edge: { id, fromNodeId, toNodeId, positions, lineId, lineName, regionName }
    // Yon: besleme tarafi -> yuk tarafi.
    type Edge = {
      id: string;
      fromNodeId: string;
      toNodeId: string;
      positions: [number, number][];
      lineId: number | null;
      lineName: string;
      regionName: string;
    };
    const edges: Edge[] = [];
    const edgeById = new Map<string, Edge>();
    // Komsu edge'lere from-node uzerinden erisim (besleme yonune gore
    // out-edge'ler).
    const outEdges = new Map<string, string[]>();
    const addEdge = (e: Edge) => {
      edges.push(e);
      edgeById.set(e.id, e);
      const arr = outEdges.get(e.fromNodeId) ?? [];
      arr.push(e.id);
      outEdges.set(e.fromNodeId, arr);
    };

    // Slot bazli (line_id, from_pole_id, to_pole_id) cihazlar (orderInSlot
    // ile sirali). Cihazlar device_position_t'ye gore siralidir; yoksa
    // created_at + id.
    type SegRec = {
      seg: typeof gridSnapshot.segments[number];
      t: number;
    };
    const segsBySlot = new Map<string, SegRec[]>();
    for (const seg of gridSnapshot.segments) {
      const k = `${seg.line_id}|${seg.from_pole_id}|${seg.to_pole_id}`;
      const arr = segsBySlot.get(k) ?? [];
      const t = (seg.device_position_t !== null && seg.device_position_t !== undefined)
        ? seg.device_position_t
        : 0.5;
      arr.push({ seg, t });
      segsBySlot.set(k, arr);
    }
    for (const arr of segsBySlot.values()) {
      arr.sort((a, b) => {
        if (a.t !== b.t) return a.t - b.t;
        const ad = new Date(a.seg.created_at).getTime();
        const bd = new Date(b.seg.created_at).getTime();
        if (ad !== bd) return ad - bd;
        return a.seg.id - b.seg.id;
      });
    }

    // Hat slot'larini sirayla isle: pole_a -> dev1 -> ... -> pole_b
    for (const [lineId, sortedPoles] of sortedPolesByLine) {
      const line = linesById.get(lineId);
      const region = line ? regionsById.get(line.region_id) : undefined;
      for (let i = 0; i < sortedPoles.length - 1; i += 1) {
        const a = sortedPoles[i];
        const b = sortedPoles[i + 1];
        const slotKey = `${lineId}|${a.id}|${b.id}`;
        const slotSegs = (segsBySlot.get(slotKey) ?? []).filter((r) => r.seg.device_id);
        const aPos: [number, number] = [a.latitude, a.longitude];
        const bPos: [number, number] = [b.latitude, b.longitude];
        if (slotSegs.length === 0) {
          // Cihaz yok: tek edge pole_a -> pole_b
          addEdge({
            id: `e-${lineId}-${a.id}-${b.id}-direct`,
            fromNodeId: poleNodeId(a.id),
            toNodeId: poleNodeId(b.id),
            positions: [aPos, bPos],
            lineId,
            lineName: line?.name ?? "",
            regionName: region?.name ?? ""
          });
          continue;
        }
        // Cihazlari sirayla yerleştir: pole_a -> dev1 -> dev2 -> ... -> pole_b
        let prevNodeId = poleNodeId(a.id);
        let prevPos = aPos;
        for (let k = 0; k < slotSegs.length; k += 1) {
          const rec = slotSegs[k];
          const did = rec.seg.device_id!;
          const dNodeId = deviceNodeId(did);
          const dPos = nodes.get(dNodeId)?.pos ?? prevPos;
          addEdge({
            id: `e-${lineId}-${a.id}-${b.id}-d${did}-in`,
            fromNodeId: prevNodeId,
            toNodeId: dNodeId,
            positions: [prevPos, dPos],
            lineId,
            lineName: line?.name ?? "",
            regionName: region?.name ?? ""
          });
          prevNodeId = dNodeId;
          prevPos = dPos;
        }
        // Son cihazdan pole_b'ye kapanis edge'i
        addEdge({
          id: `e-${lineId}-${a.id}-${b.id}-tail`,
          fromNodeId: prevNodeId,
          toNodeId: poleNodeId(b.id),
          positions: [prevPos, bPos],
          lineId,
          lineName: line?.name ?? "",
          regionName: region?.name ?? ""
        });
      }
    }

    // Bransman edge'leri: parent_pole -> branch_first_pole.
    // Genellikle bu kisimda cihaz yok — tek edge.
    for (const [lineId, sorted] of sortedPolesByLine) {
      const line = linesById.get(lineId);
      if (!line || !line.branched_from_pole_id) continue;
      const parentPole = polesById.get(line.branched_from_pole_id);
      const firstPole = sorted[0];
      if (!parentPole || !firstPole) continue;
      const region = regionsById.get(line.region_id);
      addEdge({
        id: `branch-${lineId}`,
        fromNodeId: poleNodeId(parentPole.id),
        toNodeId: poleNodeId(firstPole.id),
        positions: [
          [parentPole.latitude, parentPole.longitude],
          [firstPole.latitude, firstPole.longitude]
        ],
        lineId,
        lineName: line.name,
        regionName: region?.name ?? ""
      });
    }

    // 3) BESLEME KAYNAGI (root) — bransman olmayan hatlarin sequence_no=1 pole'u.
    const rootNodeIds: string[] = [];
    for (const [lineId, sorted] of sortedPolesByLine) {
      const line = linesById.get(lineId);
      if (!line) continue;
      if (!line.branched_from_pole_id && sorted.length > 0) {
        rootNodeIds.push(poleNodeId(sorted[0].id));
      }
    }

    // 4a) Her node icin "subtreeHasGreenDevice" — altta GREEN cihaz var mi?
    // Bu bilgi: state=red iken bir node'a vardiysak ve altta GREEN cihaz YOKSA,
    // ariza bu noktadan sonra gizlidir; pending edge'ler hat sonuna kadar fault.
    const subtreeHasGreenDevice = new Map<string, boolean>();
    {
      type Frame = { nodeId: string; phase: 0 | 1 };
      const stack: Frame[] = rootNodeIds.map((id) => ({ nodeId: id, phase: 0 as 0 | 1 }));
      while (stack.length > 0) {
        const f = stack[stack.length - 1];
        const node = nodes.get(f.nodeId);
        if (!node) {
          stack.pop();
          continue;
        }
        if (f.phase === 0) {
          f.phase = 1;
          const outs = outEdges.get(f.nodeId) ?? [];
          for (const eid of outs) {
            const e = edgeById.get(eid);
            if (!e) continue;
            stack.push({ nodeId: e.toNodeId, phase: 0 });
          }
        } else {
          stack.pop();
          let has = false;
          if (node.kind === "device" && !node.isRed) has = true;
          const outs = outEdges.get(f.nodeId) ?? [];
          for (const eid of outs) {
            const e = edgeById.get(eid);
            if (!e) continue;
            if (subtreeHasGreenDevice.get(e.toNodeId)) {
              has = true;
              break;
            }
          }
          subtreeHasGreenDevice.set(f.nodeId, has);
        }
      }
    }

    // 4) ON HESAPLAMA: Her node icin "subtreeHasRed" — node'un altindaki
    //    (besleme yonunde) subtree'de en az bir RED cihaz var mi?
    //
    // Bu bilgi sayesinde branşman noktalarinda akim hangi dala
    // "ilerledi" sezgisini kullaniriz: RED iceren dal, akimin gittigi
    // koldur. Diger dallar (sadece GREEN veya cihazsiz) ana yolun
    // disindadir; oradaki GREEN cihazlar ariza yolunda DEGIL — yani
    // ana hattaki RED'den o yondeki GREEN'e fault olusturulmamali.
    const subtreeHasRed = new Map<string, boolean>();
    {
      // Iteratif post-order: cocuklarin sonucu hesaplanmadan ebeveyn
      // hesaplanamaz. Stack'te (node, phase) yaklaşimi.
      type Frame = { nodeId: string; phase: 0 | 1 };
      const stack: Frame[] = rootNodeIds.map((id) => ({ nodeId: id, phase: 0 as 0 | 1 }));
      while (stack.length > 0) {
        const f = stack[stack.length - 1];
        const node = nodes.get(f.nodeId);
        if (!node) {
          stack.pop();
          continue;
        }
        if (f.phase === 0) {
          f.phase = 1;
          const outs = outEdges.get(f.nodeId) ?? [];
          for (const eid of outs) {
            const e = edgeById.get(eid);
            if (!e) continue;
            stack.push({ nodeId: e.toNodeId, phase: 0 });
          }
        } else {
          stack.pop();
          // Cocuklarin sonuclarini birlestir
          let has = false;
          if (node.kind === "device" && node.isRed) has = true;
          const outs = outEdges.get(f.nodeId) ?? [];
          for (const eid of outs) {
            const e = edgeById.get(eid);
            if (!e) continue;
            if (subtreeHasRed.get(e.toNodeId)) {
              has = true;
              break;
            }
          }
          subtreeHasRed.set(f.nodeId, has);
        }
      }
    }

    // 5) BESLEME YONUNDE DFS — RED -> GREEN gecisini yakala
    //
    // Mantik: Cihaz RED ise akim ondan gecmis demek; akim son RED
    // cihazdan sonraki bolgede arizaya ugrayip ilk GREEN cihaza
    // ulasamamistir. Yani SON RED ile ILK GREEN arasinda KALAN TUM
    // mikro-edge'ler (cihaz olmayan ara segmentler dahil) fault
    // adayidir.
    //
    // BRANSMAN KURALI: Bir node'un birden fazla out-edge'i varsa
    // (bransman noktasi), state=red iken sadece "subtreeHasRed=true"
    // olan dallar akimin gittigi yolu temsil eder; pending fault
    // arayisi orada devam eder. Diger dallar (sadece GREEN var):
    //   * lastState korunur (state=red propagate edilmez ki dalda
    //     ilk gelen GREEN fault olarak yorumlanmasin),
    //   * dal yine de gezilir (cihazlar isaretlenir, state guncellenir),
    //   * ama pending fault'a yazilmaz.
    // Bu sayede ana hatta RED cihaz varken bir kola dal RED, diger
    // kola GREEN ise: RED dali ariza arar, GREEN dal "akim oraya da
    // ulasti" sayilir ve normal yesil cizilir.
    const faultEdgeIds = new Set<string>();
    {
      type Item = {
        nodeId: string;
        lastState: "red" | "green" | null;
        pendingEdges: string[];
      };
      const stack: Item[] = rootNodeIds.map((id) => ({
        nodeId: id,
        lastState: null,
        pendingEdges: []
      }));
      while (stack.length > 0) {
        const cur = stack.pop()!;
        const outs = outEdges.get(cur.nodeId) ?? [];

        // PATH LEAF: out-edge'i olmayan bir node'a vardiysak ve hala
        // state=red ise, son RED'ten sonra GREEN cihaz GORMEDIK ama
        // path bitti — ariza son RED ile hat ucu arasinda olabilir.
        // Pending'deki tum edge'ler fault olarak isaretlenir.
        if (outs.length === 0 && cur.lastState === "red" && cur.pendingEdges.length > 0) {
          for (const pe of cur.pendingEdges) faultEdgeIds.add(pe);
          continue;
        }

        // BRANSMAN AYRIMI: cur.lastState=red iken birden fazla out-edge
        // varsa, sadece subtreeHasRed=true olan dal pending'i miras alir.
        // Diger dallar pending'i miras almaz; ayrica state'i de korumaz
        // (state=null gibi davranır ki o daldaki GREEN'ler ana yolun
        // RED'inden sonra gelmis ariza adayi sayilmasin).
        let redChildCount = 0;
        if (cur.lastState === "red" && outs.length > 1) {
          for (const eid of outs) {
            const e = edgeById.get(eid);
            if (e && subtreeHasRed.get(e.toNodeId)) redChildCount += 1;
          }
        }

        for (const eid of outs) {
          const e = edgeById.get(eid);
          if (!e) continue;
          const toNode = nodes.get(e.toNodeId);
          if (!toNode) continue;

          // Akim hangi dala gitti?
          //   - state=red ve birden fazla dal varsa: subtreeHasRed olan
          //     dal akimin yoluyla devam eder.
          //   - state=red ve sadece subtreeHasRed=true olan tek bir dal
          //     varsa, GREEN sadece olan dallar "yan dal" sayilir.
          //   - state!=red veya tek dal: normal davran.
          const branchHasRed = subtreeHasRed.get(e.toNodeId) === true;
          const isMainPath =
            cur.lastState !== "red" ||
            outs.length === 1 ||
            redChildCount === 0 || // hicbir dalda RED yoksa hepsi ayni durumda — ana yolu yok say
            branchHasRed;

          // Pending'e edge ekle: sadece state=red ve "ana yol" dali ise.
          const branchPending: string[] =
            cur.lastState === "red" && isMainPath
              ? [...cur.pendingEdges, e.id]
              : isMainPath
                ? [...cur.pendingEdges]
                : []; // yan dal: pending miras almaz

          // Yan dal'a girerken state baslangici: null (yeni bir logical
          // path gibi). Bu sayede yan dalda ilk gelen GREEN fault
          // tetiklemez; ama dalda RED varsa kendi icinde RED->GREEN
          // gecisi yine yakalanir.
          let entryState: "red" | "green" | null = isMainPath ? cur.lastState : null;

          let nextState: "red" | "green" | null = entryState;

          if (toNode.kind === "device") {
            const isRed = !!toNode.isRed;
            if (entryState === "red" && !isRed) {
              for (const pe of branchPending) {
                faultEdgeIds.add(pe);
              }
              branchPending.length = 0;
              nextState = "green";
            } else if (isRed) {
              branchPending.length = 0;
              nextState = "red";
            } else {
              branchPending.length = 0;
              nextState = "green";
            }
          } else {
            // Pole node: state degismez. AMA bu pole'un altinda HIC CIHAZ
            // YOKSA (ne RED ne GREEN) ve state=red ise: ariza bu noktadan
            // sonra bilinmez (cihaz yok ki transition tespit edelim);
            // pending fault'a yazilir. Bu durumda alttaki edge'ler de
            // fault olmali, pending'i sifirlamiyoruz — her edge eklendikce
            // tekrar fault yazilir (Set idempotent).
            const hasRedBelow = subtreeHasRed.get(e.toNodeId) === true;
            const hasGreenBelow = subtreeHasGreenDevice.get(e.toNodeId) === true;
            const noDeviceBelow = !hasRedBelow && !hasGreenBelow;
            if (entryState === "red" && noDeviceBelow) {
              for (const pe of branchPending) {
                faultEdgeIds.add(pe);
              }
            }
          }

          stack.push({
            nodeId: e.toNodeId,
            lastState: nextState,
            pendingEdges: branchPending
          });
        }
      }
    }

    // 5) RENDER: edge'leri polyline olarak ureti.
    for (const e of edges) {
      const isFault = faultEdgeIds.has(e.id);
      linePolylines.push({
        id: `edge-${e.id}`,
        lineId: e.lineId,
        positions: e.positions,
        color: isFault ? FAULT_COLOR : HEALTHY_DEFAULT,
        kind: isFault ? "fault" : "healthy",
        name: e.lineName,
        regionName: e.regionName
      });
    }

    // Uyumluluk icin bos diziler (eski API surekligi).
    const alarmedSegments: {
      id: string;
      positions: [number, number][];
      midpoint: [number, number];
      device: DeviceRow | undefined;
      lineName: string;
      regionName: string;
      fromSeq: number | null;
      toSeq: number | null;
    }[] = [];

    // Hangi direklerden bransman cikiyor? (parent_pole_id -> [child line name])
    const branchChildrenByPole = new Map<number, string[]>();
    for (const [, line] of linesById) {
      if (line.branched_from_pole_id) {
        const arr = branchChildrenByPole.get(line.branched_from_pole_id) ?? [];
        arr.push(line.name);
        branchChildrenByPole.set(line.branched_from_pole_id, arr);
      }
    }

    // Bransman birinci direkleri (her dal hattin ilk diregi) — parent_pole
    // ile cakisir/yakin ise gorseli birlestiririz.
    type BranchEntryInfo = {
      childPoleId: number;
      parentPoleId: number;
      mergedWithParent: boolean; // konum cok yakin mi
      parentLineName: string;
    };
    const branchEntryByChildPole = new Map<number, BranchEntryInfo>();
    // Iki nokta arasi yaklasik metre (kucuk olcekte: 1deg lat ~ 111km)
    const distMeters = (a: { latitude: number; longitude: number }, b: { latitude: number; longitude: number }) => {
      const dLat = (a.latitude - b.latitude) * 111000;
      const dLon = (a.longitude - b.longitude) * 111000 * Math.cos((a.latitude * Math.PI) / 180);
      return Math.sqrt(dLat * dLat + dLon * dLon);
    };
    for (const [lineId, line] of linesById) {
      if (!line.branched_from_pole_id) continue;
      const sorted = sortedPolesByLine.get(lineId);
      const firstPole = sorted?.[0];
      const parentPole = polesById.get(line.branched_from_pole_id);
      if (!firstPole || !parentPole) continue;
      const parentLine = linesById.get(parentPole.line_id);
      branchEntryByChildPole.set(firstPole.id, {
        childPoleId: firstPole.id,
        parentPoleId: parentPole.id,
        mergedWithParent: distMeters(firstPole, parentPole) < 8, // 8m altinda cakisik say
        parentLineName: parentLine?.name ?? ""
      });
    }

    // Direklerin baslangic/bitis bilgisi (sequence_no=1 BAS, en yuksek SON).
    // Ayrica bransman noktasi olup olmadigi ve hangi hatlarin ayrildigi
    // bilgisi UI'da farkli icon ve tooltip uretmek icin tasinir.
    const polesWithRole: {
      p: typeof gridSnapshot.poles[number];
      isStart: boolean;
      isEnd: boolean;
      isBranchPoint: boolean;
      childLineNames: string[];
      lineName: string;
      // Bu pole, baska bir hat'in baslangic diregi mi (bransman dali)?
      isBranchEntry: boolean;
      // Parent ile cakisik/birlesik mi (gorsel olarak gizlenebilir)?
      mergedWithParent: boolean;
      // Bagli oldugu parent line adi (tooltip icin)
      branchParentLineName: string;
    }[] = [];
    for (const [lineId, poles] of polesByLine) {
      const sorted = [...poles].sort((a, b) => a.sequence_no - b.sequence_no);
      const line = linesById.get(lineId);
      sorted.forEach((p, idx) => {
        const children = branchChildrenByPole.get(p.id) ?? [];
        const entry = branchEntryByChildPole.get(p.id);
        polesWithRole.push({
          p,
          isStart: idx === 0,
          isEnd: idx === sorted.length - 1,
          isBranchPoint: children.length > 0,
          childLineNames: children,
          lineName: line?.name ?? "",
          isBranchEntry: !!entry,
          mergedWithParent: entry?.mergedWithParent ?? false,
          branchParentLineName: entry?.parentLineName ?? ""
        });
      });
    }

    // Bransman baglantilari artik linePolylines icinde renklendirilerek
    // ciziliyor; ayri bir dashed-mavi link layer'i artik gerekmez.
    const branchLinks: never[] = [];

    return { linePolylines, alarmedSegments, polesWithRole, branchLinks };
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

          {/* Hat polylineları (her edge bagimsiz):
                - healthy : SOLID YESIL
                - fault   : RED DASHED (sadece son RED ile ilk GREEN
                            arasindaki tek edge)
              Bransman baglantilari da (parent_pole -> branch_first_pole)
              ayni listede normal edge gibi cizilir. */}
          {topology?.linePolylines.map((line) => {
            const isFault = line.kind === "fault";
            return (
              <Polyline
                key={line.id}
                positions={line.positions}
                pathOptions={{
                  color: line.color,
                  weight: isFault ? 5 : 4,
                  opacity: isFault ? 0.9 : 0.85,
                  dashArray: isFault ? "10 6" : undefined
                }}
                eventHandlers={{
                  click: () => {
                    setLineInfo({
                      lineId: line.lineId,
                      name: line.name,
                      regionName: line.regionName,
                      isFault
                    });
                    // Direk veya cihaz pop-up'i acik ise kapat
                    setPoleInfo(null);
                  }
                }}
              >
                <Tooltip sticky>
                  <strong>{line.name}</strong>
                  {line.regionName ? <><br />{line.regionName}</> : null}
                  {isFault ? (
                    <><br /><em style={{ color: FAULT_COLOR }}>Tahmini arıza yeri</em></>
                  ) : null}
                </Tooltip>
              </Polyline>
            );
          })}

          {/* Direkler: numara etiketli pin (trafo / bransman icin ozel rozet).
              Bransman noktalarinda pin uzerinde kucuk catallanma rozeti
              gosterilir; tooltip hover'da ayrintili bilgi verir.
              mergedWithParent: dal'in ilk diregi parent ile cakisik ise
              ayri bir pin gosterilmez (gorsel kalabaligi onler). */}
          {topology?.polesWithRole
            .filter((info) => !info.mergedWithParent)
            .map(({ p, isStart, isEnd, isBranchPoint, childLineNames, lineName, isBranchEntry, branchParentLineName }) => (
            <Marker
              key={`pole-${p.id}`}
              position={[p.latitude, p.longitude]}
              icon={polePin(String(p.sequence_no), isStart, isEnd, p.pole_type, isBranchPoint, isBranchEntry)}
              eventHandlers={{
                click: () => {
                  setPoleInfo({
                    pole: p,
                    lineName,
                    isStart,
                    isEnd,
                    isBranchPoint,
                    childLineNames,
                    isBranchEntry,
                    branchParentLineName
                  });
                }
              }}
            >
              <Tooltip sticky direction="top" offset={[0, -8]}>
                <strong>{p.name ?? `Direk #${p.sequence_no}`}</strong>
                {lineName ? <><br /><span style={{ opacity: 0.75 }}>{lineName}</span></> : null}
                {isStart ? <><br /><em>Hat başı</em></> : isEnd ? <><br /><em>Hat sonu</em></> : null}
                {(isBranchPoint && childLineNames.length > 0) || isBranchEntry ? (
                  <>
                    <br />
                    <strong style={{ color: "#6366f1" }}>Branşman noktası</strong>
                    {isBranchPoint && childLineNames.length > 0 ? (
                      <><br /><span style={{ opacity: 0.85 }}>
                        Ayrılan hat{childLineNames.length > 1 ? "lar" : ""}: {childLineNames.join(", ")}
                      </span></>
                    ) : null}
                    {isBranchEntry && branchParentLineName ? (
                      <><br /><span style={{ opacity: 0.85 }}>Bağlı: {branchParentLineName}</span></>
                    ) : null}
                  </>
                ) : null}
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
                {/* Hover'da sadece cihaz adi — kullanici sade istedi. */}
                <Tooltip direction="top" offset={[0, -10]}>
                  {device.name}
                </Tooltip>
              </Marker>
            );
          })}
        </MapContainer>

        {/* Direk bilgi karti — pin'e tiklaninca sag ust kosede acilir */}
        {poleInfo && !selectedDevice ? (
          <div className="map-info-card map-info-card--pole">
            <button
              type="button"
              className="map-info-card-close"
              onClick={() => setPoleInfo(null)}
              aria-label="Kapat"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
            <header className="map-info-card-header">
              <div className="map-info-card-icon">
                {poleInfo.pole.pole_type === "transformer" ? (
                  <span className="map-info-card-trafo-mini">
                    <span /><span />
                  </span>
                ) : (
                  <span className="material-symbols-outlined">cell_tower</span>
                )}
              </div>
              <div className="map-info-card-title">
                <h4>{poleInfo.pole.name ?? `Direk #${poleInfo.pole.sequence_no}`}</h4>
                <span className="map-info-card-sub">
                  #{poleInfo.pole.sequence_no} · {poleInfo.lineName}
                </span>
              </div>
            </header>
            <ul className="map-info-card-rows">
              <li>
                <span className="map-info-card-label">Tip</span>
                <span className="map-info-card-value">
                  {poleInfo.pole.pole_type === "transformer" ? "Trafo direği" : "Direk"}
                </span>
              </li>
              {poleInfo.isStart || poleInfo.isEnd ? (
                <li>
                  <span className="map-info-card-label">Konum</span>
                  <span className="map-info-card-value">
                    {poleInfo.isStart ? "Hat başı" : "Hat sonu"}
                  </span>
                </li>
              ) : null}
              {poleInfo.isBranchPoint && poleInfo.childLineNames.length > 0 ? (
                <li>
                  <span className="map-info-card-label">Branşman</span>
                  <span className="map-info-card-value" style={{ color: "#6366f1" }}>
                    Ayrılan: {poleInfo.childLineNames.join(", ")}
                  </span>
                </li>
              ) : null}
              {poleInfo.isBranchEntry && poleInfo.branchParentLineName ? (
                <li>
                  <span className="map-info-card-label">Bağlı</span>
                  <span className="map-info-card-value" style={{ color: "#6366f1" }}>
                    {poleInfo.branchParentLineName}
                  </span>
                </li>
              ) : null}
              <li>
                <span className="map-info-card-label">Koordinat</span>
                <span className="map-info-card-value" style={{ fontFamily: "monospace", fontSize: 11 }}>
                  {poleInfo.pole.latitude.toFixed(6)}, {poleInfo.pole.longitude.toFixed(6)}
                </span>
              </li>
            </ul>
          </div>
        ) : null}

        {/* Hat bilgi karti — polyline'a tiklaninca acilir */}
        {lineInfo && !selectedDevice && !poleInfo ? (
          <div className="map-info-card map-info-card--line">
            <button
              type="button"
              className="map-info-card-close"
              onClick={() => setLineInfo(null)}
              aria-label="Kapat"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
            <header className="map-info-card-header">
              <div className="map-info-card-icon">
                <span className="material-symbols-outlined">timeline</span>
              </div>
              <div className="map-info-card-title">
                <h4>{lineInfo.name || "Hat"}</h4>
                {lineInfo.regionName ? (
                  <span className="map-info-card-sub">{lineInfo.regionName}</span>
                ) : null}
              </div>
            </header>
            <ul className="map-info-card-rows">
              {lineInfo.regionName ? (
                <li>
                  <span className="map-info-card-label">Bölge</span>
                  <span className="map-info-card-value">{lineInfo.regionName}</span>
                </li>
              ) : null}
              <li>
                <span className="map-info-card-label">Durum</span>
                <span
                  className="map-info-card-value"
                  style={{ color: lineInfo.isFault ? FAULT_COLOR : HEALTHY_FAULT_LINE_COLOR }}
                >
                  {lineInfo.isFault ? "Tahmini arıza yeri" : "Sağlıklı"}
                </span>
              </li>
            </ul>
          </div>
        ) : null}

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
