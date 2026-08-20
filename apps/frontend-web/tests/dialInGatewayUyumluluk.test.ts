/**
 * ISTENEN vs SAHADA GECERLI — iki ayri sessiz yalanin kapisi.
 *
 * NEYI KILITLER
 * -------------
 * 1) DIAL-IN: operatorun sectigi deger, cihazda gecerli oldugu ANLAMINA
 *    GELMEZ. Form "istenen"i "cihazda aktif" gibi gosterirse, sahada hala
 *    eski aralikla rapor veren bir cihaz ekranda dogru ayarli gorunur ve
 *    haberlesme kaybi esigi yanlis hesaplanir. Kanit (cihazin kendi
 *    dosyasindan okunan deger) YOKSA blok HIC cizilmemeli.
 *
 * 2) GATEWAY YETENEK KAPISI: `gatewayCapabilities.ts` backend
 *    `app/services/gateway_compatibility.py` dosyasinin AYNASIDIR. Ayrisirsa
 *    en kotu bicimde ayrisir: arayuz "destekleniyor" der, backend payload'i
 *    sessizce dusurur, gateway guvenli tarafta calisir ve kimse bir hata
 *    gormez. Bu yuzden matris burada BACKEND KAYNAGINDAN okunarak
 *    karsilastiriliyor — iki kopyanin ayni kalmasi goze degil teste birakildi.
 *
 * NEDEN KAYNAK METNI DE OKUNUYOR
 * ------------------------------
 * Riskli mantik saf fonksiyonlarda ve onlar GERCEKTEN calistiriliyor. Formun
 * React tarafinda ise cerceve kurmadan yalnizca sozlesme dogrulanabilir:
 * "form kendi veri cekmiyor" ve "kanit yokken blok cizilmiyor"
 * (bkz. tests/run.mjs — kosucu React render etmiyor).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  V114_MIN,
  capabilityMinVersion,
  missingCapabilities,
  requiredCapabilities,
  requiredVersion,
  supportsCapability
} from "../src/shared/gatewayCapabilities";
import type { IpEndpointType, SessionPolicy } from "../src/shared/types";

const oku = (...p: string[]) => readFileSync(join(process.cwd(), ...p), "utf8");

const FORM = oku("src", "features", "devices", "Dnp3SettingsForm.tsx");
const PANEL = oku("src", "features", "devices", "DeviceManagementPanel.tsx");
const TR = JSON.parse(oku("src", "shared", "i18n", "resources", "tr.json"));
const EN = JSON.parse(oku("src", "shared", "i18n", "resources", "en.json"));
/** Backend TEK OTORITE — matris oradan okunur, buraya kopyalanmaz. */
const BACKEND = oku("..", "backend-api", "app", "services", "gateway_compatibility.py");

const UC_TIPLERI: IpEndpointType[] = ["listening", "initiating"];
const POLITIKALAR: SessionPolicy[] = ["continuous", "smart", "auto"];

// ------------------------------------------------ 1) yetenek kapisi kurali

test("surekli modda hicbir yeni yetenek istenmez", () => {
  // Surekli mod v1.11'den beri ayni; kapiya sokmak her eski gateway'i
  // sebepsiz uyari altina alirdi.
  for (const uc of UC_TIPLERI) {
    assert.deepEqual(requiredCapabilities("continuous", uc), []);
    assert.deepEqual(missingCapabilities("continuous", uc, null), []);
  }
});

test("initiating + smart KAPIYA GIRMEZ (1.12.0'dan beri calisiyor)", () => {
  // `smart_session` bilerek listede yok. Kapiya sokulsaydi, surumunu
  // bildirmemis (cok yaygin) her gateway'de SAHADA CALISAN Smart kurulumlari
  // uyari altina girerdi.
  assert.deepEqual(requiredCapabilities("smart", "initiating"), []);
  assert.deepEqual(missingCapabilities("smart", "initiating", null), []);
  assert.deepEqual(missingCapabilities("smart", "initiating", "1.12.0"), []);
});

test("listening + smart -> smart_listening; auto -> smart_auto", () => {
  assert.deepEqual(requiredCapabilities("smart", "listening"), ["smart_listening"]);
  assert.deepEqual(requiredCapabilities("auto", "initiating"), ["smart_auto"]);
  assert.deepEqual(requiredCapabilities("auto", "listening"), ["smart_auto", "smart_listening"]);
});

test("politika/uc metni bosluktan ve buyuk harften bagimsiz", () => {
  // Degerler DB'deki JSON'dan geliyor; backend de normalize ediyor.
  assert.deepEqual(requiredCapabilities(" AUTO ", " Listening "), [
    "smart_auto",
    "smart_listening"
  ]);
});

// -------------------------------------------------- 2) surum karsilastirma

test("1.14.0 ve ustu yetenekleri karsilar", () => {
  for (const surum of ["1.14.0", "1.14", "1.14.3", "2.0.0", "v1.14.0"]) {
    assert.deepEqual(
      missingCapabilities("auto", "listening", surum),
      [],
      `${surum} yeterli sayilmali`
    );
  }
});

test("1.14.0 altindaki her surum EKSIK", () => {
  for (const surum of ["1.13.9", "1.13", "1.12.0", "0.9.9"]) {
    assert.deepEqual(missingCapabilities("auto", "listening", surum), [
      "smart_auto",
      "smart_listening"
    ]);
  }
});

test("BILINMEYEN surum eksik sayilir — guvenli taraf", () => {
  // Backend de oyle yapiyor: bildirmemis bir gateway'e `auto` gondermek TUM
  // config'i reddettirebilir ve o gateway'deki BUTUN cihazlari dondurur.
  for (const bilinmeyen of [null, "", "   ", "yok"]) {
    assert.deepEqual(
      missingCapabilities("auto", "listening", bilinmeyen),
      ["smart_auto", "smart_listening"],
      `${JSON.stringify(bilinmeyen)} icin "destekleniyor" denemez`
    );
  }
  assert.equal(supportsCapability("smart_auto", null), null, "uc durumluluk kayboldu");
});

test("matriste olmayan yetenek KISITSIZ sayilir", () => {
  // Bilmedigimiz bir sey icin "desteklenmiyor" demeyiz.
  assert.equal(supportsCapability("bilinmeyen_ozellik", null), true);
  assert.equal(capabilityMinVersion("bilinmeyen_ozellik"), null);
});

test("uyaridaki surum numarasi yetenekten TURETILIR", () => {
  assert.equal(requiredVersion(["smart_auto", "smart_listening"]), V114_MIN);
  assert.equal(requiredVersion([]), null, "eksik yetenek yokken uyari cizilmemeli");
});

// ------------------------------- 3) backend AYNASI: matris ayrisMAMALI

test("minimum surumler backend matrisiyle AYNI", () => {
  for (const yetenek of ["smart_auto", "smart_listening"]) {
    const desen = new RegExp(`"${yetenek}":\\s*"([0-9.]+)"`);
    const eslesme = desen.exec(BACKEND);
    assert.ok(eslesme, `backend matrisinde ${yetenek} yok — kural tasinmis`);
    assert.equal(
      capabilityMinVersion(yetenek),
      eslesme![1],
      `${yetenek}: arayuz ${capabilityMinVersion(yetenek)} diyor, backend ${eslesme![1]}`
    );
  }
});

test("smart_session backend kapisinda da YOK", () => {
  // Backend `gerekli_yetenekler` govdesi: yalnizca auto ve listening dallari.
  const govde = /def gerekli_yetenekler\([\s\S]*?\n    return tuple\(gerekli\)/.exec(BACKEND);
  assert.ok(govde, "backend gerekli_yetenekler okunamadi — desen kaydi");
  const kod = govde[0].replace(/"""[\s\S]*?"""/g, "");
  assert.ok(
    !/smart_session/.test(kod),
    "backend smart_session'i kapiya sokmus — arayuz aynasi geride kaldi"
  );
  assert.match(kod, /gerekli\.append\("smart_auto"\)/);
  assert.match(kod, /gerekli\.append\("smart_listening"\)/);
});

test("uyumsuzluk kaydetmeyi ENGELLEMEZ (uyarir)", () => {
  // Mesru akis "once cihazi yapilandir, sonra gateway'i guncelle"dir.
  // Formda uyari bir <div>; disable/readOnly/return ile alan kapatilmamali.
  const blok = /\{uyumGerekliSurum !== null \? \([\s\S]*?\n        \) : null\}/.exec(FORM);
  assert.ok(blok, "uyumluluk uyarisi blogu bulunamadi");
  assert.ok(
    !/disabled/.test(blok[0]),
    "uyari alanlari kilitliyor — cihaz gateway surumune rehin alinmis olur"
  );
});

// ------------------------------------------- 4) Dial-In: kanit yoksa sessiz

test("Dial-In durum blogu KANIT yokken cizilmez", () => {
  assert.match(
    FORM,
    /\{dialInDurumu \? \(/,
    "durum blogu kosulsuz render ediliyor — veri yokken uydurma bir dogrulama gosterilir"
  );
});

test("'cihazdan dogrulanan' satiri ISTENEN degeri okumaz", () => {
  const satir = /dialInVerified"\)\}<\/span>[\s\S]{0,400}?<\/span>/.exec(FORM);
  assert.ok(satir, "dogrulanan satiri bulunamadi");
  assert.match(
    satir[0],
    /dialInDurumu\.readbackMin/,
    "dogrulanan satiri cihazdan gelen degeri okumuyor"
  );
  assert.ok(
    !/\bdialIn\b(?!Durumu)/.test(satir[0]),
    "dogrulanan satirinda form degeri kullanilmis — istenen, dogrulanmis gibi gosterilir"
  );
});

test("dogrulanan yoksa tire basilir, sifir/bos DEGIL", () => {
  assert.match(
    FORM,
    /dialInDurumu\.readbackMin === null\s*\?\s*DEGER_YOK/,
    "kanit yokken bosluk/0 basiliyor — ikisi de 'ayar yok' gibi okunur"
  );
});

test("ayrisma yalnizca IKI TARAF DA BILINIYORKEN 'farkli' der", () => {
  // Dogrulanan yoksa bu bir ayrisma degil, kanit eksikligidir; uyari tonu
  // vermek operatoru olmayan bir soruna yonlendirirdi.
  const karar = /const dialInFarkli =([\s\S]*?);/.exec(FORM);
  assert.ok(karar, "ayrisma karari bulunamadi");
  assert.match(karar[1], /dialIn !== null/);
  assert.match(karar[1], /dialInDurumu\.readbackMin !== null/);
  assert.match(karar[1], /dialIn !== dialInDurumu\.readbackMin/);
});

test("form KENDI veri cekmez — saf kalir", () => {
  // Fetch formda olsaydi ayar bileseni bir veri sahibi olurdu; ayni form
  // baska bir baglamda (sablon/onizleme) kullanildiginda olmayan bir cihaza
  // istek atardi.
  assert.ok(!/shared\/api/.test(FORM), "form dogrudan API cagiriyor");
  assert.ok(!/fetch\(/.test(FORM), "formda ham fetch var");
  assert.ok(
    /dialInDurumu\?:/.test(FORM) && /gatewayVersion\?:/.test(FORM),
    "kanit alanlari prop olarak alinmiyor"
  );
});

test("kanitlari panel ceker ve prop olarak gecer", () => {
  assert.match(PANEL, /fetchDeviceConfig\(accessToken, dnp3CihazId\)/, "panel config cekmiyor");
  assert.match(PANEL, /fetchGatewayUpdate\(dnp3GatewayKodu\)/, "panel gateway surumu cekmiyor");
  assert.match(PANEL, /dialInDurumu=\{dialInDurumu\}/);
  assert.match(PANEL, /gatewayVersion=\{gatewaySurumu\}/);
  // Yeni bir gateway guncelleyici YAZILMADI: mevcut duzenleme modali aciliyor.
  assert.ok(
    !/prepareGatewayUpdate|applyGatewayUpdate/.test(PANEL),
    "panel kendi guncelleme akisini kurmus — mevcut arayuz kullanilmaliydi"
  );
});

test("panel surumu ogrenemezse BILINMIYOR der, 'guncel' demez", () => {
  const dal = /catch \{[\s\S]{0,400}?setGatewaySurumu\(null\)/.exec(PANEL);
  assert.ok(dal, "surum istegi basarisizken null'a dusmuyor — sessizce 'sorun yok' olur");
});

// -------------------------------------------------------------- 5) i18n

const YENI_ANAHTARLAR = [
  "dialInConfigured",
  "dialInVerified",
  "dialInStatus",
  "dialInStatusMatched",
  "dialInStatusDiffers",
  "dialInStatusNone",
  "dialInMismatch",
  "gatewayCompatWarn",
  "gatewayVersionUnknown",
  "gatewayUpdateAction"
];

test("yeni metinler iki dilde de DOLU", () => {
  for (const k of YENI_ANAHTARLAR) {
    for (const [ad, sozluk] of [["tr", TR], ["en", EN]] as const) {
      const metin = sozluk.engineering.dnp3[k];
      assert.equal(typeof metin, "string", `${ad}.json: engineering.dnp3.${k} eksik`);
      assert.ok(metin.trim().length > 0, `${ad}.json: engineering.dnp3.${k} bos`);
    }
  }
});

test("uyari metni surumu ve mevcut gateway'i DEGISKENDEN alir", () => {
  for (const [ad, sozluk] of [["tr", TR], ["en", EN]] as const) {
    const metin: string = sozluk.engineering.dnp3.gatewayCompatWarn;
    assert.ok(metin.includes("{{version}}"), `${ad}: gerekli surum sabit yazilmis`);
    assert.ok(metin.includes("{{current}}"), `${ad}: mevcut surum metne gomulmemis`);
    assert.ok(
      !/1\.14\.0/.test(metin),
      `${ad}: surum numarasi ceviriye gomulu — matris degisince metin ayrisir`
    );
  }
});

test("readback durumlari BIRBIRINDEN ayrilir ama ARIZA iddia ETMEZ", () => {
  // NE DEGISTI (2026-08-20 urun karari): readback artik OTORITE DEGIL,
  // yalnizca tanilama. Bu test eskiden metinlerin "bekleniyor" demesini
  // ZORUNLU kiliyordu; o ifade "ayar henuz gecerli degil" anlamina gelir ve
  // artik YANLISTIR — yapilandirilan deger aninda gecerlidir.
  //
  // Korunan sey degismedi: uc durum ekranda ayirt edilebilmeli. Eklenen
  // sart: hicbiri ariza/bekleme iddia etmemeli.
  for (const [ad, sozluk] of [["tr", TR], ["en", EN]] as const) {
    const d = sozluk.engineering.dnp3;
    const metinler = [d.dialInStatusMatched, d.dialInStatusDiffers, d.dialInStatusNone];
    assert.equal(
      new Set(metinler).size,
      3,
      `${ad}: uc readback durumu ekranda ayirt edilemiyor`
    );
    for (const k of ["dialInStatusDiffers", "dialInStatusNone"]) {
      assert.doesNotMatch(
        d[k],
        /(bekleniyor|waiting|uygulanmadi|not applied|basarisiz|failed)/i,
        `${ad}.${k}: readback tanilamasi ARIZA/BEKLEME iddia ediyor — ` +
          "yapilandirilan deger zaten gecerli, bu metin operatoru yaniltir"
      );
    }
  }
});

// ------------------------------------- 6) RUNTIME durum eklenmedi (B5 siniri)

test("bu is bir DURUM ekrani getirmedi", () => {
  // Bunlarin verisi backend'de YOK; eklemek uydurma olurdu.
  const yasakli = [/next expected/i, /recovering/i, /probe/i, /son gorulme/i];
  for (const desen of yasakli) {
    assert.ok(!desen.test(FORM), `forma runtime durum sizmis: ${desen}`);
  }
  for (const k of YENI_ANAHTARLAR) {
    for (const sozluk of [TR, EN]) {
      const metin: string = sozluk.engineering.dnp3[k];
      assert.ok(
        !/(recovering|overdue|next expected)/i.test(metin),
        `${k}: runtime durumu ima ediyor`
      );
    }
  }
});

test("politika/uc ekseni bu isle YENIDEN baglanmadi", () => {
  // v1.14.0'da kalkan kisitin geri gelmedigini ayrica dogrula: kapi UYARIR,
  // secenegi bastirmaz.
  for (const uc of UC_TIPLERI) {
    for (const politika of POLITIKALAR) {
      // Uyari uretmek serbest; ama uretilen sey her zaman bir LISTE olmali,
      // hicbir kombinasyon "yasak" ile sonuclanmamali.
      assert.ok(Array.isArray(missingCapabilities(politika, uc, "1.13.0")));
    }
  }
  assert.ok(
    !/disabled=\{[^}]*eksikYetenek/.test(FORM),
    "eksik yetenek bir alani kilitliyor — uyari kapiya donusmus"
  );
});
