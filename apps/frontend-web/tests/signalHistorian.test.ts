/**
 * Arsiv (historian) arayuz kurallari — "olmayan bir ayari varmis gibi gosterme".
 *
 * Sinyaller sayfasi artik hangi sinyalin arsive yazildigini ve olu bandini
 * gosteriyor/duzenletiyor. Iki karar SAF fonksiyonlarda toplandi, cunku
 * ikisi de YANLIS olursa hata SESSIZDIR:
 *
 *   * `effectiveDeadband` yanlissa, arayuz `binary` bir ariza sinyalinde
 *     "±2" gosterir. Motor (`historian_policy`) o esigi UYGULAMAZ; kullanici
 *     yazma yukunu kistigini sanir, hicbir sey degismez.
 *   * `isHistorized` yanlissa, alani hic gondermeyen eski bir backend'de tum
 *     sinyaller "arsivlenmiyor" gorunur ve kullanici olmayan bir sorunu
 *     "duzeltmeye" calisir.
 *
 * Backend ile ayni olduklari ayrica sunucu tarafinda dogrulaniyor
 * (`test_signal_historian_yonetimi.py::test_frontend_aynasi_BACKEND_ILE_AYNI`).
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { SignalDataType } from "../src/shared/types";
import {
  DATA_TYPES,
  deadbandApplies,
  effectiveDeadband,
  isFaultSignal,
  isHistorized,
} from "../src/features/signals/signalCatalogConstants";

// ---------------------------------------------------------------------------
// isHistorized — alan YOKSA "acik" sayilir
// ---------------------------------------------------------------------------

test("arsiv bayragi yoksa ACIK sayilir (eski backend alani gondermiyor)", () => {
  // `undefined`i "kapali" saymak, guncellemeden once tum katalogu
  // "arsivlenmiyor" gosterirdi — var olmayan bir alarm.
  assert.equal(isHistorized({}), true);
  assert.equal(isHistorized({ historize: undefined }), true);
});

test("arsiv bayragi ACIKCA false ise kapali", () => {
  assert.equal(isHistorized({ historize: false }), false);
  assert.equal(isHistorized({ historize: true }), true);
});

// ---------------------------------------------------------------------------
// deadbandApplies — YALNIZCA analog
// ---------------------------------------------------------------------------

test("olu bant yalnizca analog tipte uygulanir", () => {
  assert.equal(deadbandApplies("analog"), true);
  for (const tip of ["binary", "binary_output", "counter", "string"] as SignalDataType[]) {
    assert.equal(deadbandApplies(tip), false, `${tip} icin olu bant acilmis`);
  }
});

test("analog_output da olu bant ALMAZ", () => {
  // Motorun listesi tam olarak {"analog"}; buraya bir tip daha eklemek
  // arayuzu motordan ayirir.
  assert.equal(deadbandApplies("analog_output"), false);
});

// ---------------------------------------------------------------------------
// effectiveDeadband — GORUNEN deger ile UYGULANAN deger ayni olmali
// ---------------------------------------------------------------------------

test("analog olmayan sinyalde esik tasinsa bile ETKISIZ gosterilir", () => {
  // Sinsi senaryo: sinyal once analogdu, esik verildi, sonra tipi binary
  // yapildi. Kolon degeri DB'de duruyor ama motor onu yok sayiyor.
  assert.equal(
    effectiveDeadband({ data_type: "binary", historize_deadband: 2 }),
    0,
    "ariza sinyalinde olu bant varmis gibi gosteriliyor",
  );
  assert.equal(effectiveDeadband({ data_type: "string", historize_deadband: 5 }), 0);
});

test("analogda gercek esik dondurulur", () => {
  assert.equal(effectiveDeadband({ data_type: "analog", historize_deadband: 0.5 }), 0.5);
});

test("esik yoksa / sifirsa / bozuksa 0", () => {
  assert.equal(effectiveDeadband({ data_type: "analog" }), 0);
  assert.equal(effectiveDeadband({ data_type: "analog", historize_deadband: 0 }), 0);
  // Negatif ya da NaN bir deger "acik esik" gibi gorunmemeli.
  assert.equal(effectiveDeadband({ data_type: "analog", historize_deadband: -1 }), 0);
  assert.equal(effectiveDeadband({ data_type: "analog", historize_deadband: NaN }), 0);
});

// ---------------------------------------------------------------------------
// isFaultSignal — onay penceresini tetikleyen kume
// ---------------------------------------------------------------------------

test("ariza gecisi tasiyan tipler: binary ve binary_output", () => {
  assert.equal(isFaultSignal("binary"), true);
  assert.equal(isFaultSignal("binary_output"), true);
  for (const tip of ["analog", "counter", "string", "analog_output"] as SignalDataType[]) {
    assert.equal(isFaultSignal(tip), false, `${tip} gereksiz yere onay istiyor`);
  }
});

test("her veri tipi bir karara baglanmis (yeni tip sessizce dusmesin)", () => {
  // Yeni bir `data_type` eklendiginde iki listeden hangisine girdigi
  // BILINCLI bir karar olmali; burada gorulur.
  for (const tip of DATA_TYPES) {
    assert.equal(
      typeof isFaultSignal(tip),
      "boolean",
      `${tip} icin ariza karari yok`,
    );
    assert.equal(typeof deadbandApplies(tip), "boolean");
  }
});

test("ariza tipleri ile olu bant tipleri KESISMEZ", () => {
  // Kesisselerdi, bir ariza sinyaline esik verilebilirdi ve 0 -> 1 gecisi
  // yutulurdu. Bu, urunun temel vaadini bozar.
  for (const tip of DATA_TYPES) {
    assert.ok(
      !(isFaultSignal(tip) && deadbandApplies(tip)),
      `${tip} hem ariza tasiyor hem olu bant aliyor`,
    );
  }
});
