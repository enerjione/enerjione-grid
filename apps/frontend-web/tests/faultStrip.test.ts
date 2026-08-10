/**
 * Ariza seridi geometrisi — cihaz TELIN UZERINDE mi, kirmizi parca DOGRU
 * cihazlar arasinda mi?
 *
 * Bu cizim operatore "ekibi nereye gonderecegim" diyor. Bir isaret 20 piksel
 * kayarsa kimse fark etmez ama sahada yanlis direge gidilir; bu yuzden
 * matematik React'ten ayri tutuldu ve burada dogrulaniyor.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SAG,
  WIRE_Y,
  buildStripGeometry,
  hotPathOf,
  sagAt
} from "../src/features/faults/faultStripGeometry";

const POLES = [1, 2, 3, 4, 5];

/** 3-4 arasinda "gordum", 4-5 arasinda "gormedim" cihazi olan hat. */
function segments() {
  return [
    { from_pole_seq: 1, to_pole_seq: 2, device_code: "SN2-A", device_position_t: 0.5 },
    { from_pole_seq: 3, to_pole_seq: 4, device_code: "SN2-RED", device_position_t: 0.25 },
    { from_pole_seq: 4, to_pole_seq: 5, device_code: "SN2-GREEN", device_position_t: 0.75 }
  ];
}

test("tel uclarda direge yapisik, ortada SARKAR", () => {
  assert.equal(sagAt(0), 0, "span basinda sarkma olmamali");
  assert.equal(sagAt(1), 0, "span sonunda sarkma olmamali");
  assert.equal(sagAt(0.5), SAG, "en dusuk nokta span ortasinda olmali");
  // SVG'de y asagi dogru artar: sarkan tel DAHA BUYUK y'de olmali.
  const geo = buildStripGeometry({ poleSeqs: POLES, fromSeq: 3, toSeq: 4 });
  assert.ok(geo.pointAt(0.5).y > geo.pointAt(0).y, "tel yukari dogru kavis yapiyor");
});

test("cihazlar telin UZERINDE duruyor", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: segments(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  assert.equal(geo.devices.length, 3);
  for (const d of geo.devices) {
    const p = geo.pointAt(d.pos);
    const beklenen = WIRE_Y + sagAt(d.pos - Math.floor(d.pos));
    assert.ok(
      Math.abs(p.y - beklenen) < 1e-9,
      `${d.code} telin uzerinde degil (y=${p.y}, beklenen=${beklenen})`
    );
  }
});

test("cihaz segment icindeki GERCEK oraninda konumlanir", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: segments(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED"
  });
  const red = geo.devices.find((d) => d.code === "SN2-RED");
  assert.ok(red);
  // 3-4 arasi = span indeksi 2; t=0.25.
  assert.equal(red.pos, 2.25);
  const x3 = geo.xOf(2);
  const x4 = geo.xOf(3);
  assert.ok(Math.abs(geo.pointAt(red.pos).x - (x3 + (x4 - x3) * 0.25)) < 1e-9);
});

test("KIRMIZI PARCA son 'gordum' ile ilk 'gormedim' cihazi ARASINDA", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: segments(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  assert.ok(geo.span, "arizali parca hesaplanmadi");
  assert.equal(geo.span.byDevice, true, "sinirlar cihazlardan gelmeliydi");
  assert.equal(geo.span.a, 2.25, "baslangic kirmizi cihaz konumu olmali");
  assert.equal(geo.span.b, 3.75, "bitis yesil cihaz konumu olmali");
});

test("yesil cihaz yoksa ariza HAT UCUNA kadar surer", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: segments().slice(0, 2),
    fromSeq: 3,
    toSeq: 5,
    lastRedDeviceCode: "SN2-RED"
  });
  assert.ok(geo.span);
  assert.equal(geo.span.a, 2.25);
  assert.equal(geo.span.b, POLES.length - 1, "hat ucunda bitmeliydi");
});

test("cihaz bilgisi yoksa DIREK araligina duser (gerileme yok)", () => {
  const geo = buildStripGeometry({ poleSeqs: POLES, fromSeq: 3, toSeq: 4 });
  assert.ok(geo.span);
  assert.equal(geo.span.byDevice, false);
  assert.equal(geo.span.a, 2, "3 nolu direk 2. indekste");
  assert.equal(geo.span.b, 3);
});

test("kirmizi path TAM cihaz konumunda baslar ve biter", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: segments(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  const path = hotPathOf(geo);
  const bas = geo.pointAt(geo.span!.a);
  const son = geo.pointAt(geo.span!.b);
  assert.ok(
    path.startsWith(`M${bas.x.toFixed(1)} ${bas.y.toFixed(1)}`),
    `path kirmizi cihazdan baslamiyor: ${path.slice(0, 40)}`
  );
  assert.ok(
    path.endsWith(`L${son.x.toFixed(1)} ${son.y.toFixed(1)}`),
    `path yesil cihazda bitmiyor: ${path.slice(-40)}`
  );
});

test("tel SUREKLI — kopukluk veya geri donus yok", () => {
  const geo = buildStripGeometry({ poleSeqs: POLES, fromSeq: 1, toSeq: 2 });
  for (let i = 1; i < geo.wire.length; i += 1) {
    assert.ok(geo.wire[i].x > geo.wire[i - 1].x, `tel ${i}. noktada geri donuyor`);
    assert.ok(geo.wire[i].pos > geo.wire[i - 1].pos, `pos ${i}. noktada artmiyor`);
  }
  // Son nokta son direge ulasmali.
  const sonX = geo.wire[geo.wire.length - 1].x;
  assert.ok(Math.abs(sonX - geo.xOf(POLES.length - 1)) < 1e-9, "tel son direge ulasmiyor");
});

test("komsu OLMAYAN segment cihazi cizilmez (bozuk veri yanlis yere koymasin)", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: [{ from_pole_seq: 1, to_pole_seq: 4, device_code: "SN2-BOZUK" }],
    fromSeq: 1,
    toSeq: 2
  });
  assert.equal(geo.devices.length, 0);
});

test("tek direkli/bos hatta cokme yok", () => {
  const geo = buildStripGeometry({ poleSeqs: [], fromSeq: null, toSeq: null });
  assert.ok(geo.seqs.length >= 2, "en az iki direk uydurulmali");
  assert.ok(geo.wire.length > 0, "tel bos kalmamali");
  assert.equal(geo.span, null);
});
