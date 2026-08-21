/**
 * DURUM SUZGECI — tek gorsel dil, renk durum sozlesmesinden.
 *
 * ONCEKI HALI IKI AYRI DILDI: Tumu/Cevrimici/Cevrimdisi/Alarmli koyu
 * GRADIENT + beyaz yazi; Smart Bekleme/Gecikmis pastel + koyu yazi. Ayni
 * satirdaki alti dugmeye iki farkli stil vermek "AI uretimi" hissinin asil
 * kaynagiydi.
 *
 * Iki de gercek tutarsizlik vardi:
 *   * "Tumu" INDIGO idi — hicbir duruma karsilik gelmiyor.
 *   * "Cevrimdisi" GRI idi ama haritada/agacta haberlesme kaybi KIRMIZI.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const CSS = readFileSync(join(process.cwd(), "src", "styles.css"), "utf8");
const BAR = readFileSync(
  join(process.cwd(), "src", "features", "dashboard", "DashboardFilterBar.tsx"),
  "utf8"
);

/** Suzgec kurallarinin bulundugu CSS dilimi. */
function dilim(): string {
  const bas = CSS.indexOf(".map-filter-chips {");
  assert.ok(bas > 0);
  const son = CSS.indexOf(".map-filter-divider");
  return CSS.slice(bas, son > bas ? son : bas + 6000);
}

test("cip aktif halinde GRADIENT yok", () => {
  const d = dilim();
  assert.doesNotMatch(
    d,
    /\.map-filter-chip[^{]*\.active[^}]*linear-gradient/,
    "gradient geri gelmis"
  );
});

test("renkli GOLGE yok", () => {
  const d = dilim();
  assert.doesNotMatch(d, /box-shadow:[^;]*rgba\((?:79|16|245)/, "renkli golge duruyor");
});

test("her durum kendi sozlesme rengini tasir", () => {
  const d = dilim();
  const beklenen: Record<string, string> = {
    "map-filter-chip--online": "#16a34a",
    "map-filter-chip--smartidle": "#3b82f6",
    "map-filter-chip--late": "#f97316",
    "map-filter-chip--offline": "#dc2626"
  };
  for (const [sinif, renk] of Object.entries(beklenen)) {
    const i = d.indexOf(`.${sinif}`);
    assert.ok(i > 0, `${sinif} kurali yok`);
    const blok = d.slice(i, d.indexOf("}", i));
    assert.ok(blok.includes(renk), `${sinif} rengi ${renk} degil: ${blok}`);
  }
});

test("CEVRIMDISI kirmizi — agac/harita ile ayni", () => {
  // Gri gradientti; ayni seyi iki yerde iki renkle gostermek okumayi bozar.
  const d = dilim();
  const i = d.indexOf(".map-filter-chip--offline");
  const blok = d.slice(i, d.indexOf("}", i));
  assert.ok(!/#94a3b8|#64748b/.test(blok), "cevrimdisi hala gri");
});

test("TUMU cipinde indigo yok", () => {
  const d = dilim();
  for (const ton of ["#6366f1", "#4f46e5", "#4338ca"]) {
    assert.ok(!d.includes(ton), `${ton} hala kullaniliyor`);
  }
});

test("her DURUM cipinde renk noktasi var, 'Tumu'da YOK", () => {
  // Nokta cipi haritadaki/agactaki ayni renge baglar.
  const nokta = (BAR.match(/map-filter-chip-dot/g) ?? []).length;
  assert.equal(nokta, 5, `beklenen 5 durum noktasi, bulunan ${nokta}`);
  // "Tumu" bir durum degil; noktasi olmamali.
  const tumuBlok = BAR.slice(
    BAR.indexOf('onStatusFilterChange("all")'),
    BAR.indexOf('map-filter-chip--online')
  );
  assert.doesNotMatch(tumuBlok, /map-filter-chip-dot/, "'Tumu' cipine nokta konmus");
});

test("aktiflik yalnizca renge degil DOLGU+KENARLIGA bagli", () => {
  // Renk korlugunde secili cip yine ayirt edilebilmeli.
  const d = dilim();
  const i = d.indexOf(".map-filter-chip.active {");
  assert.ok(i > 0, "aktif kurali yok");
  const blok = d.slice(i, d.indexOf("}", i));
  assert.match(blok, /background:/);
  assert.match(blok, /border-color:/);
  assert.match(blok, /font-weight:/);
});

test("secili 'Tumu' cipi SERIT ZEMININE karismaz", () => {
  // Serit zemini #f1f5f9. Notr cipin aktif dolgusu da ayni ton olsaydi
  // hangi filtrenin acik oldugu gorunmezdi.
  const d = dilim();
  const serit = d.slice(d.indexOf(".map-filter-chips {"), d.indexOf("}"));
  const zemin = /background:\s*(#[0-9a-f]{6})/i.exec(serit)?.[1]?.toLowerCase();
  assert.equal(zemin, "#f1f5f9", "serit zemini degismis, testi guncelle");

  // Not: styles.css CRLF tasiyor; sabit "\n" araminca blok bulunamiyor.
  const notr = /\.map-filter-chip\s*\{\s*--mf:[\s\S]*?\}/.exec(d)?.[0]?.toLowerCase();
  assert.ok(notr, "notr token blogu yok");
  const dolgu = /--mf-soft:\s*(#[0-9a-f]{3,6})/.exec(notr!)?.[1];
  assert.ok(dolgu, "--mf-soft yok");
  assert.notEqual(dolgu, zemin, "aktif 'Tumu' serit zeminiyle ayni tonda");
});
