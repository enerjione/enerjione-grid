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

// --------------------------------------------------------------- OLCEK

test("olcek DIREK SAYISINDAN bagimsiz: bir aralik her hatta ayni genislikte", () => {
  // Cizim eskiden `width:100%` ile esniyordu; 6 direkli hat kartin
  // genisligine yayilip devasa, 17 direkli hat ayni alana sikisip minicik
  // goruniyordu. Iki ariza karti yan yana KARSILASTIRILAMIYORDU.
  const kisa = buildStripGeometry({ poleSeqs: [1, 2, 3, 4, 5, 6], fromSeq: 2, toSeq: 3 });
  const uzun = buildStripGeometry({
    poleSeqs: Array.from({ length: 17 }, (_, i) => i + 1),
    fromSeq: 2,
    toSeq: 3
  });
  const aralik = (g: ReturnType<typeof buildStripGeometry>) => g.xOf(1) - g.xOf(0);
  assert.ok(
    Math.abs(aralik(kisa) - aralik(uzun)) < 1e-9,
    `direk araligi hatta gore degisiyor: ${aralik(kisa)} vs ${aralik(uzun)}`
  );
  // Uzun hat DAHA GENIS bir cizim uretmeli (kaydirilarak gosterilir).
  assert.ok(uzun.width > kisa.width);
});

test("direk ad ve rolu cizime tasinir", () => {
  const geo = buildStripGeometry({
    poleSeqs: [1, 2, 3],
    poles: [
      { seq: 1, name: "ANA-1", role: "line_start" },
      { seq: 2, name: "ANA-2", role: "branch" }
    ],
    fromSeq: 1,
    toSeq: 2
  });
  assert.equal(geo.poles.length, 3, "her direk icin bir kayit olmali");
  assert.equal(geo.poles[1].name, "ANA-2");
  assert.equal(geo.poles[1].role, "branch");
  // Kaydi olmayan direk yer tutucuyla gelir — cizim eksik kalmamali.
  assert.equal(geo.poles[2].seq, 3);
  assert.equal(geo.poles[2].name, undefined);
});

// ------------------------------------------------------- UC FAZ ILETKENI

test("arizali parca FAZ OFSETINE gore kayar", () => {
  // Uc iletken ayri izolator noktalarindan gecer. Arizali parca tek bir orta
  // cizgide gosterilseydi "hangi faz" bilgisi gorselden silinirdi.
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: segments(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  const orta = hotPathOf(geo);
  const sol = hotPathOf(geo, -22);
  const sag = hotPathOf(geo, 22);

  assert.ok(orta && sol && sag, "uc faz icin de path uretilmeli");
  assert.notEqual(sol, orta, "sol faz ofseti uygulanmamis");
  assert.notEqual(sag, orta, "sag faz ofseti uygulanmamis");

  // Ofset YALNIZCA x'i kaydirmali: tel yuksekligi (sarkma) korunur.
  const xler = (d: string) =>
    d.split(/[ML]/).filter(Boolean).map((par) => Number(par.trim().split(" ")[0]));
  const yler = (d: string) =>
    d.split(/[ML]/).filter(Boolean).map((par) => Number(par.trim().split(" ")[1]));

  const xOrta = xler(orta);
  const xSol = xler(sol);
  assert.equal(xOrta.length, xSol.length);
  for (let i = 0; i < xOrta.length; i += 1) {
    assert.ok(Math.abs(xSol[i] - (xOrta[i] - 22)) < 0.11, `nokta ${i} ofseti yanlis`);
  }
  assert.deepEqual(yler(sol), yler(orta), "faz ofseti tel yuksekligini degistirmemeli");
});

test("ofset verilmezse davranis DEGISMEZ (geriye uyum)", () => {
  const geo = buildStripGeometry({ poleSeqs: POLES, fromSeq: 3, toSeq: 4 });
  assert.equal(hotPathOf(geo), hotPathOf(geo, 0));
});
