/**
 * Hat secilince haritanin o hatta odaklanmasi (Hat Yonetimi > Harita).
 *
 * YASANAN ARIZA (kullanici bildirimi, 2026-08-06): "hatti seciyorum ama
 * onun diregine yakinlasmiyor". Harita tum ulkeyi gosterip oyle kaliyordu.
 *
 * Karar mantigi Leaflet cagrilarindan ayrildi (`features/grid/lineFocus.ts`)
 * cunku her kosulu bir hata sinifina karsilik geliyor ve React bileseni
 * icinde kaldigi surece CALISTIRILARAK dogrulanamiyordu.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { SINGLE_POLE_ZOOM, planLineFocus } from "../src/features/grid/lineFocus";

const D = (lat: number, lon: number) => ({ latitude: lat, longitude: lon });

test("hat secili degilse odaklanma yok", () => {
  const p = planLineFocus({ lineId: null, poles: [D(39, 35)], nonce: 0, lastKey: "" });
  assert.equal(p.kind, "skip");
});

test("direkler HENUZ GELMEDIYSE bekle — ve 'yapildi' isaretleme", () => {
  // Hattin detayi asenkron yukleniyor; secim aninda liste bostur. Burada
  // "yapildi" isaretlenseydi, direkler geldiginde bir daha odaklanilmaz ve
  // harita acilistaki ulke genelinde kalirdi. Kapatilan hata tam olarak bu.
  const bos = planLineFocus({ lineId: 7, poles: [], nonce: 0, lastKey: "" });
  assert.equal(bos.kind, "skip");

  const sonra = planLineFocus({
    lineId: 7,
    poles: [D(39.9, 32.8), D(39.95, 32.9)],
    nonce: 0,
    // Bos gecis hicbir anahtar yazmadigi icin lastKey hala bos.
    lastKey: ""
  });
  assert.equal(sonra.kind, "bounds");
});

test("tek direk: sabit yakinlikta ortala", () => {
  const p = planLineFocus({ lineId: 3, poles: [D(41.01, 28.97)], nonce: 0, lastKey: "" });
  assert.equal(p.kind, "point");
  if (p.kind !== "point") return;
  assert.equal(p.latitude, 41.01);
  assert.equal(p.longitude, 28.97);
  assert.equal(p.zoom, SINGLE_POLE_ZOOM);
});

test("birden fazla direk: hepsini kapsayan kutu", () => {
  const poles = [D(39.9, 32.8), D(39.95, 32.9), D(40.0, 33.0)];
  const p = planLineFocus({ lineId: 3, poles, nonce: 0, lastKey: "" });
  assert.equal(p.kind, "bounds");
  if (p.kind !== "bounds") return;
  assert.equal(p.points.length, 3);
});

test("BOZUK koordinat atilir — tek NaN tum kutuyu zehirlerdi", () => {
  // Leaflet'te bounds icindeki tek bir NaN sonucu gecersiz kilar; harita
  // hicbir yere gitmez ve hata da vermez.
  const poles = [
    D(39.9, 32.8),
    D(Number.NaN, 32.9),
    { latitude: null as unknown as number, longitude: 33.0 },
    D(40.0, 33.0)
  ];
  const p = planLineFocus({ lineId: 3, poles, nonce: 0, lastKey: "" });
  assert.equal(p.kind, "bounds");
  if (p.kind !== "bounds") return;
  assert.equal(p.points.length, 2, "yalnizca gecerli iki nokta kalmali");
});

test("TUM koordinatlar bozuksa odaklanma yok", () => {
  const p = planLineFocus({
    lineId: 3,
    poles: [D(Number.NaN, Number.NaN)],
    nonce: 0,
    lastKey: ""
  });
  assert.equal(p.kind, "skip");
});

test("ayni hat + ayni istek icin IKI KEZ odaklanma", () => {
  // Direk suruklemek/veri tazelemek `poles` referansini degistirir. Her
  // seferinde yeniden odaklanmak, kullanicinin elle yaptigi zoom'u geri
  // alirdi.
  const poles = [D(39.9, 32.8), D(40.0, 33.0)];
  const ilk = planLineFocus({ lineId: 5, poles, nonce: 2, lastKey: "" });
  assert.equal(ilk.kind, "bounds");
  if (ilk.kind === "skip") return;

  const ikinci = planLineFocus({ lineId: 5, poles, nonce: 2, lastKey: ilk.key });
  assert.equal(ikinci.kind, "skip");
});

test("ZATEN SECILI hatta tekrar tiklamak yeniden odaklar (nonce)", () => {
  // Sikayetin ikinci yarisi: kullanici haritayi kaydirdiktan sonra hatta
  // tikliyor ve hicbir sey olmuyordu — secim degismedigi icin.
  const poles = [D(39.9, 32.8), D(40.0, 33.0)];
  const ilk = planLineFocus({ lineId: 5, poles, nonce: 1, lastKey: "" });
  assert.equal(ilk.kind, "bounds");
  if (ilk.kind === "skip") return;

  const tekrar = planLineFocus({ lineId: 5, poles, nonce: 2, lastKey: ilk.key });
  assert.equal(tekrar.kind, "bounds", "nonce artinca ayni hat icin de odaklan");
});

test("baska hatta gecince odaklan", () => {
  const a = planLineFocus({
    lineId: 1,
    poles: [D(39.9, 32.8), D(40.0, 33.0)],
    nonce: 0,
    lastKey: ""
  });
  assert.equal(a.kind, "bounds");
  if (a.kind === "skip") return;

  const b = planLineFocus({
    lineId: 2,
    poles: [D(37.0, 35.3), D(37.1, 35.4)],
    nonce: 0,
    lastKey: a.key
  });
  assert.equal(b.kind, "bounds");
});
