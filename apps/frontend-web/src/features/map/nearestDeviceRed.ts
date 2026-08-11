/**
 * "Akim bu dala mi gitti?" — dallanma noktasindaki KARAR.
 *
 * Ariza haritasi, besleme yonunde yurudugu grafta bir dallanma diregine
 * gelince hangi kolun akimi tasidigini bilmek zorunda: RED->GREEN gecisi o
 * kolda aranir, digerleri ariza yolunun disindadir.
 *
 * YASANAN HATA
 * ------------
 * Karar `subtreeHasRed` ile veriliyordu: "bu kolun ALTINDA HERHANGI BIR
 * YERDE kirmizi cihaz var mi?". Ozyineleme cihazlarda DURMUYOR, subtree'nin
 * dibine kadar iniyordu. Test sunucusundaki gercek topolojide bu su hatayi
 * uretti:
 *
 *     ANA HAT: ...3 ─[cihaz 2 KIRMIZI]─ 4 5 6 ─[7]─ 8 9 ─[cihaz 4 YESIL]─ 10
 *                                            │                            │
 *                                    Z-12_BRS  BR-2                     BR-3
 *                                    (cihaz    [cihaz 5                   │
 *                                     YOK)      YESIL]                   BR-4
 *                                                                  [cihaz 3 KIRMIZI]
 *
 * Direk #7'de ana yol icin `subtreeHasRed` TRUE donuyordu — cunku cok
 * asagida, BR-4'te kirmizi bir cihaz var. Algoritma "akim ana yoldan gitti"
 * deyip #7'nin iki kolunu da "yan dal" ilan ediyor ve o kollar ASLA arizali
 * boyanmiyordu.
 *
 * Oysa aradaki cihaz 4 YESIL: "buradan akim gecmedi". BR-4'teki kirmizi,
 * akimin #7'den ana yola devam ettiginin kaniti OLAMAZ — arada onu yalanlayan
 * bir olcum var (ve o kirmizi zaten AYRI bir arizanin kaydi).
 *
 * DOGRU KURAL
 * -----------
 * Soruyu o yoldaki EN YAKIN cihaz cevaplar. Ozyineleme ilk cihazda DURUR:
 *
 *     cihaz dugumu  -> cevap cihazin kendi durumu (kirmizi/yesil)
 *     direk dugumu  -> cocuklardan herhangi biri kirmiziysa kirmizi
 *
 * Boylece bir yesil cihazin OTESINDEKI kirmizi, o dallanma icin delil
 * sayilmaz. #7'de hicbir kolda "yakin kirmizi" kalmaz, uc kol da esit
 * derecede supheli olur ve ikisi de boyanir — kullanicinin bekledigi budur:
 * o kollarda ariza olabilir ve bunu gorebilecek bir olcum YOK.
 */

export type GrafDugumu = {
  kind: "pole" | "device";
  isRed?: boolean;
};

/**
 * Her dugum icin: bu dugumden asagi giden yolda EN YAKIN cihaz kirmizi mi?
 *
 * `null` degil `false` doner (cihaz yok = kirmizi degil): cagiran taraf
 * "kirmizi mi" diye soruyor, "olculdu mu" diye degil. Olcum yoklugunu
 * ayrica `subtreeHasGreenDevice` ile birlikte degerlendiriyor.
 */
export function nearestDeviceRedMap(input: {
  nodes: ReadonlyMap<string, GrafDugumu>;
  /** dugum -> giden kenar kimlikleri */
  outEdges: ReadonlyMap<string, readonly string[]>;
  /** kenar kimligi -> hedef dugum */
  edgeTarget: (edgeId: string) => string | undefined;
  rootNodeIds: readonly string[];
}): Map<string, boolean> {
  const { nodes, outEdges, edgeTarget, rootNodeIds } = input;
  const sonuc = new Map<string, boolean>();

  // Iteratif post-order: cocuklarin sonucu hesaplanmadan ebeveyn
  // hesaplanamaz. Ozyinelemeli surum derin bir hatta yigini tasirabilir.
  type Cerceve = { nodeId: string; phase: 0 | 1 };
  const stack: Cerceve[] = rootNodeIds.map((id) => ({ nodeId: id, phase: 0 as 0 | 1 }));
  // Dongusel topolojide (ring/tie) sonsuz donguyu engelle.
  const gorulen = new Set<string>();

  while (stack.length > 0) {
    const f = stack[stack.length - 1];
    const node = nodes.get(f.nodeId);
    if (!node) {
      stack.pop();
      continue;
    }

    if (f.phase === 0) {
      f.phase = 1;
      // GEZINME her yere gider: cihazin ALTINDAKI dugumlerin de kendi
      // degeri olmali (cagiran algoritma her catalda soruyor, cihazdan
      // sonraki catallar dahil). Cihazda duran sey gezinme degil,
      // BIRLESTIRME — asagida.
      if (gorulen.has(f.nodeId)) continue;
      gorulen.add(f.nodeId);
      for (const eid of outEdges.get(f.nodeId) ?? []) {
        const hedef = edgeTarget(eid);
        if (hedef !== undefined) stack.push({ nodeId: hedef, phase: 0 });
      }
      continue;
    }

    stack.pop();
    if (node.kind === "device") {
      // BIRLESTIRME BURADA DURUR: cevabi cihazin KENDISI verir, altina
      // bakilmaz. Bakilsaydi bir yesil cihazin otesindeki kirmizi yukari
      // sizar ve "akim bu yoldan gitti" yanilgisini uretirdi.
      sonuc.set(f.nodeId, !!node.isRed);
      continue;
    }
    let kirmizi = false;
    for (const eid of outEdges.get(f.nodeId) ?? []) {
      const hedef = edgeTarget(eid);
      if (hedef !== undefined && sonuc.get(hedef)) {
        kirmizi = true;
        break;
      }
    }
    sonuc.set(f.nodeId, kirmizi);
  }

  return sonuc;
}
