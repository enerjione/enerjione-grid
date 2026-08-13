/**
 * Sematik cizimin GIRDI hazirligi.
 *
 * Bu turetmeler once ariza LISTESI sayfasinin `useMemo` yiginlarinda
 * yasiyordu. Cizim ikinci bir ekrana (ariza detayi) konulunca ayni hesabin
 * kopyalanmasi gerekti; kopya ayrisirsa iki ekran AYNI arizada farkli kol
 * gosterir ve ekip yanlis yere gider. Modul bu yuzden saf ve testli.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { buildFaultStripInputs } from "../src/features/faults/faultStripInputs";
import type { SeritAlarm, SeritAriza } from "../src/features/faults/faultStripInputs";

/** Ana hat: 5 direk; 3. direk bransman diregi. Kol (BR-3) oradan cikar. */
const HATLAR = [
  { id: 1, name: "ANA HAT" },
  { id: 2, name: "BR-3", branched_from_pole_id: 3 }
];

const DIREKLER = [
  { id: 1, line_id: 1, sequence_no: 1, name: "D1", latitude: 39, longitude: 35.0 },
  { id: 2, line_id: 1, sequence_no: 2, name: "D2", latitude: 39, longitude: 35.1 },
  {
    id: 3,
    line_id: 1,
    sequence_no: 3,
    name: "D3",
    topology_role: "branch",
    latitude: 39,
    longitude: 35.2
  },
  { id: 4, line_id: 1, sequence_no: 4, name: "D4", latitude: 39, longitude: 35.3 },
  { id: 5, line_id: 1, sequence_no: 5, name: "D5", latitude: 39, longitude: 35.4 },
  // Kolun direkleri: ana hattan 0.01 derece (~1.1 km) kuzeyde.
  { id: 20, line_id: 2, sequence_no: 1, name: "K1", latitude: 39.01, longitude: 35.2 },
  { id: 21, line_id: 2, sequence_no: 2, name: "K2", latitude: 39.02, longitude: 35.2 }
];

const SEGMENTLER = [
  {
    line_id: 1,
    from_pole_id: 2,
    to_pole_id: 3,
    from_pole_seq: 2,
    to_pole_seq: 3,
    device_code: "D-A",
    device_position_t: 0.5
  },
  {
    line_id: 1,
    from_pole_id: 3,
    to_pole_id: 4,
    from_pole_seq: 3,
    to_pole_seq: 4,
    device_code: "D-B",
    device_position_t: 0.5
  },
  // BAG TELI: bir ucu ana hatta (3. direk), digeri kolun ilk direginde.
  {
    line_id: 2,
    from_pole_id: 3,
    to_pole_id: 20,
    from_pole_seq: 3,
    to_pole_seq: 1,
    device_code: "BR-DEV",
    device_position_t: 0.5
  }
];

const ARIZA: SeritAriza = {
  id: 1,
  status: "open",
  line_id: 1,
  line_name: "ANA HAT",
  from_pole_seq: 2,
  to_pole_seq: 4,
  last_red_device_code: "D-A",
  first_green_device_code: "D-B",
  trigger_alarms: [
    { device_code: "D-A", signal_source: "master", title: "Faz-toprak arizasi" },
    { device_code: "D-A", signal_source: "sat01", title: "Faz-toprak arizasi" }
  ]
};

const CIHAZLAR = [
  { id: 101, code: "D-A" },
  { id: 102, code: "D-B" },
  { id: 103, code: "BR-DEV" }
];

const kur = (alarms: SeritAlarm[] = [{ device_id: 101, reset: false }]) =>
  buildFaultStripInputs({
    lines: HATLAR,
    poles: DIREKLER,
    segments: SEGMENTLER,
    fault: ARIZA,
    faults: [ARIZA],
    alarms,
    devices: CIHAZLAR,
    lineFallback: "Hat"
  });

test("yalnizca ARIZALI hattin direk ve segmentleri cizime girer", () => {
  const g = kur()!;
  assert.deepEqual(g.poleSeqs, [1, 2, 3, 4, 5]);
  assert.deepEqual(
    g.poles.map((p) => [p.seq, p.name, p.role ?? null]),
    [
      [1, "D1", null],
      [2, "D2", null],
      [3, "D3", "branch"],
      [4, "D4", null],
      [5, "D5", null]
    ]
  );
  // Kolun bag teli ana hattin segmenti degil; ana satirda cizilmemeli.
  assert.deepEqual(
    g.segments.map((s) => s.device_code),
    ["D-A", "D-B"]
  );
  assert.equal(g.lineName, "ANA HAT");
});

test("hat adi kayitta yoksa YEDEK etiket kullanilir", () => {
  const g = buildFaultStripInputs({
    lines: HATLAR,
    poles: DIREKLER,
    segments: SEGMENTLER,
    fault: { ...ARIZA, line_name: null },
    faults: [],
    alarms: [],
    devices: CIHAZLAR,
    lineFallback: "Hat"
  })!;
  assert.equal(g.lineName, "Hat");
});

test("alarm ozeti cihaz basina toplanir, FAZLAR tekillesir", () => {
  const g = kur()!;
  assert.deepEqual(g.alarmsByDevice["D-A"].sources, ["master", "sat01"]);
  // Iki alarm ayni basligi tasiyor; ipucu iki kez yazilmamali.
  assert.deepEqual(g.alarmsByDevice["D-A"].titles, ["Faz-toprak arizasi"]);
  assert.deepEqual(g.faultPhases.sort(), ["master", "sat01"]);
  assert.equal(g.alarmsByDevice["D-B"], undefined);
});

test("ariza bolgesindeki dallanma diregine asili kol ADAY olarak cizilir", () => {
  const g = kur()!;
  assert.equal(g.branchRows.length, 1);
  const kol = g.branchRows[0];
  assert.equal(kol.lineId, 2);
  assert.equal(kol.atSeq, 3, "kol yanlis direkten asilmis");
  assert.equal(kol.atPoleName, "D3");
  // Kolun kendi ariza kaydi yok: bolge kesinlesmedi.
  assert.equal(kol.confirmed, false);
  assert.equal(g.hiddenBranchCount, 0);
});

test("RESETLENMIS alarm cihaz 'arizayi gordu' saymaz", () => {
  const kirmizi = kur([
    { device_id: 101, reset: false },
    { device_id: 103, reset: false }
  ])!.branchRows[0];
  const resetli = kur([
    { device_id: 101, reset: false },
    { device_id: 103, reset: true }
  ])!.branchRows[0];
  assert.equal(kirmizi.linkDevice?.code, "BR-DEV");
  assert.equal(kirmizi.linkDevice?.tone, "red");
  assert.notEqual(resetli.linkDevice?.tone, "red");
});

test("ariza URETMEYEN alarm (haberlesme) kolu supheli yapmaz", () => {
  // Haberlesme alarmi ariza uretmez; kolu kirmiziya boyamasi ekibi olmayan
  // bir arizaya gonderirdi (bkz. alarm_reconciliation.py).
  const g = kur([
    { device_id: 101, reset: false },
    { device_id: 103, reset: false, produces_fault: false }
  ])!;
  assert.notEqual(g.branchRows[0].linkDevice?.tone, "red");
});

test("bag telinin GERCEK uzunlugu eklenir — '0 m' yazilmaz", () => {
  const g = kur()!;
  const m = g.branchRows[0].linkDistanceM;
  // 0.01 derece enlem ~1111 m; cihaz telin ortasinda (t = 0.5).
  assert.ok(m != null && m > 500 && m < 620, `bag mesafesi hatali: ${m}`);
});

test("kolun ilk diregi dallanma diregiyle AYNI noktadaysa mesafe yazilmaz", () => {
  const g = buildFaultStripInputs({
    lines: HATLAR,
    // Veri girisi kolun ilk diregini ana direkle ayni koordinata koymus.
    poles: DIREKLER.map((p) => (p.id === 20 ? { ...p, latitude: 39, longitude: 35.2 } : p)),
    segments: SEGMENTLER,
    fault: ARIZA,
    faults: [ARIZA],
    alarms: [{ device_id: 101, reset: false }],
    devices: CIHAZLAR,
    lineFallback: "Hat"
  })!;
  assert.equal(g.branchRows[0].linkDistanceM ?? null, null);
});

test("kolun KENDI acik kaydi varsa bolgesi kesinlesir", () => {
  const kolArizasi: SeritAriza = {
    id: 2,
    status: "assigned",
    line_id: 2,
    line_name: "BR-3",
    from_pole_seq: 1,
    to_pole_seq: 2
  };
  const g = buildFaultStripInputs({
    lines: HATLAR,
    poles: DIREKLER,
    segments: SEGMENTLER,
    fault: ARIZA,
    faults: [ARIZA, kolArizasi],
    alarms: [{ device_id: 101, reset: false }],
    devices: CIHAZLAR,
    lineFallback: "Hat"
  })!;
  assert.equal(g.branchRows[0].confirmed, true);
});

test("KAPATILMIS kol kaydi adayligi kesinlestirmez", () => {
  const g = buildFaultStripInputs({
    lines: HATLAR,
    poles: DIREKLER,
    segments: SEGMENTLER,
    fault: ARIZA,
    faults: [ARIZA, { id: 2, status: "closed", line_id: 2, line_name: "BR-3" }],
    alarms: [{ device_id: 101, reset: false }],
    devices: CIHAZLAR,
    lineFallback: "Hat"
  })!;
  assert.equal(g.branchRows[0].confirmed, false);
});

test("hatta direk yoksa null — bos bir cizim hatti SAGLAM gosterirdi", () => {
  const g = buildFaultStripInputs({
    lines: HATLAR,
    poles: DIREKLER.filter((p) => p.line_id !== 1),
    segments: SEGMENTLER,
    fault: ARIZA,
    faults: [ARIZA],
    alarms: [],
    devices: CIHAZLAR,
    lineFallback: "Hat"
  });
  assert.equal(g, null);
});
