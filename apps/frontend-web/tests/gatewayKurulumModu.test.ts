/**
 * "BU CIHAZA KUR" BASARISIZ OLUNCA INDIRILEN DOSYA YEREL OLMALI.
 *
 * YASANAN SORUN
 * -------------
 * Sihirbazin "bu cihaza kur" adimi host ajanina (e1-gwd) gider ve ajan
 * compose'u KENDI sablonundan `INSTALL_MODE=local` ile uretir. Ajan
 * basarisiz olursa kullaniciya "elle kurulum adimlarini goster" dugmesi
 * cikar ve akis `remote` adimina gecer: kullanici AYNI MAKINEYE kuracagi
 * dosyayi `GET /gateways/{kod}/docker-compose` ucundan indirir.
 *
 * O uc uzun sure INSTALL_MODE'u SABIT `remote` uretiyordu. Sonuc: yerel bir
 * kurulum, gateway sozlesmesinin yerel mod icin YASAKLADIGI sessiz HTTP
 * yedegini kazaniyordu -- ayni makinede NATS'a erisilememesi bir
 * YAPILANDIRMA HATASIDIR ve gorunur kalmalidir.
 *
 * NEDEN SESSIZ: derleyici sikayet etmez, backend 200 doner, dosya calisir.
 * Fark yalnizca NATS koptugu anda ortaya cikar ve o an telemetri sessizce
 * HTTP'ye kayar. Bu yuzden kontrol otomatik ve KAYNAK UZERINDEN yapilir
 * (React test cercevesi eklemeden -- bkz. tests/run.mjs).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const oku = (...p: string[]) => readFileSync(join(process.cwd(), "src", ...p), "utf8");

const MODAL = oku("features", "gateways", "GatewayCreateModal.tsx");
const API = oku("shared", "api.ts");

test("elle kuruluma dusuren dugme kurulum modunu local'e cevirir", () => {
  // Dugmenin onClick govdesi: fallback isaretlenir VE mod local yapilir.
  const govde = /fallbackToManual[\s\S]{0,400}?</.exec(MODAL)?.[0] ?? "";
  const onClick = /onClick=\{\(\) => \{([\s\S]*?)\}\}/.exec(
    MODAL.slice(Math.max(0, MODAL.indexOf("fallbackToManual") - 900)),
  )?.[1] ?? "";
  assert.ok(
    onClick.includes('setInstallMode("local")'),
    "fallbackToManual dugmesi setInstallMode(\"local\") cagirmiyor: elle " +
      "kuruluma dusen kullanici AYNI makineye remote compose indirir",
  );
  assert.ok(
    onClick.includes("setCameFromLocal(true)"),
    "fallback yolu isaretlenmiyor; secim kutusu gosterilemez",
  );
  assert.ok(govde.length > 0, "fallbackToManual dugmesi bulunamadi");
});

test("compose indirme cagrisi kurulum modunu gonderir", () => {
  const cagri = /downloadGatewayCompose\([\s\S]*?\);/.exec(MODAL)?.[0] ?? "";
  assert.ok(cagri, "downloadGatewayCompose cagrisi bulunamadi");
  assert.ok(
    /\binstallMode\b/.test(cagri),
    "indirme cagrisi installMode tasimiyor: backend varsayilani (remote) " +
      "kullanilir ve yerel kurulum yanlis modda uretilir",
  );
});

test("api istemcisi installMode'u install_mode query parametresine cevirir", () => {
  assert.ok(
    /params\.set\("install_mode", *opts\.installMode\)/.test(API),
    "installMode -> install_mode esleme yok; parametre backend'e hic gitmez",
  );
  assert.ok(
    /installMode\?: *"local" \| "remote"/.test(API),
    "installMode tipi daraltilmamis; serbest string backend'de 422 uretir",
  );
});

test("kurulum modu secimi yalnizca fallback yolunda gosterilir", () => {
  // Bastan "baska cihaza kur" secen kullanicida belirsizlik yok; ona
  // gereksiz soru sormak akisi uzatir.
  assert.ok(
    /\{cameFromLocal \? \(/.test(MODAL),
    "secim kutusu cameFromLocal ile kosullanmamis",
  );
});
