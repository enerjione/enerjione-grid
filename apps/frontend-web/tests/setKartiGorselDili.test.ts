/**
 * "TUMU" SEKMESI — tek gorsel dil, renk yalnizca ISTISNADA.
 *
 * ONCEKI HALI (olculdu)
 * ---------------------
 * TEK bilesen icin 44 farkli renk, 14 gradient kurali (ekranda 18 gradient
 * yuzey: 3 serit + 3 baslik + 12 KPI rozeti) ve 7 metin boyutu. "AI uretimi"
 * hissinin sebebi somut:
 *
 *   * Ayni kartta DORT farkli etiket/deger duzeni vardi ve ikisi birbirinin
 *     TAM TERSIYDI (KPI deger-ustte, sayac etiket-ustte).
 *   * Master KIMLIK rozeti (#fef3c7/#d97706) ile Akim KPI rozeti BIREBIR
 *     AYNI renkti: "bu unite Master" ile "bu deger akim" ayirt edilemiyordu.
 *   * Saglikli bir kartta ust uste ALTI yesil onay isareti duruyordu.
 *   * SIFIR ariza — yani iyi haber — kart dibinde iki kirmizi alarm kutusu
 *     olarak gosteriliyordu.
 *   * Kardes sekme (Genel Bakis) AYNI rozetleri duz dolguyla ciziyordu;
 *     ayni cihazin iki sekmesi iki gorsel dil konusuyordu.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const oku = (...y: string[]) => readFileSync(join(process.cwd(), ...y), "utf8");
const CSS = oku("src", "styles.css");
const TSX = oku("src", "features", "device-detail", "DeviceAllSignalsTab.tsx");

/** Set karti CSS blogu. */
function blok(): string {
  const bas = CSS.indexOf("/* ---- Tumu sekmesi ---- */");
  assert.ok(bas > 0, "set karti blogu bulunamadi");
  const son = CSS.indexOf(".device-all { display: flex", bas);
  assert.ok(son > bas, "blok sonu bulunamadi");
  // YORUMLAR CIKARILIR: aciklamalar eski degerleri anlatiyor ("onceden 44
  // renk vardi", "#fef3c7 idi") ve sayimlara karismamalari gerekir.
  return CSS.slice(bas, son).replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Tek bir CSS kurali — YORUMLAR CIKARILMIS.
 *
 *  Aciklama yorumlari eski renk degerlerini ("onceden #fef3c7 idi") ANLATIR;
 *  ham metinde aramak testi kendi gerekcesine takilir. */
function kural(ad: string): string {
  const b = blok();
  const i = b.indexOf(`.${ad} {`);
  assert.ok(i >= 0, `${ad} kurali yok`);
  return b.slice(i, b.indexOf("}", i)).replace(/\/\*[\s\S]*?\*\//g, "");
}

// ---------------------------------------------------------------------------
// 1) GRADIENT VE FAZLA RENK
// ---------------------------------------------------------------------------

test("GRADIENT kalmadi", () => {
  const n = (blok().match(/linear-gradient/g) ?? []).length;
  assert.equal(n, 0, `${n} gradient duruyor`);
});

test("renk sayisi ciddi olcude dustu", () => {
  const renkler = new Set(
    (blok().match(/#[0-9a-fA-F]{3,8}/g) ?? []).map((x) => x.toLowerCase())
  );
  assert.ok(
    renkler.size <= 32,
    `${renkler.size} farkli renk — onceden 44'tu, hedef <=32`
  );
});

test("tipografi merdiveni DAR", () => {
  // Onceden 7 metin boyutu vardi ve 10->11->12->13 adimlari %8-10:
  // ayirt edilemiyordu, dolayisiyla uc onem seviyesi tek seviye gibi
  // okunuyordu.
  const boyutlar = new Set(
    (blok().match(/font-size:\s*(\d+(?:\.\d+)?)px/g) ?? []).map((x) => x)
  );
  assert.ok(boyutlar.size <= 6, `${boyutlar.size} farkli font-size (ikonlar dahil)`);
});

test("sahte agirlik basamaklari kaldirildi", () => {
  // Govde fontu ARIAL (kanonik karar) ve Arial'da yalnizca 400/700 yuzu
  // var — 600/700/800 EKRANDA AYNI cikiyordu.
  const b = blok();
  assert.ok(!/font-weight:\s*800/.test(b), "800 hala var (Arial'da 700 ile ayni)");
  assert.ok(!/font-weight:\s*600/.test(b), "600 hala var (Arial'da 400 ile ayni)");
});

// ---------------------------------------------------------------------------
// 2) RENK ANLAM TASIR
// ---------------------------------------------------------------------------

test("OLCUMLER notr — durum degiller", () => {
  // Akim/Gerilim/Sicaklik birer olcumdur. Her birine ayri renkli rozet
  // vermek renge anlam yuklemeden gurultu uretiyordu.
  for (const m of ["current", "voltage", "temperature"]) {
    const i = TSX.indexOf(`deviceDetail.kpi.${m}`);
    assert.ok(i > 0, `${m} KPI'si yok`);
    const satir = TSX.slice(TSX.lastIndexOf("<SetKpi", i), i);
    assert.match(satir, /tone="notr"/, `${m} hala renkli ton tasiyor`);
  }
});

test("PIL istisna — yuzdesi bir DURUMDUR", () => {
  const i = TSX.indexOf('icon="battery_full"');
  const blokTsx = TSX.slice(i, i + 500);
  for (const t of ["green", "amber", "red"]) {
    assert.ok(blokTsx.includes(`"${t}"`), `pil ${t} tonunu kaybetmis`);
  }
  // Olculemediyse notr: "bilmiyoruz" bir uyari degildir.
  assert.match(blokTsx, /battPct == null\s*\?\s*"notr"/);
});

test("KIMLIK rozeti ile OLCUM rozeti ayni renk DEGIL", () => {
  // Onceden Master kimlik rozeti (#fef3c7/#d97706) ile Akim KPI rozeti
  // BIREBIR AYNIYDI.
  const kimlik = kural("device-set-icon");
  const kpi = kural("device-set-kpi-icon");
  const renk = (s: string) => (s.match(/#[0-9a-fA-F]{3,8}/g) ?? []).join(",");
  assert.ok(!/#fef3c7|#d97706/.test(kimlik), "kimlik rozeti hala olcum rengiyle ayni");
  assert.equal(renk(kimlik), renk(kpi), "ikisi de notr olmali");
});

test("kart kimligi TEK YERDE: ust serit", () => {
  const b = blok();
  // Baslik zemini gradient washi SILINDI (kimligi ucuncu kez tekrarliyordu).
  assert.ok(
    !/\.device-set-card\.tone-\w+\s+\.device-set-head/.test(b),
    "baslik zemini hala tona gore boyaniyor"
  );
  // Serit tek renk ve tona gore. Hizalama bosluklarina toleransli.
  for (const t of ["master", "green", "amber", "blue"]) {
    assert.match(
      b,
      new RegExp(`\\.device-set-card\\.tone-${t}\\s*\\{\\s*--st:`),
      `${t} icin kimlik rengi tanimli degil`
    );
  }
});

// ---------------------------------------------------------------------------
// 3) NORMAL SESSIZ, ISTISNA GORUNUR
// ---------------------------------------------------------------------------

test("'Normal' rozeti SESSIZ", () => {
  // Saglikli bir kartta ust uste ALTI yesil onay isareti vardi; renk
  // hicbir seyi ayirt etmiyordu.
  const n = kural("device-set-badge.is-normal");
  assert.ok(!/#16a34a|#22c55e|#15803d/.test(n), "normal hala yesil");
  assert.match(n, /#64748b/, "normal notr griye alinmamis");
});

test("'Aktif' rozeti TEK GERCEK VURGU", () => {
  const a = kural("device-set-badge.is-active");
  // Projenin doktrini: vurgu renkle DEGIL dolgu + kenarlik ile.
  assert.match(a, /background:/);
  assert.match(a, /border:/);
  assert.match(a, /border-radius:\s*999px/);
});

// ---------------------------------------------------------------------------
// 4) SAYACLAR — renk DEGERDEN gelir
// ---------------------------------------------------------------------------

test("SIFIR ariza alarm rengiyle gosterilmez", () => {
  const k = kural("device-set-counter");
  assert.ok(!/#fef2f2|#fff7ed/.test(k), "sayac kutusu kosulsuz alarm zeminli");
  assert.match(k, /background:\s*#f8fafc/, "varsayilan notr degil");
});

test("renk YALNIZCA sifirdan buyukken", () => {
  const b = blok();
  assert.ok(b.includes(".device-set-counter.has-fault"), "has-fault kurali yok");
  // TSX: sinif degerden hesaplanir.
  assert.match(TSX, /const arizaVar = sayi != null && sayi > 0;/);
  assert.match(TSX, /arizaVar \? " has-fault" : ""/);
});

test("olculemeyen sayac ARIZA sayilmaz", () => {
  // "Bilmiyoruz"u alarm gibi gostermek, gercek bir arizanin dikkat
  // cekiciligini azaltirdi.
  assert.match(TSX, /sayi != null && sayi > 0/);
  assert.match(TSX, /\{sayi != null \? sayi : "—"\}/);
});

// ---------------------------------------------------------------------------
// 5) TEK ETIKET/DEGER DUZENI
// ---------------------------------------------------------------------------

test("sayac ve KPI AYNI duzende: deger ustte", () => {
  // Onceden birbirinin TAM TERSIYDI ve ayni kartta dort farkli duzen vardi.
  const sayac = TSX.slice(TSX.indexOf("function SetCounter"), TSX.indexOf("function SetKpi"));
  const iDeger = sayac.indexOf("<strong>");
  const iEtiket = sayac.indexOf("device-set-counter-label");
  assert.ok(iDeger > 0 && iEtiket > 0);
  assert.ok(iDeger < iEtiket, "sayacta etiket hala degerden once");

  const kpi = TSX.slice(TSX.indexOf("function SetKpi"));
  const kDeger = kpi.indexOf("device-set-kpi-value");
  const kEtiket = kpi.indexOf("device-set-kpi-label");
  assert.ok(kDeger < kEtiket, "KPI duzeni degismis");
});

// ---------------------------------------------------------------------------
// 6) OKUNAKLILIK (kullanici istegi)
// ---------------------------------------------------------------------------

test("ariza satirlari BUYUDU", () => {
  const ad = kural("device-set-status-name");
  const px = Number(/font-size:\s*(\d+(?:\.\d+)?)px/.exec(ad)?.[1]);
  assert.ok(px >= 13, `ariza satiri ${px}px — onceden 12px, buyumeliydi`);
  // Satiri artik METIN yonetiyor, 16px'lik ikon degil.
  assert.match(ad, /line-height:/, "line-height tanimsiz — satiri ikon yonetiyor");
});

test("rozet metni de BUYUDU", () => {
  const b = kural("device-set-badge");
  const px = Number(/font-size:\s*(\d+(?:\.\d+)?)px/.exec(b)?.[1]);
  assert.ok(px >= 12.5, `rozet ${px}px — onceden 11px`);
});

test("etiket ile rozet arasi AYIRICI ile baglandi", () => {
  // `space-between` 170-355px bosluk birakiyordu ve gozun tutunacagi bir
  // sey yoktu. Cozum ayni dosyadaki olcum satirlarinda ZATEN vardi.
  assert.match(kural("device-set-status-row"), /border-bottom:/);
});

test("mikro etiketler AA kontrastina cekildi", () => {
  // #94a3b8 beyaz uzerinde 2.51:1 ve tam da en kucuk boyutlarda
  // kullaniliyordu. #64748b 4.42:1 verir.
  for (const ad of ["device-set-serial", "device-set-kpi-label", "device-set-counter-label"]) {
    assert.ok(!/#94a3b8/.test(kural(ad)), `${ad} hala #94a3b8 (2.51:1)`);
  }
});

test("kart tiklanabilir olmadigi halde YUKSELMIYOR", () => {
  // Yukselen bir yuzey, olmayan bir etkilesim vaat ediyordu.
  assert.ok(!/onClick|<button/.test(TSX.slice(0, TSX.indexOf("function SetCounter"))),
    "kart artik tiklanabilir — hover davranisi gozden gecirilmeli");
  assert.ok(!/translateY/.test(kural("device-set-card")), "hover yukselmesi geri gelmis");
});
