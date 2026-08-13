/**
 * CIFT-KODLANMIS METIN (mojibake) KAYNAKTA KALMASIN.
 *
 * `ActiveFaultCard.tsx` bir noktada UTF-8 baytlari Latin-1 sanilip yeniden
 * UTF-8'e yazilarak bozuldu. Dosyada 31 yerde bozuk dizi vardi ve ikisi
 * DOGRUDAN EKRANDAYDI:
 *
 *   `${...toFixed(0)} Â°C`  -> ariza kunyesinde "25 Â°C"
 *   <span ...>Â·</span>     -> alarm satirinda "DEMO-5 Â· 14:10"
 *
 * Geri kalani yorumlardaydi ama ayni dosyadaki em-dash donusleri (bos alan
 * gosterimi) de ekrana dusuyordu.
 *
 * NEDEN SESSIZ: derleyici sikayet etmez, tip hatasi vermez, hicbir test
 * kirilmaz. Yalnizca ekranda garip karakter cikar ve "bu bir kodlama hatasi"
 * denene kadar uzun sure oyle kalir. Bu yuzden kontrol otomatik.
 *
 * KURAL — UTF-8'in BAYT desenini arar, "iki aksanli harf yan yana"yi degil.
 * Cok baytli bir UTF-8 karakterinin ilk bayti 0xC2-0xDF ya da 0xE0-0xEF,
 * DEVAM baytlari ise 0x80-0xBF araligindadir. Bu baytlar tek tek karaktere
 * cevrilince ilk bayt U+00C2-U+00DF / U+00E0-U+00EF, devam bayti ise
 * U+0080-U+00BF (ya da cp1252'nin o bolgedeki noktalamasi: € ' " – — ...)
 * olarak gorunur.
 *
 * Daraltma SART: "Kucuk", "cozum", "olcu" gibi DOGRU Turkce sozcuklerde iki
 * aksanli harf yan yana gelir (c+o, u+c). Onlarda ikinci harf devam bayti
 * araliginda DEGILDIR (o=U+00F6, u=U+00FC), o yuzden eslesmezler.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

// Kosucu testleri gecici bir dizine PAKETLIYOR, o yuzden `import.meta.url`
// depoyu gostermez; mevcut styles.css testi de bu yuzden `process.cwd()`
// kullaniyor (bkz. index.test.ts).
const KOK = join(process.cwd(), "src");
const UZANTILAR = [".ts", ".tsx", ".css", ".json"];

/** Cok baytli UTF-8 karakterinin ILK baytinin tek tek okunmus hali. */
const ILK_BAYT = "\\u00C2-\\u00DF\\u00E0-\\u00EF";
/** DEVAM baytinin (0x80-0xBF) tek tek okunmus hali. 0xA0-0xBF kendisidir;
 *  0x80-0x9F ise cp1252'de noktalamaya duser. */
const DEVAM_BAYTI =
  "\\u00A0-\\u00BF" +
  "\\u20AC\\u201A\\u0192\\u201E\\u2026\\u2020\\u2021\\u02C6\\u2030" +
  "\\u0160\\u2039\\u0152\\u017D\\u2018\\u2019\\u201C\\u201D\\u2022" +
  "\\u2013\\u2014\\u02DC\\u2122\\u0161\\u203A\\u0153\\u017E\\u0178";

const MOJIBAKE = new RegExp(`[${ILK_BAYT}][${DEVAM_BAYTI}]`);

function dosyalar(dizin: string): string[] {
  const cikti: string[] = [];
  for (const ad of readdirSync(dizin)) {
    const yol = join(dizin, ad);
    if (statSync(yol).isDirectory()) {
      cikti.push(...dosyalar(yol));
    } else if (UZANTILAR.some((u) => ad.endsWith(u))) {
      cikti.push(yol);
    }
  }
  return cikti;
}

const kisalt = (yol: string) => yol.slice(KOK.length + 1).replace(/\\/g, "/");

test("kaynakta cift-kodlanmis metin yok", () => {
  const bulgular: string[] = [];
  for (const yol of dosyalar(KOK)) {
    readFileSync(yol, "utf8")
      .split("\n")
      .forEach((satir, i) => {
        const m = satir.match(MOJIBAKE);
        if (m) {
          bulgular.push(
            `${kisalt(yol)}:${i + 1}  ${JSON.stringify(m[0])}  ${satir.trim().slice(0, 60)}`,
          );
        }
      });
  }
  assert.deepEqual(
    bulgular,
    [],
    "Cift-kodlanmis (mojibake) metin bulundu. Bozuk diziyi elle degistirmek " +
      "yerine dosyayi UTF-8 olarak yeniden kaydedin:\n" + bulgular.join("\n"),
  );
});

// BASTAKI BOM ICIN TEST YOK — BILINCLI.
//
// Bes dosya (App.tsx, api.ts, types.ts, OutboundTargetsPanel.tsx,
// SystemStatusPage.tsx) U+FEFF ile basliyor. Bu ZARARSIZ: derleyici de
// bundler da bastaki BOM'u atar, urunde bir karsiligi yok. Tehlikeli olan
// SATIR ORTASINDAKI U+FEFF'ti — `styles.css`te bir kurali sessizce
// oldurmustu — ve onu index.test.ts zaten kontrol ediyor.
//
// Bu dosyalar ayni zamanda paralel oturumlarin en cok dokundugu yerler;
// kozmetik bir temizlik icin hepsini degistirmek merge catismasi uretirdi.
// Bir gun elden gecirilirse buraya bir test eklenebilir.
