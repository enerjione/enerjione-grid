/**
 * ADAY HAT KESIMLERI — ariza bolgesinde hangi kollar var?
 *
 * "Ariza su iki cihaz arasinda" cumlesi tek bir tel parcasini gostermiyor:
 * o araligin icindeki bir dallanma diregine asili kol da ayni anda enerjisiz
 * kalir. Bu yuruyus yanlis olursa ekip ya olmayan bir kola gider ya da
 * gezmesi gereken kolu HIC gormez — ikincisi sessiz ve agir bir hata.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { buildStripGeometry } from "../src/features/faults/faultStripGeometry";
import {
  buildBranchRows,
  type BranchScanFault,
  type BranchScanLine,
  type BranchScanPole,
  type BranchScanSegment
} from "../src/features/faults/branchRows";

/**
 * Test sebekesi:
 *
 *   ANA (1): direk 1..5   (pole id 101..105)
 *     ├─ BR-A (2)  direk 3'ten (pole 103)   → direk 1..3 (201..203)
 *     │    └─ BR-A1 (4)  BR-A'nin 1. direginden (201)
 *     └─ BR-UZAK (3) direk 1'den (pole 101) → ariza bolgesi disinda
 */
const LINES: BranchScanLine[] = [
  { id: 1, name: "ANA" },
  { id: 2, name: "BR-A", branched_from_pole_id: 103 },
  { id: 3, name: "BR-UZAK", branched_from_pole_id: 101 },
  { id: 4, name: "BR-A1", branched_from_pole_id: 201 }
];

const POLES: BranchScanPole[] = [
  ...[1, 2, 3, 4, 5].map((s) => ({
    id: 100 + s,
    line_id: 1,
    sequence_no: s,
    name: `ANA-${s}`,
    topology_role: s === 3 ? "branch" : "transit"
  })),
  ...[1, 2, 3].map((s) => ({ id: 200 + s, line_id: 2, sequence_no: s, name: `A-${s}` })),
  ...[1, 2].map((s) => ({ id: 300 + s, line_id: 3, sequence_no: s, name: `U-${s}` })),
  ...[1, 2].map((s) => ({ id: 400 + s, line_id: 4, sequence_no: s, name: `A1-${s}` }))
];

const SEGMENTS: BranchScanSegment[] = [
  { line_id: 2, from_pole_seq: 1, to_pole_seq: 2, device_code: "SN2-A1" }
];

/** Ana hatta 2-4 direkleri arasinda ariza: ICINDE kalan tek direk 3.
 *  Sinirlar (2 ve 4) arizanin SAGLAM tarafinda kalir. */
const ANA_ARIZA: BranchScanFault = { line_id: 1, from_pole_seq: 2, to_pole_seq: 4 };

function calistir(
  openFaults: [number, BranchScanFault][] = [],
  maxRows?: number
) {
  return buildBranchRows({
    lines: LINES,
    poles: POLES,
    segments: SEGMENTS,
    fault: ANA_ARIZA,
    openFaultByLine: new Map(openFaults),
    maxRows
  });
}

test("yalnizca ARIZA BOLGESINDEKI dallanma kollari aday sayilir", () => {
  const { rows } = calistir();
  const adlar = rows.map((r) => r.name);
  assert.ok(adlar.includes("BR-A"), "bolgedeki kol atlanmis");
  assert.ok(!adlar.includes("BR-UZAK"), "bolge disindaki kol aday sayilmis");
});

test("kol hangi DIREKTEN ciktigini tasir", () => {
  const { rows } = calistir();
  const kol = rows.find((r) => r.name === "BR-A");
  assert.ok(kol);
  assert.equal(kol.atSeq, 3);
  assert.equal(kol.atPoleName, "ANA-3");
  assert.equal(kol.parentLineId, null, "ana hattan cikan kolun ust satiri ana hat");
});

test("kolun kendi direkleri ve segmentleri satira tasinir", () => {
  // Kol "ana hattan cikan bir cizgi" degil kendi hattidir: kendi direkleri
  // olmadan ekip kolun neresine gidecegini bilemez.
  const { rows } = calistir();
  const kol = rows.find((r) => r.name === "BR-A");
  assert.ok(kol);
  assert.deepEqual(kol.poleSeqs, [1, 2, 3]);
  assert.equal(kol.segments?.length, 1);
  assert.equal(kol.confirmed, false, "kendi kaydi yokken dogrulanmis sayilmamali");
});

test("kendi kaydi olan kol DOGRULANMIS gelir ve bolgesini tasir", () => {
  const kendi: BranchScanFault = {
    line_id: 2,
    from_pole_seq: 2,
    to_pole_seq: 3,
    last_red_device_code: "SN2-A1",
    zone_start_m: 120,
    zone_end_m: 260,
    trigger_alarms: [
      { device_code: "SN2-A1", signal_source: "sat01", title: "Kalici ariza" }
    ]
  };
  const { rows } = calistir([[2, kendi]]);
  const kol = rows.find((r) => r.name === "BR-A");
  assert.ok(kol);
  assert.equal(kol.confirmed, true);
  assert.equal(kol.fromSeq, 2);
  assert.equal(kol.toSeq, 3);
  assert.equal(kol.zoneStartM, 120);
  assert.deepEqual(kol.faultPhases, ["sat01"], "arizali faz kolun kaydindan gelmeli");
  assert.deepEqual(kol.alarmsByDevice?.["SN2-A1"]?.sources, ["sat01"]);
});

test("kendi kaydi OLMAYAN kolun ALT kollari da adaydir", () => {
  // Kol bastan sona enerjisizse altindaki her sey de enerjisizdir.
  const { rows } = calistir();
  const alt = rows.find((r) => r.name === "BR-A1");
  assert.ok(alt, "alt kol adaylardan dusmus");
  assert.equal(alt.parentLineId, 2, "alt kol kendi ust koluna baglanmali");
  // Ust satirlar cizimde HER ZAMAN once gelmeli; yoksa bag cizilecek satiri
  // bulamaz.
  assert.ok(
    rows.findIndex((r) => r.lineId === 2) < rows.findIndex((r) => r.lineId === 4),
    "ust kol alt koldan sonra siralanmis"
  );
});

test("kendi kaydi OLAN kolun alt kollari yalnizca O ARALIKTA aday olur", () => {
  // BR-A1, BR-A'nin 1. diregine asili; BR-A'nin arizasi 2-3 arasinda ise
  // 1. direk saglam taraftadir, kol enerjisiz degildir.
  const { rows } = calistir([[2, { line_id: 2, from_pole_seq: 2, to_pole_seq: 3 }]]);
  assert.ok(
    !rows.some((r) => r.name === "BR-A1"),
    "bolge disindaki alt kol aday sayilmis"
  );
});

/* ---------------------------------------------------------------------------
 * ADAY KUMESI CIZIMDEN TURETILIR
 *
 * Bolge once dogrudan `from_pole_seq`..`to_pole_seq` araligiydi. Cizim (ve
 * harita) ise bolgeyi CIHAZLARDAN turetir: son "gordum" diyenden ilk
 * "gormedim" diyene, gormeyen yoksa hat ucuna kadar. Iki hesap ayrisinca
 * cihazin YUKARI tarafindaki — haritada yemyesil duran — bir direge asili kol
 * "kontrol edilmeli" diye isaretleniyor, ekip arizasiz bir kolu gezmeye
 * gidiyordu.
 * ------------------------------------------------------------------------- */

/** Ana hatta 3-4 arasinda "gordum" diyen cihaz; "gormedim" diyen YOK. */
const CIHAZLI_SEGMENTLER: BranchScanSegment[] = [
  ...SEGMENTS,
  { line_id: 1, from_pole_seq: 3, to_pole_seq: 4, device_code: "SN2-RED" }
];
const CIHAZLI_ARIZA: BranchScanFault = {
  line_id: 1,
  from_pole_seq: 3,
  to_pole_seq: 4,
  last_red_device_code: "SN2-RED"
};

function cihazliCalistir(kolDiregiId: number) {
  return buildBranchRows({
    lines: [
      { id: 1, name: "ANA" },
      { id: 9, name: "BR-X", branched_from_pole_id: kolDiregiId }
    ],
    poles: POLES,
    segments: CIHAZLI_SEGMENTLER,
    fault: CIHAZLI_ARIZA,
    openFaultByLine: new Map()
  });
}

test("cihazin YUKARI tarafindaki direge asili kol aday DEGILDIR", () => {
  // Cihaz 3-4 arasinda; 3 nolu direk arizanin saglam tarafinda kalir ve
  // cizimde GRI cizilir. Oradan cikan kol enerjilidir.
  assert.equal(cihazliCalistir(103).rows.length, 0, "saglam taraftaki kol aday sayilmis");
});

test("cihazin ASAGI tarafindaki direge asili kol ADAYDIR", () => {
  // "Gormedim" diyen cihaz yok: ariza cihazdan hat ucuna kadar herhangi bir
  // yerde olabilir, aradaki her direk cizimde kirmizi.
  for (const [poleId, seq] of [[104, 4], [105, 5]] as const) {
    const { rows } = cihazliCalistir(poleId);
    assert.equal(rows.length, 1, `seq ${seq} kolu adaylardan dusmus`);
    assert.equal(rows[0].atSeq, seq);
  }
});

test("aday kumesi cizimin KIRMIZI direkleriyle ayni", () => {
  // Bu testin isi iki hesabin bir daha ayrismamasini saglamak: cizim
  // `ceil(span.a)..floor(span.b)` araligini kirmizi boyar, aday taramasi da
  // ayni araligi kullanmali.
  const geo = buildStripGeometry({
    poleSeqs: [1, 2, 3, 4, 5],
    segments: CIHAZLI_SEGMENTLER.filter((s) => s.line_id === 1),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED"
  });
  assert.ok(geo.span);
  const kirmizi = new Set<number>();
  for (let i = Math.ceil(geo.span.a); i <= Math.floor(geo.span.b); i += 1) {
    kirmizi.add(geo.seqs[i]);
  }
  for (const p of POLES.filter((x) => x.line_id === 1)) {
    const aday = cihazliCalistir(p.id).rows.length > 0;
    assert.equal(
      aday,
      kirmizi.has(p.sequence_no),
      `direk ${p.sequence_no}: aday=${aday} ama cizimde kirmizi=${kirmizi.has(p.sequence_no)}`
    );
  }
});

test("hicbir cihaz gormediyse aday kol OLMAZ", () => {
  const { rows } = buildBranchRows({
    lines: LINES,
    poles: POLES,
    segments: SEGMENTS,
    fault: { line_id: 1, from_pole_seq: null, to_pole_seq: null },
    openFaultByLine: new Map()
  });
  assert.equal(rows.length, 0, "bolge yokken kol aday sayilmis");
});

test("cizime sigmayan kollar SESSIZCE dusmez — sayilir", () => {
  const { rows, hidden } = calistir([], 1);
  assert.equal(rows.length, 1);
  assert.equal(hidden, 1, "sigmayan kol sayilmali");
});

test("dongusel topoloji sonsuz donguye girmez", () => {
  const dongulu: BranchScanLine[] = [
    { id: 1, name: "ANA" },
    { id: 2, name: "BR-A", branched_from_pole_id: 103 },
    // Bozuk kayit: ana hat kendi kolunun diregine asili gorunuyor.
    { id: 1, name: "ANA", branched_from_pole_id: 201 }
  ];
  const { rows } = buildBranchRows({
    lines: dongulu,
    poles: POLES,
    segments: SEGMENTS,
    fault: ANA_ARIZA,
    openFaultByLine: new Map()
  });
  assert.ok(rows.length <= 2);
  assert.ok(!rows.some((r) => r.lineId === 1), "ana hat kendi altina cizilmis");
});
