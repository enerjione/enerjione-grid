/**
 * B5 — akilli oturum (smart session) arayuz sozlesmesi, gateway v1.14.0.
 *
 * NE DEGISTI
 * ----------
 * v1.12/v1.13'te `smart` YALNIZCA `initiating` ile gecerliydi ve arayuz
 * secimi bastiriyordu. v1.14.0'da uc tipi ile mod ORTOGONAL: alti kombinasyon
 * da desteklenir. Bastirma kaldi mi, sabit IP'li (listening) bir Horstmann'i
 * Smart calistirmak Grid uzerinden imkansiz kalirdi — bu dosya kapinin geri
 * gelmedigini kilitler.
 *
 * NEDEN BURADA
 * ------------
 * Riskli mantik saf fonksiyonlarda (`dialInValidValues`,
 * `derivedMaxSilenceSec`); onlar GERCEKTEN CALISTIRILARAK sinanir. Form/panel
 * tarafinda React cercevesi kurmadan yalnizca "kaldirilan kapinin geri
 * gelmedigi" kaynak uzerinden dogrulanir (bkz. tests/run.mjs).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  COMMUNICATION_GRACE_MIN_DEFAULT,
  DEFAULT_DNP3_EXTENDED,
  SMART_LISTEN_RECONNECT_MAX_SEC,
  SMART_LISTEN_RECONNECT_MIN_SEC,
  SMART_MAX_SILENCE_MAX_SEC,
  SMART_MAX_SILENCE_MIN_SEC,
  derivedMaxSilenceSec,
  dialInValidValues,
  mergeDnp3Extended
} from "../src/shared/types";

const oku = (...p: string[]) => readFileSync(join(process.cwd(), "src", ...p), "utf8");

const FORM = oku("features", "devices", "Dnp3SettingsForm.tsx");
const PANEL = oku("features", "devices", "DeviceManagementPanel.tsx");
const TR = JSON.parse(oku("shared", "i18n", "resources", "tr.json"));
const EN = JSON.parse(oku("shared", "i18n", "resources", "en.json"));

// ---------------------------------------------------------------- davranis

test("varsayilan surekli mod ve hicbir esik sabitlenmemis", () => {
  // Yanlis varsayilan, yukseltmenin ertesi sabahi tum filoyu akilli moda
  // gecirirdi. v1.14 alanlari null kalmali: forma konan bir varsayilan,
  // kaydi acip kapatan operator eliyle diske yazilir.
  assert.equal(DEFAULT_DNP3_EXTENDED.session_policy, "continuous");
  assert.equal(DEFAULT_DNP3_EXTENDED.smart_max_silence_sec, null);
  assert.equal(DEFAULT_DNP3_EXTENDED.dial_in_interval_min, null);
  assert.equal(DEFAULT_DNP3_EXTENDED.communication_grace_min, null);
  assert.equal(DEFAULT_DNP3_EXTENDED.smart_listen_reconnect_max_sec, null);
});

test("alani olmayan eski kayit continuous ve bos okunur", () => {
  const eski = mergeDnp3Extended({ ip_endpoint_type: "initiating", master_address: 100 });
  assert.equal(eski.session_policy, "continuous");
  assert.equal(eski.smart_max_silence_sec, null);
  assert.equal(eski.dial_in_interval_min, null);
});

test("backend'den gelen deger varsayilani ezer", () => {
  const gelen = mergeDnp3Extended({
    session_policy: "auto",
    dial_in_interval_min: 240,
    communication_grace_min: 20
  });
  assert.equal(gelen.session_policy, "auto");
  assert.equal(gelen.dial_in_interval_min, 240);
  assert.equal(gelen.communication_grace_min, 20);
});

test("Dial-In secenekleri 1440'in bolenlerinden turetilir", () => {
  // 100 dk araliktadir ama 1440'in boleni degildir; Horstmann boyle bir
  // degeri kabul etmez, Grid kaydeder ve operator ayarin uygulandigini SANIR.
  const beklenen = [60, 72, 80, 90, 96, 120, 144, 160, 180, 240, 288, 360, 480, 720, 1440];
  assert.deepEqual(dialInValidValues(), beklenen);
  assert.ok(!dialInValidValues().includes(100));
});

test("esik Dial-In + tolerans'tan turetilir", () => {
  // Kanonik iliski: (dial_in + grace) * 60. 60 + 15 -> 4500 sn (75 dk).
  assert.equal(derivedMaxSilenceSec(60, 15), 4500);
  assert.equal(derivedMaxSilenceSec(240, 30), 16200);
});

test("tolerans yazilmamissa urun varsayilani kullanilir", () => {
  // Backend de ayni varsayilani uygular ve DISKE yazmaz; ikisi ayrisirsa
  // ekranda gorunen esik ile gateway'in uyguladigi esik farkli olurdu.
  assert.equal(COMMUNICATION_GRACE_MIN_DEFAULT, 15);
  assert.equal(derivedMaxSilenceSec(60, null), derivedMaxSilenceSec(60, 15));
});

test("Dial-In yoksa esik uydurulmaz", () => {
  assert.equal(derivedMaxSilenceSec(null, 15), null);
  assert.equal(derivedMaxSilenceSec(null, null), null);
});

test("sinirlar backend sozlesmesiyle ayni", () => {
  // Ayrisirsa arayuz kabul eder, backend 422 doner — kullanici icin sebepsiz
  // bir hata olur.
  assert.equal(SMART_MAX_SILENCE_MIN_SEC, 60);
  assert.equal(SMART_MAX_SILENCE_MAX_SEC, 2592000);
  assert.equal(SMART_LISTEN_RECONNECT_MIN_SEC, 5);
  assert.equal(SMART_LISTEN_RECONNECT_MAX_SEC, 600);
});

// ------------------------------------------------------------------ form

test("uc secenek de kosulsuz render edilir", () => {
  for (const deger of ["continuous", "smart", "auto"]) {
    assert.ok(
      FORM.includes(`<option value="${deger}">`),
      `${deger} secenegi yok — operator o modu hic secemez`
    );
  }
});

test("uc nokta tipine gore politika bastirilmaz", () => {
  // v1.14.0'da kisit KALKTI. `isInitiating` yalnizca IP/port alanlarini
  // yonetmeli; politikaya karisirsa listening cihaz Smart olamaz.
  assert.ok(
    !/sessionPolicyForEndpoint/.test(FORM),
    "kaldirilan uc tipi kapisi forma geri gelmis"
  );
  assert.ok(
    !/session_policy:\s*(?:gecerli|"continuous")/.test(FORM),
    "form politikayi kendiliginden continuous'a ceviriyor"
  );
});

test("Dial-In ve tolerans yalnizca uyku modlarinda gorunur", () => {
  assert.ok(
    /const oturumUykulu =\s*v\.session_policy === "smart" \|\| v\.session_policy === "auto";/.test(
      FORM
    ),
    "alanlarin gorunurlugu politikaya bagli degil"
  );
  assert.ok(
    /\{oturumUykulu \?[\s\S]{0,600}?dialInInterval/.test(FORM),
    "Dial-In surekli modda da gorunuyor — o modda hicbir anlami yok"
  );
});

test("gizlenen alanlarin degeri SILINMEZ", () => {
  // Surekli moda gecip geri donen operator girdiklerini kaybetmemeli; bunu
  // bir useEffect'in temizlemedigini kilitliyoruz.
  assert.ok(
    !/dial_in_interval_min:\s*null[\s\S]{0,80}\}\s*,\s*\[.*session_policy/.test(FORM),
    "politika degisince Dial-In temizleniyor"
  );
});

test("hesaplanan esik girdiler eksikken gosterilmez", () => {
  assert.ok(
    /\{esikSn !== null \?/.test(FORM),
    "esik alani kosulsuz render ediliyor — uydurma bir deger okunur"
  );
});

test("esik alani READ-ONLY", () => {
  const blok = /commLossThreshold"\)\}[\s\S]{0,800}?<\/label>/.exec(FORM);
  assert.ok(blok, "esik alani bulunamadi");
  assert.ok(
    /readOnly/.test(blok[0]) && /disabled/.test(blok[0]),
    "hesaplanan esik elle duzenlenebiliyor — girdilerle celisen bir deger kaydedilir"
  );
});

test("ham esik alani bos birakilabilir (null gonderir)", () => {
  // Bos = "cihaz seviyesinde ozel esik yok". 0 gondermek YANLIS olurdu:
  // 0 gateway'de ENV tarafinin "devre disi" anlamidir.
  assert.ok(
    /smart_max_silence_sec:\s*\n?\s*e\.target\.value\.trim\(\) === "" \? null : Number/.test(
      FORM
    ),
    "bos deger null'a cevrilmiyor"
  );
});

test("arayuz calisma modunu boost sinyaline baglamaz", () => {
  // Operation Mode ile Boost Mode AYRI sinyallerdir; birini digerinin yerine
  // okumak cihazi yanlis modda gostermek olur.
  assert.ok(!/boost_mode/i.test(FORM), "form boost sinyalini oturum moduna bagliyor");
});

// ----------------------------------------------------------------- panel

test("kaydetme yolu politikayi degistirmez", () => {
  // Formda gorunen ile govdeye giren AYNI olmali; panel artik hicbir
  // duzeltme uygulamiyor, secim spread ile oldugu gibi gidiyor.
  assert.ok(
    !/sessionPolicyForEndpoint/.test(PANEL),
    "kaldirilan uc tipi kapisi kaydetme yoluna geri gelmis"
  );
});

// ------------------------------------------------------------------ i18n

test("yeni metinler iki dilde de var", () => {
  const anahtarlar = [
    "sessionPolicy",
    "sessionPolicyContinuous",
    "sessionPolicySmart",
    "sessionPolicySmartHelp",
    "sessionPolicyAuto",
    "sessionPolicyAutoHelp",
    "dialInInterval",
    "dialInIntervalUnset",
    "dialInIntervalHelp",
    "communicationGrace",
    "communicationGraceDefault",
    "communicationGraceHelp",
    "commLossThreshold",
    "commLossThresholdHelp",
    "advanced",
    "smartListenReconnectMax",
    "smartListenReconnectMaxAuto",
    "smartListenReconnectMaxHelp",
    "hours",
    "smartMaxSilence",
    "smartMaxSilenceHelp"
  ];
  for (const k of anahtarlar) {
    assert.ok(TR.engineering.dnp3[k], `tr.json: engineering.dnp3.${k} eksik`);
    assert.ok(EN.engineering.dnp3[k], `en.json: engineering.dnp3.${k} eksik`);
  }
});

test("iki dilin anahtar kumesi ayni", () => {
  const tr = Object.keys(TR.engineering.dnp3).sort();
  const en = Object.keys(EN.engineering.dnp3).sort();
  assert.deepEqual(tr, en);
});

test("Otomatik yardimi Boost sinyalini isaret etmez", () => {
  // Auto, cihazin Operation Mode sinyalini izler. "Boost Mode Enabled"
  // demek, operatore yanlis sinyali kontrol ettirmek olurdu.
  const metinler = [
    TR.engineering.dnp3.sessionPolicyAutoHelp,
    EN.engineering.dnp3.sessionPolicyAutoHelp
  ].join(" ");
  assert.ok(!/boost/i.test(metinler), "Auto yardim metni Boost sinyaline atif yapiyor");
  assert.ok(/Operation Mode/.test(metinler), "Auto yardim metni hangi sinyali izledigini soylemiyor");
});

test("Smart yardimi kaldirilan uc tipi kisitini anlatmaz", () => {
  const metinler = [
    TR.engineering.dnp3.sessionPolicySmartHelp,
    EN.engineering.dnp3.sessionPolicySmartHelp
  ].join(" ");
  assert.ok(
    !/Initiating/i.test(metinler),
    "Smart yardimi hala 'yalnizca Initiating' diyor — v1.14.0'da bu kisit yok"
  );
});

test("yeniden baglanma tavani yoklama araligi gibi anlatilmaz", () => {
  // Bu bir PING/PROBE araligi DEGILDIR: ustel geri cekilmenin tepe degeridir.
  // Etiketi "yoklama" diye adlandirmak, operatore gateway'in uyuyan cihazi
  // durtecegini dusundururdu.
  const etiketler = [
    TR.engineering.dnp3.smartListenReconnectMax,
    EN.engineering.dnp3.smartListenReconnectMax
  ].join(" ");
  assert.ok(!/(probe|yoklama|ping)/i.test(etiketler), "etiket yoklama araligi ima ediyor");
  assert.ok(
    /değildir/.test(TR.engineering.dnp3.smartListenReconnectMaxHelp),
    "yardim metni yoklama araligi olmadigini acikca soylemiyor"
  );
  assert.ok(
    /not a polling/i.test(EN.engineering.dnp3.smartListenReconnectMaxHelp),
    "en yardim metni yoklama araligi olmadigini acikca soylemiyor"
  );
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
