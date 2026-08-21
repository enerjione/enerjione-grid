/**
 * Ana haritada cihaz secilince kamera nereye gider?
 *
 * KARAR: CIHAZA YAKINLAS.
 *
 * Onceki surum hattin TAMAMINI sigdiriyordu; gerekcesi "operatorun sordugu
 * soru 'bu cihaz hattin neresinde'" idi. Sahada bunun tersi cikti: uzun bir
 * hatta sigdirma, secilen cihazi haritanin bir kosesinde nokta boyutunda
 * birakiyor ve "hangisini sectim" sorusunu cevapsiz birakiyordu. Hattin
 * butunu zaten hicbir sey secili degilken gorunuyor.
 *
 * ESKI TUZAK TEKRARLANMAMALI: ilk surumde `flyTo(hedef, 13)` vardi — SABIT
 * zoom. Kullanici direk seviyesinde (16-17) calisirken bir cihaza
 * tikladiginda harita UZAKLASIYORDU. Bu yuzden hedef yakinlik ASLA
 * MEVCUDUN ALTINA DUSMEZ.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  DEVICE_ZOOM,
  SINGLE_DEVICE_ZOOM,
  hedefZoom,
  planDeviceFocus
} from "../src/features/map/deviceFocus";

const CIHAZ = { id: 7, latitude: 39.0, longitude: 35.0 };

// ---------------------------------------------------------------------------
// 1) CIHAZA YAKINLASMA
// ---------------------------------------------------------------------------

test("cihaz secilince CIHAZA yakinlasilir", () => {
  const plan = planDeviceFocus({ device: CIHAZ, lastKey: "" });
  assert.equal(plan.kind, "point");
  if (plan.kind !== "point") return;
  assert.equal(plan.latitude, 39.0);
  assert.equal(plan.longitude, 35.0);
});

test("hat SIGDIRILMAZ — secilen cihaz nokta boyutunda kalmasin", () => {
  // Regresyon kapisi: `bounds` plani tamamen kaldirildi.
  const plan = planDeviceFocus({ device: CIHAZ, lastKey: "" });
  assert.notEqual(plan.kind as string, "bounds");
});

test("hedef yakinlik DIREK SEVIYESI", () => {
  // 15 mahalle olcegiydi ve iki komsu direk hala ayni noktada gorunuyordu.
  assert.ok(DEVICE_ZOOM >= 16, `hedef zoom hala uzak: ${DEVICE_ZOOM}`);
  assert.equal(SINGLE_DEVICE_ZOOM, DEVICE_ZOOM, "geriye donuk ad ayrismis");
});

// ---------------------------------------------------------------------------
// 2) ASLA UZAKLASTIRMA — ilk surumun hatasi
// ---------------------------------------------------------------------------

test("kullanici DAHA YAKINSA oldugu yerde kalir", () => {
  // Sikayetin ozu buydu: "cihazi goster" eylemi kullanicinin kurdugu
  // yakinligi bozuyordu.
  assert.equal(hedefZoom(18), 18);
  assert.equal(hedefZoom(19.5), 19.5);
});

test("kullanici UZAKTAYSA hedefe yakinlasilir", () => {
  assert.equal(hedefZoom(10), DEVICE_ZOOM);
  assert.equal(hedefZoom(DEVICE_ZOOM - 1), DEVICE_ZOOM);
});

test("mevcut yakinlik BILINMIYORSA hedef kullanilir", () => {
  assert.equal(hedefZoom(null), DEVICE_ZOOM);
  assert.equal(hedefZoom(undefined), DEVICE_ZOOM);
  assert.equal(hedefZoom(Number.NaN), DEVICE_ZOOM);
});

test("plan MEVCUT yakinligi hesaba katar", () => {
  const yakin = planDeviceFocus({ device: CIHAZ, lastKey: "", currentZoom: 18 });
  assert.equal(yakin.kind, "point");
  if (yakin.kind !== "point") return;
  assert.equal(yakin.zoom, 18, "kullanici uzaklastirildi");

  const uzak = planDeviceFocus({ device: CIHAZ, lastKey: "", currentZoom: 9 });
  if (uzak.kind !== "point") return;
  assert.equal(uzak.zoom, DEVICE_ZOOM);
});

// ---------------------------------------------------------------------------
// 3) KENAR DURUMLAR
// ---------------------------------------------------------------------------

test("AYNI cihaz icin iki kez odaklanilmaz", () => {
  // Polling 5 sn'de bir veriyi tazeliyor; her tazelemede kamerayi geri
  // almak kullanicinin elle kaydirmasini imkansiz kilardi.
  assert.equal(planDeviceFocus({ device: CIHAZ, lastKey: "7" }).kind, "skip");
});

test("BASKA cihaza gecilince yeniden odaklanilir", () => {
  const plan = planDeviceFocus({
    device: { id: 8, latitude: 39.05, longitude: 35.05 },
    lastKey: "7"
  });
  assert.notEqual(plan.kind, "skip");
});

test("secim yoksa hicbir sey yapilmaz", () => {
  assert.equal(planDeviceFocus({ device: null, lastKey: "" }).kind, "skip");
  assert.equal(planDeviceFocus({ device: undefined, lastKey: "" }).kind, "skip");
});

test("BOZUK koordinat sessizce haritayi kilitlemez", () => {
  // Tek bir NaN, Leaflet'in hesabini gecersiz kilar ve harita hicbir yere
  // gitmez — kullanici "tiklama calismiyor" der.
  for (const bozuk of [
    { id: 1, latitude: Number.NaN, longitude: 35.0 },
    { id: 2, latitude: 39.0, longitude: Number.NaN },
    { id: 3, latitude: Number.POSITIVE_INFINITY, longitude: 35.0 }
  ]) {
    assert.equal(planDeviceFocus({ device: bozuk, lastKey: "" }).kind, "skip");
  }
});

// ---------------------------------------------------------------------------
// 4) CAGIRAN TARAF
// ---------------------------------------------------------------------------

test("harita bileseni MEVCUT yakinligi plana veriyor", () => {
  const src = readFileSync(
    join(process.cwd(), "src", "features", "map", "DeviceMapTab.tsx"),
    "utf8"
  );
  assert.match(src, /currentZoom: map\.getZoom\(\)/, "mevcut yakinlik gecirilmiyor");
  // `flyToBounds` artik kullanilmamali: hat sigdirma kaldirildi.
  assert.ok(!src.includes("flyToBounds"), "hat sigdirma geri gelmis");
});
