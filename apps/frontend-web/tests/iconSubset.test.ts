/**
 * IKON SUBSET BUTUNLUGU — "ikon yerine ikon ADI cikmasin".
 *
 * NEDEN VAR
 * ---------
 * Material Symbols fontu subset'lenerek gonderiliyor (3.8 MB -> ~165 kB,
 * bkz. `scripts/subset-icons.py`). Subset yalnizca KODDA GECEN ikonlari
 * icerir; kodda yeni bir ikon kullanilip font yeniden uretilmezse ikon
 * SESSIZCE bozulur: ligature cozulemedigi icin tarayici ikon adini DUZ METIN
 * olarak basar.
 *
 * Bu sahaya bir kez oyle cikti: cihaz detay sekmesinde `solar_power` ikonu
 * yoktu ve baslikta "SOLAR_" + guc simgesi goruntulendi (font "solar_" i
 * metin, "power" i glyph olarak cozdu). Ne derleme ne de tip kontrolu boyle
 * bir seyi yakalar — bu test yakalar.
 *
 * NASIL
 * -----
 * `subset-icons.py` fonta giren ikon adlarini bir manifest'e yazar. Test
 * kaynak kodda GUVENILIR bicimde ikon adi oldugu belli olan yerleri tarar
 * ve her birinin manifest'te oldugunu dogrular. Dusen bir ad varsa cozum
 * bellidir: `python scripts/subset-icons.py` calistirip fontu tazele.
 *
 * Tarama KASITLI OLARAK DAR: yalnizca ikon adi olduguna emin oldugumuz iki
 * bicim okunur. Genis tarama (her kucuk-harf string'i) Turkce metinleri ve
 * CSS anahtar kelimelerini ikon sanip testi guvenilmez kilardi. Fontu ureten
 * script ise TERSINE genis tarar; yani font her zaman bu testin bekledigi
 * kumenin ustunde kalir.
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const KOK = process.cwd();
const MANIFEST = `${KOK}/src/assets/fonts/material-symbols-subset.icons.json`;

function kaynakDosyalari(dizin: string, biriktir: string[] = []): string[] {
  for (const ad of readdirSync(dizin)) {
    const yol = join(dizin, ad);
    if (statSync(yol).isDirectory()) {
      kaynakDosyalari(yol, biriktir);
    } else if (/\.(ts|tsx)$/.test(ad)) {
      biriktir.push(yol);
    }
  }
  return biriktir;
}

/** Kodda ikon adi olduguna EMIN olunan yerler: `<span class=...>ad</span>`
 *  ve `icon: "ad"` / `icon="ad"` / `icon={"ad"}`. */
function kullanilanIkonlar(): Map<string, string[]> {
  const bulunan = new Map<string, string[]>();
  const ekle = (ad: string, dosya: string) => {
    const liste = bulunan.get(ad);
    if (liste) {
      if (!liste.includes(dosya)) liste.push(dosya);
    } else {
      bulunan.set(ad, [dosya]);
    }
  };

  for (const dosya of kaynakDosyalari(`${KOK}/src`)) {
    const metin = readFileSync(dosya, "utf8");
    const kisaAd = dosya.slice(KOK.length + 1).replace(/\\/g, "/");

    // 1) <span className="... material-symbols-outlined ...">ad</span>
    //    Ifade (`{...}`) iceren cocuklar atlanir — literal degildir.
    const spanRe = /material-symbols-outlined[^>]*>\s*([^<>{}]+?)\s*</g;
    for (const m of metin.matchAll(spanRe)) {
      const ad = m[1].trim();
      if (/^[a-z][a-z0-9_]*$/.test(ad)) ekle(ad, kisaAd);
    }

    // 2) icon: "ad" | icon="ad" | icon={"ad"}
    const propRe = /\bicon\s*[:=]\s*\{?\s*"([a-z][a-z0-9_]*)"/g;
    for (const m of metin.matchAll(propRe)) ekle(m[1], kisaAd);
  }
  return bulunan;
}

test("kodda kullanilan her ikon subset fontunda olmali", () => {
  const manifest: string[] = JSON.parse(readFileSync(MANIFEST, "utf8"));
  const fontta = new Set(manifest);
  assert.ok(fontta.size > 100, "manifest bos/bozuk gorunuyor");

  const kullanilan = kullanilanIkonlar();
  assert.ok(kullanilan.size > 50, "ikon taramasi bozulmus olabilir (cok az sonuc)");

  const eksik = [...kullanilan.entries()]
    .filter(([ad]) => !fontta.has(ad))
    .map(([ad, dosyalar]) => `${ad} (${dosyalar.slice(0, 2).join(", ")})`);

  assert.deepEqual(
    eksik,
    [],
    `Su ikonlar subset fontunda YOK — ekranda ikon yerine ADI cikar.\n` +
      `Cozum: cd apps/frontend-web && python scripts/subset-icons.py\n` +
      eksik.join("\n")
  );
});

test("manifest ile font ayni anda guncelleniyor (ikon sayisi makul)", () => {
  const manifest: string[] = JSON.parse(readFileSync(MANIFEST, "utf8"));
  // Subset'in kendisi de bir dosya olarak duruyor mu — manifest guncellenip
  // font uretilmeden commit edilirse ikonlar yine bozuk cikardi.
  const font = statSync(`${KOK}/src/assets/fonts/material-symbols-outlined-subset.woff2`);
  assert.ok(font.size > 20_000, "subset font suphesiz kucuk");
  assert.ok(
    manifest.length > 150 && manifest.length < 1000,
    `manifest ikon sayisi beklenmedik: ${manifest.length}`
  );
});
