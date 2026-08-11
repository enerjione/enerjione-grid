/**
 * Bransman baglantilarinin haritada cizilmesi.
 *
 * YASANAN SORUN: "Diger hatlar" acikken bransman kollari sisteme BAGLI
 * DEGILMIS gibi goruntuluyordu — tek direkli kol hic cizilmiyor (polyline
 * iki nokta ister), iki direkli kol ise havada asili duruyordu. Operator
 * bunu "kol eksik/bozuk" diye okuyor ve dallanma noktasina cihaz
 * yerlestiremiyordu; oysa orasi arizanin ana hatta mi kolda mi oldugunu
 * ayirt eden olcum noktasi.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { branchConnectors } from "../src/features/grid/branchConnectors";

const P = (id: number, lineId: number, seq: number, lat: number, lon: number) => ({
  id,
  line_id: lineId,
  sequence_no: seq,
  latitude: lat,
  longitude: lon
});

/** Ana hat: 3 direk. Kol: 4 numarali direkten dallaniyor. */
const ANA = [P(1, 1, 1, 39.0, 35.0), P(2, 1, 2, 39.1, 35.1), P(3, 1, 3, 39.2, 35.2)];

test("TEK direkli kol da baglanti cizgisi uretir", () => {
  // Asil sikayet buydu: tek direkli kol icin polyline cizilemiyor ve
  // haritada yalniz bir gri nokta kaliyordu.
  const c = branchConnectors({
    lines: [{ id: 2, name: "BR-2", branched_from_pole_id: 2 }],
    poles: [...ANA, P(10, 2, 1, 39.15, 35.2)],
    segments: []
  });
  assert.equal(c.length, 1);
  assert.deepEqual(c[0].from, [39.1, 35.1], "dallanma diregi yanlis");
  assert.deepEqual(c[0].to, [39.15, 35.2], "kolun ilk diregi yanlis");
});

test("kolun ILK diregi sequence_no'ya gore secilir, dizi sirasina gore degil", () => {
  // Backend siralamayi garanti etmiyor; topoloji duzenlenince sira
  // numaralari yeniden atanabiliyor. Dizi sirasina guvenmek baglantiyi
  // kolun ORTASINA cizerdi.
  const c = branchConnectors({
    lines: [{ id: 2, name: "BR-4", branched_from_pole_id: 2 }],
    poles: [...ANA, P(11, 2, 3, 39.4, 35.4), P(12, 2, 1, 39.15, 35.2), P(13, 2, 2, 39.3, 35.3)],
    segments: []
  });
  assert.equal(c[0].firstPoleId, 12);
  assert.deepEqual(c[0].to, [39.15, 35.2]);
});

test("bransman OLMAYAN hat baglanti uretmez", () => {
  const c = branchConnectors({
    lines: [
      { id: 1, name: "ANA HAT", branched_from_pole_id: null },
      { id: 3, name: "BAGIMSIZ" } // alan hic yok
    ],
    poles: ANA,
    segments: []
  });
  assert.deepEqual(c, []);
});

test("dallanma diregi bulunamazsa baglanti DUSER — patlamaz", () => {
  // Direk silinmis olabilir (FK SET NULL oncesi kayitlar). `undefined.latitude`
  // tum haritayi patlatirdi.
  const c = branchConnectors({
    lines: [{ id: 2, name: "BR-2", branched_from_pole_id: 999 }],
    poles: [...ANA, P(10, 2, 1, 39.15, 35.2)],
    segments: []
  });
  assert.deepEqual(c, []);
});

test("direksiz kol baglanti uretmez", () => {
  const c = branchConnectors({
    lines: [{ id: 2, name: "BOS KOL", branched_from_pole_id: 2 }],
    poles: ANA,
    segments: []
  });
  assert.deepEqual(c, []);
});

test("kolun ilk diregi dallanma diregiyle AYNI ise cizilmez", () => {
  // Sifir uzunluklu cizgi haritada nokta gibi gorunur ve "burada bir sey
  // var" yanilgisi uretir.
  const c = branchConnectors({
    lines: [{ id: 2, name: "BR-2", branched_from_pole_id: 2 }],
    poles: [...ANA],
    segments: []
  });
  // Kolun kendi diregi yok; ayrica parent'i kendi diregi saymamali.
  assert.deepEqual(c, []);

  const c2 = branchConnectors({
    lines: [{ id: 1, name: "KENDINE", branched_from_pole_id: 1 }],
    poles: ANA,
    segments: []
  });
  assert.deepEqual(c2, [], "hat kendi ilk diregine dallanmis gorunuyor");
});

test("BOZUK koordinat baglantiyi sizdirmaz", () => {
  const c = branchConnectors({
    lines: [{ id: 2, name: "BR-2", branched_from_pole_id: 2 }],
    poles: [...ANA, P(10, 2, 1, Number.NaN, 35.2)],
    segments: []
  });
  assert.deepEqual(c, []);
});

test("baglanti segmenti KURULMUSSA isaretlenir", () => {
  // Harita "bagli ama segment yok" ile "segment var" arasindaki farki
  // gostermeli: segment yoksa cihaz baglanamaz.
  const kol = { id: 2, name: "BR-2", branched_from_pole_id: 2 };
  const direkler = [...ANA, P(10, 2, 1, 39.15, 35.2)];

  const yok = branchConnectors({ lines: [kol], poles: direkler, segments: [] });
  assert.equal(yok[0].hasSegment, false);
  assert.equal(yok[0].hasDevice, false);

  const var_ = branchConnectors({
    lines: [kol],
    poles: direkler,
    segments: [{ from_pole_id: 2, to_pole_id: 10, device_id: null }]
  });
  assert.equal(var_[0].hasSegment, true);
  assert.equal(var_[0].hasDevice, false, "cihazsiz segment 'cihaz var' demiyor");

  const cihazli = branchConnectors({
    lines: [kol],
    poles: direkler,
    segments: [{ from_pole_id: 2, to_pole_id: 10, device_id: 7 }]
  });
  assert.equal(cihazli[0].hasDevice, true);
});

test("TERS yondeki segment baglanti sayilmaz", () => {
  // Yon anlamlidir: `from` dallanma diregi, `to` kolun ilk diregi. Ters
  // kayit baska bir seydir; onu "baglanti kurulmus" saymak, kurulmamis bir
  // baglantiyi kurulmus gostermek olurdu.
  const c = branchConnectors({
    lines: [{ id: 2, name: "BR-2", branched_from_pole_id: 2 }],
    poles: [...ANA, P(10, 2, 1, 39.15, 35.2)],
    segments: [{ from_pole_id: 10, to_pole_id: 2, device_id: 5 }]
  });
  assert.equal(c[0].hasSegment, false);
  assert.equal(c[0].hasDevice, false);
});

test("cok sayida kol AYRI AYRI doner", () => {
  const c = branchConnectors({
    lines: [
      { id: 2, name: "BR-2", branched_from_pole_id: 2 },
      { id: 3, name: "BR-3", branched_from_pole_id: 3 },
      { id: 1, name: "ANA HAT", branched_from_pole_id: null }
    ],
    poles: [...ANA, P(10, 2, 1, 39.15, 35.2), P(20, 3, 1, 39.25, 35.3)],
    segments: []
  });
  assert.deepEqual(
    c.map((x) => x.lineName).sort(),
    ["BR-2", "BR-3"]
  );
});

test("ayni direkten cikan IKI kol ikisi de cizilir", () => {
  // Bir direkten birden fazla dal cikabilir; biri digerini gizlememeli.
  const c = branchConnectors({
    lines: [
      { id: 2, name: "BR-2", branched_from_pole_id: 2 },
      { id: 3, name: "BR-3", branched_from_pole_id: 2 }
    ],
    poles: [...ANA, P(10, 2, 1, 39.15, 35.2), P(20, 3, 1, 39.05, 35.15)],
    segments: []
  });
  assert.equal(c.length, 2);
  assert.notDeepEqual(c[0].to, c[1].to);
});
