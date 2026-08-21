/**
 * CIHAZ IP'SI — GECERSIZ DEGERLE KAYDEDILEMEZ.
 *
 * YASANAN
 * -------
 * Alan duz metindi: "aa" yazip "Olustur"a basmak mumkundu. Backend reddediyordu
 * ama hata ancak gonderdikten SONRA, bir balonla goruluyordu; operator formu
 * doldurup geri donuyor ve ne yazdigini ariyordu.
 *
 * Daha kotusu SESSIZ HALI: gecerli ama YANLIS bir adres kaydedildiginde hata
 * gunler sonra "haberlesme yok" kiliginda ortaya cikiyor — yazim hatasi ARIZA
 * gibi teshis ediliyordu.
 *
 * ARAYUZ KURALI BACKEND ILE AYNI OLMAK ZORUNDA: gevsek olursa kullanici 422
 * yer, kati olursa backend'in kabul ettigi mesru bir adres girilemez.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { ipv4Dolu, ipv4Gecerli } from "../src/shared/ipAddress";

test("gecerli IPv4 adresleri kabul edilir", () => {
  for (const v of ["192.168.1.50", "10.0.0.9", "8.8.8.8", "172.16.0.1", "1.1.1.1"]) {
    assert.ok(ipv4Gecerli(v), `reddedildi: ${v}`);
  }
});

test("loopback BILEREK serbest", () => {
  // Ayni makinede kosan simulatore baglanmak mesru bir kurulum: saha oncesi
  // dogrulama boyle yapiliyor. Backend de ayni istisnayi taniyor.
  assert.ok(ipv4Gecerli("127.0.0.1"));
});

test("gecersiz degerler reddedilir", () => {
  for (const v of [
    "aa",              // sahada gorulen gercek girdi
    "192.168",         // eksik parca
    "10.0.0.256",      // aralik disi
    "1.2.3.4.5",       // fazla parca
    "192.168.1.",      // sondaki nokta
    "::1",             // IPv6
    "0.0.0.0",         // "herhangi bir arayuz" — cihaz adresi degil
    "224.0.0.1"        // multicast — tek outstation gosteremez
  ]) {
    assert.ok(!ipv4Gecerli(v), `KABUL EDILDI: ${v}`);
  }
});

test("BASTA SIFIR reddedilir", () => {
  // Bazi cozumleyiciler "010"u sekizlik sayar (=8); ayni metin iki farkli
  // adres anlamina gelebilir. Backend de normalize ederek bunu kapatiyor.
  assert.ok(!ipv4Gecerli("010.0.0.1"));
});

test("BOS deger 'gecersiz' degil — zorunluluk AYRI bir soru", () => {
  // Kullanici ilk harfi yazmadan kirmizi gormemeli.
  assert.ok(ipv4Gecerli(""));
  assert.ok(ipv4Gecerli("   "));
  // Ama gonderme kapisi bos degeri gecirmez.
  assert.ok(!ipv4Dolu(""));
  assert.ok(ipv4Dolu("10.0.0.1"));
});

test("null/undefined ISTISNA uretmez", () => {
  assert.ok(ipv4Gecerli(null));
  assert.ok(ipv4Gecerli(undefined));
  assert.ok(!ipv4Dolu(null));
});

test("OLUSTUR ve KAYDET butonlari gecersiz IP'de KILITLI", () => {
  const kaynak = readFileSync(
    join(process.cwd(), "src", "features", "devices", "DeviceManagementPanel.tsx"),
    "utf8"
  );
  // Iki kapi da olmali: yeni cihaz VE mevcut cihazin duzenlenmesi.
  const kilitler = kaynak.match(/disabled=\{!ipv4Dolu\(/g) ?? [];
  assert.equal(
    kilitler.length,
    2,
    `beklenen 2 kapi (olustur + kaydet), bulunan ${kilitler.length}`
  );
  // Alanin kendisi de hatali oldugunu GOSTERMELI.
  assert.match(kaynak, /aria-invalid=\{!ipv4Gecerli\(/);
  assert.match(kaynak, /devicesPanel\.form\.ipInvalid/);
});

test("hata metni iki dilde var", () => {
  for (const lang of ["tr", "en"]) {
    const d = JSON.parse(
      readFileSync(join(process.cwd(), "src", "shared", "i18n", "resources", `${lang}.json`), "utf8")
    );
    const metin = d.engineering.devicesPanel.form.ipInvalid;
    assert.ok(typeof metin === "string" && metin.length > 10, `${lang}: ipInvalid yok`);
  }
});


// ---------------------------------------------------------------------------
// HATA MESAJI YERLESIMI — alan ZIPLAMAMALI
// ---------------------------------------------------------------------------

test("hata ETIKET SATIRINDA, alanin ALTINDA DEGIL", () => {
  // YASANAN: mesaj alanin altinda ayri bir satir olarak ciziliyordu ve
  // `label` bir `grid` oldugu icin belirip kaybolunca SATIR YUKSEKLIGI
  // DEGISIYORDU. Kullanici her tusa basista alanin asagi/yukari
  // zipladigini gordu; ustelik ayni izgara satirindaki port ve DNP3 adresi
  // alanlari ile alttaki aciklama satiri da itiliyordu.
  const kaynak = readFileSync(
    join(process.cwd(), "src", "features", "devices", "DeviceManagementPanel.tsx"),
    "utf8"
  );
  assert.ok(
    !kaynak.includes('className="field-error"'),
    "hata hala alanin altinda ayri satir olarak ciziliyor"
  );
  // Iki form da (olustur + duzenle) satir ici mesaji kullanmali.
  const satirIci = (kaynak.match(/field-error-inline/g) ?? []).length;
  assert.equal(satirIci, 2, `beklenen 2 satir ici hata, bulunan ${satirIci}`);
  const satirlar = (kaynak.match(/field-label-row/g) ?? []).length;
  assert.equal(satirlar, 2, `beklenen 2 etiket satiri, bulunan ${satirlar}`);
});

test("etiket satiri YUKSEKLIGI hatadan ETKILENMEZ", () => {
  const css = readFileSync(join(process.cwd(), "src", "styles.css"), "utf8");
  const i = css.indexOf(".field-label-row {");
  assert.ok(i > 0, "etiket satiri stili yok");
  const blok = css.slice(i, css.indexOf("}", i));
  // Taban cizgisine hizali: 11px hata, 13px etiketin satirina SIGAR ve
  // yuksekligi etiket belirler.
  assert.match(blok, /align-items:\s*baseline/);
  assert.match(blok, /display:\s*flex/);

  const j = css.indexOf(".field-error-inline {");
  const hata = css.slice(j, css.indexOf("}", j));
  const px = Number(/font-size:\s*(\d+(?:\.\d+)?)px/.exec(hata)?.[1]);
  assert.ok(px <= 12, `hata metni ${px}px — etiket satirini buyutur`);
});

test("TAM kural hala erisilebilir", () => {
  // Satir ici mesaj KISA ("Gecersiz"); tam kural ("Ornek: 192.168.1.50")
  // kaybolmamali — yer tutucuda ve `title`da durmali.
  const kaynak = readFileSync(
    join(process.cwd(), "src", "features", "devices", "DeviceManagementPanel.tsx"),
    "utf8"
  );
  const title = (kaynak.match(/title=\{t\("engineering\.devicesPanel\.form\.ipInvalid"\)\}/g) ?? []);
  assert.equal(title.length, 2, "tam kural iki formda da `title` olarak yok");
  const yerTutucu = (kaynak.match(/placeholder="192\.168\.1\.50"/g) ?? []);
  assert.equal(yerTutucu.length, 2, "ornek adres yer tutucusu iki formda da yok");
});

test("kisa hata metni iki dilde var ve GERCEKTEN kisa", () => {
  for (const lang of ["tr", "en"]) {
    const d = JSON.parse(
      readFileSync(join(process.cwd(), "src", "shared", "i18n", "resources", `${lang}.json`), "utf8")
    );
    const kisa = d.engineering.devicesPanel.form.ipInvalidShort;
    assert.ok(typeof kisa === "string" && kisa.length > 2, `${lang}: ipInvalidShort yok`);
    assert.ok(kisa.length <= 14, `${lang}: "${kisa}" etiket satirina sigmaz`);
  }
});
