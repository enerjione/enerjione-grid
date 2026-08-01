/**
 * "Yesil yalan" testleri — sistem BILMEDIGINI "sorun yok" diye gostermesin.
 *
 * Bu iki modul, arayuzun bir olcume "normal" / "canli" demeye hakki olup
 * olmadigina karar verir. Yanlis karar, ariza izleme urununde en agir hata
 * sinifini uretir; bu yuzden ikisi de React'tan bagimsiz tutuldu ve burada
 * GERCEKTEN CALISTIRILARAK dogrulanir (kaynak metni aramasi degil).
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { isTrusted, signalTrust } from "../src/shared/signalQuality";
import {
  DEFAULT_STALE_AFTER_MS,
  sureMetni,
  veriYasi,
  wsDataStatus,
} from "../src/shared/wsDataStatus";

// ---------------------------------------------------------------------------
// signalTrust — binary rozetin "Normal" demeye hakki var mi
// ---------------------------------------------------------------------------

test("deger yoksa 'missing' — 'Normal' degil", () => {
  assert.equal(signalTrust(null, "good"), "missing");
  assert.equal(signalTrust(undefined, "good"), "missing");
});

test("0 gecerli bir olcumdur; 'veri yok' ile karistirilmaz", () => {
  // Regresyonun ta kendisi: `value === 1` kontrolu ikisini ayni yere dusuruyordu.
  assert.equal(signalTrust(0, "good"), "trusted");
  assert.equal(signalTrust(1, "good"), "trusted");
});

test("haberlesme kopukken 0.0 GUVENILMEZ sayilir", () => {
  // Gateway kopan cihaz icin `comm_lost` kaliteli 0.0 basar. Bu, "ariza yok"
  // demek DEGILDIR; sadece "bilmiyoruz" demektir.
  assert.equal(signalTrust(0, "comm_lost"), "untrusted");
  for (const q of ["bad", "offline", "invalid", "restart", "forced"]) {
    assert.equal(signalTrust(0, q), "untrusted", `${q} guvenilmez olmali`);
  }
});

test("kalite buyuk/kucuk harf ve bosluktan bagimsiz", () => {
  assert.equal(signalTrust(0, "  COMM_LOST "), "untrusted");
  assert.equal(signalTrust(0, "Offline"), "untrusted");
});

test("kalite bos/bilinmiyorsa guvenilir sayilir", () => {
  // Eski kayitlarda kalite alani bos olabilir; her seyi "guvenilmez" gostermek
  // de yaniltici olurdu (bu sefer ters yonde).
  assert.equal(signalTrust(0, null), "trusted");
  assert.equal(signalTrust(0, ""), "trusted");
  assert.equal(signalTrust(0, "   "), "trusted");
  assert.equal(signalTrust(0, "good"), "trusted");
});

test("gateway kapaliyken kalite 'good' olsa bile guvenilmez", () => {
  // Son gelen deger bayattir — kalite alani onu taze yapmaz.
  assert.equal(signalTrust(1, "good", false), "untrusted");
  assert.equal(signalTrust(0, "good", false), "untrusted");
});

test("deger yoklugu gateway durumundan ONCE gelir", () => {
  assert.equal(signalTrust(null, "good", false), "missing");
});

test("isTrusted yalnizca 'trusted' icin dogru", () => {
  assert.equal(isTrusted(0, "good"), true);
  assert.equal(isTrusted(null, "good"), false);
  assert.equal(isTrusted(0, "comm_lost"), false);
});

// ---------------------------------------------------------------------------
// wsDataStatus — rozetin "Canli" demeye hakki var mi
// ---------------------------------------------------------------------------

const SIMDI = 1_000_000;

test("soket acik degilken veri yasi ONEMSIZ", () => {
  assert.equal(wsDataStatus("closed", SIMDI, SIMDI), "offline");
  assert.equal(wsDataStatus("connecting", SIMDI, SIMDI), "connecting");
  assert.equal(wsDataStatus("error", SIMDI, SIMDI), "error");
});

test("soket ACIK ama hic veri gelmediyse 'live' DEGIL", () => {
  // Kapatilan hatanin ozu: soket acik olmasi telemetri aktigi anlamina gelmez.
  assert.equal(wsDataStatus("open", null, SIMDI), "waiting");
  assert.equal(wsDataStatus("open", undefined, SIMDI), "waiting");
});

test("taze telemetri -> 'live'", () => {
  assert.equal(wsDataStatus("open", SIMDI - 1_000, SIMDI), "live");
  assert.equal(wsDataStatus("open", SIMDI, SIMDI), "live");
});

test("esik gecilince 'stale' — soket hala ACIK olsa bile", () => {
  // Sunucu 30sn'de bir ping atar, dolayisiyla gateway tamamen sussa da soket
  // "open" kalir. Rozet bu durumda yesil kalmamali.
  assert.equal(wsDataStatus("open", SIMDI - 5 * 60_000, SIMDI), "stale");
});

test("esik SINIRI: tam esikte hala 'live', bir ms sonrasi 'stale'", () => {
  const t0 = SIMDI - DEFAULT_STALE_AFTER_MS;
  assert.equal(wsDataStatus("open", t0, SIMDI), "live");
  assert.equal(wsDataStatus("open", t0 - 1, SIMDI), "stale");
});

test("esik disaridan yukseltilebilir (tarama araligi buyuk sahalar)", () => {
  const yas = 60_000;
  assert.equal(wsDataStatus("open", SIMDI - yas, SIMDI), "stale");
  assert.equal(wsDataStatus("open", SIMDI - yas, SIMDI, 120_000), "live");
});

test("veriYasi negatife dusmez (istemci saati ileri kayabilir)", () => {
  assert.equal(veriYasi(SIMDI + 5_000, SIMDI), 0);
  assert.equal(veriYasi(SIMDI - 5_000, SIMDI), 5_000);
  assert.equal(veriYasi(null, SIMDI), null);
});

test("sureMetni birim secimi", () => {
  assert.equal(sureMetni(0), "0 sn");
  assert.equal(sureMetni(45_000), "45 sn");
  assert.equal(sureMetni(59_000), "59 sn");
  assert.equal(sureMetni(180_000), "3 dk");
  assert.equal(sureMetni(3 * 3_600_000), "3 sa");
});
