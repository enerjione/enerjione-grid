/**
 * Ariza Analizi grafiklerinin renk sozlesmesi.
 *
 * Palet SIRASI keyfi degil: renk korlugu (CVD) ayrim kontrolunden gecen
 * dizilim. Ilk denenen mavi/yesil/turuncu/mor sirasinda turuncu ile yesil
 * KOMSU dusuyor ve deuteranopia'da ayirt edilemiyordu (dE 7.3, esik 8).
 * Mor'u araya almak komsulugu bozuyor ve en kotu komsu cift dE 30.3'e
 * cikiyor.
 *
 * Biri "renkler daha guzel dursun" diye sirayi degistirirse ekran renk koru
 * bir operator icin sessizce okunamaz hale gelir — gorsel olarak HICBIR SEY
 * bozulmadigi icin de fark edilmez. Bu yuzden sira testle kilitli.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { FAZ_RENK, KATEGORIK, TEK_SERI } from "../src/features/fault-analytics/faultChartTheme";

test("kategorik sira CVD dogrulamasindan gecen dizilim", () => {
  assert.deepEqual(
    [...KATEGORIK],
    ["#2563eb", "#16a34a", "#7c3aed", "#c2410c"],
    "palet sirasi degismis — scripts/validate_palette.js ile yeniden dogrulanmali"
  );
});

test("turuncu ile yesil KOMSU degil", () => {
  // Bu ikisi CVD'de en zayif cift; yan yana gelirlerse ikincil kodlama
  // (dogrudan etiket) olmadan ayirt edilemezler.
  const i = KATEGORIK.indexOf("#c2410c");
  const j = KATEGORIK.indexOf("#16a34a");
  assert.ok(Math.abs(i - j) > 1, `turuncu(${i}) ve yesil(${j}) komsu dusmus`);
});

test("faz renkleri paletten geliyor — uydurma hue yok", () => {
  for (const [faz, renk] of Object.entries(FAZ_RENK)) {
    assert.ok(
      (KATEGORIK as readonly string[]).includes(renk),
      `${faz} icin palet disi renk: ${renk}`
    );
  }
});

test("tek serili grafiklerin rengi paletin ILK hue'su", () => {
  // Tek seri icin hue secmek gerekmiyor; sabit ilk renk kullanilir ki
  // grafikler arasi renk "anlam" tasidigi sanilmasin.
  assert.equal(TEK_SERI, KATEGORIK[0]);
});
