/**
 * Modem/sebeke metninin cozumu.
 *
 * Sol paneldeki "Sebeke Sinyali" satiri bos kaliyordu: sayisal bir
 * `master.modem_rssi` araniyordu, cihaz oyle bir nokta yayinlamiyor. Gercek
 * deger modemin ham yanitini tasiyan STRING sinyalin icinde.
 *
 * Buradaki testler cozumun KONUMA degil BICIME dayandigini kilitler: modem
 * degisip alan sirasi kaydiginda sessizce yanlis bir sayi gostermek, bos
 * gostermekten daha kotudur.
 */
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { modemDurumuCoz, sinyalKalitesi } from "../src/features/device-detail/modemStatus";

const GERCEK =
  'NWS: "286 01",1651,-91,-57,-12,2120,,128,19,1,00E1A0C,"286016681396681","Turkcell",3,3,131';

test("gercek cihaz yanitindan seviye ve operator cikar", () => {
  const d = modemDurumuCoz(GERCEK);
  assert.equal(d.dbm, -91);
  assert.equal(d.operator, "Turkcell");
});

test("MCC/MNC ve IMSI operator SANILMAZ", () => {
  // "286 01" ve uzun sayi da tirnak icinde gelir; operator adi RAKAM DEGIL.
  const d = modemDurumuCoz('NWS: "286 01",10,-77,"286016681396681","Vodafone TR"');
  assert.equal(d.operator, "Vodafone TR");
});

test("onek olmadan da calisir", () => {
  assert.equal(modemDurumuCoz('"286 02",5,-83,"Turk Telekom"').dbm, -83);
});

test("ARALIK DISI negatif deger seviye sayilmaz", () => {
  // -12 EC-IO olabilir ama -5 bir seviye degil; -300 hic degil. Uydurma bir
  // cubuk cizmektense "bilmiyorum" demek dogru.
  assert.equal(modemDurumuCoz("NWS: 1651,-5,-300").dbm, undefined);
});

test("bos / bozuk girdi COKMEZ", () => {
  assert.deepEqual(modemDurumuCoz(undefined), {});
  assert.deepEqual(modemDurumuCoz(""), {});
  assert.deepEqual(modemDurumuCoz("   "), {});
  assert.equal(modemDurumuCoz("[not configured]").dbm, undefined);
});

test("kalite esikleri: cozulemeyen deger CUBUK CIZMEZ", () => {
  assert.deepEqual(sinyalKalitesi(undefined), { key: "none", bars: 0 });
  assert.deepEqual(sinyalKalitesi(-60), { key: "good", bars: 4 });
  assert.deepEqual(sinyalKalitesi(-80), { key: "fair", bars: 3 });
  assert.deepEqual(sinyalKalitesi(-91), { key: "poor", bars: 2 });
  assert.deepEqual(sinyalKalitesi(-120), { key: "poor", bars: 1 });
});
