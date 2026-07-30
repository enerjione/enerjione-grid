/**
 * Hat uzerinde "tel mesafesi" (iletken boyu) hesaplari — istemci tarafi.
 *
 * Backend karsiligi: `apps/backend-api/app/services/line_distance_service.py`.
 * Iki taraf AYNI formulu kullanir; degistirirken ikisini birlikte guncelle.
 *
 * Neden istemcide de var: "Hat Arizalari" ekrani mesafeyi backend'in yazdigi
 * `FaultEvent.zone_start_m/zone_end_m` alanlarindan okur, ama ANASAYFA HARITASI
 * (DeviceMapTab) fault kayitlarini hic cekmez — ariza edge'lerini alarmlardan
 * kendisi hesaplar. O yuzden harita tooltip'i icin mesafeyi topoloji
 * anlik goruntusunden burada uretiyoruz.
 *
 * Kabuller: 2B hesap (kot farki yok), iletken sarkma payi yok.
 */
import i18n from "./i18n";
import type { GridSnapshot } from "./api";

/** IUGG ortalama Dunya yaricapi (m). */
const EARTH_RADIUS_M = 6371008.8;

/** Iki koordinat arasi kus ucusu mesafe (metre). */
export function haversineM(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number
): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const phi1 = toRad(lat1);
  const phi2 = toRad(lat2);
  const dPhi = phi2 - phi1;
  const dLambda = toRad(lon2 - lon1);
  const a =
    Math.sin(dPhi / 2) ** 2 +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

export type LineDistanceIndex = {
  /** pole_id -> hat basindan tel mesafesi (m, bransman offseti dahil). */
  poleDistM: Map<number, number>;
  /** device_id -> hat basindan tel mesafesi (m). */
  deviceDistM: Map<number, number>;
  /** line_id -> hattin son diregine kadar tel mesafesi (m). */
  lineEndDistM: Map<number, number>;
};

/**
 * Topoloji anlik goruntusunden tel-mesafesi indeksini kur.
 *
 * Adimlar backend ile ayni: (1) hat ici kumulatif, (2) bransman offseti,
 * (3) mutlak direk mesafesi, (4) cihazin slot ici interpolasyonu.
 */
export function buildLineDistanceIndex(snapshot: GridSnapshot): LineDistanceIndex {
  const polesById = new Map(snapshot.poles.map((p) => [p.id, p]));
  const polesByLine = new Map<number, typeof snapshot.poles>();
  for (const p of snapshot.poles) {
    const arr = polesByLine.get(p.line_id) ?? [];
    arr.push(p);
    polesByLine.set(p.line_id, arr);
  }

  // 1) Hat ICI kumulatif mesafe (her hattin ilk diregi = 0).
  const local = new Map<number, number>();
  const lineLength = new Map<number, number>();
  for (const [lineId, poles] of polesByLine) {
    const sorted = [...poles].sort((a, b) => a.sequence_no - b.sequence_no);
    let acc = 0;
    let prev: (typeof sorted)[number] | null = null;
    for (const p of sorted) {
      if (prev) {
        acc += haversineM(prev.latitude, prev.longitude, p.latitude, p.longitude);
      }
      local.set(p.id, acc);
      prev = p;
    }
    lineLength.set(lineId, acc);
  }

  // 2) Bransman offseti — hattin baslangici, ana hattaki dallanma diregine
  //    kadar olan mesafedir. Zincir rekursif cozulur; dongude 0 kabul edilir.
  const linesById = new Map(snapshot.lines.map((l) => [l.id, l]));
  const lineStart = new Map<number, number>();
  const resolveStart = (lineId: number, seen: Set<number>): number => {
    const cached = lineStart.get(lineId);
    if (cached !== undefined) return cached;
    if (seen.has(lineId)) return 0;
    const line = linesById.get(lineId);
    const parentPole = line?.branched_from_pole_id
      ? polesById.get(line.branched_from_pole_id)
      : undefined;
    const value = parentPole
      ? resolveStart(parentPole.line_id, new Set(seen).add(lineId)) +
        (local.get(parentPole.id) ?? 0)
      : 0;
    lineStart.set(lineId, value);
    return value;
  };
  for (const l of snapshot.lines) resolveStart(l.id, new Set());

  // 3) Mutlak direk mesafesi.
  const poleDistM = new Map<number, number>();
  for (const [poleId, d] of local) {
    const pole = polesById.get(poleId);
    if (!pole) continue;
    poleDistM.set(poleId, (lineStart.get(pole.line_id) ?? 0) + d);
  }

  const lineEndDistM = new Map<number, number>();
  for (const [lineId, len] of lineLength) {
    lineEndDistM.set(lineId, (lineStart.get(lineId) ?? 0) + len);
  }

  // 4) Cihaz mesafesi — slot uclarindaki direk mesafeleri arasinda t ile
  //    interpolasyon. `t` kurali DeviceMapTab.deviceLocationOverride ile ayni:
  //    kayitli device_position_t varsa o, yoksa (idx+1)/(n+1) esit dagilim.
  const bySlot = new Map<string, typeof snapshot.segments>();
  for (const seg of snapshot.segments) {
    if (!seg.device_id) continue;
    const key = `${seg.from_pole_id}|${seg.to_pole_id}`;
    const arr = bySlot.get(key) ?? [];
    arr.push(seg);
    bySlot.set(key, arr);
  }
  const deviceDistM = new Map<number, number>();
  for (const [key, segs] of bySlot) {
    const [fromIdStr, toIdStr] = key.split("|");
    const a = poleDistM.get(Number(fromIdStr));
    const b = poleDistM.get(Number(toIdStr));
    if (a === undefined || b === undefined) continue;
    const sorted = [...segs].sort((x, y) => {
      const xd = new Date(x.created_at).getTime();
      const yd = new Date(y.created_at).getTime();
      if (xd !== yd) return xd - yd;
      return x.id - y.id;
    });
    const total = sorted.length;
    sorted.forEach((seg, idx) => {
      if (!seg.device_id) return;
      const tManual = seg.device_position_t;
      const t =
        tManual !== null && tManual !== undefined && tManual >= 0 && tManual <= 1
          ? tManual
          : (idx + 1) / (total + 1);
      deviceDistM.set(seg.device_id, a + (b - a) * t);
    });
  }

  return { poleDistM, deviceDistM, lineEndDistM };
}

/**
 * Metre degerini okunur metne cevir: 1 km altinda "520 m", ustunde "1,24 km".
 * Locale i18n dilinden gelir (tr-TR ondalik virgul, en-US nokta).
 */
export function formatDistanceM(meters: number | null | undefined): string {
  if (meters === null || meters === undefined || !Number.isFinite(meters)) return "—";
  const locale = (i18n?.language || "").toString().toLowerCase().startsWith("tr")
    ? "tr-TR"
    : "en-US";
  const abs = Math.abs(meters);
  if (abs < 1000) {
    return `${Math.round(abs).toLocaleString(locale)} m`;
  }
  return `${(abs / 1000).toLocaleString(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })} km`;
}

/**
 * Iki mesafe sinirini tek aralik metnine cevir: "520 m – 690 m".
 * Sinirlar 1 metreden yakinsa tek deger doner ("520 m").
 */
export function formatDistanceRange(
  fromM: number | null | undefined,
  toM: number | null | undefined
): string | null {
  if (fromM === null || fromM === undefined) return null;
  if (toM === null || toM === undefined) return formatDistanceM(fromM);
  const lo = Math.min(fromM, toM);
  const hi = Math.max(fromM, toM);
  if (hi - lo < 1) return formatDistanceM(lo);
  return `${formatDistanceM(lo)} – ${formatDistanceM(hi)}`;
}
