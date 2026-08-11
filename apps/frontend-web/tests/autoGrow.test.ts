/**
 * Icerige gore buyuyen metin alani.
 *
 * YASANAN SORUN: "Aciklama" alani `rows={2}` ile yazilmisti ama CSS
 * `min-height: 220px` dayatiyordu. Aciklama BOSKEN bile ekranin dortte
 * birini kapliyor, panel tasiyor ve icerideki alanin kendi cubuguyla
 * birlikte FAZLADAN bir kaydirma cubugu cikiyordu.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  AUTO_GROW_MAX_PX,
  AUTO_GROW_MIN_PX,
  autoGrowHeight,
  autoGrowScrolls
} from "../src/shared/autoGrow";

test("kisa metin TABANA oturur — bos alan tiklanabilir kalir", () => {
  // Sifir yukseklikli bir kutu "buraya yazilabilir" demez.
  assert.equal(autoGrowHeight(10), AUTO_GROW_MIN_PX);
  assert.equal(autoGrowHeight(AUTO_GROW_MIN_PX - 1), AUTO_GROW_MIN_PX);
});

test("orta uzunlukta metin ICERIK kadar yer kaplar", () => {
  const h = autoGrowHeight(120);
  assert.equal(h, 120);
  assert.ok(h > AUTO_GROW_MIN_PX && h < AUTO_GROW_MAX_PX);
});

test("cok uzun metin TAVANDA durur — sayfa sonsuza uzamaz", () => {
  assert.equal(autoGrowHeight(5000), AUTO_GROW_MAX_PX);
});

test("tavana dayaninca alanin KENDI kaydirmasi devreye girer", () => {
  // Tek ve anlasilir bir cubuk: alan kendi icinde kayar, panel tasmaz.
  assert.equal(autoGrowScrolls(5000), true);
  assert.equal(autoGrowScrolls(120), false);
  assert.equal(autoGrowScrolls(AUTO_GROW_MAX_PX), false, "tam tavanda kaydirma gereksiz");
});

test("GIZLI sekmede olculen alan yok olmaz", () => {
  // Gorunmeyen bir elemanin `scrollHeight`i 0 doner. 0 uygulanirsa sekme
  // acildiginda kutu tamamen kaybolmus gorunur.
  assert.equal(autoGrowHeight(0), AUTO_GROW_MIN_PX);
  assert.equal(autoGrowHeight(Number.NaN), AUTO_GROW_MIN_PX);
  assert.equal(autoGrowHeight(-40), AUTO_GROW_MIN_PX);
});

test("kesirli olcum YUKARI yuvarlanir — son satir kirpilmaz", () => {
  // Asagi yuvarlamak son satirin bir iki pikselini kesip alanda gereksiz
  // bir kaydirma cubugu birakiyordu.
  assert.equal(autoGrowHeight(120.2), 121);
});

test("sinirlar disaridan verilebilir", () => {
  assert.equal(autoGrowHeight(500, 40, 200), 200);
  assert.equal(autoGrowHeight(10, 40, 200), 40);
  assert.equal(autoGrowScrolls(300, 200), true);
});

test("taban tavandan buyukse taban kazanir — kutu negatif olmaz", () => {
  // Cagiran hatali sinir verirse bile olcu anlamli kalmali.
  const h = autoGrowHeight(100, 200, 50);
  assert.ok(h > 0, `bozuk yukseklik: ${h}`);
});
