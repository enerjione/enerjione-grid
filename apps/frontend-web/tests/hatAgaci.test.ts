/**
 * HAT AGACI — renk BILGI tasir, haberlesme OKUNUR.
 *
 * IKI SIKAYET
 * -----------
 * 1. "Cok renkli, mor kullanilmis." Agacta renk bir DURUMA karsilik
 *    gelmeli: kirmizi=ariza, yesil=saglikli, mavi=Smart uyku. Indigo
 *    hicbir duruma karsilik gelmiyordu; yalnizca "secili satir" ve "sayac"
 *    gibi NOTR ogeleri boyayip gercek durum renklerinin okunurlugunu
 *    dusuruyordu.
 *
 * 2. "Hangi cihazin haberlesmesi yok bilmiyorum." Satirdaki tek sinyal
 *    7px'lik bir noktaydi; hat basligi ise yalnizca SAYI veriyordu
 *    ("5 kopuk"), hangileri oldugunu degil.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const CSS = readFileSync(join(process.cwd(), "src", "styles.css"), "utf8");
const AGAC = readFileSync(
  join(process.cwd(), "src", "features", "devices", "DeviceLineTree.tsx"),
  "utf8"
);

/** Agac kurallarinin bulundugu CSS dilimi. */
function agacDilimi(): string {
  const bas = CSS.indexOf(".device-tree {");
  assert.ok(bas > 0, ".device-tree kurali yok");
  const son = CSS.indexOf(".device-marker-comm");
  return CSS.slice(bas, son > bas ? son : bas + 20000);
}

test("agacta INDIGO/MOR ton kalmadi", () => {
  const dilim = agacDilimi();
  for (const ton of ["#eef2ff", "#6366f1", "#4338ca", "#1e1b4b", "#a855f7", "#7e22ce"]) {
    assert.ok(
      !dilim.includes(ton),
      `${ton} agac paletinde — hicbir duruma karsilik gelmiyor`
    );
  }
});

test("kopuk cihaz SATIRDA isaretlenir", () => {
  assert.match(AGAC, /device-tree-row-off/, "satirda kopukluk isareti yok");
  // Isaret YALNIZCA gercekten kopuk olanda cikmali.
  assert.match(
    AGAC,
    /runtime\.bucket === "unhealthy"/,
    "isaret kova kontrolune bagli degil"
  );
});

test("SMART BEKLEME kopuk gibi isaretlenmez", () => {
  // `smart_idle` kovasi "healthy"; uyuyan filo isaretlenirse gosterge
  // anlamsizlasir ve gercek kopukluk gurultude kaybolur.
  assert.doesNotMatch(
    AGAC,
    /bucket !== "healthy"[\s\S]{0,80}device-tree-row-off/,
    "isaret saglikli olmayan HER durumda cikiyor (smart_idle dahil)"
  );
});

test("hat basligindaki 'N kopuk' SAYI rozeti kaldirildi", () => {
  // Rozet yalnizca sayi veriyordu; kullanicinin sorusu "HANGISI" idi.
  // `onlineCount/deviceCount` zaten ayni olcuyu tasiyor.
  assert.doesNotMatch(AGAC, /device-tree-badge--comm/, "tekrar eden rozet duruyor");
  assert.match(AGAC, /device-tree-count/, "online/toplam sayaci da kaybolmus");
});

test("ARIZA rozeti KORUNDU", () => {
  // Ariza guvenlik bilgisidir; sadelestirme adina kaldirilmaz.
  assert.match(AGAC, /device-tree-badge--fault/);
});

test("durum noktasi ayirt edilebilir boyutta", () => {
  const i = CSS.indexOf(".device-tree-dot {");
  assert.ok(i > 0);
  const blok = CSS.slice(i, CSS.indexOf("}", i));
  const m = /width:\s*(\d+(?:\.\d+)?)px/.exec(blok);
  assert.ok(m, "nokta genisligi yok");
  assert.ok(Number(m[1]) >= 9, `nokta ${m[1]}px — renk ayirt etmek zor`);
});
