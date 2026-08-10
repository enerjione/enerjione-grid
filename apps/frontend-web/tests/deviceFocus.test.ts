/**
 * Ana haritada cihaz secilince kamera nereye gider?
 *
 * YASANAN SORUN: sabit `flyTo(target, 13)`. Kullanici direk seviyesinde
 * (zoom 16-17) calisirken bir cihaza tikladiginda harita UZAKLASIYORDU —
 * "cihazi goster" eylemi, kullanicinin kurdugu yakinligi bozup onu tekrar
 * yakinlastirmaya zorluyordu.
 *
 * Dogru davranis: cihazin bagli oldugu HAT ekrana sigsin.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SINGLE_DEVICE_ZOOM,
  planDeviceFocus
} from "../src/features/map/deviceFocus";

const CIHAZ = { id: 7, latitude: 39.0, longitude: 35.0 };
const HAT = [
  { latitude: 39.0, longitude: 35.0 },
  { latitude: 39.1, longitude: 35.1 },
  { latitude: 39.2, longitude: 35.2 }
];

test("hat biliniyorsa HATTIN TAMAMI sigdirilir", () => {
  const plan = planDeviceFocus({ device: CIHAZ, linePoints: HAT, lastKey: "" });
  assert.equal(plan.kind, "bounds");
  if (plan.kind !== "bounds") return;
  // Hattin uc diregi + secili cihaz.
  assert.ok(plan.points.length >= HAT.length);
});

test("secili cihaz kutuya HER ZAMAN dahil", () => {
  // Cihazin koordinati topolojiden ayrilmis olabilir; secilen sey mutlaka
  // gorunmeli.
  const uzak = { id: 9, latitude: 40.5, longitude: 36.5 };
  const plan = planDeviceFocus({ device: uzak, linePoints: HAT, lastKey: "" });
  assert.equal(plan.kind, "bounds");
  if (plan.kind !== "bounds") return;
  assert.ok(
    plan.points.some((p) => p.latitude === 40.5 && p.longitude === 36.5),
    "secili cihaz kutunun disinda kaldi"
  );
});

test("hat bilinmiyorsa cihaza SABIT yakinlik", () => {
  const plan = planDeviceFocus({ device: CIHAZ, linePoints: [], lastKey: "" });
  assert.equal(plan.kind, "point");
  if (plan.kind !== "point") return;
  assert.equal(plan.zoom, SINGLE_DEVICE_ZOOM);
});

test("tek noktali hat sigdirilmaz — noktaya gidilir", () => {
  const plan = planDeviceFocus({
    device: CIHAZ,
    linePoints: [{ latitude: 39.0, longitude: 35.0 }],
    lastKey: ""
  });
  assert.equal(plan.kind, "point");
});

test("yedek yakinlik ESKI 13'ten DAHA YAKIN", () => {
  // Sikayetin ozu buydu: 13 sokak duzeninin bile zor secildigi bir olcek.
  assert.ok(SINGLE_DEVICE_ZOOM > 13, `yedek zoom hala uzak: ${SINGLE_DEVICE_ZOOM}`);
});

test("AYNI cihaz icin iki kez odaklanilmaz", () => {
  // Polling 5 sn'de bir veriyi tazeliyor; her tazelemede kamerayi geri
  // almak kullanicinin elle kaydirmasini imkansiz kilardi.
  const plan = planDeviceFocus({ device: CIHAZ, linePoints: HAT, lastKey: "7" });
  assert.equal(plan.kind, "skip");
});

test("BASKA cihaza gecilince yeniden odaklanilir", () => {
  const plan = planDeviceFocus({
    device: { id: 8, latitude: 39.05, longitude: 35.05 },
    linePoints: HAT,
    lastKey: "7"
  });
  assert.notEqual(plan.kind, "skip");
});

test("secim yoksa hicbir sey yapilmaz", () => {
  assert.equal(planDeviceFocus({ device: null, linePoints: HAT, lastKey: "" }).kind, "skip");
});

test("BOZUK koordinat sessizce haritayi kilitlemez", () => {
  // Tek bir NaN, Leaflet'in bounds hesabini gecersiz kilar ve harita
  // hicbir yere gitmez — kullanici "tiklama calismiyor" der.
  const bozukCihaz = planDeviceFocus({
    device: { id: 1, latitude: Number.NaN, longitude: 35.0 },
    linePoints: HAT,
    lastKey: ""
  });
  assert.equal(bozukCihaz.kind, "skip");

  const bozukHat = planDeviceFocus({
    device: CIHAZ,
    linePoints: [...HAT, { latitude: Number.NaN, longitude: Number.NaN }],
    lastKey: ""
  });
  assert.equal(bozukHat.kind, "bounds");
  if (bozukHat.kind !== "bounds") return;
  assert.ok(
    bozukHat.points.every((p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude)),
    "bozuk nokta kutuya sizdi"
  );
});
