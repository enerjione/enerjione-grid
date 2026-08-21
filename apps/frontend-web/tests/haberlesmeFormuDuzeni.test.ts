/**
 * HABERLESME AYARLARI FORMU — gruplu duzen, aciklamalar ipucunda.
 *
 * ONCEKI HALI
 * -----------
 * 15+ alan TEK duz izgarada duruyordu: baglanti, oturum, raporlama,
 * adresleme ve zaman asimlari birbirine karisiyor, bir ayari bulmak icin
 * butun listeyi okumak gerekiyordu.
 *
 * Ustune YEDI alanin ALTINDA kalici aciklama paragrafi vardi. Tek tek
 * dogruydular ama hepsinin ayni anda ekranda durmasi gerekmiyordu: bir ayari
 * ilk kez kuran okur, her gun kullanan okumaz. Sonuc bir ayar ekrani degil
 * bir metin bloguydu — goz once paragraflari tariyor, alanlari sonra
 * buluyordu.
 *
 * `master_address` aciklamasi ISE IKI KEZ basiliyordu: hem `title`
 * ozniteligi hem altta `<small>`.
 *
 * NE KILITLENIYOR
 * ---------------
 * 1. Aciklamalar SILINMEDI, tasindi. (Kaybolan aciklama, sadelestirme degil
 *    bilgi kaybidir.)
 * 2. Uyari (`dnp3-compat-warn`) ipucuna TASINMADI — gorulmesi gereken sey
 *    aranmaya birakilmaz.
 * 3. Alan yardimi klavyeyle de acilir; `title` ozniteligine geri donulmedi.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import TR from "../src/shared/i18n/resources/tr.json";
import EN from "../src/shared/i18n/resources/en.json";

const oku = (...yol: string[]) => readFileSync(join(process.cwd(), ...yol), "utf8");
const FORM = oku("src", "features", "devices", "Dnp3SettingsForm.tsx");
const YARDIM = oku("src", "components", "FieldHelp.tsx");
const CSS = oku("src", "styles.css");

/** Formda gecen `engineering.dnp3.*` yardim anahtarlari. */
const YARDIM_ANAHTARLARI = [
  "masterAddrHelp",
  "masterPortInitTooltip",
  "dialInIntervalHelp",
  "communicationGraceHelp",
  "commLossThresholdHelp",
  "smartMaxSilenceHelp",
  "smartListenReconnectMaxHelp"
];

// ---------------------------------------------------------------------------
// 1) Aciklamalar tasindi -- SILINMEDI
// ---------------------------------------------------------------------------

test("kalici aciklama paragraflari kalmadi", () => {
  assert.ok(
    !/className="dnp3-help"/.test(FORM),
    "alan altinda hala kalici aciklama paragrafi var"
  );
});

test("her aciklama YARDIM ISARETINE tasindi -- hicbiri kaybolmadi", () => {
  for (const anahtar of YARDIM_ANAHTARLARI) {
    assert.ok(
      FORM.includes(`engineering.dnp3.${anahtar}`),
      `${anahtar} formdan tamamen dusmus -- bu sadelestirme degil bilgi kaybi`
    );
    // Metni tasiyan tek yol `Etiket`in `yardim` prop'u.
    const i = FORM.indexOf(`engineering.dnp3.${anahtar}`);
    const once = FORM.slice(Math.max(0, i - 260), i);
    assert.match(
      once,
      /yardim=|politikaYardim/,
      `${anahtar} bir yardim isaretine bagli degil`
    );
  }
});

test("oturum politikasi aciklamasi da ipucunda", () => {
  // Uc politikanin metni `politikaYardim`da hesaplaniyor; secime gore degisir.
  assert.match(FORM, /yardim=\{politikaYardim\}/, "politika aciklamasi baglanmamis");
  for (const k of [
    "sessionPolicyAutoHelp",
    "sessionPolicySmartHelp",
    "sessionPolicyContinuousHelp"
  ]) {
    assert.ok(FORM.includes(`engineering.dnp3.${k}`), `${k} dusmus`);
  }
});

test("master adres aciklamasi artik IKI KEZ basilmiyor", () => {
  // Onceden hem `title` hem `<small>`; `title` ~1 sn gecikmeyle ve kirpik
  // gosteriyordu, yani ikinci kopya yalnizca gurultuydu.
  const kez = (FORM.match(/engineering\.dnp3\.masterAddrHelp/g) ?? []).length;
  assert.equal(kez, 1, `masterAddrHelp ${kez} kez basiliyor`);
});

test("hicbir alan `title` ozniteligine geri donmedi", () => {
  // `title` klavyeyle acilmaz, dokunmatikte yoktur ve uzun metni kirpar.
  assert.ok(!/\btitle=\{t\(/.test(FORM), "bir alan yine `title` kullaniyor");
});

// ---------------------------------------------------------------------------
// 2) UYARI ipucuna tasinmaz
// ---------------------------------------------------------------------------

test("gateway uyumluluk UYARISI gorunur kalir -- ipucuna girmez", () => {
  // Aciklama istege bagli okunur; UYARI gorulmek zorundadir. Ikisini ayni
  // muameleye tabi tutmak, sahada gecersiz bir ayarin sessizce kaydedilmesi
  // demekti.
  const i = FORM.indexOf('className="dnp3-compat-warn"');
  assert.ok(i > 0, "uyari blogu kaybolmus");
  const blok = FORM.slice(i, i + 700);
  assert.ok(!blok.includes("FieldHelp"), "uyari bir ipucunun icine tasinmis");
  assert.match(blok, /gatewayCompatWarn/);
});

// ---------------------------------------------------------------------------
// 3) Gruplama
// ---------------------------------------------------------------------------

const BOLUMLER = [
  "sectionConnection",
  "sectionSession",
  "sectionReporting",
  "sectionAddressing",
  "sectionTimeouts",
  "advanced"
];

test("alanlar bolumlere ayrildi", () => {
  for (const b of BOLUMLER) {
    assert.ok(
      FORM.includes(`t("engineering.dnp3.${b}")`),
      `${b} bolumu cizilmiyor`
    );
  }
  const sayi = (FORM.match(/<Bolum baslik=/g) ?? []).length;
  assert.equal(sayi, BOLUMLER.length, `beklenen ${BOLUMLER.length} bolum, bulunan ${sayi}`);
});

test("bolum basliklari iki dilde de var", () => {
  for (const [ad, sozluk] of [
    ["tr", (TR as any).engineering.dnp3],
    ["en", (EN as any).engineering.dnp3]
  ] as const) {
    for (const b of BOLUMLER) {
      const metin = sozluk[b];
      assert.ok(
        typeof metin === "string" && metin.trim().length > 2,
        `${ad}: ${b} basligi yok`
      );
    }
  }
});

test("baglanti bolumu `hideConnectionFields` ile birlikte gizlenir", () => {
  // Cihaz duzenleme paneli kendi IP/port alanlarini ciziyor; ikinci bir
  // kopya iki farkli dogruluk kaynagi yaratirdi.
  const i = FORM.indexOf("{!hideConnectionFields ? (");
  assert.ok(i > 0, "kapi kaybolmus");
  assert.match(
    FORM.slice(i, i + 140),
    /sectionConnection/,
    "kapinin ardinda baglanti bolumu yok"
  );
});

test("OTURUM bolumu kapinin DISINDA -- panelde erisilebilir kalir", () => {
  // Regresyon kapisi: oturum politikasi bir BAGLANTI alani degildir. Kapinin
  // icine dusurulurse cihaz duzenleme panelinde hicbir yerden ulasilamaz.
  const kapiSonu = FORM.indexOf("      ) : null}", FORM.indexOf("{!hideConnectionFields ? ("));
  const oturum = FORM.indexOf('sectionSession');
  assert.ok(kapiSonu > 0 && oturum > kapiSonu, "oturum bolumu baglanti kapisinin icinde");
});

// ---------------------------------------------------------------------------
// 4) Yardim isaretinin kendisi
// ---------------------------------------------------------------------------

test("bos aciklamada isaret HIC cizilmez", () => {
  // Bos bir kutu acan dugme "burada bir sey var" der ve yalan soyler.
  assert.match(YARDIM, /if \(!temiz\) return null;/);
});

test("yardim ipucu KLAVYEYLE de acilir", () => {
  // Tasidigi bilgi baska hicbir yerde yok; klavye kullanicisinin ona
  // ulasamamasi bilgiyi tamamen kaybettirirdi.
  assert.match(YARDIM, /focusable: true/, "yalnizca fare ile aciliyor");
});

test("yardim ipucu ORTAK ilkeli kullanir (ikinci kopya yok)", () => {
  assert.match(YARDIM, /useIpucuKonum/);
  // Kirpilma yapisal olarak imkansiz olmali: modal ve dar panellerin icinde.
  assert.match(YARDIM, /createPortal/);
  assert.match(YARDIM, /document\.body/);
});

test("ikon FONT DEGIL metin -- eksik glif bos kare cizmesin", () => {
  const i = YARDIM.indexOf('className="field-help"');
  const blok = YARDIM.slice(i, i + 420);
  // `className`e bakilir, ham kelimeye DEGIL: dosyanin kendi aciklama yorumu
  // da "material-symbols" gecirir ve duz arama testi yanlis yere dusururdu.
  assert.ok(
    !/className=["'][^"']*material-symbols/.test(blok),
    "isaret font ikonuna baglanmis; subset eksikse bos kare cizer"
  );
});

test("isaret ekran okuyucuya ISIMLENDIRILIR", () => {
  // Isimsiz bir "dugme" hangi alani anlattigini soylemez.
  assert.match(YARDIM, /aria-label=\{label\}/);
  assert.match(YARDIM, /role="tooltip"/);
  assert.match(FORM, /<FieldHelp metin=\{yardim\} label=\{ad\} \/>/);
});

// ---------------------------------------------------------------------------
// 5) Stil
// ---------------------------------------------------------------------------

test("bolum basligi CIZGI ile ayrilir, KUTU ile degil", () => {
  // Her grubu cerceveye almak formu ic ice kutulardan olusan bir yigina
  // cevirirdi.
  const i = CSS.indexOf(".dnp3-bolum-baslik {");
  assert.ok(i > 0, "bolum basligi stili yok");
  const blok = CSS.slice(i, CSS.indexOf("}", i));
  assert.match(blok, /border-bottom:/);
  assert.ok(!/^\s*border:/m.test(blok), "baslik kutuya alinmis");
});

test("etiket satir ici -- yardim isareti alt satira dusmez", () => {
  const i = CSS.indexOf(".dnp3-label {");
  assert.ok(i > 0);
  const blok = CSS.slice(i, CSS.indexOf("}", i));
  assert.match(blok, /display:\s*flex/, "etiket hala `block`; 14px daire alta duser");
});

test("ipucu kutusu modal ve toast'in USTUNDE", () => {
  const i = CSS.indexOf(".field-help-tip {");
  assert.ok(i > 0, "ipucu kutusu stili yok");
  const blok = CSS.slice(i, CSS.indexOf("}", i));
  const z = /z-index:\s*(\d+)/.exec(blok)?.[1];
  assert.ok(z && Number(z) >= 10000, `ipucu alt katmanda kalir (z-index=${z})`);
  assert.match(blok, /position:\s*fixed/);
});

test("isaret DOKUNMATIKTE de acilir -- sahada tablet var", () => {
  // Dokunmatikte hover YOKTUR: yalnizca fareyle acilan bir yardim, o
  // cihazlarda bilgiyi tamamen erisilmez birakirdi.
  assert.match(YARDIM, /<button/, "isaret hala tiklanamaz bir <span>");
  assert.match(YARDIM, /onClick=/, "dokunmayla acilmiyor");
});

test("isarete basmak ETIKETIN ALANINI tetiklemez", () => {
  // `<label>` icinde duruyor: varsayilanda butona basmak bagli select'i
  // acardi. "?" isaretine basinca liste acilmasi kullaniciyi sasirtirdi.
  assert.match(YARDIM, /e\.preventDefault\(\);/);
  assert.match(YARDIM, /e\.stopPropagation\(\);/);
});

test("tiklama ipucunu KAPATMAZ -- dokunusta acilir acilmaz kapanmasin", () => {
  // Dokunmatikte tek dokunus once sentetik `mouseenter` (acar) sonra `click`
  // uretir; "degistir" davranisi ipucunu aninda kapatirdi.
  const i = YARDIM.indexOf("onClick=");
  const blok = YARDIM.slice(i, YARDIM.indexOf("</button>", i));
  assert.match(blok, /\bac\(\);/, "tiklama acmiyor");
  assert.ok(!/kapat\(\)/.test(blok), "tiklama ayni zamanda kapatiyor (toggle)");
});

test("DISARI DOKUNUNCA kapanir", () => {
  // `mouseleave` dokunmatikte guvenilir degil; bu kapi olmadan ipucu ekranda
  // asili kalirdi.
  const cekirdek = oku("src", "components", "tipKonum.ts");
  assert.match(cekirdek, /pointerdown/, "disari dokunma kapisi yok");
  assert.match(cekirdek, /el\.contains\(e\.target\)/, "kendi uzerine dokunus da kapatiyor");
});

test("dugme varsayilanlari sifirlandi", () => {
  const i = CSS.indexOf(".field-help {");
  const blok = CSS.slice(i, CSS.indexOf("}", i));
  assert.match(blok, /appearance:\s*none/, "tarayici dugme gorunumu sizar");
  assert.match(blok, /padding:\s*0/);
});
