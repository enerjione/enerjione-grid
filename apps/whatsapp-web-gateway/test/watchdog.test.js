"use strict";

/**
 * Bekci: KILITLENEN surec olmeli, SAGLIKLI surec YASAMALI.
 *
 * Bu testler gercek bir cocuk surec dogurur. Sebep: bekcinin tek isi sureci
 * oldurmek ve bu, birim testiyle taklit edilebilecek bir sey degil —
 * `process.kill(process.pid, "SIGKILL")` cagrisinin GERCEKTEN tum sureci
 * indirdigini yalnizca gercek surec gosterebilir.
 *
 * Ikinci test (`saglikli surec OLDURULMEZ`) en az birincisi kadar onemli:
 * yanlis yere restart uretmek, cozdugumuz sorundan daha kotu bir arizadir.
 */

const test = require("node:test");
const assert = require("node:assert");
const { spawn } = require("node:child_process");
const path = require("node:path");

const BEKCI = path.join(__dirname, "..", "src", "watchdog.js");

/** Verilen govdeyi ayri bir node surecinde kosturur, cikisini bildirir. */
function surecKostur(govde, zamanAsimiMs) {
  return new Promise((resolve) => {
    const cocuk = spawn(process.execPath, ["-e", govde], { stdio: "ignore" });
    let bitti = false;
    const zamanlayici = setTimeout(() => {
      if (bitti) return;
      bitti = true;
      cocuk.kill("SIGKILL");
      resolve({ olduMu: false, signal: null, code: null });
    }, zamanAsimiMs);

    cocuk.on("exit", (code, signal) => {
      if (bitti) return;
      bitti = true;
      clearTimeout(zamanlayici);
      resolve({ olduMu: true, code, signal });
    });
  });
}

test("kilitlenen olay dongusu SIGKILL ile sonlandirilir", async () => {
  // Esik 0.3 sn: kalp 0.1 sn'de bir atmali, 3 kacirilmis atista olmeli.
  const govde = `
    const w = require(${JSON.stringify(BEKCI)});
    w.baslat({ esikSn: 0.3, atisSn: 0.1, servis: "test" });
    // Olay dongusunu TAMAMEN kilitle: setInterval artik hic calismaz,
    // yani kalp durur. Ayri iplikteki izleyici bunu gormeli.
    const bitis = Date.now() + 30000;
    while (Date.now() < bitis) {}
  `;

  const sonuc = await surecKostur(govde, 10000);

  assert.strictEqual(sonuc.olduMu, true, "kilitlenen surec oldurulmedi");
});

test("saglikli surec OLDURULMEZ", async () => {
  // Ayni esik, ama olay dongusu serbest: kalp atmaya devam eder.
  // HIC IS YAPILMIYOR (mesaj yok, istek yok) — atis "is yaptim"a degil
  // "dongum donuyor"a bagli oldugu icin surec yasamali. Bu kural kirilirsa
  // sakin bir gecede saglikli servis oldurulur.
  const govde = `
    const w = require(${JSON.stringify(BEKCI)});
    w.baslat({ esikSn: 0.3, atisSn: 0.1, servis: "test" });
    // Sureci ayakta tutan ref'li timer; olay dongusu bos ama donuyor.
    setTimeout(() => process.exit(7), 2000);
  `;

  const sonuc = await surecKostur(govde, 10000);

  assert.strictEqual(sonuc.olduMu, true, "surec hic bitmedi");
  assert.strictEqual(
    sonuc.code,
    7,
    "saglikli surec kendi cikis kodunu vermeliydi; bekci onu oldurmus olabilir"
  );
});

test("bekci sureci acik tutmaz", async () => {
  // Bekci `unref` edilmemis olsaydi, isi biten bir surec sonsuza kadar
  // ayakta kalirdi (izleyici iplik + timer surekli calisiyor diye).
  const govde = `
    const w = require(${JSON.stringify(BEKCI)});
    w.baslat({ esikSn: 60, atisSn: 5, servis: "test" });
    // Baska is yok: surec HEMEN kapanmali.
  `;

  const sonuc = await surecKostur(govde, 5000);

  assert.strictEqual(sonuc.olduMu, true, "bekci sureci acik tuttu");
  assert.strictEqual(sonuc.code, 0);
});
