/**
 * Calisma modu (Akilli / Boost) — sol paneldeki satirin kurali.
 *
 * ASIL KORUNAN SEY: "0" degerinin iki anlami var. Cihaz gercekten Boost
 * modda olabilir, YA DA haberlesme kopmustur ve gateway `comm_lost`
 * kalitesiyle 0.0 basiyordur. Ikisini ayirmayan bir okuma, akilli modda
 * calisan bir cihaz icin ekranda "Boost Mod" yazar — ve iki mod da gecerli
 * bir durum oldugu icin kimse bu yanlisi fark etmez.
 *
 * Bu dosya fonksiyonu GERCEKTEN CALISTIRIR; ayrica sol panelin satiri
 * `undefined` durumunda hic cizmedigini kaynak uzerinden dogrular
 * (React test cercevesi eklemeden — bkz. tests/run.mjs).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { operationModeOf } from "../src/features/device-detail/operationMode";

test("1 -> Akilli, 0 -> Boost (kalite iyi)", () => {
  assert.equal(operationModeOf(1, "good", true), "smart");
  assert.equal(operationModeOf(0, "good", true), "boost");
});

test("kalite bayragi yoksa da okunur — cogu gateway bos gonderiyor", () => {
  assert.equal(operationModeOf(1, null, true), "smart");
  assert.equal(operationModeOf(0, "", true), "boost");
});

test("deger YOKSA mod da yok", () => {
  assert.equal(operationModeOf(null, "good", true), undefined);
  assert.equal(operationModeOf(undefined, "good", true), undefined);
});

test("comm_lost 0.0 'Boost' DEGILDIR — asil korunan hata", () => {
  // Cihaz akilli modda olabilir; bunu bilmiyoruz. "Boost" demek uydurmaktir.
  assert.equal(operationModeOf(0, "comm_lost", true), undefined);
  assert.equal(operationModeOf(1, "comm_lost", true), undefined);
});

test("gateway kopukken hicbir deger taze degil", () => {
  assert.equal(operationModeOf(1, "good", false), undefined);
  assert.equal(operationModeOf(0, "good", false), undefined);
});

test("sol panel: mod bilinmiyorsa satir HIC cizilmiyor", () => {
  const kaynak = readFileSync(
    join(process.cwd(), "src/features/device-detail/DeviceSidebar.tsx"),
    "utf8"
  );
  // Kosullu render: `operationMode ? <InfoRow .../> : null`
  assert.match(
    kaynak,
    /\{operationMode \?\s*\(/,
    "operationMode satiri kosulsuz ciziliyor — bilinmeyen mod ekrana dusebilir"
  );
  assert.match(kaynak, /deviceDetail\.sidebar\.operationMode/);
});

test("sol panel: mod metni i18n'den geliyor, gomulu degil", () => {
  const kaynak = readFileSync(
    join(process.cwd(), "src/features/device-detail/DeviceSidebar.tsx"),
    "utf8"
  );
  assert.doesNotMatch(kaynak, /"Boost Mod"|"Akıllı Mod"|"Smart Mode"/);

  for (const dosya of ["tr", "en"]) {
    const sozluk = JSON.parse(
      readFileSync(join(process.cwd(), `src/shared/i18n/resources/${dosya}.json`), "utf8")
    );
    const sb = sozluk.deviceDetail.sidebar;
    for (const anahtar of ["operationMode", "operationMode_smart", "operationMode_boost"]) {
      assert.ok(sb[anahtar], `${dosya}.json icinde deviceDetail.sidebar.${anahtar} yok`);
    }
  }
});
