/**
 * Frontend test kosucusu — bagimlilik EKLEMEDEN.
 *
 * Denetim notu: "Frontend: sifir test". Bir test cercevesi (vitest + jsdom +
 * testing-library) kurmak ayri bir is ve kurulum boyutunu buyutur; buna
 * karsilik projedeki en riskli mantik SAF fonksiyonlarda toplandi
 * (`shared/wsDataStatus.ts`, `shared/signalQuality.ts`) ve bunlar React
 * olmadan calistirilabilir.
 *
 * Burada esbuild (zaten vite ile geliyor) ile testler tek bir ESM dosyasina
 * derlenir ve Node'un yerlesik `node:test` kosucusuyla calistirilir. Yeni
 * bagimlilik yok.
 *
 *   npm test
 */

import { build } from "esbuild";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const buradan = (p) => fileURLToPath(new URL(p, import.meta.url));

const gecici = mkdtempSync(join(tmpdir(), "e1-fe-test-"));
const cikti = join(gecici, "tests.mjs");

try {
  await build({
    entryPoints: [buradan("index.test.ts")],
    outfile: cikti,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node20",
    // node: yerlesikleri bundle'a girmesin.
    // `esbuild` de disarida kalmali: kendi icinde `require("fs")` yapiyor ve
    // bundle'a gomulunce ESM ciktisinda "Dynamic require of fs is not
    // supported" ile duser. Testler onu CSS'i derleyip DAVRANISI sinamak icin
    // kullaniyor (bkz. govde yazi tipi testi).
    external: ["node:*", "esbuild"],
    logLevel: "warning",
  });

  // Bundle GECICI dizine yaziliyor; oradan `import("esbuild")` cozulemez
  // (ERR_MODULE_NOT_FOUND). Projenin kendi esbuild'inin MUTLAK yolunu ortam
  // degiskeniyle geciyoruz — CSS'i derleyip davranisi sinayan test onu
  // kullaniyor.
  const esbuildUrl = pathToFileURL(
    join(process.cwd(), "node_modules", "esbuild", "lib", "main.js"),
  ).href;
  const sonuc = spawnSync(process.execPath, ["--test", cikti], {
    stdio: "inherit",
    env: { ...process.env, E1_ESBUILD: esbuildUrl },
  });
  process.exit(sonuc.status ?? 1);
} finally {
  rmSync(gecici, { recursive: true, force: true });
}
