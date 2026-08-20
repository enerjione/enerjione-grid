/**
 * CANLI DEGERLER — cikis noktalari listede GORUNMEZ.
 *
 * NE KORUNUYOR
 * ------------
 * `binary_output` / `analog_output` DNP3'un KOMUT tarafidir: operatorun
 * cihaza YAZDIGI nokta, cihazin bildirdigi bir olcum degil. Degerleri de
 * yok ("—"). 1786 satirlik bir listede bunlar aranan olcumu gomuyordu.
 *
 * `binary_output` zaten SEKME listesinde degildi ama satirlar
 * filtrelenmiyordu — "Tumu" sekmesi hepsini yine de basiyordu. Yani "sekmeden
 * cikarmak" tek basina yetmiyor; filtre VERI tarafinda olmali.
 *
 * Filtrenin TEK YERDE olmasi da sayilarin tutarliligi icin sart: sayaclar,
 * dropdown secenekleri, tablo ve toplam hep ayni listeden beslenmeli. Yalnizca
 * tabloyu filtrelemek "1786 kayit" yazip 1400 satir cizmek olurdu.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const KAYNAK = readFileSync(
  join(process.cwd(), "src", "features", "live-values", "LiveValuesPage.tsx"),
  "utf8"
);

test("iki cikis tipi de filtre kumesinde", () => {
  const m = /CIKIS_TIPLERI[^=]*=\s*new Set\(\[([^\]]*)\]\)/.exec(KAYNAK);
  assert.ok(m, "CIKIS_TIPLERI tanimi yok");
  const kume = m![1];
  assert.match(kume, /binary_output/, "binary_output filtrelenmiyor");
  assert.match(kume, /analog_output/, "analog_output filtrelenmiyor");
});

test("cikis tipleri SEKME listesinde de yok", () => {
  const m = /const DATA_TYPES: SignalDataType\[\] = \[([\s\S]*?)\];/.exec(KAYNAK);
  assert.ok(m, "DATA_TYPES tanimi yok");
  assert.doesNotMatch(m![1], /output/, "cikis tipi hala sekme olarak duruyor");
});

test("filtre TEK YERDE — tum tuketiciler ayni listeden besleniyor", () => {
  // Ham `values` yalnizca `gorunurDegerler` uretilirken okunmali. Baska bir
  // yerde okunursa sayac ile tablo ayrisir ve kullanici eksik veri sanir.
  const hamKullanim = KAYNAK.split("\n")
    .map((satir, i) => ({ satir, no: i + 1 }))
    .filter(({ satir }) => /\bof values\b|\bvalues\.filter\(|\bvalues\.length\b/.test(satir));
  assert.equal(
    hamKullanim.length,
    1,
    `ham 'values' ${hamKullanim.length} yerde okunuyor (yalnizca gorunurDegerler uretimi olmali): ` +
      hamKullanim.map((h) => h.no).join(", ")
  );
  assert.match(hamKullanim[0].satir, /values\.filter/, "tek kullanim filtre olmali");
});

test("toplam sayac da filtrelenmis listeden geliyor", () => {
  assert.match(
    KAYNAK,
    /totalCount = gorunurDegerler\.length/,
    "toplam sayi ham listeden okunuyor — tablodan fazla gosterir"
  );
});
