/**
 * B5 — akilli oturum (smart session) arayuz sozlesmesi.
 *
 * NEDEN BURADA KILITLENIYOR
 * -------------------------
 * `smart` YALNIZCA `initiating` uc nokta tipiyle gecerlidir: uykudaki cihaza
 * gateway BAGLANAMAZ, baglantiyi cihaz kurar. Backend bu kombinasyonu 422 ile
 * reddediyor — ama arayuzun gorevi onu HIC URETMEMEK. Aksi halde operator
 * formda "Akilli" secip kaydete basar ve anlamadigi bir hata alir.
 *
 * Ikinci ve daha sinsi risk: formda gorunen deger ile govdeye giren degerin
 * ayrismasi. Ikisi de AYNI saf fonksiyonu (`sessionPolicyForEndpoint`)
 * kullaniyor; bu dosya once o fonksiyonu GERCEKTEN CALISTIRARAK, sonra iki
 * cagri yerinin de onu kullandigini kaynak uzerinden dogruluyor (React test
 * cercevesi eklemeden — bkz. tests/run.mjs).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  DEFAULT_DNP3_EXTENDED,
  SMART_MAX_SILENCE_MAX_SEC,
  SMART_MAX_SILENCE_MIN_SEC,
  mergeDnp3Extended,
  sessionPolicyForEndpoint
} from "../src/shared/types";

const oku = (...p: string[]) => readFileSync(join(process.cwd(), "src", ...p), "utf8");

const FORM = oku("features", "devices", "Dnp3SettingsForm.tsx");
const PANEL = oku("features", "devices", "DeviceManagementPanel.tsx");
const TR = JSON.parse(oku("shared", "i18n", "resources", "tr.json"));
const EN = JSON.parse(oku("shared", "i18n", "resources", "en.json"));

// ---------------------------------------------------------------- davranis

test("listening secilince akilli oturum continuous'a doner", () => {
  assert.equal(sessionPolicyForEndpoint("listening", "smart"), "continuous");
  assert.equal(sessionPolicyForEndpoint("listening", "continuous"), "continuous");
});

test("initiating'de secim oldugu gibi korunur", () => {
  assert.equal(sessionPolicyForEndpoint("initiating", "smart"), "smart");
  assert.equal(sessionPolicyForEndpoint("initiating", "continuous"), "continuous");
});

test("varsayilan surekli mod ve esiksiz", () => {
  // Yanlis varsayilan, yukseltmenin ertesi sabahi tum filoyu akilli moda
  // gecirirdi.
  assert.equal(DEFAULT_DNP3_EXTENDED.session_policy, "continuous");
  assert.equal(DEFAULT_DNP3_EXTENDED.smart_max_silence_sec, null);
});

test("alani olmayan eski kayit continuous okunur", () => {
  const eski = mergeDnp3Extended({ ip_endpoint_type: "initiating", master_address: 100 });
  assert.equal(eski.session_policy, "continuous");
  assert.equal(eski.smart_max_silence_sec, null);
});

test("backend'den gelen deger varsayilani ezer", () => {
  const gelen = mergeDnp3Extended({ session_policy: "smart", smart_max_silence_sec: 93600 });
  assert.equal(gelen.session_policy, "smart");
  assert.equal(gelen.smart_max_silence_sec, 93600);
});

test("esik sinirlari backend sozlesmesiyle ayni", () => {
  // Ayrisirsa arayuz kabul eder, backend 422 doner — kullanici icin sebepsiz
  // bir hata olur.
  assert.equal(SMART_MAX_SILENCE_MIN_SEC, 60);
  assert.equal(SMART_MAX_SILENCE_MAX_SEC, 2592000);
});

// ------------------------------------------------------------------ form

test("akilli secenegi YALNIZCA initiating modunda render edilir", () => {
  const secenek = /\{isInitiating \?[\s\S]{0,200}?value="smart"/.exec(FORM);
  assert.ok(
    secenek,
    'akilli secenegi kosulsuz render ediliyor: listening modunda secilebilir ' +
      "hale gelir ve kaydetmede 422 uretir"
  );
});

test("uc nokta tipi degisince politika geri alinir", () => {
  assert.ok(
    FORM.includes("sessionPolicyForEndpoint(v.ip_endpoint_type, v.session_policy)"),
    "form uc nokta tipi degisiminde politikayi sifirlamiyor — kullanici " +
      'formda "Akilli" gorurken gecersiz bir govde gonderir'
  );
});

test("sessizlik esigi alani yalnizca akilli modda gorunur", () => {
  assert.ok(
    /\{isSmart \?[\s\S]{0,400}?smartMaxSilence/.test(FORM),
    "esik alani surekli modda da gorunuyor — o modda hicbir anlami yok"
  );
});

test("esik alani bos birakilabilir (null gonderir)", () => {
  // Bos = "cihaz seviyesinde ozel esik yok". 0 gondermek YANLIS olurdu:
  // 0 gateway'de ENV tarafinin "devre disi" anlamidir.
  assert.ok(
    /smart_max_silence_sec:\s*\n?\s*e\.target\.value\.trim\(\) === "" \? null : Number/.test(
      FORM
    ),
    "bos deger null'a cevrilmiyor"
  );
});

// ----------------------------------------------------------------- panel

test("kaydetme yolu da ayni kurali uygular", () => {
  // Formdaki useEffect ile gonderim arasinda bir yaris olsa bile govde
  // gecersiz kombinasyon TASIMAMALI.
  const cagrilar = PANEL.match(/sessionPolicyForEndpoint\(/g) ?? [];
  assert.ok(
    cagrilar.length >= 2,
    `olusturma ve guncelleme yollarinin ikisi de korunmali (bulunan: ${cagrilar.length})`
  );
});

// ------------------------------------------------------------------ i18n

test("yeni metinler iki dilde de var", () => {
  const anahtarlar = [
    "sessionPolicy",
    "sessionPolicyContinuous",
    "sessionPolicySmart",
    "sessionPolicySmartHelp",
    "smartMaxSilence",
    "smartMaxSilenceHelp"
  ];
  for (const k of anahtarlar) {
    assert.ok(TR.engineering.dnp3[k], `tr.json: engineering.dnp3.${k} eksik`);
    assert.ok(EN.engineering.dnp3[k], `en.json: engineering.dnp3.${k} eksik`);
  }
});

test('arayuz "0 = devre disi" demez', () => {
  // 0 cihaz seviyesinde GECERSIZ; sozlesmede yalnizca env tarafinda o anlama
  // gelir. Arayuzde yazmak, operatore gecersiz bir deger onerirdi.
  const metinler = [
    TR.engineering.dnp3.smartMaxSilenceHelp,
    EN.engineering.dnp3.smartMaxSilenceHelp,
    TR.engineering.dnp3.smartMaxSilence,
    EN.engineering.dnp3.smartMaxSilence
  ].join(" ");
  assert.ok(!/\b0\s*=/.test(metinler), 'yardim metni "0 = devre disi" ima ediyor');
});
