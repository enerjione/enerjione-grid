/**
 * ADAY HAT KESIMLERI — arizanin bulunabilecegi TUM kollari cikarir.
 *
 * NEDEN: "Ariza su iki cihaz arasinda" cumlesi tek bir tel parcasini
 * gostermiyor. O araligin icindeki bir dallanma diregine asili kol da ayni
 * anda enerjisiz kalir; ariza ana hatta da olabilir o kolda da. Harita bunu
 * zaten iki ayri kirmizi kesik olarak ciziyordu, sema ciziyordu ama sadece
 * ana hatti. Ekip hangi kollari gezecegini cizimden okuyamiyordu.
 *
 * Bu modul ariza bolgesinden baslayip asagi dogru yurur:
 *   - Ana hattaki ariza araliginda dallanan her kol ADAYDIR.
 *   - Bir kolun KENDI acik ariza kaydi varsa aday alan o kaydin araligina
 *     daralir (kol icinde nerede oldugunu cihazlar soyluyor).
 *   - Kendi kaydi yoksa kol BASTAN SONA adaydir; dolayisiyla o kolun
 *     altindaki alt kollar da adaydir (hepsi ayni beslemeden dusmustur).
 *
 * React'ten AYRI: bu bir topoloji yurumesi ve yanlis yurumek ekibi olmayan
 * bir hatta gonderir; node:test ile dogrulanabilsin diye saf tutuldu.
 */
import type { StripBranchRow } from "./faultStripGeometry";

export type BranchScanLine = {
  id: number;
  name: string;
  branched_from_pole_id?: number | null;
};

export type BranchScanPole = {
  id: number;
  line_id: number;
  sequence_no: number;
  name?: string | null;
  topology_role?: string | null;
};

export type BranchScanSegment = {
  line_id: number;
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  device_code?: string | null;
  device_name?: string | null;
  device_position_t?: number | null;
};

export type BranchScanFault = {
  line_id: number;
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  last_red_device_code?: string | null;
  first_green_device_code?: string | null;
  zone_start_m?: number | null;
  zone_end_m?: number | null;
  trigger_alarms?: {
    device_code?: string | null;
    signal_source?: string | null;
    title?: string | null;
  }[];
};

type Input = {
  lines: BranchScanLine[];
  poles: BranchScanPole[];
  segments: BranchScanSegment[];
  /** Cizilen ariza kaydi (ana satir). */
  fault: BranchScanFault;
  /** line_id -> o hatta ACIK ariza kaydi. Kolun kendi kaydi varsa bolge
   *  kesinlesir; yoksa kol bastan sona aday kalir. */
  openFaultByLine: Map<number, BranchScanFault>;
  /** Cizime sigacak en fazla kol satiri. */
  maxRows?: number;
};

/** Sahnede ana hattin altinda cizilecek en fazla kol satiri.
 *
 *  Ust sinir GORSEL bir karar: her satir tam bir hat cizimi ve dorttan
 *  fazlasi kartta okunamayacak kadar kucuk kaliyor. Sinira takilanlar
 *  sessizce dusmez — sayilari cizimde "+N" olarak yazar. */
export const MAX_BRANCH_ROWS = 4;

/** Ariza kaydindan cihaz basina alarm ozeti + arizali faz listesi. */
function alarmOzeti(f: BranchScanFault): {
  alarmsByDevice: StripBranchRow["alarmsByDevice"];
  faultPhases: string[];
} {
  const alarmsByDevice: NonNullable<StripBranchRow["alarmsByDevice"]> = {};
  const fazlar = new Set<string>();
  for (const a of f.trigger_alarms ?? []) {
    const code = (a.device_code ?? "").trim();
    if (a.signal_source) fazlar.add(a.signal_source);
    if (!code) continue;
    const entry = (alarmsByDevice[code] ??= { sources: [], titles: [] });
    if (a.signal_source && !entry.sources.includes(a.signal_source)) {
      entry.sources.push(a.signal_source);
    }
    const baslik = (a.title ?? "").trim();
    if (baslik && !entry.titles.includes(baslik)) entry.titles.push(baslik);
  }
  return { alarmsByDevice, faultPhases: Array.from(fazlar) };
}

export function buildBranchRows({
  lines,
  poles,
  segments,
  fault,
  openFaultByLine,
  maxRows = MAX_BRANCH_ROWS
}: Input): { rows: StripBranchRow[]; hidden: number } {
  /** dallanma diregi id -> o direkten cikan kollar. */
  const cocukKollar = new Map<number, BranchScanLine[]>();
  for (const ln of lines) {
    const anchorId = ln.branched_from_pole_id;
    if (!anchorId) continue;
    const arr = cocukKollar.get(anchorId);
    if (arr) arr.push(ln);
    else cocukKollar.set(anchorId, [ln]);
  }

  const direkler = new Map<number, BranchScanPole[]>();
  for (const p of poles) {
    const arr = direkler.get(p.line_id);
    if (arr) arr.push(p);
    else direkler.set(p.line_id, [p]);
  }
  for (const arr of direkler.values()) arr.sort((a, b) => a.sequence_no - b.sequence_no);

  const segmentler = new Map<number, BranchScanSegment[]>();
  for (const s of segments) {
    const arr = segmentler.get(s.line_id);
    if (arr) arr.push(s);
    else segmentler.set(s.line_id, [s]);
  }

  const direkleriniAl = (lineId: number) => direkler.get(lineId) ?? [];
  const segmentleriniAl = (lineId: number) => segmentler.get(lineId) ?? [];

  /**
   * Bir hattin SUPHELI araliginda dallanan kollari bulur.
   * `lo`/`hi` null ise hat bastan sona supheli demektir.
   */
  const araliktakiKollar = (lineId: number, lo: number | null, hi: number | null) => {
    const cikti: { line: BranchScanLine; pole: BranchScanPole }[] = [];
    for (const p of direkleriniAl(lineId)) {
      if (lo != null && hi != null && (p.sequence_no < lo || p.sequence_no > hi)) continue;
      for (const kol of cocukKollar.get(p.id) ?? []) cikti.push({ line: kol, pole: p });
    }
    return cikti;
  };

  const anaLo =
    fault.from_pole_seq != null && fault.to_pole_seq != null
      ? Math.min(fault.from_pole_seq, fault.to_pole_seq)
      : null;
  const anaHi =
    fault.from_pole_seq != null && fault.to_pole_seq != null
      ? Math.max(fault.from_pole_seq, fault.to_pole_seq)
      : null;

  // GENISLIK ONCELIKLI yuruyus: ust satirlar cizimde her zaman alt
  // satirlardan ONCE gelmeli (kol, bagli oldugu satira baglanacak).
  type Gorev = {
    line: BranchScanLine;
    pole: BranchScanPole;
    parentLineId: number | null;
  };
  const kuyruk: Gorev[] = araliktakiKollar(fault.line_id, anaLo, anaHi).map((k) => ({
    line: k.line,
    pole: k.pole,
    parentLineId: null
  }));

  const gorulen = new Set<number>([fault.line_id]);
  const rows: StripBranchRow[] = [];
  let hidden = 0;

  while (kuyruk.length > 0) {
    const gorev = kuyruk.shift()!;
    if (gorulen.has(gorev.line.id)) continue; // dongusel topolojiye karsi
    gorulen.add(gorev.line.id);

    if (rows.length >= maxRows) {
      hidden += 1;
      continue;
    }

    const kolDirekleri = direkleriniAl(gorev.line.id);
    const kendiKaydi = openFaultByLine.get(gorev.line.id) ?? null;
    const ozet = kendiKaydi
      ? alarmOzeti(kendiKaydi)
      : { alarmsByDevice: undefined, faultPhases: [] };

    rows.push({
      lineId: gorev.line.id,
      name: gorev.line.name,
      parentLineId: gorev.parentLineId,
      atSeq: gorev.pole.sequence_no,
      atPoleName: gorev.pole.name ?? null,
      poleSeqs: kolDirekleri.map((p) => p.sequence_no),
      poles: kolDirekleri.map((p) => ({
        seq: p.sequence_no,
        name: p.name ?? null,
        role: p.topology_role ?? null
      })),
      segments: segmentleriniAl(gorev.line.id),
      fromSeq: kendiKaydi?.from_pole_seq ?? null,
      toSeq: kendiKaydi?.to_pole_seq ?? null,
      lastRedDeviceCode: kendiKaydi?.last_red_device_code ?? null,
      firstGreenDeviceCode: kendiKaydi?.first_green_device_code ?? null,
      zoneStartM: kendiKaydi?.zone_start_m ?? null,
      zoneEndM: kendiKaydi?.zone_end_m ?? null,
      faultPhases: ozet.faultPhases,
      alarmsByDevice: ozet.alarmsByDevice,
      confirmed: Boolean(kendiKaydi)
    });

    // ALT KOLLAR: kolun kendi kaydi varsa yalnizca o araliktakiler, yoksa
    // kolun tamami supheli oldugu icin hepsi.
    const altLo =
      kendiKaydi?.from_pole_seq != null && kendiKaydi.to_pole_seq != null
        ? Math.min(kendiKaydi.from_pole_seq, kendiKaydi.to_pole_seq)
        : null;
    const altHi =
      kendiKaydi?.from_pole_seq != null && kendiKaydi.to_pole_seq != null
        ? Math.max(kendiKaydi.from_pole_seq, kendiKaydi.to_pole_seq)
        : null;
    for (const alt of araliktakiKollar(gorev.line.id, altLo, altHi)) {
      if (gorulen.has(alt.line.id)) continue;
      kuyruk.push({ line: alt.line, pole: alt.pole, parentLineId: gorev.line.id });
    }
  }

  return { rows, hidden };
}
