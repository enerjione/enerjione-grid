/**
 * Dallanma noktasinda "akim bu dala mi gitti?" karari.
 *
 * Yanlis karar iki yonde de pahali:
 *   * kolu yanlislikla "temiz" saymak  -> oradaki ariza EKRANDA GORUNMEZ,
 *   * her kolu supheli boyamak         -> ariza yeri anlamini kaybeder.
 *
 * Asagidaki son test, TEST SUNUCUSUNDAKI gercek topolojiyi birebir kurar.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { nearestDeviceRedMap, type GrafDugumu } from "../src/features/map/nearestDeviceRed";

/** Kucuk graf kurucusu: "a>b" kenarlari. */
function graf(
  dugumler: Record<string, GrafDugumu>,
  kenarlar: [string, string][],
  kokler: string[]
) {
  const nodes = new Map<string, GrafDugumu>(Object.entries(dugumler));
  const outEdges = new Map<string, string[]>();
  const hedefler = new Map<string, string>();
  kenarlar.forEach(([a, b], i) => {
    const eid = `e${i}`;
    hedefler.set(eid, b);
    const liste = outEdges.get(a) ?? [];
    liste.push(eid);
    outEdges.set(a, liste);
  });
  return nearestDeviceRedMap({
    nodes,
    outEdges,
    edgeTarget: (eid) => hedefler.get(eid),
    rootNodeIds: kokler
  });
}

const P: GrafDugumu = { kind: "pole" };
const KIRMIZI: GrafDugumu = { kind: "device", isRed: true };
const YESIL: GrafDugumu = { kind: "device", isRed: false };

test("cihazin kendisi kendi durumunu doner", () => {
  const m = graf({ d: KIRMIZI }, [], ["d"]);
  assert.equal(m.get("d"), true);
  const m2 = graf({ d: YESIL }, [], ["d"]);
  assert.equal(m2.get("d"), false);
});

test("en yakin cihaz KIRMIZI ise direk de kirmizi", () => {
  const m = graf({ p: P, d: KIRMIZI }, [["p", "d"]], ["p"]);
  assert.equal(m.get("p"), true);
});

test("en yakin cihaz YESIL ise, OTESINDEKI kirmizi sayilmaz", () => {
  // Kusurun ozu buydu. Yesil cihaz "buradan akim gecmedi" diyor; ondan
  // sonraki kirmizi baska bir arizanin kaydidir ve bu dallanma icin delil
  // olamaz.
  const m = graf(
    { p: P, yesil: YESIL, p2: P, kirmizi: KIRMIZI },
    [
      ["p", "yesil"],
      ["yesil", "p2"],
      ["p2", "kirmizi"]
    ],
    ["p"]
  );
  assert.equal(m.get("p"), false, "yesilin otesindeki kirmizi sizmis");
  assert.equal(m.get("yesil"), false);
});

test("cihazsiz kol kirmizi DEGIL — ama bu 'temiz' demek degil", () => {
  // Bu fonksiyon yalnizca "yakin kirmizi var mi" sorusunu cevaplar.
  // Olcum yoklugu cagiran tarafta ayrica ele alinir; burada false donmesi
  // "kirmizi degil" demektir, "saglam" demek degil.
  const m = graf({ p: P, p2: P }, [["p", "p2"]], ["p"]);
  assert.equal(m.get("p"), false);
});

test("dallardan BIRI kirmiziysa direk kirmizi", () => {
  const m = graf(
    { p: P, a: YESIL, b: KIRMIZI },
    [
      ["p", "a"],
      ["p", "b"]
    ],
    ["p"]
  );
  assert.equal(m.get("p"), true);
});

test("dongusel topoloji sonsuz donguye girmez", () => {
  // Ring/tie kurulumunda iki hat birbirine baglanabilir.
  const m = graf(
    { a: P, b: P, c: P },
    [
      ["a", "b"],
      ["b", "c"],
      ["c", "a"]
    ],
    ["a"]
  );
  assert.equal(m.get("a"), false);
});

test("TEST SUNUCUSU topolojisi: #7'nin iki kolu da 'yakin kirmizi' DEGIL", () => {
  //  ANA HAT: 3 ─[cihaz2 KIRMIZI]─ 4 5 6 ─[7]─ 8 9 ─[cihaz4 YESIL]─ 10
  //                                      │                           │
  //                              Z-12_BRS  BR-2                    BR-3
  //                              (cihazsiz) [cihaz5 YESIL]            │
  //                                                                 BR-4
  //                                                          [cihaz3 KIRMIZI]
  const m = graf(
    {
      p3: P, cihaz2: KIRMIZI, p4: P, p5: P, p6: P, p7: P, p8: P, p9: P,
      cihaz4: YESIL, p10: P,
      z1: P, z2: P,                    // Z-12_BRS: iki direk, CIHAZ YOK
      cihaz5: YESIL, br2p1: P,         // BR-2: baglanti telinde yesil cihaz
      br3p1: P, br4p1: P, cihaz3: KIRMIZI, br4p2: P
    },
    [
      ["p3", "cihaz2"], ["cihaz2", "p4"], ["p4", "p5"], ["p5", "p6"], ["p6", "p7"],
      ["p7", "p8"], ["p8", "p9"], ["p9", "cihaz4"], ["cihaz4", "p10"],
      ["p7", "z1"], ["z1", "z2"],                       // kol 1 (cihazsiz)
      ["p7", "cihaz5"], ["cihaz5", "br2p1"],            // kol 2 (yesil cihaz)
      ["p10", "br3p1"], ["br3p1", "br4p1"],
      ["br4p1", "cihaz3"], ["cihaz3", "br4p2"]          // BR-4'teki KIRMIZI
    ],
    ["p3"]
  );

  // Ana yol: en yakin cihaz `cihaz4` ve o YESIL. BR-4'teki kirmizi cok
  // asagida ve arada yesil var -> ana yol "yakin kirmizi" DEGIL.
  assert.equal(m.get("p8"), false, "yesilin otesindeki BR-4 kirmizisi ana yola sizmis");
  // Iki kol da yakin-kirmizi degil.
  assert.equal(m.get("z1"), false);
  assert.equal(m.get("cihaz5"), false);

  // SONUC: #7'de hicbir kolda yakin kirmizi yok -> ucu de esit derecede
  // supheli. Cagiran algoritma bu durumda (redChildCount === 0) hepsini
  // ariza yolu sayar ve kollar boyanir. Eski davranista `p8` TRUE
  // donuyordu ve kollar "yan dal" olarak elenip yesil kaliyordu.
  const kollar = ["p8", "z1", "cihaz5"].filter((k) => m.get(k) === true);
  assert.deepEqual(kollar, [], `bir kol hala akim yolu saniliyor: ${kollar}`);

  // BR-4 kendi icinde kirmizi kalmali (ayri ariza).
  assert.equal(m.get("br4p1"), true);
});
