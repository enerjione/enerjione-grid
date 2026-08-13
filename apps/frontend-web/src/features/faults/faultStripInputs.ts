/**
 * Sematik serit cizimi icin GIRDI hazirlama — React'siz.
 *
 * NEDEN AYRI MODUL
 * ----------------
 * `FaultPoleStrip` on dort prop istiyor ve hicbiri sunucudan hazir gelmiyor:
 * hattin direk siralari, direk ad/rolleri, segmentler, aday kollar, cihaz
 * basina alarm ozeti, arizali fazlar... Bu turetmeler ariza LISTESI sayfasinin
 * icinde `useMemo` yiginlari olarak yasiyordu. Cizimi ikinci bir ekrana (ariza
 * detay sayfasi) koymak, o yigini kopyalamak anlamina geliyordu; kopya
 * kacinilmaz olarak ayrisir ve iki ekran AYNI arizayi farkli cizmeye baslar.
 * Sahada "hangi kolu gezecegim" sorusunun iki farkli cevabi olamaz.
 *
 * Burada React yok: girdi -> cikti. Boylece node:test ile dogrulanabiliyor.
 *
 * ALARM KURALI: cihaz "arizayi gordu" sayilmasi icin alarmi RESETLENMEMIS ve
 * `produces_fault !== false` olmali — haberlesme alarmi ariza uretmez, kolu
 * supheli isaretlemesi yanlis olurdu (bkz. alarm_reconciliation.py).
 */
import { buildBranchRows } from "./branchRows";
import type { BranchScanFault } from "./branchRows";
import type { StripBranchRow, StripDeviceAlarms, StripPole, StripSegment } from "./faultStripGeometry";
import { haversineM } from "../../shared/lineDistance";

export type SeritHat = { id: number; name: string; branched_from_pole_id?: number | null };

export type SeritDirek = {
  id: number;
  line_id: number;
  sequence_no: number;
  name?: string | null;
  topology_role?: string | null;
  latitude: number;
  longitude: number;
};

export type SeritSegment = {
  line_id: number;
  from_pole_id: number;
  to_pole_id: number;
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  device_code?: string | null;
  device_name?: string | null;
  device_position_t?: number | null;
};

export type SeritAriza = BranchScanFault & {
  id: number;
  status: string;
  line_id: number;
  line_name?: string | null;
};

export type SeritAlarm = {
  device_id: number;
  /** Undefined = eski kayit; alarm hala acik sayilir. */
  reset?: boolean | null;
  produces_fault?: boolean | null;
};

/** `FaultPoleStrip`'e dogrudan verilebilen prop kumesi. */
export type SeritGirdileri = {
  lineName: string;
  poleSeqs: number[];
  poles: StripPole[];
  segments: StripSegment[];
  fromSeq: number | null;
  toSeq: number | null;
  lastRedDeviceCode: string | null;
  firstGreenDeviceCode: string | null;
  zoneStartM: number | null;
  zoneEndM: number | null;
  alarmsByDevice: Record<string, StripDeviceAlarms>;
  faultPhases: string[];
  branchRows: StripBranchRow[];
  hiddenBranchCount: number;
};

/** Ariza listesinde "hala is bekliyor" sayilan durumlar. `resolved` de burada:
 *  saha duzeltti ama kayit kapanmadi, kol adayligi surer. */
const AKTIF_DURUMLAR = new Set(["open", "assigned", "in_progress", "resolved"]);

export function buildFaultStripInputs(input: {
  lines: readonly SeritHat[];
  poles: readonly SeritDirek[];
  segments: readonly SeritSegment[];
  /** Cizilecek ariza kaydi. */
  fault: SeritAriza;
  /** Tum ariza kayitlari — kollarin KENDI kaydi buradan bulunur. */
  faults: readonly SeritAriza[];
  alarms: readonly SeritAlarm[];
  devices: readonly { id: number; code?: string | null }[];
  /** Hat adi cozulemezse yazilacak yedek etiket (i18n cagiranda). */
  lineFallback: string;
}): SeritGirdileri | null {
  const { lines, poles, segments, fault, faults, alarms, devices } = input;

  const hatDirekleri = poles
    .filter((p) => p.line_id === fault.line_id)
    .sort((a, b) => a.sequence_no - b.sequence_no);
  // Direksiz hatta cizilecek bir sey yok; bos bir SVG cercevesi gostermek
  // "veri yok" demekten daha kotu, cunku hat saglam gorunur.
  if (hatDirekleri.length === 0) return null;

  const hatSegmentleri: StripSegment[] = segments
    .filter((s) => s.line_id === fault.line_id)
    .map((s) => ({
      from_pole_seq: s.from_pole_seq ?? null,
      to_pole_seq: s.to_pole_seq ?? null,
      device_code: s.device_code ?? null,
      device_name: s.device_name ?? null,
      device_position_t: s.device_position_t ?? null
    }));

  const { alarmsByDevice, faultPhases } = alarmOzeti(fault);

  /** O AN alarmi olan cihaz KODLARI — kolun kendi kaydi olmasa da uzerindeki
   *  cihazlarin ne dedigi buradan okunur. */
  const idKod = new Map(devices.map((d) => [d.id, d.code]));
  const alarmliKodlar = new Set<string>();
  for (const a of alarms) {
    if (a.reset || a.produces_fault === false) continue;
    const kod = idKod.get(a.device_id);
    if (kod) alarmliKodlar.add(kod);
  }

  const acikArizaHatta = new Map<number, BranchScanFault>();
  for (const f of faults) {
    if (!AKTIF_DURUMLAR.has(f.status)) continue;
    // Ayni hatta birden fazla bolge olabilir; listedeki ilki (en yeni) yeterli.
    if (!acikArizaHatta.has(f.line_id)) acikArizaHatta.set(f.line_id, f);
  }

  const sahne = buildBranchRows({
    lines: lines.map((l) => ({
      id: l.id,
      name: l.name,
      branched_from_pole_id: l.branched_from_pole_id ?? null
    })),
    poles: [...poles],
    segments: segments.map((s) => ({
      line_id: s.line_id,
      from_pole_seq: s.from_pole_seq ?? null,
      to_pole_seq: s.to_pole_seq ?? null,
      device_code: s.device_code ?? null,
      device_name: s.device_name ?? null,
      device_position_t: s.device_position_t ?? null
    })),
    fault,
    openFaultByLine: acikArizaHatta,
    alarmedDeviceCodes: alarmliKodlar
  });

  return {
    lineName: fault.line_name ?? input.lineFallback,
    poleSeqs: hatDirekleri.map((p) => p.sequence_no),
    poles: hatDirekleri.map((p) => ({
      seq: p.sequence_no,
      name: p.name ?? null,
      role: p.topology_role ?? null
    })),
    segments: hatSegmentleri,
    fromSeq: fault.from_pole_seq ?? null,
    toSeq: fault.to_pole_seq ?? null,
    lastRedDeviceCode: fault.last_red_device_code ?? null,
    firstGreenDeviceCode: fault.first_green_device_code ?? null,
    zoneStartM: fault.zone_start_m ?? null,
    zoneEndM: fault.zone_end_m ?? null,
    alarmsByDevice,
    faultPhases,
    branchRows: bagMesafesiEkle(sahne.rows, poles, segments),
    hiddenBranchCount: sahne.hidden
  };
}

/** Ariza kaydindan cihaz basina alarm ozeti + arizali faz listesi. */
function alarmOzeti(f: BranchScanFault): {
  alarmsByDevice: Record<string, StripDeviceAlarms>;
  faultPhases: string[];
} {
  const alarmsByDevice: Record<string, StripDeviceAlarms> = {};
  const fazlar = new Set<string>();
  for (const a of f.trigger_alarms ?? []) {
    if (a.signal_source) fazlar.add(a.signal_source);
    const kod = (a.device_code ?? "").trim();
    if (!kod) continue;
    const kayit = (alarmsByDevice[kod] ??= { sources: [], titles: [] });
    if (a.signal_source && !kayit.sources.includes(a.signal_source)) {
      kayit.sources.push(a.signal_source);
    }
    const baslik = (a.title ?? "").trim();
    if (baslik && !kayit.titles.includes(baslik)) kayit.titles.push(baslik);
  }
  return { alarmsByDevice, faultPhases: Array.from(fazlar) };
}

/**
 * Kol satirlarina BAG TELININ gercek uzunlugunu ekler.
 *
 * NEDEN MESAFE INDEKSINDEN DEGIL: `buildLineDistanceIndex` kolu, asili oldugu
 * direkten BASLATIR — kolun ilk diregi ile dallanma diregi ayni mesafede
 * sayilir. Bag telinin kendi uzunlugu o modelde hic yok, fark her zaman 0
 * cikiyor ve cizimde "0 m" yaziyordu. Gercek uzunluk iki direk arasindaki
 * cografi mesafedir; giris cihazi da o telin uzerinde `t` oraninda oturur.
 */
function bagMesafesiEkle(
  rows: StripBranchRow[],
  poles: readonly SeritDirek[],
  segments: readonly SeritSegment[]
): StripBranchRow[] {
  if (rows.length === 0) return rows;
  const direkId = new Map(poles.map((p) => [p.id, p]));
  return rows.map((r) => {
    const kod = r.linkDevice?.code;
    if (!kod || r.atPoleId == null) return r;
    const direk = direkId.get(r.atPoleId);
    if (!direk) return r;
    const kolDirekIdleri = new Set(
      poles.filter((p) => p.line_id === r.lineId).map((p) => p.id)
    );
    // Bag segmenti: bir ucu kolda, bir ucu ana hatta olan giris.
    const giris = segments.find(
      (sg) =>
        sg.line_id === r.lineId &&
        sg.device_code === kod &&
        kolDirekIdleri.has(sg.from_pole_id) !== kolDirekIdleri.has(sg.to_pole_id)
    );
    if (!giris) return r;
    const otekiId = giris.from_pole_id === direk.id ? giris.to_pole_id : giris.from_pole_id;
    const oteki = direkId.get(otekiId);
    if (!oteki) return r;
    const telBoyu = haversineM(direk.latitude, direk.longitude, oteki.latitude, oteki.longitude);
    const t =
      giris.device_position_t != null &&
      giris.device_position_t >= 0 &&
      giris.device_position_t <= 1
        ? giris.device_position_t
        : 0.5;
    // Kolun ilk diregi dallanma diregiyle AYNI koordinattaysa (veri girisi)
    // mesafe anlamsizdir; "0 m" yazmak yerine hic yazma.
    const m = telBoyu * t;
    return m >= 1 ? { ...r, linkDistanceM: m } : r;
  });
}
