/**
 * Cihazin slot uzerindeki oraninin (`device_position_t`) hesabi.
 *
 * Hat Yonetimi'nde cihaz isaretcisi surukleyip birakilinca bu oran uretilir;
 * backend ayni oranla cihazin koordinatini yeniden hesaplar. Hata SESSIZDIR:
 * harita bir sey cizmeye devam eder, yalnizca yanlis yerde.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { slotNoktasi, slotOrani } from "../src/features/grid/slotProjection";

const A = { latitude: 39.0, longitude: 35.0 };
const B = { latitude: 39.02, longitude: 35.0 }; // saf kuzey-guney

test("uclar 0 ve 1", () => {
  assert.equal(slotOrani(A, B, A), 0);
  assert.equal(slotOrani(A, B, B), 1);
});

test("orta nokta 0.5", () => {
  const orta = { latitude: 39.01, longitude: 35.0 };
  assert.ok(Math.abs(slotOrani(A, B, orta) - 0.5) < 1e-9);
});

test("slot DISINA dusen nokta uca KIRPILIR", () => {
  // Cihaz kendi araliginin disina tasarsa ariza hesabi yanlis araliga bakar.
  assert.equal(slotOrani(A, B, { latitude: 38.9, longitude: 35.0 }), 0);
  assert.equal(slotOrani(A, B, { latitude: 39.5, longitude: 35.0 }), 1);
});

test("hattan SAPMIS nokta hat uzerine izdusurulur", () => {
  // Yana kacik birakilan isaretci, hat boyunca ayni yuksekligin karsiligina
  // oturmali — cihaz "havada" kalamaz.
  const yanda = { latitude: 39.01, longitude: 35.004 };
  assert.ok(Math.abs(slotOrani(A, B, yanda) - 0.5) < 1e-9);
});

test("iki direk AYNI noktadaysa 0 doner (bolme yok)", () => {
  assert.equal(slotOrani(A, A, { latitude: 39.5, longitude: 35.9 }), 0);
});

test("BOYLAM olcegi hesaba katilir — capraz hatta ham derece yanlis sonuc verir", () => {
  // ASIL REGRESYON. 39. enlemde bir boylam derecesi bir enlem derecesinden
  // ~%22 kisa GORUNUR (cos 39 ≈ 0,777). Izdusum ham derecelerle yapilinca
  // "hattin uzerindeki en yakin nokta" ekranda gorulen nokta olmuyordu.
  const kuzeydogu = { latitude: 39.02, longitude: 35.02 }; // capraz slot
  const nokta = { latitude: 39.015, longitude: 35.005 };

  const dogru = slotOrani(A, kuzeydogu, nokta);

  // Olceksiz (eski) hesap:
  const dx = kuzeydogu.latitude - A.latitude;
  const dy = kuzeydogu.longitude - A.longitude;
  const eski =
    ((nokta.latitude - A.latitude) * dx + (nokta.longitude - A.longitude) * dy) /
    (dx * dx + dy * dy);

  assert.notEqual(
    Number(dogru.toFixed(4)),
    Number(eski.toFixed(4)),
    "olcekli ve olceksiz hesap ayni cikti — duzeltme etkisiz",
  );
});

test("oran -> nokta -> oran turu kendini korur", () => {
  // Kaydedilen oranla cizilen nokta, yeniden izdusurulunce ayni orani
  // vermeli; yoksa her acilista cihaz biraz kayardi.
  const capraz = { latitude: 39.02, longitude: 35.02 };
  for (const t of [0, 0.1, 0.37, 0.5, 0.83, 1]) {
    const [lat, lon] = slotNoktasi(A, capraz, t);
    const geri = slotOrani(A, capraz, { latitude: lat, longitude: lon });
    assert.ok(Math.abs(geri - t) < 1e-9, `t=${t} turda ${geri} oldu`);
  }
});
