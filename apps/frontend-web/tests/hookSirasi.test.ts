/**
 * HOOK SIRASI — kosullu `return`den SONRA hook cagrisi olmamali.
 *
 * YASANAN ARIZA (2026-08-12)
 * --------------------------
 * `FaultDetailPage` icinde "ariza bulunamadi" dali vardi:
 *
 *     if (!fault) { return <placeholder/>; }
 *     ...
 *     const tahminiMesafe = useMemo(...);   // <-- return'un ALTINDA
 *
 * Kayit listede yokken (sekme yenilenmis, kapanmis ariza, kayit silinmis)
 * ILK render erken donuyor ve o hook HIC calismiyor; fetch gelince kayit
 * doluyor ve hook calisiyor. React icin hook SAYISI degismis oluyor:
 *
 *     "Rendered more hooks than during the previous render."
 *
 * Hata RENDER sirasinda firladigi icin ErrorBoundary'ye kadar cikti ve TUM
 * arayuz kitlendi — operator hicbir sekmeyi kullanamadi, tek care sayfayi
 * yenilemekti.
 *
 * TESTIN KILITLEDIGI SEY
 * ----------------------
 * Bilesen govdesinde (girinti 2) bir kosullu `return`den sonra hook
 * cagrisi bulunmamali. Bu kural React'in kendi kurali; ESLint
 * `react-hooks/rules-of-hooks` ayni seyi yakalar ama bu depoda ESLint
 * kurulu degil — kural bu testle korunuyor.
 *
 * KAPSAM: `src/features/**\/*.tsx` + `src/app/App.tsx`. Girinti tabanli
 * sezgisel bir tarama; ic fonksiyonlardaki (daha derin girintili)
 * return/hook ciftlerine BAKMAZ, cunku onlar ayri birer bilesendir.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

// KOK: `process.cwd()` uzerinden. `import.meta.url` KULLANILAMAZ — kosucu
// (tests/run.mjs) testleri esbuild ile GECICI bir dizine derleyip oradan
// calistiriyor, dolayisiyla modulun kendi yolu depoyu isaret etmiyor.
// `npm test` her zaman apps/frontend-web icinden kosar.
const SRC = join(process.cwd(), "src");

function tsxDosyalari(dizin: string, cikti: string[] = []): string[] {
  for (const ad of readdirSync(dizin)) {
    const yol = join(dizin, ad);
    if (statSync(yol).isDirectory()) {
      tsxDosyalari(yol, cikti);
    } else if (ad.endsWith(".tsx")) {
      cikti.push(yol);
    }
  }
  return cikti;
}

/** Bilesen govdesi = girinti 2. Daha derini ic fonksiyon/callback. */
const KOSULLU_RETURN = /^ {2}if \(.*\) \{\s*$/;
const GOVDE_RETURN = /^ {4}return[ (]/;
const GOVDE_HOOK = /^ {2}(?:const|let) .*\buse[A-Z]\w*\s*\(/;

test("kosullu return'den SONRA hook cagrilmiyor (React hook sirasi)", () => {
  const dosyalar = tsxDosyalari(join(SRC, "features")).concat(join(SRC, "app", "App.tsx"));
  const ihlaller: string[] = [];

  for (const dosya of dosyalar) {
    const satirlar = readFileSync(dosya, "utf8").split(/\r?\n/);
    let erkenReturnSatiri: number | null = null;

    for (let i = 0; i < satirlar.length; i += 1) {
      const satir = satirlar[i];
      // UST DUZEY SINIR: sutun 0'daki `}` bir fonksiyonun sonudur. Isareti
      // burada sifirlamak SART — aksi halde modul duzeyindeki bir yardimci
      // fonksiyonun (`function groupOfSuffix() { if (...) return ... }`)
      // return'u, dosyanin ilerisindeki BILESENIN hook'larini "ihlal" diye
      // isaretliyordu. Ilk surumde 17 sahte bulgu boyle cikti.
      if (/^\}/.test(satir)) {
        erkenReturnSatiri = null;
        continue;
      }
      // Kosullu return: `  if (...) {` ve hemen ardindan govde return'u.
      if (erkenReturnSatiri === null && KOSULLU_RETURN.test(satir)) {
        for (let j = i + 1; j < Math.min(i + 4, satirlar.length); j += 1) {
          if (GOVDE_RETURN.test(satirlar[j])) {
            erkenReturnSatiri = i + 1;
            break;
          }
          if (satirlar[j].trim() !== "") break;
        }
        continue;
      }
      if (erkenReturnSatiri !== null && GOVDE_HOOK.test(satir)) {
        const kisa = dosya.slice(dosya.indexOf("src"));
        ihlaller.push(
          `${kisa}:${i + 1} — ${satir.trim().slice(0, 60)} (erken return: satir ${erkenReturnSatiri})`
        );
      }
    }
  }

  assert.deepEqual(
    ihlaller,
    [],
    "Kosullu return'den sonra hook var; kayit gec geldiginde React " +
      '"Rendered more hooks than during the previous render" firlatir ve ' +
      "ekran kitlenir:\n  " + ihlaller.join("\n  ")
  );
});
