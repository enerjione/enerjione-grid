/**
 * Hat Yonetimi haritasindaki cihaz etiketi.
 *
 * YASANAN SIKAYET: "hat yonetimi sayfasinda cihazlarin isimleri gozuksun,
 * hangi cihaz ne goremiyorum". Ad yalnizca `devices` listesinden okunuyordu ve
 * o liste bos/eksik oldugunda harita cihazi ADSIZ bir elmas olarak ciziyordu.
 * Segment ise adi zaten tasiyor. Bu dosya, listeye BAGLI OLMADAN her zaman bir
 * etiket uretildigini kilitler — sessiz bir gerileme, cunku harita "bir sey"
 * cizmeye devam eder, yalnizca okunamaz.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { cihazEtiketi, cihazKodu } from "../src/features/grid/deviceLabel";

test("cihaz kaydi varsa ADI kullanilir", () => {
  assert.equal(
    cihazEtiketi({ name: "DEMO-5", code: "5860" }, { device_id: 5, device_name: "eski", device_code: "x" }),
    "DEMO-5"
  );
});

test("CIHAZ LISTESI BOSSA segmentin tasidigi ad kullanilir", () => {
  // Regresyonun ta kendisi: burada eskiden hicbir etiket cizilmiyordu.
  assert.equal(
    cihazEtiketi(undefined, { device_id: 5, device_name: "GC-12", device_code: "5860" }),
    "GC-12"
  );
  assert.equal(cihazEtiketi(null, { device_id: 5, device_name: "GC-12" }), "GC-12");
});

test("ad hicbir yerde yoksa KODA duser", () => {
  assert.equal(cihazEtiketi(undefined, { device_id: 7, device_code: "5861" }), "5861");
  assert.equal(cihazEtiketi({ name: "  ", code: "5861" }, { device_id: 7 }), "5861");
});

test("ad da kod da yoksa KIMLIK yazar — bos etiket cizilmez", () => {
  assert.equal(cihazEtiketi(undefined, { device_id: 12 }), "#12");
  assert.equal(cihazEtiketi({ name: null, code: null }, { device_id: 12 }), "#12");
});

test("yalnizca BOSLUKTAN olusan ad gecerli sayilmaz", () => {
  // Bosluk bir ad degildir; etiket bos bir siyah kutu olarak cizilirdi.
  assert.equal(
    cihazEtiketi({ name: "   ", code: "   " }, { device_name: "  ", device_code: "5862", device_id: 3 }),
    "5862"
  );
});

test("ipucundaki kod AD ILE AYNI ise tekrarlanmaz", () => {
  // "5861 (5861)" bilgi tasimaz.
  assert.equal(cihazKodu(undefined, { device_id: 7, device_code: "5861" }), "");
  assert.equal(cihazKodu({ name: "DEMO-5", code: "5860" }, { device_id: 7 }), "5860");
});

test("kod yoksa ipucu kod alani BOS kalir", () => {
  assert.equal(cihazKodu(undefined, { device_id: 7, device_name: "GC-12" }), "");
});

test("kimligi de olmayan kayit haritayi dusurmez", () => {
  assert.equal(cihazEtiketi(undefined, {}), "#?");
});
