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

// Toast susturma/konum davranis testleri (kendi dosyasinda; kosucu tek giris
// noktasi derledigi icin buradan iceri aliniyor).
import "./toast.test";

// Gateway durum modeli: "durduruldu" ariza gibi gosterilmesin.
import "./gatewayLiveness.test";

// Arsiv (historian) kurallari: olmayan bir ayar varmis gibi gosterilmesin.
import "./signalHistorian.test";

// Hat secilince harita o hatta odaklansin (Hat Yonetimi > Harita).
import "./lineFocus.test";

// Ariza seridi geometrisi: cihaz telin uzerinde, kirmizi parca dogru iki
// cihazin arasinda. Yanlis konum sahada yanlis direge gitmek demek.
import "./faultStrip.test";

// Ariza detay haritasi: kirmizi parca SON algilayan ile ILK algilamayan
// cihazin arasinda olmali. Bir direk kaymasi ekibi yanlis acikliga gonderir.
import "./faultMapView.test";

// Ana haritada cihaz secilince kamera: hat ekrana sigsin, uzaklasmasin.
import "./deviceFocus.test";

// Ariza Analizi grafiklerinin renk sozlesmesi: palet sirasi CVD (renk
// korlugu) dogrulamasindan gecen dizilim olmali. Sira degisirse ekran renk
// koru bir operator icin sessizce okunamaz hale gelir.
import "./faultChartTheme.test";

// Ariza isi haritasi olcegi: "her yer kirmizi" ya da "hicbir yer sicak degil"
// yalanlarinin ikisi de bakim butcesini yanlis yonlendirir.
import "./heatField.test";

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

// ---------------------------------------------------------------------------
// Gomulu fontlar: `@font-face` KURALI GECERLI OLMALI
// ---------------------------------------------------------------------------
// YASANAN ARIZA
// -------------
// Manrope pakete gomuldu, dosyalar dogru emitlendi, CSP de engellemiyordu —
// ama arayuz sistem fontuyla aciliyordu. Sebep tek kelimeydi:
//
//     src: url(...) format("woff2-variations");
//
// `woff2-variations` CSS Fonts 4'un ERKEN TASLAGINDAN kalma, standarttan
// CIKARILMIS bir degerdir. Tarayici taniMADIGI formatta kaynagi ATLAR; font
// hic indirilmez ve sessizce yedek yaziya dusulur.
//
// BU HATA SINIFI NEDEN SESSIZ
// ---------------------------
// Gecersiz `@font-face` ayristirma sirasinda dusuruldugu icin konsolda hata
// YOK, ag sekmesinde basarisiz istek YOK, derleme de gecer. Belirti yalnizca
// "yazi tipi bir tuhaf" seklinde goze carpar — sahada iki sürüm boyunca fark
// edilmedi.
//
// Ayni dizindeki Material Symbols duz `format("woff2")` kullaniyordu ve
// CALISIYORDU; fark tam olarak bu ekti.
const GECERLI_FORMATLAR = new Set([
  "collection", "embedded-opentype", "opentype", "svg", "truetype", "woff", "woff2",
]);

test("gomulu font CSS'lerinde format() degeri standart olmali", async () => {
  const { readdirSync, readFileSync } = await import("node:fs");
  const dizin = `${process.cwd()}/src/assets/fonts`;
  const cssler = readdirSync(dizin).filter((a) => a.endsWith(".css"));

  assert.ok(cssler.length > 0, "font CSS'i bulunamadi — dizin tasindi mi?");

  for (const dosya of cssler) {
    const metin = readFileSync(`${dizin}/${dosya}`, "utf8");
    // Yorum bloklarini cikar: aciklama metninde gecen ornekler sayilmasin.
    const kod = metin.replace(/\/\*[\s\S]*?\*\//g, "");
    const bulunan = [...kod.matchAll(/format\(\s*["']([^"']+)["']\s*\)/g)].map((m) => m[1]);

    assert.ok(bulunan.length > 0, `${dosya}: hic format() yok`);
    for (const f of bulunan) {
      assert.ok(
        GECERLI_FORMATLAR.has(f),
        `${dosya}: gecersiz format(${JSON.stringify(f)}). Tarayici bu kaynagi ` +
          `ATLAR ve font hic yuklenmez. Degisken fontlar icin de duz "woff2" kullanin.`,
      );
    }
  }
});

test("govde font zincirinde COZULMEYEN ad bulunmamali", async () => {
  // YASANAN ARIZA: zincirde "Helvetica Neue" vardi. Ubuntu'da (saha cihazi)
  // bu ad karsiliksizdir ve URW/Nimbus paketleri yoksa fontconfig onu SERIF
  // bir yuze eslestirebiliyor. Sonuc: ayni sayfada bazi basliklar serif,
  // govde sans -- "header'in yazi tipi farkli, iceriklerin farkli".
  //
  // Kural: yedek zincirinde YALNIZCA hedef sistemde karsiligi olan adlar.
  // macOS/Windows'a ozgu adlar Ubuntu'da sessizce atlanir; tehlikeli olan,
  // atlanmayip YANLIS esleseni birakmaktir.
  const { readFileSync } = await import("node:fs");
  const stil = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");
  const m = stil.match(/:root\s*\{[\s\S]*?font-family:\s*([^;]+);/);
  assert.ok(m, ":root icinde font-family bulunamadi");

  const zincir = m![1].replace(/\s+/g, " ").trim();
  assert.ok(
    !/Helvetica Neue/i.test(zincir),
    `govde zincirinde "Helvetica Neue" var -- Ubuntu'da serif'e duser: ${zincir}`,
  );
  // Zincir her zaman jenerik bir aile ile BITMELI; aksi halde hicbiri
  // bulunamazsa tarayicinin varsayilani (cogu yerde serif) devreye girer.
  assert.match(zincir, /sans-serif\s*$/, `zincir sans-serif ile bitmiyor: ${zincir}`);
});

// ---------------------------------------------------------------------------
// Govde yazi tipi kurali DERLENMIS CIKTIDA da CANLI olmali
// ---------------------------------------------------------------------------
// YASANAN ARIZA — kaynakta dogru, tarayicida olu
// ----------------------------------------------
// `styles.css` UTF-8 BOM (U+FEFF) ile basliyordu ve `:root` dosyanin ILK
// kuraliydi. BOM dosya BASINDA oldugu surece her ayristirici onu siler; kural
// sorunsuz calisir.
//
// Sonra dosyanin USTUNE ~113 satir CSS eklendi. BOM boylece dosyanin ORTASINA,
// `:root`un hemen onune dustu. Satir ortasindaki U+FEFF ARTIK BOM DEGILDIR;
// ayristirici onu bir tanimlayici sayar ve selektor sessizce suna donusur:
//
//     \feff:root { font-family: Arial, sans-serif; color: #111827; ... }
//
// Yani "U+FEFF adli element" tip selektoru. Boyle bir element olmadigi icin
// kural HICBIR ZAMAN eslesmez. Govde yazi tipini veren tek kural o oldugundan
// tum uygulama tarayici varsayilanina duser — Chrome/Windows'ta Times New
// Roman, yani SERIF.
//
// BU HATA SINIFI NEDEN BU KADAR SINSI
// -----------------------------------
//   * Kaynak dosyada kural DOGRU gorunur; goz denetimi bir sey bulamaz.
//   * DevTools'un Styles panelinde kural GORUNUR (kullanici da oyle gordu),
//     ama Computed'a hic girmez.
//   * Derleme, lint ve tip kontrolu gecer; hicbir uyari cikmaz.
//   * U+FEFF ekranda GORUNMEZ genislikte bir karakterdir.
//
// Bu yuzden test kaynakta desen aramiyor: dosyayi projenin kendi bundler'i
// (esbuild — Vite'in CSS yolu) ile DERLIYOR ve kuralin ciktida canli kaldigini
// dogruluyor. Kural hangi yolla olurse olsun olurse test duser.
test("govde yazi tipi kurali derlenmis CSS'te canli kalmali", async () => {
  const { readFileSync } = await import("node:fs");
  // Mutlak yol run.mjs tarafindan veriliyor (bundle gecici dizinde kosuyor).
  const { transformSync } = await import(process.env.E1_ESBUILD!);

  const kaynak = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");

  // 1) Gorunmez karakter hic bulunmamali. Dosya BASINDA zararsizdir ama orada
  //    birakmak tuzagi yeniden kurar: biri dosyanin ustune CSS ekledigi anda
  //    ayni ariza geri gelir. Bu yuzden hic olmasin.
  const feff = kaynak.split("\uFEFF").length - 1;
  assert.equal(
    feff,
    0,
    `styles.css icinde ${feff} adet U+FEFF var. Satir ortasindaki U+FEFF ` +
      "selektore yapisir ve kurali sessizce olduren bir tip selektorune cevirir.",
  );

  // 2) ASIL KONTROL: derlenmis ciktida kural gercekten var mi?
  const { code } = transformSync(kaynak, { loader: "css" });
  const m = code.match(/(^|[\s}])(:root\s*\{[^}]*\})/);
  assert.ok(m, "derlenmis CSS'te :root kurali bulunamadi");
  assert.match(
    m![2],
    /font-family:\s*Arial/,
    `:root derlendi ama govde fontu vermiyor: ${m![2].slice(0, 120)}`,
  );

  // 3) Selektorun onune yapismis bir sey kalmadigini ayrica dogrula.
  assert.ok(
    !/\feff\s*:root/.test(code),
    "selektor hala kacisli bir karakterle basliyor — kural olu",
  );
});
