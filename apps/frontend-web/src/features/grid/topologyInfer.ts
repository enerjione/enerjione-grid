/**
 * Topoloji cikarici — koordinat yiginindan hat agaci.
 *
 * Kullanici TUM direklerin koordinatlarini verir (sira/geri-donus numarasi
 * BEKLENMEZ); topoloji su fizik varsayimiyla kurulur: havai hatta her direk
 * en yakin komsusuna baglanir. Bu, noktalarin MINIMUM ORTEN AGACI'dir (MST).
 *
 *   1. Yakin kopyalar (<5 m) tekillestirilir.
 *   2. MST kurulur (Prim, O(n^2) — 2000 direge kadar rahat).
 *   3. Agacin EN UZUN YOLU (cap) ana hat ONERISIdir; iki ucundan hangisinin
 *      "hat basi" oldugunu MAKINE BILEMEZ — bu SORU olarak kullaniciya gider.
 *   4. Ana yoldan ayrilan her alt agac bir BRANSMAN hattidir; alt agacin
 *      kendi en uzun yolu o dalin govdesi olur, kalanlar ic ice dallanir.
 *
 * Cikti, sihirbazin "makine onerir, insan onaylar" akisini besler: oneri
 * haritada cizilir, kullanici baslangic ucunu secer, sonuc birebir gorunur.
 */

export type TNokta = { lat: number; lon: number };

export type CikarilanDal = {
  /** EBEVEYN hattin kacinci diregine baglanir (1 tabanli). */
  attachSeq: number;
  poles: TNokta[];
  branches: CikarilanDal[];
};

export type CikarilanTopoloji = {
  main: TNokta[];
  branches: CikarilanDal[];
  /** Capin iki ucu — "hangi uc baslangic?" sorusunun secenekleri. */
  endpoints: [TNokta, TNokta] | null;
  /** Tekillestirme sonrasi dugum sayisi. */
  nodeCount: number;
};

const AYNI_NOKTA_M = 5;

function metre(a: TNokta, b: TNokta): number {
  const R = 6371000;
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const x =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}

function tekillestir(points: TNokta[]): TNokta[] {
  const out: TNokta[] = [];
  for (const p of points) {
    if (!out.some((q) => metre(p, q) < AYNI_NOKTA_M)) out.push(p);
  }
  return out;
}

/** Prim MST — komsuluk listesi dondurur. */
function mst(points: TNokta[]): number[][] {
  const n = points.length;
  const adj: number[][] = Array.from({ length: n }, () => []);
  if (n <= 1) return adj;
  const inTree = new Array<boolean>(n).fill(false);
  const dist = new Array<number>(n).fill(Infinity);
  const parent = new Array<number>(n).fill(-1);
  dist[0] = 0;
  for (let it = 0; it < n; it++) {
    let u = -1;
    for (let i = 0; i < n; i++) {
      if (!inTree[i] && (u === -1 || dist[i] < dist[u])) u = i;
    }
    inTree[u] = true;
    if (parent[u] >= 0) {
      adj[u].push(parent[u]);
      adj[parent[u]].push(u);
    }
    for (let v = 0; v < n; v++) {
      if (!inTree[v]) {
        const d = metre(points[u], points[v]);
        if (d < dist[v]) {
          dist[v] = d;
          parent[v] = u;
        }
      }
    }
  }
  return adj;
}

/** Agirlikli BFS/DFS: `start`tan en uzak dugum + parent haritasi. */
function enUzak(
  points: TNokta[], adj: number[][], start: number
): { node: number; parent: Int32Array } {
  const n = points.length;
  const parent = new Int32Array(n).fill(-1);
  const dist = new Array<number>(n).fill(-1);
  dist[start] = 0;
  const stack = [start];
  let best = start;
  while (stack.length > 0) {
    const u = stack.pop()!;
    for (const v of adj[u]) {
      if (dist[v] < 0) {
        dist[v] = dist[u] + metre(points[u], points[v]);
        parent[v] = u;
        if (dist[v] > dist[best]) best = v;
        stack.push(v);
      }
    }
  }
  return { node: best, parent };
}

function yol(parent: Int32Array, hedef: number): number[] {
  const p: number[] = [];
  for (let v = hedef; v >= 0; v = parent[v]) p.push(v);
  return p.reverse();
}

/** Bir govde yolundan sarkan alt agaclari ic ice dallara ayir. */
function dallariTopla(
  points: TNokta[], adj: number[][], govde: number[], yasak: Set<number>
): CikarilanDal[] {
  const govdeSet = new Set(govde);
  const dallar: CikarilanDal[] = [];
  govde.forEach((dugum, gi) => {
    for (const komsu of adj[dugum]) {
      if (govdeSet.has(komsu) || yasak.has(komsu)) continue;
      // komsu'dan asagi inen alt agacin kendi en uzun yolu = dal govdesi.
      const engel = new Set(yasak);
      for (const g of govde) engel.add(g);
      const altYol = altAgacGovdesi(points, adj, komsu, engel);
      for (const d of altYol) engel.add(d);
      const dal: CikarilanDal = {
        attachSeq: gi + 1,
        poles: altYol.map((i) => points[i]),
        branches: dallariTopla(points, adj, altYol, engel),
      };
      for (const d of altYol) yasak.add(d);
      dallar.push(dal);
    }
  });
  return dallar;
}

/** `kok`ten baslayip yasakli dugumlere girmeden inilebilen EN UZUN yol. */
function altAgacGovdesi(
  points: TNokta[], adj: number[][], kok: number, yasak: Set<number>
): number[] {
  let bestYol: number[] = [kok];
  let bestUzunluk = 0;
  const dfs = (u: number, gecilen: number[], uzunluk: number, ziyaret: Set<number>) => {
    if (uzunluk >= bestUzunluk) {
      bestUzunluk = uzunluk;
      bestYol = [...gecilen];
    }
    for (const v of adj[u]) {
      if (yasak.has(v) || ziyaret.has(v)) continue;
      ziyaret.add(v);
      gecilen.push(v);
      dfs(v, gecilen, uzunluk + metre(points[u], points[v]), ziyaret);
      gecilen.pop();
    }
  };
  dfs(kok, [kok], 0, new Set([kok]));
  return bestYol;
}

/**
 * Topolojiyi cikar. `startIndex`: capin hangi ucu hat basi (0 | 1) —
 * kullanicinin cevabi; makine varsayilan olarak 0'i alir.
 */
export function topolojiCikar(points: TNokta[], startIndex: 0 | 1 = 0): CikarilanTopoloji {
  const tekil = tekillestir(points);
  const n = tekil.length;
  if (n === 0) return { main: [], branches: [], endpoints: null, nodeCount: 0 };
  if (n === 1) return { main: tekil, branches: [], endpoints: null, nodeCount: 1 };

  const adj = mst(tekil);
  const a = enUzak(tekil, adj, 0).node;
  const bSonuc = enUzak(tekil, adj, a);
  const b = bSonuc.node;
  const capYolu = yol(bSonuc.parent, b); // a -> b

  const uclar: [TNokta, TNokta] = [tekil[a], tekil[b]];
  const govde = startIndex === 0 ? capYolu : [...capYolu].reverse();

  const yasak = new Set<number>(govde);
  const branches = dallariTopla(tekil, adj, govde, yasak);
  return {
    main: govde.map((i) => tekil[i]),
    branches,
    endpoints: uclar,
    nodeCount: n,
  };
}

/** Onizleme/ozet icin dallari duzlestir (baglanti NOKTASI ile). */
export function dallariDuzlestir(
  topo: CikarilanTopoloji
): Array<{ from: TNokta; poles: TNokta[]; depth: number }> {
  const out: Array<{ from: TNokta; poles: TNokta[]; depth: number }> = [];
  const gez = (parentPoles: TNokta[], dallar: CikarilanDal[], depth: number) => {
    for (const d of dallar) {
      const from = parentPoles[d.attachSeq - 1];
      if (!from) continue;
      out.push({ from, poles: d.poles, depth });
      gez(d.poles, d.branches, depth + 1);
    }
  };
  gez(topo.main, topo.branches, 1);
  return out;
}

/** Toplam dal ve direk sayisi (ic ice dahil). */
export function dalIstatistik(topo: CikarilanTopoloji): { dal: number; direk: number } {
  let dal = 0;
  let direk = 0;
  const gez = (dallar: CikarilanDal[]) => {
    for (const d of dallar) {
      dal += 1;
      direk += d.poles.length;
      gez(d.branches);
    }
  };
  gez(topo.branches);
  return { dal, direk };
}
