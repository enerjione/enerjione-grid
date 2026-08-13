/**
 * Ariza detay haritasinin geometrisi.
 *
 * Bu hesap 260 satirdi ve bir React `useMemo`'sunun icinde yasiyordu; yani
 * CALISTIRILARAK dogrulanamiyordu. Tasidigi karar ise agir: KIRMIZI PARCA
 * sahada hangi iki direk arasina gidilecegini soyler. Bir direk kaymasi
 * ekibi yanlis acikliga gonderir ve bu hata sessizdir — harita her zaman
 * "bir sey" cizer.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  bolPolyline,
  buildFaultMapView,
  lerp,
  zoomFor
} from "../src/features/faults/faultMapView";

const D = (id: number, seq: number, lat: number, lon: number) => ({
  id,
  line_id: 1,
  sequence_no: seq,
  latitude: lat,
  longitude: lon,
  name: null
});

/** Duz dogu-bati hat: 5 direk, 39. paralelde. */
const DIREKLER = [D(1, 1, 39, 35.0), D(2, 2, 39, 35.1), D(3, 3, 39, 35.2), D(4, 4, 39, 35.3), D(5, 5, 39, 35.4)];

const SEG = (from: number, to: number, deviceId?: number, t?: number) => ({
  line_id: 1,
  from_pole_id: from,
  to_pole_id: to,
  device_id: deviceId ?? null,
  device_position_t: t ?? null
});

const TEMEL = {
  lines: [{ id: 1, name: "ANA HAT" }],
  devices: [
    { id: 101, name: "DEMO-1", code: "D1" },
    { id: 102, name: "DEMO-2", code: "D2" },
    { id: 103, name: "DEMO-3", code: "D3" }
  ],
  poleFallback: "Direk",
  deviceFallback: "Cihaz"
};

// ---------------------------------------------------------------------------
// bolPolyline — kirmizi parcanin sinirlarini bu belirler
// ---------------------------------------------------------------------------

test("bolme noktasi cizginin USTUNDE degilse dik izdusum alinir", () => {
  // Cihaz iki direk arasinda bir oranda durur ve genelde tam cizgi uzerinde
  // degildir. Izdusum alinmasaydi kirmizi parca cihazin yaninda degil bir
  // sonraki DIREKTEN baslardi.
  const hat: [number, number][] = [
    [0, 0],
    [0, 10]
  ];
  const { pre, post } = bolPolyline(hat, [1, 4]); // cizginin 1 birim yaninda
  assert.deepEqual(pre[pre.length - 1], [0, 4], "izdusum noktasi yanlis");
  assert.deepEqual(post[0], [0, 4]);
});

test("bolunen iki parca AYNI noktada bulusur — hat kopmaz", () => {
  const hat: [number, number][] = [
    [0, 0],
    [0, 5],
    [3, 9]
  ];
  const { pre, post } = bolPolyline(hat, [0, 3]);
  assert.deepEqual(pre[pre.length - 1], post[0], "parcalar arasinda bosluk var");
});

test("bolme hattin GERCEK geometrisini korur", () => {
  // Kivrimli bir hatta duz "from -> to" cizgisi arizayi tarlanin ortasinda
  // gosteriyordu. Ara direkler parcalarda kalmali.
  const hat: [number, number][] = [
    [0, 0],
    [5, 0],
    [5, 5],
    [10, 5]
  ];
  const { pre, post } = bolPolyline(hat, [5, 3]);
  assert.ok(pre.length >= 3, `ara direkler dusmus: ${JSON.stringify(pre)}`);
  assert.ok(post.length >= 2);
});

test("iki noktadan kisa polyline bolunmez — patlamaz", () => {
  assert.deepEqual(bolPolyline([[1, 1]], [0, 0]), { pre: [[1, 1]], post: [] });
  assert.deepEqual(bolPolyline([], [0, 0]), { pre: [], post: [] });
});

// ---------------------------------------------------------------------------
// buildFaultMapView — uc parca
// ---------------------------------------------------------------------------

test("hicbir cihaz ariza algilamadiysa hat TAMAMEN saglikli", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101), SEG(3, 4, 102)],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set()
  })!;
  assert.equal(v.faultRed.length, 0, "ariza yokken kirmizi parca cizilmis");
  assert.equal(v.preGreen.length, DIREKLER.length);
});

test("ariza SON algilayan ile ILK algilamayan cihazin ARASINDA", () => {
  // Arizanin yerini daraltmanin tek dogru yolu bu ikili.
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101), SEG(2, 3, 102), SEG(3, 4, 103)],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set([101, 102]) // 102 son kirmizi, 103 ilk yesil
  })!;
  assert.ok(v.faultRed.length >= 2, "kirmizi parca cizilmemis");

  const bas = v.faultRed[0];
  const son = v.faultRed[v.faultRed.length - 1];
  // 102: direk 2-3 arasinin ortasi (lon 35.15). 103: direk 3-4 ortasi (35.25).
  assert.ok(Math.abs(bas[1] - 35.15) < 1e-6, `kirmizi parca yanlis basliyor: ${bas}`);
  assert.ok(Math.abs(son[1] - 35.25) < 1e-6, `kirmizi parca yanlis bitiyor: ${son}`);
});

test("son kirmizidan SONRA yesil cihaz yoksa ariza hat UCUNA kadar", () => {
  // Daraltacak bir cihaz yok; "burada bitti" demek uydurma olurdu.
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101), SEG(2, 3, 102)],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set([101, 102])
  })!;
  const son = v.faultRed[v.faultRed.length - 1];
  assert.ok(Math.abs(son[1] - 35.4) < 1e-6, `hat ucuna uzanmiyor: ${son}`);
  assert.equal(v.postGreen.length, 0);
});

test("uc parca hattin TAMAMINI kaplar — arada bosluk kalmaz", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101), SEG(2, 3, 102), SEG(3, 4, 103)],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set([101, 102])
  })!;
  assert.deepEqual(v.preGreen[v.preGreen.length - 1], v.faultRed[0], "pre ile kirmizi arasi kopuk");
  assert.deepEqual(v.faultRed[v.faultRed.length - 1], v.postGreen[0], "kirmizi ile post arasi kopuk");
  assert.deepEqual(v.preGreen[0], [39, 35.0], "hat basindan baslamiyor");
  assert.deepEqual(v.postGreen[v.postGreen.length - 1], [39, 35.4], "hat ucunda bitmiyor");
});

// ---------------------------------------------------------------------------
// GECMIS ARIZA — bolge KAYITTAN gelir
// ---------------------------------------------------------------------------

test("alarm normale donmus KAPALI arizada bolge KAYITTAN cizilir", () => {
  // Onceki davranis: bolge yalnizca canli alarmlardan cikariliyordu. Ariza
  // kapaninca alarm resetleniyor, kirmizi parca kayboluyor ve gecmis bir
  // kaydin haritasi hattin TAMAMINI saglam gosteriyordu — sahada nereye
  // gidildigi bilgisi kaydin icinde durdugu halde ekranda yoktu.
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101), SEG(2, 3, 102), SEG(3, 4, 103)],
    fault: { line_id: 1, last_red_device_id: 102, first_green_device_id: 103 },
    alarmActiveDeviceIds: new Set() // hicbir alarm acik degil (ariza kapandi)
  })!;
  assert.ok(v.faultRed.length >= 2, "kapali arizada kirmizi parca cizilmemis");
  assert.ok(Math.abs(v.faultRed[0][1] - 35.15) < 1e-6, `parca yanlis basliyor: ${v.faultRed[0]}`);
  assert.ok(
    Math.abs(v.faultRed[v.faultRed.length - 1][1] - 35.25) < 1e-6,
    "parca yanlis bitiyor"
  );
  // Bolge odagi tum hatta degil BOLGEYE zoom yapmali.
  assert.ok(v.zoneBounds.length < v.lineBounds.length, "bolge kutusu tum hatta esit");
  // Kayitli cihaz haritada da kirmizi: kartta kirmizi yazanin haritada yesil
  // gorunmesi ayni ekranda iki farkli cevap demekti.
  assert.equal(v.deviceMarkers.find((d) => d.deviceId === 102)?.isRed, true);
  assert.equal(v.deviceMarkers.find((d) => d.deviceId === 103)?.isRed, false);
});

test("kayitta yesil cihaz yoksa bolge hat UCUNA kadar surer", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101), SEG(2, 3, 102)],
    fault: { line_id: 1, last_red_device_id: 102 },
    alarmActiveDeviceIds: new Set()
  })!;
  const son = v.faultRed[v.faultRed.length - 1];
  assert.ok(Math.abs(son[1] - 35.4) < 1e-6, `hat ucuna uzanmiyor: ${son}`);
});

test("kayittaki cihaz artik bu hatta degilse CANLI alarma dusulur", () => {
  // Topoloji duzenlenip cihaz baska hatta tasinmis olabilir. Kayda korukorune
  // guvenmek bolgeyi hic cizmemek olurdu.
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101), SEG(2, 3, 102)],
    fault: { line_id: 1, last_red_device_id: 555 },
    alarmActiveDeviceIds: new Set([101])
  })!;
  assert.ok(v.faultRed.length >= 2, "yedek yol kirmizi parca cizmemis");
  assert.ok(Math.abs(v.faultRed[0][1] - 35.05) < 1e-6, "yedek yol yanlis cihazdan basliyor");
});

// ---------------------------------------------------------------------------
// Cihaz konumlari
// ---------------------------------------------------------------------------

test("ayni slottaki cihazlar UST USTE binmez", () => {
  // Hepsinin `device_position_t`si varsayilan 0.5 iken tek noktada
  // cizilirlerdi; harita "burada bir cihaz var" der, oysa uc tane var.
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101), SEG(1, 2, 102), SEG(1, 2, 103)],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set()
  })!;
  const lonlar = v.deviceMarkers.map((d) => d.lon);
  assert.equal(new Set(lonlar).size, 3, `cihazlar ust uste: ${lonlar}`);
});

test("elle ayarlanmis konum KORUNUR", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101, 0.25)],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set()
  })!;
  assert.ok(Math.abs(v.deviceMarkers[0].lon - 35.025) < 1e-9, `konum kaymis: ${v.deviceMarkers[0].lon}`);
});

test("cihaz adi cozulemezse KIMLIK yine gorunur", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    devices: [],
    poles: DIREKLER,
    segments: [SEG(1, 2, 999)],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set()
  })!;
  assert.match(v.deviceMarkers[0].name, /999/, "bilinmeyen cihaz kimliksiz gosteriliyor");
});

test("BASKA hattin cihazi/direkleri bu hatta karismaz", () => {
  const yabanci = { id: 9, line_id: 2, sequence_no: 1, latitude: 40, longitude: 36, name: null };
  const v = buildFaultMapView({
    ...TEMEL,
    lines: [
      { id: 1, name: "ANA HAT" },
      { id: 2, name: "BR-4" }
    ],
    poles: [...DIREKLER, yabanci],
    segments: [SEG(1, 2, 101), { ...SEG(1, 2, 102), line_id: 2 }],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set()
  })!;
  assert.equal(v.deviceMarkers.length, 1);
  assert.equal(v.polesWithRole.length, DIREKLER.length);
});

// ---------------------------------------------------------------------------
// Odak kutulari ve merkez
// ---------------------------------------------------------------------------

test("uc odak da CIZILEBILIR kutu dondurur", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    lines: [
      { id: 1, name: "ANA HAT" },
      { id: 2, name: "BR-4" }
    ],
    poles: [
      ...DIREKLER,
      { id: 9, line_id: 2, sequence_no: 1, latitude: 40, longitude: 36, name: null },
      { id: 10, line_id: 2, sequence_no: 2, latitude: 40.1, longitude: 36.1, name: null }
    ],
    segments: [SEG(2, 3, 101), SEG(3, 4, 102)],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set([101])
  })!;
  assert.ok(v.zoneBounds.length >= 2, "bolge kutusu cizilemez");
  assert.ok(v.lineBounds.length >= 2, "hat kutusu cizilemez");
  // Sebeke gorunumu komsu hatti da kapsamali; yoksa "tum sebeke" adi yalan.
  assert.ok(v.gridBounds.length > v.lineBounds.length, "komsu hat kutuya girmemis");
  assert.equal(v.otherLines.length, 1);
});

test("ariza araligi olmayan kayitta merkez NaN OLMAZ", () => {
  // Topoloji duzenlenip aralik disari dusebilir. NaN merkez, Leaflet'i
  // hicbir yere gitmez hale getirir — harita bos gorunur.
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(1, 2, 101)],
    fault: { line_id: 1, from_pole_seq: 90, to_pole_seq: 99 },
    alarmActiveDeviceIds: new Set()
  })!;
  assert.ok(Number.isFinite(v.center[0]) && Number.isFinite(v.center[1]), `merkez bozuk: ${v.center}`);
  assert.ok(v.rangePoles.length > 0, "aralik bos kalinca direk listesi de bosalmis");
});

test("hatta hic direk yoksa null — cizecek bir sey yok", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    poles: [],
    segments: [],
    fault: { line_id: 1 },
    alarmActiveDeviceIds: new Set()
  });
  assert.equal(v, null);
});

test("ariza araligindaki direkler BASLANGIC/BITIS olarak isaretli", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    poles: DIREKLER,
    segments: [SEG(2, 3, 101)],
    fault: { line_id: 1, from_pole_id: 2, to_pole_id: 3, from_pole_seq: 2, to_pole_seq: 3 },
    alarmActiveDeviceIds: new Set([101])
  })!;
  assert.deepEqual(
    v.rangePoles.map((r) => [r.sequence_no, r.isStart, r.isEnd]),
    [
      [2, true, false],
      [3, false, true]
    ]
  );
});

test("zoom yayilimla TERS orantili", () => {
  assert.ok(zoomFor(0.001) > zoomFor(0.05), "kucuk alanda daha uzak zoom");
  assert.ok(zoomFor(0.5) >= 10 && zoomFor(0.001) <= 18, "zoom makul araligin disinda");
});

test("lerp uc noktalari tam verir", () => {
  const a = { latitude: 0, longitude: 0 };
  const b = { latitude: 10, longitude: 20 };
  assert.deepEqual(lerp(a, b, 0), [0, 0]);
  assert.deepEqual(lerp(a, b, 1), [10, 20]);
  assert.deepEqual(lerp(a, b, 0.5), [5, 10]);
});

// ---------------------------------------------------------------------------
// Arizanin SICRADIGI kollar
//
// Kollar cizimle AYNI kaynaktan gelir (`buildFaultStripInputs`); harita kendi
// hesabini yapsaydi "sema bu kol temiz derken harita supheli gosteriyor"
// durumu dogardi. Harita bu kollari HIC cizmiyordu: kol yalnizca "Tum sebeke"
// odaginda, diger butun hatlarla ayni soluk gri ile gorunuyordu. Ekip ise
// sahaya cikarken kolu da gezmek zorunda.
// ---------------------------------------------------------------------------

/** Ana hattin 3. diregine bagli, iki direkli bir kol. */
const KOL_HATLARI = [
  { id: 1, name: "ANA HAT" },
  { id: 7, name: "BR-3", branched_from_pole_id: 3 }
];
const KOL_DIREKLERI = [
  { id: 70, line_id: 7, sequence_no: 1, latitude: 39.05, longitude: 35.2, name: null },
  { id: 71, line_id: 7, sequence_no: 2, latitude: 39.1, longitude: 35.22, name: null }
];

test("kol DALLANMA DIREGINDEN baslar — cizim havada asili kalmaz", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    lines: KOL_HATLARI,
    poles: [...DIREKLER, ...KOL_DIREKLERI],
    segments: [SEG(2, 3, 101), SEG(3, 4, 102)],
    fault: { line_id: 1, from_pole_seq: 2, to_pole_seq: 4 },
    branches: [{ lineId: 7, name: "BR-3" }],
    alarmActiveDeviceIds: new Set([101])
  })!;
  assert.equal(v.branchLines.length, 1);
  const kol = v.branchLines[0];
  assert.equal(kol.name, "BR-3");
  // Ilk nokta ana hattin 3. diregi; kolun kendi ilk diregi degil.
  assert.deepEqual(kol.path[0], [39, 35.2]);
  assert.deepEqual(kol.path.at(-1), [39.1, 35.22]);
  assert.equal(kol.path.length, 3);
});

test("kol ARIZA BOLGESI kutusuna girer — yoksa odakta cerceve disinda kalir", () => {
  const ortak = {
    ...TEMEL,
    lines: KOL_HATLARI,
    poles: [...DIREKLER, ...KOL_DIREKLERI],
    segments: [SEG(2, 3, 101), SEG(3, 4, 102)],
    alarmActiveDeviceIds: new Set([101])
  };
  const kolsuz = buildFaultMapView({
    ...ortak,
    fault: { line_id: 1, from_pole_seq: 2, to_pole_seq: 4 }
  })!;
  const kollu = buildFaultMapView({
    ...ortak,
    fault: { line_id: 1, from_pole_seq: 2, to_pole_seq: 4 },
    branches: [{ lineId: 7, name: "BR-3" }]
  })!;
  assert.equal(kolsuz.branchLines.length, 0);
  const kuzey = (kutu: [number, number][]) => Math.max(...kutu.map(([lat]) => lat));
  assert.ok(
    kuzey(kollu.zoneBounds) >= 39.1 - 1e-9,
    "bolge kutusu kolun ucunu kapsamiyor"
  );
  assert.ok(kuzey(kolsuz.zoneBounds) < 39.05, "kolsuz kutu kuzeye tasmis");
  assert.ok(kuzey(kollu.lineBounds) >= 39.1 - 1e-9, "hat kutusu kolu kapsamiyor");
});

test("TEK DIREKLI kol da cizilebilir — ikinci noktayi dallanma diregi verir", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    lines: KOL_HATLARI,
    poles: [...DIREKLER, KOL_DIREKLERI[0]],
    segments: [SEG(2, 3, 101), SEG(3, 4, 102)],
    fault: { line_id: 1, from_pole_seq: 2, to_pole_seq: 4 },
    branches: [{ lineId: 7, name: "BR-3" }],
    alarmActiveDeviceIds: new Set([101])
  })!;
  assert.equal(v.branchLines.length, 1);
  assert.deepEqual(v.branchLines[0].path, [
    [39, 35.2],
    [39.05, 35.2]
  ]);
});

test("kolda KENDI ariza kaydi varsa dogrulandi isaretlenir", () => {
  const kur = (has_own_fault: boolean) =>
    buildFaultMapView({
      ...TEMEL,
      lines: KOL_HATLARI,
      poles: [...DIREKLER, ...KOL_DIREKLERI],
      segments: [SEG(2, 3, 101), SEG(3, 4, 102)],
      fault: { line_id: 1, from_pole_seq: 2, to_pole_seq: 4 },
      branches: [{ lineId: 7, name: "BR-3", confirmed: has_own_fault }],
      alarmActiveDeviceIds: new Set([101])
    })!.branchLines[0];
  assert.equal(kur(true).dogrulandi, true);
  assert.equal(kur(false).dogrulandi, false);
});

test("giris cihazi GORMEDIM diyen kol temiz isaretlenir", () => {
  // Kolun basindaki cihaz arizayi gormediyse ariza kolda olamaz; harita onu
  // supheli (amber) degil saglam (yesil) gostermeli — sema da oyle gosteriyor.
  const kol = buildFaultMapView({
    ...TEMEL,
    lines: KOL_HATLARI,
    poles: [...DIREKLER, ...KOL_DIREKLERI],
    segments: [SEG(2, 3, 101), SEG(3, 4, 102)],
    fault: { line_id: 1, from_pole_seq: 2, to_pole_seq: 4 },
    branches: [{ lineId: 7, name: "BR-3", cleared: true }],
    alarmActiveDeviceIds: new Set([101])
  })!.branchLines[0];
  assert.equal(kol.temiz, true);
  assert.equal(kol.dogrulandi, false);
  // Temiz olsa da bolge kutusuna girer: ekip yine o kolun onunden geciyor.
  assert.equal(kol.path.length, 3);
});

test("cizilemeyecek kol SESSIZCE atlanir — bozuk kayit haritayi dusurmez", () => {
  const v = buildFaultMapView({
    ...TEMEL,
    // Dallanma diregi bilinmiyor ve kolun tek diregi var: polyline icin
    // ikinci nokta yok. Tek noktali cizgi haritada gorunmez bir artiktir.
    lines: [
      { id: 1, name: "ANA HAT" },
      { id: 7, name: "BR-3" }
    ],
    poles: [...DIREKLER, KOL_DIREKLERI[0]],
    segments: [SEG(2, 3, 101)],
    fault: { line_id: 1, from_pole_seq: 2, to_pole_seq: 4 },
    branches: [
      { lineId: 7, name: "BR-3" },
      { lineId: 99, name: "YOK" }
    ],
    alarmActiveDeviceIds: new Set([101])
  })!;
  assert.equal(v.branchLines.length, 0);
});
