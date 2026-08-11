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
  PHASE_LINES,
  PX_PER_UNIT,
  ROW_PITCH,
  SAG,
  WIRE_Y,
  buildFaultScene,
  buildStripGeometry,
  frameSceneToBox,
  hotPathOf,
  sagAt
} from "../src/features/faults/faultStripGeometry";

const POLES = [1, 2, 3, 4, 5];

/** Travers payi: iletken direk EKSENINDEN degil travers UCUNDAN gerilir.
 *  Geometriden turetilir ki sabit degisince test kendini duzeltsin. */
function attachOf(geo: ReturnType<typeof buildStripGeometry>): number {
  return geo.pointAt(0).x - geo.xOf(0);
}

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
  // Oran TEL uzerinde olculur: tel travers ucundan travers ucuna gerilir,
  // direk ekseninden degil. Cihaz "spanin dortte birinde" derken kastedilen
  // gerili telin dortte biridir.
  const pay = attachOf(geo);
  const x1 = geo.xOf(2) + pay;
  const x2 = geo.xOf(3) - pay;
  assert.ok(Math.abs(geo.pointAt(red.pos).x - (x1 + (x2 - x1) * 0.25)) < 1e-9);
});

test("iletken direk EKSENINDEN degil TRAVERS UCUNDAN gerilir", () => {
  // Onceki modelde tel direk ekseninden geciyor ama izolatorler traversin
  // uclarinda duruyordu: tel havada asili, izolator hicbir seyi tutmuyor
  // gibi gorunuyordu.
  const geo = buildStripGeometry({ poleSeqs: POLES, fromSeq: 1, toSeq: 2 });
  const pay = attachOf(geo);
  assert.ok(pay > 0, "travers payi uygulanmamis");
  // Span sonu bir SONRAKI direkten pay kadar ONCE biter.
  assert.ok(Math.abs(geo.pointAt(0.999999).x - (geo.xOf(1) - pay)) < 0.01);
  // Direk uzerinde atlama (jumper): bir sonraki span pay kadar SONRA baslar.
  assert.ok(Math.abs(geo.pointAt(1).x - (geo.xOf(1) + pay)) < 1e-9);
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
    // Direk uzerindeki atlama noktalari AYNI pos'u paylasir (span sonu ile
    // sonraki span basi); geri gitmemesi yeterli.
    assert.ok(geo.wire[i].pos >= geo.wire[i - 1].pos, `pos ${i}. noktada geriliyor`);
  }
  // Tel iki uctan da gerdirilir: son nokta son traversin SAG ucudur.
  const pay = attachOf(geo);
  const ilkX = geo.wire[0].x;
  const sonX = geo.wire[geo.wire.length - 1].x;
  assert.ok(Math.abs(ilkX - (geo.xOf(0) - pay)) < 1e-9, "tel ilk direkte gerdirilmemis");
  assert.ok(
    Math.abs(sonX - (geo.xOf(POLES.length - 1) + pay)) < 1e-9,
    "tel son direkte gerdirilmemis"
  );
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

test("uc faz UC AYRI YUKSEKLIKTE — ayni seviyede iki tel yok", () => {
  // Ayni yukseklikte yan yana dizilen teller sarkma egrileriyle ust uste
  // biniyor ve hat tek kalin bir bant gibi okunuyordu. Ayrica ariza kendi
  // telinde gosterilemiyordu.
  const dyler = PHASE_LINES.map((f) => f.dy);
  assert.equal(new Set(dyler).size, PHASE_LINES.length, "iki faz ayni yukseklikte");
  // Faz araligi sarkmadan buyuk olmali; yoksa ustteki telin sarkmasi alttaki
  // telin uzerine oturur.
  const sirali = [...dyler].sort((a, b) => a - b);
  for (let i = 1; i < sirali.length; i += 1) {
    assert.ok(sirali[i] - sirali[i - 1] > SAG, "faz araligi sarkmadan dar");
  }
});

test("arizali parca FAZ OFSETINE gore kayar", () => {
  // Uc iletken uc AYRI traverse asilir. Arizali parca tek bir cizgide
  // gosterilseydi "hangi faz" bilgisi gorselden silinirdi.
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: segments(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  const orta = hotPathOf(geo);
  const ust = hotPathOf(geo, PHASE_LINES[0].dy);
  const alt = hotPathOf(geo, PHASE_LINES[2].dy);

  assert.ok(orta && ust && alt, "uc faz icin de path uretilmeli");
  assert.notEqual(ust, orta, "ust faz ofseti uygulanmamis");
  assert.notEqual(alt, orta, "alt faz ofseti uygulanmamis");

  // Ofset YALNIZCA y'yi kaydirmali: telin yatay yuruyusu korunur.
  const xler = (d: string) =>
    d.split(/[ML]/).filter(Boolean).map((par) => Number(par.trim().split(" ")[0]));
  const yler = (d: string) =>
    d.split(/[ML]/).filter(Boolean).map((par) => Number(par.trim().split(" ")[1]));

  assert.deepEqual(xler(ust), xler(orta), "faz ofseti telin x'ini degistirmemeli");
  const yOrta = yler(orta);
  const yUst = yler(ust);
  assert.equal(yOrta.length, yUst.length);
  for (let i = 0; i < yOrta.length; i += 1) {
    assert.ok(
      Math.abs(yUst[i] - (yOrta[i] + PHASE_LINES[0].dy)) < 0.11,
      `nokta ${i} ofseti yanlis`
    );
  }
});

test("ofset verilmezse davranis DEGISMEZ (geriye uyum)", () => {
  const geo = buildStripGeometry({ poleSeqs: POLES, fromSeq: 3, toSeq: 4 });
  assert.equal(hotPathOf(geo), hotPathOf(geo, 0));
});

/* ---------------------------------------------------------------------------
 * BRANSMAN GIRISI — cihaz cizimden DUSMEMELI
 *
 * Kolun giris segmentinde bir ucu ana hattin diregi, digeri kolun ilk
 * diregidir. Kolun diregi ana hattin direk listesinde OLMADIGI icin segment
 * "komsu degil" sayilip tamamen eleniyordu.
 *
 * Sonuc sessizdi ve agirdi: bransman girisini izleyen cihaz cizimden dusuyor,
 * "gordum" diyen cihaz bulunamiyor ve `span` null kaliyordu — AKTIF bir ariza
 * karti tertemiz, arizasiz bir hat gosteriyordu.
 * ------------------------------------------------------------------------- */

/** Ana hattin 3 nolu diregine takili bir kolun giris segmenti. */
function bransmanGirisi() {
  return [
    { from_pole_seq: 3, to_pole_seq: 41, device_code: "SN2-KOL", device_position_t: 0.5 }
  ];
}

test("bransman girisindeki cihaz cizimde GORUNUR", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: bransmanGirisi(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-KOL"
  });
  const kol = geo.devices.find((d) => d.code === "SN2-KOL");
  assert.ok(kol, "bransman girisindeki cihaz elenmis");
  assert.equal(kol.onBranch, true, "cihaz bransman girisi olarak isaretlenmeli");
  // Dallanma diregine cizilir: seq 3 -> indeks 2.
  assert.equal(kol.pos, 2, "cihaz dallanma diregine oturmali");
});

test("bransman cihazi ANA HAT uzerinde arizali parca TANIMLAMAZ", () => {
  // Gordugu ariza kolun asagisindadir; ana telde kirmizi bir parca cizmek
  // ekibi yanlis acikliga gonderirdi.
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: bransmanGirisi(),
    fromSeq: null,
    toSeq: null,
    lastRedDeviceCode: "SN2-KOL"
  });
  assert.equal(geo.span, null, "kol cihazi ana hatta parca uretmemeli");
  assert.equal(hotPathOf(geo), "", "ana hatta kirmizi tel cizilmemeli");
});

test("ana hattaki cihazlar bransmandan ETKILENMEZ", () => {
  // Geriye uyum: kol segmenti listeye eklense de normal cihazlarin konumu ve
  // ariza araligi aynen kalmali.
  const yalniz = buildStripGeometry({
    poleSeqs: POLES,
    segments: segments(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  const kolIle = buildStripGeometry({
    poleSeqs: POLES,
    segments: [...segments(), ...bransmanGirisi()],
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  assert.deepEqual(kolIle.span, yalniz.span, "ariza araligi degismemeli");
  assert.equal(hotPathOf(kolIle), hotPathOf(yalniz), "arizali parca degismemeli");
  assert.equal(kolIle.devices.length, yalniz.devices.length + 1, "kol cihazi eklenmeli");
});

test("iki ucu da bu hatta olmayan segment CIZILMEZ", () => {
  // Baska bir hattin kaydi yanlislikla gelirse cihaz uydurulmamali.
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: [{ from_pole_seq: 90, to_pole_seq: 91, device_code: "YABANCI" }],
    fromSeq: 3,
    toSeq: 4
  });
  assert.equal(geo.devices.length, 0, "baska hattin cihazi cizime girmis");
});

/* ---------------------------------------------------------------------------
 * COK SATIRLI SAHNE — ariza tek bir hat kesiminde olmayabilir
 *
 * Ariza bolgesi bir dallanma diregini kapsiyorsa ariza ana hatta da olabilir
 * o kolda da. Harita bunu zaten iki ayri kirmizi kesik olarak ciziyordu; sema
 * yalnizca ana hatti gosterince ekip hangi kolu gezecegini bilemiyordu.
 * ------------------------------------------------------------------------- */

test("kol AYRI BIR SATIR olarak, dallanma direginin ALTINA yerlesir", () => {
  const scene = buildFaultScene([
    { key: "main", kind: "main", title: "ANA", poleSeqs: POLES, fromSeq: 3, toSeq: 4 },
    {
      key: "b7",
      kind: "branch",
      title: "BR-7",
      poleSeqs: [1, 2, 3],
      parentKey: "main",
      parentSeq: 4,
      confirmed: false
    }
  ]);

  assert.equal(scene.rows.length, 2);
  assert.equal(scene.rows[1].y0, ROW_PITCH, "kol satiri bir adim asagida olmali");

  // seq 4 = ana hattin 4. indeksi degil 3. indeksi (POLES 1..5).
  const anchorX = scene.rows[0].geo.xOf(3);
  assert.equal(scene.rows[1].link?.fromX, anchorX, "bag dallanma direginden cikmali");
  assert.ok(
    Math.abs(scene.rows[1].x0 + scene.rows[1].geo.xOf(0) - anchorX) < 1e-9,
    "kolun ilk diregi dallanma direginin altinda olmali"
  );
  // Sahne kollari da kapsayacak kadar buyumeli; yoksa kol kirpilir ve
  // kaydirarak dahi gorulemez.
  assert.ok(scene.width >= scene.rows[1].x0 + scene.rows[1].geo.width);
  assert.ok(scene.height > scene.rows[1].y0);
});

test("kendi kaydi OLMAYAN kol BASTAN SONA aday cizilir", () => {
  // Kolun neresinde oldugunu soyleyecek cihaz yok: bir parcasini secip
  // kirmiziya boyamak uydurma olurdu, ekibi yanlis direge gonderir.
  const scene = buildFaultScene([
    { key: "main", kind: "main", title: "ANA", poleSeqs: POLES, fromSeq: 3, toSeq: 4 },
    {
      key: "b7",
      kind: "branch",
      title: "BR-7",
      poleSeqs: [1, 2, 3],
      parentKey: "main",
      parentSeq: 3,
      confirmed: false
    }
  ]);
  assert.deepEqual(scene.rows[1].geo.span, { a: 0, b: 2, byDevice: false });
});

test("kendi kaydi OLAN kol yalnizca o araligi kirmizi cizer", () => {
  const scene = buildFaultScene([
    { key: "main", kind: "main", title: "ANA", poleSeqs: POLES, fromSeq: 3, toSeq: 4 },
    {
      key: "b7",
      kind: "branch",
      title: "BR-7",
      poleSeqs: [1, 2, 3, 4],
      parentKey: "main",
      parentSeq: 3,
      fromSeq: 2,
      toSeq: 3,
      confirmed: true
    }
  ]);
  assert.deepEqual(scene.rows[1].geo.span, { a: 1, b: 2, byDevice: false });
});

test("bagi cozulemeyen kol yine de cizilir (sessizce dusmez)", () => {
  // Dallanma diregi ana hattin direk listesinde yoksa (eksik snapshot) kol
  // baglantisiz kalir ama CIZILIR: gezilecek bir kolun ekrandan kaybolmasi
  // yanlis hizalanmis bir bagdan cok daha agir bir hata.
  const scene = buildFaultScene([
    { key: "main", kind: "main", title: "ANA", poleSeqs: POLES, fromSeq: 3, toSeq: 4 },
    {
      key: "b7",
      kind: "branch",
      title: "BR-7",
      poleSeqs: [1, 2],
      parentKey: "main",
      parentSeq: 99,
      confirmed: false
    }
  ]);
  assert.equal(scene.rows.length, 2);
  assert.equal(scene.rows[1].link, null);
});

test("AYNI DIREKTEN iki kol: biri asagi, digeri YUKARI cizilir", () => {
  // Ikisi de asagi cizildiginde ayni x'te ust uste iki satirda kaliyor ve
  // ikinci kolun bagi birincinin cizimini bastan asagi kesip geciyordu.
  const scene = buildFaultScene([
    { key: "main", kind: "main", title: "ANA", poleSeqs: POLES, fromSeq: 2, toSeq: 5 },
    {
      key: "b1",
      kind: "branch",
      title: "BR-1",
      poleSeqs: [1, 2],
      parentKey: "main",
      parentSeq: 3,
      confirmed: false
    },
    {
      key: "b2",
      kind: "branch",
      title: "BR-2",
      poleSeqs: [1, 2],
      parentKey: "main",
      parentSeq: 3,
      confirmed: false
    }
  ]);

  const [ana, k1, k2] = scene.rows;
  assert.equal(ana.side, 0);
  assert.equal(k1.side, 1, "ilk kardes asagi");
  assert.equal(k2.side, -1, "ikinci kardes yukari");
  assert.ok(k1.y0 > ana.y0, "asagi kol ana hattin altinda olmali");
  assert.ok(k2.y0 < ana.y0, "yukari kol ana hattin ustunde olmali");
  // Hicbir satir negatif y'de kalmamali (viewBox 0'dan basliyor).
  for (const r of scene.rows) assert.ok(r.y0 >= 0, `${r.key} sahne disinda`);
  assert.ok(scene.height >= ana.y0 + ROW_PITCH, "sahne yuksekligi iki tarafi da kapsamali");
});

test("yukari cikan kolun bagi direk TEPESINDEN cikar", () => {
  const scene = buildFaultScene([
    { key: "main", kind: "main", title: "ANA", poleSeqs: POLES, fromSeq: 2, toSeq: 5 },
    { key: "b1", kind: "branch", title: "BR-1", poleSeqs: [1, 2], parentKey: "main", parentSeq: 3 },
    { key: "b2", kind: "branch", title: "BR-2", poleSeqs: [1, 2], parentKey: "main", parentSeq: 4 }
  ]);
  const ana = scene.rows[0];
  const asagi = scene.rows[1];
  const yukari = scene.rows[2];
  // Asagi inen kol direk DIBINDEN cikar, kolun TEPESINE girer.
  assert.ok(asagi.link!.fromY > ana.y0, "asagi bag direk dibinden cikmali");
  assert.ok(asagi.link!.toY < asagi.y0 + ROW_PITCH);
  // Yukari cikan kol direk TEPESINDEN cikar, kolun DIBINE girer.
  assert.ok(yukari.link!.fromY < asagi.link!.fromY, "yukari bag direk tepesinden cikmali");
  assert.ok(yukari.link!.toY > yukari.y0, "yukari bag kolun dibine girmeli");
});

test("ic ice kol, bagli oldugu kolun YONUNU surdurur", () => {
  // Yukari cizilmis bir kolun alt kolu asagi cizilseydi bag ana hattin
  // uzerinden atlamak zorunda kalirdi.
  const scene = buildFaultScene([
    { key: "main", kind: "main", title: "ANA", poleSeqs: POLES, fromSeq: 2, toSeq: 5 },
    { key: "b1", kind: "branch", title: "BR-1", poleSeqs: [1, 2], parentKey: "main", parentSeq: 3 },
    { key: "b2", kind: "branch", title: "BR-2", poleSeqs: [1, 2], parentKey: "main", parentSeq: 4 },
    { key: "b3", kind: "branch", title: "BR-3", poleSeqs: [1, 2], parentKey: "b2", parentSeq: 1 }
  ]);
  const yukari = scene.rows[2];
  const icIce = scene.rows[3];
  assert.equal(yukari.side, -1);
  assert.equal(icIce.side, -1, "alt kol ust kolun yonunu surdurmeli");
  assert.ok(icIce.y0 < yukari.y0, "alt kol daha yukarida olmali");
});

/* ---------------------------------------------------------------------------
 * CIZIM KUTUSU — sabit alan, degismeyen olcek
 * ------------------------------------------------------------------------- */

test("kucuk sahne kutuyu doldurmak icin BUYUTULMEZ", () => {
  // viewBox tam sahne olsaydi `meet` cizimi kutuya kadar buyuturdu: uc
  // direkli kisa bir hat ekrani kaplayan devasa direkler olarak cizilir,
  // iki ariza karti yan yana karsilastirilamazdi.
  const scene = { width: 400, height: 222 };
  const box = { w: 1400, h: 800 };
  const v = frameSceneToBox(scene, box);
  const olcek = box.w / v.w;
  assert.ok(
    Math.abs(olcek - PX_PER_UNIT) < 1e-9,
    `olcek dogal olcegi asmis: ${olcek} > ${PX_PER_UNIT}`
  );
  // Kutunun TAMAMI kaplanir (viewBox orani kutu orani ile ayni).
  assert.ok(Math.abs(v.w / v.h - box.w / box.h) < 1e-9, "viewBox orani kutuya uymuyor");
  // Sahne cercevenin ORTASINDA.
  assert.ok(Math.abs(v.x + v.w / 2 - scene.width / 2) < 1e-9);
  assert.ok(Math.abs(v.y + v.h / 2 - scene.height / 2) < 1e-9);
});

test("kutuya sigmayan sahne KUCULTULUR, kirpilmaz", () => {
  const scene = { width: 4000, height: 900 };
  const box = { w: 1000, h: 500 };
  const v = frameSceneToBox(scene, box);
  assert.ok(v.w >= scene.width - 1e-9, "sahnenin tamami pencereye girmeli");
  assert.ok(v.h >= scene.height - 1e-9, "sahnenin tamami pencereye girmeli");
  assert.ok(v.x <= 0 + 1e-9 && v.y <= 0 + 1e-9, "sahne cercevenin icinde kalmali");
});

test("kutu olculmeden once sahnenin tamami gosterilir", () => {
  // Ilk render'da ResizeObserver henuz olcmedi; cizim kaybolmamali.
  const v = frameSceneToBox({ width: 800, height: 300 }, null);
  assert.deepEqual(v, { x: 0, y: 0, w: 800, h: 300 });
  assert.deepEqual(frameSceneToBox({ width: 800, height: 300 }, { w: 0, h: 0 }), {
    x: 0,
    y: 0,
    w: 800,
    h: 300
  });
});

/* ---------------------------------------------------------------------------
 * AYNI ARALIKTA IKI CIHAZ
 *
 * Iki cihaz ayni direk araligindaysa ve konumlari verilmemisse ikisi de
 * varsayilan 0.5'e dusuyordu. Sonuc yalnizca gorsel degildi: "gordum" ile
 * "gormedim" ayni noktada oldugu icin ariza bolgesi hesaplanamiyor, kod kaba
 * direk araligina duşup ARALIGIN TAMAMINI kirmiziya boyuyordu. Sahada 34
 * metrelik bir kesim, iki direk arasinin tamami olarak gosteriliyordu.
 * ------------------------------------------------------------------------- */

/** 3-4 arasinda IKI cihaz: biri gordu, biri gormedi. Konum verilmemis. */
function ayniAralikta() {
  return [
    { from_pole_seq: 3, to_pole_seq: 4, device_code: "SN2-RED" },
    { from_pole_seq: 3, to_pole_seq: 4, device_code: "SN2-GREEN" }
  ];
}

test("ayni araliktaki iki cihaz UST USTE binmez", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: ayniAralikta(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  assert.equal(geo.devices.length, 2, "iki cihaz da cizime girmeli");
  const [a, b] = geo.devices;
  assert.ok(b.pos - a.pos > 0.15, `cihazlar cakisiyor: ${a.pos} / ${b.pos}`);
  // Ikisi de KENDI araliginin icinde kalmali (komsu aralia tasmasin).
  for (const d of geo.devices) {
    assert.ok(Math.floor(d.pos) === 2, `${d.code} kendi araliginin disina cikmis`);
  }
});

test("ayni araliktaki iki cihaz arasinda ariza bolgesi DOGRU cikar", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: ayniAralikta(),
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  assert.ok(geo.span, "bolge hesaplanamadi");
  assert.equal(geo.span.byDevice, true, "sinirlar cihazlardan gelmeliydi");
  const red = geo.devices.find((d) => d.code === "SN2-RED")!;
  const green = geo.devices.find((d) => d.code === "SN2-GREEN")!;
  assert.equal(geo.span.a, red.pos);
  assert.equal(geo.span.b, green.pos);
  // Bolge ARALIGIN TAMAMI degil, iki cihazin arasi.
  assert.ok(geo.span.a > 2, "bolge aralik basindan basliyor (eski hata)");
  assert.ok(geo.span.b < 3, "bolge aralik sonunda bitiyor (eski hata)");
});

test("GORDUM cihazi GORMEDIM'den once gelir", () => {
  // Veride hangi segmentin once geldigini soyleyen bir bilgi yok; sira
  // arizadan okunur. Ters dizmek bolgeyi ters cevirirdi.
  const tersSira = buildStripGeometry({
    poleSeqs: POLES,
    // Yesil cihaz listede ONCE geliyor.
    segments: [
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "SN2-GREEN" },
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "SN2-RED" }
    ],
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "SN2-RED",
    firstGreenDeviceCode: "SN2-GREEN"
  });
  const red = tersSira.devices.find((d) => d.code === "SN2-RED")!;
  const green = tersSira.devices.find((d) => d.code === "SN2-GREEN")!;
  assert.ok(red.pos < green.pos, "gordum cihazi gormedimden sonra dizilmis");
  assert.ok(tersSira.span && tersSira.span.b > tersSira.span.a);
});

test("konumu VERILMIS cihazlara dokunulmaz", () => {
  // `device_position_t` sahada olculmus gercek bir konum olabilir.
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: [
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "A", device_position_t: 0.2 },
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "B", device_position_t: 0.8 }
    ],
    fromSeq: 3,
    toSeq: 4
  });
  assert.equal(geo.devices.find((d) => d.code === "A")!.pos, 2.2);
  assert.equal(geo.devices.find((d) => d.code === "B")!.pos, 2.8);
});

test("kalabalik aralik icin direk arasi ACILIR", () => {
  const tek = buildStripGeometry({
    poleSeqs: POLES,
    segments: [{ from_pole_seq: 3, to_pole_seq: 4, device_code: "A" }],
    fromSeq: 3,
    toSeq: 4
  });
  const cift = buildStripGeometry({
    poleSeqs: POLES,
    segments: ayniAralikta(),
    fromSeq: 3,
    toSeq: 4
  });
  const aralik = (g: ReturnType<typeof buildStripGeometry>) => g.xOf(1) - g.xOf(0);
  assert.ok(
    aralik(cift) > aralik(tek) * 1.2,
    `iki cihazli hatta aralik acilmamis: ${aralik(tek)} -> ${aralik(cift)}`
  );
  // Olcek TUM aralikilarda ayni kalmali — hat duzenli bir tarak gibi okunsun.
  for (let i = 1; i < POLES.length - 1; i += 1) {
    assert.ok(
      Math.abs((cift.xOf(i + 1) - cift.xOf(i)) - aralik(cift)) < 1e-9,
      "aralikilar esit degil"
    );
  }
});

test("BES cihaz ayni aralikta — hepsi ayri ayri gorunur", () => {
  // "Iki cihaz" bir varsayim degil: aralikta kac segment varsa o kadar cihaz
  // olabilir. Dagitim eleman sayisina gore yapilir.
  const kodlar = ["A", "B", "C", "D", "E"];
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: kodlar.map((c) => ({ from_pole_seq: 3, to_pole_seq: 4, device_code: c })),
    fromSeq: 3,
    toSeq: 4
  });
  assert.equal(geo.devices.length, 5, "cihazlarin bir kismi cizimden dusmus");
  for (let i = 1; i < geo.devices.length; i += 1) {
    assert.ok(
      geo.devices[i].pos > geo.devices[i - 1].pos,
      `${geo.devices[i].code} onceki cihazla ayni noktada`
    );
  }
  // Hepsi KENDI araliginin icinde kalir.
  for (const d of geo.devices) {
    assert.ok(d.pos > 2 && d.pos < 3, `${d.code} aralik disina tasmis`);
  }
  // Aralik bes cihaza gore acilir.
  const tek = buildStripGeometry({ poleSeqs: POLES, fromSeq: 3, toSeq: 4 });
  assert.ok(geo.xOf(1) - geo.xOf(0) > (tek.xOf(1) - tek.xOf(0)) * 2);
});

test("cok cihazli aralikta SAGLAM cihaz bolgenin icine tasinmaz", () => {
  // Uc cihaz: A durumu bilinmiyor, B gordu, C gormedi. Once tonlara gore tam
  // siralama yapiliyordu ve A (kirmizinin yukarisinda duran saglam cihaz)
  // ariza bolgesinin ICINE dusuyordu — bilmedigimiz bir seyi iddia etmek.
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: [
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "A" },
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "B-RED" },
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "C-GREEN" }
    ],
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "B-RED",
    firstGreenDeviceCode: "C-GREEN"
  });
  const konum = (c: string) => geo.devices.find((d) => d.code === c)!.pos;
  assert.ok(konum("A") < konum("B-RED"), "durumu bilinmeyen cihaz kendi sirasindan cikmis");
  assert.ok(konum("B-RED") < konum("C-GREEN"), "gordum/gormedim sirasi bozuk");
  assert.ok(geo.span && geo.span.a === konum("B-RED") && geo.span.b === konum("C-GREEN"));
  assert.ok(konum("A") < geo.span!.a, "saglam cihaz ariza bolgesinin icinde cizilmis");
});

test("ters gelmis GORDUM/GORMEDIM ciftinde yalnizca O IKISI yer degistirir", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: [
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "C-GREEN" },
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "X" },
      { from_pole_seq: 3, to_pole_seq: 4, device_code: "B-RED" }
    ],
    fromSeq: 3,
    toSeq: 4,
    lastRedDeviceCode: "B-RED",
    firstGreenDeviceCode: "C-GREEN"
  });
  const konum = (c: string) => geo.devices.find((d) => d.code === c)!.pos;
  assert.ok(konum("B-RED") < konum("C-GREEN"), "cift duzeltilmemis");
  // Ortadaki cihaz kendi yerinde kaldi.
  assert.ok(konum("X") > konum("B-RED") && konum("X") < konum("C-GREEN"));
});

/* ---------------------------------------------------------------------------
 * TEK DIREKLI KOL — uydurma direk yok
 *
 * Kosul `uniq.length >= 2` idi: tek direkli bir bransman kolunda ikinci bir
 * direk UYDURULUYORDU ("#2"). Sahada olmayan bir direk cizmek, ekibe var
 * olmayan bir hat parcasi gostermek demek.
 * ------------------------------------------------------------------------- */

test("tek direkli kolda IKINCI direk uydurulmaz", () => {
  const geo = buildStripGeometry({
    poleSeqs: [1],
    poles: [{ seq: 1, name: "TR21_D-12" }],
    fromSeq: null,
    toSeq: null
  });
  assert.deepEqual(geo.seqs, [1], "olmayan bir direk eklenmis");
  assert.equal(geo.poles.length, 1);
  assert.equal(geo.poles[0].name, "TR21_D-12");
});

test("tek direkli kolda tel ve konum hesabi COKMEZ", () => {
  const geo = buildStripGeometry({ poleSeqs: [1], fromSeq: null, toSeq: null });
  assert.ok(geo.wire.length >= 2, "tel cizilemiyor");
  for (let i = 1; i < geo.wire.length; i += 1) {
    assert.ok(geo.wire[i].x > geo.wire[i - 1].x, "tel geri donuyor");
  }
  // Tek direkte aranacak bir hat kesimi YOKTUR: supheli olan bag telidir.
  assert.equal(geo.span, null);
  assert.ok(Number.isFinite(geo.pointAt(0).x));
  assert.ok(Number.isFinite(geo.xOf(0)));
});

test("tek direkli kol BASTAN SONA aday olarak isaretlenmez", () => {
  // `wholeLineHot` iki direk ister: tek direkte kirmiziya boyanacak bir
  // hat parcasi yok.
  const geo = buildStripGeometry({ poleSeqs: [1], wholeLineHot: true });
  assert.equal(geo.span, null);
});

test("hic direk yoksa yer tutucu URETILIR (snapshot eksik)", () => {
  const geo = buildStripGeometry({ poleSeqs: [], fromSeq: 3, toSeq: 4 });
  assert.deepEqual(geo.seqs, [3, 4]);
});

/* ---------------------------------------------------------------------------
 * ARIZAYI GOREN CIHAZ BRANSMAN GIRISINDE
 *
 * Boyle bir cihaz ana hattin uzerinde degil, dallanma diregi ile kolun ilk
 * diregi ARASINDAKI telin uzerindedir. "Gordum" demesi arizanin O KOLDA
 * oldugu anlamina gelir. Kod cihazi bolge hesabindan disliyor ama hemen
 * ardindan kaba direk araligina dusup ANA HATTI bastan sona kirmiziya
 * boyuyordu: harita kolun uzerinde tek bir kirmizi kesik gosterirken sema
 * koca bir ana hat parcasini isaretliyor, ekip yanlis yere gidiyordu.
 * ------------------------------------------------------------------------- */

test("giris cihazi gorduyse ANA HAT boyanmaz", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    // 3 nolu direkten cikan kolun girisindeki cihaz.
    segments: [{ from_pole_seq: 3, to_pole_seq: 41, device_code: "SN2-KOL" }],
    fromSeq: 3,
    toSeq: 5,
    lastRedDeviceCode: "SN2-KOL"
  });
  assert.equal(geo.span, null, "ana hatta kirmizi parca cizilmis");
  assert.equal(hotPathOf(geo), "", "ana hat teli kirmizi");
});

test("ariza kolun hangi DIREKTEN asagida oldugu bildirilir", () => {
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: [{ from_pole_seq: 3, to_pole_seq: 41, device_code: "SN2-KOL" }],
    fromSeq: 3,
    toSeq: 5,
    lastRedDeviceCode: "SN2-KOL"
  });
  assert.equal(geo.branchTapSeq, 3, "aday kol bulunamaz — ariza kaybolurdu");
});

test("giris cihazi GORMEDIYSE ana hat eskisi gibi boyanir", () => {
  // Geriye uyum: kol girisindeki cihaz bu arizayi gormediyse ana hattaki
  // kaba aralik yine gecerlidir.
  const geo = buildStripGeometry({
    poleSeqs: POLES,
    segments: [{ from_pole_seq: 3, to_pole_seq: 41, device_code: "SN2-KOL" }],
    fromSeq: 3,
    toSeq: 5,
    lastRedDeviceCode: "SN2-BASKA"
  });
  assert.ok(geo.span, "ana hat bolgesi kaybolmus");
  assert.equal(geo.branchTapSeq, null);
});
