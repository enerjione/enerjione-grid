/**
 * Gateway v1.14.0 — oturum politikasi ile UC NOKTA TIPININ ayrilmasi.
 *
 * NE DEGISTI
 * ----------
 * v1.12/v1.13 sozlesmesinde `smart` YALNIZCA `initiating` uc noktasiyla
 * gecerliydi; backend digerini 422 ile reddediyor, arayuz de secimi
 * `sessionPolicyForEndpoint` ile bastirip kullaniciyi sessizce `continuous`a
 * geri cekiyordu. v1.14.0'da bu kisit KALKTI: uc tipi ile oturum modu
 * ORTOGONAL iki eksendir ve alti kombinasyonun tamami gecerlidir.
 *
 * BU DOSYA NEYI KILITLER
 * ----------------------
 * Kaldirilan kapinin geri gelmesi SESSIZ bir kayiptir: form yine acilir, kayit
 * yine basarili olur, sadece operatorun sectigi mod diske baska turlu yazilir.
 * Sabit IP'li (listening) bir Horstmann'i Smart calistirmak Grid uzerinden
 * imkansiz hale gelir ve kimse bir hata mesaji gormez. Bu yuzden:
 *
 *   1. Alti kombinasyon model tarafinda GERCEKTEN calistirilarak sinanir.
 *   2. Uc tipi degisimi politikayi ezmemeli (form + kaydetme yolu).
 *   3. `master_ip_port` otomatigi yalnizca `initiating`e ozel KALMALI —
 *      ayrilan iki eksenden yanlis olani birbirine baglanmasin.
 *   4. Dial-In kumesi 1440'in bolenlerinden turetilmeli (100 ve 70 GECERSIZ).
 *   5. Sessizlik esigi Dial-In + tolerans'tan turetilmeli, girdi eksikse null.
 *   6. Yeni metinler iki dilde de olmali ve anahtar kumeleri AYNI kalmali.
 *   7. Ayar formu bir DURUM ekrani degildir: runtime ifadeleri sizmamali.
 *
 * NEDEN KAYNAK METNI OKUNUYOR
 * ---------------------------
 * Riskli mantik saf fonksiyonlarda (`mergeDnp3Extended`, `dialInValidValues`,
 * `derivedMaxSilenceSec`) ve onlar calistiriliyor. Formun/panelin React
 * tarafinda ise cerceve kurmadan yalnizca "kaldirilan kapi geri gelmedi"
 * dogrulanabilir; kosucu React render etmiyor (bkz. tests/run.mjs).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  COMMUNICATION_GRACE_MIN_DEFAULT,
  DEFAULT_DNP3_EXTENDED,
  DIAL_IN_INTERVAL_MIN_MAX,
  DIAL_IN_INTERVAL_MIN_MIN,
  derivedMaxSilenceSec,
  dialInValidValues,
  mergeDnp3Extended
} from "../src/shared/types";
import type {
  Dnp3ExtendedSettings,
  IpEndpointType,
  SessionPolicy
} from "../src/shared/types";

const oku = (...p: string[]) => readFileSync(join(process.cwd(), "src", ...p), "utf8");

const TYPES = oku("shared", "types.ts");
const FORM = oku("features", "devices", "Dnp3SettingsForm.tsx");
const PANEL = oku("features", "devices", "DeviceManagementPanel.tsx");
const TR = JSON.parse(oku("shared", "i18n", "resources", "tr.json"));
const EN = JSON.parse(oku("shared", "i18n", "resources", "en.json"));

const UC_TIPLERI: IpEndpointType[] = ["listening", "initiating"];
const POLITIKALAR: SessionPolicy[] = ["continuous", "smart", "auto"];

/** Formun `onChange` yamasini panelin uyguladigi SEKILDE uygular.
 *  Panel: `onChange={(patch) => setDnp3Ext((prev) => ({ ...prev, ...patch }))}`
 *  — asagida o satirin hala duz spread oldugu ayrica dogrulaniyor. */
const yamaUygula = (
  v: Dnp3ExtendedSettings,
  patch: Partial<Dnp3ExtendedSettings>
): Dnp3ExtendedSettings => ({ ...v, ...patch });

// ------------------------------------------------- 1) alti kombinasyon

test("v1.14 sozlesmesi: politika ekseni uc deger tasir", () => {
  // Tip TS'te silinir; uc secenegin sozlesmede oldugunu ancak kaynaktan
  // dogrulayabiliriz. `auto` dusseydi asagidaki kombinasyon testleri de
  // sessizce anlamsizlasirdi.
  assert.match(
    TYPES,
    /export type SessionPolicy = "continuous" \| "smart" \| "auto";/,
    "SessionPolicy uc degerli degil — v1.14 'auto' modu sozlesmeden dusmus"
  );
  assert.match(
    TYPES,
    /export type IpEndpointType = "initiating" \| "listening";/,
    "IpEndpointType degismis — kombinasyon sayisi artik 6 degil"
  );
});

test("alti kombinasyonun tamami modelde gecerli", () => {
  // ORTOGONALLIK TESTI: her uc tipi her politika ile birlikte hayatta
  // kalmali. Bir kombinasyon eleniyorsa (eskiden listening+smart eleniyordu)
  // operatorun sectigi mod diske baska turlu yazilir.
  const gorulen = new Set<string>();
  for (const uc of UC_TIPLERI) {
    for (const politika of POLITIKALAR) {
      const kayit = mergeDnp3Extended({
        ip_endpoint_type: uc,
        session_policy: politika,
        master_address: 100
      });
      assert.equal(kayit.ip_endpoint_type, uc, `${uc}+${politika}: uc tipi degismis`);
      assert.equal(
        kayit.session_policy,
        politika,
        `${uc}+${politika}: politika bastirilmis — v1.14'te bu kisit YOK`
      );
      gorulen.add(`${kayit.ip_endpoint_type}+${kayit.session_policy}`);
    }
  }
  assert.equal(gorulen.size, 6, `alti ayri kombinasyon beklenirdi: ${[...gorulen].join(", ")}`);
});

test("bastiran kapi (sessionPolicyForEndpoint) kod tabaninda YOK", () => {
  // Ne sozlesmede, ne formda, ne kaydetme yolunda. Yalnizca types.ts'teki
  // aciklama metninde adi gecebilir (neden kaldirildigini anlatiyor).
  const kod = TYPES.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  assert.ok(
    !/sessionPolicyForEndpoint/.test(kod),
    "kaldirilan kapi types.ts'e geri gelmis"
  );
  assert.ok(!/sessionPolicyForEndpoint/.test(FORM), "kaldirilan kapi forma geri gelmis");
  assert.ok(
    !/sessionPolicyForEndpoint/.test(PANEL),
    "kaldirilan kapi kaydetme yoluna geri gelmis"
  );
});

// -------------------------- 2) listening'e cevirmek Smart/Auto'yu SILMEZ

test("uc tipi listening'e cevrilince Smart/Auto SILINMEZ", () => {
  // Panelin gercek yama yolu: kullanici uc tipi acilir kutusunu degistirir,
  // yalnizca `ip_endpoint_type` yamasi gider. Politika alanina DOKUNULMAMALI.
  for (const politika of ["smart", "auto"] as SessionPolicy[]) {
    const once = mergeDnp3Extended({
      ip_endpoint_type: "initiating",
      session_policy: politika,
      dial_in_interval_min: 240,
      communication_grace_min: 20
    });
    const sonra = yamaUygula(once, { ip_endpoint_type: "listening" });

    assert.equal(sonra.ip_endpoint_type, "listening");
    assert.equal(
      sonra.session_policy,
      politika,
      `${politika} secimi listening'e gecince continuous'a cevrilmis — kaldirilan davranis`
    );
    // Uyku modunun girdileri de silinmemeli: sabit IP'li cihaz da planli
    // rapor gonderir, esik onlardan turetilir.
    assert.equal(sonra.dial_in_interval_min, 240);
    assert.equal(sonra.communication_grace_min, 20);
  }
});

test("formda politikayi duzelten bir effect YOK", () => {
  // Kaldirilan davranis tam olarak bir useEffect'ti: `ip_endpoint_type`
  // degisince `session_policy`yi geri aliyordu.
  const effectler = [...FORM.matchAll(/useEffect\(\(\) => \{([\s\S]*?)\n\s*\}, \[([^\]]*)\]\);/g)];
  assert.ok(effectler.length > 0, "formda hic useEffect bulunamadi — desen kaydi, test korlesti");
  for (const [, govde, bagimliliklar] of effectler) {
    assert.ok(
      !/session_policy/.test(govde),
      `bir useEffect politikayi yaziyor:\n${govde.trim().slice(0, 200)}`
    );
    assert.ok(
      !/session_policy/.test(bagimliliklar),
      `bir useEffect politikayi izliyor: [${bagimliliklar}]`
    );
  }
});

test("form politikaya SABIT bir deger atamaz", () => {
  // Tek mesru yazma yolu kullanicinin secimi:
  //   set({ session_policy: e.target.value as SessionPolicy })
  const atamalar = [...FORM.matchAll(/session_policy:\s*([^\n,}]+)/g)].map((m) => m[1].trim());
  assert.ok(atamalar.length > 0, "formda politika atamasi bulunamadi — desen kaydi");
  for (const atama of atamalar) {
    // Iki mesru yazma yolu da KULLANICI SECIMIDIR:
    //   eski: e.target.value as SessionPolicy   (acilir liste)
    //   yeni: pol.key                            (tiklanan secim karti)
    // Sabit bir literal ("smart" gibi) YASAK: form kullanicinin secimini
    // ezmemeli.
    assert.match(
      atama,
      /e\.target\.value as SessionPolicy|pol\.key/,
      `politikaya kullanici secimi disinda bir deger yaziliyor: ${atama}`
    );
  }
});

test("uc secenek de KOSULSUZ render edilir", () => {
  // Acilir liste SECIM KARTLARINA cevrildi; kilitlenen sey MARKUP degil,
  // uc secenegin de KOSULSUZ sunulmasi.
  const blok = /const POLITIKALAR[\s\S]*?\];/.exec(FORM);
  assert.ok(blok, "oturum politikasi secenek listesi bulunamadi");
  for (const deger of POLITIKALAR) {
    assert.ok(
      blok[0].includes(`key: "${deger}"`),
      `${deger} secenegi yok — operator o modu hic secemez`
    );
  }
  assert.ok(
    !/isInitiating|ip_endpoint_type/.test(blok[0]),
    "secenek listesi uc nokta tipine bagli — listening cihaz Smart olamaz"
  );
});

test("kaydetme yolu yamayi DUZ spread ile uygular", () => {
  // Araya girecek bir "duzeltme", formda gorunen ile govdeye gireni ayirir.
  assert.match(
    PANEL,
    /onChange=\{\(patch\) => setDnp3Ext\(\(prev\) => \(\{ \.\.\.prev, \.\.\.patch \}\)\)\}/,
    "form yamasi artik duz spread ile uygulanmiyor"
  );
});

// --------------------------- 3) master_ip_port initiating'e OZEL kalmali

test("master port otomatigi yalnizca initiating'de calisir", () => {
  const effect = /useEffect\(\(\) => \{\s*if \(!isInitiating\) return;([\s\S]*?)\n\s*\}, \[([^\]]*)\]\);/.exec(
    FORM
  );
  assert.ok(effect, "port otomatigi `if (!isInitiating) return;` ile baslamiyor");
  assert.match(effect[1], /master_ip_port/, "port otomatigi port yazmiyor — desen kaydi");
  assert.match(
    effect[2],
    /isInitiating/,
    "port otomatigi uc tipini izlemiyor"
  );
});

test("master port alani listening'de duzenlenebilir, initiating'de kilitli", () => {
  const blok = /\{t\("engineering\.dnp3\.masterPort"\)\}[\s\S]*?<\/label>/.exec(FORM);
  assert.ok(blok, "master port alani bulunamadi");
  assert.match(
    blok[0],
    /disabled=\{isInitiating\}/,
    "port kilidi uc tipine bagli degil — initiating'de otomatik atanan port elle bozulabilir"
  );
  assert.ok(
    !/session_policy/.test(blok[0]),
    "port alani oturum politikasina bakiyor — iki eksen v1.14'te AYRI"
  );
});

test("kaydetme yolunda port karari uc tipinden gelir, politikadan degil", () => {
  assert.match(
    PANEL,
    /master_ip_port:\s*dnp3Ext\.ip_endpoint_type === "initiating"/,
    "kaydetme yolunda port karari uc tipine bagli degil"
  );
  // Pencere ternary'nin SONUNDA biter: hemen ardindaki yorum satiri
  // `session_policy`den soz ediyor ve pencereye girerse test yanlis yere
  // dusrdu. (Satir sonu CRLF olabilir; desen buna dayanmaz.)
  const portKarari = /master_ip_port:\s*dnp3Ext\.ip_endpoint_type === "initiating"[\s\S]{0,200}?:\s*dnp3Ext\.master_ip_port,/.exec(
    PANEL
  );
  assert.ok(portKarari, "port karari blogu okunamadi");
  assert.ok(
    !/session_policy/.test(portKarari[0]),
    "port karari oturum politikasina bakiyor"
  );
});

// ---------------------------------------- 4) Dial-In gecerli deger uretimi

test("Dial-In kumesi 1440'in bolenlerinden turetilir", () => {
  const degerler = dialInValidValues();

  // Sahada sik secilen degerler kumede OLMALI.
  for (const gecerli of [60, 120, 240, 360, 720, 1440]) {
    assert.ok(degerler.includes(gecerli), `${gecerli} dk kumede yok`);
  }

  // 100 ve 70 araliktadir ama 1440'in boleni DEGILDIR. Horstmann boyle bir
  // degeri kabul etmez; Grid kaydeder ve operator ayarin uygulandigini SANIR.
  for (const bolenDegil of [70, 100]) {
    assert.ok(
      !degerler.includes(bolenDegil),
      `${bolenDegil} dk kumede — 1440'in boleni degil, cihaz bu ayari uygulamaz`
    );
    assert.notEqual(1440 % bolenDegil, 0, `${bolenDegil} testin varsayimini bozuyor`);
  }

  // 59 ve 1441 aralik disi (ikisi de 1440'i tam bolmez ama asil eleme sinir).
  for (const aralikDisi of [59, 1441]) {
    assert.ok(!degerler.includes(aralikDisi), `${aralikDisi} dk aralik disi olmali`);
  }

  // Kume tanimin kendisiyle tutarli: hepsi bolen, hepsi aralikta, artan sirali.
  for (const dk of degerler) {
    assert.equal(1440 % dk, 0, `${dk} 1440'in boleni degil`);
    assert.ok(dk >= DIAL_IN_INTERVAL_MIN_MIN && dk <= DIAL_IN_INTERVAL_MIN_MAX, `${dk} aralik disi`);
  }
  assert.deepEqual(degerler, [...degerler].sort((a, b) => a - b), "liste artan sirali degil");
  assert.equal(degerler[0], DIAL_IN_INTERVAL_MIN_MIN);
  assert.equal(degerler[degerler.length - 1], DIAL_IN_INTERVAL_MIN_MAX);
});

test("form Dial-In listesini sozlesmeden TURETIR", () => {
  // Elle yazilmis bir liste, backend araligi degistiginde sessizce ayrisir.
  assert.match(
    FORM,
    /const DIAL_IN_OPTIONS = dialInValidValues\(\);/,
    "form Dial-In seceneklerini kendi listesinden uretiyor"
  );
});

// ------------------------------------------------------- 5) esik hesabi

test("esik = (Dial-In + tolerans) x 60", () => {
  // Kanonik ornek: 60 dk Dial-In + 15 dk tolerans -> 75 dk = 4500 sn.
  assert.equal(derivedMaxSilenceSec(60, 15), 4500);
  assert.equal(derivedMaxSilenceSec(60, 15)! / 60, 75);
  assert.equal(derivedMaxSilenceSec(1440, 30), (1440 + 30) * 60);
});

test("tolerans yazilmamissa urun varsayilani (15 dk) uygulanir", () => {
  // Backend de ayni varsayilani uygular ve DISKE yazmaz; ayrisirlarsa ekranda
  // gorunen esik ile gateway'in uyguladigi esik farkli olur.
  assert.equal(COMMUNICATION_GRACE_MIN_DEFAULT, 15);
  assert.equal(derivedMaxSilenceSec(60, null), 4500);
});

test("girdi eksikse esik UYDURULMAZ", () => {
  assert.equal(derivedMaxSilenceSec(null, 15), null);
  assert.equal(derivedMaxSilenceSec(null, null), null);
});

test("esik uc tipinden ve politikadan BAGIMSIZ", () => {
  // Ayni Dial-In/tolerans, alti kombinasyonda da ayni esigi vermeli; esik
  // ekseni ucuncu bir gizli kosula baglanmasin.
  for (const uc of UC_TIPLERI) {
    for (const politika of POLITIKALAR) {
      const kayit = mergeDnp3Extended({
        ip_endpoint_type: uc,
        session_policy: politika,
        dial_in_interval_min: 60,
        communication_grace_min: 15
      });
      assert.equal(
        derivedMaxSilenceSec(kayit.dial_in_interval_min, kayit.communication_grace_min),
        4500,
        `${uc}+${politika}: esik farkli cikti`
      );
    }
  }
});

test("varsayilan kayit hicbir esigi diske sabitlemez", () => {
  assert.equal(DEFAULT_DNP3_EXTENDED.session_policy, "continuous");
  assert.equal(DEFAULT_DNP3_EXTENDED.dial_in_interval_min, null);
  assert.equal(DEFAULT_DNP3_EXTENDED.communication_grace_min, null);
  assert.equal(
    derivedMaxSilenceSec(
      DEFAULT_DNP3_EXTENDED.dial_in_interval_min,
      DEFAULT_DNP3_EXTENDED.communication_grace_min
    ),
    null
  );
});

// ------------------------------------------------------------ 6) i18n

const YENI_ANAHTARLAR = [
  "sessionPolicy",
  "sessionPolicyContinuous",
  "sessionPolicyContinuousHelp",
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
  "minutes",
  "seconds"
];

test("yeni anahtarlar iki dilde de DOLU", () => {
  for (const k of YENI_ANAHTARLAR) {
    const tr = TR.engineering.dnp3[k];
    const en = EN.engineering.dnp3[k];
    assert.equal(typeof tr, "string", `tr.json: engineering.dnp3.${k} eksik`);
    assert.equal(typeof en, "string", `en.json: engineering.dnp3.${k} eksik`);
    assert.ok(tr.trim().length > 0, `tr.json: engineering.dnp3.${k} bos`);
    assert.ok(en.trim().length > 0, `en.json: engineering.dnp3.${k} bos`);
  }
});

test("iki dilin anahtar kumesi AYNI", () => {
  // Ayrisirsa eksik dilde ekrana ham anahtar ("engineering.dnp3.hours") duser.
  assert.deepEqual(
    Object.keys(TR.engineering.dnp3).sort(),
    Object.keys(EN.engineering.dnp3).sort()
  );
});

test("formun kullandigi her dnp3 anahtari iki dilde de var", () => {
  const kullanilan = [...FORM.matchAll(/t\("engineering\.dnp3\.([A-Za-z0-9_]+)"/g)].map((m) => m[1]);
  assert.ok(kullanilan.length > 0, "formda hic ceviri anahtari bulunamadi — desen kaydi");
  for (const k of new Set(kullanilan)) {
    assert.ok(TR.engineering.dnp3[k], `formda kullanilan ${k} tr.json'da yok`);
    assert.ok(EN.engineering.dnp3[k], `formda kullanilan ${k} en.json'da yok`);
  }
});

test("Smart yardimi kaldirilan uc tipi kisitini ANLATMAZ", () => {
  const metinler = [
    TR.engineering.dnp3.sessionPolicySmartHelp,
    EN.engineering.dnp3.sessionPolicySmartHelp
  ].join(" ");
  assert.ok(
    !/Initiating/i.test(metinler),
    "Smart yardimi hala 'yalnizca Initiating' diyor — v1.14.0'da bu kisit yok"
  );
});

// ----------------------------------- 7) form bir DURUM ekrani DEGILDIR

test("ayar formuna runtime durum ifadeleri sizmaz", () => {
  // Bu alanlar CIHAZA YAZILAN ayarlardir. "Smart Bekleme" / "Gecikmis" gibi
  // anlik durumlar gateway'den gelir ve baska bir yerde gosterilir; ayar
  // formunda gorunmeleri, operatore kaydedilebilir bir sey sanmasina yol
  // acardi (ve kaydet'e basinca hicbir sey degismezdi).
  const yasakli: RegExp[] = [
    /Smart Bekleme/i,
    /Bekleme/i,
    /Gecikmi[sş]/i,
    /overdue/i,
    /awaiting/i,
    /last_?[Ss]een/,
    /session_state|sessionState/,
    /comm_lost/,
    /useLiveValuesSocket|liveValues/
  ];
  for (const desen of yasakli) {
    assert.ok(
      !desen.test(FORM),
      `ayar formunda runtime durum ifadesi var: ${desen} — form yalnizca AYAR yazar`
    );
  }
});

test("form yalnizca ayar alanlarini yazar (durum alani yazmaz)", () => {
  // `set({...})` ile yazilan her alan Dnp3ExtendedSettings'in bir uyesi
  // olmali; formun okuduklarinin da (v.<alan>) ayni kumede kalmasi gerekir.
  const model = new Set(Object.keys(DEFAULT_DNP3_EXTENDED));
  const okunan = new Set([...FORM.matchAll(/\bv\.([a-z_]+)\b/g)].map((m) => m[1]));
  assert.ok(okunan.size > 0, "formda model okumasi bulunamadi — desen kaydi");
  for (const alan of okunan) {
    assert.ok(
      model.has(alan),
      `form modelde olmayan bir alani okuyor: ${alan} (runtime durumu sizmis olabilir)`
    );
  }
});
