/**
 * TEKRAR EDEN ARIZA — "bu hat kacinci kez arizalandi?"
 *
 * Yanlis sayi iki yonde de zararli: olmayan bir tekrar uydurmak ekibi
 * gereksiz kok-sebep avina gonderir, gercek tekrari kacirmak ise ayni
 * arizanin ucuncu kez acilmasina goz yumar.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { buildFaultRecurrence } from "../src/features/faults/faultRecurrence";
import type { RecurrenceFault } from "../src/features/faults/faultRecurrence";

const GUN = 86_400_000;
const SIMDI = Date.parse("2026-08-10T12:00:00Z");

function kayit(
  id: number,
  gunOnce: number,
  opts: Partial<RecurrenceFault> = {}
): RecurrenceFault {
  return {
    id,
    line_id: 1,
    opened_at: new Date(SIMDI - gunOnce * GUN).toISOString(),
    from_pole_seq: 9,
    to_pole_seq: 12,
    ...opts
  };
}

const BU = kayit(100, 0);

test("baska kaydi olmayan hat: tekrar YOK", () => {
  const r = buildFaultRecurrence(BU, [BU]);
  assert.equal(r.total, 0);
  assert.equal(r.sameSection, 0);
  assert.equal(r.lastAt, null);
});

test("ayni hattaki ONCEKI kayitlar sayilir", () => {
  const r = buildFaultRecurrence(BU, [BU, kayit(1, 3), kayit(2, 30)]);
  assert.equal(r.total, 2);
});

test("BASKA hattin kaydi sayilmaz", () => {
  const r = buildFaultRecurrence(BU, [BU, kayit(1, 3, { line_id: 2 })]);
  assert.equal(r.total, 0, "baska hattin arizasi bu hattin tekrari degil");
});

test("pencere disindaki eski kayit sayilmaz", () => {
  const r = buildFaultRecurrence(BU, [BU, kayit(1, 200)], 90);
  assert.equal(r.total, 0);
  // Pencere buyutulunce geri gelir — esik keyfi degil, parametre.
  assert.equal(buildFaultRecurrence(BU, [BU, kayit(1, 200)], 365).total, 1);
});

test("SONRAKI kayitlar tekrar sayilmaz", () => {
  // Ayni anda acilmis kardes bolgeler (bir hatta iki ayri ariza) ayni olayin
  // parcalaridir; "bu ikinci kez" demek yanlis olur.
  const sonraki = kayit(2, -1);
  const r = buildFaultRecurrence(BU, [BU, sonraki]);
  assert.equal(r.total, 0);
});

test("AYNI KESIM kesisimle bulunur — kok sebep isareti", () => {
  const r = buildFaultRecurrence(BU, [
    BU,
    kayit(1, 5, { from_pole_seq: 11, to_pole_seq: 14 }), // 9-12 ile kesisir
    kayit(2, 6, { from_pole_seq: 1, to_pole_seq: 4 }) // kesismez
  ]);
  assert.equal(r.total, 2);
  assert.equal(r.sameSection, 1);
});

test("araligi BILINMEYEN kayit 'ayni kesim' sayilmaz", () => {
  // "Bilmiyorum"u "ayni yer" diye okumak ekibi yanlis yere kilitler.
  const r = buildFaultRecurrence(BU, [
    BU,
    kayit(1, 5, { from_pole_seq: null, to_pole_seq: null })
  ]);
  assert.equal(r.total, 1);
  assert.equal(r.sameSection, 0);
});

test("en son tarih ONCEKILERIN en yenisidir", () => {
  const r = buildFaultRecurrence(BU, [BU, kayit(1, 40), kayit(2, 3), kayit(3, 12)]);
  assert.equal(r.lastAt, new Date(SIMDI - 3 * GUN).toISOString());
});

test("bozuk tarih cokme uretmez", () => {
  const bozuk = { ...BU, opened_at: "hicbir sey" };
  const r = buildFaultRecurrence(bozuk, [bozuk, kayit(1, 3)]);
  assert.equal(r.total, 0, "tarihi okunamayan kayit icin sayi uydurulmamali");
  const r2 = buildFaultRecurrence(BU, [BU, { ...kayit(1, 3), opened_at: "" }]);
  assert.equal(r2.total, 0);
});
