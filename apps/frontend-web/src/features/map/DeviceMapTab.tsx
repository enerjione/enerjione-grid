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
  poleType?: string,
  isBranchPoint?: boolean
) => {
  const typeCls =
    poleType === "transformer" ? "is-transformer" : "";
  const cls = [
    isStart ? "is-start" : isEnd ? "is-end" : "",
    typeCls,
    isBranchPoint ? "is-branch-point" : ""
  ].filter(Boolean).join(" ");
  // Trafo direkleri biraz daha buyuk ve sembollu gosterilir.
  const isTrafo = poleType === "transformer";
  const inner = isTrafo
    ? `<span class="grid-pole-symbol" title="Trafo">⚡</span><span class="grid-pole-seq">${label}</span>`
    : `<span>${label}</span>`;
  // Bransman noktasi ise pin'in ust kosesine kucuk Y-catalli rozet.
  const branchBadge = isBranchPoint
    ? `<span class="grid-pole-branch-badge" title="Branşman noktası">⑂</span>`
    : "";
  const size: [number, number] = isTrafo ? [26, 26] : [20, 20];
  return L.divIcon({
    className: "grid-pole-leaflet-wrap",
    html: `<div class="grid-pole-pin grid-pole-pin--sm ${cls}">${inner}${branchBadge}</div>`,
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

    // 4) BESLEME YONUNDE DFS — RED -> GREEN gecisini yakala
    //
    // Mantik: Cihaz RED ise akim ondan gecmis demek; akim son RED
    // cihazdan sonraki bolgede arizaya ugrayip ilk GREEN cihaza
    // ulasamamistir. Yani SON RED ile ILK GREEN arasinda KALAN TUM
    // mikro-edge'ler (cihaz olmayan ara segmentler dahil) fault
    // adayidir.
    //
    // Implementation:
    //   - Her path icin "pendingEdges" listesi tasinir.
    //   - lastState=red iken gezilen her edge pendingEdges'e eklenir.
    //   - Bir GREEN cihaza ulasinca pendingEdges + bu edge -> hepsi
    //     faultEdgeIds'e yazilir; lastState=green.
    //   - Bir RED cihaza ulasinca pendingEdges TEMIZLENIR (akim daha
    //     ileri gitti; eski adaylar saglikli sayilir); lastState=red.
    //   - lastState=null iken edge'ler pending olmaz.
    //   - Pole node geçiş edge'i: state degismez ama state=red ise
    //     pendingEdges'e eklenir.
    //
    // Cycle koruma: visited node seti DFS sirasinda kullanilir; ama
    // DFS state'i path-bagimli oldugundan node'u ziyaret etmek visited
    // ile isaretlenmez (her path'te ayri pendingEdges olabilir).
    // Bunun yerine her edge sadece BIR YONDE (besleme yonunde) gezilir
    // ve outEdges kullanildigindan loop riski yok (graf agac yapida).
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
        for (const eid of outs) {
          const e = edgeById.get(eid);
          if (!e) continue;
          const toNode = nodes.get(e.toNodeId);
          if (!toNode) continue;

          // Bu kola ait (path-bagimsiz kopya) state olusturuluyor.
          let nextState: "red" | "green" | null = cur.lastState;
          // Bu edge'in pending'e dahil olup olmayacagi: state=red ise
          // edge fault adayi olarak biriksin.
          const branchPending = cur.lastState === "red"
            ? [...cur.pendingEdges, e.id]
            : [...cur.pendingEdges];

          if (toNode.kind === "device") {
            const isRed = !!toNode.isRed;
            if (cur.lastState === "red" && !isRed) {
              // Son RED'ten sonra ilk GREEN cihaza geldik:
              // pendingEdges (bu edge dahil) hepsi fault.
              for (const pe of branchPending) {
                faultEdgeIds.add(pe);
              }
              // Pending temizlendi (gecis tamamlandi); state=green.
              branchPending.length = 0;
              nextState = "green";
            } else if (isRed) {
              // RED cihaz: akim daha ileri gitti -> pending sıfırla
              // (eski edge'ler artik saglikli sayilir).
              branchPending.length = 0;
              nextState = "red";
            } else {
              // GREEN cihaz, state=null veya green:
              // pending zaten bos (state=red degildi); bu edge fault degil.
              branchPending.length = 0;
              nextState = "green";
            }
          } else {
            // Pole node: state degismez. branchPending zaten dolduruldu
            // (state=red ise). Bu edge sadece pending'e eklenir, henuz
            // fault degil — ileride GREEN cihaz gelirse fault olur.
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
    }[] = [];
    for (const [lineId, poles] of polesByLine) {
      const sorted = [...poles].sort((a, b) => a.sequence_no - b.sequence_no);
      const line = linesById.get(lineId);
      sorted.forEach((p, idx) => {
        const children = branchChildrenByPole.get(p.id) ?? [];
        polesWithRole.push({
          p,
          isStart: idx === 0,
          isEnd: idx === sorted.length - 1,
          isBranchPoint: children.length > 0,
          childLineNames: children,
          lineName: line?.name ?? ""
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
              gosterilir; tooltip'te hangi hatlarin ayrildigi yazilir. */}
          {topology?.polesWithRole.map(({ p, isStart, isEnd, isBranchPoint, childLineNames, lineName }) => (
            <Marker
              key={`pole-${p.id}`}
              position={[p.latitude, p.longitude]}
              icon={polePin(String(p.sequence_no), isStart, isEnd, p.pole_type, isBranchPoint)}
              eventHandlers={{}}
            >
              <Tooltip
                permanent
                direction="bottom"
                offset={[0, 6]}
                className="grid-pole-label-tip"
              >
                #{p.sequence_no}
              </Tooltip>
              <Tooltip sticky direction="top" offset={[0, -8]}>
                <strong>{p.name ?? `Direk #${p.sequence_no}`}</strong>
                {lineName ? <><br /><span style={{ opacity: 0.75 }}>{lineName}</span></> : null}
                {isStart ? <><br /><em>Hat başı</em></> : isEnd ? <><br /><em>Hat sonu</em></> : null}
                {isBranchPoint && childLineNames.length > 0 ? (
                  <>
                    <br />
                    <strong style={{ color: "#6366f1" }}>Branşman noktası</strong>
                    <br />
                    <span style={{ opacity: 0.85 }}>
                      Ayrılan hat{childLineNames.length > 1 ? "lar" : ""}: {childLineNames.join(", ")}
                    </span>
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
