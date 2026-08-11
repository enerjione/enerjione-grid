import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sourceLabel as ortakKaynakEtiketi } from "../signals/signalCatalogConstants";
import { useTranslation } from "react-i18next";
import { LayersControl, MapContainer, Marker, Polyline, Tooltip, useMap } from "react-leaflet";
import { nearestDeviceRedMap } from "./nearestDeviceRed";
import { DEFAULT_MAP_LAYER, MAP_LAYERS } from "../../shared/mapTiles";
import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import L from "leaflet";

import type { AlarmEvent, DeviceRow, SignalLiveRow } from "../../shared/types";
import { energyBadgeHtml, rolesOf, topologyMeta } from "../grid/poleTypeMeta";
import type { GridSnapshot } from "../../shared/api";
import { MapLayerSwitchFix } from "../../components/MapLayerSwitchFix";
import { useDeviceModelSettings } from "../../components/DeviceModelSettingsProvider";
import { voltageToPercent as voltsToPercent } from "../../shared/battery";
import { locateDevice } from "../../shared/geoLookup";
import { planDeviceFocus } from "./deviceFocus";
import type { FocusPoint } from "./deviceFocus";
import { buildLineDistanceIndex, formatDistanceRange } from "../../shared/lineDistance";

type Props = {
  devices: DeviceRow[];
  selectedDevice?: DeviceRow;
  onSelectDevice: (deviceId: number) => void;
  /** Canlı sinyal değerleri — Master/Sat01/Sat02 batarya voltajları popup'ta. */
  liveValues?: SignalLiveRow[];
  /** Şebeke topolojisi — anasayfada bölge/hat/direk/segment görselleri için. */
  gridSnapshot?: GridSnapshot | null;
  /** Hat Agaci'nda gizlenen hatlar — hat, direkleri ve cihazlari haritada
   *  grilesir. Kaldirilmaz: topoloji gorunur kalsin, sadece geri plana dussun. */
  hiddenLineIds?: Set<number>;
  /** Aktif alarmlar — segment cihazının alarm durumunu hesaplamak için. */
  alarms?: AlarmEvent[];
  /** Verilirse "Tüm detayları göster" popup içi modal yerine cihaz detay
   *  SEKMESI acar (Chrome tarzi sekme sistemi). Verilmezse eski modal davranisi. */
  onOpenDetail?: (deviceId: number) => void;
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
  pole?: { topology_role?: string | null; energy_role?: string | null; pole_type?: string | null },
  isBranchPoint?: boolean,
  isBranchEntry?: boolean
) => {
  const { topo, energy } = rolesOf(pole ?? {});
  const key = `${label}|${isStart ? 1 : 0}|${isEnd ? 1 : 0}|${topo}|${energy}|${isBranchPoint ? 1 : 0}|${isBranchEntry ? 1 : 0}`;
  const cached = _polePinCache.get(key);
  if (cached) return cached;
  // ANA ikon topolojik rolden; enerji rolu kose rozeti (rol modeli).
  const meta = topologyMeta(topo);
  const typeCls = meta.cls;
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
  const inner = meta.symbol
    ? `<span class="grid-pole-symbol" aria-label="${meta.title}">${meta.symbol}</span><span class="grid-pole-seq">${label}</span>`
    : `<span>${label}</span>`;
  // Bransman noktasi ise pin'in ust kosesine kucuk Y-catalli rozet.
  const branchBadge = (isBranchPoint || isBranchEntry)
    ? `<span class="grid-pole-branch-badge" title="Branşman noktası">⑂</span>`
    : "";
  const size: [number, number] = meta.symbol ? [40, 40] : [20, 20];
  const icon = L.divIcon({
    className: "grid-pole-leaflet-wrap",
    html: `<div class="grid-pole-pin grid-pole-pin--sm ${cls}">${inner}${branchBadge}${energyBadgeHtml(energy)}</div>`,
    iconSize: size,
    iconAnchor: [size[0] / 2, size[1] / 2]
  });
  _polePinCache.set(key, icon);
  return icon;
};

/** Leaflet harita ornegini MapContainer DISINA tasir.
 *  react-leaflet'te `useMap` sadece MapContainer'in ICINDE calisir; cevrimdisi
 *  indirme modali "o anki gorunum"u bilmek zorunda oldugu icin ornegi bir kez
 *  yukari veriyoruz. */
function MapRefBridge({ onReady }: { onReady: (map: L.Map) => void }) {
  const map = useMap();
  useEffect(() => {
    onReady(map);
  }, [map, onReady]);
  return null;
}

/**
 * Secili cihaza odaklan — cihazin bagli oldugu HAT ekrana sigacak sekilde.
 *
 * Onceden sabit `flyTo(target, 13)` vardi: kullanici direk seviyesinde
 * (zoom 16-17) calisirken bir cihaza tikladiginda harita UZAKLASIYORDU.
 * "Cihazi goster" eylemi kullanicinin kurdugu yakinligi bozuyordu.
 *
 * Karar `deviceFocus.ts`te ve SAF — testlerle kilitli.
 */
function FlyToSelected({
  selectedDevice,
  override,
  linePoints
}: {
  selectedDevice?: DeviceRow;
  override?: [number, number];
  /** Secili cihazin hattindaki tum noktalar (direkler). */
  linePoints: FocusPoint[];
}) {
  const map = useMap();
  const lastKeyRef = useRef<string>("");
  useEffect(() => {
    const timer = window.setTimeout(() => {
      map.invalidateSize();
    }, 120);
    return () => window.clearTimeout(timer);
  }, [map, selectedDevice]);

  useEffect(() => {
    if (!selectedDevice) {
      lastKeyRef.current = "";
      return;
    }
    const [lat, lng] = override ?? [selectedDevice.latitude, selectedDevice.longitude];
    const plan = planDeviceFocus({
      device: { id: selectedDevice.id, latitude: lat, longitude: lng },
      linePoints,
      lastKey: lastKeyRef.current
    });
    if (plan.kind === "skip") return;
    lastKeyRef.current = plan.key;
    if (plan.kind === "point") {
      map.flyTo([plan.latitude, plan.longitude], plan.zoom, { duration: 0.8 });
      return;
    }
    map.flyToBounds(
      L.latLngBounds(plan.points.map((pt) => L.latLng(pt.latitude, pt.longitude))),
      // Kenar payi: hat tam kenara yapismasin, secili cihazin balonu da
      // ekranda kalsin. maxZoom: kisa bir hatta asiri yakinlasip sokak
      // detayina gomulmeyi onler.
      { padding: [60, 60], maxZoom: 16, duration: 0.8 }
    );
  }, [map, selectedDevice, override, linePoints]);

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


/**
 * Sayfa ilk yüklendiğinde (cihaz / topoloji konum bilgileri geldiği anda)
 * tüm cihaz + direk koordinatlarının bounds'una kamerayı sığdırır. Kullanıcı
 * elle pan/zoom yaptığında veya bir cihaz seçildiğinde tekrar tetiklenmez —
 * yalnız ilk anlamlı koordinat setinde bir kez çalışır.
 */
function AutoFitOnLoad({
  points,
  hasSelection
}: {
  points: Array<[number, number]>;
  hasSelection: boolean;
}) {
  const map = useMap();
  const fittedRef = useRef(false);
  useEffect(() => {
    if (fittedRef.current) return;
    if (hasSelection) return;
    if (points.length === 0) return;
    const bounds = L.latLngBounds(points.map((p) => L.latLng(p[0], p[1])));
    if (!bounds.isValid()) return;
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15, animate: false });
    fittedRef.current = true;
  }, [map, points, hasSelection]);
  return null;
}

// Icon CACHE: ayni (status, alarmActive) icin AYNI L.divIcon instance'ini
// dondur. Aksi takdirde polling her 5sn'de yeni icon yarattigindan, marker
// DOM'u re-render olur ve CSS alarm-pulse animasyonu surekli %0'dan
// baslayarak titrer (kullanici sikayeti).
const _markerIconCache = new Map<string, L.DivIcon>();
function markerIcon(
  status: DeviceRow["communicationStatus"],
  alarmActive: boolean,
  /** Bagli oldugu hat gizlendi — marker grilesir (kaldirilmaz). */
  dimmed = false
) {
  const key = `${status}|${alarmActive ? 1 : 0}|${dimmed ? 1 : 0}`;
  const cached = _markerIconCache.get(key);
  if (cached) return cached;
  const color = alarmActive ? "#dc2626" : status === "online" ? "#10b981" : "#94a3b8";
  const cls = alarmActive
    ? "is-alarm"
    : status === "online"
      ? "is-online"
      : "is-offline";
  // Haberlesme kopuk (offline/unknown): marker'in sag-ust kosesine "sinyal
  // yok" rozeti. Sadece gri renk yetmiyordu — operator gri marker'i "pasif
  // cihaz" sanabiliyor; bu rozet acikca "veri gelmiyor" diyor. Alarm rozeti
  // ile cakismaz: alarm kirmizi govde, bu ayri kose isareti.
  const commLost = status !== "online";
  const commBadge = commLost
    ? `
      <span class="device-marker-comm" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="9" height="9">
          <path fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round"
                d="M3 3 L21 21"/>
          <path fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"
                d="M5 12.5a9 9 0 0 1 4.2-2.4M12 17.5h.01"/>
        </svg>
      </span>`
    : "";
  const icon = L.divIcon({
    className: `device-marker-wrap${dimmed ? " is-dimmed" : ""}`,
    html: `
      <div class="device-marker ${cls}${commLost ? " is-comm-lost" : ""}" style="--c:${color}">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path fill="#fff" d="M13 2 4 14h6l-1 8 9-12h-6z"/>
        </svg>
        ${commBadge}
      </div>
    `,
    iconSize: [28, 28],
    iconAnchor: [14, 14]
  });
  _markerIconCache.set(key, icon);
  return icon;
}

function batteryClass(percent: number | null): string {
  if (percent === null) return "device-battery--unknown";
  if (percent <= 20) return "device-battery--critical";
  if (percent <= 50) return "device-battery--low";
  return "device-battery--ok";
}

// Kaynak kumesi VERIDIR (SN 2.0'da uc unite, Pole Master Kit'te on);
// dar bir birlesim tipi yeni uydularin etiketini ham birakiyordu.
type SourceKey = string;

const SOURCE_LABEL = new Proxy({} as Record<string, string>, {
  get: (_t, k: string) => ortakKaynakEtiketi(k)
});

function formatRelative(
  iso: string | null | undefined,
  localeTag: string,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 5) return t("common.now");
  if (sec < 60) return t("common.secondsAgoShort", { count: sec });
  if (sec < 3600) return t("common.minutesAgoShort", { count: Math.round(sec / 60) });
  if (sec < 86400) return t("common.hoursAgoShort", { count: Math.round(sec / 3600) });
  return d.toLocaleString(localeTag);
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

export function DeviceMapTab({ devices, selectedDevice, onSelectDevice, liveValues, gridSnapshot, alarms, onOpenDetail, hiddenLineIds }: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [detailModalOpen, setDetailModalOpen] = useState(false);
  const mapRef = useRef<L.Map | null>(null);
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
  // Esikler cihaz TURU seviyesinde cozulur (bkz. Cihaz profilleri).
  const { thresholdsFor } = useDeviceModelSettings();
  const voltageToPercent = useCallback(
    (v: number | null) => voltsToPercent(v, thresholdsFor(selectedDevice?.model)) ?? null,
    [thresholdsFor, selectedDevice?.model]
  );

  // Anasayfa ilk acilista, harita Turkiye merkezinde 5x zoom yerine tum
  /** Secili cihazin bagli oldugu hattin TUM direkleri.
   *
   *  Cihaz -> hat baglantisi `line_segments` uzerinden kurulur (cihaz bir
   *  segmentin uzerinde oturur). Cihaz hicbir segmente bagli degilse liste
   *  bos doner ve odaklama tek noktaya duser (bkz. planDeviceFocus).
   *
   *  BRANSMAN NOTU: kol AYRI bir hattir. Kol uzerindeki bir cihaza
   *  tiklandiginda o KOL sigdirilir, ana hat degil — operatorun baktigi
   *  sey koldur. */
  const selectedLinePoints = useMemo<FocusPoint[]>(() => {
    if (!selectedDevice || !gridSnapshot) return [];
    const segment = gridSnapshot.segments?.find((sg) => sg.device_id === selectedDevice.id);
    if (!segment) return [];
    return (gridSnapshot.poles ?? [])
      .filter((pl) => pl.line_id === segment.line_id)
      .map((pl) => ({ latitude: pl.latitude, longitude: pl.longitude }));
  }, [selectedDevice, gridSnapshot]);

  // sebeke direklerinin/cihazlarinin sigdigi bounds'a yakinlasarak acilsin.
  // Topoloji direkleri varsa onu kullan; yoksa cihaz konumlarina dus.
  const autoFitPoints = useMemo<Array<[number, number]>>(() => {
    const acc: Array<[number, number]> = [];
    if (gridSnapshot?.poles?.length) {
      for (const p of gridSnapshot.poles) {
        if (typeof p.latitude === "number" && typeof p.longitude === "number") {
          acc.push([p.latitude, p.longitude]);
        }
      }
    }
    if (acc.length === 0) {
      for (const d of devices) {
        if (typeof d.latitude === "number" && typeof d.longitude === "number") {
          acc.push([d.latitude, d.longitude]);
        }
      }
    }
    return acc;
  }, [gridSnapshot?.poles, devices]);

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
    // Hangi unitelerin bataryasi oldugu MODELE baglidir: SN 2.0'da
    // master/sat01/sat02, Pole Master Kit setinde sat01/sat02/sat03.
    const targets: { key: SourceKey; signal: string }[] = (
      selectedDevice.model === "horstmann_pmk_set"
        ? (["sat01", "sat02", "sat03"] as const)
        : (["master", "sat01", "sat02"] as const)
    ).map((key) => ({
      key: key as SourceKey,
      signal: `${key}.battery_voltage_satellite`
    }));
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

  // Secili cihazin topoloji (bolge/hat) bilgisi — segment.device_id eslesmesi.
  const selectedTopo = useMemo(() => {
    if (!selectedDevice || !gridSnapshot) return null;
    const seg = gridSnapshot.segments?.find((s) => s.device_id === selectedDevice.id);
    if (!seg) return null;
    const line = gridSnapshot.lines?.find((l) => l.id === seg.line_id);
    const region = line ? gridSnapshot.regions?.find((r) => r.id === line.region_id) : undefined;
    return { regionName: region?.name ?? "", lineName: line?.name ?? "" };
  }, [selectedDevice, gridSnapshot]);

  // Master modem RSSI -> sebeke sinyali (4 kademeli cubuk + dBm) — detay
  // sidebar'i ile ayni esikler.
  const selectedSignal = useMemo(() => {
    const row =
      selectedDevice && liveValues
        ? liveValues.find(
            (r) => r.device_id === selectedDevice.id && r.signal_key === "master.modem_rssi"
          )
        : undefined;
    const rssi = typeof row?.value === "number" ? row.value : null;
    if (rssi == null) return { key: "none", dbm: "—", bars: 0 };
    const dbm = `${Math.round(rssi)} dBm`;
    if (rssi >= -70) return { key: "good", dbm, bars: 4 };
    if (rssi >= -85) return { key: "fair", dbm, bars: 3 };
    if (rssi >= -100) return { key: "poor", dbm, bars: 2 };
    return { key: "poor", dbm, bars: 1 };
  }, [selectedDevice, liveValues]);

  // ===== Sebeke topolojisi: hatlar + direkler + cihaz segmentleri =====
  // Cihazda aktif (reset edilmemis) VE hat arizasi ureten alarm var mi?
  // Marker + polyline kirmizi rengi icin. produces_fault === false olan
  // alarmlar (gecici/gurultulu) haritada ARIZA GOSTERMEZ; yalniz Alarmlar
  // ekraninda durur. `!== false`: eski/undefined alarmlar true kabul edilir
  // (geriye uyum — backend produces_fault default'u da True).
  //
  // KIMLIK KARARLILIGI: `alarms` dizisi 5 saniyede bir yeniden cekiliyor ve
  // her seferinde YENI bir dizi kimligi geliyor — icerik ayni olsa bile.
  // Bu Set dogrudan `alarms`'a bagli olsaydi her poll'de yeni bir Set uretir,
  // o da asagidaki agir `topology` memo'sunu (DFS'li graf kurulumu) 5 saniyede
  // bir yeniden hesaplatirdi. Once ICERIK IMZASI cikarilip Set ona
  // baglaniyor: alarm kumesi gercekten degismedikce kimlik sabit kaliyor.
  const alarmActiveKey = useMemo(() => {
    const ids: number[] = [];
    for (const a of alarms ?? []) {
      if (!a.reset && a.produces_fault !== false) ids.push(a.device_id);
    }
    ids.sort((x, y) => x - y);
    return ids.join(",");
  }, [alarms]);

  const alarmActiveDeviceIds = useMemo<Set<number>>(
    () => new Set(alarmActiveKey ? alarmActiveKey.split(",").map(Number) : []),
    [alarmActiveKey]
  );

  // Cihaz id -> [lat, lon]. Topoloji yalnizca cihazin KONUMUNU okuyor
  // (haberlesme durumu/batarya degil), o yuzden konum disindaki alanlar
  // degistiginde graf yeniden kurulmamali.
  //
  // Ayrica bu Map, topoloji icindeki `devices.find(...)` taramasinin yerini
  // aliyordu: segment basina O(cihaz) arama demekti — 600 cihaz x ~6.000
  // segment = her hesapta milyonlarca karsilastirma.
  const devicePositionKey = useMemo(
    () => devices.map((d) => `${d.id}:${d.latitude}:${d.longitude}`).join("|"),
    [devices]
  );

  const devicePositions = useMemo<Map<number, [number, number]>>(() => {
    const m = new Map<number, [number, number]>();
    for (const d of devices) m.set(d.id, [d.latitude, d.longitude]);
    return m;
    // Bilincli olarak `devices` DEGIL imzaya bagli: 5 sn'lik cihaz polling'i
    // (haberlesme durumu, batarya) konumu degistirmedigi surece kimlik sabit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devicePositionKey]);

  // Gizlenen hatlara ait cihaz ve direkler — marker'lari grilestirmek icin.
  // Hat gizleme bir GORSEL sadelestirme; veri filtresi degil, o yuzden
  // `devices` listesi budanmaz, sadece bu kumeye giren marker'lar solar.
  const dimmedDeviceIds = useMemo<Set<number>>(() => {
    const s = new Set<number>();
    if (!gridSnapshot || !hiddenLineIds?.size) return s;
    for (const seg of gridSnapshot.segments) {
      if (seg.device_id && hiddenLineIds.has(seg.line_id)) s.add(seg.device_id);
    }
    return s;
  }, [gridSnapshot, hiddenLineIds]);

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

  // Tel mesafesi indeksi — direk koordinatlarindan hat boyunca kumulatif
  // mesafeler. Ariza edge'inin tooltip'inde "hat basindan ~X m" gostermek
  // icin. Backend `line_distance_service` ile ayni formul.
  const lineDistIndex = useMemo(
    () => (gridSnapshot ? buildLineDistanceIndex(gridSnapshot) : null),
    [gridSnapshot]
  );

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
      // Ariza parcalari icin: parcanin iki ucunun hat basindan TEL mesafesi
      // (metre). Saglikli parcalarda null — tooltip'te gosterilmiyor.
      distFromM: number | null;
      distToM: number | null;
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
      const pos: [number, number] =
        deviceLocationOverride.get(seg.device_id)
        ?? devicePositions.get(seg.device_id)
        ?? [0, 0];
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
    // Trafo pole'lar — endpoint'i trafo direginde olan polyline parcalari
    // tam direk koordinatina kadar cizilirse hat trafonun halkalarinin
    // icinden gecmis gibi gozukur. Bunun yerine endpoint'i direk
    // koordinatindan TRAFO_PIN_RADIUS_M kadar geri cekeriz; cizgi halka
    // grubunun DIS kenarinda son bulur.
    // Yaklasim: Leaflet ekran piksellerinde halka grubu yari capi ~22px;
    // 1 derece enlem ~111000m. Kullanicinin haritasi cesitli zoom'larda
    // calisacak; sabit bir METRE ofseti yerine sabit yaklasik bir
    // derece kucucuk degeri (3 metre) kullaniyoruz — bu cogu zoom'da
    // halka grubunun yaklasik dis kenarini hedefler.
    const TRAFO_OFFSET_M = 12;
    const isTrafoPoleId = (poleId: number) => polesById.get(poleId)?.pole_type === "transformer";
    const offsetTowardsFrom = (
      from: [number, number],
      to: [number, number],
      meters: number
    ): [number, number] => {
      // 1 derece lat ~ 111000m; lon icin enleme gore cos(lat) duzeltmesi.
      const dLat = from[0] - to[0];
      const dLon = from[1] - to[1];
      const latM = dLat * 111000;
      const lonM = dLon * 111000 * Math.cos((to[0] * Math.PI) / 180);
      const distM = Math.sqrt(latM * latM + lonM * lonM);
      if (distM < 1) return to; // cok yakin, ofset etme
      const t = meters / distM;
      // to'dan from yonune dogru meters kadar git
      return [to[0] + dLat * t, to[1] + dLon * t];
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
        const rawAPos: [number, number] = [a.latitude, a.longitude];
        const rawBPos: [number, number] = [b.latitude, b.longitude];
        if (slotSegs.length === 0) {
          // Cihaz yok: tek edge pole_a -> pole_b. Trafo pole'larina
          // baglanan ucu geri cek (cizgi halkalarin disinda dursun).
          const aDraw = isTrafoPoleId(a.id) ? offsetTowardsFrom(rawBPos, rawAPos, TRAFO_OFFSET_M) : rawAPos;
          const bDraw = isTrafoPoleId(b.id) ? offsetTowardsFrom(rawAPos, rawBPos, TRAFO_OFFSET_M) : rawBPos;
          addEdge({
            id: `e-${lineId}-${a.id}-${b.id}-direct`,
            fromNodeId: poleNodeId(a.id),
            toNodeId: poleNodeId(b.id),
            positions: [aDraw, bDraw],
            lineId,
            lineName: line?.name ?? "",
            regionName: region?.name ?? ""
          });
          continue;
        }
        // Cihazlari sirayla yerleştir: pole_a -> dev1 -> dev2 -> ... -> pole_b
        let prevNodeId = poleNodeId(a.id);
        // Ilk segment'in pole_a tarafi pole_a TRAFO ise geri cekilir.
        let prevPos: [number, number] = isTrafoPoleId(a.id)
          ? offsetTowardsFrom(rawBPos, rawAPos, TRAFO_OFFSET_M)
          : rawAPos;
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
        // Son cihazdan pole_b'ye kapanis edge'i — pole_b TRAFO ise geri cekilir
        const tailEnd = isTrafoPoleId(b.id) ? offsetTowardsFrom(prevPos, rawBPos, TRAFO_OFFSET_M) : rawBPos;
        addEdge({
          id: `e-${lineId}-${a.id}-${b.id}-tail`,
          fromNodeId: prevNodeId,
          toNodeId: poleNodeId(b.id),
          positions: [prevPos, tailEnd],
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
      const parentRaw: [number, number] = [parentPole.latitude, parentPole.longitude];
      const firstRaw: [number, number] = [firstPole.latitude, firstPole.longitude];
      const parentDraw = isTrafoPoleId(parentPole.id)
        ? offsetTowardsFrom(firstRaw, parentRaw, TRAFO_OFFSET_M)
        : parentRaw;
      const firstDraw = isTrafoPoleId(firstPole.id)
        ? offsetTowardsFrom(parentRaw, firstRaw, TRAFO_OFFSET_M)
        : firstRaw;
      addEdge({
        id: `branch-${lineId}`,
        fromNodeId: poleNodeId(parentPole.id),
        toNodeId: poleNodeId(firstPole.id),
        positions: [
          parentDraw,
          firstDraw
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

    // 4) "Akim bu dala mi gitti?" — EN YAKIN cihaza gore.
    //
    // Eskiden bu bir subtree taramasiydi ("altta HERHANGI BIR yerde kirmizi
    // var mi") ve araya giren YESIL cihazi asip dibe iniyordu. Test
    // sunucusundaki gercek topolojide bu, direk #7'nin iki kolunu da
    // "yan dal" ilan edip ariza boyamasindan tamamen disarida birakti:
    // cok asagida, BR-4'te bir kirmizi vardi — ama arada onu yalanlayan
    // yesil bir cihaz (cihaz 4) duruyordu. Gerekce ve testler
    // `nearestDeviceRed.ts` icinde.
    const subtreeHasRed = nearestDeviceRedMap({
      nodes,
      outEdges,
      edgeTarget: (eid) => edgeById.get(eid)?.toNodeId,
      rootNodeIds
    });

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
    //
    // Ariza parcalari icin tel mesafesi de tasiniyor: edge'in uc node'lari
    // ya bir direk (`p-<id>`) ya bir cihazdir (`d-<id>`); ikisinin de hat
    // basindan mesafesi lineDistIndex'te hazir. Bransman baglanti edge'leri
    // (lineId=null) icin de node bazli calisir.
    const nodeDistM = (nodeId: string): number | null => {
      if (!lineDistIndex) return null;
      const n = nodes.get(nodeId);
      if (!n) return null;
      if (n.kind === "pole" && n.poleId != null) {
        return lineDistIndex.poleDistM.get(n.poleId) ?? null;
      }
      if (n.kind === "device" && n.deviceId != null) {
        return lineDistIndex.deviceDistM.get(n.deviceId) ?? null;
      }
      return null;
    };
    for (const e of edges) {
      const isFault = faultEdgeIds.has(e.id);
      linePolylines.push({
        id: `edge-${e.id}`,
        lineId: e.lineId,
        positions: e.positions,
        color: isFault ? FAULT_COLOR : HEALTHY_DEFAULT,
        kind: isFault ? "fault" : "healthy",
        name: e.lineName,
        regionName: e.regionName,
        distFromM: isFault ? nodeDistM(e.fromNodeId) : null,
        distToM: isFault ? nodeDistM(e.toNodeId) : null
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
    // `devices` yerine `devicePositions`: 5 sn'lik cihaz polling'i (haberlesme
    // durumu / batarya) bu agir grafi yeniden kurmasin. Bkz. devicePositionKey.
  }, [gridSnapshot, devicePositions, alarmActiveDeviceIds, deviceLocationOverride, lineDistIndex]);

  return (
    <section className="map-full">
      <div className="world-map-shell">
        <MapContainer className="world-map" center={[39.0, 35.0]} zoom={5} scrollWheelZoom>
          <LayersControl position="topright">
            {MAP_LAYERS.map((layer) => (
              <LayersControl.BaseLayer
                key={layer.key}
                checked={layer.key === DEFAULT_MAP_LAYER}
                name={t(layer.labelKey)}
              >
                <ResilientTileLayer
                  layer={layer.key}
                  attribution={layer.attribution}
                  maxZoom={layer.maxZoom}
                />
              </LayersControl.BaseLayer>
            ))}
          </LayersControl>
          <MapRefBridge onReady={(map) => { mapRef.current = map; }} />
          <FlyToSelected
            selectedDevice={selectedDevice}
            override={selectedDevice ? deviceLocationOverride.get(selectedDevice.id) : undefined}
            linePoints={selectedLinePoints}
          />
          <MapInvalidator deps={[devices.length]} />
          <MapLayerSwitchFix />
          <AutoFitOnLoad points={autoFitPoints} hasSelection={Boolean(selectedDevice)} />

          {/* Hat polylineları (her edge bagimsiz):
                - healthy : SOLID YESIL
                - fault   : RED DASHED (sadece son RED ile ilk GREEN
                            arasindaki tek edge)
              Bransman baglantilari da (parent_pole -> branch_first_pole)
              ayni listede normal edge gibi cizilir. */}
          {topology?.linePolylines.map((line) => {
            const isFault = line.kind === "fault";
            // Gizlenen hat: gri + soluk + ince. Silmiyoruz ki sebekenin
            // gectigi guzergah haritada okunabilir kalsin.
            // lineId null olabilir (bransman baglanti parcalari); o durumda
            // hangi hatta ait oldugu belirsiz, grilestirmiyoruz.
            const dimmed = line.lineId !== null && (hiddenLineIds?.has(line.lineId) ?? false);
            return (
              <Polyline
                key={line.id}
                positions={line.positions}
                pathOptions={{
                  color: dimmed ? "#cbd5e1" : line.color,
                  weight: dimmed ? 2.5 : isFault ? 5 : 4,
                  opacity: dimmed ? 0.55 : isFault ? 0.9 : 0.85,
                  dashArray: dimmed ? undefined : isFault ? "10 6" : undefined
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
                  {/* Tel mesafesi: bu parcanin hat basindan uzakligi. Kus
                      ucusu degil, direkler uzerinden hat boyunca olculur. */}
                  {isFault && formatDistanceRange(line.distFromM, line.distToM) ? (
                    <>
                      <br />
                      <span style={{ opacity: 0.85 }}>
                        {t("map.faultDistance", {
                          range: formatDistanceRange(line.distFromM, line.distToM)
                        })}
                      </span>
                    </>
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
              opacity={hiddenLineIds?.has(p.line_id) ? 0.35 : 1}
              icon={polePin(String(p.sequence_no), isStart, isEnd, p, isBranchPoint, isBranchEntry)}
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
                <br />
                <span style={{ opacity: 0.7, fontFamily: "ui-monospace, monospace", fontSize: 11 }}>
                  {p.latitude.toFixed(6)}, {p.longitude.toFixed(6)}
                </span>
              </Tooltip>
            </Marker>
          ))}

          {devices.map((device) => {
            const override = deviceLocationOverride.get(device.id);
            const position: [number, number] = override
              ? override
              : [device.latitude, device.longitude];
            const isAlarmed = alarmActiveDeviceIds.has(device.id);
            const dimmed = dimmedDeviceIds.has(device.id);
            return (
              <Marker
                key={device.id}
                position={position}
                icon={markerIcon(device.communicationStatus, isAlarmed, dimmed)}
                eventHandlers={{
                  click: () => onSelectDevice(device.id)
                }}
              >
                {/* Hover: cihaz adi + koordinat (operator marker'i tikladigi
                    konum kayit notuna kullanir). */}
                <Tooltip direction="top" offset={[0, -10]}>
                  <strong>{device.name}</strong>
                  <br />
                  <span style={{ opacity: 0.7, fontFamily: "ui-monospace, monospace", fontSize: 11 }}>
                    {position[0].toFixed(6)}, {position[1].toFixed(6)}
                  </span>
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
              aria-label={t("dashboard.popup.close")}
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
                <h4>{poleInfo.pole.name ?? t("dashboard.popup.poleDefault", { seq: poleInfo.pole.sequence_no })}</h4>
                <span className="map-info-card-sub">
                  #{poleInfo.pole.sequence_no} · {poleInfo.lineName}
                </span>
              </div>
            </header>
            <ul className="map-info-card-rows">
              <li>
                <span className="map-info-card-label">{t("dashboard.popup.type")}</span>
                <span className="map-info-card-value">
                  {poleInfo.pole.pole_type === "transformer" ? t("dashboard.popup.transformer") : t("dashboard.popup.pole")}
                </span>
              </li>
              {poleInfo.isStart || poleInfo.isEnd ? (
                <li>
                  <span className="map-info-card-label">{t("dashboard.popup.location")}</span>
                  <span className="map-info-card-value">
                    {poleInfo.isStart ? t("dashboard.popup.poleStart") : t("dashboard.popup.poleEnd")}
                  </span>
                </li>
              ) : null}
              {poleInfo.isBranchPoint && poleInfo.childLineNames.length > 0 ? (
                <li>
                  <span className="map-info-card-label">{t("dashboard.popup.branch")}</span>
                  <span className="map-info-card-value" style={{ color: "#6366f1" }}>
                    {t("dashboard.popup.branchSplit", { names: poleInfo.childLineNames.join(", ") })}
                  </span>
                </li>
              ) : null}
              {poleInfo.isBranchEntry && poleInfo.branchParentLineName ? (
                <li>
                  <span className="map-info-card-label">{t("dashboard.popup.connected")}</span>
                  <span className="map-info-card-value" style={{ color: "#6366f1" }}>
                    {poleInfo.branchParentLineName}
                  </span>
                </li>
              ) : null}
              <li>
                <span className="map-info-card-label">{t("dashboard.popup.coords")}</span>
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
              aria-label={t("dashboard.popup.close")}
            >
              <span className="material-symbols-outlined">close</span>
            </button>
            <header className="map-info-card-header">
              <div className="map-info-card-icon">
                <span className="material-symbols-outlined">timeline</span>
              </div>
              <div className="map-info-card-title">
                <h4>{lineInfo.name || t("dashboard.popup.lineDefault")}</h4>
                {lineInfo.regionName ? (
                  <span className="map-info-card-sub">{lineInfo.regionName}</span>
                ) : null}
              </div>
            </header>
            <ul className="map-info-card-rows">
              {lineInfo.regionName ? (
                <li>
                  <span className="map-info-card-label">{t("dashboard.popup.region")}</span>
                  <span className="map-info-card-value">{lineInfo.regionName}</span>
                </li>
              ) : null}
              <li>
                <span className="map-info-card-label">{t("dashboard.popup.status")}</span>
                <span
                  className="map-info-card-value"
                  style={{ color: lineInfo.isFault ? FAULT_COLOR : HEALTHY_FAULT_LINE_COLOR }}
                >
                  {lineInfo.isFault ? t("dashboard.popup.lineFault") : t("dashboard.popup.lineHealthy")}
                </span>
              </li>
            </ul>
          </div>
        ) : null}

        {selectedDevice ? (
          <div className="device-popup-card device-popup-card--modern device-popup-card--v2">
            <button
              type="button"
              className="device-popup-close"
              onClick={() => onSelectDevice(0)}
              aria-label={t("dashboard.popup.close")}
            >
              <span className="material-symbols-outlined">close</span>
            </button>

            <div className="device-popup-v2-body">
              {/* Kimlik — durum noktasi SOLDA (detay sidebar'i ile ayni) */}
              <div className="device-popup-v2-id">
                <div className="device-sidebar-idrow">
                  <span
                    className={`device-sidebar-statusdot ${selectedDevice.communicationStatus === "online" ? "is-online" : "is-offline"}`}
                    title={selectedDevice.communicationStatus === "online" ? t("dashboard.popup.online") : t("dashboard.popup.offline")}
                  />
                  <h2 className="device-sidebar-code">{selectedDevice.name}</h2>
                </div>
                <div className="device-sidebar-name">{selectedDevice.code}</div>
              </div>

              {/* Genel alarm durum karti — yesil "Normal" / kirmizi "Aktif Alarm" */}
              <div className={`device-sidebar-alarmcard ${selectedDevice.alarmActive ? "is-alarm" : "is-ok"}`}>
                <span className="device-sidebar-alarmcard-icon">
                  <span className="material-symbols-outlined">
                    {selectedDevice.alarmActive ? "notification_important" : "check_circle"}
                  </span>
                </span>
                <div className="device-sidebar-alarmcard-body">
                  <span className="device-sidebar-alarmcard-title">
                    {selectedDevice.alarmActive
                      ? t("deviceDetail.sidebar.alarmActive")
                      : t("deviceDetail.sidebar.alarmClear")}
                  </span>
                  <span className="device-sidebar-alarmcard-sub">
                    {selectedDevice.alarmActive
                      ? t("deviceDetail.sidebar.alarmActiveSub")
                      : t("deviceDetail.sidebar.alarmClearSub")}
                  </span>
                </div>
                {selectedDevice.alarmActive ? (
                  <span className="device-sidebar-alarmcard-pulse" aria-hidden="true" />
                ) : null}
              </div>

              {/* BILGILER — detay sayfasindaki ozellikler */}
              <div className="device-popup-v2-info">
                <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.info")}</span>
                <ul className="device-sidebar-info">
                  <li className="device-sidebar-info-row">
                    <span className="material-symbols-outlined">wifi</span>
                    <span className="device-sidebar-info-label">{t("deviceDetail.sidebar.deviceStatus")}</span>
                    <span className={`device-sidebar-info-value tone-${selectedDevice.communicationStatus === "online" ? "green" : "slate"}`}>
                      <span className={`device-sidebar-info-dot dot-${selectedDevice.communicationStatus === "online" ? "green" : "slate"}`} aria-hidden="true" />
                      {selectedDevice.communicationStatus === "online" ? t("dashboard.popup.online") : t("dashboard.popup.offline")}
                    </span>
                  </li>
                  <li className="device-sidebar-info-row">
                    <span className="material-symbols-outlined">schedule</span>
                    <span className="device-sidebar-info-label">{t("deviceDetail.sidebar.lastCommShort")}</span>
                    <span className="device-sidebar-info-value">
                      {formatRelative(selectedDevice.lastUpdateAt, localeTag, t)}
                    </span>
                  </li>
                  {selectedTopo?.regionName ? (
                    <li className="device-sidebar-info-row">
                      <span className="material-symbols-outlined">map</span>
                      <span className="device-sidebar-info-label">{t("deviceDetail.meta.region")}</span>
                      <span className="device-sidebar-info-value">{selectedTopo.regionName}</span>
                    </li>
                  ) : null}
                  {selectedTopo?.lineName ? (
                    <li className="device-sidebar-info-row">
                      <span className="material-symbols-outlined">timeline</span>
                      <span className="device-sidebar-info-label">{t("deviceDetail.meta.line")}</span>
                      <span className="device-sidebar-info-value">{selectedTopo.lineName}</span>
                    </li>
                  ) : null}
                  <li className="device-sidebar-info-row">
                    <span className="material-symbols-outlined">battery_full</span>
                    <span className="device-sidebar-info-label">{t("deviceDetail.meta.battery")}</span>
                    <span className={`device-sidebar-battery ${batteryClass(selectedDevice.batteryPercent)}`}>
                      <span className="device-battery-icon" aria-hidden="true">
                        <span
                          className="device-battery-fill"
                          style={{ width: `${Math.max(0, Math.min(100, selectedDevice.batteryPercent))}%` }}
                        />
                      </span>
                      <span className="device-sidebar-battery-text">%{Math.round(selectedDevice.batteryPercent)}</span>
                    </span>
                  </li>
                  <li className="device-sidebar-info-row">
                    <span className="material-symbols-outlined">signal_cellular_alt</span>
                    <span className="device-sidebar-info-label">{t("deviceDetail.sidebar.networkSignal")}</span>
                    <span className={`device-sidebar-signal sig-${selectedSignal.key}`}>
                      <span className="device-sidebar-signal-bars" aria-hidden="true">
                        {[1, 2, 3, 4].map((b) => (
                          <span key={b} className={`bar${b <= selectedSignal.bars ? " on" : ""}`} />
                        ))}
                      </span>
                      <span className="device-sidebar-signal-text">{selectedSignal.dbm}</span>
                    </span>
                  </li>
                </ul>
              </div>
            </div>

            <button
              type="button"
              className="device-popup-detail-btn"
              onClick={() => {
                if (onOpenDetail && selectedDevice) {
                  onOpenDetail(selectedDevice.id);
                } else {
                  setDetailModalOpen(true);
                }
              }}
            >
              <span className="material-symbols-outlined">read_more</span>
              {t("dashboard.popup.showAllDetails")}
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
// Etiketler i18n key'leri olarak tutuluyor; renderda t() ile cozuluyor.
const PER_SOURCE_BINARY: { suffix: string; labelKey: string; group?: "state" | "direction" }[] = [
  { suffix: "overcurrent_tripped", labelKey: "dashboard.deviceDetail.overcurrent", group: "state" },
  { suffix: "delta_i_delta_t_tripped", labelKey: "dashboard.deviceDetail.didt", group: "state" },
  { suffix: "voltage_loss", labelKey: "dashboard.deviceDetail.voltageLoss", group: "state" },
  { suffix: "current_loss", labelKey: "dashboard.deviceDetail.currentLoss", group: "state" },
  { suffix: "battery_status", labelKey: "dashboard.deviceDetail.battery", group: "state" },
  { suffix: "communication_status", labelKey: "dashboard.popup.lastData", group: "state" },
  { suffix: "permanent_fault", labelKey: "dashboard.deviceDetail.permanentFault", group: "state" },
  { suffix: "momentary_fault", labelKey: "dashboard.deviceDetail.temporaryFault", group: "state" },
  { suffix: "load_flow_direction_green_a", labelKey: "dashboard.deviceDetail.flowDirA", group: "direction" },
  { suffix: "load_flow_direction_red_b", labelKey: "dashboard.deviceDetail.flowDirB", group: "direction" },
  { suffix: "overcurrent_fault_direction_green_a", labelKey: "dashboard.deviceDetail.overcurrentFaultDirA", group: "direction" },
  { suffix: "overcurrent_fault_direction_red_b", labelKey: "dashboard.deviceDetail.overcurrentFaultDirB", group: "direction" },
  { suffix: "delta_i_delta_t_fault_direction_green_a", labelKey: "dashboard.deviceDetail.didtFaultDirA", group: "direction" },
  { suffix: "delta_i_delta_t_fault_direction_red_b", labelKey: "dashboard.deviceDetail.didtFaultDirB", group: "direction" }
];

const PER_SOURCE_ANALOG: { suffix: string; labelKey: string; unit: string; group?: "live" | "fault" }[] = [
  { suffix: "actual_current", labelKey: "dashboard.deviceDetail.current", unit: "mA", group: "live" },
  { suffix: "actual_voltage", labelKey: "dashboard.deviceDetail.voltage", unit: "V", group: "live" },
  { suffix: "average_current", labelKey: "dashboard.deviceDetail.avgCurrent", unit: "mA", group: "live" },
  { suffix: "maximum_current", labelKey: "dashboard.deviceDetail.maxCurrent", unit: "mA", group: "live" },
  { suffix: "conductor_temperature", labelKey: "dashboard.deviceDetail.conductorTemp", unit: "°C", group: "live" },
  { suffix: "device_temperature", labelKey: "dashboard.deviceDetail.deviceTemp", unit: "°C", group: "live" },
  { suffix: "fault_current", labelKey: "dashboard.deviceDetail.faultCurrent", unit: "mA", group: "fault" },
  { suffix: "fault_duration", labelKey: "dashboard.deviceDetail.faultDuration", unit: "ms", group: "fault" },
  { suffix: "last_good_known_current", labelKey: "dashboard.deviceDetail.lastGoodCurrent", unit: "mA", group: "fault" },
  { suffix: "minimum_current", labelKey: "dashboard.deviceDetail.minCurrent", unit: "mA", group: "fault" }
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
  const { t } = useTranslation();
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
      labelKey,
      unit
    }: { suffix: string; labelKey: string; unit: string }) => {
      const row = valueByKey.get(`${src}.${suffix}`);
      const v = row?.value;
      const display =
        typeof v === "number" && Number.isFinite(v) ? v.toFixed(2) : "—";
      return (
        <div key={suffix} className="device-detail-col-analog-row">
          <span className="lbl">{t(labelKey)}</span>
          <span className="val">
            {display}
            <span className="unit"> {row?.unit ?? unit}</span>
          </span>
        </div>
      );
    };

    const renderBinaryRow = ({ suffix, labelKey }: { suffix: string; labelKey: string }) => {
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
          <span className="lbl">{t(labelKey)}</span>
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
            title={typeof battV === "number" ? `${battV.toFixed(2)} V` : t("dashboard.popup.noData")}
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
              <div className="lbl">{t("dashboard.deviceDetail.permanent")}</div>
              <div className="val">{permVal ?? "—"}</div>
            </div>
          </div>
          <div className="device-detail-mini-counter is-transient">
            <span className="material-symbols-outlined">flash_on</span>
            <div>
              <div className="lbl">{t("dashboard.deviceDetail.temporary")}</div>
              <div className="val">{tempVal ?? "—"}</div>
            </div>
          </div>
        </div>

        {/* Olcumler — canli */}
        <div className="device-detail-col-section">
          <div className="device-detail-col-title">
            <span className="material-symbols-outlined">monitoring</span>
            {t("dashboard.deviceDetail.measurements")}
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
              {t("dashboard.deviceDetail.faultMeasurements")}
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
            {t("dashboard.deviceDetail.status")}
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
              {t("dashboard.deviceDetail.faultDirection")}
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
                  {t("dashboard.sidebar.noLine")}
                </span>
              )}
            </div>
          </div>
          <button
            type="button"
            className="device-detail-modal-close"
            onClick={onClose}
            aria-label={t("common.close")}
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
